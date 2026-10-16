# Code style: AI-code. AI-generated one-shot recovery transition regressions.
import copy
import importlib.util
from pathlib import Path
import tempfile
import time
import unittest


SOURCE = Path(__file__).resolve().parents[1]/'scripts/run_mu_qcd_publication_recovery_control.py'
SPEC = importlib.util.spec_from_file_location('recovery_control', SOURCE)
CONTROL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONTROL)


class FakeNative:
    def __init__(self, ad, manager):
        self.ad, self.manager = copy.deepcopy(ad), manager
        self.edits, self.releases = [], []
        self.fail_edit = False
        self.corrupt_readback = False

    def query(self, constraint, attributes, **kwargs):
        if 'ClusterId == 12958654' in constraint:
            return [dict(ClusterId=CONTROL.PARENT, ProcId=0, JobStatus=2, Iwd=str(self.manager))]
        return [copy.deepcopy(self.ad)]

    def edit(self, job, attributes, **kwargs):
        self.edits.append((job, attributes, kwargs.get('dry_run', False)))
        if kwargs.get('dry_run'):
            return 'native dry-run accepted'
        if self.fail_edit:
            raise RuntimeError('native edit failed')
        self.ad.update(attributes)
        if self.corrupt_readback:
            self.ad['MaxRuntime'] = 5
        return 'native edits accepted'

    def run_condor(self, command, **kwargs):
        self.releases.append(command)
        self.ad['JobStatus'] = 1
        return 'native release accepted'


class FakeGuard:
    def __init__(self, manager, result=0, book=True):
        self.manager, self.result, self.book = manager, result, book
        self.calls = []

    def admit(self, manager, node):
        self.calls.append((manager, node))
        if self.result == 0 and self.book:
            now = time.time()
            CONTROL.save(self.manager/'admission/decisions'/(node+'.json'),
                         dict(admitted=True, node=node, checked_at_epoch=now))
            CONTROL.save(self.manager/'admission/pre_reservations.json', {node:{'at_epoch':now}})
        return self.result


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.manager = self.root/'manager'
        (self.manager/'admission/decisions').mkdir(parents=True)
        CONTROL.save(self.manager/'admission/policy.json', {'pre_reservation_seconds':900})
        self.original = dict(ClusterId=12967824, ProcId=0, Owner='jniedzie', JobUniverse=5,
            JobStatus=5, NumJobStarts=1, ExitCode=1, DAGManJobId=CONTROL.PARENT,
            DAGNodeName='L0614', Iwd=str(self.manager/'ordinary'), Cmd='/usr/bin/python3',
            Args='original generator worker', TransferInput='old_input',
            TransferOutput='result.json,artifacts.zip', TransferOutputRemaps='old_output_map',
            WhenToTransferOutput='ON_EXIT_OR_EVICT', MaxRuntime=7200, RequestMemory=6000,
            RequestDisk=3000000, Out='original.out', Err='original.err', UserLog='original.events',
            ShiftMuEnrichedQCD=True, ShiftSuiteTag='qcd_mu_enriched_20261015_v1',
            ShiftMuEnrichedNodeId=CONTROL.NODE)
        self.row = dict(job_id=CONTROL.JOB, node_id=CONTROL.NODE, DAG_manager=CONTROL.PARENT,
            DAG_node='L0614', expected_current_state={key:self.original[key] for key in
            ('JobStatus','NumJobStarts','ExitCode','Iwd','Cmd')})
        self.overrides = dict(Args='publication recovery --publish', TransferInput='pinned_inputs',
            TransferOutputRemaps='preserved_failure_separate_attempt', WhenToTransferOutput='ON_EXIT',
            Out='attempt.out', Err='attempt.err', ShiftMuPublicationRecovery=True,
            ShiftMuOriginalFailedReceiptSHA256='a'*64)
        self.native = FakeNative(self.original, self.manager)
        self.guard = FakeGuard(self.manager)
        self.receipt = self.root/'transition.json'

    def run_control(self):
        return CONTROL.control(self.manager, self.row, self.overrides, self.original,
                               self.guard, self.native, self.receipt)

    def test_guard_deferral_changes_no_job_and_releases_nothing(self):
        self.guard.result = 75
        self.assertEqual(self.run_control(), 75)
        self.assertFalse(self.native.edits)
        self.assertFalse(self.native.releases)
        self.assertEqual(CONTROL.read(self.receipt)['phase'], 'deferred')

    def test_held_source_and_native_parent_are_checked_before_admission(self):
        self.native.ad['JobStatus'] = 1
        self.assertEqual(self.run_control(), 1)
        self.assertFalse(self.guard.calls)
        self.assertFalse(self.native.edits)
        self.assertFalse(self.native.releases)

    def test_success_requires_real_reservation_then_edit_readback_release(self):
        self.assertEqual(self.run_control(), 0)
        result = CONTROL.read(self.receipt)
        self.assertTrue(result['admission_succeeded'])
        self.assertTrue(result['applied'])
        self.assertTrue(result['released'])
        self.assertFalse(result['science_success_verified'])
        self.assertEqual(self.native.releases, [['/usr/bin/condor_release', CONTROL.JOB]])
        self.assertEqual([dry for _,_,dry in self.native.edits], [True,False])
        self.assertEqual(self.native.ad['Iwd'], self.original['Iwd'])
        self.assertEqual(self.native.ad['DAGManJobId'], CONTROL.PARENT)
        self.assertEqual(self.native.ad['ExitCode'], 1)
        self.assertEqual(self.native.ad['NumJobStarts'], 1)
        self.assertFalse({'JobStatus','ExitCode','ExitStatus','NumJobStarts'} & set(self.overrides))

    def test_unrecorded_admission_success_cannot_release(self):
        self.guard.book = False
        self.assertEqual(self.run_control(), 1)
        self.assertFalse(self.native.edits)
        self.assertFalse(self.native.releases)

    def test_edit_failure_keeps_original_job_held_and_reservation_intact(self):
        self.native.fail_edit = True
        self.assertEqual(self.run_control(), 1)
        result = CONTROL.read(self.receipt)
        self.assertTrue(result['admission_succeeded'])
        self.assertFalse(result['released'])
        self.assertFalse(self.native.releases)
        self.assertEqual(self.native.ad['JobStatus'], 5)
        self.assertIn(CONTROL.NODE, CONTROL.read(self.manager/'admission/pre_reservations.json'))

    def test_changed_protected_resource_on_readback_cannot_release(self):
        self.native.corrupt_readback = True
        self.assertEqual(self.run_control(), 1)
        self.assertFalse(self.native.releases)
        self.assertEqual(self.native.ad['JobStatus'], 5)

    def test_unknown_guard_result_changes_no_job(self):
        self.guard.result = 1
        self.assertEqual(self.run_control(), 1)
        self.assertFalse(self.native.edits)
        self.assertFalse(self.native.releases)


if __name__ == '__main__':
    unittest.main()
