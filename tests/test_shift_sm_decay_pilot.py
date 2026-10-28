# Code style: AI-code. AI-generated tests for paired original grouping and metadata.
import copy
import importlib.util
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import os
import types

ROOT_DIR=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT_DIR/'tea_shift_cmssw/utils'))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import run_shift_sm_decay_collection as collection
from run_shift_sm_decay_pilot import weight_authority
from prepare_shift_sm_decay_validation import frozen_draw, original_census
from shift_sm_decay_bank import probabilities as bank_probabilities, choose as choose_bank, muon_score
from shift_sm_decay_bank import audit_native_member, audit_record, validate_settings


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
    def native_audit(self):
        from audit_shift_weighted_gen import DEFERRED_CHARM_PROFILE
        return dict(base_kernel='native_ordinary',original_full_source=dict(
            generator_profile=DEFERRED_CHARM_PROFILE,source_family='qcd_ordinary',decay_dependent_filter=False,
            source_id='synthetic',gen_sha256='a'*64,saved_events=1,
            deferred_charm_frontiers=[dict(event_id=[1,1,2],parents=[])]),original_event_id=[1,1,2],
            proposal_definition=dict(ordinary_fraction=1.,components=[]),ordinary_fraction=1.,
            proposal_density_ratio=1.,proposal_correction=1.,proposal_component=0,
            selected_parent_barcodes=[],deferred_charm_completion=dict(
                all_original_D_roots_completed=True,no_nested_new_D=True,parent_barcodes=[],parent_pdg_ids=[]))

    def test_only_explicit_native_kernel_expands_bank_size_bound(self):
        settings=dict(size=32,control_fraction=.1)
        self.assertEqual(validate_settings(settings),'smooth_lifetime')
        settings['size']=128
        with self.assertRaises(ValueError):validate_settings(settings)
        settings['base_kernel']='native_ordinary'
        self.assertEqual(validate_settings(settings),'native_ordinary')
        for key,value in [('size',257),('size',True),('base_kernel','forced_D'),('control_fraction',0.)]:
            changed=dict(settings);changed[key]=value
            with self.subTest(key=key,value=value),self.assertRaises(ValueError):validate_settings(changed)

    def test_native_member_rejects_nonunit_lifetime_channel_and_missing_frontier_audits(self):
        audit_native_member(self.native_audit())
        for key,value in [('proposal_density_ratio',2.),('proposal_correction',.5),
                          ('ordinary_fraction',.1),('proposal_density_ratio',True),
                          ('selected_parent_barcodes',[7]),('proposal_component',1),
                          ('deferred_charm_completion',dict(no_nested_new_D=False))]:
            audit=self.native_audit();audit[key]=value
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,'unit-density'):audit_native_member(audit)
        audit=self.native_audit();audit['original_full_source']['generator_profile']='ordinary'
        with self.assertRaises(ValueError):audit_native_member(audit)
        audit=self.native_audit();audit['deferred_charm_completion']['parent_barcodes']=[7]
        with self.assertRaisesRegex(ValueError,'frozen D frontier'):audit_native_member(audit)

    def test_native_bank_choice_record_and_final_weight_apply_only_one_exact_choice_factor(self):
        selection=choose_bank([0.,1.],.1,17)
        members=[dict(index=i,seed=71+i,score=dict(score=score),GEN_sha256=str(i)*64,
            graph_sha256=str(i)*64,
            base_kernel='native_ordinary',lifetime_density_ratio=1.,lifetime_correction=1.)
            for i,score in enumerate([0.,1.])]
        record=dict(size=2,base_kernel='native_ordinary',same_original_event_id=[1,1,2],members=members,
            control_fraction=.1,selection_seed=17,selection=selection,
            selected_GEN_sha256=members[selection['index']]['GEN_sha256'],material_survival_correction_applied=False)
        self.assertEqual(audit_record(record,[1,1,2]),selection)
        changed=copy.deepcopy(record);changed['members'][0]['lifetime_correction']=.5
        with self.assertRaisesRegex(ValueError,'nonunit'):audit_record(changed,[1,1,2])
        changed=copy.deepcopy(record);changed['members'][0].pop('graph_sha256')
        with self.assertRaisesRegex(ValueError,'graph hash'):audit_record(changed,[1,1,2])
        legacy=copy.deepcopy(record);legacy.pop('base_kernel')
        for member in legacy['members']:member.pop('graph_sha256')
        self.assertEqual(audit_record(legacy,[1,1,2]),selection)
        audit=self.native_audit()
        request=dict(event_id=[1,1,2],original_source=copy.deepcopy(audit['original_full_source']),
            native_weight=3.e-9,source_inclusion_probability=.125,decay_seed=17,arm='mixture')
        probability=selection['probabilities'][selection['index']]
        audit['decay_bank']=dict(size=2,base_kernel='native_ordinary',selected_probability=probability,
            selection_correction=1./(2.*probability))
        authority=weight_authority(request,audit)
        self.assertAlmostEqual(authority['proposal_gen_weight'],3.e-9/.125/(2.*probability))
        self.assertEqual(authority['lifetime_correction'],1.)

    def test_native_bank_prepares_only_ordinary_members_and_collector_rereads_every_member(self):
        import gzip
        import shutil
        import run_shift_sm_decay_pilot as pilot
        import shift_gen_material_features as feature_reader
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);geometry=root/'geometry';geometry.write_bytes(b'synthetic')
            request=dict(event_id=[1,1,2],arm='mixture',decay_seed=71,
                original_source=self.native_audit()['original_full_source'],
                decay_bank=dict(base_kernel='native_ordinary',size=2,control_fraction=.1,selection_seed=17,
                    material=dict(geometry_path=str(geometry),geometry_sha256=pilot.sha(geometry),
                        frame={},event_timeout_seconds=1.)))
            request['original_source']['url']='synthetic'
            calls=[]
            def prepare(variant,raw):
                calls.append(variant['arm']);self.assertEqual(variant['arm'],'ordinary')
                Path('placed.root').write_bytes(str(variant['decay_seed']).encode())
                audit=self.native_audit();audit.update(original_full_source=variant['original_source'],
                    proposal_draw_seed=variant['decay_seed'],prepared_GEN_sha256=pilot.sha('placed.root'))
                pilot.dump('gen_audit.json',audit);return audit
            def features(*args):
                return dict(event_id=[1,1,2],graph_sha256=pilot.sha('placed.root'),
                    muons=[dict(score=float(Path.cwd().name[-1]))],parent_flights=[])
            def score(muons,parents):return dict(score=muons[0]['score'],pair=None)
            manager=types.SimpleNamespace(LockDefaultUnits=lambda x:None,SetDefaultUnits=lambda x:None,
                kRootUnits=1,Import=lambda path:object())
            fake_root=types.SimpleNamespace(TGeoManager=manager)
            fwlite=types.SimpleNamespace(Events=lambda path:iter([object()]))
            previous=Path.cwd()
            try:
                os.chdir(root)
                with patch.dict(sys.modules,{'ROOT':fake_root,'DataFormats':types.ModuleType('DataFormats'),
                                            'DataFormats.FWLite':fwlite}), \
                     patch.object(pilot,'download',side_effect=lambda *args:Path(args[1]).write_bytes(b'raw')), \
                     patch.object(pilot,'prepare',side_effect=prepare), \
                     patch.object(feature_reader,'read_hepmc',return_value=([],1.,[1.],None)), \
                     patch.object(feature_reader,'bounded_event_features',side_effect=features), \
                     patch('shift_sm_decay_bank.muon_score',side_effect=score):
                    audit=pilot.prepare_bank(request)
                    self.assertEqual(calls,['ordinary','ordinary'])
                    record=json.loads(Path('decay_bank.json').read_text())
                    self.assertEqual(record['base_kernel'],'native_ordinary')
                    for index,member in enumerate(record['members']):
                        self.assertEqual(member['graph_sha256'],json.loads(
                            (root/f'bank_{index:02d}/material_features.json').read_text())['graph_sha256'])
                    pins=[dict(name='decay_bank.json',path=str(root/'decay_bank.json'),sha256=pilot.sha('decay_bank.json')),
                          dict(name='gen.root',sha256=pilot.sha('placed.root'))]
                    for index in range(2):
                        for local,label in [('gen_audit.json','audit.json'),('material_features.json','material_features.json')]:
                            original=root/f'bank_{index:02d}'/local
                            compressed=original.with_suffix(original.suffix+'.gz')
                            compressed.write_bytes(gzip.compress(original.read_bytes(),mtime=0))
                            pins.append(dict(name=f'bank_{index:02d}_'+label+'.gz',path=str(compressed),sha256=pilot.sha(compressed)))
                    request.update(native_weight=3.e-9,source_inclusion_probability=.125)
                    result=dict(GEN_publications=pins,GEN_audit=audit,weight_authority=pilot.weight_authority(request,audit))
                    with patch.object(collection,'download',side_effect=lambda source,target,checksum:shutil.copyfile(source,target)):
                        readback=collection.audit_bank_result(result,request,'synthetic')
                        record['members'][0]['graph_sha256']='f'*64
                        pilot.dump('decay_bank.json',record)
                        pins[0]['sha256']=pilot.sha('decay_bank.json')
                        audit['decay_bank']['sha256']=pins[0]['sha256']
                        with self.assertRaisesRegex(ValueError,'graph differs'):
                            collection.audit_bank_result(result,request,'changed_graph')
                    self.assertEqual(readback['members'],2)
            finally:
                os.chdir(previous)

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

    def test_native_ordinary_bank_closes_with_shared_original_nuisance_and_no_branching_weight(self):
        import itertools
        p_pair=.01;size=3;floor=.1;p_prompt=.4
        corrected=[0.,0.,0.];native=[1.,p_prompt,p_pair+p_prompt+2.*p_pair*p_prompt]
        # The prompt pair belongs to the fixed original and is shared by all
        # bank members. Only the ordinary native decay outcome is redrawn.
        for prompt in (False,True):
            nuisance_density=p_prompt if prompt else 1.-p_prompt
            for bank in itertools.product((0,1),repeat=size):
                bank_density=nuisance_density
                for pair in bank:
                    bank_density*=p_pair if pair else 1.-p_pair
                choice=bank_probabilities(bank,floor)
                for index,pair in enumerate(bank):
                    correction=1./(size*choice[index])
                    self.assertLessEqual(correction,1./floor)
                    # All OS combinations retain cross-parent/unrelated pairs.
                    observables=[1.,int(prompt),(pair+int(prompt))**2]
                    for i,value in enumerate(observables):
                        corrected[i]+=bank_density*choice[index]*correction*value
        for expected,actual in zip(native,corrected):
            self.assertAlmostEqual(actual,expected,places=14)

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


class PreparedPublicationTest(unittest.TestCase):
    def test_failed_produced_GEN_has_a_bounded_return_archive_without_hiding_the_first_error(self):
        import tarfile
        import run_shift_sm_decay_pilot as pilot
        previous=Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory);Path('bank_00').mkdir()
                Path('bank_00/placed.root').write_bytes(b'minimal failed GEN')
                Path('bank_00/prepare.log').write_text('first native error')
                saved=pilot.preserve_failed_prepared(dict(failure_GEN_return_bound_bytes=4096))
                self.assertLessEqual(saved['archive']['bytes'],4096)
                with tarfile.open(saved['archive']['path']) as archive:
                    self.assertEqual(archive.extractfile('placed.root').read(),b'minimal failed GEN')
                self.assertTrue(Path('bank_00/placed.root').exists())
                self.assertIsNone(pilot.preserve_failed_prepared({}))
            finally:os.chdir(previous)

    def test_opt_in_publishes_exact_prepared_state_and_authority_without_SIM(self):
        import run_shift_sm_decay_pilot as pilot
        request=dict(case_id='synthetic',arm='ordinary',event_id=[1,1,2],native_weight=3.e-9,
            original_source=dict(source_id='synthetic',gen_sha256='a'*64),decay_seed=17,
            detector_source=dict(production_selection={},sampling={}),
            publication_directory='/eos/user/j/jniedzie/shift_cmssw_support/synthetic',reservation_bytes=4096)
        def prepare(request):
            audit=dict(proposal_density_ratio=1.,proposal_correction=1.)
            Path('placed.root').write_bytes(b'exact frozen state')
            pilot.dump('gen_audit.json',audit)
            return audit
        published=[]
        def publish(outputs,base):
            published.extend(name for _,name in outputs)
            return [dict(name=name,path=base+'/'+name,sha256=pilot.sha(path),bytes=path.stat().st_size)
                    for path,name in outputs]
        previous=Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                with patch.object(pilot,'validate'),patch.object(pilot,'prepare',side_effect=prepare), \
                     patch.object(pilot,'preflight',return_value=dict(complete=True)), \
                     patch.object(pilot,'publish',side_effect=publish) as publisher, \
                     patch.object(pilot,'command',side_effect=AssertionError('SIM must not run')):
                    pilot.run(request,prepare_only=True)
                    self.assertFalse(publisher.called)
                    pilot.run(request,prepare_only=True,publish_prepared=True)
                    report=json.loads(Path('pilot_report.json').read_text())
                    self.assertTrue(report['complete']);self.assertFalse(report['detector_run'])
                    self.assertTrue(report['prepared_state_must_not_be_redrawn'])
                    self.assertEqual(set(published),{'gen.root','gen_audit.json','prepared_source.json',
                        'weight_authority.json','pilot_admission_authority.json'})
                    descriptor=json.loads(Path('prepared_source.json').read_text())
                    self.assertEqual(descriptor['gen_sha256'],pilot.sha('placed.root'))
                    self.assertEqual(descriptor['prepared_weight_authority']['sha256'],pilot.sha('weight_authority.json'))
                    self.assertEqual(json.loads(Path('weight_authority.json').read_text())[0],report['weight_authority'])
                    publisher.reset_mock();request['reservation_bytes']=1
                    with self.assertRaisesRegex(ValueError,'publication bound'):
                        pilot.run(request,prepare_only=True,publish_prepared=True)
                    self.assertFalse(publisher.called)
                    with self.assertRaisesRegex(ValueError,'requires --prepare-only'):
                        pilot.run(request,publish_prepared=True)
            finally:
                os.chdir(previous)


class PreparedCollectionTest(unittest.TestCase):
    def test_GEN_pair_report_keeps_unrelated_pairs_and_labels_pretransport_truth(self):
        frozen=dict(event_id=[1,1,2],original_source=dict(deferred_charm_frontiers=[
            dict(event_id=[1,1,2],parents=[dict(barcode=7),dict(barcode=8)])]))
        energy=math.sqrt(4.+.105658**2)
        features=dict(muons=[dict(barcode=i,pdg=pdg,momentum_GeV=p,energy_GeV=energy,birth_cm=[0.,0.,1.],
            first_non_muon_parent_barcodes=parents,ancestor_barcodes=parents)
            for i,pdg,p,parents in [(1,13,[2.,0.,0.],[7]),(2,-13,[-2.,0.,0.],[8]),(3,-13,[0.,2.,0.],[9])]])
        result=collection.prepared_kinematics(features,frozen)
        self.assertEqual(result['stable_muons'],3);self.assertEqual(result['OS_pairs'],2)
        self.assertEqual(result['direct_D_muons'],2);self.assertEqual(result['direct_D_OS_pairs'],1)
        self.assertTrue(result['before_G4_survival_and_reconstruction'])
        self.assertAlmostEqual(result['maximum_mass_GeV'],2.*energy)

    def test_GEN_only_collector_rereads_exact_sidecar_and_writes_watchdog_completion_fields(self):
        import shutil
        import run_shift_sm_decay_pilot as pilot
        previous=Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory);root=Path(directory);case=root/'case';case.mkdir();pub=root/'published';pub.mkdir()
                audit=FiniteDecayBankTest().native_audit()
                frozen=dict(arm='ordinary',event_id=[1,1,2],native_weight=3.e-9,decay_seed=17,
                    original_source=audit['original_full_source'],reservation_bytes=4096)
                pilot.dump(case/'request.json',frozen)
                (pub/'gen.root').write_bytes(b'prepared GEN')
                audit['prepared_GEN_sha256']=pilot.sha(pub/'gen.root')
                pilot.dump(pub/'gen_audit.json',audit)
                authority=pilot.weight_authority(frozen,audit);pilot.dump(pub/'weight_authority.json',[authority])
                pilot.dump(pub/'pilot_admission_authority.json',dict(event_id=[1,1,2],
                    original_source_sha256='a'*64,prepared_GEN_sha256=audit['prepared_GEN_sha256'],sampling_probability=1.))
                descriptor=dict(gen=str(pub/'gen.root'),gen_sha256=pilot.sha(pub/'gen.root'),event_ids=[[1,1,2]],
                    weights=[3.e-9],normalization_record=frozen['original_source'],
                    sampling=dict(plan_sha256=pilot.sha(pub/'pilot_admission_authority.json')),
                    prepared_weight_authority=dict(path=str(pub/'weight_authority.json'),
                        sha256=pilot.sha(pub/'weight_authority.json'),required_for_downstream_weights=True))
                pilot.dump(pub/'prepared_source.json',descriptor)
                pins=[dict(name=p.name,path=str(p),sha256=pilot.sha(p),bytes=p.stat().st_size) for p in pub.iterdir()]
                result=dict(complete=True,exit_code=0,request_sha256=pilot.sha(case/'request.json'),detector_run=False,
                    published_prepared_state=True,original_event_id=[1,1,2],GEN_publications=pins,GEN_audit=audit,weight_authority=authority)
                pilot.dump(case/'result.json',result)
                plan=dict(cases=[dict(case_id='synthetic',arm='ordinary',request_path=str(case/'request.json'),
                    request_sha256=pilot.sha(case/'request.json'))],independent_originals=1,
                    comparison_groups=['ordinary'],conditional_original_choice=True,
                    maximum_new_prepared_publication_bytes=4096,existing_verified_source_publication_bytes=0,maximum_total_component_bytes=4096)
                pilot.dump(root/'plan.json',plan)
                request=dict(plan_path=str(root/'plan.json'),plan_sha256=pilot.sha(root/'plan.json'),
                    prepared_GEN_only=True,reservation_bytes=0)
                pilot.dump('collector_request.json',request)
                def download(source,target,expected):
                    self.assertEqual(pilot.sha(source),expected);shutil.copyfile(source,target)
                with patch.object(collection,'download',side_effect=download), \
                     patch.object(collection,'read_prepared_GEN',return_value=dict(graph_sha256='b'*64,kinematics=dict(stable_muons=0,OS_pairs=0))), \
                     patch.object(collection,'publish',side_effect=AssertionError('GEN-only collection must not write EOS')):
                    collection.run(Path('collector_request.json'))
                    report=json.loads(Path('report.json').read_text())
                    self.assertTrue(report['complete']);self.assertTrue(report['complete_frozen_draw_collected'])
                    self.assertEqual(report['benchmark_plan_sha256'],request['plan_sha256'])
                    self.assertFalse(report['detector_run'])
                    changed=[dict(authority,proposal_correction=.5)]
                    pilot.dump(pub/'weight_authority.json',changed)
                    for pin in result['GEN_publications']:
                        if pin['name']=='weight_authority.json':pin['sha256']=pilot.sha(pub/'weight_authority.json')
                    pilot.dump(case/'result.json',result)
                    with self.assertRaisesRegex(ValueError,'sidecar differs'):
                        collection.run(Path('collector_request.json'))
                    self.assertFalse(json.loads(Path('report.json').read_text())['complete'])
            finally:os.chdir(previous)


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
