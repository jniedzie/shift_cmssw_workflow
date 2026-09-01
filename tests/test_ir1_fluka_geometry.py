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
    audit_omitted_region_geometry,
    extract_and_write_field_manifest,
    extract_field_assignments,
    _install_raw_zone_aabb_fallback,
    normalized_deck,
    summarize_region_coverage,
    summarize_preflight_omissions,
    validate_field_assets,
    verify_source_bundle,
)


class Ir1FlukaGeometryTest(unittest.TestCase):
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
