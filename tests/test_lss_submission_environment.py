from pathlib import Path
import unittest


WORKFLOW = Path(__file__).resolve().parents[1]


class LssSubmissionEnvironmentTest(unittest.TestCase):
    def test_cms_payload_controls_are_serialized_into_condor_jobs(self):
        launcher = (WORKFLOW / "run_condor.sh").read_text()
        submit = (WORKFLOW / "condor/shift_cmssw.sub").read_text()
        for name in ("SHIFT_LSS_SYMMETRIC_TWO_SIDED", "SHIFT_LSS_FIELD_DATA_DIRECTORY",
                     "SHIFT_LSS_ARTIFACT_ORIGIN_IN_MODEL_CM"):
            with self.subTest(name=name):
                self.assertIn(name, launcher)
                self.assertIn(f"{name}=$ENV({name})", submit)


if __name__ == "__main__":
    unittest.main()
