# Code style: AI-code. AI-generated frozen operational deadline dispatch tests.
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import dark_photon_stage_budget as budget
import prepare_dark_photon_production as preparer
import run_dark_photon_meson_production as meson
from unittest import mock


class StageBudget(unittest.TestCase):
    def fixture(self,root):
        production=root/'original';sources=production/'frozen/sources';sources.mkdir(parents=True)
        (production/'plan.json').write_text('{"events":1}')
        (production/'seal.json').write_text('{}')
        common=sources/'run_shift_gen_to_nano.py';common.write_text('import json,sys\nfrom pathlib import Path\nPath("observed.json").write_text(json.dumps(sys.argv[1:]))\n')
        detector=sources/'run_dark_photon_detector.py';detector.write_text('import subprocess,sys\nfrom pathlib import Path\nsubprocess.run([sys.executable,str(Path(__file__).with_name("run_shift_gen_to_nano.py")),*sys.argv[1:]],check=True)\n')
        worker=production/'chunk_worker.py';worker.write_text('import subprocess,sys\nfrom pathlib import Path\ndef run(point,chunk,seal):\n subprocess.run([sys.executable,str(Path(__file__).parent/"frozen/sources/run_dark_photon_detector.py"),"unchanged-source","--job","6500038","--seed","880003800","--count","1"],check=True)\n return dict(complete=True,point=point,chunk=chunk,events=1,nano_sha256="a"*64)\n')
        policy=dict(schema=budget.SCHEMA,production_directory=str(production),plan_sha256=budget.sha(production/'plan.json'),
            worker_sha256=budget.sha(worker),detector_sha256=budget.sha(detector),common_runner_sha256=budget.sha(common),
            stage_timeouts_seconds=[43200,7200,14400,14400],absolute_stage_timeouts=True,
            allowed_slices=[dict(point='m0p3_prompt',chunk=38)])
        path=root/'policy.json';path.write_text(json.dumps(policy));return production,policy,path

    def test_common_command_only_extends_existing_argv(self):
        with tempfile.TemporaryDirectory() as temporary:
            production,policy,path=self.fixture(Path(temporary))
            command=['python3',str(production/'frozen/sources/run_shift_gen_to_nano.py'),'source.json','--job','6500038','--count','1']
            original=list(command);actual=budget.common_command(command,policy)
            self.assertEqual(command,original)
            self.assertEqual(actual,original+['--absolute-stage-timeouts','--stage-timeouts','43200,7200,14400,14400'])
            self.assertEqual(budget.common_command(['cmsRun','step1_cfg.py'],policy),['cmsRun','step1_cfg.py'])
            with self.assertRaisesRegex(ValueError,'another explicit'):
                budget.common_command(command+['--absolute-stage-timeouts'],policy)

    def test_real_nested_entry_from_scratch_preserves_all_original_sources_and_seed_arguments(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);production,policy,path=self.fixture(root);scratch=root/'scratch';scratch.mkdir()
            before={p:budget.sha(p) for p in production.rglob('*') if p.is_file()}
            invocation=[sys.executable,'-B',budget.__file__,'--policy',str(path),'--mode','worker','--','m0p3_prompt','38',budget.sha(production/'seal.json')]
            result=subprocess.run(invocation,cwd=scratch,check=True,capture_output=True,text=True)
            self.assertTrue(json.loads(result.stdout)['complete'])
            self.assertEqual(json.loads((scratch/'observed.json').read_text()),['unchanged-source','--job','6500038','--seed','880003800','--count','1','--absolute-stage-timeouts','--stage-timeouts','43200,7200,14400,14400'])
            for p,digest in before.items():self.assertEqual(budget.sha(p),digest)
            invocation[-2]='37'
            rejected=subprocess.run(invocation,cwd=scratch,capture_output=True,text=True)
            self.assertNotEqual(rejected.returncode,0);self.assertIn('bounded reviewed retry',rejected.stderr)

    def test_new_production_freezes_budget_policy_and_qualified_shell_dispatch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);production,policy,path=self.fixture(root)
            wrapper=production/'detector_worker.sh'
            wrapper.write_text('#!/bin/bash\n/usr/bin/env -u PYTHONHOME /usr/bin/python3 bootstrap.py --entry '+str(production/'chunk_worker.py')+' -- "$@"\n')
            preparer.freeze_absolute_stage_budget(production,[43200,7200,14400,14400])
            pinned=budget.load_policy(production/'stage_budget_policy.json')
            self.assertEqual(pinned['allowed_slices'],'all_planned')
            self.assertIn('--mode worker -- "$@"',wrapper.read_text())
            self.assertNotIn('--entry '+str(production/'chunk_worker.py'),wrapper.read_text())
            subprocess.run(['bash','-n',str(wrapper)],check=True)
            self.assertEqual(pinned['worker_sha256'],policy['worker_sha256'])

    def test_new_handoff_preserves_submitted_old_owner_and_uses_new_workers_for_future_batches(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);old=root/'old/handoffs/oldbatch';new=root/'new/handoffs/newbatch'
            for d in (old,new):
                d.mkdir(parents=True);(d/'run.sh').write_text('worker')
                (d/'worker.sub.in').write_text('seal OPERATIONS_SEAL_DIGEST')
                seal=d.parents[1]/'seal.json';seal.write_text(json.dumps(dict(files={str(d/'run.sh'):budget.sha(d/'run.sh'),str(d/'worker.sub.in'):budget.sha(d/'worker.sub.in')})))
                (d/'worker.sub').write_text('seal '+budget.sha(seal))
            config=dict(directory=str(root/'science'),operational_handoffs=dict(directory=str(root/'new/handoffs'),preserved_directories=dict(oldbatch=str(old))))
            self.assertEqual(meson.handoff_directory(config,dict(batch='oldbatch')),old)
            self.assertEqual(meson.handoff_directory(config,dict(batch='newbatch')),new)
            (old/'run.sh').write_text('changed')
            with self.assertRaisesRegex(ValueError,'changed after freezing'):
                meson.handoff_directory(config,dict(batch='oldbatch'))

    def test_changed_source_or_too_small_budget_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);production,policy,path=self.fixture(root)
            self.assertEqual(budget.load_policy(path),policy)
            (production/'chunk_worker.py').write_text('modified')
            with self.assertRaisesRegex(ValueError,'original worker source'):
                budget.load_policy(path)
            policy['stage_timeouts_seconds'][0]=1800;path.write_text(json.dumps(policy))
            with self.assertRaisesRegex(ValueError,'explicit frozen authority'):
                budget.load_policy(path)


if __name__=='__main__':unittest.main()
