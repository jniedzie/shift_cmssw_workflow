# Code style: AI-code. Reviewed current SM paid-write reservation regressions.
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import run_dark_photon_meson_production as meson
import run_dark_photon_statistics as statistics
from shift_condor_native import NativeCondorError


class CurrentSMStorage(unittest.TestCase):
    reserved = 16717291177

    def pin(self, path, body):
        path.write_text(json.dumps(body))
        return dict(path=str(path),sha256=meson.durable.sha(path))

    def fixture(self, root):
        scope = root/'sm'; work = scope/'preparation/sources/dy_0.5to1'
        work.mkdir(parents=True)
        prefix = '/eos/user/j/jniedzie/shift_cmssw_support/new_SM/dy_0.5to1/'
        item = dict(path=prefix+'selected.root',bytes=100,sha256='a'*64,current_mtime_ns=10000000000)
        prior = self.pin(root/'prior.json',dict(rows=[]))
        baseline = self.pin(root/'baseline.json',dict(total_SM_remaining_bytes=self.reserved,new_paid_credit=prior))
        registration = self.pin(root/'registration.json',dict(
            schema='shift-SM-weekend-first-increment-registration-v1',
            SM_shared_envelope_bytes=self.reserved,SM_total_remaining_bound_including_new_increment=self.reserved-1))
        self.pin(work/'request.json',dict(publication_directory=prefix.rstrip('/')))
        request_hash = meson.durable.sha(work/'request.json')
        result = dict(complete=True,exit_code=0,source_id='dy_0.5to1',new_GEN_exposure=0,
            finished_epoch=15,request_sha256=request_hash,
            publications=[dict({k:item[k] for k in ('path','bytes','sha256')},independent_sha256_readback=True)],
            raw_publications=[dict(path='/eos/user/j/jniedzie/shift_cmssw_support/old/GEN.root',
                bytes=500,sha256='b'*64,independent_sha256_readback=True)])
        self.pin(work/'result.json',result)
        self.pin(work/'status.json',dict(complete=True,exit_code=0))
        (work/'events.log').write_text('005 (123.000.000) Job terminated.\nNormal termination (return value 0)\n...\n')
        (work/'worker.sub').write_text('initialdir = '+str(work)+'\narguments = '+request_hash+' capsule request.json\n')
        pins = {name:dict(path=str(work/name),sha256=meson.durable.sha(work/name))
                for name in ('request.json','result.json','status.json','events.log','worker.sub')}
        plan = self.pin(root/'plan.json',dict(sources=[dict(source_id='dy_0.5to1',directory=str(work))]))
        manager = scope/'preparation'
        submission = self.pin(manager/'submission.json',dict(cluster_id=122,directory=str(manager)))
        (manager/'dag').write_text('JOB PREP_0000 '+str(work/'worker.sub')+' DIR '+str(work)+'\n')
        (manager/'out').write_text('ULOG_TERMINATED for HTCondor Node PREP_0000 (123.0.0)\n')
        (manager/'log').write_text('005 (122.000.000) Job terminated.\nNormal termination (return value 0)\n...\n')
        proof = dict(schema='shift-reviewed-current-SM-paid-storage-v1',complete=True,checked_epoch=20,
            raw_GEN_excluded=True,prior_preparation_bytes_not_recredited=True,
            reserved_bytes=self.reserved,paid_bytes=100,scope_directory=str(scope),
            original_remaining_authority=baseline,registration=registration,prior_preparation_credit=prior,
            plan=plan,producers=1,objects=1,manager=dict(submission=submission,
                dag=dict(path=str(manager/'dag'),sha256=meson.durable.sha(manager/'dag')),
                dagman_output=dict(path=str(manager/'out'),sha256=meson.durable.sha(manager/'out')),
                native_closure=dict(successful=True,exit_code=0,cluster_id=122,
                    event_log=str(manager/'log'),event_log_sha256=meson.durable.sha(manager/'log'))),
            rows=[dict(source_id='dy_0.5to1',directory=str(work),cluster=123,native_node='PREP_0000',
                pins=pins,objects=[item],bytes=100)])
        authority = self.pin(root/'proof.json',proof)
        config = dict(schema='shift-dp-statistics-controller-v1',directory=str(root/'DP'),
            current_SM_remaining_storage=authority,dependencies={authority['path']:authority['sha256']},
            fixed_publication_breakdown=dict(SM_remaining=self.reserved),
            minimum_eos_free_bytes=50000000000,minimum_eos_free_files=20000)
        observation = dict(observed_utc=datetime.now(timezone.utc).isoformat(),free_bytes=70000000000,free_files=25000)
        return config,observation,proof,work

    def test_only_new_paid_objects_release_once_and_cached_receipts_are_reused(self):
        with tempfile.TemporaryDirectory() as temporary:
            config,observation,proof,_ = self.fixture(Path(temporary))
            with patch.object(meson.durable,'jobs',return_value=[]) as jobs, \
                 patch('watch_dark_photon_production.EosRpc.metadata',return_value=dict(size='100',mtime='10')) as metadata, \
                 patch.object(meson,'committed_sm_storage_entry',wraps=meson.committed_sm_storage_entry) as producer:
                for _ in range(2):
                    credit,evidence = meson.current_SM_storage_release(config,observation)
                    self.assertEqual(credit,100)
                    self.assertEqual(evidence['remaining_bytes'],self.reserved-100)
                self.assertEqual(producer.call_count,1)
                self.assertEqual(jobs.call_count,2)
                self.assertEqual(metadata.call_count,2)

    def test_live_or_unknown_native_state_never_releases_credit(self):
        with tempfile.TemporaryDirectory() as temporary:
            config,observation,_,work = self.fixture(Path(temporary))
            with patch.object(meson.durable,'jobs',return_value=[dict(ClusterId=123,Iwd=str(work))]), \
                 self.assertRaisesRegex(RuntimeError,'active native'):
                meson.current_SM_storage_release(config,observation)
            with patch.object(meson.durable,'jobs',side_effect=NativeCondorError('unknown')), \
                 patch.object(meson,'adaptive_reservation',return_value=self.reserved), \
                 patch.object(meson,'additional_reservations',return_value=(0,[])), \
                 patch.object(meson,'aggregation_reservation',return_value=0):
                allowed,evidence = meson.storage_gate(config,observation)
                self.assertFalse(allowed)
                self.assertEqual(evidence['reason'],'waiting_for_verified_current_SM_storage')

    def test_partial_changed_duplicate_prior_raw_or_unregistered_credit_fails(self):
        for scenario in ('partial','raw','duplicate','prior','quota','reservation','node','mtime','size','second_credit'):
            with self.subTest(scenario=scenario),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary); config,observation,proof,work=self.fixture(root)
                metadata=dict(size='100',mtime='10')
                if scenario=='partial':
                    self.pin(work/'status.json',dict(complete=False,exit_code=0))
                    proof['rows'][0]['pins']['status.json']['sha256']=meson.durable.sha(work/'status.json')
                elif scenario=='raw':proof['rows'][0]['objects'][0]['path']='/eos/user/j/jniedzie/shift_cmssw_support/old/GEN.root'
                elif scenario=='duplicate':proof['rows'].append(copy.deepcopy(proof['rows'][0]))
                elif scenario=='prior':
                    proof['prior_preparation_credit']=self.pin(root/'prior.json',dict(rows=[dict(objects=proof['rows'][0]['objects'])]))
                    baseline=statistics.read(root/'baseline.json');baseline['new_paid_credit']=proof['prior_preparation_credit']
                    proof['original_remaining_authority']=self.pin(root/'baseline.json',baseline)
                elif scenario=='quota':observation['observed_utc']='1970-01-01T00:00:19+00:00'
                elif scenario=='reservation':config['fixed_publication_breakdown']['SM_remaining']-=1
                elif scenario=='node':proof['rows'][0]['native_node']='OTHER'
                elif scenario=='mtime':metadata['mtime']='11'
                elif scenario=='size':metadata['size']='99'
                else:config['additional_SM_preparation_peer']=dict(path='/other',sha256='f'*64)
                authority=self.pin(root/'proof.json',proof);config['current_SM_remaining_storage']=authority
                config['dependencies']={authority['path']:authority['sha256']}
                with patch.object(meson.durable,'jobs',return_value=[]), \
                     patch('watch_dark_photon_production.EosRpc.metadata',return_value=metadata), \
                     self.assertRaises(ValueError):
                    meson.current_SM_storage_release(config,observation)

    def test_cached_pin_changes_and_rpc_failures_are_visible(self):
        with tempfile.TemporaryDirectory() as temporary:
            config,observation,_,work=self.fixture(Path(temporary))
            with patch.object(meson.durable,'jobs',return_value=[]), \
                 patch('watch_dark_photon_production.EosRpc.metadata',return_value=dict(size='100',mtime='10')):
                meson.current_SM_storage_release(config,observation)
                (work/'request.json').write_text('changed')
                with self.assertRaisesRegex(ValueError,'Cached current SM'):
                    meson.current_SM_storage_release(config,observation)

    def test_both_gates_and_future_budget_use_the_same_single_credit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);config,observation,_,_=self.fixture(root)
            evidence=dict(remaining_bytes=self.reserved-100)
            policy=self.pin(root/'policy.json',dict(schema='shift-dp-supplement-admission-v1',
                minimum_free_bytes=50000000000+self.reserved,minimum_free_files=20000,
                campaigns=[dict(directory=config['directory'],kind='statistics',name='own')]))
            config['supplement_admission']=policy;config['dependencies'][policy['path']]=policy['sha256']
            config['minimum_free_bytes']=50000000000
            config['storage_budget_template']=str(root/'budget.json')
            statistics.save(root/'budget.json',dict())
            config['fixed_publication_bytes']=50000000000+self.reserved
            with patch.object(meson,'current_SM_storage_release',return_value=(100,evidence)):
                with patch.object(meson,'adaptive_reservation',return_value=self.reserved), \
                     patch.object(meson,'additional_reservations',return_value=(0,[])), \
                     patch.object(meson,'aggregation_reservation',return_value=0):
                    _,meson_gate=meson.storage_gate(config,observation)
                with patch.object(statistics,'supplement_canaries',return_value=True), \
                     patch.object(statistics,'supplement_campaign_claim',return_value=dict(remaining_bytes=0)):
                    _,direct_gate=statistics.supplement_admission(config,observation)
                with patch.object(statistics,'quota',return_value=observation):
                    budget=statistics.production_budget(config,dict(events=1,detector_chunks=1,points=[{}]))
            self.assertEqual(meson_gate['protected_adaptive_bytes'],self.reserved-100)
            self.assertEqual(direct_gate['required_free_bytes'],budget['fixed_reservation_bytes'])
            self.assertEqual(budget['fixed_reservation_breakdown']['SM_remaining'],self.reserved-100)
            self.assertEqual(config['fixed_publication_breakdown']['SM_remaining'],self.reserved)


if __name__=='__main__':unittest.main()
