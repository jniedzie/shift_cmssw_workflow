#!/usr/bin/env python3

from pathlib import Path
import json
import sys
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from fetch_lpc_bunch_mask import FillingSchemeError, normalize_lpc_response  # noqa: E402
from sample_zero_bias_trigger_timeline import (  # noqa: E402
    TriggerLibraryError,
    load_ip5_bunch_mask,
    validate_run_fill_map,
)


CSV_TEXT = """INJECTION SCHEME : test
Collisions at IP1&5 : 2

BEAM 1
RFbucket,Slot,Head-On IP1,Head-On IP2,Head-On IP5
11,1,1,0,1
21,2,0,0,0
31,3,1,0,1

BEAM 2
RFbucket,Slot,Head-On IP1,Head-On IP2,Head-On IP5
11,1,1,0,1
31,3,1,0,1

"""

MODERN_CSV_TEXT = """INJECTION SCHEME : modern_test

Tot number of B1 bunches(probe/Nominal) : 0/3
Tot number of B2 bunches(probe/Nominal) : 0/3

Collisions at IP1&5: 2

HEAD ON COLLISIONS FOR B1
B1 bucket number,IP1,IP2,IP5,IP8
1,1,-,1,-
11,-,-,-,-
35631,35631,-,35631,-

HEAD ON COLLISIONS FOR B2
B2 bucket number,IP1,IP2,IP5,IP8
1,1,-,1,-
11,-,-,-,-
35631,35631,-,35631,-

"""


class LpcBunchMaskTest(unittest.TestCase):
    def test_normalizes_beam_and_ip5_slots(self):
        result = normalize_lpc_response(
            {"fills": {"9999": {"name": "test_scheme", "csv": CSV_TEXT}}},
            9999,
        )
        self.assertEqual(result["beam1_filled_bx_slots"], [1, 2, 3])
        self.assertEqual(result["beam2_filled_bx_slots"], [1, 3])
        self.assertEqual(result["colliding_ip5_bx_slots"], [1, 3])
        self.assertEqual(result["counts"]["colliding_ip5"], 2)
        self.assertEqual(len(result["source"]["csv_sha256"]), 64)

    def test_normalizes_current_lpc_head_on_tables(self):
        result = normalize_lpc_response(
            {"fills": {"9999": {"name": "modern_test", "csv": MODERN_CSV_TEXT}}},
            9999,
        )
        self.assertEqual(result["beam1_filled_bx_slots"], [1, 2, 3564])
        self.assertEqual(result["beam2_filled_bx_slots"], [1, 2, 3564])
        self.assertEqual(result["colliding_ip5_bx_slots"], [1, 3564])
        self.assertEqual(result["counts"]["colliding_ip5"], 2)

    def test_rejects_beam_disagreement(self):
        broken = CSV_TEXT.replace("31,3,1,0,1\n\n", "31,3,1,0,0\n\n", 1)
        with self.assertRaisesRegex(FillingSchemeError, "disagree"):
            normalize_lpc_response(
                {"fills": {"9999": {"name": "test_scheme", "csv": broken}}},
                9999,
            )

    def test_rejects_declared_collision_count_mismatch(self):
        broken = CSV_TEXT.replace("Collisions at IP1&5 : 2", "Collisions at IP1&5 : 3")
        with self.assertRaisesRegex(FillingSchemeError, "declares 3"):
            normalize_lpc_response(
                {"fills": {"9999": {"name": "test_scheme", "csv": broken}}},
                9999,
            )

    def test_maps_relative_bx_through_physical_orbit_with_wrap(self):
        payload = {
            "schema": "cms-lpc-ip5-bunch-mask",
            "schema_version": 1,
            "orbit_slots": 3564,
            "fill_number": 9999,
            "scheme_name": "test",
            "beam1_filled_bx_slots": [1, 3564],
            "beam2_filled_bx_slots": [1],
            "colliding_ip5_bx_slots": [1],
            "source": {"csv_sha256": "0" * 64},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mask.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            relative, provenance = load_ip5_bunch_mask(
                str(path), [-1, 0, 1], 3564, 1
            )
        self.assertEqual(relative, {1})
        self.assertEqual(provenance["reference_bx_slot"], 3564)
        self.assertEqual(provenance["shift_beam"], 1)
        self.assertEqual(len(provenance["file_sha256"]), 64)

    def test_rejects_unfilled_shift_reference_slot(self):
        payload = {
            "schema": "cms-lpc-ip5-bunch-mask", "schema_version": 1,
            "orbit_slots": 3564, "beam1_filled_bx_slots": [1],
            "fill_number": 9999, "scheme_name": "test",
            "beam2_filled_bx_slots": [1], "colliding_ip5_bx_slots": [1],
            "source": {"csv_sha256": "0" * 64},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mask.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(TriggerLibraryError, "not filled"):
                load_ip5_bunch_mask(str(path), [0], 2, 1)

    def write_run_fill_map(self, fill=9017):
        payload = {
            "schema": "cms-run-to-fill-map",
            "schema_version": 1,
            "source": {
                "service": "test",
                "query": "test query",
                "retrieved_at": "2026-09-01",
            },
            "runs": {"369943": {"fill_number": fill}},
        }
        temporary = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        self.addCleanup(Path(temporary.name).unlink, missing_ok=True)
        json.dump(payload, temporary)
        temporary.close()
        return temporary.name

    def test_validates_trigger_run_against_mask_fill(self):
        result = validate_run_fill_map(
            self.write_run_fill_map(), [369943], 9017
        )
        self.assertEqual(result["status"], "validated")
        self.assertEqual(result["fill_number"], 9017)
        self.assertEqual(len(result["file_sha256"]), 64)

    def test_rejects_trigger_run_fill_mismatch(self):
        with self.assertRaisesRegex(TriggerLibraryError, "not bunch-mask fill"):
            validate_run_fill_map(
                self.write_run_fill_map(fill=9018), [369943], 9017
            )


if __name__ == "__main__":
    unittest.main()
