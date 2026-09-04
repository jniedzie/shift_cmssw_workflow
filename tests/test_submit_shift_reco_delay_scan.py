#!/usr/bin/env python3

from pathlib import Path
import sys
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from submit_shift_reco_delay_scan import (  # noqa: E402
    file_groups,
    prepare_output_directories,
    render_submit,
)


class SubmitShiftRecoDelayScanTest(unittest.TestCase):
    def test_groups_cover_every_file_once(self):
        self.assertEqual(file_groups(45, 20), [(0, 20), (20, 20), (40, 5)])

    def test_submit_requests_paired_group_jobs(self):
        text = render_submit(
            Path("/workflow/run.py"), Path("/input"), Path("/output"),
            "-100:100:10", [(0, 20), (20, 5)], 2, 8000, Path("/logs"),
        )
        self.assertIn("--first-file $(first_file)", text)
        self.assertIn("--files-per-job $(file_count)", text)
        self.assertIn("request_cpus = 2", text)
        self.assertIn("transfer_executable = False", text)
        self.assertIn("0 20\n20 5", text)
        self.assertIn(
            'arguments = "\'/input\' \'/output\' \'--delays=-100:100:10\'',
            text,
        )

    def test_shared_delay_directories_are_precreated(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "scan"
            prepare_output_directories(output, [-25, 0, 12.5])
            for name in ("delay_m25ns", "delay_p0ns", "delay_p12p5ns"):
                self.assertTrue((output / name).is_dir())
                self.assertTrue((output / "configs" / name).is_dir())
                self.assertTrue((output / "logs" / name).is_dir())


if __name__ == "__main__":
    unittest.main()
