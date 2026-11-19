# Code style: AI-code.
"""AI-generated checks for data ownership and sanitized worker metrics."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import sys
import os
import subprocess
import tarfile
import time
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import shift_data_attempts

SPEC = importlib.util.spec_from_file_location(
    'data_worker', Path(__file__).parents[1] / 'scripts/run_shift_data_reconstruction.py')
WORKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(WORKER)


class DataWorkerTest(unittest.TestCase):
    def request(self):
        return dict(schema='shift-data-reconstruction-job-v1', directory='/afs/example/data/job',
                    events=[dict(run=369943, luminosityBlock=356, event=2**63+7)],
                    source_offset=10,
                    batch_name='DATA_Run2023D_ZeroBias_SHIFTReco_20261118_test')

    def test_unsigned_event_identity_is_preserved(self):
        self.assertEqual(WORKER.validate_request(self.request()), [(369943, 356, 2**63+7)])

    def test_duplicate_originals_are_rejected(self):
        request = self.request()
        request['events'].append(copy.deepcopy(request['events'][0]))
        with self.assertRaises(ValueError):
            WORKER.validate_request(request)

    def muon_request(self):
        request = self.request()
        request.update(schema='shift-data-reconstruction-job-v2',
                       dataset='/Muon0/Run2023D-v1/RAW', era='Run2023D',
                       source_url='root://eoscms.cern.ch//store/data/Run2023D/Muon0/RAW/v1/fixture.root',
                       batch_name='DATA_Run2023D_Muon0_SHIFTReco_20261118_benchmark100')
        return request

    def test_explicit_muon_dataset_preserves_owned_unsigned_identity(self):
        self.assertEqual(WORKER.validate_request(self.muon_request()), [(369943, 356, 2**63+7)])

    def test_dataset_source_and_batch_must_agree(self):
        changes = (dict(source_url='root://eoscms.cern.ch//store/data/Run2023D/ZeroBias/RAW/v1/fixture.root'),
                   dict(dataset='/Muon0/Run2023D-v2/RAW'),
                   dict(era='Run2023C'),
                   dict(batch_name='DATA_Run2023D_ZeroBias_SHIFTReco_20261118_benchmark100'))
        for change in changes:
            request = self.muon_request()
            request.update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                WORKER.validate_request(request)

    def test_bad_or_out_of_range_identities_are_rejected(self):
        for value in (True, -1, 2**64, 1.5):
            request = self.request()
            request['events'][0]['event'] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                WORKER.validate_request(request)

    def test_worker_scratch_is_not_a_persistent_directory(self):
        for directory in ('/tmp/data/job', '/pool/condor/dir_1', 'relative'):
            request = self.request()
            request['directory'] = directory
            with self.subTest(directory=directory), self.assertRaises(ValueError):
                WORKER.validate_request(request)

    def test_job_report_exposes_only_operational_timing(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / 'report.xml'
            report.write_text('<Report><Metric Name="TotalInitTime" Value="40.5"/>'
                              '<Metric Name="ProtectedMass" Value="123.5"/></Report>')
            self.assertEqual(WORKER.timing_report(report), {'TotalInitTime': 40.5})

    def run_archive_finish(self, fail_tar):
        with tempfile.TemporaryDirectory() as directory:
            task = Path(directory)
            scratch, persistent = task / 'scratch', task / 'persistent'
            scratch.mkdir()
            persistent.mkdir()
            (scratch / 'restricted_nano.root').write_bytes(b'preserved synthetic ROOT')
            (scratch / 'result.json').write_text('{"complete":true,"native":{"exit_code":0}}')
            (scratch / 'status.json').write_text('{"complete":true,"exit_code":0}')
            original = b'previous immutable returned archive'
            (scratch / 'return_payload.tar.gz').write_bytes(original)
            source = Path(__file__).parents[1] / 'scripts/run_shift_data_condor.sh'
            # Exercise the deployed EXIT function without downloading CMSSW.
            entry = task / 'archive_finish.sh'
            entry.write_text(source.read_text().split('trap finish EXIT\n', 1)[0] + '\nfinish\n')
            environment = dict(os.environ, _CONDOR_SCRATCH_DIR=str(scratch))
            if fail_tar:
                bin_path = task / 'bin'
                bin_path.mkdir()
                executable = bin_path / 'tar'
                executable.write_text('#!/bin/sh\nexit 99\n')
                executable.chmod(0o700)
                environment['PATH'] = str(bin_path) + ':' + environment['PATH']
            result = subprocess.run(['bash', str(entry), str(persistent), 'request',
                                     'tools', 'runtime', 'hash'], env=environment,
                                    capture_output=True)
            if fail_tar:
                self.assertEqual(result.returncode, 74, result.stderr)
                self.assertEqual((scratch / 'return_payload.tar.gz').read_bytes(), original)
                bundles = list((persistent / 'attempt_history').iterdir())
                self.assertEqual(len(bundles), 1)
                self.assertEqual((bundles[0] / 'restricted_nano.root').read_bytes(),
                                 b'preserved synthetic ROOT')
                self.assertIn('"complete":true', (bundles[0] / 'result.json').read_text())
                self.assertIn('"complete":false', (scratch / 'result.json').read_text())
            else:
                self.assertEqual(result.returncode, 0, result.stderr)
                with tarfile.open(scratch / 'return_payload.tar.gz') as archive:
                    self.assertEqual(archive.extractfile('restricted_nano.root').read(),
                                     b'preserved synthetic ROOT')

    def test_archive_failure_preserves_native_success_and_previous_archive(self):
        self.run_archive_finish(fail_tar=True)

    def test_completed_archive_is_readable(self):
        self.run_archive_finish(fail_tar=False)


class ReviewedTimeoutRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.task = Path(self.temporary.name)
        self.persistent = self.task / 'persistent'
        self.work = self.task / 'scratch'
        self.persistent.mkdir()
        self.work.mkdir()
        self.request_sha = 'a' * 64
        self.request = dict(directory=str(self.persistent), source_offset=0,
                            native_timeout_seconds=9000, job_max_runtime_seconds=14400)
        self.guard = dict(request_sha256=self.request_sha, recovery_count=0,
                          started_utc=time.time() - 10000)
        self.previous = dict(request_sha256=self.request_sha, complete=False,
                             exit_code=124, native=dict(exit_code=124),
                             operational_interruption=False, exception_categories=[])
        self.write_first_attempt()
        self.receipt_path = self.task / 'reviewed_timeout.json'
        self.native_job = dict(cluster_id=123, proc_id=0,
                               job_start_date=int(time.time()) - 10000,
                               max_runtime_seconds=86400)
        self.ad = self.task / 'job.ad'
        self.write_ad()
        self.write_review()
        self.claims = mock.Mock()
        self.claims.reserve.return_value = self.task / 'retry_slot.json'
        self.remote = mock.Mock()
        self.remote.restore_completed.return_value = None
        self.environment = mock.patch.dict(os.environ, _CONDOR_JOB_AD=str(self.ad))
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.modules = mock.patch.dict(sys.modules, shift_data_storage_claims=self.claims,
                                       shift_data_remote_output=self.remote)
        self.modules.start()
        self.addCleanup(self.modules.stop)

    def write_ad(self):
        self.ad.write_text('\n'.join(f'{attribute} = {self.native_job[name]}' for attribute, name in
                           (('ClusterId', 'cluster_id'), ('ProcId', 'proc_id'),
                            ('JobStartDate', 'job_start_date'), ('MaxRuntime', 'max_runtime_seconds'))) + '\n')

    def write_first_attempt(self):
        (self.persistent / 'attempt_started.json').write_text(json.dumps(self.guard))
        (self.persistent / 'result.json').write_text(json.dumps(self.previous))
        with tarfile.open(self.persistent / 'return_payload.tar.gz', 'w:gz') as archive:
            archive.add(self.persistent / 'result.json', arcname='result.json')
        self.snapshot = shift_data_attempts.snapshot_previous(self.persistent, self.work)

    def write_review(self, **changes):
        self.receipt = dict(schema='shift-data-timeout-recovery-v1', request_sha256=self.request_sha,
                            directory=str(self.persistent), native_job=self.native_job,
                            previous_archive_sha256=shift_data_attempts.sha256(self.snapshot / 'return_payload.tar.gz'),
                            original_guard_sha256=shift_data_attempts.sha256(self.snapshot / 'attempt_started.json'),
                            previous_result_sha256=shift_data_attempts.sha256(self.snapshot / 'result.json'),
                            original_timeout_seconds=9000, effective_timeout_seconds=18000,
                            maximum_retries=1, review=dict(reason='Reviewed native CPU timeout; unchanged inputs and physics.'))
        self.receipt.update(changes)
        self.receipt_path.write_text(json.dumps(self.receipt))
        self.receipt_sha = shift_data_attempts.sha256(self.receipt_path)

    def begin(self, receipt_sha=None):
        return shift_data_attempts.begin_attempt(
            self.persistent, self.work, self.request, self.request_sha, [], mock.Mock(),
            self.receipt_path, receipt_sha or self.receipt_sha)

    def test_reviewed_retry_retains_request_and_reserves_original_owned_storage(self):
        original = copy.deepcopy(self.request)
        result = self.begin()
        self.assertEqual(result['action'], 'retry_reviewed_timeout')
        self.assertEqual(result['recovery_count'], 1)
        self.assertEqual(result['timeout_recovery']['effective_timeout_seconds'], 18000)
        self.assertFalse(result['timeout_recovery']['new_independent_exposure'])
        self.assertEqual(self.request, original)
        self.claims.reserve.assert_called_once_with('retry', self.request, self.request_sha)

    def test_receipt_hash_and_original_evidence_are_required(self):
        with self.assertRaises(ValueError):
            self.begin('b' * 64)
        for change in (dict(request_sha256='b' * 64), dict(directory=str(self.work)),
                       dict(previous_archive_sha256='b' * 64), dict(original_guard_sha256='b' * 64),
                       dict(previous_result_sha256='b' * 64)):
            self.write_review(**change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.begin()
        self.claims.reserve.assert_not_called()

    def test_non_timeout_and_completed_native_attempts_are_never_replayed(self):
        for changes in (dict(exit_code=1, native=dict(exit_code=1)),
                        dict(complete=True, exit_code=0, native=dict(exit_code=0)),
                        dict(operational_interruption=True), dict(exception_categories=['FatalError'])):
            previous = copy.deepcopy(self.previous)
            previous.update(changes)
            (self.snapshot / 'result.json').write_text(json.dumps(previous))
            self.write_review()
            with self.subTest(changes=changes), self.assertRaises(FileExistsError):
                self.begin()
        self.claims.reserve.assert_not_called()

    def test_second_retry_is_rejected_even_with_a_new_reviewed_receipt(self):
        self.guard['recovery_count'] = 1
        for path in (self.persistent / 'attempt_started.json', self.snapshot / 'attempt_started.json'):
            path.write_text(json.dumps(self.guard))
        self.write_review()
        with self.assertRaises(FileExistsError):
            self.begin()
        self.claims.reserve.assert_not_called()

    def test_completed_reviewed_retry_is_adopted_from_private_remote_root_after_eviction(self):
        root = self.work / 'restricted_nano.root'
        root.write_bytes(b'verified remote ROOT')
        output = dict(bytes=root.stat().st_size, sha256=shift_data_attempts.sha256(root))
        root.unlink()
        self.previous.update(complete=True, exit_code=0, native=dict(exit_code=0), output=output,
                             timeout_recovery=dict(sha256=self.receipt_sha, effective_timeout_seconds=18000))
        self.guard['recovery_count'] = 1
        self.write_first_attempt()
        def restore(request, previous, destination):
            destination.write_bytes(b'verified remote ROOT')
            return destination
        self.remote.restore_completed.side_effect = restore
        audit = mock.Mock(return_value=dict(root_uuid='verified-fixture'))
        result = shift_data_attempts.begin_attempt(
            self.persistent, self.work, self.request, self.request_sha, [], audit,
            self.receipt_path, self.receipt_sha)
        self.assertEqual(result['action'], 'adopt')
        self.assertEqual(result['result']['timeout_recovery']['sha256'], self.receipt_sha)
        self.assertFalse(result['result']['recovery']['new_independent_exposure'])
        self.remote.restore_completed.assert_called_once_with(self.request, self.previous, root)
        audit.assert_called_once_with(root, [])
        self.claims.reserve.assert_not_called()

    def test_original_native_clock_and_ownership_are_enforced(self):
        self.native_job['job_start_date'] = int(time.time()) - 70000
        self.write_ad()
        self.write_review()
        with self.assertRaises(FileExistsError):
            self.begin()
        self.native_job['job_start_date'] = int(time.time()) - 10000
        self.write_review()
        self.write_ad()
        self.ad.write_text(self.ad.read_text().replace('ClusterId = 123', 'ClusterId = 456'))
        with self.assertRaises(ValueError):
            self.begin()
        self.claims.reserve.assert_not_called()

    def test_ordinary_timeout_stays_quarantined_without_an_explicit_review(self):
        with self.assertRaises(FileExistsError):
            shift_data_attempts.begin_attempt(
                self.persistent, self.work, self.request, self.request_sha, [], mock.Mock())
        self.claims.reserve.assert_not_called()

    def run_replacement_worker(self, native_timeout):
        payload = self.task / 'payload'
        payload.mkdir()
        (payload / 'original_helper.py').write_text('# synthetic frozen helper\n')
        self.request.update(schema='shift-data-reconstruction-job-v1', job_name='owned_fixture',
                            batch_name='DATA_Run2023D_ZeroBias_SHIFTReco_20261118_test',
                            events=[dict(run=1, luminosityBlock=2, event=3)],
                            payload_files={'original_helper.py': shift_data_attempts.sha256(payload / 'original_helper.py')})
        request_path = self.persistent / 'request.json'
        request_path.write_text(json.dumps(self.request))
        self.request_sha = shift_data_attempts.sha256(request_path)
        self.guard['request_sha256'] = self.request_sha
        self.previous['request_sha256'] = self.request_sha
        self.write_first_attempt()
        self.write_review()
        child = mock.Mock()
        child.wait.side_effect = ([subprocess.TimeoutExpired('cmsRun', 18000), -9]
                                  if native_timeout else [0])
        def configuration(payload, work, request):
            (work / 'restricted_nano.root').write_bytes(b'synthetic ROOT')
            return work / 'execute_cfg.py'
        original_cwd, original_path = Path.cwd(), list(sys.path)
        try:
            os.chdir(self.work)
            argv = ['data-worker', str(request_path), self.request_sha, '--payload', str(payload),
                    '--timeout-recovery', str(self.receipt_path),
                    '--timeout-recovery-sha256', self.receipt_sha]
            with mock.patch.object(sys, 'argv', argv), \
                    mock.patch.object(WORKER, 'validate_request', return_value=[(1, 2, 3)]), \
                    mock.patch.object(WORKER, 'write_config', side_effect=configuration), \
                    mock.patch.object(WORKER, 'audit_identities', return_value=dict(root_uuid='fixture')) as audit, \
                    mock.patch.object(WORKER.subprocess, 'Popen', return_value=child), \
                    self.assertRaises(SystemExit) as exit_context:
                WORKER.main()
            self.assertEqual(exit_context.exception.code, 124 if native_timeout else 0)
            self.assertEqual(child.wait.call_args_list[0], mock.call(timeout=18000))
            result = json.loads((self.work / 'result.json').read_text())
            guard = json.loads((self.persistent / 'attempt_started.json').read_text())
            self.assertEqual(result['timeout_recovery']['sha256'], self.receipt_sha)
            self.assertEqual(guard['recovery_count'], 1)
            self.assertEqual(guard['recovery']['timeout_recovery'], result['timeout_recovery'])
            self.assertEqual(shift_data_attempts.sha256(request_path), self.request_sha)
            if native_timeout:
                audit.assert_not_called()
                child.kill.assert_called_once_with()
            else:
                audit.assert_called_once_with(self.work / 'restricted_nano.root', [(1, 2, 3)])
        finally:
            os.chdir(original_cwd)
            sys.path[:] = original_path

    def test_replacement_worker_uses_reviewed_limit_and_preserves_original_request(self):
        self.run_replacement_worker(native_timeout=False)

    def test_exhausted_replacement_keeps_its_review_and_failure_evidence(self):
        self.run_replacement_worker(native_timeout=True)


if __name__ == '__main__':
    unittest.main()
