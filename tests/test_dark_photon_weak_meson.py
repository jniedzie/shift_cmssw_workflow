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
    decay_width, longitudinal_muon_density, pion_loop_chi, sample_decay)


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


if __name__ == '__main__':
    unittest.main()
