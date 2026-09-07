#!/usr/bin/env python3
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


class OpportunityTimelineTest(unittest.TestCase):
    def command(self, csv_path, output):
        return [sys.executable, str(SCRIPTS / "build_shift_l1a_opportunity_timeline.py"), "--input-csv", str(csv_path), "--source", "TCDS export", "--run-period", "2023", "--physics-valid", "--output", str(output)]

    def test_writes_ordered_measured_timeline(self):
        with tempfile.TemporaryDirectory() as directory:
            csv_path = Path(directory) / "input.csv"
            csv_path.write_text("signal_event_id,parent_slot,relative_bx,l1a_recorded,hlt_persisted,run,lumi\na,10,-1,0,0,369943,3\na,10,0,1,1,369943,3\n")
            output = Path(directory) / "timeline.jsonl"
            result = subprocess.run(self.command(csv_path, output), text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            records = [json.loads(line) for line in output.read_text().splitlines()]
        self.assertTrue(records[0]["physics_valid"])
        self.assertEqual(records[2]["relative_bx"], 0)
        self.assertTrue(records[2]["hlt_persisted"])

    def test_rejects_persistence_without_l1a(self):
        with tempfile.TemporaryDirectory() as directory:
            csv_path = Path(directory) / "input.csv"
            csv_path.write_text("signal_event_id,parent_slot,relative_bx,l1a_recorded,hlt_persisted,run,lumi\na,10,0,0,1,369943,3\n")
            result = subprocess.run(self.command(csv_path, Path(directory) / "timeline.jsonl"), text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("without recorded", result.stderr)


if __name__ == "__main__":
    unittest.main()
