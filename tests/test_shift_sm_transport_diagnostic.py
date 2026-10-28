# Code style: AI-code. AI-generated diagnostic isolation regressions.
import os
import gzip
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from run_shift_sm_transport_diagnostic import recover_saved, sha, stage_command


class PassiveEnvironmentTest(unittest.TestCase):
    def test_only_sim_cmsrun_gets_a_private_copy_of_the_logger_environment(self):
        seen=[]
        def command(argv,**kwargs):
            seen.append((argv,kwargs));return 7
        wrapped=stage_command(command,Path('/worker/logger.so'))
        environment=dict(PATH='/usr/bin',RNG='unchanged')
        with patch.dict(os.environ,environment,clear=True):
            self.assertEqual(wrapped(['cmsRun','step1_cfg.py'],timeout=10),7)
            self.assertNotIn('LD_PRELOAD',os.environ)
        self.assertEqual(seen[-1][0],['cmsRun','step1_cfg.py'])
        self.assertEqual(seen[-1][1]['env'],dict(environment,LD_PRELOAD='/worker/logger.so'))
        for argv in [['cmsRun','step2_cfg.py'],['cmsRun','preflight_cfg.py'],['python3','step1_cfg.py']]:
            wrapped(argv,timeout=10)
            self.assertNotIn('env',seen[-1][1])

    def test_plain_control_keeps_existing_command_and_unknown_preloads_are_rejected(self):
        seen=[]
        def command(argv,**kwargs):seen.append((argv,kwargs))
        environment={'PATH':'/usr/bin'}
        stage_command(command,None)(['cmsRun','step1_cfg.py'],env=environment)
        self.assertIs(seen[-1][1]['env'],environment)
        with self.assertRaisesRegex(ValueError,'unknown preloaded'):
            stage_command(command,Path('/worker/logger.so'))(
                ['cmsRun','step1_cfg.py'],env={'LD_PRELOAD':'/unexpected.so'})


class SavedRecoveryTest(unittest.TestCase):
    def fixture(self, directory):
        def save(name, value):
            path=directory/name;path.write_text(json.dumps(value));return path
        identity=[600500001,1,859]
        original=save('original_request.json',dict(event_id=identity))
        result=save('original_result.json',dict(complete=True,exit_code=0,
            original_event_id=identity,request_sha256=sha(original),detector=dict(seed=17)))
        frozen=save('request.json',dict(event_id=identity,
            original_request=dict(path=str(original),sha256=sha(original)),
            original_result=dict(path=str(result),sha256=sha(result)),
            selected_GEN=dict(sha256='selected')))
        with tarfile.open(directory/'return_payload.tar.gz','w:gz') as archive:
            values={'report.json':dict(complete=False,source_gen_sha256='selected',seed=17),
                    'native_sim.json.gz':{},'sim_audit.json.gz':dict(passed=True),
                    'transport_log.json.gz':{},'nano_readback.json':{}}
            for name,value in values.items():
                blob=json.dumps(value).encode()
                if name.endswith('.gz'):blob=gzip.compress(blob)
                member=tarfile.TarInfo(name);member.size=len(blob);archive.addfile(member,io.BytesIO(blob))
        save('result.json',dict(request_sha256=sha(frozen),exit_code=1,
            error="ValueError('Passive log contains another original event')"))
        save('status.json',dict(return_payload_sha256=sha(directory/'return_payload.tar.gz')))
        plan=save('plan.json',dict(cases=[dict(case_id='e0859_logged',path=str(frozen),sha256=sha(frozen))]))
        return dict(plan_path=str(plan),plan_sha256=sha(plan),output_path=str(directory/'recovered.json'))

    def test_failed_physics_is_never_recovered_as_a_readout_error(self):
        with tempfile.TemporaryDirectory() as temporary,patch('run_shift_sm_transport_diagnostic.download') as download:
            directory=Path(temporary);request=self.fixture(directory)
            with self.assertRaisesRegex(ValueError,'Failed physics'):
                recover_saved(request)
            download.assert_not_called()
            self.assertFalse((directory/'recovered.json').exists())

    def test_changed_original_authority_and_unknown_failures_require_review(self):
        for change in ['authority','unknown_error']:
            with self.subTest(change=change),tempfile.TemporaryDirectory() as temporary:
                directory=Path(temporary);request=self.fixture(directory)
                if change=='authority':
                    (directory/'original_request.json').write_text('{}')
                else:
                    result=json.loads((directory/'result.json').read_text());result['error']="RuntimeError('unknown')"
                    (directory/'result.json').write_text(json.dumps(result))
                with patch('run_shift_sm_transport_diagnostic.download') as download:
                    with self.assertRaisesRegex(ValueError,'changed|reviewed diagnostic'):
                        recover_saved(request)
                    download.assert_not_called()


if __name__=='__main__':unittest.main()
