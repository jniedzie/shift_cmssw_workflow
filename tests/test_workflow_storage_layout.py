"""Check the shell workflow's dated, flat data layout without creating data."""
import os
from pathlib import Path
import subprocess
import unittest


WORKFLOW = Path(__file__).resolve().parents[1]


class WorkflowStorageLayoutTest(unittest.TestCase):
    def run_config(self, **values):
        environment = {key: value for key, value in os.environ.items() if key not in (
            'SAMPLE_DIR', 'SAMPLES_DIR', 'STEP1_DIR', 'STEP2_DIR', 'STEP3_DIR', 'STEP4_DIR',
            'SAMPLE_NAME', 'PROCESS', 'CAMPAIGN_NAME', 'GEN_PTHAT_MIN', 'GEN_PTHAT_MAX',
            'SAMPLE_SUPPORT_BASE', 'CONFIG_BASE_DIR', 'CROSS_SECTION_FILE')}
        environment.update(WORKFLOW_HOST='lxplus.test', SAMPLE_BASE='/tmp/shift-data',
                           CAMPAIGN_NAME='storage_test_20261009')
        environment.update(values)
        return subprocess.run(['bash', '--noprofile', '--norc', '-c',
            'source "$1" || exit $?; printf "%s\\n" "$SAMPLE_DIR" "$STEP1_DIR" "$STEP2_DIR" '
            '"$STEP3_DIR" "$STEP4_DIR" "$CROSS_SECTION_FILE" "$DEFAULT_PILEUP_INPUT"',
            'bash', str(WORKFLOW / 'config/workflow.env')], env=environment,
            capture_output=True, text=True)

    def test_default_fragment_bin_and_direct_tiers(self):
        result = self.run_config()
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        campaign = '/tmp/shift-data/jpsi_pThat1to5GeV/storage_test_20261009'
        self.assertEqual(lines[:6], [campaign] + [campaign + '/' + suffix for suffix in (
            'GEN_SIM', 'DIGI_RAW_HLT', 'AODSIM', 'nanoAOD', 'metadata/cross_sections.txt')])
        self.assertTrue(lines[6].startswith('filelist:/tmp/shift-data_support/pileup_inputs/'))

    def test_explicit_generator_bounds_keep_exact_precision(self):
        result = self.run_config(SAMPLE_NAME='qcd', GEN_PTHAT_MIN='0.211317', GEN_PTHAT_MAX='0.5')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[0],
            '/tmp/shift-data/qcd_pThat0p211317to0p5GeV/storage_test_20261009')

    def test_incomplete_bounds_and_undated_campaign_fail(self):
        for values in ({'GEN_PTHAT_MIN':'1'}, {'CAMPAIGN_NAME':'undated_2023'}):
            with self.subTest(values=values):
                result = self.run_config(**values)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('could not determine', result.stderr)

    def test_explicit_site_directory_is_preserved(self):
        result = self.run_config(SAMPLE_DIR='/tmp/custom/storage_test_20261009',
                                 STEP4_DIR='/tmp/custom/output/nanoAOD')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[0], '/tmp/custom/storage_test_20261009')
        self.assertEqual(result.stdout.splitlines()[4], '/tmp/custom/output/nanoAOD')


if __name__ == '__main__':
    unittest.main()
