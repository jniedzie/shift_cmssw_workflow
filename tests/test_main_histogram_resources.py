# Code style: AI-code. Measured detector resources preserve the old default and reject unsafe inputs.
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from prepare_dark_photon_production import main_detector_memory_mb


class MainHistogramResourcesTest(unittest.TestCase):
    def test_existing_profiles_keep_their_original_memory(self):
        self.assertEqual(main_detector_memory_mb({}),4000)

    def test_measured_single_event_profile_can_request_three_gb(self):
        self.assertEqual(main_detector_memory_mb({'worker_memory_mb':3000}),3000)

    def test_invalid_resource_profiles_fail_before_stage_creation(self):
        for value in (True,0,999,16001,3000.0,'3000',None):
            with self.subTest(value=value),self.assertRaisesRegex(ValueError,'worker memory'):
                main_detector_memory_mb({'worker_memory_mb':value})


if __name__=='__main__':unittest.main()
