# Code style: AI-code. AI-generated tests for paired original grouping and metadata.
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT_DIR=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT_DIR/'tea_shift_cmssw/utils'))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import run_shift_sm_decay_collection as collection
from run_shift_sm_decay_pilot import weight_authority
from prepare_shift_sm_decay_validation import frozen_draw, original_census


class GroupedStatisticsTest(unittest.TestCase):
    def test_cross_DAG_wait_does_not_turn_missing_or_failed_responses_into_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            request=Path(directory)/'request.json'
            plan=dict(cases=[dict(path=str(request),case_id='paired-original')])
            with self.assertRaisesRegex(TimeoutError,'Missing paired'):
                collection.wait_for_case_results(plan,0)
            request.with_name('result.json').write_text(json.dumps(dict(complete=False,exit_code=1)))
            with self.assertRaisesRegex(ValueError,'Parent response failed'):
                collection.wait_for_case_results(plan,60)
            request.with_name('result.json').write_text(json.dumps(dict(complete=True,exit_code=0)))
            collection.wait_for_case_results(plan,0)

    def test_native_inclusion_and_decay_corrections_are_each_applied_once(self):
        request=dict(event_id=[1,1,2],original_source=dict(source_id='synthetic',gen_sha256='a'*64),
                     native_weight=3.e-9,source_inclusion_probability=.125,decay_seed=17,arm='mixture')
        authority=weight_authority(request,dict(proposal_density_ratio=2.e8,proposal_correction=5.e-9))
        self.assertAlmostEqual(authority['sampling_gen_weight']/3.e-9,8.)
        self.assertAlmostEqual(authority['proposal_gen_weight']/(3.e-9*5.e-9),8.)
        request['arm']='ordinary'
        self.assertEqual(weight_authority(request,dict(proposal_density_ratio=1.,proposal_correction=1.))[
            'proposal_gen_weight'],3.e-9/.125)
        with self.assertRaisesRegex(ValueError,'ordinary'):
            weight_authority(request,dict(proposal_density_ratio=2.,proposal_correction=.5))

    def test_several_pairs_from_one_original_remain_one_independent_contribution(self):
        rows=[dict(labels=dict(pair_counts=dict(both_both=2)),
                   authority=dict(proposal_gen_weight=3.)),
              dict(labels=dict(pair_counts=dict(both_both=0)),
                   authority=dict(proposal_gen_weight=7.))]
        result=collection.statistics(rows,'both_both')
        self.assertEqual(result['raw_pairs'],2)
        self.assertEqual(result['positive_originals'],1)
        self.assertEqual(result['sumw'],6.)
        self.assertEqual(result['grouped_second_moment'],36.)
        self.assertEqual(result['effective_originals'],1.)
        rows[0]['labels']['pair_counts']['both_both']=0
        self.assertTrue(collection.statistics(rows,'both_both')['zero_observed_is_not_zero_rate'])


class IndependentDrawTest(unittest.TestCase):
    def test_pion_pointing_away_is_a_known_miss_not_an_unknown_model(self):
        import math
        source=dict(saved_events=1,gen_sha256='a'*64)
        event=dict(event_id=[1,1,1],native_weight=2.,target_position_cm=[0.,0.,14800.],
            completion=dict(parent_barcodes=[1,2,3],parent_PDG_ids=[211,-211,211]))
        parents=[dict(barcode=i,pdg=pdg,birth_cm=[0.,0.,14800.],momentum_GeV=p,
            energy_GeV=math.sqrt(sum(v*v for v in p)+.14**2),
            material=dict(first_depth_one_distance_cm=10.))
            for i,pdg,p in [(1,211,[10.,0.,-500.]),(2,-211,[-10.,0.,-500.]),
                            (3,211,[1.,0.,500.])]]
        feature=dict(event_id=[1,1,1],native_weight=2.,original_source_sha256='a'*64,parent_flights=parents)
        audit=dict(complete=True,units=[dict(source=source,events=[event])])
        census=original_census(audit,dict(events=[feature]),source)
        self.assertEqual(census[0]['region'],'tail')
        self.assertEqual(census[0]['proposal']['parent_barcodes'],[1,2])

    def test_complete_population_controls_have_exact_inclusion_and_unknowns_stay(self):
        rows=[dict(event_id=[1,1,i],region='control') for i in range(1,12)]
        rows.extend([dict(event_id=[1,1,12],region='tail'),dict(event_id=[1,1,13],region='unknown')])
        before=copy.deepcopy(rows);draw=frozen_draw(rows,'frozen-before-reco')
        self.assertEqual(rows,before)
        self.assertEqual(draw,frozen_draw(rows,'frozen-before-reco'))
        self.assertEqual(sum(r['selected'] for r in draw),4)
        self.assertTrue(all(r['selected'] and r['sampling_probability']==1. for r in draw[-2:]))
        self.assertTrue(all(r['sampling_probability']==2/11 for r in draw[:11]))
        # A constant observable closes exactly to all 13 originals after W/p.
        self.assertAlmostEqual(sum(1/r['sampling_probability'] for r in draw if r['selected']),13.)
        with self.assertRaisesRegex(ValueError,'Repeated'):
            frozen_draw(rows+[rows[0]],'seed')

    def test_missing_or_differently_ordered_originals_stop_census_preparation(self):
        source=dict(saved_events=1,gen_sha256='a'*64)
        audit=dict(complete=True,units=[dict(source=source,events=[dict(event_id=[1,1,1])])])
        with self.assertRaisesRegex(ValueError,'ordered events'):
            original_census(audit,dict(events=[dict(event_id=[1,1,2])]),source)


@unittest.skipUnless(importlib.util.find_spec('ROOT') and importlib.util.find_spec('uproot'),
                     'Requires native ROOT and uproot')
class AnnotationMergeTest(unittest.TestCase):
    def fixture(self,path,event):
        import numpy as np
        import uproot
        from shift_event_weights import WEIGHT_POLICY
        annotation=dict(original_event_ids=[[1,1,event]],weight_policy=WEIGHT_POLICY,
                        input_nano_sha256=str(event)*64,decay_correction_application_count=1,
                        source_exposures=[dict(source_id='synthetic',original_source_sha256='a'*64,
                                               full_source_trials=1000)])
        with uproot.recreate(path) as root:
            for name,columns in dict(Events=dict(event=[event],weight=[1.e-23]),
                Runs=dict(run=[1],generated=[1000]),LuminosityBlocks=dict(lumi=[1]),
                MetaData=dict(history=[17]),ParameterSets=dict(parameter=[23])).items():
                arrays={k:np.asarray(v,dtype='float64' if k=='weight' else 'uint64') for k,v in columns.items()}
                root.mktree(name,{k:v.dtype for k,v in arrays.items()});root[name].extend(arrays)
            root['ShiftDecayProposalWeights']=json.dumps(annotation)

    def test_combined_authority_preserves_all_values_and_native_metadata_through_merge(self):
        import ROOT
        from shift_nano_integrity import semantic_inventory,compare_all_branches
        with tempfile.TemporaryDirectory() as directory:
            paths=[Path(directory)/f'input{i}.root' for i in (1,2)]
            for i,path in enumerate(paths,1):self.fixture(path,i)
            before=[semantic_inventory([p]) for p in paths]
            with self.assertRaisesRegex(ValueError,'string metadata'):
                semantic_inventory(paths)
            collection.combine_annotation_metadata([dict(local=p) for p in paths])
            after=[semantic_inventory([p]) for p in paths]
            for original,current in zip(before,after):
                original.pop('ShiftDecayProposalWeights');remaining=dict(current);remaining.pop('ShiftDecayProposalWeights')
                self.assertEqual(original,remaining)
            metadata=json.loads(after[0]['ShiftDecayProposalWeights']['value'])
            self.assertEqual(metadata['independent_originals'],2)
            self.assertEqual(len(metadata['members']),2)
            self.assertEqual(len(metadata['source_exposures']),1)
            self.assertEqual(metadata['source_exposures'][0]['full_source_trials'],1000)
            merged=Path(directory)/'merged.root';merger=ROOT.TFileMerger(False)
            merger.OutputFile(str(merged),'RECREATE')
            for path in paths:merger.AddFile(str(path))
            self.assertTrue(merger.Merge())
            self.assertEqual(compare_all_branches(paths,merged)['events'],2)

    def test_repeated_original_authorities_are_rejected_before_any_file_is_changed(self):
        from shift_nano_integrity import semantic_inventory
        with tempfile.TemporaryDirectory() as directory:
            paths=[Path(directory)/f'input{i}.root' for i in (1,2)]
            for path in paths:self.fixture(path,1)
            before=[semantic_inventory([p]) for p in paths]
            with self.assertRaisesRegex(ValueError,'Repeated'):
                collection.combine_annotation_metadata([dict(local=p) for p in paths])
            self.assertEqual(before,[semantic_inventory([p]) for p in paths])


if __name__=='__main__':unittest.main()
