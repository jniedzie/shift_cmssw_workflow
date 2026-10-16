# Code style: AI-code. AI-generated explicit cold histogram retry regressions.
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import prepare_shift_stream_retry as module
from prepare_shift_stream_tasks import make_task, request
from run_shift_stream_controller import load_tasks, read, save, sha


class RetryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='stream-retry-', dir=SCRIPTS.parent / 'tmp')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name in ('tasks', 'cohorts', 'frozen'):
            (self.root / name).mkdir()
        worker = self.root / 'frozen/worker.sh'
        worker.write_text('#!/bin/bash\nexit 0\n')
        archive = self.root / 'frozen/frozen_tools.tar.gz'
        archive.write_bytes(b'old tools')
        corrected = self.root / 'corrected/frozen_tools.tar.gz'
        corrected.parent.mkdir()
        corrected.write_bytes(b'corrected histogram tools')
        worker_policy = dict(executable=str(worker), executable_sha256=sha(worker),
                             tools_archive=str(archive), tools_sha256=sha(archive))
        self.policy = dict(tag='stream', worker=worker_policy,
            histogram_worker=worker_policy | {'tools_archive': str(corrected), 'tools_sha256': sha(corrected)})
        save(self.root / 'policy.json', {'tag': 'stream',
            'admission': {'account_owner': 'jniedzie', 'schedd_name': 'bigbird26.cern.ch'}})
        descriptor = self.root / 'nano_cohort.json'
        save(descriptor, {'selected_ids': [[42, 1, 9]], 'full_trials': 1000})
        self.reco = make_task(self.root, self.policy, 'source_reco', 'reco', 'source',
            request('reco', 'source', 'cmssw', ['python3', 'reco.py'], 'report.json', self.policy), [descriptor])
        self.task = make_task(self.root, self.policy | {'histogram_worker': worker_policy},
            'source_histogram', 'histogram', 'source',
            request('histogram', 'source', 'tea', ['python3', 'hist.py'], 'histograms/cohort_complete.json', self.policy),
            [descriptor], ['source_reco'], checks={'descriptor_sha256': sha(descriptor)}, canary=True)
        save(self.task['status_path'], {'complete': False, 'exit_code': 1})
        save(self.task['result_path'], {'complete': False, 'exit_code': 1})
        Path(self.task['event_log']).write_text('001 (321.000.000) execute\n...\n012 (321.000.000) held\n...\n')
        self.ledger = {
            'source_histogram': dict(phase='failed', execution_verified=True, job=[321, 0], nonce='old-hist-nonce',
                task_sha256=sha(self.root / 'tasks/source_histogram.json'),
                queue={'JobStatus': 5, 'ExitBySignal': False, 'ExitCode': 1}),
            'source_reco': dict(phase='complete', job=[123, 0], nonce='unchanged-reco-nonce',
                task_sha256=sha(self.root / 'tasks/source_reco.json'))}
        save(self.root / 'ledger.json', self.ledger)
        self.queue = self.root / 'native_queue.json'
        self.observation = dict(schema='shift-native-owner-queue-receipt-v1', complete=True,
            owner='jniedzie', schedd_name='bigbird26.cern.ch', constraint='Owner == "jniedzie"',
            attributes=sorted(module.QUEUE_ATTRIBUTES), native_exit_code=0, native_stderr='',
            checked_at_epoch=1000, ads=[])
        save(self.queue, self.observation)

    def retry(self):
        return module.retry_histogram(self.root, 'source_histogram', self.policy,
                                      self.queue, 'Reviewed configuration import failure', now=1000)

    def test_retry_preserves_failed_attempt_and_reco_while_resetting_only_histogram(self):
        old_task_hash = sha(self.root / 'tasks/source_histogram.json')
        old_status_hash = sha(self.task['status_path'])
        receipt = self.retry()
        ledger = read(self.root / 'ledger.json')
        task = read(self.root / 'tasks/source_histogram.json')
        self.assertEqual(ledger['source_reco'], self.ledger['source_reco'])
        self.assertEqual(ledger['source_histogram']['phase'], 'waiting')
        self.assertNotIn('nonce', ledger['source_histogram'])
        self.assertEqual(ledger['source_histogram']['attempts'][0]['failed_record']['nonce'], 'old-hist-nonce')
        self.assertEqual(task['task_id'], self.task['task_id'])
        self.assertEqual(task['depends_on'], self.task['depends_on'])
        self.assertEqual(task['completion_checks'], self.task['completion_checks'])
        self.assertTrue(task['working_directory'].endswith('_attempt2'))
        self.assertIn(self.policy['histogram_worker']['tools_archive'], task['input_sha256'])
        self.assertEqual(sha(self.task['status_path']), old_status_hash)
        self.assertEqual(receipt['old_task_sha256'], old_task_hash)
        self.assertTrue(receipt['applied'])
        self.assertFalse(receipt['submission_performed'])
        load_tasks(self.root, ledger)
        self.retry()
        self.assertEqual(len(read(self.root / 'ledger.json')['source_histogram']['attempts']), 1)

    def test_retry_requires_old_supervisors_and_old_failed_job_absent(self):
        for ad in (
            {'Owner': 'jniedzie', 'ClusterId': 321, 'ProcId': 0},
            {'Owner': 'jniedzie', 'JobUniverse': 7, 'Iwd': str(self.root)},
            {'Owner': 'jniedzie', 'ShiftStreamCampaign': 'stream', 'ShiftStreamController': True}):
            with self.subTest(ad=ad):
                save(self.queue, self.observation | {'ads': [ad]})
                with self.assertRaises(ValueError):
                    self.retry()
        self.assertEqual(read(self.root / 'ledger.json'), self.ledger)

    def test_retry_rejects_stale_queue_or_a_completed_receipt(self):
        save(self.queue, self.observation | {'checked_at_epoch': 900})
        with self.assertRaisesRegex(ValueError, 'fresh'):
            self.retry()
        save(self.queue, self.observation)
        save(self.task['result_path'], {'complete': True})
        with self.assertRaisesRegex(ValueError, 'completed scientific attempt'):
            self.retry()

    def test_retry_rejects_fresh_unscoped_empty_queue_receipts(self):
        invalid = [dict(checked_at_epoch=1000, ads=[]),
                   self.observation | {'constraint': 'ClusterId == 999'},
                   self.observation | {'schedd_name': 'other.cern.ch'},
                   self.observation | {'attributes': ['ClusterId']},
                   self.observation | {'native_exit_code': 1},
                   self.observation | {'native_stderr': 'Query failed'}]
        for observation in invalid:
            with self.subTest(observation=observation):
                save(self.queue, observation)
                with self.assertRaisesRegex(ValueError, 'owner-wide query'):
                    self.retry()
        self.assertEqual(read(self.root / 'ledger.json'), self.ledger)

    def test_retry_recovers_after_task_manifest_replacement_before_ledger_write(self):
        real_save = module.save
        failed = False
        def interrupted(path, value):
            nonlocal failed
            if Path(path) == self.root / 'ledger.json' and not failed:
                failed = True
                raise OSError('simulated interruption after the new task manifest')
            return real_save(path, value)
        with mock.patch.object(module, 'save', side_effect=interrupted), self.assertRaises(OSError):
            self.retry()
        receipt = self.retry()
        self.assertTrue(receipt['applied'])
        load_tasks(self.root, read(self.root / 'ledger.json'))
        self.assertEqual(len(read(self.root / 'ledger.json')['source_histogram']['attempts']), 1)


if __name__ == '__main__':
    unittest.main()
