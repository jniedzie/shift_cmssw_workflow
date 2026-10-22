# Code style: AI-code. AI-generated neutral-dipion source and duplicate-recoil checks.
import copy
import json
import math
from pathlib import Path
import random
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from test_dark_photon_neutral_vector_dipion_sampler import model as current_model
from audit_dark_photon_gen import validate_meson_radiative_record
from dark_photon_meson_source import (FULL_SOURCE_NATIVE_PRECISION_POLICY as POLICY,
    coefficient_bound,deferred_native_precision_measure,dipion_kernel,exclusive_channels,
    full_source_native_precision_summary,parent_branching_coefficient,resolved_parent_models,
    resolve_parent_kinematics)
from run_dark_photon_meson_gen import SOURCES


def parent(pdg=113):
    current=current_model(pdg,32);current['reference']='https://arxiv.org/pdf/hep-ph/0105179'
    return dict(pdg_id=pdg,channel=f'{pdg}->pi0_pi0_Aprime',mass_gev=current['reference_parent_mass_gev'],
        decay_type='neutral-vector-dipion',pion_mass_gev=current['pion_mass_gev'],
        neutral_dipion_model=current,recoil_pdg_ids=[111,111],parent_polarization='unpolarized',
        parent_mass_model='zero-width-pole',pdg_reference='synthetic pinned native pole')


def resolve(pdg=113,mass=.3,policy=POLICY):
    raw=parent(pdg);M=raw['mass_gev']
    card=dict(schema='shift-dark-photon-meson-model-v1',mechanism='meson-radiative',
        parents=[raw],channels=[raw['channel']],native_precision_policy=copy.deepcopy(policy))
    configuration=dict(parents=[dict(pdg_id=pdg,mass_min_gev=M,mass_max_gev=M,mass_width_gev=0.)])
    return card,resolved_parent_models(card,configuration,mass,epsilon=1.e-6)


class NeutralVectorDipionSourceTest(unittest.TestCase):
    def test_combined_card_owns_each_exclusive_channel_once(self):
        path=ROOT/'config/physics/dark_photon_meson_leading_model.json'
        card=json.loads(path.read_text());self.assertEqual(len(card['parents']),30)
        rows={item['pdg_id']:item['mass_gev'] for item in card['parents']}
        configuration=dict(check_event=True,ecm_gev=112.97005223140982,
            parents=[dict(pdg_id=pdg,mass_min_gev=value,mass_max_gev=value,mass_width_gev=0.)
                     for pdg,value in rows.items()],native_kaon_mass_authorities=[
                dict(pdg_id=pdg,mass_gev=value,mass_min_gev=value,mass_max_gev=value,mass_width_gev=0.)
                for pdg,value in ((130,.49761),(310,.49761),(311,.497611))])
        models=resolved_parent_models(card,configuration,.3,epsilon=1.557820285384222e-6,
            model_directory=path.parent)
        self.assertEqual(sum(len(exclusive_channels(value)) for value in models.values()),30)
        self.assertEqual(len(exclusive_channels(models[113])),4)
        self.assertEqual(len(exclusive_channels(models[223])),3)
        self.assertEqual(len(exclusive_channels(models[321])),3)
        self.assertGreater(coefficient_bound(configuration['ecm_gev'],.4,models,.3),0.)
        self.assertFalse(card['physics_complete']);self.assertFalse(card['production_rate_validated'])

    def test_fixed_native_rate_has_a_deterministic_count_bound_and_explicit_policy(self):
        card,models=resolve();raw=card['parents'][0];M=raw['mass_gev'];current=models[113]
        value=parent_branching_coefficient(M,.3,current,.4)
        self.assertGreater(value,0.)
        self.assertEqual(coefficient_bound(10*M,.4,models,.3),10*value)
        self.assertEqual(dipion_kernel(current).__name__,'dark_photon_neutral_vector_dipion')
        with self.assertRaisesRegex(ValueError,'precision policy'):resolve(policy=None)
        changed=copy.deepcopy(models);changed[113]['recoil_pdg_ids']=[211,-211]
        with self.assertRaises(ValueError):coefficient_bound(10*M,.4,changed,.3)
        self.assertIn('dark_photon_neutral_vector_dipion.py',SOURCES)
        self.assertIn('dark_photon_double_radiative.py',SOURCES)

    def test_native_continuation_charges_the_entire_channel_without_changing_probability(self):
        card,models=resolve();M=card['parents'][0]['mass_gev']
        original=dict(index=7,pdg_id=113,native_mass_gev=M,p4=[M*1.0004,0.,0.,0.])
        checked,=resolve_parent_kinematics([original],.001,.3,models,.4,native_precision_policy=POLICY)
        self.assertEqual(checked['p4'],original['p4']);self.assertEqual(checked['mass_gev'],M)
        self.assertEqual(checked['coefficient_difference_from_kinematic_mass'],0.)
        rate=parent_branching_coefficient(M,.3,models[113],.4)
        measure,count=deferred_native_precision_measure([checked],.4,POLICY)
        self.assertEqual(count,1);self.assertEqual(measure,2*rate)
        self.assertFalse(full_source_native_precision_summary(POLICY,rate,measure,count)['complete'])
        self.assertTrue(full_source_native_precision_summary(POLICY,3000*rate,measure,count)['complete'])
        with self.assertRaisesRegex(ValueError,'policy'):
            resolve_parent_kinematics([original],.001,.3,models,.4)
        with self.assertRaisesRegex(ValueError,'Pythia'):
            resolve_parent_kinematics([original],1.e-8,.3,models,.4,native_precision_policy=POLICY)
        sample=dipion_kernel(models[113]).sample_decay(original['p4'],.3,10.,random.Random(14),
            model=models[113],native_mass_gev=M,native_mass_tolerance=.001,defer_to_full_source=True)
        self.assertEqual(sample['event_parent_mass_gev'],original['p4'][0])
        mean=dipion_kernel(models[113]).mean_lab_beta_gamma(original['p4'],.3,models[113],
            native_mass_gev=M,native_mass_tolerance=.001,defer_to_full_source=True)
        self.assertGreater(mean,0.)

    def test_two_identical_pi0_recoils_are_audited_once_each(self):
        for pdg in (113,223):
            card,models=resolve(pdg);M=card['parents'][0]['mass_gev'];current=models[pdg]
            sample=dipion_kernel(current).sample_decay((M,0.,0.,0.),.3,10.,random.Random(35),model=current)
            sample=json.loads(json.dumps(sample))
            selected=dict(slot=1,trial=31,parent=dict(index=12,pdg_id=pdg,native_mass_gev=M,p4=[M,0.,0.,0.]),
                graph=dict(barcode=12,pdg_id=pdg,native_mass_gev=M,
                    production_position_mm=[0.,0.,0.,0.],decay_position_mm=[0.,0.,0.,.001]),
                sampled_decay=sample,resolved_model=current)
            record=dict(mother_barcode=12,mother_pdg_id=pdg,mother_generated_mass_gev=M,mother_p4=[M,0.,0.,0.],
                mother_production_position_mm=[0.,0.,0.,0.],decay_position_mm=[0.,0.,0.,.001],outgoing=[
                    dict(pdg_id=32,status=3,p4=sample['dark_photon_p4'])]+[
                    dict(pdg_id=111,status=2,p4=p4) for p4 in sample['recoil_p4s']])
            self.assertLess(validate_meson_radiative_record(record,selected)['maximum_momentum_residual_gev'],1.e-12)
            changed=copy.deepcopy(record);changed['outgoing'].reverse()
            validate_meson_radiative_record(changed,selected)
            changed['outgoing'][0]['p4']=changed['outgoing'][1]['p4']
            with self.assertRaisesRegex(ValueError,'missing or reused'):validate_meson_radiative_record(changed,selected)


if __name__=='__main__':unittest.main()
