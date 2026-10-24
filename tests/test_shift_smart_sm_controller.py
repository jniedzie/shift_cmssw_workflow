# Code style: AI-code. AI-generated native scheduler and receipt regression checks.
import json
import hashlib
import copy
from unittest.mock import patch
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from run_shift_smart_sm_controller import admission_order, native_exit, observe, publication_charge, gen_supply_progress, advance_gen_supply, retry_stalled_sim, closed_tasks


class PublicationChargeTests(unittest.TestCase):
    def empty(self):
        return dict(schema='shift-smart-SM-source-v1',empty_source=True,selected_events=0,
            source=dict(saved_events=0),raw_publications=[dict(name=name,bytes=size,
            independent_sha256_readback=True) for name,size in
            [('gen.root',100000),('resolved_gen_cfg.py',1000000),('validation.json',4000)]])

    def test_empty_native_output_releases_only_the_unused_reservation(self):
        evidence=self.empty();charge=publication_charge(evidence,134217728)
        self.assertGreater(charge,1104000)
        self.assertLess(charge,1500000)
        self.assertEqual(evidence['source']['saved_events'],0)

    def test_nonempty_unknown_receipt_keeps_full_conservative_charge(self):
        self.assertEqual(publication_charge({},134217728),134217728+65536)
        self.assertEqual(publication_charge(dict(published_storage_bytes=12345),134217728),12345)

    def test_empty_credit_requires_census_and_readback_proof(self):
        for mutate in (lambda e:e.update(selected_events=1),
                lambda e:e['source'].update(saved_events=1),
                lambda e:e['raw_publications'][0].update(independent_sha256_readback=False)):
            evidence=self.empty();mutate(evidence)
            with self.assertRaises(ValueError):publication_charge(evidence,134217728)


class SchedulingTests(unittest.TestCase):
    def test_new_tasks_bypass_a_full_legacy_AFS_directory(self):
        import errno
        import run_shift_smart_sm_controller as controller
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            legacy=root/'jobs'/'detector_0000001'
            legacy.mkdir(parents=True)
            (legacy/'request.json').write_text('original frozen request')
            original_mkdir=Path.mkdir
            def bounded_mkdir(path,*args,**kwargs):
                if path.parent==root/'jobs':
                    raise OSError(errno.EFBIG,'AFS directory full')
                return original_mkdir(path,*args,**kwargs)
            config=dict(worker_template='initialdir = @DIRECTORY@\nrequest = @REQUEST_SHA@\n')
            with patch.object(Path,'mkdir',bounded_mkdir):
                task=controller.make_task(root,config,'detector_0030025',dict(events=5),'detector','qcd_2to5')
            path=Path(task['directory'])
            self.assertEqual(path.parent.parent,root/'jobs_sharded')
            self.assertEqual(json.loads((path/'request.json').read_text()),dict(events=5))
            self.assertIn(str(path),(path/'worker.sub').read_text())
            self.assertEqual((legacy/'request.json').read_text(),'original frozen request')

    def test_gen_prefetch_cannot_be_starved_by_detector_backlog(self):
        tasks = {f'qcd{i}':dict(kind='detector',phase='ready',stratum='qcd_20toinf')
                 for i in range(100)}
        tasks['new_jpsi'] = dict(kind='source',phase='ready',stratum='jpsi_2to5')
        self.assertEqual(admission_order(tasks)[0][0], 'new_jpsi')

    def test_detector_admission_balances_bins_without_stopping_live_jobs(self):
        tasks = dict(live=dict(kind='detector',phase='submitted',stratum='qcd_20toinf'),
            qcd=dict(kind='detector',phase='ready',stratum='qcd_20toinf'),
            jpsi=dict(kind='detector',phase='ready',stratum='jpsi_2to5'),
            dy=dict(kind='detector',phase='ready',stratum='dy_20toinf'))
        order=[name for name,_ in admission_order(tasks)]
        self.assertEqual(set(order[:2]), {'jpsi','dy'})
        self.assertEqual(order[-1], 'qcd')

    def test_source_backpressure_keeps_existing_workers(self):
        tasks={str(i):dict(kind='source',phase='submitted',stratum='qcd_5to10') for i in range(2)}
        tasks['next']=dict(kind='source',phase='ready',stratum='qcd_5to10')
        self.assertEqual(admission_order(tasks), [])
        self.assertTrue(all(tasks[str(i)]['phase']=='submitted' for i in range(2)))


class SupplyAndRetryTests(unittest.TestCase):
    def test_saved_GEN_is_counted_once_across_raw_and_prepared_sources(self):
        tasks={str(i):dict(kind=kind,phase='done',stratum='qcd_2to5',
            raw_gen_sha256='a'*64,saved_GEN_events=600) for i,kind in enumerate(['raw_source','source'])}
        p=gen_supply_progress({},tasks)
        self.assertEqual(p['QCD_saved_events'],600)
        self.assertFalse(p['raw_supply_is_detector_exposure'])

    def test_replaced_failed_parent_is_closed_only_when_every_retry_finishes(self):
        tasks=dict(parent=dict(phase='replaced',replacement_tasks=['a','b']),
            a=dict(phase='done'),b=dict(phase='submitted'))
        self.assertFalse(closed_tasks(tasks));tasks['b']['phase']='done';self.assertTrue(closed_tasks(tasks))

    def test_extra_supply_waits_for_every_bin_minimum(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            config=dict(campaign='test',source_templates={st:dict(source_request=dict()) for st in ['qcd_0to1','qcd_1to2']},
                worker_template='initialdir = @DIRECTORY@\nrequest = @REQUEST_SHA@\n',
                gen_supply=dict(target_QCD_saved_events=3,continue_until_epoch=1.e20,
                    chunk_events_or_trials=1000,publication_base='/synthetic',bins={st:dict(
                    minimum_chunks=1,maximum_chunks=2,run_base=600000000+i*100000,seed_base=700000000+i*1000000)
                    for i,st in enumerate(['qcd_0to1','qcd_1to2'])}))
            state=dict(tasks=dict(done=dict(kind='raw_source',phase='done',stratum='qcd_0to1',
                raw_gen_sha256='a'*64,saved_GEN_events=1)))
            # Keep detector backlog high so this test exercises only GEN admission.
            state['tasks'].update({str(i):dict(kind='detector',phase='ready') for i in range(500)})
            advance_gen_supply(root,config,state,set())
            added=[t for t in state['tasks'].values() if t['kind']=='raw_source' and t['phase']=='ready']
            self.assertEqual(len(added),1);self.assertEqual(added[0]['stratum'],'qcd_1to2')
            request=json.loads((Path(added[0]['directory'])/'request.json').read_text())
            self.assertTrue(request['generate_only']);self.assertEqual(request['source_request']['events_or_trials'],1000)

    def test_large_AFS_read_reopens_after_transient_EIO(self):
        import run_shift_smart_sm_controller as controller
        import errno,io
        with patch.object(Path,'open',side_effect=[OSError(errno.EIO,'transient'),io.BytesIO(b'{"x":1}')]),patch.object(controller.time,'sleep'):
            self.assertEqual(controller.read('/synthetic/ledger.json'),dict(x=1))

    def test_CPU_retry_entry_writes_identical_descriptor_and_keeps_job_seed(self):
        import run_shift_smart_sm_controller as controller
        import os
        with tempfile.TemporaryDirectory() as directory:
            here=os.getcwd()
            try:
                os.chdir(directory)
                request=dict(detector_source=dict(gen='/synthetic',weights=[.2]),selected_skip=30,selected_count=5,detector_job=123)
                child=controller.cpu_retry_request(request)
                Path('request.json').write_text(json.dumps(child))
                with patch('subprocess.run') as run:exec(compile(child['argv'][2],'<retry>','exec'),{})
                written=json.loads(Path('selected_source.json').read_text())
                self.assertEqual(written,dict(request['detector_source'],gen_path_resolved=True))
                argv=run.call_args.args[0]
                self.assertEqual(argv[argv.index('--job')+1],'123')
                self.assertEqual(argv[argv.index('--count')+1],'5')
                self.assertIn('43200,3600,5400,10800',argv)
            finally:os.chdir(here)

    def test_reviewed_native_SIM_timeout_keeps_event_order_seed_and_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);original=root/'original';original.mkdir()
            descriptor=dict(gen='/synthetic/GEN.root',weights=[.2]*40,selected_indices=list(range(40)),sampling_probabilities=[.25]*40)
            request=dict(detector_source=descriptor,selected_skip=30,selected_count=5,detector_job=123)
            path=original/'request.json';path.write_text(json.dumps(request,indent=2)+'\n')
            checksum=hashlib.sha256(path.read_bytes()).hexdigest()
            fp=hashlib.sha256((json.dumps(dict(descriptor,gen_path_resolved=True),indent=2)+'\n').encode()).hexdigest()
            result=dict(error="TimeoutError('SIM made no event progress for 3600 seconds; records started=4')",
                request_sha256=checksum,source_descriptor_sha256=fp,source_skip=30,events=5,
                current_tier='SIM',source_indices=list(range(30,35)),validated_tier_events=dict(NANO=0))
            (original/'result.json').write_text(json.dumps(result))
            (original/'events.log').write_text('005 (123.000.000) event\nNormal termination (return value 1)\n...\n')
            task=dict(kind='detector',phase='failed',directory=str(original),cluster=123,request_sha256=checksum,stratum='dy_low')
            state=dict(tasks=dict(parent=task),next_detector_job=200)
            config=dict(reviewed_SIM_timeout_retry=True,worker_template='@DIRECTORY@ @REQUEST_SHA@')
            ad=dict(ClusterId=123,ProcId=0,JobStatus=5,ExitCode=1,RemoteUserCpu=6300,Iwd=str(original))
            # A live/released worker is never duplicated.
            retry_stalled_sim(root,config,state,[dict(ad,JobStatus=2)])
            self.assertEqual(len(state['tasks']),1)
            retry_stalled_sim(root,config,state,[ad])
            self.assertEqual(task['phase'],'replaced');self.assertEqual(len(task['replacement_tasks']),1)
            rows=[json.loads((Path(state['tasks'][n]['directory'])/'request.json').read_text()) for n in task['replacement_tasks']]
            self.assertEqual([r['selected_skip'] for r in rows],[30])
            self.assertTrue(all(r['selected_count']==5 and r['detector_job']==123 and r['detector_source']==descriptor for r in rows))
            self.assertEqual(state['reviewed_cpu_retries']['parent']['original_task']['phase'],'failed')
            retry_stalled_sim(root,config,state,[ad]);self.assertEqual(len(state['tasks']),2)


class CompletionTests(unittest.TestCase):
    def test_exact_native_job_is_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'events.log'
            path.write_text('005 (124.000.000) event\nNormal termination (return value 0)\n...\n')
            self.assertEqual(native_exit(path, 124), 0)
            self.assertIsNone(native_exit(path, 123))

    def test_returned_success_without_native_closure_does_not_credit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path/'status.json').write_text(json.dumps(dict(complete=True, exit_code=0)))
            task = dict(directory=tmp, phase='submitted', cluster=123)
            observe(task, [])
            self.assertEqual(task['phase'], 'submitted')

    def test_hold_is_kept_for_review(self):
        task = dict(directory='/persistent/job', phase='submitted', cluster=123)
        observe(task, [dict(ClusterId=123, ProcId=0, Iwd='/persistent/job',
                           JobStatus=5, HoldReason='Actual worker failed')])
        self.assertEqual(task['phase'], 'failed')
        self.assertEqual(task['error'], 'Actual worker failed')

    def test_success_is_bound_to_request_and_native_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path/'events.log').write_text('005 (123.000.000) event\nNormal termination (return value 0)\n...\n')
            (path/'status.json').write_text(json.dumps(dict(complete=True, exit_code=0)))
            (path/'result.json').write_text(json.dumps(dict(complete=True, request_sha256='a'*64)))
            task = dict(directory=tmp, phase='submitted', cluster=123,
                        request_sha256='a'*64, bound_bytes=10000)
            observe(task, [])
            self.assertEqual(task['phase'], 'done')
            task.update(phase='submitted', request_sha256='b'*64)
            observe(task, [])
            self.assertEqual(task['phase'], 'failed')

    def test_uncertain_submission_is_never_repeated(self):
        with self.assertRaisesRegex(RuntimeError, 'Uncertain submission'):
            observe(dict(directory='/persistent/job', phase='submitting'), [])


if __name__ == '__main__':
    unittest.main()
