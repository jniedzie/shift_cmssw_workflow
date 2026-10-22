# Code style: AI-code. AI-generated independent rho FSR/contact checks.
import copy
import math
from pathlib import Path
import random
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import dark_photon_rho_radiative as rho
from dark_photon_meson import boost
from dark_photon_vector_meson import two_body_momentum


MODEL = dict(kind='gauged-derivative-scalar-qed', alpha=1/137.035999084,
    pion_mass_gev=.13957039, reference_parent_mass_gev=.77526,
    valid_mass_range_gev=[0., .49611922], parent_polarization='unpolarized',
    parent_mass_continuation='zero-width-pole',
    pole_mass_numerical_tolerance_gev=1.e-6,
    normalization=dict(channel='pipi', branching_fraction=1.,
        reference='PDG2025: rho -> pi pi approximately 100 percent; explicit pole approximation'),
    reference='https://arxiv.org/abs/hep-ph/9903217, gauged rho-pion term, Eq.4',
    assumption='Pointlike pion FSR plus contact; structure-dependent and rescattering terms absent',
    signal_rate_validated=False)


def dot(first, second):
    return math.fsum(a*b for a, b in zip(first, second))


def minkowski(first, second):
    return first[0]*second[0]-dot(first[1:], second[1:])


def add(*vectors):
    return tuple(math.fsum(values) for values in zip(*vectors))


def unit(vector):
    length = math.sqrt(dot(vector, vector))
    return tuple(x/length for x in vector)


def cross(first, second):
    return (first[1]*second[2]-first[2]*second[1],
            first[2]*second[0]-first[0]*second[2],
            first[0]*second[1]-first[1]*second[0])


def momenta(s, cosine, mass):
    """Independent factorized three-body phase space in the rho rest frame."""
    parent = MODEL['reference_parent_mass_gev']
    pion = MODEL['pion_mass_gev']
    momentum = math.sqrt((parent*parent-s-mass*mass)**2-4*s*mass*mass)/(2*parent)
    dark_energy = (parent*parent+mass*mass-s)/(2*parent)
    pair_energy = parent-dark_energy
    pion_momentum = math.sqrt(s/4-pion*pion)
    direction = (math.sqrt(1-cosine*cosine), 0., cosine)
    positive = boost((math.sqrt(s)/2,)+tuple(pion_momentum*x for x in direction),
                     (0., 0., -momentum/pair_energy))
    negative = boost((math.sqrt(s)/2,)+tuple(-pion_momentum*x for x in direction),
                     (0., 0., -momentum/pair_energy))
    return positive, negative, (dark_energy, 0., 0., momentum)


def covariant_matrix_squared(positive, negative, dark):
    """Contract the explicit tensor with independently supplied spin vectors."""
    tensor = rho.current_tensor(positive, negative, dark)
    virtuality = minkowski(dark, dark)
    # rho is at rest and A' points along +z. These are covariant A' vectors.
    polarizations = [(0., -1., 0., 0.), (0., 0., -1., 0.)]
    if virtuality > 1.e-14:
        mass = math.sqrt(virtuality)
        polarizations.append((dark[3]/mass, 0., 0., -dark[0]/mass))
    return math.fsum(dot(tensor[i], epsilon)**2 for i in range(1, 4)
                     for epsilon in polarizations)/3


class RhoRadiativePhysicsTest(unittest.TestCase):
    def test_both_ward_identities_with_massive_current_and_contact(self):
        for mass in (0., .22, .4):
            for s in (.085, .1, (.77526-mass)**2-.001):
                if s >= (.77526-mass)**2:
                    continue
                for cosine in (-.95, -.3, 0., .4, .99):
                    positive, negative, dark = momenta(s, cosine, mass)
                    tensor = rho.current_tensor(positive, negative, dark)
                    parent = add(positive, negative, dark)
                    dark_cov = (dark[0],)+tuple(-x for x in dark[1:])
                    parent_cov = (parent[0],)+tuple(-x for x in parent[1:])
                    for mu in range(4):
                        self.assertAlmostEqual(dot(tensor[mu], dark_cov), 0., delta=2.e-12)
                    for nu in range(4):
                        self.assertAlmostEqual(math.fsum(parent_cov[mu]*tensor[mu][nu]
                                                        for mu in range(4)), 0., delta=2.e-12)
                    # Omitting the seagull breaks gauge invariance. The test
                    # is sensitive to a common wrong-sign contact convention.
                    broken = [list(row) for row in tensor]
                    for index in range(4):
                        broken[index][index] += 2 if index == 0 else -2
                    self.assertGreater(max(abs(dot(row, dark_cov)) for row in broken), dark[0])

    def test_positive_density_matches_independent_covariant_spin_sum(self):
        for mass in (0., .22, .25, .4):
            for fraction in (.001, .2, .6, .99):
                threshold = 4*MODEL['pion_mass_gev']**2
                s = threshold+fraction*((.77526-mass)**2-threshold)
                for cosine in (-.999, -.3, .0, .7, .999):
                    positive, negative, dark = momenta(s, cosine, mass)
                    expected = covariant_matrix_squared(positive, negative, dark)
                    actual = rho.matrix_element_squared(s, cosine, .77526, mass, MODEL)
                    self.assertAlmostEqual(actual/expected, 1., places=11)

    def test_independent_cut_photon_validation_and_massive_rates(self):
        # Independent direct tensor/phase-space integration gives these unit
        # epsilon rates. The real-photon check is PDG2025 BR=.0099+-.0016 for
        # E_gamma>50 MeV in the rho frame; the cut is part of the observable.
        photon = rho.photon_branching_above_cut(.05, MODEL)
        self.assertAlmostEqual(photon, .01155135843614686, places=12)
        self.assertLess(abs(photon-.0099)/.0016, 1.04)
        expected = { .22:.001391855555846118, .25:.001041014835015579,
                     .3:.0006121826652162655, .4:.00013172674502672432 }
        for mass, value in expected.items():
            actual = rho.branching_coefficient(.77526, mass, MODEL)
            self.assertAlmostEqual(actual/value, 1., places=11)
            key = rho._model_key(MODEL)
            coarse = rho._integral(.77526, mass, key, 32)
            fine = rho._integral(.77526, mass, key, 128)
            self.assertAlmostEqual(coarse/fine, 1., places=10)
        self.assertGreater(rho.photon_branching_above_cut(.025, MODEL), photon)
        self.assertEqual(rho.photon_branching_above_cut(.5, MODEL), 0.)
        for mass in (.49611922, .5, .9):
            self.assertEqual(rho.branching_coefficient(.77526, mass, MODEL), 0.)
        scaled = copy.deepcopy(MODEL)
        scaled['normalization']['branching_fraction'] = .8
        self.assertAlmostEqual(rho.branching_coefficient(.77526, .22, scaled)/expected[.22], .8)

    def test_analytic_rejection_bound_covers_phase_space(self):
        for mass in (.22, .4, .49):
            cells, _ = rho._envelope(.77526, mass, rho._model_key(MODEL))
            for low_s, high_s, low_c, high_c, bound in cells:
                for fraction_s, fraction_c in ((.001, .001), (.5, .5), (.999, .999), (.999, .001)):
                    s = low_s+(high_s-low_s)*fraction_s
                    cosine = low_c+(high_c-low_c)*fraction_c
                    value = rho._phase_density(s, cosine, .77526, mass, MODEL['pion_mass_gev'])
                    self.assertLessEqual(value, bound)

    def test_numerical_pole_interpolation_preserves_mass_precision_sensitivity(self):
        key = rho._model_key(MODEL)
        for mass in (.22, .25, .3, .4):
            receipt = rho.rate_interpolation_validation(mass, MODEL)
            self.assertTrue(receipt['qualified'])
            self.assertEqual(receipt['independent_checks'], 24)
            self.assertLessEqual(receipt['maximum_checked_relative_error'], 1.e-10)
            nominal = rho.branching_coefficient(.77526, mass, MODEL)
            for shift in (-.91e-6, -.12e-6, .37e-6, .88e-6):
                parent_mass = .77526+shift
                actual = rho.branching_coefficient(parent_mass, mass, MODEL)
                exact = rho._exact_coefficient(parent_mass, mass, key)
                self.assertAlmostEqual(actual/exact, 1., delta=1.e-10)
                self.assertNotEqual(actual, nominal)
        # Larger curvature outside the current production masses also forces
        # exact evaluation rather than relaxing the interpolation tolerance.
        self.assertFalse(rho.rate_interpolation_validation(.49, MODEL)['qualified'])
        mass = .496118
        receipt = rho.rate_interpolation_validation(mass, MODEL)
        self.assertFalse(receipt['qualified'])
        self.assertEqual(receipt['reason'], 'near-closed-endpoint')
        self.assertEqual(rho.branching_coefficient(.77526, mass, MODEL),
                         rho._exact_coefficient(.77526, mass, key))

    def test_unconditional_four_momentum_shells_lifetime_and_boost(self):
        rng = random.Random(981021004)
        for momentum in ((0., 0., 0.), (1., -2., -30.), (0., 0., -600.)):
            parent = (math.sqrt(.77526**2+dot(momentum, momentum)),)+momentum
            for mass in (.22, .4, .49):
                for _ in range(12):
                    row = rho.sample_decay(parent, mass, 37., rng, model=MODEL)
                    dark, muon, antimuon = (row[key] for key in ('dark_photon_p4', 'muon_p4', 'antimuon_p4'))
                    positive, negative = row['recoil_p4s']
                    for value, expected in zip(add(dark, positive, negative), parent):
                        self.assertAlmostEqual(value, expected, delta=2.e-7)
                    for value, expected in zip(add(muon, antimuon), dark):
                        self.assertAlmostEqual(value, expected, delta=2.e-7)
                    for vector, mass_shell in ((dark, mass), (positive, MODEL['pion_mass_gev']),
                            (negative, MODEL['pion_mass_gev']), (muon, rho.MUON_MASS_GEV)):
                        self.assertAlmostEqual(minkowski(vector, vector), mass_shell**2, delta=2.e-7)
                    tensor = rho.current_tensor(positive, negative, dark)
                    dark_cov = (dark[0],)+tuple(-x for x in dark[1:])
                    self.assertLess(max(abs(dot(current, dark_cov)) for current in tensor), 2.e-7)
                    length = row['proper_decay_length_mm']
                    for value, component in zip(row['decay_displacement_mm'], dark[1:]):
                        self.assertAlmostEqual(value, length*component/mass)
                    self.assertAlmostEqual(row['decay_ct_mm'], length*dark[0]/mass)
                    self.assertEqual(row['parent_polarization'], 'unpolarized')
                    self.assertEqual(row['recoil_pdg_ids'], [211, -211])
                    self.assertTrue(row['unconditional_decay'])

    def test_sampled_spectrum_isotropy_full_muon_spin_density_and_lifetime(self):
        # n_y is normal to the pion plane. Its expectation uses the complete
        # finite-muon Gram matrix, not an independent transverse helicity law.
        rng = random.Random(981021005)
        mass, count = .4, 10000
        beta2 = 1-4*rho.MUON_MASS_GEV**2/mass**2
        total_s = total_cosine = total_axis = total_lifetime = 0.
        actual_plane = expected_plane = 0.
        for _ in range(count):
            row = rho.sample_decay((.77526, 0., 0., 0.), mass, 37., rng, model=MODEL)
            dark, positive, negative, muon = (row['dark_photon_p4'], *row['recoil_p4s'], row['muon_p4'])
            pair = add(positive, negative)
            s = minkowski(pair, pair)
            axis = unit(dark[1:])
            pion_rest = boost(positive, tuple(-x/pair[0] for x in pair[1:]))
            cosine = dot(unit(pion_rest[1:]), axis)
            normal = unit(cross(positive[1:], negative[1:]))
            muon_rest = boost(muon, tuple(-x/dark[0] for x in dark[1:]))
            bxx, bxz, bzx, bzz = rho._density_components(s, cosine, .77526, mass, MODEL['pion_mass_gev'])
            trace = bxx*bxx+bxz*bxz+4+bzx*bzx+bzz*bzz
            actual_plane += dot(unit(muon_rest[1:]), normal)**2
            expected_plane += (1/3-beta2/15-2*beta2*(4/trace)/15)/(1-beta2/3)
            total_s += s
            total_cosine += cosine
            total_axis += axis[2]**2
            total_lifetime += row['proper_decay_length_mm']
        nodes = rho._quadrature(.77526, mass, rho._model_key(MODEL), 128)
        expected_s = math.fsum(s*w for s, w in nodes)/math.fsum(w for _, w in nodes)
        self.assertAlmostEqual(total_s/count, expected_s, delta=.0006)
        self.assertAlmostEqual(total_cosine/count, 0., delta=.018)
        self.assertAlmostEqual(total_axis/count, 1/3, delta=.012)
        self.assertAlmostEqual(total_lifetime/count, 37., delta=1.2)
        self.assertAlmostEqual(actual_plane/count, expected_plane/count, delta=.012)

    def test_lab_mean_boost_agrees_with_direct_spectrum_angular_integration(self):
        mass = .22
        for momentum in (0., 1., 30.):
            parent = (math.hypot(.77526, momentum), 0., 0., -momentum)
            actual = rho.mean_lab_beta_gamma(parent, mass, MODEL)
            nodes = rho._quadrature(.77526, mass, rho._model_key(MODEL), 32)
            expected = 0.
            for s, weight in nodes:
                energy = (.77526**2+mass**2-s)/(2*.77526)
                recoil = two_body_momentum(.77526, math.sqrt(s), mass)
                a, b = parent[0]/.77526*energy, momentum/.77526*recoil
                expected += weight*math.fsum(math.sqrt(max(0., (a+b*(-1+(i+.5)/1000))**2-mass**2))
                                            for i in range(2000))/(2000*mass)
            expected /= math.fsum(weight for _, weight in nodes)
            self.assertAlmostEqual(actual/expected, 1., delta=2.e-6)

    def test_invalid_models_massless_rate_and_offshell_parent_fail(self):
        for key, value in (('alpha', 0.), ('reference', ''), ('parent_polarization', 'transverse'),
                           ('signal_rate_validated', True), ('parent_mass_continuation', 'broad-rho')):
            altered = dict(MODEL, **{key: value})
            with self.assertRaises(ValueError):
                rho.branching_coefficient(.77526, .22, altered)
        for parent in (.75, .8):
            with self.assertRaisesRegex(ValueError, 'Off-shell'):
                rho.branching_coefficient(parent, .22, MODEL)
        with self.assertRaises(ValueError):
            rho.branching_coefficient(.77526, 0., MODEL)
        with self.assertRaises(ValueError):
            rho.photon_branching_above_cut(0., MODEL)
        with self.assertRaises(ValueError):
            rho.sample_decay((.77526, 0., 0., 0.), .2, 37., random.Random(1), model=MODEL)

    def test_near_endpoint_mean_preserves_support_at_large_native_boost(self):
        for momentum in (600., 1000., 3000., 6800.):
            parent = (math.hypot(.77526, momentum), 0., 0., -momentum)
            actual = rho.mean_lab_beta_gamma(parent, .496118, MODEL)
            self.assertTrue(math.isfinite(actual))
            # At threshold A' and the parent share their velocity.
            self.assertAlmostEqual(actual/(momentum/.77526), 1., delta=2.e-5)

    def test_native_generator_residual_requires_context_and_tight_rate_guard(self):
        # Exact Pythia seed 831210001, successful collision 4721, rho index55.
        # The stored native mass is the fixed pole; its accepted native p4
        # has a shell residual larger than Python floating-point cancellation.
        parent = (1460.193269723507, 1.526253157507588, .23096819334015048,
                  -1460.1922479959876)
        mass = math.sqrt(parent[0]**2-dot(parent[1:], parent[1:]))
        self.assertAlmostEqual(mass-.77526, 9.78781098581738e-6, delta=1.e-12)
        nominal = rho.branching_coefficient(.77526, .3, MODEL)
        with self.assertRaisesRegex(ValueError, 'Off-shell'):
            rho.branching_coefficient(mass, .3, MODEL)
        context = dict(native_mass_gev=.77526, native_energy_gev=parent[0], native_mass_tolerance=.001)
        diagnostic = rho.branching_coefficient(mass, .3, MODEL, **context)
        self.assertAlmostEqual(diagnostic/nominal-1, 7.1367241e-5, delta=1.e-8)
        self.assertEqual(diagnostic, rho._exact_coefficient(mass, .3, rho._model_key(MODEL)))
        for bad in (dict(context, native_mass_gev=.78), dict(context, native_mass_tolerance=1.e-10)):
            with self.assertRaises(ValueError):
                rho.branching_coefficient(mass, .3, MODEL, **bad)
        with self.assertRaisesRegex(ValueError, '0.1%'):
            rho.branching_coefficient(.8, .3, MODEL, **context)
        with self.assertRaisesRegex(ValueError, 'Complete native'):
            rho.branching_coefficient(mass, .3, MODEL, native_energy_gev=parent[0])
        with self.assertRaisesRegex(ValueError, 'whether the rare decay is open'):
            rho.branching_coefficient(.5, .3, MODEL, **context)
        sample_context = dict(native_mass_gev=.77526, native_mass_tolerance=.001)
        self.assertTrue(math.isfinite(rho.mean_lab_beta_gamma(parent, .3, MODEL, **sample_context)))
        row = rho.sample_decay(parent, .3, 37., random.Random(981021007), model=MODEL, **sample_context)
        for value, expected in zip(add(row['dark_photon_p4'], *row['recoil_p4s']), parent):
            self.assertAlmostEqual(value, expected, delta=2.e-6)


if __name__ == '__main__':
    unittest.main()
