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
