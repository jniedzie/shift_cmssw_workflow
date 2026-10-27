# Code style: AI-code. AI-generated tests of frozen response-cohort accounting.
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import prepare_shift_sm_response_inventory as inventory


def cached_source(source_id='qcd_20toinf_source0000', raw_hash='a'*64):
    return dict(normalization=dict(source_id=source_id, stratum='qcd_20toinf',
        gen_sha256=raw_hash, saved_events=3, denominator_kind='fixed_source_trials',
        full_source_trials=5, sigma_nd_pb=100.),
        selected=dict(event_ids=[[10,1,1],[10,1,3]], weights=[1.,2.],
            selected_indices=[0,1], sampling_probabilities=[1.,.5]),
        descriptor_hashes=['d'*64], original_result_path='/receipt/'+source_id+'/result.json',
        original_result_sha256='e'*64)


class InventoryTests(unittest.TestCase):
    def test_missing_response_is_not_a_physics_zero(self):
        source=inventory.source_record(cached_source())
        sources={source['source_id']:source}
        nano=dict(path='/nano1.root', events=1, source_descriptor_sha256='d'*64)
        grouped=inventory.classify_response_coverage(sources,[nano])
        self.assertEqual(len(grouped[source['source_id']]),1)
        self.assertEqual(source['completed_response_events'],1)
        self.assertEqual(source['missing_selected_events'],1)
        self.assertFalse(source['complete_response'])
        counts=inventory.summarize(sources,[])
        self.assertEqual(counts['qcd_20toinf']['response_events'] if 'response_events' in counts['qcd_20toinf'] else 0,0)
        self.assertEqual(counts['qcd_20toinf']['excluded_or_missing_selected_events'],2)

    def test_duplicate_completion_cannot_close_source(self):
        source=inventory.source_record(cached_source())
        nano=dict(path='/nano1.root',events=1,source_descriptor_sha256='d'*64)
        with self.assertRaisesRegex(ValueError,'Duplicate completed Nano'):
            inventory.classify_response_coverage({source['source_id']:source},[nano,nano])

    def test_raw_file_union_cannot_hide_missing_or_repeated_events(self):
        source=inventory.source_record(cached_source())
        sources={source['source_id']:source}
        rows=inventory.expected_events([source])
        files=[dict(expected_events=rows[:1])]
        with self.assertRaisesRegex(ValueError,'complete frozen source'):
            inventory.validate_file_identities(sources,files)
        files=[dict(expected_events=rows),dict(expected_events=rows[:1])]
        with self.assertRaisesRegex(ValueError,'Repeated original'):
            inventory.validate_file_identities(sources,files)
        inventory.validate_file_identities(sources,[dict(expected_events=rows)])

    def test_overlapping_grouped_snapshots_are_not_added(self):
        a=dict(stratum='qcd_20toinf',source_ids=['a'],events=2,finished_epoch=1.)
        b=dict(stratum='qcd_20toinf',source_ids=['a','b'],events=3,finished_epoch=2.)
        self.assertEqual(inventory.choose_grouped([a,b]),[b])

    def test_repeated_original_identity_rejected(self):
        a=inventory.source_record(cached_source())
        b=inventory.source_record(cached_source('qcd_20toinf_source0001'))
        with self.assertRaisesRegex(ValueError,'Duplicate original'):
            inventory.expected_events([a,b])

    def test_event_numbers_from_distinct_originals_remain_distinct(self):
        a=inventory.source_record(cached_source())
        b=inventory.source_record(cached_source('qcd_20toinf_source0001','b'*64))
        self.assertEqual(len(inventory.expected_events([a,b])),4)

    def test_enrichment_preserves_nontrivial_original_indices_and_probabilities(self):
        source=inventory.source_record(cached_source())
        descriptor=dict(event_ids=[[10,1,1],[10,1,2],[10,1,3]], weights=[1.,7.,2.],
            selected_indices=[0,2],sampling_probabilities=[1.,.5],
            gen_sha256='c'*64,gen='/prepared/qcd_full_GEN.root',sampling={'plan_sha256':'f'*64})
        result=dict(complete=True,exit_code=0,source=source['full_original_exposure'],selected_source=descriptor)
        updated=inventory.enrich_source(source,lambda *args:result)
        self.assertEqual(updated['original_raw_gen_sha256'],'a'*64)
        self.assertEqual(updated['prepared_gen_sha256'],'c'*64)
        self.assertEqual([r['original_index'] for r in updated['selected_events']],[0,2])
        self.assertEqual(updated['selected_events'][1]['sampling_probability'],.5)
        self.assertEqual(updated['feature_path'],'/prepared/qcd_features.json.gz')
        result['selected_source']['weights'][2]=3.
        with self.assertRaisesRegex(ValueError,'IDs or weights changed'):
            inventory.enrich_source(source,lambda *args:result)

    def test_stable_holdout_split_ignores_completed_response(self):
        cached=cached_source()
        first=inventory.source_record(cached)
        cached['unrelated_completion_label']=True
        second=inventory.source_record(cached)
        self.assertEqual(first['source_fold'],second['source_fold'])
        self.assertEqual(first['study_split'],second['study_split'])

    def test_changed_frozen_receipt_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'receipt.json'
            path.write_text('{"ok":true}\n')
            expected=hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertTrue(inventory.read_record(path,expected)['ok'])
            path.write_text('{"ok":false}\n')
            with self.assertRaisesRegex(ValueError,'Frozen receipt changed'):
                inventory.read_record(path,expected)

    def test_failed_and_pending_publication_are_missing(self):
        cache=cached_source()
        publication=dict(sources={'cache':cache},nanos={},tasks={
            'pending':dict(phase='ready',directory='/nonexistent'),
            'failed':dict(phase='failed',directory='/nonexistent')})
        result=inventory.build_fast_inventory(publication)
        self.assertEqual(result['grouped_files'],[])
        self.assertEqual(result['summary']['qcd_20toinf']['selected_events'],2)
        self.assertFalse(result['missing_response_is_physics_rejection'])
        self.assertTrue(result['complete_cohort_conditioning_may_be_informative'])

    def test_cost_is_stage_wall_and_multievent_shares_are_explicit(self):
        source=inventory.source_record(cached_source())
        source['prepared_gen_sha256']='c'*64
        ids=[[10,1,1],[10,1,3]]
        result=dict(complete=True,exit_code=0,source_gen_sha256='c'*64,
            normalization_record=source['full_original_exposure'],nano_sha256='f'*64,
            events=2,wall_seconds=250.,stages={str(i):dict(seconds=100./i,
                audit=dict(event_ids=ids)) for i in range(1,5)})
        nano=dict(path='/nano.root',sha256='f'*64,events=2)
        cost=inventory.detector_cost_record(result,nano,source)
        self.assertEqual(cost['measured_unit'],'wall_seconds')
        self.assertEqual(cost['event_cost_kind'],'allocated_multievent_stage_wall_proxy')
        self.assertEqual(sum(r['SIM_stage_wall_seconds'] for r in cost['event_costs']),100.)
        self.assertEqual(cost['event_costs'][1]['native_weight'],2.)
        self.assertEqual(cost['event_costs'][1]['sampling_probability'],.5)
        result['stages']['1']['audit']['event_ids']=[[10,1,9],[10,1,3]]
        with self.assertRaisesRegex(ValueError,'identity census'):
            inventory.detector_cost_record(result,nano,source)

    def test_single_event_cost_and_wrong_provenance(self):
        source=inventory.source_record(cached_source())
        source['prepared_gen_sha256']='c'*64
        ids=[[10,1,1]]
        result=dict(complete=True,exit_code=0,source_gen_sha256='c'*64,
            normalization_record=source['full_original_exposure'],nano_sha256='f'*64,
            events=1,wall_seconds=250.,stages={str(i):dict(seconds=100./i,
                audit=dict(event_ids=ids)) for i in range(1,5)})
        nano=dict(path='/nano.root',sha256='f'*64,events=1)
        cost=inventory.detector_cost_record(result,nano,source)
        self.assertEqual(cost['event_cost_kind'],'actual_single_event_stage_wall')
        self.assertEqual(cost['event_costs'][0]['SIM_stage_wall_seconds'],100.)
        result['source_gen_sha256']='9'*64
        with self.assertRaisesRegex(ValueError,'binding changed'):
            inventory.detector_cost_record(result,nano,source)


if __name__=='__main__':
    unittest.main()
