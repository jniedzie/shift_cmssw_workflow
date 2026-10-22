# Code style: AI-code. AI-generated Ward, spin, real/virtual and rate controls.
import json
import math
from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from dark_photon_meson import boost
from dark_photon_kaon_leptonic import (
    GAMMA, GAMMA5, METRIC, current_operators, decay_width, differential_width,
    distinguishable_dilepton_differential_width, equal_mass_h_over_q2,
    form_factors, polarization_basis, rest_kinematics, spin_density)


CARD = Path(__file__).resolve().parents[1]/'config/physics/dark_photon_kl2_chpt_1993.json'


def model(lepton='muon'):
    card = json.loads(CARD.read_text())
    return dict(card['form_factor_model'],
        lepton_mass_gev=card['explicit_conventions'][lepton+'_mass_gev'])


def real_photon_primary_density(card, parent, lepton, dark):
    """Independent published Dalitz polynomials, primary Eqs3.14--3.17."""
    kaon, ml, fk = card['parent_mass_gev'], card['lepton_mass_gev'], card['fk_gev']
    x, y, ratio = 2*dark[0]/kaon, 2*lepton[0]/kaon, (ml/kaon)**2
    v = -1/(8*math.pi**2*card['fpi_gev'])
    a = -4*(card['l9']+card['l10'])/card['fpi_gev']
    coefficient = (card['fermi_constant_gev_minus2']**2*card['v_us']**2*
        card['alpha_em']*kaon**5/(32*math.pi**2))
    propagator = x+y-1-ratio
    ib = ((1-y+ratio)/(x*x*propagator)*
        (x*x+2*(1-x)*(1-ratio)-2*x*ratio*(1-ratio)/propagator))
    sd_plus = propagator*((x+y-1)*(1-x)-ratio)
    sd_minus = (1-y+ratio)*((1-x)*(1-y)+ratio)
    int_plus = (1-y+ratio)/(x*propagator)*((1-x)*(1-x-y)+ratio)
    int_minus = (1-y+ratio)/(x*propagator)*(x*x-(1-x)*(1-x-y)-ratio)
    return dict(IB=4*ratio*(fk/kaon)**2*coefficient*ib,
        SD=coefficient*kaon**2*((v+a)**2*sd_plus+(v-a)**2*sd_minus),
        interference=4*ratio*fk*coefficient*((v+a)*int_plus+(v-a)*int_minus))


class LeptonicCurrentTest(unittest.TestCase):
    def test_clifford_and_chiral_trace_conventions(self):
        for i in range(4):
            for j in range(4):
                expected = 2*METRIC[i]*np.eye(4) if i == j else np.zeros((4, 4))
                np.testing.assert_allclose(GAMMA[i]@GAMMA[j]+GAMMA[j]@GAMMA[i], expected)
            np.testing.assert_allclose(GAMMA5@GAMMA[i]+GAMMA[i]@GAMMA5, 0.)

    def test_ward_identity_of_ib_and_virtual_sd_separately(self):
        for lepton in ('electron', 'muon'):
            card = model(lepton)
            for mass in (.22, .25, .3):
                for cosine in (-.75, .2, .8):
                    point = rest_kinematics(card['parent_mass_gev'], card['lepton_mass_gev'],
                        mass, (card['lepton_mass_gev']**2+(card['parent_mass_gev']-mass)**2)/2,
                        cosine)
                    for name, operator in current_operators(*point, card).items():
                        ward = np.einsum('r,r,rjk->jk', METRIC, point[3], operator)
                        self.assertLess(np.max(np.abs(ward)), 1.e-15)

    def test_real_photon_limit_matches_primary_ib_sd_and_interference(self):
        # This fixes the relative epsilon-tensor/gamma5 signs, not only a
        # total squared rate in which a wrong interference could be hidden.
        for lepton in ('electron', 'muon'):
            card = model(lepton)
            for weak_squared, cosine in ((.025, .4), (.1, -.5), (.19, .2)):
                point = rest_kinematics(card['parent_mass_gev'], card['lepton_mass_gev'],
                    0., weak_squared, cosine)
                density = spin_density(*point, card, epsilon=.001)
                expected = real_photon_primary_density(card, point[0], point[1], point[3])
                for name, target in expected.items():
                    actual = float(np.trace(density[name]).real)*card['parent_mass_gev']/(256*math.pi**3)
                    self.assertLess(abs(actual/(target*1.e-6)-1.), 1.e-6)

    def test_density_is_positive_and_retains_interference_and_longitudinal_state(self):
        card = model()
        point = rest_kinematics(card['parent_mass_gev'], card['lepton_mass_gev'], .25, .035, .3)
        density = spin_density(*point, card, epsilon=.001)
        for name in ('total', 'IB', 'SD'):
            np.testing.assert_allclose(density[name], density[name].conjugate().T, atol=1.e-35)
            self.assertGreater(np.linalg.eigvalsh(density[name]).min(), -1.e-35)
        self.assertGreater(abs(density['total'][0, 1]), 0.)
        self.assertGreater(density['total'][2, 2].real, 0.)
        np.testing.assert_allclose(density['total'],
            density['IB']+density['SD']+density['interference'], atol=1.e-35)

    def test_polarization_density_is_lorentz_invariant_with_transported_basis(self):
        card = model()
        point = rest_kinematics(card['parent_mass_gev'], card['lepton_mass_gev'], .25, .035, -.4)
        states = polarization_basis(point[3])
        original = spin_density(*point, card, epsilon=.001, polarizations=states)['total']
        for beta in ((.2, -.1, .3), (0., 0., -.95)):
            moved = [boost(p, beta) for p in point]
            polarizations = [boost(p, beta) for p in states]
            actual = spin_density(*moved, card, epsilon=.001, polarizations=polarizations)['total']
            np.testing.assert_allclose(actual, original, rtol=1.e-10, atol=1.e-34)

    def test_helicity_suppression_does_not_remove_structure_dependent_electron_rate(self):
        card = model('electron')
        point = rest_kinematics(card['parent_mass_gev'], card['lepton_mass_gev'], .25, .025, .3)
        physical = spin_density(*point, card, epsilon=.001)
        self.assertLess(np.trace(physical['IB']).real/np.trace(physical['SD']).real, 1.e-3)
        massless = dict(card, lepton_mass_gev=0.)
        point = rest_kinematics(card['parent_mass_gev'], 0., .25, .025, .3)
        limit = spin_density(*point, massless, epsilon=.001)
        self.assertEqual(np.trace(limit['IB']).real, 0.)
        self.assertGreater(np.trace(limit['SD']).real, 0.)

    def test_massive_longitudinal_state_decouples_toward_real_photon_limit(self):
        card = model()
        longitudinal = []
        transverse = []
        for mass in (.01, .001, .0001):
            point = rest_kinematics(card['parent_mass_gev'], card['lepton_mass_gev'], mass, .1, .3)
            density = spin_density(*point, card, epsilon=.001)['total']
            longitudinal.append(density[2, 2].real)
            transverse.append(density[0, 0].real+density[1, 1].real)
        for first, second in zip(longitudinal, longitudinal[1:]):
            self.assertLess(abs(first/second/100-1), .02)
        self.assertLess(abs(transverse[-1]/transverse[-2]-1), .001)


class LeptonicFormFactorTest(unittest.TestCase):
    def test_finite_subtraction_optical_cut_and_scale_cancellation(self):
        card = model()
        fpi, l9, scale, pion = [card[key] for key in
            ('fpi_gev', 'l9', 'lec_scale_gev', 'charged_pion_mass_gev')]
        value0 = equal_mass_h_over_q2(0., pion, fpi, l9, scale)
        small = equal_mass_h_over_q2(1.e-10, pion, fpi, l9, scale)
        self.assertLess(abs(small/value0-1), 1.e-8)
        self.assertEqual(equal_mass_h_over_q2(.27**2, pion, fpi, l9, scale).imag, 0.)
        self.assertGreater(equal_mass_h_over_q2(.3**2, pion, fpi, l9, scale).imag, 0.)
        new_scale = 1.0
        running_l9 = l9+.25/(16*math.pi**2)*math.log(scale/new_scale)
        for q2 in (0., .02, .09, .16):
            old = equal_mass_h_over_q2(q2, pion, fpi, l9, scale)
            new = equal_mass_h_over_q2(q2, pion, fpi, running_l9, new_scale)
            self.assertLess(abs(old-new), 1.e-14)

    def test_virtual_only_form_factors_are_not_silently_omitted(self):
        card = model()
        factors = form_factors(.25**2, .035, card)
        self.assertNotEqual(factors['A2'], 0.)
        self.assertNotEqual(factors['A4'], 0.)
        self.assertAlmostEqual(factors['A2']/(card['parent_mass_gev']**2-.035), -factors['A4'])
        self.assertGreater(form_factors(.3**2, .035, card)['kaon_em_form_factor'].imag, 0.)
        ib = form_factors(.25**2, .035, dict(card, order='IB-only-control'))
        self.assertEqual(ib['A2'], 0.)
        self.assertEqual(ib['kaon_em_form_factor'], 1.)

    def test_rejects_wrong_parent_order_domain_and_identical_pair_conversion(self):
        card = model()
        for changes in (dict(parent_pdg_id=130), dict(order='p6'), dict(reference=''),
                        dict(valid_q2_range_gev2=[.09, .1])):
            with self.assertRaises(ValueError):
                form_factors(.25**2, .035, dict(card, **changes))
        with self.assertRaises(ValueError):
            distinguishable_dilepton_differential_width(card, .25, card['lepton_mass_gev'])


class LeptonicRateTest(unittest.TestCase):
    def test_mixing_scaling_rate_components_and_threshold(self):
        card = model()
        original = decay_width(card, .25, epsilon=.001, quadrature_order=12)
        changed = decay_width(card, .25, epsilon=.002, quadrature_order=12)
        self.assertAlmostEqual(changed['total']/original['total'], 4.)
        self.assertAlmostEqual((original['IB']+original['SD']+original['interference'])/original['total'], 1.)
        self.assertAlmostEqual((original['transverse']+original['longitudinal'])/original['total'], 1.)
        self.assertEqual(decay_width(card, .4, epsilon=.001)['total'], 0.)
        self.assertGreater(decay_width(model('electron'), .4, epsilon=.001, quadrature_order=12)['total'], 0.)

    def test_quadrature_stability_for_both_open_lepton_channels(self):
        for lepton in ('electron', 'muon'):
            for mass in (.22, .3):
                low = decay_width(model(lepton), mass, epsilon=.001, quadrature_order=12)['total']
                high = decay_width(model(lepton), mass, epsilon=.001, quadrature_order=24)['total']
                self.assertLess(abs(low/high-1.), .0002)

    def test_independent_virtual_prediction_with_the_published_pair_mass_cut(self):
        card = model()
        authority = json.loads(CARD.read_text())
        low, high = .14**2, (card['parent_mass_gev']-card['lepton_mass_gev'])**2
        nodes, weights = np.polynomial.legendre.leggauss(20)
        integral = 0.
        # Split at the physical pion cut, rather than asking a polynomial
        # quadrature to cross its nonanalytic onset in one large interval.
        edges = [low, (2*card['charged_pion_mass_gev'])**2, high]
        for start, end in zip(edges, edges[1:]):
            for node, weight in zip(nodes, weights):
                q2 = (start+end)/2+(end-start)*node/2
                integral += weight*(end-start)/2*distinguishable_dilepton_differential_width(
                    card, math.sqrt(q2), authority['explicit_conventions']['electron_mass_gev'],
                    quadrature_order=12)
        conversion = (authority['explicit_conventions']['charged_kaon_lifetime_seconds']/
            authority['explicit_conventions']['hbar_gev_seconds'])
        expected = authority['independent_controls']['old_nonidentical_virtual_prediction']['born_branching_full']
        self.assertLess(abs(integral*conversion/expected-1.), .007)


if __name__ == '__main__':
    unittest.main()
