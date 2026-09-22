import importlib.util
from fractions import Fraction
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import numpy as np

from fluka_halfspace_bounds import (
    HalfspaceBoundsError, UnboundedHalfspaceError, certified_zone_bounds,
    halfspace_bounds_guard,
)


@unittest.skipUnless(importlib.util.find_spec("pyg4ometry"), "pyg4ometry not installed")
class HalfspaceBoundsTest(unittest.TestCase):
    def setUp(self):
        from pyg4ometry import fluka
        self.f = fluka

    def box(self, width=2, transform=None):
        zone = self.f.Zone("bounded")
        for axis, cls in enumerate((self.f.YZP, self.f.XZP, self.f.XYP)):
            zone.addIntersection(cls(f"upper{axis}", width/2, transform=transform))
            zone.addSubtraction(cls(f"lower{axis}", -width/2, transform=transform))
        return zone

    def volume(self, zone):
        return abs(zone.mesh().volume())

    def global_solid_mesh(self, zone, registry):
        from pyg4ometry import transformation
        solid = zone.geant4Solid(registry)
        mesh = solid.mesh()
        axis, angle = transformation.tbxyz2axisangle(zone.tbxyz())
        mesh.rotate(axis, -np.degrees(angle))
        mesh.translate(zone.centre())
        return mesh

    def test_box_certificates_raw_and_serialized_frame(self):
        from pyg4ometry import geant4
        zone = self.box()
        bounds, proof = certified_zone_bounds(zone)
        np.testing.assert_allclose(bounds, [[-1]*3, [1]*3], atol=1e-14)
        self.assertEqual(len(proof["dual_certificates"]), 6)
        with halfspace_bounds_guard({}):
            self.assertAlmostEqual(self.volume(zone), 8, delta=1e-8)
            self.assertAlmostEqual(abs(self.global_solid_mesh(zone, geant4.Registry()).volume()), 8, delta=1e-8)

    def test_rotated_translated_scaled_wedge(self):
        from pyg4ometry.fluka.directive import RotoTranslation, Transform
        transform = Transform(expansion=2.0, translation=[7, -9, 11],
                              rotoTranslation=RotoTranslation("rotation", axis="z", azimuth=31))
        zone = self.box(transform=transform)
        zone.addIntersection(self.f.PLA("diagonal", [1, 1, 0], [0, 0, 0], transform=transform))
        bounds, _ = certified_zone_bounds(zone)
        with halfspace_bounds_guard({}):
            mesh = zone.mesh()
            self.assertAlmostEqual(abs(mesh.volume()), 32, delta=1e-7)
            vertices = np.asarray(mesh.toVerticesAndPolygons()[0])
            self.assertTrue(np.all(vertices.min(axis=0) >= bounds[0] - 1e-8))
            self.assertTrue(np.all(vertices.max(axis=0) <= bounds[1] + 1e-8))

    def test_rational_certificates_verify_exactly(self):
        from pyg4ometry.fluka.directive import RotoTranslation, Transform
        zone = self.box(transform=Transform(translation=[1e4, -2e4, 3e4],
                        rotoTranslation=RotoTranslation("rotated", axis="z", azimuth=17)))
        _, proof = certified_zone_bounds(zone)
        for certificate in proof["dual_certificates"]:
            constraints = [proof["constraints"][index] for index in certificate["constraint_indices"]]
            weights = list(map(Fraction, certificate["nonnegative_weights"]))
            self.assertTrue(all(weight >= 0 for weight in weights))
            for axis in range(3):
                self.assertEqual(sum(weight * Fraction(row["A"][axis])
                                     for weight, row in zip(weights, constraints)),
                                 certificate["sign"] if axis == certificate["axis"] else 0)
            self.assertEqual(sum(weight * Fraction(row["b"]) for weight, row in zip(weights, constraints)),
                             Fraction(certificate["upper_rational"]))

    def test_nested_unbounded_subzone_keeps_complement_topology(self):
        from pyg4ometry import geant4
        zone = self.f.Zone("outer")
        zone.addIntersection(self.f.RPP("finite", -1, 1, -1, 1, -1, 1))
        subtract = self.f.Zone("unbounded_subzone")
        subtract.addIntersection(self.f.YZP("left", 0))
        subtract.addSubtraction(self.f.XZP("bottom", 0))
        zone.addSubtraction(subtract)
        before = zone.dumps()
        with halfspace_bounds_guard({}):
            self.assertAlmostEqual(self.volume(zone), 6, delta=1e-8)
            self.assertAlmostEqual(abs(self.global_solid_mesh(zone, geant4.Registry()).volume()), 6, delta=1e-8)
        self.assertEqual(zone.dumps(), before)

    def test_shared_bodies_distinct_contexts_and_far_redundant_plane(self):
        from pyg4ometry import geant4
        shared = self.f.YZP("shared", 1000)
        registry = geant4.Registry()
        with halfspace_bounds_guard({}):
            for offset in (0, 50):
                zone = self.f.Zone("at" + str(offset))
                zone.addIntersection(shared)
                zone.addIntersection(self.f.RPP("cube" + str(offset), offset-1, offset+1, -1, 1, -1, 1))
                self.assertAlmostEqual(self.volume(zone), 8, delta=1e-5)
                mesh = self.global_solid_mesh(zone, registry)
                self.assertAlmostEqual(abs(mesh.volume()), 8, delta=1e-5)
                vertices = np.asarray(mesh.toVerticesAndPolygons()[0])
                self.assertAlmostEqual(vertices[:, 0].mean(), offset, delta=1e-8)

    def test_nested_subzone_reused_under_two_parent_frames(self):
        from pyg4ometry import geant4
        shared = self.f.Zone("shared_subzone")
        shared.addIntersection(self.f.YZP("shared_left", 3))
        shared.addSubtraction(self.f.XZP("shared_bottom", 0))
        before = shared.dumps()
        registry = geant4.Registry()
        with halfspace_bounds_guard({}):
            for low, high, volume in ((-1, 1, 4), (2, 4, 6), (-1, 1, 4)):
                parent = self.f.Zone("parent" + str(low))
                parent.addIntersection(self.f.RPP("finite" + str(low), low, high, -1, 1, -1, 1))
                parent.addSubtraction(shared)
                self.assertAlmostEqual(self.volume(parent), volume, delta=1e-8)
                mesh = self.global_solid_mesh(parent, registry)
                self.assertAlmostEqual(abs(mesh.volume()), volume, delta=1e-8)
        self.assertEqual(shared.dumps(), before)

    def test_named_zone_repeated_across_guards_in_one_output_registry(self):
        from pyg4ometry import geant4
        zone = self.box()
        registry = geant4.Registry()
        names = []
        for _ in range(2):
            with halfspace_bounds_guard({}):
                solid = zone.geant4Solid(registry)
                names.append(solid.name)
                self.assertAlmostEqual(abs(solid.mesh().volume()), 8, delta=1e-8)
        self.assertNotEqual(*names)

    def test_thin_region_no_tolerance_collapse(self):
        zone = self.f.Zone("thin")
        zone.addIntersection(self.f.RPP("finite", 0, 1, 0, 1, 0, 1))
        zone.addIntersection(self.f.YZP("thin_upper", 1e-8))
        bounds, _ = certified_zone_bounds(zone)
        self.assertGreater(bounds[1][0], 1e-8)
        self.assertLess(bounds[0][0], 0)
        with halfspace_bounds_guard({}):
            self.assertAlmostEqual(self.volume(zone), 1e-8, delta=1e-12)

    def test_contradiction_and_unbounded_context_fail_closed(self):
        zone = self.f.Zone("unbounded")
        zone.addIntersection(self.f.YZP("only", 0))
        with self.assertRaises(UnboundedHalfspaceError):
            certified_zone_bounds(zone)
        with halfspace_bounds_guard({}), self.assertRaises(UnboundedHalfspaceError):
            zone.mesh()
        zone = self.box()
        zone.addSubtraction(self.f.YZP("contradiction", 2))
        with self.assertRaises(HalfspaceBoundsError):
            certified_zone_bounds(zone)

    def test_guard_restoration_and_length_safety(self):
        from pyg4ometry.fluka.body import _HalfSpaceMixin
        from pyg4ometry.fluka.region import Zone
        methods = [Zone.mesh, Zone.geant4Solid, Zone.centre, _HalfSpaceMixin._boxFullSize]
        zone = self.box()
        bigger, smaller = self.f.FlukaRegistry(), self.f.FlukaRegistry()
        for body in zone.bodies():
            bigger.addBody(body.safetyExpanded())
            smaller.addBody(body.safetyShrunk())
        shrunk = zone.withLengthSafety(bigger, smaller, True)
        with self.assertRaisesRegex(RuntimeError, "fixture"):
            with halfspace_bounds_guard({}):
                self.assertAlmostEqual(self.volume(shrunk), (2-2e-6)**3, delta=1e-8)
                raise RuntimeError("fixture")
        self.assertEqual(methods, [Zone.mesh, Zone.geant4Solid, Zone.centre, _HalfSpaceMixin._boxFullSize])

    def test_seven_body_oblique_window_regression(self):
        from pyg4ometry.fluka import body as body_module
        from pyg4ometry.fluka.directive import Transform
        transform = Transform(translation=[3500, -30000, 20000])
        zone = self.f.Zone("generic_oblique_window")
        zone.addIntersection(self.f.YZP("upper_x", 5.2, transform=transform))
        zone.addIntersection(self.f.PLA("upper_y", [-.4383711467891, .89879404629916, 0],
                                       [3.5703312757423, 20.179736069635, 0], transform=transform))
        zone.addSubtraction(self.f.PLA("lower_z", [0, -.7071067811866, -.7071067811866],
                                      [0, -1.75, -11.25], transform=transform))
        zone.addSubtraction(self.f.YZP("lower_x", 2.2, transform=transform))
        zone.addSubtraction(self.f.PLA("lower_y", [.43837114678908, .89879404629916, 0],
                                      [-18.09996444845, -9.610426641082, 0], transform=transform))
        zone.addSubtraction(self.f.PLA("upper_z", [-.2588190451025, 0, .96592582628907],
                                      [.8514141515541, 0, -38.17752087189], transform=transform))
        zone.addSubtraction(self.f.RPP("finite_cut", 2.2, 21.2, -10, 10, -67, -32,
                                      transform=transform))
        expected, _ = certified_zone_bounds(zone)
        np.testing.assert_allclose(expected,
            [[3502.2, -30020.97457861502, 19962.183831617127],
             [3505.2, -29979.025421384977, 20007.974578615016]], atol=1e-8, rtol=0)
        seen = []
        for infinity in (50_000_000, 100_000_000):
            with body_module.infinity(infinity), halfspace_bounds_guard({}):
                vertices = np.asarray(zone.mesh().toVerticesAndPolygons()[0])
                seen.append([vertices.min(axis=0), vertices.max(axis=0)])
        np.testing.assert_allclose(seen[0], expected, atol=1e-7, rtol=0)
        np.testing.assert_allclose(seen[1], seen[0], atol=1e-10, rtol=0)


if __name__ == "__main__":
    unittest.main()
