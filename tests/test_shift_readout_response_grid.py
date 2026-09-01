#!/usr/bin/env python3

from decimal import Decimal
from pathlib import Path
import sys
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from run_shift_readout_response_grid import (  # noqa: E402
    parse_offsets,
    parse_phases,
    point_name,
    runtime_command,
    sanitized_runtime_environment,
)


class ShiftReadoutResponseGridTest(unittest.TestCase):
    def test_inclusive_ranges_and_deduplication(self):
        self.assertEqual(parse_offsets("-2:2,0,4"), [-2, -1, 0, 1, 2, 4])
        self.assertEqual(parse_offsets("3:-1:-2"), [-1, 1, 3])

    def test_invalid_ranges_fail(self):
        for spec in ("", "0:3:0", "0:3:-1", "1:2:3:4"):
            with self.subTest(spec=spec), self.assertRaises(ValueError):
                parse_offsets(spec)

    def test_phases_are_canonical_and_bounded(self):
        self.assertEqual(
            parse_phases("0,6.25,12.500,6.25"),
            [Decimal("0"), Decimal("6.25"), Decimal("12.500")],
        )
        for spec in ("", "-0.1", "25", "nan", "late"):
            with self.subTest(spec=spec), self.assertRaises(ValueError):
                parse_phases(spec)

    def test_point_names_are_distinct_and_shell_neutral(self):
        self.assertEqual(point_name(-2), "bx_m2_phase_0")
        self.assertEqual(point_name(0, Decimal("6.25")), "bx_p0_phase_6p25")
        self.assertEqual(point_name(2, Decimal("12.500")), "bx_p2_phase_12p5")

    def test_runtime_environment_drops_inherited_build_paths(self):
        observed = sanitized_runtime_environment(
            {
                "PATH": "/usr/bin",
                "SHIFT_READOUT_DIAGNOSTICS": "1",
                "LD_LIBRARY_PATH": "/unrelated/lib",
                "PYTHONPATH": "/unrelated/python",
                "SRT_LD_LIBRARY_PATH_SCRAMRT": "/old/cmssw/lib",
            }
        )
        self.assertEqual(observed["PATH"], "/usr/bin:/bin")
        self.assertNotIn("SHIFT_READOUT_DIAGNOSTICS", observed)
        self.assertNotIn("LD_LIBRARY_PATH", observed)
        self.assertNotIn("PYTHONPATH", observed)
        self.assertNotIn("SRT_LD_LIBRARY_PATH_SCRAMRT", observed)

    def test_runtime_command_does_not_reload_login_profile(self):
        command = runtime_command(Path("/tmp/cmssw"), ["cmsRun", "test.py"])
        self.assertEqual(command[:2], ["/bin/bash", "-c"])


if __name__ == "__main__":
    unittest.main()
