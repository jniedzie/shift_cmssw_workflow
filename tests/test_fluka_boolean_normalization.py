import itertools
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from fluka_boolean_normalization import (
    BooleanNormalizationError, body_names, evaluate_expression, has_nested_union,
    lower_expression, normalize_nested_unions, parse_expression, render_expression,
)


class FlukaBooleanNormalizationTest(unittest.TestCase):
    def assert_truth_equivalent(self, text):
        original = parse_expression(text)
        lowered = lower_expression(original)
        reparsed = parse_expression(render_expression(lowered))
        names = sorted(body_names(original))
        self.assertFalse(has_nested_union(reparsed))
        self.assertEqual(body_names(original), body_names(reparsed))
        for values in itertools.product((False, True), repeat=len(names)):
            assignment = dict(zip(names, values))
            self.assertEqual(evaluate_expression(original, assignment),
                             evaluate_expression(reparsed, assignment), assignment)

    def test_nested_unions_and_complements_exhaustively(self):
        for text in (
            "+( | +A | +B ) -( | +C | +D )",
            "+A -( +B -( +C | +D ) )",
            "+( +A -( +B | +C ) | +D ) +( +E | +F )",
            "+A -( -B -C )",
            "+A -( -( +B | +C ) | +D )",
            "| A -B | +( +C | +D ) -( +E -F | +G )",
        ):
            with self.subTest(text=text):
                self.assert_truth_equivalent(text)

    def test_actual_dr_m1bca_expression_exhaustively(self):
        self.assert_truth_equivalent(
            "+( | +DRCM1Bl4 -DRCM1Bl1 +DRCM1By3 -DRCM1By4 +DR_BODY -DR_M1Bzl"
            " | +DRCM1Bl4 -DRCM1Bl1 +DRCM1By3 -DRCM1By4 +DR_BODY +DR_M1Bzr )"
            " -( | +DRCM1Bl6 -DRCM1Bl5 +DRCM1By5 -DRCM1By6 +DR_BODY -DR_M1Bzl"
            " | +DRCM1Bl6 -DRCM1Bl5 +DRCM1By5 -DRCM1By6 +DR_BODY +DR_M1Bzr )")

    def test_negative_union_keeps_two_output_zones(self):
        result = lower_expression(parse_expression("+(+A|+B)-(+C|+D)"))
        self.assertEqual(render_expression(result), "+A -C -D | +B -C -D")

    def test_expansion_caps_fail_without_truncation(self):
        expression = parse_expression("+(+A|+B) +(+C|+D) +(+E|+F)")
        with self.assertRaises(BooleanNormalizationError):
            lower_expression(expression, max_zones=7)
        with self.assertRaises(BooleanNormalizationError):
            lower_expression(expression, max_body_occurrences=23)
        self.assertEqual(len(lower_expression(expression, max_zones=8).zones), 8)

    def test_malformed_syntax_rejected(self):
        for text in ("", "|", "+A|", "+A||+B", "+( +A", "+A)", "+A B", "+()", "+A & +B", "++A"):
            with self.subTest(text=text), self.assertRaises(BooleanNormalizationError):
                parse_expression(text)
        with self.assertRaises(BooleanNormalizationError):
            lower_expression(parse_expression("-(+A|+B)"))

    def test_only_region_section_changes_and_lines_are_preserved(self):
        text = ("* header +( | unchanged )\nGEOBEGIN COMBNAME\n0 0 title\n"
                "RPP A 0 1 0 1 0 1\nEND\n"
                "REGION 5 +( | +A\n  | +B ) ! retained\n* inside comment\n"
                "SECOND 5 +A -B\nEND\nGEOEND\n* footer +( | unchanged )\n")
        normalized, ledger = normalize_nested_unions(text)
        self.assertEqual(len(normalized.splitlines()), len(text.splitlines()))
        self.assertEqual(normalized.splitlines()[:5], text.splitlines()[:5])
        self.assertEqual(normalized.splitlines()[8:], text.splitlines()[8:])
        self.assertIn("! retained", normalized)
        self.assertIn("* inside comment", normalized)
        self.assertEqual(ledger[0]["region"], "REGION")
        self.assertEqual(ledger[0]["source_line_start"], 6)
        self.assertEqual(normalized.splitlines()[5], "REGION 5 +A | +B")
        self.assertEqual(normalize_nested_unions(normalized), (normalized, []))


if __name__ == "__main__":
    unittest.main()
