#!/usr/bin/env python3

from decimal import Decimal
from pathlib import Path
import sys
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from run_shift_reco_delay_scan import (  # noqa: E402
    compact_footer,
    delay_name,
    find_step1_files,
    normalize_delay,
    parse_delays,
    publish_file,
    write_json_atomic,
)


class ShiftRecoDelayScanTest(unittest.TestCase):
    def test_decimal_ranges_are_inclusive(self):
        self.assertEqual(
            parse_delays("-12.5:12.5:6.25,0"),
            [Decimal("-12.5"), Decimal("-6.25"), Decimal("0"),
             Decimal("6.25"), Decimal("12.5")],
        )

    def test_invalid_ranges_fail(self):
        for spec in ("", "0:1:0", "0:1:-1", "0:1:2:3", "nan"):
            with self.subTest(spec=spec), self.assertRaises(ValueError):
                parse_delays(spec)

    def test_signed_delay_normalization_is_exact(self):
        cases = {
            Decimal("-25"): (-1, Decimal("0")),
            Decimal("-6.25"): (-1, Decimal("18.75")),
            Decimal("0"): (0, Decimal("0")),
            Decimal("31.25"): (1, Decimal("6.25")),
        }
        for delay, expected in cases.items():
            with self.subTest(delay=delay):
                self.assertEqual(normalize_delay(delay), expected)

    def test_names_are_shell_neutral_and_unambiguous(self):
        self.assertEqual(delay_name(Decimal("-6.25")), "delay_m6p25ns")
        self.assertEqual(delay_name(Decimal("0")), "delay_p0ns")

    def test_sample_or_step1_directory_is_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            sample = Path(directory)
            step1 = sample / "samples" / "step1"
            step1.mkdir(parents=True)
            expected = step1 / "events_step1_part0000.root"
            expected.touch()
            self.assertEqual(find_step1_files(sample), [expected])
            self.assertEqual(find_step1_files(step1), [expected])

    def test_compact_output_keeps_delay_provenance_and_no_aod_path(self):
        footer = compact_footer(
            Path("/tmp/output.root"), Decimal("-6.25"), -1,
            Decimal("18.75"), [Path("/input.root")],
        )
        self.assertIn('"delay_ns":"-6.25"', footer)
        self.assertIn('"bx_offset":-1', footer)
        self.assertIn('process.schedule.remove(process.AODSIMoutput_step)', footer)
        self.assertIn('"NanoAODOutputModule"', footer)
        self.assertIn("nanoaodUniqueString_nanoMetadata", footer)
        self.assertIn('"source_step1":["/input.root"]', footer)

    def test_completed_files_and_reports_are_published_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "local.log"
            destination = root / "shared" / "published.log"
            source.write_text("complete\n", encoding="utf-8")
            publish_file(source, destination)
            self.assertEqual(destination.read_text(encoding="utf-8"), "complete\n")
            self.assertEqual(list(destination.parent.glob("*.partial")), [])

            report = root / "shared" / "report.json"
            write_json_atomic(report, {"status": "complete", "events": 10})
            self.assertIn('"status": "complete"', report.read_text(encoding="utf-8"))
            self.assertEqual(list(report.parent.glob("*.partial")), [])


if __name__ == "__main__":
    unittest.main()
