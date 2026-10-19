# Code style: AI-code. AI-generated independent dipion-current physics checks.
import copy
import itertools
import json
import math
from pathlib import Path
import random
import sys
import time
import unittest

SCRIPTS = Path(__file__).resolve().parents[1]/'scripts'
sys.path.insert(0, str(SCRIPTS))
import dark_photon_dipion_meson as dipion
from dark_photon_meson import boost

INPUTS = json.loads((SCRIPTS.parent/'config/campaigns/dark_photon_dipion_channels.json').read_text())['channels']


def squared(vector):
    return math.fsum((vector[0]**2, *(-x*x for x in vector[1:])))


def add(*vectors):
    return tuple(math.fsum(values) for values in zip(*vectors))


def unit(vector):
    length = math.sqrt(math.fsum(x*x for x in vector))
    return tuple(x/length for x in vector)


def cross(first, second):
    return (first[1]*second[2]-first[2]*second[1],
            first[2]*second[0]-first[0]*second[2],
            first[0]*second[1]-first[1]*second[0])


def dot(first, second):
    return math.fsum(x*y for x, y in zip(first, second))


def lorentz_current(first, second, dark):
    # An independent determinant construction, with covariant input momenta.
    covariant = [tuple(x if i == 0 else -x for i, x in enumerate(vector))
                 for vector in (first, second, dark)]
    result = []
    for mu in range(4):
        terms = []
        for permutation in itertools.permutations(range(4)):
            if permutation[0] != mu:
                continue
            inversions = sum(permutation[i] > permutation[j] for i in range(4) for j in range(i+1, 4))
            sign = -1 if inversions % 2 else 1
            terms.append(sign*math.prod(covariant[j][permutation[j+1]] for j in range(3)))
        result.append(math.fsum(terms))
    return tuple(result)


class DipionPhysicsTest(unittest.TestCase):
    def test_measured_inputs_and_coherent_contact_interference(self):
        model = INPUTS[1]['dipion_model']
        for s, q2 in ((.1, .09), (.5, .0), (.605, .04)):
            pole = model['rho_mass_gev']
            width = .1491*math.sqrt(s)/pole*((1-4*.13957039**2/s)/(1-4*.13957039**2/pole**2))**1.5
            rho = pole**2/complex(pole**2-s, -pole*width)
            om = model['omega_mixing']
            omega = om['amplitude']*complex(math.cos(om['phase_radians']), math.sin(om['phase_radians']))*(
                om['mass_gev']**2/complex(om['mass_gev']**2-s, -om['mass_gev']*om['width_gev']))
            vpole = model['virtual_rho_mass_gev']
            vwidth = 0. if q2 <= 4*.13957039**2 else .1491*math.sqrt(q2)/vpole*((
                1-4*.13957039**2/q2)/(1-4*.13957039**2/vpole**2))**1.5
            virtual = vpole**2/complex(vpole**2-q2, -vpole*vwidth)
            expected = -.5+1.5*(rho+omega)*virtual
            actual = dipion.magnetic_form_factor(s, q2, model)
            self.assertAlmostEqual(abs(actual-expected), 0., places=13)
            incoherent = .25+abs(1.5*rho*virtual)**2+abs(1.5*omega*virtual)**2
            self.assertGreater(abs(abs(actual)**2-incoherent), .1)

    def test_no_identical_photon_factor_and_closed_channels(self):
        for channel in INPUTS:
            model = copy.deepcopy(channel['dipion_model'])
            model['normalization'] = dict(channel='pipi-gamma', branching_fraction=channel['br_pipi_gamma'], reference='measured gamma')
            parent = model['reference_parent_mass_gev']
            self.assertAlmostEqual(dipion.branching_coefficient(parent, 0., model), channel['br_pipi_gamma'], places=13)
            endpoint = parent-2*model['pion_mass_gev']
            self.assertEqual(dipion.branching_coefficient(parent, endpoint, model), 0.)
            self.assertEqual(dipion.branching_coefficient(parent, endpoint+.001, model), 0.)
            self.assertGreater(dipion.branching_coefficient(parent, endpoint-1.e-5, model), 0.)

    def test_dalitz_normalization_and_independent_crosschecks(self):
        for channel in INPUTS:
            model = channel['dipion_model']
            normalized = model['normalization']
            self.assertAlmostEqual(dipion.predicted_dalitz_branching(normalized['lepton_mass_gev'], model),
                                   normalized['branching_fraction'], places=12)
            # A measured input is not an independent rate validation. Preserve
            # and expose the independent 10% radiative mismatch rather than
            # silently rescaling both measured channels.
            predicted = dipion.branching_coefficient(model['reference_parent_mass_gev'], 0., model)
            self.assertGreater(predicted/channel['br_pipi_gamma'], 1.09)
            self.assertLess(predicted/channel['br_pipi_gamma'], 1.12)
        eta, etap = (row['dipion_model'] for row in INPUTS)
        self.assertLess(dipion.predicted_dalitz_branching(dipion.MUON_MASS_GEV, eta), 4.e-7)
        self.assertAlmostEqual(dipion.predicted_dalitz_branching(.00051099895, etap), .00245,
                               delta=math.hypot(.00002, .00008))

    def test_independent_measured_muon_plane_distribution(self):
        eta, etap = (row['dipion_model'] for row in INPUTS)
        # BESIII2501.10130 measured eta ee plane a=-.696+-.061.
        self.assertAlmostEqual(dipion.dalitz_plane_coefficient(.00051099895, eta), -.696, delta=2*.061)
        # BESIII2402.01993 Table6 measured eta-prime mumu b=-.35+-.10.
        self.assertAlmostEqual(dipion.dalitz_plane_coefficient(dipion.MUON_MASS_GEV, etap), -.35, delta=.10)
        # Their ee value -.72+-.01 is NOT reproduced by the pure magnetic
        # prediction (-.618 after threshold integration). Keep it explicit.
        self.assertGreater(abs(dipion.dalitz_plane_coefficient(.00051099895, etap)+.72), .09)

    def test_covariant_current_reproduces_differential_phase_space(self):
        # Independent three-body phase space p_A * beta_pi ds multiplied by
        # -J² gives the s*beta_pi³*lambda^(3/2) spectrum used for generation.
        M, mass, pion_mass = .95778, .3, .13957039
        ratios = []
        for s in (.09, .15, .25, .4):
            lam = (M*M-s-mass*mass)**2-4*s*mass*mass
            momentum = math.sqrt(lam)/(2*M)
            dark = ((M*M+mass*mass-s)/(2*M), 0., 0., momentum)
            dipion_energy = (M*M+s-mass*mass)/(2*M)
            beta = (0., 0., -momentum/dipion_energy)
            pion_p = math.sqrt(s/4-pion_mass*pion_mass)
            angle = .4
            positive = boost((math.sqrt(s)/2, pion_p*math.sqrt(1-angle*angle), 0., pion_p*angle), beta)
            negative = boost((math.sqrt(s)/2, -pion_p*math.sqrt(1-angle*angle), 0., -pion_p*angle), beta)
            current = lorentz_current(positive, negative, dark)
            beta_pi = math.sqrt(1-4*pion_mass*pion_mass/s)
            weighted = -squared(current)*momentum*beta_pi/(1-angle*angle)
            expected_shape = s*beta_pi**3*lam**1.5
            ratios.append(weighted/expected_shape)
        for ratio in ratios:
            self.assertAlmostEqual(ratio, ratios[0], places=13)

    def test_fixed_width_rate_bound_increases_with_parent_mass(self):
        for channel in INPUTS:
            model = channel['dipion_model']
            nominal = model['reference_parent_mass_gev']
            for mass in (.22, .25):
                values = [dipion.branching_coefficient(nominal+offset, mass, model)
                          for offset in (-.001, -.0002, 0., .0002, .001)]
                self.assertEqual(values, sorted(values))

    def test_piecewise_rejection_bound_covers_resonance_and_continuum(self):
        for channel in INPUTS:
            model = channel['dipion_model'];parent = model['reference_parent_mass_gev']
            for mass in (0., .22, .25):
                key = dipion._model_key(model)
                edges, bounds, _ = dipion._envelope(parent, mass, key)
                for index, bound in enumerate(bounds):
                    for fraction in (.0001, .1, .3, .5, .7, .9, .9999):
                        s = edges[index]+fraction*(edges[index+1]-edges[index])
                        self.assertLessEqual(dipion.dipion_spectrum(s, parent, mass, model), bound)

    def test_lab_conservation_mass_shells_ward_identity_and_lifetime(self):
        rng = random.Random(981017001)
        for channel in INPUTS:
            model = channel['dipion_model'];M = model['reference_parent_mass_gev']
            for momentum in ((0., 0., 0.), (1., -2., -30.)):
                parent = (math.sqrt(M*M+dot(momentum, momentum)),)+momentum
                for mass in (.22, .25):
                    for _ in range(20):
                        row = dipion.sample_decay(parent, mass, 37., rng, model=model)
                        dark, muon, antimuon = (row[key] for key in ('dark_photon_p4', 'muon_p4', 'antimuon_p4'))
                        positive, negative = row['recoil_p4s']
                        for actual, expected in zip(add(muon, antimuon), dark):
                            self.assertAlmostEqual(actual, expected, places=8)
                        for actual, expected in zip(add(dark, positive, negative), parent):
                            self.assertAlmostEqual(actual, expected, places=8)
                        for vector, shell in ((dark, mass), (positive, model['pion_mass_gev']),
                                (negative, model['pion_mass_gev']), (muon, dipion.MUON_MASS_GEV)):
                            self.assertAlmostEqual(squared(vector), shell*shell, places=8)
                        current = lorentz_current(positive, negative, dark)
                        self.assertAlmostEqual(current[0]*dark[0]-dot(current[1:], dark[1:]), 0., delta=1.e-9)
                        self.assertLess(squared(current), 0.)
                        for value, component in zip(row['decay_displacement_mm'], dark[1:]):
                            self.assertAlmostEqual(value, row['proper_decay_length_mm']*component/mass, places=9)
                        self.assertAlmostEqual(row['decay_ct_mm'], row['proper_decay_length_mm']*dark[0]/mass)
                        self.assertEqual(row['recoil_pdg_ids'], [211, -211])

    def test_sampled_pion_helicity_and_linear_muon_plane_correlation(self):
        channel = INPUTS[1];model = channel['dipion_model'];M = model['reference_parent_mass_gev']
        count = 12000
        for mass in (.22, .5):
            rng = random.Random(981017002)
            pion_second = normal_second = plane_second = axis_second = length_sum = dipion_mass_sum = 0.
            for _ in range(count):
                row = dipion.sample_decay((M, 0., 0., 0.), mass, 37., rng, model=channel)
                dark = row['dark_photon_p4'];pions = row['recoil_p4s'];muon = row['muon_p4']
                axis = unit(dark[1:]);normal = unit(cross(pions[0][1:], pions[1][1:]))
                radial = unit(cross(normal, axis))
                pair = add(*pions)
                dipion_mass_sum += squared(pair)
                first = boost(pions[0], tuple(-x/pair[0] for x in pair[1:]))
                pion_second += (dot(first[1:], axis)/math.sqrt(dot(first[1:], first[1:])))**2
                muon = boost(muon, tuple(-x/dark[0] for x in dark[1:]))
                direction = unit(muon[1:])
                normal_second += dot(direction, normal)**2
                transverse = dot(direction, radial)**2+dot(direction, normal)**2
                plane_second += (dot(direction, radial)**2-dot(direction, normal)**2)/transverse
                axis_second += axis[2]**2
                length_sum += row['proper_decay_length_mm']
            beta2 = 1-4*dipion.MUON_MASS_GEV**2/mass**2
            self.assertAlmostEqual(pion_second/count, .2, delta=.008)
            self.assertAlmostEqual(normal_second/count, (1/3-beta2/5)/(1-beta2/3), delta=.01)
            self.assertAlmostEqual(plane_second/count, beta2/(6-2*beta2), delta=.018)
            self.assertAlmostEqual(axis_second/count, 1/3, delta=.01)
            self.assertAlmostEqual(length_sum/count, 37., delta=1.2)
            expected_s = math.fsum(recoil*recoil*weight for recoil, weight in
                                  dipion._moment_nodes(M, mass, dipion._model_key(model), 64))
            self.assertAlmostEqual(dipion_mass_sum/count, expected_s, delta=.0015)

    def test_dipion_spectrum_moments_and_lab_boost_quadrature(self):
        model = INPUTS[1]['dipion_model'];M = model['reference_parent_mass_gev']
        for mass in (.22, .5, M-2*model['pion_mass_gev']-1.e-5):
            key = dipion._model_key(model)
            first = dipion._moment_nodes(M, mass, key, 32)
            second = dipion._moment_nodes(M, mass, key, 64)
            self.assertAlmostEqual(math.fsum(s*s*w for s, w in first), math.fsum(s*s*w for s, w in second), delta=1.e-10)
            for momentum in (0., .3, 30., 600.):
                parent = (math.hypot(M, momentum), 0., 0., -momentum)
                actual = dipion.mean_lab_beta_gamma(parent, mass, model)
                # Independent direct midpoint angular integration at each s.
                expected = 0.
                for recoil, weight in first:
                    energy = (M*M+mass*mass-recoil*recoil)/(2*M)
                    p = math.sqrt(max(0., energy*energy-mass*mass))
                    a, b = parent[0]*energy/M, momentum*p/M
                    expected += weight*math.fsum(math.sqrt(max(0., (a+b*(-1+(i+.5)/1000))**2-mass*mass))
                                                for i in range(2000))/(2000*mass)
                self.assertAlmostEqual(actual/expected, 1., delta=2.e-6)

    def test_linear_polarization_survives_noncollinear_lab_boost(self):
        model = INPUTS[1]['dipion_model'];M = model['reference_parent_mass_gev'];mass = .4
        parent = (math.sqrt(M*M+1+4+900), 1., -2., -30.)
        rng = random.Random(981017003);total = 0.;count = 8000
        for _ in range(count):
            row = dipion.sample_decay(parent, mass, 37., rng, model=model)
            dark = row['dark_photon_p4'];positive, negative = row['recoil_p4s']
            current = lorentz_current(positive, negative, dark)
            inverse = tuple(-x/dark[0] for x in dark[1:])
            current = boost(current, inverse)
            muon = boost(row['muon_p4'], inverse)
            self.assertAlmostEqual(current[0], 0., delta=1.e-8)
            total += dot(unit(current[1:]), unit(muon[1:]))**2
        beta2 = 1-4*dipion.MUON_MASS_GEV**2/mass**2
        self.assertAlmostEqual(total/count, (1/3-beta2/5)/(1-beta2/3), delta=.012)

    def test_invalid_models_and_thresholds_fail(self):
        base = INPUTS[0]['dipion_model']
        for key, value in (('rho_width_gev', 0.), ('reference', ''), ('parent_mass_continuation', 'unknown'),
                           ('virtual_width_prescription', 'lepton-dependent')):
            model = dict(base, **{key: value})
            with self.assertRaises(ValueError):
                dipion.branching_coefficient(.547862, .22, model)
        for mass in (.2, .3):
            with self.assertRaises(ValueError):
                dipion.sample_decay((.547862, 0., 0., 0.), mass, 1., random.Random(1), model=base)


if __name__ == '__main__':
    unittest.main()
