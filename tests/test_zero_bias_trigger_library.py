#!/usr/bin/env python3

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from zero_bias_trigger_library import (  # noqa: E402
    event_group_key,
    load_trigger_library,
    sample_loaded_events,
    validate_trigger_library,
)


def make_event(event_number, accepted=None):
    block = {
        "initial": [1, 2, 3],
        "intermediate": [1, 3],
        "final": [3],
        "final_or": True,
        "final_or_pre_veto": True,
        "final_or_veto": False,
        "prescale_column": 7,
        "menu_uuid": 0x1234,
        "firmware_uuid": 0x5678,
        "bx_in_event": 0,
    }
    return {
        "record_type": "event",
        "source_event_index": event_number - 1,
        "run": 100,
        "lumi": 2,
        "event": event_number,
        "orbit": 10 + event_number,
        "bx": 20 + event_number,
        "is_real_data": True,
        "l1_by_bx": {"0": [block]},
        "l1_external_by_bx": {"0": [[11]]},
        "hlt_menu_id": "menu-a",
        "hlt_accepted": accepted or ["HLT_ZeroBias_v1"],
        "hlt_errors": [],
    }


class TriggerLibraryTest(unittest.TestCase):
    def write_library(self, events):
        directory = tempfile.TemporaryDirectory()
        path = Path(directory.name) / "library.jsonl"
        records = [
            {
                "record_type": "metadata",
                "schema": "shift-zero-bias-trigger-bits",
                "schema_version": 1,
                "source_dataset": "/ZeroBias/Test/RAW",
            },
            {
                "record_type": "hlt_menu",
                "menu_id": "menu-a",
                "paths": ["HLT_ZeroBias_v1", "HLT_Mu_v1"],
            },
            *events,
        ]
        with path.open("w", encoding="utf-8") as output:
            for record in records:
                output.write(json.dumps(record) + "\n")
        return directory, path

    def test_valid_library_groups_events_and_samples_whole_records(self):
        directory, path = self.write_library(
            [make_event(1), make_event(2, ["HLT_ZeroBias_v1", "HLT_Mu_v1"])]
        )
        self.addCleanup(directory.cleanup)
        library = load_trigger_library([str(path)])
        errors, warnings, groups = validate_trigger_library(library)

        self.assertEqual(errors, [])
        self.assertEqual(len(warnings), 1)
        self.assertEqual(len(groups), 1)
        key = event_group_key(library.events[0].record)
        self.assertEqual(key.prescale_column, 7)

        first = sample_loaded_events(groups[key], 20, seed=12345)
        second = sample_loaded_events(groups[key], 20, seed=12345)
        self.assertEqual(
            [item.record["event"] for item in first],
            [item.record["event"] for item in second],
        )
        self.assertTrue(
            all(item.record["l1_by_bx"]["0"][0]["initial"] == [1, 2, 3] for item in first)
        )

    def test_duplicate_event_is_an_error(self):
        directory, path = self.write_library([make_event(1), make_event(1)])
        self.addCleanup(directory.cleanup)
        library = load_trigger_library([str(path)])
        errors, _, _ = validate_trigger_library(library)
        self.assertTrue(any("duplicate event" in error for error in errors))

    def test_non_nested_l1_stages_are_an_error(self):
        event = make_event(1)
        event["l1_by_bx"]["0"][0]["final"] = [2]
        directory, path = self.write_library([event])
        self.addCleanup(directory.cleanup)
        library = load_trigger_library([str(path)])
        errors, _, _ = validate_trigger_library(library)
        self.assertTrue(any("final bits are not a subset" in error for error in errors))

    def test_validator_and_timeline_cli(self):
        directory, path = self.write_library([make_event(1), make_event(2)])
        self.addCleanup(directory.cleanup)
        summary_path = Path(directory.name) / "summary.json"
        timeline_path = Path(directory.name) / "timeline.jsonl"
        colliding_path = Path(directory.name) / "colliding.txt"
        menu_path = Path(directory.name) / "l1_menu.json"
        colliding_path.write_text("-1\n1\n", encoding="utf-8")
        menu_path.write_text(
            json.dumps(
                {
                    "schema": "shift-zero-bias-l1-menu",
                    "schema_version": 1,
                    "menu_uuid": 0x1234,
                    "firmware_uuid": 0x5678,
                    "global_tag": "test",
                    "algorithms": {"1": "L1_A", "2": "L1_B", "3": "L1_C"},
                }
            ),
            encoding="utf-8",
        )

        validator = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "validate_zero_bias_trigger_library.py"),
                str(path),
                "--min-events-per-group",
                "2",
                "--output",
                str(summary_path),
                "--l1-menu",
                str(menu_path),
            ],
            check=True,
            text=True,
            capture_output=True,
        )
        self.assertIn("events=2 menus=1 groups=1 errors=0", validator.stdout)
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        self.assertEqual(summary["groups"][0]["event_count"], 2)
        self.assertEqual(
            summary["groups"][0]["l1_algorithm_counts"]["final"],
            [{"bit": 3, "name": "L1_C", "count": 2}],
        )

        subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "sample_zero_bias_trigger_timeline.py"),
                str(path),
                "--output",
                str(timeline_path),
                "--start-bx",
                "-2",
                "--end-bx",
                "2",
                "--seed",
                "123",
                "--signal-events",
                "2",
                "--colliding-bx-file",
                str(colliding_path),
                "--l1-menu",
                str(menu_path),
            ],
            check=True,
            text=True,
            capture_output=True,
        )
        timeline = [json.loads(line) for line in timeline_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(timeline), 11)
        self.assertFalse(timeline[0]["deadtime_applied"])
        self.assertEqual(timeline[0]["signal_events"], 2)
        self.assertEqual(timeline[0]["l1_menu"]["algorithms"]["3"], "L1_C")
        bx_records = timeline[1:]
        self.assertEqual(
            [record["timeline_bx"] for record in bx_records],
            [-2, -1, 0, 1, 2, -2, -1, 0, 1, 2],
        )
        self.assertEqual(
            [record["signal_event_index"] for record in bx_records],
            [0, 0, 0, 0, 0, 1, 1, 1, 1, 1],
        )
        self.assertEqual(
            [record["colliding"] for record in bx_records],
            [False, True, False, True, False] * 2,
        )
        self.assertTrue(
            all(
                record["readout_after_trigger_rules"] is None
                for record in bx_records
            )
        )
        self.assertTrue(
            all(
                record["candidate_decisions"]["l1_by_bx"]["0"][0]["initial"] == [1, 2, 3]
                for record in bx_records
                if record["colliding"]
            )
        )


if __name__ == "__main__":
    unittest.main()
