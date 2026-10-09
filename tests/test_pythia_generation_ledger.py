"""Fail closed on missing, duplicated or contradictory GEN proposal accounting."""
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from pythia_generation_ledger import parse_pythia_generation_ledger, read_pythia_generation_ledger


def table(rows=None, total="1567 300 300 | 6.984e-11 3.247e-12"):
    rows = rows or ["f fbar -> gamma*/Z0/Zprime0 3001 | 1567 300 300 | 6.984e-11 3.247e-12"]
    return "\n".join([
        " *------- PYTHIA Event and Cross Section Statistics -------*",
        " | Subprocess Code | Number of events | sigma +- delta |",
        " | | Tried Selected Accepted | (estimated) (mb) |",
        " | | | |",
        *[" | " + row + " |" for row in rows],
        " | sum | " + total + " |",
        " *------- End PYTHIA Event and Cross Section Statistics -------*",
    ])


class PythiaGenerationLedgerTest(unittest.TestCase):
    def test_identical_terminal_tables_count_once(self):
        result = parse_pythia_generation_ledger("startup\n" + table() + "\nwarning\n" + table(), 300)
        self.assertEqual((result["n_tried"], result["n_selected"], result["n_accepted"]), (1567, 300, 300))
        self.assertEqual(result["tried_minus_selected"], 1267)
        self.assertEqual(result["selected_minus_accepted"], 0)
        self.assertEqual(result["statistics_blocks_seen"], 2)
        self.assertEqual(result["identical_duplicate_blocks"], 1)
        self.assertEqual(result["printed_cross_section_mb"], 6.984e-11)
        self.assertIn("GenRunInfoProduct", result["cross_section_authority"])
        self.assertEqual(result["statistics_block_line_ranges"], [[2, 8], [10, 16]])

    def test_duplicate_comparison_ignores_spacing_and_row_order(self):
        rows = ["process a 3001 | 100 60 50 | 1.000e-10 1.000e-11",
                "process b 3002 | 200 80 70 | 2.000e-10 2.000e-11"]
        first = table(rows, "300 140 120 | 3.000e-10 2.236e-11")
        second = table(list(reversed(rows)), "300 140 120 | 3.000e-10 2.236e-11").replace(" | ", "  |   ")
        result = parse_pythia_generation_ledger(first + "\n" + second, 120)
        self.assertEqual(result["n_tried"], 300)
        self.assertEqual(result["selected_minus_accepted"], 20)
        self.assertEqual([row["code"] for row in result["subprocesses"]], [3001, 3002])

    def test_missing_truncated_nested_and_orphan_blocks_fail(self):
        for log in ("", "TrigReport Events total = 300", table().split("End PYTHIA")[0],
                    table().splitlines()[-1], table().splitlines()[0] + "\n" + table()):
            with self.subTest(log=log), self.assertRaises(ValueError):
                parse_pythia_generation_ledger(log)

    def test_inconsistent_duplicate_counts_or_rates_fail(self):
        for change in (table().replace("1567", "1568"), table().replace("6.984e-11", "6.985e-11"),
                       table().replace("3.247e-12", "3.248e-12")):
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "Conflicting"):
                parse_pythia_generation_ledger(table() + "\n" + change)

    def test_invalid_or_missing_counter_rows_fail(self):
        for log in (table().replace("1567 300 300", "299 300 300"),
                    table().replace("1567 300 300", "1567 299 300"),
                    table().replace("1567 300 300", "1567 -1 300"),
                    table().replace("1567 300 300", "1567 300"),
                    table().replace("Tried Selected Accepted", "Tried Accepted"),
                    table().replace("(mb)", "(pb)"),
                    table().replace(" | sum |", " | other |")):
            with self.subTest(log=log), self.assertRaises(ValueError):
                parse_pythia_generation_ledger(log)

    def test_subprocess_counter_and_rate_sums_must_close(self):
        for total in ("1568 300 300 | 6.984e-11 3.247e-12",
                      "1567 301 300 | 6.984e-11 3.247e-12",
                      "1567 300 299 | 6.984e-11 3.247e-12",
                      "1567 300 300 | 7.984e-11 3.247e-12"):
            with self.subTest(total=total), self.assertRaises(ValueError):
                parse_pythia_generation_ledger(table(total=total))

    def test_printed_rounding_does_not_require_exact_rate_sum(self):
        rows = ["process a 1 | 10 5 5 | 1.234e-10 1.000e-11",
                "process b 2 | 20 10 10 | 2.345e-10 2.000e-11"]
        result = parse_pythia_generation_ledger(table(rows, "30 15 15 | 3.580e-10 2.236e-11"), 15)
        self.assertEqual(result["n_accepted"], 15)

    def test_duplicate_process_codes_or_sum_rows_fail(self):
        duplicate = table(["process a 1 | 10 5 5 | 1.000e-10 1.000e-11",
                           "process b 1 | 20 10 10 | 2.000e-10 2.000e-11"],
                          "30 15 15 | 3.000e-10 2.236e-11")
        repeated_sum = table().replace(" | sum |", " | sum | 1567 300 300 | 6.984e-11 3.247e-12 |\n | sum |")
        for log in (duplicate, repeated_sum):
            with self.subTest(log=log), self.assertRaises(ValueError):
                parse_pythia_generation_ledger(log)

    def test_malformed_zero_yield_row_cannot_be_silently_ignored(self):
        extra = " | empty subprocess 3002 | 0 0 0 | 0.000e+00 0.000e+00 | extra |\n"
        log = table().replace(" | sum |", extra + " | sum |")
        with self.assertRaisesRegex(ValueError, "Malformed"):
            parse_pythia_generation_ledger(log)

    def test_nonfinite_negative_and_missing_rates_fail(self):
        for rate in ("nan", "inf", "-1", "1e999", "garbage"):
            with self.subTest(rate=rate), self.assertRaises(ValueError):
                parse_pythia_generation_ledger(table().replace("6.984e-11", rate))
        for error in ("nan", "inf", "-1", "1e999", ""):
            with self.subTest(error=error), self.assertRaises(ValueError):
                parse_pythia_generation_ledger(table().replace("3.247e-12", error))

    def test_acceptance_request_must_match_without_filter_inference(self):
        with self.assertRaisesRegex(ValueError, "expected persisted"):
            parse_pythia_generation_ledger(table(), 299)
        for invalid in (-1, 300., True, "300"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                parse_pythia_generation_ledger(table(), invalid)
        result = parse_pythia_generation_ledger(table(), None)
        self.assertIsNone(result["expected_accepted"])

    def test_zero_yield_and_fortran_exponents_are_retained(self):
        zero = table(["empty process 1 | 50 0 0 | 0.000D+00 0.000D+00"],
                     "50 0 0 | 0.000D+00 0.000D+00")
        result = parse_pythia_generation_ledger(zero, 0)
        self.assertEqual(result["tried_minus_selected"], 50)
        self.assertEqual(result["printed_cross_section_mb"], 0.)

    def test_file_read_records_exact_source_digest(self):
        payload = ("cmsRun\n" + table()).encode()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cmsRun.log"
            path.write_bytes(payload)
            result = read_pythia_generation_ledger(path, 300)
            self.assertEqual(result["log_sha256"], hashlib.sha256(payload).hexdigest())
            self.assertEqual(result["log_path"], str(path.resolve()))
            self.assertEqual(path.read_bytes(), payload)


if __name__ == "__main__":
    unittest.main()
