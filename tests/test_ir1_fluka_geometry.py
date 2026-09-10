#!/usr/bin/env python3

import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest


REPOSITORY = Path(__file__).resolve().parents[1]
SCRIPTS = REPOSITORY / "scripts"
MODEL = REPOSITORY / "models" / "lss5_ir1_atlas_proxy"
sys.path.insert(0, str(SCRIPTS))

from ir1_fluka_geometry import (  # noqa: E402
    _axis_aligned_plane_box_bounds,
    audit_omitted_region_geometry,
    cached_raw_preflight,
    ProxyModelError,
    extract_and_write_field_manifest,
    extract_field_assignments,
    install_exact_half_space_preservation,
    install_axis_aligned_plane_box_lowering,
    _install_raw_zone_aabb_fallback,
    normalized_deck,
    summarize_region_coverage,
    summarize_preflight_omissions,
    validate_field_assets,
    verify_source_bundle,
)


class _Plane:
    def __init__(self, normal, point, name="plane"):
        self._normal = normal
        self._point = point
        self.name = name

    def toPlane(self):
        return self._normal, self._point


class _Operation:
    def __init__(self, body):
        self.body = body


class _Zone:
    def __init__(self, intersections, subtractions):
        self.intersections = [_Operation(body) for body in intersections]
        self.subtractions = [_Operation(body) for body in subtractions]

    def bodies(self):
        return [item.body for item in self.intersections + self.subtractions]


class _Region:
    def __init__(self, bodies):
        self._bodies = bodies

    def bodies(self):
        return self._bodies


class _Registry:
    def __init__(self, regions):
        self.regionDict = regions


class Ir1FlukaGeometryTest(unittest.TestCase):
    def test_raw_cache_reuses_only_matching_complete_intact_audits(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cache.json"
            identity = {"source": "first", "version": "one"}
            result = {"evaluation_errors": [], "conversion_candidate_regions": ["region"]}
            calls = []

            def evaluate():
                calls.append(True)
                return result

            self.assertEqual(cached_raw_preflight(path, identity, evaluate), result)
            self.assertEqual(cached_raw_preflight(path, identity, evaluate), result)
            self.assertEqual(len(calls), 1)
            with self.assertRaises(ProxyModelError):
                cached_raw_preflight(path, {"source": "changed"}, evaluate)
            payload = json.loads(path.read_text())
            payload["result"]["conversion_candidate_regions"] = []
            path.write_text(json.dumps(payload))
            with self.assertRaises(ProxyModelError):
                cached_raw_preflight(path, identity, evaluate)
            self.assertEqual(len(calls), 1)

    def test_raw_cache_does_not_publish_failed_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cache.json"
            result = {"evaluation_errors": [{"name": "region", "error": "timeout"}]}
            self.assertEqual(cached_raw_preflight(path, {}, lambda: result), result)
            self.assertFalse(path.exists())

    def test_exact_half_space_preservation_wraps_lossy_pruning(self):
        lower, upper = _Plane([0, 0, 1], [0, 0, 3], "lower"), _Plane(
            [0, 0, 1], [0, 0, 8], "upper"
        )
        source = _Registry({"Region": _Region([lower, upper])})
        converter = SimpleNamespace(
            _filterHalfSpaces=lambda registry, bounds: _Registry(
                {"Region": _Region([upper])}
            )
        )
        report = []
        original = install_exact_half_space_preservation(converter, report)
        try:
            result = converter._filterHalfSpaces(source, {"Region": object()})
        finally:
            converter._filterHalfSpaces = original
        self.assertEqual(result.regionDict["Region"].bodies()[0]._point, [0, 0, 3])
        self.assertIsNot(result, source)
        self.assertEqual(
            report, [{"region": "Region", "preserved_planes": [lower.name]}]
        )

    def test_shared_plane_pruning_is_preserved_and_lowered(self):
        try:
            from pyg4ometry import fluka
            import importlib

            converter = importlib.import_module("pyg4ometry.convert.fluka2Geant4")
        except ImportError:
            self.skipTest("pyg4ometry is required for the converter integration test")

        registry = fluka.FlukaRegistry()
        shared = fluka.XYP("shared", 0.0, flukaregistry=registry)
        box_a = fluka.RPP(
            "box_a", -1, 1, -1, 1, 10, 20, flukaregistry=registry
        )
        box_b = fluka.RPP(
            "box_b", -1, 1, -1, 1, -1, 1, flukaregistry=registry
        )
        zone_a = fluka.Zone("zone_a")
        zone_a.addIntersection(box_a)
        zone_a.addSubtraction(shared)
        zone_b = fluka.Zone("zone_b")
        zone_b.addIntersection(box_b)
        zone_b.addSubtraction(shared)
        region_a = fluka.Region("A")
        region_a.addZone(zone_a)
        region_b = fluka.Region("B")
        region_b.addZone(zone_b)
        registry.addRegion(region_a)
        registry.addRegion(region_b)
        region_zone_aabbs = {
            "A": [fluka.AABB([-1, -1, 10], [1, 1, 20])],
            "B": [fluka.AABB([-1, -1, 0], [1, 1, 1])],
        }

        baseline = converter._filterHalfSpaces(registry, region_zone_aabbs)
        self.assertNotIn(
            "shared", {body.name for body in baseline.regionDict["A"].bodies()}
        )
        self.assertIn(
            "shared", {body.name for body in baseline.regionDict["B"].bodies()}
        )

        preserved = []
        lowered = []
        original_preservation = install_exact_half_space_preservation(
            converter, preserved
        )
        original_lowering = install_axis_aligned_plane_box_lowering(
            converter, lowered, preserved
        )
        try:
            repaired = converter._filterHalfSpaces(registry, region_zone_aabbs)
        finally:
            converter._filterHalfSpaces = original_lowering
            converter._filterHalfSpaces = original_preservation

        self.assertEqual(
            preserved, [{"region": "A", "preserved_planes": ["shared"]}]
        )
        self.assertEqual(len(lowered), 1)
        self.assertEqual(lowered[0]["region"], "A")
        self.assertEqual(
            lowered[0]["bounds_mm"],
            [[-10000.0, -10000.0, 0.0], [10000.0, 10000.0, 10000.0]],
        )
        repaired_a = {body.name for body in repaired.regionDict["A"].bodies()}
        repaired_b = {body.name for body in repaired.regionDict["B"].bodies()}
        self.assertIn("A_zone0_axis_box", repaired_a)
        self.assertNotIn("shared", repaired_a)
        self.assertIn("shared", repaired_b)

    def test_axis_aligned_plane_box_bounds_uses_tightest_exact_planes(self):
        zone = _Zone(
            intersections=[
                _Plane([1, 0, 0], [4, 0, 0]),
                _Plane([0, 1, 0], [0, 6, 0]),
                _Plane([0, 0, 1], [0, 0, 8]),
                _Plane([0, 0, 1], [0, 0, 80]),
            ],
            subtractions=[
                _Plane([1, 0, 0], [1, 0, 0]),
                _Plane([0, 1, 0], [0, 2, 0]),
                _Plane([0, 0, 1], [0, 0, 3]),
                _Plane([0, 0, 1], [0, 0, -30]),
            ],
        )
        self.assertEqual(
            _axis_aligned_plane_box_bounds(zone),
            [[1.0, 2.0, 3.0], [4.0, 6.0, 8.0]],
        )

    def test_axis_aligned_plane_box_bounds_rejects_non_rectangular_zones(self):
        oblique = _Zone(
            intersections=[_Plane([1, 1, 0], [1, 1, 0])],
            subtractions=[],
        )
        unbounded = _Zone(
            intersections=[_Plane([1, 0, 0], [1, 0, 0])],
            subtractions=[],
        )
        self.assertIsNone(_axis_aligned_plane_box_bounds(oblique))
        self.assertIsNone(_axis_aligned_plane_box_bounds(unbounded))
        invalid = _Zone(
            intersections=[_Plane([0, 0, 0], [0, 0, 0])],
            subtractions=[],
        )
        self.assertIsNone(_axis_aligned_plane_box_bounds(invalid))

    def test_raw_zone_aabb_fallback_replaces_only_independently_non_null_zones(self):
        converter = SimpleNamespace()
        retained = object()
        converter._getRegionZoneAABBs = lambda registry, regions, quadrics: {
            "Recovered": [None, retained],
            "Untouched": [None],
        }
        preflight = {
            "secondary_classification": {
                "non_null_regions": ["Recovered"],
                "zone_bounds_mm": {
                    "Recovered": [
                        [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]],
                        None,
                    ],
                },
            },
        }
        original, details = _install_raw_zone_aabb_fallback(
            converter, preflight, padding_mm=0.25
        )
        try:
            result = converter._getRegionZoneAABBs(
                object(), ["Recovered", "Untouched"], {}
            )
        finally:
            converter._getRegionZoneAABBs = original
        recovered = result["Recovered"][0]
        self.assertEqual(list(recovered.lower), [0.75, 1.75, 2.75])
        self.assertEqual(list(recovered.upper), [4.25, 5.25, 6.25])
        self.assertIs(result["Recovered"][1], retained)
        self.assertIsNone(result["Untouched"][0])
        self.assertEqual(
            details, [{"name": "Recovered", "replaced_zone_count": 1}]
        )

    def test_frozen_source_checksums(self):
        observed = verify_source_bundle(MODEL)
        self.assertEqual(len(observed), 8)
        self.assertIn("source/lhc_ir1_exp_b2.inp", observed)

    def test_field_assignments_and_assets_are_complete(self):
        assignments = extract_field_assignments(MODEL / "source" / "lhc_ir1_exp_b2.inp")
        validate_field_assets(MODEL, assignments)
        self.assertEqual(len(assignments), 29)
        self.assertEqual(assignments[0].field_type, "MQXA")
        self.assertEqual(assignments[0].region_from, 1)
        self.assertTrue(any(item.field_type == "CONST" for item in assignments))
        self.assertTrue(any(item.field_type == "MBXW" for item in assignments))

    def test_field_manifest_keeps_cmssw_transform_unresolved(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "fields.json"
            assignments = extract_and_write_field_manifest(MODEL, output)
            payload = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(payload["model_status"], "provisional-ir1-atlas-proxy")
        self.assertIsNone(payload["coordinate_transform_to_cms"])
        self.assertEqual(len(payload["assignments"]), 29)

    def test_omitted_region_audit_separates_null_and_unevaluable_regions(self):
        class Mesh:
            def __init__(self, is_null):
                self._is_null = is_null

            def isNull(self):
                return self._is_null

            def vertexCount(self):
                return 0 if self._is_null else 8

            def polygonCount(self):
                return 0 if self._is_null else 12

            def volume(self):
                return 0.0 if self._is_null else 1.0

        class Region:
            zones = [object()]

            def __init__(self, mesh=None, error=None):
                self._mesh = mesh
                self._error = error

            def mesh(self):
                if self._error:
                    raise self._error
                return self._mesh

            def dumps(self):
                return "+body"

        class Registry:
            regionDict = {
                "Null": Region(Mesh(True)),
                "NonNull": Region(Mesh(False)),
                "Error": Region(error=RuntimeError("bad mesh")),
            }

        audit = audit_omitted_region_geometry(
            Registry(), ["Null", "NonNull", "Error"]
        )
        self.assertEqual(audit["source_null_regions"], ["Null"])
        self.assertEqual(audit["unexpected_omitted_regions"], ["NonNull", "Error"])
        self.assertEqual(audit["details"][2]["evaluation_error"], "RuntimeError: bad mesh")

    def test_selected_region_coverage_does_not_call_unselected_regions_omitted(self):
        coverage = summarize_region_coverage(
            ["A", "B", "C"], ["A", "C"], ["wl", "A_lv"]
        )
        self.assertEqual(coverage["source_region_count"], 3)
        self.assertEqual(coverage["requested_region_count"], 2)
        self.assertEqual(coverage["unselected_region_count"], 1)
        self.assertEqual(coverage["converted_regions"], ["A"])
        self.assertEqual(coverage["omitted_regions"], ["C"])

    def test_preflight_omissions_are_exhaustive_and_reasoned(self):
        coverage = {
            "omitted_regions": ["Blackhole", "Null", "Deferred", "Lost"],
        }
        preflight = {
            "blackhole_regions": ["Blackhole"],
            "source_null_regions": ["Null"],
            "deferred_null_validation_regions": [
                "Deferred",
                "ConvertedDeferred",
            ],
        }
        audit = summarize_preflight_omissions(coverage, preflight)
        self.assertEqual(
            audit["intentionally_omitted_blackhole_regions"], ["Blackhole"]
        )
        self.assertEqual(audit["source_null_regions"], ["Deferred", "Null"])
        self.assertEqual(
            audit["deferred_source_null_regions"], ["Deferred"]
        )
        self.assertEqual(
            audit["deferred_region_conversion_failures"],
            ["ConvertedDeferred"],
        )
        self.assertEqual(audit["unexpected_omitted_regions"], ["Lost"])
        self.assertEqual(
            [item["reason"] for item in audit["details"]],
            [
                "blackhole",
                "confirmed_source_null",
                "deferred_source_null",
                "unexpected",
            ],
        )

    def test_normalization_removes_only_known_empty_compound_card(self):
        source = MODEL / "source" / "lhc_ir1_exp_b2.inp"
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "normalized.inp"
            normalization = normalized_deck(source, output)
            normalized_text = output.read_text(encoding="ascii")
        removed = normalization["removed_noop_cards"]
        replacements = normalization["fortran_exponent_replacements"]
        self.assertEqual(len(removed), 2)
        self.assertTrue(all("BTresin" in item["text"] for item in removed))
        self.assertTrue(any("D-4" in item["original"] for item in replacements))
        self.assertNotIn("COMPOUND                                                              BTresin", normalized_text)
        self.assertNotIn("-2.106D-4", normalized_text)


if __name__ == "__main__":
    unittest.main()
