# Code style: AI-code. AI-generated independent vector-meson physics checks.
import math
import json
from pathlib import Path
import random
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from dark_photon_vector_meson import (MUON_MASS_GEV, two_body_momentum,
    radiative_branching,transition_form_factor_abs2,transition_form_factor_variations,
    sample_decay,mean_lab_beta_gamma)

SUPPLEMENT = Path(__file__).resolve().parents[1]/'config/campaigns/dark_photon_vector_radiative_supplement.json'


class VectorMesonTest(unittest.TestCase):
    def test_running_width_pole_matches_published_complex_propagator(self):
        model = json.loads(SUPPLEMENT.read_text())['parents'][0]['transition_form_factor']
        self.assertEqual(model['kind'], 'running-width-vector-pole')
        pole, width, pion = (model[x] for x in ('pole_mass_gev','pole_width_gev','pion_mass_gev'))
        for mass in (0.,.22,2*pion,.4,pole,.8,.884):
            running = (width*pole**2/mass**2*((mass**2-4*pion**2)/(pole**2-4*pion**2))**1.5
                       if mass > 2*pion else 0.)
            expected = pole**4/((pole**2-mass**2)**2+mass**2*running**2)
            self.assertAlmostEqual(transition_form_factor_abs2(mass,model)/expected,1.,places=14)
        self.assertAlmostEqual(transition_form_factor_abs2(pole,model),(pole/width)**2,places=12)
        with self.assertRaisesRegex(ValueError,'domain'):
            transition_form_factor_abs2(.9,model)

    def test_chiral_contact_keeps_published_amplitude_and_normalization(self):
        parents = json.loads(SUPPLEMENT.read_text())['parents']
        for parent in parents:
            model=parent['transition_form_factor']
            if model['kind'] != 'chiral-contact-vector':
                continue
            mass=.22;pole=model['pole_mass_gev'];width=model['pole_width_gev']
            # At this mass the rho's two-pion width is zero; omega constant
            # width enters as sqrt(q2)*Gamma in Ivashyn Eq.(10).
            running=width if model['propagator_width']=='constant' else 0.
            couplings=model['couplings']
            amplitude=math.sqrt(2)*couplings['h_V']-couplings['sigma_V']*couplings['f_V']*mass**2/complex(mass**2-pole**2,mass*running)
            expected=abs(amplitude/(math.sqrt(2)*couplings['h_V']))**2
            self.assertAlmostEqual(transition_form_factor_abs2(mass,model)/expected,1.,places=14)
            self.assertAlmostEqual(transition_form_factor_abs2(0.,model),1.,places=14)
            self.assertFalse(model['model_discrepancy_quantified'])

    def test_strange_finite_pole_is_normalized_without_zero_width_singularity(self):
        parents = json.loads(SUPPLEMENT.read_text())['parents']
        for parent in parents:
            model=parent['transition_form_factor']
            if model['kind'] != 'finite-width-vector-pole':
                continue
            self.assertAlmostEqual(transition_form_factor_abs2(0.,model),1.,places=14)
            self.assertGreater(transition_form_factor_abs2(.3,model),1.)
            self.assertLess(model['valid_mass_range_gev'][1],model['pole_mass_gev'])
            with self.assertRaisesRegex(ValueError,'domain'):
                transition_form_factor_abs2(model['pole_mass_gev'],model)

    def test_measured_bin_validation_is_finite_and_has_explicit_domain(self):
        table=json.loads(SUPPLEMENT.read_text())['parents'][0]['measured_transition_validation']
        expected={.22:1.46,.25:1.22,.3:2.30,.4:3.14,.5:8.49,.7:17.4}
        for mass,value in expected.items():
            self.assertEqual(transition_form_factor_abs2(mass,table),value)
            self.assertTrue(all(value>0 for value in transition_form_factor_variations(mass,table)))
        self.assertEqual(transition_form_factor_abs2(0.,table),1.)
        with self.assertRaisesRegex(ValueError,'domain'):
            transition_form_factor_abs2(.700001,table)

    def test_parameter_box_retains_measured_errors_and_missing_model_uncertainty(self):
        card=json.loads(SUPPLEMENT.read_text())
        for parent in card['parents']:
            model=parent['transition_form_factor']
            central=transition_form_factor_abs2(.22,model)
            variations=transition_form_factor_variations(.22,model)
            self.assertLess(min(variations),central)
            self.assertGreater(max(variations),central)
            self.assertFalse(model['model_discrepancy_quantified'])
        slope=dict(kind='single-pole-slope',slope_gev_minus2=2.223,
            slope_stat_gev_minus2=.026,slope_syst_gev_minus2=.037,
            valid_mass_range_gev=[.211316751,.64],reference='https://arxiv.org/abs/1608.07898')
        values=transition_form_factor_variations(.4,slope)
        error=math.hypot(.026,.037)
        self.assertAlmostEqual(min(values),(1-(2.223-error)*.4**2)**-2)
        self.assertAlmostEqual(max(values),(1-(2.223+error)*.4**2)**-2)

    def test_charge_conjugates_have_equal_rates_and_signed_recoils(self):
        parents=json.loads(SUPPLEMENT.read_text())['parents']
        for pdg in (213,313,323):
            positive=next(row for row in parents if row['pdg_id']==pdg)
            negative=next(row for row in parents if row['pdg_id']==-pdg)
            self.assertEqual(positive['recoil_pdg_id'],-negative['recoil_pdg_id'])
            for key in ('mass_gev','recoil_mass_gev','br_p_gamma','transition_form_factor'):
                self.assertEqual(positive[key],negative[key])
            self.assertEqual(positive['parent_mass_model'],'zero-width-pole')

    def test_malformed_model_or_missing_width_does_not_silently_default(self):
        model=json.loads(SUPPLEMENT.read_text())['parents'][0]['transition_form_factor']
        for changed in (dict(model,pole_width_gev=0.),dict(model,propagator_width='unknown'),
                        dict(model,kaon_mass_gev=None),dict(model,pion_mass_gev=-1.),
                        dict(model,kind='invented')):
            with self.assertRaises(ValueError):
                transition_form_factor_abs2(.3,changed)

    def test_rate_matches_kloe_lambda_ratio_without_factor_two(self):
        for parent,recoil in ((.78266,.1349768),(1.01946,.547862),(.89555,.497611)):
            for mass in (0.,.1,.22):
                lam = parent**4+recoil**4+mass**4-2*(parent**2*recoil**2+parent**2*mass**2+recoil**2*mass**2)
                expected = .002**2*.0833*1.7*(lam/(parent**2-recoil**2)**2)**1.5
                actual = radiative_branching(parent,recoil,mass,.002,.0833,1.7)
                self.assertAlmostEqual(actual/expected,1.,places=13)
        self.assertAlmostEqual(radiative_branching(.78266,.1349768,0.,.002,.0833,1.),.002**2*.0833)
        first=radiative_branching(.78266,.1349768,.3,.001,.0833,1.)
        self.assertAlmostEqual(radiative_branching(.78266,.1349768,.3,.002,.0833,1.)/first,4.)

    def test_endpoint_and_massless_recoil_limits(self):
        endpoint=.78266-.1349768
        for mass in (endpoint,endpoint+.001):
            self.assertEqual(two_body_momentum(.78266,.1349768,mass),0.)
            self.assertEqual(radiative_branching(.78266,.1349768,mass,.001,.0833,1.),0.)
        self.assertGreater(two_body_momentum(.78266,.1349768,endpoint-1.e-12),0.)
        value=radiative_branching(.8,0.,.3,.001,.1,1.)
        self.assertAlmostEqual(value/(.001**2*.1),(1-(.3/.8)**2)**3)

    def test_explicit_na60_form_factor_domain_and_pole_gate(self):
        model=dict(kind='single-pole-slope',slope_gev_minus2=2.223,
                   valid_mass_range_gev=[.211316751,.64],reference='https://arxiv.org/abs/1608.07898')
        self.assertEqual(transition_form_factor_abs2(0.,model),1.)
        self.assertAlmostEqual(transition_form_factor_abs2(.4,model),(1-2.223*.4**2)**-2)
        with self.assertRaisesRegex(ValueError,'domain'):
            transition_form_factor_abs2(.65,model)
        with self.assertRaisesRegex(ValueError,'reference'):
            transition_form_factor_abs2(.3,{})
        with self.assertRaisesRegex(ValueError,'pole'):
            transition_form_factor_abs2(.7,dict(model,valid_mass_range_gev=[0.,.8]))

    def test_independent_lab_conservation_shells_and_spacetime(self):
        rng=random.Random(761016160)
        for parent_mass,recoil_mass,mass in ((.78266,.1349768,.22),(.78266,.1349768,.6),
                 (1.01946,.547862,.4),(.89555,.497611,.3)):
            for p in ((0.,0.,0.),(1.,-2.,-30.)):
                parent=(math.sqrt(parent_mass**2+sum(x*x for x in p)),)+p
                for _ in range(15):
                    row=sample_decay(parent,recoil_mass,mass,120.,rng,polarization='unpolarized')
                    pair=tuple(math.fsum((a,b)) for a,b in zip(row['muon_p4'],row['antimuon_p4']))
                    for actual,expected in zip(pair,row['dark_photon_p4']):
                        self.assertAlmostEqual(actual,expected,places=9)
                    for actual,expected in zip((a+b for a,b in zip(pair,row['recoil_p4'])),parent):
                        self.assertAlmostEqual(actual,expected,places=9)
                    for field,shell in (('dark_photon_p4',mass),('recoil_p4',recoil_mass),
                                       ('muon_p4',MUON_MASS_GEV),('antimuon_p4',MUON_MASS_GEV)):
                        v=row[field];squared=math.fsum((v[0]**2,*(-x*x for x in v[1:])))
                        self.assertAlmostEqual(squared,shell**2,places=8)
                    proper=row['proper_decay_length_mm']
                    for distance,momentum in zip(row['decay_displacement_mm'],row['dark_photon_p4'][1:]):
                        self.assertAlmostEqual(distance,proper*momentum/mass,places=10)
                    self.assertAlmostEqual(row['decay_ct_mm'],proper*row['dark_photon_p4'][0]/mass)
                    self.assertTrue(row['recoil_natural_decay_required'])

    def test_unpolarized_axis_and_massive_muon_helicity_moments(self):
        # Independent inverse boost along the known A' axis, rather than
        # reading back the angles drawn by the sampler.
        count=16000
        for mass in (.212,.4):
            rng=random.Random(761016161)
            axis_first=axis_second=cosine_second=length_sum=0.
            for _ in range(count):
                row=sample_decay((.78266,0.,0.,0.),.1349768,mass,120.,rng,polarization='unpolarized')
                dark=row['dark_photon_p4'];muon=row['muon_p4']
                p=math.sqrt(sum(x*x for x in dark[1:]));axis=tuple(x/p for x in dark[1:])
                projection=sum(a*b for a,b in zip(muon[1:],axis))
                longitudinal=(dark[0]*projection-p*muon[0])/mass
                rest_momentum=math.sqrt(mass**2/4-MUON_MASS_GEV**2)
                cosine=longitudinal/rest_momentum
                axis_first+=axis[2];axis_second+=axis[2]**2;cosine_second+=cosine**2
                length_sum+=row['proper_decay_length_mm']
            r=(MUON_MASS_GEV/mass)**2
            expected=(2*(1+4*r)/3+2*(1-4*r)/5)/(8*(1+2*r)/3)
            self.assertAlmostEqual(axis_first/count,0.,delta=.015)
            self.assertAlmostEqual(axis_second/count,1/3,delta=.012)
            self.assertAlmostEqual(cosine_second/count,expected,delta=.012)
            self.assertAlmostEqual(length_sum/count,120.,delta=4.)

    def test_generic_massive_recoil_boost_mean_matches_angular_integral(self):
        for momentum,mass in ((0.,.3),(.1,.3),(1.,.3),(30.,.6),(6000.,.6),(1.,.64768319)):
            parent=(math.hypot(momentum,.78266),0.,0.,-momentum)
            parent_mass=math.sqrt(parent[0]**2-momentum**2)
            # Independent quadratic energy formulas and midpoint integral.
            energy=(parent_mass**2+mass**2-.1349768**2)/(2*parent_mass)
            p=math.sqrt(max(0.,energy**2-mass**2))
            a=parent[0]/parent_mass*energy;b=momentum/parent_mass*p
            count=20000
            numeric=math.fsum(math.sqrt(max(0.,(a+b*(-1+(i+.5)*2/count))**2-mass**2))
                              for i in range(count))/(count*mass)
            exact=mean_lab_beta_gamma(parent,.1349768,mass,polarization='unpolarized')
            self.assertAlmostEqual(exact/numeric,1.,places=7)

    def test_unknown_polarization_and_closed_dimuons_fail(self):
        for polarization,mass in (('unknown',.3),('transverse',.3),('unpolarized',.2)):
            with self.assertRaises(ValueError):
                sample_decay((.78266,0.,0.,0.),.1349768,mass,120.,random.Random(1),polarization=polarization)


if __name__=='__main__':
    unittest.main()
