import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import cleanup_condor_logs as cleanup

class CleanupTest(unittest.TestCase):
    def test_old_campaigns_but_not_live_logs_or_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ('old', 'new'):
                (root / name).mkdir()
            for name in ('old/condor_1.0.out', 'new/condor_2.0.err', 'new/condor_3.log', 'old/notes.txt'):
                (root / name).write_text('test')
            (root / 'old/condor_4.log').symlink_to(root / 'old/notes.txt')
            jobs = [dict(ClusterId=2, UserLog=str(root / 'new/condor_3.log'))]
            rows = cleanup.candidates(root, jobs, time.time_ns())
            self.assertEqual([r[0].name for r in rows], ['condor_1.0.out'])
            self.assertEqual(cleanup.remove_unchanged(rows)[0], 1)
            for name in ('new/condor_2.0.err', 'new/condor_3.log', 'old/notes.txt'):
                self.assertTrue((root / name).exists())

    def test_changed_and_new_files_are_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / 'condor_1.log'
            path.write_text('old')
            self.assertEqual(cleanup.candidates(root, [], 0), [])
            rows = cleanup.candidates(root, [], time.time_ns())
            path.write_text('changed')
            self.assertEqual(cleanup.remove_unchanged(rows), (0, 0))

    def test_global_query_includes_frozen_wrappers(self):
        response = subprocess.CompletedProcess([], 0, json.dumps([dict(ClusterId=2)]), '')
        with patch.object(cleanup.subprocess, 'run', return_value=response) as run:
            self.assertEqual(cleanup.active_jobs(), [dict(ClusterId=2)])
        self.assertIn('-global', run.call_args.args[0])
        self.assertNotIn('Cmd', str(run.call_args.args[0]))

    def test_incomplete_or_warning_response_rejected(self):
        for output, error in [('[]', 'scheduler unavailable'), ('bad json', ''), ('[{}]', '')]:
            response = subprocess.CompletedProcess([], 0, output, error)
            with patch.object(cleanup.subprocess, 'run', return_value=response):
                with self.assertRaises(ValueError):
                    cleanup.active_jobs()

    def test_queue_failure_never_removes_files(self):
        root = Path(cleanup.__file__).resolve().parents[1] / 'condor/logs'
        with patch.object(sys, 'argv', ['cleanup', str(root / 'test'), '/unused/payload', 'wrapper']), \
             patch.object(cleanup, 'active_jobs', side_effect=subprocess.TimeoutExpired('condor_q', 45)), \
             patch.object(cleanup, 'remove_unchanged') as remove:
            cleanup.main()
            remove.assert_not_called()

    def test_unrelated_directory_is_rejected_before_queue_or_deletion(self):
        with patch.object(sys, 'argv', ['cleanup', '/tmp', '/unused/payload', 'wrapper']), \
             patch.object(cleanup, 'active_jobs') as query, \
             patch.object(cleanup, 'remove_unchanged') as remove:
            with self.assertRaises(ValueError):
                cleanup.main()
            query.assert_not_called()
            remove.assert_not_called()

    def test_job_appearing_on_second_query_is_protected(self):
        root = Path(cleanup.__file__).resolve().parents[1] / 'condor/logs'
        row = (root / 'old/condor_123.log', 1, 1, 1)
        with patch.object(sys, 'argv', ['cleanup', str(root / 'test'), '/unused/payload', 'wrapper']), \
             patch.object(cleanup, 'active_jobs', side_effect=[[], [dict(ClusterId=123)]]), \
             patch.object(cleanup, 'candidates', side_effect=[[row], []]), \
             patch.object(cleanup, 'remove_unchanged', return_value=(0, 0)) as remove:
            cleanup.main()
            remove.assert_called_once_with([])

if __name__ == '__main__':
    unittest.main()
