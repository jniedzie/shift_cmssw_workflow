# Code style: AI-code.
"""Check aggregate progress, lost transitions and native terminal accounting."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

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


if __name__ == '__main__':
    unittest.main()
