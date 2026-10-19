# Code style: AI-code. AI-generated retained-recovery boundary tests.
"""An audited recovery must preserve its original event slice and seed."""
import copy
import hashlib
import io
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from prepare_dark_photon_chunk_recovery import checked_extract,checked_replay_dependency,validate_replay,validate_storage,validate_replay_paths,guard_publication


class RecoveryBoundary(unittest.TestCase):
    def setUp(self):
        self.ids=[[77,1,3],[77,1,4]]
        self.chunk=dict(chunk=1,skip=2,count=2,job=4000101)
        self.row=dict(point='m2_prompt',chunks=[self.chunk])
        self.parent=dict(event_ids=[[77,1,1],[77,1,2],*self.ids])
        self.item=dict(chunk=1,failed_stage='NANO',expected_event_ids=self.ids,
            root_failure_archive=dict(path='/eos/pinned/source.tar.gz',sha256='a'*64,bytes=100))
        self.replay=dict(complete=True,diagnostic_only=True,point='m2_prompt',chunk=1,
            event_ids=self.ids,detector_job=4000101,nano_seed=630010140,
            source_failure_archive=self.item['root_failure_archive'],
            input_audit=dict(event_ids=self.ids),nano_audit=dict(event_ids=self.ids,sumw=2.))

    def test_full_original_slice_and_seed_are_preserved(self):
        self.assertEqual(validate_replay(self.item,self.replay,self.row,self.parent),self.chunk)

    def test_partial_foreign_or_changed_replay_is_rejected(self):
        for key,value in [('complete',False),('point','m5_prompt'),('chunk',2),
                          ('event_ids',self.ids[:1]),('nano_seed',630010141),
                          ('source_failure_archive',{}),('nano_audit',dict(event_ids=self.ids,sumw=1.)),
                          ('input_audit',dict(event_ids=list(reversed(self.ids))))]:
            changed=copy.deepcopy(self.replay);changed[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):
                validate_replay(self.item,changed,self.row,self.parent)

    def test_only_named_regular_hash_pinned_archive_member_can_be_read(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);payload=b'bounded synthetic ROOT bytes'
            archive=root/'input.tar'
            with tarfile.open(archive,'w') as output:
                member=tarfile.TarInfo('worker/step3.root');member.size=len(payload)
                output.addfile(member,io.BytesIO(payload))
                link=tarfile.TarInfo('worker/link.root');link.type=tarfile.SYMTYPE;link.linkname='/outside'
                output.addfile(link)
            authority=dict(bytes=len(payload),sha256=hashlib.sha256(payload).hexdigest())
            with tarfile.open(archive) as source:
                checked_extract(source,'worker/step3.root',root/'good.root',authority)
                self.assertEqual((root/'good.root').read_bytes(),payload)
                for name,expected in [('worker/step3.root',dict(authority,sha256='b'*64)),
                                      ('worker/link.root',dict(bytes=0,sha256='a'*64)),
                                      ('../outside',authority)]:
                    with self.subTest(name=name),self.assertRaises(ValueError):
                        checked_extract(source,name,root/('bad'+str(len(list(root.iterdir())))),expected)

    def test_recorded_scheduler_repair_does_not_allow_any_science_change(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'replay.sub'
            current='universe = vanilla\n+JobFlavour = "tomorrow"\nqueue\n'
            removed='requirements = (TARGET.OpSysMajorVer == 9)\n'
            previous=current.replace('+JobFlavour',removed+'+JobFlavour',1)
            path.write_text(current)
            digest=hashlib.sha256(current.encode()).hexdigest()
            authority=dict(bytes=len(previous),sha256=hashlib.sha256(previous.encode()).hexdigest())
            request=dict(reviewed_replay_submit_changes={str(path):dict(current_sha256=digest,removed_line=removed)})
            self.assertEqual(checked_replay_dependency(path,authority,request),digest)
            path.write_text(current+'changed physics\n')
            with self.assertRaises(ValueError):checked_replay_dependency(path,authority,request)

    def test_duplicate_replay_inputs_and_changed_science_dependency_fail(self):
        with self.assertRaises(ValueError):validate_replay_paths(['/tmp/same','/tmp/../tmp/same'])
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'replay_nano.py';path.write_text('exact original science\n')
            authority=dict(bytes=path.stat().st_size,sha256=hashlib.sha256(path.read_bytes()).hexdigest())
            checked_replay_dependency(path,authority,{})
            path.write_text('changed science\n')
            with self.assertRaises(ValueError):checked_replay_dependency(path,authority,{})

    def test_storage_sums_and_limits_fail_closed(self):
        valid=dict(storage_protection=dict(free_floor_bytes=100000000000,adaptive=137974600000,meson=28922000000),
            protected_bytes=266896600000,recovery_forecast_bytes=500000000,audit_scratch_limit_bytes=100000000)
        validate_storage(valid)
        for key,value in [('protected_bytes',100000000000),('recovery_forecast_bytes',500000001),
                          ('audit_scratch_limit_bytes',100000001),('audit_scratch_limit_bytes',0)]:
            changed=copy.deepcopy(valid);changed[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):validate_storage(changed)

    def test_shared_publication_limit_counts_each_distinct_immutable_output_once(self):
        import json
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);prefix='/eos/user/j/jniedzie/shift_cmssw/darkphoton_m2_prompt/recovery'
            (root/'plan.json').write_text(json.dumps(dict(points=[dict(final_output_base=prefix)],maximum_recovery_publication_bytes=5)))
            source=root/'data';source.write_bytes(b'123')
            worker=SimpleNamespace(B=root,read=lambda p:json.loads(Path(p).read_text()),publish=lambda *args:True)
            guard_publication(worker)
            self.assertTrue(worker.publish(source,prefix+'/first',root))
            self.assertTrue(worker.publish(source,prefix+'/first',root))
            with self.assertRaisesRegex(ValueError,'total publication'):worker.publish(source,prefix+'/second',root)
            with self.assertRaisesRegex(ValueError,'namespace'):worker.publish(source,'/eos/outside',root)
            source.write_bytes(b'new')
            with self.assertRaisesRegex(ValueError,'changed'):worker.publish(source,prefix+'/first',root)


if __name__=='__main__':unittest.main()
