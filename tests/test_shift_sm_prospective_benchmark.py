# Code style: AI-code. AI-generated tests for prospective benchmark sampling.
import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from prepare_shift_sm_prospective_benchmark import (benchmark_region, prepare_benchmark,
    prepare_selection_comparison, validate_population)


def event(number, region='credible', native=1., probability=1.):
    joint = dict(n_credible_shallow_pairs=int(region == 'credible'),
        n_shallow_parent_unknown_column_pairs=int(region == 'unknown_column'),
        n_unknown_parent_shallow_prefix_pairs=int(region == 'unknown_parent'),
        n_joint_pointing_pairs=int(region != 'miss'))
    depth = dict(credible=0., unknown_column=0., unknown_parent=0.,
                 border=4., deep=40., miss=None)[region]
    return dict(source_id='qcd_20toinf_source0040', stratum='qcd_20toinf',
        gen_sha256='a'*64, prepared_gen_sha256='b'*64,
        event_id=[1, 1, number], original_index=number-1,
        selected_position=number-1, native_weight=native, existing_sampling_probability=probability,
        joint=joint, features=dict(min_parent_prefix_depth_among_pointing_pairs=depth,
            best_pair_min_energy_GeV=.1))


def authority_fixture():
    """Selected position zero deliberately points at original GEN index two."""
    row=event(1,native=2.5,probability=.25)
    row.update(original_index=2,event_id=[1,1,30])
    exposure=dict(source_id=row['source_id'],stratum=row['stratum'],
        gen_sha256='a'*64,saved_events=4,denominator_kind='fixed_source_trials',
        full_source_trials=100,sigma_nd_pb=50.)
    descriptor=dict(gen_sha256='b'*64,stratum=row['stratum'],normalization_record=copy.deepcopy(exposure),
        event_ids=[[1,1,10],[1,1,20],[1,1,30],[1,1,40]],weights=[4.,5.,2.5,8.],
        selected_indices=[2],sampling_probabilities=[.25])
    receipt=dict(complete=True,exit_code=0,source=copy.deepcopy(exposure),selected_source=descriptor)
    data=json.dumps(receipt,sort_keys=True,separators=(',',':')).encode()
    checksum=hashlib.sha256(data).hexdigest()
    source=dict(source_id=row['source_id'],stratum=row['stratum'],
        original_raw_gen_sha256='a'*64,prepared_gen_sha256='b'*64,selected_event_count=1,
        full_original_exposure=exposure,original_result_path='/synthetic/source/result.json',
        original_result_sha256=checksum)
    manifest=dict(every_known_previously_attempted_QCD_request_checked=True,
        old_masks_weights_and_campaign_ledger_unchanged=True,
        cohort_selection_uses_only_preexisting_attempt_status_not_reconstruction_passes=True,
        selected_events=1,sources=[source])
    def load(path,expected_sha256):
        if path != source['original_result_path'] or expected_sha256 != checksum:
            raise ValueError('Frozen receipt changed: synthetic SHA-256 mismatch')
        return copy.deepcopy(receipt)
    return row,manifest,load


class ProspectiveBenchmarkTest(unittest.TestCase):
    def comparison_population(self):
        rows = []
        for source in range(20):
            for number in range(1, 101):
                row = event(number, 'credible' if number <= 3 else ('deep' if number % 2 else 'miss'))
                row.update(source_id='qcd_source_'+str(source), gen_sha256=hashlib.sha256(
                    str(source).encode()).hexdigest(), event_id=[source+1, 1, number])
                rows.append(row)
        return rows

    def test_comparison_uses_one_control_budget_and_equal_disjoint_arms(self):
        rows = self.comparison_population()
        result = prepare_selection_comparison(rows, 'predeclared')
        budget = result['comparison_budgets'][0]
        self.assertEqual(budget['smart_candidates'], 30)
        self.assertEqual(budget['smart_controls'], 4)
        arms = {arm: [row for row in result['selected'] if row['comparison_arm'] == arm]
                for arm in ('smart', 'random')}
        self.assertEqual(len(arms['smart']), len(arms['random']))
        self.assertFalse({r['source_id'] for r in arms['smart']} &
                         {r['source_id'] for r in arms['random']})
        self.assertTrue(all(r['benchmark_inclusion_probability'] == 34/1000 for r in arms['random']))
        controls = [r for r in arms['smart'] if r['benchmark_region'] != 'credible_shallow_pointing']
        self.assertTrue(all(r['benchmark_inclusion_probability'] in (2/480, 2/490) for r in controls))
        self.assertEqual(result, prepare_selection_comparison(list(reversed(rows)), 'predeclared'))

    def test_comparison_rejects_old_masks_missing_sources_and_overlaps(self):
        rows = self.comparison_population()
        rows[0]['existing_sampling_probability'] = .5
        with self.assertRaisesRegex(ValueError, 'untouched'):
            prepare_selection_comparison(rows, 'old-mask')
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            prepare_selection_comparison([event(1), event(1)], 'overlap')
        with self.assertRaisesRegex(ValueError, 'two independent'):
            prepare_selection_comparison([event(1)], 'one-source')

    def test_comparison_keeps_positive_support_when_candidates_exceed_budget(self):
        rows = self.comparison_population()
        result = prepare_selection_comparison(rows, 'small', maximum_per_bin_arm=8)
        self.assertEqual(result['selected_events'], 16)
        self.assertEqual(result['comparison_budgets'][0]['smart_candidates'], 6)
        self.assertTrue(all(0 < c['benchmark_inclusion_probability'] <= 1 for c in result['strata']))
        self.assertTrue(any(c['benchmark_region'] == 'credible_shallow_pointing'
                            and c['benchmark_inclusion_probability'] < 1 for c in result['strata']))

    def test_complete_parent_unknown_muon_column_still_retained(self):
        row=event(1, 'unknown_column')
        self.assertEqual(benchmark_region(row), 'credible_shallow_pointing')
        result=prepare_benchmark([row], 'frozen-seed')
        self.assertEqual(result['selected'][0]['benchmark_inclusion_probability'], 1.)

    def test_unknown_zero_prefix_is_not_physical_shallow_path(self):
        row=event(1, 'unknown_parent')
        row['features']['independent_full_parent_pair_exists']=True
        self.assertEqual(benchmark_region(row), 'unknown_parent')

    def test_full_support_and_exact_case_control_probabilities(self):
        rows=[event(1)] + [event(i, 'border') for i in range(2,22)]
        result=prepare_benchmark(rows, 'before-outcomes')
        self.assertEqual(result['selected_events'], 6)
        controls=[r for r in result['selected'] if r['benchmark_region']=='border_pointing']
        self.assertEqual(len(controls),5)
        self.assertTrue(all(r['benchmark_inclusion_probability']==.25 for r in controls))
        self.assertTrue(result['positive_q_for_every_nonempty_population_stratum'])

    def test_existing_weights_probability_and_input_are_not_changed(self):
        rows=[event(i,'border',native=2.,probability=.5) for i in range(1,21)]
        original=copy.deepcopy(rows)
        result=prepare_benchmark(rows,'weights')
        row=result['selected'][0]
        self.assertEqual(rows,original)
        self.assertEqual(row['existing_sampling_probability'],.5)
        self.assertEqual(row['native_weight'],2.)
        self.assertEqual(row['original_worker_analysis_weight'],4.)
        self.assertEqual(row['diagnostic_analysis_weight'],16.)
        self.assertEqual(row['cumulative_study_probability'],.125)
        self.assertFalse(result['ordinary_campaign_publication_qualified'])

    def test_draw_is_independent_of_population_iteration_order(self):
        rows=[event(i,'deep') for i in range(1,31)]
        forward=prepare_benchmark(rows,'seed')['selected']
        reverse=prepare_benchmark(list(reversed(rows)),'seed')['selected']
        self.assertEqual([r['event_id'] for r in forward],[r['event_id'] for r in reverse])

    def test_explicit_unknown_physical_model_keeps_probability_one(self):
        rows=[event(i,'deep') for i in range(1,21)]
        for row in rows:row['unclassified_physical_model']=True
        result=prepare_benchmark(rows,'unknown-model')
        self.assertEqual(result['selected_events'],20)
        self.assertTrue(all(row['benchmark_inclusion_probability']==1 for row in result['selected']))

    def test_no_energy_cut_and_empty_population(self):
        row=event(1)
        self.assertLess(row['features']['best_pair_min_energy_GeV'],1.)
        self.assertEqual(prepare_benchmark([row],'soft')['selected_events'],1)
        self.assertEqual(prepare_benchmark([],'empty')['selected_events'],0)

    def test_duplicate_or_unsupported_process_or_invalid_probability_is_rejected(self):
        row=event(1)
        with self.assertRaisesRegex(ValueError,'Duplicate'):
            prepare_benchmark([row,copy.deepcopy(row)],'duplicate')
        row['stratum']='bsm_20toinf'
        with self.assertRaisesRegex(ValueError,'ordinary SM'):
            prepare_benchmark([row],'wrong-process')
        row=event(1,probability=0.)
        with self.assertRaisesRegex(ValueError,'frozen admission'):
            prepare_benchmark([row],'bad-p')

    def test_dy_and_jpsi_unknown_parents_are_protected_without_changing_qcd(self):
        for family, expected in [('qcd', 10), ('dy', 30), ('jpsi', 30)]:
            rows = [event(i, 'unknown_parent', native=2.) for i in range(1, 31)]
            for row in rows:
                row.update(stratum=family+'_20toinf', source_id=family+'_source')
            result = prepare_benchmark(rows, 'fixed-before-response')
            self.assertEqual(result['selected_events'], expected)
            self.assertTrue(all(r['benchmark_inclusion_probability'] == expected/30
                                for r in result['selected']))
            self.assertTrue(all(r['diagnostic_analysis_weight'] == 2.*30/expected
                                for r in result['selected']))

    def test_candidate_cap_keeps_exact_probability_and_unknown_parent_protection(self):
        rows = [event(i, native=2.) for i in range(1, 101)]
        rows += [event(i, 'unknown_parent') for i in range(101, 121)]
        for row in rows:
            row['stratum'] = 'dy_20toinf'
        result = prepare_benchmark(rows, 'before-response', maximum_candidates=10)
        candidates = [r for r in result['selected'] if r['benchmark_region']=='credible_shallow_pointing']
        self.assertEqual(len(candidates),10)
        self.assertTrue(all(r['benchmark_inclusion_probability']==.1 and
                            r['diagnostic_analysis_weight']==20. for r in candidates))
        self.assertEqual(sum(r['benchmark_region']=='unknown_parent' for r in result['selected']),20)

    def test_over_budget_and_zero_control_support_are_rejected(self):
        with self.assertRaisesRegex(ValueError,'event budget'):
            prepare_benchmark([event(i) for i in range(1,402)],'too-large')
        with self.assertRaisesRegex(ValueError,'positive sampling support'):
            prepare_benchmark([event(1,'deep')],'zero',controls=dict(
                border_pointing=5,deep_pointing=0,no_both_sides_pointing_pair=10,unknown_parent=10))

    def test_source_authority_and_selected_position_census_are_required(self):
        row,manifest,load=authority_fixture()
        validate_population([row],manifest,load)
        changed=copy.deepcopy(row)
        changed['gen_sha256']='c'*64
        with self.assertRaisesRegex(ValueError,'pristine source authority'):
            validate_population([changed],manifest,load)
        changed=copy.deepcopy(row)
        changed['selected_position']=1
        with self.assertRaisesRegex(ValueError,'selected position'):
            validate_population([changed],manifest,load)

    def test_event_identity_index_weight_and_old_probability_are_pinned(self):
        row,manifest,load=authority_fixture()
        changes=[('event_id',[1,1,99],'event identity'),
                 ('original_index',0,'Original GEN index'),
                 ('native_weight',2.5000000000000004,'Native GEN weight'),
                 ('existing_sampling_probability',.5,'sampling probability')]
        for field,value,message in changes:
            with self.subTest(field=field):
                changed=copy.deepcopy(row)
                changed[field]=value
                with self.assertRaisesRegex(ValueError,message):
                    validate_population([changed],manifest,load)

    def test_receipt_sha_and_full_original_exposure_are_pinned(self):
        row,manifest,load=authority_fixture()
        changed=copy.deepcopy(manifest)
        changed['sources'][0]['original_result_sha256']='c'*64
        with self.assertRaisesRegex(ValueError,'SHA-256 mismatch'):
            validate_population([row],changed,load)
        changed=copy.deepcopy(manifest)
        changed['sources'][0]['full_original_exposure']['full_source_trials']=101
        with self.assertRaisesRegex(ValueError,'full-GEN exposure'):
            validate_population([row],changed,load)

    def test_real_cli_requires_prior_receipt_pins(self):
        row,manifest,load=authority_fixture()
        del manifest['sources'][0]['original_result_sha256']
        with self.assertRaisesRegex(ValueError,'pinned SHA-256'):
            validate_population([row],manifest,load)

    def test_joint_counts_cannot_invent_a_pointing_opportunity(self):
        row=event(1)
        row['joint']['n_joint_pointing_pairs']=0
        with self.assertRaisesRegex(ValueError,'pointing-pair census'):
            benchmark_region(row)


if __name__ == '__main__':
    unittest.main()
