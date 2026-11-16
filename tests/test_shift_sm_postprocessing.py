# Code style: AI-code. AI-generated tests of closed-source SM publication.
import sys
from pathlib import Path
import unittest
import tempfile
import json
import errno
import copy
import math
import importlib.util
import hashlib
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tea_shift_cmssw/utils'))
from run_shift_sm_postprocessing import exposure, selected_rows, hadd_command, validate_dimuon_histograms
from run_shift_sm_postprocessing_controller import closed_sources, meaningful_update, snapshot, publication_order
from run_shift_smart_sm_controller import postprocessing_charge
from run_shift_smart_sm_source import accepted_class_authority
import run_shift_smart_sm_controller as main_controller
import run_shift_sm_postprocessing as post
from test_shift_production_selection import native_proposal_fixture
from shift_production_selection import bind_source, selected_decay_proposals, proposal_source_exposure
from run_shift_sm_postprocessing_controller import compact_source, response_closed_source


class PublicationOrderTest(unittest.TestCase):
    def test_default_keeps_small_jobs_first(self):
        tasks = {'large': dict(stratum='dy_20toinf', events=90),
                 'small': dict(stratum='jpsi_0to1', events=10)}
        self.assertEqual([name for name, _ in publication_order(tasks)], ['small', 'large'])

    def test_requested_complete_bin_precedes_other_groups_and_preserves_inputs(self):
        tasks = {'dy_group': dict(stratum='dy_20toinf', events=90),
                 'other': dict(stratum='jpsi_0to1', events=10),
                 'dy_snapshot': dict(stratum='dy_20toinf', events=8500)}
        original = copy.deepcopy(tasks)
        self.assertEqual([name for name, _ in publication_order(tasks, ['dy_20toinf'])],
                         ['dy_group', 'dy_snapshot', 'other'])
        self.assertEqual(tasks, original)

    def test_invalid_or_repeated_preferences_are_rejected(self):
        for preference in ['dy_20toinf', ['dy_20toinf', 'dy_20toinf'], [None], ['']]:
            with self.subTest(preference=preference), self.assertRaises(ValueError):
                publication_order({}, preference)


class ResponsePublicationTest(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.root=Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)
        self.normalization=dict(source_id='response',stratum='qcd_20toinf',gen_sha256='a'*64,
            denominator_kind='fixed_source_trials',full_source_trials=1000,sigma_nd_pb=123.)
        original=self.write('original.json',dict(complete=True,exit_code=0,source=self.normalization))
        source=dict(event_ids=[[1,1,11],[1,1,99],[1,1,12]],weights=[2.,4.,3.],
            selected_indices=[0,1,2],sampling_probabilities=[.25,1.,.5],gen_sha256='b'*64,
            normalization_record=self.normalization,nano_base='/eos/user/j/jniedzie/shift_cmssw_support/test')
        selected=[]
        for position,region,q in [(0,'deep',.5),(2,'candidate',1.)]:
            selected.append(dict(source_id='response',stratum='qcd_20toinf',
                event_id=source['event_ids'][position],native_weight=source['weights'][position],
                existing_sampling_probability=source['sampling_probabilities'][position],
                selected_position=position,original_index=position,gen_sha256='a'*64,
                prepared_gen_sha256='b'*64,benchmark_region=region,benchmark_inclusion_probability=q))
        self.plan=dict(draw_frozen=True,population_events=3,selected_events=2,selected=selected,
            strata=[dict(source_id='response',stratum='qcd_20toinf',benchmark_region=region,
                population_events=n,selected_events=1,benchmark_inclusion_probability=q)
                for region,n,q in [('deep',2,.5),('candidate',1,1.)]])
        plan_pin=self.write('plan.json',self.plan)
        full=self.write('full.json',dict(sources=[dict(source_id='response',
            original_raw_gen_sha256='a'*64,prepared_gen_sha256='b'*64,
            original_full_exposure=self.normalization,old_selected_events=3,original_saved_events=5,
            original_result_path=original['path'],original_result_sha256=original['sha256'])],
            original_saved_GEN_events=5,includes_sources_with_no_benchmark_event_drawn=True))
        jobs=[];producers=[]
        for index,chosen in enumerate(selected):
            directory=self.root/('det_'+str(index));directory.mkdir()
            request=self.write(str(directory/'request.json'),dict(detector_source=source,
                selected_skip=chosen['selected_position'],selected_count=1))
            nano_path='/eos/test/nano'+str(index)+'.root';nano_sha=str(index+1)*64
            result=self.write(str(directory/'result.json'),dict(complete=True,exit_code=0,
                request_sha256=request['sha256'],nano_sha256=nano_sha,nano_readback_sha256=nano_sha,
                nano_path=nano_path,nano=dict(event_ids=[chosen['event_id']]),
                source_indices=[chosen['original_index']],sampling_probabilities=[chosen['existing_sampling_probability']],
                source_descriptor_sha256='e'*64))
            jobs.append(dict(job_directory=str(directory),request_sha256=request['sha256'],selected=chosen))
            producers.append(dict(directory=str(directory),request_sha256=request['sha256'],
                result_sha256=result['sha256'],nano_path=nano_path,nano_sha256=nano_sha))
        collector=self.root/'collect';collector.mkdir()
        self.collector_request=dict(schema='shift-stream-payload-v1',stage='sm_response_collect',
            benchmark_plan=plan_pin,full_population_exposure=full,job_records=jobs)
        request=self.write(str(collector/'request.json'),self.collector_request)
        self.collector=dict(schema='shift-sm-prospective-response-collection-v1',complete=True,exit_code=0,
            complete_frozen_draw_collected=True,original_weights_and_p_unchanged=True,
            request_sha256=request['sha256'],benchmark_plan_sha256=plan_pin['sha256'],events=2,
            producer_receipts=producers)
        self.collector_pin=self.write(str(collector/'result.json'),self.collector)
        self.prepared_pin=self.write('prepared.json',dict(schema='shift-sm-candidate-handoff-v1',
            source_id='response',collector=dict(job_directory=str(collector),request_sha256=request['sha256']),
            benchmark_plan=plan_pin))

    def write(self,name,value):
        path=self.root/name
        path.write_text(json.dumps(value,indent=2)+'\n')
        return dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest())

    def discover(self):
        return response_closed_source(self.prepared_pin,self.collector_pin)

    def test_complete_response_discovery_retains_original_p_and_full_trials(self):
        result=self.discover();source=compact_source(result['selected'])
        self.assertEqual(source['sampling_probabilities'],[.25,.5])
        self.assertEqual(post.histogram_weight_branch([source]),'shiftResponseGenWeight')
        rows,full,nanos=post.response_sampling_contract(source)
        self.assertEqual([r['response_sampling_probability'] for r in rows],[.5,1.])
        self.assertEqual([r['native_weight']/r['sampling_probability']/r['response_sampling_probability'] for r in rows],[16.,6.])
        self.assertEqual(full[0]['full_source_trials'],1000)
        self.assertEqual(len(nanos),2)
        self.assertEqual(exposure([result['normalization']],'qcd_20toinf')['full_source_sumw'],1000)

    def test_partial_or_failed_collectors_cannot_enter_discovery(self):
        for change in [dict(complete_frozen_draw_collected=False),dict(complete=False),
                       dict(exit_code=1),dict(events=1),dict(original_weights_and_p_unchanged=False)]:
            self.collector_pin=self.write('collect/result.json',dict(self.collector,**change))
            with self.subTest(change=change),self.assertRaisesRegex(ValueError,'complete, pinned'):
                self.discover()

    def test_changed_plan_and_same_count_changed_original_are_rejected(self):
        source=self.discover()['selected']
        changed=copy.deepcopy(source);changed['event_ids'][0]=[1,1,999]
        with self.assertRaisesRegex(ValueError,'descriptor changed'):
            selected_rows(changed)
        changed=copy.deepcopy(source);changed['gen_sha256']='f'*64
        with self.assertRaisesRegex(ValueError,'full original normalization'):
            selected_rows(changed)
        self.write('plan.json',dict(self.plan,population_events=4))
        with self.assertRaisesRegex(ValueError,'authority changed'):
            selected_rows(source)

    def test_missing_producer_and_failed_or_changed_nano_receipts_are_rejected(self):
        self.collector_pin=self.write('collect/result.json',dict(self.collector,producer_receipts=self.collector['producer_receipts'][:1]))
        with self.assertRaisesRegex(ValueError,'one producer'):
            self.discover()
        self.collector_pin=self.write('collect/result.json',self.collector)
        path=self.root/'det_0/result.json';value=json.loads(path.read_text());value['nano_readback_sha256']='f'*64
        pin=self.write(str(path),value)
        changed=copy.deepcopy(self.collector);changed['producer_receipts'][0]['result_sha256']=pin['sha256']
        self.collector_pin=self.write('collect/result.json',changed)
        with self.assertRaisesRegex(ValueError,'producer is incomplete'):
            self.discover()

    def test_response_cannot_mix_ordinary_or_decay_proposal_weights(self):
        source=self.discover()['selected']
        ordinary=dict(event_ids=[[2,1,1]],weights=[1.],selected_indices=[0],sampling_probabilities=[1.])
        with self.assertRaisesRegex(ValueError,'separate publication'):
            post.histogram_weight_branch([source,ordinary])
        source['decay_proposal']={}
        with self.assertRaisesRegex(ValueError,'separate snapshots'):
            selected_rows(source)

    def test_frozen_q_still_has_to_match_complete_cell_counts(self):
        source=self.discover()['selected'];changed=copy.deepcopy(self.plan)
        changed['selected'][0]['benchmark_inclusion_probability']=.2
        changed['strata'][0]['benchmark_inclusion_probability']=.2
        original=post.read_response_pin
        with patch.object(post,'read_response_pin',side_effect=lambda pin:
                changed if Path(pin['path']).name=='plan.json' else original(pin)):
            with self.assertRaisesRegex(ValueError,'q=n/N'):
                selected_rows(source)

    def install_global_projection(self, arm):
        selected = copy.deepcopy(self.plan['selected'])
        for row in selected:
            row['comparison_arm'] = arm
            if arm == 'random':
                row['benchmark_inclusion_probability'] = 2/3
        global_rows = copy.deepcopy(selected)
        for row in copy.deepcopy(selected):
            row.update(source_id='other')
            row['event_id'][0] = 2
            global_rows.append(row)
        if arm == 'random':
            cells = [dict(stratum='qcd_20toinf',comparison_arm=arm,
                benchmark_region='uniform_complete_population',population_events=6,
                selected_events=4,benchmark_inclusion_probability=2/3)]
        else:
            cells = [dict(r,comparison_arm=arm,population_events=r['population_events']*2,
                          selected_events=2) for r in self.plan['strata']]
            for row in cells:
                row.pop('source_id')
        global_plan = dict(draw_frozen=True,
            comparison_kind='disjoint-source-smart-versus-random-v1',
            source_assignments=dict(response=arm,other=arm),population_events=6,
            selected_events=4,selected=global_rows,strata=cells)
        if arm == 'cohort':
            global_plan.pop('comparison_kind')
            global_plan.pop('source_assignments')
            global_plan['controls_scope'] = 'cohort'
            for row in selected + global_rows + cells:
                row.pop('comparison_arm', None)
        global_pin = self.write('global.json', global_plan)
        local = dict(self.plan,selected=selected,strata=[],
                     source_projection_does_not_redraw=True)
        local['global_response_plan' if arm == 'cohort' else 'global_comparison_plan'] = global_pin
        plan_pin = self.write('plan.json',local)
        request = copy.deepcopy(self.collector_request)
        request['benchmark_plan'] = plan_pin
        for job,row in zip(request['job_records'],selected):
            job['selected'] = row
        request_pin = self.write('collect/request.json',request)
        collector = dict(self.collector,request_sha256=request_pin['sha256'],
                         benchmark_plan_sha256=plan_pin['sha256'])
        self.collector_pin = self.write('collect/result.json',collector)
        self.prepared_pin = self.write('prepared.json',dict(schema='shift-sm-candidate-handoff-v1',
            source_id='response',collector=dict(job_directory=str(self.root/'collect'),
                request_sha256=request_pin['sha256']),benchmark_plan=plan_pin))

    def test_global_smart_cells_keep_global_q_when_published_one_source_at_a_time(self):
        self.install_global_projection('smart')
        source = self.discover()['selected']
        rows,_,_ = post.response_sampling_contract(source)
        self.assertEqual([r['response_population_events'] for r in rows],[4,2])
        self.assertEqual([r['response_selected_events'] for r in rows],[2,2])
        self.assertEqual([r['response_sampling_probability'] for r in rows],[.5,1.])

    def test_cohort_control_counts_are_not_repeated_for_each_published_source(self):
        self.install_global_projection('cohort')
        rows,_,_ = post.response_sampling_contract(self.discover()['selected'])
        self.assertEqual([r['response_population_events'] for r in rows],[4,2])
        self.assertEqual([r['response_selected_events'] for r in rows],[2,2])
        self.assertEqual([r['response_sampling_probability'] for r in rows],[.5,1.])

    def test_random_projection_uses_uniform_cell_even_when_physical_regions_differ(self):
        self.install_global_projection('random')
        rows,_,_ = post.response_sampling_contract(self.discover()['selected'])
        self.assertEqual([r['response_sampling_probability'] for r in rows],[2/3,2/3])
        self.assertEqual([r['response_population_events'] for r in rows],[6,6])
        source = self.discover()['selected']
        path = self.root/'global.json'
        value = json.loads(path.read_text());value['selected'][0]['native_weight'] *= 2
        self.write('global.json',value)
        with self.assertRaisesRegex(ValueError,'authority changed'):
            post.response_sampling_contract(source)

    def test_discovered_response_uses_the_existing_snapshot_request(self):
        source=self.discover()
        config=dict(campaign='response_test',histogram_runtime=dict(path='runtime',sha256='a'*64),
            worker_publication_bound_bytes=1000000,output_base='/eos/synthetic',
            worker_template='initialdir = @DIRECTORY@\nrequest_sha = @REQUEST_SHA@\n')
        task=snapshot(self.root,config,'qcd_20toinf',[source])
        request=json.loads((Path(task['directory'])/'request.json').read_text())
        self.assertEqual(request['schema'],'shift-smart-SM-postprocessing-v1')
        self.assertEqual(request['selected_sources'][0]['response_sampling'],source['selected']['response_sampling'])
        self.assertEqual(len(request['nano_inputs']),2)
        self.assertEqual(request['normalization_sources'][0]['full_source_trials'],1000)
        extra=copy.deepcopy(request);extra['normalization_sources'].append(dict(self.normalization,source_id='unfinished',gen_sha256='f'*64))
        path=self.write('extra_exposure.json',extra)['path']
        with self.assertRaisesRegex(ValueError,'exactly the complete'):
            post.run(path)
        request['nano_inputs']=request['nano_inputs'][:1]
        path=self.write('incomplete_publication.json',request)['path']
        with self.assertRaisesRegex(ValueError,'Nano inputs differ'):
            post.run(path)


    def test_interim_histograms_and_final_nano_share_exact_source_exposure(self):
        import run_shift_sm_postprocessing_controller as controller
        source=self.discover()
        monitor=self.write('publication_monitor.json',dict(plan=self.write('publication_plan.json',
            dict(sources=[dict(source_id=sid,stratum='qcd_20toinf') for sid in ('response','next')]))))
        config=dict(campaign='response_test',histogram_runtime=dict(path='runtime',sha256='a'*64),
            worker_publication_bound_bytes=1000000,output_base='/eos/synthetic',worker_template='',
            merged_nano_only_when_bin_complete=True,response_monitor_config=monitor,
            worker_publication_bounds_by_stratum=dict(qcd_20toinf=2000000),
            interim_histogram_publication_bound_bytes=100000)
        task=controller.snapshot(self.root,config,'qcd_20toinf',[source])
        request=json.loads((Path(task['directory'])/'request.json').read_text())
        self.assertFalse(task['publishes_merged_nano'])
        self.assertEqual(task['publication_bound_bytes'],100000)
        self.assertEqual(request['maximum_publication_bytes'],100000)
        self.assertFalse(post.merged_nano_publication(request))
        self.assertEqual(request['normalization_sources'],[self.normalization])
        self.assertEqual(request['expected_bin_source_ids'],['next','response'])
        with patch.object(controller,'complete_bin_source_ids',return_value=['response']):
            final=controller.snapshot(self.root,config,'qcd_20toinf',[source])
        final_request=json.loads((Path(final['directory'])/'request.json').read_text())
        self.assertTrue(final['publishes_merged_nano']);self.assertTrue(post.merged_nano_publication(final_request))
        self.assertEqual(final['publication_bound_bytes'],2000000)
        self.assertNotEqual(task['request_sha256'],final['request_sha256'])
        self.assertEqual(final_request['normalization_sources'],request['normalization_sources'])
        self.assertEqual(final_request['nano_inputs'],request['nano_inputs'])
        changed=dict(request,publish_merged_nano=True)
        with self.assertRaisesRegex(ValueError,'entire frozen bin'):
            post.merged_nano_publication(changed)
        changed=dict(final_request,publish_merged_nano=False)
        with self.assertRaisesRegex(ValueError,'entire frozen bin'):
            post.merged_nano_publication(changed)

    def test_explicit_histogram_only_snapshot_keeps_all_original_inputs(self):
        source = self.discover()
        config = dict(campaign='histogram_only', histogram_runtime=dict(path='runtime', sha256='a'*64),
                      worker_publication_bound_bytes=10000000, output_base='/eos/synthetic',
                      worker_template='', publish_merged_nano=False)
        task = snapshot(self.root, config, 'qcd_20toinf', [source])
        request = json.loads((Path(task['directory'])/'request.json').read_text())
        self.assertFalse(post.merged_nano_publication(request))
        self.assertEqual(request['nano_inputs'], source['nano_inputs'])
        self.assertEqual(request['normalization_sources'], [self.normalization])

    def test_cumulative_bound_changes_only_reservation_and_preserves_source_group_bound(self):
        source = self.discover()
        source['histogram_group'] = dict(source_ids=['response'], events=2,
            histogram=dict(path='/eos/synthetic/group.root', sha256='c'*64))
        config = dict(campaign='grouped', histogram_runtime=dict(path='runtime', sha256='a'*64),
            worker_publication_bound_bytes=67108864, output_base='/eos/synthetic',
            worker_template='', grouped_response_publication=True)
        first = snapshot(self.root, config, 'qcd_20toinf', [source])
        request = post.read(Path(first['directory'])/'request.json')
        self.assertEqual(first['publication_bound_bytes'], 67108864)
        self.assertFalse(request['publish_merged_nano'])
        larger = snapshot(self.root, dict(config, cumulative_histogram_publication_bound_bytes=536870912),
                          'qcd_20toinf', [source])
        changed = post.read(Path(larger['directory'])/'request.json')
        self.assertEqual(larger['publication_bound_bytes'], 536870912)
        for key in ('normalization_sources', 'selected_sources', 'nano_inputs', 'histogram_groups',
                    'zero_selected_source_ids', 'histogram_runtime', 'publish_merged_nano'):
            self.assertEqual(changed[key], request[key], key)
        group = snapshot(self.root, dict(config, source_group_only=True,
            cumulative_histogram_publication_bound_bytes=536870912), 'qcd_20toinf', [source])
        self.assertEqual(group['publication_bound_bytes'], 67108864)
        self.assertTrue(post.read(Path(group['directory'])/'request.json')['source_group_only'])

    def test_cumulative_bound_rejects_boolean_noninteger_and_nonpositive_values(self):
        source = self.discover()
        source['histogram_group'] = dict(source_ids=['response'])
        config = dict(campaign='grouped', histogram_runtime=dict(path='runtime', sha256='a'*64),
            worker_publication_bound_bytes=67108864, output_base='/eos/synthetic',
            worker_template='', grouped_response_publication=True)
        for bound in (True, False, 0, -1, 1.5, '536870912'):
            with self.subTest(bound=bound), self.assertRaisesRegex(ValueError, 'positive integer'):
                snapshot(self.root, dict(config, cumulative_histogram_publication_bound_bytes=bound),
                         'qcd_20toinf', [source])


class ResponseDiscoveryControllerTest(unittest.TestCase):
    def setUp(self):
        # Native proof deliberately rejects frontend-local /tmp authorities.
        task_area=Path(__file__).resolve().parents[1]/'tmp'
        task_area.mkdir(exist_ok=True)
        self.temporary=tempfile.TemporaryDirectory(dir=task_area)
        self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)
        self.output=self.root/'reconstruction'/'source'
        (self.output/'jobs/collect').mkdir(parents=True)
        self.prepared=dict(benchmark_plan=dict(sha256='b'*64),detector_jobs=[{}])
        self.collector=dict(complete=True,exit_code=0,benchmark_plan_sha256='b'*64,
                            complete_frozen_draw_collected=True,events=1)
        for name,value in [('prepared.json',self.prepared),('submission.json',{}),
            ('jobs/collect/result.json',self.collector),('jobs/collect/request.json',{}),
            ('jobs/collect/status.json',dict(complete=True,exit_code=0))]:
            self.write(self.output/name,value)
        (self.output/'jobs/collect/events.log').write_text('native terminal proof')
        plan=self.write(self.root/'plan.json',dict(sources=[dict(source_id='source', stratum='qcd_20toinf')]))
        monitor=dict(schema='shift-sm-candidate-search-watchdog-v1',root_directory=str(self.root),
            plan=plan,individual_source_watchdogs=False)
        self.config=dict(response_monitor_config=self.write(self.root/'monitor.json',monitor))
        self.source=dict(normalization=dict(source_id='source',stratum='qcd_20toinf'))
        self.state={}

    def write(self,path,value):
        path.write_text(json.dumps(value)+'\n')
        return dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest())

    def discover(self,ads=(),closure=None):
        import run_shift_sm_postprocessing_controller as controller
        import monitor_shift_sm_candidate_search as monitor
        proof=dict(owner=dict(cluster_id=5),collector_terminal=dict(successful=True))
        with patch.object(monitor,'native_collector_closure',return_value=closure or proof) as native, \
             patch.object(monitor,'source_owner',return_value=dict(cluster_id=5)), \
             patch.object(controller,'response_closed_source',return_value=self.source) as adapt:
            groups=controller.response_source_cache(self.config,self.state,ads)
        return groups,native,adapt

    def test_native_qualified_source_is_cached_and_never_redrawn(self):
        groups,native,adapt=self.discover()
        self.assertEqual(groups['qcd_20toinf'],[self.source])
        self.assertEqual(native.call_count,1);self.assertEqual(adapt.call_count,1)
        groups,native,adapt=self.discover()
        self.assertEqual(groups['qcd_20toinf'],[self.source])
        self.assertEqual(native.call_count,0);self.assertEqual(adapt.call_count,0)

    def test_fresh_active_or_held_replay_blocks_cached_exposure(self):
        self.discover()
        for status in (1,2,3,5,6,7):
            with self.assertRaisesRegex(ValueError,'active replay'):
                self.discover([dict(Iwd=str(self.output/'jobs/det_0'),
                    JobUniverse=5,JobStatus=status)])
        self.state={}
        groups,_,_=self.discover([dict(Iwd=str(self.output/'jobs/det_0'),JobUniverse=5,JobStatus=2)])
        self.assertEqual(dict(groups),{})

    def test_changed_cached_result_or_native_status_is_rejected(self):
        self.discover()
        self.write(self.output/'jobs/collect/status.json',dict(complete=True,exit_code=1))
        with self.assertRaisesRegex(ValueError,'Cached response native closure changed'):
            self.discover()

    def test_changed_monitor_or_missing_worklist_member_is_rejected(self):
        self.discover()
        self.write(self.root/'monitor.json',{})
        with self.assertRaisesRegex(ValueError,'authority changed'):
            self.discover()

    def test_json_completion_without_native_closure_has_no_exposure(self):
        import run_shift_sm_postprocessing_controller as controller
        import monitor_shift_sm_candidate_search as monitor
        with patch.object(monitor,'native_collector_closure',return_value=None), \
                patch.object(monitor,'source_owner',return_value=dict(cluster_id=5)):
            self.assertEqual(dict(controller.response_source_cache(self.config,self.state,[])),{})
        self.assertEqual(self.state['response_sources'],{})

    def test_changed_owner_and_failed_native_proof_stop_discovery(self):
        import run_shift_sm_postprocessing_controller as controller
        import monitor_shift_sm_candidate_search as monitor
        with patch.object(monitor,'native_collector_closure',side_effect=ValueError('wrong native DAG owner')), \
                patch.object(monitor,'source_owner',return_value=dict(cluster_id=5)):
            with self.assertRaisesRegex(ValueError,'wrong native DAG owner'):
                controller.response_source_cache(self.config,self.state,[])
        self.discover()
        with patch.object(monitor,'source_owner',return_value=dict(cluster_id=99)):
            with self.assertRaisesRegex(ValueError,'native owner changed'):
                controller.response_source_cache(self.config,self.state,[])

    def second_monitor(self, source_id='later'):
        root = self.root / 'later_cohort'
        output = root / 'reconstruction' / source_id
        (output / 'jobs/collect').mkdir(parents=True)
        for name in ('prepared.json', 'submission.json', 'jobs/collect/result.json',
                     'jobs/collect/request.json', 'jobs/collect/status.json', 'jobs/collect/events.log'):
            (output / name).write_bytes((self.output / name).read_bytes())
        plan = self.write(root / 'plan.json', dict(sources=[dict(source_id=source_id, stratum='qcd_20toinf')]))
        return self.write(root / 'monitor.json', dict(
            schema='shift-sm-candidate-search-watchdog-v1', root_directory=str(root),
            plan=plan, individual_source_watchdogs=False))

    def test_independent_monitor_union_preserves_cached_and_later_exposure(self):
        import run_shift_sm_postprocessing_controller as controller
        import monitor_shift_sm_candidate_search as monitor
        self.discover()
        first = self.config['response_monitor_config']
        second = self.second_monitor()
        self.config = dict(response_monitor_configs=[first, second])
        later = dict(normalization=dict(source_id='later', stratum='qcd_20toinf'))
        proof = dict(owner=dict(cluster_id=5), collector_terminal=dict(successful=True))
        with patch.object(monitor, 'native_collector_closure', return_value=proof) as native, \
             patch.object(monitor, 'source_owner', return_value=dict(cluster_id=5)), \
             patch.object(controller, 'response_closed_source', return_value=later):
            groups = controller.response_source_cache(self.config, self.state, [])
            self.assertEqual(groups['qcd_20toinf'], [self.source, later])
            self.assertEqual(native.call_count, 1)
            self.assertEqual(set(self.state['response_sources']), {'source', 'later'})
            self.assertEqual(controller.complete_bin_source_ids(self.config, 'qcd_20toinf'), ['later', 'source'])
        self.config = dict(response_monitor_configs=[second])
        with self.assertRaisesRegex(ValueError, 'disappeared'):
            controller.response_source_cache(self.config, self.state, [])

    def test_overlapping_or_changed_monitor_union_cannot_add_exposure(self):
        import run_shift_sm_postprocessing_controller as controller
        first = self.config['response_monitor_config']
        second = self.second_monitor(source_id='source')
        with self.assertRaisesRegex(ValueError, 'repeat an original source'):
            controller.response_source_cache(dict(response_monitor_configs=[first, second]), {}, [])
        with self.assertRaisesRegex(ValueError, 'not both'):
            controller.response_monitor_pins(dict(response_monitor_config=first, response_monitor_configs=[second]))
        with self.assertRaisesRegex(ValueError, 'Repeated response monitor'):
            controller.response_monitor_pins(dict(response_monitor_configs=[first, first]))
        self.write(Path(second['path']), {})
        with self.assertRaisesRegex(ValueError, 'authority changed'):
            controller.response_source_cache(dict(response_monitor_configs=[first, second]), {}, [])


class PublisherDeploymentTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.helpers = self.root / 'helpers'
        self.helpers.mkdir()
        self.config = dict(frozen_files={}, refresh_quota=True, response_monitor_config={})
        for name in ('run_shift_sm_postprocessing', 'shift_condor_native',
                     'shift_event_weights', 'shift_nano_integrity',
                     'run_shift_smart_sm_controller'):
            path = self.helpers / (name + '.py')
            path.write_text('# frozen helper\n')
            self.config['frozen_files'][str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.worker = self.root / 'worker.sh'
        self.worker.write_text('#!/bin/sh\n')
        self.archive = self.root / 'frozen_tools.tar.gz'
        self.archive.write_bytes(b'frozen archive')
        checksum = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        self.config['worker_template'] = (
            f'executable = {self.worker}\narguments = @REQUEST_SHA@ {checksum} publication\n'
            f'transfer_input_files = @DIRECTORY@/request.json,{self.archive}\n')

    def validate(self):
        from run_shift_sm_postprocessing_controller import validate_deployment_inputs
        validate_deployment_inputs(self.root, self.config, self.helpers)

    def test_complete_closure_and_literal_worker_paths_pass(self):
        self.validate()

    def test_omitted_delayed_import_fails_before_any_source_arrives(self):
        path = self.helpers / 'shift_event_weights.py'
        del self.config['frozen_files'][str(path)]
        path.unlink()
        with self.assertRaisesRegex(ValueError, 'Missing pinned publication helper'):
            self.validate()

    def test_date_replacement_in_worker_path_is_rejected(self):
        self.config['worker_template'] = self.config['worker_template'].replace(
            str(self.worker), str(self.root / 'wrong_campaign' / 'worker.sh'))
        with self.assertRaisesRegex(ValueError, 'worker executable is missing'):
            self.validate()

    def test_changed_archive_cannot_reuse_the_qualified_hash(self):
        self.archive.write_bytes(b'another archive')
        with self.assertRaisesRegex(ValueError, 'archive differs from its argument hash'):
            self.validate()

    def test_compact_source_requires_the_pinned_lazy_qcd_adapter(self):
        self.config['compact_source_authority']=True
        with self.assertRaisesRegex(ValueError,'run_shift_qcd_production_source'):
            self.validate()
        path=self.helpers/'run_shift_qcd_production_source.py'
        path.write_text('# frozen compact authority reader\n')
        self.config['frozen_files'][str(path)]=hashlib.sha256(path.read_bytes()).hexdigest()
        self.validate()
        del self.config['frozen_files'][str(path)]
        with self.assertRaisesRegex(ValueError,'Missing pinned publication helper'):
            self.validate()


class OrdinaryPublisherPollTest(unittest.TestCase):
    def test_reviewed_failure_unblocks_only_snapshot_and_retains_all_128MiB_claims(self):
        import run_shift_sm_postprocessing_controller as controller
        import time
        for flags in ((True, True), (True, False), (None, None), (1, 1), ('yes', 'yes')):
            with self.subTest(flags=flags), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                parent = root/'parent'
                quota = parent/'quota'
                quota.mkdir(parents=True)
                post.save(quota/'result.json', dict(complete=True, checked_epoch=time.time(), free_bytes=10**12))
                post.save(parent/'ledger.json', dict(tasks=dict(quota=dict(kind='quota', phase='done',
                    directory=str(quota), result_sha256=post.digest(quota/'result.json'),
                    native_exit_code=0, finished_epoch=time.time(), published_bytes=0))))
                policy = root/'policy.json'
                post.save(policy, dict(own_reservation_bytes=10**11,
                    external_future_reservation_bytes=0, safety_floor_bytes=0, account_worker_ceiling=2000))
                bound = 67108864
                config = dict(parent_state=str(parent), parent_config=str(policy),
                    maximum_publication_bytes=2*bound, worker_publication_bound_bytes=bound)
                failed = {str(index): dict(kind='aggregate', phase='failed', stratum='qcd_1to2', events=1,
                    publication_bound_bytes=bound, audit_only_retired_failure=flag)
                    for index, flag in enumerate(flags)}
                original = copy.deepcopy(failed)
                state = dict(tasks=failed, latest=dict(qcd_1to2=[]), sources={}, nanos={})
                proposal = dict(directory=str(root/'next'), kind='aggregate', phase='ready',
                    stratum='qcd_1to2', source_ids=['new'], events=1, publication_bound_bytes=bound)
                with patch.object(controller, 'query', return_value=[]), \
                     patch.object(controller, 'publication_history', return_value={}), \
                     patch.object(controller, 'source_cache'), patch.object(controller, 'nano_cache'), \
                     patch.object(controller, 'closed_sources', return_value={'qcd_1to2': [
                         dict(normalization=dict(source_id='new'))]}), \
                     patch.object(controller, 'snapshot', return_value=proposal) as make, \
                     patch.object(controller, 'run_condor') as submit:
                    controller.poll(root, config, state)
                self.assertEqual(make.call_count, int(all(flag is True for flag in flags)))
                submit.assert_not_called()
                self.assertEqual({key: state['tasks'][key] for key in original}, original)
                self.assertEqual(post.read(root/'heartbeat.json')['publication_budget_remaining_bytes'], 0)

    def test_large_publisher_two_bounded_batches_only_checkpoint_four_times(self):
        import run_shift_sm_postprocessing_controller as controller
        import time
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);parent=root/'parent';parent.mkdir();quota=parent/'quota';quota.mkdir()
            post.save(quota/'result.json',dict(complete=True,checked_epoch=time.time(),free_bytes=10**12))
            post.save(parent/'ledger.json',dict(tasks=dict(quota=dict(kind='quota',phase='done',
                directory=str(quota),result_sha256=post.digest(quota/'result.json'),native_exit_code=0,
                finished_epoch=time.time(),published_bytes=0))))
            policy=root/'policy.json';post.save(policy,dict(own_reservation_bytes=10**11,
                external_future_reservation_bytes=0,safety_floor_bytes=0,account_worker_ceiling=2000))
            config=dict(campaign='large-publication',parent_state=str(parent),parent_config=str(policy),
                submission_journal=True,maximum_starts_per_poll=64,maximum_group_workers=10000,
                maximum_publication_bytes=10**10,worker_publication_bound_bytes=100,maximum_idle_workers=1800)
            state=dict(tasks={},latest=dict(qcd_1to2=['old']),nanos={},sources={})
            for index in range(16000):
                state['sources'][str(index)]=dict(normalization=dict(source_id=str(index),
                    full_source_trials=1000,gen_sha256='a'*64),selected=dict(event_ids=[[1,index,1]],
                    weights=[1.],sampling_probabilities=[.1],selected_indices=[0]))
            for index in range(4000):
                state['tasks']['closed'+str(index)]=dict(kind='source_group',phase='done',events=1,
                    stratum='qcd_1to2',source_ids=[str(index)],published_bytes=100)
            for index in range(100):
                directory=root/str(index);directory.mkdir()
                post.save(directory/'request.json',dict(original_id=[1,index,1],weight=1.,p=.1))
                (directory/'worker.sub').write_text('queue 1\n')
                state['tasks'][str(index)]=dict(kind='source_group',phase='ready',events=1,
                    stratum='qcd_1to2',directory=str(directory),source_ids=[str(index)],
                    request_sha256=post.digest(directory/'request.json'),publication_bound_bytes=100)
            original_save=main_controller.save;checkpoints=[];clusters=iter(range(1000,1100))
            def save(path,value):
                if Path(path)==root/'state.json':checkpoints.append(1)
                original_save(path,value)
            def native(*args,**kwargs):
                cluster=next(clusters);return f'{cluster}.0 - {cluster}.0\n'
            peers=[dict(ClusterId=30000+i,JobStatus=2 if i<1344 else 1,JobUniverse=5)
                   for i in range(1344+855)]
            with patch.object(controller,'query',return_value=peers) as query, \
                    patch.object(controller,'publication_history',return_value={}), \
                    patch.object(controller,'source_cache'),patch.object(controller,'nano_cache'), \
                    patch.object(controller,'closed_sources',return_value={}), \
                    patch.object(main_controller,'save',side_effect=save), \
                    patch.object(main_controller,'run_condor',side_effect=native) as submit:
                controller.poll(root,config,state)
                self.assertEqual(submit.call_count,64)
                query.return_value=peers+[dict(ClusterId=t['cluster'],JobStatus=1,JobUniverse=5)
                    for t in state['tasks'].values() if t['phase']=='submitted']
                controller.poll(root,config,state)
                self.assertEqual(submit.call_count,100)
            self.assertEqual(len(checkpoints),4)
            restored=post.read(root/'state.json')
            self.assertEqual(sum(t['phase']=='submitted' for t in restored['tasks'].values()),100)
            self.assertEqual(restored['sources'],state['sources'])
            self.assertFalse((root/'ledger.json').exists())
            heartbeat=post.read(root/'heartbeat.json')
            self.assertEqual(heartbeat['account_running_workers'],1344)
            self.assertEqual(heartbeat['account_idle_workers'],855+64)
            directory=root/'blocked';directory.mkdir();post.save(directory/'request.json',dict(event=[1,2,3]))
            (directory/'worker.sub').write_text('queue 1\n')
            state['tasks']['blocked']=dict(kind='source_group',phase='ready',events=1,stratum='qcd_1to2',
                directory=str(directory),source_ids=['blocked'],publication_bound_bytes=100,
                request_sha256=post.digest(directory/'request.json'))
            own=[dict(ClusterId=t['cluster'],JobStatus=1,JobUniverse=5)
                 for t in state['tasks'].values() if t['phase']=='submitted']
            for limit,ads,reason in [(955,peers+own,'idle_worker_limit'),
                    (1800,[dict(ClusterId=40000+i,JobStatus=2,JobUniverse=5) for i in range(2000)]+own,
                     'running_worker_limit')]:
                config['maximum_idle_workers']=limit
                with patch.object(controller,'query',return_value=ads), \
                        patch.object(controller,'publication_history',return_value={}), \
                        patch.object(controller,'source_cache'),patch.object(controller,'nano_cache'), \
                        patch.object(controller,'closed_sources',return_value={}), \
                        patch.object(main_controller,'run_condor') as submit:
                    controller.poll(root,config,state)
                submit.assert_not_called()
                self.assertEqual(post.read(root/'heartbeat.json')['admission_wait_reason'],reason)
                self.assertEqual(state['tasks']['blocked']['phase'],'ready')

    def test_compact_receipt_keeps_all_parsed_histogram_and_normalization_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            result=dict(events=2,sumw=5.,sumw2=13.,source_ids=['first','second'],
                full_original_exposure=dict(full_source_sumw=1000,original_sources=[dict(source_id='first')]),
                histograms=dict(mass=dict(contents=[0.,2.,3.],errors=[0.,2.,3.]),
                    logMass=dict(contents=[0.,2.,3.],errors=[0.,2.,3.])))
            post.save(root/'pretty.json',result)
            post.save(root/'compact.json',result,compact=True)
            self.assertEqual(post.read(root/'pretty.json'),post.read(root/'compact.json'))
            self.assertLess((root/'compact.json').stat().st_size,(root/'pretty.json').stat().st_size)
            with self.assertRaisesRegex(ValueError,'boolean'):
                post.save(root/'invalid.json',result,compact=1)

    def check_real_ordinary_path(self, inventory_mode, hourly=False, final=False, hourly_limit=None,
                                 previous_hourlies=(), growth=False, expected_ready=1):
        import run_shift_sm_postprocessing_controller as controller
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);parent=root/'parent';parent.mkdir()
            def write(path,value):
                path.write_text(json.dumps(value,indent=2)+'\n')
                return hashlib.sha256(path.read_bytes()).hexdigest()
            source=dict(event_ids=[[11,1,1],[11,1,2]],weights=[2.,5.],
                        selected_indices=[0],sampling_probabilities=[.25],gen_sha256='c'*64)
            normalization=dict(source_id='positive',stratum='jpsi_5to10',gen_sha256='a'*64,
                denominator_kind='fixed_source_trials',full_source_trials=100,sigma_nd_pb=123.)
            zero=dict(normalization,source_id='zero',gen_sha256='d'*64,full_source_trials=200,saved_events=0)
            tasks={}
            for name,result in [('positive',dict(source=normalization,selected_source=source)),
                                ('zero',dict(source=zero,empty_source=True,selected_events=0))]:
                p=parent/name;p.mkdir();checksum=write(p/'result.json',result)
                tasks[name]=dict(kind='source',phase='done',directory=str(p),result_sha256=checksum,stratum='jpsi_5to10')
            nano=dict(path='/eos/test/nano_job0000001.root',sha256='b'*64,events=1,
                      stratum='jpsi_5to10',source_descriptor_sha256=controller.descriptor_hashes(source)[0])
            p=parent/'detector';p.mkdir();checksum=write(p/'result.json',dict(nano_path=nano['path'],
                nano_sha256=nano['sha256'],events=1,source_descriptor_sha256=nano['source_descriptor_sha256']))
            write(p/'status.json',dict(complete=True,exit_code=0,nano_path=nano['path'],
                nano_sha256=nano['sha256'],events=1,source_descriptor_sha256=nano['source_descriptor_sha256']))
            tasks['detector_0000001']=dict(kind='detector',phase='done',directory=str(p),
                result_sha256=checksum,stratum='jpsi_5to10')
            if hourly or final:
                tasks['pending_source']=dict(kind='source',phase='submitted',stratum='qcd_2to5' if final else 'jpsi_5to10')
            write(parent/'ledger.json',dict(tasks=tasks))
            if inventory_mode=='present':write(parent/'source_inventory.json',dict(nano_outputs=[nano]))
            if inventory_mode=='malformed':(parent/'source_inventory.json').write_text('malformed existing inventory')
            config=dict(campaign='ordinary',parent_state=str(parent),worker_publication_bound_bytes=1000000,
                histogram_runtime=dict(path='/pinned/histogram.tar.gz'),output_base='/eos/test',
                worker_template='initialdir = @DIRECTORY@\narguments = @REQUEST_SHA@\nqueue 1\n')
            state=dict(tasks={},latest={},sources={},nanos={})
            if hourly or final:
                import time
                config.update(minimum_new_sources=250,minimum_relative_source_growth=2.)
                state['latest']['jpsi_5to10']=['positive']
                state['tasks'].update(old_cumulative=dict(kind='aggregate',phase='done',stratum='jpsi_5to10',
                    finished_epoch=time.time() if final else time.time()-4000,published_bytes=0,events=1),recent_group=dict(kind='source_group',
                    phase='done',stratum='jpsi_5to10',finished_epoch=time.time(),published_bytes=0,events=0))
            if hourly_limit is not None:
                config['maximum_hourly_updates_per_stratum']=hourly_limit
            for index,phase in enumerate(previous_hourlies):
                state['tasks']['old_hourly'+str(index)]=dict(kind='aggregate',phase=phase,
                    publication_trigger='hourly',stratum='jpsi_5to10',finished_epoch=0,
                    events=1,published_bytes=0)
            if growth:
                config['minimum_new_sources']=1
            # Exercise the persisted count, as on a real controller restart.
            state=json.loads(json.dumps(state))
            with patch.object(controller,'query',return_value=[]),patch.object(controller,'run_condor') as submit:
                if inventory_mode=='malformed':
                    with self.assertRaises(ValueError):controller.poll(root,config,state)
                    submit.assert_not_called()
                    return
                controller.poll(root,config,state)
            submit.assert_not_called()
            self.assertEqual(len(state['sources']),2);self.assertEqual(len(state['nanos']),1)
            ready=[t for t in state['tasks'].values() if t['phase']=='ready' and 'directory' in t]
            self.assertEqual(len(ready),expected_ready)
            if not expected_ready:
                return
            task=ready[0];request=json.loads((Path(task['directory'])/'request.json').read_text())
            self.assertEqual(task['phase'],'ready');self.assertEqual(task['events'],1)
            self.assertEqual({r['source_id'] for r in request['normalization_sources']},{'positive','zero'})
            self.assertEqual(sum(r['full_source_trials'] for r in request['normalization_sources']),300)
            self.assertEqual(request['selected_sources'][0]['sampling_probabilities'],[.25])
            if final:
                self.assertTrue(request['parent_completion_status']['all_attempts_settled'])
                self.assertTrue(request['parent_completion_status']['all_responses_complete'])
            self.assertEqual(json.loads((root/'heartbeat.json').read_text())['phase'],'waiting_for_quota')

    def test_real_ordinary_parent_cache_snapshot_and_zero_exposure_before_quota(self):
        self.check_real_ordinary_path('present')

    def test_missing_inventory_uses_completed_native_status_fallback(self):
        self.check_real_ordinary_path('missing')

    def test_existing_malformed_inventory_is_not_treated_as_empty(self):
        self.check_real_ordinary_path('malformed')

    def test_steady_source_groups_do_not_postpone_hourly_cumulative_refresh(self):
        self.check_real_ordinary_path('present',hourly=True)

    def test_hourly_limit_survives_restart_and_counts_failed_and_pending_snapshots(self):
        self.check_real_ordinary_path('present',hourly=True,hourly_limit=2,
            previous_hourlies=('done',),expected_ready=1)
        for phases in [('done','done'),('done','failed'),('done','ready')]:
            with self.subTest(phases=phases):
                self.check_real_ordinary_path('present',hourly=True,hourly_limit=2,
                    previous_hourlies=phases,expected_ready=0)

    def test_exhausted_hourly_limit_keeps_growth_and_final_flushes(self):
        self.check_real_ordinary_path('present',hourly=True,hourly_limit=0,growth=True)
        self.check_real_ordinary_path('present',final=True,hourly_limit=0)

    def test_final_settled_increment_flushes_without_waiting_for_another_bin(self):
        self.check_real_ordinary_path('present',final=True)

    def test_startup_without_inventory_or_completed_jobs_waits_without_exposure(self):
        import run_shift_sm_postprocessing_controller as controller
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);parent=root/'parent';parent.mkdir()
            (parent/'ledger.json').write_text(json.dumps(dict(tasks={})))
            state=dict(tasks={},latest={},sources={},nanos={})
            config=dict(parent_state=str(parent),worker_publication_bound_bytes=100)
            with patch.object(controller,'query',return_value=[]),patch.object(controller,'run_condor') as submit:
                controller.poll(root,config,state)
            submit.assert_not_called();self.assertEqual(state,dict(tasks={},latest={},sources={},nanos={}))
            self.assertEqual(json.loads((root/'heartbeat.json').read_text())['phase'],'waiting_for_quota')
            self.assertFalse((parent/'source_inventory.json').exists())


class CumulativeBaselineSnapshotTest(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name);self.stratum='qcd_1to2'
        def normalization(sid,checksum,trials):
            return dict(source_id=sid,stratum=self.stratum,gen_sha256=checksum*64,
                denominator_kind='fixed_source_trials',full_source_trials=trials,sigma_nd_pb=123.,
                source_family='qcd_ordinary',generator_profile='ordinary',generation_sampling='weighted_trials')
        self.old=[normalization('old','a',100),normalization('oldzero','c',200)]
        empty=dict(event_ids=[],weights=[],selected_indices=[],sampling_probabilities=[])
        self.old_selected=[dict(event_ids=[[1,1,1]],weights=[2.],selected_indices=[0],sampling_probabilities=[.5]),empty]
        histogram=dict(path='/eos/baseline/hist.root',sha256='f'*64,bytes=100)
        group=dict(source_ids=['old','oldzero'],events=1,sumw=4.,sumw2=16.,histogram=histogram)
        receipt=dict(complete=True,exit_code=0,source_ids=group['source_ids'],events=1,sumw=4.,sumw2=16.,
            published=dict(merged_histogram=histogram),full_original_exposure=exposure(self.old,self.stratum))
        p=self.root/'old_result.json';p.write_text(json.dumps(receipt));group['result_pin']=dict(path=str(p),sha256=post.digest(p))
        self.baseline=dict(schema='shift-native-cumulative-baseline-v1',stratum=self.stratum,
            baseline_source_ids=['old','oldzero'],normalization_sources=self.old,selected_sources=self.old_selected,
            nano_inputs=[dict(path='/eos/baseline/nano.root',sha256='e'*64,events=1)],histogram_group=group,
            events=1,sumw=4.,sumw2=16.,full_original_GEN=300,cross_section_pb=123.,selected_event_ids=[[1,1,1]])
        selected=dict(event_ids=[[2,1,1]],weights=[3.],selected_indices=[0],sampling_probabilities=[.25])
        self.fresh=dict(normalization=normalization('fresh','b',400),selected=selected,
            nano_inputs=[dict(path='/eos/new/nano.root',sha256='d'*64,events=1)],
            histogram_group=dict(source_ids=['fresh'],events=1,sumw=12.,sumw2=144.,
                histogram=dict(path='/eos/new/hist.root',sha256='9'*64,bytes=100)))
        self.zero=dict(normalization=normalization('freshzero','d',500),selected=copy.deepcopy(empty),nano_inputs=[])
        self.config=dict(campaign='cumulative',histogram_runtime=dict(path='/frozen/runtime'),
            output_base='/eos/test',worker_template='initialdir = @DIRECTORY@\narguments = @REQUEST_SHA@\n',
            worker_publication_bound_bytes=1000000,grouped_response_publication=True)
        self.pin_baseline()

    def pin_baseline(self):
        p=self.root/'baseline.json';p.write_text(json.dumps(self.baseline))
        self.config['baseline_publications_by_stratum']={self.stratum:dict(path=str(p),sha256=post.digest(p))}

    def test_baseline_augments_once_keeps_new_discovery_ids_and_full_zero_exposure(self):
        status=dict(all_attempts_settled=True,all_responses_complete=False,
                    excluded_incomplete_tasks=[dict(task_name='quarantined_source')])
        task=snapshot(self.root,self.config,self.stratum,[self.fresh,self.zero],publication_status=status)
        request=json.loads((Path(task['directory'])/'request.json').read_text())
        self.assertEqual(task['source_ids'],['fresh','freshzero'])
        self.assertEqual(set(task['published_source_ids']),{'fresh','freshzero','old','oldzero'})
        self.assertEqual(task['baseline_source_ids'],['old','oldzero']);self.assertEqual(task['events'],2)
        self.assertEqual(request['parent_completion_status'],status)
        self.assertEqual(task['parent_completion_status'],status)
        self.assertEqual(len(request['histogram_groups']),2)
        self.assertEqual(request['zero_selected_source_ids'],['freshzero'])
        self.assertEqual(exposure(request['normalization_sources'],self.stratum)['full_source_sumw'],1200)
        rows=[row for source in request['selected_sources'] for row in selected_rows(source)]
        self.assertEqual(sorted(row['native_weight']/row['sampling_probability'] for row in rows),[4.,12.])

    def test_zero_only_new_increment_still_publishes_the_ready_baseline(self):
        task=snapshot(self.root,self.config,self.stratum,[self.zero])
        request=json.loads((Path(task['directory'])/'request.json').read_text())
        self.assertEqual(task['events'],1);self.assertEqual(task['source_ids'],['freshzero'])
        self.assertEqual(request['zero_selected_source_ids'],['freshzero'])
        self.assertEqual(request['histogram_groups'][0]['source_ids'],['old','oldzero'])
        self.assertEqual(exposure(request['normalization_sources'],self.stratum)['full_source_sumw'],800)

    def test_source_group_workers_do_not_include_the_cumulative_baseline(self):
        task=snapshot(self.root,dict(self.config,source_group_only=True),self.stratum,[self.fresh])
        request=json.loads((Path(task['directory'])/'request.json').read_text())
        self.assertEqual(task['events'],1);self.assertEqual(task['source_ids'],['fresh'])
        self.assertNotIn('baseline_source_ids',task);self.assertNotIn('histogram_groups',request)

    def test_duplicate_ids_rles_and_missing_baseline_exposure_are_rejected(self):
        cases=['source_id','event_id','full_GEN','group_coverage','source_class','pin']
        original=copy.deepcopy(self.baseline)
        for case in cases:
            with self.subTest(case=case):
                self.baseline=copy.deepcopy(original);fresh=copy.deepcopy(self.fresh)
                if case=='source_id':fresh['normalization']['source_id']='old'
                if case=='event_id':fresh['selected']['event_ids']=[[1,1,1]]
                if case=='full_GEN':self.baseline['full_original_GEN']=299
                if case=='group_coverage':self.baseline['histogram_group']['source_ids']=['old']
                if case=='source_class':fresh['normalization']['generation_sampling']='native_class'
                self.pin_baseline()
                if case=='pin':(self.root/'baseline.json').write_text('{}')
                with self.assertRaises(ValueError):snapshot(self.root,self.config,self.stratum,[fresh])


class PublisherQuotaTest(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)
        self.parent_config=self.root/'quota_config.json'
        self.parent_config.write_text(json.dumps(dict(worker_template='queue 1\n')))
        self.config=dict(campaign='night',parent_state=str(self.root/'quota'),parent_config=str(self.parent_config))

    def terminal_fixture(self, include_independent_worker=False):
        import time
        parent = self.root/'terminal_parent'
        quota = parent/'quota'
        quota.mkdir(parents=True)
        post.save(quota/'result.json', dict(complete=True, checked_epoch=time.time(), free_bytes=10**12))
        post.save(parent/'ledger.json', dict(tasks=dict(quota=dict(kind='quota', phase='done',
            directory=str(quota), result_sha256=post.digest(quota/'result.json'), native_exit_code=0,
            finished_epoch=time.time(), published_bytes=0))))
        policy = self.root/'terminal_policy.json'
        post.save(policy, dict(own_reservation_bytes=10**11, external_future_reservation_bytes=0,
            safety_floor_bytes=0, account_worker_ceiling=100))
        directory = self.root/'terminal_worker'
        directory.mkdir()
        post.save(directory/'request.json', dict(original_id=[1, 2, 3], weight=1., p=1.))
        bound = 536870912
        task = dict(kind='aggregate', phase='submitted', cluster=20, stratum='qcd_2to5', events=1,
            directory=str(directory), source_ids=['original'], publication_bound_bytes=bound,
            request_sha256=post.digest(directory/'request.json'))
        state = dict(tasks=dict(terminal=task), latest={}, sources={}, nanos={})
        if include_independent_worker:
            ready = self.root/'independent'
            ready.mkdir()
            (ready/'worker.sub').write_text('queue 1\n')
            state['tasks']['independent'] = dict(kind='source_group', phase='ready', stratum='qcd_5to10',
                events=1, directory=str(ready), source_ids=['independent'], publication_bound_bytes=67108864)
        config = dict(parent_state=str(parent), parent_config=str(policy),
            worker_publication_bound_bytes=67108864, maximum_publication_bytes=bound+67108864)
        terminal = dict(ClusterId=20, ProcId=0, Iwd=str(directory), JobStatus=3,
            ExitCode=0, ExitBySignal=False, RemoveReason='SYSTEM_PERIODIC_REMOVE: runtime exceeded')
        return config, state, terminal

    def test_native_removal_without_returned_files_keeps_claim_and_independent_admission(self):
        import run_shift_sm_postprocessing_controller as controller
        config, state, terminal = self.terminal_fixture(include_independent_worker=True)
        task = state['tasks']['terminal']
        original_request = (Path(task['directory'])/'request.json').read_bytes()
        with patch.object(controller, 'query', return_value=[]), \
             patch.object(controller, 'publication_history', return_value={20: terminal}), \
             patch.object(controller, 'run_condor', return_value='123.0 - 123.0\n') as submit:
            controller.poll(self.root, config, state)
        self.assertEqual(task['phase'], 'failed')
        self.assertIn('SYSTEM_PERIODIC_REMOVE', task['error'])
        self.assertEqual(task['native'], [terminal])
        self.assertEqual(task['publication_bound_bytes'], 536870912)
        self.assertEqual((Path(task['directory'])/'request.json').read_bytes(), original_request)
        self.assertFalse((Path(task['directory'])/'result.json').exists())
        self.assertEqual(state['latest'], {})
        self.assertEqual(state['tasks']['independent']['phase'], 'submitted')
        self.assertEqual(submit.call_count, 1)
        self.assertEqual(post.read(self.root/'heartbeat.json')['publication_budget_remaining_bytes'], 0)
        self.assertIn('terminal', post.read(self.root/'worker_attention_required.json')['failed_workers'])

    def test_unknown_absence_and_native_success_wait_for_returned_receipt_without_replay(self):
        import run_shift_sm_postprocessing_controller as controller
        config, state, terminal = self.terminal_fixture()
        terminal = dict(terminal, JobStatus=4)
        terminal.pop('RemoveReason')
        with patch.object(controller, 'query', return_value=[]), \
             patch.object(controller, 'publication_history', return_value={}) as history, \
             patch.object(controller, 'run_condor') as submit:
            controller.poll(self.root, config, state)
            self.assertEqual(state['tasks']['terminal']['phase'], 'submitted')
            history.return_value = {20: terminal}
            controller.poll(self.root, config, state)
            self.assertEqual(state['tasks']['terminal']['phase'], 'submitted')
            task = state['tasks']['terminal']
            post.save(Path(task['directory'])/'result.json', dict(complete=True,
                request_sha256=task['request_sha256'], events=1, published=dict(histogram=dict(bytes=11))))
            controller.poll(self.root, config, state)
        submit.assert_not_called()
        self.assertEqual(task['phase'], 'done')
        self.assertEqual(state['latest'], {'qcd_2to5': ['original']})

    def test_removed_terminal_must_match_exact_original_worker(self):
        import run_shift_sm_postprocessing_controller as controller
        config, state, terminal = self.terminal_fixture()
        for changed in (dict(ClusterId=21), dict(ProcId=1), dict(Iwd=str(self.root/'other'))):
            with self.subTest(changed=changed), \
                 patch.object(controller, 'query', return_value=[]), \
                 patch.object(controller, 'publication_history', return_value={20: dict(terminal, **changed)}), \
                 self.assertRaisesRegex(ValueError, 'differs from frozen task'):
                controller.poll(self.root, config, state)
            self.assertEqual(state['tasks']['terminal']['phase'], 'submitted')

    def test_native_history_keeps_scheduler_removal_evidence(self):
        import run_shift_sm_postprocessing_controller as controller
        _, state, terminal = self.terminal_fixture()
        with patch.object(controller, 'run_condor', return_value=json.dumps([terminal])) as native:
            self.assertEqual(controller.publication_history(state['tasks'], {}), {20: terminal})
        argv = native.call_args.args[0]
        attributes = argv[argv.index('-attributes')+1].split(',')
        for name in ('JobStatus', 'RemoveReason', 'CompletionDate', 'EnteredCurrentStatus'):
            self.assertIn(name, attributes)

    def test_quota_runs_with_full_science_caps_and_never_duplicates_pending_worker(self):
        import run_shift_sm_postprocessing_controller as controller
        import run_shift_smart_sm_controller as native
        task=dict(kind='quota',phase='ready',directory=str(self.root/'job'),request_sha256='a'*64)
        self.config.update(account_worker_ceiling=2000,maximum_idle_workers=1800)
        full=[dict(ClusterId=i+1000,JobUniverse=5,JobStatus=2 if i<2000 else 1)
              for i in range(3800)]
        with patch.object(native,'make_task',return_value=task) as make, \
             patch.object(controller,'run_condor',return_value='123.0 - 123.0\n') as submit:
            controller.refresh_quota(self.root,self.config,full)
            request=make.call_args.args[3]
            self.assertEqual(request['stage'],'smart_sm_quota')
            self.assertEqual(request['reservation_bytes'],0)
            self.assertEqual(submit.call_count,1)
            with patch.object(native,'observe'):
                controller.refresh_quota(self.root,self.config,full)
            self.assertEqual(make.call_count,1);self.assertEqual(submit.call_count,1)
        ledger=json.loads((self.root/'quota/ledger.json').read_text())
        self.assertEqual(ledger['tasks']['quota_00000']['cluster'],123)

    def test_source_future_credit_requires_newer_quota_but_never_reduces_allocation(self):
        from run_shift_sm_postprocessing_controller import science_write_reservation
        config=dict(reserved_science_bytes=1000,science_source_write_bounds=dict(done=300,partial=400))
        state=dict(response_sources=dict(done=dict(closed_epoch=20)))
        self.assertEqual(science_write_reservation(config,state,10),(1000,1000))
        self.assertEqual(science_write_reservation(config,state,30),(1000,700))
        # Unassigned300B and unfinished400B stay protected. A legacy cache
        # without its native-proof observation epoch receives no future credit.
        state['response_sources']['partial']={}
        self.assertEqual(science_write_reservation(config,state,30),(1000,700))
        config['science_source_write_bounds']['partial']=800
        with self.assertRaisesRegex(ValueError,'source write bounds'):
            science_write_reservation(config,state,30)

    def test_private_quota_refresh_preserves_a_separate_finished_science_ledger(self):
        import run_shift_sm_postprocessing_controller as controller
        import run_shift_smart_sm_controller as native
        parent=self.root/'science';parent.mkdir()
        ledger=parent/'ledger.json';ledger.write_text(json.dumps(dict(tasks=dict(
            done=dict(kind='source',phase='done',published_bytes=400)))))
        before=ledger.read_bytes()
        policy=self.root/'science_config.json';policy.write_text(json.dumps(dict(own_reservation_bytes=500)))
        config=dict(self.config,parent_state=str(parent),parent_config=str(policy),
            quota_state=str(self.root/'quota'),quota_config=str(self.parent_config),
            quota_config_sha256=post.digest(self.parent_config))
        task=dict(kind='quota',phase='ready',directory=str(self.root/'job'),request_sha256='a'*64)
        with patch.object(native,'make_task',return_value=task) as make, \
             patch.object(controller,'run_condor',return_value='123.0 - 123.0\n'):
            controller.refresh_quota(self.root,config,[])
        self.assertEqual(make.call_args.args[0],self.root/'quota')
        self.assertEqual(make.call_args.args[1],dict(worker_template='queue 1\n'))
        self.assertEqual(ledger.read_bytes(),before)
        self.parent_config.write_text('{}')
        with self.assertRaisesRegex(ValueError,'quota configuration changed'):
            controller.refresh_quota(self.root,config,[])

    def test_separate_fresh_quota_never_replaces_real_science_accounting_or_its_full_allocation(self):
        import run_shift_sm_postprocessing_controller as controller
        import time
        for allocation in (509,510):
            with self.subTest(allocation=allocation),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);parent=root/'science';parent.mkdir();private=root/'quota';private.mkdir()
                result=private/'result.json';result.write_text(json.dumps(dict(
                    complete=True,checked_epoch=time.time(),free_bytes=10000)))
                quota=dict(kind='quota',phase='done',directory=str(private),result_sha256=post.digest(result),
                    native_exit_code=0,finished_epoch=time.time(),published_bytes=0)
                (private/'ledger.json').write_text(json.dumps(dict(tasks=dict(quota=quota))))
                (parent/'ledger.json').write_text(json.dumps(dict(tasks=dict(
                    source=dict(kind='source',phase='done',published_bytes=400,
                        native_exit_code=0,result_sha256='a'*64),
                    pending=dict(kind='detector',phase='submitted',bound_bytes=100)))))
                parent_policy=root/'parent_config.json';parent_policy.write_text(json.dumps(dict(
                    own_reservation_bytes=allocation,external_future_reservation_bytes=0,
                    safety_floor_bytes=0,account_worker_ceiling=100)))
                config=dict(campaign='separate',parent_state=str(parent),parent_config=str(parent_policy),
                    parent_config_sha256=post.digest(parent_policy),quota_state=str(private),refresh_quota=True,
                    worker_publication_bound_bytes=10,maximum_publication_bytes=1000)
                job=root/'group';job.mkdir();(job/'worker.sub').write_text('queue 1\n')
                state=dict(tasks=dict(group=dict(phase='ready',kind='source_group',stratum='qcd_1to2',
                    events=1,source_ids=['group'],directory=str(job),publication_bound_bytes=10)),
                    latest={},sources={},nanos={})
                with patch.object(controller,'query',return_value=[]), \
                     patch.object(controller,'publication_history',return_value={}), \
                     patch.object(controller,'refresh_quota'), \
                     patch.object(controller,'source_cache'),patch.object(controller,'nano_cache'), \
                     patch.object(controller,'closed_sources',return_value={}), \
                     patch.object(controller,'run_condor',return_value='123.0 - 123.0\n') as submit:
                    controller.poll(root,config,state)
                self.assertEqual(submit.call_count,int(allocation==510))
                self.assertEqual(state['tasks']['group']['phase'],'submitted' if allocation==510 else 'ready')

    def test_separate_quota_rejects_changed_parent_and_missing_native_science_proof(self):
        import run_shift_sm_postprocessing_controller as controller
        import time
        for failure in ('changed_config','quota_only_parent','nonzero_science'):
            with self.subTest(failure=failure),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);parent=root/'science';parent.mkdir();private=root/'quota';private.mkdir()
                result=private/'result.json';result.write_text(json.dumps(dict(
                    complete=True,checked_epoch=time.time(),free_bytes=10000)))
                quota=dict(kind='quota',phase='done',directory=str(private),result_sha256=post.digest(result),
                    native_exit_code=0,finished_epoch=time.time(),published_bytes=0)
                (private/'ledger.json').write_text(json.dumps(dict(tasks=dict(quota=quota))))
                science=dict(source=dict(kind='source',phase='done',published_bytes=40,
                    native_exit_code=0,result_sha256='a'*64))
                if failure=='quota_only_parent':science={}
                if failure=='nonzero_science':science['source']['native_exit_code']=1
                (parent/'ledger.json').write_text(json.dumps(dict(tasks=science)))
                policy=root/'parent_config.json';policy.write_text(json.dumps(dict(own_reservation_bytes=500)))
                config=dict(campaign='separate',parent_state=str(parent),parent_config=str(policy),
                    parent_config_sha256=post.digest(policy),quota_state=str(private),refresh_quota=True,
                    worker_publication_bound_bytes=10,maximum_publication_bytes=1000)
                if failure=='changed_config':policy.write_text('{}')
                with patch.object(controller,'query',return_value=[]), \
                     patch.object(controller,'publication_history',return_value={}), \
                     patch.object(controller,'refresh_quota'),patch.object(controller,'source_cache'), \
                     patch.object(controller,'nano_cache'),patch.object(controller,'closed_sources',return_value={}), \
                     patch.object(controller,'run_condor') as submit, self.assertRaises(ValueError):
                    controller.poll(root,config,dict(tasks={},latest={},sources={},nanos={}))
                submit.assert_not_called()

    def test_worker_bounds_follow_actual_bin_and_interim_request(self):
        from run_shift_sm_postprocessing_controller import worker_publication_bound
        config=dict(worker_publication_bound_bytes=100,
            worker_publication_bounds_by_stratum=dict(qcd_5to10=300,qcd_20toinf=50),
            interim_histogram_publication_bound_bytes=8)
        self.assertEqual(worker_publication_bound(config,'qcd_5to10'),300)
        self.assertEqual(worker_publication_bound(config,'qcd_20toinf'),50)
        self.assertEqual(worker_publication_bound(config,'dy_1to2'),100)
        self.assertEqual(worker_publication_bound(config,'qcd_5to10',interim=True),8)
        config['worker_publication_bounds_by_stratum']['qcd_5to10']=0
        with self.assertRaisesRegex(ValueError,'positive integer'):
            worker_publication_bound(config,'qcd_5to10')

    def test_required_native_publication_canary_blocks_admission_until_done(self):
        from run_shift_sm_postprocessing_controller import publication_qualification_ready
        config=dict(qualification_task='positive')
        state=dict(tasks=dict(positive=dict(qualification_only=True,phase='submitted')))
        self.assertFalse(publication_qualification_ready(config,state))
        state['tasks']['positive']['phase']='done'
        self.assertTrue(publication_qualification_ready(config,state))
        state['tasks']['positive']['phase']='failed'
        with self.assertRaisesRegex(ValueError,'canary failed'):
            publication_qualification_ready(config,state)
        with self.assertRaisesRegex(ValueError,'canary is missing'):
            publication_qualification_ready(config,dict(tasks={}))

    def test_explicit_passed_canary_allows_parallel_group_and_aggregate_before_latest(self):
        import run_shift_sm_postprocessing_controller as controller
        import time
        for prequalified in [False,True,'native_terminal']:
            with self.subTest(prequalified=prequalified),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);parent=root/'parent';parent.mkdir()
                q=parent/'quota';q.mkdir();result=q/'result.json'
                result.write_text(json.dumps(dict(complete=True,checked_epoch=time.time(),free_bytes=10**9)))
                quota=dict(kind='quota',phase='done',directory=str(q),result_sha256=post.digest(result),
                           native_exit_code=0,finished_epoch=time.time(),published_bytes=0)
                (parent/'ledger.json').write_text(json.dumps(dict(tasks=dict(quota=quota))))
                (parent/'source_inventory.json').write_text(json.dumps(dict(nano_outputs=[])))
                parent_config=root/'parent_config.json';parent_config.write_text(json.dumps(dict(
                    own_reservation_bytes=10**8,external_future_reservation_bytes=0,safety_floor_bytes=0,
                    account_worker_ceiling=100,reserved_science_bytes=0)))
                config=dict(campaign='parallel',parent_state=str(parent),parent_config=str(parent_config),
                    grouped_response_publication=True,worker_publication_bound_bytes=100,
                    maximum_publication_bytes=10000000)
                state=dict(tasks={},latest={},sources={},nanos={})
                terminals={}
                if prequalified:
                    config['qualification_task']='positive'
                    state['tasks']['positive']=dict(phase='done',qualification_only=True,stratum='qcd_1to2',
                                                    events=1,published_bytes=0)
                    if prequalified=='native_terminal':
                        p=root/'qualification';p.mkdir()
                        (p/'result.json').write_text(json.dumps(dict(complete=True,request_sha256='a'*64,
                            events=1,published=dict(histogram=dict(bytes=1)))))
                        state['tasks']['positive'].update(kind='qualification',phase='submitted',cluster=20,
                            directory=str(p),request_sha256='a'*64,source_ids=['OLD_BASELINE_REPLAY'])
                        terminals={20:dict(Iwd=str(p),ExitCode=0,ExitBySignal=False)}
                for name,kind,events in [('group','source_group',1),('aggregate','aggregate',355)]:
                    p=root/name;p.mkdir();(p/'worker.sub').write_text('queue 1\n')
                    state['tasks'][name]=dict(phase='ready',kind=kind,stratum='qcd_1to2',events=events,
                        source_ids=[name],directory=str(p),publication_bound_bytes=100)
                with patch.object(controller,'query',return_value=[]),\
                        patch.object(controller,'publication_history',return_value=terminals),\
                        patch.object(controller,'run_condor',side_effect=['123.0 - 123.0\n','124.0 - 124.0\n']) as submit:
                    controller.poll(root,config,state)
                self.assertEqual(submit.call_count,2 if prequalified else 1)
                self.assertEqual(state['tasks']['aggregate']['phase'],'submitted' if prequalified else 'ready')
                self.assertEqual(state['latest'],{})

    def test_failed_worker_retains_partial_write_bound_and_alerts_without_retry(self):
        import run_shift_sm_postprocessing_controller as controller
        import time
        for failure in ['held','nonzero_terminal']:
            for publication_budget in [199,200]:
                with self.subTest(failure=failure,budget=publication_budget), tempfile.TemporaryDirectory() as directory:
                    root=Path(directory);parent=root/'parent';parent.mkdir();quota=parent/'quota';quota.mkdir()
                    result=quota/'result.json';result.write_text(json.dumps(dict(
                        complete=True,checked_epoch=time.time(),free_bytes=10**9)))
                    (parent/'ledger.json').write_text(json.dumps(dict(tasks=dict(quota=dict(
                        kind='quota',phase='done',directory=str(quota),result_sha256=post.digest(result),
                        native_exit_code=0,finished_epoch=time.time(),published_bytes=0)))))
                    (parent/'source_inventory.json').write_text(json.dumps(dict(nano_outputs=[])))
                    policy=root/'policy.json';policy.write_text(json.dumps(dict(own_reservation_bytes=10**8,
                        external_future_reservation_bytes=0,safety_floor_bytes=0,account_worker_ceiling=100)))
                    config=dict(parent_state=str(parent),parent_config=str(policy),
                        worker_publication_bound_bytes=900,maximum_publication_bytes=publication_budget)
                    bad=root/'bad';bad.mkdir();(bad/'result.json').write_text('{}')
                    good=root/'good';good.mkdir();(good/'worker.sub').write_text('queue 1\n')
                    state=dict(tasks=dict(bad=dict(kind='source_group',phase='submitted',cluster=20,
                        stratum='qcd_1to2',directory=str(bad),request_sha256='a'*64,source_ids=['bad'],
                        events=1,publication_bound_bytes=100),good=dict(kind='source_group',phase='ready',
                        stratum='qcd_2to5',directory=str(good),source_ids=['good'],events=1,
                        publication_bound_bytes=100)),latest={},sources={},nanos={})
                    live=[dict(ClusterId=20,JobStatus=5,JobUniverse=5)] if failure=='held' else []
                    history={} if live else {20:dict(Iwd=str(bad),ExitCode=1,ExitBySignal=False)}
                    with patch.object(controller,'query',return_value=live), \
                            patch.object(controller,'publication_history',return_value=history), \
                            patch.object(controller,'run_condor',return_value='123.0 - 123.0\n') as submit:
                        controller.poll(root,config,state)
                    self.assertEqual(state['tasks']['bad']['phase'],'failed')
                    self.assertEqual(state['tasks']['good']['phase'],'submitted' if publication_budget==200 else 'ready')
                    self.assertEqual(submit.call_count,int(publication_budget==200))
                    alert=json.loads((root/'worker_attention_required.json').read_text())
                    self.assertEqual(alert['failed_workers']['bad']['request_sha256'],'a'*64)
                    self.assertIn('no automatic retry',alert['action'])
                    self.assertFalse((root/'attention_required.json').exists())
                    heartbeat=json.loads((root/'heartbeat.json').read_text())
                    self.assertEqual(heartbeat['tasks']['failed'],1)
                    self.assertEqual(heartbeat['publication_budget_remaining_bytes'],99 if publication_budget==199 else 0)

    def test_old_or_physics_ledger_cannot_become_quota_authority(self):
        import run_shift_sm_postprocessing_controller as controller
        with self.assertRaisesRegex(ValueError,'own private quota'):
            controller.refresh_quota(self.root,dict(self.config,parent_state='/afs/old_campaign'),[])
        (self.root/'quota').mkdir()
        (self.root/'quota/ledger.json').write_text(json.dumps(dict(tasks={'physics':dict(kind='detector')})))
        with self.assertRaisesRegex(ValueError,'another production ledger'):
            controller.refresh_quota(self.root,self.config,[])

    def test_uncertain_submission_and_failed_worker_never_auto_retry(self):
        import run_shift_sm_postprocessing_controller as controller
        import run_shift_smart_sm_controller as native
        task=dict(kind='quota',phase='ready',directory=str(self.root/'job'),request_sha256='a'*64)
        with patch.object(native,'make_task',return_value=task), \
             patch.object(controller,'run_condor',return_value='unknown'):
            with self.assertRaisesRegex(ValueError,'Uncertain native quota submission'):
                controller.refresh_quota(self.root,self.config,[])
        ledger=json.loads((self.root/'quota/ledger.json').read_text())
        self.assertEqual(ledger['tasks']['quota_00000']['phase'],'submitting')
        with self.assertRaisesRegex(RuntimeError,'Uncertain submission'):
            controller.refresh_quota(self.root,self.config,[])
        ledger['tasks']['quota_00000']['phase']='failed'
        (self.root/'quota/ledger.json').write_text(json.dumps(ledger))
        with self.assertRaisesRegex(ValueError,'Native quota worker failed'):
            controller.refresh_quota(self.root,self.config,[])


class PostprocessingTest(unittest.TestCase):
    def test_prepared_sidecar_requires_full_census_contract_before_identity_compaction(self):
        sidecar = dict(path='/eos/prepared/weight_authority.json', sha256='a'*64,
                       required_for_downstream_weights=True, diagnostic_only=True)
        conditional = dict(event_ids=[[1, 1, 1]], weights=[3.], selected_indices=[0],
                           sampling_probabilities=[1.], prepared_weight_authority=sidecar)
        for consumer in (selected_rows, compact_source):
            with self.subTest(consumer=consumer), self.assertRaisesRegex(ValueError, 'full-census decay_proposal'):
                consumer(conditional)
        source, ledger = native_proposal_fixture()
        source['prepared_weight_authority'] = sidecar
        bound = bind_source(source, ledger)
        self.assertEqual(selected_rows(bound), selected_rows(compact_source(bound)))
        self.assertEqual(post.histogram_weight_branch([bound]), 'shiftProposalGenWeight')

    def test_optional_source_survives_controller_without_changing_legacy_compaction(self):
        source, ledger = native_proposal_fixture()
        bound = bind_source(source, ledger)
        compact = compact_source(bound)
        self.assertEqual(compact['decay_proposal'], bound['decay_proposal'])
        self.assertEqual(compact['normalization_record'], bound['normalization_record'])
        self.assertNotIn('receipt', compact)
        self.assertIsNot(compact, bound)
        self.assertEqual(post.histogram_weight_branch([compact]), 'shiftProposalGenWeight')
        self.assertEqual(selected_rows(compact), selected_decay_proposals(bound)[0])
        legacy = dict(event_ids=[[1, 1, 1], [1, 1, 2]], weights=[3., 7.],
                      selected_indices=[1], sampling_probabilities=[.1])
        self.assertEqual(compact_source(legacy), dict(event_ids=[[1, 1, 2]], weights=[7.],
                         selected_indices=[0], sampling_probabilities=[.1]))
        self.assertEqual(post.histogram_weight_branch([legacy]), 'shiftSamplingGenWeight')
        with self.assertRaisesRegex(ValueError, 'separate publication'):
            post.histogram_weight_branch([compact, legacy])

    def test_exact_annotation_binds_draw_factors_and_full_original_exposure(self):
        from shift_event_weights import WEIGHT_POLICY
        from shift_material_preselection import checksum
        source, ledger = native_proposal_fixture()
        bound = bind_source(source, ledger)
        rows, exposures = selected_decay_proposals(bound)
        annotation = dict(schema='shift-decay-proposal-weight-annotation-v1',
            weight_policy=WEIGHT_POLICY, weight_branch='shiftProposalGenWeight', events=len(rows),
            input_nano_sha256='f'*64, original_event_ids=[row['event_id'] for row in rows],
            event_authority_sha256=checksum(rows), source_exposures=exposures,
            one_draw_per_original=True, native_weights_unchanged=True, detector_probability_unchanged=True,
            decay_correction_application_count=1, material_survival_correction_applied=False)
        post.validate_proposal_annotation(annotation, rows, exposures)
        for change in [dict(decay_correction_application_count=2), dict(source_exposures=[]),
                       dict(event_authority_sha256='e'*64), dict(material_survival_correction_applied=True)]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                post.validate_proposal_annotation(dict(annotation, **change), rows, exposures)

    def test_publication_requires_categories_beyond_inclusive_and_both(self):
        class Root:
            def Get(self, name):
                return name not in ('dimuon/ShiftDimuonVertexOther_logMass',
                                    'dimuon/ShiftDimuonVertexNear-Far_mass')
        with self.assertRaisesRegex(ValueError, 'Other_logMass'):
            validate_dimuon_histograms(Root())

    def test_empty_booked_categories_are_valid_for_a_production_chunk(self):
        class EmptyHistogram:
            def GetEntries(self):
                return 0
        class Root:
            def Get(self, name):
                return EmptyHistogram()
        self.assertEqual(len(validate_dimuon_histograms(Root())), 21)

    def test_larger_hadd_file_limit_preserves_input_order_and_compression(self):
        self.assertEqual(hadd_command('out.root', ['a.root', 'b.root'], 200),
            ['hadd', '-fk', '-n', '200', 'out.root', 'a.root', 'b.root'])
        with self.assertRaises(ValueError):
            hadd_command('out.root', ['a.root'], 0)

    def source(self, checksum, count=100):
        return dict(stratum='jpsi_5to10', source_id=checksum, gen_sha256=checksum,
                    denominator_kind='fixed_source_trials', full_source_trials=count,
                    sigma_nd_pb=123., physics_normalization_ready=False,
                    forced_decay_branching_convention_ready=False)

    def test_full_exposure_includes_zero_admission_sources(self):
        result = exposure([self.source('a'), self.source('b', 200)], 'jpsi_5to10')
        self.assertEqual(result['full_source_sumw'], 300)
        self.assertEqual(result['cross_section_pb'], 123.)
        self.assertFalse(result['physics_normalization_ready'])
        self.assertFalse(result['forced_decay_branching_convention_ready'])

    def test_original_gen_cannot_be_counted_twice(self):
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            exposure([self.source('a'), self.source('a')], 'jpsi_5to10')

    def test_accepted_event_cross_sections_keep_their_denominator(self):
        sources = []
        for key, count, rate in [('a', 2, 10.), ('b', 6, 30.)]:
            sources.append(dict(source_id=key, gen_sha256=key, stratum='dy_1to2',
                denominator_kind='accepted_gen_events', full_source_events=count,
                native_cross_section_pb=rate))
        result = exposure(sources, 'dy_1to2')
        self.assertEqual(result['full_source_sumw'], 8)
        self.assertEqual(result['cross_section_pb'], 25.)

    def accepted_class_source(self, key, count, trials):
        sigma_nd = 1200.
        rate, error = sigma_nd*count/trials, sigma_nd*math.sqrt(count)/trials
        source = dict(source_id=key, gen_sha256=key*64, stratum='jpsi_0to1',
            source_family='jpsi', bounds=[0., 1.], denominator_kind='accepted_gen_events',
            full_source_events=count, native_cross_section_pb=rate,
            native_cross_section_error_pb=error)
        audit = dict(events=count, accepted_events=count, framework_requested_events=count,
            generator_failed_framework_slots=0, sum_weights=count, sum_weights_squared=count,
            generated_filter_efficiency=1., hard_process_codes={'101': count},
            event_class='direct_jpsi', configured_pthat_bounds=[0., 1.],
            input_sha256=key*64, mpi_model_settings_sha256='c'*64,
            mpi_model_contract='pythia8-softqcd-nd-cp5-processlevel3-v1',
            partition_contract='direct-hard-jpsi-status23or33-v1',
            pythia_process_statistics=dict(tried=trials, selected=trials, accepted=count,
                sigma_pb=rate, error_pb=error),
            runs=[dict(internal_xsec_pb=rate, error_pb=error)])
        source['native_class_normalization'] = accepted_class_authority(source, audit, 'd'*64)
        return source, audit

    def test_accepted_class_pools_native_trials_keeps_accepted_denominator(self):
        first, _ = self.accepted_class_source('a', 2, 100)
        second, _ = self.accepted_class_source('b', 6, 600)
        result = exposure([first, second], 'jpsi_0to1')
        self.assertEqual(result['full_source_sumw'], 8)
        self.assertAlmostEqual(result['cross_section_pb'], 1200.*8/700)
        self.assertNotAlmostEqual(result['cross_section_pb'], (2*24.+6*12.)/8)
        self.assertEqual(result['native_pythia_trials'], 700)
        regrouped, _ = self.accepted_class_source('e', 8, 700)
        combined = exposure([regrouped], 'jpsi_0to1')
        self.assertAlmostEqual(result['cross_section_pb'], combined['cross_section_pb'])
        self.assertAlmostEqual(result['cross_section_error_pb'], combined['cross_section_error_pb'])

    def test_accepted_class_rejects_missing_or_incompatible_native_authority(self):
        first, _ = self.accepted_class_source('a', 2, 100)
        second, _ = self.accepted_class_source('b', 6, 600)
        for change in [dict(pythia_tried=6), dict(pythia_accepted=5),
                       dict(mpi_model_settings_sha256='e'*64), dict(sigma_nd_pb=1300.),
                       dict(event_class='qcd'), dict(bounds=[1., 2.])]:
            changed = copy.deepcopy(second)
            changed['native_class_normalization'].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                exposure([first, changed], 'jpsi_0to1')
        legacy = copy.deepcopy(second)
        del legacy['native_class_normalization']
        with self.assertRaisesRegex(ValueError, 'Exact native Pythia'):
            exposure([first, legacy], 'jpsi_0to1')
        # One legacy source needs no pooling: retain its exact native authority.
        self.assertEqual(exposure([legacy], 'jpsi_0to1')['cross_section_pb'],
                         legacy['native_cross_section_pb'])
        incompatible = copy.deepcopy(second)
        incompatible['native_cross_section_pb'] *= 2.
        incompatible['native_class_normalization']['sigma_nd_pb'] *= 2.
        with self.assertRaisesRegex(ValueError, 'incompatible native model or ND'):
            exposure([first, incompatible], 'jpsi_0to1')

    def test_native_class_authority_requires_the_exact_complete_unit_source_audit(self):
        source, audit = self.accepted_class_source('a', 5, 142017)
        for change in [dict(input_sha256='f'*64), dict(generator_failed_framework_slots=1),
                       dict(sum_weights=4.), dict(generated_filter_efficiency=.8),
                       dict(framework_requested_events=6)]:
            changed = copy.deepcopy(audit)
            changed.update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                accepted_class_authority(source, changed, 'd'*64)

    def test_signal_histograms_keep_conditional_gen_denominator_and_physical_rate(self):
        source=dict(source_id='signal',gen_sha256='a',stratum='darkphoton_m0p7_lab_100m',
            denominator_kind='conditional_signal_events',full_source_events=141,
            physical_dimuon_cross_section_pb=1.5e-5,
            signal_rate_record=dict(full_gen=dict(accepted_events=141,sum_weights=141.),
                physical_selected_cross_sections_pb=[dict(value_pb=1.5e-5)],
                source_exposure=dict(source_collisions=10000)))
        result=exposure([source],source['stratum'])
        self.assertEqual(result['full_source_sumw'],141)
        self.assertEqual(result['cross_section_pb'],1.5e-5)
        self.assertTrue(result['no_second_filter_efficiency'])
        with self.assertRaisesRegex(ValueError,'source-aware aggregator'):
            exposure([source,dict(source,gen_sha256='b')],source['stratum'])
        source['physical_dimuon_cross_section_pb']*=.4
        with self.assertRaisesRegex(ValueError,'full source-rate record'):
            exposure([source],source['stratum'])

    def test_partial_source_waits_but_zero_selected_source_closes(self):
        sources = [dict(descriptor_hashes=['a'], normalization=self.source('a'),
                        selected=dict(event_ids=[[1, 1, 1], [1, 1, 2]])),
                   dict(descriptor_hashes=['b'], normalization=self.source('b'),
                        selected=dict(event_ids=[]))]
        nanos = [dict(source_descriptor_sha256='a', path='one.root', events=1)]
        self.assertEqual([s['normalization']['source_id'] for s in
                          closed_sources(sources, nanos)['jpsi_5to10']], ['b'])
        nanos.append(dict(source_descriptor_sha256='a', path='two.root', events=1))
        self.assertEqual(len(closed_sources(sources, nanos)['jpsi_5to10']), 2)

    def test_full_census_proposal_closes_only_its_frozen_admitted_count(self):
        source, ledger = native_proposal_fixture(3)
        ledger['events'][1]['admitted'] = False
        bound = bind_source(source, ledger)
        selected = compact_source(bound)
        self.assertEqual(len(selected['event_ids']), 3)
        self.assertEqual(selected['selected_indices'], [0, 2])
        normalization = dict(source['normalization_record'], stratum=source['stratum'], sigma_nd_pb=123.)
        cached = dict(descriptor_hashes=['frozen'], normalization=normalization, selected=selected)
        nanos = [dict(source_descriptor_sha256='frozen', path=f'nano{i}.root', events=1) for i in range(3)]
        self.assertFalse(closed_sources([cached], nanos[:1]))
        complete = closed_sources([cached], nanos[:2])[source['stratum']]
        self.assertEqual(len(complete), 1)
        self.assertEqual(complete[0]['nano_inputs'], nanos[:2])
        self.assertEqual(complete[0]['normalization'], normalization)
        self.assertEqual(exposure([complete[0]['normalization']], source['stratum'])['full_source_sumw'], 2000)
        config = dict(campaign='synthetic', histogram_runtime=dict(path='runtime', sha256='a'*64),
            worker_publication_bound_bytes=1000000, output_base='/eos/synthetic',
            worker_template='initialdir = @DIRECTORY@\nrequest_sha = @REQUEST_SHA@\n')
        with tempfile.TemporaryDirectory() as directory:
            task = snapshot(Path(directory), config, source['stratum'], complete)
            self.assertEqual(task['events'], 2)
            request = json.loads((Path(task['directory'])/'request.json').read_text())
            self.assertEqual(len(request['selected_sources'][0]['event_ids']), 3)
            self.assertEqual(request['selected_sources'][0]['selected_indices'], [0, 2])
            self.assertEqual(request['normalization_sources'], [normalization])
        with self.assertRaisesRegex(ValueError, 'over-coverage'):
            closed_sources([cached], nanos)

    def test_zero_admitted_proposal_closes_without_losing_full_original_exposure(self):
        source, ledger = native_proposal_fixture(3)
        for row in ledger['events']: row['admitted'] = False
        selected = compact_source(bind_source(source, ledger))
        self.assertEqual(len(selected['event_ids']), 3)
        self.assertEqual(selected['selected_indices'], [])
        normalization = dict(source['normalization_record'], stratum=source['stratum'], sigma_nd_pb=123.)
        cached = dict(descriptor_hashes=['zero'], normalization=normalization, selected=selected)
        complete = closed_sources([cached], [])[source['stratum']]
        self.assertEqual(complete[0]['nano_inputs'], [])
        self.assertEqual(complete[0]['selected']['event_ids'], source['event_ids'])
        self.assertEqual(exposure([complete[0]['normalization']], source['stratum'])['full_source_sumw'], 2000)
        with tempfile.TemporaryDirectory() as directory:
            self.assertIsNone(snapshot(Path(directory), {}, source['stratum'], complete))
            self.assertFalse(list(Path(directory).iterdir()))

    def test_selected_weights_use_selected_position_probability(self):
        rows = selected_rows(dict(event_ids=[[1, 1, 1], [1, 1, 2]], weights=[3., 7.],
                                  selected_indices=[1], sampling_probabilities=[0.1]))
        self.assertEqual(rows, [dict(event_id=[1, 1, 2], native_weight=7., sampling_probability=0.1)])

    def test_remerge_needs_meaningful_new_stats(self):
        self.assertFalse(meaningful_update(list(range(100)), list(range(101))))
        self.assertTrue(meaningful_update(list(range(100)), list(range(110))))
        self.assertTrue(meaningful_update([], ['first']))
        with self.assertRaises(ValueError):
            meaningful_update(['missing'], ['other'])

    def test_opt_in_refresh_counts_baseline_in_both_total_sizes(self):
        self.assertFalse(meaningful_update([],range(222),250,.2,1111))
        self.assertTrue(meaningful_update([],range(223),250,.2,1111))
        self.assertFalse(meaningful_update(range(223),range(472),250,.2,1111))
        self.assertTrue(meaningful_update(range(223),range(473),250,.2,1111))
        self.assertFalse(meaningful_update([],range(89),250,.2,449))
        self.assertTrue(meaningful_update([],range(90),250,.2,449))

    def test_final_settled_status_is_per_bin_and_preserves_missing_responses(self):
        from run_shift_sm_postprocessing_controller import settled_stratum_status
        parent=dict(tasks=dict(done=dict(kind='source',stratum='qcd_1to2',phase='done'),
            failed=dict(kind='detector',stratum='qcd_1to2',phase='failed',error='Missing response',selected_events=1),
            other=dict(kind='source',stratum='qcd_2to5',phase='submitted')))
        self.assertIsNone(settled_stratum_status(parent,'qcd_2to5'))
        status=settled_stratum_status(parent,'qcd_1to2')
        self.assertTrue(status['all_attempts_settled']);self.assertFalse(status['all_responses_complete'])
        self.assertEqual(status['excluded_incomplete_tasks'][0]['task_name'],'failed')
        self.assertFalse(status['missing_responses_are_physics_rejections'])

    def test_parent_storage_charge_includes_downstream_committed_and_pending(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'config.json').write_text(json.dumps(dict(worker_publication_bound_bytes=100)))
            (root / 'state.json').write_text(json.dumps(dict(tasks={
                'done': dict(phase='done', published_bytes=17),
                'running': dict(phase='submitted'), 'uncertain': dict(phase='submitting'),
                'not_yet_admitted': dict(phase='ready')})))
            self.assertEqual(postprocessing_charge(dict(postprocessing_directory=str(root))), (17, 200))
        self.assertEqual(postprocessing_charge({}), (0, 0))

    def test_AFS_outage_retries_the_same_serialized_checkpoint(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'state.json'
            payload = dict(value=1)
            original_open = Path.open
            failures = []

            def interrupted_open(current, mode='r', *args, **kwargs):
                if mode == 'wb' and len(failures) < 3:
                    failures.append(current)
                    raise OSError(errno.EIO, 'AFS unavailable')
                return original_open(current, mode, *args, **kwargs)

            with patch.object(Path, 'open', interrupted_open), patch.object(
                    main_controller.time, 'sleep', side_effect=lambda seconds: payload.update(value=99)):
                main_controller.save(path, payload)
            self.assertEqual(json.loads(path.read_text()), dict(value=1))
            self.assertEqual(len(failures), 3)

    def test_permission_error_is_not_hidden_as_AFS_outage(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(
                Path, 'open', side_effect=PermissionError(errno.EACCES, 'permission denied')):
            with self.assertRaises(PermissionError):
                main_controller.save(Path(temporary) / 'state.json', dict(value=1))


@unittest.skipUnless(importlib.util.find_spec('ROOT') and importlib.util.find_spec('uproot'),
                     'Requires native ROOT and uproot')
class NativeProposalPublicationTest(unittest.TestCase):
    def fixture(self, path, rows):
        from array import array
        import ROOT
        output = ROOT.TFile(str(path), 'RECREATE')
        tree = ROOT.TTree('Events', 'Native detector output before annotation')
        identifiers = {name: array('Q', [0]) for name in ('run', 'luminosityBlock', 'event')}
        for name, value in identifiers.items(): tree.Branch(name, value, name+'/l')
        native, muons = array('f', [0.]), array('I', [2])
        tree.Branch('genWeight', native, 'genWeight/F')
        tree.Branch('nShiftMuon', muons, 'nShiftMuon/i')
        for row in rows:
            for name, value in zip(identifiers, row['event_id']): identifiers[name][0] = value
            native[0] = row['native_weight']
            tree.Fill()
        tree.Write()
        for name in ('Runs', 'LuminosityBlocks', 'MetaData', 'ParameterSets'):
            metadata = ROOT.TTree(name, 'Unchanged native metadata')
            value = array('Q', [2000])
            metadata.Branch('original', value, 'original/l')
            metadata.Fill(); metadata.Write()
        ROOT.TObjString('Native metadata remains unchanged').Write('OriginalMetadata')
        output.Close()

    def test_worker_annotation_multievent_merge_and_histogram_branch_keep_exact_weights(self):
        import ROOT
        import run_shift_gen_to_nano as worker
        from shift_event_weights import annotate_proposal_weights
        from shift_nano_integrity import semantic_inventory, compare_all_branches
        source, ledger = native_proposal_fixture(3)
        bound = bind_source(source, ledger)
        expected_rows, exposures = selected_decay_proposals(bound)
        expected = {tuple(row['event_id']): row for row in expected_rows}
        exposure_map = {(row['source_id'], row['original_source_sha256']): row for row in exposures}
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory)/f'input{i}.root' for i in (1, 2)]
            authority_hashes = []
            for path, indices in zip(paths, ([0, 1], [2])):
                proposal = worker.prepare_decay_annotation(bound, indices)
                rows = proposal[0]
                self.fixture(path, rows)
                signature = [dict(id=row['event_id'], weight=row['native_weight']) for row in rows]
                audit, annotation = worker.finalize_nano_weights(path, signature,
                    [row['sampling_probability'] for row in rows], proposal)
                self.assertEqual(audit['events'], len(rows))
                self.assertEqual(annotation['decay_correction_application_count'], 1)
                authority_hashes.append(post.digest(path))
                identities, sumw, _ = post.inspect_nano(path, expected, exposure_map)
                self.assertEqual(identities, [tuple(row['event_id']) for row in rows])
                self.assertAlmostEqual(sumw, math.fsum(row['native_weight']/.4/1.6 for row in rows))
                with self.assertRaisesRegex(ValueError, 'second correction'):
                    annotate_proposal_weights(path, *proposal, expected_nano_sha256=post.digest(path))
                with self.assertRaisesRegex(ValueError, 'Undeclared proposal'):
                    post.inspect_nano(path, expected)
            before = [semantic_inventory([path]) for path in paths]
            annotation = post.combine_proposal_annotations(paths, expected, exposure_map)
            self.assertEqual([member['upstream_Nano_sha256'] for member in annotation['members']], authority_hashes)
            self.assertEqual(annotation['events'], 3)
            self.assertEqual(annotation['source_exposures'], exposures)
            after = [semantic_inventory([path]) for path in paths]
            for old, new in zip(before, after):
                old.pop('ShiftDecayProposalWeights'); new.pop('ShiftDecayProposalWeights')
                self.assertEqual(old, new)
            merged = Path(directory)/'merged.root'
            merger = ROOT.TFileMerger(False)
            merger.OutputFile(str(merged), 'RECREATE')
            for path in paths: merger.AddFile(str(path))
            self.assertTrue(merger.Merge())
            self.assertEqual(compare_all_branches(paths, merged)['events'], 3)
            _, sumw, sumw2 = post.inspect_nano(merged, expected, exposure_map)
            reference = [row['proposal_gen_weight'] for row in expected_rows]
            self.assertAlmostEqual(sumw, math.fsum(reference))
            self.assertAlmostEqual(sumw2, math.fsum(value*value for value in reference))
            self.assertEqual(post.histogram_weight_branch([bound]), 'shiftProposalGenWeight')
            metadata = post.exposure([dict(source['normalization_record'], stratum=source['stratum'],
                sigma_nd_pb=123.)], source['stratum'])
            self.assertEqual(metadata['full_source_sumw'], 2000)

    def test_repeated_original_is_rejected_before_metadata_working_copies_change(self):
        import run_shift_gen_to_nano as worker
        from shift_nano_integrity import semantic_inventory
        source, ledger = native_proposal_fixture(1)
        bound = bind_source(source, ledger)
        rows, exposures = selected_decay_proposals(bound)
        expected = {tuple(row['event_id']): row for row in rows}
        exposure_map = {(row['source_id'], row['original_source_sha256']): row for row in exposures}
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory)/f'input{i}.root' for i in (1, 2)]
            for path in paths:
                self.fixture(path, rows)
                worker.finalize_nano_weights(path, [dict(id=rows[0]['event_id'], weight=rows[0]['native_weight'])],
                                              [.4], (rows, exposures))
            before = [semantic_inventory([path]) for path in paths]
            with self.assertRaisesRegex(ValueError, 'Repeated original'):
                post.combine_proposal_annotations(paths, expected, exposure_map)
            self.assertEqual(before, [semantic_inventory([path]) for path in paths])


@unittest.skipUnless(importlib.util.find_spec('ROOT'), 'Requires native ROOT')
class GroupedHistogramPublicationTest(unittest.TestCase):
    def test_disjoint_groups_preserve_weighted_bins_and_exposure_and_reject_overlap(self):
        import ROOT,os,shutil
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);records=[];groups=[];selected=[]
            for index,weight in enumerate((2.,5.)):
                source=dict(source_id='source'+str(index),stratum='qcd_20toinf',
                    gen_sha256=str(index+1)*64,denominator_kind='fixed_source_trials',
                    full_source_trials=1000,sigma_nd_pb=123.)
                records.append(source)
                selected.append(dict(event_ids=[[index+1,1,1]],weights=[weight],selected_indices=[0],sampling_probabilities=[1.]))
                meta=post.exposure([source],'qcd_20toinf')
                meta.update(histogram_weight='native_gen_weight/(reco_inclusion_probability*response_sampling_probability)',
                    histogram_weight_branch='shiftResponseGenWeight',response_correction_application_count=1,
                    material_survival_correction_applied=False,complete_frozen_response_draws_only=True,
                    response_draw_authorities=[dict(source_id=source['source_id'])])
                path=root/('group'+str(index)+'.root');f=ROOT.TFile(str(path),'RECREATE')
                h=ROOT.TH1D('rawEventsCutFlow','',1,0,1);h.SetBinContent(1,1);h.Write()
                f.mkdir('event').cd();h=ROOT.TH1D('Event_nShiftMuon','',5,0,5);h.Sumw2();h.Fill(2,weight);h.Write()
                f.mkdir('dimuon').cd()
                for category in post.DIMUON_CATEGORIES:
                    for variable in ('mass','pt','logMass'):
                        h=ROOT.TH1D('ShiftDimuonVertex'+category+'_'+variable,'',10,0,10);h.Sumw2();h.Fill(3,weight);h.Write()
                f.cd();ROOT.TObjString(json.dumps(meta)).Write('ShiftFullGenNormalization');f.Close()
                groups.append(dict(source_ids=[source['source_id']],events=1,sumw=weight,sumw2=weight*weight,
                    histogram=dict(path=str(path),sha256=post.digest(path))))
            request=dict(campaign='test',stratum='qcd_20toinf',normalization_sources=records,
                selected_sources=selected,histogram_groups=groups,maximum_publication_bytes=67108864,
                snapshot_name='combined',sample_directory='/eos/user/j/jniedzie/shift_cmssw_support/test')
            path=root/'request.json';path.write_text(json.dumps(request));old=Path.cwd()
            def local_download(source,target,checksum=None):
                shutil.copyfile(source,target)
                if checksum:self.assertEqual(post.digest(target),checksum)
            def local_publish(path,directory):
                return dict(path=str(path),sha256=post.digest(path),bytes=Path(path).stat().st_size)
            try:
                os.chdir(root)
                with patch.object(post,'download',side_effect=local_download),patch.object(post,'publish',side_effect=local_publish):
                    post.aggregate_histogram_groups(path,request,50,60)
                    f=ROOT.TFile.Open('combined.root');h=f.Get('dimuon/ShiftDimuonVertexBoth-Both_mass')
                    self.assertEqual(h.GetEntries(),2);self.assertEqual(h.Integral(),7)
                    self.assertAlmostEqual(sum(h.GetBinError(i)**2 for i in range(h.GetNcells())),29)
                    meta=json.loads(str(f.Get('ShiftFullGenNormalization').GetString()))
                    self.assertEqual(meta['full_source_sumw'],2000);f.Close()
                    bad=dict(request,histogram_groups=[groups[0],groups[0]])
                    with self.assertRaisesRegex(ValueError,'Overlapping'):
                        post.aggregate_histogram_groups(path,bad,50,60)
            finally:os.chdir(old)


class GroupedWeightPolicyTest(unittest.TestCase):
    def test_ordinary_groups_keep_native_sampling_weights_without_response_correction(self):
        source=dict(source_id='ordinary',stratum='qcd_20toinf',gen_sha256='a'*64,
                    denominator_kind='fixed_source_trials',full_source_trials=1000,sigma_nd_pb=123.)
        meta=post.exposure([source],'qcd_20toinf')
        result=post.grouped_weight_metadata([meta,dict(meta)])
        self.assertEqual(result['histogram_weight_branch'],'shiftSamplingGenWeight')
        self.assertEqual(result['histogram_weight'],meta['histogram_weight'])
        self.assertNotIn('response_correction_application_count',result)
        for changed in [dict(response_correction_application_count=1),
                        dict(histogram_weight_branch='shiftResponseGenWeight'),
                        dict(response_draw_authorities=[{}]),
                        dict(material_survival_correction_applied=True)]:
            with self.subTest(changed=changed),self.assertRaisesRegex(ValueError,'second correction'):
                post.grouped_weight_metadata([dict(meta,**changed)])

    def test_mixed_or_unknown_group_policies_are_rejected(self):
        ordinary=dict(histogram_weight='native_gen_weight/reco_inclusion_probability',
                      no_second_filter_efficiency=True)
        response=dict(histogram_weight='native_gen_weight/(reco_inclusion_probability*response_sampling_probability)',
                      histogram_weight_branch='shiftResponseGenWeight',response_correction_application_count=1,
                      material_survival_correction_applied=False,complete_frozen_response_draws_only=True,
                      response_draw_authorities=[dict(source_id='one')])
        self.assertEqual(post.grouped_weight_metadata([response])['response_correction_application_count'],1)
        for values in [[],[ordinary,response],[dict(histogram_weight='invented')]]:
            with self.subTest(values=values),self.assertRaises(ValueError):
                post.grouped_weight_metadata(values)

if __name__ == '__main__':
    unittest.main()
