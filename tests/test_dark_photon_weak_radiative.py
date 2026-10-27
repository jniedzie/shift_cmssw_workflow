# Code style: AI-code. AI-generated weak-radiative current and spectral controls.
import json
import math
from pathlib import Path
import random
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from dark_photon_dipion_meson import _gauss_legendre
from dark_photon_meson import boost
from dark_photon_weak_radiative import (
    P6_KIND, _cut_moments, _g_prime, _density, _integrated_width, _sampling_segments, amplitude, branching_coefficient,
    decay_width, loop_functions, mean_lab_beta_gamma, polarization_density, reduced_loop_current,
    sample_decay, scalar_loop, vmd_form_factors)


CARD = Path(__file__).resolve().parents[1]/'config/physics/dark_photon_kl_pi0_gamma_chpt_1997.json'


def model():
    return json.loads(CARD.read_text())['form_factor_model']


def vmd_model():
    inputs = model();parent = inputs['parent_mass_gev']
    # Signed exchange parameters are the explicit benchmark of Eq.(41), not
    # an a_V fit to a real-photon branching fraction.
    return dict(inputs, kind='chpt-p4-plus-offshell-vmd-kl-pi0-photon-vector',
        vmd_reference='https://arxiv.org/pdf/hep-ph/9702278#page=10', full_p6_loop_model=False,
        vmd_exchange=[dict(mass_gev=.77526, coupling_gev_minus2=.68e-8/parent**2),
                      dict(mass_gev=.78265, coupling_gev_minus2=-.28e-7/parent**2)])


def p6_model():
    inputs = vmd_model();mass = inputs['parent_mass_gev']
    return dict(inputs, kind=P6_KIND, full_p6_loop_model=True,
        unitarity_model=dict(kind='kl-three-pion-polynomial-msbar',
            reference='https://arxiv.org/pdf/hep-ph/9702278#page=8',
            a1_gev_minus4=3.1e-6/mass**4, a2_gev_minus4=-1.26e-6/mass**4,
            renormalization_scale_gev=.77526, quadrature_order=48,
            unqualified_higher_operators=True))


class HigherOrderUnitarityTest(unittest.TestCase):
    def test_full_known_current_samples_an_unconditional_on_shell_decay_chain(self):
        current = p6_model();parent = (current['parent_mass_gev'],0.,0.,0.)
        event = sample_decay(parent,.22,7.,random.Random(521),model=current)
        self.assertTrue(event['unconditional_decay'])
        self.assertEqual(event['amplitude_model'],P6_KIND)
        final = [event['dark_photon_p4'],*event['recoil_p4s']]
        for i in range(4):
            self.assertAlmostEqual(math.fsum(row[i] for row in final),parent[i],places=12)
            self.assertAlmostEqual(event['muon_p4'][i]+event['antimuon_p4'][i],event['dark_photon_p4'][i],places=12)
        dark = event['dark_photon_p4']
        self.assertAlmostEqual(dark[0]**2-math.fsum(x*x for x in dark[1:]),.22**2,places=12)
        self.assertAlmostEqual(event['decay_ct_mm']/event['proper_decay_length_mm'],dark[0]/.22,places=12)

    def test_independent_higher_order_electron_and_muon_primary_controls(self):
        authority = json.loads(CARD.read_text())
        card = authority['higher_order_model'];constants = authority['explicit_conventions']
        conversion = constants['kaon_lifetime_seconds']/constants['hbar_gev_seconds']
        for lepton, target in [('electron',2.3e-8), ('muon',5.8e-11)]:
            # The primary analytic formulas have a shared r_pi convention.
            # Adopt an explicit common charged-pion reproduction convention;
            # the primary does not print its exact numerical input card.
            # Production retains the actual neutral-recoil mass.
            current = dict(card)
            if lepton == 'muon':
                current['recoil_pion_mass_gev'] = current['charged_loop_pion_mass_gev']
                current['valid_vector_mass_range_gev'] = [0.,current['parent_mass_gev']-current['recoil_pion_mass_gev']]
            ml = constants[lepton+'_mass_gev']
            edges = [math.log((2*ml)**2), math.log((2*current['charged_loop_pion_mass_gev'])**2),
                     math.log((current['parent_mass_gev']-current['recoil_pion_mass_gev'])**2)]
            integral = 0.
            for low, high in zip(edges,edges[1:]):
                for node, weight in _gauss_legendre(16):
                    q2 = math.exp((low+high)/2+(high-low)*node/2);ratio = ml**2/q2
                    spectral = current['alpha_em']/(3*math.pi)*math.sqrt(1-4*ratio)*(1+2*ratio)
                    integral += weight*(high-low)/2*spectral*decay_width(current,math.sqrt(q2),
                        epsilon=.001,quadrature_order=16)/1.e-6
            self.assertLess(abs(integral*conversion/target-1),.05)

    def test_analytic_feynman_moments_keep_the_exact_negative_i0_cut(self):
        a, change = .1, .3
        inverse, logs = _cut_moments(a, change, .77526)
        self.assertAlmostEqual(inverse[0].real, math.log(.5)/change, places=13)
        self.assertAlmostEqual(inverse[0].imag, math.pi/change, places=13)
        for n, value in enumerate(logs):
            self.assertAlmostEqual(value.imag, -math.pi*(1-(a/change)**(n+1))/(n+1), places=13)

    def test_compact_amplitude_matches_unreduced_dimensional_loop_tensors(self):
        from dark_photon_weak_radiative import _pair_frame
        card = p6_model();card['unitarity_model']['a1_gev_minus4'] = 0.
        card['vmd_exchange'] = [dict(row, coupling_gev_minus2=0.) for row in card['vmd_exchange']]
        # Independent direct integration of Eqs.(24)--(27), with dimensional
        # Gaussian tensor moments before compact A/B/D reduction. The pion cut
        # uses analytic inner-parameter integration and endpoint panels.
        references = [(.05,.1,complex(-.006264823132713232,0.)),
                      (.1,.22,complex(-.004036876714898746,.04324890214172304)),
                      (.12,.3,complex(-.016278775563534617,.010693954600110585))]
        coefficient = 1j*.001*card['unitarity_model']['a2_gev_minus4']*4*math.pi*card['alpha_em']/(4*math.pi)**2
        for invariant, mass, expected in references:
            parent, dark, photon, _ = _pair_frame(invariant, .3, mass, card)
            first, second = (0.,0.,1.,0.), (0.,0.,-1.,0.)
            full = amplitude(dark, photon, first, second, mass, .001, card, parent=parent)
            leading = amplitude(dark, photon, first, second, mass, .001, model(), parent=parent)
            self.assertLess(abs((full-leading)/coefficient-expected), 5.e-7)

    def test_known_loop_current_is_gauge_invariant_and_preserves_the_declared_scope(self):
        from dark_photon_weak_radiative import _pair_frame
        card = p6_model();mass = .3
        parent, dark, photon, _ = _pair_frame(.12, -.4, mass, card)
        first = (0.,1.,0.,0.);second = (0.,0.,1.,0.)
        self.assertLess(abs(amplitude(dark, photon, dark, first, mass, .001, card, parent=parent)), 1.e-20)
        self.assertLess(abs(amplitude(dark, photon, second, photon, mass, .001, card, parent=parent)), 1.e-20)
        broken = dict(card, unitarity_model=dict(card['unitarity_model'], unqualified_higher_operators=False))
        with self.assertRaisesRegex(ValueError, 'remaining operators'):
            decay_width(broken, mass, epsilon=.001, quadrature_order=16)

    def test_zero_polynomial_coefficients_return_the_unchanged_p4_plus_vmd_model(self):
        card = p6_model();card['unitarity_model'].update(a1_gev_minus4=0., a2_gev_minus4=0.)
        for mass in (.22, .3):
            self.assertAlmostEqual(decay_width(card, mass, epsilon=.001, quadrature_order=16)/
                decay_width(vmd_model(), mass, epsilon=.001, quadrature_order=16), 1., places=13)


class WeakRadiativeSourceTest(unittest.TestCase):
    def test_native_energy_allows_only_propagated_boosted_shell_roundoff(self):
        inputs = model();mass = inputs['parent_mass_gev']
        card = dict(weak_radiative_model=inputs, total_parent_width_gev=1.e-17)
        with self.assertRaisesRegex(ValueError, 'pole'):
            branching_coefficient(mass+1.e-7, .22, card)
        shifted = branching_coefficient(mass+1.e-7, .22, card, native_energy_gev=6800.)
        central = branching_coefficient(mass, .22, card)
        self.assertLess(abs(shifted/central-1), 1.e-4)
        with self.assertRaisesRegex(ValueError, 'pole'):
            branching_coefficient(mass+.001, .22, card, native_energy_gev=6800.)

    def test_branching_coefficient_keeps_parent_width_and_mixing_separate(self):
        inputs = model();parent = inputs['parent_mass_gev'];width = 6.582119569e-25/5.116e-8
        card = dict(weak_radiative_model=inputs, total_parent_width_gev=width)
        coefficient = branching_coefficient(parent, .22, card)
        self.assertAlmostEqual(coefficient*width*1.e-6/decay_width(inputs, .22, epsilon=.001), 1.)
        with self.assertRaises(ValueError):branching_coefficient(parent+.001, .22, card)
        with self.assertRaises(ValueError):branching_coefficient(parent, .22, dict(card, total_parent_width_gev=0.))
        changed = dict(inputs, pole_mass_numerical_tolerance_gev=.01)
        with self.assertRaises(ValueError):branching_coefficient(parent, .22, dict(card, weak_radiative_model=changed))

    def test_continuous_sampling_cdf_closes_against_independent_width_quadrature(self):
        for mass in (.22, .25, .3):
            inputs = model();key = json.dumps(inputs, sort_keys=True, separators=(',', ':'))
            segments, cumulative = _sampling_segments(mass, key)
            self.assertEqual(cumulative[0], 0.)
            self.assertTrue(all(b > a for a, b in zip(cumulative, cumulative[1:])))
            actual = cumulative[-1]/(512*math.pi**3*inputs['parent_mass_gev']**3)
            expected = decay_width(inputs, mass, epsilon=.001, quadrature_order=96)/1.e-6
            self.assertLess(abs(actual/expected-1), 2.e-5)
            self.assertGreater(len(segments), 20)

    def test_sampler_conserves_all_four_momenta_and_keeps_unconditional_flights(self):
        inputs = model();parent = inputs['parent_mass_gev']
        for mass in (.22, .25, .3):
            for index in range(8):
                event = sample_decay((parent, 0., 0., 0.), mass, 100., random.Random(index), model=inputs)
                vectors = [event['dark_photon_p4'], *event['recoil_p4s']]
                for coordinate in range(4):
                    self.assertAlmostEqual(sum(p[coordinate] for p in vectors), parent if coordinate == 0 else 0., places=13)
                    self.assertAlmostEqual(event['muon_p4'][coordinate]+event['antimuon_p4'][coordinate],
                                           event['dark_photon_p4'][coordinate], places=13)
                self.assertEqual(event['recoil_pdg_ids'], [111, 22])
                self.assertTrue(event['unconditional_decay'])
                self.assertEqual(event['parent_polarization'], 'spin-zero')
                proper = event['proper_decay_length_mm'];dark = event['dark_photon_p4']
                for displacement, momentum in zip(event['decay_displacement_mm'], dark[1:]):
                    self.assertAlmostEqual(displacement, proper*momentum/mass)
                self.assertAlmostEqual(event['decay_ct_mm'], proper*dark[0]/mass)
                self.assertGreater(proper, 0.)

    def test_transverse_dimuon_angular_density_and_rate_weighted_boost(self):
        inputs = model();parent = inputs['parent_mass_gev'];mass = .3;rng = random.Random(73214)
        samples = [sample_decay((parent, 0., 0., 0.), mass, 10., rng, model=inputs) for _ in range(600)]
        beta2 = 1-4*(.1056583755/mass)**2
        expected = (1/3-beta2/15)/(1-beta2/3)
        measured = sum(event['dimuon_helicity_cosine']**2 for event in samples)/len(samples)
        self.assertLess(abs(measured-expected), .035)
        measured_boost = sum(math.sqrt(sum(x*x for x in event['dark_photon_p4'][1:]))/mass
                             for event in samples)/len(samples)
        self.assertLess(abs(measured_boost/mean_lab_beta_gamma((parent, 0., 0., 0.), mass, inputs)-1), .05)
        self.assertEqual(len({event['photon_pair_invariant_gev2'] for event in samples}), len(samples))

    def test_boosted_parent_and_mass_guard(self):
        inputs = model();parent = inputs['parent_mass_gev']
        for momentum in (10., 100., 6800.):
            source = (math.hypot(parent, momentum), 0., 0., momentum)
            event = sample_decay(source, .25, 10., random.Random(38), model=inputs)
            # Propagate the cancellation in E²-p² and the subsequent boost;
            # this is a numerical error bound, not a kaon mass continuation.
            tolerance = 128*math.ulp(source[0]**2)*source[0]/parent**2
            for coordinate in range(4):
                total = event['dark_photon_p4'][coordinate]+sum(p[coordinate] for p in event['recoil_p4s'])
                self.assertLessEqual(abs(total-source[coordinate]), tolerance)
        with self.assertRaisesRegex(ValueError, 'pole'):
            sample_decay((parent+.001, 0., 0., 0.), .25, 10., random.Random(1), model=inputs)
        with self.assertRaises(ValueError):sample_decay((parent, 0., 0., 0.), .2, 10., random.Random(1), model=inputs)


class OffshellVectorExchangeTest(unittest.TestCase):
    def test_both_ward_identities_and_transported_exchange_current(self):
        inputs = vmd_model();parent = inputs['parent_mass_gev'];mass = .22
        from dark_photon_weak_radiative import _pair_frame
        kaon, dark, photon, _ = _pair_frame(.1, .35, mass, inputs)
        transverse = (0., 1., 0., 0.)
        original = amplitude(dark, photon, transverse, transverse, mass, .001, inputs, parent=kaon)
        for beta in ((0., 0., 0.), (.3, -.2, .1), (0., 0., -.95)):
            p, q, k, e = [boost(vector, beta) for vector in (kaon, dark, photon, transverse)]
            value = amplitude(q, k, e, e, mass, .001, inputs, parent=p)
            self.assertLess(abs(value/original-1), 1.e-10)
            self.assertLess(abs(amplitude(q, k, q, e, mass, .001, inputs, parent=p)/original), 1.e-10)
            self.assertLess(abs(amplitude(q, k, e, k, mass, .001, inputs, parent=p)/original), 1.e-10)

    def test_full_density_is_positive_and_vmd_has_a_longitudinal_component(self):
        for inputs in (model(), vmd_model()):
            density = polarization_density(.1, .35, .22, inputs)
            for i in range(3):
                self.assertGreaterEqual(density[i][i].real, 0.)
                for j in range(3):self.assertEqual(density[i][j], density[j][i].conjugate())
            for vector in ((1., 0., 0.), (1., -.3, .7), (.2, 1., -.1)):
                self.assertGreaterEqual(sum(vector[i]*density[i][j].real*vector[j] for i in range(3) for j in range(3)), 0.)
            if inputs['kind'] == model()['kind']:self.assertLess(abs(density[2][2]), 1.e-40)
            else:self.assertGreater(density[2][2].real, 0.)

    def test_zero_exchange_reduces_to_p4_and_attributed_exchange_preserves_epsilon_scaling(self):
        inputs = vmd_model();zero = dict(inputs, vmd_exchange=[dict(term, coupling_gev_minus2=0.) for term in inputs['vmd_exchange']])
        for mass in (0., .22, .3):
            leading = decay_width(model(), mass, epsilon=.001, quadrature_order=24)
            self.assertLess(abs(decay_width(zero, mass, epsilon=.001, quadrature_order=24)/leading-1), 1.e-12)
            low = decay_width(inputs, mass, epsilon=.001, quadrature_order=16)
            high = decay_width(inputs, mass, epsilon=.001, quadrature_order=32)
            self.assertLess(abs(low/high-1), .001)
            self.assertAlmostEqual(decay_width(inputs, mass, epsilon=.002, quadrature_order=16)/low, 4.)
        with self.assertRaises(ValueError):decay_width(dict(inputs, full_p6_loop_model=True), .22, epsilon=.001)

    def test_continuous_vmd_sampling_normalization_and_one_full_decay(self):
        inputs = vmd_model();mass = .22;key = json.dumps(inputs, sort_keys=True, separators=(',', ':'))
        _, cumulative = _sampling_segments(mass, key)
        actual = cumulative[-1]/(512*math.pi**3*inputs['parent_mass_gev']**3)
        expected = decay_width(inputs, mass, epsilon=.001, quadrature_order=48)/1.e-6
        self.assertLess(abs(actual/expected-1), .0001)
        event = sample_decay((inputs['parent_mass_gev'], 0., 0., 0.), mass, 10., random.Random(91), model=inputs)
        self.assertEqual(event['polarization_model'], 'full-massive-vector-density')
        self.assertEqual(event['amplitude_model'], inputs['kind'])


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
