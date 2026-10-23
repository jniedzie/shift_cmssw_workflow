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
                                     source_timing_input, prepared_source_record, validate_prepared_source,
                                     source_origin_sha256, GENPARTICLE_CONDITIONING_SCHEMA,
                                     GENPARTICLE_ROUNDOFF_ALGORITHM, response_source)
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


def jpsi_receipt_fixture(directory, family='jpsi'):
    from test_shift_weighted_gen_audit import fixture, configuration
    from audit_shift_weighted_gen import ALGORITHM, validate_config, validate_snapshot
    sample = 'direct_jpsi' if family == 'jpsi' else 'qcd'
    ledger, events, run = fixture(sample=sample)
    config = configuration(ledger)
    audit = validate_snapshot(ledger, ledger, events, run)
    payloads = {'gen.root': b'GEN', 'resolved_gen_cfg.py': config.encode(),
                'proposal_ledger.json': json.dumps(ledger).encode()}
    sha = lambda value: hashlib.sha256(value).hexdigest()
    audit.update(schema='shift-weighted-gen-semantic-audit-v1', complete=True, healthy=True,
        sample=sample, generator_profile='ordinary', requested=4, tried=4, bounds=[5., 10.],
        ledger=ledger, algorithm=ALGORITHM, weighted_run_info=run,
        forced_decay=('443 -> 13 -13; branching convention remains an independent gate'
                      if family == 'jpsi' else 'none'),
        input_sha256=sha(payloads['gen.root']), ledger_sha256=sha(payloads['proposal_ledger.json']),
        config_sha256=sha(payloads['resolved_gen_cfg.py']), auditor_sha256='b'*64,
        source_model_settings_sha256=validate_config(config, ledger))
    payloads['semantic_audit.json'] = json.dumps(audit).encode()
    source_sample = 'jpsi' if family == 'jpsi' else 'qcd'
    request = dict(id='native_'+source_sample, sample=source_sample, stratum=source_sample+'_5to10', bounds=[5., 10.],
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


def ordinary_hf_receipt_fixture(directory, mode='mixture'):
    """Synthetic unfiltered fixed-trial receipt using the native HF proof fields."""
    from shift_first_mpi_source_proposal import ALGORITHM, BASE_ALGORITHM, SCHEMA
    path, record = jpsi_receipt_fixture(directory, family='qcd_ordinary')
    with zipfile.ZipFile(path.with_name('artifacts.zip')) as archive:
        payloads = {name: archive.read(name) for name in archive.namelist()}
    payloads['resolved_gen_cfg.py'] += b"""
process.ProductionFilterSequence=cms.Sequence(process.generator)
process.generation_step=cms.Path(process.ProductionFilterSequence+process.pgen)
process.FEVTDEBUGoutput=cms.OutputModule('PoolOutputModule',SelectEvents=cms.untracked.PSet(SelectEvents=cms.vstring('generation_step')))
"""
    digest = lambda data: hashlib.sha256(data).hexdigest()
    record['semantic_audit']['config_sha256'] = digest(payloads['resolved_gen_cfg.py'])
    payloads['semantic_audit.json'] = json.dumps(record['semantic_audit']).encode()
    payloads.update({'hard_channel_history.jsonl': b'complete native first-MPI history',
                     'prefilter.root': b'complete unfiltered native accepted GEN'})
    proposal = dict(schema=SCHEMA, algorithm=ALGORITHM, base_sampler_algorithm=BASE_ALGORITHM,
        mode=mode, target='heavy', component_seed=123, overlay_sha256='a'*64,
        natural_decays=True, maximum_history_bytes=1024*1024)
    ledger = record['ledger']
    proof = dict(schema='shift-native-first-mpi-source-audit-v1', complete=True, passed=True,
        natural_decays=True, all_intrinsic_retry_paths_checked=True, source_only_native_weight_checked=True,
        generator_profile='ordinary', decay_dependent_filter=False, saved_events_equal_native_acceptance=True,
        requires_full_muon_completion=True, physics_valid=False, normalization_ready=False,
        algorithm=ALGORITHM, base_sampler_algorithm=BASE_ALGORITHM, source_proposal=proposal,
        full_source_trials=ledger['tried'], first_scatter_calls=ledger['proposal_calls'],
        prefilter_accepted_events=ledger['accepted'], saved_events=record['event_count'],
        GEN_sha256=digest(payloads['gen.root']), ledger_sha256=digest(payloads['proposal_ledger.json']),
        semantic_audit_sha256=digest(payloads['semantic_audit.json']),
        history_sha256=digest(payloads['hard_channel_history.jsonl']),
        history_bytes=len(payloads['hard_channel_history.jsonl']),
        prefilter_GEN_sha256=digest(payloads['prefilter.root']), prefilter_GEN_bytes=len(payloads['prefilter.root']),
        auditor_sha256='c'*64)
    record.update(algorithm=ALGORITHM, source_proposal=proposal, source_proposal_audit=proof,
                  publication_size_guard={'reserved_bytes':160000000})
    record['request']['stage'] = 'pilot'
    record['source_inputs'].update({'shift_first_mpi_source_proposal.py':'c'*64,
                                     'libShiftMpiTailProposal.so':'a'*64})
    write_ordinary_hf_fixture(path, record, payloads)
    return path, record, payloads


def write_ordinary_hf_fixture(path, record, payloads):
    payloads['semantic_audit.json'] = json.dumps(record['semantic_audit']).encode()
    payloads['source_proposal_audit.json'] = json.dumps(record['source_proposal_audit']).encode()
    record['artifacts'] = {name:dict(bytes=len(data),sha256=hashlib.sha256(data).hexdigest())
                           for name,data in payloads.items()}
    record['publication'] = [dict(name=name,path=record['publication_directory']+'/'+name,
        independent_sha256_readback=True,**record['artifacts'][name]) for name in payloads]
    path.write_text(json.dumps(record))
    with zipfile.ZipFile(path.with_name('artifacts.zip'),'w') as archive:
        for name,data in payloads.items():archive.writestr(name,data)


def preparation_receipt_fixture(directory, raw_source, event_ids, native_weights, payload=b'prepared GEN'):
    """Use the real full-GEN canary receipt fields, with no physical simulation."""
    metadata = dict(runs=[dict(run=event_ids[0][0], original='full source run')],
                    lumis=[dict(run=event_ids[0][0], lumi=1, original='full source luminosity')])
    family = 'qcd' if raw_source['source_family'] == 'qcd_ordinary' else raw_source['source_family']
    events = []
    for identity, weight in zip(event_ids, native_weights):
        closure = dict(exact_p4_graph_identity_weights_units_RNG=True, maximum_vertex_residual_mm=0.,
                       before_graph_sha256='a'*64, after_graph_sha256='b'*64, particles=2, vertices=1)
        completion = dict(all_original_p4_and_ancestry_preserved=True, ordinary_fraction=1.,
            proposal_density_ratio=1., proposal_correction=1., completed_unshifted_graph_sha256='c'*64,
            enabled_PDG_ids=[211,321], parent_barcodes=[], parent_PDG_ids=[], sampled_proper_lifetime_mm=[],
            nominal_proper_lifetime_mm=[], decayed_parents=0, added_stable_muons=0, added_particles=0,
            added_vertices=0, maximum_p4_residual_GeV=0., maximum_proper_clock_residual_mm=0.)
        events.append(dict(event_id=identity, native_weight=weight, target_position_cm=[-6., 0., 14840.],
            placement=dict(closure), completion=completion,
            genParticles=dict(stock_persistence_encoding_exact=True, exact_PDG_status_and_barcode_ancestry=True,
                stock_persistence_schema_sha256='66c6124fc1d1580acefe469c404ac143c95ba833aed9fcd8117698c2dcec62fa',
                encoding='native reco::GenParticle constructed from newly timed HepMC, then stock ROOT dictionary roundtrip',
                expected_encoded_p4_residual_GeV=0., expected_encoded_vertex_residual_cm=0.,
                maximum_p4_raw_to_encoded_residual_GeV=0., maximum_vertex_residual_cm=0.,
                rebuilt_process='SHIFTFULLGEN', particles=2),
            nominal_timing=dict(closure, before_graph_sha256='c'*64, after_graph_sha256='d'*64),
            timing_metadata=dict(sourceXmm=-60.,sourceYmm=0.,sourceZmm=148400.,sourceCtBeforeMm=0.,
                                 appliedShiftCtMm=-148400.), DY_existing_nominal_graph_exact=family == 'dy'))
    kind, count, sigma = source_normalization(raw_source)
    prepared_sha = hashlib.sha256(payload).hexdigest()
    unit = dict(family=family, source=raw_source, source_GEN_sha256=raw_source['gen_sha256'],
        source_full_accounting=metadata, native_denominator_kind=kind, native_denominator=count,
        native_source_cross_section_pb=sigma, full_Run_Lumi_preserved=True, complete_natural_decays=True,
        GENParticles_rebuilt=True, prepared_process='SHIFTFULLGEN', completed_unshifted_label='shiftFullMuonDecays',
        raw_unshifted_label='shiftSourcePlacement', raw_unshifted_instance='original',
        events=events, output_sha256=prepared_sha, output_bytes=len(payload))
    audit = dict(schema='shift-assumed-jaw-full-GEN-canary-v1', complete=True,
        publication_sha256_readback_verified=True, new_GEN_exposure=0, generator_run=False,
        SIM_or_detector_run=False, physics_normalization_ready=False, source_distribution_qualified=False,
        full_production_ready=False, target_map_sha256='f'*64, source_assumption={'diagnostic': True},
        units=[unit], events=len(events), published=[dict(name=family+'_full_GEN.root',
        path='/eos/synthetic/'+family+'_full_GEN.root', sha256=prepared_sha, bytes=len(payload),
        independent_sha256_readback=True)])
    path = Path(directory)/'complete.json';path.write_text(json.dumps(audit))
    return path, audit


class PreparedSourceTests(unittest.TestCase):
    def test_response_source_supports_exact_inline_or_frozen_source_without_path_escape(self):
        source = {'source_id':'synthetic', 'native_weight':.25}
        self.assertIs(response_source({'source':source}, '/not/used'), source)
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            root=Path(directory);path=root/'sources/prepared.json';path.parent.mkdir()
            path.write_text(json.dumps(source));descriptor=dict(path='sources/prepared.json',
                sha256=hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(response_source({'source_descriptor':descriptor}, root), source)
            invalid=[{}, {'source':source,'source_descriptor':descriptor},
                {'source_descriptor':dict(descriptor,sha256='e'*64)},
                {'source_descriptor':dict(descriptor,path=str(path))},
                {'source_descriptor':dict(descriptor,path='../outside.json')},
                {'source_descriptor':dict(descriptor,path='missing.json')},
                {'source_descriptor':dict(descriptor,path=[])},
                {'source_descriptor':dict(descriptor,extra=True)}]
            escaped=Path(outside)/'source.json';escaped.write_bytes(path.read_bytes())
            (root/'escaped.json').symlink_to(escaped)
            (root/'escaped_directory').symlink_to(Path(outside),target_is_directory=True)
            invalid.extend([{'source_descriptor':dict(descriptor,path='escaped.json')},
                {'source_descriptor':dict(descriptor,path='escaped_directory/source.json')}])
            for request in invalid:
                with self.subTest(request=request), self.assertRaises(ValueError):
                    response_source(request, root)

    def conditioned_truth(self, truth):
        truth.update(stock_persistence_audit_schema=GENPARTICLE_CONDITIONING_SCHEMA,
            roundoff_algorithm=GENPARTICLE_ROUNDOFF_ALGORITHM, stock_persistence_encoding_exact=False,
            exact_encoded_pt_eta_phi_xyz_vertex=True, expected_encoded_xyz_residual_GeV=0.,
            expected_encoded_p4_residual_GeV=1.11e-16, mass_shell_roundoff_checks_passed=True,
            roundoff_operations=8, roundoff_unit=2.**-53, mass_float32_unit=2.**-24,
            mass_float32_min_subnormal=2.**-149, maximum_signed_mass2_residual_GeV2=1.11e-16,
            maximum_signed_mass2_bound_GeV2=1.5e-15, maximum_signed_mass2_bound_fraction=.7,
            maximum_energy2_residual_GeV2=2.22e-16, maximum_energy2_bound_GeV2=1.e-15,
            maximum_energy2_bound_fraction=.4)

    def make_raw(self, directory, family='qcd_ordinary'):
        if family == 'dy':
            path, record = dy_receipt_fixture(directory)
            source = source_record(path, family=family, audit_directory=directory)
            return source, record['event_ids'][:1], [1.]
        path, record = jpsi_receipt_fixture(directory, family=family)
        source = source_record(path, family=family)
        return source, record['semantic_audit']['event_ids'][:1], record['semantic_audit']['weights'][:1]

    def test_all_native_families_keep_raw_exposure_weights_and_distinct_prepared_input(self):
        for family in ('qcd_ordinary', 'dy', 'jpsi'):
            with self.subTest(family=family), tempfile.TemporaryDirectory() as directory:
                raw, ids, weights = self.make_raw(directory, family)
                path, _ = preparation_receipt_fixture(directory, raw, ids, weights)
                prepared = prepared_source_record(raw, path, expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
                self.assertEqual(source_origin_sha256(prepared), raw['gen_sha256'])
                self.assertNotEqual(prepared['gen_sha256'], raw['gen_sha256'])
                self.assertEqual(source_normalization(prepared), source_normalization(raw))
                self.assertEqual(source_rate_weight(prepared, weights[0]), source_rate_weight(raw, weights[0]))
                self.assertEqual(prepared['prepared_event_ids'], ids)
                self.assertEqual(prepared['prepared_native_weights'], weights)
                self.assertEqual(prepared['timing_input_process'], 'SHIFTFULLGEN')
                self.assertEqual(source_timing_input(prepared), 'shiftFullMuonDecays')
                self.assertTrue(prepared['muon_enhancement_complete'])
                self.assertFalse(prepared['requires_full_muon_completion'])
                self.assertFalse(prepared['physics_normalization_ready'])
                sample = dict(indices=[0], event_ids=ids, native_weights=weights, probabilities=[.25])
                validate_prepared_source(prepared, sample)
                self.assertEqual(sample['probabilities'], [.25])
                with self.assertRaises(ValueError): validate_prepared_source(prepared, dict(sample, native_weights=[99.]))
                with self.assertRaises(ValueError): validate_prepared_source(prepared, dict(sample, indices=[1]))
                with self.assertRaises(ValueError): validate_prepared_source(dict(prepared, gen_sha256='e'*64))

    def test_semantic_mutations_fail_even_with_updated_native_receipt_SHA(self):
        changes = [lambda a:a.update(complete=False), lambda a:a.update(new_GEN_exposure=1),
            lambda a:a.update(publication_sha256_readback_verified=False),
            lambda a:a['units'][0].update(source_GEN_sha256='e'*64),
            lambda a:a['units'][0].update(native_denominator=2),
            lambda a:a['units'][0].update(full_Run_Lumi_preserved=False),
            lambda a:a['units'][0].update(source_full_accounting={}),
            lambda a:a['units'][0].update(complete_natural_decays=False),
            lambda a:a['units'][0].update(completed_unshifted_label='generatorSmeared'),
            lambda a:a['units'][0].update(raw_unshifted_label='shiftRawSource'),
            lambda a:a['units'][0].pop('raw_unshifted_instance'),
            lambda a:a['units'][0].update(raw_unshifted_instance=''),
            lambda a:a['units'][0].update(prepared_process='OLDGEN'),
            lambda a:a['units'][0]['events'][0]['completion'].update(proposal_correction=2.),
            lambda a:a['units'][0]['events'][0]['completion'].pop('parent_barcodes'),
            lambda a:a['units'][0]['events'][0]['genParticles'].update(exact_PDG_status_and_barcode_ancestry=False),
            lambda a:a['units'][0]['events'][0]['genParticles'].update(stock_persistence_encoding_exact=False),
            lambda a:a['units'][0]['events'][0]['genParticles'].pop('stock_persistence_encoding_exact'),
            lambda a:a['units'][0]['events'][0]['genParticles'].update(stock_persistence_schema_sha256='e'*64),
            lambda a:a['units'][0]['events'][0]['genParticles'].update(encoding='raw HepMC doubles'),
            lambda a:a['units'][0]['events'][0]['genParticles'].update(expected_encoded_p4_residual_GeV=1.e-12),
            lambda a:a['units'][0]['events'][0]['genParticles'].update(expected_encoded_vertex_residual_cm=1.e-12),
            lambda a:a['units'][0]['events'][0]['genParticles'].pop('maximum_vertex_residual_cm'),
            lambda a:a['units'][0]['events'][0]['genParticles'].update(maximum_vertex_residual_cm=-1.),
            lambda a:a['units'][0]['events'][0]['genParticles'].update(maximum_p4_raw_to_encoded_residual_GeV=float('nan')),
            lambda a:a['units'][0]['events'][0]['nominal_timing'].update(before_graph_sha256='e'*64),
            lambda a:a['units'][0]['events'][0]['timing_metadata'].update(appliedShiftCtMm=-296800.),
            lambda a:a['published'][0].update(sha256='e'*64)]
        for change in changes:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                raw, ids, weights = self.make_raw(directory)
                path, audit = preparation_receipt_fixture(directory, raw, ids, weights)
                change(audit);path.write_text(json.dumps(audit))
                with self.assertRaises(ValueError):
                    prepared_source_record(raw, path, expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest())

    def test_stock_encoding_can_change_raw_doubles_but_expected_persisted_values_must_match_exactly(self):
        for family in ('qcd_ordinary', 'dy', 'jpsi'):
            with self.subTest(family=family), tempfile.TemporaryDirectory() as directory:
                raw, ids, weights = self.make_raw(directory, family)
                path, audit = preparation_receipt_fixture(directory, raw, ids, weights)
                truth = audit['units'][0]['events'][0]['genParticles']
                truth.update(maximum_vertex_residual_cm=.0007, maximum_p4_raw_to_encoded_residual_GeV=.002)
                path.write_text(json.dumps(audit))
                source = prepared_source_record(raw, path, expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
                self.assertEqual(source_rate_weight(source, weights[0]), source_rate_weight(raw, weights[0]))
                self.assertEqual(source['prepared_event_ids'], ids)
                self.assertEqual(source['prepared_native_weights'], weights)

    def test_conditioning_V2_preserves_each_family_identity_weights_and_native_exposure(self):
        for family in ('qcd_ordinary', 'dy', 'jpsi'):
            with self.subTest(family=family), tempfile.TemporaryDirectory() as directory:
                raw, ids, weights = self.make_raw(directory, family)
                path, audit = preparation_receipt_fixture(directory, raw, ids, weights)
                self.conditioned_truth(audit['units'][0]['events'][0]['genParticles'])
                path.write_text(json.dumps(audit))
                source = prepared_source_record(raw, path, expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
                self.assertEqual(source['prepared_event_ids'], ids)
                self.assertEqual(source['prepared_native_weights'], weights)
                self.assertEqual(source_normalization(source), source_normalization(raw))
                self.assertEqual(source_rate_weight(source, weights[0]), source_rate_weight(raw, weights[0]))
                validate_prepared_source(source, dict(indices=[0], event_ids=ids,
                    native_weights=weights, probabilities=[.25]))

    def test_conditioning_V3_requires_pinned_eta_operations_and_exact_other_coordinates(self):
        from shift_material_response import (GENPARTICLE_ETA_CONDITIONING_SCHEMA,
            GENPARTICLE_ETA_ALGORITHM, GENPARTICLE_ETA_HEADER_SHA256)
        changes=[None, lambda t:t.update(eta_roundoff_checks_passed=False),
            lambda t:t.update(eta_roundoff_algorithm='loose'),
            lambda t:t.update(eta_header_sha256='e'*64),
            lambda t:t.update(exact_encoded_pt_phi_xy_vertex=False),
            lambda t:t.update(exact_polar_cartesian_z_cache=False),
            lambda t:t.update(eta_reference_differences=-1),
            lambda t:t.update(eta_reference_differences=True)]
        for change in changes:
            with self.subTest(change=change),tempfile.TemporaryDirectory() as directory:
                raw,ids,weights=self.make_raw(directory)
                path,audit=preparation_receipt_fixture(directory,raw,ids,weights)
                truth=audit['units'][0]['events'][0]['genParticles'];self.conditioned_truth(truth)
                truth.update(stock_persistence_audit_schema=GENPARTICLE_ETA_CONDITIONING_SCHEMA,
                    eta_roundoff_algorithm=GENPARTICLE_ETA_ALGORITHM,
                    eta_header_sha256=GENPARTICLE_ETA_HEADER_SHA256,
                    eta_roundoff_checks_passed=True,eta_reference_differences=0,
                    exact_encoded_pt_phi_xy_vertex=True,exact_polar_cartesian_z_cache=True)
                truth.pop('exact_encoded_pt_eta_phi_xyz_vertex')
                if change:change(truth)
                path.write_text(json.dumps(audit));checksum=hashlib.sha256(path.read_bytes()).hexdigest()
                if change:
                    with self.assertRaises(ValueError):prepared_source_record(raw,path,expected_sha256=checksum)
                else:
                    source=prepared_source_record(raw,path,expected_sha256=checksum)
                    self.assertEqual(source_normalization(source),source_normalization(raw))

    def test_conditioning_V2_requires_exact_coordinates_pinned_algorithm_and_per_particle_bounds(self):
        changes = [
            lambda t:t.pop('stock_persistence_audit_schema'),
            lambda t:t.update(stock_persistence_audit_schema='unknown'),
            lambda t:t.update(stock_persistence_encoding_exact=True),
            lambda t:t.update(exact_encoded_pt_eta_phi_xyz_vertex=False),
            lambda t:t.update(exact_PDG_status_and_barcode_ancestry=False),
            lambda t:t.update(expected_encoded_xyz_residual_GeV=1.e-16),
            lambda t:t.update(expected_encoded_vertex_residual_cm=1.e-16),
            lambda t:t.update(stock_persistence_schema_sha256='e'*64),
            lambda t:t.update(encoding='raw doubles'),
            lambda t:t.update(mass_shell_roundoff_checks_passed=False),
            lambda t:t.update(roundoff_algorithm='loose tolerance'),
            lambda t:t.update(roundoff_operations=9),
            lambda t:t.update(roundoff_unit=2.**-52),
            lambda t:t.update(mass_float32_unit=2.**-23),
            lambda t:t.update(mass_float32_min_subnormal=0.),
            lambda t:t.update(expected_encoded_p4_residual_GeV=float('nan')),
            lambda t:t.update(maximum_signed_mass2_bound_fraction=1.01),
            lambda t:t.update(maximum_energy2_bound_fraction=1.01),
            lambda t:t.update(maximum_signed_mass2_residual_GeV2=2.),
            lambda t:t.update(maximum_energy2_residual_GeV2=2.),
            lambda t:t.update(maximum_signed_mass2_bound_GeV2=-1.),
            lambda t:t.update(maximum_energy2_bound_GeV2=float('inf')),
            lambda t:t.update(maximum_energy2_bound_fraction=float('nan')),
            lambda t:t.update(maximum_signed_mass2_bound_fraction=-.1),
            lambda t:t.pop('maximum_energy2_residual_GeV2')]
        for change in changes:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                raw, ids, weights = self.make_raw(directory)
                path, audit = preparation_receipt_fixture(directory, raw, ids, weights)
                truth = audit['units'][0]['events'][0]['genParticles']
                self.conditioned_truth(truth);change(truth);path.write_text(json.dumps(audit))
                with self.assertRaises(ValueError):
                    prepared_source_record(raw, path, expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest())

    def test_prepared_event_must_match_original_native_census_not_just_internal_proof(self):
        with tempfile.TemporaryDirectory() as directory:
            raw, ids, weights = self.make_raw(directory)
            path, _ = preparation_receipt_fixture(directory, raw, ids, [weights[0]*2.])
            with self.assertRaisesRegex(ValueError, 'original source census'):
                prepared_source_record(raw, path, expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest())

    def test_preparation_is_required_and_original_exposure_is_not_repeated(self):
        with tempfile.TemporaryDirectory() as directory:
            raw, ids, weights = self.make_raw(directory)
            with self.assertRaises(ValueError): validate_prepared_source(dict(raw, timing_input_label='shiftFullMuonDecays'))
            path, _ = preparation_receipt_fixture(directory, raw, ids, weights)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            prepared = prepared_source_record(raw, path, expected_sha256=digest)
            with self.assertRaises(ValueError): prepared_source_record(raw, path, expected_sha256='0'*64)
            with self.assertRaises(ValueError): validate_prepared_source(dict(prepared, original_source_sha256='e'*64))
            path, _ = preparation_receipt_fixture(directory, raw, ids, weights, payload=b'another prepared serialization')
            other = prepared_source_record(raw, path, expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertNotEqual(prepared['gen_sha256'], other['gen_sha256'])
            with self.assertRaisesRegex(ValueError, 'Repeated original source exposure'):
                source_exposures([prepared, other])


class SourceRecordTest(unittest.TestCase):
    def test_ordinary_HF_and_disabled_control_keep_all_acceptance_native_W_and_full_trials(self):
        for mode in ('mixture','disabled'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                path, record, _ = ordinary_hf_receipt_fixture(directory,mode)
                source = source_record(path,family='qcd_ordinary')
                self.assertEqual(source['saved_events'],record['ledger']['accepted'])
                self.assertEqual(source['full_source_trials'],4)
                self.assertAlmostEqual(source['mean_abs_native_weight'],.3)
                self.assertEqual(source_rate_weight(source,.2),.2*65.e9/4)
                self.assertEqual(source['source_proposal']['mode'],mode)
                self.assertEqual(source['generator_algorithm'],record['algorithm'])
                self.assertTrue(source['native_weight_already_contains_source_correction'])
                self.assertTrue(source['requires_full_muon_completion'])
                self.assertFalse(source['muon_enhancement_complete'])
                self.assertFalse(source['decay_dependent_filter'])
                self.assertFalse(source['physics_normalization_ready'])
                with self.assertRaises(ValueError):source_record(path)
                with self.assertRaises(ValueError):source_record(path,family='jpsi')

    def test_ordinary_HF_requires_full_native_proof_even_with_rehashed_returned_bytes(self):
        changes = [lambda r:r['source_proposal_audit'].update(passed=False),
            lambda r:r.pop('generator_profile'),
            lambda r:r['semantic_audit'].pop('generator_profile'),
            lambda r:r.update(source_proposal=None),
            lambda r:r['source_proposal_audit'].pop('saved_events_equal_native_acceptance'),
            lambda r:r['source_proposal_audit'].update(decay_dependent_filter=True),
            lambda r:r['source_proposal_audit'].update(generator_profile='mu_enriched'),
            lambda r:r['source_proposal_audit'].update(requires_full_muon_completion=False),
            lambda r:r['source_proposal_audit'].update(full_source_trials=2),
            lambda r:r['source_proposal_audit'].update(prefilter_accepted_events=1,saved_events=1),
            lambda r:r['source_proposal_audit'].update(history_sha256='d'*64),
            lambda r:r['source_proposal_audit'].update(history_bytes=1),
            lambda r:r['source_proposal_audit'].update(prefilter_GEN_sha256='d'*64),
            lambda r:r['source_proposal_audit'].update(auditor_sha256='d'*64),
            lambda r:r['request'].update(stage='production')]
        for change in changes:
            with self.subTest(change=change),tempfile.TemporaryDirectory() as directory:
                path,record,payloads=ordinary_hf_receipt_fixture(directory)
                change(record);write_ordinary_hf_fixture(path,record,payloads)
                with self.assertRaises(ValueError):source_record(path,family='qcd_ordinary')

    def test_ordinary_HF_rejects_rehashed_hidden_filters_and_cylinder_or_censored_sources(self):
        for change in ('hidden_filter','filter_sequence','cylinder','subset'):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as directory:
                path,record,payloads=ordinary_hf_receipt_fixture(directory)
                config=payloads['resolved_gen_cfg.py']
                if change=='hidden_filter':config+=b"\nprocess.extra=cms.EDFilter('MCSmartSingleParticleFilter')\n"
                elif change=='filter_sequence':config=config.replace(b'cms.Sequence(process.generator)',b'cms.Sequence(process.generator+process.extra)')
                elif change=='cylinder':config=config.replace(b'ParticleDecays:tau0Max = 10',b'ParticleDecays:limitCylinder = on')
                else:
                    record['semantic_audit']['event_ids']=record['semantic_audit']['event_ids'][:1]
                    record['semantic_audit']['weights']=record['semantic_audit']['weights'][:1]
                payloads['resolved_gen_cfg.py']=config
                record['semantic_audit']['config_sha256']=hashlib.sha256(config).hexdigest()
                record['source_proposal_audit']['semantic_audit_sha256']=hashlib.sha256(json.dumps(record['semantic_audit']).encode()).hexdigest()
                write_ordinary_hf_fixture(path,record,payloads)
                with self.assertRaises(ValueError):source_record(path,family='qcd_ordinary')

    def test_ordinary_HF_proof_requires_the_actual_returned_SHA_bound_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path,_,payloads=ordinary_hf_receipt_fixture(directory)
            payloads['source_proposal_audit.json']=b'{"passed":true}'
            with zipfile.ZipFile(path.with_name('artifacts.zip'),'w') as archive:
                for name,data in payloads.items():archive.writestr(name,data)
            with self.assertRaisesRegex(ValueError,'native returned bytes'):
                source_record(path,family='qcd_ordinary')

    def test_raw_ordinary_QCD_is_explicit_and_requires_full_natural_muon_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            path, _ = jpsi_receipt_fixture(directory, family='qcd_ordinary')
            source = source_record(path, family='qcd_ordinary')
            self.assertEqual(source['generator_profile'], 'ordinary')
            self.assertEqual(source['source_family'], 'qcd_ordinary')
            self.assertEqual(source['forced_decay'], 'none')
            self.assertFalse(source['decay_dependent_filter'])
            self.assertTrue(source['requires_full_muon_completion'])
            self.assertFalse(source['muon_enhancement_complete'])
            self.assertTrue(source['diagnostic_only'])
            self.assertFalse(source['physics_normalization_ready'])
            self.assertEqual(source_normalization(source), ('fixed_source_trials', 4, 65.e9))
            self.assertEqual(source_rate_weight(source, .2), .2*65.e9/4)
            with self.assertRaisesRegex(ValueError, 'Muon-enriched'):
                source_record(path)

    def test_raw_ordinary_QCD_does_not_accept_filtered_or_wrong_process_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            path, _ = source_receipt_fixture(directory, composite=False)
            with self.assertRaises(ValueError): source_record(path, family='qcd_ordinary')
        with tempfile.TemporaryDirectory() as directory:
            path, _ = jpsi_receipt_fixture(directory)
            with self.assertRaises(ValueError): source_record(path, family='qcd_ordinary')
        with tempfile.TemporaryDirectory() as directory:
            path, _ = dy_receipt_fixture(directory)
            with self.assertRaises(ValueError): source_record(path, family='qcd_ordinary')

    def test_raw_ordinary_QCD_cannot_hide_a_cylinder_change_behind_ordinary_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            path, record = jpsi_receipt_fixture(directory, family='qcd_ordinary')
            with zipfile.ZipFile(path.with_name('artifacts.zip')) as archive:
                payloads = {name: archive.read(name) for name in archive.namelist()}
            data = payloads['resolved_gen_cfg.py'].replace(b'ParticleDecays:tau0Max = 10',
                                                        b'ParticleDecays:limitCylinder = on')
            payloads['resolved_gen_cfg.py'] = data
            digest = hashlib.sha256(data).hexdigest()
            record['semantic_audit']['config_sha256'] = digest
            payloads['semantic_audit.json'] = json.dumps(record['semantic_audit']).encode()
            for name in ('resolved_gen_cfg.py', 'semantic_audit.json'):
                native = dict(bytes=len(payloads[name]), sha256=hashlib.sha256(payloads[name]).hexdigest())
                record['artifacts'][name] = native
                next(item for item in record['publication'] if item['name'] == name).update(native)
            path.write_text(json.dumps(record))
            with zipfile.ZipFile(path.with_name('artifacts.zip'), 'w') as archive:
                for name, payload in payloads.items(): archive.writestr(name, payload)
            with self.assertRaisesRegex(ValueError, 'Ordered source model'):
                source_record(path, family='qcd_ordinary')
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
        for family in ('dy', 'jpsi', 'qcd_ordinary'):
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
