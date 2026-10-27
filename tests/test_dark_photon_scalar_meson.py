# Code style: AI-code. AI-generated independent published scalar-current controls.
from copy import deepcopy
import json
import math
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import dark_photon_scalar_meson as scalar
from dark_photon_dipion_meson import _gauss_legendre


CARD = Path(__file__).resolve().parents[1]/'config/physics/dark_photon_scalar_meson_2020.json'
_INTEGRATION_NODES = _gauss_legendre(512)


def endpoint_integral(function, low, high):
    # Endpoint-log/square-root singularities are integrable. A sin² mapping
    # removes the leading endpoint cusp without sampling a divergent point.
    result = []
    for node, weight in _INTEGRATION_NODES:
        angle = (node+1)*math.pi/4
        x = low+(high-low)*math.sin(angle)**2
        jacobian = (high-low)*math.sin(2*angle)*math.pi/4
        result.append(weight*jacobian*function(x))
    return math.fsum(result)


def model(parent=221, recoil=111, kind=scalar.SIGMA_KIND):
    reference = json.loads(CARD.read_text())
    result = deepcopy(reference['reference_parameters'])
    result.update(kind=kind, parent_pdg_id=parent, recoil_pdg_id=recoil,
                  parent_mass_gev=reference['pseudoscalars'][str(parent)]['mass_gev'],
                  recoil_mass_gev=reference['pseudoscalars'][str(recoil)]['mass_gev'])
    return result


def parameter_bubble(s, first, second):
    """Independent log Feynman-parameter integral with its cut split exactly."""
    a, b = first*first, second*second
    roots = []
    if s:
        discriminant = (s-a-b)**2-4*a*b
        if discriminant > 0:
            roots = [(s+b-a+sign*math.sqrt(discriminant))/(2*s) for sign in (-1, 1)]
    points = sorted([0., *[root for root in roots if 0 < root < 1], 1.])
    def denominator(x):
        return a*x+b*(1-x)-s*x*(1-x)
    real = -math.fsum(endpoint_integral(lambda x: math.log(abs(denominator(x))/(first*second)),
                                       start, end)
                     for start, end in zip(points, points[1:]))
    negative_length = math.fsum(end-start for start, end in zip(points, points[1:])
                                if denominator((start+end)/2) < 0)
    return complex(real, -math.pi*negative_length)


def photon_loop(z):
    """Eq2 itself, independently of the off-shell electromagnetic kernel."""
    if z < 1e-3:
        return 1/24+z/180+z*z/1120+z**3/6300
    if z <= 4:
        f = -math.asin(math.sqrt(z)/2)**2
    else:
        beta = math.sqrt(1-4/z)
        f = .25*(math.log((1+beta)/(1-beta))-1j*math.pi)**2
    return -1/(2*z)-2/z**2*f


def photon_width(current):
    """Independent one-dimensional identical-photon phase space, in eV."""
    parent, recoil = current['parent_mass_gev'], current['recoil_mass_gev']
    upper = (parent-recoil)**2
    def density(s):
        coefficient = 2*current['alpha_em']/math.pi*sum(
            photon_loop(s/mass**2)*amplitude/mass**2
            for mass, amplitude in scalar.scalar_strong_amplitudes(s, current))
        kallen = math.sqrt(max(0., (upper-s)*((parent+recoil)**2-s)))
        # Sum|T1|²=s²/2. The 1/2! for identical photons is included.
        return 1e9*kallen*s*s*abs(coefficient)**2/(1024*math.pi**3*parent**3)
    points = [0., *[x for x in scalar.scalar_thresholds(current) if 0 < x < upper], upper]
    return math.fsum(endpoint_integral(density, start, end)
                     for start, end in zip(points, points[1:]))


class ScalarSelfEnergyTest(unittest.TestCase):
    def test_equal_mass_bubble_matches_independent_parameter_integral(self):
        mass = .13957039
        for invariant in (0., 1e-12, .03, .077, .079, .2, 1.):
            expected = parameter_bubble(invariant, mass, mass)
            self.assertLess(abs(scalar._bubble(invariant, mass, mass)-expected), 2e-10)

    def test_unequal_mass_all_three_branches_match_parameter_integral(self):
        first, second = .547862, .13957039
        for invariant in (0., 1e-10, .01, .10, .20, .40, .50, 1.1):
            expected = parameter_bubble(invariant, first, second)
            self.assertLess(abs(scalar._bubble(invariant, first, second)-expected), 2e-9)
            self.assertEqual(scalar._bubble(invariant, first, second),
                             scalar._bubble(invariant, second, first))

    def test_threshold_values_are_continuous_and_have_physical_cut_sign(self):
        for first, second in ((.14, .14), (.14, .55)):
            for threshold in ((first-second)**2, (first+second)**2):
                if threshold:
                    below = scalar._bubble(threshold*(1-1e-10), first, second)
                    above = scalar._bubble(threshold*(1+1e-10), first, second)
                    at = scalar._bubble(threshold, first, second)
                    self.assertLess(abs(above-at), 5e-5)
                    self.assertLess(abs(below-at), 5e-5)
            self.assertEqual(scalar._bubble(.9*(first+second)**2, first, second).imag, 0.)
            self.assertLess(scalar._bubble(1.1*(first+second)**2, first, second).imag, 0.)

    def test_on_shell_subtraction_and_total_isospin_width(self):
        current = model()
        for resonance in ('sigma', 'f0', 'a0'):
            mass = current['scalar_masses_gev'][resonance]
            actual = scalar.scalar_propagator(mass*mass, resonance, current)
            self.assertAlmostEqual(actual.real, 0., places=13)
            self.assertLess(actual.imag, 0.)
        # Independent optical-theorem check of sigma -> pi pi, including
        # charged+neutral isospin multiplicity3/2. KK is closed at this mass.
        pion, mass = current['pion_mass_gev'], current['scalar_masses_gev']['sigma']
        coupling2 = 1.5*((pion*pion-mass*mass)/current['f_pi_gev']
                        *math.cos(math.radians(current['phi_s_deg'])))**2
        expected = -coupling2/(16*math.pi)*math.sqrt(1-4*pion*pion/(mass*mass))
        self.assertAlmostEqual(scalar.scalar_propagator(mass*mass, 'sigma', current).imag,
                               expected, places=13)


class StrongCurrentTest(unittest.TestCase):
    def test_primary_photon_component_widths_without_measured_rate_rescaling(self):
        reference = json.loads(CARD.read_text())
        for parent, recoil in ((221, 111), (331, 111), (331, 221)):
            for kind, label in ((scalar.CHPT_KIND, 'chpt'), (scalar.SIGMA_KIND, 'linear_sigma')):
                target = reference['published_component_widths_ev'][f'{parent}_to_{recoil}'][label]
                # TableII has only one/two significant figures; the primary
                # does not print its full numerical pseudoscalar mass card.
                # Our explicit physical charged/neutral convention is tested
                # against it at8%, without silently adjusting masses or rates.
                self.assertLess(abs(photon_width(model(parent, recoil, kind))/target-1), .08)

    def test_published_matched_chiral_limit_at_tree_level(self):
        # The primary's matching statement concerns infinite tree scalar
        # masses before loop resummation. Sending a resummed mass to infinity
        # at fixed f is not this limit (the self-energy grows as mass^4).
        for parent, recoil in ((221,111), (331,111), (331,221)):
            current = model(parent, recoil)
            current['f_k_gev'] = current['f_pi_gev']
            current['scalar_masses_gev'] = dict(sigma=1e5, f0=1e5, a0=1e5)
            chiral = dict(current, kind=scalar.CHPT_KIND)
            with patch.object(scalar, 'scalar_propagator',
                              side_effect=lambda s, name, m: s-m['scalar_masses_gev'][name]**2):
                for invariant in (.02, .1):
                    actual = scalar.scalar_strong_amplitudes(invariant, current)
                    expected = scalar.scalar_strong_amplitudes(invariant, chiral)
                    for (mass, value), (other_mass, target) in zip(actual, expected):
                        self.assertEqual(mass, other_mass)
                        self.assertLess(abs(value-target), 2e-8)

    def test_threshold_inventory_covers_pion_kaon_and_a0_cuts(self):
        current = model(331,221)
        thresholds = scalar.scalar_thresholds(current)
        self.assertIn(4*current['pion_mass_gev']**2, thresholds)
        self.assertIn(4*current['kaon_mass_gev']**2, thresholds)
        self.assertIn((current['eta_mass_gev']+current['pion_mass_gev'])**2, thresholds)
        self.assertIn((current['eta_mass_gev']-current['pion_mass_gev'])**2, thresholds)

    def test_each_reference_card_is_explicit_and_validated(self):
        for parent, recoil in ((221,111), (331,111), (331,221)):
            self.assertEqual(scalar.validate_scalar_model(model(parent,recoil)), model(parent,recoil))
        for field, value in (('isospin_limit',False), ('alpha_em',float('nan')),
                             ('crossed_scalar_prescription','constant-width'),
                             ('parent_mass_gev',.8)):
            current = model();current[field] = value
            with self.assertRaises(ValueError):
                scalar.validate_scalar_model(current)
        for invariant in (-.1, float('nan')):
            with self.assertRaises(ValueError):
                scalar.scalar_strong_amplitudes(invariant, model())


if __name__ == '__main__':
    unittest.main()
