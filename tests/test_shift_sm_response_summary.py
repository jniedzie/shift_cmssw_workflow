# Code style: AI-code. AI-generated tests for measured response accounting.
import copy
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from shift_sm_response_labels import event_labels
from summarize_shift_sm_response import candidate_regions, response_statistics, sampling_forecast, summarize, wilson_interval


def event(number, pairs=(), native=1., probability=1.):
    return dict(gen_sha256='a'*64, event_id=[1, 1, number], stratum='qcd_20toinf',
        study_split='validation', native_weight=native, sampling_probability=probability,
        features=dict(best_pair_miss_cm=0., best_pair_min_energy_GeV=30.,
            max_pair_min_energy_GeV=30., min_pair_max_column_loss_over_p=.1,
            min_parent_prefix_depth=0., best_pair_max_column_loss_over_p=.1,
            best_pair_birth_z_min_cm=14800., n_os_pairs=1, n_both_forward_os_pairs=1,
            all_incoming_pairs_parent_depth_ge_10=False), labels=event_labels(pairs))


def pair():
    return dict(topologyMin=2, topologyMax=2, isOS=1, dcaValid=1, dca=2., mass=30., pt=15.)


class ResponseSummaryTest(unittest.TestCase):
    def test_zero_successes_have_nonzero_upper_limit(self):
        result = response_statistics([event(i) for i in range(100)], 'both_both')
        self.assertGreater(result['raw_event_probability_interval95'][1], 0.)
        self.assertAlmostEqual(result['zero_observation_upper95_raw'], .029513049607039932)
        self.assertFalse(result['zero_observation_is_zero_physical_rate'])

    def test_multiple_pairs_remain_one_independent_contribution(self):
        result = response_statistics([event(1, [pair(), pair()]), event(2)], 'both_both')
        self.assertEqual(result['pairs'], 2)
        self.assertEqual(result['positive_originals'], 1)
        self.assertEqual(result['effective_pair_contributions'], 1.)

    def test_sampling_weight_is_applied_once(self):
        result = response_statistics([event(1, [pair()], native=2., probability=.1)], 'both_both')
        self.assertEqual(result['weighted_pair_sum'], 20.)
        self.assertEqual(result['event_level_pair_second_moment'], 400.)

    def test_signed_weight_ratio_is_not_reported_as_a_probability(self):
        result = response_statistics([event(1, [pair()]), event(2, native=-.75)], 'both_both')
        self.assertEqual(result['weighted_conditional_event_ratio'], 4.)
        self.assertIsNone(result['weighted_conditional_event_probability'])
        self.assertFalse(result['weighted_event_probability_defined'])
        self.assertTrue(result['has_negative_native_weights'])
        self.assertEqual(result['raw_event_probability'], .5)

    def test_nonnegative_weight_ratio_remains_a_probability(self):
        result = response_statistics([event(1, [pair()], native=2.), event(2)], 'both_both')
        self.assertAlmostEqual(result['weighted_conditional_event_ratio'], 2/3)
        self.assertAlmostEqual(result['weighted_conditional_event_probability'], 2/3)
        self.assertTrue(result['weighted_event_probability_defined'])
        zero = response_statistics([event(1, native=0.)], 'both_both')
        self.assertIsNone(zero['weighted_conditional_event_ratio'])
        self.assertIsNone(zero['weighted_conditional_event_probability'])
        self.assertFalse(zero['weighted_event_probability_defined'])

    def test_overlapping_snapshots_are_rejected(self):
        row = event(1)
        with self.assertRaisesRegex(ValueError, 'Overlapping'):
            summarize([row, copy.deepcopy(row)])

    def test_good_and_both_both_are_separate(self):
        bad = pair()
        bad['dcaValid'] = 0
        result = summarize([event(1, [bad])])['groups'][0]
        self.assertEqual(result['totals']['both_both']['positive_originals'], 1)
        self.assertEqual(result['totals']['both_both_good']['positive_originals'], 0)

    def test_unknown_pointing_is_not_a_claimed_physics_veto(self):
        row = event(1)
        row['features']['best_pair_miss_cm'] = None
        self.assertFalse(candidate_regions(row['features'])['poor_both_endcap_pointing_5m'])

    def test_missing_split_and_invalid_counts_fail(self):
        row = event(1)
        row['study_split'] = 'tuned_on_test'
        with self.assertRaises(ValueError):
            summarize([row])
        with self.assertRaises(ValueError):
            wilson_interval(2, 1)

    def test_cost_forecast_counts_the_penalty_for_demoting_a_positive(self):
        positive = event(1, [pair()])
        negative = event(2)
        positive['cost'] = dict(sim_wall_seconds_per_event=10., cost_kind='single_event_actual')
        negative['cost'] = dict(sim_wall_seconds_per_event=90., cost_kind='single_event_actual')
        negative['features']['best_pair_miss_cm'] = 10000.
        good = sampling_forecast([positive, negative], 'both_both', 'poor_both_endcap_pointing_5m', .1)
        self.assertAlmostEqual(good['conditional_information_per_SIM_wall_gain'], 100/19)
        self.assertEqual(good['demoted_positive_originals'], 0)
        self.assertEqual(good['reference_admission_probability'], 1.)
        self.assertEqual(good['reference_policy'], 'full_reconstruction_of_original_GEN_cohort')
        self.assertEqual(good['target_scope'], 'all_pairs_in_category')
        self.assertIsNone(good['target_tail'])
        positive['features']['best_pair_miss_cm'] = 10000.
        bad = sampling_forecast([positive, negative], 'both_both', 'poor_both_endcap_pointing_5m', .1)
        self.assertAlmostEqual(bad['pair_second_moment_ratio'], 10.)
        self.assertAlmostEqual(bad['conditional_information_per_SIM_wall_gain'], 1.)
        self.assertFalse(bad['production_policy_qualified'])

    def test_reference_is_full_admission_even_for_thinned_response_data(self):
        positive = event(1, [pair()], probability=.1)
        negative = event(2, probability=.1)
        for row in (positive, negative):
            row['cost'] = dict(sim_wall_seconds_per_event=10., cost_kind='single_event_actual')
        negative['features']['best_pair_miss_cm'] = 10000.
        forecast = sampling_forecast([positive, negative], 'both_both', 'poor_both_endcap_pointing_5m', .1)
        self.assertEqual(forecast['reference_admission_probability'], 1.)
        self.assertAlmostEqual(forecast['expected_event_count_ratio'], .55)
        self.assertTrue(forecast['response_sampling_probability_used_for_estimation'])
        summary = summarize([positive, negative])
        self.assertFalse(summary['counterfactual_sampling_includes_tails'])


if __name__ == '__main__':
    unittest.main()
