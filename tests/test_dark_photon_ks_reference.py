# Code style: AI-code. AI-generated independent controls of published KS data.
"""Physics closure of the attributed arXiv:1609.03574 Figure 8 reference.

No source PDF or PDF parser is needed at runtime. Original vector coordinates
are checked against the paper's independently calculable chiral amplitude,
and the extracted dispersive amplitude against all four Table 2 integrals.
The latter are predictions, not measurements or a statistical error model.
"""
import bisect
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from dark_photon_kaon import KSDispersiveReference, normalized_dalitz_density


REFERENCE = Path(__file__).resolve().parents[1]/'config/physics/dark_photon_ks_dispersive_2016.json'
REFERENCE_SHA256 = '15b1330ed008b81b5af0c2b7824b33afacad4e9373838bc6761f6029af4abdd2'
MASS = .497611
PION_MASS = .13957039
ALPHA_EM = 1/137.035999084
HBAR_GEV_S = 6.582119569e-25
KS_LIFETIME_S = 89.562e-12  # KLOE arXiv:1011.2668; explicit width conversion.


def interpolate(points, q2):
    right = min(max(bisect.bisect_right([row[0] for row in points], q2), 1), len(points)-1)
    first, second = points[right-1:right+1]
    return first[1]+(q2-first[0])/(second[0]-first[0])*(second[1]-first[1])


def chiral_amplitude(q2):
    """Independent LO formula, primary Eqs22-25 at s=M_K^2.

    The kaon-loop subtraction's prefactor vanishes at this physical point.
    G8 and Fpi are the explicit numerical inputs below Eq19 in the paper.
    This LO expression validates coordinate extraction; it is not a fallback
    dispersive production model.
    """
    def f(a):
        if a == 0:
            return 0.
        if a <= 4:
            return 1-4/a*math.asin(math.sqrt(a)/2)**2
        speed = math.sqrt(1-4/a)
        return 1+(math.log((1-speed)/(1+speed))+1j*math.pi)**2/a

    def g(a):
        if a == 0:
            return 1.
        if a <= 4:
            return math.sqrt(4/a-1)*math.asin(math.sqrt(a)/2)
        speed = math.sqrt(1-4/a)
        return speed/2*(math.log((1+speed)/(1-speed))-1j*math.pi)

    s, pion2 = MASS**2, PION_MASS**2
    h = s**2/(2*(s-q2)**2)*(q2/s*f(q2/pion2)-f(s/pion2)
                              -2*q2/s*(g(q2/pion2)-g(s/pion2)))
    return 4*math.pi*ALPHA_EM*9.1e-6*.0922/math.pi**2*(1-pion2/s)*h


def integrate_dalitz(amplitude, minimum, maximum, lepton_mass):
    """Composite Simpson integration in log q2; resolve electron threshold."""
    steps = 20000
    low, high = math.log(minimum), math.log(maximum)
    step = (high-low)/steps

    def integrand(log_q2):
        q2 = math.exp(log_q2)
        if q2 <= minimum:
            return 0.
        lepton = math.sqrt(1-minimum/q2)*(1+2*lepton_mass**2/q2)
        # Primary Eq67, direct absolute width; independent of normalization
        # to a photon branching fraction or the normalized kernel helper.
        return (MASS**3/(32*math.pi)*(1-q2/MASS**2)**3*abs(amplitude(q2))**2
                *ALPHA_EM/(3*math.pi)*lepton*KS_LIFETIME_S/HBAR_GEV_S)

    total = integrand(low)+integrand(high)
    total += math.fsum((4 if index % 2 else 2)*integrand(low+index*step)
                       for index in range(1, steps))
    return total*step/3


class KSReferenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.table = json.loads(REFERENCE.read_text())
        cls.reference = KSDispersiveReference(REFERENCE, REFERENCE_SHA256)

    def test_pinned_input_and_domain_are_required(self):
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            KSDispersiveReference(REFERENCE, '0'*64)
        self.assertEqual(self.reference.form_factor(0.), 1.+0j)
        for mass in [-.1, .496, MASS, math.inf, math.nan]:
            with self.assertRaises(ValueError):
                self.reference.amplitude(mass)
        self.assertNotEqual(self.reference.form_factor(.4).imag, 0.)

    def test_every_published_band_contains_its_central_amplitude(self):
        for mass in [0., .22, .25, .3, .4, math.sqrt(self.reference.q2[-1])]:
            minimum, maximum = self.reference.amplitude_abs2_range(mass)
            self.assertLessEqual(minimum, abs(self.reference.amplitude(mass))**2)
            self.assertGreaterEqual(maximum, abs(self.reference.amplitude(mass))**2)

    def test_malformed_tables_fail_even_if_their_bytes_are_pinned(self):
        for change in ('order', 'band', 'units', 'figure'):
            table = deepcopy(self.table)
            if change == 'order':
                table['rows'][1][0] = table['rows'][0][0]
            elif change == 'band':
                table['rows'][0][3] = table['rows'][0][4]+1.
            elif change == 'units':
                table['units']['amplitude'] = 'arbitrary'
            else:
                table['source']['figure_sha256'] = '0'*64
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory)/'input.json'
                path.write_text(json.dumps(table))
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                with self.assertRaises(ValueError):
                    KSDispersiveReference(path, digest)

    def test_original_lo_coordinates_match_independent_chiral_formula(self):
        curves = self.table['independent_validation_curves']
        errors = []
        for index in range(301):
            q2 = .24*index/300
            actual = complex(interpolate(curves['real'], q2), interpolate(curves['imaginary'], q2))
            expected = chiral_amplitude(q2)
            errors.append(abs(actual-expected)/abs(expected))
        # Includes finite precision in the published PDF and physical input
        # rounding; no fitted normalization or amplitude has been applied.
        self.assertLess(max(errors), .002)

    def test_independent_electron_and_muon_integrals_close_table_two(self):
        curves = self.table['independent_validation_curves']
        lo = lambda q2: complex(interpolate(curves['real'], q2), interpolate(curves['imaginary'], q2))
        dispersive = lambda q2: self.reference.amplitude(math.sqrt(q2))
        cases = [(lo, .00051099895, 3.09e-8), (lo, .1056583755, 7.25e-10),
                 (dispersive, .00051099895, 4.38e-8), (dispersive, .1056583755, 1.45e-9)]
        for curve, lepton, expected in cases:
            actual = integrate_dalitz(curve, 4*lepton**2, self.reference.q2[-1], lepton)
            self.assertLess(abs(actual/expected-1.), .002)

    def test_absolute_and_normalized_widths_agree_without_rescaling(self):
        from dark_photon_kaon import radiative_branching
        gamma_gamma = self.reference.gamma_gamma_width(MASS)
        # This q2=0 result belongs to the Figure8 calculation. Its inputs
        # differ from the GMM calculation in Table1; do not force agreement
        # by a hidden normalization to Table1 or a measured photon rate.
        self.assertAlmostEqual(gamma_gamma*KS_LIFETIME_S/HBAR_GEV_S/2.6926e-6, 1., places=4)
        for mass in [.22, .25, .3, .4]:
            epsilon = 1.e-3
            from_normalized = gamma_gamma*radiative_branching(310, MASS, mass, epsilon,
                                                             1., self.reference.form_factor(mass))
            actual = self.reference.radiative_width(MASS, mass, epsilon)
            self.assertAlmostEqual(actual/from_normalized, 1., places=13)
            low, high = self.reference.radiative_width(MASS, mass, epsilon, band=True)
            self.assertLessEqual(low, actual)
            self.assertGreaterEqual(high, actual)
            self.assertAlmostEqual(self.reference.radiative_width(MASS, mass, 2*epsilon)/actual, 4.)
            density = normalized_dalitz_density(MASS, mass, .1056583755, ALPHA_EM,
                                                self.reference.form_factor(mass))
            self.assertGreater(gamma_gamma*density, 0.)


if __name__ == '__main__':
    unittest.main()
