#!/usr/bin/env python3

import gzip
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from lss5_crossing_records import CrossingFormatError, parse_crossings  # noqa: E402


HEADER = """ # Scoring particles entering Region No 3965
 # Col 1: FLUKA run number
"""
ROWS = """    0 2 25 9.1023884540431038E+02 1.0 6.8E-02 -3.1E+00 -5.5E-05 -4.1E-04 1.4006E-07 6.3489E+03 1
    0 2 7 6.1463401900396591E-02 2.5 -9.1E-01 4.5E+00 1.2E-04 4.1E-03 1.4007E-07 6.3489E+03 13
    0 4 10 1.0 1.0 0.0 0.0 0.6 0.8 2.0E-07 7.0E+03 2
"""


class Lss5CrossingRecordsTest(unittest.TestCase):
    def write_source(self, text, compressed=False):
        directory = tempfile.TemporaryDirectory()
        suffix = ".dat.gz" if compressed else ".dat"
        path = Path(directory.name) / f"crossings{suffix}"
        if compressed:
            with gzip.open(path, "wt", encoding="ascii", newline="") as output:
                output.write(text)
        else:
            path.write_text(text, encoding="ascii")
        return directory, path

    def test_parser_groups_events_and_preserves_decimal_tokens(self):
        directory, path = self.write_source(HEADER + ROWS, compressed=True)
        self.addCleanup(directory.cleanup)
        parsed = parse_crossings(path)
        self.assertEqual(len(parsed.events), 2)
        self.assertEqual(parsed.particle_count, 3)
        self.assertEqual(parsed.events[0]["primary_event"], 2)
        self.assertEqual(len(parsed.events[0]["particles"]), 2)
        self.assertEqual(
            parsed.events[0]["particles"][0]["kinetic_energy_gev"],
            "9.1023884540431038E+02",
        )
        self.assertIsNone(parsed.events[0]["particles"][0]["longitudinal_direction_sign"])
        self.assertEqual(len(parsed.content_sha256), 64)

    def test_converter_writes_explicitly_unvalidated_metadata(self):
        directory, path = self.write_source(HEADER + ROWS)
        self.addCleanup(directory.cleanup)
        output_path = Path(directory.name) / "crossings.jsonl"
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "convert_fluka_crossings.py"),
                str(path),
                "--output",
                str(output_path),
                "--model-label",
                "HL-LHC CMS output fixture",
                "--interface-label",
                "CMS cavern interface from source header",
            ],
            check=True,
            text=True,
            capture_output=True,
        )
        self.assertIn("wrote 2 events and 3 particles", result.stdout)
        records = [json.loads(line) for line in output_path.read_text().splitlines()]
        metadata = records[0]
        self.assertEqual(metadata["schema"], "shift-fluka-interface-crossings")
        self.assertEqual(metadata["counts"], {"events": 2, "particles": 3})
        self.assertFalse(metadata["model"]["run3_ir5_geometry_validated"])
        self.assertFalse(metadata["model"]["run3_ir5_magnetic_fields_validated"])
        self.assertIsNone(metadata["interface"]["coordinate_transform"])
        self.assertEqual(records[1]["record_type"], "event")

        repeated = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "convert_fluka_crossings.py"),
                str(path),
                "--output",
                str(output_path),
            ],
            check=False,
            text=True,
            capture_output=True,
        )
        self.assertEqual(repeated.returncode, 1)
        self.assertIn("output already exists", repeated.stderr)

    def test_rejects_non_contiguous_event(self):
        rows = ROWS + ROWS.splitlines(keepends=True)[0]
        directory, path = self.write_source(HEADER + rows)
        self.addCleanup(directory.cleanup)
        with self.assertRaisesRegex(CrossingFormatError, "contiguous and nondecreasing"):
            parse_crossings(path)

    def test_rejects_impossible_direction_cosines(self):
        row = "0 1 10 1 1 0 0 0.9 0.9 1e-9 0 1\n"
        directory, path = self.write_source(HEADER + row)
        self.addCleanup(directory.cleanup)
        with self.assertRaisesRegex(CrossingFormatError, "norm exceeds one"):
            parse_crossings(path)

    def test_rejects_wrong_column_count(self):
        directory, path = self.write_source(HEADER + "0 1 2\n")
        self.addCleanup(directory.cleanup)
        with self.assertRaisesRegex(CrossingFormatError, "expected 12 columns"):
            parse_crossings(path)

    def test_converter_supports_gzip_output(self):
        directory, path = self.write_source(HEADER + ROWS)
        self.addCleanup(directory.cleanup)
        output_path = Path(directory.name) / "crossings.jsonl.gz"
        subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "convert_fluka_crossings.py"),
                str(path),
                "--output",
                str(output_path),
            ],
            check=True,
            text=True,
            capture_output=True,
        )
        with gzip.open(output_path, "rt", encoding="utf-8") as source:
            metadata = json.loads(source.readline())
        self.assertEqual(metadata["counts"], {"events": 2, "particles": 3})


if __name__ == "__main__":
    unittest.main()
