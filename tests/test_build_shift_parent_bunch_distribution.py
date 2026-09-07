#!/usr/bin/env python3
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


class ParentBunchDistributionTest(unittest.TestCase):
    def write_mask(self, directory):
        path = Path(directory) / "mask.json"
        path.write_text(json.dumps({"schema": "cms-lpc-ip5-bunch-mask", "schema_version": 1, "orbit_slots": 3564, "fill_number": 9017, "beam1_filled_bx_slots": [1], "beam2_filled_bx_slots": [10, 11], "colliding_ip5_bx_slots": [10], "source": {"csv_sha256": "0" * 64}}))
        return path

    def command(self, mask, csv_path, output, *extra):
        return [sys.executable, str(SCRIPTS / "build_shift_parent_bunch_distribution.py"), "--bunch-mask", str(mask), "--weights-csv", str(csv_path), "--shift-beam", "2", "--bunch-population-source", "BRIL test", "--source-weight-source", "SHIFT test", "--output", str(output), *extra]

    def test_normalizes_complete_measured_weights(self):
        with tempfile.TemporaryDirectory() as directory:
            mask = self.write_mask(directory)
            weights = Path(directory) / "weights.csv"
            weights.write_text("slot,bunch_population,source_weight\n10,2,3\n11,1,2\n")
            output = Path(directory) / "parent.json"
            result = subprocess.run(self.command(mask, weights, output, "--physics-valid"), text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(output.read_text())
        self.assertTrue(data["physics_valid"])
        self.assertAlmostEqual(data["slots"][0]["weight"], 0.75)
        self.assertAlmostEqual(data["slots"][1]["weight"], 0.25)

    def test_rejects_missing_filled_slot(self):
        with tempfile.TemporaryDirectory() as directory:
            mask = self.write_mask(directory)
            weights = Path(directory) / "weights.csv"
            weights.write_text("slot,bunch_population,source_weight\n10,2,3\n")
            result = subprocess.run(self.command(mask, weights, Path(directory) / "parent.json"), text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("omits", result.stderr)


if __name__ == "__main__":
    unittest.main()
