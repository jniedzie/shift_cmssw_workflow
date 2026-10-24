# Code style: AI-code. AI-generated contract tests for the sealed full-census scanner.
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1]/'scripts/prepare_dark_photon_kinematics_preflight.py'
SPEC = importlib.util.spec_from_file_location('primary_preflight', SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class PreflightPreparation(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.base = Path(self.scratch.name)
        self.production = self.base/'production'; self.production.mkdir()
        (self.production/'plan.json').write_text(json.dumps({'points':[{'point':'m2_prompt'}]}))
        (self.production/'seal.json').write_text('{}')
        (self.production/'gen_inventory.json').write_text(json.dumps({
            'complete':True,'points':{'m2_prompt':{'events':7}}}))
        self.profile = self.base/'profile.json'
        self.profile.write_text(json.dumps({'runtime_sha256':'a'*64}))
        self.out = self.base/'scan'

    def tearDown(self):
        self.scratch.cleanup()

    def prepare(self):
        return MODULE.prepare(self.production,MODULE.pin(self.profile),self.out)

    def test_frozen_entry_and_native_identity(self):
        original = MODULE.sha(self.production/'plan.json')
        result = self.prepare()
        config = MODULE.read_pin(result['config'])
        self.assertEqual(config['original_plan']['sha256'], original)
        self.assertEqual(config['replacement_runtime_sha256'],'a'*64)
        submit = Path(result['submit']['path']).read_text()
        self.assertIn('+SendCredential = True',submit)
        self.assertIn('priority = 1000000',submit)
        self.assertIn('+ShiftDPStatisticsTask = '+json.dumps(str(self.out)),submit)
        self.assertIn('initialdir = '+str(self.out),submit)
        self.assertIn('periodic_release = False',submit)
        self.assertEqual(MODULE.sha(self.production/'plan.json'),original)

    def test_refuses_incomplete_inventory(self):
        (self.production/'gen_inventory.json').write_text(json.dumps({'complete':False,'points':{}}))
        with self.assertRaisesRegex(ValueError,'complete original'):
            self.prepare()
        self.assertFalse(self.out.exists())

    def test_refuses_missing_point(self):
        (self.production/'gen_inventory.json').write_text(json.dumps({'complete':True,'points':{}}))
        with self.assertRaisesRegex(ValueError,'point sets differ'):
            self.prepare()

    def test_refuses_extra_point(self):
        (self.production/'gen_inventory.json').write_text(json.dumps({
            'complete':True,'points':{'m2_prompt':{},'m5_prompt':{}}}))
        with self.assertRaisesRegex(ValueError,'point sets differ'):
            self.prepare()

    def test_refuses_changed_profile(self):
        authority = MODULE.pin(self.profile)
        self.profile.write_text('{}')
        with self.assertRaisesRegex(ValueError,'Changed preflight dependency'):
            MODULE.prepare(self.production,authority,self.out)

    def test_preserves_existing_attempt(self):
        self.prepare()
        with self.assertRaisesRegex(FileExistsError,'Preserve existing'):
            self.prepare()

    def test_pinned_inputs_detect_later_changes(self):
        result = self.prepare(); config = MODULE.read_pin(result['config'])
        (self.production/'plan.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'Changed preflight dependency'):
            MODULE.read_pin(config['original_plan'])


if __name__ == '__main__':
    unittest.main()
