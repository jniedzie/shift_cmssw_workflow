# Code style: AI-code. AI-generated event-grouped response and CPU regression tests.
import copy
import hashlib
import json
import sys
from pathlib import Path
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from shift_material_response import (compare, bootstrap_comparison, shape_comparison,
                                     retained_tail_counts, response_reporting_scope, source_record, TARGETS,
                                     source_normalization, source_exposures, source_rate_weight,
                                     source_timing_input)
from shift_material_preselection import admission_probability


def source_receipt_fixture(directory, composite=True):
    from test_shift_weighted_gen_audit import fixture
    from test_qcd_mu_enrichment import filters
    from qcd_mu_enrichment import filtered_snapshot, PROFILE
    from shift_first_mpi_source_proposal import ALGORITHM, BASE_ALGORITHM, SCHEMA
    ledger, events, run = fixture()
    saved = events[:1]
    semantic = filtered_snapshot(ledger, ledger, saved, run, filters(ledger, saved))
    payloads = {'gen.root': b'GEN', 'resolved_gen_cfg.py': b'resolved config',
                'proposal_ledger.json': json.dumps(ledger).encode()}
    sha = lambda data: hashlib.sha256(data).hexdigest()
    semantic.update(complete=True, healthy=True, ledger=ledger, algorithm=BASE_ALGORITHM,
        generator_profile=PROFILE, weighted_run_info=run, filter_records=filters(ledger, saved),
        input_sha256=sha(payloads['gen.root']), ledger_sha256=sha(payloads['proposal_ledger.json']),
        config_sha256=sha(payloads['resolved_gen_cfg.py']), auditor_sha256='b'*64)
    payloads['semantic_audit.json'] = json.dumps(semantic).encode()
    request = dict(id='source', sample='qcd', stratum='qcd_5to10', bounds=[5., 10.],
                   trials=4, run=ledger['run'], stage='pilot')
    record = dict(schema='shift-weighted-gen-receipt-v3', complete=True, generated_edm=True,
        publication_complete=True, physics_valid=False, normalization_ready=False,
        generator_profile=PROFILE, algorithm=ALGORITHM if composite else BASE_ALGORITHM,
        request=request, ledger=ledger, semantic_audit=semantic, event_count=1,
        source_inputs={'audit_shift_weighted_gen.py': 'b'*64}, publication_directory='/synthetic/source')
    if composite:
        proposal = dict(schema=SCHEMA, algorithm=ALGORITHM, base_sampler_algorithm=BASE_ALGORITHM,
            mode='mixture', target='heavy', component_seed=123, overlay_sha256='a'*64,
            natural_decays=True, maximum_history_bytes=1024*1024)
        payloads.update({'hard_channel_history.jsonl': b'original full history', 'prefilter.root': b'prefilter GEN'})
        proof = dict(complete=True, passed=True, natural_decays=True, all_intrinsic_retry_paths_checked=True,
            source_only_native_weight_checked=True, algorithm=ALGORITHM, base_sampler_algorithm=BASE_ALGORITHM,
            source_proposal=proposal, full_source_trials=4, first_scatter_calls=ledger['proposal_calls'],
            prefilter_accepted_events=ledger['accepted'], saved_events=1,
            GEN_sha256=semantic['input_sha256'], ledger_sha256=semantic['ledger_sha256'],
            semantic_audit_sha256=sha(payloads['semantic_audit.json']),
            history_sha256=sha(payloads['hard_channel_history.jsonl']),
            prefilter_GEN_sha256=sha(payloads['prefilter.root']), auditor_sha256='c'*64)
        payloads['source_proposal_audit.json'] = json.dumps(proof).encode()
        record.update(source_proposal=proposal, source_proposal_audit=proof,
            publication_size_guard=dict(reserved_bytes=160000000))
        record['source_inputs'].update({'shift_first_mpi_source_proposal.py': 'c'*64,
                                       'libShiftMpiTailProposal.so': 'a'*64})
    record['artifacts'] = {name: dict(bytes=len(data), sha256=sha(data)) for name, data in payloads.items()}
    record['publication'] = [dict(name=name, path='/synthetic/source/'+name,
        independent_sha256_readback=True, **record['artifacts'][name]) for name in payloads]
    path = Path(directory)/'result.json'
    path.write_text(json.dumps(record))
    with zipfile.ZipFile(path.with_name('artifacts.zip'), 'w') as archive:
        for name, data in payloads.items():
            archive.writestr(name, data)
    return path, record


def jpsi_receipt_fixture(directory):
    from test_shift_weighted_gen_audit import fixture, configuration
    from audit_shift_weighted_gen import ALGORITHM, validate_config, validate_snapshot
    ledger, events, run = fixture(sample='direct_jpsi')
    config = configuration(ledger)
    audit = validate_snapshot(ledger, ledger, events, run)
    payloads = {'gen.root': b'GEN', 'resolved_gen_cfg.py': config.encode(),
                'proposal_ledger.json': json.dumps(ledger).encode()}
    sha = lambda value: hashlib.sha256(value).hexdigest()
    audit.update(schema='shift-weighted-gen-semantic-audit-v1', complete=True, healthy=True,
        sample='direct_jpsi', generator_profile='ordinary', requested=4, tried=4, bounds=[5., 10.],
        ledger=ledger, algorithm=ALGORITHM, weighted_run_info=run,
        forced_decay='443 -> 13 -13; branching convention remains an independent gate',
        input_sha256=sha(payloads['gen.root']), ledger_sha256=sha(payloads['proposal_ledger.json']),
        config_sha256=sha(payloads['resolved_gen_cfg.py']), auditor_sha256='b'*64,
        source_model_settings_sha256=validate_config(config, ledger))
    payloads['semantic_audit.json'] = json.dumps(audit).encode()
    request = dict(id='native_jpsi', sample='jpsi', stratum='jpsi_5to10', bounds=[5., 10.],
                   trials=4, run=ledger['run'])
    record = dict(schema='shift-weighted-gen-receipt-v3', complete=True, generated_edm=True,
        publication_complete=True, physics_valid=False, normalization_ready=False,
        generator_profile='ordinary', algorithm=ALGORITHM, request=request, ledger=ledger,
        semantic_audit=audit, event_count=2, publication_directory='/synthetic/jpsi',
        source_inputs={'audit_shift_weighted_gen.py': 'b'*64})
    record['artifacts'] = {name: dict(bytes=len(data), sha256=sha(data)) for name, data in payloads.items()}
    record['publication'] = [dict(name=name, path='/synthetic/jpsi/'+name,
        independent_sha256_readback=True, **record['artifacts'][name]) for name in payloads]
    path = Path(directory)/'result.json'
    path.write_text(json.dumps(record))
    with zipfile.ZipFile(path.with_name('artifacts.zip'), 'w') as archive:
        for name, data in payloads.items(): archive.writestr(name, data)
    return path, record


def dy_receipt_fixture(directory):
    from fixed_target_generation import beam_settings, process_settings
    directory = Path(directory)
    contract = dict(schema='shift-gen-pilot-v1', sample='dy', year=2023, requested_events=4,
        run_number=1200001, lower=10., upper=20., bin_variable='mHat', filtering='none',
        forced_decay='23 -> 13 -13', detector_simulated=False, physics_valid=False,
        normalization_ready=False, process_settings=process_settings('dy', 10., 20.),
        beam_settings=beam_settings(6800.), beam_energy_GeV=6800.,
        decay_policy='CMS lifetime cutoff; long-lived particles retained for transport',
        source_model='provisional on-axis stationary proton target; no nuclear model')
    audit = dict(counts={'events': 4}, hard_process_codes={'221': 4}, sum_weights=4.,
        sum_weights_squared=4., run_info=[dict(internal_xsec_pb=100., error_pb=5.)],
        generator_filter='none', physics_valid=False, normalization_ready=False,
        generated_mass_range=[10.1, 19.9])
    (directory/'gen.root').write_bytes(b'synthetic native DY GEN')
    (directory/'contract.json').write_text(json.dumps(contract))
    (directory/'validation.json').write_text(json.dumps(audit))
    record = dict(schema='shift-dag-receipt-v1', complete=True, stratum='dy_10to20',
        chunk=0, requested_events=4, events=4, event_ids=[[1200001, 1, i] for i in range(1, 5)],
        semantic_audit=audit, physics_valid=False, normalization_ready=False)
    record['artifacts'] = [dict(path='/synthetic/dy/'+name, bytes=(directory/name).stat().st_size,
        sha256=hashlib.sha256((directory/name).read_bytes()).hexdigest())
        for name in ('gen.root', 'contract.json', 'validation.json')]
    path = directory/'receipt.json'; path.write_text(json.dumps(record))
    return path, record


class SourceRecordTest(unittest.TestCase):
    def test_weighted_jpsi_uses_full_trials_not_saved_count_and_does_not_guess_BR(self):
        with tempfile.TemporaryDirectory() as directory:
            path, _ = jpsi_receipt_fixture(directory)
            source = source_record(path, family='jpsi')
            self.assertEqual(source['saved_events'], 2)
            self.assertEqual(source_normalization(source), ('fixed_source_trials', 4, 65.e9))
            self.assertEqual(source_rate_weight(source, .2), .2*65.e9/4)
            self.assertFalse(source['forced_decay_branching_convention_ready'])
            self.assertFalse(source['additional_branching_factor_applied'])
            self.assertFalse(source['physics_normalization_ready'])

    def test_jpsi_rejects_changed_forced_decay_and_nonordinary_source(self):
        for change in (lambda r: r['semantic_audit'].update(forced_decay='none'),
                       lambda r: r.update(generator_profile='shift-qcd-mu-enriched-cylinder-v1'),
                       lambda r: r['request'].update(trials=2),
                       lambda r: r.update(source_proposal={'target': 'heavy'})):
            with tempfile.TemporaryDirectory() as directory:
                path, record = jpsi_receipt_fixture(directory)
                change(record); path.write_text(json.dumps(record))
                with self.assertRaises(ValueError): source_record(path, family='jpsi')

    def test_native_dy_uses_accepted_GEN_denominator_and_pools_chunk_cross_sections(self):
        with tempfile.TemporaryDirectory() as directory:
            path, _ = dy_receipt_fixture(directory)
            source = source_record(path, family='dy', audit_directory=directory)
            self.assertEqual(source_normalization(source), ('accepted_gen_events', 4, 100.))
            self.assertNotIn('full_source_trials', source)
            self.assertNotIn('sigma_nd_pb', source)
            self.assertEqual(source_rate_weight(source, 1.), 25.)
            other = dict(source, gen_sha256='b'*64, full_source_events=6, native_cross_section_pb=120.)
            counts, kinds = source_exposures([source, other])
            self.assertEqual(kinds, {'dy_10to20': 'accepted_gen_events'})
            self.assertEqual(counts, {'dy_10to20': 10})
            self.assertEqual(source_rate_weight(source, 1., 10), 10.)
            self.assertEqual(source_rate_weight(other, 1., 10), 12.)
            self.assertFalse(source['forced_decay_branching_convention_ready'])
            self.assertEqual(source_timing_input(source), 'VtxSmeared')
            with self.assertRaises(ValueError):
                source_timing_input(dict(source, timing_input_label='generatorSmeared'))

    def test_legacy_QCD_and_native_jpsi_keep_original_untimed_smeared_input(self):
        self.assertEqual(source_timing_input({}), 'generatorSmeared')
        with tempfile.TemporaryDirectory() as directory:
            path, _ = jpsi_receipt_fixture(directory)
            self.assertEqual(source_timing_input(source_record(path, family='jpsi')), 'generatorSmeared')
        with self.assertRaises(ValueError): source_timing_input(dict(timing_input_label='invented'))

    def test_dy_rejects_changed_native_count_process_filter_and_artifact(self):
        for change in (lambda r: r.update(events=3),
                       lambda r: r['event_ids'][0].__setitem__(1, 2),
                       lambda r: r['semantic_audit'].update(hard_process_codes={'101': 4}),
                       lambda r: r['semantic_audit'].update(generator_filter='muon filter'),
                       lambda r: r['artifacts'][0].update(sha256='0'*64)):
            with tempfile.TemporaryDirectory() as directory:
                path, record = dy_receipt_fixture(directory)
                change(record); path.write_text(json.dumps(record))
                with self.assertRaises(ValueError): source_record(path, family='dy', audit_directory=directory)

    def test_explicit_family_and_unambiguous_denominator_are_required(self):
        with tempfile.TemporaryDirectory() as directory:
            path, _ = dy_receipt_fixture(directory)
            with self.assertRaises(ValueError): source_record(path, family='signal')
            with self.assertRaises(ValueError): source_record(path, family='dy')
            dy = source_record(path, family='dy', audit_directory=directory)
            with self.assertRaises(ValueError): source_normalization(dict(dy, full_source_trials=4))
            qcd = dict(stratum=dy['stratum'], gen_sha256='c'*64, full_source_trials=4, sigma_nd_pb=65.e9)
            with self.assertRaises(ValueError): source_exposures([qcd, dy])
            with self.assertRaises(ValueError): source_exposures([dy, dy])

    def test_composite_source_keeps_native_weight_and_full_before_filter_exposure(self):
        with tempfile.TemporaryDirectory() as directory:
            path, _ = source_receipt_fixture(directory)
            source = source_record(path)
            self.assertEqual(source['saved_events'], 1)
            self.assertEqual(source['full_source_trials'], 4)
            self.assertEqual(source['sigma_nd_pb'], 65.e9)
            self.assertEqual(source['mean_abs_native_weight'], .2)
            self.assertTrue(source['native_weight_already_contains_source_correction'])
            self.assertFalse(source['physics_normalization_ready'])

    def test_native_base_source_remains_supported_without_composite_proof(self):
        with tempfile.TemporaryDirectory() as directory:
            path, _ = source_receipt_fixture(directory, False)
            source = source_record(path)
            self.assertNotIn('source_proposal', source)
            self.assertEqual(source['full_source_trials'], 4)

    def test_composite_audit_population_provenance_and_publication_mutations_fail(self):
        changes = [lambda r: r['source_proposal_audit'].update(passed=False),
                   lambda r: r['source_proposal_audit'].update(saved_events=2),
                   lambda r: r['source_proposal_audit'].update(history_sha256='d'*64),
                   lambda r: r['source_proposal_audit'].update(prefilter_GEN_sha256='d'*64),
                   lambda r: r['source_proposal'].update(natural_decays=False),
                   lambda r: r['request'].update(trials=1),
                   lambda r: r['semantic_audit']['weights'].__setitem__(0, .4),
                   lambda r: r['publication'][-1].update(independent_sha256_readback=False)]
        for change in changes:
            with tempfile.TemporaryDirectory() as directory:
                path, record = source_receipt_fixture(directory)
                change(record);path.write_text(json.dumps(record))
                with self.assertRaises(ValueError):
                    source_record(path)

    def test_composite_source_requires_actual_sha_bound_returned_audit_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path, _ = source_receipt_fixture(directory)
            with zipfile.ZipFile(path.with_name('artifacts.zip')) as archive:
                payloads = {name: archive.read(name) for name in archive.namelist()}
            payloads['source_proposal_audit.json'] = b'{"passed":true}'
            with zipfile.ZipFile(path.with_name('artifacts.zip'), 'w') as archive:
                for name, data in payloads.items():
                    archive.writestr(name, data)
            with self.assertRaisesRegex(ValueError, 'source-path proof'):
                source_record(path)

    def test_recorded_census_and_filter_arithmetic_are_checked_without_synthetic_process_fields(self):
        changes = [lambda r: r['semantic_audit']['event_ids'][0].__setitem__(2, 5),
                   lambda r: r['semantic_audit']['weights'].__setitem__(0, float('nan')),
                   lambda r: r['semantic_audit']['scale'].__setitem__(0, 10.),
                   lambda r: r['semantic_audit']['charged'].__setitem__(0, -1),
                   lambda r: r['semantic_audit']['filter_records'][0].update(pass_positive=0),
                   lambda r: r['semantic_audit']['filter_records'][0].update(sum_pass_weights=.4),
                   lambda r: r['semantic_audit']['weighted_run_info'].update(filter_efficiency=.5)]
        for change in changes:
            with tempfile.TemporaryDirectory() as directory:
                path, record = source_receipt_fixture(directory, False)
                change(record);path.write_text(json.dumps(record))
                with self.assertRaises(ValueError):
                    source_record(path)

    def test_native_audit_bytes_are_required_for_base_source_too(self):
        with tempfile.TemporaryDirectory() as directory:
            path, _ = source_receipt_fixture(directory, False)
            with zipfile.ZipFile(path.with_name('artifacts.zip'), 'w') as archive:
                archive.writestr('semantic_audit.json', b'{}')
                archive.writestr('proposal_ledger.json', b'{}')
            with self.assertRaisesRegex(ValueError, 'Native source accounting bytes'):
                source_record(path)


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

    def test_native_DY_and_jpsi_reporting_do_not_claim_QCD_cylinder_filter(self):
        for family in ('dy', 'jpsi'):
            plan = dict(sources={'a': dict(source_family=family)},
                samples=[dict(source_id='a', event_ids=[[1, 1, 1]], probability=1.)])
            scope = response_reporting_scope(plan)
            self.assertNotIn('finite-cylinder', scope['source_population_scope'])
            self.assertNotIn('mu-enriched', scope['source_population_scope'])
            self.assertIn('Forced-decay', scope['source_population_scope'])
            self.assertFalse(scope['inclusive_reconstructed_tail_rates_estimable'])

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
