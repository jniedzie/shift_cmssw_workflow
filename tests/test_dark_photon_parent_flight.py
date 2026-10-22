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
