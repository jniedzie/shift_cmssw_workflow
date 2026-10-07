import contextlib
import io
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import prepare_shift_ntuple_restart as preparer
try:
    import classad2
except ImportError:
    classad2 = None


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
            self.assertEqual(policy['config_timeout_seconds'], 600)
            self.assertEqual(policy['event_stall_timeout_seconds'], 1800)
            self.assertEqual(bootstrap['config_timeout_seconds'], 600)
            self.assertEqual(bootstrap['event_stall_timeout_seconds'], 1800)
            self.assertIn('--stage-timeouts "$stage_limits"', (root / 'bootstrap.sh').read_text())
            self.assertIn('+MaxRuntime = 21600', (root / 'bulk.sub').read_text())
            self.assertIn('JobMaterializeDate', (root / 'bulk.sub').read_text())
            subprocess.run(['/bin/bash', '-n', str(root / 'bootstrap.sh')], check=True)
            self.assertNotIn('eval ', (root / 'bootstrap.sh').read_text())

    def test_explicit_guard_limits_are_frozen_and_forwarded_to_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.campaign(root, sampled=True)
            self.launch(root, '--config-timeout-seconds', '1800', '--event-stall-timeout-seconds', '7200',
                        '--stage-timeouts', '72000,18000,54000,144000')
            for name in ('policy.json', 'bootstrap.json'):
                frozen = json.loads((root / name).read_text())
                self.assertEqual(frozen['config_timeout_seconds'], 1800)
                self.assertEqual(frozen['event_stall_timeout_seconds'], 7200)
                self.assertEqual(frozen['stage_timeouts_seconds'], [72000,18000,54000,144000])
            worker = root / 'payload/workflow/scripts/run_shift_gen_to_nano.py'
            worker.parent.mkdir(parents=True)
            worker.write_text('import json,sys\nfrom pathlib import Path\n'
                              'Path("forwarded.json").write_text(json.dumps(sys.argv[1:]))\n')
            forwarding = preparer.BOOTSTRAP[preparer.BOOTSTRAP.index('extra=()'):]
            subprocess.run(['/bin/bash', '-c',
                'mode=canary\nsource_index=00000\nskip=1\ncount=2\njob=800000\n' + forwarding],
                cwd=root, check=True, timeout=10)
            argv = json.loads((root / 'forwarded.json').read_text())
            self.assertEqual(argv, ['source00000.json','--skip','1','--count','2','--job','800000',
                '--templates','templates','--stage-timeouts','72000,18000,54000,144000',
                '--config-timeout','1800','--event-stall-timeout','7200','--publish','--canary'])

    @staticmethod
    def bootstrap_python(after):
        return preparer.BOOTSTRAP.split(after, 1)[1].split("<<'PY'\n", 1)[1].split('\nPY\n', 1)[0]

    def run_exit_python(self, root, result, attempt, upload):
        previous = Path.cwd()
        try:
            os.chdir(root)
            with patch.object(sys, 'argv', ['exit-handler', str(result), '17', '00000', attempt]), \
                 patch.object(subprocess, 'run', side_effect=upload):
                exec(compile(self.bootstrap_python('finish() {'), '<bootstrap-exit>', 'exec'), {})
        finally:
            os.chdir(previous)
        return json.loads((root / 'status.json').read_text())

    def test_upload_failure_keeps_status_and_distinct_attempt_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = dict(complete=False, error='slow event', events=10, source_stratum='qcd_20toinf',
                stages={'SIM':{'seconds':3}, 'pending':None}, nano={'compressed_event_bytes':100},
                validated_tier_events={'GEN':10,'SIM':10}, wall_seconds=40)
            (root / 'report.json').write_text(json.dumps(report))
            (root / 'evidence.tar.gz').write_bytes(b'preserved failure evidence')
            (root / 'source00000.json').write_text(json.dumps({'output_base':'/eos/user/t/test/campaign/bin'}))
            uploads = []
            def failed_upload(argv, **kwargs):
                initial = json.loads((root / 'status.json').read_text())
                self.assertEqual((initial['job'], initial['exit_code']), (17, 9))
                self.assertFalse(initial['complete'])
                self.assertTrue(all(name not in kwargs['env'] for name in (
                    'LD_LIBRARY_PATH','LD_PRELOAD','PYTHONPATH','PYTHONHOME','ROOTSYS')))
                uploads.append(argv)
                if argv[0].endswith('xrdfs'):
                    raise OSError('upload unavailable')
                return SimpleNamespace(returncode=37)
            first = self.run_exit_python(root, 9, '42.7.100', failed_upload)
            second = self.run_exit_python(root, 9, '42.7.101', failed_upload)
            self.assertNotEqual(first['failure_evidence_path'], second['failure_evidence_path'])
            self.assertEqual(first['attempt'], '42.7.100')
            self.assertEqual(len(first['failure_upload_errors']), 3)
            self.assertEqual(first['stage_seconds'], {'SIM':3})
            self.assertEqual(first['validated_tier_events'], report['validated_tier_events'])
            self.assertEqual(first['compressed_event_bytes'], 100)
            self.assertEqual(first['report_sha256'], preparer.sha(root / 'report.json'))
            self.assertTrue(all('/attempt_' in argv[-1] for argv in uploads))

    def test_missing_or_invalid_report_still_produces_status(self):
        for report in (None, '{broken', '[]'):
            with self.subTest(report=report), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                if report is not None:
                    (root / 'report.json').write_text(report)
                status = self.run_exit_python(root, 5, 'unknown.unknown.1', lambda *args, **kwargs: self.fail('unexpected upload'))
                self.assertEqual((status['job'], status['exit_code'], status['complete']), (17,5,False))
                self.assertTrue(status['error'])

    def test_successful_exit_preserves_completion_fields_without_failure_upload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = dict(complete=True, source_stratum='dy', events=10, nano_path='/eos/nano.root',
                nano_bytes=12, wall_seconds=4, stages={'NANO':{'seconds':3}},
                validated_tier_events={tier:10 for tier in ('GEN','SIM','DIGIHLT','RECO','NANO')},
                nano={'compressed_event_bytes':6})
            (root / 'report.json').write_text(json.dumps(report))
            (root / 'evidence.tar.gz').write_bytes(b'evidence')
            status = self.run_exit_python(root, 0, '42.7.100', lambda *args, **kwargs: self.fail('unexpected upload'))
            self.assertTrue(status['complete'])
            for key in ('source_stratum','events','nano_path','nano_bytes','wall_seconds','validated_tier_events'):
                self.assertEqual(status[key], report[key])
            self.assertEqual(status['report_sha256'], preparer.sha(root / 'report.json'))

    def test_attempt_tokens_bind_scheduler_identity_and_are_unique(self):
        with tempfile.TemporaryDirectory() as directory:
            ad = Path(directory) / 'job.ad'
            ad.write_text('ClusterId = 42\nProcId = 7\n')
            outputs = []
            with patch.dict(os.environ, {'_CONDOR_JOB_AD':str(ad)}), \
                 patch('time.time_ns', side_effect=[100,101]):
                for _ in range(2):
                    stream = io.StringIO()
                    with contextlib.redirect_stdout(stream):
                        exec(compile(self.bootstrap_python('attempt=$('), '<bootstrap-attempt>', 'exec'), {})
                    outputs.append(stream.getvalue().strip())
            self.assertEqual(outputs, ['42.7.100','42.7.101'])

    def test_bootstrap_failure_from_nested_directory_writes_scratch_status(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'nested').mkdir()
            prefix = preparer.BOOTSTRAP.split('trap finish EXIT', 1)[0] + 'trap finish EXIT\n'
            script = root / 'exit_check.sh'
            script.write_text(prefix + 'cd "$scratch/nested"\nexit 7\n')
            env = dict(os.environ, _CONDOR_SCRATCH_DIR=str(root), PYTHONHOME='/invalid-test-home',
                       PYTHONPATH='/invalid-test-path')
            result = subprocess.run(['/bin/bash', str(script), 'unused', '00000', '0', '10', '17'],
                                    env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 7, result.stdout + result.stderr)
            self.assertEqual(json.loads((root / 'status.json').read_text())['exit_code'], 7)
            self.assertFalse((root / 'nested/status.json').exists())

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

    @unittest.skipUnless(classad2, 'native ClassAd Python evaluator unavailable')
    def test_native_periodic_guard_handles_stage_transitions_and_attempt_setup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.campaign(root, sampled=True)
            self.launch(root)
            expression = next(line.split('=',1)[1].strip() for line in (root / 'bulk.sub').read_text().splitlines()
                              if line.startswith('periodic_hold ='))
            now = int(time.time())
            current = dict(JobStatus=2, JobCurrentStartDate=now-4000,
                ShiftNtupleTier='NANO_AUDIT', ShiftNtupleProgressEpoch=now-1440,
                ShiftNtupleStageTimeout=900, ShiftNtupleStageDeadlineEpoch=now+900)
            def evaluate(values):
                ad = classad2.ClassAd(values)
                ad['Hold'] = classad2.ExprTree(expression)
                return ad.eval('Hold')
            self.assertIs(evaluate(current), False)
            self.assertIs(evaluate({**current, 'ShiftNtupleStageDeadlineEpoch':now-181}), True)
            self.assertIs(evaluate({**current, 'JobCurrentStartDate':now-21601}), True)
            self.assertIs(evaluate({**current, 'ShiftNtupleStageDeadlineEpoch':0}), True)
            for progress in (None, now-3000):
                for deadline in (None, now-3000, now+900):
                    early = dict(JobStatus=2, JobCurrentStartDate=now-10, ShiftNtupleStageTimeout=1)
                    if progress is not None:
                        early['ShiftNtupleProgressEpoch'] = progress
                    if deadline is not None:
                        early['ShiftNtupleStageDeadlineEpoch'] = deadline
                    self.assertIs(evaluate(early), False)
                    self.assertIs(evaluate({**early, 'JobCurrentStartDate':now-1981}), True)
            idle = dict(JobStatus=1, QDate=now-40000, EnteredCurrentStatus=now-40000,
                        JobMaterializeDate=now-10)
            self.assertIs(evaluate(idle), False)
            self.assertIs(evaluate({**idle, 'JobMaterializeDate':now-7201}), True)

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
            environment = {key:value for key,value in os.environ.items()
                           if key not in ('CONDOR_CONFIG','BASH_ENV','ENV') and not key.startswith('_CONDOR_')}
            environment.update(CONDOR_CONFIG='/dev/null', SKIP_LOCAL_CONFIG_FILE='TRUE',
                               _CONDOR_SKIP_LOCAL_CONFIG_FILE='TRUE', _CONDOR_NETWORK_INTERFACE='127.0.0.1',
                               _CONDOR_FULL_HOSTNAME='localhost')
            check=subprocess.run([shutil.which('condor_submit'),'-dry-run',str(root/'logs_check.ads'),
                                 str(root/'logs_check.sub')],env=environment,capture_output=True,text=True,timeout=30)
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
        for option in ('--config-timeout-seconds','--event-stall-timeout-seconds'):
            for value in ('0','-1','bad'):
                with self.subTest(option=option, value=value), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    self.campaign(root, sampled=True)
                    with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                        self.launch(root, option, value)
                    self.assertFalse((root / 'policy.json').exists())
                    self.assertFalse((root / 'bootstrap.json').exists())


if __name__ == '__main__':
    unittest.main()
