# Code style: AI-code. AI-generated event-grouped response and CPU regression tests.
import copy
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from shift_material_response import (compare, bootstrap_comparison, shape_comparison,
                                     retained_tail_counts, response_reporting_scope, TARGETS)
from shift_material_preselection import admission_probability


def row(event, count, probability):
    return dict(original_source_sha256='a'*64, event_id=[1, 1, event],
                benchmark_probability=1., candidate_probability=probability,
                rate_weight=2., detector_cpu_seconds=10., feature_cpu_seconds=.1,
                tail_counts=dict.fromkeys(TARGETS, count))


class ResponseTest(unittest.TestCase):
    def test_legacy_plan_scope_counts_declared_and_sampled_sources(self):
        plan = dict(sources={'a': {}, 'b': {}, 'zero_selected': {}}, samples=[
            dict(source_id='a', event_ids=[[1, 1, 1]], probability=.2),
            dict(source_id='b', event_ids=[[2, 1, 1]], probability=.1)])
        scope = response_reporting_scope(plan)
        self.assertEqual(scope['declared_source_count'], 3)
        self.assertEqual(scope['sampled_source_count'], 2)
        self.assertIn('Uniform inclusion within each declared saved source', scope['sampling_design'])
        self.assertFalse(scope['mechanism_study'])
        self.assertFalse(scope['inclusive_reconstructed_tail_rates_estimable'])

    def test_tail_control_design_is_preserved_and_never_called_uniform(self):
        plan = dict(sources={'a': {}}, samples=[dict(source_id='a',
            event_ids=[[1, 1, 1], [1, 1, 2]], probabilities=[1., .01])],
            sampling_design='GEN tail certainty + independent 1% controls.',
            source_scope='Conditional finite-cylinder saved GEN.')
        scope = response_reporting_scope(plan)
        self.assertEqual(scope['sampling_design'], plan['sampling_design'])
        self.assertEqual(scope['source_population_scope'], plan['source_scope'])
        self.assertNotIn('uniform', scope['source_inclusion_scope'].lower())
        self.assertNotIn('Random', scope['reference_label'])

    def test_gen_mass_only_scope_excludes_inclusive_reco_tail_claims(self):
        plan = dict(sources={'a': {}, 'zero_selected': {}}, samples=[dict(source_id='a',
            event_ids=[[1, 1, 1]], probability=1.)],
            sampling_design='All OS GEN mass>20 originals; no inclusive reconstructed-tail rate claim.',
            source_scope='GEN-mass-selected physical mechanism test.')
        scope = response_reporting_scope(plan)
        self.assertTrue(scope['mechanism_study'])
        self.assertEqual(scope['reference_label'], 'GEN-mass-selected originals')
        self.assertIn('Cannot estimate inclusive reconstructed-tail rates',
                      scope['reconstructed_tail_rate_scope'])
        self.assertIn('charge or momentum errors', scope['reconstructed_tail_rate_scope'])

    def test_multiple_pairs_are_one_statistical_unit(self):
        result = compare([row(1, 2, .5), row(2, 0, .1)])['targets']['mass_gt20']
        self.assertEqual(result['positive_originals'], 1)
        self.assertEqual(result['baseline_second_moment'], 16.)
        self.assertEqual(result['baseline_effective_count'], 1.)
        self.assertEqual(result['candidate_second_moment'], 32.)

    def test_zero_tails_cannot_prove_improvement(self):
        result = compare([row(1, 0, .1)])
        self.assertFalse(result['tail_improvement_proven'])
        self.assertIsNone(result['targets']['mass_gt20']['precision_per_CPU_gain_estimate'])
        self.assertFalse(result['targets']['mass_gt20']['statistics_sufficient'])

    def test_tail_preference_improves_and_wrong_preference_hurts(self):
        good = [row(1, 1, 1.)] + [row(i, 0, .1) for i in range(2, 11)]
        bad = [row(1, 1, .1)] + [row(i, 0, 1.) for i in range(2, 11)]
        key = 'precision_per_CPU_gain_estimate'
        self.assertAlmostEqual(compare(good)['targets']['mass_gt20'][key], 5.)
        self.assertLess(compare(bad)['targets']['mass_gt20'][key], .2)

    def test_probability_one_costs_feature_scan(self):
        result = compare([row(1, 1, 1.)])
        self.assertAlmostEqual(result['targets']['mass_gt20']['precision_per_CPU_gain_estimate'], 10/10.1)

    def test_uniform_benchmark_weight_and_no_duplicate_originals(self):
        a = row(1, 1, .5)
        a['benchmark_probability'] = .25
        result = compare([a])['targets']['mass_gt20']
        self.assertEqual(result['weighted_sum'], 8.)
        self.assertEqual(result['baseline_second_moment'], 16.)
        with self.assertRaises(ValueError):
            compare([a, copy.deepcopy(a)])

    def test_retained_OS_both_both_only(self):
        pair = dict(isOS=1., topologyMin=2., topologyMax=2., mass=51., pt=21.)
        self.assertEqual(retained_tail_counts([pair]), dict.fromkeys(TARGETS, 1))
        self.assertEqual(retained_tail_counts([dict(pair, isOS=0)]), dict.fromkeys(TARGETS, 0))
        self.assertEqual(retained_tail_counts([dict(pair, topologyMin=1)]), dict.fromkeys(TARGETS, 0))

    def test_shared_admission_rule_keeps_controls_and_high_weights(self):
        policy = dict(control_probability=.1, unknown_probability=.1)
        rank = dict(priority=0., unknown_potential_pair=False)
        self.assertAlmostEqual(admission_probability(rank, 1., 1., policy), .1)
        self.assertEqual(admission_probability(rank, 100., 1., policy), 1.)
        rank['priority'] = .8
        self.assertAlmostEqual(admission_probability(rank, 1., 1., policy), .82)

    def test_bootstrap_requires_tail_statistics_and_reproducible_support(self):
        rows = [row(i, int(i <= 40), 1. if i <= 40 else .1) for i in range(1, 401)]
        for record in rows:
            record['stratum'] = 'one_bin'
        a = bootstrap_comparison(rows, replicates=100)
        self.assertTrue(a['tail_improvement_proven'])
        self.assertEqual(a, bootstrap_comparison(rows, replicates=100))
        b = bootstrap_comparison(rows[:10], replicates=100)
        self.assertFalse(b['tail_improvement_proven'])

    def test_shape_errors_group_pairs_and_keep_shared_sample_covariance(self):
        record = row(1, 2, .5)
        pair = dict(isOS=True, topologyMin=2, topologyMax=2, mass=51., pt=21.)
        record['retained_pairs'] = [pair, pair]
        result = shape_comparison([record], dict(selection_domain='test', selection_seed='fixed'))['mass']
        index = result['edges'].index(50.)
        self.assertEqual(result['reference'][index], 4.)
        self.assertEqual(result['reference_variance'][index], 16.)
        self.assertEqual(result['candidate_variance'][index], 32.)
        self.assertEqual(result['difference_variance'][index], 16.)
        self.assertNotEqual(result['difference_variance'][index], 16.+32.)

    def test_undefined_mass_does_not_manufacture_a_tail(self):
        pair = dict(isOS=True, topologyMin=2, topologyMax=2, mass=None, pt=21.)
        counts = retained_tail_counts([pair])
        self.assertEqual(counts['mass_gt20'], 0)
        self.assertEqual(counts['pt_gt20'], 1)


if __name__ == '__main__':
    unittest.main()
