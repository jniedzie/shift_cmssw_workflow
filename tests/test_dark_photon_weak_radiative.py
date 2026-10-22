# Code style: AI-code. AI-generated weak-radiative current and spectral controls.
import json
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from dark_photon_dipion_meson import _gauss_legendre
from dark_photon_meson import boost
from dark_photon_weak_radiative import (
    _g_prime, amplitude, decay_width, loop_functions, reduced_loop_current, scalar_loop)


CARD = Path(__file__).resolve().parents[1]/'config/physics/dark_photon_kl_pi0_gamma_chpt_1997.json'


def model():
    return json.loads(CARD.read_text())['form_factor_model']


class WeakRadiativeLoopTest(unittest.TestCase):
    def test_closed_form_matches_original_feynman_integral(self):
        quadrature = _gauss_legendre(96)
        mass = .13957039
        for q2, s in ((0., .03), (.02, .05), (.0484, .07), (.0625, .075)):
            integral = 0.
            for n, w in quadrature:
                x = (n+1)/2
                numerator = mass**2-x*(1-x)*q2
                for m, v in quadrature:
                    y = (m+1)*(1-x)/2
                    denominator = x*y*(s-q2)+x*(1-x)*q2-mass**2
                    integral += w*v*(1-x)/4*numerator/denominator
            self.assertLess(abs(scalar_loop(s, q2, mass)-(1+2*integral)), 1.e-12)

    def test_soft_limit_uses_exact_derivative_integral_without_second_cancellation(self):
        mass = .13957039
        for q2 in (0., .02, .05, .10):
            self.assertEqual(scalar_loop(q2, q2, mass), 0.)
            difference = 1.e-10
            ratio = scalar_loop(q2+difference, q2, mass)/difference
            self.assertLess(abs(ratio/(_g_prime(q2, mass)/2)-1), 2.e-7)

    def test_complex_pion_cut_and_threshold_continuity(self):
        mass = .13957039
        self.assertEqual(loop_functions(.07, mass)[0].imag, 0.)
        for part in loop_functions(.09, mass):
            self.assertLess(part.imag, 0.)
        threshold = 4*mass**2
        for first, second in zip(loop_functions(threshold*(1-1.e-12), mass),
                                 loop_functions(threshold*(1+1.e-12), mass)):
            self.assertLess(abs(first-second), 1.e-5)
        with self.assertRaises(ValueError):
            scalar_loop(.02, .03, mass)


class WeakRadiativeCurrentTest(unittest.TestCase):
    def test_both_ward_identities_and_transported_polarization_amplitudes(self):
        mass, invariant = .22, .1
        root = math.sqrt(invariant)
        momentum = (invariant-mass**2)/(2*root)
        dark = (math.hypot(momentum, mass), 0., 0., momentum)
        photon = (momentum, 0., 0., -momentum)
        first, second = (0., 1., 0., 0.), (0., 0., 1., 0.)
        original = amplitude(dark, photon, first, first, mass, .001, model())
        self.assertNotEqual(original, 0.)
        for beta in ((0., 0., 0.), (.3, -.2, .1), (0., 0., -.95)):
            moved = [boost(p, beta) for p in (dark, photon, first, second)]
            actual = amplitude(moved[0], moved[1], moved[2], moved[2], mass, .001, model())
            self.assertLess(abs(actual/original-1), 1.e-11)
            self.assertLess(abs(amplitude(moved[0], moved[1], moved[0], moved[2],
                mass, .001, model())/original), 1.e-12)
            self.assertLess(abs(amplitude(moved[0], moved[1], moved[2], moved[1],
                mass, .001, model())/original), 1.e-12)

    def test_all_physical_polarizations_close_against_scalar_width_numerator(self):
        card = model()
        mass, invariant = .25, .11
        root = math.sqrt(invariant)
        momentum = (invariant-mass**2)/(2*root)
        dark = (math.hypot(momentum, mass), 0., 0., momentum)
        photon = (momentum, 0., 0., -momentum)
        transverse = [(0., 1., 0., 0.), (0., 0., 1., 0.)]
        longitudinal = (momentum/mass, 0., 0., dark[0]/mass)
        total = sum(abs(amplitude(dark, photon, p, g, mass, .001, card))**2
            for p in transverse+[longitudinal] for g in transverse)
        coefficient = .001*2*(4*math.pi*card['alpha_em'])*card['g8_gev_minus2']/(16*math.pi**2)
        expected = 2*coefficient**2*abs(reduced_loop_current(invariant, mass, card))**2
        self.assertAlmostEqual(total/expected, 1.)
        self.assertEqual(amplitude(dark, photon, longitudinal, transverse[0], mass, .001, card), 0.)


class WeakRadiativeRateTest(unittest.TestCase):
    def test_mixing_scaling_phase_integration_and_explicit_scope(self):
        card = model()
        for mass in (.22, .25, .3):
            low = decay_width(card, mass, epsilon=.001, quadrature_order=32)
            high = decay_width(card, mass, epsilon=.001, quadrature_order=96)
            self.assertLess(abs(low/high-1), .0001)
            self.assertAlmostEqual(decay_width(card, mass, epsilon=.002)/
                decay_width(card, mass, epsilon=.001), 4.)
        self.assertEqual(decay_width(card, card['parent_mass_gev']-card['recoil_pion_mass_gev'],
            epsilon=.001), 0.)
        authority = json.loads(CARD.read_text())
        self.assertFalse(authority['physical_production_ready'])
        for changes in (dict(parent_pdg_id=310), dict(reference=''),
                        dict(valid_vector_mass_range_gev=[.29, .32])):
            with self.assertRaises(ValueError):
                decay_width(dict(card, **changes), .25, epsilon=.001)

    def test_published_virtual_electron_and_muon_controls_without_rescaling(self):
        authority = json.loads(CARD.read_text())
        card, constants = authority['form_factor_model'], authority['explicit_conventions']
        conversion = constants['kaon_lifetime_seconds']/constants['hbar_gev_seconds']
        for lepton in ('electron', 'muon'):
            ml = constants[lepton+'_mass_gev']
            lo, hi = math.log((2*ml)**2), math.log((card['parent_mass_gev']-card['recoil_pion_mass_gev'])**2)
            integral = 0.
            for node, weight in _gauss_legendre(64):
                q2 = math.exp((lo+hi)/2+(hi-lo)*node/2)
                ratio = ml**2/q2
                spectral = card['alpha_em']/(3*math.pi)*math.sqrt(1-4*ratio)*(1+2*ratio)
                integral += weight*(hi-lo)/2*spectral*decay_width(card, math.sqrt(q2),
                    epsilon=.001, quadrature_order=64)/1.e-6
            branching = integral*conversion
            key = 'leading_ee_gamma_branching_rounded' if lepton == 'electron' else 'leading_mumu_gamma_branching_rounded'
            # The explicit historical G8 convention and current mass inputs
            # agree with the primary rounded control rates to about 8%.
            # This is a leading model check, not precision normalization.
            self.assertLess(abs(branching/authority['primary_controls'][key]-1), .10)


if __name__ == '__main__':
    unittest.main()
