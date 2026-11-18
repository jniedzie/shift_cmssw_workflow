# Code style: AI-code.
"""AI-generated failure-mode tests for bounded data reconstruction preparation."""

import hashlib
import importlib.util
import tempfile
from pathlib import Path
import unittest


SPEC = importlib.util.spec_from_file_location(
    "data_reconstruction", Path(__file__).parents[1] / "scripts/prepare_shift_data_reconstruction.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class DataReconstructionContractTest(unittest.TestCase):
    def request(self, **changes):
        values = dict(dataset="/ZeroBias/Run2023D-v1/RAW",
                      inputs=["root://eoscms.cern.ch//store/data/Run2023D/ZeroBias/RAW/file.root"],
                      global_tag="130X_dataRun3_Prompt_v4", era="Run3_2023", max_events=10,
                      certification={"369943": [[356, 359]]})
        values.update(changes)
        return MODULE.validate_request(**values)

    def test_complete_request_is_accepted(self):
        self.request()

    def test_simulation_or_unpinned_conditions_are_rejected(self):
        for tag in ("auto:run3_data_prompt", "130X_mcRun3_2023_realistic_v14", ""):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                self.request(global_tag=tag)

    def test_unbounded_processing_is_rejected(self):
        for events in (-1, 0, 1001, True):
            with self.subTest(events=events), self.assertRaises(ValueError):
                self.request(max_events=events)

    def test_duplicate_or_missing_file_ownership_is_rejected(self):
        for files in ([], ["file:a.root", "file:a.root"]):
            with self.subTest(files=files), self.assertRaises(ValueError):
                self.request(inputs=files)

    def test_bad_certification_is_rejected(self):
        for certification in ({}, {"369943": []}, {"369943": [[0, 3]]},
                              {"369943": [[4, 3]]}, {"369943": [[1, 3], [3, 5]]},
                              {"369943": [[True, 5]]}, {"369943": [[1.0, 5]]}):
            with self.subTest(certification=certification), self.assertRaises(ValueError):
                self.request(certification=certification)

    def test_profile_mutation_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / "profile.py"
            profile.write_text("# frozen profile\n")
            pin = hashlib.sha256(profile.read_bytes()).hexdigest()
            MODULE.validate_profile(profile, pin)
            profile.write_text("# altered profile\n")
            with self.assertRaises(ValueError):
                MODULE.validate_profile(profile, pin)

    def test_unpinned_profile_is_rejected(self):
        with self.assertRaises(ValueError):
            MODULE.validate_profile(Path("missing_profile.py"), "")

    def test_wrong_tier_or_campaign_is_rejected(self):
        for dataset in ("/ZeroBias/Run2023D-v1/AOD", "/MC/Run3Summer23/GEN-SIM",
                        "/ZeroBias/Run2024D-v1/RAW"):
            with self.subTest(dataset=dataset), self.assertRaises(ValueError):
                self.request(dataset=dataset)


if __name__ == "__main__":
    unittest.main()
