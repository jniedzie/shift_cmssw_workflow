import contextlib
import io
import json
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import prepare_shift_ntuple_restart as preparer


class RestartTests(unittest.TestCase):
    def campaign(self, root, *, sampled):
        (root / 'sources').mkdir()
        (root / 'templates').mkdir()
        manifest = dict(eos_output='/eos/test', strata={'qcd_10to20': 20},
                        events=20, jobs=2, events_per_job=10,
                        sources=[dict(index=0, stratum='qcd_10to20', events=20)])
        if sampled:
            manifest['detector_sampling'] = dict(schema='shift-detector-pps-sample-v1')
        (root / 'manifest.json').write_text(json.dumps(manifest))
        (root / 'runtime_freeze.json').write_text(json.dumps(dict(sha256='frozen-runtime')))
        (root / 'sources' / 'source00000.json').write_text('{}')
        for stage in range(1, 5):
            (root / 'templates' / f'step{stage}.py').write_text('process = None\n')
        (root / 'jobs.txt').write_text('0 0 10 0\n0 10 10 1\n')
        (root / 'canaries.json').write_text(json.dumps([dict(source=0, job=800000)]))

    def launch(self, root, *extra):
        args = ['prepare', str(root), '--email', 'alerts@example.test', '--pilot-size', '0', *extra]
        with patch.object(sys, 'argv', args), contextlib.redirect_stdout(io.StringIO()):
            preparer.main()

    def test_sample_launch_keeps_repaired_limits_in_policy_and_hashed_bootstrap(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.campaign(root, sampled=True)
            self.launch(root)
            policy = json.loads((root / 'policy.json').read_text())
            bootstrap = json.loads((root / 'bootstrap.json').read_text())
            self.assertEqual(policy['worker_timeout_seconds'], 21600)
            self.assertEqual(policy['stage_timeouts_seconds'], [72000, 18000, 54000, 14400])
            self.assertEqual(bootstrap['stage_timeouts_seconds'], policy['stage_timeouts_seconds'])
            self.assertIn('--stage-timeouts "$stage_limits"', (root / 'bootstrap.sh').read_text())
            self.assertIn('+MaxRuntime = 21600', (root / 'bulk.sub').read_text())
            self.assertIn('JobMaterializeDate', (root / 'bulk.sub').read_text())
            subprocess.run(['/bin/bash', '-n', str(root / 'bootstrap.sh')], check=True)

    def test_full_inventory_limits_and_explicit_overrides_are_frozen(self):
        for extra, expected_stages, expected_worker in [
                ((), [10800, 1800, 5400, 14400], 108000),
                (('--stage-timeouts', '7200,900,1800,5400', '--worker-timeout-seconds', '16200'),
                 [7200, 900, 1800, 5400], 16200)]:
            with self.subTest(extra=extra), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self.campaign(root, sampled=False)
                self.launch(root, *extra)
                policy = json.loads((root / 'policy.json').read_text())
                self.assertEqual(policy['worker_timeout_seconds'], expected_worker)
                self.assertEqual(policy['stage_timeouts_seconds'], expected_stages)
                self.assertEqual(json.loads((root / 'bootstrap.json').read_text())['stage_timeouts_seconds'],
                                 expected_stages)

    @unittest.skipUnless(shutil.which('condor_submit'), 'native Condor dry run unavailable')
    def test_native_log_paths_cross_shard_boundary_and_have_bounded_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            self.campaign(root,sampled=True)
            manifest=json.loads((root/'manifest.json').read_text())
            manifest.update(jobs=501,events=501,events_per_job=1,strata={'qcd_10to20':501},
                            sources=[dict(index=0,stratum='qcd_10to20',events=501)])
            (root/'manifest.json').write_text(json.dumps(manifest))
            (root/'jobs.txt').write_text(''.join(f'00000 {job} 1 {job}\n' for job in range(501)))
            self.launch(root)
            description=(root/'bulk.sub').read_text()
            description='\n'.join(line for line in description.splitlines()
                                  if not line.startswith(('max_materialize =','max_idle =')))+'\n'
            (root/'logs_check.sub').write_text(description)
            check=subprocess.run([shutil.which('condor_submit'),'-dry-run',str(root/'logs_check.ads'),
                                 str(root/'logs_check.sub')],capture_output=True,text=True,timeout=30)
            self.assertEqual(check.returncode,0,check.stdout+check.stderr)
            logs=[]
            for line in (root/'logs_check.ads').read_text().splitlines():
                if line.startswith(('Out=','Err=')):
                    logs.append(Path(json.loads(line.split('=',1)[1])))
            self.assertEqual(len(logs),1002)
            self.assertEqual(len(set(logs)),1002)
            self.assertTrue(all((root/path.parent).is_dir() for path in logs))
            self.assertEqual(sum(path.parent==Path('logs/g0') for path in logs),1000)
            self.assertEqual(sum(path.parent==Path('logs/g1') for path in logs),2)
            self.assertTrue((root/'logs/g1600').is_dir())

    def test_invalid_timeouts_fail_before_launch_files_are_written(self):
        for values in ('1,2,3', '1,2,3,0', '1,2,3,bad'):
            with self.subTest(values=values), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self.campaign(root, sampled=True)
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    self.launch(root, '--stage-timeouts', values)
                self.assertFalse((root / 'policy.json').exists())
                self.assertFalse((root / 'bootstrap.json').exists())


if __name__ == '__main__':
    unittest.main()
