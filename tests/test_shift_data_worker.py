# Code style: AI-code.
"""AI-generated checks for data ownership and sanitized worker metrics."""
import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest
import sys
import os
import subprocess
import tarfile

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))

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


if __name__ == '__main__':
    unittest.main()
