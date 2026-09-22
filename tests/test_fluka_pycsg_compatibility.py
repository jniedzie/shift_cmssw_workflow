import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from fluka_pycsg_compatibility import triangulate_polygon_2d


def twice_area(polygon):
    return sum(a[0] * b[1] - b[0] * a[1]
               for a, b in zip(polygon, polygon[1:] + polygon[:1]))


class PolygonDecompositionTest(unittest.TestCase):
    def test_concave_orientation_area_and_vertex_preservation(self):
        polygon = [(0, 0), (3, 0), (3, 1), (1, 1), (1, 3), (0, 3)]
        for vertices in (polygon, list(reversed(polygon))):
            triangles = triangulate_polygon_2d(vertices)
            self.assertEqual(len(triangles), 4)
            self.assertEqual(sum(map(twice_area, triangles)), twice_area(vertices))
            self.assertTrue(all(twice_area(triangle) * twice_area(vertices) > 0 for triangle in triangles))
            self.assertEqual({v for triangle in triangles for v in triangle}, set(polygon))

    def test_closed_ring_and_exact_collinear_intermediate_point(self):
        triangles = triangulate_polygon_2d([(0, 0), (1, 0), (2, 0), (2, 2), (0, 2), (0, 0)])
        self.assertEqual(len(triangles), 2)
        self.assertEqual(sum(map(twice_area, triangles)), 8)

    def test_invalid_polygons_fail_closed(self):
        for polygon in ([(0, 0), (1, 1), (0, 1), (1, 0)],
                        [(0, 0), (1, 0), (2, 0)],
                        [(0, 0), (1, 0), (1, 0), (0, 1)],
                        [(0, 0), (1, 0), (float("nan"), 1)],
                        [(0, 0), (1, 0), (float("inf"), 1)],
                        [(0, 0), (1, 0)], [(0, 0, 1), (1, 0), (0, 1)],
                        [(0, 0), (2, 0), (1, 0), (1, 1)]):
            with self.subTest(polygon=polygon), self.assertRaises(ValueError):
                triangulate_polygon_2d(polygon)

    def test_small_nonzero_polygon_not_collapsed_by_tolerance(self):
        triangles = triangulate_polygon_2d([(0, 0), (1e-100, 0), (0, 1e-100)])
        self.assertEqual(len(triangles), 1)
        self.assertGreater(twice_area(triangles[0]), 0)

    @unittest.skipUnless(importlib.util.find_spec("pyg4ometry"), "pyg4ometry not installed")
    def test_real_pycsg_identities_nested_boolean_primitives_and_restoration(self):
        code = r'''
import importlib
import json
import math
from fluka_region_preflight_worker import bootstrap_pyg4ometry_pycsg
package = bootstrap_pyg4ometry_pycsg()
from pyg4ometry.pycsg.core import CSG
from fluka_pycsg_compatibility import pycsg_compatibility_guard
from pyg4ometry.geant4 import Registry
from pyg4ometry.geant4.solid import ExtrudedSolid, GenericPolyhedra
def volume(mesh):
    result = 0.0
    for polygon in mesh.toPolygons():
        p = polygon.vertices
        a = p[0].pos
        for i in range(1, len(p) - 1):
            b, c = p[i].pos, p[i + 1].pos
            result += a.dot(b.cross(c)) / 6
    return result
originals = {name: getattr(CSG, name) for name in ('union', 'subtract', 'intersect')}
extruded = importlib.import_module('pyg4ometry.geant4.solid.ExtrudedSolid')
assert not hasattr(extruded, '_PolygonProcessing')
box = CSG.cube(radius=[1, 1, 1])
empty = CSG.fromPolygons([])
failed_before = False
try:
    box.intersect(empty)
except AttributeError:
    failed_before = True
assert failed_before
box_before = repr(box.toPolygons())
with pycsg_compatibility_guard():
    for a, b in ((box, empty), (empty, box), (empty, empty)):
        assert a.intersect(b).isNull()
        assert abs(volume(a.union(b)) - (0 if a.isNull() and b.isNull() else 8)) < 1e-12
        assert abs(volume(a.subtract(b)) - (0 if a.isNull() else 8)) < 1e-12
    assert box.subtract(box).union(box).polygonCount() == box.polygonCount()
    distant = CSG.cube(center=[10, 0, 0], radius=[1, 1, 1])
    assert box.intersect(distant).subtract(box).isNull()
    assert abs(volume(box.intersect(distant).union(box)) - 8) < 1e-12
    overlapping = CSG.cube(center=[1, 0, 0], radius=[1, 1, 1])
    assert abs(volume(box.union(overlapping)) - 12) < 1e-12
    assert abs(volume(box.subtract(overlapping)) - 4) < 1e-12
    assert abs(volume(box.intersect(overlapping)) - 4) < 1e-12
    registry = Registry()
    prism = ExtrudedSolid('prism', [[0,0],[3,0],[3,1],[1,1],[1,3],[0,3]],
                          [[0,[0,0],1],[2,[0,0],1]], registry).mesh()
    assert abs(volume(prism) - 10) < 1e-12, volume(prism)
    wedge = GenericPolyhedra('wedge', 0, math.pi/2, 1,
                            [0,2,2,0], [0,0,3,3], registry).mesh()
    assert abs(volume(wedge) - 6) < 1e-12, volume(wedge)
assert repr(box.toPolygons()) == box_before
for name, original in originals.items():
    assert getattr(CSG, name) is original
assert not hasattr(extruded, '_PolygonProcessing')
try:
    with pycsg_compatibility_guard():
        raise ValueError('expected fixture exception')
except ValueError:
    pass
for name, original in originals.items():
    assert getattr(CSG, name) is original
assert not hasattr(extruded, '_PolygonProcessing')
print(json.dumps({'passed': True, 'backend': package.config.backendName()}))
'''
        environment = dict(os.environ, PYTHONPATH=str(SCRIPTS), PYTHONDONTWRITEBYTECODE="1")
        result = subprocess.run([sys.executable, "-c", code], env=environment,
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(json.loads(result.stdout.splitlines()[-1])["passed"])


if __name__ == "__main__":
    unittest.main()
