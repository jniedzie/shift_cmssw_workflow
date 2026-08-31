#!/usr/bin/env python3

from pathlib import Path
import sys
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from classify_shift_trigger_readouts import _classification, _spatial_lct  # noqa: E402


class ShiftTriggerReadoutsTest(unittest.TestCase):
    def test_spatial_identity_intentionally_omits_bx_and_format_payload(self):
        primitive = {
            "chamber_id": 42,
            "readout_relative_bx": 1,
            "track_number": 2,
            "quality": 3,
            "key_wire": 4,
            "strip": 5,
            "bend": 1,
            "pattern": 9,
        }
        shifted = {**primitive, "readout_relative_bx": 2, "pattern": 10}
        self.assertEqual(_spatial_lct(primitive), _spatial_lct(shifted))

    def test_classifies_one_readout_union_partial_and_absent(self):
        expected = {("a",), ("b",)}
        self.assertEqual(
            _classification(expected, {"bx0": expected, "bx1": set()}),
            "complete_in_one_tested_readout",
        )
        self.assertEqual(
            _classification(expected, {"bx0": {("a",)}, "bx1": {("b",)}}),
            "complete_only_in_union_of_tested_readouts",
        )
        self.assertEqual(
            _classification(expected, {"bx0": {("a",)}, "bx1": set()}),
            "partial_across_tested_readouts",
        )
        self.assertEqual(
            _classification(expected, {"bx0": set(), "bx1": set()}),
            "no_LCT_content_in_tested_readouts",
        )

    def test_no_expected_primitive_is_not_called_complete(self):
        self.assertEqual(
            _classification(set(), {"bx0": set(), "bx1": set()}),
            "no_chamber_compatible_simulated_LCT",
        )


if __name__ == "__main__":
    unittest.main()
