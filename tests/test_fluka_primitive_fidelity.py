import importlib.util
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from fluka_primitive_fidelity import AFFECTED_BODY_TYPES, PrimitiveFidelityError, primitive_fidelity_guard
from convert_ir1_fluka_geometry_full import full_conversion_guards


@unittest.skipUnless(importlib.util.find_spec("pyg4ometry"), "pyg4ometry unavailable")
class PrimitiveFidelityTest(unittest.TestCase):
    def setUp(self):
        from pyg4ometry import fluka, geant4
        self.f, self.g = fluka, geant4

    def rec(self, transform=None):
        return self.f.REC("ellipse", [7, -9, 11], [0, 0, 8], [3, 4, 0], [-8, 6, 0],
                          transform=transform)

    def transform(self):
        return self.f.Transform(expansion=2.5, translation=[20, -30, 40],
            rotoTranslation=self.f.RotoTranslation("rot", axis="x", azimuth=23))

    def test_rec_raw_mesh_extents_and_approximate_volume(self):
        body = self.f.REC("aligned", [7, -9, 11], [0, 0, 8], [3, 0, 0], [0, 5, 0])
        ledger = {}
        with primitive_fidelity_guard(ledger):
            mesh = body.mesh()
            points = np.asarray(mesh.toVerticesAndPolygons()[0])
        np.testing.assert_allclose(points.min(axis=0), [4, -14, 11], atol=1e-12)
        np.testing.assert_allclose(points.max(axis=0), [10, -4, 19], atol=1e-12)
        self.assertAlmostEqual(abs(mesh.volume()), math.pi * 3 * 5 * 8,
                               delta=0.05 * math.pi * 3 * 5 * 8)
        self.assertEqual(ledger["corrected_bodies"][0]["gdml_dx_dy_dz_mm"], [3, 5, 4])

    def test_rotated_expanded_translated_rec_and_length_safety(self):
        from fluka_analytic_bounds import body_bounds
        body = self.rec(self.transform())
        for candidate in (body, body.safetyShrunk(), body.safetyExpanded()):
            with primitive_fidelity_guard({}):
                solid = candidate.geant4Solid(self.g.Registry())
                mesh = candidate.mesh()
            expansion = candidate.transform.netExpansion()
            expected = [candidate.semiminor.length() * expansion,
                        candidate.semimajor.length() * expansion,
                        candidate.direction.length() * expansion / 2]
            np.testing.assert_allclose([solid.pDx, solid.pDy, solid.pDz], expected, rtol=1e-14)
            volume = math.pi * expected[0] * expected[1] * 2 * expected[2]
            self.assertAlmostEqual(abs(mesh.volume()), volume, delta=0.05 * volume)
            lower, upper = body_bounds(candidate)
            vertices = np.asarray(mesh.toVerticesAndPolygons()[0])
            self.assertTrue(np.all(vertices >= lower - 1e-10))
            self.assertTrue(np.all(vertices <= upper + 1e-10))

    def infinite_cases(self, transform=None):
        return [(self.f.XEC("xellipse", 7, 11, 3, 5, transform=transform), [5, 3]),
                (self.f.YEC("yellipse", 11, 7, 5, 3, transform=transform), [3, 5]),
                (self.f.ZEC("zellipse", 7, 11, 3, 5, transform=transform), [3, 5])]

    def test_infinite_parameters_use_global_context_full_length_once(self):
        bounds = self.f.AABB([-100, -120, -140], [100, 120, 140])
        ledger = {}
        with primitive_fidelity_guard(ledger):
            for body, axes in self.infinite_cases(self.transform()):
                solid = body.geant4Solid(self.g.Registry(), aabb=bounds)
                np.testing.assert_allclose([solid.pDx, solid.pDy], np.array(axes) * 2.5)
                self.assertAlmostEqual(solid.pDz, np.linalg.norm(bounds.size) * 1.1 / 2)
                self.assertTrue(np.isfinite(body.mesh(aabb=bounds).volume()))
        self.assertTrue(all(record["finite_context_supplied"] for record in ledger["corrected_bodies"]))
        self.assertTrue(all(not record["finite_axial_extent_validated"] for record in ledger["corrected_bodies"]))

    def test_infinite_unbounded_surrogate_is_explicit_and_restored(self):
        from pyg4ometry.fluka import body as module
        ledger = {}
        with full_conversion_guards((2000, 2000, 2000), performance_report={}), primitive_fidelity_guard(ledger):
            for body, axes in self.infinite_cases(self.transform()):
                solid = body.geant4Solid(self.g.Registry())
                self.assertEqual(solid.pDz, module.INFINITY / 2)
                self.assertFalse(ledger["corrected_bodies"][-1]["finite_context_supplied"])
                # Existing full-conversion guards supply the correct centre
                # when no finite AABB is passed to an infinite cylinder.
                self.assertTrue(np.isfinite(body.mesh().volume()))

    def test_restoration_on_success_exception_and_nested_guard(self):
        from pyg4ometry.fluka import body as module
        original = {name: getattr(module, name).geant4Solid for name in AFFECTED_BODY_TYPES}
        with self.assertRaisesRegex(RuntimeError, "injected"):
            with primitive_fidelity_guard({}):
                outer = module.REC.geant4Solid
                with primitive_fidelity_guard({}):
                    self.assertIsNot(module.REC.geant4Solid, outer)
                self.assertIs(module.REC.geant4Solid, outer)
                raise RuntimeError("injected")
        for name, function in original.items():
            self.assertIs(getattr(module, name).geant4Solid, function)

    def test_zero_or_nonfinite_dimensions_fail_closed(self):
        with primitive_fidelity_guard({}):
            for radius in (0, -1, float("inf"), float("nan")):
                body = self.f.ZEC("bad", 0, 0, radius, 2)
                with self.assertRaises(PrimitiveFidelityError):
                    body.geant4Solid(self.g.Registry())

    def write_fixture(self, body, path, aabb=None):
        from pyg4ometry import gdml, transformation
        registry = self.g.Registry()
        with primitive_fidelity_guard({}):
            solid = body.geant4Solid(registry, aabb=aabb)
        world_solid = self.g.solid.Box("world_solid", 5000, 5000, 5000, registry)
        vacuum = self.g.MaterialPredefined("G4_Galactic", registry)
        world = self.g.LogicalVolume(world_solid, vacuum, "world", registry)
        child = self.g.LogicalVolume(solid, vacuum, "child", registry)
        self.g.PhysicalVolume(list(transformation.reverse(body.tbxyz())),
                              list(body.centre(aabb=aabb)), child, "child_pv", world, registry)
        registry.setWorld(world)
        writer = gdml.Writer()
        writer.addDetector(registry)
        writer.write(str(path))
        return solid

    def root_membership(self, path, points, dimensions):
        checks = []
        for point, expected in points:
            coords = ",".join(format(float(value), ".17g") for value in point)
            checks.append('{ auto n=g->FindNode(' + coords + '); bool inside=n && TString(n->GetVolume()->GetName())=="child"; '
                          + f'if(inside!={str(expected).lower()}) ++failures; }}')
        checks.extend(f'if(std::abs(s->{method}()-{value:.17g})>1e-10) ++failures;'
                      for method, value in zip(("GetA", "GetB", "GetDz"), dimensions))
        code = ('TGeoManager::SetDefaultUnits(TGeoManager::kG4Units); '
                f'auto g=TGeoManager::Import({json.dumps(str(path))}); if(!g) gSystem->Exit(2); '
                'auto s=dynamic_cast<TGeoEltu*>(g->FindVolumeFast("child")->GetShape()); if(!s) gSystem->Exit(3); '
                'int failures=0; ' + ' '.join(checks) + ' std::cout << "PRIMITIVE_FAILURES " << failures << std::endl; gSystem->Exit(failures?4:0);')
        result = subprocess.run([shutil.which("root"), "-l", "-b", "-q", "-e", code],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(shutil.which("root"), "ROOT unavailable")
    def test_independent_root_rec_source_faces_transforms_and_safety(self):
        original = self.rec(self.transform())
        for body in (original, original.safetyShrunk()):
            points = []
            for radial in (body.semiminor, body.semimajor):
                for sign in (-1, 1):
                    for factor, inside in ((0.99999, True), (1.00001, False)):
                        point = body.face + body.direction / 2 + radial * sign * factor
                        points.append((body.transform.leftMultiplyVector(point), inside))
            for fraction, inside in ((-0.00001, False), (0.00001, True), (0.99999, True), (1.00001, False)):
                points.append((body.transform.leftMultiplyVector(body.face + fraction * body.direction), inside))
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "rec.gdml"
                solid = self.write_fixture(body, path)
                node = ET.parse(path).find("./solids/eltube")
                self.assertEqual(float(node.get("dz")), body.direction.length() * 2.5 / 2)
                self.root_membership(path, points, [solid.pDx, solid.pDy, solid.pDz])

    @unittest.skipUnless(shutil.which("root"), "ROOT unavailable")
    def test_independent_root_infinite_radial_faces_in_bounded_context(self):
        bounds = self.f.AABB([-300, -300, -300], [300, 300, 300])
        for body, axes in self.infinite_cases(self.transform()):
            axis = "XYZ".index(type(body).__name__[0])
            point = np.zeros(3)
            for radial in range(3):
                if radial != axis:
                    point[radial] = getattr(body, "xyz"[radial])
            points = []
            for radial in range(3):
                if radial == axis:
                    continue
                for sign in (-1, 1):
                    for factor, inside in ((0.99999, True), (1.00001, False)):
                        probe = point.copy()
                        probe[radial] += sign * factor * getattr(body, "xyz"[radial] + "semi")
                        points.append((body.transform.leftMultiplyVector(probe), inside))
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "infinite.gdml"
                solid = self.write_fixture(body, path, aabb=bounds)
                self.root_membership(path, points, [solid.pDx, solid.pDy, solid.pDz])


if __name__ == "__main__":
    unittest.main()
