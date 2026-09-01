#!/usr/bin/env python3

import os
from pathlib import Path
import signal
import sys
import time
import unittest


REPOSITORY = Path(__file__).resolve().parents[1]
SCRIPTS = REPOSITORY / "scripts"
sys.path.insert(0, str(SCRIPTS))

from fluka_region_preflight import (  # noqa: E402
    classify_raw_regions,
    isolated_region_bounds,
    resolve_raw_region_classifications,
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
    def __init__(self, result=None, error=None, child_signal=None, delay=0.0):
        self.result = result
        self.error = error
        self.child_signal = child_signal
        self.delay = delay

    def zoneAABBs(self, aabb=None):
        if self.delay:
            time.sleep(self.delay)
        if self.child_signal is not None:
            os.kill(os.getpid(), self.child_signal)
        if self.error is not None:
            raise self.error
        return self.result


class FakeRegistry:
    def __init__(self, regions, assignments=None):
        self.regionDict = regions
        self.assignmas = assignments or {}


class FlukaRegionPreflightTest(unittest.TestCase):
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

    def test_times_out_and_reaps_stuck_native_evaluation(self):
        result = isolated_region_bounds(
            FakeRegion(delay=2.0), timeout_seconds=0.02
        )
        self.assertEqual(result["status"], "error")
        self.assertTrue(result["timed_out"])
        self.assertIn("exceeded", result["error"])

    def test_classifies_blackhole_without_evaluating_it(self):
        registry = FakeRegistry(
            {
                "Material": FakeRegion([FakeBound([0, 0, 0], [1, 1, 1])]),
                "Null": FakeRegion([]),
                "Blackhole": FakeRegion(child_signal=signal.SIGSEGV),
                "Broken": FakeRegion(error=RuntimeError("broken")),
            },
            {"Blackhole": ("BLCKHOLE", None, None)},
        )
        result = classify_raw_regions(
            registry,
            ["Material", "Null", "Blackhole", "Broken"],
            include_bounds=True,
        )
        self.assertEqual(result["blackhole_regions"], ["Blackhole"])
        self.assertEqual(result["non_null_regions"], ["Material"])
        self.assertEqual(result["source_null_regions"], ["Null"])
        self.assertEqual(
            [item["name"] for item in result["evaluation_errors"]], ["Broken"]
        )
        self.assertEqual(
            result["bounds_mm"]["Material"],
            [[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]],
        )
        self.assertEqual(
            result["zone_bounds_mm"]["Material"],
            [[[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]]],
        )
        self.assertEqual(result["zone_bounds_mm"]["Null"], [])
        self.assertFalse(result["passed"])

    def test_resolves_null_disagreement_and_crash_fallback(self):
        primary = {
            "blackhole_regions": ["Blackhole"],
            "non_null_regions": ["Material"],
            "source_null_regions": ["Disputed", "ConfirmedNull"],
            "evaluation_errors": [
                {"name": "CgalCrash", "error": "signal 11"},
            ],
        }
        secondary = {
            "non_null_regions": ["Disputed", "CgalCrash"],
            "source_null_regions": ["ConfirmedNull"],
            "evaluation_errors": [],
        }
        result = resolve_raw_region_classifications(
            primary,
            secondary,
            [
                "Material",
                "Disputed",
                "ConfirmedNull",
                "CgalCrash",
                "Blackhole",
            ],
        )
        self.assertEqual(
            result["non_null_regions"], ["Material", "Disputed", "CgalCrash"]
        )
        self.assertEqual(result["source_null_regions"], ["ConfirmedNull"])
        self.assertEqual(result["fallback_non_null_regions"], ["CgalCrash"])
        self.assertEqual(
            [item["name"] for item in result["backend_disagreements"]],
            ["Disputed"],
        )
        self.assertTrue(result["passed"])

    def test_rejects_crash_followed_only_by_secondary_null(self):
        primary = {
            "blackhole_regions": [],
            "non_null_regions": [],
            "source_null_regions": [],
            "evaluation_errors": [{"name": "Broken", "error": "signal 11"}],
        }
        secondary = {
            "non_null_regions": [],
            "source_null_regions": ["Broken"],
            "evaluation_errors": [],
        }
        result = resolve_raw_region_classifications(
            primary, secondary, ["Broken"]
        )
        self.assertFalse(result["passed"])
        self.assertEqual(result["evaluation_errors"][0]["name"], "Broken")

    def test_defers_primary_null_when_secondary_backend_errors(self):
        primary = {
            "blackhole_regions": [],
            "non_null_regions": ["Material"],
            "source_null_regions": ["Deferred"],
            "evaluation_errors": [],
        }
        secondary = {
            "non_null_regions": [],
            "source_null_regions": [],
            "evaluation_errors": [
                {"name": "Deferred", "error": "polygon processing failed"},
            ],
        }
        result = resolve_raw_region_classifications(
            primary, secondary, ["Material", "Deferred"]
        )
        self.assertEqual(
            result["conversion_candidate_regions"],
            ["Material", "Deferred"],
        )
        self.assertEqual(
            result["deferred_null_validation_regions"], ["Deferred"]
        )
        self.assertTrue(result["passed"])

    def test_rejects_duplicate_or_unknown_region_names(self):
        registry = FakeRegistry({"A": FakeRegion([])})
        with self.assertRaisesRegex(ValueError, "duplicates"):
            classify_raw_regions(registry, ["A", "A"])
        with self.assertRaisesRegex(ValueError, "unknown FLUKA"):
            classify_raw_regions(registry, ["missing"])


if __name__ == "__main__":
    unittest.main()
