#!/usr/bin/env python3

import json
from pathlib import Path
import sys
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from scan_shift_reference_slots import scan_reference_slots  # noqa: E402
from sample_zero_bias_trigger_timeline import TriggerLibraryError  # noqa: E402


class ShiftReferenceSlotScanTest(unittest.TestCase):
    def write_mask(self, directory):
        payload = {
            "schema": "cms-lpc-ip5-bunch-mask",
            "schema_version": 1,
            "orbit_slots": 3564,
            "fill_number": 9999,
            "scheme_name": "test",
            "beam1_filled_bx_slots": [1, 2, 3564],
            "beam2_filled_bx_slots": [1, 2, 3564],
            "colliding_ip5_bx_slots": [1, 3564],
            "source": {"csv_sha256": "0" * 64},
        }
        path = Path(directory) / "mask.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_groups_all_filled_slots_with_orbit_wrap(self):
        with tempfile.TemporaryDirectory() as directory:
            result = scan_reference_slots(
                self.write_mask(directory), beam=2, start_bx=-1, end_bx=1
            )
        self.assertEqual(result["reference_slot_count"], 3)
        self.assertEqual(result["pattern_group_count"], 3)
        self.assertEqual(
            result["collision_opportunity_distribution"], {"1": 1, "2": 2}
        )
        by_slot = {
            record["reference_bx_slot"]: record["colliding_relative_bxs"]
            for record in result["reference_slots"]
        }
        self.assertEqual(by_slot[1], [-1, 0])
        self.assertEqual(by_slot[2], [-1])
        self.assertEqual(by_slot[3564], [0, 1])
        self.assertFalse(result["weighting"]["physics_valid"])

    def test_rejects_reversed_range(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(TriggerLibraryError, "precedes"):
                scan_reference_slots(
                    self.write_mask(directory), beam=2, start_bx=2, end_bx=1
                )


if __name__ == "__main__":
    unittest.main()
