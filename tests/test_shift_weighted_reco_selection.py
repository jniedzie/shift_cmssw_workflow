# Code style: AI-code.
"""Guard statistical support, protected paths and untouched control weights."""
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from shift_weighted_reco_selection import (GRID, fit_weight_scale,
    inclusion_probability, primary_probability)


class WeightedRecoSelectionTest(unittest.TestCase):
    def test_native_weight_over_probability_preserves_signed_expectation(self):
        weights = [0., 1.e-25, .01, 1., -2., 100.]
        for weight in weights:
            p = primary_probability(weight, 4., False, 10., .01)
            self.assertGreater(p, 0)
            self.assertEqual(p * GRID, int(p * GRID))
            self.assertTrue(math.isclose(p * (weight / p), weight,
                                         rel_tol=1.e-14, abs_tol=0.))
        self.assertEqual(primary_probability(1., 4., False, 10.),
                         primary_probability(-1., 4., False, 10.))

    def test_unknown_cannot_be_sacrificed_to_meet_budget(self):
        rows = [dict(native_weight=1.e-20, estimated_seconds=1., protected_unknown=True),
                dict(native_weight=1., estimated_seconds=1., protected_unknown=False)]
        self.assertEqual(primary_probability(1.e-20, 1., True, 100.), 1.)
        with self.assertRaisesRegex(ValueError, 'cannot preserve protected'):
            fit_weight_scale(rows, .1)
        result = fit_weight_scale(rows, .75)
        self.assertLessEqual(result['expected_primary_cost_fraction'], .75)

    def test_cost_budget_and_variance_use_the_same_probability(self):
        rows = [dict(native_weight=w, estimated_seconds=c, protected_unknown=False)
                for w, c in [(100., 4.), (10., 1.), (1., 1.), (.001, 1.)]]
        fit = fit_weight_scale(rows, .5)
        p = [primary_probability(r['native_weight'], r['estimated_seconds'], False,
                                 fit['weight_scale']) for r in rows]
        self.assertAlmostEqual(sum(r['estimated_seconds'] * q for r, q in zip(rows, p)), 3.5)
        variance = sum(r['native_weight'] ** 2 / q for r, q in zip(rows, p))
        uniform_variance = sum(r['native_weight'] ** 2 / .5 for r in rows)
        self.assertLess(variance, uniform_variance)

    def test_unit_weight_case_does_not_invent_information(self):
        rows = [dict(native_weight=1., estimated_seconds=2., protected_unknown=False)] * 20
        fit = fit_weight_scale(rows, .1)
        p = primary_probability(1., 2., False, fit['weight_scale'])
        # Thinning identical weighted events preserves precision per cost,
        # and reduces absolute information in this same finite GEN sample.
        self.assertAlmostEqual(p, .1)
        self.assertAlmostEqual((20 / (20 / p)) / p, 1.)

    def test_rejected_controls_keep_their_original_probability(self):
        decision = dict(passes_filter=False, protected_unknown=False)
        self.assertEqual(inclusion_probability(100., 1., decision, 1., .0001), .0001)
        with self.assertRaises(ValueError):
            inclusion_probability(1., 1., dict(passes_filter=False,
                protected_unknown=True), 1., .01)

    def test_frozen_gen_tail_group_is_kept_even_with_tiny_weight(self):
        self.assertEqual(primary_probability(1.e-25, 1., False, 100.,
                                            protected_tail=True), 1.)
        rows = [dict(native_weight=1.e-25, estimated_seconds=1.,
                     protected_unknown=False, protected_tail=True),
                dict(native_weight=1., estimated_seconds=1., protected_unknown=False)]
        with self.assertRaisesRegex(ValueError, 'cannot preserve protected'):
            fit_weight_scale(rows, .1)
        fit = fit_weight_scale(rows, .75)
        self.assertEqual(fit['protected_tail_events'], 1)
        self.assertEqual(fit['protected_unknown_events'], 0)

    def test_common_normalization_changes_do_not_change_admission(self):
        rows = [dict(native_weight=w, estimated_seconds=2., protected_unknown=False)
                for w in [.001, .1, 1., 10.]]
        scaled = [dict(r, native_weight=r['native_weight'] * 1.e6) for r in rows]
        fits = [fit_weight_scale(population, .5) for population in (rows, scaled)]
        for first, second in zip(rows, scaled):
            p = primary_probability(first['native_weight'], 2., False, fits[0]['weight_scale'])
            q = primary_probability(second['native_weight'], 2., False, fits[1]['weight_scale'])
            self.assertAlmostEqual(p, q)

    def test_bad_inputs_do_not_create_zero_or_nonfinite_support(self):
        for weight in [float('nan'), float('inf'), True]:
            with self.assertRaises(ValueError):
                primary_probability(weight, 1., False, 1.)
        for floor in [0., 1.e-30, 1.1]:
            with self.assertRaises(ValueError):
                primary_probability(1., 1., False, 1., floor)
        with self.assertRaises(ValueError):
            primary_probability(1., 0., False, 1.)


if __name__ == '__main__':
    unittest.main()
