#!/usr/bin/env python3

import json
from pathlib import Path
import sys
import tempfile
import unittest


REPOSITORY = Path(__file__).resolve().parents[1]
SCRIPTS = REPOSITORY / "scripts"
MODEL = REPOSITORY / "models" / "lss5_ir1_atlas_proxy"
sys.path.insert(0, str(SCRIPTS))

from ir1_fluka_geometry import (  # noqa: E402
    extract_and_write_field_manifest,
    extract_field_assignments,
    normalized_deck,
    validate_field_assets,
    verify_source_bundle,
)


class Ir1FlukaGeometryTest(unittest.TestCase):
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
