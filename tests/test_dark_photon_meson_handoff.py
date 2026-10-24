# Code style: AI-code. AI-generated native bootstrap and handoff recovery tests.
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import run_dark_photon_meson_production as production
from dark_photon_detector_runtime import clean_environment


class NativeBootstrap(unittest.TestCase):
    def test_system_python_starts_with_poisoned_lcg_environment(self):
        with patch.dict(os.environ,dict(PYTHONHOME='/nonexistent/lcg-python312',
                PYTHONPATH='/nonexistent/lcg-site',LD_LIBRARY_PATH='/wrong/lcg-libraries',
                KRB5CCNAME='FILE:/tmp/credential-to-preserve')):
            environment=clean_environment()
            result=subprocess.run(['/usr/bin/python3','-c',
                'import json,encodings,os; print(json.dumps(dict(credential=os.environ["KRB5CCNAME"])))'],
                env=environment,check=True,capture_output=True,text=True)
            self.assertEqual(json.loads(result.stdout)['credential'],'FILE:/tmp/credential-to-preserve')
            for key in ('PYTHONHOME','PYTHONPATH','LD_LIBRARY_PATH'):
                self.assertNotIn(key,environment)

    def test_material_bootstrap_gets_clean_environment_and_reuses_exact_request(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); batch=dict(batch='a',campaign='signal',final_output_base='/eos/signal')
            directory=root/'batches/a'; (directory/'gen').mkdir(parents=True); (directory/'logs').mkdir()
            (directory/'gen/contract.json').write_text('{}')
            descriptor=directory/'gen/material_selected_source.json'; descriptor.write_text('{}')
            (directory/'gen/material_selection_receipt.json').write_text(json.dumps(dict(
                complete=True,descriptor_sha256=production.durable.sha(descriptor))))
            config=dict(directory=str(root),tools='/frozen/tools',detector_runtime={},
                material_admission=dict(geometry={},frame={}))
            status=dict(parent={},publication={})
            with patch.dict(os.environ,dict(PYTHONHOME='/wrong',PYTHONPATH='/wrong')), \
                 patch.object(production.subprocess,'run') as launch:
                self.assertEqual(production.prepare_material_admission(config,batch,status),{})
            invocation=launch.call_args.args[0]
            self.assertEqual(invocation[:2],['/usr/bin/python3','/frozen/tools/dark_photon_detector_runtime.py'])
            self.assertNotIn('PYTHONHOME',launch.call_args.kwargs['env'])
            self.assertNotIn('PYTHONPATH',launch.call_args.kwargs['env'])
            self.assertEqual(launch.call_args.kwargs['cwd'],directory)


class OperationalHandoff(unittest.TestCase):
    def fixture(self,root):
        config=dict(directory=str(root/'science'),tools='/original/science/tools')
        batch=dict(batch='a')
        ops=root/'ops'; tools=ops/'tools';tools.mkdir(parents=True)
        paths=production.prepare_handoff_worker(config,batch,ops,'science-seal')
        (ops/'seal.json').write_text(json.dumps(dict(files={str(path):production.durable.sha(path) for path in paths})))
        directory=ops/'handoffs/a'; digest=production.durable.sha(ops/'seal.json')
        (directory/'worker.sub').write_text((directory/'worker.sub.in').read_text().replace('OPERATIONS_SEAL_DIGEST',digest))
        config['operational_handoffs']=dict(directory=str(ops/'handoffs'),reviewed_failures={})
        return config,batch,directory

    def test_new_handoff_uses_operational_runner_and_persistent_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            config,batch,directory=self.fixture(Path(temporary))
            self.assertEqual(production.handoff_directory(config,batch),directory)
            wrapper=(directory/'run.sh').read_text()
            self.assertIn('--controller-config ',wrapper)
            self.assertIn('--controller-seal "$1"',wrapper)
            self.assertIn(' --seal science-seal',wrapper)
            self.assertIn('cd '+str(directory),wrapper)
            self.assertIn('+SendCredential = True',(directory/'worker.sub').read_text())
            (directory/'worker.sub').write_text('mutated')
            with self.assertRaisesRegex(ValueError,'sealed submit template'):
                production.handoff_directory(config,batch)

    def test_operational_preparation_retains_its_detector_worker_reservation(self):
        with tempfile.TemporaryDirectory() as temporary:
            config,batch,directory=self.fixture(Path(temporary))
            self.assertFalse(production.detector_admission_pending(config,batch,{}))
            (directory/'submission_intent.json').write_text('{}')
            self.assertTrue(production.detector_admission_pending(config,batch,{}))
            self.assertFalse(production.detector_admission_pending(config,batch,dict(detector_native_complete=True)))

    def test_active_original_handoff_cannot_be_replaced(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);config,batch,directory=self.fixture(root)
            original=root/'science/batches/a/handoff';original.mkdir(parents=True)
            (original/'worker.sub').write_text('unchanged')
            receipt=dict(cluster=123,task=str(original),submit_sha256=production.durable.sha(original/'worker.sub'))
            (original/'submission.json').write_text(json.dumps(receipt))
            status=original.parent/'source_status.json';status.write_text('{"complete":true}')
            proof=root/'review.json';proof.write_text(json.dumps(dict(schema='shift-meson-handoff-recovery-v1',
                original_submission=receipt,source_status_sha256=production.durable.sha(status),
                gen_or_detector_rerun_allowed=False)))
            config['operational_handoffs']['reviewed_failures']['a']=dict(path=str(proof),sha256=production.durable.sha(proof))
            with patch.object(production,'native_status',return_value='active'):
                with self.assertRaisesRegex(RuntimeError,'active or successful'):
                    production.handoff_directory(config,batch)
            with patch.object(production,'native_status',side_effect=production.MesonJobFailure('held',[])):
                self.assertEqual(production.handoff_directory(config,batch),directory)

class FutureMesonDetectorGate(unittest.TestCase):
    def test_new_runtime_profile_is_preflighted_before_any_detector_DAG(self):
        for failed in (False,True):
            with self.subTest(failed_scan=failed),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);batch=dict(batch='a',point='m2_prompt');directory=root/'batches/a';directory.mkdir(parents=True)
                original=dict(events=1,detector_chunks=1,private_gen_runtime={'sha256':'original_GEN'},detector_runtime={'sha256':'old'})
                (directory/'preparation.json').write_text(json.dumps(original));prepared=root/'new_preparation.json';prepared.write_text(json.dumps(dict(original,detector_runtime={'sha256':'new'})))
                budget=root/'budget.json';budget.write_text('{}');(directory/'rates.json').write_text('{"complete":true}')
                config=dict(directory=str(root),tools='/frozen',future_detector_runtime={},storage_budget_template=str(budget),
                    minimum_eos_free_bytes=100,qualified_template='/qualified',prior_inventories=[],failure_base='/eos/failures',publication_canary='/canary')
                order=[];commands=[]
                def prepare(argv,*args,**kwargs):
                    order.append('prepare');commands.append(list(map(str,argv)));(directory/'production').mkdir()
                def scan(*args):
                    order.append('preflight')
                    if failed:raise RuntimeError('fullGEN preflight failed')
                def submit(*args):order.append('detector');return dict(cluster=123)
                with patch.object(production,'adopt',return_value=original),patch.object(production,'handoff_directory',return_value=root/'ops'), \
                     patch.object(production.durable,'run_logged',side_effect=prepare),patch.object(production.durable,'submit_dag',side_effect=submit) as dag, \
                     patch('dark_photon_runtime_recovery.future_preparation',return_value=prepared),patch('dark_photon_runtime_recovery.future_preflight',side_effect=scan):
                    if failed:
                        with self.assertRaises(RuntimeError):production.handoff(config,batch)
                    else:production.handoff(config,batch)
                self.assertEqual(order,['prepare','preflight']+([] if failed else ['detector']))
                self.assertEqual(dag.call_count,0 if failed else 1)
                self.assertEqual(commands[0][commands[0].index('--preparation')+1],str(prepared))
                self.assertEqual(json.loads((directory/'preparation.json').read_text()),original)


class ReviewedRuntimeFailure(unittest.TestCase):
    def test_retry_uses_pinned_guard_even_when_supervisor_adapter_differs(self):
        import dark_photon_runtime_recovery as adapter
        with tempfile.TemporaryDirectory(prefix='.runtime-reconcile-test-',dir=Path(__file__).resolve().parents[1]) as temporary:
            config,batch,entry,state,receipt,snapshot,_=self.fixture(Path(temporary))
            # Simulate a supervisor whose imported adapter belongs to a later
            # deployment. The completed retry still uses its original pin.
            with patch.object(adapter,'pinned',side_effect=AssertionError('new adapter used')):
                production.reconcile_runtime_failures(config,state,batch,entry,receipt,snapshot)
            self.assertEqual(state['failed_batches'],{})

    def fixture(self,root):
        import run_dark_photon_runtime_recovery as guard
        import dark_photon_runtime_recovery as adapter
        science=root/'science';batch=dict(batch='a',point='m2_prompt')
        directory=science/'batches/a/production';directory.mkdir(parents=True)
        for name in ('plan.json','seal.json'):(directory/name).write_text('{}')
        def write(path,value):
            path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value))
            return dict(path=str(path),sha256=production.durable.sha(path))
        profile=write(root/'profile.json',dict(runtime_sha256='new_runtime'))
        authority=write(root/'authority.json',dict(new_profile=profile))
        old=dict(ClusterId=123,ProcId=0,DAGManJobId=100,JobStatus=5,Iwd=str(directory),
            ExitCode=1,ExitBySignal=False,NumJobStarts=1,Args='m2_prompt 0 original_seal',Cmd='original')
        edits=dict(Cmd='reviewed_new_worker',TransferExecutable=False)
        terminal=dict(old,**edits);terminal.update(JobStatus=4,ExitCode=0,NumJobStarts=2)
        review=dict(production=str(directory),point=batch['point'],original_native=old,
            plan_sha256=production.durable.sha(directory/'plan.json'),seal_sha256=production.durable.sha(directory/'seal.json'),
            gen_sha256='unchanged_GEN',runtime_sha256='old_runtime',expected_event_ids=[[1,1,7]],
            status_path=str(directory/'statuses/m2_prompt_000.json'))
        status=dict(complete=True,expected_event_ids=review['expected_event_ids'],plan_sha256=review['plan_sha256'],
            seal_sha256=review['seal_sha256'],parent_gen_sha256=review['gen_sha256'],events=1,nano_sha256='ready_Nano',
            private_detector_runtime_sha256='new_runtime',original_private_detector_runtime_sha256='old_runtime',
            runtime_override=dict(authority=authority))
        status_pin=write(Path(review['status_path']),status)
        attempt=dict(phase='complete',review=review,authority=authority,edits=edits,native_terminal=terminal,
            complete_status_sha256=status_pin['sha256'],nano_sha256=status['nano_sha256'],completed_utc='2026-10-24T10:00:00+00:00')
        guard_dir=root/'guard';write(guard_dir/'state.json',dict(attempts={'123':attempt}))
        guard_config=dict(schema=guard.SCHEMA,directory=str(guard_dir),production_directories=[str(science)],
            dependencies={module.__file__:production.durable.sha(module.__file__) for module in (guard,adapter)})
        config=dict(directory=str(science),reviewed_runtime_recovery=write(guard_dir/'config.json',guard_config))
        manager=dict(ClusterId=100,ProcId=0,JobStatus=2,Iwd=str(directory))
        healthy=dict(ClusterId=124,ProcId=0,JobStatus=2,DAGManJobId=100,Iwd=str(directory))
        failure=dict(stage='detector',cluster=100,error='preserved original held child',native_jobs=[manager,old,healthy])
        entry=dict(stage='detector',failure=failure)
        state=dict(failed_batches={'a':[failure]})
        snapshot=dict(before_epoch=1803377400,rows=[manager,healthy])
        return config,batch,entry,state,dict(cluster=100,task=str(directory)),snapshot,attempt

    def test_only_exact_completed_reviewed_child_is_archived_with_original_failure(self):
        with tempfile.TemporaryDirectory(prefix='.runtime-reconcile-test-',dir=Path(__file__).resolve().parents[1]) as temporary:
            config,batch,entry,state,receipt,snapshot,attempt=self.fixture(Path(temporary))
            original=json.loads(json.dumps(state['failed_batches']['a'][0]))
            with patch.object(production.durable,'jobs') as query:
                production.reconcile_runtime_failures(config,state,batch,entry,receipt,snapshot)
            query.assert_not_called()
            self.assertEqual(state['failed_batches'],{})
            self.assertNotIn('failure',entry)
            archived=state['resolved_failed_batches']['a'][0]
            self.assertEqual(archived['failure'],original)
            self.assertEqual(archived['attempts']['123'],attempt)
            self.assertEqual(json.loads(Path(attempt['review']['status_path']).read_text())['parent_gen_sha256'],'unchanged_GEN')

    def test_unfinished_unrelated_or_additional_held_children_remain_visible(self):
        for scenario in ('released','handoff','unknown_child','failed_manager'):
            with self.subTest(scenario=scenario),tempfile.TemporaryDirectory(prefix='.runtime-reconcile-test-',dir=Path(__file__).resolve().parents[1]) as temporary:
                config,batch,entry,state,receipt,snapshot,attempt=self.fixture(Path(temporary))
                failure=state['failed_batches']['a'][0]
                if scenario=='released':
                    path=Path(config['reviewed_runtime_recovery']['path']).parent/'state.json'
                    attempt['phase']='released';path.write_text(json.dumps(dict(attempts={'123':attempt})))
                elif scenario=='handoff':failure['stage']='handoff'
                elif scenario=='unknown_child':failure['native_jobs'].append(dict(failure['native_jobs'][1],ClusterId=125))
                else:failure['native_jobs'][0].update(JobStatus=5,ExitCode=1,ExitBySignal=False)
                original=json.loads(json.dumps(state))
                production.reconcile_runtime_failures(config,state,batch,entry,receipt,snapshot)
                self.assertEqual(state,original)

    def test_changed_success_population_native_exit_or_live_retry_cannot_clear_failure(self):
        for scenario in ('status_hash','native_exit','population','old_native','live'):
            with self.subTest(scenario=scenario),tempfile.TemporaryDirectory(prefix='.runtime-reconcile-test-',dir=Path(__file__).resolve().parents[1]) as temporary:
                config,batch,entry,state,receipt,snapshot,attempt=self.fixture(Path(temporary))
                state_path=Path(config['reviewed_runtime_recovery']['path']).parent/'state.json'
                if scenario in ('status_hash','population'):
                    path=Path(attempt['review']['status_path']);status=json.loads(path.read_text());status['expected_event_ids']=[[1,1,8]]
                    path.write_text(json.dumps(status))
                    if scenario=='population':attempt['complete_status_sha256']=production.durable.sha(path)
                elif scenario=='native_exit':attempt['native_terminal']['ExitCode']=1
                elif scenario=='old_native':attempt['review']['original_native']['NumJobStarts']=2
                else:snapshot['rows'].append(attempt['native_terminal'])
                state_path.write_text(json.dumps(dict(attempts={'123':attempt})))
                with self.assertRaises((ValueError,RuntimeError)):
                    production.reconcile_runtime_failures(config,state,batch,entry,receipt,snapshot)
                self.assertIn('a',state['failed_batches'])
                self.assertNotIn('resolved_failed_batches',state)

    def test_stale_snapshot_requires_native_absence_and_preserves_other_stage_failures(self):
        with tempfile.TemporaryDirectory(prefix='.runtime-reconcile-test-',dir=Path(__file__).resolve().parents[1]) as temporary:
            config,batch,entry,state,receipt,snapshot,attempt=self.fixture(Path(temporary))
            snapshot['before_epoch']=0
            unrelated=dict(stage='GEN',cluster=90,error='original GEN issue',native_jobs=[])
            state['failed_batches']['a'].append(unrelated)
            with patch.object(production.durable,'jobs',return_value=[]) as query:
                production.reconcile_runtime_failures(config,state,batch,entry,receipt,snapshot)
            query.assert_called_once_with(config,'member(ClusterId,{123})')
            self.assertEqual(state['failed_batches']['a'],[unrelated])


if __name__=='__main__':
    unittest.main()
