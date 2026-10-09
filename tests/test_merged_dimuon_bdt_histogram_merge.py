"""Fail-closed histogram merge checks with no scheduler or EOS mutation."""
import copy
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import merge_merged_dimuon_bdt_histograms as merger


def digest_bytes(data):
    return hashlib.sha256(data).hexdigest()


def publication(path, data=b'published fixture'):
    return {'path': path, 'sha256': digest_bytes(data), 'bytes': len(data)}


class MergeSafetyTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.plan_hash = 'a' * 64
        self.model_hash = 'b' * 64
        self.row = {'index': 0, 'source_logical_path': '/original/nano.root',
                    'source_transport_path': '/transport/nano.root', 'expected_events': 3,
                    'process': 'qcd', 'stratum': 'qcd_0to1', 'weight_branch': 'shiftSamplingGenWeight',
                    'output_nano': '/new/qcd/campaign/nanoAOD_merged/ntuple_0.root',
                    'output_histograms': '/new/qcd/campaign/histograms/ntuple_0.root',
                    'complete_marker': '/new/qcd/campaign/metadata/complete.json',
                    'score_receipt': '/new/qcd/campaign/metadata/score.json',
                    'bdt_hist_receipt': '/new/qcd/campaign/metadata/bdt.json',
                    'score_log': '/new/qcd/campaign/metadata/score.log',
                    'histogram_log': '/new/qcd/campaign/metadata/histogram.log'}
        self.plan = {'model_sha256': self.model_hash, 'files': [self.row], 'batches': [[0]]}
        self.marker = {'schema': 'shift-merged-dimuon-bdt-complete-v1', 'complete': True,
                       'source': '/original/nano.root', 'source_sha256': 'c' * 64,
                       'plan_sha256': self.plan_hash, 'model_sha256': self.model_hash,
                       'selection_applied': False, 'events': 3, 'pairs': 0,
                       'published': {key: publication(path) for key, path in merger.expected_publications(self.row).items()}}
        self.score = {'complete': True, 'source_sha256': 'c' * 64,
                      'source': '/original/nano.root', 'source_transport': '/transport/nano.root',
                      'output': self.row['output_nano'],
                      'output_sha256': self.marker['published']['nano']['sha256'],
                      'model_sha256': self.model_hash, 'plan_sha256': self.plan_hash,
                      'selection_applied': False, 'events': 3, 'retained_pairs': 0,
                      'verification': {'all_original_content_equal': True, 'all_scores_recomputed_equal': True}}
        self.hist = {'schema': 'shift-dimuon-bdt-histograms-v1', 'complete': True,
                     'plan_sha256': self.plan_hash, 'events': 3, 'pairs': 0,
                     'output': self.row['output_histograms'], 'input': self.row['output_nano'],
                     'output_sha256': self.marker['published']['histograms']['sha256'],
                     'score_selection_applied': False, 'weights_branch': self.row['weight_branch'],
                     'weight_application_count': 1, 'event_sumw': 3., 'event_sumw2': 3.,
                     'pair_sumw': 0., 'pair_sumw2': 0., 'standard_cutflow_float32_sumw': 3.,
                     'label_pairs': dict(Genuine=0, Accidental=0, Unknown=0), 'category_pairs': {'': 0}}
        self.status = {'batch': 0, 'pilot': False, 'complete': True,
                       'plan_sha256': self.plan_hash, 'selection_applied': False,
                       'expected_indices': [0], 'pending_indices': [],
                       'files': [{'index': 0, 'complete': True, 'marker': self.marker}]}

    def write_status(self, name, value):
        (self.root / name).write_text(json.dumps(value))

    def test_missing_success_stops_before_any_merge(self):
        with self.assertRaisesRegex(ValueError, 'Production is not complete'):
            merger.completed_statuses(self.plan, self.plan_hash, self.root)

    def test_retained_failure_plus_valid_recovery_is_accepted(self):
        failed = dict(self.status, complete=False, files=[], pending_indices=[0])
        self.write_status('batch0_attempt1.json', failed)
        self.write_status('batch0_attempt2.json', self.status)
        success, failures = merger.completed_statuses(self.plan, self.plan_hash, self.root)
        self.assertEqual(set(success), {0})
        self.assertEqual(len(failures), 1)

    def test_successful_status_must_cover_the_exact_batch(self):
        status = dict(self.status, pending_indices=[0])
        self.write_status('batch0_bad.json', status)
        with self.assertRaisesRegex(ValueError, 'complete exact batch'):
            merger.completed_statuses(self.plan, self.plan_hash, self.root)

    def test_other_plan_status_is_not_an_input(self):
        self.write_status('batch0_bad.json', dict(self.status, plan_sha256='d' * 64))
        with self.assertRaisesRegex(ValueError, 'another plan'):
            merger.completed_statuses(self.plan, self.plan_hash, self.root)

    def test_completion_marker_checks_every_output_path(self):
        marker = copy.deepcopy(self.marker)
        marker['published']['histograms']['path'] += '.wrong'
        with self.assertRaisesRegex(ValueError, 'artifact identity'):
            merger.validate_marker(marker, self.row, self.plan, self.plan_hash)

    def test_zero_pairs_and_empty_label_histograms_are_valid(self):
        merger.validate_marker(self.marker, self.row, self.plan, self.plan_hash)
        merger.validate_receipts(self.score, self.hist, self.marker, self.row, self.plan_hash)

    def test_score_content_verification_is_required(self):
        score = copy.deepcopy(self.score)
        score['verification']['all_original_content_equal'] = False
        with self.assertRaisesRegex(ValueError, 'original-content'):
            merger.validate_receipts(score, self.hist, self.marker, self.row, self.plan_hash)

    def test_score_receipt_transport_identity_cannot_change_on_reuse(self):
        score = dict(self.score, source_transport='/another/source.root')
        with self.assertRaisesRegex(ValueError, 'original-content'):
            merger.validate_receipts(score, self.hist, self.marker, self.row, self.plan_hash)

    def test_histogram_receipt_rejects_a_second_weight_application(self):
        histogram = dict(self.hist, weight_application_count=2)
        with self.assertRaisesRegex(ValueError, 'exact scored population'):
            merger.validate_receipts(self.score, histogram, self.marker, self.row, self.plan_hash)

    def test_label_counts_partition_all_pairs_including_unknown(self):
        histogram = dict(self.hist, pairs=1, category_pairs={'': 1})
        marker = dict(self.marker, pairs=1)
        score = dict(self.score, retained_pairs=1)
        with self.assertRaisesRegex(ValueError, 'partition every retained pair'):
            merger.validate_receipts(score, histogram, marker, self.row, self.plan_hash)
        histogram['label_pairs'] = dict(Genuine=0, Accidental=0, Unknown=1)
        merger.validate_receipts(score, histogram, marker, self.row, self.plan_hash)

    def test_group_float_cutflows_are_added_in_double(self):
        first = dict(self.hist, standard_cutflow_float32_sumw=16777216.)
        second = dict(self.hist, standard_cutflow_float32_sumw=1.)
        result = merger.totals([{'histogram_receipt': first}, {'histogram_receipt': second}])
        self.assertEqual(result['standard_cutflow_float32_sumw'], 16777217.)

    def test_signed_weights_and_nonnegative_second_moments_are_retained(self):
        histogram = dict(self.hist, event_sumw=-2., event_sumw2=4.)
        merger.validate_receipts(self.score, histogram, self.marker, self.row, self.plan_hash)
        histogram['event_sumw2'] = -1.
        with self.assertRaisesRegex(ValueError, 'Squared weight sum'):
            merger.validate_receipts(self.score, histogram, self.marker, self.row, self.plan_hash)

    def test_stage_checks_sha_and_size(self):
        target = self.root / 'input.root'
        with mock.patch.object(merger.io, 'get_remote', side_effect=lambda _path, output: output.write_bytes(b'corrupt')):
            with self.assertRaisesRegex(ValueError, 'checksum/size'):
                merger.stage_checked(publication('/published/file.root'), target)

    def test_group_exposures_come_from_parent_gen_and_signal_has_no_rate(self):
        signal = dict(self.row, index=1, process='signal', point='m15_prompt',
                      output_histograms='/new/signal/campaign/histograms/ntuple_0.root')
        sources = {'bins': {'qcd_0to1': {'bin': '0to1', 'cross_section_pb': 10., 'full_parent_gen_sumw': 100000.}},
                   'signals': [{'point': 'm15_prompt', 'mass_gev': 15., 'epsilon': 0.001, 'proper_ctau_mm': 0.0001}]}
        groups = merger.group_inventory(dict(self.plan, files=[self.row, signal]), sources)
        rows = {row['name']: row for row in groups}
        self.assertEqual(rows['qcd_0to1']['full_gen_sumw'], 100000.)
        self.assertIsNone(rows['m15_prompt']['cross_section_pb'])
        self.assertIsNone(rows['m15_prompt']['full_gen_sumw'])
        self.assertTrue(rows['qcd_0to1']['histogram_path'].endswith('/histograms_merged/ntuple_0.root'))

    def test_incomplete_publication_is_preserved_without_overwrite(self):
        group = {'name': 'qcd_0to1', 'process': 'qcd', 'indices': [0],
                 'histogram_path': '/output/histograms.root', 'receipt_path': '/output/receipt.json',
                 'log_path': '/output/merge.log', 'complete_marker': '/output/complete.json'}
        record = {'index': 0, 'marker_path': self.row['complete_marker'],
                  'marker_sha256': 'e' * 64, 'marker': self.marker, 'histogram_receipt': self.hist}
        with mock.patch.object(merger.io, 'remote_exists', side_effect=lambda path: path == group['histogram_path']), \
                mock.patch.object(merger.io, 'put_remote') as put, \
                mock.patch.object(merger.subprocess, 'run') as execute:
            with self.assertRaisesRegex(FileExistsError, 'without overwrite'):
                merger.merge_group(group, {0: record}, self.plan_hash, self.model_hash, self.root)
            put.assert_not_called()
            execute.assert_not_called()

    def publication_fixture(self, tamper=None):
        group = {'name': 'qcd_0to1', 'process': 'qcd', 'indices': [0],
                 'histogram_path': '/output/histograms.root', 'receipt_path': '/output/receipt.json',
                 'log_path': '/output/merge.log', 'complete_marker': '/output/complete.json'}
        record = {'index': 0, 'marker_path': self.row['complete_marker'],
                  'marker_sha256': 'e' * 64, 'marker': self.marker, 'histogram_receipt': self.hist}
        remote = {self.row['output_histograms']: b'published fixture'}
        published = []

        def get_remote(path, target):
            data = remote[path]
            target.write_bytes(b'changed on readback' if tamper == path and path in published else data)

        def put_remote(source, target):
            if target in remote:
                raise FileExistsError(target)
            remote[target] = source.read_bytes()
            published.append(target)

        def execute(command, **kwargs):
            self.assertEqual(command[0], 'hadd')
            self.assertNotIn('-f', command)
            Path(command[1]).write_bytes(b'merged histogram fixture')
            kwargs['stdout'].write('hadd completed\n')

        for name, replacement in (('remote_exists', lambda path: path in remote),
                                  ('get_remote', get_remote), ('put_remote', put_remote),
                                  ('transfer', lambda argv: None)):
            patch = mock.patch.object(merger.io, name, replacement)
            patch.start()
            self.addCleanup(patch.stop)
        patch = mock.patch.object(merger.subprocess, 'run', execute)
        patch.start()
        self.addCleanup(patch.stop)
        patch = mock.patch.object(merger, 'validate_histograms', return_value={'all_checks_equal': True})
        patch.start()
        self.addCleanup(patch.stop)
        return group, {0: record}, remote, published

    def test_completion_marker_is_published_last_after_hash_readbacks(self):
        group, records, remote, published = self.publication_fixture()
        output = merger.merge_group(group, records, self.plan_hash, self.model_hash, self.root)
        self.assertFalse(output['reused'])
        self.assertEqual(published[-1], group['complete_marker'])
        self.assertEqual(len(published), 4)
        marker = json.loads(remote[group['complete_marker']])
        self.assertTrue(marker['complete'])
        self.assertEqual(marker['published']['histograms']['sha256'], digest_bytes(remote[group['histogram_path']]))

    def test_corrupt_publication_readback_never_publishes_completion_marker(self):
        group, records, remote, published = self.publication_fixture('/output/histograms.root')
        with self.assertRaisesRegex(ValueError, 'checksum/size'):
            merger.merge_group(group, records, self.plan_hash, self.model_hash, self.root)
        self.assertNotIn(group['complete_marker'], remote)
        self.assertEqual(published, [group['histogram_path']])

    def test_completed_exact_merge_can_be_reused_without_overwrite(self):
        group, records, remote, published = self.publication_fixture()
        original = merger.merge_group(group, records, self.plan_hash, self.model_hash, self.root)
        resume = self.root / 'resume'
        resume.mkdir()
        with mock.patch.object(merger.subprocess, 'run') as execute:
            reused = merger.merge_group(group, records, self.plan_hash, self.model_hash, resume)
        execute.assert_not_called()
        self.assertTrue(reused['reused'])
        self.assertEqual(reused['histogram_sha256'], original['histogram_sha256'])
        self.assertEqual(len(published), 4)

    def compatibility_fixture(self):
        previous = dict(copy.deepcopy(self.plan), schema='shift-merged-dimuon-bdt-plan-v1',
                        selection_applied=False, mc_to_data_normalization_measured=False,
                        campaign='20261009-test', input_campaign_complete=False, missing_detector_jobs=1,
                        storage_contract={'version': 2, 'direct_canonical_sources': True})
        previous['dependencies'] = {
            'classifier/add_bdt_score.py': {'path': '/old/writer.py', 'sha256': '1' * 64},
            'workflow/scripts/run_merged_dimuon_bdt.py': {'path': '/old/resume.py', 'sha256': '2' * 64},
            'classifier/features.py': {'path': '/old/features.py', 'sha256': '3' * 64},
            'tea/utils/shift_bdt_histogrammer.py': {'path': '/old/histograms.py', 'sha256': '4' * 64},
        }
        proof_path = self.root / 'previous_plan.json'
        proof_path.write_text(json.dumps(previous))
        previous_hash = merger.io.digest(proof_path)
        current = copy.deepcopy(previous)
        current['dependencies']['classifier/add_bdt_score.py']['sha256'] = '5' * 64
        current['dependencies']['workflow/scripts/run_merged_dimuon_bdt.py']['sha256'] = '6' * 64
        proof = {'path': str(proof_path), 'sha256': previous_hash}
        current['compatible_previous_plans'] = {previous_hash: proof}
        current['dependencies']['previous_plan.json'] = dict(proof)
        return current, previous_hash

    def test_writer_only_recovery_accepts_unchanged_previous_markers(self):
        plan, old_hash = self.compatibility_fixture()
        allowed = merger.validate_compatibility(plan, self.plan_hash)
        marker = dict(self.marker, plan_sha256=old_hash)
        merger.validate_marker(marker, self.row, plan, self.plan_hash, allowed)
        self.assertEqual(allowed, {self.plan_hash, old_hash})

    def test_recovery_rejects_changed_predictors_or_histogram_physics(self):
        plan, _ = self.compatibility_fixture()
        for role in ('classifier/features.py', 'tea/utils/shift_bdt_histogrammer.py'):
            changed = copy.deepcopy(plan)
            changed['dependencies'][role]['sha256'] = '7' * 64
            with self.assertRaisesRegex(ValueError, 'non-writer physics/analysis dependency'):
                merger.validate_compatibility(changed, self.plan_hash)

    def test_recovery_rejects_changed_event_weights_or_storage_contract(self):
        plan, _ = self.compatibility_fixture()
        changed = copy.deepcopy(plan)
        changed['files'][0]['weight_branch'] = 'genWeight'
        with self.assertRaisesRegex(ValueError, 'population/model/storage contract: files'):
            merger.validate_compatibility(changed, self.plan_hash)
        changed = copy.deepcopy(plan)
        changed['storage_contract']['version'] = 3
        with self.assertRaisesRegex(ValueError, 'storage_contract'):
            merger.validate_compatibility(changed, self.plan_hash)

    def test_previous_plan_proof_must_remain_hash_identical(self):
        plan, old_hash = self.compatibility_fixture()
        proof = plan['compatible_previous_plans'][old_hash]
        Path(proof['path']).write_text('{}')
        with self.assertRaisesRegex(ValueError, 'proof checksum differs'):
            merger.validate_compatibility(plan, self.plan_hash)

    def test_new_recovery_status_can_retain_old_publication_hash(self):
        plan, old_hash = self.compatibility_fixture()
        status = copy.deepcopy(self.status)
        status['files'][0]['marker']['plan_sha256'] = old_hash
        self.write_status('batch0_recovery.json', status)
        successes, _ = merger.completed_statuses(plan, self.plan_hash, self.root)
        self.assertEqual(successes[0]['status']['files'][0]['marker']['plan_sha256'], old_hash)

    def test_old_batch_status_does_not_establish_recovery_completion(self):
        plan, old_hash = self.compatibility_fixture()
        status = copy.deepcopy(self.status)
        status['plan_sha256'] = old_hash
        status['files'][0]['marker']['plan_sha256'] = old_hash
        self.write_status('batch0_original.json', status)
        with self.assertRaisesRegex(ValueError, 'another plan'):
            merger.completed_statuses(plan, self.plan_hash, self.root)

    def test_reused_receipts_keep_their_original_publication_plan(self):
        _, old_hash = self.compatibility_fixture()
        marker = dict(self.marker, plan_sha256=old_hash)
        score = dict(self.score, plan_sha256=old_hash)
        histogram = dict(self.hist, plan_sha256=old_hash)
        merger.validate_receipts(score, histogram, marker, self.row, self.plan_hash)
        histogram['plan_sha256'] = self.plan_hash
        with self.assertRaisesRegex(ValueError, 'exact scored population'):
            merger.validate_receipts(score, histogram, marker, self.row, self.plan_hash)


@unittest.skipUnless(importlib.util.find_spec('ROOT'), 'Native ROOT runtime required')
class HistogramContentTest(unittest.TestCase):
    def setUp(self):
        import ROOT
        ROOT.gROOT.SetBatch(True)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.path = self.directory / 'histograms.root'
        root = ROOT.TFile.Open(str(self.path), 'CREATE')
        raw = ROOT.TH1D('rawEventsCutFlow', '', 1, 0, 1)
        raw.SetBinContent(1, 3.)
        cutflow = ROOT.TH1D('cutFlow', '', 1, 0, 1)
        cutflow.SetBinContent(1, 16777217.)
        root.mkdir('dimuon_bdt').cd()
        histograms = {}
        for label in ('', *merger.LABELS):
            for unweighted in (False, True):
                name = merger.score_name(label=label, unweighted=unweighted).split('/')[-1]
                histograms[label, unweighted] = ROOT.TH1D(name, '', 10, 0., 1.)
                histograms[label, unweighted].Sumw2()
        for score, weight, label in ((0.2, 2., 'Genuine'), (0.5, -1., 'Unknown'), (1., 3., 'Genuine')):
            for suffix in ('', label):
                histograms[suffix, False].Fill(score, weight)
                histograms[suffix, True].Fill(score, 1.)
        root.Write()
        root.Close()
        self.expected = {'events': 3, 'pairs': 3, 'pair_sumw': 4., 'pair_sumw2': 14.,
                         'standard_cutflow_float32_sumw': 16777217.,
                         'category_pairs': {'': 3}, 'label_pairs': dict(Genuine=2, Accidental=0, Unknown=1)}

    def test_signed_weights_zero_labels_and_score_overflow_are_checked(self):
        result = merger.validate_histograms(self.path, self.expected)
        self.assertTrue(result['all_flow_bins_included'])
        self.assertTrue(result['bdt_pair_sumw2_equal'])

    def test_changed_second_moment_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'pair sumw2'):
            merger.validate_histograms(self.path, dict(self.expected, pair_sumw2=13.))

    def test_event_tree_is_never_merged(self):
        import ROOT
        root = ROOT.TFile.Open(str(self.path), 'UPDATE')
        tree = ROOT.TTree('Events', 'unexpected event data')
        tree.Write()
        root.Close()
        with self.assertRaisesRegex(ValueError, 'Refuse to merge event trees'):
            merger.validate_histograms(self.path, self.expected)

    def test_raw_cutflow_must_match_every_event(self):
        with self.assertRaisesRegex(ValueError, 'Raw cutflow'):
            merger.validate_histograms(self.path, dict(self.expected, events=2))


if __name__ == '__main__':
    unittest.main()
