# Code style: AI-code. AI-generated benchmark weight and exposure guards.
import copy
from pathlib import Path
import sys
import unittest

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


if __name__=='__main__':
    unittest.main()
