import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import run_shift_ntuple_controller as controller
import run_shift_gen_to_nano as worker
import prepare_shift_ntuple_restart as preparer
import run_shift_quota_audit as quota_auditor
from types import SimpleNamespace


class ControllerTests(unittest.TestCase):
    @staticmethod
    def receipt(root, job, stratum='qcd', events=2):
        result=dict(job=job,exit_code=0,complete=True,source_stratum=stratum,nano_path='/eos/nano.root',
            report_sha256='validated',events=events,compressed_event_bytes=20,nano_bytes=120,evidence_bytes=10,
            wall_seconds=2,validated_tier_events={tier:events for tier in ('GEN','SIM','DIGIHLT','RECO','NANO')})
        (root/'results'/f'status{job}.json').write_text(json.dumps(result))
        return result

    @staticmethod
    def campaign(root, *, strata=None, jobs=3, pilot=True, submission=None):
        (root/'results').mkdir()
        strata={'qcd':6} if strata is None else strata
        manifest=dict(strata=strata,sources=[dict(index=i,stratum=s,events=n) for i,(s,n) in enumerate(strata.items())],
            events_per_job=2,jobs=jobs,events=sum(strata.values()),minimum_free_bytes=0,minimum_free_files=0)
        if pilot:
            manifest.update(pilot_jobs=list(range(min(2,jobs))),pilot_events=min(2,jobs)*2,
                pilot_strata={s:2 for s in strata},pilot_sources=manifest['sources'])
        policy=dict(tag='test',attention_email='jeremi.niedziela@cern.ch',queue_timeout_seconds=7200,
            worker_timeout_seconds=16200,setup_timeout_seconds=1200,completion_timeout_seconds=16200,
            canary_timeout_seconds=3600)
        (root/'manifest.json').write_text(json.dumps(manifest))
        (root/'policy.json').write_text(json.dumps(policy))
        (root/'submission.json').write_text(json.dumps(submission or dict(canary_cluster=1,canary_jobs=[800000])))
        return manifest,policy

    def test_stalled_stage_and_queue_trigger_stop(self):
        policy = dict(queue_timeout_seconds=7200, worker_timeout_seconds=16200, setup_timeout_seconds=900)
        self.assertIsNone(controller.problem(dict(JobStatus=2, JobCurrentStartDate=100,
            ShiftNtupleProgressEpoch=900, ShiftNtupleStageTimeout=1000), 1500, policy))
        self.assertIn('progress', controller.problem(dict(JobStatus=2, JobCurrentStartDate=100,
            ShiftNtupleProgressEpoch=100, ShiftNtupleStageTimeout=1000), 1500, policy))
        self.assertIn('queue', controller.problem(dict(JobStatus=1, QDate=0), 7201, policy))
        self.assertIn('held', controller.problem(dict(JobStatus=5), 0, policy))

    def test_cleared_and_previous_attempt_telemetry_use_current_setup_guard(self):
        policy = dict(queue_timeout_seconds=7200, worker_timeout_seconds=21600, setup_timeout_seconds=1200)
        for telemetry in (dict(ShiftNtupleProgressEpoch=None, ShiftNtupleStageTimeout=None,
                               ShiftNtupleStageDeadlineEpoch=None),
                          dict(ShiftNtupleProgressEpoch=1, ShiftNtupleStageTimeout=1,
                               ShiftNtupleStageDeadlineEpoch=100),
                          dict(ShiftNtupleProgressEpoch=None, ShiftNtupleStageTimeout=1,
                               ShiftNtupleStageDeadlineEpoch=20000)):
            ad = dict(JobStatus=2, JobCurrentStartDate=10000, **telemetry)
            self.assertIsNone(controller.problem(ad, 10001, policy))
            self.assertIsNone(controller.problem(ad, 11000, policy))
            self.assertIn('progress', controller.problem(ad, 11381, policy))

    def test_current_absolute_deadline_survives_non_atomic_audit_telemetry(self):
        policy = dict(queue_timeout_seconds=7200, worker_timeout_seconds=21600, setup_timeout_seconds=1800)
        ad = dict(JobStatus=2, JobCurrentStartDate=1000, ShiftNtupleTier='NANO_AUDIT',
                  ShiftNtupleProgressEpoch=2000, ShiftNtupleStageTimeout=900,
                  ShiftNtupleStageDeadlineEpoch=4340)
        self.assertIsNone(controller.problem(ad, 3440, policy))  # previous NANO epoch is 24 minutes old
        self.assertIn('stage wall-time', controller.problem(ad, 4371, policy))
        self.assertIn('total time', controller.problem(ad, 22601, policy))
        self.assertIn('progress', controller.problem({**ad, 'ShiftNtupleStageDeadlineEpoch':None}, 3440, policy))
        for prior in (None, 999):
            stale = {**ad, 'ShiftNtupleProgressEpoch':prior, 'ShiftNtupleStageDeadlineEpoch':10000}
            self.assertIsNone(controller.problem(stale, 1001, policy))
            self.assertIn('progress', controller.problem(stale, 2981, policy))

    def test_one_email_across_controller_restarts(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(controller.subprocess, 'run') as send:
            root = Path(directory)
            controller.notify_once(root, 'jeremi.niedziela@cern.ch', 'first failure')
            controller.notify_once(root, 'jeremi.niedziela@cern.ch', 'second failure')
            self.assertEqual(send.call_count, 1)
            self.assertEqual(json.loads((root / 'attention_email.json').read_text())['status'], 'accepted_by_local_mailer')

    def test_large_receipt_batch_resolves_all_incident_identities_with_one_ledger_read(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'worker_incidents.json'
            ledger = dict(updated_at_epoch=90, evidence='preserved', jobs={
                '1':dict(job=1, active=True, first_seen_epoch=10, last_seen_epoch=80),
                '42.7':dict(job=1, cluster=42, proc=7, first_seen_epoch=20, reason='old attempt'),
                '2':dict(job=2, active=False, resolved_at_epoch=50, receipt={'job':2}),
                '43.8':dict(job=20000, cluster=43, proc=8, active=True),
                '43.9':dict(cluster=43, proc=9, active=True),
            })
            path.write_text(json.dumps(ledger))
            with patch.object(controller, 'read', wraps=controller.read) as read, \
                 patch.object(controller, 'save', wraps=controller.save) as save, \
                 patch.object(controller, 'notify_once') as notify, \
                 patch.object(controller.time, 'time', return_value=100):
                controller.resolve_incidents(root, range(19500))
                read.assert_called_once_with(path)
                save.assert_called_once()
                notify.assert_not_called()
            expected = json.loads(json.dumps(ledger))
            for identity in ('1', '42.7'):
                expected['jobs'][identity].update(active=False, resolved_at_epoch=100)
            self.assertEqual(json.loads(path.read_text()), expected)

    def test_bulk_startup_resolves_successful_receipts_once_per_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.campaign(root, pilot=False, submission=dict(bulk_cluster=42))
            for job in range(3):
                self.receipt(root, job)
            with patch.object(controller, 'quota', return_value=(10**9, 10**6)), \
                 patch.object(controller, 'query', return_value=[]), \
                 patch.object(controller, 'resolve_incidents', wraps=controller.resolve_incidents) as resolve:
                controller.supervise(root)
                resolve.assert_called_once_with(root, [0, 1, 2])
            self.assertTrue(json.loads((root / 'production_complete.json').read_text())['complete'])

    def test_receipt_validation_refreshes_heartbeat_before_finishing_startup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.campaign(root, pilot=False, submission=dict(bulk_cluster=42))
            for job in range(3):
                self.receipt(root, job)
            clock = [0]
            validate = controller.validated_receipt
            def slow_validation(result, job):
                clock[0] += 31
                return validate(result, job)
            with patch.object(controller, 'quota', return_value=(10**9, 10**6)), \
                 patch.object(controller, 'query', return_value=[]), \
                 patch.object(controller, 'validated_receipt', side_effect=slow_validation), \
                 patch.object(controller.time, 'monotonic', side_effect=lambda:clock[0]), \
                 patch.object(controller.time, 'time', side_effect=lambda:1000+clock[0]), \
                 patch.object(controller, 'save', wraps=controller.save) as save:
                controller.supervise(root)
            heartbeats = [call.args[1] for call in save.call_args_list
                if call.args[0] == root / 'controller_state.json'
                and call.args[1].get('activity') == 'reconciling preserved worker receipts']
            self.assertEqual([row['checked_at_epoch'] for row in heartbeats], [1031, 1062])
            self.assertTrue(all(row['phase'] == 'bulk' for row in heartbeats))

    def test_quota_gate_requires_every_bin_and_headroom(self):
        manifest = dict(strata={'dy':1000}, sources=[dict(stratum='dy',events=1000)], events_per_job=100,
                        jobs=10, minimum_free_bytes=50000000, minimum_free_files=20000)
        with self.assertRaisesRegex(ValueError, 'Every'):
            controller.reservation([], manifest, 1000000000, 50000)
        result = dict(source_stratum='dy', compressed_event_bytes=10000, events=100,
                      nano_bytes=1000000,evidence_bytes=500000)
        with self.assertRaisesRegex(ValueError, 'reservation'):
            controller.reservation([result], manifest, 50000000, 50000)
        self.assertGreater(controller.reservation([result], manifest, 1000000000, 50000), 0)

    def test_worker_command_timeout_terminates_process(self):
        import subprocess
        with self.assertRaises(subprocess.TimeoutExpired):
            worker.command([sys.executable,'-c','import time; time.sleep(60)'], timeout=0.1)

    def test_watchdog_detects_controller_without_heartbeat(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'controller_state.json').write_text(json.dumps(dict(phase='bulk',checked_at_epoch=1)))
            (root/'submission.json').write_text(json.dumps(dict(controller_cluster=17)))
            with patch.object(controller.time,'time',return_value=1000), patch.object(controller,'query',return_value=[dict(JobStatus=2)]):
                with self.assertRaisesRegex(TimeoutError,'five minutes'):
                    controller.watchdog(root,{})

    def test_watchdog_refreshes_controller_heartbeat_after_scheduler_outage(self):
        class EndCheck(BaseException):pass
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            state_path=root/'controller_state.json'
            state_path.write_text(json.dumps(dict(phase='bulk',checked_at_epoch=1000)))
            (root/'submission.json').write_text(json.dumps(dict(controller_cluster=17)))
            now=[1000]
            def recovered(*args,**kwargs):
                now[0]=1400
                state_path.write_text(json.dumps(dict(phase='bulk',checked_at_epoch=1390)))
                return [dict(JobStatus=2)]
            with patch.object(controller,'recoverable_query',side_effect=recovered),patch.object(controller.time,'time',side_effect=lambda:now[0]),patch.object(controller.time,'sleep',side_effect=EndCheck):
                with self.assertRaises(EndCheck):controller.watchdog(root,{})

    def test_watchdog_accepts_controller_completion_during_scheduler_read(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            state_path=root/'controller_state.json'
            state_path.write_text(json.dumps(dict(phase='bulk',checked_at_epoch=1)))
            (root/'submission.json').write_text(json.dumps(dict(controller_cluster=17)))
            def recovered(*args,**kwargs):
                state_path.write_text(json.dumps(dict(phase='complete',completed_at_epoch=1000)))
                return [dict(JobStatus=4)]
            with patch.object(controller,'recoverable_query',side_effect=recovered),patch.object(controller.time,'time',return_value=1000),patch.object(controller.time,'sleep') as sleep:
                controller.watchdog(root,{})
            sleep.assert_not_called()
            self.assertEqual(json.loads((root/'watchdog_state.json').read_text())['phase'],'complete')

    def test_absolute_stage_deadline_and_recent_idle_transition(self):
        policy=dict(queue_timeout_seconds=7200,worker_timeout_seconds=16200,setup_timeout_seconds=1200)
        self.assertIn('stage wall-time',controller.problem(dict(JobStatus=2,JobCurrentStartDate=100,
            ShiftNtupleProgressEpoch=1490,ShiftNtupleStageTimeout=1200,ShiftNtupleStageDeadlineEpoch=1400),1500,policy))
        self.assertIsNone(controller.problem(dict(JobStatus=1,QDate=1,EnteredCurrentStatus=9900),10000,policy))
        self.assertIsNone(controller.problem(dict(JobStatus=1,QDate=1,JobMaterializeDate=9900),10000,policy))
        self.assertIsNone(controller.problem(dict(JobStatus=1,QDate=1,EnteredCurrentStatus=1,JobMaterializeDate=9900),10000,policy))
        self.assertIsNone(controller.problem(dict(JobStatus=2,JobCurrentStartDate=100,
            ShiftNtupleProgressEpoch=1490,ShiftNtupleStageTimeout=1200,ShiftNtupleStageDeadlineEpoch=0),1500,policy))
        self.assertIsNone(controller.problem(dict(JobStatus=2,JobCurrentStartDate=100,
            ShiftNtupleProgressEpoch=1490,ShiftNtupleStageTimeout=1200,ShiftNtupleStageDeadlineEpoch=1480),1500,policy))

    def test_native_query_recovers_once_without_holding_jobs(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'controller_state.json').write_text(json.dumps(dict(phase='pilot',health='degraded',checked_at_epoch=1)))
            def wait(seconds):
                self.assertEqual(seconds,10)
                state=json.loads((root/'controller_state.json').read_text())
                self.assertEqual((state['health'],state['scheduler_state']),('unknown','unknown'))
                self.assertEqual(state['checked_at_epoch'],1000)
            with patch.object(controller,'query',side_effect=[controller.NativeCondorError('temporary timeout'),[dict(JobStatus=2)]]) as query,patch.object(controller.time,'sleep',side_effect=wait),patch.object(controller.time,'time',return_value=1000),patch.object(controller,'run_condor') as mutate:
                self.assertEqual(controller.bounded_query(root,'ClusterId == 2'),[dict(JobStatus=2)])
            self.assertEqual(query.call_count,2);mutate.assert_not_called()
            state=json.loads((root/'controller_state.json').read_text())
            self.assertEqual((state['health'],state['scheduler_state']),('degraded','known'))
            self.assertNotIn('scheduler_query_error',state)

    def test_native_query_stops_after_three_unknown_results_without_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with patch.object(controller,'query',side_effect=controller.NativeCondorError('cannot verify scheduler')) as query,patch.object(controller.time,'sleep') as sleep,patch.object(controller,'run_condor') as mutate:
                with self.assertRaises(controller.NativeCondorError):controller.bounded_query(root,'ClusterId == 2')
            self.assertEqual(query.call_count,3)
            self.assertEqual([call.args[0] for call in sleep.call_args_list],[10,10])
            mutate.assert_not_called()
            state=json.loads((root/'controller_state.json').read_text())
            self.assertEqual((state['health'],state['scheduler_query_attempt']),('unknown',3))

    def test_watchdog_query_retry_updates_only_watchdog_heartbeat(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            initial=dict(phase='bulk',health='healthy',checked_at_epoch=1)
            (root/'controller_state.json').write_text(json.dumps(initial))
            with patch.object(controller,'query',side_effect=[controller.NativeCondorError('transient'),[]]),patch.object(controller.time,'sleep'),patch.object(controller.time,'time',return_value=1000),patch.object(controller,'run_condor') as mutate:
                self.assertEqual(controller.bounded_query(root,'ClusterId == 2',role='watchdog'),[])
            mutate.assert_not_called()
            self.assertEqual(json.loads((root/'controller_state.json').read_text()),initial)
            self.assertEqual(json.loads((root/'watchdog_state.json').read_text())['checked_at_epoch'],1000)

    def test_long_scheduler_outage_recovers_without_worker_mutations(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'controller_state.json').write_text(json.dumps(dict(phase='bulk',health='healthy')))
            outages=[controller.NativeCondorError('security negotiation failed')]*4
            with patch.object(controller,'query',side_effect=outages+[[dict(JobStatus=2)]]) as query, patch.object(controller.time,'sleep'), patch.object(controller,'run_condor') as mutate:
                self.assertEqual(controller.recoverable_query(root,'ClusterId == 2'),[dict(JobStatus=2)])
            self.assertEqual(query.call_count,5)
            mutate.assert_not_called()
            self.assertFalse((root/'stop_requested.json').exists())
            state=json.loads((root/'controller_state.json').read_text())
            self.assertEqual(state['phase'],'bulk')
            self.assertEqual(state['scheduler_state'],'known')

    def test_persistent_scheduler_outage_stays_observable_without_holding_jobs(self):
        class EndCheck(BaseException):pass
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with patch.object(controller,'query',side_effect=controller.NativeCondorError('unknown')), patch.object(controller.time,'sleep',side_effect=[None,None,EndCheck()]), patch.object(controller,'run_condor') as mutate:
                with self.assertRaises(EndCheck):controller.recoverable_query(root,'ClusterId == 2',role='watchdog')
            mutate.assert_not_called()
            state=json.loads((root/'watchdog_state.json').read_text())
            self.assertEqual(state['health'],'unknown')
            self.assertGreater(state['checked_at_epoch'],0)
            self.assertFalse((root/'stop_requested.json').exists())

    def test_known_query_restores_blocked_health_after_previous_outage(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'controller_state.json').write_text(json.dumps(dict(phase='blocked',health='unknown',
                scheduler_state='unknown',health_before_unknown='healthy',scheduler_query_error='earlier outage')))
            with patch.object(controller,'query',return_value=[]),patch.object(controller,'run_condor') as mutate:
                self.assertEqual(controller.bounded_query(root,'ClusterId == 2'),[])
            mutate.assert_not_called()
            state=json.loads((root/'controller_state.json').read_text())
            self.assertEqual((state['health'],state['scheduler_state']),('blocked','known'))

    def test_restarting_bulk_rebuilds_completion_without_submission(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            self.campaign(root,submission=dict(canary_cluster=1,canary_jobs=[800000],pilot_cluster=2,bulk_cluster=3,recovery_clusters=[4]))
            self.receipt(root,800000)
            for job in range(3):self.receipt(root,job)
            with patch.object(controller,'quota',return_value=(10**9,10**6)),patch.object(controller,'query',return_value=[]) as query,patch.object(controller,'submit') as submit:
                controller.supervise(root)
            self.assertIn('ClusterId == 4',query.call_args.args[0])
            submit.assert_not_called()
            complete=json.loads((root/'production_complete.json').read_text())
            self.assertEqual(complete['tier_events']['qcd']['NANO'],6)

    def test_recovery_factory_allocation_is_reserved_before_workers_start(self):
        submitted=dict(recovery_worker_budgets={'4':200})
        self.assertEqual(controller.recovery_reservation([],submitted),200)
        live=[dict(ClusterId=4,JobStatus=2)]*50
        self.assertEqual(controller.recovery_reservation(live,submitted),150)
        self.assertEqual(controller.recovery_reservation(live+[dict(ClusterId=4,JobStatus=5)]*10,submitted),150)
        self.assertEqual(controller.recovery_reservation([dict(ClusterId=4,JobStatus=2)]*200,submitted),0)
        submitted['recovery_job_ids']={'4':[10,11]}
        self.assertEqual(controller.recovery_reservation([],submitted,{10}),1)
        self.assertEqual(controller.recovery_reservation([],submitted,{10,11}),0)

    def test_recovery_reservation_distinguishes_equal_ids_on_other_schedds(self):
        policy=dict(schedd_name='ours')
        submitted=dict(recovery_worker_budgets={'42':200})
        ads=([dict(ClusterId=42,JobStatus=2,_queried_schedd='ours')]*50+
             [dict(ClusterId=42,JobStatus=2,_queried_schedd='other')]*100)
        self.assertEqual(controller.recovery_reservation(ads,submitted,policy=policy),150)
        # The account budget includes the remote workers and all 200 slots
        # allocated to this campaign's recovery factory, without collisions.
        self.assertEqual(controller.external_workers(ads,41,policy)+
            controller.recovery_reservation(ads,submitted,policy=policy),300)

    def test_restarting_pilot_submits_bulk_once_and_preserves_pilot_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            self.campaign(root,submission=dict(canary_cluster=1,canary_jobs=[800000],pilot_cluster=2))
            (root/'pilot_submission_intent.json').write_text('{}')
            self.receipt(root,800000);self.receipt(root,0)
            releases=[]
            def submit(campaign,name):
                releases.append(name)
                self.receipt(root,1);self.receipt(root,2)
                return 3
            with patch.object(controller,'quota',return_value=(10**9,10**6)),patch.object(controller,'query',return_value=[]),patch.object(controller,'submit',side_effect=submit):
                controller.supervise(root)
            self.assertEqual(releases,['bulk'])
            self.assertEqual(json.loads((root/'production_complete.json').read_text())['tier_events']['qcd']['NANO'],6)

    def test_failed_pilot_worker_does_not_stop_healthy_bin(self):
        class EndCheck(Exception):pass
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            self.campaign(root,strata={'qcd':2,'dy':2},jobs=2,
                submission=dict(canary_cluster=1,canary_jobs=[800000],pilot_cluster=2))
            failed=dict(job=0,exit_code=1,complete=False,source_stratum='qcd',error='bad node')
            (root/'results'/'status0.json').write_text(json.dumps(failed))
            self.receipt(root,800000)
            ads=[dict(ClusterId=2,ProcId=0,ShiftNtupleJob=0,JobStatus=5,HoldReason='worker failed'),
                 dict(ClusterId=2,ProcId=1,ShiftNtupleJob=1,JobStatus=2)]
            iterations=[]
            def tick(seconds):
                iterations.append(seconds)
                if len(iterations)==1:self.receipt(root,1,'dy')
                else:raise EndCheck()
            with patch.object(controller,'quota',return_value=(10**9,10**6)),patch.object(controller,'query',return_value=ads),patch.object(controller,'submit') as submit,patch.object(controller,'run_condor') as hold,patch.object(controller,'notify_once') as notify,patch.object(controller.time,'sleep',side_effect=tick):
                with self.assertRaises(EndCheck):controller.supervise(root)
            submit.assert_not_called();hold.assert_not_called()
            self.assertEqual(notify.call_count,1)
            state=json.loads((root/'controller_state.json').read_text())
            self.assertEqual((state['phase'],state['health'],state['completed_jobs']),('pilot','degraded',1))
            self.assertEqual(state['failed_job_ids'],[0])
            self.assertFalse((root/'stop_requested.json').exists())

    def test_timed_out_worker_holds_only_its_exact_proc(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            ad=dict(ClusterId=42,ProcId=7,JobStatus=2,ShiftNtupleJob=123)
            with patch.object(controller,'run_condor') as hold,patch.object(controller,'notify_once'):
                controller.isolate_worker(root,dict(attention_email='jeremi.niedziela@cern.ch'),ad,'stage stalled')
            self.assertEqual(hold.call_args.args[0],['/usr/bin/condor_hold','42.7'])

    def test_blocked_monitor_remains_alive_and_keeps_failure_reason(self):
        class EndCheck(Exception):pass
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'controller_state.json').write_text(json.dumps(dict(phase='blocked',error='no space',checked_at_epoch=1)))
            with patch.object(controller,'query',return_value=[dict(ClusterId=2,ProcId=0,JobStatus=5)]),patch.object(controller.time,'time',return_value=1000),patch.object(controller.time,'sleep',side_effect=EndCheck):
                with self.assertRaises(EndCheck):controller.monitor_blocked(root,dict(tag='test'))
            state=json.loads((root/'controller_state.json').read_text())
            self.assertEqual(state['checked_at_epoch'],1000)
            self.assertEqual(state['error'],'no space')
            self.assertTrue(state['monitoring'])

    def test_watchdog_continues_to_watch_blocked_controller(self):
        class EndCheck(Exception):pass
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'controller_state.json').write_text(json.dumps(dict(phase='blocked',error='no space',checked_at_epoch=1000)))
            (root/'submission.json').write_text(json.dumps(dict(controller_cluster=17)))
            with patch.object(controller,'query',return_value=[dict(JobStatus=2)]),patch.object(controller,'notify_once'),patch.object(controller.time,'time',return_value=1000),patch.object(controller.time,'sleep',side_effect=EndCheck):
                with self.assertRaises(EndCheck):controller.watchdog(root,dict(attention_email='jeremi.niedziela@cern.ch'))
            self.assertEqual(json.loads((root/'watchdog_state.json').read_text())['controller_phase'],'blocked')

    def test_campaign_allows_only_one_controller(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            lock=controller.lock_supervisor(root,'controller')
            try:
                with self.assertRaisesRegex(RuntimeError,'already supervising'):
                    controller.lock_supervisor(root,'controller')
                separate=controller.lock_supervisor(root,'watchdog');separate.close()
            finally:lock.close()

    def test_passed_canary_proofs_need_no_new_validation_jobs(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            self.campaign(root,strata={'qcd':2},jobs=1,pilot=False,
                submission=dict(canary_cluster=None,canary_jobs=[800000]))
            proof=self.receipt(root,800000)
            proof_path=root/'canary_proofs.json';proof_path.write_text(json.dumps([proof]))
            policy=json.loads((root/'policy.json').read_text());policy['validated_canary_results']=str(proof_path)
            (root/'policy.json').write_text(json.dumps(policy))
            def submit(campaign,name):
                self.assertEqual(name,'bulk');self.receipt(root,0);return 3
            with patch.object(controller,'quota',return_value=(10**9,10**6)),patch.object(controller,'query',return_value=[]),patch.object(controller,'submit',side_effect=submit) as release:
                controller.supervise(root)
            self.assertEqual(release.call_count,1)

    def test_full_validated_queue_releases_directly_at_initial_capacity(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            self.campaign(root,pilot=False,submission=dict(canary_cluster=None,canary_jobs=[800000]))
            policy=json.loads((root/'policy.json').read_text())
            policy.update(worker_ceiling=300,initial_workers=100,adaptive_capacity=True)
            (root/'policy.json').write_text(json.dumps(policy));self.receipt(root,800000)
            releases=[]
            def submit(campaign,name,**kwargs):
                releases.append((name,kwargs))
                for job in range(3):self.receipt(root,job)
                return 3
            with patch.object(controller,'quota',return_value=(10**9,10**6)),patch.object(controller,'query',return_value=[]),patch.object(controller,'account_workers',return_value=[]),patch.object(controller,'submit',side_effect=submit):
                controller.supervise(root)
            self.assertEqual(releases,[('bulk',{'materialize_limit':100})])
            self.assertEqual(json.loads((root/'production_complete.json').read_text())['tier_events']['qcd']['NANO'],6)

    def test_initial_release_waits_when_other_workers_fill_the_budget(self):
        class EndCheck(Exception):pass
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            self.campaign(root,pilot=False,submission=dict(canary_cluster=None,canary_jobs=[800000]))
            policy=json.loads((root/'policy.json').read_text());policy.update(self.adaptive_policy())
            (root/'policy.json').write_text(json.dumps(policy));self.receipt(root,800000)
            other=[dict(ClusterId=90,JobStatus=2)]*100
            with patch.object(controller,'quota',return_value=(10**9,10**6)),patch.object(controller,'account_workers',return_value=other),patch.object(controller,'submit') as submit,patch.object(controller.time,'sleep',side_effect=EndCheck):
                with self.assertRaises(EndCheck):controller.supervise(root)
            submit.assert_not_called()
            state=json.loads((root/'controller_state.json').read_text())
            self.assertEqual(state['capacity']['factory_budget'],0)
            self.assertEqual(state['activity'],'waiting_for_other_SHIFT_workers')

    def test_initial_release_reserves_capacity_for_other_live_campaigns(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            self.campaign(root,pilot=False,submission=dict(canary_cluster=None,canary_jobs=[800000]))
            policy=json.loads((root/'policy.json').read_text());policy.update(self.adaptive_policy())
            (root/'policy.json').write_text(json.dumps(policy));self.receipt(root,800000)
            limits=[]
            def submit(campaign,name,**kwargs):
                limits.append(kwargs['materialize_limit'])
                for job in range(3):self.receipt(root,job)
                return 3
            with patch.object(controller,'quota',return_value=(10**9,10**6)),patch.object(controller,'query',return_value=[]),patch.object(controller,'account_workers',return_value=[dict(ClusterId=90,JobStatus=2)]*60),patch.object(controller,'submit',side_effect=submit):
                controller.supervise(root)
            self.assertEqual(limits,[40])

    def test_late_watchdog_registration_survives_both_phase_transitions(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);self.campaign(root);self.receipt(root,800000)
            def quota(campaign):
                submitted=json.loads((root/'submission.json').read_text())
                submitted.update(watchdog_cluster=77,controller_cluster=76)
                (root/'submission.json').write_text(json.dumps(submitted))
                return 10**9,10**6
            def submit(campaign,name):
                if name=='pilot':self.receipt(root,0);return 2
                self.receipt(root,1);self.receipt(root,2);return 3
            with patch.object(controller,'quota',side_effect=quota),patch.object(controller,'query',return_value=[]),patch.object(controller,'submit',side_effect=submit):
                controller.supervise(root)
            self.assertEqual(json.loads((root/'submission.json').read_text())['watchdog_cluster'],77)
            self.assertEqual(json.loads((root/'submission.json').read_text())['controller_cluster'],76)

    @staticmethod
    def adaptive_policy():
        return dict(worker_ceiling=300,initial_workers=100,adaptive_capacity=True,
            capacity_step=50,capacity_interval_seconds=1200,capacity_completions=10,capacity_idle_fraction=0.25)

    def test_capacity_growth_requires_time_completions_quota_and_low_idle(self):
        policy=self.adaptive_policy()
        previous=dict(current_capacity=100,last_capacity_change_epoch=0,completed_at_change=0)
        active=[dict(JobStatus=2)]*80+[dict(JobStatus=1)]*20
        kwargs=dict(now=1200,completed_jobs=10,ads=active,healthy=True,quota_verified=True,
                    account_verified=True,external=30)
        grown=controller.capacity_plan(policy,previous,**kwargs)
        self.assertEqual((grown['current_capacity'],grown['factory_budget']),(150,120))
        for replacement in (dict(now=1199),dict(completed_jobs=9),dict(quota_verified=False),
                            dict(account_verified=False),dict(ads=[dict(JobStatus=1)]*30+[dict(JobStatus=2)]*70)):
            with self.subTest(replacement=replacement):
                unchanged=controller.capacity_plan(policy,previous,**{**kwargs,**replacement})
                self.assertEqual(unchanged['current_capacity'],100)

    def test_degraded_capacity_returns_to_initial_without_killing_workers(self):
        policy=self.adaptive_policy()
        state=controller.capacity_plan(policy,dict(current_capacity=300,last_capacity_change_epoch=0,
            completed_at_change=0),now=1200,completed_jobs=50,ads=[dict(JobStatus=2)]*300,
            healthy=False,quota_verified=True,account_verified=True,external=0)
        self.assertEqual(state['current_capacity'],100)
        self.assertTrue(state['growth_frozen'])

    def test_capacity_hard_ceiling_and_exhausted_global_budget(self):
        policy=self.adaptive_policy();policy['capacity_step']=100
        previous=dict(current_capacity=250,last_capacity_change_epoch=0,completed_at_change=0)
        state=controller.capacity_plan(policy,previous,now=1200,completed_jobs=10,
            ads=[dict(JobStatus=2)],healthy=True,quota_verified=True,account_verified=True,external=400)
        self.assertEqual(state['current_capacity'],300)
        self.assertEqual(state['factory_budget'],0)
        self.assertTrue(state['materialization_paused'])

    def test_explicit_thousand_worker_policy_reserves_other_workers(self):
        policy=self.adaptive_policy()
        policy.update(worker_ceiling=1000,initial_workers=1000,adaptive_capacity=False)
        state=controller.capacity_plan(policy,dict(current_capacity=100),now=1200,
            completed_jobs=4,ads=[dict(JobStatus=2)]*100,healthy=True,
            quota_verified=True,account_verified=True,external=75)
        self.assertEqual(state['current_capacity'],1000)
        self.assertEqual(state['factory_budget'],925)
        with self.assertRaises(ValueError):
            controller.capacity_plan({**policy,'worker_ceiling':1001}, {},now=1200,
                completed_jobs=0,ads=[],healthy=True,quota_verified=True,
                account_verified=True,external=0)

    def test_capacity_override_preserves_physics_and_timeout_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            base=dict(tag='frozen',worker_ceiling=300,initial_workers=100,worker_timeout_seconds=21600)
            (root/'policy.json').write_text(json.dumps(base))
            override=root/'capacity.json';override.write_text(json.dumps(dict(worker_ceiling=1000,initial_workers=1000)))
            effective=controller.effective_policy(root,override)
            self.assertEqual(effective['worker_ceiling'],1000)
            self.assertEqual(effective['worker_timeout_seconds'],21600)
            self.assertEqual(json.loads((root/'policy.json').read_text()),base)
            override.write_text(json.dumps(dict(worker_timeout_seconds=1)))
            with self.assertRaisesRegex(ValueError,'scheduling limits'):
                controller.effective_policy(root,override)

    def test_factory_accepts_thousand_and_rejects_above_ceiling(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with patch.object(controller,'run_condor') as mutate,patch.object(controller,'query',return_value=[
                    dict(JobMaterializeLimit=1000,JobMaterializeMaxIdle=100)]):
                self.assertEqual(controller.set_factory_budget(root,42,1000,100)['JobMaterializeLimit'],1000)
                self.assertEqual(mutate.call_args.args[0],['/usr/bin/condor_qedit','42',
                    'JobMaterializeLimit','1000','JobMaterializeMaxIdle','100'])
                with self.assertRaises(ValueError):controller.set_factory_budget(root,42,1001,100)

    def test_factory_idle_allowance_can_reach_the_capacity_ceiling(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with patch.object(controller,'run_condor') as mutate,patch.object(controller,'query',return_value=[
                    dict(JobMaterializeLimit=1000,JobMaterializeMaxIdle=1000)]):
                controller.set_factory_budget(root,42,1000,1000)
            self.assertEqual(mutate.call_args.args[0],['/usr/bin/condor_qedit','42',
                'JobMaterializeLimit','1000','JobMaterializeMaxIdle','1000'])

    def test_zero_factory_budget_pauses_materialization_with_positive_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with patch.object(controller,'run_condor') as mutate,patch.object(controller,'query',return_value=[
                    dict(JobMaterializeLimit=1,JobMaterializeMaxIdle=0)]):
                controller.set_factory_budget(root,42,0,100)
            self.assertEqual(mutate.call_args.args[0],['/usr/bin/condor_qedit','42',
                'JobMaterializeLimit','1','JobMaterializeMaxIdle','0'])

    def test_factory_verification_reads_cluster_when_no_procs_exist(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            def command(args,**kwargs):
                if args[0]=='/usr/bin/condor_q':
                    self.assertIn('-factory',args)
                    return json.dumps([dict(JobMaterializeLimit=100,JobMaterializeMaxIdle=100)])
                return 'Set attributes\n'
            with patch.object(controller,'run_condor',side_effect=command),patch.object(controller,'query',return_value=[]):
                self.assertEqual(controller.set_factory_budget(root,42,100,100)['JobMaterializeLimit'],100)

    def test_factory_query_distinguishes_retired_from_unmaterialized(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(controller, 'run_condor', side_effect=[
                json.dumps([dict(ClusterId=42, JobMaterializeLimit=1, JobMaterializeMaxIdle=0)]), ''
            ]) as query, patch.object(controller, 'query') as workers:
                self.assertEqual(controller.query_factory(root, 42)[0]['ClusterId'], 42)
                self.assertEqual(controller.query_factory(root, 42), [])
                workers.assert_not_called()
            for call in query.call_args_list:
                self.assertEqual(call.args[0][:2], ['/usr/bin/condor_q', '-factory'])
                self.assertIn('ClusterId == 42', call.args[0])

    def test_retired_main_factory_keeps_recovery_monitoring_and_admission(self):
        class EndCheck(BaseException):pass
        for present in (False, True):
            with self.subTest(unmaterialized_main_present=present), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                submitted = dict(bulk_cluster=42, recovery_clusters=[43, 44],
                    recovery_worker_budgets={'43':1, '44':1}, recovery_job_ids={'43':[1], '44':[2]},
                    deferred_recovery_clusters=[44])
                _, policy = self.campaign(root, pilot=False, submission=submitted)
                policy.update(self.adaptive_policy(), schedd_name='ours')
                self.receipt(root, 0)
                recovery = [dict(ClusterId=43, ProcId=0, JobStatus=2, ShiftNtupleJob=1,
                    JobCurrentStartDate=1000, ShiftNtupleProgressEpoch=1000)]
                factory = [dict(ClusterId=42, JobMaterializeLimit=1, JobMaterializeMaxIdle=0)] if present else []
                with patch.object(controller, 'quota', return_value=(10**9, 10**6)), \
                     patch.object(controller, 'query', return_value=recovery), \
                     patch.object(controller, 'account_workers', return_value=recovery), \
                     patch.object(controller, 'run_condor', return_value=json.dumps(factory)) as query, \
                     patch.object(controller, 'set_factory_budget', return_value={}) as budget, \
                     patch.object(controller.time, 'time', return_value=1000), \
                     patch.object(controller.time, 'sleep', side_effect=EndCheck):
                    with self.assertRaises(EndCheck):
                        controller.supervise(root, policy)
                self.assertEqual(query.call_args.args[0][:2], ['/usr/bin/condor_q', '-factory'])
                self.assertEqual([call.args[1] for call in budget.call_args_list], [42, 44] if present else [44])
                state = json.loads((root / 'controller_state.json').read_text())
                self.assertEqual((state['phase'], state['completed_jobs'], state['queue']), ('bulk', 1, recovery))
                self.assertEqual(state['capacity']['main_factory_present'], present)
                if not present:
                    self.assertEqual(state['capacity']['factory_budget'], 0)
                    self.assertEqual(state['capacity']['reason'], 'main_factory_retired_recovery_observed')
                self.assertEqual(json.loads((root / 'submission.json').read_text())['deferred_recovery_clusters'], [])
                self.assertFalse((root / 'production_complete.json').exists())
                self.assertFalse((root / 'stop_requested.json').exists())

    def test_unknown_factory_query_preserves_workers_and_prior_capacity(self):
        class EndCheck(BaseException):pass
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, policy = self.campaign(root, pilot=False, submission=dict(bulk_cluster=42))
            policy.update(self.adaptive_policy())
            previous = dict(main_factory_present=True, factory_budget=100)
            (root / 'capacity_state.json').write_text(json.dumps(previous))
            with patch.object(controller, 'quota', return_value=(10**9, 10**6)), \
                 patch.object(controller, 'query', return_value=[]), \
                 patch.object(controller, 'account_workers', return_value=[]), \
                 patch.object(controller, 'run_condor', side_effect=controller.NativeCondorError('factory read unavailable')), \
                 patch.object(controller, 'set_factory_budget') as budget, \
                 patch.object(controller, 'activate_deferred_recovery') as activate, \
                 patch.object(controller.time, 'sleep', side_effect=EndCheck):
                with self.assertRaises(EndCheck):
                    controller.supervise(root, policy)
            budget.assert_not_called()
            activate.assert_not_called()
            self.assertEqual(json.loads((root / 'capacity_state.json').read_text()), previous)
            self.assertEqual(json.loads((root / 'controller_state.json').read_text())['scheduler_state'], 'unknown')
            self.assertFalse((root / 'production_complete.json').exists())
            self.assertFalse((root / 'stop_requested.json').exists())

    def test_account_budget_counts_other_schedds_and_excludes_held_workers(self):
        ads=[dict(ClusterId=42,JobStatus=2,_queried_schedd='ours'),
             dict(ClusterId=42,JobStatus=2,_queried_schedd='other'),
             dict(ClusterId=43,JobStatus=1,_queried_schedd='ours'),
             dict(ClusterId=44,JobStatus=5,_queried_schedd='ours')]
        self.assertEqual(controller.external_workers(ads,42,dict(schedd_name='ours')),2)

    def test_account_coverage_failure_never_authorizes_factory_growth(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            snapshot=dict(nonce='verified',complete=True,checked_at_epoch=controller.time.time(),
                          account_owner='owner',account_pool='tweetybird04.cern.ch',account_coverage_verified=False,
                          account_error='coverage unknown')
            (root/'last_quota.json').write_text(json.dumps(snapshot))
            with patch.object(controller,'run_condor') as mutate:
                with self.assertRaises(controller.NativeCondorError):controller.account_workers(root,dict(account_owner='owner'))
            mutate.assert_not_called()

    def test_execute_audit_keeps_quota_valid_when_account_authentication_fails(self):
        text='space=/eos/user/j/jniedzie/ maxlogicalbytes=100000 usedlogicalbytes=100 maxfiles=1000 usedfiles=10\n'
        request=dict(nonce='one',account_owner='owner',account_pool='tweetybird04.cern.ch')
        with patch.object(quota_auditor.subprocess,'run',return_value=SimpleNamespace(stdout=text)),patch.object(quota_auditor,'query_account',side_effect=controller.NativeCondorError('authentication unavailable')) as query,patch.object(quota_auditor.time,'sleep'):
            result=quota_auditor.audit(request)
        self.assertTrue(result['complete']);self.assertFalse(result['account_coverage_verified'])
        self.assertEqual(query.call_count,3)
        self.assertEqual(result['free_bytes'],99900)
        self.assertFalse(query.call_args.kwargs['local_schedd'])

    def test_bulk_controller_increases_verified_factory_after_useful_progress(self):
        class EndCheck(Exception):pass
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            self.campaign(root,strata={'qcd':42},jobs=21,pilot=False,
                submission=dict(canary_cluster=None,canary_jobs=[800000],bulk_cluster=42))
            policy=json.loads((root/'policy.json').read_text());policy.update(self.adaptive_policy(),schedd_name='ours')
            (root/'policy.json').write_text(json.dumps(policy))
            (root/'capacity_state.json').write_text(json.dumps(dict(current_capacity=100,
                last_capacity_change_epoch=0,completed_at_change=0)))
            for job in range(10):self.receipt(root,job)
            (root/'last_quota.json').write_text(json.dumps(dict(nonce='verified',complete=True,
                checked_at_epoch=1200,account_checked_at_epoch=1200,free_bytes=10**9,free_files=10**6,
                account_owner=controller.pwd.getpwuid(controller.os.getuid()).pw_name,
                account_pool='tweetybird04.cern.ch',account_coverage_verified=True,
                account_workers=[dict(ClusterId=42,JobStatus=2,_queried_schedd='ours')])))
            def query(constraint,attributes,**kwargs):
                if 'JobMaterializeLimit' in attributes:return [dict(JobMaterializeLimit=150,JobMaterializeMaxIdle=100)]
                return [dict(ClusterId=42,ProcId=10,JobStatus=2,ShiftNtupleJob=10)]
            with patch.object(controller,'quota',return_value=(10**9,10**6)),patch.object(controller,'query',side_effect=query),patch.object(controller,'query_factory',return_value=[dict(ClusterId=42)]),patch.object(controller.time,'time',return_value=1200),patch.object(controller.time,'sleep',side_effect=EndCheck),patch.object(controller,'run_condor') as mutate:
                with self.assertRaises(EndCheck):controller.supervise(root)
            state=json.loads((root/'capacity_state.json').read_text())
            self.assertEqual(state['current_capacity'],150)
            self.assertEqual(mutate.call_args.args[0],['/usr/bin/condor_qedit','42',
                'JobMaterializeLimit','150','JobMaterializeMaxIdle','100'])

    def test_preparer_zero_pilot_balances_all_bins_and_freezes_sample_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'sources').mkdir();(root/'templates').mkdir()
            manifest=dict(eos_output='/eos/sample',strata={'qcd':4,'dy':4},events=8,jobs=4,events_per_job=2,
                sources=[dict(index=0,stratum='qcd',events=4),dict(index=1,stratum='dy',events=4)])
            (root/'manifest.json').write_text(json.dumps(manifest));(root/'runtime_freeze.json').write_text('{"sha256":"frozen"}')
            (root/'jobs.txt').write_text('0 0 2 0\n0 2 2 1\n1 0 2 2\n1 2 2 3\n')
            (root/'canaries.json').write_text(json.dumps([dict(source=0,job=800000),dict(source=1,job=800100)]))
            for index in (0,1):(root/'sources'/f'source{index:05d}.json').write_text('{}')
            for stage in range(1,5):(root/'templates'/f'step{stage}.py').write_text('process = None\n')
            args=['prepare',str(root),'--email','jeremi.niedziela@cern.ch','--pilot-size','0',
                '--worker-ceiling','300','--initial-workers','100','--worker-timeout-seconds','21600']
            with patch.object(sys,'argv',args),patch('builtins.print'):preparer.main()
            self.assertEqual([int(line.split()[3]) for line in (root/'jobs.txt').read_text().splitlines()],[0,2,1,3])
            self.assertEqual(json.loads((root/'manifest.json').read_text())['pilot_jobs'],[])
            submit=(root/'bulk.sub').read_text()
            self.assertIn('max_materialize = 100',submit);self.assertIn('max_idle = 100',submit)
            self.assertIn('+MaxRuntime = 21600',submit)
            policy=json.loads((root/'policy.json').read_text())
            self.assertEqual((policy['worker_ceiling'],policy['initial_workers'],policy['worker_timeout_seconds']),(300,100,21600))

    def test_completion_requires_exact_manifest_event_totals(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            self.campaign(root,submission=dict(canary_cluster=1,canary_jobs=[800000],pilot_cluster=2,bulk_cluster=3))
            for job in range(3):self.receipt(root,job,events=1)
            with patch.object(controller,'quota',return_value=(10**9,10**6)),patch.object(controller,'query',return_value=[]):
                with self.assertRaisesRegex(controller.GlobalProductionError,'frozen event inventory'):controller.supervise(root)
            self.assertFalse((root/'production_complete.json').exists())

    def test_stop_pauses_whole_factory_and_only_campaign_workers(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'submission.json').write_text(json.dumps(dict(bulk_cluster=42)))
            policy=dict(tag='my_campaign',attention_email='jeremi.niedziela@cern.ch')
            with patch.object(controller,'query',return_value=[dict(ClusterId=42)]) as query, patch.object(controller,'run_condor') as hold, patch.object(controller,'notify_once') as notify:
                controller.stop(root,policy,'stalled job')
                self.assertIn('ShiftSuiteTag == "my_campaign"',query.call_args.args[0])
                self.assertEqual(hold.call_args.args[0],['/usr/bin/condor_hold','42'])
                self.assertTrue((root/'stop_requested.json').exists())
                notify.assert_called_once_with(root,'jeremi.niedziela@cern.ch','stalled job')

    def test_stop_holds_unmaterialized_recovery_factories(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'submission.json').write_text(json.dumps(dict(
                bulk_cluster=42,pilot_cluster=41,recovery_clusters=[43,44])))
            policy=dict(tag='my_campaign',attention_email='jeremi.niedziela@cern.ch')
            with patch.object(controller,'query',return_value=[]),patch.object(controller,'run_condor') as hold,patch.object(controller,'notify_once'):
                controller.stop(root,policy,'EOS safety headroom exhausted')
            self.assertEqual([call.args[0] for call in hold.call_args_list],
                [['/usr/bin/condor_hold',str(cluster)] for cluster in (41,42,43,44)])

    def test_stop_holds_recorded_factories_when_scheduler_read_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'submission.json').write_text(json.dumps(dict(
                bulk_cluster=42,recovery_clusters=[43])))
            policy=dict(tag='my_campaign',attention_email='jeremi.niedziela@cern.ch')
            with patch.object(controller,'bounded_query',side_effect=controller.NativeCondorError('scheduler read unavailable')),patch.object(controller,'run_condor') as hold,patch.object(controller,'notify_once'):
                controller.stop(root,policy,'EOS safety headroom exhausted')
            self.assertEqual([call.args[0] for call in hold.call_args_list],
                [['/usr/bin/condor_hold','42'],['/usr/bin/condor_hold','43'],
                 ['/usr/bin/condor_hold','-constraint',
                  'ShiftNtupleProduction == true && ShiftSuiteTag == "my_campaign"']])
            state=json.loads((root/'controller_state.json').read_text())
            self.assertEqual(state['phase'],'blocked')
            self.assertIn('scheduler read unavailable',state['stop_errors'][0])

    def test_delayed_background_quota_audit_never_holds_production_or_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            def submit(command, **kwargs):
                self.assertEqual(command[0], '/usr/bin/condor_submit')
                self.assertIn('priority = 100', Path(command[-1]).read_text())
                return '123.0 - 123.0\n'
            with patch.object(controller,'run_condor',side_effect=submit) as commands, \
                 patch.object(controller,'bounded_query',return_value=[dict(JobStatus=1)]), \
                 patch.object(controller.time,'time',return_value=1000):
                self.assertEqual(controller.refresh_quota(root), {})
            with patch.object(controller,'run_condor') as mutate, \
                 patch.object(controller,'bounded_query',return_value=[dict(JobStatus=1)]), \
                 patch.object(controller.time,'time',return_value=3000):
                self.assertEqual(controller.refresh_quota(root), {})
                mutate.assert_not_called()
            self.assertEqual(commands.call_count,1)
            self.assertEqual(json.loads((root/'pending_quota.json').read_text())['status'],'pending')

    def test_background_quota_failure_retries_only_after_confirmed_terminal_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); folder=root/'audit';folder.mkdir()
            pending=dict(folder=str(folder),nonce='fixed',cluster=123,status='pending',submitted_at_epoch=1)
            (root/'pending_quota.json').write_text(json.dumps(pending))
            (folder/'quota_result.json').write_text(json.dumps(dict(nonce='fixed',complete=False,error='EOS read timeout')))
            with patch.object(controller,'bounded_query',return_value=[dict(JobStatus=2)]), \
                 patch.object(controller,'run_condor') as mutate:
                controller.refresh_quota(root)
                mutate.assert_not_called()
            self.assertEqual(json.loads((root/'pending_quota.json').read_text())['status'],'pending')
            with patch.object(controller,'bounded_query',return_value=[dict(JobStatus=5)]), \
                 patch.object(controller,'run_condor') as mutate:
                controller.refresh_quota(root)
                controller.refresh_quota(root)
                mutate.assert_not_called()
            self.assertEqual(json.loads((root/'pending_quota.json').read_text())['status'],'failed')

    def test_background_quota_accepts_verified_exhaustion_for_global_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); folder=root/'audit';folder.mkdir()
            pending=dict(folder=str(folder),nonce='fixed',cluster=123,status='pending',submitted_at_epoch=1)
            (root/'pending_quota.json').write_text(json.dumps(pending))
            result=dict(nonce='fixed',complete=True,checked_at_epoch=1000,free_bytes=0,free_files=10)
            (folder/'quota_result.json').write_text(json.dumps(result))
            with patch.object(controller.time,'time',return_value=1001):
                self.assertEqual(controller.refresh_quota(root),result)
            self.assertEqual(json.loads((root/'last_quota.json').read_text()),result)

    def test_observer_fault_restarts_without_any_worker_hold(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);policy=dict(attention_email='jeremi.niedziela@cern.ch')
            with patch.object(controller,'supervise',side_effect=[TypeError('bad telemetry'),None]) as observe, \
                 patch.object(controller,'notify_once'),patch.object(controller,'stop') as stop, \
                 patch.object(controller,'run_condor') as mutate,patch.object(controller.time,'sleep'):
                controller.observe_with_recovery(root,policy,'controller')
                self.assertEqual(observe.call_count,2)
                stop.assert_not_called();mutate.assert_not_called()
            fault=json.loads((root/'controller_fault.json').read_text())
            self.assertEqual(fault['worker_action'],'none')
            with patch.object(controller,'supervise',side_effect=controller.GlobalProductionError('verified exhausted quota')):
                with self.assertRaises(controller.GlobalProductionError):
                    controller.observe_with_recovery(root,policy,'controller')

    def test_async_stale_account_coverage_does_not_call_blocking_quota(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(controller,'quota') as blocking:
                with self.assertRaises(controller.NativeCondorError):
                    controller.account_workers(Path(directory),dict(asynchronous_audits=True))
                blocking.assert_not_called()

    def test_deferred_recovery_waits_for_account_and_local_allocation_to_drain(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            submitted=dict(deferred_recovery_clusters=[42],recovery_worker_budgets={'42':6})
            (root/'submission.json').write_text(json.dumps(submitted))
            full=[dict(JobStatus=1,ClusterId=1,ProcId=i) for i in range(1000)];drained=full[:994]
            with patch.object(controller,'set_factory_budget') as activate:
                controller.activate_deferred_recovery(root,dict(worker_ceiling=1000),submitted,full,drained)
                controller.activate_deferred_recovery(root,dict(worker_ceiling=1000),submitted,drained,full)
                activate.assert_not_called()
                result=controller.activate_deferred_recovery(root,dict(worker_ceiling=1000),submitted,drained,drained)
                activate.assert_called_once_with(root,42,6,6)
            self.assertEqual(result['deferred_recovery_clusters'],[])
            self.assertEqual(json.loads((root/'submission.json').read_text())['deferred_recovery_clusters'],[])

    def test_deferred_recovery_counts_union_of_changed_and_remote_worker_snapshots(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            submitted=dict(deferred_recovery_clusters=[42],recovery_worker_budgets={'42':6})
            (root/'submission.json').write_text(json.dumps(submitted))
            policy=dict(worker_ceiling=1000,schedd_name='ours')
            current=[dict(ClusterId=1,ProcId=i,JobStatus=2) for i in range(994)]
            # The cached global read lacks recently started local workers and
            # includes equal numeric job IDs on a different schedd.
            cached=([dict(ClusterId=1,ProcId=i,JobStatus=2,_queried_schedd='ours') for i in range(500)]+
                    [dict(ClusterId=1,ProcId=i,JobStatus=2,_queried_schedd='other') for i in range(10)])
            with patch.object(controller,'set_factory_budget') as activate:
                controller.activate_deferred_recovery(root,policy,submitted,current,cached)
                activate.assert_not_called()
                controller.activate_deferred_recovery(root,policy,submitted,current,cached[:500])
                activate.assert_called_once_with(root,42,6,6)

    def test_bulk_monitoring_continues_during_background_audit_delay(self):
        class EndCheck(BaseException):pass
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            self.campaign(root,pilot=False,submission=dict(bulk_cluster=42))
            policy=json.loads((root/'policy.json').read_text());policy['asynchronous_audits']=True
            with patch.object(controller,'refresh_quota',return_value={}), \
                 patch.object(controller,'quota') as blocking, \
                 patch.object(controller,'query',return_value=[dict(ClusterId=42,ProcId=0,JobStatus=2,
                     ShiftNtupleJob=0,JobCurrentStartDate=1000,ShiftNtupleProgressEpoch=None,
                     ShiftNtupleStageTimeout=None,ShiftNtupleStageDeadlineEpoch=None)]), \
                 patch.object(controller,'run_condor') as mutate,patch.object(controller.time,'time',return_value=1000), \
                 patch.object(controller.time,'sleep',side_effect=EndCheck):
                with self.assertRaises(EndCheck):controller.supervise(root,policy)
                blocking.assert_not_called();mutate.assert_not_called()
            self.assertEqual(json.loads((root/'controller_state.json').read_text())['phase'],'bulk')
            self.assertFalse((root/'stop_requested.json').exists())

    def test_background_audit_ambiguous_submit_is_never_repeated(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with patch.object(controller,'run_condor',return_value='lost submit response') as commands, \
                 patch.object(controller,'bounded_query',return_value=[]):
                controller.refresh_quota(root)
                controller.refresh_quota(root)
                self.assertEqual(commands.call_count,1)
            self.assertEqual(json.loads((root/'pending_quota.json').read_text())['status'],'submitting')

    def test_scheduler_quota_check_delegates_to_execute_node(self):
        import time
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            def submit(command,**kwargs):
                self.assertEqual(command[0],'/usr/bin/condor_submit')
                folder=Path(command[-1]).parent
                result=dict(nonce=folder.name,complete=True,checked_at_epoch=time.time(),free_bytes=123456,free_files=456)
                (folder/'quota_result.json').write_text(json.dumps(result))
                return '123.0 - 123.0\n'
            with patch.object(controller,'run_condor',side_effect=submit):
                self.assertEqual(controller.quota(root,heartbeat=False),(123456,456))

    def test_quota_refreshes_completed_result_after_long_scheduler_outage(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            now=[1000]
            folders=[]
            def submit(command,**kwargs):
                self.assertEqual(command[0],'/usr/bin/condor_submit')
                folder=Path(command[-1]).parent
                folders.append(folder)
                if len(folders)==2:
                    (folder/'quota_result.json').write_text(json.dumps(dict(nonce=folder.name,
                        complete=True,checked_at_epoch=now[0],free_bytes=123456,free_files=456)))
                return f'{len(folders)}.0 - {len(folders)}.0\n'
            def recovered(*args,**kwargs):
                # The audit completed early, but its scheduler read took
                # longer than both freshness and the polling deadline.
                (folders[0]/'quota_result.json').write_text(json.dumps(dict(nonce=folders[0].name,
                    complete=True,checked_at_epoch=1010,free_bytes=100000,free_files=400)))
                now[0]=2000
                return [dict(JobStatus=4,ExitCode=0)]
            with patch.object(controller,'run_condor',side_effect=submit) as commands,patch.object(controller,'recoverable_query',side_effect=recovered),patch.object(controller.time,'time',side_effect=lambda:now[0]),patch.object(controller.time,'sleep') as sleep:
                self.assertEqual(controller.quota(root,heartbeat=False),(123456,456))
            self.assertEqual(commands.call_count,2)
            sleep.assert_not_called()
            self.assertTrue((folders[0]/'quota_result.json').exists())
            self.assertTrue((folders[0]/'refresh.json').exists())
            self.assertEqual(json.loads((root/'last_quota.json').read_text())['nonce'],folders[1].name)

    def test_quota_does_not_replace_a_stale_result_from_a_pending_audit(self):
        class EndCheck(BaseException):pass
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            def submit(command,**kwargs):
                self.assertEqual(command[0],'/usr/bin/condor_submit')
                folder=Path(command[-1]).parent
                (folder/'quota_result.json').write_text(json.dumps(dict(nonce=folder.name,
                    complete=True,checked_at_epoch=800,free_bytes=123456,free_files=456)))
                return '123.0 - 123.0\n'
            with patch.object(controller,'run_condor',side_effect=submit) as commands,patch.object(controller,'recoverable_query',return_value=[dict(JobStatus=2)]),patch.object(controller.time,'time',return_value=1000),patch.object(controller.time,'sleep',side_effect=EndCheck):
                with self.assertRaises(EndCheck):controller.quota(root,heartbeat=False)
            self.assertEqual(commands.call_count,1)
            self.assertFalse((root/'last_quota.json').exists())

    def test_quota_keeps_invalid_outage_results_fail_closed(self):
        for updates in ({'nonce':'wrong'},{'complete':False}):
            with self.subTest(updates=updates),tempfile.TemporaryDirectory() as directory:
                root=Path(directory)
                now=[1000]
                folders=[]
                def submit(command,**kwargs):
                    folders.append(Path(command[-1]).parent)
                    return '123.0 - 123.0\n'
                def recovered(*args,**kwargs):
                    result=dict(nonce=folders[0].name,complete=True,checked_at_epoch=1010,
                                free_bytes=123456,free_files=456)
                    result.update(updates)
                    (folders[0]/'quota_result.json').write_text(json.dumps(result))
                    now[0]=2000
                    return [dict(JobStatus=4,ExitCode=0)]
                with patch.object(controller,'run_condor',side_effect=submit) as commands,patch.object(controller,'recoverable_query',side_effect=recovered),patch.object(controller.time,'time',side_effect=lambda:now[0]):
                    with self.assertRaisesRegex(RuntimeError,'EOS quota audit failed'):
                        controller.quota(root,heartbeat=False)
                self.assertEqual(commands.call_count,1)
                self.assertFalse((root/'last_quota.json').exists())

    def test_short_checks_release_pilot_then_bulk_without_double_counting(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'results').mkdir()
            manifest=dict(strata={'qcd':6},sources=[dict(index=0,stratum='qcd',events=6)],
                events_per_job=2,jobs=3,events=6,minimum_free_bytes=0,minimum_free_files=0,
                pilot_jobs=[0,1],pilot_events=4,pilot_strata={'qcd':4},pilot_sources=[dict(index=0,stratum='qcd',events=4)])
            (root/'manifest.json').write_text(json.dumps(manifest))
            (root/'policy.json').write_text(json.dumps(dict(queue_timeout_seconds=7200,worker_timeout_seconds=16200,
                setup_timeout_seconds=1200,completion_timeout_seconds=16200,canary_timeout_seconds=3600)))
            (root/'submission.json').write_text(json.dumps(dict(canary_cluster=1,canary_jobs=[800000])))
            def receipt(job):
                result=dict(job=job,exit_code=0,complete=True,source_stratum='qcd',nano_path='/eos/nano.root',
                    report_sha256='validated',events=2,compressed_event_bytes=20,nano_bytes=120,evidence_bytes=10,
                    wall_seconds=2,validated_tier_events={t:2 for t in ('GEN','SIM','DIGIHLT','RECO','NANO')})
                (root/'results'/f'status{job}.json').write_text(json.dumps(result))
            receipt(800000)
            releases=[]
            def submit(root,name):
                releases.append(name)
                if name=='pilot': receipt(0);return 2
                receipt(1);receipt(2);return 3
            with patch.object(controller,'quota',return_value=(10**9,10**6)),patch.object(controller,'query',return_value=[]),patch.object(controller,'submit',side_effect=submit),patch.object(controller,'run_condor'),patch.object(controller.time,'sleep'):
                controller.supervise(root)
            self.assertEqual(releases,['pilot','bulk'])
            complete=json.loads((root/'production_complete.json').read_text())
            self.assertEqual(complete['tier_events']['qcd']['NANO'],6)


if __name__ == '__main__':
    unittest.main()
