# Code style: AI-code. AI-generated controls of conditional kaon rate bounds.
import math
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from dark_photon_kaon import (KSDispersiveReference, amplitude_rate_matrix,
    conditional_event_ceiling, conditional_grid_rate_ceiling,
    cp_diagonal_rate_ceiling, cpt_mixing_decay_lower_factor, cpt_mixing_decay_matrix,
    positive_operator_rate_ceiling)


AUTHORITY = 'Synthetic exact two-state amplitudes and total rates; test only'


def rotate(matrix, cosine=.6, sine=.8):
    unitary = ((cosine, 1j*sine), (1j*sine, cosine))
    result = tuple(tuple(sum(unitary[a][i]*matrix[i][j]*unitary[b][j].conjugate()
                             for i in range(2) for j in range(2))
                         for b in range(2)) for a in range(2))
    # Store the same exact Hermitian convention as the production interface.
    return ((result[0][0].real, result[0][1]),
            (result[0][1].conjugate(), result[1][1].real))


def toy_yield(mixing, absorption=0., steps=12000):
    """RK4 no-jump evolution with a separately integrated signal current.

    Gamma_decay=diag(5,1), Gamma_signal=diag(1,.001). Material mixing
    couples the two states; absorption removes trace without adding kaons.
    The initial pure second state has a vacuum signal probability .001.
    """
    step = 24./steps
    state = (0j, 1.+0j, 0.)

    def derivative(value):
        first, second, _ = value
        return (-.5*(5+absorption)*first-1j*mixing*second,
                -.5*(1+absorption)*second-1j*mixing*first,
                abs(first)**2+.001*abs(second)**2)

    for _ in range(steps):
        one = derivative(state)
        two = derivative(tuple(x+.5*step*y for x, y in zip(state, one)))
        three = derivative(tuple(x+.5*step*y for x, y in zip(state, two)))
        four = derivative(tuple(x+step*y for x, y in zip(state, three)))
        state = tuple(x+step/6*(a+2*b+2*c+d)
                      for x, a, b, c, d in zip(state, one, two, three, four))
    return state[2], abs(state[0])**2+abs(state[1])**2


class KaonCeilingTest(unittest.TestCase):
    def test_diagonal_result_is_the_largest_branching_ratio(self):
        for widths in [(5., 1.), (1.e-14, 2.e-17), (1., 100.)]:
            for short, long in [(.2, .001), (.01, .3), (0., 0.)]:
                signal = ((short*widths[0], 0.), (0., long*widths[1]))
                total = ((widths[0], 0.), (0., widths[1]))
                result = positive_operator_rate_ceiling(signal, total, authority=AUTHORITY)
                self.assertAlmostEqual(result, cp_diagonal_rate_ceiling(short, long))

    def test_complex_congruence_preserves_the_bound(self):
        signal = ((.03, .001+.002j), (.001-.002j, .004))
        total = ((5., .03-.04j), (.03+.04j, .2))
        expected = positive_operator_rate_ceiling(signal, total, authority=AUTHORITY)
        moved = positive_operator_rate_ceiling(rotate(signal), rotate(total), authority=AUTHORITY)
        self.assertAlmostEqual(moved/expected, 1., places=13)

    def test_nondiagonal_amplitudes_need_the_operator_eigenvalue(self):
        signal = amplitude_rate_matrix([(1., .3+.4j)], authority=AUTHORITY)
        total = ((1., 0.), (0., 1.))
        bound = positive_operator_rate_ceiling(signal, total, authority=AUTHORITY)
        self.assertAlmostEqual(bound, 1.25)
        self.assertGreater(bound, max(signal[0][0], signal[1][1]))

    def test_all_normalized_states_obey_the_original_matrix_inequality(self):
        signal = ((.03, .001+.002j), (.001-.002j, .004))
        total = ((5., .03-.04j), (.03+.04j, .2))
        bound = positive_operator_rate_ceiling(signal, total, authority=AUTHORITY)
        for index in range(101):
            theta, phi = math.pi*index/200, math.pi*index*.037
            psi = (math.cos(theta), math.sin(theta)*complex(math.cos(phi), math.sin(phi)))
            numerator = sum(psi[i].conjugate()*signal[i][j]*psi[j] for i in range(2) for j in range(2)).real
            denominator = sum(psi[i].conjugate()*total[i][j]*psi[j] for i in range(2) for j in range(2)).real
            self.assertLessEqual(numerator/denominator, bound*(1+1.e-14))

    def test_missing_authority_and_unphysical_matrices_fail(self):
        unit = ((1., 0.), (0., 1.))
        with self.assertRaises(ValueError):
            positive_operator_rate_ceiling(unit, unit, authority='')
        for bad in [((1., 2.), (2., 1.)), ((1., 1j), (1j, 1.)),
                    ((-1., 0.), (0., 1.)), ((math.inf, 0.), (0., 1.))]:
            with self.assertRaises(ValueError):
                positive_operator_rate_ceiling(bad, unit, authority=AUTHORITY)
        for singular in [((1., 0.), (0., 0.)), ((1., 1.), (1., 1.))]:
            with self.assertRaises(ValueError):
                positive_operator_rate_ceiling(unit, singular, authority=AUTHORITY)

    def test_vacuum_is_not_an_upper_bound_but_operator_ceiling_survives_mixing(self):
        vacuum, _ = toy_yield(0.)
        mixed, _ = toy_yield(4.)
        absorbed, _ = toy_yield(4., absorption=2.)
        ceiling = positive_operator_rate_ceiling(((1., 0.), (0., .001)),
                                                 ((5., 0.), (0., 1.)), authority=AUTHORITY)
        self.assertAlmostEqual(vacuum/.001, 1., places=9)
        self.assertGreater(mixed, 20*vacuum)
        self.assertLess(absorbed, mixed)
        for value in (vacuum, mixed, absorbed):
            self.assertLess(value, ceiling)

    def test_cpt_mixing_keeps_physical_widths_but_changes_the_cp_basis_operator(self):
        self.assertEqual(cpt_mixing_decay_matrix(5., 1., 2., 0.), ((5., 0j), (-0j, 1.)))
        result = cpt_mixing_decay_matrix(5., 1., 2., .01+.02j)
        self.assertNotEqual(result[0][1], 0j)
        self.assertAlmostEqual(result[0][0]+result[1][1], 6.)
        for epsilon in [1., math.nan, .9]:
            with self.assertRaises(ValueError):
                cpt_mixing_decay_matrix(5., 1., 2., epsilon)

    def test_uniform_indirect_mixing_budget_bounds_all_tested_phases(self):
        short, long, delta_mass, radius = 1/89.562e-12, 1/50.92e-9, 5.270e9, .0024
        factor = cpt_mixing_decay_lower_factor(short, long, delta_mass, radius)
        self.assertGreater(factor, .9)
        self.assertLess(factor, 1.)
        signal = ((.000001*short, 0.), (0., .0001*long))
        reference_bound = positive_operator_rate_ceiling(signal, ((short, 0.), (0., long)), authority=AUTHORITY)
        for index in range(100):
            phase = 2*math.pi*index/100
            epsilon = radius*complex(math.cos(phase), math.sin(phase))
            total = cpt_mixing_decay_matrix(short, long, delta_mass, epsilon)
            actual = positive_operator_rate_ceiling(signal, total, authority=AUTHORITY)
            self.assertLessEqual(actual, reference_bound/factor)

    def test_source_luminosity_and_probability_units_are_explicit(self):
        self.assertEqual(conditional_event_ceiling(2., 3., .01, .3), 18.)
        for probability in [-.1, 1.1, math.nan]:
            with self.assertRaises(ValueError):
                conditional_event_ceiling(2., 3., probability, .3)


class GridRegenerationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ks = KSDispersiveReference(Path(__file__).resolve().parents[1]/
            'config/physics/dark_photon_ks_dispersive_2016.json',
            '15b1330ed008b81b5af0c2b7824b33afacad4e9373838bc6761f6029af4abdd2')

    def setUp(self):
        self.inventory = dict(source_class='SoftQCD:nonDiffractive',
            source_sampling_verified=True, resolved_source_configuration=True,
            collision_exposure=1000, native=dict(sigma_source_mb=1.),
            source_configuration=dict(check_event=True,mass_tolerance=.001,energy_tolerance=.01),
            resolved_beams=dict(eA_gev=1.,eB_gev=100.),
            resolved_lifetime_settings=dict(limitTau0=True,tau0Max=10.),
            particle_data=[dict(pdg_id=130,mass_gev=.497611,mass_min_gev=.49761,
                may_decay=True,mean_proper_lifetime_mm=15330.),
                dict(pdg_id=310,mass_gev=.497611,mass_min_gev=.49761,
                may_decay=True,mean_proper_lifetime_mm=26.842)],
            species=[dict(pdg_id=pdg,mean_parents_per_collision=.8,
                sum_collision_count_squared=1200) for pdg in (130,310)])
        self.points = [dict(point='test_prompt',mass_gev=.22,epsilon=.001,
            proper_ctau_mm=.000262181684591659,target_mean_lab_flight_m=None,prompt=True),
            dict(point='test_lab5m',mass_gev=.22,epsilon=1.e-6,
            proper_ctau_mm=262.181684591659,target_mean_lab_flight_m=5.,prompt=False)]
        self.inputs = dict(authority=AUTHORITY,kl_br_gamma_gamma=.0005471,
            kl_shape_candidates=[dict(parent_pdg_id=130,kind='kl-dip-single-virtual',
                alpha_dip=-1.729,rho_mass_gev=.77526,valid_mass_range_gev=[0.,.497611],
                reference='https://arxiv.org/pdf/hep-ex/0702039')])

    def calculate(self, **changes):
        return conditional_grid_rate_ceiling(changes.get('inventory',self.inventory),
            changes.get('points',self.points),self.ks,self.inputs,
            source_authority='Synthetic immutable source-count receipt, SHA test')

    def test_updated_census_reuses_the_physical_grid_and_joint_count_budget(self):
        result = self.calculate()
        self.assertEqual(len(result['rows']),2)
        for actual,original in zip(result['rows'],self.points):
            self.assertEqual(actual['epsilon'],original['epsilon'])
            self.assertEqual(actual['proper_ctau_mm'],original['proper_ctau_mm'])
            counts = actual['source_count_conditional_event_ceiling']
            self.assertAlmostEqual(counts['0.3']/counts['0.03'],10.)
            self.assertAlmostEqual(counts['3.0']/counts['0.3'],10.)
        for bound in result['source_count_bounds'].values():
            self.assertEqual(bound['confidence'],.975)
            self.assertEqual(bound['statistical_bound']['method'],'empirical_bernstein')
        more = deepcopy(self.inventory)
        more['collision_exposure'] *= 10
        for row in more['species']:
            row['sum_collision_count_squared'] *= 10
        self.assertLess(self.calculate(inventory=more)['total_primary_kaon_mean_ceiling'],
                        result['total_primary_kaon_mean_ceiling'])

    def test_unverified_source_or_ambiguous_grid_cannot_regenerate(self):
        unknown = dict(self.inventory,source_sampling_verified=False)
        with self.assertRaises(ValueError):
            self.calculate(inventory=unknown)
        with self.assertRaises(ValueError):
            self.calculate(points=self.points+self.points)

    def test_closed_mass_and_operational_canaries_are_explicit(self):
        closed = dict(self.points[0],point='closed',mass_gev=.5)
        canary = dict(self.points[0],operational_canary=True)
        result = self.calculate(points=[closed,canary])
        self.assertEqual(len(result['rows']),1)
        self.assertEqual(result['rows'][0]['arbitrary_mixing_probability_ceiling'],0.)
        self.assertEqual(sum(result['rows'][0]['source_count_conditional_event_ceiling'].values()),0.)


if __name__ == '__main__':
    unittest.main()
