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


class GroupedStatisticsTest(unittest.TestCase):
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
