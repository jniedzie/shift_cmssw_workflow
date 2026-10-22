# Code style: AI-code. AI-generated neutral-kaon amplitude and rate controls.
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from dark_photon_kaon import amplitude, normalized_dalitz_density, radiative_branching, transition_form_factor
from dark_photon_meson import boost


PARENT_MASS = .497611
MUON_MASS = .1056583755


def dip_model():
    return dict(parent_pdg_id=130, kind='kl-dip-single-virtual',
                alpha_dip=-1.729, rho_mass_gev=.77526,
                valid_mass_range_gev=[0., PARENT_MASS],
                reference='https://arxiv.org/pdf/hep-ex/0702039#page=1')


def bms_model():
    return dict(dip_model(), kind='kl-bms-single-virtual',
                c_alpha_kstar=-.517, omega_mass_gev=.78266,
                phi_mass_gev=1.01946, kstar_mass_gev=.89166)


def rest_vectors(mass):
    momentum = (PARENT_MASS**2-mass**2)/(2*PARENT_MASS)
    energy = (PARENT_MASS**2+mass**2)/(2*PARENT_MASS)
    photon = (momentum, 0., 0., momentum)
    dark = (energy, 0., 0., -momentum)
    transverse = [(0., 1., 0., 0.), (0., 0., 1., 0.)]
    longitudinal = (momentum/mass, 0., 0., -energy/mass) if mass else None
    return photon, dark, transverse, longitudinal


def polarization_sum(parent_pdg_id, mass):
    photon, dark, transverse, longitudinal = rest_vectors(mass)
    polarizations = transverse+[longitudinal] if longitudinal else transverse
    return sum(abs(amplitude(parent_pdg_id, photon, dark, first, second))**2
               for first in transverse for second in polarizations)


class NeutralKaonTest(unittest.TestCase):
    def test_both_cp_tensors_obey_both_ward_identities_under_boosts(self):
        for mass in [.22, .3, .4]:
            photon, dark, transverse, _ = rest_vectors(mass)
            for beta in [(0., 0., 0.), (.2, -.3, .4), (0., 0., -.99)]:
                k, q = boost(photon, beta), boost(dark, beta)
                first, second = [boost(vector, beta) for vector in transverse]
                scale = abs(sum(a*b for a, b in zip(k, q)))
                for pdg in (130, 310):
                    self.assertLess(abs(amplitude(pdg, k, q, k, second)), 1.e-12*scale)
                    self.assertLess(abs(amplitude(pdg, k, q, first, q)), 1.e-12*scale)

    def test_separate_cp_amplitudes_are_lorentz_invariant(self):
        photon, dark, transverse, _ = rest_vectors(.3)
        beta = (.2, -.3, .7)
        for pdg in (130, 310):
            for first in transverse:
                for second in transverse:
                    original = amplitude(pdg, photon, dark, first, second, 1+2j)
                    moved = amplitude(pdg, boost(photon, beta), boost(dark, beta),
                                      boost(first, beta), boost(second, beta), 1+2j)
                    self.assertAlmostEqual(abs(original-moved), 0., places=13)

    def test_longitudinal_dark_production_vanishes_for_both_cp_tensors(self):
        for mass in [.001, .22, .3, .4, PARENT_MASS-1.e-6]:
            photon, dark, transverse, longitudinal = rest_vectors(mass)
            for pdg in (130, 310):
                for first in transverse:
                    self.assertAlmostEqual(abs(amplitude(pdg, photon, dark, first, longitudinal)), 0.)

    def test_cp_tensors_have_different_polarization_correlations(self):
        photon, dark, transverse, _ = rest_vectors(.3)
        first, second = transverse
        self.assertEqual(amplitude(130, photon, dark, first, first), 0.)
        self.assertNotEqual(amplitude(310, photon, dark, first, first), 0.)
        self.assertNotEqual(amplitude(130, photon, dark, first, second), 0.)
        self.assertEqual(amplitude(310, photon, dark, first, second), 0.)
        interference = sum(amplitude(130, photon, dark, a, b, 1+2j)
                           *amplitude(310, photon, dark, a, b, 3-4j).conjugate()
                           for a in transverse for b in transverse)
        self.assertEqual(interference, 0.)

    def test_spin_sum_and_identical_photon_phase_space_determine_branching(self):
        photon_momentum = PARENT_MASS/2
        for pdg in (130, 310):
            gamma_gamma = polarization_sum(pdg, 0.)
            for mass in [.22, .25, .3, .4, PARENT_MASS-1.e-5]:
                recoil = (PARENT_MASS**2-mass**2)/(2*PARENT_MASS)
                # Two-body width is p*sum|M|^2/(8*pi*M^2), with an extra
                # 1/2! for the two identical photons in the denominator.
                from_amplitude = 2*(recoil/photon_momentum)*polarization_sum(pdg, mass)/gamma_gamma
                from_rate = radiative_branching(pdg, PARENT_MASS, mass, .001, .1, 1.)/(.001**2*.1)
                self.assertAlmostEqual(from_amplitude/from_rate, 1., places=10)

    def test_dip_and_bms_use_separately_fitted_parameters_and_normalize(self):
        self.assertEqual(transition_form_factor(0., dip_model()), 1.)
        self.assertEqual(transition_form_factor(0., bms_model()), 1.)
        constant = dict(dip_model(), alpha_dip=0.)
        self.assertEqual(transition_form_factor(.4, constant), 1.)
        rho_only = dict(bms_model(), c_alpha_kstar=0.)
        self.assertAlmostEqual(transition_form_factor(.3, rho_only), 1/(1-.3**2/.77526**2))
        wrong_parameter = bms_model()
        wrong_parameter['alpha_kstar'] = wrong_parameter.pop('c_alpha_kstar')
        with self.assertRaises(ValueError):
            transition_form_factor(.3, wrong_parameter)

    def test_ks_does_not_silently_inherit_the_kl_form_factor(self):
        with self.assertRaisesRegex(ValueError, 'K_S requires'):
            transition_form_factor(.3, dict(dip_model(), parent_pdg_id=310))

    def test_form_factor_cannot_cross_its_declared_domain_or_vector_pole(self):
        for mass in [-.1, .6, math.inf, math.nan]:
            with self.assertRaises(ValueError):
                transition_form_factor(mass, dip_model())
        for change in [dict(valid_mass_range_gev=[0., .8]), dict(reference=''),
                       dict(alpha_dip=math.nan), dict(rho_mass_gev=-1.)]:
            with self.assertRaises(ValueError):
                transition_form_factor(.3, dict(dip_model(), **change))

    def test_rates_keep_complex_form_factor_and_epsilon_scaling(self):
        for pdg in (130, 310):
            first = radiative_branching(pdg, PARENT_MASS, .3, 1.e-6, .001, 1+2j)
            second = radiative_branching(pdg, PARENT_MASS, .3, 2.e-6, .001, 1+2j)
            self.assertAlmostEqual(second/first, 4.)
            real = radiative_branching(pdg, PARENT_MASS, .3, 1.e-6, .001, math.sqrt(5.))
            self.assertAlmostEqual(real/first, 1.)
            self.assertEqual(radiative_branching(pdg, PARENT_MASS, .5, 1.e-6, .001, 1.), 0.)

    def test_flavor_states_require_separate_mixing_and_source_treatment(self):
        for pdg in (311, -311, 221):
            with self.assertRaises(ValueError):
                radiative_branching(pdg, PARENT_MASS, .3, .001, .001, 1.)

    def test_dalitz_spectrum_has_the_physical_lepton_threshold(self):
        for mass in [.2, 2*MUON_MASS, PARENT_MASS, .6]:
            self.assertEqual(normalized_dalitz_density(PARENT_MASS, mass, MUON_MASS, 1/137., 1.), 0.)
        mass = .3
        muons = normalized_dalitz_density(PARENT_MASS, mass, MUON_MASS, 1/137., 1.)
        light = normalized_dalitz_density(PARENT_MASS, mass, .00051099895, 1/137., 1.)
        self.assertGreater(light, muons)
        self.assertGreater(muons, 0.)

    def test_dalitz_conversion_matches_narrow_vector_width_relation(self):
        mass, epsilon = .3, 1.e-4
        alpha_em = 1/137.035999084
        form_factor = transition_form_factor(mass, dip_model())
        pair_width = (alpha_em*epsilon**2*mass/3
                      *math.sqrt(1-4*MUON_MASS**2/mass**2)*(1+2*MUON_MASS**2/mass**2))
        production_ratio = radiative_branching(130, PARENT_MASS, mass, epsilon, 1., form_factor)
        continuum = normalized_dalitz_density(PARENT_MASS, mass, MUON_MASS, alpha_em, form_factor)
        # A narrow electromagnetically coupled vector replaces the virtual
        # propagator: its production factor times Gamma_mumu/(pi*m^3*eps^4)
        # must recover the photon-conversion density, with no extra BR_mumu.
        self.assertAlmostEqual(production_ratio*pair_width/(math.pi*mass**3*epsilon**4)/continuum, 1.)


if __name__ == '__main__':
    unittest.main()
