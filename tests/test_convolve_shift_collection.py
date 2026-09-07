#!/usr/bin/env python3
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


class CollectionConvolutionTest(unittest.TestCase):
    def write(self, directory, name, data, jsonl=False):
        path = Path(directory) / name
        if jsonl:
            path.write_text("\n".join(json.dumps(row) for row in data) + "\n", encoding="utf-8")
        else:
            path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def inputs(self, directory, valid=True):
        provenance = {"source": "unit-test"}
        parent = self.write(directory, "parent.json", {"schema": "shift-parent-bunch-distribution", "schema_version": 1, "physics_valid": valid, "provenance": provenance, "slots": [{"slot": 10, "weight": 2}, {"slot": 11, "weight": 1}]})
        response = self.write(directory, "response.json", {"schema": "shift-event-delay-response", "schema_version": 1, "physics_valid": valid, "provenance": provenance, "bunch_spacing_ns": 25, "events": [
            {"signal_event_id": "a", "physical_delay_ns": 0, "responses": [{"additional_delay_ns": -25, "readout": False, "reconstructed_muon": False, "reconstructed_dimuon": False}, {"additional_delay_ns": 0, "readout": True, "reconstructed_muon": True, "reconstructed_dimuon": False}]},
            {"signal_event_id": "b", "physical_delay_ns": 0, "responses": [{"additional_delay_ns": 0, "readout": True, "reconstructed_muon": True, "reconstructed_dimuon": True}]},
        ]})
        timeline = self.write(directory, "timeline.jsonl", [
            {"schema": "shift-l1a-opportunity-timeline", "schema_version": 1, "physics_valid": valid, "provenance": provenance},
            {"signal_event_id": "a", "parent_slot": 10, "relative_bx": 0, "l1a_recorded": True, "hlt_persisted": True},
            {"signal_event_id": "a", "parent_slot": 10, "relative_bx": 1, "l1a_recorded": True, "hlt_persisted": True},
            {"signal_event_id": "b", "parent_slot": 11, "relative_bx": 0, "l1a_recorded": True, "hlt_persisted": True},
        ], True)
        return parent, response, timeline

    def run_convolution(self, parent, response, timeline, output, *extra):
        return subprocess.run([sys.executable, str(SCRIPTS / "convolve_shift_collection.py"), "--parent-weights", str(parent), "--response", str(response), "--timeline", str(timeline), "--output", str(output), *extra], text=True, capture_output=True)

    def test_weighted_event_level_union(self):
        with tempfile.TemporaryDirectory() as directory:
            parent, response, timeline = self.inputs(directory)
            output = Path(directory) / "output.json"
            result = self.run_convolution(parent, response, timeline, output)
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(output.read_text())
        self.assertTrue(data["physics_valid"])
        self.assertAlmostEqual(data["probabilities"]["reconstructed_muon"], 1.0)
        self.assertAlmostEqual(data["probabilities"]["reconstructed_dimuon"], 1 / 3)
        self.assertEqual(data["events"][0]["recorded_l1as"], 2)

    def test_provisional_inputs_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            parent, response, timeline = self.inputs(directory, valid=False)
            output = Path(directory) / "output.json"
            result = self.run_convolution(parent, response, timeline, output)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("physics_valid", result.stderr)
            result = self.run_convolution(parent, response, timeline, output, "--allow-provisional")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(json.loads(output.read_text())["physics_valid"])

    def test_unsampled_weighted_slot_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            parent, response, timeline = self.inputs(directory)
            records = [json.loads(line) for line in timeline.read_text().splitlines()]
            records = [records[0], *[record for record in records[1:] if record["parent_slot"] == 10]]
            timeline.write_text("\n".join(json.dumps(record) for record in records) + "\n")
            result = self.run_convolution(parent, response, timeline, Path(directory) / "output.json")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no sampled event", result.stderr)


if __name__ == "__main__":
    unittest.main()
