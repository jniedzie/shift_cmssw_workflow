# Code style: AI-code. AI-generated charged-rho tensor and sampling checks.
import copy
import math
import json
from pathlib import Path
import random
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import dark_photon_charged_rho_radiative as rho
from dark_photon_meson import boost

MODEL = dict(kind='gauged-proca-pion-g2-pole', alpha=1/137.035999084,
    charged_pion_mass_gev=.13957039, neutral_pion_mass_gev=.1349768,
    reference_parent_mass_gev=.77511, valid_mass_range_gev=[0., .50056281],
    parent_polarization='unpolarized', parent_mass_continuation='zero-width-pole',
    pole_mass_numerical_tolerance_gev=1.e-6, electromagnetic_gyromagnetic_ratio=2,
    normalization=dict(channel='charged-pion-neutral-pion', branching_fraction=1.,
        reference='PDG2025 dominant dipion; explicit BR=1 pole approximation'),
    reference='https://arxiv.org/abs/hep-ph/9707202 Eq.2 soft benchmark; explicit Proca Lagrangian',
    assumption='g_EM=2 local pole model; timelike hadronic form factors absent',
    signal_rate_validated=False)


def parent_rate_context(mass, charge=1):
    # Resolve the existing measured two-body sibling from the maintained card;
    # this fixture never imports a production or dated validation directory.
    from dark_photon_meson_source import parent_form_factor_abs2
    path = Path(__file__).resolve().parents[1]/'config/campaigns/dark_photon_meson_extended_model.json'
    card = json.loads(path.read_text())
    physical = next(row for row in card['parents'] if row['pdg_id'] == charge*213
                    and row['decay_type'] == 'vector-pseudoscalar')
    companion = dict(physical, mass_min_gev=.77511, mass_max_gev=.77511,
        form_factor_abs2=parent_form_factor_abs2(mass, physical),
        provenance=dict(pdg=physical['pdg_reference'],
                        transition=physical['transition_form_factor']['reference']))
    charged = dict(decay_type='charged-vector-dipion', channel='charged-dipion',
        parent_mass_model='zero-width-pole', parent_polarization='unpolarized',
        mass_min_gev=.77511, mass_max_gev=.77511,
        charged_pion_mass_gev=MODEL['charged_pion_mass_gev'],
        neutral_pion_mass_gev=MODEL['neutral_pion_mass_gev'],
        recoil_pdg_ids=[charge*211, 111], br_pipi=1., charged_rho_radiative_model=MODEL)
    return dict(parent_pdg_id=charge*213,
                exclusive_channels=[dict(row, pdg_id=charge*213) for row in (companion, charged)])


def minkowski(first, second):
    return math.fsum((first[0]*second[0], *(-a*b for a, b in zip(first[1:], second[1:]))))


def dot(first, second):
    return math.fsum(a*b for a, b in zip(first, second))


def momenta(s, cosine, mass, model=MODEL):
    """Independent two-step phase-space construction, including unequal masses."""
    parent_mass = model['reference_parent_mass_gev']
    charged_mass, neutral_mass = model['charged_pion_mass_gev'], model['neutral_pion_mass_gev']
    momentum = math.sqrt(max(0., (parent_mass**2-s-mass**2)**2-4*s*mass**2))/(2*parent_mass)
    energy = (parent_mass**2+mass**2-s)/(2*parent_mass)
    pair_energy = parent_mass-energy
    pion_energy = (s+charged_mass**2-neutral_mass**2)/(2*math.sqrt(s))
    other_energy = math.sqrt(s)-pion_energy
    pion_momentum = math.sqrt(max(0., pion_energy**2-charged_mass**2))
    direction = (math.sqrt(max(0., 1-cosine**2)), 0., cosine)
    beta = (0., 0., -momentum/pair_energy)
    charged = boost((pion_energy,)+tuple(pion_momentum*x for x in direction), beta)
    neutral = boost((other_energy,)+tuple(-pion_momentum*x for x in direction), beta)
    return charged, neutral, (energy, 0., 0., momentum)


def covariant_spin_sum(charged, neutral, dark, parent_mass):
    tensor = rho.current_tensor(charged, neutral, dark, parent_mass)
    polarizations = [(0., -1., 0., 0.), (0., 0., -1., 0.)]
    virtuality = minkowski(dark, dark)
    if virtuality > 1.e-12:
        mass = math.sqrt(virtuality)
        polarizations.append((dark[3]/mass, 0., 0., -dark[0]/mass))
    return math.fsum(dot(tensor[index], epsilon)**2
                     for index in range(1, 4) for epsilon in polarizations)/3


class ChargedRhoRadiativeTest(unittest.TestCase):
    def test_raw_physical_ward_identity_with_unequal_masses(self):
        for mass in (0., .22, .3, .49):
            low = (MODEL['charged_pion_mass_gev']+MODEL['neutral_pion_mass_gev'])**2
            high = (MODEL['reference_parent_mass_gev']-mass)**2
            for fraction in (.001, .2, .8, .999):
                s = low+fraction*(high-low)
                for cosine in (-.999, -.4, 0., .6, .999):
                    charged, neutral, dark = momenta(s, cosine, mass)
                    tensor = rho.current_tensor(charged, neutral, dark, MODEL['reference_parent_mass_gev'])
                    dark_cov = (dark[0],)+tuple(-x for x in dark[1:])
                    for initial_spin in range(1, 4):
                        self.assertAlmostEqual(dot(tensor[initial_spin], dark_cov), 0., delta=5.e-13)
                    # The raw time row need not vanish: the initial rho is a
                    # massive vector, whose physical polarizations obey eta.P=0.
                    self.assertTrue(all(math.isfinite(x) for row in tensor for x in row))
                    conjugate = rho.current_tensor(charged, neutral, dark,
                                                    MODEL['reference_parent_mass_gev'], charge=-1)
                    self.assertEqual(conjugate, tuple(tuple(-x for x in row) for row in tensor))
                    broken = [list(row) for row in tensor]
                    for i in range(4):
                        broken[i][i] += 1 if i == 0 else -1
                    self.assertGreater(abs(dot(broken[3], dark_cov)), .5*dark[3])

    def test_density_matches_independent_covariant_polarizations(self):
        for mass in (0., .22, .3, .4, .49):
            low = (MODEL['charged_pion_mass_gev']+MODEL['neutral_pion_mass_gev'])**2
            for fraction in (.001, .3, .8, .999):
                s = low+fraction*((MODEL['reference_parent_mass_gev']-mass)**2-low)
                for cosine in (-.999, -.4, .2, .999):
                    p, r, k = momenta(s, cosine, mass)
                    expected = covariant_spin_sum(p, r, k, MODEL['reference_parent_mass_gev'])
                    actual = rho.matrix_element_squared(s, cosine, MODEL['reference_parent_mass_gev'], mass, MODEL)
                    self.assertAlmostEqual(actual/expected, 1., delta=8.e-12)
        p, r, k = momenta(.14, .4, .3)
        tensor = rho.current_tensor(p, r, k, MODEL['reference_parent_mass_gev'])
        transverse = math.fsum(tensor[i][j]**2 for i in range(1, 4) for j in (1, 2))/3
        self.assertGreater(covariant_spin_sum(p, r, k, MODEL['reference_parent_mass_gev']), transverse)

    def test_real_photon_matches_published_g2_low_tensor(self):
        # hep-ph/9707202 Eq.(2), Delta kappa=0, without its unspecified O(k)
        # structure-dependent terms. This is an algebra check, not a fit.
        parent = (MODEL['reference_parent_mass_gev'], 0., 0., 0.)
        for s in (.1, .2, .4, .55):
            for cosine in (-.95, .1, .9):
                p, r, k = momenta(s, cosine, 0.)
                tensor = rho.current_tensor(p, r, k, parent[0])
                delta = tuple(a-b for a, b in zip(p, r))
                for i in range(1, 4):
                    eta = tuple(float(index == i) for index in range(4))
                    for j in (1, 2):
                        epsilon = tuple(float(index == j) for index in range(4))
                        infrared = minkowski(p, epsilon)/minkowski(p, k)
                        expected = (infrared*minkowski(delta, eta)+infrared*minkowski(k, eta)
                            -2*minkowski(epsilon, eta)
                            -2*(infrared*minkowski(k, eta)-minkowski(epsilon, eta))
                             *minkowski(p, k)/minkowski(parent, k))
                        self.assertAlmostEqual(tensor[i][j], expected, delta=2.e-13)

    def test_rates_and_quadrature_converge_without_tuning_full_model(self):
        expected = {.22:.00038540692293042553, .25:.00028440969428495424,
                    .3:.0001642950386042034, .4:.00003549395546078674}
        key = rho._model_key(MODEL)
        for mass, coefficient in expected.items():
            self.assertAlmostEqual(rho.branching_coefficient(.77511, mass, MODEL)/coefficient,
                                   1., delta=2.e-11)
            coarse, fine = (rho._integral(.77511, mass, key, count) for count in (32, 128))
            self.assertAlmostEqual(coarse/fine, 1., delta=2.e-10)
        paper = copy.deepcopy(MODEL)
        paper['reference_parent_mass_gev'] = .775
        paper['valid_mass_range_gev'][1] = .775-paper['charged_pion_mass_gev']-paper['neutral_pion_mass_gev']
        photon = rho.photon_branching_above_cut(.05, paper)
        self.assertAlmostEqual(photon, .003738282632, delta=8.e-13)
        # 0708.3256 Table III quotes .00423 for a *full hadronic model*.
        # Do not tune this local benchmark to it or claim precise agreement.
        self.assertLess(photon, .00423)
        self.assertGreater(photon/.00423, .88)
        self.assertGreater(rho.photon_branching_above_cut(.025, MODEL), photon)
        self.assertEqual(rho.photon_branching_above_cut(.5, MODEL), 0.)

    def test_analytic_envelope_covers_every_tested_rectangle(self):
        rng = random.Random(20261021)
        for mass in (.22, .3, .4, .49):
            cells, _ = rho._envelope(.77511, mass, rho._model_key(MODEL))
            for low_s, high_s, low_c, high_c, bound in cells:
                for fraction_s, fraction_c in ((0., 0.), (1., 1.), (.5, .5), (rng.random(), rng.random())):
                    s, c = low_s+fraction_s*(high_s-low_s), low_c+fraction_c*(high_c-low_c)
                    density = rho._phase_density(s, c, .77511, mass,
                                                 MODEL['charged_pion_mass_gev'], MODEL['neutral_pion_mass_gev'])
                    self.assertLessEqual(density, bound*(1+1.e-12))

    def test_sample_conserves_momenta_shells_charge_and_displacement(self):
        rng = random.Random(981021)
        for mass in (.22, .3, .4, .49):
            for parent in ((.77511, 0., 0., 0.), boost((.77511, 0., 0., 0.), (.3, -.2, .7))):
                for charge in (-1, 1):
                    for _ in range(20):
                        row = rho.sample_decay(parent, mass, 37., rng, model=MODEL, charge=charge)
                        dark, muon, antimuon = (row[name] for name in ('dark_photon_p4', 'muon_p4', 'antimuon_p4'))
                        charged, neutral = row['recoil_p4s']
                        for index in range(4):
                            self.assertAlmostEqual(dark[index]+charged[index]+neutral[index], parent[index], delta=2.e-12)
                            self.assertAlmostEqual(muon[index]+antimuon[index], dark[index], delta=2.e-12)
                        for vector, shell in ((dark, mass), (charged, MODEL['charged_pion_mass_gev']),
                                              (neutral, MODEL['neutral_pion_mass_gev']), (muon, rho.MUON_MASS_GEV)):
                            self.assertAlmostEqual(minkowski(vector, vector), shell*shell, delta=2.e-12)
                        self.assertEqual(row['recoil_pdg_ids'], [charge*211, 111])
                        length = row['proper_decay_length_mm']
                        self.assertGreater(length, 0.)
                        self.assertAlmostEqual(row['decay_ct_mm'], length*dark[0]/mass)
                        self.assertTrue(row['unconditional_decay'])
        positive = rho.sample_decay((.77511, 0., 0., 0.), .3, 37., random.Random(5), model=MODEL, charge=1)
        negative = rho.sample_decay((.77511, 0., 0., 0.), .3, 37., random.Random(5), model=MODEL, charge=-1)
        self.assertEqual(positive['dark_photon_p4'], negative['dark_photon_p4'])

    def test_sampling_moments_agree_with_integrated_spin_density(self):
        rng = random.Random(5141)
        mass, parent_mass = .3, MODEL['reference_parent_mass_gev']
        key = rho._model_key(MODEL)
        numerator, denominator = 0., 0.
        low, high = (MODEL['charged_pion_mass_gev']+MODEL['neutral_pion_mass_gev'])**2, (parent_mass-mass)**2
        for node, weight in rho._gauss_legendre(64):
            theta = (node+1)*math.pi/4
            s = low+(high-low)*math.sin(theta)**2
            jac = 2*(high-low)*math.sin(theta)*math.cos(theta)*math.pi/4
            for c, angular_weight in rho._gauss_legendre(32):
                w = weight*angular_weight*jac*rho._phase_density(s, c, parent_mass, mass,
                                  MODEL['charged_pion_mass_gev'], MODEL['neutral_pion_mass_gev'])
                denominator += w
                numerator += w*c
        sampled = [rho._sample_invariants(parent_mass, mass, key, rng)[1] for _ in range(4000)]
        mean = math.fsum(sampled)/len(sampled)
        variance = math.fsum((c-mean)**2 for c in sampled)/len(sampled)
        self.assertLess(abs(mean-numerator/denominator), 5*math.sqrt(variance/len(sampled)))

    def test_observed_dimuon_angle_matches_finite_mass_density(self):
        # Hold one physical dipion configuration fixed and recover the dimuon
        # angle solely from final four-vectors. A fixed configuration prevents
        # different pion configurations from washing out their spin alignment.
        # Its prediction contracts the raw tensor with independent spin vectors.
        mass, parent_mass = .3, MODEL['reference_parent_mass_gev']
        beta_mu2 = 1-4*rho.MUON_MASS_GEV**2/mass**2
        low = (MODEL['charged_pion_mass_gev']+MODEL['neutral_pion_mass_gev'])**2
        s, cosine = low+.99*((parent_mass-mass)**2-low), .99
        charged, neutral, dark = momenta(s, cosine, mass)
        tensor = rho.current_tensor(charged, neutral, dark, parent_mass)
        longitudinal = (dark[3]/mass, 0., 0., -dark[0]/mass)
        rzz = math.fsum(dot(tensor[i], longitudinal)**2 for i in range(1, 4))
        trace = 3*covariant_spin_sum(charged, neutral, dark, parent_mass)
        expected = (1/3-beta_mu2*(1+2*rzz/trace)/15)/(1-beta_mu2/3)
        rng = random.Random(213213)
        observed, proper_lengths = [], []
        with patch.object(rho, '_sample_invariants', return_value=(s, cosine)):
            for _ in range(12000):
                row = rho.sample_decay((parent_mass, 0., 0., 0.), mass, 37., rng, model=MODEL)
                dark = row['dark_photon_p4']
                muon_rest = boost(row['muon_p4'], tuple(-p/dark[0] for p in dark[1:]))
                axis_length = math.sqrt(dot(dark[1:], dark[1:]))
                muon_length = math.sqrt(dot(muon_rest[1:], muon_rest[1:]))
                muon_cosine = dot(dark[1:], muon_rest[1:])/(axis_length*muon_length)
                observed.append(muon_cosine*muon_cosine)
                proper_lengths.append(row['proper_decay_length_mm'])
        mean = math.fsum(observed)/len(observed)
        variance = math.fsum((value-mean)**2 for value in observed)/len(observed)
        error = math.sqrt(variance/len(observed))
        self.assertLess(abs(mean-expected), 5*error)
        self.assertGreater(abs(expected-1/3), 5*error)
        self.assertLess(abs(math.fsum(proper_lengths)/len(proper_lengths)-37.), 5*37/math.sqrt(len(proper_lengths)))

    def test_boost_mean_keeps_one_invariant_at_near_endpoint_nodes(self):
        # Four-term fsum and E²-rounded(|p|²) differ by 4.9e-10 GeV here.
        # That is enough to close the last otherwise physical quadrature node
        # at mA=.5. Preserve p4 and use one invariant throughout the integral.
        parent = (3000., 1.055274238141377, -1.946398203743612, 2999.99908285566)
        parent_mass = math.sqrt(rho._minkowski(parent, parent))
        mass = .5
        actual = rho.mean_lab_beta_gamma(parent, mass, MODEL)
        nodes = rho._quadrature(MODEL['reference_parent_mass_gev'], mass, rho._model_key(MODEL))
        self.assertLess(nodes[-1][0], (parent_mass-mass)**2)
        total_weight, expected = math.fsum(weight for _, weight in nodes), 0.
        for s, weight in nodes:
            momentum = math.sqrt((parent_mass**2-s-mass**2)**2-4*s*mass**2)/(2*parent_mass)
            energy = (parent_mass**2+mass**2-s)/(2*parent_mass)
            a = parent[0]/parent_mass*energy
            b = math.sqrt(dot(parent[1:], parent[1:]))/parent_mass*momentum
            angular_mean = math.fsum(w*math.sqrt((a+b*c)**2-mass**2)/mass
                                     for c, w in rho._gauss_legendre(64))/2
            expected += weight*angular_mean
        self.assertAlmostEqual(actual/(expected/total_weight), 1., delta=2.e-12)

    def test_actual_native_endpoint_failure_uses_audited_parent_rate_bound(self):
        # Exact seed841220001 trial6663, rho+ index52. The original isolated
        # 0.1% channel check correctly rejects its 1.67% relative endpoint shift.
        # The new path proves the <=0.1% bound on this same parent's total.
        parent = (455.44498130766743, -.34545302901765806, .09521061883127688, -455.4441807650014)
        parent_mass = math.sqrt(minkowski(parent, parent))
        context = dict(native_mass_gev=.77511, native_energy_gev=parent[0], native_mass_tolerance=.001)
        with self.assertRaisesRegex(ValueError, '0.1%'):
            rho.branching_coefficient(parent_mass, .5, MODEL, **context)
        owned = parent_rate_context(.5)
        report = rho.native_parent_precision_report(parent_mass, .5, MODEL,
                    native_parent_rate_model=owned, **context)
        self.assertGreater(report['channel_relative_rate_shift'], .016)
        self.assertAlmostEqual(report['channel_nominal_parent_fraction'], 2.7725762200625056e-6, delta=1.e-16)
        self.assertLess(report['parent_l1_relative_rate_shift'], .00005)
        self.assertEqual(report['conditional_distribution_fraction_bound'], report['channel_nominal_parent_fraction'])
        self.assertLess(report['parent_total_relative_bound'], .001)
        shifted = rho.branching_coefficient(parent_mass, .5, MODEL,
                    native_parent_rate_model=owned, **context)
        self.assertEqual(shifted, report['kinematic_channel_coefficient'])
        sample_context = dict(native_mass_gev=.77511, native_mass_tolerance=.001,
                              native_parent_rate_model=owned)
        self.assertTrue(math.isfinite(rho.mean_lab_beta_gamma(parent, .5, MODEL, **sample_context)))
        row = rho.sample_decay(parent, .5, 37., random.Random(30213), model=MODEL, **sample_context)
        for index in range(4):
            self.assertAlmostEqual(math.fsum(vector[index] for vector in
                [row['dark_photon_p4']]+row['recoil_p4s']), parent[index], delta=2.e-7)
        self.assertEqual(row['recoil_pdg_ids'], [211, 111])
        with self.assertRaises(ValueError):
            rho.sample_decay(parent, .5, 37., random.Random(30213), model=MODEL,
                             charge=-1, **sample_context)

    def test_parent_context_cannot_disable_or_inflate_the_precision_budget(self):
        context = dict(native_mass_gev=.77511, native_energy_gev=455., native_mass_tolerance=.001)
        owned = parent_rate_context(.5)
        invalid = []
        for field, value in (('parent_pdg_id', 113), ('exclusive_channels', owned['exclusive_channels'][:1])):
            changed = copy.deepcopy(owned)
            changed[field] = value
            invalid.append(changed)
        for index, field, value in ((0, 'pdg_id', -213), (1, 'pdg_id', None),
                                   (0, 'recoil_pdg_id', -211), (0, 'mass_min_gev', .77),
                                   (0, 'br_p_gamma', 2.), (1, 'recoil_pdg_ids', [-211,111]),
                                   (1, 'channel', owned['exclusive_channels'][0]['channel'])):
            changed = copy.deepcopy(owned)
            changed['exclusive_channels'][index][field] = value
            invalid.append(changed)
        for changed in invalid:
            with self.subTest(context=changed), self.assertRaises(ValueError):
                rho.branching_coefficient(.775115, .5, MODEL, native_parent_rate_model=changed, **context)
        weak_sibling = copy.deepcopy(owned)
        weak_sibling['exclusive_channels'][0]['br_p_gamma'] *= .0001
        with self.assertRaisesRegex(ValueError, 'conditional-shape'):
            rho.branching_coefficient(.775115, .5, MODEL, native_parent_rate_model=weak_sibling, **context)
        with self.assertRaisesRegex(ValueError, 'conditional-shape'):
            rho.branching_coefficient(.7753, .5, MODEL, native_parent_rate_model=owned, **context)
        with self.assertRaisesRegex(ValueError, 'open'):
            rho.branching_coefficient(.7745, .5, MODEL, native_parent_rate_model=owned, **context)
        with self.assertRaisesRegex(ValueError, 'complete native'):
            rho.branching_coefficient(.77511, .5, MODEL, native_parent_rate_model=owned)
        with self.assertRaisesRegex(ValueError, 'complete native'):
            rho.mean_lab_beta_gamma((.77511, 0., 0., 0.), .5, MODEL, native_parent_rate_model=owned)

    def test_cubic_interpolation_meets_unchanged_checks_and_never_extrapolates(self):
        key = rho._model_key(MODEL)
        for mass in (.22, .25, .3, .4, .49, .5):
            receipt = rho.rate_interpolation_validation(mass, MODEL)
            self.assertTrue(receipt['qualified'])
            self.assertEqual(receipt['independent_checks'], 24)
            self.assertLessEqual(receipt['maximum_checked_relative_error'], 1.e-10)
            for parent_mass in (.77510913, .77511079):
                actual = rho._interpolated_or_exact_coefficient(parent_mass, mass, key)
                exact = rho._exact_coefficient(parent_mass, mass, key)
                self.assertAlmostEqual(actual/exact, 1., delta=1.e-10)
        with patch.object(rho, '_local_cubic', side_effect=AssertionError('No extrapolation')):
            self.assertEqual(rho._interpolated_or_exact_coefficient(.77512, .5, key),
                             rho._exact_coefficient(.77512, .5, key))
        self.assertFalse(rho.rate_interpolation_validation(.5005628, MODEL)['qualified'])

    def test_native_precision_guard_and_bad_inputs(self):
        for name, value in (('electromagnetic_gyromagnetic_ratio', 1), ('signal_rate_validated', True),
                            ('parent_mass_continuation', 'broad'), ('pole_mass_numerical_tolerance_gev', .01)):
            bad = copy.deepcopy(MODEL)
            bad[name] = value
            with self.assertRaises(ValueError):
                rho.branching_coefficient(.77511, .3, bad)
        with self.assertRaises(ValueError):
            rho.branching_coefficient(.78, .3, MODEL)
        context = dict(native_mass_gev=.77511, native_energy_gev=1460., native_mass_tolerance=1.e-4)
        nominal = rho.branching_coefficient(.77511, .3, MODEL)
        actual = rho.branching_coefficient(.77512, .3, MODEL, **context)
        self.assertLess(abs(actual/nominal-1), .001)
        with self.assertRaises(ValueError):
            rho.branching_coefficient(.8, .3, MODEL, **context)
        self.assertEqual(rho.branching_coefficient(.77511, .9, MODEL), 0.)
        with self.assertRaises(ValueError):
            rho.photon_branching_above_cut(0., MODEL)
        with self.assertRaises(ValueError):
            rho.sample_decay((.77511, 0., 0., 0.), .2, 37., random.Random(1), model=MODEL)


if __name__ == '__main__':
    unittest.main()
