# Code style: AI-code. AI-generated strict runtime-only recovery adapter tests.
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import dark_photon_runtime_recovery as recovery
import dark_photon_stage_budget as budget


class RuntimeRecovery(unittest.TestCase):
    def fixture(self, root):
        production=root/'original';sources=production/'frozen/sources';sources.mkdir(parents=True)
        def write(path,value):path.write_text(json.dumps(value,indent=2,sort_keys=True)+'\n')
        def pin(path):return dict(path=str(path),sha256=recovery.sha(path))
        common=sources/'run_shift_gen_to_nano.py'
        common.write_text('import json,sys\nfrom pathlib import Path\nPath("observed_argv.json").write_text(json.dumps(sys.argv[1:]))\n')
        detector=sources/'run_dark_photon_detector.py'
        detector.write_text('import sys,subprocess\nfrom pathlib import Path\nsubprocess.run([sys.executable,str(Path(__file__).with_name("run_shift_gen_to_nano.py")),*sys.argv[1:]],check=True)\n')
        profiles=[]
        for name,digest in [('old','a'*64),('new','b'*64)]:
            manifest=root/(name+'_manifest.json')
            write(manifest,dict(schema='shift-kaon-complete-private-detector-capsule-v2',default_configs_unchanged=True,
                explicit_optin_point_only=True,private_geometry_payloads_included=False,files={
                    'workflow_helpers/'+p.name:dict(bytes=p.stat().st_size,sha256=recovery.sha(p)) for p in [common,detector]}))
            profile=root/(name+'_profile.json')
            write(profile,dict(schema='shift-predefined-signal-detector-runtime-v1',transport_predefined_signal_decays=True,
                cmssw_relative='CMSSW_17_0_0_pre4',scram_arch='el9_amd64_gcc13',runtime_sha256=digest,runtime_bytes=1,
                runtime_url='root://eosuser.cern.ch//eos/user/j/jniedzie/shift_cmssw_support/fixture.tar.gz',manifest=pin(manifest)))
            profiles.append(pin(profile))
        old,new=profiles;old_data=json.loads(Path(old['path']).read_text())
        write(production/'frozen/detector_runtime.json',old_data)
        write(production/'plan.json',dict(detector_runtime=old_data,points=[dict(point='m2_prompt',chunks=[dict(chunk=7)])]))
        identities=[[1762350000,1,index+1] for index in range(20)]
        write(production/'gen_inventory.json',dict(complete=True,points={'m2_prompt':dict(events=20,event_ids=identities,gen_sha256='e'*64)}))
        # Keep the exact production receipt expression and original-root validation.
        worker=production/'chunk_worker.py'
        worker.write_text("import hashlib,json,subprocess,sys\nfrom pathlib import Path\nB=Path(__file__).resolve().parent\ndef run(point,index,seal):\n assert hashlib.sha256((B/'seal.json').read_bytes()).hexdigest()==seal\n plan=json.loads((B/'plan.json').read_text())\n receipt={'private_detector_runtime_sha256':plan['detector_runtime']['runtime_sha256']}\n receipt.update(complete=True,point=point,chunk=index,events=1,nano_sha256='c'*64)\n subprocess.run([sys.executable,str(B/'frozen/sources/run_dark_photon_detector.py'),'unchanged_GEN','--job','6500007','--seed','880000700','--skip','17','--count','1'],check=True)\n Path('observed_receipt.json').write_text(json.dumps(receipt))\n return receipt\n")
        # Use the real validator module; only its CLI capsule deployment is a synthetic bootstrap.
        original=Path(recovery.__file__).with_name('dark_photon_detector_runtime.py').read_text()
        main="""if __name__=='__main__':
    import runpy
    entry=sys.argv[sys.argv.index('--entry')+1]
    profile=json.loads(Path(sys.argv[sys.argv.index('--profile')+1]).read_text())
    Path('observed_runtime.json').write_text(json.dumps(profile))
    sys.argv=[entry]+sys.argv[sys.argv.index('--')+1:]
    sys.path.insert(0,str(Path(entry).parent))
    runpy.run_path(entry,run_name='__main__')
"""
        bootstrap=production/'dark_photon_detector_runtime.py'
        bootstrap.write_text(original.replace("if __name__ == '__main__':\n    main()",main))
        trap="trap 'task_status=$?; /usr/bin/env -u PYTHONHOME -u PYTHONPATH /usr/bin/python3 "+str(production/'preserve_failure.py')+' "$task_status"; exit "$task_status"\' EXIT\n'
        (production/'preserve_failure.py').write_text("import os,sys\nfrom pathlib import Path\nPath('trap_status.txt').write_text(sys.argv[1])\nPath('trap_failure_id.txt').write_text(os.environ['BSM_FAILURE_ID'])\n")
        (production/'detector_worker.sh').write_text('#!/bin/bash\nset -euo pipefail\n'+trap+
            '/usr/bin/env -u PYTHONHOME -u PYTHONPATH -u LD_LIBRARY_PATH -u LD_PRELOAD /usr/bin/python3 '+str(bootstrap)+
            ' --profile '+old['path']+' --entry '+str(worker)+' -- "$@"\n')
        chain=root/'weighted_chain.json';write(chain,dict(complete=True))
        qualification=root/'qualification.json'
        flags=dict(class_layout_unchanged=True,source_GEN_unchanged=True,detector_electronics_unchanged=True,core_sources_unchanged=True)
        write(qualification,dict(schema=recovery.QUALIFICATION_SCHEMA,complete=True,**flags,
            profiles=[dict(original_runtime_sha256='a'*64,replacement_profile=new)],weighted_fullchain=[pin(chain)]))
        preflight=root/'preflight.json'
        authority=root/'authority.json'
        write(authority,dict(schema=recovery.AUTHORITY_SCHEMA,complete=True,full_chain_passed=True,**flags,
            old_profile=old,new_profile=new,qualification=pin(qualification),production_preflight=pin(chain)))
        write(production/'seal.json',dict(files={str(p):recovery.sha(p) for p in [worker,production/'plan.json',production/'gen_inventory.json',production/'frozen/detector_runtime.json',bootstrap,common,detector]}))
        write(preflight,dict(complete=True,original_plan_sha256=recovery.sha(production/'plan.json'),original_seal_sha256=recovery.sha(production/'seal.json'),replacement_runtime_sha256='b'*64,converter_source_sha256='f'*64,configuration_sha256='f'*64,points={'m2_prompt':dict(gen_sha256='e'*64,event_ids_sha256=hashlib.sha256(json.dumps(identities,separators=(',',':')).encode()).hexdigest(),full_count=20,converted_count=20)}))
        auth=json.loads(authority.read_text());auth['production_preflight']=pin(preflight);write(authority,auth)
        return production,pin(authority),trap

    def test_real_nested_entry_retains_trap_GEN_arguments_and_original_seal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);production,authority,trap=self.fixture(root)
            before={str(p):recovery.sha(p) for p in production.rglob('*') if p.is_file()}
            view=recovery.prepare_view(production,authority,root/'view',allowed_slices=[dict(point='m2_prompt',chunk=7)])
            self.assertIn(trap,Path(view['wrapper']).read_text())
            scratch=root/'scratch';scratch.mkdir()
            env=dict(os.environ,PYTHONHOME='/invalid/LCG/python312',PYTHONPATH='/invalid/LCG/site',BSM_FAILURE_ID='original_failed_attempt')
            completed=subprocess.run(['/bin/bash',view['wrapper'],'m2_prompt','7',recovery.sha(production/'seal.json')],
                cwd=scratch,env=env,text=True,capture_output=True)
            self.assertEqual(completed.returncode,0,completed.stderr)
            self.assertEqual(json.loads((scratch/'observed_argv.json').read_text()),['unchanged_GEN','--job','6500007',
                '--seed','880000700','--skip','17','--count','1','--absolute-stage-timeouts','--stage-timeouts','43200,7200,14400,14400'])
            receipt=json.loads((scratch/'observed_receipt.json').read_text())
            self.assertEqual(receipt['private_detector_runtime_sha256'],'b'*64)
            self.assertEqual(receipt['original_private_detector_runtime_sha256'],'a'*64)
            self.assertEqual(receipt['runtime_override']['authority'],authority)
            self.assertEqual((scratch/'trap_status.txt').read_text(),'0')
            self.assertEqual((scratch/'trap_failure_id.txt').read_text(),'original_failed_attempt_runtime_'+authority['sha256'][:12])
            self.assertEqual(env['BSM_FAILURE_ID'],'original_failed_attempt')
            rejected=root/'rejected';rejected.mkdir()
            failed=subprocess.run(['/bin/bash',view['wrapper'],'m2_prompt','8',recovery.sha(production/'seal.json')],cwd=rejected,env=env,text=True,capture_output=True)
            self.assertNotEqual(failed.returncode,0)
            self.assertEqual((rejected/'trap_failure_id.txt').read_text(),'original_failed_attempt_runtime_'+authority['sha256'][:12])
            for path,digest in before.items():self.assertEqual(recovery.sha(path),digest)

    def test_changed_clone_qualification_profile_or_seal_is_rejected(self):
        for change in ['clone','qualification','profile','seal']:
            with self.subTest(change=change),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);production,authority,_=self.fixture(root)
                view=recovery.prepare_view(production,authority,root/'view')
                policy=json.loads(Path(view['policy']).read_text());override=policy['runtime_override']
                target={'clone':override['worker_clone']['path'],'qualification':override['qualification']['path'],
                    'profile':override['profile']['path'],'seal':str(production/'seal.json')}[change]
                Path(target).write_text('{}')
                with self.assertRaises((ValueError,KeyError)):budget.load_policy(view['policy'])

    def test_incomplete_qualification_and_changed_core_mapping_fail_before_execution(self):
        for change in ['incomplete','mapping','fullchain','layout','core']:
            with self.subTest(change=change),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);production,authority,_=self.fixture(root)
                auth=json.loads(Path(authority['path']).read_text());qpath=Path(auth['qualification']['path']);q=json.loads(qpath.read_text())
                if change=='incomplete':q['complete']=False
                elif change=='mapping':q['profiles'][0]['original_runtime_sha256']='d'*64
                elif change=='fullchain':q['weighted_fullchain']=[]
                elif change=='layout':q['class_layout_unchanged']=False
                else:
                    profilepath=Path(auth['new_profile']['path']);profile=json.loads(profilepath.read_text());m=Path(profile['manifest']['path']);manifest=json.loads(m.read_text());manifest['files']['workflow_helpers/run_dark_photon_detector.py']['sha256']='d'*64;m.write_text(json.dumps(manifest));profile['manifest']['sha256']=recovery.sha(m);profilepath.write_text(json.dumps(profile));auth['new_profile']['sha256']=recovery.sha(profilepath);q['profiles'][0]['replacement_profile']=auth['new_profile']
                qpath.write_text(json.dumps(q));auth['qualification']['sha256']=recovery.sha(qpath);Path(authority['path']).write_text(json.dumps(auth));authority['sha256']=recovery.sha(authority['path'])
                with self.assertRaises(ValueError):recovery.prepare_view(production,authority,root/'view')


if __name__=='__main__':unittest.main()

class RuntimeRecoveryStrictness(unittest.TestCase):
    fixture=RuntimeRecovery.fixture

    def test_rehashed_arbitrary_clone_code_is_still_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);production,authority,_=self.fixture(root)
            # Input pin ordering cannot alter deterministic clone bytes.
            authority=dict(sha256=authority['sha256'],path=authority['path'])
            view=recovery.prepare_view(production,authority,root/'view')
            policy=json.loads(Path(view['policy']).read_text());clone=Path(policy['runtime_override']['worker_clone']['path'])
            clone.write_text(clone.read_text()+'\nraise RuntimeError("unreviewed code")\n')
            policy['runtime_override']['worker_clone']['sha256']=recovery.sha(clone)
            Path(view['policy']).write_text(json.dumps(policy))
            with self.assertRaisesRegex(ValueError,'more than explicit receipt'):
                budget.load_policy(view['policy'])

    def test_preflight_must_cover_full_GEN_not_selected_slice(self):
        for field,value in [('full_count',1),('converted_count',19),('event_ids_sha256','f'*64),('gen_sha256','f'*64)]:
            with self.subTest(field=field),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);production,authority,_=self.fixture(root)
                auth=json.loads(Path(authority['path']).read_text());path=Path(auth['production_preflight']['path']);preflight=json.loads(path.read_text())
                preflight['points']['m2_prompt'][field]=value;path.write_text(json.dumps(preflight));auth['production_preflight']['sha256']=recovery.sha(path)
                Path(authority['path']).write_text(json.dumps(auth));authority['sha256']=recovery.sha(authority['path'])
                with self.assertRaisesRegex(ValueError,'dropped full GEN'):
                    recovery.prepare_view(production,authority,root/'view')

    def test_frozen_import_hashes_are_checked_by_policy_loader(self):
        for key in ('stage_budget_helper_sha256','runtime_adapter_sha256'):
            with self.subTest(key=key),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);production,authority,_=self.fixture(root)
                view=recovery.prepare_view(production,authority,root/'view');path=Path(view['policy']);policy=json.loads(path.read_text());policy[key]='f'*64;path.write_text(json.dumps(policy))
                with self.assertRaisesRegex(ValueError,'helper changed'):
                    budget.load_policy(path)

    def scoped_fixture(self,root):
        production,authority,_=self.fixture(root)
        plan_path=production/'plan.json';plan=json.loads(plan_path.read_text())
        plan['points'].append(dict(point='m15_prompt',chunks=[dict(chunk=8)]));plan_path.write_text(json.dumps(plan))
        inventory_path=production/'gen_inventory.json';inventory=json.loads(inventory_path.read_text())
        inventory['points']['m15_prompt']=copy.deepcopy(inventory['points']['m2_prompt']);inventory_path.write_text(json.dumps(inventory))
        seal_path=production/'seal.json';seal=json.loads(seal_path.read_text())
        for path in (plan_path,inventory_path):seal['files'][str(path)]=recovery.sha(path)
        seal_path.write_text(json.dumps(seal))
        auth_path=Path(authority['path']);auth=json.loads(auth_path.read_text())
        preflight_path=Path(auth['production_preflight']['path']);preflight=json.loads(preflight_path.read_text())
        preflight.update(original_plan_sha256=recovery.sha(plan_path),original_seal_sha256=recovery.sha(seal_path),
            original_inventory_sha256=recovery.sha(inventory_path),point_scope=['m2_prompt'])
        preflight_path.write_text(json.dumps(preflight));auth['production_preflight']['sha256']=recovery.sha(preflight_path)
        auth_path.write_text(json.dumps(auth));authority['sha256']=recovery.sha(auth_path)
        return production,authority,preflight_path

    def test_point_scoped_full_GEN_receipt_authorizes_only_its_exact_recovery_slices(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);production,authority,_=self.scoped_fixture(root)
            view=recovery.prepare_view(production,authority,root/'view',allowed_slices=[dict(point='m2_prompt',chunk=7)])
            policy=json.loads(Path(view['policy']).read_text())
            recovery.validate_override(policy)
            for slices in ('all_planned',[dict(point='m15_prompt',chunk=8)],
                    [dict(point='m2_prompt',chunk=7),dict(point='m15_prompt',chunk=8)],
                    [dict(point='m2_prompt',chunk=7),dict(point='m2_prompt',chunk=7)]):
                with self.subTest(slices=slices),self.assertRaises(ValueError):
                    recovery.validate_override(dict(policy,allowed_slices=slices))

    def test_scoped_receipts_reject_dropped_GEN_forged_scope_and_changed_inventory(self):
        for mutation in ('selected_only','missing_scope','extra_scope','duplicate_scope','inventory_hash','extra_rows'):
            with self.subTest(mutation=mutation),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);production,authority,preflight_path=self.scoped_fixture(root)
                report=json.loads(preflight_path.read_text());profile=json.loads((root/'new_profile.json').read_text())
                if mutation=='selected_only':report['points']['m2_prompt']['converted_count']=1
                elif mutation=='missing_scope':report.pop('point_scope')
                elif mutation=='extra_scope':report['point_scope']=['m15_prompt','m2_prompt']
                elif mutation=='duplicate_scope':report['point_scope']=['m2_prompt','m2_prompt']
                elif mutation=='inventory_hash':report['original_inventory_sha256']='0'*64
                else:report['points']['m15_prompt']=copy.deepcopy(report['points']['m2_prompt'])
                with self.assertRaises(ValueError):
                    recovery.validate_full_gen_preflight(production,profile,report,point_scope=['m2_prompt'])


class IndependentPointPreflightGates(unittest.TestCase):
    def fixture(self,root):
        import shutil
        import prepare_dark_photon_production as preparer
        production=root/'production';production.mkdir();(production/'frozen').mkdir()
        helpers=production/'independent_preflight_helpers';helpers.mkdir()
        for name in ('prepare_dark_photon_kinematics_preflight.py','dark_photon_detector_runtime.py'):
            shutil.copy2(Path(recovery.__file__).with_name(name),helpers/name)
        profile=dict(runtime_sha256='a'*64)
        (production/'frozen/detector_runtime.json').write_text(json.dumps(profile))
        rows=[dict(point=name,chunks=[dict(chunk=0),dict(chunk=1)]) for name in ('m15_prompt','m40_prompt')]
        plan=dict(independent_point_preflights=True,points=rows,detector_runtime=profile)
        (production/'plan.json').write_text(json.dumps(plan))
        (production/'gen_inventory.json').write_text(json.dumps(dict(complete=True,points={
            row['point']:dict(events=2,event_ids=[[10+i,1,1],[10+i,1,2]],gen_sha256='b'*64)
            for i,row in enumerate(rows)})))
        template=production/'pipeline.dag.template'
        template.write_text(preparer.dag_text(plan,production,['m15_prompt'],600,2))
        seal=dict(files={str(path):recovery.sha(path) for path in [template,production/'plan.json',
            production/'gen_inventory.json',production/'frozen/detector_runtime.json',*helpers.iterdir()]})
        (production/'seal.json').write_text(json.dumps(seal))
        (production/'pipeline.dag').write_text(template.read_text().replace('SEAL_DIGEST',recovery.sha(production/'seal.json')))
        preparer.prepare_independent_point_preflights(production)
        return production,profile

    def test_real_scoped_tasks_bind_full_plan_without_waiting_for_bad_point(self):
        with tempfile.TemporaryDirectory() as temporary:
            production,profile=self.fixture(Path(temporary))
            pin=recovery.validate_independent_point_preflights(production,profile)
            manifest=json.loads(Path(pin['path']).read_text())
            self.assertEqual(set(manifest['points']),{'m15_prompt','m40_prompt'})
            for name,task in manifest['points'].items():
                cfg=json.loads(Path(task['config']['path']).read_text())
                self.assertEqual(cfg['point_scope'],[name])
                inventory=json.loads(Path(cfg['inventory']['path']).read_text())
                self.assertEqual(set(inventory['points']),{'m15_prompt','m40_prompt'})
            # The native m40 failure is retained; its scan is not a condition
            # for preparing or running the independent m15 branch.
            Path(manifest['points']['m40_prompt']['receipt']).write_text(json.dumps(dict(complete=False,error='D* flight')))
            recovery.validate_independent_point_preflights(production,profile)

    def test_scope_population_helper_and_DAG_tampering_fail_closed(self):
        for change in ('scope','inventory','helper','DAG','missing_point'):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as temporary:
                production,profile=self.fixture(Path(temporary))
                path=production/'point_preflights/manifest.json';manifest=json.loads(path.read_text())
                task=manifest['points']['m40_prompt'];cfg_path=Path(task['config']['path']);cfg=json.loads(cfg_path.read_text())
                if change=='scope':
                    cfg['point_scope']=['m15_prompt'];cfg_path.write_text(json.dumps(cfg));task['config']['sha256']=recovery.sha(cfg_path)
                elif change=='inventory':
                    cfg['inventory']['sha256']='0'*64;cfg_path.write_text(json.dumps(cfg));task['config']['sha256']=recovery.sha(cfg_path)
                elif change=='helper':
                    helper=Path(cfg['helpers'][0]['path']);helper.write_text('raise RuntimeError("tampered")\n')
                    cfg['helpers'][0]['sha256']=recovery.sha(helper);cfg_path.write_text(json.dumps(cfg));task['config']['sha256']=recovery.sha(cfg_path)
                elif change=='DAG':
                    dag=production/'pipeline.dag';dag.write_text(dag.read_text().replace('PARENT P_m40_prompt CHILD','PARENT P_m15_prompt CHILD'))
                    manifest['pipeline_dag_sha256']=recovery.sha(dag)
                else:del manifest['points']['m40_prompt']
                path.write_text(json.dumps(manifest))
                with self.assertRaises(ValueError):recovery.validate_independent_point_preflights(production,profile)


class FutureDetectorHandoff(unittest.TestCase):
    fixture=RuntimeRecovery.fixture

    def setting(self,authority):
        record=json.loads(Path(authority['path']).read_text())
        return {key:record[key] for key in ('qualification',)} | {'profile':record['new_profile']}

    def test_only_detector_profile_changes_in_new_operational_preparation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);production,authority,_=self.fixture(root);setting=self.setting(authority)
            source=root/'preparation.json';plan=json.loads((production/'plan.json').read_text())
            plan.update(private_gen_runtime={'runtime_sha256':'e'*64},selection={'IDs':[[1,1,18]],'p':[.1]},seed=12345)
            source.write_text(json.dumps(plan));before=recovery.sha(source)
            output=recovery.future_preparation(source,setting,root/'ops');changed=json.loads(output.read_text())
            expected=copy.deepcopy(plan);expected['detector_runtime']=json.loads(Path(setting['profile']['path']).read_text())
            self.assertEqual(changed,expected);self.assertEqual(recovery.sha(source),before)
            self.assertEqual(recovery.future_preparation(source,setting,root/'ops'),output)
            changed['seed']=0;output.write_text(json.dumps(changed))
            with self.assertRaises(ValueError):recovery.future_preparation(source,setting,root/'ops')

    def test_incomplete_mapping_cannot_prepare_future_detector_runtime(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);production,authority,_=self.fixture(root);setting=self.setting(authority)
            path=Path(setting['qualification']['path']);q=json.loads(path.read_text());q['complete']=False;path.write_text(json.dumps(q));setting['qualification']['sha256']=recovery.sha(path)
            with self.assertRaises(ValueError):recovery.future_preparation(production/'plan.json',setting,root/'ops')
            self.assertFalse((root/'ops').exists())

    def gate_fixture(self,root):
        import shutil
        from types import SimpleNamespace
        from unittest.mock import Mock
        production,authority,_=self.fixture(root);setting=self.setting(authority);ops=root/'ops'
        prepared=recovery.future_preparation(production/'plan.json',setting,ops)
        future=root/'new_production';shutil.copytree(production,future);(future/'plan.json').write_text(prepared.read_text())
        directory=ops/'runtime_preflight';directory.mkdir()
        config=directory/'preflight.json';config.write_text('{}')
        worker=directory/'worker.sh';worker.write_text('#!/bin/bash\ntrue\n')
        submit=directory/'worker.sub';submit.write_text('queue\n')
        result=directory/'receipt.json';report=json.loads((root/'preflight.json').read_text());report['original_plan_sha256']=recovery.sha(future/'plan.json');report['original_seal_sha256']=recovery.sha(future/'seal.json');result.write_text(json.dumps(report))
        (directory/'preparation.json').write_text(json.dumps(dict(config=dict(path=str(config),sha256=recovery.sha(config)),worker=dict(path=str(worker),sha256=recovery.sha(worker)),submit=dict(path=str(submit),sha256=recovery.sha(submit)),receipt=str(result))))
        native=dict(ClusterId=123,ProcId=0,Iwd=str(directory),JobStatus=4,ExitCode=0,ExitBySignal=False)
        durable=SimpleNamespace(submission=Mock(return_value=dict(cluster=123)),jobs=Mock(side_effect=[[dict(JobStatus=2)],[]]),
            parse_ads=json.loads,run_condor=Mock(return_value=json.dumps([native])),scheduler_arguments=lambda cfg:[],save=Mock())
        return future,setting,ops,durable,result

    def test_native_preflight_closes_before_admission_and_is_cached(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temporary:
            future,setting,ops,durable,result=self.gate_fixture(Path(temporary))
            with patch.object(recovery.time,'sleep') as wait:
                recovery.future_preflight(future,setting,ops,durable,{})
            wait.assert_called_once_with(30)
            self.assertTrue(json.loads((ops/'future_detector_ready.json').read_text())['complete'])
            durable.jobs.side_effect=None;durable.jobs.return_value=[];durable.run_condor.reset_mock()
            recovery.future_preflight(future,setting,ops,durable,{})
            durable.run_condor.assert_not_called()

    def test_independent_mode_admits_only_the_DAG_gates_without_claiming_scans_passed(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temporary:
            future,setting,ops,durable,result=self.gate_fixture(Path(temporary))
            manifest=dict(path=str(future/'point_preflights/manifest.json'),sha256='f'*64)
            with patch('prepare_dark_photon_production.prepare_independent_point_preflights') as prepare, \
                 patch.object(recovery,'validate_independent_point_preflights',return_value=manifest):
                recovery.future_preflight(future,setting,ops,durable,dict(independent_point_preflights=True))
            prepare.assert_called_once_with(future)
            durable.submission.assert_not_called()
            ready=json.loads((ops/'future_detector_ready.json').read_text())
            self.assertEqual(ready['gate_mode'],'independent_full_GEN_preflights')
            self.assertFalse(ready['full_GEN_preflight_complete'])
            self.assertEqual(ready['point_preflight_manifest'],manifest)

    def test_real_preparer_manifest_relocation_is_accepted_only_with_exact_seal(self):
        from unittest.mock import patch
        import shutil
        for change in ['valid','manifest_hash','runtime_hash','unsealed_path','manifest_bytes']:
            with self.subTest(change=change),tempfile.TemporaryDirectory() as temporary:
                future,setting,ops,durable,result=self.gate_fixture(Path(temporary))
                profile=json.loads(Path(setting['profile']['path']).read_text())
                manifest=future/'frozen/detector_runtime_manifest.json'
                shutil.copy2(profile['manifest']['path'],manifest)
                plan=json.loads((future/'plan.json').read_text())
                plan['detector_runtime']['manifest']=dict(path=str(manifest),sha256=recovery.sha(manifest))
                seal=json.loads((future/'seal.json').read_text())
                seal['files'][str(manifest)]=recovery.sha(manifest)
                if change=='manifest_hash':plan['detector_runtime']['manifest']['sha256']='0'*64
                elif change=='runtime_hash':plan['detector_runtime']['runtime_sha256']='0'*64
                elif change=='unsealed_path':seal['files'].pop(str(manifest))
                elif change=='manifest_bytes':manifest.write_text('{}')
                (future/'plan.json').write_text(json.dumps(plan));(future/'seal.json').write_text(json.dumps(seal))
                report=json.loads(result.read_text());report.update(original_plan_sha256=recovery.sha(future/'plan.json'),
                    original_seal_sha256=recovery.sha(future/'seal.json'));result.write_text(json.dumps(report))
                with patch.object(recovery.time,'sleep'):
                    if change=='valid':
                        recovery.future_preflight(future,setting,ops,durable,{})
                        self.assertTrue((ops/'future_detector_ready.json').exists())
                    else:
                        with self.assertRaises(ValueError):recovery.future_preflight(future,setting,ops,durable,{})
                        self.assertFalse((ops/'future_detector_ready.json').exists())

    def test_failed_or_incomplete_full_GEN_scan_cannot_admit_detector_jobs(self):
        for changed in ['failed','missing_records','native_failure']:
            with self.subTest(changed=changed),tempfile.TemporaryDirectory() as temporary:
                future,setting,ops,durable,result=self.gate_fixture(Path(temporary))
                report=json.loads(result.read_text())
                if changed=='failed':report['complete']=False
                elif changed=='missing_records':report['points']['m2_prompt']['converted_count']=19
                else:durable.run_condor.return_value=json.dumps([dict(ClusterId=123,ProcId=0,Iwd=str(result.parent),JobStatus=4,ExitCode=1,ExitBySignal=False)])
                result.write_text(json.dumps(report));durable.jobs.side_effect=[[]]
                with self.assertRaises((ValueError,RuntimeError)):
                    recovery.future_preflight(future,setting,ops,durable,{})
                self.assertFalse((ops/'future_detector_ready.json').exists())

    def test_already_submitted_old_production_is_never_rewritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);production,authority,_=self.fixture(root);setting=self.setting(authority)
            (production/'submission.json').write_text('{}');before=recovery.sha(production/'plan.json')
            recovery.future_preflight(production,setting,root/'ops',None,{})
            self.assertEqual(recovery.sha(production/'plan.json'),before)
            (production/'submission.json').unlink()
            with self.assertRaises(ValueError):recovery.future_preflight(production,setting,root/'ops',None,{})
