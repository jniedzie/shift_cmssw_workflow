import unittest
from pathlib import Path


WORKFLOW_ROOT = Path(__file__).resolve().parents[1]
STAGE_SCRIPTS = (
    "run_step1_generation.sh",
    "run_step2_digi_raw.sh",
    "run_step3_aod.sh",
    "run_step4_exonanoAOD.sh",
)


class OutputReuseSafetyTest(unittest.TestCase):
    def test_outputs_that_fail_validation_are_removed_for_retry(self):
        for script_name in STAGE_SCRIPTS:
            text = (WORKFLOW_ROOT / script_name).read_text(encoding="utf-8")
            with self.subTest(script=script_name):
                self.assertIn("Removing invalid Step", text)

    def test_eos_fuse_outputs_are_validated_through_xrootd(self):
        text = (WORKFLOW_ROOT / "scripts/setup_cmssw.sh").read_text(encoding="utf-8")
        self.assertIn("^/eos/home-([^/])/([^/]+)(/.*)$", text)
        self.assertIn("root://eosuser.cern.ch//eos/user/", text)

    def test_submission_fails_closed_on_full_eos_quota(self):
        text = (WORKFLOW_ROOT / "run_condor.sh").read_text(encoding="utf-8")
        self.assertIn("EOS quota is full", text)
        self.assertIn("submission aborted before building CMSSW", text)


if __name__ == "__main__":
    unittest.main()
