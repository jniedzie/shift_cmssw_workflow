# Code style: AI-code. AI-generated native scheduler and receipt regression checks.
import json
import hashlib
import copy
import gzip
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
    def test_worker_names_identify_kind_stratum_and_task(self):
        import run_shift_smart_sm_controller as controller
        with tempfile.TemporaryDirectory() as directory:
            config = dict(worker_template='universe = vanilla\n+JobBatchName = "SM_@STRATUM@_@KIND@_20261113_@TASK_NAME@"\nqueue 1\n')
            task = controller.make_task(Path(directory), config, 'detector_7300000', {}, 'detector', 'qcd_2to5')
            submit = (Path(task['directory'])/'worker.sub').read_text()
            self.assertIn('SM_qcd_2to5_detector_20261113_detector_7300000', submit)
            self.assertNotIn('@KIND@', submit)

    def test_short_quota_jobs_have_priority_before_the_queue_statement(self):
        import run_shift_smart_sm_controller as controller
        with tempfile.TemporaryDirectory() as directory:
            task=controller.make_task(Path(directory),dict(worker_template='universe = vanilla\nqueue 1\n'),
                'quota_1',dict(stage='quota'),'quota','')
            text=(Path(task['directory'])/'worker.sub').read_text()
            self.assertLess(text.index('priority = 1000'),text.index('queue 1'))

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

    def test_detector_first_keeps_service_jobs_first_and_gen_default_unchanged(self):
        tasks=dict(quota=dict(kind='quota',phase='ready'),inventory=dict(kind='inventory',phase='ready'),
            source=dict(kind='source',phase='ready',stratum='qcd_low'),
            raw=dict(kind='raw_source',phase='ready',stratum='qcd_low'),
            detector=dict(kind='detector',phase='ready',stratum='qcd_low'))
        self.assertEqual([name for name,_ in admission_order(tasks)],['quota','inventory','source','raw','detector'])
        self.assertEqual([name for name,_ in admission_order(tasks,source_prefetch_per_bin=500,detector_first=True)],
                         ['quota','inventory','detector','source','raw'])

    def test_priority_token_is_stage_specific_and_absence_preserves_template(self):
        import run_shift_smart_sm_controller as controller
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            template='initialdir = @DIRECTORY@\npriority = @PRIORITY@\nqueue 1\n'
            config=dict(worker_template=template,stage_priorities=dict(source=0,detector=1000))
            for kind,expected in [('source',0),('detector',1000),('inventory',1000)]:
                task=controller.make_task(root,config,kind,{},kind,'qcd_low')
                self.assertIn('priority = '+str(expected)+'\n',(Path(task['directory'])/'worker.sub').read_text())
            task=controller.make_task(root,dict(worker_template='queue 1\n',stage_priorities=dict(source=0)),
                                      'legacy',{},'source','qcd_low')
            self.assertEqual((Path(task['directory'])/'worker.sub').read_text(),'queue 1\n')


class SupplyAndRetryTests(unittest.TestCase):
    def test_native_on_exit_hold_does_not_require_a_termination_event(self):
        from run_shift_smart_sm_controller import native_exit_hold
        with tempfile.TemporaryDirectory() as directory:
            log=Path(directory)/'events.log'
            log.write_text('012 (123.000.000) job held\nCode 3 Subcode 0\n...\n')
            ad=dict(JobStatus=5,ExitCode=1,ExitBySignal=False,HoldReasonCode=3)
            self.assertTrue(native_exit_hold(log,123,ad))
            self.assertFalse(native_exit_hold(log,124,ad))
            self.assertFalse(native_exit_hold(log,123,dict(ad,ExitBySignal=True)))
            self.assertFalse(native_exit_hold(log,123,dict(ad,HoldReasonCode=34)))

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


class ControllerRecoveryTests(unittest.TestCase):
    def test_watchdog_survives_temporary_storage_and_scheduler_failures(self):
        import run_shift_smart_sm_controller as controller
        import errno
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with patch.object(controller,'watch',side_effect=[OSError(errno.EIO,'AFS unavailable'),
                    controller.NativeCondorError('Scheduler unavailable'),True]) as watch, patch.object(controller.time,'sleep'):
                controller.run_watchdog(root)
            self.assertEqual(watch.call_count,3)
            self.assertTrue((root/'watchdog_storage_retry.json').exists())
            self.assertTrue((root/'watchdog_scheduler_unknown.json').exists())

    def test_interrupted_preparation_reuses_the_exact_unsubmitted_request(self):
        import run_shift_smart_sm_controller as controller
        import errno
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);config=dict(worker_template='@DIRECTORY@ @REQUEST_SHA@\nqueue 1\n')
            request=dict(events=5,weights=[.25,2.])
            original_replace=Path.replace
            def fail_worker_commit(path,target):
                if path.name=='worker.tmp':raise OSError(errno.EIO,'temporary AFS failure')
                return original_replace(path,target)
            with patch.object(Path,'replace',fail_worker_commit):
                with self.assertRaises(OSError):
                    controller.make_task(root,config,'detector_1',request,'detector','qcd_2to5')
            with patch.object(controller,'query',return_value=[]) as query:
                task=controller.make_task(root,config,'detector_1',request,'detector','qcd_2to5')
            query.assert_called_once()
            path=Path(task['directory'])
            self.assertEqual(json.loads((path/'request.json').read_text()),request)
            self.assertFalse(list(path.parent.glob('*.preparing.json')))

    def test_existing_task_never_overwrites_execution_or_changed_inputs(self):
        import run_shift_smart_sm_controller as controller
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);config=dict(worker_template='@DIRECTORY@ @REQUEST_SHA@')
            task=controller.make_task(root,config,'detector_1',dict(events=5),'detector','qcd_low')
            path=Path(task['directory'])
            with patch.object(controller,'query',return_value=[]):
                with self.assertRaisesRegex(ValueError,'original preparation intent'):
                    controller.make_task(root,config,'detector_1',dict(events=6),'detector','qcd_low')
                (path/'events.log').write_text('native execute evidence')
                with self.assertRaisesRegex(ValueError,'execution evidence'):
                    controller.make_task(root,config,'detector_1',dict(events=5),'detector','qcd_low')
            (path/'events.log').unlink()
            with patch.object(controller,'query',return_value=[dict(ClusterId=123,Iwd=str(path))]):
                with self.assertRaisesRegex(ValueError,'queued worker'):
                    controller.make_task(root,config,'detector_1',dict(events=5),'detector','qcd_low')
            self.assertEqual(json.loads((path/'request.json').read_text()),dict(events=5))

    def test_temporary_failure_reloads_committed_state_before_retrying(self):
        import run_shift_smart_sm_controller as controller
        import errno
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);controller.save(root/'ledger.json',dict(tasks={},next_detector_job=10))
            attempts=[]
            def poll(root,config,state):
                attempts.append(state['next_detector_job'])
                if len(attempts)==1:
                    state['next_detector_job']=999
                    raise OSError(errno.EIO,'temporary read failure')
                return dict(phase='ntuples_complete')
            with patch.object(controller,'step',side_effect=poll),patch.object(controller.time,'sleep'):
                result=controller.run_controller(root,{})
            self.assertEqual(attempts,[10,10]);self.assertEqual(result['phase'],'ntuples_complete')
            self.assertEqual(controller.read(root/'controller_transient.json')['phase'],'storage_retry')
            self.assertFalse((root/'controller_failure.json').exists())

    def test_lost_submit_reply_stops_without_a_second_submit(self):
        import run_shift_smart_sm_controller as controller
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);controller.save(root/'ledger.json',dict(tasks={}))
            def uncertain(root,config,state):
                state['tasks']['job']=dict(phase='submitting')
                controller.save(root/'ledger.json',state)
                raise controller.NativeCondorError('Lost native submit reply')
            with patch.object(controller,'step',side_effect=uncertain) as poll:
                with self.assertRaises(controller.NativeCondorError):controller.run_controller(root,{})
            self.assertEqual(poll.call_count,1)
            self.assertEqual(controller.read(root/'ledger.json')['tasks']['job']['phase'],'submitting')

    def test_failed_save_after_submit_cannot_replay_the_persisted_intent(self):
        import run_shift_smart_sm_controller as controller
        import errno
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);controller.save(root/'ledger.json',dict(tasks={}))
            def save_failed(root,config,state):
                state['tasks']['job']=dict(phase='submitting')
                controller.save(root/'ledger.json',state)
                state['tasks']['job'].update(phase='submitted',cluster=123)
                raise OSError(errno.EIO,'Lost receipt checkpoint')
            with patch.object(controller,'step',side_effect=save_failed) as poll,patch.object(controller.time,'sleep'):
                with self.assertRaisesRegex(RuntimeError,'Uncertain persisted submission'):
                    controller.run_controller(root,{})
            self.assertEqual(poll.call_count,1)

    def test_unknown_errors_still_stop_for_review(self):
        import run_shift_smart_sm_controller as controller
        import errno
        for error in [ValueError('bad source weights'),OSError(errno.EINVAL,'invalid storage operation')]:
            with self.subTest(error=error),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);controller.save(root/'ledger.json',dict(tasks={}))
                with patch.object(controller,'step',side_effect=error) as poll:
                    with self.assertRaises(type(error)):controller.run_controller(root,{})
                self.assertEqual(poll.call_count,1)
                self.assertTrue((root/'controller_failure.json').exists())


class CompactFiniteSourceTests(unittest.TestCase):
    def source(self, root, name, selected=2, corrupt=False):
        import run_shift_smart_sm_controller as controller
        directory=root/name;directory.mkdir()
        normalization=dict(source_id=name,stratum='qcd_low',saved_events=3,gen_sha256=name*8)
        source=dict(selected_indices=list(range(selected)),event_ids=[[11,1,i+1] for i in range(3)],
            weights=[1.,.25,2.],sampling_probabilities=[1.,.1,.01],gen='/immutable/gen.root')
        authority=dict(complete=True,primary_preflight=dict(complete=True),source=normalization,
            selected_source=source,selected_events=selected)
        data=gzip.compress(json.dumps(authority).encode())
        (directory/'source_authority.json.gz').write_bytes(b'corrupt' if corrupt else data)
        controller.save(directory/'result.json',dict(complete=True,source=normalization,
            source_authority=dict(name='source_authority.json.gz',bytes=len(data),
                                  sha256=hashlib.sha256(data).hexdigest())))
        return dict(kind='source',phase='done',directory=str(directory),stratum='qcd_low',
                    request_sha256=name*8,bound_bytes=1000000),source

    def test_compact_handoff_preserves_order_probabilities_and_zero_source(self):
        import run_shift_smart_sm_controller as controller
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);task,source=self.source(root,'good');state=dict(tasks={},next_detector_job=10)
            config=dict(campaign='bounded',compact_source_authority=True,detector_group_size=1,
                        worker_template='initialdir = @DIRECTORY@\nqueue 1\n')
            controller.detector_tasks(root,config,state,task,source_task_name='source_good')
            requests=[controller.read(Path(t['directory'])/'request.json') for t in state['tasks'].values()]
            self.assertEqual([r['selected_skip'] for r in requests],[0,1])
            self.assertEqual([r['selected_count'] for r in requests],[1,1])
            self.assertTrue(all(r['detector_source']==source for r in requests))
            self.assertTrue(all(t['parent_source_task']=='source_good' for t in state['tasks'].values()))
            zero,_=self.source(root,'zero',selected=0)
            controller.detector_tasks(root,config,state,zero,source_task_name='source_zero')
            self.assertEqual(len(state['tasks']),2)
            self.assertEqual(zero['selected_events'],0)
            self.assertEqual(controller.read(Path(zero['directory'])/'result.json')['source']['saved_events'],3)

    def test_finite_worklist_prevents_refill_or_changed_source(self):
        import run_shift_smart_sm_controller as controller
        task=dict(kind='source',phase='ready',stratum='qcd_low',request_sha256='a'*64)
        state=dict(tasks=dict(source_one=task))
        config=dict(production=dict(qcd_low=dict(chunks=1)),finite_source_worklist=[dict(
            task_name='source_one',stratum='qcd_low',request_sha256='a'*64)])
        self.assertTrue(controller.validate_source_worklist(config,state))
        for changed in [dict(config,gen_supply=dict(enabled=True)),dict(config,finite_source_worklist=[]),
                        dict(config,production=dict(qcd_low=dict(chunks=2)))]:
            with self.assertRaises(ValueError):controller.validate_source_worklist(changed,state)
        state['tasks']['source_one']['request_sha256']='b'*64
        with self.assertRaisesRegex(ValueError,'request changed'):controller.validate_source_worklist(config,state)

    def test_configured_source_parallelism_keeps_legacy_default(self):
        tasks={str(i):dict(kind='source',phase='ready',stratum='qcd_low') for i in range(10)}
        self.assertEqual(len(admission_order(tasks)),2)
        self.assertEqual(len(admission_order(tasks,source_prefetch_per_bin=10)),10)
        with self.assertRaises(ValueError):admission_order(tasks,source_prefetch_per_bin=0)

    def test_pinned_detector_qualification_adds_no_old_events_or_exposure(self):
        import run_shift_smart_sm_controller as controller
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);request=root/'request.json';request.write_text('{"old_event":1}')
            request_sha=controller.sha(request)
            controller.save(root/'result.json',dict(complete=True,exit_code=0,request_sha256=request_sha))
            controller.save(root/'status.json',dict(complete=True,exit_code=0))
            (root/'events.log').write_text('005 (7.000.000) terminated\nNormal termination (return value 0)\n...\n')
            proof=root/'runtime_proof.json';controller.save(proof,dict(complete=True,detector_helper_closure_unchanged=True))
            pin=dict(directory=str(root),cluster=7,request_sha256=request_sha,result_sha256=controller.sha(root/'result.json'),
                     runtime_proof=dict(path=str(proof),sha256=controller.sha(proof)))
            state=dict(tasks={},next_detector_job=10);before=copy.deepcopy(state)
            self.assertTrue(controller.detector_canary_qualified(pin));self.assertEqual(state,before)
            self.assertFalse(controller.detector_canary_qualified(dict(pin,cluster=8)))
            controller.save(proof,dict(complete=True,detector_helper_closure_unchanged=False))
            with self.assertRaisesRegex(ValueError,'runtime proof changed'):controller.detector_canary_qualified(pin)
            pin['runtime_proof']['sha256']=controller.sha(proof)
            with self.assertRaisesRegex(ValueError,'not qualified'):controller.detector_canary_qualified(pin)

    def test_corrupt_authority_quarantines_only_its_source_during_real_poll(self):
        import run_shift_smart_sm_controller as controller
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);bad,_=self.source(root,'bad',corrupt=True);good,_=self.source(root,'good')
            canary=root/'canary';canary.mkdir();controller.save(canary/'status.json',dict(complete=True))
            (canary/'events.log').write_text('005 (7.000.000) terminated\nNormal termination (return value 0)\n...\n')
            state=dict(tasks=dict(source_bad=bad,source_good=good),next_detector_job=10)
            config=dict(campaign='bounded',compact_source_authority=True,per_source_quarantine=True,
                detector_group_size=1,worker_template='initialdir = @DIRECTORY@\nqueue 1\n',
                quota_path='/eos/cms/store/group/phys_exotica',peer_storage_authority=dict(path='/pinned/peers.json'),
                detector_reservation_bytes=33554432,
                combined_canaries=dict(qcd=str(canary)),combined_clusters=dict(qcd=7),
                production=dict(qcd_low=dict(chunks=2)),account_worker_ceiling=0,
                maximum_idle_workers=100,maximum_starts_per_poll=100,
                finite_source_worklist=[dict(task_name=n,stratum=t['stratum'],request_sha256=t['request_sha256'])
                                        for n,t in state['tasks'].items()])
            with patch.object(controller,'query',return_value=[]),patch.object(controller,'observe'),\
                    patch.object(controller,'run_condor') as submit:
                result=controller.step(root,config,state)
            submit.assert_not_called()
            self.assertEqual(bad['phase'],'failed');self.assertEqual(good['phase'],'done')
            self.assertEqual(len([t for t in state['tasks'].values() if t['kind']=='detector']),2)
            self.assertTrue(all(t['bound_bytes']==33554432 for t in state['tasks'].values() if t['kind']=='detector'))
            quota=next(t for t in state['tasks'].values() if t['kind']=='quota')
            quota_request=controller.read(Path(quota['directory'])/'request.json')
            self.assertEqual(quota_request['quota_path'],config['quota_path'])
            self.assertEqual(quota_request['peer_storage_authority'],config['peer_storage_authority'])
            missing=result['missing_exposure'];self.assertEqual(len(missing['source_cohorts']),1)
            self.assertEqual(missing['source_cohorts'][0]['generated_but_unqualified_exposure']['saved_events'],3)
            self.assertFalse(missing['missing_responses_are_physics_rejections'])

    def test_detector_budget_checks_whole_cohort_and_excludes_retry_replays(self):
        import run_shift_smart_sm_controller as controller
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);task,source=self.source(root,'good')
            original=dict(kind='detector',phase='done',selected_events=2)
            replay=dict(kind='detector',phase='done',selected_events=2,retry_level=1)
            state=dict(tasks=dict(source_good=task,old=original,replay=replay),next_detector_job=10)
            config=dict(campaign='bounded',compact_source_authority=True,detector_group_size=1,
                        maximum_detector_events=3,worker_template='initialdir = @DIRECTORY@\nqueue 1\n')
            with self.assertRaises(controller.DetectorAdmissionBudgetExceeded) as failure:
                controller.detector_tasks(root,config,state,task,'source_good')
            self.assertEqual(state['next_detector_job'],10)
            self.assertEqual(set(state['tasks']),{'source_good','old','replay'})
            self.assertFalse((root/'jobs_sharded').exists())
            controller.source_quarantine(root,state,'source_good',task,failure.exception)
            missing=controller.missing_exposure(state)['source_cohorts'][0]
            self.assertTrue(missing['source_qualification_preserved'])
            self.assertEqual(missing['qualified_but_unprocessed_exposure']['saved_events'],3)
            self.assertIsNone(missing['generated_but_unqualified_exposure'])
            self.assertEqual(missing['admission_budget']['original_detector_events_already_admitted'],2)
            task['phase']='done';config['maximum_detector_events']=4
            controller.detector_tasks(root,config,state,task,'source_good')
            self.assertEqual(state['next_detector_job'],12)
            self.assertEqual(len(state['tasks']),5)

    def test_interrupted_detector_handoff_reuses_exact_unsubmitted_children(self):
        import run_shift_smart_sm_controller as controller
        import errno
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);task,source=self.source(root,'good')
            initial=dict(tasks={},next_detector_job=10);state=copy.deepcopy(initial)
            config=dict(campaign='bounded',compact_source_authority=True,detector_group_size=1,
                        worker_template='initialdir = @DIRECTORY@\nqueue 1\n')
            original=controller.make_task;calls=[]
            def interrupted(*args,**kwargs):
                calls.append(args[2])
                if len(calls)==2:raise OSError(errno.EIO,'temporary handoff failure')
                return original(*args,**kwargs)
            with patch.object(controller,'make_task',side_effect=interrupted):
                with self.assertRaises(OSError):controller.detector_tasks(root,config,state,task,'source_good')
            state=copy.deepcopy(initial)
            with patch.object(controller,'query',return_value=[]):controller.detector_tasks(root,config,state,task,'source_good')
            self.assertEqual(list(state['tasks']),['detector_0000010','detector_0000011'])
            self.assertEqual(state['next_detector_job'],12)


class BoundedWatchdogRestartTests(unittest.TestCase):
    def fixture(self, root):
        import run_shift_smart_sm_controller as controller
        deployment=root/'deployment';deployment.mkdir()
        (deployment/'config.json').write_text('{}')
        helper=deployment/'controller.py';helper.write_text('# frozen controller\n')
        controller.save(root/'ledger.json',dict(tasks={}))
        template='initialdir = @DEPLOYMENT@\n+ShiftSmartSMController = True\narguments = @CONFIG_SHA256@\nqueue 1\n'
        return dict(_deployment_directory=str(deployment),_configuration_sha256=controller.sha(deployment/'config.json'),
            deployment_files={'controller.py':controller.sha(helper)},
            watchdog_restart=dict(maximum_restarts=1,absence_grace_seconds=0,submit_template=template)),deployment

    def test_absent_restart_is_bounded_and_persists_native_submit(self):
        import run_shift_smart_sm_controller as controller
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);config,deployment=self.fixture(root)
            config['watchdog_restart']['submit_template']=config['watchdog_restart']['submit_template'].replace('@DEPLOYMENT@','/persistent/deploy')
            with patch.object(controller,'query',return_value=[]),patch.object(controller,'run_condor',return_value='123.0 - 123.0\n') as submit:
                result=controller.restart_controller(root,config,Path('/persistent/deploy'))
                self.assertEqual(result,dict(action='submitted',cluster=123))
                self.assertEqual(controller.restart_controller(root,config,Path('/persistent/deploy'))['action'],'refused')
            submit.assert_called_once()
            self.assertEqual(controller.read(root/'watchdog_restarts.json')['phase'],'submitted')

    def test_restart_refuses_changed_hash_ambiguous_worker_and_scientific_failure(self):
        import run_shift_smart_sm_controller as controller
        for failure in ['hash','ambiguous','scientific','ownership','held']:
            with self.subTest(failure=failure),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);config,deployment=self.fixture(root)
                ads=[]
                if failure=='hash':(deployment/'controller.py').write_text('changed')
                if failure=='ambiguous':controller.save(root/'ledger.json',dict(tasks=dict(worker=dict(phase='submitting'))))
                if failure=='scientific':controller.save(root/'controller_failure.json',dict(error='bad weights'))
                if failure in ('ownership','held'):
                    ads=[dict(ClusterId=1,ProcId=0,JobStatus=5,Iwd=str(deployment),Owner='other' if failure=='ownership' else controller.pwd.getpwuid(controller.os.getuid()).pw_name)]
                with patch.object(controller,'query',return_value=ads),patch.object(controller,'run_condor') as submit:
                    if failure=='hash':
                        with self.assertRaisesRegex(ValueError,'helper changed'):controller.restart_controller(root,config,deployment)
                    else:self.assertEqual(controller.restart_controller(root,config,deployment)['action'],'refused')
                submit.assert_not_called()

    def test_live_lock_and_new_queued_controller_prevent_restart(self):
        import run_shift_smart_sm_controller as controller
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);config,deployment=self.fixture(root)
            with (root/'controller.lock').open('a') as lock:
                controller.fcntl.flock(lock,controller.fcntl.LOCK_EX|controller.fcntl.LOCK_NB)
                with patch.object(controller,'query') as query,patch.object(controller,'run_condor') as submit:
                    result=controller.restart_controller(root,config,deployment)
                self.assertIn('lock',result['reason']);query.assert_not_called();submit.assert_not_called()
            ads=[dict(ClusterId=1,ProcId=0,Owner=controller.pwd.getpwuid(controller.os.getuid()).pw_name,
                      Iwd=str(deployment),JobStatus=1)]
            with patch.object(controller,'query',return_value=ads),patch.object(controller,'run_condor') as submit:
                self.assertEqual(controller.restart_controller(root,config,deployment)['action'],'refused')
            submit.assert_not_called()

    def test_reviewed_operational_hold_preserves_old_ad_and_replaces_once(self):
        import run_shift_smart_sm_controller as controller
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);config,deployment=self.fixture(root);iwd=Path('/persistent/deploy')
            config['watchdog_restart']['submit_template']=config['watchdog_restart']['submit_template'].replace('@DEPLOYMENT@',str(iwd))
            config['watchdog_restart']['allowed_operational_holds']=[dict(code=34,subcode=0,reason_contains='reviewed timeout')]
            ad=dict(ClusterId=12,ProcId=0,Iwd=str(iwd),Owner=controller.pwd.getpwuid(controller.os.getuid()).pw_name,
                    JobStatus=5,HoldReasonCode=34,HoldReasonSubCode=0,HoldReason='reviewed timeout')
            with patch.object(controller,'query',side_effect=[[ad],[]]),\
                    patch.object(controller,'run_condor',side_effect=['removed\n','123.0 - 123.0\n']) as submit:
                result=controller.restart_controller(root,config,iwd)
            self.assertEqual(result['cluster'],123)
            self.assertEqual(submit.call_args_list[0].args[0][0],'/usr/bin/condor_rm')
            self.assertIn('JobStatus == 5',submit.call_args_list[0].args[0][-1])
            receipt=controller.read(root/'watchdog_restarts.json')
            self.assertEqual(receipt['attempts'][0]['previous_controller_ads'],[ad])

    def test_lost_restart_submit_response_is_never_replayed(self):
        import run_shift_smart_sm_controller as controller
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);config,deployment=self.fixture(root);iwd=Path('/persistent/deploy')
            config['watchdog_restart']['submit_template']=config['watchdog_restart']['submit_template'].replace('@DEPLOYMENT@',str(iwd))
            with patch.object(controller,'query',return_value=[]),\
                    patch.object(controller,'run_condor',side_effect=controller.NativeCondorError('lost reply')) as submit:
                with self.assertRaises(controller.NativeCondorError):controller.restart_controller(root,config,iwd)
                result=controller.restart_controller(root,config,iwd)
            self.assertEqual(result['action'],'refused');self.assertIn('ambiguous',result['reason'])
            submit.assert_called_once()

    def test_publisher_role_uses_state_attention_and_its_own_classad(self):
        import run_shift_smart_sm_controller as controller
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);config,deployment=self.fixture(root);iwd=Path('/persistent/publisher')
            (root/'ledger.json').unlink()
            policy=config['watchdog_restart'];policy.update(controller_attribute='ShiftSMPostprocessingController',
                ledger_filename='state.json',failure_filename='attention_required.json')
            policy['submit_template']=policy['submit_template'].replace('@DEPLOYMENT@',str(iwd)).replace(
                'ShiftSmartSMController','ShiftSMPostprocessingController')
            controller.save(root/'state.json',dict(tasks={}))
            controller.save(root/'attention_required.json',dict(error='scientific publication failure'))
            with patch.object(controller,'query') as query,patch.object(controller,'run_condor') as submit:
                self.assertEqual(controller.restart_controller(root,config,iwd)['action'],'refused')
            query.assert_not_called();submit.assert_not_called();(root/'attention_required.json').unlink()
            controller.save(root/'state.json',dict(tasks=dict(histogram=dict(phase='submitting'))))
            with patch.object(controller,'query') as query,patch.object(controller,'run_condor') as submit:
                self.assertIn('ambiguous',controller.restart_controller(root,config,iwd)['reason'])
            query.assert_not_called();submit.assert_not_called()
            controller.save(root/'state.json',dict(tasks={}))
            with patch.object(controller,'query',return_value=[]) as query,\
                    patch.object(controller,'run_condor',return_value='123.0 - 123.0\n'):
                self.assertEqual(controller.restart_controller(root,config,iwd)['cluster'],123)
            self.assertIn('ShiftSMPostprocessingController',query.call_args.args[0])
            self.assertNotIn('ShiftSmartSMController',query.call_args.args[0])

    def test_watch_query_and_template_follow_role_and_reject_unknown_knobs(self):
        import run_shift_smart_sm_controller as controller
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);config,deployment=self.fixture(root);iwd=Path('/persistent/publisher')
            config['watchdog_restart'].update(controller_attribute='ShiftSMPostprocessingController',
                ledger_filename='state.json',failure_filename='attention_required.json')
            controller.save(root/'heartbeat.json',dict(phase='running',checked_epoch=controller.time.time()))
            with patch.object(controller,'query',return_value=[dict(ClusterId=1,JobStatus=2)]) as query:
                self.assertFalse(controller.watch(root,iwd,config))
            self.assertIn('ShiftSMPostprocessingController',query.call_args.args[0])
            controller.save(root/'state.json',dict(tasks={}))
            with patch.object(controller,'query',return_value=[]),patch.object(controller,'run_condor') as submit:
                # A source-role submit template cannot restart a publisher.
                with self.assertRaisesRegex(ValueError,'persistent controller ownership'):
                    controller.restart_controller(root,config,iwd)
            submit.assert_not_called()
            for key,value in [('controller_attribute','UnrelatedController'),('ledger_filename','../ledger.json'),
                              ('failure_filename','ignored_failure.json')]:
                bad=copy.deepcopy(config);bad['watchdog_restart'][key]=value
                with self.subTest(key=key),self.assertRaisesRegex(ValueError,'Unsupported watchdog'):
                    controller.watchdog_role(bad)


if __name__ == '__main__':
    unittest.main()
