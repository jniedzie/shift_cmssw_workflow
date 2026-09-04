#!/usr/bin/env python3

from decimal import Decimal
import json
from pathlib import Path
import sys
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from submit_shift_reco_delay_repair import (  # noqa: E402
    find_repair_points,
    point_is_complete,
    render_submit,
)


class SubmitShiftRecoDelayRepairTest(unittest.TestCase):
    def test_complete_point_requires_exact_report_and_size(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            source = output / "events_step1_part0007.root"
            source.write_bytes(b"source")
            point = output / "delay_m30ns"
            point.mkdir()
            root = point / "events_shiftDelayScan_part0007.root"
            root.write_bytes(b"x" * 2048)
            report = point / "events_shiftDelayScan_part0007.json"
            payload = {
                "status": "complete",
                "format": "shift-reco-delay-scan-v1",
                "delay_ns": "-30",
                "bx_offset": -2,
                "phase_ns": "20",
                "source_step1": [str(source)],
                "output": str(root),
                "output_bytes": 2048,
                "pileup_mode": "none",
            }
            report.write_text(json.dumps(payload), encoding="utf-8")
            self.assertTrue(point_is_complete(output, Decimal("-30"), source, 7))
            payload["output_bytes"] = 2047
            report.write_text(json.dumps(payload), encoding="utf-8")
            self.assertFalse(point_is_complete(output, Decimal("-30"), source, 7))

    def test_submit_has_one_forced_point_per_row(self):
        text = render_submit(
            Path("/workflow/run.py"),
            Path("/input"),
            Path("/output"),
            [(Decimal("-30"), 7), (Decimal("12.5"), 11)],
            5000,
            Path("/logs"),
        )
        self.assertIn("'--delays=$(delay)'", text)
        self.assertIn("--files 1 --files-per-job 1 --workers 1 --force", text)
        self.assertIn("request_cpus = 1", text)
        self.assertIn("-30 7\n12.5 11", text)

    def test_parallel_inventory_keeps_deterministic_file_delay_order(self):
        with tempfile.TemporaryDirectory() as temporary:
            baseline = Path(temporary) / "baseline" / "samples" / "step1"
            baseline.mkdir(parents=True)
            for index in range(3):
                (baseline / f"events_step1_part{index:04d}.root").touch()
            output = Path(temporary) / "output"
            delays = [Decimal("-5"), Decimal("0")]
            self.assertEqual(
                find_repair_points(baseline, output, delays, 3, workers=2),
                [
                    (Decimal("-5"), 0), (Decimal("0"), 0),
                    (Decimal("-5"), 1), (Decimal("0"), 1),
                    (Decimal("-5"), 2), (Decimal("0"), 2),
                ],
            )


if __name__ == "__main__":
    unittest.main()
