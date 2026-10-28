# Code style: AI-code. AI-generated diagnostic isolation regressions.
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from run_shift_sm_transport_diagnostic import stage_command


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


if __name__=='__main__':unittest.main()
