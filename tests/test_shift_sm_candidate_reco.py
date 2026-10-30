# Code style: AI-code.
"""Check source authority, same-pair candidate evidence and exact diagnostic q."""
import copy
import gzip
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from prepare_shift_sm_candidate_reco import materialize_fresh_authorities, prepare_draw
from prepare_shift_sm_response_jobs import detector_request
from run_shift_sm_response_collection import verify_exposure


def checksum(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def event(number, depth=20., x=300.):
    muons = []
    for barcode, pdg in [(11, 13), (12, -13)]:
        muons.append(dict(barcode=barcode, pdg=pdg, birth_cm=[x, 0., 14800.],
            momentum_GeV=[0., 0., -100.], p_GeV=100., energy_GeV=math.hypot(100., .105658),
            ancestor_barcodes=[7], first_non_muon_parent_barcodes=[7], immediate_parent_barcodes=[7],
            transport_parent_barcodes=[7], straight_line_towards_CMS_entry=True,
            material_to_CMS_entry=dict(complete=True, column_density_g_cm2=1000.)))
    return dict(event_id=[100, 1, number], native_weight=float(number), GEN_muons=2, graph_sha256='c'*64,
        muons=muons, parent_flights=[dict(barcode=7, material=dict(
            complete=True, full_saved_chord_coverage=True, interaction_depth_proxy=depth))],
        ancestor_nodes=[dict(barcode=7, pdg=211, status=2)], unknown_transport_ancestor_barcodes=[],
        source_id='supply_qcd_20toinf_test', original_source_identity_known=True,
        original_source_sha256='a'*64, source_file_sha256='b'*64)


class CandidateRecoTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.events = [event(1, depth=0.), event(2), event(3), event(4), event(5),
                       event(6, x=5000.), event(7, x=5000.)]
        self.indices = list(range(len(self.events)))

    def tearDown(self):
        self.temp.cleanup()

    def record(self, source_id='supply_qcd_20toinf_test', raw_hash='a'*64, prepared_hash='b'*64):
        count = len(self.events)
        events = copy.deepcopy(self.events)
        for row in events:
            row.update(source_id=source_id, original_source_sha256=raw_hash, source_file_sha256=prepared_hash)
        source = dict(source_id=source_id, stratum='qcd_20toinf',
            gen_sha256=raw_hash, saved_events=count, full_source_trials=1000,
            physics_normalization_ready=False)
        features = dict(schema='shift-gen-material-features-v1', events=events,
            maximum_events_per_source=None, prefix_requested=False,
            zero_GEN_muon_records_retained=True,
            sources=[dict(sha256=prepared_hash, source_file_event_count=count)],
            summary=dict(events=count, source_read_counts=[dict(source_file_sha256=prepared_hash,
                entire_input_file_read=True, events_read=count, source_file_event_count=count)]))
        feature_path = self.directory/'features.json.gz'
        with gzip.open(feature_path, 'wt') as stream:
            json.dump(features, stream)
        descriptor = dict(gen='/eos/prepared.root', gen_sha256=prepared_hash, stratum='qcd_20toinf',
            event_ids=[row['event_id'] for row in self.events],
            weights=[row['native_weight'] for row in self.events], selected_indices=self.indices,
            sampling_probabilities=[.25]*len(self.indices), normalization_record=source,
            nano_base='/eos/user/j/jniedzie/shift_cmssw_support/candidate/nano',
            metadata_base='/eos/user/j/jniedzie/shift_cmssw_support/candidate/meta',
            maximum_publication_bytes=4*1048576)
        authority = dict(complete=True, exit_code=0, source_id=source['source_id'], source=source,
            selected_source=descriptor, prepared_publications=[
                dict(name='qcd_features.json.gz', path='/eos/features.json.gz',
                     sha256=checksum(feature_path), bytes=feature_path.stat().st_size),
                dict(name='qcd_full_GEN.root', path=descriptor['gen'], sha256=prepared_hash, bytes=123)])
        result_path = self.directory/'result.json'
        result_path.write_text(json.dumps(authority))
        return dict(result_path=str(result_path), result_sha256=checksum(result_path),
            feature_path=str(feature_path), feature_sha256=checksum(feature_path))

    def test_candidates_all_retained_exact_control_probability_and_native_weights(self):
        original = copy.deepcopy(self.events)
        result = prepare_draw([self.record()], 'fixed-seed', controls_per_cell=1)
        plan = result['plan']
        self.assertEqual(plan['selected_events'], 3)
        self.assertEqual(plan['population_events'], 7)
        cells = {row['benchmark_region']: row for row in plan['strata']}
        self.assertEqual(cells['credible_shallow_pointing']['benchmark_inclusion_probability'], 1.)
        self.assertEqual(cells['deep_pointing']['benchmark_inclusion_probability'], .25)
        self.assertEqual(cells['no_both_sides_pointing_pair']['benchmark_inclusion_probability'], .5)
        for row in plan['selected']:
            self.assertEqual(row['existing_sampling_probability'], .25)
            self.assertEqual(row['original_worker_analysis_weight'], row['native_weight']/.25)
            self.assertEqual(row['diagnostic_analysis_weight'], row['native_weight']/(.25*row['benchmark_inclusion_probability']))
        self.assertEqual(self.events, original)
        self.assertEqual(result['exposure']['sources'][0]['original_full_exposure']['full_source_trials'], 1000)
        verify_exposure(result['exposure'], plan)
        pin = result['source_pins']['sources'][0]
        for row in plan['selected']:
            request = detector_request(dict(path='/frozen/plan', sha256='d'*64), pin, row,
                4000000, '/eos/user/j/jniedzie/shift_cmssw_support/candidate')
            self.assertEqual(request['detector_source']['sampling_probabilities'], [.25]*7)

    def test_deterministic_draw_independent_of_event_iteration(self):
        record = self.record()
        first = prepare_draw([record], 'fixed-seed', controls_per_cell=1)
        second = prepare_draw([record], 'fixed-seed', controls_per_cell=1)
        self.assertEqual(first, second)

    def test_missed_original_candidate_is_reported_and_never_silently_readmitted(self):
        self.indices = list(range(1, 7))
        result = prepare_draw([self.record()], 'seed', controls_per_cell=1)
        self.assertEqual([row['event_id'] for row in result['missed_candidates']], [[100, 1, 1]])
        self.assertNotIn([100, 1, 1], [row['event_id'] for row in result['plan']['selected']])
        self.assertEqual(result['exposure']['original_saved_GEN_events'], 7)
        self.assertEqual(result['plan']['population_events'], 6)

    def test_explicit_fresh_census_covers_masked_candidate_before_first_sim(self):
        self.indices = list(range(1, 7))
        record = self.record()
        original_bytes = Path(record['result_path']).read_bytes()
        record.update(fresh_census=True, first_sim_source=True, preparation_mask_unused=True)
        result = prepare_draw([record], 'seed', controls_per_cell=1)
        self.assertFalse(result['missed_candidates'])
        candidate = next(row for row in result['plan']['selected'] if row['event_id'] == [100, 1, 1])
        self.assertEqual(candidate['existing_sampling_probability'], 1.)
        self.assertEqual(candidate['benchmark_inclusion_probability'], 1.)
        self.assertEqual(result['plan']['population_events'], 7)
        materialize_fresh_authorities(result, self.directory/'response_authority')
        pin = result['source_pins']['sources'][0]
        authority = json.loads(Path(pin['original_result_path']).read_text())
        self.assertEqual(authority['selected_source'], pin['original_selected_source'])
        self.assertEqual(authority['response_census_selection']['original_unused_selected_source']['selected_indices'], self.indices)
        self.assertEqual(Path(record['result_path']).read_bytes(), original_bytes)
        self.assertEqual(pin['original_selected_source']['sampling_probabilities'], [1.]*7)

    def test_fresh_census_requires_first_sim_and_unused_mask_declarations(self):
        record = self.record()
        record['fresh_census'] = True
        with self.assertRaisesRegex(ValueError, 'first-SIM'):
            prepare_draw([record], 'seed')

    def test_unknown_parent_prefix_is_not_promoted_to_known_shallow(self):
        self.events[0]['parent_flights'][0]['material']['full_saved_chord_coverage'] = False
        result = prepare_draw([self.record()], 'seed', controls_per_cell=1)
        regions = {row['benchmark_region'] for row in result['plan']['selected']}
        self.assertIn('unknown_parent', regions)
        self.assertNotIn('credible_shallow_pointing', regions)

    def test_unknown_muon_column_keeps_genuine_shallow_candidate(self):
        self.events[0]['muons'][0]['material_to_CMS_entry'] = dict(complete=False,
            column_density_g_cm2=None, unknown_reason='unscored')
        result = prepare_draw([self.record()], 'seed', controls_per_cell=1)
        candidate = next(row for row in result['plan']['selected'] if row['event_id'] == [100, 1, 1])
        self.assertEqual(candidate['benchmark_inclusion_probability'], 1.)
        self.assertEqual(candidate['joint']['n_shallow_parent_unknown_column_pairs'], 1)

    def test_reject_duplicate_exposure_tampering_and_insufficient_budget(self):
        record = self.record()
        with self.assertRaisesRegex(ValueError, 'Duplicate original source'):
            prepare_draw([record, record], 'seed')
        with self.assertRaisesRegex(ValueError, 'event budget'):
            prepare_draw([record], 'seed', maximum_events=1)
        Path(record['result_path']).write_text('{}')
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            prepare_draw([record], 'seed')

    def test_census_identity_tamper_rejected_even_with_updated_feature_hash(self):
        record = self.record()
        self.events[0]['event_id'] = [100, 1, 999]
        path = Path(record['feature_path'])
        with gzip.open(path, 'rt') as stream:
            features = json.load(stream)
        features['events'] = self.events
        with gzip.open(path, 'wt') as stream:
            json.dump(features, stream)
        authority = json.loads(Path(record['result_path']).read_text())
        authority['prepared_publications'][0].update(sha256=checksum(path), bytes=path.stat().st_size)
        Path(record['result_path']).write_text(json.dumps(authority))
        record.update(result_sha256=checksum(Path(record['result_path'])), feature_sha256=checksum(path))
        with self.assertRaisesRegex(ValueError, 'identity'):
            prepare_draw([record], 'seed')

    def test_cli_refuses_existing_frozen_output(self):
        config = self.directory/'config.json'
        config.write_text(json.dumps(dict(sources=[self.record()], seed='fixed', controls_per_cell=1)))
        output = self.directory/'frozen'
        script = Path(__file__).resolve().parents[1]/'scripts/prepare_shift_sm_candidate_reco.py'
        first = subprocess.run([sys.executable, str(script), str(config), str(output)], capture_output=True, text=True)
        self.assertEqual(first.returncode, 0, first.stderr)
        pins = json.loads((output/'source_pins.json').read_text())
        self.assertEqual(pins['frozen_benchmark_sha256'], checksum(output/'plan.json'))
        second = subprocess.run([sys.executable, str(script), str(config), str(output)], capture_output=True, text=True)
        self.assertNotEqual(second.returncode, 0)
        self.assertIn('Refuse to overwrite', second.stderr)

    def test_cli_comparison_freezes_both_source_authorities_and_exposures(self):
        original_directory = self.directory
        records = []
        for source in range(2):
            self.directory = original_directory/str(source)
            self.directory.mkdir()
            record = self.record(source_id='source_'+str(source), raw_hash=str(source+1)*64,
                                 prepared_hash=str(source+3)*64)
            record.update(fresh_census=True, first_sim_source=True, preparation_mask_unused=True)
            records.append(record)
        self.directory = original_directory
        config = self.directory/'comparison.json'
        config.write_text(json.dumps(dict(sources=records, seed='fixed', maximum_events=32,
            comparison=dict(maximum_per_bin_arm=16, control_fraction=.1))))
        output = self.directory/'comparison'
        script = Path(__file__).resolve().parents[1]/'scripts/prepare_shift_sm_candidate_reco.py'
        result = subprocess.run([sys.executable, str(script), str(config), str(output)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads((output/'plan.json').read_text())
        exposure = json.loads((output/'exposure.json').read_text())
        pins = json.loads((output/'source_pins.json').read_text())
        self.assertEqual(plan['population_events'], 14)
        self.assertEqual(exposure['original_saved_GEN_events'], 14)
        self.assertEqual({s['comparison_arm'] for s in exposure['sources']}, {'smart', 'random'})
        for pin in pins['sources']:
            self.assertEqual(checksum(Path(pin['original_result_path'])), pin['original_result_sha256'])
            self.assertEqual(pin['original_selected_source']['sampling_probabilities'], [1.]*7)
        self.assertNotIn('controls_per_source_region', plan)


if __name__ == '__main__':
    unittest.main()
