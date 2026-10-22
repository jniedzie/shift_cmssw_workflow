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
from run_dark_photon_meson_gen import SOURCES,freeze_meson_model,sample_kaon_decay,kaon_mean_beta_gamma
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

    def test_leptonic_dispatch_keeps_physical_rate_lifetime_and_three_body_mean(self):
        from dark_photon_meson_source import resolved_parent_models,parent_branching_coefficient,source_cross_section_pb
        from dark_photon_kaon_leptonic import decay_width,mean_lab_beta_gamma
        from dark_photon_model import HBAR_C_GEV_MM,mixing_parameters
        authority=json.loads((ROOT/'config/physics/dark_photon_kl2_chpt_1993.json').read_text())
        signal=.3;epsilon=1.557820285384222e-6;ctau=58.55432679904731;branching=.4
        for pdg in (321,-321):
            for lepton in ('electron','muon'):
                sign=1 if pdg>0 else -1;charged=-sign*(11 if lepton=='electron' else 13)
                current=dict(authority['form_factor_model'],lepton_mass_gev=authority['explicit_conventions'][lepton+'_mass_gev'])
                raw=dict(pdg_id=pdg,mass_gev=.493677,mean_proper_ctau_mm=3713.,
                    decay_type='weak-kaon-leptonic',channel=f'{pdg}_{lepton}',
                    parent_mass_model='native-stable-kaon',pdg_reference='synthetic initialized source',
                    lifetime_reference='synthetic initialized source',relative_numerical_mass_precision_budget=1e-5,
                    charged_lepton_pdg_id=charged,neutrino_pdg_id=sign*(abs(charged)+1),
                    leptonic_form_factor_model=current,
                    leptonic_rate_quadrature=dict(order=24,check_order=48,relative_convergence_tolerance=2e-4),
                    native_kinematic_precision=dict(relative_mass_limit=1e-7,relative_endpoint_limit=1e-6))
                configuration=dict(ecm_gev=10.,check_event=True,parents=[dict(pdg_id=pdg,
                    mass_min_gev=.493677,mass_max_gev=.493677,mass_width_gev=0.)])
                model=resolved_parent_models(dict(mechanism='meson-radiative',parents=[raw],
                    channels=[raw['channel']]),configuration,signal,epsilon=epsilon)[pdg]
                coefficient=parent_branching_coefficient(.493677,signal,model,branching)
                em=mixing_parameters(signal,epsilon).em_coefficient
                physical=decay_width(current,signal,epsilon=em,quadrature_order=48)['total']/(HBAR_C_GEV_MM/3713.)*branching
                self.assertAlmostEqual(coefficient*epsilon**2/physical,1.,places=12)
                self.assertAlmostEqual(source_cross_section_pb(26.,epsilon,coefficient,2),26e9*physical/2)
                parent=dict(index=7,pdg_id=pdg,native_mass_gev=.493677,p4=[.493677,0.,0.,0.])
                sample=sample_kaon_decay(parent,model,signal,ctau,random.Random(8))
                doubled=sample_kaon_decay(parent,model,signal,2*ctau,random.Random(8))
                self.assertEqual(sample['dark_photon_p4'],doubled['dark_photon_p4'])
                self.assertEqual(2*sample['proper_decay_length_mm'],doubled['proper_decay_length_mm'])
                self.assertFalse(sample['parent_flight_included'])
                self.assertEqual(sample['recoil_pdg_ids'],model['recoil_pdg_ids'])
                sample=json.loads(json.dumps(sample))
                native=dict(parent,production_position_mm=[1.,2.,3.,4.],
                    stored_decay_position_mm=[1.,2.,3.,123.],selected_proper_lifetime_mm=119.)
                parent.update(native)
                sample.update(native_parent_flight=native,native_parent_flight_attached=True,
                    recoil_records=[dict(pdg_id=identifier,status=1) for identifier in sample['recoil_pdg_ids']])
                graph=dict(barcode=7,pdg_id=pdg,native_mass_gev=.493677,
                    production_position_mm=native['production_position_mm'],decay_position_mm=native['stored_decay_position_mm'])
                selected=dict(parent=parent,graph=graph,sampled_decay=sample,resolved_model=model,trial=1,slot=1)
                record=dict(mother_barcode=7,mother_pdg_id=pdg,mother_generated_mass_gev=.493677,
                    mother_p4=parent['p4'],mother_production_position_mm=graph['production_position_mm'],
                    decay_position_mm=graph['decay_position_mm'],outgoing=[dict(pdg_id=32,status=3,p4=sample['dark_photon_p4'])]+
                    [dict(pdg_id=identifier,status=1,p4=p4) for identifier,p4 in zip(sample['recoil_pdg_ids'],sample['recoil_p4s'])])
                self.assertLess(validate_meson_radiative_record(record,selected)['maximum_momentum_residual_gev'],1e-12)
                broken=copy.deepcopy(selected);broken['sampled_decay']['neutrino_pdg_id']*=-1
                with self.assertRaisesRegex(ValueError,'Leptonic kaon'):validate_meson_radiative_record(record,broken)
                mean=kaon_mean_beta_gamma(parent,model,signal)
                self.assertEqual(mean,mean_lab_beta_gamma(parent['p4'],signal,model=current,
                    native_kinematic_precision=model['native_kinematic_precision']))
                self.assertGreater(mean,0.)
                # The effective recoil has a continuum mass; a zero-mass
                # two-body reference would systematically overestimate it.
                import dark_photon_vector_meson as vector
                self.assertLess(mean,vector.mean_lab_beta_gamma(parent['p4'],0.,signal,polarization='unpolarized'))


if __name__=='__main__':unittest.main()
