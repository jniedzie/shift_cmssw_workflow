# Code style: AI-code.
"""Same-pair enrichment, untouched exposure and exact rejected-control W/p."""
import copy
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from shift_qcd_gen_filter import (SCHEMA, bind_filter, default_policy, filter_event,
                                  prepare_filter, rejected_control_probability, validate_contract)


def event(number, *, depth=.5, weight=1., x=300.):
    muons = [dict(barcode=barcode, pdg=pdg, birth_cm=[x, 0., 14800.],
        momentum_GeV=[0., 0., -100.], p_GeV=100.,
        ancestor_barcodes=[7], first_non_muon_parent_barcodes=[7],
        immediate_parent_barcodes=[7], transport_parent_barcodes=[7],
        material_to_CMS_entry=dict(complete=True, column_density_g_cm2=1000.))
        for barcode, pdg in [(11, 13), (12, -13)]]
    return dict(event_id=[100, 1, number], native_weight=weight, GEN_muons=2,
        graph_sha256='c'*64, original_source_sha256='a'*64,
        source_file_sha256='b'*64, source_id='qcd_test', muons=muons,
        parent_flights=[dict(barcode=7, material=dict(complete=True,
            full_saved_chord_coverage=True, interaction_depth_proxy=depth))],
        ancestor_nodes=[dict(barcode=7, pdg=211, status=2)],
        unknown_transport_ancestor_barcodes=[])


def source(events):
    return dict(source_id='qcd_test', stratum='qcd_5to10', gen_sha256='a'*64,
        saved_events=len(events), generator_profile='ordinary',
        denominator_kind='fixed_source_trials', full_source_trials=1000,
        sigma_nd_pb=100., original_native_metadata=dict(retain='exactly'))


def descriptor(events, original):
    return dict(stratum=original['stratum'], gen_sha256='b'*64,
        event_ids=[e['event_id'] for e in events],
        weights=[e['native_weight'] for e in events], normalization_record=original,
        gen='/eos/unchanged.root', nano_base='/eos/next', metadata_base='/eos/meta')


class TwoMuonFilterTest(unittest.TestCase):
    def test_at_least_two_muons_and_same_sign_kept_by_default(self):
        e = event(1)
        self.assertTrue(filter_event(e)['passes_filter'])
        e['muons'][1]['pdg'] = 13
        self.assertTrue(filter_event(e)['passes_filter'])
        self.assertFalse(filter_event(e, default_policy(require_opposite_sign=True))['passes_filter'])
        e['muons'].pop(); e['GEN_muons'] = 1
        self.assertFalse(filter_event(e)['passes_filter'])

    def test_shared_parent_counted_once_and_counterexample_depth_retained(self):
        accepted = filter_event(event(1, depth=1.692449))
        self.assertTrue(accepted['passes_filter'])
        self.assertEqual(accepted['pair']['parent_interaction_depth'], 1.692449)
        self.assertFalse(filter_event(event(1, depth=2.001))['passes_filter'])

    def test_no_pt_cut_and_known_total_momentum_range_predictor(self):
        e = event(1)
        self.assertEqual(math.hypot(*e['muons'][0]['momentum_GeV'][:2]), 0.)
        self.assertTrue(filter_event(e)['passes_filter'])
        e['muons'][0]['material_to_CMS_entry']['column_density_g_cm2'] = 100000.
        self.assertFalse(filter_event(e)['passes_filter'])

    def test_unknown_paths_protected_only_for_a_potentially_pointing_pair(self):
        e = event(1)
        e['parent_flights'][0]['material']['full_saved_chord_coverage'] = False
        decision = filter_event(e)
        self.assertTrue(decision['passes_filter'])
        self.assertTrue(decision['protected_unknown'])
        for muon in e['muons']:
            muon['momentum_GeV'] = [0., 0., 100.]
        self.assertFalse(filter_event(e)['passes_filter'])
        e['muons'][0]['birth_cm'] = None
        # The second known muon still points away; missing data cannot rescue it.
        self.assertFalse(filter_event(e)['passes_filter'])
        e['muons'][1]['momentum_GeV'] = [0., 0., -100.]
        self.assertTrue(filter_event(e)['protected_unknown'])

    def test_unknown_material_and_ancestry_do_not_become_zero_material(self):
        for change in ['column', 'ancestor', 'parent_inventory']:
            e = event(1)
            if change == 'column':
                e['muons'][0]['material_to_CMS_entry'] = dict(complete=False)
            elif change == 'ancestor':
                e['ancestor_nodes'][0]['pdg'] = 999999
                e['unknown_transport_ancestor_barcodes'] = [7]
            else:
                e['muons'][0]['transport_parent_barcodes'] = None
            decision = filter_event(e)
            self.assertTrue(decision['passes_filter'], change)
            self.assertTrue(decision['protected_unknown'], change)
        # Incoming stable hadrons alone do not make a two-muon opportunity.
        e = event(2); e['muons'] = []; e['GEN_muons'] = 0
        e['incoming_stable_hadrons_without_saved_decay'] = 100
        self.assertFalse(filter_event(e)['passes_filter'])

    def test_unknown_continuation_cannot_hide_known_pair_requirement_failures(self):
        e = event(1)
        e['muons'][0]['material_to_CMS_entry']['column_density_g_cm2'] = 100000.
        e['muons'][1]['material_to_CMS_entry'] = dict(complete=False)
        self.assertFalse(filter_event(e)['passes_filter'])
        e = event(1, depth=3.)
        e['parent_flights'][0]['material']['full_saved_chord_coverage'] = False
        self.assertFalse(filter_event(e)['passes_filter'])
        e['parent_flights'][0]['material']['interaction_depth_proxy'] = 1.5
        decision = filter_event(e)
        self.assertTrue(decision['protected_unknown'])
        self.assertIsNone(decision['pair']['parent_interaction_depth'])
        self.assertEqual(decision['pair']['known_parent_prefix_depth_lower_bound'], 1.5)

    def test_known_partial_parent_union_prefix_is_not_counted_twice(self):
        e = event(1, depth=1.5)
        e['parent_flights'][0]['material']['full_saved_chord_coverage'] = False
        decision = filter_event(e)
        self.assertTrue(decision['passes_filter'])
        self.assertEqual(decision['pair']['known_parent_prefix_depth_lower_bound'], 1.5)

    def test_different_pairs_cannot_supply_independent_best_quantities(self):
        e = event(1)
        third = copy.deepcopy(e['muons'][1]); third['barcode'] = 13
        e['muons'].append(third); e['GEN_muons'] = 3
        for index, muon in enumerate(e['muons']):
            barcode = 7+index
            for key in ['ancestor_barcodes', 'first_non_muon_parent_barcodes',
                        'immediate_parent_barcodes', 'transport_parent_barcodes']:
                muon[key] = [barcode]
        e['parent_flights'] = [dict(barcode=7+i, material=dict(complete=True,
            full_saved_chord_coverage=True, interaction_depth_proxy=3. if i == 0 else 0.))
            for i in range(3)]
        e['ancestor_nodes'] = [dict(barcode=7+i, pdg=211, status=2) for i in range(3)]
        e['muons'][1]['material_to_CMS_entry']['column_density_g_cm2'] = 100000.
        e['muons'][2]['birth_cm'][0] = 5000.
        self.assertFalse(filter_event(e)['passes_filter'])

    def test_bad_inventory_is_an_error(self):
        e = event(1); e['muons'][1]['barcode'] = e['muons'][0]['barcode']
        with self.assertRaisesRegex(ValueError, 'unique'):
            filter_event(e)


class FilterAccountingTest(unittest.TestCase):
    def setUp(self):
        self.events = [event(1, weight=2.), event(2, depth=3., weight=7.), event(3, weight=11.)]
        self.events[2]['muons'][0]['birth_cm'] = None
        self.source = source(self.events)
        self.policy = default_policy(minimum_rejected_control_probability=.25,
                                     maximum_rejected_control_probability=.25)

    def prepare(self, **kwargs):
        return prepare_filter(self.events, self.source, self.policy, 'test-seed',
                              before_first_sim=True, **kwargs)

    def test_primary_filter_cross_section_is_independent_of_rejected_sampling(self):
        before = copy.deepcopy(self.events)
        ledger = self.prepare()
        n = ledger['normalization']
        self.assertEqual(n['saved_raw_MC_events'], 3)
        self.assertEqual(n['passing_raw_MC_events'], 2)
        self.assertEqual(n['sumw_before'], 20.)
        self.assertEqual(n['sumw_pass'], 13.)
        self.assertEqual(n['sumw_fail'], 7.)
        self.assertEqual(n['sumw2_pass'], 125.)
        self.assertEqual(n['filtered_cross_section_pb'], 1.3)
        self.assertEqual(n['original_denominator'], 1000)
        self.assertEqual(ledger['events'][1]['sampling_probability'], .25)
        self.assertEqual(ledger['events'][1]['sampling_gen_weight'], 28.)
        self.assertEqual(ledger['events'][2]['sampling_probability'], 1.)
        self.assertEqual(self.events, before)
        changed = prepare_filter(self.events, self.source,
            default_policy(), 'other-seed', before_first_sim=True)
        self.assertEqual(changed['normalization'], n)

    def test_accepted_event_low_bin_retains_native_cross_section(self):
        events = [event(1), event(2, depth=3.)]
        original = dict(source(events), stratum='qcd_0to1', denominator_kind='accepted_gen_events',
                        full_source_events=2, native_cross_section_pb=80.)
        original.pop('full_source_trials'); original.pop('sigma_nd_pb')
        ledger = prepare_filter(events, original, self.policy, 'seed', before_first_sim=True)
        self.assertEqual(ledger['normalization']['filtered_cross_section_pb'], 40.)
        self.assertEqual(ledger['source'], original)

    def test_draw_is_source_bound_reproducible_and_order_independent(self):
        first = self.prepare()
        second = self.prepare()
        self.assertEqual(first, second)
        reversed_events = list(reversed(self.events))
        reversed_ledger = prepare_filter(reversed_events, self.source, self.policy,
            'test-seed', before_first_sim=True)
        key = lambda x: {tuple(e['event_id']): (e['admission_draw'], e['admitted']) for e in x['events']}
        self.assertEqual(key(first), key(reversed_ledger))
        changed_events = copy.deepcopy(self.events)
        for row in changed_events: row['original_source_sha256'] = 'd'*64
        changed_source = dict(self.source, gen_sha256='d'*64)
        changed = prepare_filter(changed_events, changed_source, self.policy,
            'test-seed', before_first_sim=True)
        self.assertNotEqual(first['events'][1]['admission_draw'], changed['events'][1]['admission_draw'])

    def test_hash_grid_probability_and_native_weight_are_the_same_draw(self):
        ledger = prepare_filter(self.events, self.source, default_policy(), 'seed', before_first_sim=True)
        probability = ledger['events'][1]['sampling_probability']
        self.assertEqual(probability*(1 << 52), int(probability*(1 << 52)))
        self.assertLessEqual(probability, .01)
        self.assertLess(.01-probability, 1./(1 << 52))
        self.assertEqual(ledger['events'][1]['sampling_gen_weight'], 7./probability)

    def test_controls_follow_primary_supply_instead_of_the_large_rejected_pool(self):
        policy = default_policy()
        probability = rejected_control_probability(policy, 100, 100000)
        self.assertGreaterEqual(probability*100000, 25.)
        self.assertLess(probability*100000-25., 100000./(1 << 52))
        self.assertLess(probability, .001)
        # More primary GEN events allow more controls; changing the RNG seed
        # or reconstructed outcomes is not an input to this allocation.
        self.assertAlmostEqual(rejected_control_probability(policy, 200, 100000)*100000, 50.)

    def test_zero_primary_census_keeps_small_positive_rejected_support(self):
        policy = default_policy()
        for rejected in [0, 100, 100000]:
            probability = rejected_control_probability(policy, 0, rejected)
            self.assertGreaterEqual(probability, 1.e-5)
            self.assertLess(probability-1.e-5, 1./(1 << 52))
        events = [event(i, depth=3.) for i in range(1, 101)]
        ledger = prepare_filter(events, source(events), policy, 'seed', before_first_sim=True)
        self.assertTrue(ledger['control_budget_uses_complete_GEN_counts_only'])
        self.assertAlmostEqual(ledger['expected_rejected_control_raw_MC_events'], .001)
        self.assertEqual(ledger['normalization']['passing_raw_MC_events'], 0)
        self.assertTrue(all(row['sampling_probability'] > 0 for row in ledger['events']))

    def test_control_budget_bounds_and_bad_inputs_are_explicit(self):
        policy = default_policy()
        self.assertLessEqual(rejected_control_probability(policy, 10000, 1), .01)
        for arguments in [(True, 10), (-1, 10), (1, 1.5)]:
            with self.assertRaises(ValueError):
                rejected_control_probability(policy, *arguments)
        with self.assertRaisesRegex(ValueError, 'bounds'):
            default_policy(minimum_rejected_control_probability=.1,
                           maximum_rejected_control_probability=.01)

    def test_cannot_change_processed_or_partial_censuses(self):
        with self.assertRaisesRegex(ValueError, 'first-SIM'):
            prepare_filter(self.events, self.source, self.policy, 'seed', before_first_sim=False)
        for previous in [dict(selected_indices=[0], sampling_probabilities=[1.]),
                         dict(selected_indices=[0, 1, 2], sampling_probabilities=[.5, .5, .5])]:
            with self.assertRaisesRegex(ValueError, 'partial or frozen'):
                self.prepare(previous_selection=previous)
        with self.assertRaisesRegex(ValueError, 'complete'):
            prepare_filter(self.events[:2], self.source, self.policy, 'seed', before_first_sim=True)
        with self.assertRaisesRegex(ValueError, 'QCD-only'):
            prepare_filter(self.events, dict(self.source, stratum='bsm_point'),
                           self.policy, 'seed', before_first_sim=True)

    def test_binding_preserves_all_originals_exposure_and_w_over_p(self):
        ledger = self.prepare(); original = descriptor(self.events, self.source)
        before = copy.deepcopy(original)
        bound = bind_filter(original, ledger)
        validate_contract(bound['production_selection'])
        self.assertEqual(bound['production_selection']['schema'], SCHEMA)
        self.assertEqual(bound['event_ids'], original['event_ids'])
        self.assertEqual(bound['normalization_record'], self.source)
        self.assertEqual(bound['weights'], original['weights'])
        self.assertEqual(original, before)
        self.assertFalse(bound['qcd_gen_filter']['filter_efficiency_applied_to_event_weights'])
        with self.assertRaisesRegex(ValueError, 'reopen'):
            bind_filter(bound, ledger)
        broken = copy.deepcopy(ledger); broken['normalization']['sumw_pass'] += 1
        with self.assertRaisesRegex(ValueError, 'normalization'):
            bind_filter(original, broken)
        broken = copy.deepcopy(ledger); broken['events'][1]['sampling_gen_weight'] = 7.
        with self.assertRaisesRegex(ValueError, 'W/p'):
            bind_filter(original, broken)

    def test_empty_fixed_trial_source_still_has_original_exposure(self):
        ledger = prepare_filter([], source([]), self.policy, 'seed', before_first_sim=True)
        self.assertEqual(ledger['normalization']['original_denominator'], 1000)
        self.assertEqual(ledger['normalization']['filtered_cross_section_pb'], 0.)
        self.assertIsNone(ledger['normalization']['weighted_passing_fraction'])

    def test_native_failed_weight_sums_do_not_subtract_large_passed_sums(self):
        events = [event(1, weight=1.e16), event(2, depth=3., weight=1.)]
        ledger = prepare_filter(events, source(events), self.policy, 'seed', before_first_sim=True)
        self.assertEqual(ledger['normalization']['sumw_fail'], 1.)
        self.assertEqual(ledger['normalization']['sumw2_fail'], 1.)

    def test_source_exposure_cannot_be_ambiguous_or_under_count_saved_census(self):
        for change in [dict(full_source_events=3), dict(full_source_trials=2)]:
            with self.assertRaises(ValueError):
                prepare_filter(self.events, dict(self.source, **change), self.policy,
                               'seed', before_first_sim=True)


if __name__ == '__main__':
    unittest.main()
