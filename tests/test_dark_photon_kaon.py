# Code style: AI-code. AI-generated neutral-kaon amplitude and rate controls.
import math
import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from dark_photon_kaon import (amplitude, normalized_dalitz_density, radiative_branching,
    transition_form_factor,native_source_mass_precision)
from dark_photon_meson import boost


PARENT_MASS = .497611
MUON_MASS = .1056583755


class NativeSourceFramePrecisionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture=json.loads((Path(__file__).parent/'fixtures/kaon_native_source_frame_20261027.json').read_text())

    def test_failed_and_successful_native_records_use_the_actual_source_frame(self):
        for example in self.fixture['examples']:
            context=example['context'];record=context['native_source_record'];before=copy.deepcopy(context)
            result=native_source_mass_precision(example['kinematic_mass_gev'],context,
                expected_p4=record['p4'],expected_pdg_id=130)
            self.assertLessEqual(abs(result['kinematic_mass_gev']-record['native_mass_gev']),result['mass_allowance_gev'])
            self.assertLess(result['mass_allowance_gev'],1.e-8)
            self.assertEqual(result['native_mass_gev'],record['native_mass_gev'])
            self.assertEqual(context,before)
            if record['index']==50:
                self.assertFalse(example['old_local_energy_guard_passed'])
                self.assertLess(record['p4'][0],2.)
                self.assertGreater(record['source_com_energy_gev'],20.)
                self.assertGreater(result['source_boost_scale_gev'],2500.)

    def test_only_masses_from_the_immutable_record_are_accepted(self):
        for example in self.fixture['examples']:
            context=example['context'];p=context['native_source_record']['p4']
            values=[context['native_source_record']['native_mass_gev'],
                math.sqrt(math.fsum([p[0]**2]+[-x*x for x in p[1:]])),
                math.sqrt(math.fsum([p[0]**2]+[-x**2 for x in p[1:]])),
                math.sqrt(math.fsum([p[0]*p[0]]+[-x*x for x in p[1:]]))]
            for mass in values:native_source_mass_precision(mass,context)
            with self.assertRaisesRegex(ValueError,'immutable'):
                native_source_mass_precision(example['kinematic_mass_gev']+1.e-11,context)

    def test_changed_momentum_or_source_certificate_is_rejected(self):
        example=self.fixture['examples'][1];original=example['context'];mass=example['kinematic_mass_gev']
        for field in ('p4','source_total_p4'):
            changed=copy.deepcopy(original);changed['native_source_record'][field][0]+=.01
            with self.assertRaises(ValueError):native_source_mass_precision(mass,changed)
        changed=copy.deepcopy(original);changed['authority']['independent_com_energy_interval_gev'][1]+=.01
        with self.assertRaises(ValueError):native_source_mass_precision(mass,changed)
        changed=copy.deepcopy(original);changed['source_ecm_gev']*=1.001
        with self.assertRaises(ValueError):native_source_mass_precision(mass,changed)
        with self.assertRaises(ValueError):native_source_mass_precision(mass,original,expected_pdg_id=310)
        p=list(original['native_source_record']['p4']);p[0]+=1.e-10
        with self.assertRaisesRegex(ValueError,'momentum'):
            native_source_mass_precision(mass,original,expected_p4=p)

    def test_missing_context_and_continuous_native_mass_support_are_rejected(self):
        example=self.fixture['examples'][1];context=example['context'];mass=example['kinematic_mass_gev']
        for key in ('native_source_record','authority','source_ecm_gev','native_record_mass_support_gev'):
            changed=copy.deepcopy(context);changed.pop(key)
            with self.assertRaises(ValueError):native_source_mass_precision(mass,changed)
        changed=copy.deepcopy(context);changed['native_source_record']['native_mass_gev']+=.001
        with self.assertRaisesRegex(ValueError,'support'):
            native_source_mass_precision(mass,changed)


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

    def test_cp_interference_vanishes_at_fixed_muon_momenta_for_vector_current(self):
        # At fixed observed muon momenta the unpolarized vector-current trace
        # is symmetric in its two Lorentz indices. Summing the unobserved real
        # photon polarization contracts it with an antisymmetric CP-even/odd
        # cross tensor, so the interference vanishes before any angular or
        # lifetime integration. This test retains finite muon mass and phases.
        def dot(first, second):
            return first[0]*second[0]-sum(a*b for a, b in zip(first[1:], second[1:]))

        for mass in [.22, .3, .4]:
            photon, dark, transverse, longitudinal = rest_vectors(mass)
            muon_energy = mass/2
            muon_momentum = math.sqrt(muon_energy**2-MUON_MASS**2)
            for cosine, phi in [(-.8, .3), (-.2, 1.2), (.4, 2.1), (.9, .7)]:
                direction = (math.sqrt(1-cosine**2)*math.cos(phi),
                             math.sqrt(1-cosine**2)*math.sin(phi), cosine)
                minus = (muon_energy,)+tuple(muon_momentum*x for x in direction)
                plus = (muon_energy,)+tuple(-muon_momentum*x for x in direction)
                pair_beta = tuple(x/dark[0] for x in dark[1:])
                minus, plus = boost(minus, pair_beta), boost(plus, pair_beta)
                for beta in [(0., 0., 0.), (.2, -.3, .7)]:
                    k, q = boost(photon, beta), boost(dark, beta)
                    mu_minus, mu_plus = boost(minus, beta), boost(plus, beta)
                    photons = [boost(e, beta) for e in transverse]
                    darks = [boost(e, beta) for e in transverse+[longitudinal]]
                    trace = [[4*(dot(b, mu_minus)*dot(c, mu_plus)
                                 +dot(b, mu_plus)*dot(c, mu_minus)
                                 -dot(b, c)*(dot(mu_minus, mu_plus)+MUON_MASS**2))
                              for c in darks] for b in darks]
                    interference, scale = 0j, 0.
                    for a in photons:
                        even = [amplitude(310, k, q, a, b, 1+2j) for b in darks]
                        odd = [amplitude(130, k, q, a, b, 3-4j) for b in darks]
                        for b in range(3):
                            for c in range(3):
                                term = even[b]*odd[c].conjugate()*trace[b][c]
                                interference += term
                                scale += abs(term)
                    self.assertGreater(scale, 0.)
                    self.assertLess(abs(interference)/scale, 1.e-11)

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
