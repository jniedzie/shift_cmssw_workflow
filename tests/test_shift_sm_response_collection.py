# Code style: AI-code. AI-generated benchmark weight and exposure guards.
import copy
import gzip
import hashlib
import json
import os
from pathlib import Path
import sys
import unittest
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from run_shift_sm_response_collection import study_row, verify_exposure, verify_worker_request


class ResponseCollectionTest(unittest.TestCase):
    def selected(self):
        return dict(gen_sha256='a'*64, prepared_gen_sha256='b'*64, event_id=[1,1,2],
            source_id='qcd_20toinf_source0040', stratum='qcd_20toinf',
            selected_position=0, original_index=1, native_weight=2.,
            existing_sampling_probability=.5, benchmark_inclusion_probability=.1,
            benchmark_region='deep_pointing', features={})

    def test_q_is_applied_once_only_in_analysis(self):
        selected=self.selected()
        label=dict(gen_sha256='a'*64, event_id=[1,1,2], native_weight=2.,sampling_probability=.5)
        original=copy.deepcopy(label)
        result=dict(events=1,nano={'event_ids':[[1,1,2]]},source_indices=[1],
            sampling_probabilities=[.5],stages={'1':{'seconds':10.}},wall_seconds=20.)
        row=study_row(label,selected,result)
        self.assertEqual(label,original)
        self.assertEqual(row['sampling_probability'],.05)
        self.assertEqual(row['native_weight']/row['sampling_probability'],40.)
        self.assertEqual(row['labels']['sampling_probability'],.5)
        self.assertTrue(row['prospective_holdout'])
        selected['comparison_arm'] = 'random'
        comparison = study_row(label, selected, result)
        self.assertEqual(comparison.pop('comparison_arm'), 'random')
        self.assertEqual(comparison, row)

    def test_mismatched_original_or_q_fails(self):
        selected=self.selected()
        label=dict(gen_sha256='a'*64,event_id=[1,1,2],native_weight=3.,sampling_probability=.5)
        with self.assertRaisesRegex(ValueError,'original W/p'):
            study_row(label,selected,{})
        label['native_weight']=2.
        selected['benchmark_inclusion_probability']=0.
        with self.assertRaisesRegex(ValueError,'positive'):
            study_row(label,selected,{})

    def test_native_receipt_event_count_cannot_hide_a_different_original(self):
        selected=self.selected()
        label=dict(gen_sha256='a'*64,event_id=[1,1,2],native_weight=2.,sampling_probability=.5)
        result=dict(events=1,nano={'event_ids':[[1,1,3]]},source_indices=[1],
                    sampling_probabilities=[.5])
        with self.assertRaisesRegex(ValueError,'exactly the selected original'):
            study_row(label,selected,result)

    def test_all_population_sources_count_once_even_when_not_drawn(self):
        sources=[dict(original_raw_gen_sha256='a'*64,old_selected_events=8,original_saved_events=10),
                 dict(original_raw_gen_sha256='b'*64,old_selected_events=12,original_saved_events=15)]
        exposure=dict(sources=sources,includes_sources_with_no_benchmark_event_drawn=True,
                      original_saved_GEN_events=25)
        verify_exposure(exposure,dict(population_events=20,selected_events=1))
        exposure['sources']=sources[:1]
        with self.assertRaisesRegex(ValueError,'full original population'):
            verify_exposure(exposure,dict(population_events=20))

    def test_worker_original_offset_and_diagnostic_output_are_checked(self):
        selected=self.selected()
        source=dict(selected_indices=[1],event_ids=[[1,1,1],[1,1,2]],weights=[9.,2.],
            sampling_probabilities=[.5],gen_sha256='b'*64,
            normalization_record=dict(gen_sha256='a'*64),
            nano_base='/eos/user/j/jniedzie/shift_cmssw_support/response/nano')
        request=dict(detector_source=source,selected_skip=0,selected_count=1)
        verify_worker_request(request,selected)
        source['weights'][1]=3.
        with self.assertRaisesRegex(ValueError,'weight or probability'):
            verify_worker_request(request,selected)
        source['weights'][1]=2.
        source['nano_base']='/eos/user/j/jniedzie/shift_cmssw/qcd/nano'
        with self.assertRaisesRegex(ValueError,'ordinary campaign'):
            verify_worker_request(request,selected)


class LazyGENFeatureTest(unittest.TestCase):
    def test_feature_limits_reject_unallocated_compressed_inputs_before_download(self):
        from run_shift_sm_response_collection import analysis_features
        original = dict(source_id='source')
        default_pin = dict(path='/eos/features.json.gz', sha256='a'*64,
                           bytes=32*1048576+1)
        with patch('run_shift_sm_response_collection.download') as download:
            with self.assertRaisesRegex(ValueError, 'compressed bound'):
                analysis_features(original, {'source': default_pin}, {})
            for limits in (dict(maximum_compressed_bytes=128*1048576+1),
                           dict(maximum_expanded_bytes=2*1024*1048576+1),
                           dict(maximum_compressed_bytes=True)):
                with self.assertRaisesRegex(ValueError, 'reader allocation'):
                    analysis_features(original, {'source': dict(default_pin, **limits)}, {})
            download.assert_not_called()

    def test_expansion_failure_releases_old_cache_and_removes_partial_input(self):
        from run_shift_sm_response_collection import analysis_features
        data = gzip.compress(json.dumps(dict(events=[dict(payload='x'*100)])).encode())
        pin = dict(path='/eos/source/features.json.gz', sha256=hashlib.sha256(data).hexdigest(),
                   bytes=len(data), maximum_compressed_bytes=128*1048576,
                   maximum_expanded_bytes=10)
        cache = dict(pin={'old': 'source'}, events=[{'old': 'census'}])
        def copy_input(path, target, digest):
            self.assertEqual(cache, {})
            Path(target).write_bytes(data)
        here = Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                with patch('run_shift_sm_response_collection.download', side_effect=copy_input):
                    with self.assertRaisesRegex(ValueError, 'Expanded GEN feature census'):
                        analysis_features(dict(source_id='new'), {'new': pin}, cache)
                self.assertEqual(cache, {})
                self.assertFalse(Path('analysis_GEN_features.json.gz').exists())
            finally:
                os.chdir(here)

    def test_pinned_features_are_loaded_once_without_redrawing_or_reweighting(self):
        from run_shift_sm_response_collection import analysis_features
        from test_shift_sm_candidate_reco import event
        row = event(2, depth=1.)
        original = dict(source_id=row['source_id'], original_index=0,
            event_id=row['event_id'], native_weight=row['native_weight'],
            gen_sha256='a'*64, prepared_gen_sha256='b'*64,
            existing_sampling_probability=.001, benchmark_inclusion_probability=1., features={})
        before = copy.deepcopy(original)
        data = gzip.compress(json.dumps(dict(events=[row])).encode())
        pin = dict(path='/eos/source/features.json.gz', sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))
        def copy_input(path, target, digest):
            self.assertEqual(digest, hashlib.sha256(data).hexdigest())
            Path(target).write_bytes(data)
        here = Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                cache = {}
                with patch('run_shift_sm_response_collection.download', side_effect=copy_input) as download:
                    result = analysis_features(original, {row['source_id']:pin}, cache)
                    second = analysis_features(original, {row['source_id']:pin}, cache)
                self.assertEqual(download.call_count, 1)
                self.assertEqual(result, second)
                self.assertEqual(result['features']['n_gen_muons'], 2)
                self.assertEqual(original, before)
                self.assertEqual(result['existing_sampling_probability'], .001)
                self.assertFalse(Path('analysis_GEN_features.json.gz').exists())
                # A larger explicitly allocated reader keeps identity and W/p
                # checks identical to the unchanged default reader.
                larger_pin = dict(pin, maximum_compressed_bytes=128*1048576,
                                  maximum_expanded_bytes=2*1024*1048576)
                with patch('run_shift_sm_response_collection.download', side_effect=copy_input):
                    self.assertEqual(analysis_features(original, {row['source_id']:larger_pin}, cache), result)
                changed = dict(original, native_weight=123.)
                with self.assertRaisesRegex(ValueError, 'differ from the original'):
                    analysis_features(changed, {row['source_id']:larger_pin}, cache)
            finally:
                os.chdir(here)


if __name__=='__main__':
    unittest.main()
