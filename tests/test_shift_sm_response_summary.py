# Code style: AI-code. AI-generated tests for measured response accounting.
import copy
import math
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from shift_sm_response_labels import event_labels
from summarize_shift_sm_response import (benchmark_report, candidate_regions, candidate_response_report,
    representative_candidate_pair, response_statistics, sampling_forecast, summarize, wilson_interval,
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


class CandidateReportTest(unittest.TestCase):
    def row(self, number, pairs=(), native=1., p=1., q=1., region='credible_shallow_pointing'):
        pairs = [dict(item, vz=14800.) for item in pairs]
        row = event(number, pairs, native, p*q)
        row.update(original_sampling_probability=p, benchmark_sampling_probability=q,
            benchmark_region=region, cost=dict(sim_wall_seconds_per_event=10.))
        row['labels'].update(n_shift_muons=2 if pairs else 0, retained_pairs=pairs)
        return row

    def test_partial_positive_is_not_a_completed_zero_or_efficiency(self):
        row = self.row(1, [pair()])
        result = candidate_response_report([], [row], dict(complete_sources=0, selected_events=20))
        self.assertEqual(result['scope']['complete_events'], 0)
        self.assertEqual(result['scope']['partial_completed_events'], 1)
        pooled = result['pooled_groups'][0]
        self.assertIsNone(pooled['targets']['both_both']['raw_event_probability'])
        partial = next(r for r in result['partial_groups'] if r['stratum']=='all_qcd'
            and r['region_group']=='candidate')
        self.assertEqual(partial['targets']['both_both']['positive_originals'], 1)
        self.assertNotIn('raw_event_probability', partial['targets']['both_both'])
        self.assertTrue(partial['rates_and_intervals_withheld'])
        observation = result['partial_positive_originals'][0]
        self.assertEqual(observation['event_id'], row['event_id'])
        self.assertEqual(observation['retained_pairs'], row['labels']['retained_pairs'])
        self.assertTrue(observation['rates_and_intervals_withheld'])

    def test_histogram_moments_group_pairs_by_original_and_correct_once(self):
        rows = [self.row(1, [pair(), pair()], native=2., p=.5),
                self.row(2, [pair()], native=3., q=.25)]
        result = candidate_response_report(rows, [], dict(complete_sources=1, complete_events=2))
        hist = next(r for r in result['histograms'] if r['stratum']=='all_qcd'
            and r['region_group']=='candidate' and r['category']=='both_both_good' and r['field']=='mass')
        self.assertEqual(sum(hist['raw_counts']), 3)
        self.assertEqual(sum(hist['event_weighted_sum']), 20.)
        self.assertEqual(sum(hist['event_weighted_second_moment']), 208.)
        stats = result['pooled_groups'][0]['targets']['both_both_good']
        self.assertEqual(stats['weighted_pair_sum'], 20.)
        self.assertAlmostEqual(stats['effective_pair_contributions'], 400/208)

    def test_pooled_effective_count_is_recomputed_not_added(self):
        rows = [self.row(1, [pair()]), self.row(2, [pair()], native=9.)]
        rows[1]['stratum'] = 'qcd_10to20'
        result = candidate_response_report(rows, [], dict(complete_sources=2))
        stats = result['pooled_groups'][0]['targets']['both_both_good']
        self.assertAlmostEqual(stats['effective_pair_contributions'], 100/82)
        self.assertEqual(len(result['groups']), 4)

    def test_repeated_originals_wrong_probabilities_and_label_drift_fail(self):
        row = self.row(1, [pair()], p=.5, q=.25)
        with self.assertRaisesRegex(ValueError, 'Overlapping'):
            candidate_response_report([row], [copy.deepcopy(row)], {})
        wrong = copy.deepcopy(row)
        wrong['sampling_probability'] = .5
        with self.assertRaisesRegex(ValueError, 'p\\*q'):
            candidate_response_report([wrong], [], {})
        wrong = copy.deepcopy(row)
        wrong['labels']['pair_counts']['both_both_good'] = 0
        with self.assertRaisesRegex(ValueError, 'retained reconstructed pairs'):
            candidate_response_report([wrong], [], {})
        with self.assertRaisesRegex(ValueError, 'promoted'):
            candidate_response_report([row], [], dict(complete_sources=0))
        with self.assertRaisesRegex(ValueError, 'snapshot scope'):
            candidate_response_report([row], [], dict(complete_events=2))
        wrong = copy.deepcopy(row)
        wrong['benchmark_region'] = 'misspelled_candidate'
        with self.assertRaisesRegex(ValueError, 'explicit classification'):
            candidate_response_report([], [wrong], {})
        wrong = copy.deepcopy(row)
        wrong['native_weight'] = float('nan')
        with self.assertRaisesRegex(ValueError, 'finite native weight'):
            candidate_response_report([], [wrong], {})

    def test_historical_comparison_does_not_claim_matched_policy_gain(self):
        result = candidate_response_report([], [], {}, historical_rows=[self.row(1, [pair()])])
        baseline = result['historical_baseline']
        self.assertEqual(baseline['events'], 1)
        self.assertFalse(baseline['matched_policy_gain_qualified'])
        self.assertIn('one station', baseline['classifier_difference'])
        self.assertFalse(result['tail_gain_qualified'])

    def test_mixed_policies_need_separate_source_coverage_and_are_not_pooled(self):
        positive,negative = self.row(1,[pair()]),self.row(2)
        positive['selection_policy_version'] = 'v1'
        negative['selection_policy_version'] = 'v2'
        with self.assertRaisesRegex(ValueError,'separate frozen source coverage'):
            candidate_response_report([positive,negative],[],{})
        scopes = {key:dict(completed_sources=1,completed_source_events=1,selected_events=1,
                          pending_events=0,total_sources=1) for key in ('v1','v2')}
        result = candidate_response_report([positive,negative],[],dict(completed_sources=2,
            completed_source_events=2),policy_scopes=scopes)
        self.assertEqual(result['pooled_groups'],[])
        self.assertTrue(result['mixed_policy_efficiencies_withheld'])
        policies = {r['selection_policy_version']:r for r in result['policy_groups']}
        self.assertEqual(policies['v1']['pooled_groups'][0]['targets']['both_both_good']['raw_event_probability'],1.)
        self.assertEqual(policies['v2']['pooled_groups'][0]['targets']['both_both_good']['raw_event_probability'],0.)
        self.assertEqual(result['processing_counts']['pair_counts']['both_both_good'],1)

    def test_pending_policy_is_explicitly_empty_and_missing_version_fails(self):
        row = self.row(1)
        row['selection_policy_version'] = 'v1'
        scopes = dict(v1=dict(complete_sources=1,complete_events=1),
                      v2=dict(complete_sources=0,complete_events=0,pending_events=20))
        result = candidate_response_report([row],[],{},policy_scopes=scopes)
        empty = next(r for r in result['policy_groups'] if r['selection_policy_version']=='v2')
        self.assertIsNone(empty['pooled_groups'][0]['targets']['both_both']['raw_event_probability'])
        other = self.row(2)
        with self.assertRaisesRegex(ValueError,'lack their selection policy version'):
            candidate_response_report([row,other],[],{},policy_scopes=scopes)

    def test_representative_features_stay_on_one_pair(self):
        row = self.row(1,[pair()])
        first = dict(muon_barcodes=[1,2],min_muon_p_GeV=100.,max_muon_column_loss_over_p=.95,
            known_union_parent_depth=.1,two_station_miss_cm=0.)
        second = dict(muon_barcodes=[3,4],min_muon_p_GeV=50.,max_muon_column_loss_over_p=.1,
            known_union_parent_depth=.2,two_station_miss_cm=20.)
        row['joint'] = dict(n_credible_shallow_pairs=2,n_shallow_parent_unknown_column_pairs=0,
                            candidate_pair_examples=[first,second])
        representative = representative_candidate_pair(row)
        self.assertEqual(representative['muon_barcodes'],[3,4])
        self.assertEqual(representative['values']['min_muon_p_GeV'],50.)
        self.assertEqual(representative['values']['known_union_parent_depth'],.2)
        self.assertEqual(representative['values']['residual_momentum_proxy_GeV'],45.)

    def test_censored_pair_inventory_and_unknown_column_are_not_zero_material(self):
        row = self.row(1,[pair()])
        example = dict(muon_barcodes=[1,2],min_muon_p_GeV=50.,max_muon_column_loss_over_p=.1,
            known_union_parent_depth=0.,two_station_miss_cm=0.)
        row['joint'] = dict(n_credible_shallow_pairs=2,n_shallow_parent_unknown_column_pairs=0,
                            candidate_pair_examples=[example])
        self.assertEqual(representative_candidate_pair(row)['status'],'censored_pair_examples')
        result = candidate_response_report([row],[],dict(complete_sources=1))
        profile = next(r for r in result['energy_material_profiles'] if r['stratum']=='all_qcd'
            and r['region_group']=='candidate' and r['feature']=='known_union_parent_depth')
        self.assertEqual(profile['cells'][0]['bin'],'unknown')
        self.assertEqual(profile['cells'][0]['targets']['both_both_good']['positive_originals'],1)
        row['joint']['n_credible_shallow_pairs'] = 1
        example['max_muon_column_loss_over_p'] = None
        self.assertEqual(representative_candidate_pair(row)['status'],'unknown_muon_column')


if __name__ == '__main__':
    unittest.main()
