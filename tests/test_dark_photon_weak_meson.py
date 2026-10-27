# Code style: AI-code. AI-generated weak-current, spectral and helicity controls.
import math
import json
from pathlib import Path
import random
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from dark_photon_meson import MUON_MASS_GEV, boost
from dark_photon_vector_meson import two_body_momentum
from dark_photon_weak_meson import (
    amplitude, born_dilepton_differential_width, chiral_form_factor,
    decay_width, longitudinal_muon_density, pion_loop_chi, sample_decay,
    _dipion_angular_polynomial, _dipion_integral, _dipion_t_density,
    neutral_dipion_current, neutral_dipion_rest_kinematics,
    neutral_dipion_polarization_density, neutral_dipion_differential_width,
    neutral_dipion_decay_width, neutral_dipion_branching_coefficient,
    sample_neutral_dipion_decay, mean_neutral_dipion_lab_beta_gamma,
    inami_lim_functions, kl_cpv_form_factor, kl_cpv_form_factor_components)
from dark_photon_model import DEFAULT_EW_INPUTS, mixing_parameters
from dark_photon_dipion_meson import _gauss_legendre
from dark_photon_double_radiative import polarizations


KAON, PION = .493677, .13957039
ALPHA, FERMI = 1/137.035999084, 1.1663787e-5


def model():
    return dict(kind='chpt-p6-kaon-em-vector', parent_pdg_id=321,
        a=-.575, b=-.722, alpha_pipi=-20.40e-8, beta_pipi=-2.05e-8,
        fermi_constant_gev_minus2=FERMI, pion_vector_rv_squared=2.5,
        loop_pion_mass_gev=PION,
        valid_mass_range_gev=[0., KAON-PION],
        reference='https://arxiv.org/html/2209.05076v3#S4',
        loop_reference='https://arxiv.org/pdf/hep-ph/9808289#page=5')


def rest_state(mass):
    momentum = two_body_momentum(KAON, PION, mass)
    parent = (KAON, 0., 0., 0.)
    recoil = (math.hypot(momentum, PION), 0., 0., -momentum)
    q = (math.hypot(momentum, mass), 0., 0., momentum)
    longitudinal = (momentum/mass, 0., 0., q[0]/mass) if mass else None
    return parent, recoil, q, longitudinal


class WeakCurrentTest(unittest.TestCase):
    def test_ward_identity_lorentz_invariance_and_longitudinal_polarization(self):
        for mass in (.22, .25, .3, KAON-PION-1.e-6):
            parent, recoil, q, longitudinal = rest_state(mass)
            coefficient = chiral_form_factor(KAON, PION, mass, model())
            original = amplitude(parent, recoil, longitudinal, .001, ALPHA, coefficient)
            self.assertNotEqual(original, 0.)
            for transverse in ((0., 1., 0., 0.), (0., 0., 1., 0.)):
                self.assertEqual(amplitude(parent, recoil, transverse, .001, ALPHA, coefficient), 0.)
            for beta in ((0., 0., 0.), (.2, -.3, .4), (0., 0., -.95)):
                moved = amplitude(boost(parent, beta), boost(recoil, beta),
                    boost(longitudinal, beta), .001, ALPHA, coefficient)
                ward = amplitude(boost(parent, beta), boost(recoil, beta),
                    boost(q, beta), .001, ALPHA, coefficient)
                self.assertLess(abs(moved-original)/abs(original), 2.e-10)
                self.assertLess(abs(ward)/abs(original), 1.e-12)

    def test_two_body_width_closes_against_independent_polarization_amplitude(self):
        for mass in (.22, .25, .3, KAON-PION-1.e-6):
            parent, recoil, _, longitudinal = rest_state(mass)
            form = chiral_form_factor(KAON, PION, mass, model())
            matrix = amplitude(parent, recoil, longitudinal, .001, ALPHA, form)
            direct = two_body_momentum(KAON, PION, mass)*abs(matrix)**2/(8*math.pi*KAON**2)
            calculated = decay_width(KAON, PION, mass, .001, ALPHA, form)
            self.assertAlmostEqual(direct/calculated, 1., places=10)

    def test_born_spectral_conversion_keeps_phase_space_and_massive_leptons(self):
        for mass in (.212, .22, .25, .3, .35):
            form = chiral_form_factor(KAON, PION, mass, model())
            differential = born_dilepton_differential_width(KAON, PION, mass, MUON_MASS_GEV, ALPHA, form)
            width = decay_width(KAON, PION, mass, .001, ALPHA, form)
            ratio = MUON_MASS_GEV**2/mass**2
            expected = .001**2*3*math.pi*mass**2/(ALPHA*math.sqrt(1-4*ratio)*(1+2*ratio))
            self.assertAlmostEqual(width/differential/expected, 1., places=12)

    def test_real_photon_transition_vanishes_and_closed_channels_have_zero_rate(self):
        parent, recoil, _, _ = rest_state(0.)
        for polarization in ((0., 1., 0., 0.), (0., 0., 1., 0.)):
            self.assertEqual(amplitude(parent, recoil, polarization, .001, ALPHA, 1.), 0.)
        self.assertEqual(decay_width(KAON, PION, 0., .001, ALPHA, 1.), 0.)
        for mass in (KAON-PION, .4):
            self.assertEqual(decay_width(KAON, PION, mass, .001, ALPHA, 1.), 0.)
        self.assertEqual(born_dilepton_differential_width(KAON, PION, 2*MUON_MASS_GEV,
            MUON_MASS_GEV, ALPHA, 1.), 0.)

    def test_mixing_and_complex_form_factor_normalizations_are_not_photon_br_ratios(self):
        first = decay_width(KAON, PION, .25, .001, ALPHA, 1+2j)
        self.assertEqual(decay_width(KAON, PION, .25, .002, ALPHA, 1+2j)/first, 4.)
        self.assertEqual(decay_width(KAON, PION, .25, .001, ALPHA, 2+4j)/first, 4.)
        self.assertAlmostEqual(decay_width(KAON, PION, .25, .001, ALPHA, 1.)/first, .2)


class ChiralFormFactorTest(unittest.TestCase):
    def test_pion_loop_is_subtracted_continuous_and_has_the_pwave_cut(self):
        self.assertEqual(pion_loop_chi(0.), 0.)
        self.assertAlmostEqual(pion_loop_chi(1.e-8).real/(1.e-8/60), 1., places=8)
        self.assertAlmostEqual(pion_loop_chi(4.).real, 1/9)
        for x in (4.0001, 4.5, 6., 100.):
            self.assertAlmostEqual(pion_loop_chi(x).imag/(math.pi/6*(1-4/x)**1.5), 1., places=12)
        self.assertLess(abs(pion_loop_chi(4.-1.e-9)-pion_loop_chi(4.+1.e-9)), 1.e-8)

    def test_fit_and_loop_inputs_are_independent_explicit_parameters(self):
        card = model()
        mass = .25
        base = chiral_form_factor(KAON, PION, mass, card)
        shifted = dict(card, a=card['a']+.1, b=card['b']-.2)
        difference = chiral_form_factor(KAON, PION, mass, shifted)-base
        self.assertAlmostEqual(difference.real, FERMI*KAON**2*(.1-.2*(mass/KAON)**2))
        no_loop = dict(card, alpha_pipi=0., beta_pipi=0.)
        self.assertAlmostEqual(chiral_form_factor(KAON, PION, 0., card), FERMI*KAON**2*card['a'])
        self.assertEqual(chiral_form_factor(KAON, PION, .3, no_loop).imag, 0.)
        self.assertLess(chiral_form_factor(KAON, PION, .3, card).imag, 0.)

    def test_neutral_recoil_does_not_move_the_charged_pion_loop_threshold(self):
        neutral_pion, neutral_kaon = .1349768, .497611
        # Synthetic coefficients check the shared current machinery only;
        # this is not a measured KS form-factor model or a production card.
        card = dict(model(), parent_pdg_id=310,
            valid_mass_range_gev=[0., neutral_kaon-neutral_pion])
        self.assertEqual(chiral_form_factor(neutral_kaon, neutral_pion, .275, card).imag, 0.)
        self.assertLess(chiral_form_factor(neutral_kaon, neutral_pion, .28, card).imag, 0.)

    def test_old_published_integrated_branching_polynomial_is_reproduced(self):
        # Independent rounded coefficient table, hep-ph/9808289 Eq.(12),
        # with that paper's historical K->3pi inputs and lifetime convention.
        card = dict(model(), alpha_pipi=-20.6e-8, beta_pipi=-2.8e-8)
        a, b = card['a'], card['b']
        expected = (1.13-19.2*a-6.32*b+116*a*a+67.3*a*b+10.3*b*b)*1.e-9
        low, high, count = (2*MUON_MASS_GEV)**2, (KAON-PION)**2, 20000
        integral = math.fsum(born_dilepton_differential_width(KAON, PION,
            math.sqrt(low+(i+.5)*(high-low)/count), MUON_MASS_GEV, ALPHA,
            chiral_form_factor(KAON, PION, math.sqrt(low+(i+.5)*(high-low)/count), card))
            for i in range(count))*(high-low)/count
        branching = integral*1.238e-8/6.582119569e-25
        self.assertLess(abs(branching/expected-1.), .001)

    def test_maintained_fit_authority_keeps_radiative_and_covariance_scope(self):
        path = Path(__file__).resolve().parents[1]/'config/physics/dark_photon_kpi_vector_na62_2022.json'
        authority = json.loads(path.read_text())
        constants = authority['explicit_conventions']
        for mass in (.22, .25, .3):
            value = chiral_form_factor(constants['parent_mass_gev'],
                constants['charged_pion_mass_gev'], mass, authority['form_factor_model'])
            self.assertGreater(abs(value), 0.)
        self.assertIsNone(authority['fit_uncertainties']['total_a_b_covariance'])
        self.assertFalse(authority['physical_production_ready'])

    def test_ks_benchmarks_close_both_published_dilepton_polynomials(self):
        path = Path(__file__).resolve().parents[1]/'config/physics/dark_photon_ks_pi0_vector_na48_2004.json'
        authority = json.loads(path.read_text())
        constants = authority['explicit_conventions']
        parent, recoil = constants['parent_mass_gev'], constants['neutral_pion_mass_gev']
        for scenario in authority['normalization_scenarios'][:2]:
            card = dict(authority['form_factor_model'], a=scenario['a'], b=scenario['b'],
                valid_mass_range_gev=[0., parent-recoil])
            a, b = card['a'], card['b']
            for lepton in ('electron', 'muon'):
                ml = constants[lepton+'_mass_gev']
                low, high, count = (2*ml)**2, (parent-recoil)**2, 20000
                step = (high-low)/count
                integral = math.fsum(born_dilepton_differential_width(parent, recoil,
                    math.sqrt(low+(i+.5)*step), ml, ALPHA,
                    chiral_form_factor(parent, recoil, math.sqrt(low+(i+.5)*step), card))
                    for i in range(count))*step
                branching = integral*constants['kaon_lifetime_seconds']/constants['hbar_gev_seconds']
                expected = ((.01-.76*a-.21*b+46.5*a*a+12.9*a*b+1.44*b*b)*1.e-10
                    if lepton == 'electron' else
                    (.07-4.52*a-1.50*b+98.7*a*a+57.7*a*b+8.95*b*b)*1.e-11)
                # The primary coefficients are rounded and use historical
                # constants. Their two lepton channels close within 0.5%.
                self.assertLess(abs(branching/expected-1.), .006)

    def test_ks_sign_ambiguity_retains_the_fixed_loop_interference(self):
        path = Path(__file__).resolve().parents[1]/'config/physics/dark_photon_ks_pi0_vector_na48_2004.json'
        authority = json.loads(path.read_text())
        card = authority['form_factor_model']
        parent, recoil, mass = .497611, .1349768, .3
        positive = chiral_form_factor(parent, recoil, mass, card)
        negative = chiral_form_factor(parent, recoil, mass,
            dict(card, a=-card['a'], b=-card['b']))
        self.assertNotEqual(abs(positive)**2, abs(negative)**2)
        self.assertEqual(positive.imag, negative.imag)
        self.assertLess(positive.imag, 0.)
        # The fit errors belong to an assumed VMD shape. They do not become
        # measured slope errors or a full confidence band for this model.
        self.assertIsNone(authority['unconstrained_shape_diagnostics']['a_b_covariances'])
        self.assertEqual(len(authority['normalization_scenarios']), 4)
        self.assertFalse(authority['physical_production_ready'])

    def test_neutral_weak_current_has_the_same_longitudinal_width(self):
        parent, recoil_mass, mass = .497611, .1349768, .3
        momentum = two_body_momentum(parent, recoil_mass, mass)
        recoil = (math.hypot(momentum, recoil_mass), 0., 0., -momentum)
        dark = (math.hypot(momentum, mass), 0., 0., momentum)
        longitudinal = (momentum/mass, 0., 0., dark[0]/mass)
        for coefficient in (1., 1.+2j):
            matrix = amplitude((parent, 0., 0., 0.), recoil, longitudinal,
                .001, ALPHA, coefficient)
            direct = momentum*abs(matrix)**2/(8*math.pi*parent**2)
            calculated = decay_width(parent, recoil_mass, mass, .001, ALPHA, coefficient)
            self.assertAlmostEqual(direct/calculated, 1., places=12)
            self.assertEqual(amplitude((parent, 0., 0., 0.), recoil, (0., 1., 0., 0.),
                .001, ALPHA, coefficient), 0.)

    def test_missing_unqualified_or_out_of_domain_inputs_are_rejected(self):
        invalid = [dict(parent_pdg_id=130), dict(reference=''), dict(loop_reference=''),
            dict(a=None), dict(pion_vector_rv_squared=0.),
            dict(valid_mass_range_gev=[.26, .35])]
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                chiral_form_factor(KAON, PION, .25, dict(model(), **changes))


class LongitudinalDecayTest(unittest.TestCase):
    def test_massive_muon_density_normalization_and_limits(self):
        for mass in (.212, .22, .3):
            values = [longitudinal_muon_density(c, mass) for c in (-1., 0., 1.)]
            self.assertAlmostEqual((values[0]+4*values[1]+values[2])/3, 1.)
        self.assertAlmostEqual(longitudinal_muon_density(0., 1., 0.), .75)
        self.assertEqual(longitudinal_muon_density(1., 1., 0.), 0.)
        self.assertAlmostEqual(longitudinal_muon_density(.8, 2*MUON_MASS_GEV*(1+1.e-10)), .5, places=9)

    def test_full_chain_conserves_four_momentum_and_relative_flight(self):
        rng = random.Random(1761622201)
        for momentum in (0., 3., 1000.):
            parent = (math.hypot(momentum, KAON), 0., 0., -momentum)
            for mass in (.22, .25, .3):
                for _ in range(10):
                    row = sample_decay(parent, PION, mass, 3., rng)
                    pair = [a+b for a, b in zip(row['muon_p4'], row['antimuon_p4'])]
                    total = [a+b for a, b in zip(pair, row['recoil_p4'])]
                    for actual, expected in zip(total, parent):
                        self.assertLess(abs(actual-expected), 2.e-6)
                    for actual, expected in zip(pair, row['dark_photon_p4']):
                        self.assertLess(abs(actual-expected), 2.e-6)
                    self.assertEqual(row['dark_photon_polarization'], 'longitudinal')
                    self.assertTrue(row['unconditional_decay'])
                    self.assertAlmostEqual(row['decay_ct_mm'],
                        row['proper_decay_length_mm']*row['dark_photon_p4'][0]/mass)

    def test_generated_helicity_moment_matches_longitudinal_current(self):
        rng = random.Random(1761622202)
        mass, count = .3, 6000
        rows = [sample_decay((KAON, 0., 0., 0.), PION, mass, 2., rng) for _ in range(count)]
        angles = []
        for row in rows:
            q = row['dark_photon_p4']
            muon = boost(row['muon_p4'], tuple(-value/q[0] for value in q[1:]))
            axis = [value/math.sqrt(sum(p*p for p in q[1:])) for value in q[1:]]
            angle = sum(a*b for a, b in zip(muon[1:], axis))/math.sqrt(sum(p*p for p in muon[1:]))
            self.assertAlmostEqual(angle, row['helicity_cosine'])
            angles.append(angle)
        beta_squared = 1-4*(MUON_MASS_GEV/mass)**2
        expected = (1/3-beta_squared/5)/(1-beta_squared/3)
        self.assertLess(abs(math.fsum(c*c for c in angles)/count-expected), .012)
        self.assertLess(abs(math.fsum(row['proper_decay_length_mm'] for row in rows)/count/2.-1.), .04)


def neutral_dipion_model(**changes):
    parent, pion, fs = .497611, .1349768, 3.92e-7
    radius = -.07*5.067730716**2
    pole = -fs*radius*parent**3/(3*math.sqrt(2)*(parent**2-pion**2))
    result = dict(kind='neutral-kaon-dipion-finite-current', parent_pdg_id=130,
        reference_parent_mass_gev=parent, pion_mass_gev=pion, alpha_em=ALPHA,
        total_width_gev=1.286575365324472e-17, valid_mass_range_gev=[0., parent-2*pion],
        reference='https://arxiv.org/html/2602.15117v1#A1',
        coefficient_reference='https://arxiv.org/html/2602.15117v1#A3',
        coefficients=dict(electric_quadrupole=[fs*1.28*parent, 0.],
            charge_radius_pole=[pole, 0.],
            electric_contact=[pole*.5/math.log(parent**2/pion**2), 0.],
            magnetic_quadrupole=[0., 0.]))
    result.update(changes)
    return result


def only_dipion_component(name, coefficient=1.):
    card = neutral_dipion_model()
    card['coefficients'] = {key: [coefficient.real, coefficient.imag] if key == name else [0., 0.]
                            for key in card['coefficients']}
    return card


class NeutralKaonDipionTest(unittest.TestCase):
    def test_known_kaon_mixing_acts_on_amplitudes_without_lifetime_rescaling(self):
        reference=Path(__file__).resolve().parents[1]/'config/physics/dark_photon_ks_neutral_dipion_1993.json'
        card=json.loads(reference.read_text());eps=complex(*card['mixing_input']['epsilon_k_complex'])
        N=math.sqrt(1+abs(eps)**2)
        for name,base,other in [('KS',card['model_template'],card['cp_odd_electric_reference_model']),
                                ('KL',card['cp_odd_electric_reference_model'],card['model_template'])]:
            mixed=card['mixed_model_templates'][name]
            for key in mixed['coefficients']:
                expected=(complex(*base['coefficients'][key])+eps*complex(*other['coefficients'][key]))/N
                self.assertEqual(complex(*mixed['coefficients'][key]),expected)
            induced=json.loads(json.dumps(mixed))
            induced['coefficients']={key:[(eps*complex(*other['coefficients'][key])/N).real,
                                          (eps*complex(*other['coefficients'][key])/N).imag]
                                     for key in mixed['coefficients']}
            inherited=neutral_dipion_decay_width(induced,.22,epsilon=.001,quadrature_order=32)
            opposite=neutral_dipion_decay_width(other,.22,epsilon=.001,quadrature_order=32)
            self.assertLess(abs(inherited/opposite/(abs(eps)**2/N**2)-1.),1.e-11)
            self.assertEqual(mixed['total_width_gev'],base['total_width_gev'])

    def test_mixed_electric_magnetic_rate_is_incoherent_but_spin_density_is_not(self):
        reference=Path(__file__).resolve().parents[1]/'config/physics/dark_photon_ks_neutral_dipion_1993.json'
        card=json.loads(reference.read_text());eps=complex(*card['mixing_input']['epsilon_k_complex'])
        N2=1+abs(eps)**2
        for name,base,other in [('KS',card['model_template'],card['cp_odd_electric_reference_model']),
                                ('KL',card['cp_odd_electric_reference_model'],card['model_template'])]:
            mixed=card['mixed_model_templates'][name]
            own_rho=neutral_dipion_polarization_density(.075,.43,.22,base)
            other_rho=neutral_dipion_polarization_density(.075,.43,.22,other)
            full=neutral_dipion_polarization_density(.075,.43,.22,mixed)
            incoherent=[[ (own_rho[i][j]+abs(eps)**2*other_rho[i][j])/N2
                           for j in range(3)] for i in range(3)]
            trace=sum(full[i][i].real for i in range(3))
            expected=sum(incoherent[i][i].real for i in range(3))
            self.assertLess(abs(trace/expected-1.),1.e-12)
            self.assertGreater(max(abs(full[i][j]-incoherent[i][j]) for i in range(3) for j in range(3) if i!=j),0.)
            # An unmeasured M2 strong phase changes correlated off-diagonal
            # density, even though it cannot change the summed E/M width.
            shifted=json.loads(json.dumps(mixed))
            magnetic=complex(*shifted['coefficients']['magnetic_quadrupole'])*1.j
            shifted['coefficients']['magnetic_quadrupole']=[magnetic.real,magnetic.imag]
            changed=neutral_dipion_polarization_density(.075,.43,.22,shifted)
            self.assertLess(abs(sum(changed[i][i].real for i in range(3))/trace-1.),1.e-12)
            self.assertGreater(max(abs(full[i][j]-changed[i][j]) for i in range(3) for j in range(3) if i!=j),0.)

    def test_original_heiliger_sehgal_m2_photon_spectrum_matches_absolute_tensor(self):
        reference=Path(__file__).resolve().parents[1]/'config/physics/dark_photon_ks_neutral_dipion_1993.json'
        card=json.loads(reference.read_text())
        model=card['model_template']
        M,pion=model['reference_parent_mass_gev'],model['pion_mass_gev']
        coefficient=model['coefficients']['magnetic_quadrupole'][0]
        g_over_scale_squared=math.sqrt(4*math.pi*model['alpha_em'])*coefficient/M
        # Primary1993 Eq3, including its absolute3840*pi^3 and full
        # neutral phase space. No normalization is fitted to the kernel.
        for photon_energy in (.01,.04,.09,.14,.17):
            invariant=M*M-2*M*photon_energy
            beta=math.sqrt(1-4*pion*pion/invariant)
            published=(g_over_scale_squared**2/(3840*math.pi**3*M**3)
                       *(1-2*photon_energy/M)*beta**5*photon_energy**5)
            numerical=2*M*sum(weight*neutral_dipion_differential_width(
                invariant,cosine,0.,model) for cosine,weight in _gauss_legendre(16))
            self.assertLess(abs(numerical/published-1.),5.e-12)

    def test_primary_m1_reference_cut_and_physical_pion_masses_fix_m2_benchmark(self):
        reference=Path(__file__).resolve().parents[1]/'config/physics/dark_photon_ks_neutral_dipion_1993.json'
        card=json.loads(reference.read_text());inputs=card['normalization_inputs']
        M,pion=inputs['reference_parent_mass_gev'],inputs['charged_pion_mass_gev']
        maximum=(M*M-4*pion*pion)/(2*M)
        minimum=inputs['charged_reference_photon_energy_min_gev']
        # Independently integrate the primary Eq4 M1 spin sum. Charged
        # pions are distinguishable; the neutral M2 rate has one1/2!.
        def density(energy):
            invariant=M*M-2*M*energy
            return energy**3*invariant*(1-4*pion*pion/invariant)**1.5/(384*math.pi**3*M**7)
        unit=_dipion_integral(density,minimum,maximum,64)
        expected_g=math.sqrt(inputs['charged_kl_m1_branching_fraction']
            *inputs['hbar_c_gev_mm']/inputs['native_kl_ctau_mm']/unit)
        self.assertLess(abs(expected_g/card['independent_normalization']['gM1_gev']-1.),1.e-8)
        coefficient=expected_g*M/(math.sqrt(4*math.pi*inputs['alpha_em'])*inputs['interaction_scale_gev']**2)
        self.assertLess(abs(coefficient/card['model_template']['coefficients']['magnetic_quadrupole'][0]-1.),1.e-8)
        self.assertEqual(inputs['neutral_photon_energy_min_gev'],0.)
        self.assertAlmostEqual(card['independent_normalization']['neutral_fraction_below_20MeV'],3.6130257974905064e-5)
        self.assertTrue(card['model_template']['primary_amplitude_verified'])
        self.assertFalse(card['model_template']['physical_omission_bound_available'])

    def test_m2_massive_spectrum_and_quoted_photon_scenario_are_independent_checks(self):
        reference=Path(__file__).resolve().parents[1]/'config/physics/dark_photon_ks_neutral_dipion_1993.json'
        card=json.loads(reference.read_text());model=card['model_template']
        M,pion=model['reference_parent_mass_gev'],model['pion_mass_gev']
        coefficient=model['coefficients']['magnetic_quadrupole'][0]
        # The conserved M2 tensor stays transverse for a massive vector.
        # Its exact angular integral is e²*c²*k^5*s*beta_pi^5/(7680*pi³*M^8).
        for mass in (0.,.212,.22,.225,.227):
            low,high=4*pion*pion,(M-mass)**2
            def reference_density(angle):
                invariant=low+(high-low)*math.sin(math.pi*angle/2)**2
                momentum=math.sqrt((M*M-(math.sqrt(invariant)+mass)**2)
                    *(M*M-(math.sqrt(invariant)-mass)**2))/(2*M)
                beta=math.sqrt(1-4*pion*pion/invariant)
                jacobian=(high-low)*math.pi/2*math.sin(math.pi*angle)
                return (4*math.pi*model['alpha_em']*coefficient**2*momentum**5
                    *invariant*beta**5/(7680*math.pi**3*M**8)*jacobian)
            independent=_dipion_integral(reference_density,0.,1.,64)
            actual=neutral_dipion_decay_width(model,mass,epsilon=.001,quadrature_order=64)/1.e-6
            self.assertLess(abs(actual/independent-1.),2.e-10)
        photon=neutral_dipion_decay_width(model,0.,epsilon=.001,quadrature_order=64)/1.e-6
        self.assertLess(abs(photon/model['total_width_gev']/card['independent_normalization']['predicted_neutral_photon_branching_fraction']-1.),2.e-12)
        model=json.loads(json.dumps(model))
        model['coefficients']['magnetic_quadrupole'][0]=card['quoted_rate_normalization_scenario']['cM2']
        photon=neutral_dipion_decay_width(model,0.,epsilon=.001,quadrature_order=64)/1.e-6
        self.assertLess(abs(photon/model['total_width_gev']/1.7e-11-1.),2.e-12)

    def test_all_four_currents_are_ward_transverse_and_bose_even_in_moving_frames(self):
        card, mass = neutral_dipion_model(), .22
        parent, q, first, second = neutral_dipion_rest_kinematics(.497611, .1349768, mass, .075, .43)
        inner = lambda a, b: a[0]*b[0]-sum(x*y for x, y in zip(a[1:], b[1:]))
        beta = (.2, -.31, .65)
        for name in card['coefficients']:
            card = only_dipion_component(name, .5+.7j)
            current = neutral_dipion_current(parent, first, second, model=card)
            swapped = neutral_dipion_current(parent, second, first, model=card)
            scale = max(abs(x) for x in current)
            self.assertGreater(scale, 0.)
            self.assertLess(abs(inner(q, current)), 2.e-14*scale)
            for a, b in zip(current, swapped):
                self.assertAlmostEqual(abs(a-b)/scale, 0., places=13)
            moved = neutral_dipion_current(boost(parent, beta), boost(first, beta), boost(second, beta), model=card)
            real = boost(tuple(x.real for x in current), beta)
            imaginary = boost(tuple(x.imag for x in current), beta)
            for a, b in zip(moved, (complex(r, i) for r, i in zip(real, imaginary))):
                self.assertLess(abs(a-b)/scale, 2.e-12)

    def test_spin_density_is_hermitian_positive_and_retains_coherent_interference(self):
        card = neutral_dipion_model()
        card['coefficients']['magnetic_quadrupole'] = [1.e-6, 2.e-6]
        rho = neutral_dipion_polarization_density(.075, .43, .22, card)
        for i in range(3):
            self.assertGreaterEqual(rho[i][i].real, 0.)
            for j in range(3):
                self.assertEqual(rho[i][j], rho[j][i].conjugate())
        self.assertTrue(any(abs(rho[i][j]) > 0 for i in range(3) for j in range(3) if i != j))
        for probe in ((1, 2j, .3-1j), (1, 1, 1), (0, 1j, -2)):
            value = sum(probe[i].conjugate()*rho[i][j]*probe[j] for i in range(3) for j in range(3))
            self.assertGreaterEqual(value.real, -1.e-30)
            self.assertLess(abs(value.imag), 1.e-27)

    def test_magnetic_quadrupole_is_transverse_and_its_bose_node_is_preserved(self):
        card = only_dipion_component('magnetic_quadrupole')
        for cosine in (-1., -.4, 0., .4, 1.):
            rho = neutral_dipion_polarization_density(.075, cosine, .22, card)
            self.assertEqual(rho[2][2], 0.)
            trace = sum(rho[i][i].real for i in range(3))
            if cosine in (-1., 0., 1.):
                self.assertEqual(trace, 0.)
            else:
                self.assertGreater(trace, 0.)

    def test_angular_polynomial_is_exact_for_arbitrary_complex_finite_coefficients(self):
        card = neutral_dipion_model()
        for index, name in enumerate(card['coefficients']):
            card['coefficients'][name] = [1+index, -.4*index]
        for invariant in (.073, .075, .0768):
            a, b, c = _dipion_angular_polynomial(invariant, .22, card)
            for cosine in (-.83, -.24, .17, .68):
                actual = neutral_dipion_differential_width(invariant, cosine, .22, card)
                self.assertLess(abs((a+b*cosine**2+c*cosine**4)/actual-1.), 2.e-12)

    def test_absolute_phase_space_has_one_identical_pion_factor_and_epsilon_squared(self):
        card = only_dipion_component('electric_contact', 2.+3j)
        invariant, cosine, mass = .075, .17, .22
        parent, dark, first, second = neutral_dipion_rest_kinematics(.497611, .1349768, mass, invariant, cosine)
        # Independent H_contact^2 in P rest: sum|e H.eps|²=e² |c|² q² k²/M⁴.
        momentum = dark[3]
        matrix = 4*math.pi*ALPHA*13*mass**2*momentum**2/.497611**4
        phase = momentum*math.sqrt(invariant/4-.1349768**2)/(256*math.pi**3*.497611**2*math.sqrt(invariant))
        self.assertAlmostEqual(neutral_dipion_differential_width(invariant, cosine, mass, card)/(matrix*phase), 1., places=12)
        width = neutral_dipion_decay_width(card, mass, epsilon=.001)
        self.assertEqual(neutral_dipion_decay_width(card, mass, epsilon=.002)/width, 4.)

    def test_rate_integral_converges_and_matches_sampler_normalization(self):
        card = neutral_dipion_model()
        widths = [neutral_dipion_decay_width(card, .22, epsilon=.001, quadrature_order=n) for n in (16, 32, 64)]
        for value in widths:
            self.assertLess(abs(value/widths[-1]-1.), 1.e-10)
        source = neutral_dipion_branching_coefficient(.497611, .22, card)
        self.assertLess(abs(source*card['total_width_gev']*1.e-6/widths[-1]-1.), 1.e-10)

    def test_corrected_sehgal_continuum_benchmarks_without_rate_rescaling(self):
        # Eq63 and Appendix A.3 historical rounded totals. Current constants
        # and exact hbar*c conversion differ by ~2%; no factor is fitted here.
        card, electron = neutral_dipion_model(), .00051099895
        low = math.log((2*electron)**2)
        high = math.log((.497611-2*.1349768)**2)
        for sign, expected in ((1., 1.96e-10), (-1., 2.68e-10)):
            card['coefficients']['electric_quadrupole'][0] = sign*3.92e-7*1.28*.497611
            total = 0.
            for node, weight in _gauss_legendre(48):
                q2 = math.exp((high+low)/2+(high-low)*node/2)
                ratio = electron**2/q2
                spectral = ALPHA/(3*math.pi)*math.sqrt(1-4*ratio)*(1+2*ratio)
                width = neutral_dipion_decay_width(card, math.sqrt(q2), epsilon=.001, quadrature_order=24)/1.e-6
                total += weight*(high-low)/2*spectral*width/card['total_width_gev']
            self.assertLess(abs(total/expected-1.), .03)

    def test_sampler_conserves_shells_and_both_relative_lifetime_quantities(self):
        card, rng = neutral_dipion_model(), random.Random(1761623101)
        for momentum in (0., 3., 300.):
            parent = (math.hypot(momentum, .497611), 0., 0., -momentum)
            for _ in range(4):
                row = sample_neutral_dipion_decay(parent, .22, 7., rng, model=card)
                vectors = [row['muon_p4'], row['antimuon_p4'], *row['recoil_p4s']]
                for actual, expected in zip(map(sum, zip(*vectors)), parent):
                    self.assertLess(abs(actual-expected), 2.e-8)
                for value, mass in zip(vectors, (MUON_MASS_GEV, MUON_MASS_GEV, .1349768, .1349768)):
                    squared = math.fsum((value[0]**2, *(-x*x for x in value[1:])))
                    self.assertLess(abs(squared-mass**2), 1.e-8)
                self.assertEqual(row['recoil_pdg_ids'], [111, 111])
                self.assertTrue(row['unconditional_decay'])
                self.assertAlmostEqual(row['decay_ct_mm'], row['proper_decay_length_mm']*row['dark_photon_p4'][0]/.22)
                for offset, component in zip(row['decay_displacement_mm'], row['dark_photon_p4'][1:]):
                    self.assertAlmostEqual(offset, row['proper_decay_length_mm']*component/.22)

    def test_magnetic_sampler_and_mean_boost_preserve_the_conditional_angular_law(self):
        card = only_dipion_component('magnetic_quadrupole', 1.e-6)
        rng, count = random.Random(1761623102), 240
        rows = [sample_neutral_dipion_decay((.497611, 0., 0., 0.), .22, 1., rng, model=card) for _ in range(count)]
        # M2 unpolarized angular spectrum is c²(1-c²), with <c²>=3/7.
        self.assertLess(abs(sum(row['pion_helicity_cosine']**2 for row in rows)/count-3/7), .055)
        observed = sum(math.sqrt(sum(x*x for x in row['dark_photon_p4'][1:]))/.22 for row in rows)/count
        expected = mean_neutral_dipion_lab_beta_gamma((.497611, 0., 0., 0.), .22, card)
        self.assertLess(abs(observed/expected-1.), .05)

    def test_native_pole_continuation_is_explicit_and_conserves_actual_mass(self):
        card = neutral_dipion_model()
        with self.assertRaises(ValueError):
            neutral_dipion_branching_coefficient(.497614, .22, card)
        card['pole_mass_numerical_tolerance_gev'] = 3.1e-6
        row = sample_neutral_dipion_decay((.497614, 0., 0., 0.), .22, 1., random.Random(81), model=card)
        self.assertAlmostEqual(sum(v[0] for v in [row['dark_photon_p4'], *row['recoil_p4s']]), .497614)
        self.assertEqual(row['event_parent_mass_gev'], .497614)
        self.assertEqual(row['reference_parent_mass_gev'], .497611)

    def test_checked_source_frame_precision_repairs_failed_native_row_without_p4_changes(self):
        directory=Path(__file__).resolve().parent
        fixture=json.loads((directory/'fixtures/kaon_native_source_frame_20261027.json').read_text())
        card=json.loads((directory.parent/'config/physics/dark_photon_ks_neutral_dipion_1993.json').read_text())
        model=card['mixed_model_templates']['KL']
        model['pole_mass_numerical_tolerance_gev']=fixture['weak_radiative_model']['pole_mass_numerical_tolerance_gev']
        seen_failure=False
        for example in fixture['examples']:
            context=example['context'];record=context['native_source_record']
            parent=tuple(record['p4'])
            original=json.dumps(context,sort_keys=True)
            squared=math.fsum((parent[0]**2,*(-x*x for x in parent[1:])))
            actual_mass=math.sqrt(squared)
            if not example['old_local_energy_guard_passed']:
                seen_failure=True
                with self.assertRaisesRegex(ValueError,'mass mismatch'):
                    neutral_dipion_branching_coefficient(actual_mass,.22,model,native_energy_gev=parent[0])
            coefficient=neutral_dipion_branching_coefficient(actual_mass,.22,model,
                native_energy_gev=parent[0],native_context=context)
            nominal=neutral_dipion_branching_coefficient(record['native_mass_gev'],.22,model,
                native_energy_gev=parent[0],native_context=context)
            self.assertLess(abs(coefficient/nominal-1.),1.e-7)
            self.assertGreater(mean_neutral_dipion_lab_beta_gamma(parent,.22,model,native_context=context),0.)
            row=sample_neutral_dipion_decay(parent,.22,7.,random.Random(38174+record['index']),
                                           model=model,native_context=context)
            total=tuple(math.fsum(components) for components in zip(row['dark_photon_p4'],*row['recoil_p4s']))
            for component,expected in zip(total,parent):
                self.assertLess(abs(component-expected),3.e-8)
            self.assertEqual(json.dumps(context,sort_keys=True),original)
        self.assertTrue(seen_failure)

    def test_source_context_cannot_widen_reference_table_support_or_borrow_momentum(self):
        directory=Path(__file__).resolve().parent
        fixture=json.loads((directory/'fixtures/kaon_native_source_frame_20261027.json').read_text())
        context=next(row['context'] for row in fixture['examples'] if not row['old_local_energy_guard_passed'])
        parent=context['native_source_record']['p4']
        mass=math.sqrt(math.fsum((parent[0]**2,*(-x*x for x in parent[1:]))))
        card=json.loads((directory.parent/'config/physics/dark_photon_ks_neutral_dipion_1993.json').read_text())
        model=card['mixed_model_templates']['KL']
        # The exact native table mass is1keV above this reference. A source
        # boost allowance must never supply a missing physical-table allowance.
        with self.assertRaisesRegex(ValueError,'reference support'):
            neutral_dipion_branching_coefficient(mass,.22,model,
                native_energy_gev=parent[0],native_context=context)
        model['pole_mass_numerical_tolerance_gev']=fixture['weak_radiative_model']['pole_mass_numerical_tolerance_gev']
        changed=list(parent);changed[1]=math.nextafter(changed[1],math.inf)
        with self.assertRaisesRegex(ValueError,'momentum'):
            sample_neutral_dipion_decay(changed,.22,1.,random.Random(1),model=model,native_context=context)
        with self.assertRaisesRegex(ValueError,'final-kaon'):
            neutral_dipion_branching_coefficient(mass,.22,dict(model,parent_pdg_id=310),native_context=context)

    def test_unattributed_models_missing_coefficients_and_closed_points_fail(self):
        for changes in (dict(reference=''), dict(coefficient_reference=''), dict(parent_pdg_id=321),
                        dict(coefficients={}), dict(valid_mass_range_gev=[0., .3])):
            with self.assertRaises(ValueError):
                neutral_dipion_decay_width(neutral_dipion_model(**changes), .22, epsilon=.001)
        with self.assertRaises(ValueError):
            neutral_dipion_decay_width(neutral_dipion_model(), .25, epsilon=.001)


def cpv_model(mass=.25, epsilon=.001):
    directory = Path(__file__).resolve().parents[1]/'config/physics'
    authority = json.loads((directory/'dark_photon_kl_pi0_cpv_reference.json').read_text())
    card = authority['form_factor_template']
    mixing = authority['kaon_mixing_input']
    phase = math.radians(mixing['phase_degrees'])
    card.update(physical_epsilon=epsilon, signal_mass_gev=mass,
        kaon_mixing_epsilon=[mixing['absolute_epsilon_k']*math.cos(phase), mixing['absolute_epsilon_k']*math.sin(phase)])
    ks_model = json.loads((directory/'dark_photon_ks_pi0_vector_na48_2004.json').read_text())['form_factor_model']
    ks_current = chiral_form_factor(.497611, .1349768, mass, ks_model)
    card['indirect_ks_form_factor_at_signal_mass'] = [ks_current.real, ks_current.imag]
    return card


class MatchedKLCurrentTest(unittest.TestCase):
    def test_inami_lim_functions_have_smooth_removable_poles_and_independent_Z0(self):
        self.assertEqual(inami_lim_functions(1.)['C0'], 3/16)
        self.assertEqual(inami_lim_functions(1.)['D0'], 11/216)
        self.assertEqual(inami_lim_functions(1.)['E0'], 43/72)
        for x in (1.-1.e-8, 1.+1.e-8):
            for name, value in inami_lim_functions(1.).items():
                self.assertLess(abs(inami_lim_functions(x)[name]-value), 1.e-8)
        for x in (.2, 2., 4.3, 10.):
            independent = (-math.log(x)/9+(18*x**4-163*x**3+259*x*x-108*x)/(144*(x-1)**3)
                +(32*x**4-38*x**3-15*x*x+18*x)/(72*(x-1)**4)*math.log(x))
            self.assertLess(abs(inami_lim_functions(x)['Z0']-independent), 2.e-13)

    def test_full_mixing_and_matched_photon_neutral_current_coefficients_are_retained(self):
        mass, epsilon = .3, .02
        card = cpv_model(mass, epsilon)
        values = kl_cpv_form_factor_components(.497611, .1349768, mass, card)
        mix = mixing_parameters(mass, epsilon)
        ew = DEFAULT_EW_INPUTS
        ratio = mix.neutral_current_coefficient/mix.em_coefficient*card['w_mass_gev']**2/(ew.sin_theta_w*ew.cos_theta_w*mass**2)
        self.assertEqual(values['neutral_current_ratio'], ratio)
        self.assertGreater(abs(ratio-1.), 1.e-5)
        functions = inami_lim_functions((card['running_top_mass_gev']/card['w_mass_gev'])**2)
        expected = 4*functions['Z0']-card['qcd_p0']-card['qcd_pe']*functions['E0']+4*(ratio-1)*functions['C0']
        self.assertAlmostEqual(values['matched_short_distance_coefficient'], expected)
        # Replacing this with complete y7V would insert the independent
        # semileptonic Z/box term, which has no emitted on-shell A' here.
        x = (card['running_top_mass_gev']/card['w_mass_gev'])**2
        y0 = x/8*((x-4)/(x-1)+3*x/(x-1)**2*math.log(x))
        self.assertGreater(abs(expected-(expected-y0/ew.sin2_theta_w)), 1.)

    def test_physical_MW_scheme_is_explicit_and_not_hidden_in_mixing(self):
        card = cpv_model(epsilon=1.e-6)
        nominal = kl_cpv_form_factor_components(.497611, .1349768, .25, card)
        moved = kl_cpv_form_factor_components(.497611, .1349768, .25, dict(card, w_mass_gev=80.385))
        self.assertLess(abs(nominal['neutral_current_ratio']-1.), 1.e-5)
        self.assertAlmostEqual(moved['neutral_current_ratio']/nominal['neutral_current_ratio'],
                               (80.385/card['w_mass_gev'])**2, places=12)
        self.assertGreater(abs(moved['neutral_current_ratio']-nominal['neutral_current_ratio']), .002)

    def test_absolute_direct_normalization_closes_against_the_matched_quark_current(self):
        mass, epsilon, parent_mass, pion = .25, .001, .497611, .1349768
        card = cpv_model(mass, epsilon)
        card.update(kaon_mixing_epsilon=[0., 0.], indirect_ks_form_factor_at_signal_mass=[0., 0.])
        values = kl_cpv_form_factor_components(parent_mass, pion, mass, card)
        coefficient = (mixing_parameters(mass, epsilon).em_coefficient*FERMI*math.sqrt(4*math.pi*ALPHA)
            /(8*math.sqrt(2)*math.pi**2)*mass**2*card['imag_lambda_t']
            *values['semileptonic_form_factor']*values['matched_short_distance_coefficient'])
        momentum = two_body_momentum(parent_mass, pion, mass)
        independent = momentum**3*abs(coefficient)**2/(2*math.pi*mass**2)
        calculated = decay_width(parent_mass, pion, mass, mixing_parameters(mass, epsilon).em_coefficient,
            ALPHA, values['total'])
        self.assertAlmostEqual(calculated/independent, 1., places=12)

    def test_indirect_current_is_coherent_and_can_cancel_or_enhance_direct_current(self):
        card, parent, pion, mass = cpv_model(), .497611, .1349768, .25
        values = kl_cpv_form_factor_components(parent, pion, mass, card)
        self.assertLess(abs(values['total']-values['direct']-values['indirect']), 1.e-23)
        self.assertNotEqual(abs(values['total'])**2, abs(values['direct'])**2+abs(values['indirect'])**2)
        mixing = complex(*card['kaon_mixing_epsilon'])
        cancelling_ks = -values['direct']*math.sqrt(1+abs(mixing)**2)/mixing
        card['indirect_ks_form_factor_at_signal_mass'] = [cancelling_ks.real, cancelling_ks.imag]
        cancelled = kl_cpv_form_factor(parent, pion, mass, card)
        self.assertLess(abs(cancelled), 1.e-23)
        card['indirect_ks_form_factor_at_signal_mass'] = [-cancelling_ks.real, -cancelling_ks.imag]
        enhanced = kl_cpv_form_factor(parent, pion, mass, card)
        self.assertAlmostEqual(abs(enhanced/values['direct']), 2.)

    def test_common_CKM_uncertainty_scales_the_direct_amplitude_once(self):
        card = cpv_model()
        first = kl_cpv_form_factor_components(.497611, .1349768, .25, card)
        second = kl_cpv_form_factor_components(.497611, .1349768, .25,
            dict(card, imag_lambda_t=2*card['imag_lambda_t']))
        self.assertEqual(second['direct'], 2*first['direct'])
        self.assertEqual(second['indirect'], first['indirect'])
        self.assertAlmostEqual(abs(second['direct'])**2/abs(first['direct'])**2, 4.)

    def test_current_remains_longitudinal_and_uses_the_same_polarized_decay(self):
        parent, pion, mass = .497611, .1349768, .25
        form = kl_cpv_form_factor(parent, pion, mass, cpv_model(mass))
        momentum = two_body_momentum(parent, pion, mass)
        recoil = (math.hypot(momentum, pion), 0., 0., -momentum)
        for state in ((0., 1., 0., 0.), (0., 0., 1., 0.)):
            self.assertEqual(amplitude((parent, 0., 0., 0.), recoil, state, .001, ALPHA, form), 0.)
        row = sample_decay((parent, 0., 0., 0.), pion, mass, 2., random.Random(44))
        self.assertEqual(row['dark_photon_polarization'], 'longitudinal')

    def test_unresolved_or_wrong_mass_CP_phase_and_inclusive_models_are_rejected(self):
        card = cpv_model()
        for changes in (dict(physical_epsilon=None), dict(signal_mass_gev=.3),
                        dict(indirect_ks_form_factor_at_signal_mass=None), dict(kaon_mixing_epsilon=None),
                        dict(parent_pdg_id=310), dict(qcd_scheme='inclusive-y7V'), dict(qcd_reference='')):
            with self.assertRaises(ValueError):
                kl_cpv_form_factor(.497611, .1349768, .25, dict(card, **changes))
        path = Path(__file__).resolve().parents[1]/'config/physics/dark_photon_kl_pi0_cpv_reference.json'
        authority = json.loads(path.read_text())
        self.assertFalse(authority['physical_production_ready'])
        self.assertGreater(authority['ckm_input']['uncertainty_plus'], 0.)
        self.assertIn('Nonlocal', authority['unqualified_terms'][0])


if __name__ == '__main__':
    unittest.main()
