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


if __name__=='__main__':
    unittest.main()
