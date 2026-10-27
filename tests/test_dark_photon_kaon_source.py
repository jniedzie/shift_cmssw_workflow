# Code style: AI-code. AI-generated kaon source coefficient/count controls.
import copy
import hashlib
import json
import math
from pathlib import Path
import random
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from dark_photon_meson_source import (coefficient_bound,choose_collision,exclusive_channels,
    parent_branching_coefficient,resolved_parent_models,resolve_parent_kinematics,
    source_cross_section_pb,validate_models)
from dark_photon_kaon import KSDispersiveReference,radiative_branching,transition_form_factor
from dark_photon_model import HBAR_C_GEV_MM,mixing_parameters
from dark_photon_weak_meson import chiral_form_factor,decay_width

MASS=.3;EPSILON=1.557820285384222e-6;BR=.4;ECM=10.


def inputs():
    charged=json.loads((ROOT/'config/physics/dark_photon_kpi_vector_na62_2022.json').read_text())['form_factor_model']
    short=json.loads((ROOT/'config/physics/dark_photon_ks_pi0_vector_na48_2004.json').read_text())['form_factor_model']
    reference=ROOT/'config/physics/dark_photon_ks_dispersive_2016.json'
    parents=[]
    for pdg,mass,mean,kind,name in ((130,.497611,15340.,'neutral-kaon-photon','KL_gamma'),
            (310,.497611,26.844,'neutral-kaon-photon','KS_gamma'),
            (321,.493677,3713.,'weak-kaon-pion','Kplus_pi'),
            (-321,.493677,3713.,'weak-kaon-pion','Kminus_pi'),
            (310,.497611,26.844,'weak-kaon-pion','KS_pi0')):
        row=dict(pdg_id=pdg,mass_gev=mass,mean_proper_ctau_mm=mean,decay_type=kind,
            channel=name,parent_mass_model='native-stable-kaon',pdg_reference='synthetic native test table',
            lifetime_reference='synthetic native test lifetime',relative_numerical_mass_precision_budget=1.e-5)
        if pdg in (130,310):
            row.update(native_record_mass_support_gev=[mass],
                native_record_mass_support_reference='synthetic initialized physical/flavor union')
        if kind=='neutral-kaon-photon':
            if pdg==130:
                row.update(br_gamma_gamma=.0005471,br_gamma_gamma_reference='explicit test calibration',
                    transition_form_factor=dict(kind='kl-dip-single-virtual',parent_pdg_id=130,
                        alpha_dip=-1.729,rho_mass_gev=.77526,valid_mass_range_gev=[0.,.497611],
                        reference='https://arxiv.org/pdf/hep-ex/0702039'))
            else:
                row['ks_dispersive_reference']=dict(path=str(reference),sha256=hashlib.sha256(reference.read_bytes()).hexdigest())
        else:
            weak=copy.deepcopy(short if pdg==310 else charged);weak['parent_pdg_id']=pdg
            row.update(recoil_pdg_id={321:211,-321:-211,310:111}[pdg],
                recoil_mass_gev=.1349768 if pdg==310 else .13957039,
                weak_form_factor_model=weak,alpha_em=1/137.035999084)
        parents.append(row)
    card=dict(mechanism='meson-radiative',parents=parents,channels=[p['channel'] for p in parents])
    native=dict(ecm_gev=ECM,check_event=True,parents=[dict(pdg_id=p,
        mass_min_gev=.493677 if abs(p)==321 else .497611,
        mass_max_gev=.493677 if abs(p)==321 else .497611,mass_width_gev=0.) for p in (130,310,321,-321)])
    native['native_kaon_mass_authorities']=[dict(pdg_id=p,mass_gev=.497611,
        mass_min_gev=.497611,mass_max_gev=.497611,mass_width_gev=0.) for p in (130,310,311)]
    return card,native


def parent_row(pdg,model,index=1):
    channel=exclusive_channels(model)[0];mass=channel['mass_min_gev']
    momentum=(.2,.1,.3);energy=math.sqrt(mass*mass+sum(x*x for x in momentum))
    return dict(index=index,pdg_id=pdg,native_mass_gev=mass,p4=[energy,*momentum],
        native_final=True,native_status=91,mother_indices=[999],daughter_indices=[],
        source_total_p4=[ECM,0.,0.,0.],source_com_energy_gev=energy,
        mean_proper_lifetime_mm=channel['mean_proper_ctau_mm'])


class KaonSourceTest(unittest.TestCase):
    def test_kl_cpv_source_freezes_coherent_ks_current_and_actual_signal_point(self):
        from dark_photon_weak_meson import kl_cpv_form_factor
        from run_dark_photon_meson_gen import sample_kaon_decay
        card,native=inputs()
        template=json.loads((ROOT/'config/physics/dark_photon_kl_pi0_cpv_reference.json').read_text())
        row=copy.deepcopy(card['parents'][0]);row.update(decay_type='weak-kaon-pion',channel='KL_pi0',
            recoil_pdg_id=111,recoil_mass_gev=.1349768,alpha_em=1/137.035999084)
        weak=copy.deepcopy(template['form_factor_template'])
        weak['kaon_mixing_epsilon']=[.002228*math.cos(math.radians(43.52)),.002228*math.sin(math.radians(43.52))]
        weak['indirect_ks_form_factor_model']=copy.deepcopy(card['parents'][-1]['weak_form_factor_model'])
        row['weak_form_factor_model']=weak
        card=dict(card,parents=[row],channels=['KL_pi0'])
        native=dict(native,parents=[p for p in native['parents'] if p['pdg_id']==130])
        model=resolved_parent_models(card,native,MASS,epsilon=EPSILON)[130]
        fixed=model['weak_form_factor_model']
        self.assertEqual(fixed['physical_epsilon'],EPSILON);self.assertEqual(fixed['signal_mass_gev'],MASS)
        ks=chiral_form_factor(.497611,.1349768,MASS,weak['indirect_ks_form_factor_model'])
        self.assertEqual(fixed['indirect_ks_form_factor_at_signal_mass'],[ks.real,ks.imag])
        form=kl_cpv_form_factor(.497611,.1349768,MASS,fixed)
        expected=decay_width(.497611,.1349768,MASS,.001,row['alpha_em'],form)/1.e-6
        expected*=model['em_mixing_over_epsilon_abs2']*BR/model['total_parent_width_gev']
        self.assertAlmostEqual(parent_branching_coefficient(.497611,MASS,model,BR)/expected,1.)
        sample=sample_kaon_decay(dict(pdg_id=130,p4=(.497611,0.,0.,0.)),model,MASS,10.,random.Random(37))
        self.assertEqual(sample['recoil_pdg_id'],111);self.assertFalse(sample['parent_flight_included'])
        self.assertEqual(sample['dark_photon_polarization'],'longitudinal')
        self.assertEqual(weak['physical_epsilon'],None)
        closed=resolved_parent_models(card,native,.4,epsilon=EPSILON)[130]
        self.assertFalse(closed['kinematically_open'])
        self.assertEqual(parent_branching_coefficient(.497611,.4,closed,BR),0.)
        changed=copy.deepcopy(card);changed['parents'][0]['weak_form_factor_model']['physical_epsilon']=2*EPSILON
        with self.assertRaisesRegex(ValueError,'different physical signal point'):
            resolved_parent_models(changed,native,MASS,epsilon=EPSILON)

    @staticmethod
    def neutral_dipion_source(pdg=130):
        mass=.497611;pion=.1349768;mean=15340. if pdg==130 else 26.844
        current=dict(kind='neutral-kaon-dipion-finite-current',parent_pdg_id=pdg,
            reference_parent_mass_gev=mass,pion_mass_gev=pion,total_width_gev=HBAR_C_GEV_MM/mean,
            alpha_em=1/137.035999084,valid_mass_range_gev=[0.,mass-2*pion],
            reference='synthetic finite-current source test',coefficient_reference='synthetic coefficients, not a production authority',
            coefficients=dict(electric_quadrupole=[1.e-9,0.],charge_radius_pole=[2.e-10,1.e-10],
                electric_contact=[1.e-10,0.],magnetic_quadrupole=[3.e-10,0.]))
        row=dict(pdg_id=pdg,mass_gev=mass,mean_proper_ctau_mm=mean,decay_type='neutral-kaon-dipion',
            channel=('KL' if pdg==130 else 'KS')+'->pi0_pi0_Aprime',parent_mass_model='native-stable-kaon',
            pdg_reference='synthetic native table',lifetime_reference='synthetic lifetime',
            relative_numerical_mass_precision_budget=1.e-5,native_record_mass_support_gev=[mass],
            native_record_mass_support_reference='synthetic initialized physical/flavor union',
            recoil_pdg_ids=[111,111],recoil_masses_gev=[pion,pion],neutral_kaon_dipion_model=current)
        card=dict(mechanism='meson-radiative',parents=[row],channels=[row['channel']])
        native=dict(ecm_gev=ECM,check_event=True,parents=[dict(pdg_id=pdg,mass_min_gev=mass,mass_max_gev=mass,mass_width_gev=0.)],
            native_kaon_mass_authorities=[dict(pdg_id=p,mass_gev=mass,mass_min_gev=mass,mass_max_gev=mass,mass_width_gev=0.) for p in (130,310,311)])
        return card,native

    def test_neutral_dipion_current_rate_and_source_count_have_one_physical_normalization(self):
        from dark_photon_weak_meson import neutral_dipion_branching_coefficient
        for pdg in (130,310):
            card,native=self.neutral_dipion_source(pdg)
            model=resolved_parent_models(card,native,.22,epsilon=EPSILON)[pdg]
            bare=neutral_dipion_branching_coefficient(.497611,.22,model)
            factor=(mixing_parameters(.22,EPSILON).em_coefficient/EPSILON)**2
            expected=bare*factor*BR
            self.assertAlmostEqual(parent_branching_coefficient(.497611,.22,model,BR)/expected,1.)
            self.assertAlmostEqual(coefficient_bound(ECM,BR,{pdg:model},.22)/expected,math.floor(ECM/.497611))
            closed=resolved_parent_models(card,native,.25,epsilon=EPSILON)[pdg]
            self.assertFalse(closed['kinematically_open'])
            self.assertEqual(parent_branching_coefficient(.497611,.25,closed,BR),0.)
            damaged=copy.deepcopy(card);damaged['parents'][0]['neutral_kaon_dipion_model']['total_width_gev']*=2
            with self.assertRaisesRegex(ValueError,'same native pole and lifetime'):
                resolved_parent_models(damaged,native,.22,epsilon=EPSILON)

    def test_neutral_dipion_source_sampler_does_not_redraw_the_kaon_flight(self):
        from run_dark_photon_meson_gen import sample_kaon_decay,kaon_mean_beta_gamma
        card,native=self.neutral_dipion_source()
        model=resolved_parent_models(card,native,.22,epsilon=EPSILON)[130]
        raw=parent_row(130,{130:model}[130])
        parent,=resolve_parent_kinematics([raw],.001,.22,{130:model},BR)
        sample=sample_kaon_decay(parent,model,.22,10.,random.Random(37))
        self.assertEqual(sample['recoil_pdg_ids'],[111,111]);self.assertFalse(sample['parent_flight_included'])
        self.assertEqual(sample['parent_pdg_id'],130)
        self.assertNotIn('parent_proper_decay_length_mm',sample)
        self.assertGreater(kaon_mean_beta_gamma(parent,model,.22),0.)

    def test_neutral_dipion_rate_precision_uses_the_actual_native_energy(self):
        card,native=self.neutral_dipion_source()
        model=resolved_parent_models(card,native,.22,epsilon=EPSILON)[130]
        energy=math.hypot(6800.,model['reference_parent_mass_gev'])
        invariant=math.sqrt(math.fsum([energy**2,-6800.**2]))
        nominal=parent_branching_coefficient(model['reference_parent_mass_gev'],.22,model,BR)
        shifted=parent_branching_coefficient(invariant,.22,model,BR,native_precision=dict(native_energy_gev=energy))
        self.assertLess(abs(shifted/nominal-1.),1.e-3)
        from dark_photon_weak_meson import neutral_dipion_branching_coefficient
        with self.assertRaisesRegex(ValueError,'mass mismatch'):
            neutral_dipion_branching_coefficient(invariant,.22,model)

    def setUp(self):
        self.card,self.native=inputs()
        self.models=resolved_parent_models(self.card,self.native,MASS,epsilon=EPSILON)

    def resolve(self,rows):
        return resolve_parent_kinematics(rows,.001,MASS,self.models,BR)

    def test_exceptional_ks_rate_is_bounded_in_full_source_without_repairing_p4(self):
        from dark_photon_meson_source import FULL_SOURCE_NATIVE_PRECISION_POLICY,deferred_native_precision_measure
        raw=parent_row(310,self.models[310]);before=copy.deepcopy(raw)
        target=raw['native_mass_gev']*(1-4.e-5)
        raw['p4'][0]=math.sqrt(target*target+math.fsum(v*v for v in raw['p4'][1:]))
        raw['source_com_energy_gev']=raw['p4'][0];before=copy.deepcopy(raw)
        with self.assertRaisesRegex(ValueError,'precision.*budget'):self.resolve([raw])
        checked,=resolve_parent_kinematics([raw],.001,MASS,self.models,BR,
            native_precision_policy=FULL_SOURCE_NATIVE_PRECISION_POLICY)
        self.assertEqual(raw,before);self.assertEqual(checked['p4'],raw['p4'])
        reports=checked['native_channel_precision_reports'];self.assertTrue(reports)
        measure,count=deferred_native_precision_measure([checked],BR,FULL_SOURCE_NATIVE_PRECISION_POLICY)
        expected=0.
        for channel,report in zip(exclusive_channels(self.models[310]),reports):
            a=parent_branching_coefficient(raw['native_mass_gev'],MASS,channel,BR)
            b=parent_branching_coefficient(target,MASS,channel,BR)
            expected+=2*max(a,b)
        self.assertAlmostEqual(measure,expected,delta=expected*1.e-9)
        self.assertEqual(count,1)

    def test_native_rates_match_each_independent_qualified_current(self):
        em=mixing_parameters(MASS,EPSILON).em_coefficient
        for parent in self.card['parents']:
            model=next(c for c in exclusive_channels(self.models[parent['pdg_id']]) if c['channel']==parent['channel'])
            M=parent['mass_gev'];width=HBAR_C_GEV_MM/parent['mean_proper_ctau_mm']
            if parent['channel']=='KL_gamma':
                physical=radiative_branching(130,M,MASS,em,parent['br_gamma_gamma'],
                    transition_form_factor(MASS,parent['transition_form_factor']))
            elif parent['channel']=='KS_gamma':
                ref=parent['ks_dispersive_reference'];ref=KSDispersiveReference(ref['path'],ref['sha256'])
                physical=ref.radiative_width(M,MASS,em)/width
            else:
                W=chiral_form_factor(M,parent['recoil_mass_gev'],MASS,parent['weak_form_factor_model'])
                physical=decay_width(M,parent['recoil_mass_gev'],MASS,em,parent['alpha_em'],W)/width
            self.assertAlmostEqual(parent_branching_coefficient(M,MASS,model,BR)/(physical/EPSILON**2*BR),1.,places=12)
        self.assertEqual(len(self.models),4)
        self.assertEqual(len(exclusive_channels(self.models[310])),2)

    def test_exact_narrow_support_gives_analytic_exclusive_sum_envelope(self):
        expected=math.fsum(math.floor(ECM/g['mass_min_gev'])*math.fsum(
            parent_branching_coefficient(c['mass_min_gev'],MASS,c,BR) for c in exclusive_channels(g))
            for g in self.models.values())
        bound=coefficient_bound(ECM,BR,self.models,MASS)
        self.assertAlmostEqual(bound,expected,places=15)
        for pdg,model in self.models.items():
            for count in (1,model.get('native_parent_count_bound',exclusive_channels(model)[0]['native_parent_count_bound'])):
                originals=[parent_row(pdg,model,index=i+1) for i in range(count)]
                if count>1:
                    for row in originals:
                        row['p4']=[row['native_mass_gev'],0.,0.,0.]
                        row['source_com_energy_gev']=row['native_mass_gev']
                rows=self.resolve(originals)
                result=choose_collision(rows,MASS,BR,self.models,bound,random.Random(4))
                self.assertLessEqual(result['coefficient_sum'],bound)
        with self.assertRaises(ValueError):coefficient_bound(ECM+1,BR,self.models,MASS)

    def test_whole_collision_zeros_unit_weights_and_source_sigma_are_preserved(self):
        bound=coefficient_bound(ECM,BR,self.models,MASS)
        zero=choose_collision(self.resolve([]),MASS,BR,self.models,bound,random.Random(1))
        self.assertEqual((zero['parent_count'],zero['coefficient_sum'],zero['accepted']),(0,0.,False))
        rows=self.resolve([parent_row(310,self.models[310])])
        class Draws:
            def __init__(self,selection):self.values=iter([0.,selection])
            def random(self):return next(self.values)
        first=choose_collision(rows,MASS,BR,self.models,bound,Draws(0.))
        last=choose_collision(rows,MASS,BR,self.models,bound,Draws(1-1.e-12))
        self.assertEqual({first['selected_channel'],last['selected_channel']},{'KS_gamma','KS_pi0'})
        self.assertEqual(first['gen_weight'],1.)
        C=first['coefficient_sum']
        self.assertAlmostEqual(source_cross_section_pb(26.,EPSILON,2*C,13),26.e9*EPSILON**2*2*C/13)
        with self.assertRaises(ValueError):choose_collision(rows,MASS,BR,self.models,bound,Draws(0.),native_weight=.5)

    def test_native_mass_authority_is_not_replaced_by_invariant_mass_continuation(self):
        row=parent_row(130,self.models[130]);nominal=row['native_mass_gev']
        row['p4'][0]*=1+1.e-10;row['source_com_energy_gev']=row['p4'][0]
        resolved,=self.resolve([row])
        self.assertEqual(resolved['mass_gev'],nominal)
        self.assertNotEqual(resolved['kinematic_mass_gev'],nominal)
        self.assertNotEqual(resolved['coefficient_relative_precision_shift'],0.)
        expected=parent_branching_coefficient(nominal,MASS,self.models[130],BR)
        result=choose_collision([resolved],MASS,BR,self.models,coefficient_bound(ECM,BR,self.models,MASS),random.Random(1))
        self.assertEqual(result['coefficient_sum'],expected)
        self.assertLess(abs(resolved['coefficient_relative_precision_shift']),1.e-5)

    def test_existing_natural_meson_and_final_kaon_share_one_collision_draw(self):
        self.models[221]=dict(mass_min_gev=.547862,mass_max_gev=.547862,
            br_gamma_gamma=.39,form_factor_abs2=1.,relative_model_uncertainty=.01,
            provenance='synthetic pre-existing pseudoscalar model')
        eta=dict(index=5,pdg_id=221,native_mass_gev=.547862,p4=[.547862,0.,0.,0.])
        kaon=parent_row(130,self.models[130])
        rows=self.resolve([eta,kaon]);bound=coefficient_bound(ECM,BR,self.models,MASS)
        result=choose_collision(rows,MASS,BR,self.models,bound,random.Random(1))
        expected=math.fsum(parent_branching_coefficient(row['mass_gev'],MASS,
            self.models[row['pdg_id']],BR) for row in rows)
        self.assertEqual(result['coefficient_sum'],expected)
        self.assertNotIn('kaon_source_count_authority',rows[0])
        self.assertIn('kaon_source_count_authority',rows[1])

    def test_collision_identity_and_resolved_count_proof_cannot_change_before_draw(self):
        row=parent_row(130,self.models[130]);second=parent_row(321,self.models[321],index=2)
        second['source_total_p4']=[ECM+1,0.,0.,0.]
        with self.assertRaisesRegex(ValueError,'one native beam/collision'):self.resolve([row,second])
        resolved,=self.resolve([row])
        resolved['kaon_source_count_authority']['native_parent_count_bound']+=1
        with self.assertRaisesRegex(ValueError,'count proof'):
            choose_collision([resolved],MASS,BR,self.models,
                coefficient_bound(ECM,BR,self.models,MASS),random.Random(1))

    def test_actual_com_count_authority_is_frame_independent(self):
        for pdg in self.models:
            original=parent_row(pdg,self.models[pdg]);energy=original['p4'][0]
            for beta in (0.,.9,-.9999):
                row=copy.deepcopy(original);gamma=1/math.sqrt((1-beta)*(1+beta))
                E,px,py,pz=row['p4']
                row['p4']=[gamma*(E+beta*pz),px,py,gamma*(pz+beta*E)]
                row['source_total_p4']=[gamma*ECM,0.,0.,gamma*beta*ECM]
                resolved,=self.resolve([row]);proof=resolved['kaon_source_count_authority']
                self.assertEqual(proof['source_com_energy_gev'],energy)
                self.assertEqual(proof['native_parent_count_bound'],math.floor(ECM/row['native_mass_gev']))
                self.assertLessEqual(proof['independent_com_energy_interval_gev'][0],energy)
                self.assertGreaterEqual(proof['independent_com_energy_interval_gev'][1],energy)

    def test_count_authority_rejects_missing_com_wrong_lifetime_and_nonfinal_rows(self):
        row=parent_row(130,self.models[130])
        for key,value in (('native_final',False),('source_com_energy_gev',row['p4'][0]+.1),
                ('mean_proper_lifetime_mm',1.),('daughter_indices',[5]),('source_total_p4',[1.,0.,0.,2.])):
            broken=dict(row);broken[key]=value
            with self.assertRaises(ValueError):self.resolve([broken])
        duplicate=dict(row)
        with self.assertRaises(ValueError):self.resolve([row,duplicate])
        descendant=parent_row(130,self.models[130],index=2);descendant['mother_indices']=[1]
        with self.assertRaises(ValueError):self.resolve([row,descendant])
        with self.assertRaises(ValueError):
            self.resolve([parent_row(130,self.models[130],index=i+1) for i in range(22)])

    def test_boosted_native_mass_drift_uses_independent_com_count_floor(self):
        # Exact charged-kaon inputs from the two October 23 source failures.
        beam=[6800.938269999998,0.,0.,-6799.999935268339]
        ecm=112.97005223140982
        cases=[([2365.5808596790444,.0019168436679661977,-.7321924814721366,
                  -2365.5806937297775],19.658618300369028),
               ([42.5715954313909,.10648654462553739,-.3487119255716363,
                  -42.567184147121],.6191299193342757)]
        native=copy.deepcopy(self.native);native['ecm_gev']=ecm
        for signal_mass in (.7,.9):
            models=resolved_parent_models(self.card,native,signal_mass,epsilon=EPSILON)
            for p4,energy in cases:
                raw=parent_row(321,models[321]);raw.update(p4=p4,
                    source_total_p4=beam,source_com_energy_gev=energy)
                before=copy.deepcopy(raw)
                resolved,=resolve_parent_kinematics([raw],.001,signal_mass,models,BR)
                proof=resolved['kaon_source_count_authority']
                self.assertEqual(proof['native_parent_count_bound'],math.floor(ecm/.493677))
                self.assertGreater(proof['roundoff_mass_floor_gev'],
                    proof['independent_source_ecm_interval_gev'][1]/
                    (proof['native_parent_count_bound']+1))
                self.assertEqual(raw,before);self.assertEqual(resolved['p4'],p4)
                self.assertEqual(resolved['mass_gev'],raw['native_mass_gev'])
                self.assertEqual(resolved['coefficient_relative_precision_shift'],0.)
        # Open-channel rate precision is an independent, unchanged restriction.
        models=resolved_parent_models(self.card,native,MASS,epsilon=EPSILON)
        with self.assertRaisesRegex(ValueError,'precision.*budget'):
            resolve_parent_kinematics([raw],.001,MASS,models,BR)

    def test_com_floor_still_rejects_a_changed_integer_count_limit(self):
        raw=parent_row(321,self.models[321])
        # Timelike, within the fixed Pythia tolerance, but below ECM/(C+1).
        raw['p4']=[.493,0.,0.,0.];raw['source_com_energy_gev']=.493
        native=copy.deepcopy(self.native);native['ecm_gev']=10.36
        raw['source_total_p4']=[10.36,0.,0.,0.]
        models=resolved_parent_models(self.card,native,.7,epsilon=EPSILON)
        with self.assertRaisesRegex(ValueError,'integer source-count envelope'):
            resolve_parent_kinematics([raw],.001,.7,models,BR)

    def test_broad_support_missing_physical_coupling_and_unqualified_families_fail(self):
        native=copy.deepcopy(self.native);native['parents'][0]['mass_width_gev']=1.e-6
        with self.assertRaises(ValueError):resolved_parent_models(self.card,native,MASS,epsilon=EPSILON)
        with self.assertRaises(ValueError):resolved_parent_models(self.card,self.native,MASS)
        card=copy.deepcopy(self.card);card['parents'][0]['decay_type']='weak-kaon-leptonic'
        with self.assertRaisesRegex(ValueError,'K_l2'):resolved_parent_models(card,self.native,MASS,epsilon=EPSILON)
        card=copy.deepcopy(self.card);card['parents'][0]['pdg_id']=311
        with self.assertRaises(ValueError):resolved_parent_models(card,self.native,MASS,epsilon=EPSILON)
        card=copy.deepcopy(self.card);card['parents'][0]['decay_type']='pseudoscalar-photon'
        with self.assertRaisesRegex(ValueError,'qualified kaon current'):
            resolved_parent_models(card,self.native,MASS,epsilon=EPSILON)
        model=copy.deepcopy(self.models);model[130]['decay_type']='pseudoscalar-photon'
        with self.assertRaisesRegex(ValueError,'qualified kaon current'):validate_models(model)
        model=copy.deepcopy(self.models);model[310]['exclusive_channels'][1]['mean_proper_ctau_mm']*=2
        with self.assertRaises(ValueError):validate_models(model)

    def test_closed_points_and_fixed_virtuality_remain_explicit(self):
        closed=resolved_parent_models(self.card,self.native,.7,epsilon=EPSILON)
        self.assertEqual(coefficient_bound(ECM,BR,closed,.7),0.)
        with self.assertRaises(ValueError):parent_branching_coefficient(.497611,.25,self.models[130],BR)

    def test_finite_neutral_support_uses_initialized_flavor_and_physical_masses(self):
        card,native=inputs()
        for parent in card['parents']:
            if parent['pdg_id'] in (130,310):
                parent['mass_gev']=.49761
                parent['native_record_mass_support_gev']=[.49761,.497611]
        for row in native['parents']:
            if row['pdg_id'] in (130,310):row.update(mass_min_gev=.49761,mass_max_gev=.49761)
        for row in native['native_kaon_mass_authorities']:
            if row['pdg_id'] in (130,310):row.update(mass_gev=.49761,mass_min_gev=.49761,mass_max_gev=.49761)
        models=resolved_parent_models(card,native,MASS,epsilon=EPSILON)
        for pdg in (130,310):
            model=models[pdg]
            for mass in (.49761,.497611):
                row=parent_row(pdg,model);row['native_mass_gev']=mass
                row['p4']=[mass,0.,0.,0.];row['source_com_energy_gev']=mass
                resolved,=resolve_parent_kinematics([row],.001,MASS,models,BR)
                self.assertEqual(resolved['mass_gev'],mass)
                self.assertEqual(resolved['p4'],row['p4'])
                for channel in exclusive_channels(model):
                    self.assertEqual(channel['reference_parent_mass_gev'],.49761)
                    self.assertEqual(channel['native_parent_count_bound'],math.floor(ECM/.49761))
                    self.assertEqual(channel['native_mass_rate_coefficients'][1]['branching_over_epsilon_squared'],
                        parent_branching_coefficient(.497611,MASS,channel,1.))
            row['native_mass_gev']=.4976105;row['p4']=[.4976105,0.,0.,0.];row['source_com_energy_gev']=.4976105
            with self.assertRaisesRegex(ValueError,'finite declared'):
                resolve_parent_kinematics([row],.001,MASS,models,BR)
        expected=math.fsum(channel['native_parent_count_bound']*max(
            parent_branching_coefficient(value,MASS,channel,BR) for value in channel['native_record_mass_support_gev'])
            for model in models.values() for channel in exclusive_channels(model))
        self.assertEqual(coefficient_bound(ECM,BR,models,MASS),expected)
        for field in ('native_record_mass_support_gev','native_record_mass_support_reference'):
            broken=copy.deepcopy(card);broken['parents'][0].pop(field)
            with self.assertRaises(ValueError):resolved_parent_models(broken,native,MASS,epsilon=EPSILON)
        broken=copy.deepcopy(native);broken['native_kaon_mass_authorities'][2]['mass_gev']=.497612
        with self.assertRaises(ValueError):resolved_parent_models(card,broken,MASS,epsilon=EPSILON)


if __name__=='__main__':unittest.main()
