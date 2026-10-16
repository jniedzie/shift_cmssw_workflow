# Code style: AI-code. AI-generated exact six-source release safety tests.
import copy
import importlib.util
from pathlib import Path
import tempfile
import time
import unittest


SOURCE = Path(__file__).resolve().parents[1]/'scripts/release_mu_qcd_saved_publications.py'
SPEC = importlib.util.spec_from_file_location('release_saved', SOURCE)
CONTROL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONTROL)


class FakeGuard:
    def __init__(self, manager):
        self.manager, self.calls = manager, []
        self.status, self.book, self.expire = 0, True, False

    def admit(self, manager, node):
        self.calls.append(node)
        if self.status == 0 and self.book:
            now = time.time()
            CONTROL.save(manager/'admission/decisions'/(node+'.json'),
                         dict(node=node, admitted=True, checked_at_epoch=now))
            path = manager/'admission/pre_reservations.json'
            rows = CONTROL.read(path) if path.exists() else {}
            rows[node] = {'at_epoch':now-1000 if self.expire else now}
            CONTROL.save(path, rows)
        return self.status


class FakeNative:
    def __init__(self, rows):
        self.rows = copy.deepcopy(rows)
        self.edits, self.releases = [], []
        self.fail_release, self.alter_resource = False, False

    def query(self, constraint, attributes, **kwargs):
        if constraint != 'Owner == "jniedzie"':
            raise AssertionError('Owner-wide native coverage is required')
        return copy.deepcopy(self.rows)

    def edit(self, job, attributes, **kwargs):
        self.edits.append((job, attributes))
        ad = next(row for row in self.rows if CONTROL.identity(row) == job)
        ad.update(attributes)
        if self.alter_resource:
            ad['RequestMemory'] = 12000
        return 'native transfer-only edit accepted'

    def run_condor(self, command, **kwargs):
        if command[:1] != ['/usr/bin/condor_release']:
            raise AssertionError('Only exact release is allowed; never submit/remove')
        self.releases.append(command[1])
        if self.fail_release:
            raise RuntimeError('Native release response lost')
        next(row for row in self.rows if CONTROL.identity(row)==command[1])['JobStatus'] = 1
        return 'native release accepted'


class ReleaseSavedTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        manager = self.root/'manager'
        (manager/'admission/decisions').mkdir(parents=True)
        CONTROL.save(manager/'admission/policy.json', {'pre_reservation_seconds':900})
        self.config = dict(manager_directory=str(manager), workers=[])
        self.bindings = dict(schema='shift-held-GEN-publication-recovery-bindings-v1',jobs={})
        ads = []
        for index, (node, original) in enumerate(CONTROL.ORIGINALS.items()):
            expected = dict(Owner='jniedzie', JobUniverse=5, Iwd='/afs/synthetic-recovery/'+node,
                Cmd='/usr/bin/python3', Args='saved-only --publish', TransferInput='exact_inputs',
                TransferOutput='result.json,artifacts.zip', TransferOutputRemaps='exact_outputs',
                WhenToTransferOutput='ON_EXIT', RequestCpus=2, RequestMemory=6000,
                RequestDisk=3000000,MaxRuntime=7200,ShiftMuEnrichedQCD=True,
                ShiftSuiteTag='qcd_mu_enriched_20261015_v1',ShiftMuEnrichedNodeId=node,
                ShiftMuPublicationRecovery=True,ShiftMuRecoveryOriginalJobId=original,
                ShiftMuRecoveryNonce='nonce'+str(index),PeriodicRelease=False)
            row=dict(node_id=node,nonce=expected['ShiftMuRecoveryNonce'],expected_attributes=expected)
            self.config['workers'].append(row)
            self.bindings['jobs'][node]=str(900100+index)+'.0'
            ads.append(dict(expected,ClusterId=900100+index,ProcId=0,JobStatus=5,
                NumJobStarts=0,CurrentHosts=0,RemoteWallClockTime=0,
                WhenToTransferOutput='ON_EXIT_OR_EVICT'))
        self.native, self.guard = FakeNative(ads), FakeGuard(manager)
        self.receipt = self.root/'transition.json'

    def run_control(self):
        return CONTROL.control(self.config,self.bindings,self.guard,self.native,self.receipt)

    def test_exact_six_admitted_edited_read_back_and_released_once(self):
        self.assertEqual(self.run_control(),0)
        self.assertEqual(len(self.guard.calls),6)
        self.assertEqual(len(self.native.edits),6)
        self.assertEqual(len(self.native.releases),6)
        self.assertTrue(all(edit=={'WhenToTransferOutput':'ON_EXIT'} for _,edit in self.native.edits))
        result=CONTROL.read(self.receipt)
        self.assertFalse(result['submitted_new_jobs'])
        self.assertFalse(result['science_success_verified'])
        self.assertTrue(all(row['released'] for row in result['jobs'].values()))

    def test_deferral_never_edits_or_releases(self):
        self.guard.status=75
        self.assertEqual(self.run_control(),75)
        self.assertFalse(self.native.edits)
        self.assertFalse(self.native.releases)

    def test_unknown_guard_or_missing_claim_leaves_held(self):
        self.guard.book=False
        self.assertEqual(self.run_control(),1)
        self.assertFalse(self.native.edits)
        self.assertFalse(self.native.releases)

    def test_expired_claim_leaves_held(self):
        self.guard.expire=True
        self.assertEqual(self.run_control(),1)
        self.assertFalse(self.native.edits)
        self.assertFalse(self.native.releases)

    def test_old_original_still_queued_blocks_every_mutation(self):
        cluster=int(next(iter(CONTROL.ORIGINALS.values())).split('.')[0])
        self.native.rows.append(dict(ClusterId=cluster,ProcId=0,JobStatus=5))
        self.assertEqual(self.run_control(),1)
        self.assertFalse(self.guard.calls)
        self.assertFalse(self.native.edits)

    def test_duplicate_source_or_wrong_nonce_blocks_before_admission(self):
        self.native.rows[0]['ShiftMuRecoveryNonce']='wrong'
        self.assertEqual(self.run_control(),1)
        self.assertFalse(self.guard.calls)

    def test_any_started_job_blocks_full_preflight(self):
        self.native.rows[-1]['NumJobStarts']=1
        self.assertEqual(self.run_control(),1)
        self.assertFalse(self.guard.calls)
        self.assertFalse(self.native.releases)

    def test_changed_protected_resource_after_edit_never_releases(self):
        self.native.alter_resource=True
        self.assertEqual(self.run_control(),1)
        self.assertEqual(len(self.native.edits),1)
        self.assertFalse(self.native.releases)

    def test_lost_release_response_and_replay_cannot_duplicate(self):
        self.native.fail_release=True
        self.assertEqual(self.run_control(),1)
        self.assertEqual(len(self.native.releases),1)
        with self.assertRaisesRegex(ValueError,'Prior edit/release intent'):
            self.run_control()
        self.assertEqual(len(self.native.releases),1)

    def test_bindings_reject_duplicate_or_original_jobs(self):
        keys=list(self.bindings['jobs'])
        self.bindings['jobs'][keys[1]]=self.bindings['jobs'][keys[0]]
        with self.assertRaisesRegex(ValueError,'six distinct fresh'):
            self.run_control()
        self.assertFalse(self.guard.calls)


if __name__ == '__main__':
    unittest.main()
