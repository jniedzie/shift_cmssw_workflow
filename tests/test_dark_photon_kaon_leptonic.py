# Code style: AI-code. AI-generated Ward, spin, real/virtual and rate controls.
import json
import math
from pathlib import Path
import random
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from dark_photon_meson import boost
from dark_photon_kaon_leptonic import (
    GAMMA, GAMMA5, METRIC, current_operators, decay_width, differential_width,
    distinguishable_dilepton_differential_width, equal_mass_h_over_q2,
    form_factors, polarization_basis, rest_kinematics, spin_density,mean_lab_beta_gamma,
    LeptonicProductionProposal, production_cell_bound, sample_decay,
    sample_vector_muon_direction, slash, IDENTITY)
from dark_photon_meson import MUON_MASS_GEV


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


class LeptonicBoostMeanTest(unittest.TestCase):
    def test_rest_and_boosted_mean_converge_for_both_lepton_spectra(self):
        for lepton,mass in (('muon',.22),('muon',.3),('electron',.22),('electron',.4)):
            card=model(lepton);M=card['parent_mass_gev']
            for momentum in (0.,40.):
                parent=[math.hypot(M,momentum),0.,0.,momentum]
                low=mean_lab_beta_gamma(parent,mass,model=card,quadrature_order=16)
                high=mean_lab_beta_gamma(parent,mass,model=card,quadrature_order=32)
                self.assertGreater(high,0.)
                self.assertLess(abs(low/high-1),1.e-4)

    def test_independent_unit_weight_events_and_isotropic_angle_match_mean(self):
        rng=random.Random(660193)
        for lepton,mass in (('muon',.25),('electron',.3)):
            card=model(lepton);M=card['parent_mass_gev'];momentum=20.
            parent=[math.hypot(M,momentum),0.,0.,momentum]
            predicted=mean_lab_beta_gamma(parent,mass,model=card,quadrature_order=24)
            proposal=LeptonicProductionProposal(card,mass);values=[]
            for _ in range(1200):
                dark=proposal.sample(rng)['point'][3]
                cosine=2*rng.random()-1
                energy=(parent[0]*dark[0]+momentum*dark[3]*cosine)/M
                values.append(math.sqrt((energy-mass)*(energy+mass))/mass)
            mean=math.fsum(values)/len(values)
            error=math.sqrt(math.fsum((value-mean)**2 for value in values)/(len(values)*(len(values)-1)))
            self.assertLess(abs(mean-predicted),4.5*error)

    def test_mass_continuation_is_separate_small_and_fail_closed(self):
        for lepton,mass in (('muon',.22),('muon',.387),('electron',.4)):
            card=model(lepton);M=card['parent_mass_gev'];gap=M-mass-card['lepton_mass_gev']
            for sign in (-1,1):
                delta=sign*.8*min(M*1.e-7,gap*1.e-6)
                actual=M+delta;parent=[math.hypot(actual,2.),0.,0.,2.]
                continued=mean_lab_beta_gamma(parent,mass,model=card,quadrature_order=24)
                exact=mean_lab_beta_gamma(parent,mass,model=dict(card,parent_mass_gev=actual),quadrature_order=24)
                self.assertLess(abs(continued/exact-1),1.e-6)
            with self.assertRaisesRegex(ValueError,'precision gate'):
                mean_lab_beta_gamma([M+2.e-6,0.,0.,0.],mass,model=card)
        with self.assertRaises(ValueError):mean_lab_beta_gamma([1.,0.,0.,2.],.3,model=model())
        with self.assertRaises(ValueError):mean_lab_beta_gamma([.493677,0.,0.,0.],.4,model=model())


class LeptonicEventTest(unittest.TestCase):
    def test_analytic_envelopes_cover_each_cell_and_are_useful(self):
        # This finite control supplements the norm proof; scanned maxima
        # neither define nor enlarge the envelope used by the sampler.
        rng = random.Random(7711)
        for lepton, mass in (('muon', .22), ('muon', .3), ('electron', .22),
                             ('electron', .3), ('electron', .4)):
            card = model(lepton)
            proposal = LeptonicProductionProposal(card, mass, weak_cells=6, angular_cells=4)
            for weak, angle, bound in proposal.cells:
                for fraction in (.00001, .5, .99999, rng.random()):
                    squared = weak[0]+(weak[1]-weak[0])*fraction
                    cosine = angle[0]+(angle[1]-angle[0])*rng.random()
                    target = np.trace(differential_width(card, mass, squared, cosine,
                        epsilon=.001)['total']).real
                    self.assertLess(target, bound)
            rate = decay_width(card, mass, epsilon=.001, quadrature_order=12)['total']
            self.assertGreater(rate/proposal.envelope_integral, .08)

    def test_production_marginals_match_independent_quadrature(self):
        rng = random.Random(93981)
        for lepton, mass, count in (('muon', .25, 1600), ('electron', .3, 1600)):
            card = model(lepton)
            low, high = card['lepton_mass_gev']**2, (card['parent_mass_gev']-mass)**2
            nodes, weights = np.polynomial.legendre.leggauss(20)
            norm, first, second = 0., np.zeros(3), np.zeros(3)
            for node, weight in zip(nodes, weights):
                squared = (high+low)/2+(high-low)*node/2
                for cosine, angular_weight in zip(nodes, weights):
                    density = np.trace(differential_width(card, mass, float(squared),
                        float(cosine), epsilon=.001)['total']).real
                    value = np.array([node, cosine, node*cosine])
                    w = density*weight*angular_weight
                    norm += w; first += w*value; second += w*value**2
            expected, variance = first/norm, second/norm-(first/norm)**2
            proposal = LeptonicProductionProposal(card, mass)
            actual = []
            for _ in range(count):
                row = proposal.sample(rng)
                x = 2*(row['weak_mass_squared']-low)/(high-low)-1
                c = row['lepton_helicity_cosine']
                actual.append((x, c, x*c))
            pulls = (np.mean(actual, axis=0)-expected)/np.sqrt(variance/count)
            self.assertLess(np.max(np.abs(pulls)), 5.)

    def test_vector_decay_moments_retain_coherent_and_longitudinal_terms(self):
        rng = random.Random(5033)
        mass = .4
        beta_squared = 1-4*(MUON_MASS_GEV/mass)**2
        for state in (np.array([0., 0., 1.]), np.array([1., 1., .7j]),
                      np.array([1., .4+.2j, 1.3])):
            rho = np.outer(state, state.conjugate())
            normalized = rho.real/np.trace(rho).real
            expected = (np.eye(3)*(1/3-beta_squared/15)-
                2*beta_squared*normalized/15)/(1-beta_squared/3)
            directions = np.array([sample_vector_muon_direction(rho, mass, rng)[0]
                for _ in range(6500)])
            products = np.einsum('ni,nj->nij', directions, directions)
            error = products.std(axis=0)/math.sqrt(len(products))
            self.assertLess(np.max(np.abs(products.mean(axis=0)-expected)/
                np.maximum(error, 1.e-10)), 5.)

    def test_conditional_angle_matches_independent_massive_muon_dirac_trace(self):
        card = model()
        point = rest_kinematics(card['parent_mass_gev'], card['lepton_mass_gev'], .3, .025, .4)
        rho = spin_density(*point, card, epsilon=.001)['total']
        mass, ml = .3, MUON_MASS_GEV
        beta_squared = 1-4*(ml/mass)**2
        for vector in ((.2, .5, math.sqrt(.71)), (-.8, 0., .6), (0., 1., 0.)):
            n = np.array(vector)
            momentum = mass/2*math.sqrt(beta_squared)*n
            minus, plus = (mass/2, *momentum), (mass/2, *(-momentum))
            leptonic = np.array([[np.trace((slash(minus)+ml*IDENTITY)@GAMMA[i+1]@
                (slash(plus)-ml*IDENTITY)@GAMMA[j+1]) for j in range(3)] for i in range(3)])
            actual = float(np.sum(rho*leptonic).real)/(2*mass**2*np.trace(rho).real)
            expected = 1-beta_squared*float(n@rho.real@n)/np.trace(rho).real
            self.assertAlmostEqual(actual, expected, places=13)

    def test_full_chain_boosted_masses_conservation_and_separate_parent_flight(self):
        for lepton in ('electron', 'muon'):
            card = model(lepton)
            for beta in ((0., 0., 0.), (.1, -.2, .8), (0., 0., -.999999)):
                parent = boost((card['parent_mass_gev'], 0., 0., 0.), beta)
                for parent_id in (321, -321):
                    row = sample_decay(parent, .25, 200., random.Random(541),
                        model=card, parent_pdg_id=parent_id)
                    dark = np.array(row['dark_photon_p4'])
                    primary = np.array(row['charged_lepton_p4'])+row['neutrino_p4']+dark
                    np.testing.assert_allclose(primary, parent, rtol=2.e-10, atol=1.e-13)
                    np.testing.assert_allclose(np.array(row['muon_p4'])+row['antimuon_p4'], dark,
                        rtol=2.e-10, atol=1.e-13)
                    for key, mass in (('dark_photon_p4', .25), ('muon_p4', MUON_MASS_GEV),
                                     ('antimuon_p4', MUON_MASS_GEV),
                                     ('charged_lepton_p4', card['lepton_mass_gev']), ('neutrino_p4', 0.)):
                        p = np.array(row[key])
                        self.assertLess(abs(np.sum(METRIC*p*p)-mass**2),
                            256*math.ulp(max(parent[0]**2, 1.)))
                    self.assertFalse(row['native_parent_flight_attached'])
                    self.assertTrue(row['unconditional_decay'])
                    self.assertEqual(row['recoil_pdg_ids'],
                        [row['charged_lepton_pdg_id'], row['neutrino_pdg_id']])
                    json.dumps(row, allow_nan=False)
                    np.testing.assert_allclose(row['decay_displacement_mm'],
                        row['proper_decay_length_mm']*dark[1:]/.25)
                    self.assertAlmostEqual(row['decay_ct_mm'],
                        row['proper_decay_length_mm']*dark[0]/.25)

    def test_cp_reflection_swaps_charges_without_conjugating_strong_phases(self):
        card = model()
        parent = (card['parent_mass_gev'], 0., 0., 0.)
        for seed in (41, 110, 871):
            positive = sample_decay(parent, .3, 10., random.Random(seed), model=card)
            negative = sample_decay(parent, .3, 10., random.Random(seed), model=card, parent_pdg_id=-321)
            for key in ('dark_photon_p4', 'charged_lepton_p4', 'neutrino_p4'):
                np.testing.assert_allclose(negative[key], np.array(positive[key])*METRIC)
            np.testing.assert_allclose(negative['muon_p4'], np.array(positive['antimuon_p4'])*METRIC)
            np.testing.assert_allclose(negative['antimuon_p4'], np.array(positive['muon_p4'])*METRIC)
            self.assertEqual(positive['charged_lepton_pdg_id'], -13)
            self.assertEqual(negative['charged_lepton_pdg_id'], 13)
            self.assertEqual(negative['neutrino_pdg_id'], -14)
            for part in ('real', 'imaginary'):
                np.testing.assert_array_equal(negative['production_density_matrix_'+part],
                    positive['production_density_matrix_'+part])

    def test_unconditional_rotation_and_exponential_lifetime(self):
        card, rng, count = model('electron'), random.Random(78286), 500
        directions, lengths = [], []
        for _ in range(count):
            row = sample_decay((card['parent_mass_gev'], 0., 0., 0.), .3, 100., rng, model=card)
            spatial = np.array(row['dark_photon_p4'][1:])
            directions.append(spatial/np.linalg.norm(spatial))
            lengths.append(row['proper_decay_length_mm']/100.)
        directions = np.array(directions)
        self.assertLess(np.max(np.abs(directions.mean(axis=0)))*math.sqrt(3*count), 5.)
        self.assertLess(np.max(np.abs((directions**2).mean(axis=0)-1/3))/math.sqrt(4/(45*count)), 5.)
        self.assertLess(abs(np.mean(lengths)-1)*math.sqrt(count), 5.)
        self.assertLess(abs(np.mean(np.array(lengths)**2)-2)*math.sqrt(count/20), 5.)

    def test_invalid_density_parent_and_trial_limits_fail_instead_of_biasing(self):
        card, rng = model(), random.Random(391)
        for density in (np.zeros((3, 3)), np.diag([1., -1., 1.]), np.ones((2, 2)),
                        np.array([[1., 2j, 0.], [-2j, 1., 0.], [0., 0., 1.]])):
            with self.assertRaises(ValueError):
                sample_vector_muon_direction(density, .3, rng)
        with self.assertRaises(ValueError):
            sample_decay((.5, 0., 0., 0.), .25, 10., rng, model=card)
        with self.assertRaises(ValueError):
            sample_decay((card['parent_mass_gev'], 0., 0., 0.), .25, 10., rng,
                model=dict(card, lepton_mass_gev=0.))
        proposal = LeptonicProductionProposal(card, .25)
        with self.assertRaises(ValueError):
            proposal.sample(rng, maximum_trials=0)
        proposal.cells = [(w, c, bound*1.e-12) for w, c, bound in proposal.cells]
        with self.assertRaisesRegex(RuntimeError, 'envelope violated'):
            proposal.sample(rng)


if __name__ == '__main__':
    unittest.main()
