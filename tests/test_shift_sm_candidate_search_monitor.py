# Code style: AI-code.
"""Check aggregate progress, lost transitions and native terminal accounting."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import monitor_shift_sm_candidate_search as monitor
from shift_condor_native import NativeCondorError


def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value))


class CandidateSearchMonitorTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.sid = 'supply_qcd_20toinf_00004'
        self.preparation = self.root/'preparation'/'sources'/self.sid
        self.reconstruction = self.root/'reconstruction'/self.sid
        self.handoff = self.root/'handoff'/self.sid
        self.plan = self.root/'plan.json'
        save(self.plan,dict(sources=[dict(source_id=self.sid,directory=str(self.preparation),
            request_sha256='b'*64)]))
        self.managers = [dict(name='preparation',cluster_id=101,directory=str(self.root/'preparation'),
                event_log=str(self.root/'preparation'/'manager.log')),
            dict(name='handoff',cluster_id=102,directory=str(self.root/'handoff'),
                event_log=str(self.root/'handoff'/'manager.log'))]
        self.config = dict(schema=monitor.SCHEMA,root_directory=str(self.root),
            working_directory=str(self.root),owner='jniedzie',managers=self.managers,
            plan=dict(path=str(self.plan),sha256=monitor.digest(self.plan)),
            transition_grace_seconds=60,maximum_wall_seconds=86400,
            state_path=str(self.root/'state.json'),heartbeat_path=str(self.root/'heartbeat.json'),
            failure_path=str(self.root/'failure.json'))

    def tearDown(self):
        self.temporary.cleanup()

    def ad(self,cluster,iwd,status=2,universe=7,parent=None):
        result = dict(ClusterId=cluster,ProcId=0,Owner='jniedzie',Iwd=str(iwd),
            JobStatus=status,JobUniverse=universe)
        if parent is not None:
            result['DAGManJobId'] = parent
        return result

    def prepared(self):
        save(self.preparation/'result.json',dict(complete=True,exit_code=0,request_sha256='b'*64))

    def collected(self):
        self.prepared()
        save(self.reconstruction/'prepared.json',dict(benchmark_plan=dict(sha256='a'*64),
            detector_jobs=[{},{}]))
        save(self.reconstruction/'submission.json',dict(manager=103))
        save(self.reconstruction/'jobs/collect/result.json',dict(complete=True,exit_code=0,
            complete_frozen_draw_collected=True,benchmark_plan_sha256='a'*64,events=2))
        save(self.reconstruction/'watchdog/heartbeat.json',dict(terminal=True,checked_epoch=1000,
            problems=[],manager_terminal=dict(ExitCode=0,ExitBySignal=False,DAG_Status=0,DAG_NodesFailed=0)))

    def close_managers(self):
        for row in self.managers:
            path = Path(row['event_log'])
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text('005 ('+str(row['cluster_id'])+'.000.000) 10/29 12:00:00 Job terminated.\n'
                '    (1) Normal termination (return value 0)\n...\n')

    def test_single_queue_read_and_no_false_failure_while_preparation_is_queued(self):
        ads = [self.ad(101,self.root/'preparation'),self.ad(102,self.root/'handoff'),
            self.ad(103,self.preparation,1,5,101),
            self.ad(500,str(self.root)+'_different',5,5)]
        query = Mock(return_value=ads)
        state = {}
        first = monitor.observe(self.config,state,now=1000,query_jobs=query)
        last = monitor.observe(self.config,state,now=9000,query_jobs=query)
        self.assertEqual(query.call_count,2)
        self.assertEqual(first['phase'],'running')
        self.assertEqual(last['problems'],[])
        self.assertEqual(last['counts']['expected_sources'],1)
        self.assertEqual(last['counts']['queue_jobs'],3)
        self.assertEqual(last['counts']['queued_science_workers'],1)
        self.assertIsNone(last['sources'][0]['collected_events'])

    def test_held_job_and_source_failure_preserved_without_job_mutation(self):
        save(self.preparation/'result.json',dict(complete=False,exit_code=1,error='kept failure'))
        ad = self.ad(103,self.preparation,5,5,101)
        ad['HoldReason'] = 'Payload failed'
        result = monitor.observe(self.config,{},now=1000,query_jobs=Mock(return_value=[ad]))
        self.assertEqual(result['phase'],'attention_required')
        self.assertTrue(any('Held job 103.0' in row for row in result['problems']))
        self.assertTrue(any('GEN preparation failed' in row for row in result['problems']))
        self.assertFalse(result['job_mutations'])

    def test_missing_handoff_detected_after_grace(self):
        self.prepared()
        state = {}
        query = Mock(return_value=[self.ad(101,self.root/'preparation'),self.ad(102,self.root/'handoff')])
        first = monitor.observe(self.config,state,now=1000,query_jobs=query)
        last = monitor.observe(self.config,state,now=1061,query_jobs=query)
        self.assertEqual(first['problems'],[])
        self.assertTrue(any('no downstream handoff' in row for row in last['problems']))

    def test_global_draw_wait_does_not_hide_a_later_lost_handoff(self):
        self.prepared()
        completion = self.root/'draw/complete.json'
        self.config['global_draw_completion_path'] = str(completion)
        ads = [self.ad(101,self.root/'preparation'),self.ad(102,self.root/'handoff'),
               self.ad(104,self.root/'preparation/sources/another_source',1,5,101)]
        state = {}
        query = Mock(return_value=ads)
        monitor.observe(self.config,state,now=1000,query_jobs=query)
        pending = monitor.observe(self.config,state,now=10000,query_jobs=query)
        self.assertTrue(pending['global_draw_pending'])
        self.assertFalse(any('no downstream handoff' in row for row in pending['problems']))
        save(completion,dict(complete=True))
        opened = monitor.observe(self.config,state,now=10010,query_jobs=query)
        self.assertFalse(opened['global_draw_pending'])
        self.assertFalse(any('no downstream handoff' in row for row in opened['problems']))
        lost = monitor.observe(self.config,state,now=10071,query_jobs=query)
        self.assertTrue(any('no downstream handoff' in row for row in lost['problems']))

    def test_failed_global_draw_marker_is_not_successful_admission(self):
        completion = self.root/'draw/complete.json'
        self.config['global_draw_completion_path'] = str(completion)
        save(completion,dict(complete=False))
        with self.assertRaisesRegex(ValueError, 'not successful'):
            monitor.observe(self.config,{},query_jobs=Mock(return_value=[]))

    def test_complete_requires_collector_native_manager_closure_and_no_science_workers(self):
        self.collected()
        self.close_managers()
        state = {}
        result = monitor.observe(self.config,state,now=1000,query_jobs=Mock(return_value=[]))
        self.assertTrue(result['terminal'])
        self.assertEqual(result['counts']['collected_events'],2)
        self.assertFalse(result['physics_rates_measured'])
        active = self.ad(104,self.reconstruction/'jobs/det_0000',2,5,103)
        blocked = monitor.observe(self.config,state,now=1010,query_jobs=Mock(return_value=[active]))
        self.assertFalse(blocked['terminal'])
        self.assertEqual(blocked['counts']['queued_science_workers'],1)

    def test_mismatched_collector_plan_cannot_be_called_complete(self):
        self.collected()
        path = self.reconstruction/'jobs/collect/result.json'
        result = monitor.read(path)
        result['benchmark_plan_sha256'] = 'c'*64
        save(path,result)
        self.close_managers()
        observed = monitor.observe(self.config,{},now=1000,query_jobs=Mock(return_value=[]))
        self.assertFalse(observed['terminal'])
        self.assertTrue(any('matching frozen response draw' in row for row in observed['problems']))

    def test_stale_watchdog_and_missing_receipts_are_visible(self):
        self.prepared()
        save(self.reconstruction/'submission.json',dict(manager=103))
        save(self.reconstruction/'watchdog/heartbeat.json',dict(checked_epoch=100,terminal=False,problems=[]))
        observed = monitor.observe(self.config,{},now=1000,query_jobs=Mock(return_value=[]))
        self.assertTrue(any('heartbeat is stale' in row for row in observed['problems']))

    def test_native_terminal_is_cached_and_failed_or_removed_manager_not_success(self):
        self.close_managers()
        state = {}
        result = monitor.native_terminal(self.managers[0],state)
        self.assertTrue(result['successful'])
        Path(self.managers[0]['event_log']).unlink()
        self.assertEqual(monitor.native_terminal(self.managers[0],state),result)
        path = Path(self.managers[1]['event_log'])
        path.write_text('009 (102.000.000) 10/29 12:00:00 Job was aborted.\n    removed\n...\n')
        self.assertTrue(monitor.native_terminal(self.managers[1],state)['removed'])
        self.assertFalse(monitor.native_terminal(self.managers[1],state)['successful'])

    def test_scheduler_failure_is_unknown_instead_of_empty_or_completed(self):
        def failed(config,state,now):
            raise NativeCondorError('query failed')
        state = {}
        calls = []
        result = monitor.run_once(self.config,state,now=1000,observer=failed,
            writer=lambda path,value:calls.append((path,value)))
        self.assertEqual(result['phase'],'scheduler_unknown')
        self.assertFalse(result['terminal'])
        self.assertNotIn('counts',result)
        self.assertEqual(len(calls),3)
        self.assertIn('first_failure',state)

    def test_lifetime_bound_records_incomplete_state(self):
        state = dict(started_epoch=1000)
        observer = Mock(side_effect=AssertionError('No polling after lifetime bound'))
        result = monitor.run_once(self.config,state,now=1000+86400,observer=observer,
            writer=lambda path,value:None)
        self.assertEqual(result['phase'],'watchdog_runtime_limit')
        self.assertFalse(result['terminal'])
        self.assertEqual(observer.call_count,0)

    def test_weekend_lifetime_is_explicit_bounded_and_does_not_expire_on_sunday(self):
        self.config['maximum_wall_seconds'] = 96*3600
        with patch.object(monitor, 'persistent', side_effect=Path):
            monitor.validate(self.config)
        observer = Mock(return_value=dict(phase='running', terminal=False, problems=[]))
        result = monitor.run_once(self.config, dict(started_epoch=1000),
            now=1000+72*3600, observer=observer, writer=lambda path,value:None)
        self.assertEqual(result['phase'], 'running')
        self.assertEqual(observer.call_count, 1)
        for invalid in (True, 96*3600., 7*86400+1):
            self.config['maximum_wall_seconds'] = invalid
            with patch.object(monitor, 'persistent', side_effect=Path):
                with self.assertRaisesRegex(ValueError, 'integer monitor lifetime'):
                    monitor.validate(self.config)

    def native_collection(self, shared=False):
        persistence = patch.object(monitor, 'persistent', side_effect=Path)
        persistence.start()
        self.addCleanup(persistence.stop)
        self.collected()
        self.close_managers()
        self.config['individual_source_watchdogs'] = False
        (self.reconstruction/'watchdog/heartbeat.json').unlink()
        directory = self.reconstruction/'jobs/collect'
        save(directory/'request.json', dict(frozen='request'))
        prepared = monitor.read(self.reconstruction/'prepared.json')
        prepared['collector'] = dict(node='COLLECT',job_directory=str(directory),
            request_sha256=monitor.digest(directory/'request.json'))
        save(self.reconstruction/'prepared.json',prepared)
        result = monitor.read(directory/'result.json')
        result['request_sha256'] = prepared['collector']['request_sha256']
        save(directory/'result.json',result)
        save(directory/'status.json',dict(complete=True,exit_code=0))
        (directory/'events.log').write_text('000 (104.000.000) 10/29 12:00:00 Job submitted.\n...\n'
            '005 (104.000.000) 10/29 12:01:00 Job terminated.\n'
            '    (1) Normal termination (return value 0)\n...\n')
        manager_dir = self.root/'waves/w000' if shared else self.reconstruction
        manager_dir.mkdir(parents=True,exist_ok=True)
        node = 'S000_COLLECT' if shared else 'COLLECT'
        (manager_dir/'response.dag.dagman.out').write_text('Event: ULOG_SUBMIT for HTCondor Node '
            +node+' (104.0.0) {10/29/26 12:00:00}\n')
        manager = dict(cluster_id=103,binding=dict(working_directory=str(manager_dir)),
            native_job=dict(ClusterId=103,Iwd=str(manager_dir),GlobalJobId='schedd#103.0#1000'))
        submission = dict(source_id=self.sid,manager=manager)
        if shared:
            source_pin = dict(path=str(self.reconstruction/'prepared.json'),
                sha256=monitor.digest(self.reconstruction/'prepared.json'))
            source_nodes = dict(COLLECT=node)
            wave_path = manager_dir/'prepared.json'
            save(wave_path,dict(sources=[dict(source_id=self.sid,prepared_manifest=source_pin,
                source_job_nodes=source_nodes,collector=prepared['collector'])]))
            submission.update(shared_wave=True,shared_wave_directory=str(manager_dir),
                source_prepared_manifest=source_pin,wave_prepared_manifest=dict(path=str(wave_path),
                    sha256=monitor.digest(wave_path)),source_job_nodes=source_nodes)
        save(self.reconstruction/'submission.json',submission)
        return manager_dir,directory

    def test_native_collector_closes_without_per_source_watchdog_or_manager_exit(self):
        manager_dir,_ = self.native_collection()
        live = self.ad(103,manager_dir)
        live['GlobalJobId'] = 'schedd#103.0#1000'
        state = {}
        observed = monitor.observe(self.config,state,now=1000,query_jobs=Mock(return_value=[live]))
        self.assertTrue(observed['sources'][0]['collector_complete'])
        self.assertEqual(observed['counts']['collected_events'],2)
        self.assertIn(self.sid,state['source_collector_closures'])

    def test_shared_wave_membership_and_native_node_ownership_close_one_source(self):
        manager_dir,_ = self.native_collection(shared=True)
        live = self.ad(103,manager_dir)
        live['GlobalJobId'] = 'schedd#103.0#1000'
        observed = monitor.observe(self.config,{},now=1000,query_jobs=Mock(return_value=[live]))
        self.assertTrue(observed['sources'][0]['collector_complete'])
        (manager_dir/'response.dag.dagman.out').write_text('Event: ULOG_SUBMIT for HTCondor Node '
            'S001_COLLECT (104.0.0)\n')
        failed = monitor.observe(self.config,{},now=1000,query_jobs=Mock(return_value=[live]))
        self.assertFalse(failed['sources'][0]['collector_complete'])
        self.assertTrue(any('not bound to its source DAG node' in value for value in failed['problems']))

    def test_wrong_wave_membership_or_queued_payload_owner_cannot_be_credited(self):
        manager_dir,_ = self.native_collection(shared=True)
        active = self.ad(104,self.reconstruction/'jobs/collect',2,5,999)
        failed = monitor.observe(self.config,{},now=1000,query_jobs=Mock(return_value=[active]))
        self.assertTrue(any('another native DAG' in value for value in failed['problems']))
        path = self.reconstruction/'submission.json'
        submission = monitor.read(path)
        wave_path = Path(submission['wave_prepared_manifest']['path'])
        save(wave_path,dict(sources=[]))
        submission['wave_prepared_manifest']['sha256'] = monitor.digest(wave_path)
        save(path,submission)
        failed = monitor.observe(self.config,{},now=1000,query_jobs=Mock(return_value=[]))
        self.assertTrue(any('not an exact member' in value for value in failed['problems']))

    def test_failed_native_collector_cannot_be_replaced_by_successful_json(self):
        _,directory = self.native_collection()
        path = directory/'events.log'
        path.write_text(path.read_text().replace('return value 0','return value 1'))
        failed = monitor.observe(self.config,{},now=1000,query_jobs=Mock(return_value=[]))
        self.assertTrue(any('did not terminate successfully' in value for value in failed['problems']))

    def test_wrong_request_or_active_source_response_prevents_native_closure(self):
        _,directory = self.native_collection()
        active = self.ad(200,self.reconstruction/'jobs/det_0000',2,5,103)
        observed = monitor.observe(self.config,{},now=1000,query_jobs=Mock(return_value=[active]))
        self.assertFalse(observed['sources'][0]['collector_complete'])
        save(directory/'request.json',dict(changed=True))
        failed = monitor.observe(self.config,{},now=1000,query_jobs=Mock(return_value=[]))
        self.assertTrue(any('request binding differs' in value for value in failed['problems']))


if __name__ == '__main__':
    unittest.main()
