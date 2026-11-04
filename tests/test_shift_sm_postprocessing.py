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
from run_shift_sm_postprocessing_controller import closed_sources, meaningful_update, snapshot
from run_shift_smart_sm_controller import postprocessing_charge
from run_shift_smart_sm_source import accepted_class_authority
import run_shift_smart_sm_controller as main_controller
import run_shift_sm_postprocessing as post
from test_shift_production_selection import native_proposal_fixture
from shift_production_selection import bind_source, selected_decay_proposals, proposal_source_exposure
from run_shift_sm_postprocessing_controller import compact_source, response_closed_source


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
        global_pin = self.write('global.json',dict(draw_frozen=True,
            comparison_kind='disjoint-source-smart-versus-random-v1',
            source_assignments=dict(response=arm,other=arm),population_events=6,
            selected_events=4,selected=global_rows,strata=cells))
        local = dict(self.plan,selected=selected,strata=[],global_comparison_plan=global_pin,
                     source_projection_does_not_redraw=True)
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
        plan=self.write(self.root/'plan.json',dict(sources=[dict(source_id='source')]))
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
        with patch.object(monitor,'native_collector_closure',return_value=None):
            self.assertEqual(dict(controller.response_source_cache(self.config,self.state,[])),{})
        self.assertEqual(self.state['response_sources'],{})

    def test_changed_owner_and_failed_native_proof_stop_discovery(self):
        import run_shift_sm_postprocessing_controller as controller
        import monitor_shift_sm_candidate_search as monitor
        with patch.object(monitor,'native_collector_closure',side_effect=ValueError('wrong native DAG owner')):
            with self.assertRaisesRegex(ValueError,'wrong native DAG owner'):
                controller.response_source_cache(self.config,self.state,[])
        self.discover()
        with patch.object(monitor,'source_owner',return_value=dict(cluster_id=99)):
            with self.assertRaisesRegex(ValueError,'native owner changed'):
                controller.response_source_cache(self.config,self.state,[])


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


class PublisherQuotaTest(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)
        self.parent_config=self.root/'quota_config.json'
        self.parent_config.write_text(json.dumps(dict(worker_template='queue 1\n')))
        self.config=dict(campaign='night',parent_state=str(self.root/'quota'),parent_config=str(self.parent_config))

    def test_quota_uses_normal_native_stage_and_does_not_duplicate_pending_worker(self):
        import run_shift_sm_postprocessing_controller as controller
        import run_shift_smart_sm_controller as native
        task=dict(kind='quota',phase='ready',directory=str(self.root/'job'),request_sha256='a'*64)
        with patch.object(native,'make_task',return_value=task) as make, \
             patch.object(controller,'run_condor',return_value='123.0 - 123.0\n') as submit:
            controller.refresh_quota(self.root,self.config,[])
            request=make.call_args.args[3]
            self.assertEqual(request['stage'],'smart_sm_quota')
            self.assertEqual(request['reservation_bytes'],0)
            self.assertEqual(submit.call_count,1)
            with patch.object(native,'observe'):
                controller.refresh_quota(self.root,self.config,[])
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


if __name__ == '__main__':
    unittest.main()
