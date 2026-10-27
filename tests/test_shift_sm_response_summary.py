# Code style: AI-code. AI-generated tests for measured response accounting.
import copy
import math
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from shift_sm_response_labels import event_labels
from summarize_shift_sm_response import (benchmark_report, candidate_regions,
    response_statistics, sampling_forecast, summarize, wilson_interval,
    zero_success_population_upper)
from prepare_shift_sm_prospective_benchmark import prepare_benchmark


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


class StratifiedResponseTest(unittest.TestCase):
    def fixture(self, unknown_pointing=False):
        population = []
        for number in range(1, 7):
            positive_region = number <= 2
            features = dict(event(number)['features'], n_incomplete_incoming_parent_paths=0,
                n_pointing_pairs_known_muon_column=int(positive_region),
                n_pointing_pairs_full_parent_coverage=int(positive_region),
                n_pointing_os_pairs=int(positive_region),
                min_parent_prefix_depth_among_pointing_pairs=0. if positive_region else None)
            population.append(dict(gen_sha256='a'*64, event_id=[1, 1, number],
                native_weight=2., existing_sampling_probability=.5,
                stratum='qcd_20toinf', features=features,
                joint=dict(n_credible_shallow_pairs=int(positive_region),
                    n_shallow_parent_unknown_column_pairs=0,
                    n_unknown_parent_shallow_prefix_pairs=0,
                    n_joint_pointing_pairs=int(positive_region))))
        if unknown_pointing:
            row = population[2]
            row['features'].update(n_pointing_os_pairs=1,
                n_pointing_pairs_known_muon_column=1,
                n_incomplete_incoming_parent_paths=1,
                min_parent_prefix_depth_among_pointing_pairs=12.)
            row['joint']['n_joint_pointing_pairs'] = 1
        plan = prepare_benchmark(population, 'fixed-test', controls=dict(
            border_pointing=1, deep_pointing=1, no_both_sides_pointing_pair=1, unknown_parent=1))
        rows = []
        for selected in plan['selected']:
            number = selected['event_id'][2]
            row = event(number, [pair()] if number == 1 else [], native=2.,
                probability=selected['cumulative_study_probability'])
            row.update(features=selected['features'], original_sampling_probability=.5,
                benchmark_sampling_probability=selected['benchmark_inclusion_probability'],
                cost=dict(sim_wall_seconds_per_event=10., cost_kind='single_event_actual'))
            rows.append(row)
        return population, plan, rows

    def test_finite_zero_bound_matches_exhaustive_small_populations(self):
        for population in range(1, 15):
            for sampled in range(population+1):
                accepted = [positive for positive in range(population+1)
                    if (math.comb(population-positive, sampled)/math.comb(population, sampled)
                        if population-positive >= sampled else 0.) >= .05]
                self.assertEqual(zero_success_population_upper(population, sampled), max(accepted))
        self.assertEqual(zero_success_population_upper(10000, 0), 10000)
        self.assertEqual(zero_success_population_upper(10000, 10000), 0)
        with self.assertRaises(ValueError):
            zero_success_population_upper(5, 6)

    def test_complete_cell_and_tiny_control_have_different_zero_bounds(self):
        population, plan, rows = self.fixture()
        result = benchmark_report(population, plan, rows)
        cells = {cell['region']: cell for cell in result['cells']}
        self.assertTrue(cells['credible_shallow_pointing']['complete_cell'])
        missed = cells['no_both_sides_pointing_pair']
        self.assertEqual(missed['targets']['both_both']['finite_cell_zero_success_upper95_positive_events'], 3)
        self.assertFalse(result['production_policy_qualified'])

    def test_native_p_and_benchmark_q_are_not_applied_twice(self):
        population, plan, rows = self.fixture()
        result = benchmark_report(population, plan, rows)
        credible = next(cell for cell in result['cells'] if cell['region']=='credible_shallow_pointing')
        self.assertEqual(credible['targets']['both_both']['weighted_pair_sum'], 4.)
        forecast = result['planning_forecasts'][0]
        self.assertAlmostEqual(forecast['expected_admission_fraction'], (2+.1*4)/6)
        self.assertEqual(forecast['demoted_observed_good_BothBoth'], 0)
        self.assertFalse(forecast['tail_statistics_improvement_qualified'])

    def test_unknown_paths_keep_unit_probability_in_forecast(self):
        original, plan, rows = self.fixture(unknown_pointing=True)
        forecast = benchmark_report(original, plan, rows)['planning_forecasts'][0]
        self.assertEqual(forecast['protected_or_promising_events'], 3)
        self.assertAlmostEqual(forecast['expected_admission_fraction'], .55)

    def test_unrelated_nonpointing_unknown_path_does_not_force_admission(self):
        original, plan, rows = self.fixture()
        original[2]['features']['n_incomplete_incoming_parent_paths'] = 1
        for chosen in plan['selected']:
            if chosen['event_id'][2] == 3:
                chosen['features'] = copy.deepcopy(original[2]['features'])
        for row in rows:
            if row['event_id'][2] == 3:
                row['features'] = copy.deepcopy(original[2]['features'])
        forecast = benchmark_report(original, plan, rows)['planning_forecasts'][0]
        self.assertEqual(forecast['protected_or_promising_events'], 2)

    def test_missing_repeated_and_changed_responses_fail(self):
        population, plan, rows = self.fixture()
        for invalid in [rows[:-1], rows+[copy.deepcopy(rows[0])]]:
            with self.assertRaises(ValueError):
                benchmark_report(population, plan, invalid)
        for field, value in [('native_weight', 7.), ('benchmark_sampling_probability', 1.),
                             ('original_sampling_probability', 1.), ('stratum', 'qcd_1to2')]:
            changed = copy.deepcopy(rows)
            changed[-1][field] = value
            with self.assertRaises(ValueError):
                benchmark_report(population, plan, changed)


if __name__ == '__main__':
    unittest.main()
