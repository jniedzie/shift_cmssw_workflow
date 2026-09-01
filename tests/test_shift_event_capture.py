#!/usr/bin/env python3

from pathlib import Path
import sys
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from classify_shift_event_capture import (  # noqa: E402
    _capture_classification,
    _parse_offset_spec,
    _readout_sets,
)


def timeline_record(bx, accepted, hlt=True):
    return {
        "analysis_window": True,
        "timeline_bx": bx,
        "candidate_decisions": {
            "l1_by_bx": {"0": [{"final_or": True}]},
            "hlt_accepted": ["HLT_ZeroBias_v1"] if hlt else [],
        },
        "readout_after_trigger_rules": accepted,
        "trigger_rule_decision": {"accepted": accepted},
        "source": {"event": 1},
    }


class ShiftEventCaptureTest(unittest.TestCase):
    def test_timeline_bx_maps_to_opposite_signal_response_offset(self):
        candidate, raw, persisted, details, uncovered = _readout_sets(
            [timeline_record(-2, True)], {2}
        )
        self.assertEqual(candidate, {2})
        self.assertEqual(raw, {2})
        self.assertEqual(persisted, {2})
        self.assertEqual(details[0]["response_signal_bx_offset"], 2)
        self.assertEqual(uncovered, [])

    def test_rule_and_hlt_states_remain_separate(self):
        candidate, raw, persisted, _, _ = _readout_sets(
            [timeline_record(0, False), timeline_record(-1, True, hlt=False)],
            {0, 1},
        )
        self.assertEqual(candidate, {0, 1})
        self.assertEqual(raw, {1})
        self.assertEqual(persisted, set())

    def test_missing_grid_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "does not cover"):
            _readout_sets([timeline_record(-3, True)], {0, 1})

    def test_pre_rule_timeline_fails_closed(self):
        record = timeline_record(0, True)
        record["readout_after_trigger_rules"] = None
        with self.assertRaisesRegex(RuntimeError, "timeline is pre-rule"):
            _readout_sets([record], {0})

    def test_capture_classification_distinguishes_rule_and_electronics_loss(self):
        expected = {"a", "b"}
        stored = {0: {"a"}, 1: {"b"}}
        self.assertEqual(
            _capture_classification(expected, stored, {0, 1}, {0}),
            "partial_trigger_rule_loss",
        )
        self.assertEqual(
            _capture_classification(expected, stored, {0}, {0}),
            "partial_electronics_loss",
        )
        self.assertEqual(
            _capture_classification(expected, stored, {0, 1}, {0, 1}),
            "split_across_readouts",
        )
        self.assertEqual(
            _capture_classification(
                expected, {0: {"a", "b"}}, {0}, {0}, {0}
            ),
            "split_within_readout",
        )

    def test_offset_spec_is_integer_and_unique_key_ready(self):
        self.assertEqual(_parse_offset_spec("-2=/tmp/a.json"), (-2, "/tmp/a.json"))
        with self.assertRaisesRegex(ValueError, "must be an integer"):
            _parse_offset_spec("late=/tmp/a.json")


if __name__ == "__main__":
    unittest.main()
