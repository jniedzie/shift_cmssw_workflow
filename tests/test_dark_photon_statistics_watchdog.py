# Code style: AI-code. AI-generated checks for the read-only production watchdog.
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location('dp_watchdog', SCRIPTS / 'watch_dark_photon_statistics.py')
WATCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(WATCH)


class StatisticsWatchdogTest(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.root = Path(self.scratch.name)
        self.addCleanup(self.scratch.cleanup)
        worklist = self.root / 'worklist.json'
        worklist.write_text(json.dumps(dict(total_additional_events=417100)))
        self.config = dict(production_directory=str(self.root), worklist=str(worklist),
                           owner='jniedzie', condor_pool='pool.example', condor_schedd='schedd.example',
                           initial_gen_manager_cluster=100, initial_supervisor_cluster=200,
                           queue_warning_seconds=7200, queue_critical_seconds=21600,
                           heartbeat_warning_seconds=1200, gen_progress_warning_seconds=14400,
                           detector_progress_warning_seconds=50400)
        self.now = 200000.
        self.directory = self.root / 'gen_stage'
        self.directory.mkdir()
        self.receipts = {100: dict(directory=str(self.directory), receipt='fake', kind='gen_stage', submitted=self.now - 120)}
        self.counts = dict(planned_events=60, completed_events=0, started_receipts=0,
                           failed_receipts=0, complete_receipts=0, completed_points=0,
                           failed_files=[], latest_receipt_epoch=0)

    def assess(self, children, events=None, previous=None, manager_age=120):
        manager = dict(ClusterId=100, ProcId=0, JobStatus=2, Iwd=str(self.directory), JobStartDate=self.now - manager_age)
        with patch.object(WATCH, 'stage_counts', return_value=self.counts), patch.object(WATCH, 'execute_events', return_value=events or []):
            return WATCH.assess(self.config, previous or {}, [manager, *children], [], self.receipts, self.now)

    def child(self, **kwargs):
        return dict(ClusterId=101, ProcId=0, DAGManJobId=100, JobStatus=kwargs.get('status', 1),
                    Iwd=kwargs.get('iwd', str(self.directory)), NumJobStarts=kwargs.get('starts', 0),
                    QDate=self.now - kwargs.get('age', 120))

    def test_first_minutes_in_queue_are_not_a_failure_or_execution_claim(self):
        report = self.assess([self.child()])
        self.assertEqual(report['health'], 'ok')
        self.assertEqual(report['observed_state'], 'awaiting_payload_execution')

    def test_thirteen_hours_queued_is_critical(self):
        report = self.assess([self.child(age=13 * 3600)])
        self.assertEqual(report['health'], 'critical')
        self.assertTrue(any(item['code'] == 'queue_101' for item in report['issues']))

    def test_running_classad_alone_does_not_prove_execution(self):
        report = self.assess([self.child(status=2, starts=1)])
        self.assertFalse(report['stages']['100']['execution_verified'])

    def test_dag_execute_event_proves_child_execution(self):
        report = self.assess([self.child(status=2, starts=1)], ['101.0'])
        self.assertTrue(report['stages']['100']['execution_verified'])

    def test_wrong_worker_scratch_iwd_is_immediately_critical(self):
        report = self.assess([self.child(iwd='/pool/condor/dir_123')])
        self.assertTrue(any(item['code'] == 'wrong_iwd_101' and item['severity'] == 'critical' for item in report['issues']))

    def test_held_child_is_detected_even_with_a_running_manager(self):
        report = self.assess([self.child(status=5)])
        self.assertTrue(any(item['code'] == 'held_101' for item in report['issues']))

    def test_brief_no_child_transition_does_not_inherit_full_manager_age(self):
        self.counts.update(complete_receipts=3, completed_events=60, latest_receipt_epoch=self.now)
        report = self.assess([], manager_age=13 * 3600)
        self.assertFalse(any(item['code'].startswith('no_children') for item in report['issues']))

    def test_no_child_condition_persists_across_polls(self):
        old = self.assess([])
        old['stages']['100']['no_children_since_epoch'] = self.now - 3700
        report = self.assess([], previous=old)
        self.assertTrue(any(item['code'] == 'no_children_100' and item['severity'] == 'critical' for item in report['issues']))

    def test_stale_supervisor_heartbeat_is_reported(self):
        (self.root / 'state.json').write_text(json.dumps(dict(checked_utc='1970-01-01T00:00:00+00:00')))
        report = self.assess([self.child()])
        self.assertTrue(any(item['code'] == 'supervisor_heartbeat' for item in report['issues']))

    def test_history_uses_one_exact_match_and_caches_terminal_managers(self):
        receipts = dict(self.receipts)
        receipts[300] = dict(self.receipts[100])
        queued = [dict(ClusterId=100, ProcId=0)]
        ended = [dict(ClusterId=300, ProcId=0, JobStatus=4, ExitCode=0)]
        with patch.object(WATCH, 'scheduler_options', return_value=({}, [])), \
                patch.object(WATCH, 'query', return_value=queued), \
                patch.object(WATCH, 'run_condor', return_value=json.dumps(ended)) as command:
            rows, history, _ = WATCH.scheduler_snapshot(self.config, receipts, {})
            argv = command.call_args.args[0]
            self.assertIn('300.0', argv)
            self.assertEqual(argv[argv.index('-limit') + 1], '1')
            self.assertEqual(history, ended)
            command.reset_mock()
            WATCH.scheduler_snapshot(self.config, receipts, dict(terminal_history=history))
            command.assert_not_called()

    def test_rollover_reads_the_new_supervisor_receipt_without_rewriting_the_old_one(self):
        old = self.root / 'submission.json'
        old.write_text(json.dumps(dict(cluster=200, utc='1970-01-01T00:00:00+00:00')))
        new = self.root / 'controller_v2/submission.json'
        new.parent.mkdir()
        new.write_text(json.dumps(dict(cluster=300, utc='1970-01-01T00:00:00+00:00')))
        receipts = WATCH.manager_receipts(self.root, new)
        self.assertEqual(set(receipts), {300})
        self.assertEqual(receipts[300]['kind'], 'supervisor')
        self.assertEqual(receipts[300]['directory'], str(new.parent))
        self.assertEqual(WATCH.read(old)['cluster'], 200)

    def test_only_the_exact_held_or_terminal_retired_supervisor_is_ignored(self):
        self.config['retired_supervisors'] = {'200': str(self.root)}
        for status, iwd, expected in ((5, str(self.root), 'ok'), (3, str(self.root), 'ok'),
                                     (4, str(self.root), 'ok'), (2, str(self.root), 'critical'),
                                     (1, str(self.root), 'critical'), (5, '/pool/condor/wrong', 'critical')):
            with self.subTest(status=status, iwd=iwd):
                row = dict(ClusterId=200, ProcId=0, JobStatus=status, Iwd=iwd)
                report = self.assess([self.child(), row])
                self.assertEqual(report['health'], expected)
                if expected == 'critical':
                    self.assertTrue(any(item['code'] == 'retired_supervisor_200' for item in report['issues']))

    def test_meson_batch_receipts_distinguish_source_worker_and_detector_manager(self):
        source = self.root / 'batches/b000'
        detector = source / 'production'
        detector.mkdir(parents=True)
        for path, cluster in ((source, 101), (detector, 102)):
            (path/'submission.json').write_text(json.dumps(dict(cluster=cluster, utc='1970-01-01T00:00:00+00:00')))
        receipts = WATCH.manager_receipts(self.root)
        self.assertEqual(receipts[101]['kind'], 'meson_source')
        self.assertEqual(receipts[102]['kind'], 'production')
        (source/'source_status.json').write_text(json.dumps(dict(source_collisions=10000, complete=True, events=23)))
        counts = WATCH.stage_counts(source)
        self.assertEqual(counts['completed_events'], 23)
        self.assertEqual(counts['planned_source_collisions'], 10000)
        self.assertEqual(counts['complete_receipts'], 1)

    def test_meson_watch_keeps_source_M_separate_from_actual_signal_N(self):
        worklist = Path(self.config['worklist'])
        worklist.write_text(json.dumps(dict(schema='shift-dp-meson-production-v1', batches=[dict(
            batch='b000', point='m0p3_prompt', source_collisions=10000, expected_signal_events=25)])))
        (self.root/'state.json').write_text(json.dumps(dict(checked_utc='1970-01-03T07:33:20+00:00',
            complete=True, batches={'b000':{'published':{'events':23}}})))
        self.receipts[100]['kind'] = 'meson_source'
        self.counts.update(completed_events=23, complete_receipts=1,
                           planned_source_collisions=10000, source_heartbeat_epoch=self.now)
        report = self.assess([])
        self.assertTrue(report['controller_finished'])
        self.assertEqual(report['actual_planned_progress']['gen_completed_events'], 23)
        self.assertEqual(report['actual_planned_progress']['authorized_source_collisions'], 10000)
        self.assertEqual(report['actual_planned_progress']['expected_signal_events'], 25)
        self.assertIsNone(report['actual_planned_progress']['authorized_new_events'])
        self.assertFalse(any(item['code'].startswith('no_children') for item in report['issues']))

    def test_fresh_source_heartbeat_does_not_hide_stalled_physics(self):
        self.receipts[100]['kind'] = 'meson_source'
        self.counts.update(started_receipts=1, source_ledger_bytes=1000,
                           source_heartbeat_epoch=self.now - 30, source_phase='GEN')
        previous = self.assess([])
        previous['stages']['100']['last_progress_epoch'] = self.now - 15000
        self.counts['source_heartbeat_epoch'] = self.now
        report = self.assess([], previous=previous)
        self.assertTrue(any(item['code'] == 'stale_source_progress_100' for item in report['issues']))
        self.assertFalse(any(item['code'] == 'source_heartbeat_100' for item in report['issues']))

    def test_missing_or_mismatched_explicit_supervisor_receipt_fails_closed(self):
        receipt = self.root / 'controller_v2/submission.json'
        self.config['supervisor_receipt'] = str(receipt)
        with self.assertRaisesRegex(ValueError, 'receipt is missing'):
            WATCH.poll(self.config, {})
        receipt.parent.mkdir()
        receipt.write_text(json.dumps(dict(cluster=300, utc='1970-01-01T00:00:00+00:00')))
        with patch.object(WATCH, 'scheduler_snapshot') as query:
            with self.assertRaisesRegex(ValueError, 'configured native cluster'):
                WATCH.poll(self.config, {})
            query.assert_not_called()


if __name__ == '__main__':
    unittest.main()
