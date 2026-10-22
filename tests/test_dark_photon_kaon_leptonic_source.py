# Code style: AI-code. AI-generated fixed-M0 K_l2 source and proposal controls.
import copy
import json
import math
from pathlib import Path
import random
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from test_dark_photon_kaon_source import inputs as old_inputs,parent_row,MASS,EPSILON,BR,ECM
from dark_photon_meson_source import (coefficient_bound,choose_collision,exclusive_channels,
    parent_branching_coefficient,resolved_parent_models,resolve_parent_kinematics,
    source_cross_section_pb,validate_models)
from dark_photon_kaon_leptonic import decay_width
from dark_photon_model import HBAR_C_GEV_MM,mixing_parameters


def inputs(*,include_pions=False):
    old,native=old_inputs()
    authority=json.loads((ROOT/'config/physics/dark_photon_kl2_chpt_1993.json').read_text())
    parents=copy.deepcopy(old['parents']) if include_pions else []
    for pdg in (321,-321):
        for name,identifier in (('electron',11),('muon',13)):
            sign=1 if pdg>0 else -1
            current=dict(authority['form_factor_model'],
                lepton_mass_gev=authority['explicit_conventions'][name+'_mass_gev'])
            parents.append(dict(pdg_id=pdg,mass_gev=.493677,mean_proper_ctau_mm=3713.,
                decay_type='weak-kaon-leptonic',channel=f'{pdg}_{name}_nu',
                parent_mass_model='native-stable-kaon',pdg_reference='synthetic native mass authority',
                lifetime_reference='synthetic native lifetime authority',
                relative_numerical_mass_precision_budget=1.e-5,
                charged_lepton_pdg_id=-sign*identifier,neutrino_pdg_id=sign*(identifier+1),
                leptonic_form_factor_model=current,
                leptonic_rate_quadrature=dict(order=24,check_order=48,relative_convergence_tolerance=2.e-4),
                native_kinematic_precision=dict(relative_mass_limit=1.e-7,relative_endpoint_limit=1.e-6)))
    if not include_pions:native['parents']=[row for row in native['parents'] if abs(row['pdg_id'])==321]
    return dict(mechanism='meson-radiative',parents=parents,channels=[p['channel'] for p in parents]),native


class LeptonicKaonSourceTest(unittest.TestCase):
    def setUp(self):
        self.card,self.native=inputs()
        self.models=resolved_parent_models(self.card,self.native,MASS,epsilon=EPSILON)

    def resolve(self,parents,mass=MASS,models=None):
        return resolve_parent_kinematics(parents,.001,mass,models or self.models,BR)

    def test_physical_width_lifetime_and_exact_em_coupling_are_applied_once(self):
        for pdg,group in self.models.items():
            for channel in exclusive_channels(group):
                em=mixing_parameters(MASS,EPSILON).em_coefficient
                current=channel['leptonic_form_factor_model']
                physical=decay_width(current,MASS,epsilon=em,quadrature_order=48)['total']
                total_width=HBAR_C_GEV_MM/channel['mean_proper_ctau_mm']
                expected=physical/total_width/EPSILON**2*BR
                actual=parent_branching_coefficient(channel['mass_min_gev'],MASS,channel,BR)
                self.assertAlmostEqual(actual/expected,1.,places=12)
                self.assertEqual(channel['physical_epsilon'],EPSILON)
                self.assertEqual(channel['mean_proper_ctau_mm'],3713.)
                self.assertLess(channel['leptonic_rate_numerics']['relative_order_difference'],2.e-4)
                self.assertEqual(channel['recoil_pdg_ids'],
                    [channel['charged_lepton_pdg_id'],channel['neutrino_pdg_id']])
        positive=exclusive_channels(self.models[321]);negative=exclusive_channels(self.models[-321])
        for first,second in zip(positive,negative):
            self.assertEqual(first['leptonic_width_over_em_coupling_squared_gev'],
                second['leptonic_width_over_em_coupling_squared_gev'])

    def test_collision_coefficient_and_bound_use_exact_same_cached_value(self):
        rows=self.resolve([parent_row(321,self.models[321])])
        # Dense integration must not re-enter per collision, parent, sibling,
        # validation or envelope call once the fixed-point authority resolves.
        with patch('dark_photon_kaon_leptonic.decay_width',side_effect=AssertionError('rate recomputed')):
            bound=coefficient_bound(ECM,BR,self.models,MASS)
            result=choose_collision(rows,MASS,BR,self.models,bound,random.Random(1))
        expected=math.fsum(c['leptonic_width_over_em_coupling_squared_gev']/c['total_parent_width_gev']*
            c['em_mixing_over_epsilon_abs2']*BR for c in exclusive_channels(self.models[321]))
        self.assertEqual(result['coefficient_sum'],expected)
        self.assertEqual(bound,math.fsum(c['native_parent_count_bound']*
            parent_branching_coefficient(c['mass_min_gev'],MASS,c,BR)
            for g in self.models.values() for c in exclusive_channels(g)))

    def test_same_native_parent_owns_pion_electron_and_muon_siblings(self):
        card,native=inputs(include_pions=True)
        models=resolved_parent_models(card,native,MASS,epsilon=EPSILON)
        self.assertEqual(len(exclusive_channels(models[321])),3)
        row,=self.resolve([parent_row(321,models[321])],models=models)
        bound=coefficient_bound(ECM,BR,models,MASS)
        coefficients=[parent_branching_coefficient(c['mass_min_gev'],MASS,c,BR)
            for c in exclusive_channels(models[321])]
        class Draws:
            def __init__(self,draw):self.values=iter([0.,draw])
            def random(self):return next(self.values)
        cumulative=0.;selected=[]
        for channel,coefficient in zip(exclusive_channels(models[321]),coefficients):
            draw=(cumulative+coefficient/2)/sum(coefficients)
            result=choose_collision([row],MASS,BR,models,bound,Draws(draw))
            self.assertEqual(result['parent_count'],1)
            self.assertEqual(result['parent_index'],row['index'])
            self.assertEqual(result['gen_weight'],1.)
            selected.append(result['selected_channel']);cumulative+=coefficient
        self.assertEqual(selected,[c['channel'] for c in exclusive_channels(models[321])])

    def test_species_count_floor_is_deterministic_and_zeros_remain_in_exposure(self):
        bound=coefficient_bound(ECM,BR,self.models,MASS)
        empty=choose_collision(self.resolve([]),MASS,BR,self.models,bound,random.Random(3))
        self.assertEqual(empty['coefficient_sum'],0.)
        self.assertFalse(empty['accepted'])
        for pdg,group in self.models.items():
            count=math.floor(ECM/group['mass_min_gev'])
            raw=[parent_row(pdg,group,index=i+1) for i in range(count)]
            for row in raw:
                row['p4']=[row['native_mass_gev'],0.,0.,0.]
                row['source_com_energy_gev']=row['native_mass_gev']
            rows=self.resolve(raw)
            result=choose_collision(rows,MASS,BR,self.models,bound,random.Random(7))
            self.assertLessEqual(result['coefficient_sum'],bound)
            self.assertEqual(result['parent_count'],count)
            with self.assertRaises(ValueError):self.resolve(raw+[dict(raw[0],index=count+1)])
        C=result['coefficient_sum']
        self.assertAlmostEqual(source_cross_section_pb(26.,EPSILON,C,100),26.e9*EPSILON**2*C/100)

    def test_fixed_rate_does_not_hide_the_independent_native_kinematic_gate(self):
        row=parent_row(321,self.models[321]);nominal=row['native_mass_gev']
        row['p4'][0]*=1+1.e-10;row['source_com_energy_gev']=row['p4'][0]
        before=copy.deepcopy(row)
        resolved,=self.resolve([row])
        self.assertEqual(row,before)
        self.assertEqual(resolved['mass_gev'],nominal)
        self.assertEqual(resolved['coefficient_relative_precision_shift'],0.)
        self.assertEqual(len(resolved['leptonic_kinematic_precision']),2)
        self.assertTrue(all(c['relative_mass_shift']>0 for c in resolved['leptonic_kinematic_precision']))
        saved=copy.deepcopy(resolved)
        repeated,=self.resolve([resolved])
        self.assertEqual(resolved,saved)
        self.assertEqual(repeated['leptonic_kinematic_precision'],resolved['leptonic_kinematic_precision'])
        broken=parent_row(321,self.models[321]);broken['p4'][0]*=1+1.e-6
        broken['source_com_energy_gev']=broken['p4'][0]
        with self.assertRaisesRegex(ValueError,'numerical precision gate'):self.resolve([broken])

    def test_endpoint_gate_catches_small_mass_shift_before_mass_limit(self):
        signal=.387
        models=resolved_parent_models(self.card,self.native,signal,epsilon=EPSILON)
        row=parent_row(321,models[321]);mass=row['native_mass_gev']
        row['p4'][0]=math.sqrt((mass+2.e-9)**2+sum(x*x for x in row['p4'][1:]))
        row['source_com_energy_gev']=row['p4'][0]
        with self.assertRaisesRegex(ValueError,'endpoint numerical precision gate'):
            self.resolve([row],mass=signal,models=models)

    def test_closed_muon_and_closed_full_points_have_zero_cached_rates(self):
        models=resolved_parent_models(self.card,self.native,.4,epsilon=EPSILON)
        channels=exclusive_channels(models[321])
        self.assertGreater(channels[0]['leptonic_width_over_em_coupling_squared_gev'],0.)
        self.assertEqual(channels[1]['leptonic_width_over_em_coupling_squared_gev'],0.)
        closed=resolved_parent_models(self.card,self.native,.7,epsilon=EPSILON)
        self.assertEqual(coefficient_bound(ECM,BR,closed,.7),0.)

    def test_bad_flavors_duplicate_branches_and_changed_cached_authority_fail(self):
        for changes in (dict(neutrino_pdg_id=-12),dict(charged_lepton_pdg_id=11),
                        dict(native_kinematic_precision={}),dict(leptonic_rate_quadrature={})):
            card=copy.deepcopy(self.card);card['parents'][0].update(changes)
            with self.assertRaises(ValueError):resolved_parent_models(card,self.native,MASS,epsilon=EPSILON)
        card=copy.deepcopy(self.card);duplicate=copy.deepcopy(card['parents'][0]);duplicate['channel']='duplicate_electron'
        card['parents'].append(duplicate);card['channels'].append(duplicate['channel'])
        with self.assertRaisesRegex(ValueError,'Duplicate exclusive kaon'):resolved_parent_models(card,self.native,MASS,epsilon=EPSILON)
        model=copy.deepcopy(self.models);exclusive_channels(model[321])[0]['leptonic_width_over_em_coupling_squared_gev']*=2
        with self.assertRaisesRegex(ValueError,'rate authority'):validate_models(model)
        model=copy.deepcopy(self.models);exclusive_channels(model[321])[0]['leptonic_form_factor_model']['parent_pdg_id']=-321
        with self.assertRaises(ValueError):validate_models(model)


if __name__=='__main__':unittest.main()
