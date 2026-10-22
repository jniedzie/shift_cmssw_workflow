# Code style: AI-code. AI-generated portable kaon runner and metadata controls.
import copy
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from audit_dark_photon_gen import validate_meson_radiative_record
from run_dark_photon_meson_gen import SOURCES,freeze_meson_model,sample_kaon_decay
from run_dark_photon_meson_production import compact_files
from prepare_dark_photon_meson_production import TOOLS
from export_dark_photon_cross_sections import input_files


class KaonRunnerTest(unittest.TestCase):
    def test_declared_rate_input_is_frozen_portably_and_refreezes_identically(self):
        source=ROOT/'config/physics/dark_photon_kaon_source_canary.json'
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary);first=directory/'first';second=directory/'second'
            first.mkdir();second.mkdir()
            card,inputs=freeze_meson_model(source,first/'meson_model.json')
            repeated,repeated_inputs=freeze_meson_model(first/'meson_model.json',second/'meson_model.json')
            self.assertEqual((card,inputs),(repeated,repeated_inputs))
            self.assertEqual((first/'meson_model.json').read_bytes(),(second/'meson_model.json').read_bytes())
            self.assertEqual(len(inputs),1)
            for relative,expected in inputs.items():
                self.assertTrue(relative.startswith('physics_inputs/'))
                self.assertEqual(hashlib.sha256((second/relative).read_bytes()).hexdigest(),expected)
            (second/'contract.json').write_text(json.dumps(dict(meson_physics_inputs_sha256=inputs,
                meson_model_card=card,production_mechanism='meson-radiative')))
            kept={str(path.relative_to(second)) for path in compact_files(second)}
            self.assertTrue(set(inputs)<=kept)
            self.assertTrue(set(inputs)<=set(input_files(second,{})))
            relative,=inputs
            (second/relative).write_text('changed')
            with self.assertRaisesRegex(ValueError,'Pinned rate input changed'):compact_files(second)
            with self.assertRaisesRegex(ValueError,'Pinned KS dispersive input changed'):
                freeze_meson_model(second/'meson_model.json',directory/'failed.json')

    def test_old_flat_model_keeps_its_bytes_and_declared_paths_cannot_escape(self):
        source=ROOT/'config/campaigns/dark_photon_meson_model.json'
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary);target=directory/'meson_model.json'
            _,inputs=freeze_meson_model(source,target)
            self.assertEqual(inputs,{})
            self.assertEqual(source.read_bytes(),target.read_bytes())
            (directory/'contract.json').write_text(json.dumps(dict(
                meson_physics_inputs_sha256={'../external.json':'0'*64})))
            with self.assertRaisesRegex(ValueError,'frozen relative path'):compact_files(directory)

    def test_isolated_gen_detector_aggregate_and_rate_deployments_import_coherently(self):
        groups={'GEN':set(SOURCES),'detector':set(TOOLS),
            'aggregate':set(SOURCES)|{'aggregate_dark_photon_samples.py'},'rate_export':set(SOURCES)}
        with tempfile.TemporaryDirectory() as temporary:
            for name,files in groups.items():
                directory=Path(temporary)/name;directory.mkdir()
                for filename in files:shutil.copy2(ROOT/'scripts'/filename,directory/filename)
                imports=['run_dark_photon_meson_gen','dark_photon_meson_source','dark_photon_kaon',
                         'dark_photon_weak_meson','dark_photon_kaon_leptonic','export_dark_photon_cross_sections']
                if name=='detector':imports+=['prepare_dark_photon_meson_production','prepare_dark_photon_production']
                if name=='aggregate':imports+=['aggregate_dark_photon_samples']
                code='import importlib,sys;sys.dont_write_bytecode=True;sys.path.insert(0,'+repr(str(directory))+');'
                code+=';'.join('importlib.import_module('+repr(module)+')' for module in imports)
                completed=subprocess.run([sys.executable,'-I','-c',code],cwd=directory,text=True,
                    capture_output=True,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'),check=False)
                self.assertEqual(completed.returncode,0,name+': '+completed.stderr)

    def test_each_kaon_current_preserves_its_spin_recoil_and_native_parent_flight(self):
        for pdg,kind,recoil,mass_recoil in ((130,'neutral-kaon-photon',22,0.),
                (310,'neutral-kaon-photon',22,0.),(321,'weak-kaon-pion',211,.13957039),
                (-321,'weak-kaon-pion',-211,.13957039),(310,'weak-kaon-pion',111,.1349768)):
            mass=.493677 if abs(pdg)==321 else .49761
            native=dict(index=7,pdg_id=pdg,native_mass_gev=mass,p4=[mass,0.,0.,0.],
                production_position_mm=[1.,2.,3.,4.],stored_decay_position_mm=[1.,2.,3.,123.],
                selected_proper_lifetime_mm=119.,mean_proper_lifetime_mm=30.)
            parent=copy.deepcopy(native)
            model=dict(decay_type=kind,recoil_pdg_id=recoil,recoil_mass_gev=mass_recoil)
            sampled=sample_kaon_decay(parent,model,.3,58.,random.Random(2))
            sampled=json.loads(json.dumps(sampled))
            sampled.update(native_parent_flight=native,native_parent_flight_attached=True)
            graph=dict(barcode=7,pdg_id=pdg,native_mass_gev=mass,
                production_position_mm=native['production_position_mm'],decay_position_mm=native['stored_decay_position_mm'])
            selected=dict(parent=parent,graph=graph,sampled_decay=sampled,resolved_model=model,trial=13,slot=1)
            record=dict(mother_barcode=7,mother_pdg_id=pdg,mother_generated_mass_gev=mass,
                mother_p4=parent['p4'],mother_production_position_mm=graph['production_position_mm'],
                decay_position_mm=graph['decay_position_mm'],outgoing=[dict(pdg_id=32,status=3,p4=sampled['dark_photon_p4']),
                    dict(pdg_id=recoil,status=1 if recoil==22 else 2,
                        p4=sampled['photon_p4' if recoil==22 else 'recoil_p4'])])
            result=validate_meson_radiative_record(record,selected)
            self.assertLess(result['maximum_momentum_residual_gev'],1e-12)
            for key,value in [('native_parent_flight_attached',False),('parent_flight_included',True),
                    ('dark_photon_polarization','unknown')]:
                changed=copy.deepcopy(selected);changed['sampled_decay'][key]=value
                with self.assertRaises(ValueError):validate_meson_radiative_record(record,changed)
            changed=copy.deepcopy(selected);changed['sampled_decay']['native_parent_flight']['selected_proper_lifetime_mm']+=1
            with self.assertRaisesRegex(ValueError,'native flight'):validate_meson_radiative_record(record,changed)


if __name__=='__main__':unittest.main()
