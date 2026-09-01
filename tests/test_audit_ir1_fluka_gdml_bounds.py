#!/usr/bin/env python3

from pathlib import Path
import os
import signal
import sys
import unittest


REPOSITORY = Path(__file__).resolve().parents[1]
SCRIPTS = REPOSITORY / "scripts"
sys.path.insert(0, str(SCRIPTS))

from audit_ir1_fluka_gdml_bounds import (  # noqa: E402
    BOUND_PREFIX,
    ProxyModelError,
    apply_secondary_source_bounds,
    containment_result,
    isolated_region_bounds,
    parse_root_bounds,
    root_bounds_expression,
)


class FakeBound:
    def __init__(self, lower, upper):
        self.lower = lower
        self.upper = upper

    def union(self, other):
        return FakeBound(
            [min(a, b) for a, b in zip(self.lower, other.lower)],
            [max(a, b) for a, b in zip(self.upper, other.upper)],
        )


class FakeRegion:
    def __init__(self, result=None, error=None, child_signal=None):
        self.result = result
        self.error = error
        self.child_signal = child_signal

    def zoneAABBs(self, aabb=None):
        if self.child_signal is not None:
            os.kill(os.getpid(), self.child_signal)
        if self.error is not None:
            raise self.error
        return self.result


class Ir1FlukaGdmlBoundsAuditTest(unittest.TestCase):
    def test_isolates_success_null_exception_and_native_signal(self):
        result = isolated_region_bounds(FakeRegion([
            FakeBound([0, 1, 2], [3, 4, 5]),
            FakeBound([-1, 2, 0], [2, 6, 7]),
        ]))
        self.assertEqual(result, {
            "status": "ok",
            "bounds": [[-1.0, 1.0, 0.0], [3.0, 6.0, 7.0]],
        })
        self.assertEqual(isolated_region_bounds(FakeRegion([])), {"status": "null"})
        result = isolated_region_bounds(FakeRegion(error=ValueError("bad CSG")))
        self.assertEqual(result["status"], "error")
        self.assertIn("ValueError: bad CSG", result["error"])
        result = isolated_region_bounds(FakeRegion(child_signal=signal.SIGSEGV))
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["signal"], signal.SIGSEGV)

    def test_parses_root_bounds_in_mm(self):
        output = (
            "unrelated ROOT output\n"
            + BOUND_PREFIX
            + "Region_lv\t1\t2\t3\t4\t5\t6\n"
        )
        self.assertEqual(
            parse_root_bounds(output)["Region"],
            [[1.0, 3.0, 5.0], [2.0, 4.0, 6.0]],
        )

    def test_rejects_duplicate_or_non_region_volumes(self):
        record = BOUND_PREFIX + "Region_lv\t1\t2\t3\t4\t5\t6\n"
        with self.assertRaisesRegex(ProxyModelError, "duplicate"):
            parse_root_bounds(record + record)
        with self.assertRaisesRegex(ProxyModelError, "unexpected external volume"):
            parse_root_bounds(BOUND_PREFIX + "world\t1\t2\t3\t4\t5\t6\n")

    def test_containment_tolerates_length_safety_but_detects_loss(self):
        source = [[0.0, 0.0, 0.0], [10.0, 20.0, 30.0]]
        expanded = [[-0.001, -2.0, 0.001], [10.001, 22.0, 30.001]]
        result = containment_result(source, expanded, tolerance_mm=0.01)
        self.assertTrue(result["contained"])
        self.assertAlmostEqual(result["maximum_containment_deficit_mm"], 0.001)
        self.assertEqual(result["maximum_conservative_excess_mm"], 2.0)

        truncated = [[0.02, 0.0, 0.0], [10.0, 20.0, 30.0]]
        result = containment_result(source, truncated, tolerance_mm=0.01)
        self.assertFalse(result["contained"])
        self.assertEqual(result["maximum_containment_deficit_mm"], 0.02)

    def test_secondary_bounds_replace_only_expected_nulls_and_errors(self):
        bounds = {"Primary": [[0, 0, 0], [1, 1, 1]]}
        nulls = ["RecoveredNull", "UnresolvedNull"]
        errors = [
            {"name": "RecoveredError", "error": "signal 11"},
            {"name": "UnresolvedError", "error": "signal 6"},
        ]
        preflight = {
            "secondary_classification": {
                "bounds_mm": {
                    "RecoveredNull": [[1, 2, 3], [4, 5, 6]],
                    "RecoveredError": [[7, 8, 9], [10, 11, 12]],
                    "NotConverted": [[13, 14, 15], [16, 17, 18]],
                }
            }
        }
        replacements = apply_secondary_source_bounds(
            bounds,
            nulls,
            errors,
            preflight,
            ["Primary", "RecoveredNull", "RecoveredError"],
        )
        self.assertEqual(replacements, ["RecoveredError", "RecoveredNull"])
        self.assertEqual(nulls, ["UnresolvedNull"])
        self.assertEqual(errors, [{"name": "UnresolvedError", "error": "signal 6"}])
        self.assertNotIn("NotConverted", bounds)

    def test_root_expression_uses_placed_node_transform_and_mm(self):
        expression = root_bounds_expression(Path("geometry.gdml"))
        self.assertIn("node->LocalToMaster", expression)
        self.assertIn("dynamic_cast<TGeoBBox *>", expression)
        self.assertNotIn("shape->GetAxisRange", expression)
        self.assertIn("10. * placedLow", expression)
        self.assertIn(BOUND_PREFIX, expression)


if __name__ == "__main__":
    unittest.main()
