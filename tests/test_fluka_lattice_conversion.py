import importlib
import importlib.util
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


@unittest.skipUnless(importlib.util.find_spec("pyg4ometry"), "pyg4ometry not installed")
class GenericLatticeTest(unittest.TestCase):
    def setUp(self):
        import numpy as np
        from pyg4ometry import fluka
        from fluka_lattice_conversion import lattice_conversion_guard, LatticeConversionError
        self.np, self.fluka = np, fluka
        self.guard, self.error = lattice_conversion_guard, LatticeConversionError
        self.converter = importlib.import_module("pyg4ometry.convert.fluka2Geant4")

    def region(self, registry, name, body, subtraction=None):
        zone = self.fluka.Zone()
        zone.addIntersection(body)
        if subtraction is not None:
            zone.addSubtraction(subtraction)
        region = self.fluka.Region(name)
        region.addZone(zone)
        registry.addRegion(region)
        registry.assignma("IRON", name)
        return region

    def fixture(self, *, multiple=False, rotated=False):
        fluka = self.fluka
        registry = fluka.FlukaRegistry()
        proto_transform = (fluka.Transform(rotoTranslation=fluka.RotoTranslation(
            "protoRot", axis="z", azimuth=23, translation=[1, 2, 0])) if rotated else None)
        first = fluka.RPP("protoBody", 1, 3, -1, 1, -1, 1, transform=proto_transform, flukaregistry=registry)
        self.region(registry, "FIRST", first)
        if multiple:
            second = fluka.RPP("secondBody", 8, 15, -2, 2, -1, 1, flukaregistry=registry)
            self.region(registry, "SECOND", second)
        transform = fluka.RotoTranslation("toProto", translation=[-100, -200, -30],
                                         axis="z" if rotated else None, azimuth=37 if rotated else 0)
        cell_body = fluka.RPP("cellBody", -10, 10, -10, 10, -10, 10,
                             transform=fluka.Transform(rotoTranslation=transform, invertRotoTranslation=True),
                             flukaregistry=registry)
        zone = fluka.Zone()
        zone.addIntersection(cell_body)
        cell = fluka.Region("CELL")
        cell.addZone(zone)
        lattice = fluka.Lattice(cell, transform, flukaregistry=registry)
        return registry, lattice

    def convert(self, registry, *, report=None, regions=None, raw_preflight=None):
        from convert_ir1_fluka_geometry_full import full_conversion_guards
        report = {} if report is None else report
        with full_conversion_guards((2000, 2000, 2000), performance_report={}):
            with self.guard(report, source_registry=registry, raw_preflight=raw_preflight):
                converted = self.converter.fluka2Geant4(registry, regions=regions,
                    worldDimensions=[2000, 2000, 2000], withLengthSafety=False)
        return converted, report

    def exclusion_fixture(self):
        from fluka_region_preflight import classify_raw_regions, resolve_raw_region_classifications
        registry, _ = self.fixture()
        null_body = self.fluka.RPP("nullbody", -3, 3, -3, 3, -3, 3, flukaregistry=registry)
        self.region(registry, "EMPTY", null_body, null_body)
        black_body = self.fluka.RPP("blackbody", -5, 5, -5, 5, -5, 5, flukaregistry=registry)
        self.region(registry, "ABSORBER", black_body)
        registry.assignma("BLCKHOLE", "ABSORBER")
        names = list(registry.regionDict)
        primary = classify_raw_regions(registry, names, timeout_seconds=5, include_bounds=True)
        # This tiny exact same-box subtraction is null independently of CSG
        # backend. Backend cross-check semantics, not an actual backend switch,
        # are the target of this unit fixture; complete-deck pilots do both.
        secondary = classify_raw_regions(registry, primary["source_null_regions"], timeout_seconds=5,
                                         include_bounds=True)
        secondary["backend"] = "pycsg"
        report = resolve_raw_region_classifications(primary, secondary, names)
        return registry, report

    def test_validated_null_and_exact_blackhole_exclusions_are_explicit(self):
        registry, preflight = self.exclusion_fixture()
        converted, report = self.convert(registry, regions=["FIRST"], raw_preflight=preflight)
        self.assertEqual(report["placement_count"], 1)
        self.assertIn("CELL__FIRST_lattice_pv", converted.physicalVolumeDict)
        excluded = {entry["name"]: entry for entry in report["lattices"][0]["preclassification_excluded_candidates"]}
        self.assertEqual(set(excluded), {"EMPTY", "ABSORBER"})
        self.assertEqual(excluded["EMPTY"]["reason"], "confirmed_two_backend_source_null")
        self.assertTrue(excluded["ABSORBER"]["absorbing_not_empty"])
        self.assertFalse(excluded["ABSORBER"]["transport_equivalence_validated"])
        provenance = report["candidate_exclusion_validation"]
        self.assertTrue(provenance["complete_source_partition_validated"])
        self.assertEqual(len(provenance["raw_preflight_report_sha256"]), 64)

    def test_without_report_null_and_blackhole_candidates_are_not_excluded(self):
        registry, _ = self.exclusion_fixture()
        with self.assertRaisesRegex(self.error, "unconverted possible prototypes"):
            self.convert(registry, regions=["FIRST"])

    def test_exclusions_require_complete_source_registry_not_selected_subset(self):
        from fluka_lattice_conversion import validated_lattice_exclusions
        registry, preflight = self.exclusion_fixture()
        with self.assertRaisesRegex(self.error, "explicit complete source_registry"):
            with self.guard({}, raw_preflight=preflight):
                pass
        del registry.regionDict["EMPTY"]
        with self.assertRaisesRegex(self.error, "exactly partition"):
            validated_lattice_exclusions(registry, preflight)

    def test_top_level_lists_cannot_forge_nonempty_source_null_exclusions(self):
        from fluka_lattice_conversion import validated_lattice_exclusions
        registry, valid = self.exclusion_fixture()
        forged = deepcopy(valid)
        forged["source_null_regions"].append("FIRST")
        forged["source_null_region_count"] += 1
        forged["non_null_regions"].remove("FIRST")
        forged["non_null_region_count"] -= 1
        forged["conversion_candidate_regions"].remove("FIRST")
        forged["conversion_candidate_region_count"] -= 1
        with self.assertRaisesRegex(self.error, "inconsistent with its two backends"):
            validated_lattice_exclusions(registry, forged)

    def test_blackhole_exclusion_requires_exact_material_assignment(self):
        from fluka_lattice_conversion import validated_lattice_exclusions
        registry, report = self.exclusion_fixture()
        registry.assignma("IRON", "ABSORBER")
        with self.assertRaisesRegex(self.error, "exact source BLCKHOLE assignments"):
            validated_lattice_exclusions(registry, report)

    def test_unknown_duplicate_mismatched_backend_and_ambiguous_reports_rejected(self):
        from fluka_lattice_conversion import validated_lattice_exclusions
        registry, valid = self.exclusion_fixture()
        mutations = [
            lambda r: r["primary_classification"]["source_null_regions"].append("UNKNOWN"),
            lambda r: r["primary_classification"]["source_null_regions"].append("EMPTY"),
            lambda r: r["secondary_classification"]["source_null_regions"].clear(),
            lambda r: r["primary_classification"]["evaluation_errors"].append({"name": "EMPTY", "error": "failed"}),
            lambda r: r["secondary_classification"].update(backend="cgal_sm"),
            lambda r: r.update(deferred_null_validation_regions=["EMPTY"], deferred_null_validation_region_count=1),
            lambda r: r.update(requested_region_count=1),
            lambda r: r["primary_classification"].update(requested_region_count=3.0),
        ]
        for index, mutation in enumerate(mutations):
            with self.subTest(index=index):
                report = deepcopy(valid)
                mutation(report)
                with self.assertRaises(self.error):
                    validated_lattice_exclusions(registry, report)

    def test_backend_disagreement_retains_region_and_cannot_authorize_exclusion(self):
        from fluka_lattice_conversion import validated_lattice_exclusions
        from fluka_region_preflight import resolve_raw_region_classifications
        registry, preflight = self.exclusion_fixture()
        primary = deepcopy(preflight["primary_classification"])
        secondary = deepcopy(preflight["secondary_classification"])
        secondary.update(source_null_regions=[], source_null_region_count=0,
                         non_null_regions=["EMPTY"], non_null_region_count=1)
        resolved = resolve_raw_region_classifications(primary, secondary, list(registry.regionDict))
        exclusions, provenance = validated_lattice_exclusions(registry, resolved)
        self.assertNotIn("EMPTY", exclusions)
        self.assertEqual(provenance["confirmed_source_null_count"], 0)

    def test_enclosed_prototype_is_discovered_and_retains_offset(self):
        registry, _ = self.fixture()
        with self.guard({}):
            contents = self.converter._getContentsOfLatticeCells(registry, {})
        self.assertEqual(contents, {"CELL": ["FIRST"]})
        converted, report = self.convert(registry)
        self.assertTrue(report["passed"])
        self.assertFalse(report["production_ready"])
        pv = converted.physicalVolumeDict["CELL__FIRST_lattice_pv"]
        self.np.testing.assert_allclose(pv.position.eval(), [102, 200, 30])
        self.assertAlmostEqual(abs(pv.logicalVolume.solid.mesh().volume()), 8)

    def test_multiple_prototypes_and_partial_cell_clipping(self):
        registry, _ = self.fixture(multiple=True)
        converted, report = self.convert(registry)
        self.assertEqual(report["placement_count"], 2)
        second = converted.physicalVolumeDict["CELL__SECOND_lattice_pv"]
        self.assertEqual(second.logicalVolume.solid.type, "Intersection")
        self.assertAlmostEqual(abs(second.logicalVolume.solid.mesh().volume()), 16)
        self.assertFalse(report["source_bound_clipping"])

    def test_full_inverse_rotation_translation_and_prototype_rotation(self):
        from pyg4ometry import transformation
        registry, lattice = self.fixture(rotated=True)
        converted, report = self.convert(registry)
        original = converted.physicalVolumeDict["FIRST_pv"]
        expected = self.np.identity(4)
        expected[:3, :3] = transformation.tbxyz2matrix(transformation.reverse(original.rotation.eval()))
        expected[:3, 3] = original.position.eval()
        expected = self.np.linalg.inv(lattice.getTransform().to4DMatrix()) @ expected
        actual = report["lattices"][0]["placements"][0]["prototype_to_physical_matrix"]
        self.np.testing.assert_allclose(actual, expected, atol=1e-12)
        self.assertAlmostEqual(abs(converted.physicalVolumeDict["CELL__FIRST_lattice_pv"].logicalVolume.solid.mesh().volume()), 8)

    def test_safe_disjoint_candidate_rejection_and_empty_cell_failure(self):
        from fluka_lattice_conversion import conservative_lattice_candidates
        registry, lattice = self.fixture()
        lattice.rotoTranslation = self.fluka.RotoTranslation("missing", translation=[-10000, 0, 0])
        self.assertEqual(conservative_lattice_candidates(registry), {"CELL": []})
        with self.assertRaisesRegex(self.error, "no possible source prototypes"):
            self.convert(registry)

    def test_refined_disjoint_proof_removes_broad_false_candidate(self):
        from fluka_lattice_conversion import conservative_lattice_candidates
        registry, _ = self.fixture()
        self.assertEqual(conservative_lattice_candidates(registry), {"CELL": ["FIRST"]})
        report = {}

        def certified_far_away(_region):
            return (self.np.array([10000., 10000., 10000.]),
                    self.np.array([10001., 10001., 10001.]))

        refined = conservative_lattice_candidates(
            registry,
            refinement_bounds_provider=certified_far_away,
            refinement_report=report,
        )
        self.assertEqual(refined, {"CELL": []})
        item = report["lattices"]["CELL"]
        self.assertEqual(item["certified_disjoint_count"], 1)
        self.assertEqual(item["certified_disjoint_candidates"][0]["name"], "FIRST")
        self.assertTrue(report["strict_disjoint_bounds_only"])

    def test_failed_refinement_retains_candidate(self):
        from fluka_lattice_conversion import conservative_lattice_candidates
        registry, _ = self.fixture()
        report = {}

        def unresolved(_region):
            raise ValueError("no finite certificate")

        refined = conservative_lattice_candidates(
            registry,
            refinement_bounds_provider=unresolved,
            refinement_report=report,
        )
        self.assertEqual(refined, {"CELL": ["FIRST"]})
        item = report["lattices"]["CELL"]
        self.assertEqual(item["unresolved_refinement_count"], 1)
        self.assertEqual(item["unresolved_refinements"][0]["name"], "FIRST")

    def test_missing_selected_prototype_is_not_silently_omitted(self):
        registry, _ = self.fixture(multiple=True)
        report = {}
        with self.assertRaisesRegex(self.error, "unconverted possible prototypes"):
            self.convert(registry, report=report, regions=["FIRST"])
        self.assertEqual(report["lattices"][0]["missing_converted_candidates"], ["SECOND"])

    def test_ambiguous_empty_boolean_is_fail_closed_not_a_missing_placement(self):
        registry, _ = self.fixture()
        # The conservative prototype bound overlaps the cell, but the actual
        # prototype is the outer box minus a box enclosing the complete cell.
        registry.regionDict.clear()
        outer = self.fluka.RPP("outer", -20, 20, -20, 20, -20, 20, flukaregistry=registry)
        inner = self.fluka.RPP("inner", -11, 11, -11, 11, -11, 11, flukaregistry=registry)
        self.region(registry, "SHELL", outer, inner)
        report = {}
        with self.assertRaisesRegex(self.error, "cannot omit a potentially non-empty analytic solid"):
            self.convert(registry, report=report)
        self.assertIn("unresolved_intersection", report["lattices"][0]["placements"][0])
        self.assertFalse(report["passed"])

    def test_stable_names_and_guard_restoration_after_success_and_exception(self):
        original = self.converter._convertLatticeCells
        original_contents = self.converter._getContentsOfLatticeCells
        names = []
        for _ in range(2):
            converted, _ = self.convert(self.fixture(multiple=True)[0])
            names.append(sorted(converted.physicalVolumeDict))
        self.assertEqual(*names)
        self.assertIs(self.converter._convertLatticeCells, original)
        with self.assertRaisesRegex(RuntimeError, "deliberate"):
            with self.guard({}):
                raise RuntimeError("deliberate")
        self.assertIs(self.converter._convertLatticeCells, original)
        self.assertIs(self.converter._getContentsOfLatticeCells, original_contents)

    def test_upstream_cannot_swallow_unbound_local_evaluation_error(self):
        from convert_ir1_fluka_geometry_full import full_conversion_guards
        registry, _ = self.fixture()
        report = {}

        def failing_bounds(cell):
            raise UnboundLocalError("injected cell-bound evaluation failure")

        with full_conversion_guards((2000, 2000, 2000), performance_report={}):
            with self.guard(report, source_registry=registry, cell_bounds_provider=failing_bounds):
                with self.assertRaisesRegex(self.error, "refusing upstream silent omission"):
                    self.converter.fluka2Geant4(registry, worldDimensions=[2000, 2000, 2000],
                                               withLengthSafety=False)
        self.assertFalse(report["passed"])
        self.assertIn("injected cell-bound evaluation failure", report["evaluation_error"])
        self.assertFalse(report["lattices"][0]["passed"])

    def test_curved_analytic_bounds_do_not_use_mesh_vertices(self):
        from fluka_analytic_bounds import body_bounds
        sphere = self.fluka.SPH("sphere", [7, 8, 9], 2)
        sphere.mesh = lambda *args, **kwargs: self.fail("mesh must not be consulted")
        lower, upper = body_bounds(sphere)
        self.assertTrue(self.np.all(lower <= [5, 6, 7]))
        self.assertTrue(self.np.all(upper >= [9, 10, 11]))
        ellipse = self.fluka.REC("ellipse", [4, 5, 6], [0, 0, 10], [3, 4, 0], [-8, 6, 0])
        lower, upper = body_bounds(ellipse)
        self.assertLessEqual(lower[0], 4 - self.np.sqrt(73))
        self.assertGreaterEqual(upper[1], 5 + self.np.sqrt(52))

    def test_bounds_large_cancellation_and_rotated_primitives_are_outward(self):
        from decimal import Decimal, localcontext
        from itertools import product
        from fluka_analytic_bounds import body_bounds, transform_bounds
        np = self.np
        matrix = np.array([[.6, -.8, 0, 2e15], [.8, .6, 0, -14e15], [0, 0, 1, -1e16], [0, 0, 0, 1.]])
        transform = SimpleNamespace(to4DMatrix=lambda: matrix)
        body = self.fluka.RPP("huge", 1e16, 1e16 + 10, 1e16, 1e16 + 20, 1e16, 1e16 + 30,
                              transform=transform)
        for function in (lambda: body_bounds(body), lambda: transform_bounds((body.lower, body.upper), matrix)):
            bounds = function()
            with localcontext() as context:
                context.prec = 80
                for point in product(*zip(body.lower, body.upper)):
                    for axis in range(3):
                        exact = sum(Decimal(float(matrix[axis, k])) * Decimal(float(point[k])) for k in range(3)) + Decimal(float(matrix[axis, 3]))
                        self.assertLessEqual(Decimal(float(bounds[0][axis])), exact)
                        self.assertGreaterEqual(Decimal(float(bounds[1][axis])), exact)

    def test_unknown_bounds_stay_unknown_and_malformed_transform_fails(self):
        from fluka_analytic_bounds import body_bounds, transform_bounds
        lower, upper = body_bounds(SimpleNamespace(name="unknown"))
        self.assertTrue(self.np.isneginf(lower).all())
        self.assertTrue(self.np.isposinf(upper).all())
        with self.assertRaises(ValueError):
            transform_bounds((lower, upper), self.np.identity(3))

    def test_curved_bounds_enclose_transformed_surfaces(self):
        from fluka_analytic_bounds import body_bounds
        np, fluka = self.np, self.fluka
        transform = fluka.Transform(expansion=3, rotoTranslation=fluka.RotoTranslation(
            "boundsRot", axis="z", azimuth=31, translation=[13, -17, 29]))
        bodies = [
            (fluka.RCC("cylinder", [2, 3, 4], [0, 0, 8], 5, transform=transform),
             lambda a, t: [2 + 5 * np.cos(a), 3 + 5 * np.sin(a), 4 + 8 * t]),
            (fluka.REC("ellipse", [2, 3, 4], [0, 0, 8], [3, 4, 0], [-8, 6, 0], transform=transform),
             lambda a, t: np.array([2, 3, 4 + 8 * t]) + np.cos(a) * np.array([3, 4, 0]) + np.sin(a) * np.array([-8, 6, 0])),
            (fluka.TRC("cone", [2, 3, 4], [0, 0, 8], 5, 2, transform=transform),
             lambda a, t: [2 + (5 - 3 * t) * np.cos(a), 3 + (5 - 3 * t) * np.sin(a), 4 + 8 * t]),
        ]
        for body, point in bodies:
            lower, upper = body_bounds(body)
            for angle in np.linspace(0, 2 * np.pi, 97):
                for fraction in (0, .2, .7, 1):
                    transformed = transform.leftMultiplyVector(point(angle, fraction))
                    self.assertTrue(np.all(lower <= transformed))
                    self.assertTrue(np.all(upper >= transformed))

    def test_subtractions_do_not_shrink_bounds_and_unions_cover_all_zones(self):
        from fluka_analytic_bounds import region_bounds
        registry = self.fluka.FlukaRegistry()
        outer = self.fluka.RPP("outer", -20, 20, -10, 10, -5, 5, flukaregistry=registry)
        subtract = self.fluka.RPP("subtract", 0, 20, -10, 10, -5, 5, flukaregistry=registry)
        region = self.region(registry, "REGION", outer, subtract)
        second = self.fluka.Zone()
        second.addIntersection(self.fluka.RPP("other", 50, 60, -1, 1, -1, 1, flukaregistry=registry))
        region.addZone(second)
        bounds = region_bounds(region)
        self.assertTrue(self.np.all(bounds[0] <= [-20, -10, -5]))
        self.assertTrue(self.np.all(bounds[1] >= [60, 10, 5]))

    @unittest.skipUnless(shutil.which("root"), "ROOT unavailable")
    def test_independent_root_full_inverse_rotation(self):
        from pyg4ometry import gdml
        registry, lattice = self.fixture(rotated=True)
        converted, _ = self.convert(registry)
        # Expected point comes directly from the source body and inverse FLUKA
        # matrix, independently of the GDML placements or their recorded matrix.
        body_matrix = registry.bodyDict["protoBody"].transform.to4DMatrix()
        physical = self.np.linalg.inv(lattice.getTransform().to4DMatrix()) @ body_matrix
        inside = physical @ self.np.array([2.8, .8, .7, 1.])
        outside = physical @ self.np.array([3.5, .8, .7, 1.])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rotated.gdml"
            writer = gdml.Writer()
            writer.addDetector(converted)
            writer.write(str(path))
            coordinates = lambda point: ",".join(format(value, ".17g") for value in point[:3])
            code = ('TGeoManager::SetDefaultUnits(TGeoManager::kG4Units); '
                    f'auto g=TGeoManager::Import({json.dumps(str(path))}); if(!g){{gSystem->Exit(2);}} '
                    f'auto a=g->FindNode({coordinates(inside)}); auto b=g->FindNode({coordinates(outside)}); '
                    'bool ok=a && b && TString(a->GetVolume()->GetName()).Contains("CELL__FIRST") '
                    '&& TString(b->GetVolume()->GetName())=="wl"; gSystem->Exit(ok?0:3);')
            result = subprocess.run([shutil.which("root"), "-l", "-b", "-q", "-e", code],
                                    capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(shutil.which("root"), "ROOT unavailable")
    def test_independent_root_navigation_of_rotation_offset_and_cell_clip(self):
        from pyg4ometry import gdml
        registry, _ = self.fixture(multiple=True)
        converted, _ = self.convert(registry)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lattice.gdml"
            writer = gdml.Writer()
            writer.addDetector(converted)
            writer.write(str(path))
            code = ('TGeoManager::SetDefaultUnits(TGeoManager::kG4Units); '
                    f'auto g=TGeoManager::Import({json.dumps(str(path))}); if(!g){{gSystem->Exit(2);}} '
                    'auto a=g->FindNode(102,200,30); auto b=g->FindNode(109,200,30); '
                    'auto c=g->FindNode(112,200,30); '
                    'bool ok=a && b && c && TString(a->GetVolume()->GetName()).Contains("CELL__FIRST") '
                    '&& TString(b->GetVolume()->GetName()).Contains("CELL__SECOND") '
                    '&& TString(c->GetVolume()->GetName())=="wl"; '
                    'gSystem->Exit(ok?0:3);')
            result = subprocess.run([shutil.which("root"), "-l", "-b", "-q", "-e", code],
                                    capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
