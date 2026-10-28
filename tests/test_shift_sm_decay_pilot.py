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
from shift_sm_decay_bank import probabilities as bank_probabilities, choose as choose_bank, muon_score


class GroupedStatisticsTest(unittest.TestCase):
    def test_single_group_cannot_be_used_as_an_unqualified_population_comparison(self):
        self.assertEqual(collection.comparison_groups({}),['ordinary','mixture'])
        self.assertEqual(collection.comparison_groups(dict(comparison_groups=['ordinary'],
            conditional_original_choice=True)),['ordinary'])
        with self.assertRaisesRegex(ValueError,'conditional diagnostic'):
            collection.comparison_groups(dict(comparison_groups=['ordinary']))
        for groups in [[],['ordinary','ordinary'],['bad/path'],['a','b','c']]:
            with self.subTest(groups=groups),self.assertRaises(ValueError):
                collection.comparison_groups(dict(comparison_groups=groups,conditional_original_choice=True))

    def test_fixed_size_variance_matches_exact_enumeration_of_every_control_draw(self):
        import itertools
        observed=[];estimates=[];values=[0.,0.,1.,3.]
        for selected in itertools.combinations(range(4),2):
            draw=[dict(event_id=[1,1,i+1],selected=i in selected,region='control',
                       native_weight=1.,sampling_probability=.5) for i in range(4)]
            rows=[dict(labels=dict(event_id=[1,1,i+1],pair_counts=dict(target=values[i])),
                authority=dict(native_weight=1.,sampling_probability=.5,proposal_correction=1.,
                               proposal_gen_weight=2.)) for i in selected]
            result=collection.cohort_variance(rows,draw,4,'target')
            observed.append(result['sumw']);estimates.append(result)
        design_variance=sum((x-4.)**2 for x in observed)/6
        self.assertAlmostEqual(design_variance,8.)
        self.assertAlmostEqual(sum(r['fixed_size_control_variance_estimate'] for r in estimates)/6,8.)
        self.assertAlmostEqual(sum(r['total_variance_estimate'] for r in estimates)/6,16.)
        self.assertAlmostEqual(sum(r['natural_sampling_variance_estimate'] for r in estimates)/6,8.)
        with self.assertRaisesRegex(ValueError,'every selected original'):
            collection.cohort_variance(rows[:-1],draw,4,'target')

    def precision_rows(self,probability,correction):
        row=dict(labels=dict(event_id=[1,1,1],pair_counts=dict(both_both=2)),
            authority=dict(native_weight=3.,sampling_probability=probability,
                proposal_correction=correction,proposal_gen_weight=3.*correction/probability),
            payload_seconds=2.)
        ordinary=copy.deepcopy(row);ordinary['authority']['proposal_correction']=1.
        ordinary['authority']['proposal_gen_weight']=3./probability
        return [row],[ordinary]

    def test_uniform_thinning_of_useful_events_does_not_invent_a_precision_gain(self):
        rows,ordinary=self.precision_rows(.125,1.)
        comparison=collection.natural_sampling_comparison(rows,ordinary,'both_both')
        self.assertAlmostEqual(comparison['second_moment_ratio'],.125)
        self.assertAlmostEqual(comparison['second_moment_per_wall_time_gain_estimate'],1.)

    def test_actual_density_can_estimate_natural_variance_without_an_observed_control_pair(self):
        rows,ordinary=self.precision_rows(1.,.001)
        ordinary[0]['labels']['pair_counts']['both_both']=0
        comparison=collection.natural_sampling_comparison(rows,ordinary,'both_both')
        self.assertAlmostEqual(comparison['second_moment_ratio'],1000.)
        self.assertTrue(comparison['baseline_estimated_with_exact_likelihood_not_observed_tail'])
        self.assertFalse(comparison['production_gain_qualified'])
        self.assertIsNone(comparison['confidence_interval'])
        rows,ordinary=self.precision_rows(1.,10.)
        self.assertAlmostEqual(collection.natural_sampling_comparison(rows,ordinary,'both_both')[
            'second_moment_per_wall_time_gain_estimate'],.1)

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


class FiniteDecayBankTest(unittest.TestCase):
    def test_material_ranking_uses_the_same_pair_and_counts_shared_parent_once(self):
        import math
        muons=[dict(barcode=i,pdg=pdg,momentum_GeV=[px,0.,-500.],
            birth_cm=[0.,0.,14800.],energy_GeV=math.sqrt(500.**2+px**2+.106**2),
            transport_parent_barcodes=[1],material_to_CMS_entry=dict(complete=True,column_density_g_cm2=0.))
            for i,pdg,px in [(1,13,10.),(2,-13,-10.)]]
        reference=muon_score(muons)
        parents={1:dict(material=dict(complete=True,interaction_depth_proxy=2.))}
        measured=muon_score(muons,parents)
        self.assertAlmostEqual(measured['score']/reference['score'],math.exp(-2.))
        self.assertFalse(measured['pair']['unknown_material_coverage'])
        parents[1]['material']=dict(complete=False,interaction_depth_proxy=0.)
        unknown=muon_score(muons,parents)
        self.assertTrue(unknown['pair']['unknown_material_coverage'])
        self.assertGreater(unknown['score'],0.)

    def test_exact_two_state_enumeration_preserves_native_rate_after_bank_choice(self):
        import itertools
        native=[.99,.01];proposal=[.5,.5];answer=0.
        for bank in itertools.product(range(2),repeat=3):
            probabilities=bank_probabilities([float(x==1) for x in bank],.1)
            bank_density=1.
            for state in bank:bank_density*=proposal[state]
            for index,state in enumerate(bank):
                correction=native[state]/proposal[state]/(len(bank)*probabilities[index])
                answer+=bank_density*probabilities[index]*correction*(state==1)
        self.assertAlmostEqual(answer,.01,places=14)
        self.assertEqual(bank_probabilities([0.,0.],.1),[.5,.5])
        self.assertTrue(all(p>=.1/3 for p in bank_probabilities([0.,1.,100.],.1)))
        self.assertEqual(choose_bank([0.,1.,100.],.1,17),choose_bank([0.,1.,100.],.1,17))
        with self.assertRaises(ValueError):bank_probabilities([0.,1.],0.)

    def test_lifetime_source_and_bank_corrections_remain_separate(self):
        request=dict(event_id=[1,1,2],original_source=dict(source_id='synthetic',gen_sha256='a'*64),
                     native_weight=3.e-9,source_inclusion_probability=.125,decay_seed=17,arm='mixture')
        audit=dict(proposal_density_ratio=2.e8,proposal_correction=5.e-9,
            decay_bank=dict(size=8,selected_probability=.5,selection_correction=.25))
        authority=weight_authority(request,audit)
        self.assertAlmostEqual(authority['proposal_density_ratio'],8.e8)
        self.assertAlmostEqual(authority['proposal_gen_weight']/(3.e-9/.125*5.e-9),.25)
        audit['decay_bank']['selection_correction']=2.
        with self.assertRaisesRegex(ValueError,'Finite-bank'):
            weight_authority(request,audit)


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
