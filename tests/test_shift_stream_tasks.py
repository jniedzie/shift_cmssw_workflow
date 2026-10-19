# Code style: AI-code. AI-generated fixed-source task preparation regressions.
import json
import io
from pathlib import Path
import sys
import tempfile
import tarfile
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import prepare_shift_stream_tasks as module
from run_shift_stream_controller import load_tasks, save, sha


class FactoryTests(unittest.TestCase):
    def setUp(self):
        parent = SCRIPTS.parent / 'tmp'
        parent.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='20261016-stream-factory-', dir=parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ('tasks', 'cohorts', 'frozen'):
            (self.root/name).mkdir()
        worker = self.root/'frozen/worker.sh';worker.write_text('#!/bin/bash\nexit 0\n')
        archive = self.root/'frozen/frozen_tools.tar.gz';archive.write_bytes(b'test-only-tools')
        self.policy = dict(tag='stream', output_campaign='20261016_mu_enriched_stream_v1',
            worker=dict(executable=str(worker), executable_sha256=sha(worker),
                        tools_archive=str(archive), tools_sha256=sha(archive)),
            tools=dict(selection='@TOOLS@/scripts/shift_streaming_qcd_inputs.py',
                reco='@TOOLS@/scripts/run_shift_gen_to_nano.py', templates='@TOOLS@/templates',
                histograms='@TOOLS@/tea/utils/shift_streaming_histograms.py',
                histogrammer='@SCRATCH@/runtime/bin/shift_histogrammer',
                merger='@SCRATCH@/runtime/scripts/merge.py'),
            snapshot_eos_base='/eos/user/j/jniedzie/shift_cmssw_support/stream/snapshots',
            plot_output=str(self.root/'plots'), canary_source_ids=['production_qcd_0to1_0000'])
        self.source = dict(source_id='production_qcd_0to1_0000', stratum='qcd_0to1',
            gen='/eos/user/j/jniedzie/shift_cmssw/qcd_pThat0to1GeV/20261015_mu_enriched_v1/GEN/source/gen.root',
            gen_sha256='a'*64, event_ids=[[42,1,1], [42,1,2]], weights=[1.,1.],
            normalization=dict(schema='shift-stream-source-exposure-v1', source_id='production_qcd_0to1_0000',
                stratum='qcd_0to1', full_source_trials=400, sigma_nd_pb=123., gen_sha256='a'*64))
        self.selected = self.source | dict(selected_events=[
            dict(raw_index=0,event_id=[42,1,1],native_weight=1.,sampling_probability=.5)],
            selected_indices=[0], sampling_probabilities=[.5], sampling={'plan_sha256':'b'*64})
        self.slot = dict(source_id=self.source['source_id'], receipt='/unused/receipt', manifest='/unused/manifest')

    def source_step(self, ledger):
        with mock.patch.object(module, 'source_descriptor', return_value=self.source), \
                mock.patch.object(module, 'select_events', return_value=self.selected):
            module.prepare_source(self.root,self.policy,{},self.slot,3,ledger)

    def task(self, suffix):
        return json.loads((self.root/'tasks'/(self.source['source_id']+suffix+'.json')).read_text())

    def test_scan_is_separate_and_never_reconstructs_before_audit(self):
        self.source_step({})
        self.assertEqual(len(list((self.root/'tasks').glob('*.json'))),1)
        task=self.task('_scan');request=module.read(Path(task['working_directory'])/'request.json')
        self.assertEqual(request['argv'][2],'scan')
        self.assertEqual(request['status_fields'],{})
        self.assertTrue(task['operational_canary'])
        description=Path(task['submit_file']).read_text()
        self.assertIn('transfer_output_files = status.json,result.json,return_payload.tar.gz',description)
        self.assertNotIn('transfer_output_remaps',description)
        self.assertIn('request_memory = 2048',description)
        self.assertIn('+SendCredential = True', description)
        self.assertIn('periodic_release = False', description)
        self.assertNotIn('requirements = ', description)

    def test_new_workers_exclude_the_exact_reviewed_hosts_in_every_stage(self):
        hosts = ['b9p13p2979.cern.ch', 'b9p20p4677.cern.ch',
                 'b9p22p1144.cern.ch', 'b9p18p8103.cern.ch']
        policy = self.policy | {'excluded_worker_hosts': hosts}
        for stage in ('prepare', 'reco', 'histogram', 'merge'):
            payload = module.request(stage, 'source0', 'tea', ['test-only'], 'result.json', policy)
            task = module.make_task(self.root, policy, 'excluded_'+stage,
                                    stage, 'source0', payload, [])
            description = Path(task['submit_file']).read_text()
            requirement = next(line for line in description.splitlines()
                               if line.startswith('requirements = '))
            self.assertEqual(requirement, 'requirements = ' + ' && '.join(
                '(TARGET.Machine =!= '+json.dumps(host)+')' for host in hosts))
            self.assertEqual(module.read(Path(task['working_directory'])/'request.json'), payload)
            self.assertEqual(task['input_sha256'][policy['worker']['executable']],
                             policy['worker']['executable_sha256'])

    def test_invalid_or_duplicate_worker_hosts_fail_before_creating_inputs(self):
        invalid = [[], ['a.cern.ch']*2, ['a.cern.ch', 'A.cern.ch'],
                   ['outside.example.com'], ['cern.ch'], ['bad host.cern.ch'],
                   ['bad_host.cern.ch'], ['-bad.cern.ch'], ['bad.cern.ch;true'],
                   [123], None, 'a.cern.ch',
                   ['h'+str(index)+'.cern.ch' for index in range(9)]]
        for hosts in invalid:
            with self.subTest(hosts=hosts), self.assertRaisesRegex(ValueError, 'excluded_worker_hosts'):
                module.make_task(self.root, self.policy | {'excluded_worker_hosts': hosts},
                                 'invalid_hosts', 'histogram', 'source0', {}, [])
        self.assertFalse((self.root/'tasks/invalid_hosts.json').exists())
        self.assertFalse((self.root/'cohorts/source0').exists())

    def test_host_policy_does_not_rewrite_previously_frozen_tasks(self):
        payload = module.request('histogram', 'source0', 'tea', ['test-only'], 'result.json', self.policy)
        task = module.make_task(self.root, self.policy, 'already_frozen',
                                'histogram', 'source0', payload, [])
        paths = [self.root/'tasks/already_frozen.json', Path(task['submit_file']),
                 Path(task['working_directory'])/'request.json']
        before = {str(path):path.read_bytes() for path in paths}
        again = module.make_task(self.root,
            self.policy | {'excluded_worker_hosts': ['b9p18p8103.cern.ch']},
            'already_frozen', 'histogram', 'source0', payload, [])
        self.assertEqual(task, again)
        self.assertEqual(before, {str(path):path.read_bytes() for path in paths})

    def test_preparation_hashes_shared_immutable_runtime_once_but_each_scientific_input(self):
        checked = {}
        scientific = self.root/'scientific.json'
        scientific.write_text('{"event_ids":[[42,1,1]]}')
        with mock.patch.object(module, 'sha', wraps=module.sha) as checksum:
            for index in range(20):
                identity = 'batch_'+str(index)
                payload = module.request('reco', 'source0', 'cmssw', ['test-only'], 'report.json', self.policy)
                module.make_task(self.root, self.policy, identity, 'reco', 'source0', payload,
                                 [scientific], worker_checksums=checked)
        paths = [str(call.args[0]) for call in checksum.call_args_list]
        self.assertEqual(paths.count(self.policy['worker']['tools_archive']), 1)
        self.assertEqual(paths.count(self.policy['worker']['executable']), 1)
        self.assertEqual(paths.count(str(scientific)), 20)

    def test_preparation_cache_rejects_runtime_drift_and_changed_expected_pin(self):
        checked = {}
        archive = Path(self.policy['worker']['tools_archive'])
        module.immutable_worker_checksum(archive, checked)
        archive.write_bytes(b'changed-same-run')
        with self.assertRaisesRegex(ValueError, 'changed during task preparation'):
            module.immutable_worker_checksum(archive, checked)
        checked = {}
        payload = module.request('reco', 'source0', 'cmssw', ['test-only'], 'report.json', self.policy)
        changed = self.policy | {'worker': self.policy['worker'] | {'tools_sha256': 'a'*64}}
        with self.assertRaisesRegex(ValueError, 'Frozen tools archive changed'):
            module.make_task(self.root, changed, 'changed_pin', 'reco', 'source0', payload, [], worker_checksums=checked)

    def test_preparation_rejects_runtime_that_changes_during_hash(self):
        archive = Path(self.policy['worker']['tools_archive'])
        original = module.sha
        def change_after_read(path):
            value = original(path)
            archive.write_bytes(b'changed-while-hashing')
            return value
        with mock.patch.object(module, 'sha', side_effect=change_after_read), \
                self.assertRaisesRegex(ValueError, 'changed while hashing'):
            module.immutable_worker_checksum(archive, {})

    def test_histogram_stage_priority_prevents_starvation_while_reco_keeps_its_default(self):
        policy = self.policy | {'stage_worker_priorities': {'histogram': 50000000, 'merge': 50000000}}
        for stage, expected in (('reco', 200000), ('histogram', 50000000), ('merge', 50000000)):
            task = module.make_task(self.root, policy, 'priority_'+stage, stage, 'priority_source',
                                    module.request(stage, 'priority_source', 'tea', ['test-only'], 'result.json', policy), [])
            self.assertIn('priority = '+str(expected)+'\n', Path(task['submit_file']).read_text())
        for overrides in ({'histogram': True}, {'histogram': 2147483648}, {'reco': 50000000}):
            with self.subTest(overrides=overrides), self.assertRaisesRegex(ValueError, 'priorities'):
                module.make_task(self.root, self.policy | {'stage_worker_priorities': overrides},
                    'invalid_priority', 'histogram', 'priority_source', {}, [])

    def test_fixed_selection_creates_one_event_jobs_without_changing_native_weights(self):
        self.source_step({})
        task=self.task('_scan');save(task['result_path'],dict(complete=True,events=[]))
        self.source_step({self.source['source_id']+'_scan':{'phase':'complete'}})
        task=self.task('_reco_000');request=module.read(Path(task['working_directory'])/'request.json')
        self.assertEqual(request['argv'][request['argv'].index('--count')+1],'1')
        self.assertIn('--remove-consumed-intermediates',request['argv'])
        descriptor=module.read(self.root/'cohorts'/task['cohort_id']/'selected.json')
        self.assertEqual(descriptor['weights'],[1.,1.])
        self.assertEqual(descriptor['sampling_probabilities'],[.5])
        self.assertEqual(task['completion_checks']['selected_event_ids_sha256'],module.digest([[42,1,1]]))
        self.assertLess(230000000+(10000+1044*32+31)*100,900000000)

    def test_bulk_batches_cover_each_original_once_and_keep_disjoint_source_jobs(self):
        selected = self.selected | {'selected_events': [
            dict(raw_index=index, event_id=[42, 1, index + 1], native_weight=1.,
                 sampling_probability=.75) for index in range(103)]}
        policy = self.policy | {'reco_events_per_job': 20, 'reco_job_stride': 1024,
                                'reco_job_offset': 200000}
        batches = module.reco_batches(selected, policy, 3)
        self.assertEqual([batch['count'] for batch in batches], [20]*5 + [3])
        self.assertEqual([batch['skip'] for batch in batches], [0, 20, 40, 60, 80, 100])
        self.assertEqual([identity for batch in batches for identity in batch['event_ids']],
                         [row['event_id'] for row in selected['selected_events']])
        self.assertEqual([batch['job'] for batch in batches], list(range(203072, 203078)))
        following = module.reco_batches(selected, policy, 4)
        self.assertFalse(set(batch['job'] for batch in batches) & set(batch['job'] for batch in following))

    def test_bulk_factory_records_exact_batch_ids_counts_weights_and_priority(self):
        self.policy.update(reco_events_per_job=20, reco_job_stride=1024, reco_job_offset=200000,
                           prioritize_GEN_strata=True)
        self.slot.update(stratum='qcd_0to1', request={'chunk_index': 0})
        identities = [[42, 1, index + 1] for index in range(23)]
        self.source.update(event_ids=identities, weights=[1.]*23)
        self.selected.update(event_ids=identities, weights=[1.]*23,
            selected_indices=list(range(23)), sampling_probabilities=[.5]*23,
            selected_events=[dict(raw_index=index, event_id=identity, native_weight=1.,
                sampling_probability=.5, feature_stratum='tail' if index < 20 else 'remainder')
                for index, identity in enumerate(identities)])
        self.source_step({})
        scan = self.task('_scan');save(scan['result_path'], {'complete': True})
        self.source_step({self.source['source_id']+'_scan': {'phase': 'complete'}})
        first, last = self.task('_reco_000'), self.task('_reco_001')
        self.assertEqual(first['completion_checks']['events'], 20)
        self.assertEqual(last['completion_checks']['events'], 3)
        self.assertEqual(first['completion_checks']['selected_event_ids_sha256'], module.digest(identities[:20]))
        self.assertEqual(last['completion_checks']['selected_event_ids_sha256'], module.digest(identities[20:]))
        request = module.read(Path(last['working_directory'])/'request.json')
        self.assertEqual(request['argv'][request['argv'].index('--skip')+1], '20')
        self.assertEqual(request['argv'][request['argv'].index('--count')+1], '3')
        self.assertGreater(first['priority'], last['priority'])
        self.assertEqual(module.read(self.root/'cohorts'/first['cohort_id']/'selected.json')['weights'], [1.]*23)

    def test_bulk_job_stride_and_random_seed_bounds_fail_before_task_creation(self):
        selected = self.selected | {'selected_events': self.selected['selected_events']*33}
        with self.assertRaisesRegex(ValueError, 'job stride'):
            module.reco_batches(selected, self.policy, 0)
        for key, value in (('reco_events_per_job', 0), ('reco_events_per_job', 51),
                           ('reco_events_per_job', True), ('reco_job_stride', 0),
                           ('reco_job_offset', -1), ('reco_job_offset', 6700000)):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                module.reco_batches(self.selected, self.policy | {key: value}, 0)

    def test_stage_publication_bounds_keep_reco_small_and_source_merges_large(self):
        policy = self.policy | {'worker_publication_bytes': 128*1024**2,
            'stage_publication_bounds': {'histogram': 512*1024**2, 'merge': 512*1024**2}}
        self.assertEqual(module.publication_bound(policy, 'reco'), 128*1024**2)
        self.assertEqual(module.publication_bound(policy, 'histogram'), 512*1024**2)
        self.assertEqual(module.publication_bound(policy, 'merge'), 512*1024**2)
        for invalid in (True, 65536, 513*1024**2):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                module.publication_bound(policy | {'stage_publication_bounds': {'histogram': invalid}}, 'histogram')

    def test_batching_is_not_an_allowed_rollover_change(self):
        save(self.root/'factory_policy.json', self.policy)
        save(self.root/'source_plan.json', {})
        replacement = self.root/'replacement_policy.json'
        for key, value in (('reco_events_per_job', 20), ('reco_job_stride', 1024),
                           ('reco_job_offset', 200000), ('prioritize_GEN_strata', True)):
            with self.subTest(key=key):
                save(replacement, self.policy | {key: value})
                with self.assertRaisesRegex(ValueError, 'undeclared detector, source or output setting'):
                    module.prepare(self.root, replacement)

    def test_first_scan_must_return_before_other_sources_are_prepared(self):
        slots=[]
        for index,stratum in enumerate(module.STRATA):
            receipt=self.root/('receipt'+str(index)+'.json');receipt.write_text('{}')
            slots.append(dict(source_id=stratum+'_0',stratum=stratum,request={'chunk_index':0},receipt=str(receipt)))
        module.unchanged(self.root/'source_plan.json',{'source_slots':slots,'plan_sha256':'a'*64})
        policy=self.policy|{'maximum_open_sources':6,'maximum_open_sources_before_first_scan':1}
        module.unchanged(self.root/'factory_policy.json',policy)
        with mock.patch.object(module,'validate_plan'),mock.patch.object(module,'prepare_source') as prepare, \
                mock.patch.object(module,'prepare_snapshots'),mock.patch.object(module,'publish_returned_plots'), \
                mock.patch.object(module,'complete_operational_gate'):
            checkpoint=module.prepare(self.root)
            self.assertEqual(prepare.call_count,1)
            self.assertEqual(checkpoint['open_sources'],1)
            save(self.root/'ledger.json',{slots[0]['source_id']+'_scan':{'phase':'complete'}})
            prepare.reset_mock()
            checkpoint=module.prepare(self.root)
            self.assertEqual(prepare.call_count,6)
            self.assertEqual(checkpoint['open_sources'],6)

    def test_operational_canary_can_disable_rate_snapshots_without_changing_defaults(self):
        save(self.root/'source_plan.json', {'source_slots': [], 'plan_sha256': 'a'*64})
        for enabled in (False, True):
            save(self.root/'factory_policy.json', self.policy | {'produce_snapshots': enabled})
            with mock.patch.object(module, 'validate_plan'), mock.patch.object(module, 'prepare_snapshots') as snapshots, \
                    mock.patch.object(module, 'publish_returned_plots'), \
                    mock.patch.object(module, 'complete_operational_gate'):
                module.prepare(self.root)
                self.assertEqual(snapshots.call_count, int(enabled))

    def test_quarantined_source_frees_capacity_without_repreparation_or_state_changes(self):
        self.source_step({})
        scan_id = self.source['source_id']+'_scan'
        scan = self.task('_scan')
        save(scan['result_path'], {'complete': True})
        ledger = {scan_id: {'phase': 'complete'}}
        self.source_step(ledger)
        failed = self.task('_reco_000')
        failure_path = Path(failed['working_directory'])/'result.json'
        save(failure_path, {'complete': False, 'error': 'detector timeout'})
        ledger[failed['task_id']] = dict(phase='failed', failure_scope='cohort',
            nonce='original-nonce', job=[123, 0], failed_result_path=str(failure_path),
            failed_result_sha256=sha(failure_path))
        receipt = self.root/'receipt.json'
        save(receipt, {})
        slots = [self.slot | dict(stratum='qcd_0to1', request={'chunk_index': 0}, receipt=str(receipt)),
                 dict(source_id='next_source', stratum='qcd_1to2',
                      request={'chunk_index': 0}, receipt=str(receipt))]
        save(self.root/'factory_policy.json', self.policy | {'maximum_open_sources': 1})
        save(self.root/'source_plan.json', {'source_slots': slots, 'plan_sha256': 'a'*64})
        save(self.root/'admission/pre_reservations.json', {failed['task_id']: {'at_epoch': 999}})
        for scope, expected in (('cohort', 'next_source'), ('global', self.source['source_id']),
                                (None, self.source['source_id'])):
            ledger[failed['task_id']]['failure_scope'] = scope
            save(self.root/'ledger.json', ledger)
            paths = [path for path in self.root.rglob('*') if path.is_file()]
            before = {path: sha(path) for path in paths}
            with self.subTest(scope=scope), mock.patch.object(module, 'validate_plan'), \
                    mock.patch.object(module, 'prepare_source') as prepare, \
                    mock.patch.object(module, 'publish_returned_plots'), \
                    mock.patch.object(module, 'complete_operational_gate'):
                for _ in range(2):
                    prepare.reset_mock()
                    checkpoint = module.prepare(self.root)
                    self.assertEqual([call.args[3]['source_id'] for call in prepare.call_args_list], [expected])
                    self.assertEqual(checkpoint['open_sources'], 1)
                    self.assertEqual(checkpoint['planned_sources'], 2)
                    self.assertEqual({path: sha(path) for path in paths}, before)
                    self.assertFalse(any(self.root.glob('tasks/snapshot_*.json')))

    def test_failed_source_does_not_block_ready_sources_in_other_bins(self):
        slots = [dict(source_id=stratum+'_0', stratum=stratum, request={'chunk_index': 0})
                 for stratum in module.STRATA]
        ledger = {slot['source_id']+'_histogram': {'phase': 'complete'} for slot in slots}
        failed_source = slots[0]['source_id']
        ledger[failed_source+'_histogram'] = {'phase': 'waiting'}
        ledger[failed_source+'_reco_000'] = {'phase': 'failed', 'failure_scope': 'cohort'}
        for slot in slots[1:]:
            identity = slot['source_id']+'_histogram'
            result = self.root/(identity+'_result.json')
            save(result, {'complete': True, 'nano_audit': {'events': 100}})
            save(self.root/'tasks'/(identity+'.json'), {'result_path': str(result)})
        self.policy['plot_output'] = str(self.root/'plots')
        module.prepare_snapshots(self.root, self.policy, {'source_slots': slots}, ledger)
        task = module.read(next(self.root.glob('tasks/snapshot_ready_*.json')))
        self.assertEqual(len(task['depends_on']), 5)
        self.assertNotIn(failed_source+'_histogram', task['depends_on'])
        self.assertEqual(ledger[failed_source+'_histogram']['phase'], 'waiting')

    def test_merge_waits_for_meaningful_growth_but_not_more_than_six_hours(self):
        slot = dict(source_id='qcd_0to1_0', stratum='qcd_0to1', request={'chunk_index': 0})
        identity = slot['source_id']+'_histogram'
        result = self.root/'source_result.json'
        save(result, {'complete': True, 'nano_audit': {'events': 1100}})
        save(self.root/'tasks'/(identity+'.json'), {'result_path': str(result)})
        latest = Path(self.policy['plot_output'])/'latest_ready.json'
        latest.parent.mkdir()
        save(latest, dict(closed_cohort_ids=['older_source'], events_by_stratum={'qcd_0to1': 1000},
                         merged_at_epoch=10000))
        ledger = {identity: {'phase': 'complete'}}
        with mock.patch.object(module.time, 'time', return_value=13600):
            module.prepare_snapshots(self.root, self.policy, {'source_slots': [slot]}, ledger)
        self.assertFalse(list(self.root.glob('tasks/snapshot_ready_*.json')))
        with mock.patch.object(module.time, 'time', return_value=32000):
            module.prepare_snapshots(self.root, self.policy, {'source_slots': [slot]}, ledger)
        task = module.read(next(self.root.glob('tasks/snapshot_ready_*.json')))
        self.assertEqual(task['depends_on'], [identity])
        ledger[task['task_id']] = {'phase': 'submitted'}
        with mock.patch.object(module.time, 'time', return_value=64000):
            module.prepare_snapshots(self.root, self.policy, {'source_slots': [slot]}, ledger)
        self.assertEqual(len(list(self.root.glob('tasks/snapshot_ready_*.json'))), 1)

    def test_snapshot_pins_reference_inputs_and_uses_the_reviewed_publication_bound(self):
        self.policy['worker_publication_bytes'] = 128*1024**2
        references = self.root/'reference_histograms.json'
        save(references, {'schema': 'shift-sm-reference-histograms-v1', 'samples': []})
        self.policy.update(reference_histogram_inputs=str(references),
                           reference_histogram_inputs_sha256=sha(references))
        slots = [dict(source_id=stratum+'_0', stratum=stratum, request={'chunk_index': 0})
                 for stratum in module.STRATA]
        ledger = {}
        for slot in slots:
            identity = slot['source_id']+'_histogram'
            result = self.root/(identity+'_result.json')
            save(result, {'complete': True})
            save(self.root/'tasks'/(identity+'.json'), {'result_path': str(result)})
            ledger[identity] = {'phase': 'complete'}
        module.prepare_snapshots(self.root, self.policy, {'source_slots': slots}, ledger)
        task = module.read(next(self.root.glob('tasks/snapshot_ready_*.json')))
        request = module.read(Path(task['working_directory'])/'request.json')
        self.assertEqual(request['argv'][request['argv'].index('--reference-inputs')+1], references.name)
        self.assertEqual(task['input_sha256'][str(references)], sha(references))
        inventory = module.read(Path(task['working_directory']).parent/'snapshot.json')
        self.assertEqual(inventory['maximum_publication_bytes'], 128*1024**2)

    def test_oversized_selection_cannot_overlap_next_source_job_ids(self):
        self.source_step({})
        scan = self.task('_scan')
        save(scan['result_path'], dict(complete=True, events=[]))
        self.selected['selected_events'] = [
            dict(raw_index=index, event_id=[42, 1, index + 1],
                 native_weight=1., sampling_probability=1.)
            for index in range(33)]
        with self.assertRaisesRegex(ValueError, 'frozen RECO job stride'):
            self.source_step({self.source['source_id']+'_scan': {'phase': 'complete'}})
        self.assertEqual(list((self.root/'tasks').glob('*_reco_*.json')), [])

    def test_histogram_waits_for_every_selected_original_and_preserves_full_trials(self):
        runtime = '/cvmfs/cms.cern.ch/el9_amd64_gcc13/cms/cmssw/CMSSW_17_0_0_pre4'
        self.policy['tools']['nano_integrity_runtime'] = runtime
        self.source_step({});scan=self.task('_scan');save(scan['result_path'],dict(complete=True,events=[]))
        ledger={self.source['source_id']+'_scan':{'phase':'complete'}}
        self.source_step(ledger)
        self.assertFalse((self.root/'tasks'/(self.source['source_id']+'_histogram.json')).exists())
        reco=self.task('_reco_000')
        save(reco['status_path'],dict(nano_path='/eos/user/j/jniedzie/example/nano.root',nano_sha256='c'*64,events=1))
        ledger[self.source['source_id']+'_reco_000']={'phase':'complete'}
        self.source_step(ledger)
        histogram=self.task('_histogram');request=module.read(Path(histogram['working_directory'])/'request.json')
        self.assertIn('--retire-consumed-nano',request['argv'])
        self.assertEqual(request['argv'][request['argv'].index('--nano-integrity-runtime')+1], runtime)
        cohort=module.read(self.root/'cohorts'/histogram['cohort_id']/'nano_cohort.json')
        self.assertEqual(cohort['normalization']['full_source_trials'],400)
        self.assertEqual(cohort['selected_events'][0]['sampling_probability'],.5)
        self.assertEqual(cohort['expected_reco_job_ids'],[10096])

    def test_ready_merge_includes_finished_later_sources_while_earlier_sources_wait(self):
        slots=[]
        for stratum in module.STRATA:
            for index in (0,1):
                slots.append(dict(source_id=stratum+'_'+str(index),stratum=stratum,request={'chunk_index':index}))
        ledger={slot['source_id']+'_histogram':{'phase':'complete'} for slot in slots
                if slot['request']['chunk_index']==1}
        self.policy['plot_output'] = str(self.root/'plots')
        for identity in ledger:
            result = self.root/(identity+'_result.json')
            save(result, {'complete': True, 'nano_audit': {'events': 100}})
            save(self.root/'tasks'/(identity+'.json'), {'result_path': str(result)})
        module.prepare_snapshots(self.root,self.policy,{'source_slots':slots},ledger)
        task = module.read(next(self.root.glob('tasks/snapshot_ready_*.json')))
        self.assertEqual(set(task['depends_on']), set(ledger))

    def test_immutable_prepared_values_cannot_be_overwritten(self):
        path=self.root/'input.json';module.unchanged(path,{'a':1})
        module.unchanged(path,{'a':1})
        with self.assertRaisesRegex(ValueError,'changed'):module.unchanged(path,{'a':2})

    def test_histogram_archive_override_keeps_scan_and_reco_archives_unchanged(self):
        overlay = self.root / 'corrected'
        overlay.mkdir()
        archive = overlay / 'frozen_tools.tar.gz'
        archive.write_bytes(b'corrected histogram runtime only')
        self.policy['histogram_worker'] = self.policy['worker'] | {
            'tools_archive': str(archive), 'tools_sha256': sha(archive)}
        for stage in ('prepare', 'reco', 'histogram', 'merge'):
            payload = module.request(stage, 'source0', 'tea', ['python3', 'tool.py'], 'receipt.json', self.policy)
            task = module.make_task(self.root, self.policy, 'source0_' + stage, stage, 'source0', payload, [])
            pins = task['input_sha256']
            expected = str(archive) if stage in ('histogram', 'merge') else self.policy['worker']['tools_archive']
            self.assertIn(expected, pins)
            self.assertEqual(Path(task['submit_file']).read_text().count('frozen_tools.tar.gz'), 1)

    def test_replacement_factory_policy_cannot_change_selection_or_reco(self):
        save(self.root / 'factory_policy.json', self.policy)
        save(self.root / 'source_plan.json', {})
        replacement = self.root / 'replacement_policy.json'
        for key, value in (('output_campaign', 'changed'), ('worker', {}), ('canary_source_ids', [])):
            with self.subTest(key=key):
                save(replacement, self.policy | {key: value})
                with self.assertRaisesRegex(ValueError, 'undeclared detector, source or output setting'):
                    module.prepare(self.root, replacement)

    def test_open_source_rollover_preserves_plan_and_balanced_source_order(self):
        slots = []
        for index in range(3):
            for stratum in module.STRATA:
                receipt = self.root/(stratum+'_'+str(index)+'.json')
                receipt.write_text('{}')
                slots.append(dict(source_id=receipt.stem, stratum=stratum,
                    request={'chunk_index': index}, receipt=str(receipt)))
        save(self.root/'factory_policy.json', self.policy | {'maximum_open_sources': 6})
        save(self.root/'source_plan.json', {'source_slots': slots, 'plan_sha256': 'a'*64})
        save(self.root/'ledger.json', {slots[0]['source_id']+'_scan': {'phase': 'complete'}})
        pins = {path: sha(path) for path in (
            self.root/'factory_policy.json', self.root/'source_plan.json', self.root/'ledger.json')}
        replacement = self.root/'capacity_policy.json'
        save(replacement, self.policy | {'maximum_open_sources': 12})
        with mock.patch.object(module, 'validate_plan'), mock.patch.object(module, 'prepare_source') as prepare, \
                mock.patch.object(module, 'prepare_snapshots'), mock.patch.object(module, 'publish_returned_plots'), \
                mock.patch.object(module, 'complete_operational_gate'):
            result = module.prepare(self.root, replacement)
        self.assertEqual(result['open_sources'], 12)
        self.assertEqual([call.args[3]['stratum'] for call in prepare.call_args_list],
                         list(module.STRATA)*2)
        self.assertEqual({path: sha(path) for path in pins}, pins)
        for invalid in (0, -1, True, '300'):
            save(replacement, self.policy | {'maximum_open_sources': invalid})
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, 'positive integer'):
                module.prepare(self.root, replacement)

    def plot_task(self, member_name='histograms/plots/raw/mass.pdf'):
        self.policy['plot_output']=str(self.root/'project/plots/sm_streaming')
        payload=module.request('histogram','source0','tea',['python3','tool.py'],'receipt.json',self.policy)
        payload['return_files']=['histograms/plots']
        task=module.make_task(self.root,self.policy,'source0_histogram','histogram','source0',payload,[])
        archive_path=Path(task['working_directory'])/'return_payload.tar.gz'
        data=b'%PDF-1.4\nsynthetic unit-test reference\n'
        with tarfile.open(archive_path,'w:gz') as archive:
            info=tarfile.TarInfo(member_name);info.size=len(data)
            archive.addfile(info,io.BytesIO(data))
        save(task['status_path'],{'return_payload_sha256':sha(archive_path)})
        return task,archive_path,{'source0_histogram':{'phase':'complete'}}

    def test_plot_publication_checks_hash_then_removes_duplicate_archive(self):
        task,archive_path,ledger=self.plot_task()
        module.publish_returned_plots(self.root,self.policy,ledger)
        self.assertTrue((self.root/'project/plots/sm_streaming/source0/raw/mass.pdf').exists())
        self.assertFalse(archive_path.exists())
        self.assertTrue((Path(task['working_directory'])/'plots_published.json').exists())
        module.publish_returned_plots(self.root,self.policy,ledger)

    def test_plot_archive_traversal_is_rejected(self):
        _,archive_path,ledger=self.plot_task('histograms/plots/../../outside.pdf')
        with self.assertRaisesRegex(ValueError,'unsafe entry'):
            module.publish_returned_plots(self.root,self.policy,ledger)
        self.assertTrue(archive_path.exists())

    def test_plot_archive_checksum_mismatch_is_rejected(self):
        _,archive_path,ledger=self.plot_task()
        with archive_path.open('ab') as stream:stream.write(b'changed')
        with self.assertRaisesRegex(ValueError,'differs'):
            module.publish_returned_plots(self.root,self.policy,ledger)


    def snapshot_plot_task(self, count):
        self.policy['plot_output'] = str(self.root/'project/plots/sm_streaming')
        identity = 'snapshot_' + str(count)
        payload = module.request('merge', identity, 'tea', ['python3', 'tool.py'], 'receipt.json', self.policy)
        payload['return_files'] = ['snapshot/plots']
        task = module.make_task(self.root, self.policy, identity, 'merge', identity, payload, [])
        archive_path = Path(task['working_directory'])/'return_payload.tar.gz'
        data = b'%PDF-1.4\nsynthetic snapshot\n'
        with tarfile.open(archive_path, 'w:gz') as archive:
            info = tarfile.TarInfo('snapshot/plots/rate/mass.pdf')
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
        save(task['status_path'], {'return_payload_sha256': sha(archive_path)})
        record = dict(schema='shift-streaming-plot-inputs-v1',
            samples=[dict(stratum='qcd_0to1', path='/eos/user/test/hist.root', raw_path='/eos/user/test/raw.root')],
            snapshot_id=identity, partial_production=True,
            reference_samples=[dict(stratum='dy_20to-1', path='/eos/user/test/dy.root')])
        contents = (json.dumps(record, indent=2) + '\n').encode()
        save(task['result_path'], dict(complete=True, snapshot_id=identity,
            closed_cohort_ids=list(range(count)), samples=record['samples'], plot_inputs_record=record,
            plot_inputs=dict(path='/eos/user/test/plot_inputs.json', bytes=len(contents),
                             sha256=module.hashlib.sha256(contents).hexdigest())))
        return task, {identity: dict(phase='complete')}

    def test_snapshot_publishes_checked_inputs_and_keeps_newest_ready_alias(self):
        task, ledger = self.snapshot_plot_task(2)
        module.publish_returned_plots(self.root, self.policy, ledger)
        base = Path(self.policy['plot_output'])
        self.assertTrue((base/'snapshot_2/plot_inputs.json').is_file())
        self.assertEqual(module.read(base/'latest_ready.json')['snapshot_cohort_count'], 2)
        older, older_ledger = self.snapshot_plot_task(1)
        module.publish_returned_plots(self.root, self.policy, older_ledger)
        latest = module.read(base/'latest_ready.json')
        self.assertEqual(latest['snapshot_id'], 'snapshot_2')
        self.assertEqual(latest['reference_samples'][0]['stratum'], 'dy_20to-1')

    def test_snapshot_alias_requires_the_worker_plot_input_checksum(self):
        task, ledger = self.snapshot_plot_task(1)
        result = module.read(task['result_path'])
        result['plot_inputs']['sha256'] = '0' * 64
        save(task['result_path'], result)
        with self.assertRaisesRegex(ValueError, 'worker checksum'):
            module.publish_returned_plots(self.root, self.policy, ledger)
        self.assertFalse((Path(self.policy['plot_output'])/'latest_ready.json').exists())

    def test_first_new_snapshot_replaces_explicit_old_production_seed(self):
        task, ledger = self.snapshot_plot_task(1)
        base = Path(self.policy['plot_output'])
        base.mkdir(parents=True)
        save(base/'latest_ready.json', dict(snapshot_cohort_count=36,
            seed_from_previous_production=True, snapshot_id='old_prefix6'))
        module.publish_returned_plots(self.root, self.policy, ledger)
        latest = module.read(base/'latest_ready.json')
        self.assertEqual(latest['snapshot_id'], 'snapshot_1')
        self.assertEqual(latest['snapshot_cohort_count'], 1)
        self.assertNotIn('seed_from_previous_production', latest)


if __name__ == '__main__':
    unittest.main()
