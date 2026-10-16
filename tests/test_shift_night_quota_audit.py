# Code style: AI-code. AI-generated tests for split local and remote account observations.
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import run_shift_night_quota_audit as audit


class NightQuotaTests(unittest.TestCase):
    def request(self):
        return dict(nonce='test', account_owner='jniedzie', account_pool='collector',
                    local_schedd_name='local')

    def quota(self):
        return subprocess.CompletedProcess([], 0,
            'space=/eos/user/j/jniedzie/ maxlogicalbytes=100 usedlogicalbytes=10 maxfiles=20 usedfiles=2\n', '')

    def test_worker_discovers_account_but_never_named_queries_local_scheduler(self):
        with patch.object(audit.subprocess, 'run', return_value=self.quota()), \
                patch.object(audit, '_account_schedds', return_value={'local', 'other'}), \
                patch.object(audit, 'query', return_value=[{'ClusterId': 8}]) as query:
            result = audit.audit(self.request())
        self.assertTrue(result['remote_account_coverage_verified'])
        self.assertFalse(result['account_coverage_verified'])
        self.assertEqual(result['excluded_local_schedd'], 'local')
        self.assertEqual(query.call_count, 1)
        self.assertEqual(query.call_args.kwargs['schedd'], 'other')

    def test_changed_scheduler_coverage_cannot_admit_workers(self):
        with patch.object(audit.subprocess, 'run', return_value=self.quota()), \
                patch.object(audit, '_account_schedds', side_effect=[{'local'}, {'local', 'other'}]):
            result = audit.audit(self.request())
        self.assertTrue(result['complete'])
        self.assertFalse(result.get('remote_account_coverage_verified', False))
        self.assertFalse(result['account_coverage_verified'])

    def test_external_commitments_finish_before_the_eos_space_measurement(self):
        request = self.request() | {'external_storage_reservations': [{'production_directory': '/sealed'}]}
        calls = []
        def command(arguments, **options):
            calls.append(arguments[0:2])
            if arguments[0] == '/usr/bin/python3':
                self.assertEqual(options['timeout'], 600)
                return subprocess.CompletedProcess(arguments, 0, '', '')
            return self.quota()
        with patch.object(audit.subprocess, 'run', side_effect=command), \
                patch.object(audit.Path, 'write_text'), \
                patch.object(audit.Path, 'read_text', return_value=json.dumps({'complete': True, 'checked_at_epoch': 998})), \
                patch.object(audit, '_account_schedds', return_value={'local'}):
            result = audit.audit(request)
        self.assertTrue(result['remote_account_coverage_verified'])
        self.assertEqual(calls[0], ['/usr/bin/python3', 'read_shift_external_storage_commitments.py'])
        self.assertEqual(calls[1][0], '/usr/bin/eos')
        self.assertIn('quota_observation_started_at_epoch', result)

    def test_external_reader_timeout_does_not_authorize_storage(self):
        request = self.request() | {'external_storage_reservations': [{'production_directory': '/sealed'}]}
        with patch.object(audit.Path, 'write_text'), \
                patch.object(audit.subprocess, 'run', side_effect=subprocess.TimeoutExpired('reader', 600)), \
                patch.object(audit, '_account_schedds') as discovery:
            result = audit.audit(request)
        self.assertFalse(result['complete'])
        discovery.assert_not_called()


if __name__ == '__main__':
    unittest.main()
