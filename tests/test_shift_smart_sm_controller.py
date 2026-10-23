# Code style: AI-code. AI-generated native scheduler and receipt regression checks.
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from run_shift_smart_sm_controller import native_exit, observe


class CompletionTests(unittest.TestCase):
    def test_exact_native_job_is_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'events.log'
            path.write_text('005 (124.000.000) event\nNormal termination (return value 0)\n...\n')
            self.assertEqual(native_exit(path, 124), 0)
            self.assertIsNone(native_exit(path, 123))

    def test_returned_success_without_native_closure_does_not_credit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path/'status.json').write_text(json.dumps(dict(complete=True, exit_code=0)))
            task = dict(directory=tmp, phase='submitted', cluster=123)
            observe(task, [])
            self.assertEqual(task['phase'], 'submitted')

    def test_hold_is_kept_for_review(self):
        task = dict(directory='/persistent/job', phase='submitted', cluster=123)
        observe(task, [dict(ClusterId=123, ProcId=0, Iwd='/persistent/job',
                           JobStatus=5, HoldReason='Actual worker failed')])
        self.assertEqual(task['phase'], 'failed')
        self.assertEqual(task['error'], 'Actual worker failed')

    def test_success_is_bound_to_request_and_native_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path/'events.log').write_text('005 (123.000.000) event\nNormal termination (return value 0)\n...\n')
            (path/'status.json').write_text(json.dumps(dict(complete=True, exit_code=0)))
            (path/'result.json').write_text(json.dumps(dict(complete=True, request_sha256='a'*64)))
            task = dict(directory=tmp, phase='submitted', cluster=123,
                        request_sha256='a'*64, bound_bytes=10000)
            observe(task, [])
            self.assertEqual(task['phase'], 'done')
            task.update(phase='submitted', request_sha256='b'*64)
            observe(task, [])
            self.assertEqual(task['phase'], 'failed')

    def test_uncertain_submission_is_never_repeated(self):
        with self.assertRaisesRegex(RuntimeError, 'Uncertain submission'):
            observe(dict(directory='/persistent/job', phase='submitting'), [])


if __name__ == '__main__':
    unittest.main()
