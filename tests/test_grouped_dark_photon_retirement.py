# Code style: AI-code. AI-generated grouped Nano retention safety regressions.
import json
import getpass
import gzip
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import retire_grouped_dark_photon_chunks as retirement


class SemanticChecks(unittest.TestCase):
    def setUp(self):
        try:
            import uproot
            import awkward
            import numpy
        except ImportError:
            self.skipTest('ROOT/uproot analysis runtime is required')
        self.temporary=tempfile.TemporaryDirectory()
        self.root=Path(self.temporary.name)

    def tearDown(self):
        if hasattr(self,'temporary'):
            self.temporary.cleanup()

    def nano(self,name,rows,tag='same tag',extra=False):
        import numpy as np
        import awkward as ak
        import uproot
        path=self.root/name
        with uproot.recreate(path) as output:
            output['tag']=tag
            branches=dict(event='uint64',genWeight='float32',Muon_pt='var * float32')
            if extra:branches['extra']='int32'
            tree=output.mktree('Events',branches)
            for row in rows:
                values=dict(event=np.asarray([row[0]],dtype=np.uint64),
                    genWeight=np.asarray([1.],dtype=np.float32),Muon_pt=ak.Array([row[1]]))
                if extra:values['extra']=np.asarray([1],dtype=np.int32)
                tree.extend(values)
            metadata=output.mktree('MetaData',dict(ProcessHistory='int32'))
            for row in rows:
                metadata.extend(dict(ProcessHistory=np.asarray([row[0]],dtype=np.int32)))
        return path

    def test_all_branch_values_and_ordered_metadata_baskets_survive_grouping(self):
        first=self.nano('first.root',[(1,[2.,3.])])
        second=self.nano('second.root',[(2,[])])
        grouped=self.nano('grouped.root',[(1,[2.,3.]),(2,[])])
        result=retirement.compare_all_branches([first,second],grouped)
        self.assertEqual(result['events'],2)
        self.assertTrue(result['edm_metadata_baskets_preserved'])
        self.assertGreaterEqual(result['event_branches'],4)

    def test_non_identity_branch_mutation_schema_metadata_or_order_blocks_retirement(self):
        first=self.nano('first.root',[(1,[2.,3.])])
        second=self.nano('second.root',[(2,[])])
        alternatives=[dict(rows=[(1,[2.,4.]),(2,[])]),
            dict(rows=[(2,[]),(1,[2.,3.])]),dict(rows=[(1,[2.,3.]),(2,[])],extra=True),
            dict(rows=[(1,[2.,3.]),(2,[])],tag='different')]
        for index,options in enumerate(alternatives):
            with self.subTest(options=options):
                other=self.nano('bad_'+str(index)+'.root',**options)
                with self.assertRaises(ValueError):
                    retirement.compare_all_branches([first,second],other)


class ExactExecution(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.root=Path(self.temporary.name)
        self.production=self.root/'production'
        self.production.mkdir()
        self.prefix='/eos/user/j/jniedzie/shift_cmssw/darkphoton_m2_prompt/20261016-test'
        self.payloads={self.prefix+'/nanoAOD/chunk_000.root':b'raw original',
            self.prefix+'/GEN/gen.root':b'full GEN',
            self.prefix+'/nanoAOD_merged_unscored/ntuple_0.root':b'complete grouped Nano',
            self.prefix+'/nanoAOD_merged/ntuple_0.root':b'complete scored Nano',
            self.prefix+'/histograms_merged/ntuple_0.root':b'complete histogram file'}
        self.artifacts=[]
        for index,(path,contents) in enumerate(self.payloads.items()):
            local=self.root/str(index)
            local.write_bytes(contents)
            self.artifacts.append(dict(retirement.artifact(local),path=path))
        local=self.production/'report.json'
        local.write_text('{}')
        self.local=[retirement.artifact(local)]
        self.plan=dict(schema=retirement.SCHEMA,complete=True,production=str(self.production),
            point='m2_prompt',prefix=self.prefix,production_seal_sha256='f'*64,
            runner_sha256=retirement.support.sha256(retirement.__file__),
            events=1,candidates=self.artifacts[:1],protected_local=self.local,
            protected_remote=self.artifacts[1:],total_candidate_bytes=len(b'raw original'),
            all_branch_equivalence=[dict(complete=True,events=1,edm_metadata_values_preserved=True)])
        self.path=self.root/'plan.json'
        retirement.write(self.path,self.plan)
        self.row=dict(groups=[{}])
        self.authority=patch.object(retirement,'source_authority',return_value=(self.production,{},self.row,
            dict(union=dict(inputs=self.artifacts[:1])),self.local,self.artifacts[1:]))
        self.authority.start()
        self.guard=patch.object(retirement,'scheduler_guard',return_value=dict(complete=True))
        self.guard_mock=self.guard.start()
        payloads=self.payloads
        class Transport:
            removed=[]
            def fetch(inner,item,destination):
                destination=Path(destination)
                destination.write_bytes(payloads[item['path']])
                observed=retirement.artifact(destination)
                retirement.require(observed['bytes']==item['bytes'] and observed['sha256']==item['sha256'],'changed')
                return destination
            def remove(inner,path):
                inner.removed.append(path)
                del payloads[path]
            def absent(inner,path):
                return path not in payloads
        self.transport=Transport()

    def tearDown(self):
        self.authority.stop()
        self.guard.stop()
        self.temporary.cleanup()

    def execute(self):
        return retirement.execute(self.path,retirement.support.sha256(self.path),self.root/'proof.json',
                                  self.root,self.transport)

    def test_only_reviewed_raw_duplicate_is_removed_and_retained_files_are_read_back(self):
        receipt=self.execute()
        self.assertTrue(receipt['complete'])
        self.assertEqual(self.transport.removed,[self.artifacts[0]['path']])
        self.assertEqual(set(self.payloads),{item['path'] for item in self.artifacts[1:]})
        self.assertTrue(receipt['retained_readbacks_verified'])
        # Resume proves absence and does not repeat a successful deletion.
        self.execute()
        self.assertEqual(len(self.transport.removed),1)

    def test_changed_retained_raw_or_local_authority_blocks_every_deletion(self):
        for item in self.artifacts:
            with self.subTest(item=item['path']):
                old=self.payloads[item['path']]
                self.payloads[item['path']]=b'changed'
                with self.assertRaises(ValueError):self.execute()
                self.assertFalse(self.transport.removed)
                self.payloads[item['path']]=old
        Path(self.local[0]['path']).write_text('changed')
        with self.assertRaises(ValueError):self.execute()
        self.assertFalse(self.transport.removed)

    def test_live_or_unknown_scheduler_state_blocks_deletion(self):
        self.guard_mock.side_effect=ValueError('Unknown account coverage or live downstream reader')
        with self.assertRaises(ValueError):self.execute()
        self.assertFalse(self.transport.removed)

    def test_storage_gate_credits_only_closed_finite_receipts(self):
        self.execute()
        path=self.path.with_name('plan_deletion_receipt.json')
        config=dict(directory=str(self.root),maximum_retired_bytes=1000)
        state=dict(targets=dict(canary=dict(retired=True,receipt=str(path))))
        gate=retirement.publish_freed_gate(config,state,'a'*64)
        self.assertEqual(gate['verified_removed_bytes'],len(b'raw original'))
        self.assertEqual(len(gate['receipts']),1)
        receipt=retirement.read(path)
        for change in [dict(closed_epoch=float('inf')),dict(removed_bytes=999),
                       dict(complete=False),dict(retained_readbacks_verified=False)]:
            retirement.write(path,dict(receipt,**change))
            with self.assertRaises(ValueError):
                retirement.publish_freed_gate(config,state,'a'*64)
        retirement.write(path,receipt)

    def test_crash_after_remove_is_reconciled_only_with_the_durable_exact_intent(self):
        original=self.transport.remove
        def interrupted(path):
            original(path)
            raise RuntimeError('Worker interrupted after EOS removal')
        self.transport.remove=interrupted
        with self.assertRaisesRegex(RuntimeError,'interrupted'):
            self.execute()
        receipt_path=self.path.with_name('plan_deletion_receipt.json')
        receipt=retirement.read(receipt_path)
        self.assertFalse(receipt['deleted'])
        intent=receipt['deletion_intents'][self.artifacts[0]['path']]
        self.assertEqual(intent['artifact'],self.artifacts[0])
        self.assertEqual(intent['plan_sha256'],retirement.support.sha256(self.path))
        self.transport.remove=original
        result=self.execute()
        self.assertTrue(result['complete'])
        self.assertTrue(result['deleted'][0]['reconciled_after_durable_intent'])
        self.assertEqual(len(self.transport.removed),1)

    def test_absence_without_an_intent_is_never_credited_as_our_deletion(self):
        del self.payloads[self.artifacts[0]['path']]
        with self.assertRaises((ValueError,KeyError)):
            self.execute()
        self.assertFalse(self.transport.removed)


class DetachedPolicy(unittest.TestCase):
    def test_read_only_account_query_retry_never_accepts_partial_failed_coverage(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'proof.json.gz'
            with patch.object(retirement.native,'query_account',side_effect=[
                    retirement.native.NativeCondorError('unknown coverage'),[]]) as query, \
                 patch.object(retirement.native,'_account_schedds',return_value={'verifiedSchedd'}), \
                 patch.object(retirement.time,'sleep'):
                value=retirement.capture_account_proof(path)
            self.assertEqual(query.call_count,2)
            self.assertEqual(value['ads'],[])
            self.assertTrue(path.is_file())
            path.unlink()
            with patch.object(retirement.native,'query_account',side_effect=retirement.native.NativeCondorError('unknown')), \
                 patch.object(retirement.native,'_account_schedds',return_value={'verifiedSchedd'}), \
                 patch.object(retirement.time,'sleep'):
                with self.assertRaises(retirement.native.NativeCondorError):
                    retirement.capture_account_proof(path)
            self.assertFalse(path.exists())

    def test_controller_waits_without_a_real_reviewed_deletion_canary(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            config=dict(directory=str(root),authorities=[],activation_gate=str(root/'activation.json'))
            state=retirement.controller_step(config,{},'a'*64,root)
            self.assertEqual(state['phase'],'waiting_for_root_reviewed_deletion_canary')
            retirement.write(root/'activation.json',dict(complete=True,controller_seal_sha256='a'*64,
                schema='shift-dp-raw-nano-retirement-activation-v1',initial_receipts={}))
            with self.assertRaises(ValueError):
                retirement.controller_step(config,{},'a'*64,root)

    def test_compact_native_proof_preserves_global_scope_and_rejects_live_consumers(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            user=getpass.getuser()
            value=dict(schema=retirement.guards.PROOF_SCHEMA,complete=True,account_wide=True,
                account=user,captured_epoch=time.time(),query=dict(returncode=0,stderr='',
                    command=['/usr/bin/condor_q','-global'],
                    constraint=f'Owner == "{user}" || AccountingGroupUser == "{user}"'),ads=[])
            proof=root/'proof.json.gz'
            def save():
                with gzip.open(proof,'wt') as stream:json.dump(value,stream)
            save()
            retirement.scheduler_guard(proof,'/closed/production','/eos/sample/campaign')
            value['ads']=[dict(Owner=user,Iwd='/closed/production',Cmd='/usr/bin/python3')]
            save()
            with self.assertRaisesRegex(ValueError,'Live scheduler reference'):
                retirement.scheduler_guard(proof,'/closed/production','/eos/sample/campaign')
            value['ads']=[]
            value['captured_epoch']=time.time()-301
            save()
            with self.assertRaisesRegex(ValueError,'stale'):
                retirement.scheduler_guard(proof,'/closed/production','/eos/sample/campaign')

    def test_frozen_controller_uses_exact_source_worklists_and_failure_holds(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            adaptive,meson=root/'adaptive',root/'meson'
            adaptive.mkdir();meson.mkdir()
            retirement.write(adaptive/'worklist.json',dict(waves=[dict(wave_index=0,batches=[
                dict(point='m2_prompt',seed=123,run_number=456,events=20)])]))
            retirement.write(meson/'config.json',dict(batches=[dict(batch='canary_0',point='m0p3_prompt',
                seed=789,run_number=987,mass_gev=.3,epsilon=.001,source_collisions=4000)]))
            for directory,name in [(adaptive,'worklist.json'),(meson,'config.json')]:
                retirement.write(directory/'seal.json',dict(files={str(directory/name):retirement.support.sha256(directory/name)}))
            output=root/'controller'
            receipt=retirement.prepare_controller(output,adaptive,meson)
            self.assertEqual(receipt['targets'],2)
            config=retirement.read(output/'config.json')
            self.assertEqual(config['targets'][0]['expected']['events'],20)
            self.assertEqual(config['targets'][1]['fixed_source_collisions'],4000)
            descriptor=(output/'controller.sub').read_text()
            self.assertIn('+SendCredential = True',descriptor)
            self.assertIn('+MaxRuntime = 604800',descriptor)
            self.assertIn('periodic_release = False',descriptor)
            self.assertIn('initialdir = '+str(output),descriptor)
            seal=retirement.read(output/'seal.json')
            self.assertEqual(seal['files'][str(adaptive/'worklist.json')],retirement.support.sha256(adaptive/'worklist.json'))


if __name__=='__main__':
    unittest.main()
