# Code style: AI-code. AI-generated contract tests for the sealed full-census scanner.
import importlib.util
import json
import os
import hashlib
import shutil
from unittest import mock
from pathlib import Path
import tempfile
import sys
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

    def test_scanner_uses_only_pinned_capsule_authority_helper_and_clears_inherited_authority(self):
        release=self.base/'CMSSW_17_0_0_pre4';release.mkdir()
        helper=self.base/'workflow_helpers/dark_photon_stage_budget.py';helper.parent.mkdir()
        helper.write_text('def recorded_primary_environment(profile,gen):\n return {"SHIFT_RECORDED_PRIMARY_GEN_SHA256":gen}\n')
        manifest=dict(files={'workflow_helpers/dark_photon_stage_budget.py':dict(sha256=MODULE.sha(helper))})
        inherited={'SHIFT_RECORDED_PRIMARY_AUTHORITY':'bad','SHIFT_RECORDED_PRIMARY_AUTHORITY_SHA256':'bad','SHIFT_RECORDED_PRIMARY_GEN_SHA256':'bad'}
        with mock.patch.dict(os.environ,inherited):
            plain=MODULE.native_point_environment({},manifest,release,'a'*64)
            for name in inherited:self.assertNotIn(name,plain)
            actual=MODULE.native_point_environment(dict(recorded_primary_authority={}),manifest,release,'a'*64)
            self.assertEqual(actual['SHIFT_RECORDED_PRIMARY_GEN_SHA256'],'a'*64)
            self.assertNotIn('SHIFT_RECORDED_PRIMARY_AUTHORITY',actual)
            helper.write_text(helper.read_text()+'# modified\n')
            with self.assertRaisesRegex(ValueError,'helper'):
                MODULE.native_point_environment(dict(recorded_primary_authority={}),manifest,release,'a'*64)

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

    def test_each_point_configuration_uses_fresh_native_interpreter(self):
        config = self.base/'preflight.json'
        first = MODULE.point_config_command(config,'m2_prompt',self.base/'one.root',self.base/'first')
        second = MODULE.point_config_command(config,'m15_prompt',self.base/'two.root',self.base/'second')
        self.assertEqual(first[:3],[sys.executable,str(SCRIPT),'--build-point'])
        self.assertEqual(second[:3],[sys.executable,str(SCRIPT),'--build-point'])
        self.assertEqual(first[4],'m2_prompt')
        self.assertEqual(second[4],'m15_prompt')
        self.assertNotEqual(first,second)


    def test_explicit_point_scope_freezes_full_original_plan_and_inventory(self):
        plan={'points':[{'point':'m2_prompt'},{'point':'m15_prompt'},{'point':'m40_prompt'}]}
        inventory={'complete':True,'points':{name:{'events':1000} for name in ('m2_prompt','m15_prompt','m40_prompt')}}
        (self.production/'plan.json').write_text(json.dumps(plan))
        (self.production/'gen_inventory.json').write_text(json.dumps(inventory))
        before={name:MODULE.sha(self.production/name) for name in ('plan.json','gen_inventory.json','seal.json')}
        result=MODULE.prepare(self.production,MODULE.pin(self.profile),self.out,point_scope=['m15_prompt'])
        config=MODULE.read_pin(result['config'])
        self.assertEqual(config['point_scope'],['m15_prompt'])
        self.assertEqual(config['original_plan']['sha256'],before['plan.json'])
        self.assertEqual(config['inventory']['sha256'],before['gen_inventory.json'])
        self.assertEqual(config['original_seal']['sha256'],before['seal.json'])
        self.assertEqual(MODULE.scoped_points(plan,config['point_scope']),[plan['points'][1]])
        self.assertEqual({name:MODULE.sha(self.production/name) for name in before},before)
        # The actual frozen scanner contains the scope gate, rather than a
        # worker wrapper filtering the GEN or material-selected event list.
        frozen=next(pin for pin in config['helpers'] if Path(pin['path']).name==SCRIPT.name)
        self.assertEqual(frozen['sha256'],MODULE.sha(SCRIPT))
        spec=importlib.util.spec_from_file_location('frozen_scoped_scan',frozen['path'])
        scanner=importlib.util.module_from_spec(spec);spec.loader.exec_module(scanner)
        self.assertEqual(scanner.scoped_points(plan,config['point_scope']),[plan['points'][1]])
        self.assertEqual(scanner.scoped_points(plan),plan['points'])

    def test_empty_duplicate_unknown_or_malformed_scope_is_rejected_before_freezing(self):
        for scope in ([],['m2_prompt','m2_prompt'],['m15_prompt'],'m2_prompt',[123]):
            with self.subTest(scope=scope),self.assertRaises(ValueError):
                MODULE.prepare(self.production,MODULE.pin(self.profile),self.out,point_scope=scope)
            self.assertFalse(self.out.exists())

    def test_native_scan_receipt_contains_only_requested_whole_point(self):
        import hashlib
        import os
        from types import SimpleNamespace,ModuleType
        from unittest.mock import patch
        points=['m2_prompt','m15_prompt','m40_prompt'];identities=[[1,1,index] for index in range(1,4)]
        payload=b'full original GEN including material-rejected events'
        inventory={'complete':True,'points':{name:{'events':3,'event_ids':identities,
            'gen_sha256':hashlib.sha256(payload).hexdigest(),'gen_path':'/eos/'+name+'.root'} for name in points}}
        plan={'points':[{'point':name,'material_selection':{'selected_indices':[0]}} for name in points]}
        (self.production/'plan.json').write_text(json.dumps(plan));(self.production/'gen_inventory.json').write_text(json.dumps(inventory))
        release=self.base/'release';files={}
        for name in ('SimG4Core/Generators/src/Generator.cc','SimG4Core/Generators/src/Generator3.cc',
                     'SimG4Core/Generators/interface/RecordedPrimaryMomentum.h'):
            path=release/'src'/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('frozen converter')
            files['CMSSW_17_0_0_pre4/src/'+name]={'sha256':MODULE.sha(path)}
        manifest=self.base/'manifest.json';manifest.write_text(json.dumps({'files':files}))
        self.profile.write_text(json.dumps({'runtime_sha256':'a'*64,'manifest':MODULE.pin(manifest)}))
        result=MODULE.prepare(self.production,MODULE.pin(self.profile),self.out,point_scope=['m15_prompt'])
        fw=ModuleType('DataFormats.FWLite')
        fw.Events=lambda path:[SimpleNamespace(eventAuxiliary=lambda identity=identity:SimpleNamespace(
            run=lambda:identity[0],luminosityBlock=lambda:identity[1],event=lambda:identity[2])) for identity in identities]
        calls=[]
        def run(command,**kwargs):
            calls.append(command)
            if command[0]=='/usr/bin/xrdcp':Path(command[-1]).write_bytes(payload)
            elif '--build-point' in command:
                name=command[4];(self.out/(name+'_cfg.py')).write_text('full census configuration')
            elif command[0]=='cmsRun':
                (self.out/'m15_prompt_converter.json').write_text(json.dumps({'complete':True,'events':3}))
        with patch.dict(os.environ,CMSSW_BASE=str(release)),patch.dict(sys.modules,{'DataFormats.FWLite':fw}),patch.object(MODULE.subprocess,'run',side_effect=run):
            MODULE.scan(result['config']['path'])
        receipt=json.loads(Path(result['receipt']).read_text())
        self.assertTrue(receipt['complete']);self.assertEqual(receipt['point_scope'],['m15_prompt'])
        self.assertEqual(set(receipt['points']),{'m15_prompt'})
        self.assertEqual(receipt['points']['m15_prompt']['full_count'],3)
        self.assertEqual(receipt['points']['m15_prompt']['converted_count'],3)
        self.assertEqual(receipt['original_inventory_sha256'],MODULE.sha(self.production/'gen_inventory.json'))
        self.assertEqual([command[4] for command in calls if '--build-point' in command],['m15_prompt'])


if __name__ == '__main__':
    unittest.main()
