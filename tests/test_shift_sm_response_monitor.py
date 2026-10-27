# Code style: AI-code. AI-generated tests for the read-only benchmark watchdog.
import copy
import errno
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from monitor_shift_sm_response_benchmark import observe, run_once, write_snapshot
from shift_condor_native import NativeCondorError


def config():
    return dict(manager_id=123, owner='jniedzie', manager_iwd='/afs/test/dag',
        working_directory='/afs/test/watch', final_result_path='/afs/test/result.json',
        expected_plan_sha256='a'*64, heartbeat_path='/afs/test/heartbeat.json',
        failure_path='/afs/test/failure.json', state_path='/afs/test/state.json')


def manager(status=2):
    return dict(ClusterId=123, ProcId=0, Owner='jniedzie', Iwd='/afs/test/dag', JobStatus=status)


def child(status=2):
    return dict(ClusterId=124, ProcId=0, Owner='jniedzie', Iwd='/afs/test/dag/jobs/event',
                JobStatus=status, DAGManJobId=123, HoldReason='test failure')


def terminal():
    return dict(manager(4), ExitCode=0, ExitBySignal=False, DAG_Status=0, DAG_NodesFailed=0)


class ResponseMonitorTest(unittest.TestCase):
    def observation(self, ads, state=None, now=1000, history=None, final=None):
        state = {} if state is None else state
        with patch('monitor_shift_sm_response_benchmark.collector_result', return_value=final):
            record = observe(config(), state, now=now, query_jobs=lambda *a, **k: ads,
                             history=history or (lambda c: []))
        return record, state

    def test_running_manager_and_child_are_healthy(self):
        record, _ = self.observation([manager(), child()])
        self.assertEqual(record['phase'], 'running')
        self.assertFalse(record['terminal'])

    def test_held_child_records_failure_without_action(self):
        record, _ = self.observation([manager(), child(5)])
        self.assertTrue(record['problems'])
        self.assertEqual(record['held_children'][0]['ClusterId'], 124)

    def test_no_children_timer_survives_poll_and_resets(self):
        record, state = self.observation([manager()], now=1000)
        self.assertFalse(record['problems'])
        record, state = self.observation([manager()], state, now=1301)
        self.assertTrue(record['problems'])
        record, state = self.observation([manager(), child()], state, now=1302)
        self.assertNotIn('no_children_since', state)

    def test_missing_and_failed_manager_are_not_success(self):
        record, _ = self.observation([], final={'complete': True})
        self.assertFalse(record['terminal'])
        self.assertIn('absent', record['problems'][0])
        failed = dict(terminal(), ExitCode=1)
        record, _ = self.observation([], history=lambda c: [failed], final={'complete': True})
        self.assertIn('unsuccessfully', record['problems'][0])

    def test_success_needs_complete_collector_and_caches_native_history(self):
        calls = []
        def history(c):
            calls.append(c)
            return [terminal()]
        record, state = self.observation([], history=history)
        self.assertFalse(record['terminal'])
        record, state = self.observation([], state, history=history, final={'complete': True})
        self.assertTrue(record['terminal'])
        self.assertEqual(len(calls), 1)

    def test_wrong_native_identity_never_completes(self):
        wrong = dict(terminal(), Iwd='/afs/unrelated')
        with self.assertRaisesRegex(ValueError, 'identity'):
            self.observation([], history=lambda c: [wrong], final={'complete': True})

    def test_collector_requires_plan_and_full_frozen_draw(self):
        from monitor_shift_sm_response_benchmark import collector_result
        final = dict(complete=True, exit_code=0, complete_frozen_draw_collected=True,
                     benchmark_plan_sha256='a'*64)
        with patch('monitor_shift_sm_response_benchmark.read_json', return_value=final):
            self.assertIsNotNone(collector_result(config()))
        wrong = dict(final, benchmark_plan_sha256='b'*64)
        with patch('monitor_shift_sm_response_benchmark.read_json', return_value=wrong):
            with self.assertRaisesRegex(ValueError, 'different frozen plan'):
                collector_result(config())
        with patch('monitor_shift_sm_response_benchmark.read_json', return_value=dict(final, complete_frozen_draw_collected=False)):
            self.assertIsNone(collector_result(config()))

    def test_scheduler_failure_is_unknown_not_absence(self):
        writes = []
        def failed(*args, **kwargs):
            raise NativeCondorError('query failed')
        record = run_once(config(), {}, observer=failed, writer=lambda p, v: writes.append((p, copy.deepcopy(v))))
        self.assertEqual(record['phase'], 'scheduler_unknown')
        self.assertFalse(record['terminal'])
        self.assertEqual(len(writes), 3)

    def test_long_AFS_outage_retries_same_snapshot_without_repoll(self):
        native_calls = []
        attempts, sleeps = [], []
        def observer(c, s, now=None):
            native_calls.append(now)
            return dict(checked_epoch=now, phase='running', terminal=False, problems=[])
        def failing_write(data):
            attempts.append(data)
            if len(attempts) < 5:
                raise OSError(errno.EIO, 'temporary AFS outage')
        def writer(path, value):
            write_snapshot(path, value, sleep=lambda seconds: sleeps.append(seconds),
                           write_bytes=failing_write)
        run_once(config(), {}, now=1000, observer=observer, writer=writer)
        self.assertEqual(native_calls, [1000])
        self.assertEqual(sum(sleeps), 40)
        self.assertTrue(all(data == attempts[0] for data in attempts[:5]))

    def test_quota_and_permission_failures_are_visible(self):
        for code in (errno.EDQUOT, errno.EACCES):
            def fail(data):
                raise OSError(code, 'semantic storage failure')
            with self.assertRaises(OSError):
                write_snapshot('/afs/test/result', {}, write_bytes=fail)

    def test_internal_runtime_limit_records_failure_without_native_action(self):
        settings = dict(config(), maximum_wall_seconds=60)
        state = dict(monitor_started_epoch=1000)
        writes = []
        def forbidden(*args, **kwargs):
            self.fail('Expired watchdog must not make another native query')
        record = run_once(settings, state, now=1060, observer=forbidden,
                          writer=lambda path, value: writes.append((path, copy.deepcopy(value))))
        self.assertEqual(record['phase'], 'watchdog_runtime_limit')
        self.assertFalse(record['terminal'])
        self.assertEqual(len(writes), 3)
        self.assertEqual(state['first_failure'], record)


if __name__ == '__main__':
    unittest.main()
