#!/usr/bin/env python3
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


class EventDelayResponseTest(unittest.TestCase):
    def command(self, source, output):
        return [sys.executable, str(SCRIPTS / "build_shift_event_delay_response.py"), "--input-csv", str(source), "--source", "full-chain test", "--model-version", "fixed-target-v1", "--physics-valid", "--output", str(output)]

    def test_writes_sorted_event_responses(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "response.csv"
            source.write_text("signal_event_id,physical_delay_ns,additional_delay_ns,readout,reconstructed_muon,reconstructed_dimuon,classification\na,0,25,1,1,0,complete\na,0,0,1,1,1,complete\n")
            output = Path(directory) / "response.json"
            result = subprocess.run(self.command(source, output), text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(output.read_text())
        self.assertEqual([point["additional_delay_ns"] for point in data["events"][0]["responses"]], [0.0, 25.0])

    def test_rejects_reconstruction_without_readout(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "response.csv"
            source.write_text("signal_event_id,physical_delay_ns,additional_delay_ns,readout,reconstructed_muon,reconstructed_dimuon,classification\na,0,0,0,1,0,bad\n")
            result = subprocess.run(self.command(source, Path(directory) / "response.json"), text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("without readout", result.stderr)


if __name__ == "__main__":
    unittest.main()
