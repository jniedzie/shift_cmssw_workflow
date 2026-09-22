import os
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SubmissionTest(unittest.TestCase):
    def run_check(self, overrides='', arguments='--check'):
        command = ('set -a\nsource config/campaigns/qcd_unfiltered_2023.env\n' +
                   overrides + '\nset +a\nbash run_condor.sh ' + arguments)
        env = dict(os.environ, QCD_BIN='1to2', N_JOBS='400')
        return subprocess.run(['bash', '-c', command], cwd=ROOT, env=env,
                              capture_output=True, text=True)

    def test_production_contract(self):
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('no build, cleanup, or submission', result.stdout)

    def test_invalid_resources_or_counts_rejected(self):
        for override in ('N_EVENTS=0', 'N_EVENTS=bad', 'N_JOBS=100001',
                         'CONDOR_REQUEST_MEMORY_MB=0', 'CONDOR_REQUEST_DISK_MB=bad',
                         'CONDOR_MAX_RUNTIME_SECONDS=-1', 'CONDOR_JOB_FLAVOUR=tomorrow'):
            with self.subTest(override=override):
                self.assertNotEqual(self.run_check(override).returncode, 0)

    def test_unsafe_cleanup_mode_rejected(self):
        for args in ('--check --force', '--check --steps 4', '--check --steps 2,3,4'):
            with self.subTest(args=args):
                self.assertNotEqual(self.run_check(arguments=args).returncode, 0)

    def test_other_supported_bins(self):
        for lower, upper in [(2, 5), (5, 10), (10, 20), (20, -1)]:
            with self.subTest(bounds=(lower, upper)):
                result = self.run_check(f'GEN_PTHAT_MIN={lower}\nGEN_PTHAT_MAX={upper}')
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
