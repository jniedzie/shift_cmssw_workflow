# Code style: AI-code. AI-generated bounded publication-submission regressions.
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import time
import unittest


SOURCE = Path(__file__).resolve().parents[1]/'scripts/submit_mu_qcd_publication_recovery.py'
SPEC = importlib.util.spec_from_file_location('fresh_recovery', SOURCE)
CONTROL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONTROL)


class FakeGuard:
    def __init__(self, manager):
        self.manager, self.calls, self.result, self.book = manager, [], 0, True

    def admit(self, manager, node):
        self.calls.append((manager, node))
        if self.result == 0 and self.book:
            now = time.time()
            CONTROL.save(manager/'admission/decisions'/(node+'.json'),
                         dict(node=node, admitted=True, checked_at_epoch=now))
            CONTROL.save(manager/'admission/pre_reservations.json', {node:{'at_epoch':now}})
        return self.result


class FakeNative:
    def __init__(self, expected):
        self.expected, self.ads, self.commands = expected, [], []
        self.fail_submit, self.bad_readback, self.bad_response = False, False, False

    def query(self, constraint, attributes, **kwargs):
        if constraint == 'Owner == "jniedzie"':
            return copy.deepcopy(self.ads)
        ad = dict(self.expected, ClusterId=900001, ProcId=0, JobStatus=1,
                  ShiftMuRecoveryNonce=self.nonce)
        if self.bad_readback:
            ad['DAGManJobId'] = 12958654
        return [ad]

    def run_condor(self, command, **kwargs):
        self.commands.append((command, kwargs))
        self.nonce = json.loads(command[-1].split(' = ', 1)[1])
        if self.fail_submit:
            raise RuntimeError('Submit response lost; job may exist')
        return 'unparseable' if self.bad_response else '900001.0 - 900001.0\n'


class FreshPublicationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.manager = self.root/'manager'
        (self.manager/'admission/decisions').mkdir(parents=True)
        CONTROL.save(self.manager/'admission/policy.json', {'pre_reservation_seconds':900})
        self.log = self.root/'nodes.log'
        self.log.write_text('001 (12967824.000.000) 10/16 08:27:12 executing\n...\n'
            '009 (12967824.000.000) 10/16 12:45:05 aborted\n'
            'SYSTEM_PERIODIC_REMOVE wall time exceeded allowed max\n...\n')
        self.status = self.root/'nodes.status'
        self.status.write_text('[\n Type = "NodeStatus";\n Node = "L0614";\n NodeStatus = 6;\n]\n')
        expected = dict(Owner='jniedzie', JobUniverse=5, Iwd=str(self.root/'worker'),
            Cmd='/usr/bin/python3', MaxRuntime=7200, ShiftMuEnrichedQCD=True,
            ShiftSuiteTag=CONTROL.SUITE, ShiftMuEnrichedNodeId=CONTROL.NODE,
            ShiftMuPublicationRecovery=True, ShiftMuRecoveryOriginalJobId=CONTROL.JOB)
        self.config = dict(original_job_id=CONTROL.JOB, original_node_id=CONTROL.NODE,
            original_event_log=str(self.log), original_node_status=str(self.status),
            manager_directory=str(self.manager), worker_submit=str(self.root/'worker/worker.sub'),
            worker_directory=str(self.root/'worker'), expected_worker_attributes=expected)
        self.guard, self.native = FakeGuard(self.manager), FakeNative(expected)
        self.receipt = self.root/'transition.json'

    def run_control(self):
        return CONTROL.control(self.config, self.guard, self.native, self.receipt)

    def test_success_is_fresh_native_submit_with_nonce_not_release_or_edit(self):
        self.assertEqual(self.run_control(), 0)
        proof = CONTROL.read(self.receipt)
        self.assertTrue(proof['admission_succeeded'])
        self.assertTrue(proof['submitted'])
        self.assertFalse(proof['science_success_verified'])
        self.assertEqual(proof['new_job_id'], '900001.0')
        self.assertEqual(len(self.native.commands), 1)
        command, options = self.native.commands[0]
        self.assertEqual(command[0], '/usr/bin/condor_submit')
        self.assertEqual(options['cwd'], self.config['worker_directory'])
        self.assertTrue(options['local_schedd'])
        self.assertIsNone(proof['queued_readback'].get('DAGManJobId'))

    def test_guard_deferral_never_submits(self):
        self.guard.result = 75
        self.assertEqual(self.run_control(), 75)
        self.assertFalse(self.native.commands)
        self.assertEqual(CONTROL.read(self.receipt)['phase'], 'deferred')

    def test_missing_real_reservation_never_submits(self):
        self.guard.book = False
        self.assertEqual(self.run_control(), 1)
        self.assertFalse(self.native.commands)

    def test_held_recovery_or_original_source_blocks_admission(self):
        self.native.ads = [dict(ClusterId=123, ProcId=0, JobStatus=5,
                               ShiftMuEnrichedNodeId=CONTROL.NODE)]
        self.assertEqual(self.run_control(), 1)
        self.assertFalse(self.guard.calls)
        self.assertFalse(self.native.commands)

    def test_original_must_have_real_abort_and_unchanged_DAG_error(self):
        self.status.write_text(self.status.read_text().replace('= 6;', '= 5;'))
        self.assertEqual(self.run_control(), 1)
        self.assertFalse(self.guard.calls)

    def test_ambiguous_submission_and_replay_never_duplicate(self):
        self.native.fail_submit = True
        self.assertEqual(self.run_control(), 1)
        proof = CONTROL.read(self.receipt)
        self.assertTrue(proof['submission_outcome_unknown'])
        self.assertEqual(len(self.native.commands), 1)
        with self.assertRaisesRegex(ValueError, 'Prior submit intent'):
            self.run_control()
        self.assertEqual(len(self.native.commands), 1)

    def test_unparseable_submit_response_is_not_retried(self):
        self.native.bad_response = True
        self.assertEqual(self.run_control(), 1)
        self.assertTrue(CONTROL.read(self.receipt)['submit_attempted'])
        self.assertEqual(len(self.native.commands), 1)

    def test_inherited_DAG_attributes_fail_readback_without_another_submit(self):
        self.native.bad_readback = True
        self.assertEqual(self.run_control(), 1)
        self.assertTrue(CONTROL.read(self.receipt)['submitted'])
        self.assertEqual(len(self.native.commands), 1)


if __name__ == '__main__':
    unittest.main()
