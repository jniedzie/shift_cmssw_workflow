# Code style: AI-code. AI-generated long-lived-parent flight and ownership tests.
import copy
import math
from pathlib import Path
import random
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from dark_photon_parent_flight import parent_flight, neutral_kaon_chain_ownership


class ParentFlightTest(unittest.TestCase):
    def flight(self, **options):
        values = dict(parent_p4=(math.hypot(3., .5), 0., 0., -3.), parent_mass_gev=.5,
            production_position_mm=(1., 2., 3., 4.), mean_proper_ctau_mm=15330.,
            mass_authority='frozen native particle table SHA-256',
            lifetime_authority='frozen measured lifetime authority', uniform=.5)
        values.update(options)
        return parent_flight(**values)

    def test_boosted_parent_flight_retains_its_nonzero_birth_and_time(self):
        row = self.flight()
        length = 15330.*math.log(2.)
        energy = math.hypot(3., .5)
        self.assertAlmostEqual(row['proper_flight_mm'], length)
        self.assertEqual(row['production_position_mm'], [1., 2., 3., 4.])
        self.assertAlmostEqual(row['decay_position_mm'][2], 3.-6.*length)
        self.assertAlmostEqual(row['decay_position_mm'][3], 4.+energy/.5*length)
        delta = row['displacement_mm']
        self.assertAlmostEqual((delta[3]**2-delta[2]**2)/length**2, 1., places=12)
        self.assertEqual(row['lifetime_method'], 'unconditional-exponential-vacuum-flight')

    def test_rest_parent_advances_time_without_spatial_flight(self):
        row = self.flight(parent_p4=(.5, 0., 0., 0.))
        self.assertEqual(row['decay_position_mm'][:3], [1., 2., 3.])
        self.assertAlmostEqual(row['displacement_mm'][3], row['proper_flight_mm'])

    def test_native_vertex_is_preserved_without_second_draw(self):
        sampled = self.flight()
        native = self.flight(uniform=None,
            native_decay_position_mm=sampled['decay_position_mm'])
        self.assertEqual(native['decay_position_mm'], sampled['decay_position_mm'])
        self.assertAlmostEqual(native['proper_flight_mm'], sampled['proper_flight_mm'])
        self.assertEqual(native['lifetime_method'], 'preserved-native-end-vertex')
        # Preserving a native vertex does not establish that its source had no
        # lifetime/volume censoring. That remains the caller's responsibility.
        self.assertIsNone(native['unconditional_new_flight'])
        self.assertTrue(sampled['unconditional_new_flight'])
        with self.assertRaises(ValueError):
            self.flight(native_decay_position_mm=sampled['decay_position_mm'])

    def test_native_vertex_must_follow_parent_momentum_and_future_time(self):
        sampled = self.flight()
        for index, step in ((0, 1.), (2, 10.), (3, -2.*sampled['displacement_mm'][3])):
            endpoint = list(sampled['decay_position_mm'])
            endpoint[index] += step
            with self.subTest(index=index), self.assertRaises(ValueError):
                self.flight(uniform=None, native_decay_position_mm=endpoint)

    def test_unconditional_sampling_has_full_exponential_support(self):
        self.assertEqual(self.flight(uniform=0.)['proper_flight_mm'], 0.)
        tail = self.flight(uniform=math.nextafter(1., 0.))
        self.assertGreater(tail['proper_flight_mm'], 30.*15330.)
        rng = random.Random(1761622001)
        lengths = [self.flight(uniform=rng.random())['proper_flight_mm']/15330.
                   for _ in range(20000)]
        self.assertLess(abs(math.fsum(lengths)/len(lengths)-1.), .025)
        self.assertLess(abs(sum(value < 1. for value in lengths)/len(lengths)-(1.-math.exp(-1.))), .015)

    def test_input_authorities_and_finite_timelike_momentum_are_required(self):
        invalid = [dict(uniform=None), dict(uniform=-.1), dict(uniform=1.),
            dict(uniform=float('nan')), dict(parent_p4=(1., 0., 0., 2.)),
            dict(parent_p4=(-1., 0., 0., 0.)), dict(parent_mass_gev=0.),
            dict(mean_proper_ctau_mm=0.), dict(mass_authority=''),
            dict(lifetime_authority=''), dict(production_position_mm=(0., 0., float('inf'), 0.))]
        for options in invalid:
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.flight(**options)

    def test_vacuum_result_cannot_be_mistaken_for_full_material_prediction(self):
        row = self.flight()
        for key in ('material_attenuation_modelled', 'regeneration_modelled',
                    'coherent_neutral_kaon_mixing_modelled', 'secondary_sources_modelled',
                    'physical_publication_ready', 'inclusive_signal_upper_bound'):
            self.assertFalse(row[key])

    def test_nonfinite_coordinate_difference_is_rejected(self):
        with self.assertRaises(ValueError):
            self.flight(uniform=None, production_position_mm=(0., 0., 0., -1.e308),
                native_decay_position_mm=(0., 0., 0., 1.e308))

    def test_final_native_kaon_reuses_sampled_tau_without_claiming_an_end_vertex(self):
        # Exact native KL regression from the October 22 unfiltered source
        # census: final status91, sampled tau/vDec, no HepMC end vertex.
        momentum = [216.27522913754743, -.3771051994787957,
                    -.5616255845161958, -216.2735986896288]
        origin = [-1.0894791012855905e-6, -1.6225693464937358e-6,
                  -.0006248271470609258, .0006248318575208599]
        endpoint = [-7354.24339840416, -10952.729511583824,
                    -4217732.048273567, 4217763.844999941]
        row = self.flight(parent_p4=momentum, parent_mass_gev=.497611,
            production_position_mm=origin, uniform=None,
            native_sampled_proper_flight_mm=9704.327641833348,
            native_stored_decay_position_mm=endpoint)
        self.assertEqual(row['proper_flight_mm'], 9704.327641833348)
        self.assertEqual(row['decay_position_mm'], endpoint)
        self.assertFalse(row['native_end_vertex_present'])
        self.assertIsNone(row['unconditional_new_flight'])
        self.assertEqual(row['maximum_native_coordinate_residual_mm'], 0.)
        self.assertLess(row['mass_shell_residual_gev2'], row['mass_shell_roundoff_gev2'])
        self.assertEqual(row['lifetime_method'], 'preserved-native-sampled-lifetime-without-end-vertex')

    def test_stored_native_flight_requires_both_values_and_forbids_resampling(self):
        row = self.flight()
        good = dict(uniform=None, native_sampled_proper_flight_mm=row['proper_flight_mm'],
            native_stored_decay_position_mm=row['decay_position_mm'])
        self.flight(**good)
        changes = [dict(uniform=.1), dict(native_sampled_proper_flight_mm=None),
            dict(native_sampled_proper_flight_mm=-1.), dict(native_stored_decay_position_mm=None),
            dict(native_decay_position_mm=row['decay_position_mm']),
            dict(native_sampled_proper_flight_mm=row['proper_flight_mm']+1.)]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.flight(**dict(good, **change))

    def test_mass_authority_must_close_or_carry_an_explicit_extra_budget(self):
        with self.assertRaises(ValueError):
            self.flight(parent_mass_gev=.6)
        row = self.flight(parent_mass_gev=.50001, mass_shell_relative_tolerance=.001)
        self.assertEqual(row['parent_mass_gev'], .50001)
        self.assertEqual(row['parent_p4'], [math.hypot(3., .5), 0., 0., -3.])
        self.assertGreater(row['mass_shell_residual_gev2'], row['mass_shell_roundoff_gev2'])
        for value in (-.1, 1., float('nan')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.flight(mass_shell_relative_tolerance=value)

    def test_coordinate_quantization_is_bounded_at_actual_large_source_scale(self):
        origin = [1.e12, -1.e12, 1.e12, 1.e12]
        sampled = self.flight(production_position_mm=origin)
        row = self.flight(production_position_mm=origin, uniform=None,
            native_sampled_proper_flight_mm=sampled['proper_flight_mm'],
            native_stored_decay_position_mm=sampled['decay_position_mm'])
        self.assertLessEqual(row['maximum_native_coordinate_residual_mm'],
                             row['native_coordinate_roundoff_mm'])
        changed = list(sampled['decay_position_mm'])
        changed[0] += 2.*row['native_coordinate_roundoff_mm']
        with self.assertRaises(ValueError):
            self.flight(production_position_mm=origin, uniform=None,
                native_sampled_proper_flight_mm=sampled['proper_flight_mm'],
                native_stored_decay_position_mm=changed)

    def test_extreme_finite_inputs_cannot_overflow_flight_accounting(self):
        with self.assertRaises(ValueError):
            self.flight(parent_p4=(1.e308, 0., 0., 0.))


class NeutralKaonOwnershipTest(unittest.TestCase):
    def particle(self, index, pdg, mothers=(), daughters=()):
        return dict(index=index, pdg_id=pdg,
            mother_indices=list(mothers), daughter_indices=list(daughters))

    def test_native_flavor_assignment_is_counted_once_without_second_half(self):
        particles = [self.particle(1, 311, daughters=(2,)),
                     self.particle(2, 130, mothers=(1,), daughters=(3,)),
                     self.particle(3, 130, mothers=(2,))]
        rows = neutral_kaon_chain_ownership(particles)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['parent_index'], 3)
        self.assertEqual(rows[0]['neutral_kaon_chain_indices'], [1, 2, 3])
        self.assertEqual(rows[0]['flavor_ancestor_indices'], [1])
        self.assertEqual(rows[0]['additional_eigenstate_weight'], 1.)

    def test_independent_kaons_from_one_other_meson_are_distinct(self):
        particles = [self.particle(1, 333, daughters=(2, 3)),
                     self.particle(2, 130, mothers=(1,)),
                     self.particle(3, 310, mothers=(1,))]
        self.assertEqual([row['parent_index'] for row in neutral_kaon_chain_ownership(particles)], [2, 3])

    def test_antiflavor_and_direct_eigenstates_are_supported(self):
        particles = [self.particle(1, -311, daughters=(2,)),
                     self.particle(2, 310, mothers=(1,)), self.particle(3, 130)]
        self.assertEqual(len(neutral_kaon_chain_ownership(particles)), 2)
        self.assertEqual(neutral_kaon_chain_ownership([self.particle(1, 311)]), [])

    def test_duplicate_ownership_or_state_change_requires_a_different_model(self):
        alternatives = [
            [self.particle(1, 311, daughters=(2, 3)),
             self.particle(2, 130, mothers=(1,)), self.particle(3, 310, mothers=(1,))],
            [self.particle(1, 130, daughters=(2,)), self.particle(2, 310, mothers=(1,))],
            [self.particle(1, 311, daughters=(3,)), self.particle(2, -311, daughters=(3,)),
             self.particle(3, 130, mothers=(1, 2))]]
        for particles in alternatives:
            with self.subTest(particles=particles), self.assertRaises(ValueError):
                neutral_kaon_chain_ownership(particles)

    def test_bad_or_cyclic_native_graph_is_rejected(self):
        good = [self.particle(1, 311, daughters=(2,)), self.particle(2, 130, mothers=(1,))]
        broken = copy.deepcopy(good)
        broken[0]['daughter_indices'] = []
        cycle = [self.particle(1, 130, mothers=(2,), daughters=(2,)),
                 self.particle(2, 130, mothers=(1,), daughters=(1,))]
        for particles in (good+good, broken, cycle):
            with self.subTest(particles=particles), self.assertRaises(ValueError):
                neutral_kaon_chain_ownership(particles)

    def test_malformed_native_ancestry_is_rejected(self):
        for invalid in ([[1]], [1, 1], [0], [True], None):
            row = self.particle(1, 130)
            row['mother_indices'] = invalid
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                neutral_kaon_chain_ownership([row])


if __name__ == '__main__':
    unittest.main()
