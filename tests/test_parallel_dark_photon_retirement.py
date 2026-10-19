# Code style: AI-code. AI-generated independent-worker retention regressions.
from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import run_grouped_dark_photon_retirement as parallel


class ParallelRetirement(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.root=Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def config(self,count=6):
        targets=[]
        for index in range(count):
            directory=self.root/('production_'+str(index))
            marker=directory/'completed_points'/'m2_prompt.json'
            marker.parent.mkdir(parents=True)
            parallel.guard.write(marker,dict(complete=True))
            targets.append(dict(key='target_'+str(index),production=str(directory),point='m2_prompt'))
        return dict(directory=str(self.root),authorities=[],targets=targets,workers=4,
                    activation_gate=str(self.root/'activation.json'),maximum_retired_bytes=1000)

    def test_real_canary_is_required_and_adopted_before_selection(self):
        config=self.config(1)
        state={}
        self.assertFalse(parallel.adopt_activation(config,state,'seal'))
        receipt=self.root/'receipt.json'
        plan=self.root/'plan.json'
        plan.write_text('{}')
        digest=parallel.guard.support.sha256(plan)
        parallel.guard.write(receipt,dict(complete=True,retained_readbacks_verified=True,
            point='m2_prompt',production=config['targets'][0]['production'],
            plan_path=str(plan),plan_sha256=digest))
        gate=dict(schema='shift-dp-raw-nano-retirement-activation-v1',complete=True,
            controller_seal_sha256='seal',reviewed_canary_plan_sha256=digest,
            initial_receipts={'target_0':parallel.guard.artifact(receipt)})
        parallel.guard.write(config['activation_gate'],gate)
        self.assertTrue(parallel.adopt_activation(config,state,'seal'))
        self.assertTrue(state['targets']['target_0']['retired'])
        self.assertTrue(state['targets']['target_0']['initial_reviewed_canary'])
        # An unchanged initial receipt is re-adopted; a changed one never is.
        self.assertTrue(parallel.adopt_activation(config,state,'seal'))
        receipt.write_text('{}')
        with self.assertRaises(ValueError):parallel.adopt_activation(config,state,'seal')

    def test_bounded_cohort_runs_four_independent_targets_and_parent_alone_credits(self):
        config=self.config()
        active=0
        maximum=0
        calls=[]
        lock=threading.Lock()
        parent=threading.get_ident()
        def launch(config,target,action,proof=None,review=None):
            nonlocal active,maximum
            with lock:
                active+=1
                maximum=max(maximum,active)
                calls.append((target['key'],action))
            time.sleep(.03)
            with lock:active-=1
            if action=='review':
                return dict(manifest='/exact/'+target['key'],manifest_sha256='hash',bytes=10)
            path=self.root/(target['key']+'_receipt.json')
            parallel.guard.write(path,dict(removed_bytes=10))
            return dict(receipt=str(path))
        credited=[]
        def credit(*args):
            self.assertEqual(threading.get_ident(),parent)
            credited.append(copy.deepcopy(args[1]))
        def proof(path):
            Path(path).write_text('{}')
            return dict(ads=[])
        with patch.object(parallel,'adopt_activation',return_value=True), \
             patch.object(parallel.guard,'capture_account_proof',side_effect=proof), \
             patch.object(parallel.guard,'validate_target',side_effect=lambda t:(Path(t['production']),dict(final_output_base='/eos/exact'))), \
             patch.object(parallel.guard,'native_closed_manager',return_value=dict(native_exit_code=0)), \
             patch.object(parallel.guard,'publish_freed_gate',side_effect=credit):
            state=parallel.cohort_step(config,{},'seal',launch)
        self.assertEqual(maximum,4)
        self.assertEqual({key for key,action in calls},{'target_'+str(i) for i in range(4)})
        self.assertEqual(len(calls),8)
        self.assertEqual(len(credited),4)
        self.assertEqual(sum(bool(item.get('retired')) for item in state['targets'].values()),4)

    def test_failed_semantic_review_cannot_start_any_deletion(self):
        config=self.config()
        actions=[]
        def launch(config,target,action,*args):
            actions.append(action)
            raise ValueError('Changed native metadata')
        def proof(path):
            Path(path).write_text('{}')
            return dict(ads=[])
        with patch.object(parallel,'adopt_activation',return_value=True), \
             patch.object(parallel.guard,'capture_account_proof',side_effect=proof), \
             patch.object(parallel.guard,'validate_target',side_effect=lambda t:(Path(t['production']),dict(final_output_base='/eos/exact'))), \
             patch.object(parallel.guard,'native_closed_manager',return_value=dict(native_exit_code=0)):
            with self.assertRaisesRegex(ValueError,'Changed native metadata'):
                parallel.cohort_step(config,{},'seal',launch)
        self.assertTrue(actions)
        self.assertEqual(set(actions),{'review'})

    def test_unknown_account_query_waits_without_review_delete_or_credit(self):
        config=self.config(1)
        with patch.object(parallel,'adopt_activation',return_value=True), \
             patch.object(parallel.guard,'capture_account_proof',side_effect=parallel.guard.native.NativeCondorError('coverage unknown')), \
             patch.object(parallel.guard,'native_closed_manager') as native, \
             patch.object(parallel.guard,'publish_freed_gate') as credit:
            launch=unittest.mock.Mock()
            state=parallel.cohort_step(config,dict(complete=True),'seal',launch)
        self.assertEqual(state['phase'],'waiting_for_verified_scheduler')
        self.assertFalse(state['complete'])
        self.assertEqual(state['last_unknown_scheduler']['stage'],'account_coverage')
        self.assertIn('coverage unknown',state['native_unknown'])
        self.assertNotIn('error',state)
        native.assert_not_called()
        credit.assert_not_called()
        launch.assert_not_called()
        self.assertEqual(list((self.root/'cohorts').iterdir()),[])

    def test_unknown_history_after_selection_starts_no_operations(self):
        config=self.config(2)
        def proof(path):
            Path(path).write_text('{}')
            return dict(ads=[])
        with patch.object(parallel,'adopt_activation',return_value=True), \
             patch.object(parallel.guard,'capture_account_proof',side_effect=proof), \
             patch.object(parallel.guard,'validate_target',side_effect=lambda t:(Path(t['production']),dict(final_output_base='/eos/exact'))), \
             patch.object(parallel.guard,'native_closed_manager',side_effect=[dict(native_exit_code=0),parallel.guard.native.NativeCondorTimeout('history unknown')]), \
             patch.object(parallel.guard,'publish_freed_gate') as credit:
            launch=unittest.mock.Mock()
            state=parallel.cohort_step(config,{},'seal',launch)
        self.assertEqual(state['phase'],'waiting_for_verified_scheduler')
        self.assertEqual(state['last_unknown_scheduler']['target'],'target_1')
        self.assertFalse(state['complete'])
        credit.assert_not_called()
        launch.assert_not_called()
        self.assertEqual(list((self.root/'cohorts').iterdir()),[])

    def test_fresh_next_poll_recovers_but_retains_last_unknown_evidence(self):
        config=self.config(1)
        def proof(path):
            Path(path).write_text('{}')
            return dict(ads=[])
        def launch(config,target,action,*args):
            if action=='review':
                return dict(manifest='/exact/plan',manifest_sha256='hash',bytes=10)
            receipt=self.root/'closed_receipt.json'
            parallel.guard.write(receipt,dict(removed_bytes=10))
            return dict(receipt=str(receipt))
        with patch.object(parallel,'adopt_activation',return_value=True), \
             patch.object(parallel.guard,'capture_account_proof',side_effect=[parallel.guard.native.NativeCondorError('unavailable')]):
            state=parallel.cohort_step(config,{},'seal',launch)
        previous=copy.deepcopy(state['last_unknown_scheduler'])
        with patch.object(parallel,'adopt_activation',return_value=True), \
             patch.object(parallel.guard,'capture_account_proof',side_effect=proof) as capture, \
             patch.object(parallel.guard,'validate_target',side_effect=lambda t:(Path(t['production']),dict(final_output_base='/eos/exact'))), \
             patch.object(parallel.guard,'native_closed_manager',return_value=dict(native_exit_code=0)) as native, \
             patch.object(parallel.guard,'publish_freed_gate') as credit:
            result=parallel.cohort_step(config,state,'seal',launch)
        capture.assert_called_once()
        native.assert_called_once()
        self.assertNotIn('native_unknown',result)
        self.assertEqual(result['last_unknown_scheduler'],previous)
        self.assertTrue(result['targets']['target_0']['retired'])
        credit.assert_called_once()

    def test_real_failed_or_empty_native_closure_still_raises(self):
        config=self.config(1)
        def proof(path):
            Path(path).write_text('{}')
            return dict(ads=[])
        for sequence,failure in enumerate([ValueError('DAG exit1'),RuntimeError('missing closure')],1):
            with self.subTest(failure=failure), \
                 patch.object(parallel,'adopt_activation',return_value=True), \
                 patch.object(parallel.guard,'capture_account_proof',side_effect=proof), \
                 patch.object(parallel.guard,'validate_target',side_effect=lambda t:(Path(t['production']),dict(final_output_base='/eos/exact'))), \
                 patch.object(parallel.guard,'native_closed_manager',side_effect=failure), \
                 patch.object(parallel.guard,'publish_freed_gate') as credit:
                launch=unittest.mock.Mock()
                with self.assertRaises(type(failure)):
                    parallel.cohort_step(config,dict(cohort_index=sequence-1),'seal',launch)
                credit.assert_not_called()
                launch.assert_not_called()

    def test_native_error_after_review_begins_remains_fatal(self):
        config=self.config(1)
        def proof(path):
            Path(path).write_text('{}')
            return dict(ads=[])
        launch=unittest.mock.Mock(side_effect=parallel.guard.native.NativeCondorError('operation failed'))
        with patch.object(parallel,'adopt_activation',return_value=True), \
             patch.object(parallel.guard,'capture_account_proof',side_effect=proof), \
             patch.object(parallel.guard,'validate_target',side_effect=lambda t:(Path(t['production']),dict(final_output_base='/eos/exact'))), \
             patch.object(parallel.guard,'native_closed_manager',return_value=dict(native_exit_code=0)), \
             patch.object(parallel.guard,'publish_freed_gate') as credit:
            with self.assertRaises(parallel.guard.native.NativeCondorError):
                parallel.cohort_step(config,{},'seal',launch)
        self.assertEqual(launch.call_args.args[2],'review')
        credit.assert_not_called()

    def test_existing_fatal_error_cannot_be_replaced_by_waiting_or_restart(self):
        config=self.config(1)
        state=dict(error='Unrelated semantic mismatch',complete=False)
        with patch.object(parallel.guard,'capture_account_proof') as query:
            with self.assertRaisesRegex(ValueError,'existing fatal'):
                parallel.cohort_step(config,state,'seal')
        query.assert_not_called()
        self.assertEqual(state['error'],'Unrelated semantic mismatch')
        config.update(controller_deployment=str(self.root))
        parallel.guard.write(self.root/'state.json',state)
        with patch.object(parallel.threading,'Thread'), \
             patch.object(parallel.guard,'local_scratch',return_value=self.root):
            with self.assertRaisesRegex(ValueError,'existing fatal'):
                parallel.run_controller(config,'seal',self.root)
        self.assertEqual(parallel.guard.read(self.root/'state.json')['error'],'Unrelated semantic mismatch')

    def test_aggregate_bound_rejects_cohort_before_deletion(self):
        config=self.config()
        config['maximum_retired_bytes']=39
        actions=[]
        def launch(config,target,action,*args):
            actions.append(action)
            return dict(bytes=10,manifest='exact',manifest_sha256='hash')
        def proof(path):
            Path(path).write_text('{}')
            return dict(ads=[])
        with patch.object(parallel,'adopt_activation',return_value=True), \
             patch.object(parallel.guard,'capture_account_proof',side_effect=proof), \
             patch.object(parallel.guard,'validate_target',side_effect=lambda t:(Path(t['production']),dict(final_output_base='/eos/exact'))), \
             patch.object(parallel.guard,'native_closed_manager',return_value=dict(native_exit_code=0)):
            with self.assertRaisesRegex(ValueError,'bound'):
                parallel.cohort_step(config,{},'seal',launch)
        self.assertEqual(actions,['review']*4)

    def test_separate_interpreter_timeout_kills_its_whole_process_group(self):
        config=self.config(1)
        config.update(controller_deployment=str(self.root),seal_sha256='seal',scratch='/tmp',operation_timeout_seconds=1)
        with patch.object(parallel.subprocess,'Popen') as popen,patch.object(parallel.os,'killpg') as kill:
            child=popen.return_value
            child.pid=1234
            child.communicate.side_effect=[parallel.subprocess.TimeoutExpired('worker',1),('','')]
            with self.assertRaisesRegex(RuntimeError,'timed out'):
                parallel.launch_worker(config,config['targets'][0],'review',self.root/'proof')
            self.assertTrue(popen.call_args.kwargs['start_new_session'])
            self.assertEqual(popen.call_args.args[0][0],sys.executable)
            kill.assert_called_once_with(1234,parallel.signal.SIGKILL)

    def test_new_freeze_copies_validated_guard_and_keeps_original_adoption_paths(self):
        adaptive,meson=self.root/'adaptive',self.root/'meson'
        adaptive.mkdir();meson.mkdir()
        parallel.guard.write(adaptive/'worklist.json',dict(waves=[]))
        parallel.guard.write(meson/'config.json',dict(batches=[]))
        for directory,name in [(adaptive,'worklist.json'),(meson,'config.json')]:
            parallel.guard.write(directory/'seal.json',dict(files={str(directory/name):parallel.guard.support.sha256(directory/name)}))
        template=self.root/'v1'
        original=parallel.guard.prepare_controller(template,adaptive,meson)
        original_seal=parallel.guard.support.sha256(template/'seal.json')
        result=parallel.prepare(self.root/'v2',template,original_seal)
        config=parallel.verified_config(self.root/'v2',result['seal_sha256'])
        self.assertEqual(config['directory'],str(template))
        self.assertEqual(config['activation_gate'],str(template/'activation.json'))
        self.assertEqual(config['workers'],4)
        self.assertEqual(result['validated_guard_sha256'],parallel.guard.support.sha256(parallel.guard.__file__))
        self.assertEqual(parallel.guard.support.sha256(template/'seal.json'),original_seal)
        descriptor=(self.root/'v2'/'controller.sub').read_text()
        for expected in ['request_cpus = 4','request_memory = 8000MB','+SendCredential = True',
                         '+MaxRuntime = 604800','periodic_release = False',
                         'initialdir = '+str(self.root/'v2')]:
            self.assertIn(expected,descriptor)


class QualificationAdmission(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.root=Path(self.temporary.name)
        admission=self.root/'admission';admission.mkdir()
        controller=self.root/'controller';controller.mkdir()
        (controller/'controller.sub.in').write_text('arguments = PARALLEL_SEAL_DIGEST\n')
        (controller/'controller.sub').write_text('arguments = seal\n')
        plan=self.root/'plan.json';plan.write_text('{}')
        plan_sha=parallel.guard.support.sha256(plan)
        receipt=self.root/'receipt.json'
        parallel.guard.write(receipt,dict(complete=True,retained_readbacks_verified=True,
            point='m2_prompt',production='/exact/production',plan_path=str(plan),plan_sha256=plan_sha))
        targets=[dict(key='target_'+str(i),point='m2_prompt',production='/exact/production') for i in range(264)]
        self.target_config=dict(targets=targets,maximum_retired_bytes=50000000000,authorities=[],
            directory=str(controller),activation_gate=str(self.root/'activation.json'))
        authorization=self.root/'authorization.json'
        parallel.guard.write(authorization,dict(complete=True,user_answer='Approve verified duplicate cleanup',
            approved_scope=dict(target_count=264,maximum_retired_bytes=50000000000,
                targets_sha256=hashlib.sha256(json.dumps(targets,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
                controller_seal_sha256='seal')))
        self.config=dict(directory=str(admission),controller=dict(directory=str(controller),seal_sha256='seal'),
            authorization=parallel.guard.artifact(authorization),initial_receipt=parallel.guard.artifact(receipt),
            initial_plan_sha256=plan_sha,initial_key='target_0',condor_schedd='schedd',condor_pool='pool',
            qualification=dict(directory=str(self.root/'qualification'),cluster=123),excluded_hosts=['bad1.cern.ch','bad2.cern.ch'])
        qualification=Path(self.config['qualification']['directory']);qualification.mkdir()
        parallel.guard.write(qualification/'seal.json',dict(files={}))
        self.config['qualification']['seal_sha256']=parallel.guard.support.sha256(qualification/'seal.json')

    def tearDown(self):self.temporary.cleanup()

    def test_no_activation_or_submission_while_actual_qualification_is_pending(self):
        with patch.object(parallel,'qualification_ready',return_value=False),patch.object(parallel.guard.native,'run_condor') as submit:
            state=parallel.admission_step(self.config,{})
        self.assertEqual(state['phase'],'waiting_for_actual_standalone_qualification')
        self.assertFalse(Path(self.target_config['activation_gate']).exists())
        submit.assert_not_called()

    def test_idle_or_transferring_qualification_does_not_probe_missing_history(self):
        for status in [1,2,6]:
            with patch.object(parallel.guard.native,'query',return_value=[dict(Iwd=self.config['qualification']['directory'],JobStatus=status)]), \
                 patch.object(parallel.guard.native,'run_condor') as history:
                self.assertFalse(parallel.qualification_ready(self.config))
                history.assert_not_called()

    def test_known_qualification_hold_is_a_failure(self):
        with patch.object(parallel.guard.native,'query',return_value=[dict(Iwd=self.config['qualification']['directory'],JobStatus=5)]):
            with self.assertRaisesRegex(ValueError,'failed, held'):
                parallel.qualification_ready(self.config)

    def test_native_unknown_waits_without_activation_but_known_failure_propagates(self):
        with patch.object(parallel,'qualification_ready',side_effect=parallel.guard.native.NativeCondorError('query failed')):
            state=parallel.admission_observe(self.config,{})
        self.assertEqual(state['phase'],'waiting_for_known_native_state')
        self.assertIn('query failed',state['native_unknown'])
        self.assertFalse(Path(self.target_config['activation_gate']).exists())
        with patch.object(parallel,'qualification_ready',side_effect=ValueError('known failed qualification')):
            with self.assertRaisesRegex(ValueError,'known failed qualification'):
                parallel.admission_observe(self.config,{})

    def qualification_reports(self):
        directory=Path(self.config['qualification']['directory'])
        keys=['target_'+str(i) for i in range(4)]
        parallel.guard.write(directory/'config.json',dict(targets=keys))
        (directory/'qualify.events.log').write_text('001 (123.000.000) Job executing\n')
        results=[]
        for index,key in enumerate(keys):
            record=directory/'records'/key;record.mkdir(parents=True)
            proof=record/'account_scheduler_proof.json.gz';proof.write_bytes(b'exact validated proof fixture')
            plan=record/'semantic_plan.json'
            parallel.guard.write(plan,dict(complete=True,point='m2_prompt',production='/exact/production',events=1,
                runner_sha256=parallel.guard.support.sha256(parallel.guard.__file__),scheduler_review=parallel.guard.artifact(proof),
                all_branch_equivalence=[dict(complete=True,edm_metadata_values_preserved=True,events=1)]))
            results.append(dict(target=key,pid=10+index,started_epoch=100,closed_epoch=200,complete=True,
                all_raw_candidates_still_present=True,deleted_bytes=0,account_schedds=['verified'],
                semantic_plan=parallel.guard.artifact(plan),checked_events=1))
        report=dict(complete=True,read_only=True,deleted_bytes=0,activation_written=False,storage_credit_written=False,results=results)
        parallel.guard.write(directory/'result.json',report)
        native=dict(ClusterId=123,ProcId=0,Iwd=str(directory),ExitCode=0,ExitBySignal=False,
                    LastRemoteHost='slot1@worker.cern.ch',JobStartDate=100,CompletionDate=201,SendCredential=True)
        return directory,report,native

    def test_real_qualification_validator_accepts_only_all_four_unchanged_semantic_results(self):
        directory,report,native=self.qualification_reports()
        with patch.object(parallel.guard.native,'query',return_value=[]), \
             patch.object(parallel.guard.native,'run_condor',return_value=json.dumps([native])), \
             patch.object(parallel,'verified_config',return_value=self.target_config):
            self.assertTrue(parallel.qualification_ready(self.config))
            report['results'].pop()
            parallel.guard.write(directory/'result.json',report)
            with self.assertRaisesRegex(ValueError,'four independent children'):
                parallel.qualification_ready(self.config)

    def test_changed_qualification_semantic_manifest_blocks_the_real_validator(self):
        directory,report,native=self.qualification_reports()
        Path(report['results'][0]['semantic_plan']['path']).write_text('{}')
        with patch.object(parallel.guard.native,'query',return_value=[]), \
             patch.object(parallel.guard.native,'run_condor',return_value=json.dumps([native])), \
             patch.object(parallel,'verified_config',return_value=self.target_config):
            with self.assertRaisesRegex(ValueError,'semantic manifest changed'):
                parallel.qualification_ready(self.config)

    def test_native_failure_and_missing_execute_event_block_the_real_validator(self):
        directory,report,native=self.qualification_reports()
        with patch.object(parallel.guard.native,'query',return_value=[]), \
             patch.object(parallel.guard.native,'run_condor',return_value=json.dumps([dict(native,ExitCode=1)])):
            with self.assertRaisesRegex(ValueError,'native completion'):
                parallel.qualification_ready(self.config)
        (directory/'qualify.events.log').write_text('000 submitted only\n')
        with patch.object(parallel.guard.native,'query',return_value=[]), \
             patch.object(parallel.guard.native,'run_condor',return_value=json.dumps([native])):
            with self.assertRaisesRegex(ValueError,'real execute event'):
                parallel.qualification_ready(self.config)

    def test_changed_rendered_descriptor_cannot_be_submitted(self):
        (Path(self.config['controller']['directory'])/'controller.sub').write_text('unreviewed command\n')
        with patch.object(parallel,'qualification_ready',return_value=dict(native_exit_code=0)), \
             patch.object(parallel,'verified_config',return_value=self.target_config), \
             patch.object(parallel.guard.native,'run_condor') as submit:
            with self.assertRaisesRegex(ValueError,'sealed template'):
                parallel.admission_step(self.config,{})
        submit.assert_not_called()

    def test_activation_and_durable_intent_precede_the_only_native_submit(self):
        admission=Path(self.config['directory'])
        def submit(command,**kwargs):
            self.assertTrue(Path(self.target_config['activation_gate']).is_file())
            intent=parallel.guard.read(admission/'submission_intent.json')
            self.assertEqual(intent['command'],command)
            self.assertEqual(kwargs['cwd'],self.config['controller']['directory'])
            return '1 job(s) submitted to cluster 456.\n'
        with patch.object(parallel,'qualification_ready',return_value=dict(native_exit_code=0)), \
             patch.object(parallel,'verified_config',return_value=self.target_config), \
             patch.object(parallel.guard,'capture_account_proof',return_value=dict(ads=[])), \
             patch.object(parallel.guard.native,'run_condor',side_effect=submit) as mutation, \
             patch.object(parallel,'admission_readback',return_value=dict(Iwd=self.config['controller']['directory'])):
            state=parallel.admission_step(self.config,{})
            self.assertTrue(state['complete'])
            mutation.assert_called_once()
        self.assertTrue((admission/'admission_complete.json').exists())

    def test_ambiguous_attempt_is_never_retried_even_with_successful_empty_native_query(self):
        admission=Path(self.config['directory'])
        parallel.guard.write(admission/'submission_intent.json',dict(attempted_epoch=time.time()))
        with patch.object(parallel,'qualification_ready',return_value=dict(native_exit_code=0)), \
             patch.object(parallel,'verified_config',return_value=self.target_config), \
             patch.object(parallel.guard,'capture_account_proof',return_value=dict(ads=[])), \
             patch.object(parallel.guard.native,'run_condor') as submit:
            state=parallel.admission_step(self.config,{})
        self.assertEqual(state['phase'],'waiting_for_ambiguous_attempt_native_evidence_do_not_resubmit')
        submit.assert_not_called()
        self.assertFalse(Path(self.target_config['activation_gate']).exists())

    def test_a_preexisting_foreign_controller_is_never_adopted_or_duplicated(self):
        with patch.object(parallel,'qualification_ready',return_value=dict(native_exit_code=0)), \
             patch.object(parallel,'verified_config',return_value=self.target_config), \
             patch.object(parallel.guard,'capture_account_proof',return_value=dict(ads=[dict(Iwd=self.config['controller']['directory'],ClusterId=456)])), \
             patch.object(parallel.guard.native,'run_condor') as submit:
            with self.assertRaisesRegex(ValueError,'unreviewed retirement controller'):
                parallel.admission_step(self.config,{})
        submit.assert_not_called()

    def test_authorization_scope_mismatch_blocks_activation_and_submission(self):
        self.target_config['maximum_retired_bytes']+=1
        with patch.object(parallel,'qualification_ready',return_value=dict(native_exit_code=0)), \
             patch.object(parallel,'verified_config',return_value=self.target_config), \
             patch.object(parallel.guard.native,'run_condor') as submit:
            with self.assertRaisesRegex(ValueError,'exact frozen retirement scope'):
                parallel.admission_step(self.config,{})
        submit.assert_not_called()
        self.assertFalse(Path(self.target_config['activation_gate']).exists())


if __name__=='__main__':unittest.main()
