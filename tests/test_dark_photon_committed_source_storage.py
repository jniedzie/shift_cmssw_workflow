# Code style: AI-code. Credit paid source objects without inventing detector exposure.
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import run_dark_photon_meson_production as production

class SourceStorageTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.directory=self.root/'batches/b0'
        (self.directory/'gen').mkdir(parents=True)
        self.batch=dict(batch='b0',point='m0p25_lab_0p01m',source_collisions=50000)
        self.config=dict(directory=str(self.root),eos_base='/eos/user/j/test',campaign_prefix='campaign')
        self.entry=dict(source_cluster=42)
        self.write(self.root/'seal.json',dict(files={}))
        self.write(self.directory/'submission.json',dict(cluster=42,task=str(self.directory)))
        self.write(self.directory/'gen/manifest.json',dict(output_sha256='a'*64))
        base='/eos/user/j/test/darkphoton_m0p25_lab_0p01m/campaign-b0'
        gen=dict(path=base+'/GEN/gen.root',bytes=10,sha256='a'*64,readback_sha256='a'*64)
        archive=dict(path=base+'/metadata/GEN/gen_metadata.tar.gz',bytes=20,
                     sha256='b'*64,readback_sha256='b'*64)
        self.write(self.directory/'gen/root_publication.json',gen)
        self.status=dict(complete=True,batch='b0',point=self.batch['point'],source_collisions=50000,
            seal_sha256=production.durable.sha(self.root/'seal.json'),publication=gen,metadata_archive=archive,
            parent=dict(manifest_sha256=production.durable.sha(self.directory/'gen/manifest.json')))
        self.write(self.directory/'source_status.json',self.status)
        (self.directory/'events.log').write_text('005 (42.000.000) Job terminated.\nNormal termination (return value 0)\n...\n')
    def write(self,p,x):p.write_text(json.dumps(x))
    def credit(self,live=()):return production.committed_meson_source_bytes(self.config,self.batch,self.entry,set(live))
    def test_running_or_failed_sources_keep_their_claim(self):
        with patch('run_shift_sm_postprocessing.download') as download:
            self.assertEqual(self.credit([42]),0)
            self.status['complete']=False;self.write(self.directory/'source_status.json',self.status)
            self.assertEqual(self.credit(),0);download.assert_not_called()
    def test_only_actual_bytes_are_credited_and_read_once(self):
        with patch('run_shift_sm_postprocessing.download') as download:
            self.assertEqual(self.credit(),30);self.assertEqual(download.call_count,2)
            self.assertEqual(self.credit(),30);self.assertEqual(download.call_count,2)
        self.assertEqual(self.entry,dict(source_cluster=42))
    def test_changed_cached_source_evidence_is_not_recredited(self):
        with patch('run_shift_sm_postprocessing.download'):
            self.assertEqual(self.credit(),30)
            self.status['unexpected_change']=True;self.write(self.directory/'source_status.json',self.status)
            with self.assertRaisesRegex(ValueError,'changed completed GEN'):
                self.credit()
    def test_peer_reader_keeps_unverified_claim_without_competing_cache_writes(self):
        with patch('run_shift_sm_postprocessing.download') as download:
            self.assertEqual(production.committed_meson_source_bytes(self.config,self.batch,
                self.entry,set(),persist_terminal_cache=False),0)
            download.assert_not_called()
            self.assertFalse((self.root/'committed_source_storage').exists())
            self.assertEqual(self.credit(),30)
            self.assertEqual(production.committed_meson_source_bytes(self.config,self.batch,
                self.entry,set(),persist_terminal_cache=False),30)
            self.assertEqual(download.call_count,2)
    def test_failed_terminal_event_and_unverified_output_are_not_paid(self):
        log=self.directory/'events.log';log.write_text(log.read_text().replace('value 0','value 1'))
        with patch('run_shift_sm_postprocessing.download') as download:
            self.assertEqual(self.credit(),0);download.assert_not_called()
        log.write_text(log.read_text().replace('value 1','value 0'))
        self.status['metadata_archive']['readback_sha256']='c'*64
        self.write(self.directory/'source_status.json',self.status)
        with self.assertRaisesRegex(ValueError,'original source or readback'):self.credit()

class PeerStorageTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.peer=self.root/'direct'
        self.stage=self.peer/'waves/w000/production';(self.stage/'completed_points').mkdir(parents=True)
        self.config=dict(directory=str(self.root/'low'))
        self.registration=dict(storage_registration=dict(
            registered_controller_directories=[str(self.peer)],
            peer_reservations=[dict(name='direct_topups',reserved_bytes=100)]))
        self.write(self.peer/'worklist.json',dict(waves=[{}]))
        peer=dict(directory=str(self.peer),campaign_prefix='new',worklist=str(self.peer/'worklist.json'),
                  dependencies={str(self.peer/'worklist.json'):production.durable.sha(self.peer/'worklist.json')})
        self.write(self.peer/'config.json',peer)
        self.config['committed_direct_storage_peer']=dict(configuration=self.pin(self.peer/'config.json'),
            reservation_name='direct_topups',reserved_bytes=100)
        self.plan=dict(campaign='new-w000',points=[dict(point='p',events=7,full_gen_events=7,
            final_output_base='/eos/new/w000')])
        self.write(self.stage/'plan.json',self.plan)
        self.write(self.stage/'seal.json',dict(files={str(self.stage/'plan.json'):production.durable.sha(self.stage/'plan.json')}))
        self.write(self.stage/'submission.json',dict(cluster=7))
        (self.stage/'pipeline.dag.dagman.log').write_text('005 (7.000.000) Job terminated.\nNormal termination (return value 0)\n...\n')
        self.nano=dict(path='/eos/new/w000/nano.root',bytes=10,sha256='a'*64)
        self.hist=dict(path='/eos/new/w000/hist.root',bytes=20,sha256='b'*64)
        self.retirement=dict(complete=True,retained_readbacks_verified=True,replacement=self.nano,retained_histogram=self.hist)
        self.write(self.stage/'retirement.json',self.retirement)
        self.report=dict(complete=True,point='p',events=7,full_gen_events=7,
            plan_sha256=production.durable.sha(self.stage/'plan.json'),seal_sha256=production.durable.sha(self.stage/'seal.json'),
            main_histograms=dict(complete=True,primitive_nano_retirement=dict(self.pin(self.stage/'retirement.json'),complete=True),
                published=dict(merged_nano=self.nano,merged_histogram=self.hist)))
        self.write(self.stage/'completed_points/p.json',self.report)
    def write(self,p,x):p.write_text(json.dumps(x))
    def pin(self,p):return dict(path=str(p),sha256=production.durable.sha(p))
    def credit(self,live=()):
        import time
        with patch.object(production,'fresh_production_authority',return_value=self.registration), \
             patch.object(production.durable,'jobs',return_value=list(live)):
            return production.committed_direct_peer_bytes(self.config,time.time()+1)
    def test_only_closed_new_objects_reduce_the_existing_peer_claim(self):
        self.assertEqual(self.credit(),30)
        self.assertEqual(self.credit([dict(ClusterId=7,Iwd=str(self.stage))]),0)
    def test_native_failure_or_foreign_campaign_never_credits_peer(self):
        p=self.stage/'pipeline.dag.dagman.log';p.write_text(p.read_text().replace('value 0','value 1'))
        self.assertEqual(self.credit(),0)
        p.write_text(p.read_text().replace('value 1','value 0'))
        self.plan['campaign']='old_baseline';self.write(self.stage/'plan.json',self.plan)
        with self.assertRaisesRegex(ValueError,'registered campaign'):self.credit()
    def test_retained_readback_and_registered_budget_are_required(self):
        self.retirement['retained_readbacks_verified']=False;self.write(self.stage/'retirement.json',self.retirement)
        self.report['main_histograms']['primitive_nano_retirement'].update(self.pin(self.stage/'retirement.json'))
        self.write(self.stage/'completed_points/p.json',self.report)
        with self.assertRaisesRegex(ValueError,'independent retained readbacks'):self.credit()
        self.config['committed_direct_storage_peer']['reserved_bytes']=999
        with self.assertRaisesRegex(ValueError,'existing registration'):self.credit()

class SMPreparationCreditTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.peer=self.root/'SM';self.work=self.peer/'preparation/sources/s0'
        self.work.mkdir(parents=True)
        self.write(self.work/'request.json',dict(publication_directory='/eos/new/s0'))
        self.write(self.work/'result.json',dict(complete=True,exit_code=0,finished_epoch=1,source_id='s0',new_GEN_exposure=0))
        self.write(self.work/'status.json',{})
        (self.work/'events.log').write_text('005 (11.000.000) Terminated\nNormal termination (return value 0)\n...\n')
        plan=dict(total_registered_bound_bytes=100,sources=[dict(source_id='s0',directory=str(self.work),
            request_sha256=production.durable.sha(self.work/'request.json'))])
        self.write(self.peer/'plan.json',plan)
        reg=dict(schema='shift-shared-production-storage-registration-v1',directory=str(self.peer),
            total_parent_SM_reservation_bytes=137000000000,additional_global_reservation_bytes=0,
            old_claims_preserved=True,maximum_new_component_publication_bytes=100,plan=self.pin(self.peer/'plan.json'))
        self.write(self.peer/'registration.json',reg)
        self.config=dict(committed_SM_preparation_peer=dict(registration=self.pin(self.peer/'registration.json'),
            plan=self.pin(self.peer/'plan.json'),EOS_namespace_prefix='/eos/new',registered_peer_bytes=1000))
        self.fresh=dict(storage_registration=dict(peer_reservations=[dict(name='SM_and_parent',reserved_bytes=1000)]))
        self.objects=[dict(path='/eos/new/s0/GEN.root',bytes=20,sha256='a'*64),
                      dict(path='/eos/old/raw.root',bytes=999,sha256='b'*64)]
    def write(self,p,x):p.write_text(json.dumps(x))
    def pin(self,p):return dict(path=str(p),sha256=production.durable.sha(p))
    def credit(self,live=()):
        with patch.object(production,'fresh_production_authority',return_value=self.fresh), \
             patch.object(production.durable,'jobs',return_value=list(live)), \
             patch.object(production,'committed_sm_storage_entry',return_value=dict(objects=self.objects)):
            return production.committed_SM_preparation_bytes(self.config,2)
    def test_reused_raw_GEN_is_excluded_and_live_producer_keeps_claim(self):
        self.assertEqual(self.credit(),20)
        self.assertEqual(self.credit([dict(ClusterId=11)]),0)
    def test_changed_request_or_budget_is_rejected(self):
        self.write(self.work/'request.json',dict(publication_directory='/eos/other'))
        with self.assertRaisesRegex(ValueError,'registered plan'):self.credit()
        self.config['committed_SM_preparation_peer']['registered_peer_bytes']=900
        with self.assertRaisesRegex(ValueError,'shared allocation'):self.credit()


class SMTerminalCloseoutCreditTest(unittest.TestCase):
    write = SMPreparationCreditTest.write
    pin = SMPreparationCreditTest.pin
    credit = SMPreparationCreditTest.credit

    def setUp(self):
        SMPreparationCreditTest.setUp(self)
        components = [('GEN_preparation',20),('detector_Nano_evidence_receipt',10),
                      ('response_collection',3),('namespaced_control_Nano_evidence_receipt',2)]
        records=[]
        for index,(component,amount) in enumerate(components):
            work=self.peer/'proofs'/component;work.mkdir(parents=True)
            self.write(work/'request.json',dict(stage=component))
            for name in ('result','status'):
                self.write(work/(name+'.json'),dict(complete=True,exit_code=0))
            (work/'events.log').write_text(f'005 ({100+index}.000.000) Terminated\n'
                'Normal termination (return value 0)\n...\n')
            def pin(path):
                return dict(self.pin(path),bytes=path.stat().st_size)
            records.append(dict(component=component,producer_directory=str(work),
                producer_proof=dict(**{n:pin(work/(n+'.json')) for n in ('request','result','status')},
                    native_terminal=dict(cluster_id=100+index,exit_code=0,completion_epoch=1,
                        fresh_queue_absence=True,event_log=pin(work/'events.log'))),
                publications=[dict(path='/eos/new/'+component+'/payload',bytes=amount,sha256='a'*64,
                    proof='independent ledger-builder SHA256 readback')],verified_committed_bytes=amount))
        self.ledger=dict(schema='shift-verified-storage-commit-ledger-v1',created_epoch=1.5,
            closeout=True,uncredited_responses=[],global_allocation_unchanged=True,
            global_SM_allocation_bytes=137000000000,search_plan=self.pin(self.peer/'plan.json'),
            candidate_allocation_bytes=100,verified_committed_bytes=35,protected_future_writes_bytes=65,
            components={c:dict(producers=1,verified_committed_bytes=n) for c,n in components},
            producer_records=records)
        self.install_closeout()

    def install_closeout(self):
        self.write(self.peer/'closeout.json',self.ledger)
        receipt=dict(schema='shift-SM-final-storage-closeout-receipt-v1',ledger=self.pin(self.peer/'closeout.json'),
            global_SM_allocation_bytes=137000000000,candidate_allocation_bytes=100,
            components=self.ledger['components'],verified_committed_bytes=35,
            protected_future_writes_bytes=65,exact_published_paths=4)
        self.write(self.peer/'closeout_receipt.json',receipt)
        review=dict(schema='shift-reviewed-SM-additional-paid-storage-v1',ledger=receipt['ledger'],
            closeout_receipt=self.pin(self.peer/'closeout_receipt.json'),global_SM_allocation_bytes=137000000000,
            allocation_bytes=100,components=self.ledger['components'],total_paid_bytes=35,
            already_credited_GEN_preparation_bytes=20,additional_paid_bytes=15,
            objects=4,duplicate_paths=0,GEN_paths_disjoint_from_extra=True)
        self.write(self.peer/'review.json',review)
        self.config['committed_SM_preparation_peer']['reviewed_terminal_closeout']=dict(
            ledger=receipt['ledger'],receipt=review['closeout_receipt'],review=self.pin(self.peer/'review.json'))

    def test_total_replaces_preparation_credit_and_cached_polls_keep_live_checks(self):
        with patch.object(production,'storage_payload',wraps=production.storage_payload) as reads:
            self.assertEqual(self.credit(),35)
            reads.reset_mock()
            self.assertEqual(self.credit(),35)
            self.assertFalse(any('/proofs/' in str(call.args[0]) for call in reads.call_args_list))
        with self.assertRaisesRegex(RuntimeError,'live producers'):
            self.credit([dict(ClusterId=101,Iwd=self.ledger['producer_records'][1]['producer_directory'])])

    def test_foreign_namespace_and_duplicate_object_are_rejected(self):
        item=self.ledger['producer_records'][1]['publications'][0]
        original=item['path'];item['path']='/eos/old/reused_GEN.root';self.install_closeout()
        with self.assertRaisesRegex(ValueError,'in-namespace'):self.credit()
        item['path']=self.ledger['producer_records'][0]['publications'][0]['path'];self.install_closeout()
        with self.assertRaisesRegex(ValueError,'double-count'):self.credit()
        item['path']=original

    def test_changed_authority_and_changed_cached_producer_block_credit(self):
        self.assertEqual(self.credit(),35)
        work=Path(self.ledger['producer_records'][1]['producer_directory'])
        self.write(work/'status.json',dict(complete=False,exit_code=1))
        with self.assertRaisesRegex(ValueError,'Cached SM closeout producer proof changed'):self.credit()
        (self.peer/'closeout.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'Pinned storage evidence changed'):self.credit()

    def test_future_producers_and_unwritten_allowance_are_rejected(self):
        native=self.ledger['producer_records'][1]['producer_proof']['native_terminal']
        native['completion_epoch']=3;self.install_closeout()
        with self.assertRaisesRegex(ValueError,'future producer'):self.credit()
        native['completion_epoch']=1
        self.ledger['producer_records'][1]['publications'][0]['proof']='unwritten future allowance'
        self.install_closeout()
        with self.assertRaisesRegex(ValueError,'readback'):self.credit()

    def test_changed_preparation_subtotal_and_role_counts_are_rejected(self):
        review=json.loads((self.peer/'review.json').read_text())
        review['already_credited_GEN_preparation_bytes']=21;self.write(self.peer/'review.json',review)
        self.config['committed_SM_preparation_peer']['reviewed_terminal_closeout']['review']=self.pin(self.peer/'review.json')
        with self.assertRaisesRegex(ValueError,'preparation subtotal'):self.credit()
        self.ledger['components']['detector_Nano_evidence_receipt']['producers']=2;self.install_closeout()
        with self.assertRaisesRegex(ValueError,'role counts'):self.credit()
