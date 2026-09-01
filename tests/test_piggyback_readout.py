#!/usr/bin/env python3

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from summarize_piggyback_readout import (  # noqa: E402
    PiggybackError,
    build_piggyback_summary,
)


class PiggybackReadoutTest(unittest.TestCase):
    def write_timeline(self, directory, start_bx=0, end_bx=0):
        metadata = {
            "record_type": "metadata",
            "schema": "shift-zero-bias-trigger-timeline",
            "schema_version": 1,
            "start_bx": start_bx,
            "end_bx": end_bx,
            "trigger_rules_applied": False,
            "trigger_rules_embodied_by_recorded_l1a": True,
            "deadtime_applied": True,
            "trigger_rules": {"status": "requires_run_period_tcds_validation"},
            "run_fill_validation": {"status": "validated"},
            "colliding_bx_mask": {
                "fill_number": 9017,
                "shift_beam": 2,
                "reference_slot_sampling": {
                    "mode": "uniform-colliding",
                    "physics_valid": False,
                    "weighting_status": "uniform_filled_slots_provisional",
                },
            },
        }
        accepted = {
            "record_type": "timeline_bx",
            "signal_event_index": 0,
            "timeline_bx": 0,
            "analysis_window": True,
            "reference_bx_slot": 86,
            "colliding": True,
            "candidate_decisions": {
                "source_event_was_read_out": True,
                "l1_by_bx": {"0": [{"final_or": True}]},
                "hlt_accepted": ["HLT_ZeroBias_v1"],
            },
            "trigger_rule_decision": {
                "accepted": True,
                "reason": "recorded_source_event_l1a",
                "rules_reapplied": False,
            },
            "readout_after_trigger_rules": True,
            "source": {"run": 369943, "event": 1},
        }
        recorded_without_hlt_proxy = {
            "record_type": "timeline_bx",
            "signal_event_index": 1,
            "timeline_bx": 0,
            "analysis_window": True,
            "reference_bx_slot": 1871,
            "colliding": True,
            "candidate_decisions": {
                "source_event_was_read_out": True,
                "l1_by_bx": {"0": [{"final_or": False}]},
                "hlt_accepted": [],
            },
            "trigger_rule_decision": {
                "accepted": True,
                "reason": "recorded_source_event_l1a",
                "rules_reapplied": False,
            },
            "readout_after_trigger_rules": True,
            "source": {"run": 369943, "event": 2},
        }
        path = Path(directory) / "timeline.jsonl"
        path.write_text(
            "\n".join(
                json.dumps(record)
                for record in (metadata, accepted, recorded_without_hlt_proxy)
            )
            + "\n",
            encoding="utf-8",
        )
        return path

    def test_summary_excludes_shift_from_trigger_and_selects_persisted_event(self):
        with tempfile.TemporaryDirectory() as directory:
            timeline = self.write_timeline(directory)
            event_ids = [
                {"run": 1, "lumi": 1, "event": 11},
                {"run": 1, "lumi": 1, "event": 12},
            ]
            result = build_piggyback_summary(timeline, event_ids)
            report = Path(directory) / "decisions.json"
            report.write_text(json.dumps(result), encoding="utf-8")
            command = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "piggyback_event_ranges.py"),
                    str(report),
                    "--level",
                    "persisted",
                ],
                check=True,
                text=True,
                capture_output=True,
            )
        self.assertFalse(result["signal_contributed_to_trigger_decision"])
        self.assertFalse(result["physics_result_valid"])
        self.assertEqual(result["summary"]["events"], 2)
        self.assertEqual(result["summary"]["persisted_by_ordinary_hlt_proxy"], 1)
        self.assertEqual(json.loads(command.stdout), ["1:1:11"])

    def test_rejects_noncentral_analysis_window(self):
        with tempfile.TemporaryDirectory() as directory:
            timeline = self.write_timeline(directory, start_bx=-1)
            with self.assertRaisesRegex(PiggybackError, "BX 0 only"):
                build_piggyback_summary(
                    timeline, [{"run": 1, "lumi": 1, "event": 1}] * 2
                )


if __name__ == "__main__":
    unittest.main()
