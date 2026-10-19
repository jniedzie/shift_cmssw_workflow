# Code style: AI-code. AI-generated streaming pipeline safety regression tests.
import copy
import json
import hashlib
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
import subprocess
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import run_shift_stream_controller as module
import prepare_shift_stream_controller as preparation


class StreamTests(unittest.TestCase):
    def test_prefix_scheduling_starts_the_missing_scan_before_later_reconstruction(self):
        scan = dict(task_id='scan1', stage='prepare', cohort_id='source_production_qcd_5to10_0001',
                    priority=0, operational_canary=True)
        reco = dict(task_id='reco13', stage='reco', cohort_id='source_production_qcd_20toinf_0013',
                    priority=999999)
        policy = {'prioritize_source_prefix': True}
        self.assertLess(module.task_scheduling_key(scan, policy), module.task_scheduling_key(reco, policy))
        self.assertGreater(module.task_scheduling_key(scan, {}), module.task_scheduling_key(reco, {}))

    def test_prefix_scheduling_keeps_publication_first_and_tail_ties(self):
        scan = dict(task_id='scan1', stage='prepare', cohort_id='source_production_qcd_5to10_0001')
        reco = dict(task_id='tail1', stage='reco', cohort_id='source_production_qcd_20toinf_0001', priority=10)
        later_scan = dict(scan, task_id='scan2', cohort_id='source_production_qcd_5to10_0002',
                          operational_canary=True)
        hist = dict(task_id='hist', stage='histogram', cohort_id='source_production_qcd_20toinf_0030')
        merge = dict(task_id='merge', stage='merge', cohort_id='qcd_GEN_prefix_000002')
        policy = {'prioritize_source_prefix': True}
        ordered = sorted([later_scan, scan, reco, hist, merge], key=lambda t: module.task_scheduling_key(t, policy))
        self.assertEqual([t['task_id'] for t in ordered], ['merge', 'hist', 'tail1', 'scan1', 'scan2'])

    def test_prefix_scheduling_rejects_unknown_source_or_policy(self):
        task = dict(task_id='unknown', stage='prepare', cohort_id='unknown')
        with self.assertRaisesRegex(ValueError, 'numbered source'):
            module.task_scheduling_key(task, {'prioritize_source_prefix': True})
        for value in (None, 0, 1, 'true'):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'explicit boolean'):
                module.stream_admission_policy({'prioritize_source_prefix': value})

    def setUp(self):
        parent = SCRIPTS.parent / 'tmp'
        parent.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='20261016-stream-tests-', dir=parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'controller'
        self.admission = dict(worker_ceiling=4, account_ceiling=1000,
            minimum_free_bytes=50, minimum_free_files=20, minimum_AFS_free_bytes=10,
            reserved_bytes_per_worker=10, reserved_files_per_worker=2,
            observation_max_age_seconds=600, pre_reservation_seconds=900,
            account_owner='jniedzie', account_pool='pool', schedd_name='local', suite_tag='stream')
        self.policy = dict(tag='stream', admission=self.admission,
            worker_limits=dict(queue_timeout_seconds=7200, worker_timeout_seconds=32400,
                               setup_timeout_seconds=1800), manager_runtime_seconds=604800)
        preparation.prepare(self.root, self.policy)
        self.observation = dict(complete=True, quota_checked_at_epoch=1000,
            quota_observation_started_at_epoch=999,
            remote_account_checked_at_epoch=1000, remote_account_coverage_verified=True,
            excluded_local_schedd='local', account_schedds=['local', 'remote'],
            remote_account_workers=[], free_bytes=10000, free_files=10000)
        self.reader = mock.Mock(return_value=[])
        self.commander = mock.Mock(return_value='123.0 - 123.0\n')
        self.controller = module.StreamController(self.root, self.reader, self.commander)

    def task(self, identity='source0_reco', stage='reco', dependencies=None, canary=True):
        directory = self.root / 'cohorts' / identity
        directory.mkdir(exist_ok=True)
        submit = directory / 'worker.sub'
        submit.write_text('universe = vanilla\ninitialdir = '+str(directory)+'\nqueue 1\n')
        task = dict(schema='shift-stream-task-v1', task_id=identity, cohort_id='source0',
            source_id='production_source0', stage=stage, depends_on=dependencies or [],
            submit_file=str(submit), working_directory=str(directory),
            status_path=str(directory / 'status.json'), event_log=str(directory / 'events.log'),
            completion_checks={'complete': True, 'exit_code': 0, 'events': 10,
                               'source_descriptor_sha256': 'a'*64},
            matching_status_fields=[['nano_sha256', 'nano_readback_sha256']],
            input_sha256={str(submit): module.sha(submit)}, operational_canary=canary)
        module.save(self.root / 'tasks' / (identity+'.json'), task)
        return task

    def step(self, **kwargs):
        with mock.patch.object(module, 'refresh_quota', return_value=self.observation), \
                mock.patch.object(module, 'afs_headroom', return_value=10000):
            return self.controller.step(now=1000, **kwargs)

    def events(self, task, text):
        Path(task['event_log']).write_text(text)

    def successful_status(self, task):
        module.save(task['status_path'], dict(complete=True, exit_code=0, events=10,
            source_descriptor_sha256='a'*64, nano_sha256='b'*64,
            nano_readback_sha256='b'*64, analysis_release=False))

    def test_canary_has_persistent_submission_intent_and_single_job(self):
        task = self.task()
        result = self.step()
        self.assertEqual(result['submitted_this_poll'], 1)
        self.assertEqual(self.controller.ledger['source0_reco']['job'], [123, 0])
        command = self.commander.call_args[0][0]
        self.assertIn('+ShiftStreamTask = "source0_reco"', command)
        self.assertEqual(self.commander.call_args.kwargs['cwd'], task['working_directory'])
        self.assertTrue(self.commander.call_args.kwargs['local_schedd'])

    def test_stream_preparation_defaults_to_actual_reservations_and_keeps_explicit_wave_mode(self):
        self.assertFalse(self.controller.admission_policy['reserve_full_worker_ceiling'])
        self.assertFalse(module.read(self.root/'admission/policy.json')['reserve_full_worker_ceiling'])
        destination = self.root.parent/'explicit_legacy_wave'
        policy = self.policy | {'admission': self.admission | {'reserve_full_worker_ceiling': True}}
        preparation.prepare(destination, policy)
        self.assertTrue(module.read(destination/'admission/policy.json')['reserve_full_worker_ceiling'])
        explicit = module.StreamController(destination, self.reader, self.commander)
        self.assertTrue(explicit.admission_policy['reserve_full_worker_ceiling'])

    def test_bulk_waits_for_explicit_operational_gate(self):
        self.task(canary=False)
        self.assertEqual(self.step()['submitted_this_poll'], 0)
        self.commander.assert_not_called()

    def test_idle_limit_counts_successful_submissions_before_next_queue_observation(self):
        self.task('source0_reco')
        self.task('source1_reco')
        self.controller.policy['maximum_idle_workers'] = 1
        result = self.step()
        self.assertEqual(result['submitted_this_poll'], 1)
        self.assertEqual(result['admission']['reason'], 'This production has reached its idle-worker ceiling')
        self.assertEqual(result['admission']['reserved_idle_workers'], 1)
        self.assertEqual(self.commander.call_count, 1)
        # The scheduler still has not exposed the submitted nonce. Preserve its
        # idle claim rather than submitting another job or repeating this one.
        self.assertEqual(self.step()['submitted_this_poll'], 0)
        self.assertEqual(self.commander.call_count, 1)

    def test_idle_limit_preserves_high_running_worker_capacity(self):
        policy = self.policy | {'maximum_idle_workers': 20}
        ads = [dict(JobUniverse=5, JobStatus=2, ShiftStreamCampaign='stream',
                    ShiftStreamTask='running_' + str(index)) for index in range(800)]
        ads.append(dict(JobUniverse=5, JobStatus=1, ShiftStreamCampaign='other',
                        ShiftStreamTask='other_idle'))
        capacity = module.idle_worker_capacity(policy, ads, {}, {}, 'new')
        self.assertTrue(capacity['admitted'])
        self.assertEqual(capacity['native_idle_workers'], 0)
        self.assertEqual(capacity['proposed_idle_workers'], 1)
        self.assertNotIn('maximum_idle_workers', self.controller.admission_policy)

    def test_idle_limit_counts_unseen_PREs_once_and_releases_only_completed_claims(self):
        policy = self.policy | {'maximum_idle_workers': 2}
        reservations = {name: {'at_epoch': 999} for name in ('queued', 'pending', 'complete')}
        ads = [dict(JobUniverse=5, JobStatus=1, ShiftStreamCampaign='stream', ShiftStreamTask='queued')]
        ledger = {'queued': {'phase': 'submitted'}, 'pending': {'phase': 'submitting'},
                  'complete': {'phase': 'complete'}}
        capacity = module.idle_worker_capacity(policy, ads, reservations, ledger, 'new')
        self.assertFalse(capacity['admitted'])
        self.assertEqual(capacity['native_idle_workers'], 1)
        self.assertEqual(capacity['pending_idle_workers'], 1)
        self.assertEqual(capacity['proposed_idle_workers'], 3)
        # An already reserved candidate keeps its existing slot.
        self.assertTrue(module.idle_worker_capacity(policy, ads, reservations, ledger, 'pending')['admitted'])

    def test_idle_limit_applies_to_dry_run_submissions_in_same_poll(self):
        self.task('source0_reco')
        self.task('source1_reco')
        self.controller.policy['maximum_idle_workers'] = 1
        module.save(self.root / 'admission/last_quota.json', self.observation)
        self.assertEqual(self.step(dry_run=True)['submitted_this_poll'], 1)
        self.commander.assert_not_called()

    def test_absent_idle_limit_preserves_existing_submission_policy(self):
        self.task('source0_reco')
        self.task('source1_reco')
        self.assertEqual(self.step()['submitted_this_poll'], 2)
        self.assertEqual(self.commander.call_count, 2)

    def test_invalid_idle_limit_is_rejected_before_freezing(self):
        for value in (True, 0, -1, 5, '2'):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'Maximum idle workers'):
                module.stream_admission_policy(self.policy | {'maximum_idle_workers': value})

    def test_idle_limit_rejects_unknown_native_task_identity(self):
        policy = self.policy | {'maximum_idle_workers': 2}
        with self.assertRaisesRegex(ValueError, 'exact task identity'):
            module.idle_worker_capacity(policy,
                [dict(JobUniverse=5, JobStatus=1, ShiftStreamCampaign='stream')], {}, {}, 'new')

    def test_slow_preparation_uses_fresh_audit_clock_and_PRE_birth_time(self):
        self.task()
        self.controller.policy['prepare_command'] = ['test-only-preparation']
        clock = [1000]
        def prepare(*args, **kwargs):
            clock[0] = 1601  # No sleep: model preparation lasting over ten minutes.
        def fresh_quota(*args, **kwargs):
            clock[0] = 1603
            return self.observation | dict(quota_observation_started_at_epoch=1601,
                quota_checked_at_epoch=1602, remote_account_checked_at_epoch=1602)
        with mock.patch.object(module.time, 'time', side_effect=lambda: clock[0]), \
                mock.patch.object(module.subprocess, 'run', side_effect=prepare), \
                mock.patch.object(module, 'refresh_quota', side_effect=fresh_quota), \
                mock.patch.object(module, 'afs_headroom', return_value=10000):
            result = self.controller.step()
        self.assertEqual(result['submitted_this_poll'], 1)
        self.assertEqual(self.reader.call_count, 2)
        self.assertEqual(result['checked_at_epoch'], 1603)
        self.assertEqual(self.controller.ledger['source0_reco']['submitted_at_epoch'], 1603)
        self.assertEqual(module.read(self.root/'admission/pre_reservations.json')['source0_reco']['at_epoch'], 1603)

    def test_post_preparation_native_queue_refresh_blocks_new_capacity_and_unknown_state(self):
        self.task()
        self.controller.policy['prepare_command'] = ['test-only-preparation']
        self.controller.admission_policy['account_ceiling'] = 4
        other = dict(JobUniverse=5, JobStatus=2)
        self.reader.side_effect = [[], [other]*4]
        with mock.patch.object(module.subprocess, 'run'), \
                mock.patch.object(module, 'refresh_quota', return_value=self.observation), \
                mock.patch.object(module, 'afs_headroom', return_value=10000):
            result = self.controller.step(now=1000)
        self.assertEqual(result['submitted_this_poll'], 0)
        self.assertIn('capacity', result['admission']['reason'])
        self.commander.assert_not_called()
        self.reader.side_effect = [[], module.NativeCondorError('fresh queue unavailable')]
        with mock.patch.object(module.subprocess, 'run'), \
                mock.patch.object(module, 'refresh_quota', return_value=self.observation), \
                self.assertRaises(module.NativeCondorError):
            self.controller.step(now=1000)
        self.commander.assert_not_called()

    def test_gate_requires_authorities_and_true_pass(self):
        gate = self.root / 'operational_gates' / 'canary.json'
        policy = {'operational_gates': [{'path': str(gate),
            'expected': {'authorities.worker_sha256': 'a'*64}}]}
        for receipt, expected in [({'passed': False}, False),
            ({'passed': True, 'authorities': {'worker_sha256': 'c'*64}}, False),
            ({'passed': True, 'authorities': {'worker_sha256': 'a'*64}}, True)]:
            module.save(gate, receipt)
            self.assertEqual(module.operational_gates(policy), expected)

    def test_no_submission_after_unknown_scheduler_read(self):
        self.task()
        self.reader.side_effect = module.NativeCondorError('unavailable')
        with self.assertRaises(module.NativeCondorError):
            self.step()
        self.commander.assert_not_called()

    def test_ambiguous_submission_is_never_retried(self):
        self.task()
        self.commander.side_effect = module.NativeCondorError('lost response')
        with self.assertRaises(module.NativeCondorError):
            self.step()
        self.assertEqual(self.controller.ledger['source0_reco']['phase'], 'submitting')
        self.commander.side_effect = None
        self.step()
        self.assertEqual(self.commander.call_count, 1)

    def test_ambiguous_submission_recovers_exact_native_nonce(self):
        task = self.task()
        self.commander.side_effect = module.NativeCondorError('lost response')
        with self.assertRaises(module.NativeCondorError):self.step()
        record = self.controller.ledger['source0_reco']
        self.reader.return_value = [dict(ClusterId=456, ProcId=0, JobStatus=2,
            JobCurrentStartDate=999, Iwd=task['working_directory'],
            ShiftStreamCampaign='stream', ShiftStreamTask='source0_reco',
            ShiftStreamNonce=record['nonce'])]
        self.step()
        self.assertEqual(record['job'], [456, 0])
        self.assertEqual(self.commander.call_count, 1)

    def test_success_requires_native_exit_and_complete_semantic_status(self):
        task = self.task()
        self.step()
        self.successful_status(task)
        self.step()
        self.assertEqual(self.controller.ledger['source0_reco']['phase'], 'submitted')
        self.events(task, '001 (123.000.000) execution\n...\n005 (123.000.000) exit\n'
                         '    (1) Normal termination (return value 0)\n...\n')
        self.step()
        self.assertEqual(self.controller.ledger['source0_reco']['phase'], 'complete')
        self.assertEqual(self.commander.call_count, 1)

    def test_partial_event_log_is_not_terminal_proof(self):
        task = self.task()
        self.events(task, '005 (123.000.000) exit\nNormal termination (return value 0)')
        self.assertIsNone(module.exit_event(task['event_log'], 123, 0)['terminal'])

    def test_new_execution_requires_its_own_exit(self):
        task = self.task()
        self.step()
        self.successful_status(task)
        self.events(task, '001 (123.000.000) execution\n...\n'
                         '005 (123.000.000) exit\n'
                         'Normal termination (return value 0)\n...\n'
                         '001 (123.000.000) new execution\n...\n')
        self.step()
        self.assertIsNone(module.exit_event(task['event_log'], 123, 0)['terminal'])
        self.assertEqual(self.controller.ledger['source0_reco']['phase'], 'submitted')
        self.assertEqual(self.commander.call_count, 1)

    def test_readback_mismatch_blocks_completion(self):
        task = self.task()
        status = dict(complete=True, exit_code=0, events=10, source_descriptor_sha256='a'*64,
                      nano_sha256='b'*64, nano_readback_sha256='c'*64)
        with self.assertRaisesRegex(ValueError, 'readback mismatch'):
            module.validate_completion(task, status)

    def test_dependencies_require_complete_not_merely_published_filename(self):
        task = self.task(stage='histogram', dependencies=['source0_reco'])
        self.assertFalse(module.dependencies_complete(task, {'source0_reco': {'phase': 'submitted'}}))
        self.assertTrue(module.dependencies_complete(task, {'source0_reco': {'phase': 'complete'}}))

    def test_failed_cohort_is_not_silently_omitted_or_retried(self):
        task = self.task()
        self.step()
        self.events(task, '005 (123.000.000) exit\nNormal termination (return value 1)\n...\n')
        self.task(identity='another_reco')
        self.assertEqual(self.step()['phase'], 'attention_required')
        self.step()
        self.assertEqual(self.commander.call_count, 1)

    def test_isolated_detector_timeout_quarantines_source_and_descendants_but_not_other_sources(self):
        self.controller.policy['isolate_failed_cohorts'] = True
        failed = self.task()
        failed['completion_checks']['selected_event_ids_sha256'] = 'c'*64
        module.save(self.root/'tasks/source0_reco.json', failed)
        self.step()
        result = Path(failed['working_directory'])/'result.json'
        module.save(result, dict(schema='shift-gen-to-nano-receipt-v1', complete=False,
            current_tier='SIM', validated_tier_events={'GEN': 10}, events=10,
            source_descriptor_sha256='a'*64, selected_event_ids_sha256='c'*64,
            error="TimeoutError('SIM made no event progress for 3600 seconds')"))
        self.events(failed, '001 (123.000.000) execution\n...\n005 (123.000.000) exit\n'
                            'Normal termination (return value 1)\n...\n')
        sibling = self.task(identity='source0_other')
        dependent = self.task(identity='snapshot', stage='merge', dependencies=['source0_other'])
        dependent['cohort_id'] = 'snapshot'
        module.save(self.root/'tasks/snapshot.json', dependent)
        other = self.task(identity='source1_reco')
        other['cohort_id'] = 'source1'
        module.save(self.root/'tasks/source1_reco.json', other)
        report = self.step()
        self.assertEqual(report['submitted_this_poll'], 1)
        self.assertEqual(report['quarantined_cohorts'], ['source0'])
        self.assertEqual(report['quarantined_tasks'], 3)
        self.assertEqual(report['problems'], [])
        self.assertEqual(self.controller.ledger['source0_reco']['phase'], 'failed')
        self.assertEqual(self.controller.ledger['source0_reco']['failure_scope'], 'cohort')
        self.assertEqual(self.controller.ledger['source0_other']['phase'], 'waiting')
        self.assertEqual(self.controller.ledger['source1_reco']['phase'], 'submitted')
        self.assertEqual(self.commander.call_count, 2)
        self.step()
        self.assertEqual(self.commander.call_count, 2)
        module.save(result, {'changed': True})
        with self.assertRaisesRegex(ValueError, 'failure evidence changed'):
            self.step()

    def test_only_proven_detector_execution_failures_are_isolated(self):
        task = self.task()
        task['completion_checks']['selected_event_ids_sha256'] = 'c'*64
        report = dict(schema='shift-gen-to-nano-receipt-v1', complete=False,
            current_tier='RECO', validated_tier_events={'GEN': 10}, events=10,
            source_descriptor_sha256='a'*64, selected_event_ids_sha256='c'*64,
            error="CalledProcessError(1, ['cmsRun', 'step3_cfg.py'])")
        path = Path(task['working_directory'])/'result.json'
        proof = {'executed': True, 'terminal': 1}
        module.save(path, report)
        self.assertTrue(module.isolated_detector_failure(task, proof))
        for change in ({'current_tier': 'RECO_CONFIG'}, {'current_tier': 'RECO_AUDIT'},
                {'current_tier': 'PUBLISHING'}, {'source_descriptor_sha256': 'd'*64},
                {'error': "ValueError('Simulation changed GEN weights')"},
                {'error': "CalledProcessError(1, ['xrdcp', 'input.root'])"},
                {'validated_tier_events': {'GEN': 0}}):
            module.save(path, report | change)
            with self.subTest(change=change):
                self.assertFalse(module.isolated_detector_failure(task, proof))
        module.save(path, report)
        self.assertFalse(module.isolated_detector_failure(task, {'executed': False, 'terminal': 1}))
        self.assertFalse(module.isolated_detector_failure(task, {'executed': True, 'terminal': 'signal_or_unknown'}))

    def held_detector_failure(self):
        task = self.task()
        directory = Path(task['working_directory'])
        submit = Path(task['submit_file'])
        submit.write_text('universe = vanilla\ninitialdir = '+str(directory)+
            '\nexecutable = /frozen/worker.sh\narguments = pinned_request pinned_tools request.json\nqueue 1\n')
        task['input_sha256'][str(submit)] = module.sha(submit)
        task['completion_checks']['selected_event_ids_sha256'] = 'c'*64
        module.save(self.root/'tasks/source0_reco.json', task)
        self.controller.policy['isolate_failed_cohorts'] = True
        self.step()
        self.events(task, '001 (123.000.000) execution\n...\n'
            '012 (123.000.000) Job was held.\n'
            ' The job attribute OnExitHold expression evaluated to TRUE\n Code 3 Subcode 0\n...\n')
        (directory/'return_payload.tar.gz').write_bytes(b'empty archive fixture')
        report = dict(schema='shift-gen-to-nano-receipt-v1', complete=False, exit_code=1,
            current_tier='NANO', validated_tier_events={'GEN': 10, 'RECO': 10, 'NANO': 0},
            events=10, source_descriptor_sha256='a'*64, selected_event_ids_sha256='c'*64,
            error="TimeoutError('NANO made no event progress for 3600 seconds; records started=14')")
        module.save(directory/'result.json', report)
        status = {key: report[key] for key in ('complete', 'exit_code', 'events',
            'source_descriptor_sha256', 'selected_event_ids_sha256', 'validated_tier_events', 'error')}
        status.update(stage='reco', cohort_id=task['cohort_id'],
            return_payload_sha256=module.sha(directory/'return_payload.tar.gz'))
        module.save(task['status_path'], status)
        ad = dict(ClusterId=123, ProcId=0, GlobalJobId='local#123.0#999',
            Owner='jniedzie', JobUniverse=5, JobStatus=5, Iwd=str(directory),
            Cmd='/frozen/worker.sh', Args='pinned_request pinned_tools request.json',
            ShiftStreamCampaign='stream', ShiftStreamTask=task['task_id'],
            ShiftStreamNonce=self.controller.ledger[task['task_id']]['nonce'],
            ExitCode=1, ExitBySignal=False, HoldReasonCode=3, HoldReasonSubCode=0,
            HoldReason='OnExitHold expression evaluated to TRUE')
        return task, report, status, ad

    def test_on_exit_hold_quarantines_only_proven_detector_failure_without_retry(self):
        task, report, status, ad = self.held_detector_failure()
        self.assertEqual(module.exit_event(task['event_log'], 123, 0),
                         {'executed': True, 'terminal': None})
        self.reader.return_value = [ad]
        other = self.task(identity='source1_reco')
        other['cohort_id'] = 'source1'
        module.save(self.root/'tasks/source1_reco.json', other)
        heartbeat = self.step()
        record = self.controller.ledger[task['task_id']]
        self.assertEqual((record['phase'], record['failure_scope']), ('failed', 'cohort'))
        self.assertTrue(record['native_exit']['held_on_exit'])
        self.assertEqual(heartbeat['quarantined_cohorts'], ['source0'])
        self.assertEqual(heartbeat['submitted_this_poll'], 1)
        self.step()
        self.assertEqual(self.commander.call_count, 2)

    def test_on_exit_hold_rejects_missing_contradictory_or_unrelated_native_facts(self):
        task, report, status, ad = self.held_detector_failure()
        record = self.controller.ledger[task['task_id']]
        for change in ({'Owner': 'other'}, {'Iwd': '/unrelated'}, {'Cmd': '/other/worker.sh'},
                {'Args': 'different'}, {'ShiftStreamNonce': 'other'}, {'JobUniverse': 7},
                {'ClusterId': 456}, {'GlobalJobId': None}, {'JobStatus': 2},
                {'ExitCode': 0}, {'ExitCode': True}, {'ExitBySignal': True},
                {'HoldReasonCode': 16}, {'HoldReason': 'Manual user hold'},
                {'ShiftStreamTask': 'other'}, {'ShiftStreamCampaign': 'other'}):
            with self.subTest(change=change):
                self.assertIsNone(module.held_detector_exit(task, record, ad | change,
                                                          self.controller.policy))
        for key in ('ExitCode', 'ExitBySignal', 'GlobalJobId', 'HoldReasonSubCode'):
            with self.subTest(missing=key):
                changed = dict(ad)
                changed.pop(key)
                self.assertIsNone(module.held_detector_exit(task, record, changed,
                                                          self.controller.policy))
        self.events(task, '001 (123.000.000) execution\n...\n'
            '012 (123.000.000) Manual hold\n Code 3 Subcode 0\n...\n')
        self.assertIsNone(module.held_detector_exit(task, record, ad, self.controller.policy))
        self.reader.return_value = [ad]
        self.assertEqual(self.step()['phase'], 'attention_required')
        self.assertEqual(self.controller.ledger[task['task_id']]['failure_scope'], 'global')

    def test_on_exit_hold_requires_consistent_slice_receipts_and_archive(self):
        task, report, status, ad = self.held_detector_failure()
        record = self.controller.ledger[task['task_id']]
        directory = Path(task['working_directory'])
        for change in ({'selected_event_ids_sha256': 'd'*64}, {'error': 'different'},
                {'validated_tier_events': {'GEN': 0}}, {'exit_code': 2},
                {'return_payload_sha256': 'd'*64}, {'complete': True}, {'stage': 'histogram'}):
            with self.subTest(change=change):
                module.save(task['status_path'], status | change)
                self.assertIsNone(module.held_detector_exit(task, record, ad, self.controller.policy))
        module.save(task['status_path'], status)
        for change in ({'current_tier': 'PUBLISHING'}, {'validated_tier_events': {'GEN': 0}},
                {'error': "ValueError('GEN weights changed')"}):
            changed = report | change
            module.save(directory/'result.json', changed)
            changed_status = status | {key: changed[key] for key in ('error', 'validated_tier_events')}
            module.save(task['status_path'], changed_status)
            with self.subTest(report_change=change):
                self.assertIsNone(module.held_detector_exit(task, record, ad, self.controller.policy))
        module.save(directory/'result.json', report)
        module.save(task['status_path'], status)
        (directory/'return_payload.tar.gz').unlink()
        self.assertIsNone(module.held_detector_exit(task, record, ad, self.controller.policy))

    def test_explicit_cold_failure_review_keeps_failed_phase_and_original_evidence(self):
        task, report, status, ad = self.held_detector_failure()
        record = self.controller.ledger[task['task_id']]
        record.update(phase='failed', failure_scope='global', error='Original held failure', queue=ad)
        self.controller.persist()
        ledger = module.read(self.root/'ledger.json')
        tasks = module.load_tasks(self.root, ledger)
        directory = Path(task['working_directory'])
        pins = dict(ledger=module.sha(self.root/'ledger.json'), tasks={task['task_id']: {
            key: module.sha(path) for key, path in dict(
                task_manifest=self.root/'tasks/source0_reco.json', result=directory/'result.json',
                status=directory/'status.json', event_log=directory/'events.log',
                return_payload=directory/'return_payload.tar.gz').items()}})
        updated, receipt = module.review_failed_cohort_reclassification(
            self.root, self.controller.policy, tasks, ledger, [ad], pins, 1000, now=1001)
        self.assertEqual(updated[task['task_id']]['phase'], 'failed')
        self.assertEqual(updated[task['task_id']]['failure_scope'], 'cohort')
        self.assertEqual(updated[task['task_id']]['error'], 'Original held failure')
        self.assertEqual(module.read(self.root/'ledger.json'), ledger)
        self.assertEqual(receipt['scientific_retries'], 0)
        self.assertEqual(receipt['tasks'][task['task_id']]['original_record'], ledger[task['task_id']])
        for ads, changes, observed in (([ad], {}, 900),
                ([ad, ad | {'ShiftStreamController': True}], {}, 1000),
                ([ad, ad | {'JobUniverse': 7}], {}, 1000),
                ([], {}, 1000), ([ad, ad], {}, 1000),
                ([ad], {'ledger': 'd'*64}, 1000)):
            with self.subTest(ads=len(ads), changes=changes, observed=observed):
                with self.assertRaises(ValueError):
                    module.review_failed_cohort_reclassification(self.root, self.controller.policy,
                        tasks, ledger, ads, pins | changes, observed, now=1001)
        (directory/'return_payload.tar.gz').write_bytes(b'changed evidence')
        with self.assertRaisesRegex(ValueError, 'evidence changed'):
            module.review_failed_cohort_reclassification(self.root, self.controller.policy,
                tasks, ledger, [ad], pins, 1000, now=1001)

    def removed_held_failure_review(self):
        task, report, status, held = self.held_detector_failure()
        held.update(QDate=999, NumJobStarts=1)
        record = self.controller.ledger[task['task_id']]
        record.update(phase='failed', failure_scope='global', error='Original held failure', queue=held)
        self.controller.persist()
        event_path = Path(task['event_log'])
        prefix = event_path.read_text()
        reason = 'Job removed by SYSTEM_PERIODIC_REMOVE due to wall time exceeded allowed max.'
        event_path.write_text(prefix+'009 (123.000.000) Job was aborted.\n '+reason+'\n...\n')
        historical = held | dict(JobStatus=3, LastJobStatus=5, RemoveReason=reason)
        snapshot = dict(known=True, checked_at_epoch=1000,
                        command=['/usr/bin/condor_history', '123.0', '-limit', '1', '-json'], rows=[historical])
        history_path = self.root/'removed_history.json'
        module.save(history_path, snapshot)
        history = {task['task_id']: dict(path=str(history_path), sha256=module.sha(history_path))}
        directory = Path(task['working_directory'])
        paths = dict(task_manifest=self.root/'tasks/source0_reco.json', result=directory/'result.json',
                     status=directory/'status.json', event_log=event_path,
                     return_payload=directory/'return_payload.tar.gz')
        pins = dict(ledger=module.sha(self.root/'ledger.json'),
                    tasks={task['task_id']: {key: module.sha(path) for key, path in paths.items()}})
        ledger = module.read(self.root/'ledger.json')
        tasks = module.load_tasks(self.root, ledger)
        return task, held, historical, snapshot, history, paths, pins, ledger, tasks, prefix

    def test_cold_removed_held_review_preserves_failure_and_both_native_records(self):
        task, held, historical, snapshot, history, paths, pins, ledger, tasks, prefix = self.removed_held_failure_review()
        updated, receipt = module.review_failed_cohort_reclassification(
            self.root, self.controller.policy, tasks, ledger, [], pins, 1000, now=1001,
            removed_job_history=history)
        row = updated[task['task_id']]
        self.assertEqual((row['phase'], row['failure_scope']), ('failed', 'cohort'))
        self.assertEqual(row['error'], ledger[task['task_id']]['error'])
        self.assertTrue(row['native_exit']['removed_after_held_exit'])
        self.assertEqual(row['native_exit']['terminal'], 1)
        retained = receipt['tasks'][task['task_id']]
        self.assertEqual(retained['native_ad'], historical)
        self.assertEqual(retained['cached_original_held_ad'], held)
        self.assertEqual(retained['native_history'], history[task['task_id']])
        self.assertEqual(retained['native_exit']['event_log_sha256'], module.sha(paths['event_log']))
        self.assertEqual(module.read(self.root/'ledger.json'), ledger)
        self.assertFalse(receipt['task_phases_changed'])
        self.assertEqual(receipt['scientific_retries'], 0)
        self.assertIsNone(module.held_detector_exit(task, ledger[task['task_id']], held, self.controller.policy))
        with self.assertRaisesRegex(ValueError, 'proven held'):
            module.review_failed_cohort_reclassification(
                self.root, self.controller.policy, tasks, ledger, [], pins, 1000, now=1001)

    def test_removed_held_review_requires_fresh_exact_pinned_history_provenance(self):
        task, held, historical, snapshot, history, paths, pins, ledger, tasks, prefix = self.removed_held_failure_review()
        history_path = Path(history[task['task_id']]['path'])
        for change in ({'known': False}, {'checked_at_epoch': 900}, {'checked_at_epoch': 1002},
                {'command': ['/usr/bin/condor_history', '456.0', '-limit', '1', '-json']},
                {'command': ['/usr/bin/condor_history', '123.0', '-limit', '2', '-json']},
                {'command': ['/usr/bin/condor_history', '-constraint', 'true', '123.0', '-limit', '1', '-json']},
                {'rows': []}, {'rows': [historical, historical]}):
            module.save(history_path, snapshot | change)
            descriptor = {task['task_id']: dict(path=str(history_path), sha256=module.sha(history_path))}
            with self.subTest(change=change), self.assertRaises(ValueError):
                module.review_failed_cohort_reclassification(
                    self.root, self.controller.policy, tasks, ledger, [], pins, 1000, now=1001,
                    removed_job_history=descriptor)
        module.save(history_path, snapshot)
        with self.assertRaisesRegex(ValueError, 'pinned'):
            module.review_failed_cohort_reclassification(
                self.root, self.controller.policy, tasks, ledger, [], pins, 1000, now=1001,
                removed_job_history={task['task_id']: dict(path=str(history_path), sha256='d'*64)})

    def test_removed_held_review_rejects_changed_identity_exit_or_site_removal(self):
        task, held, historical, snapshot, history, paths, pins, ledger, tasks, prefix = self.removed_held_failure_review()
        history_path = Path(history[task['task_id']]['path'])
        for change in ({'GlobalJobId': 'other#123.0#999'}, {'ShiftStreamNonce': 'different'},
                {'NumJobStarts': 2}, {'NumJobStarts': True}, {'Owner': 'other'}, {'Iwd': '/different'},
                {'JobStatus': 4}, {'LastJobStatus': 2}, {'ExitCode': 0}, {'ExitBySignal': True},
                {'RemoveReason': 'Job removed by user'}, {'QDate': 998}, {'Args': 'different'}):
            module.save(history_path, snapshot | {'rows': [historical | change]})
            descriptor = {task['task_id']: dict(path=str(history_path), sha256=module.sha(history_path))}
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, 'original unsuccessful'):
                module.review_failed_cohort_reclassification(
                    self.root, self.controller.policy, tasks, ledger, [], pins, 1000, now=1001,
                    removed_job_history=descriptor)

    def test_removed_held_review_rejects_live_original_or_same_task_replacement(self):
        task, held, historical, snapshot, history, paths, pins, ledger, tasks, prefix = self.removed_held_failure_review()
        for ad in (held, held | {'ClusterId': 456, 'JobStatus': 2}):
            with self.subTest(ad=ad), self.assertRaisesRegex(ValueError, 'original job or a replacement'):
                module.review_failed_cohort_reclassification(
                    self.root, self.controller.policy, tasks, ledger, [ad], pins, 1000, now=1001,
                    removed_job_history=history)

    def test_removed_held_review_rejects_release_reexecution_or_ambiguous_events(self):
        task, held, historical, snapshot, history, paths, pins, ledger, tasks, prefix = self.removed_held_failure_review()
        removal = '009 (123.000.000) Job was aborted.\n '+historical['RemoveReason']+'\n...\n'
        for ending in ('013 (123.000.000) Job released.\n...\n'+removal,
                '001 (123.000.000) execution\n...\n'+removal,
                '005 (123.000.000) termination\n...\n'+removal,
                '012 (123.000.000) OnExitHold Code 3 Subcode 0\n...\n'+removal,
                removal.replace(historical['RemoveReason'], 'Job removed by user'),
                removal+'001 (123.000.000) execution\n...\n'):
            paths['event_log'].write_text(prefix+ending)
            changed = copy.deepcopy(pins)
            changed['tasks'][task['task_id']]['event_log'] = module.sha(paths['event_log'])
            with self.subTest(ending=ending), self.assertRaisesRegex(ValueError, 'held-then-site-removal'):
                module.review_failed_cohort_reclassification(
                    self.root, self.controller.policy, tasks, ledger, [], changed, 1000, now=1001,
                    removed_job_history=history)

    def test_stage_storage_reserves_actual_histogram_mix_and_candidate_once(self):
        policy = self.policy | {'stage_publication_bounds': {'histogram': 40, 'merge': 40}}
        tasks = {name: {'stage': stage} for name, stage in (
            ('old_hist', 'histogram'), ('live_merge', 'merge'), ('pending_reco', 'reco'), ('new_hist', 'histogram'))}
        reservations = {'old_hist': {'at_epoch': 999}, 'pending_reco': {'at_epoch': 999}}
        ads = [dict(ClusterId=1, ProcId=0, JobUniverse=5, JobStatus=2,
                    ShiftStreamCampaign='stream', ShiftStreamTask='live_merge')]
        # Four baseline slots=40, three actual histogram extras=90, floor=50.
        observation = self.observation | {'free_bytes': 180}
        adjusted = module.reserve_stage_publications(policy, self.admission, observation,
            tasks, ads, reservations, 'new_hist')
        self.assertEqual(adjusted['extra_stage_reserved_bytes'], 90)
        self.assertEqual(adjusted['free_bytes'], 180)
        self.assertEqual(observation['free_bytes'], 180)
        decision = module.decision(self.admission, adjusted, module.mapped_ads(ads, 'stream'),
                                   reservations, 'new_hist', 1000, 10000)
        self.assertTrue(decision['admitted'])
        adjusted['free_bytes'] -= 1
        self.assertFalse(module.decision(self.admission, adjusted, module.mapped_ads(ads, 'stream'),
                                        reservations, 'new_hist', 1000, 10000)['admitted'])
        # A candidate already queued/claimed does not reserve its extra twice.
        same = module.reserve_stage_publications(policy, self.admission, observation,
            tasks, ads, reservations | {'live_merge': {'at_epoch': 999}}, 'live_merge')
        self.assertEqual(same['extra_stage_reserved_bytes'], 60)

    def test_stage_storage_keeps_completed_and_held_claims_and_rejects_unknown_tasks(self):
        policy = self.policy | {'stage_publication_bounds': {'histogram': 40, 'merge': 40}}
        tasks = {'finished': {'stage': 'histogram'}, 'held': {'stage': 'merge'}, 'next': {'stage': 'reco'}}
        reservations = {'finished': {'at_epoch': 900, 'seen_queued': True, 'missing_since_epoch': 998}}
        ads = [dict(JobUniverse=5, JobStatus=5, ShiftStreamCampaign='stream', ShiftStreamTask='held')]
        adjusted = module.reserve_stage_publications(policy, self.admission, self.observation,
            tasks, ads, reservations, 'next')
        self.assertEqual(adjusted['extra_stage_reserved_bytes'], 60)
        # Only the reviewed completion/fresh-quota release removes this claim.
        adjusted = module.reserve_stage_publications(policy, self.admission, self.observation,
            tasks, ads, {}, 'next')
        self.assertEqual(adjusted['extra_stage_reserved_bytes'], 30)
        for changes in ({'unknown': {'at_epoch': 999}}, {'finished': {'at_epoch': 999}}):
            known = tasks if 'unknown' in changes else tasks | {'finished': {'stage': 'unknown'}}
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, 'unknown streaming task'):
                module.reserve_stage_publications(policy, self.admission, self.observation,
                    known, ads, changes, 'next')

    def test_constrained_peer_storage_charges_full_stage_at_exact_byte_boundary(self):
        authority = [{'schema': 'synthetic-peer-authority'}]
        admission = self.admission | dict(external_storage_reservations=authority,
            maximum_outstanding_publication_bytes=60)
        observation = self.observation | dict(free_bytes=280, stream_peer_writers_verified=True,
            external_storage_commitments=dict(complete=True, checked_at_epoch=998,
                authority_contracts_sha256=hashlib.sha256(json.dumps(authority, sort_keys=True).encode()).hexdigest(),
                required_free_bytes=300, required_free_files=30,
                stream_capacity=dict(constrained_required_free_bytes=240, protected_stream_bytes=60)))
        policy = self.policy | dict(stage_publication_bounds={'histogram':40})
        task = {'candidate': {'stage': 'histogram'}}
        observed = module.reserve_stage_publications(policy, admission, observation, task, [], {}, 'candidate')
        fit = module.decision(admission, observed, [], {}, 'candidate', 1000, 10000)
        self.assertTrue(fit['admitted'])
        self.assertEqual(fit['outstanding_publication_bytes'], 40)
        self.assertEqual(fit['storage_mode'], 'constrained_within_peer_QCD_allowance')
        shortage = observed | {'free_bytes': 279}
        self.assertFalse(module.decision(admission, shortage, [], {}, 'candidate', 1000, 10000)['admitted'])
        # Full mode needs the same complete publication bound above its full floor.
        self.assertEqual(module.decision(admission, observed | {'free_bytes':340}, [], {},
            'candidate', 1000, 10000)['storage_mode'], 'full')
        active_supplement = observed | {'stream_requires_full_peer_mode': True}
        self.assertFalse(module.decision(admission, active_supplement, [], {}, 'candidate', 1000, 10000)['admitted'])

    def test_stage_storage_rejects_bounds_larger_than_frozen_stage_or_reviewed_limit(self):
        for bounds in ({'histogram': True}, {'histogram': 9}, {'histogram': 513*1024**2}, {'reco': 40}):
            with self.subTest(bounds=bounds), self.assertRaises(ValueError):
                module.stage_publication_bounds(self.policy | {'stage_publication_bounds': bounds}, self.admission)
        policy = self.policy | {'stage_publication_bounds': {'histogram': 40}}
        with self.assertRaisesRegex(ValueError, 'exceeds its reserved stage'):
            module.reserve_stage_publications(policy, self.admission, self.observation,
                {'next': {'stage': 'histogram', 'publication_reservation_bytes': 41}}, [], {}, 'next')

    def test_shared_peer_writer_check_rejects_unknown_bsm_and_any_legacy_sm_writer(self):
        quota = self.observation | {'remote_account_peer_writer_rows': [],
            'external_storage_commitments': {'stream_capacity': {
                'allowed_bsm_directories': ['/sealed/main', '/sealed/meson'],
                'required_absent_stream_campaigns': ['old_preview', 'closed_canary']}}}
        own = dict(JobUniverse=5, JobStatus=2, ShiftStreamCampaign='stream', Iwd='/our/task')
        known = dict(JobUniverse=5, JobStatus=2, ShiftBSMPoint='m10', Iwd='/sealed/main/waves/w001/production')
        self.assertTrue(module.verify_stream_peer_writers(self.policy, quota, [own, known])['stream_peer_writers_verified'])
        for bad in (known | {'Iwd': '/unreviewed/new_BSM'},
                    own | {'ShiftStreamCampaign': 'old_preview', 'JobStatus': 5},
                    own | {'ShiftStreamCampaign': 'new_unreviewed_SM'},
                    own | {'ShiftMuEnrichedQCD': True}):
            self.assertFalse(module.verify_stream_peer_writers(self.policy, quota, [bad])['stream_peer_writers_verified'])
        remote = quota | {'remote_account_peer_writer_rows': [known | {'Iwd': '/unknown', 'JobStatus': 5}]}
        self.assertFalse(module.verify_stream_peer_writers(self.policy, remote, [])['stream_peer_writers_verified'])
        remote_own = quota | {'remote_account_peer_writer_rows': [own]}
        self.assertFalse(module.verify_stream_peer_writers(self.policy, remote_own, [])['stream_peer_writers_verified'])

    def test_shared_peer_native_controller_must_use_exact_pinned_guard_launch(self):
        expected = dict(Iwd='/sealed/main/supervisor_v5', Cmd='/usr/bin/python3',
            Args='/sealed/main/supervisor_v5/run.py --config /sealed/main/supervisor_v5/config.json',
            ShiftDPStatisticsTask='/sealed/main/supervisor_v5')
        quota = self.observation | {'remote_account_peer_writer_rows': [],
            'external_storage_commitments': {'stream_capacity': {
                'allowed_bsm_directories': ['/sealed/main'],
                'required_absent_stream_campaigns': ['old_preview'],
                'controller_bindings': [dict(name='main', expected_native=expected)]}}}
        manager = expected | dict(JobUniverse=5, JobStatus=2, JobBatchName='SHIFT_DP_statistics_supervisor')
        self.assertTrue(module.verify_stream_peer_writers(self.policy, quota, [manager])['stream_peer_writers_verified'])
        for change in (dict(Iwd='/sealed/main/supervisor_v6'), dict(Args=expected['Args']+' --different-guard'),
                       dict(Cmd='/changed/launcher'), dict(ShiftDPStatisticsTask='/sealed/main/new')):
            with self.subTest(change=change):
                result = module.verify_stream_peer_writers(self.policy, quota, [manager | change])
                self.assertFalse(result['stream_peer_writers_verified'])
                self.assertIn('controller', result['stream_peer_writer_error'])
        # A replacement on a remote scheduler is held to the same launch binding.
        remote = quota | {'remote_account_peer_writer_rows': [manager | dict(JobStatus=5, Iwd='/sealed/main/supervisor_v6')]}
        self.assertFalse(module.verify_stream_peer_writers(self.policy, remote, [])['stream_peer_writers_verified'])

    def nonpublishing_peer(self):
        directory = self.root.parent / 'bsm_watchdog'
        directory.mkdir(exist_ok=True)
        program, config = directory / 'watch.py', directory / 'config.json'
        program.write_text('# reviewed read-only watchdog\n')
        module.save(config, {'action': 'observe_only'})
        expected = dict(ClusterId=456, ProcId=0, Owner='jniedzie', JobUniverse=5,
            Iwd=str(directory), Cmd='/usr/bin/python3',
            Args=str(program) + ' --config ' + str(config), JobBatchName='BSM_read_only_watchdog')
        binding = dict(name='bsm_watchdog', audited_purpose='read_only_watchdog',
            expected_native=expected,
            frozen_input_sha256={str(program): module.sha(program), str(config): module.sha(config)})
        quota = self.observation | {'remote_account_peer_writer_rows': [],
            'external_storage_commitments': {'stream_capacity': {
                'allowed_bsm_directories': ['/sealed/main'],
                'required_absent_stream_campaigns': ['old_preview']}}}
        return binding, expected | {'JobStatus': 2}, quota

    def test_reviewed_nonpublishing_peer_needs_exact_launch_pins_and_audited_purpose(self):
        binding, ad, quota = self.nonpublishing_peer()
        policy = self.policy | {'nonpublishing_peer_controllers': [binding]}
        result = module.verify_stream_peer_writers(policy, quota, [ad])
        self.assertTrue(result['stream_peer_writers_verified'])
        self.assertEqual(result['verified_nonpublishing_peer_controllers'], ['bsm_watchdog'])
        self.assertEqual(result['verified_BSM_writers'], 0)
        # Exact matching remote operations are equally accountable.
        remote = quota | {'remote_account_peer_writer_rows': [ad]}
        self.assertTrue(module.verify_stream_peer_writers(policy, remote, [])['stream_peer_writers_verified'])
        self.assertFalse(module.verify_stream_peer_writers(self.policy, quota, [ad])['stream_peer_writers_verified'])

    def test_nonpublishing_peer_identity_and_argument_drift_never_inherit_the_exception(self):
        binding, ad, quota = self.nonpublishing_peer()
        policy = self.policy | {'nonpublishing_peer_controllers': [binding]}
        for change in (dict(ClusterId=457), dict(ProcId=1), dict(Owner='another_user'),
                       dict(Args=ad['Args'] + ' --submit'), dict(Cmd='/other/python3'),
                       dict(Iwd='/unreviewed/ops'), dict(JobBatchName='changed')):
            with self.subTest(change=change):
                self.assertFalse(module.verify_stream_peer_writers(policy, quota, [ad | change])['stream_peer_writers_verified'])

    def test_nonpublishing_peer_code_config_and_science_role_cannot_change(self):
        binding, ad, quota = self.nonpublishing_peer()
        policy = self.policy | {'nonpublishing_peer_controllers': [binding]}
        for path in binding['frozen_input_sha256']:
            target = Path(path)
            original = target.read_bytes()
            target.write_bytes(original + b'changed')
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, 'frozen input changed'):
                module.verify_stream_peer_writers(policy, quota, [ad])
            target.write_bytes(original)
        for change in (dict(ShiftBSMPoint='m10'), dict(ShiftSignalValidationOnly=True),
                       dict(ShiftStreamTask='reco'), dict(DAGManJobId=999),
                       dict(ShiftMuEnrichedQCD=True), dict(ShiftStreamCampaign='old_preview')):
            with self.subTest(change=change):
                self.assertFalse(module.verify_stream_peer_writers(policy, quota, [ad | change])['stream_peer_writers_verified'])
        for change in (dict(audited_purpose='science_controller'), dict(frozen_input_sha256={})):
            with self.subTest(change=change), self.assertRaises(ValueError):
                module.nonpublishing_peer_bindings(self.policy | {'nonpublishing_peer_controllers': [binding | change]})

    def test_nonpublishing_peer_cannot_use_a_directory_only_or_unpinned_launcher_binding(self):
        binding, ad, quota = self.nonpublishing_peer()
        for key in ('ClusterId', 'ProcId', 'Owner', 'JobUniverse', 'Iwd', 'Cmd', 'Args'):
            expected = {name: value for name, value in binding['expected_native'].items() if name != key}
            with self.subTest(missing=key), self.assertRaises(ValueError):
                module.nonpublishing_peer_bindings(self.policy | {'nonpublishing_peer_controllers':
                    [binding | {'expected_native': expected}]})
        program = next(iter(binding['frozen_input_sha256']))
        pins = {name: value for name, value in binding['frozen_input_sha256'].items() if name != program}
        with self.assertRaisesRegex(ValueError, 'launched program is not pinned'):
            module.nonpublishing_peer_bindings(self.policy | {'nonpublishing_peer_controllers':
                [binding | {'frozen_input_sha256': pins}]})

    def test_supervisor_rollover_can_bind_nonpublishing_operations_without_changing_science(self):
        binding, ad, quota = self.nonpublishing_peer()
        module.save(self.root / 'ledger.json', {})
        pins = {path: module.sha(path) for path in (self.root / 'policy.json', self.root / 'admission/policy.json')}
        replacement = self.root.parent / 'ops_binding_supervision'
        preparation.adopt(replacement, self.root, {'nonpublishing_peer_controllers': [binding]})
        adopted = module.StreamController(replacement, self.reader, self.commander)
        self.assertEqual(adopted.policy['nonpublishing_peer_controllers'], [binding])
        self.assertEqual(adopted.admission_policy, self.controller.admission_policy)
        self.assertEqual({path: module.sha(path) for path in pins}, pins)
        self.assertTrue(module.verify_stream_peer_writers(adopted.policy, quota, [ad])['stream_peer_writers_verified'])

    def meson_companion(self, watchdog=False):
        directory = self.root.parent / 'bsm_new_meson'
        (directory/'tools').mkdir(parents=True)
        program = directory/'tools/run_dark_photon_meson_production.py'
        program.write_text('# Synthetic pinned coverage entry point.\n')
        model, particle = directory/'model.json', directory/'ParticleData.xml'
        module.save(model, {'channels':['eta->gamma_Aprime']})
        particle.write_text('<particle id="221" />\n')
        config = directory/'config.json'
        module.save(config, dict(schema='shift-dp-meson-production-v1', production_mechanism='meson-radiative',
            directory=str(directory), tools=str(directory/'tools'), model_card=str(model),
            coverage_audit=dict(particle_data=dict(path=str(particle),sha256=module.sha(particle)))))
        seal = directory/'seal.json'
        pins = {str(path):module.sha(path) for path in (program,model,config,particle)}
        module.save(seal, dict(files=dict(pins)))
        pins[str(seal)] = module.sha(seal)
        cwd = directory/'coverage'
        cwd.mkdir()
        purpose = 'read_only_meson_coverage'
        arguments = str(program)+' --directory '+str(directory)+' --seal '+module.sha(seal)+' --action coverage'
        if watchdog:
            cwd = directory/'monitor'
            cwd.mkdir()
            program = cwd/'watch_dark_photon_statistics.py'
            program.write_text('# Synthetic read-only watcher.\n')
            monitor = cwd/'config.json'
            dependencies = {str(path):module.sha(path) for path in (program,config,seal)}
            module.save(monitor, dict(schema='shift-dp-statistics-watchdog-v1', no_job_mutations=True,
                production_directory=str(directory), worklist=str(config), dependencies=dependencies))
            pins.update(dependencies)
            pins[str(monitor)] = module.sha(monitor)
            purpose = 'read_only_meson_watchdog'
            arguments = str(program)+' --config '+str(monitor)
        expected = dict(Owner='jniedzie',JobUniverse=5,Iwd=str(cwd),Cmd='/usr/bin/python3',Args=arguments,
            ShiftDPStatisticsTask=str(cwd),ShiftDPStatisticsReadOnly=True)
        if watchdog:
            expected['ShiftDPStatisticsWatchdog'] = True
        descriptor = cwd/'worker.sub'
        descriptor.write_text('initialdir = '+str(cwd)+'\nexecutable = /usr/bin/python3\n'
            'arguments = '+arguments+'\n+ShiftDPStatisticsReadOnly = True\n'
            + ('+ShiftDPStatisticsWatchdog = True\n' if watchdog else ''))
        pins[str(descriptor)] = module.sha(descriptor)
        binding = dict(name='new_meson_companion',audited_purpose=purpose,expected_native=expected,
            meson_config=dict(path=str(config),sha256=module.sha(config)),
            meson_seal=dict(path=str(seal),sha256=module.sha(seal)),
            submit=dict(path=str(descriptor),sha256=module.sha(descriptor)),frozen_input_sha256=pins)
        ad = dict(expected,ClusterId=999,ProcId=0,JobStatus=2)
        quota = self.observation | {'remote_account_peer_writer_rows': [],
            'external_storage_commitments': {'stream_capacity': {
                'allowed_bsm_directories': [str(directory)], 'deferred_bsm_directories':[str(directory)],
                'required_absent_stream_campaigns': ['old_preview']}}}
        return binding,ad,quota

    def test_future_sealed_coverage_and_watchdog_are_exact_readonly_companions(self):
        for watchdog in (False, True):
            with self.subTest(watchdog=watchdog):
                binding,ad,quota = self.meson_companion(watchdog)
                policy = self.policy | {'nonpublishing_peer_controllers':[binding]}
                result = module.verify_stream_peer_writers(policy,quota,[ad])
                self.assertTrue(result['stream_peer_writers_verified'])
                self.assertFalse(result['stream_requires_full_peer_mode'])
                self.assertEqual(result['verified_BSM_writers'],0)
                # Unknown scientific payloads still force the full future reserve.
                science = dict(JobUniverse=5,JobStatus=2,Iwd=str(Path(ad['Iwd']).parent/'batches/first'),
                    ShiftBSMPoint='m0p3_prompt')
                result = module.verify_stream_peer_writers(policy,quota,[ad,science])
                self.assertTrue(result['stream_peer_writers_verified'])
                self.assertTrue(result['stream_requires_full_peer_mode'])
                # Fresh folder for the second case, without retaining probes.
                import shutil
                shutil.rmtree(Path(binding['meson_config']['path']).parent)

    def test_readonly_meson_companion_cannot_change_role_launch_or_duplicate_execution(self):
        binding,ad,quota = self.meson_companion()
        policy = self.policy | {'nonpublishing_peer_controllers':[binding]}
        for change in (dict(Args=ad['Args'].replace('--action coverage','--action source')),
                       dict(ShiftDPStatisticsReadOnly=False),dict(ShiftBSMPoint='signal'),
                       dict(DAGManJobId=123),dict(Owner='another_user')):
            with self.subTest(change=change):
                self.assertFalse(module.verify_stream_peer_writers(policy,quota,[ad|change])['stream_peer_writers_verified'])
        self.assertFalse(module.verify_stream_peer_writers(policy,quota,[ad,ad|dict(ClusterId=1000)])['stream_peer_writers_verified'])
        model = Path(binding['meson_config']['path']).parent/'model.json'
        model.write_text('{}')
        with self.assertRaisesRegex(ValueError,'frozen input changed'):
            module.verify_stream_peer_writers(policy,quota,[ad])

    def test_additive_peer_rollover_preserves_original_policy_and_uses_new_real_audit_root(self):
        task = self.task()
        self.step()
        original_job = dict(self.controller.ledger[task['task_id']])
        self.reader.return_value = [dict(ClusterId=123, ProcId=0, JobStatus=2,
            JobCurrentStartDate=999, Iwd=task['working_directory'], ShiftStreamCampaign='stream',
            ShiftStreamTask=task['task_id'], ShiftStreamNonce=original_job['nonce'])]
        authority = dict(schema='shift-statistics-peer-storage-authority-v1',
                         constrained_stream=dict(active_campaign_names=['main']))
        self.policy['admission']['external_storage_reservations'] = [authority]
        module.save(self.root/'policy.json',self.policy)
        deployment = module.read(self.root/'deployment.json')
        deployment['files']['policy.json'] = module.sha(self.root/'policy.json')
        module.save(self.root/'deployment.json',deployment)
        before = {str(path):module.sha(path) for path in (self.root/'policy.json',
            self.root/'admission/policy.json',self.root/'tasks'/('source0_reco.json'),Path(task['submit_file']))}
        additions = [dict(campaign={'name':'new_meson'})]
        replacement = self.root.parent/'extra_peer_supervision'
        preparation.adopt(replacement,self.root,{'additional_peer_meson_campaigns':additions})
        adopted = module.StreamController(replacement,self.reader,self.commander)
        self.assertEqual(adopted.root,self.root)
        self.assertEqual(adopted.ledger[task['task_id']]['nonce'], original_job['nonce'])
        self.assertEqual(adopted.ledger[task['task_id']]['job'], [123,0])
        self.assertEqual(adopted.policy['admission'],self.policy['admission'])
        self.assertNotIn('additional_meson_campaigns',authority['constrained_stream'])
        self.assertEqual(adopted.admission_policy['external_storage_reservations'][0][
            'constrained_stream']['additional_meson_campaigns'],additions)
        with mock.patch.object(module,'refresh_quota',return_value=self.observation) as audit, \
             mock.patch.object(module,'afs_headroom',return_value=10000):
            adopted.step(now=1000)
        audit.assert_called_once_with(replacement/'admission')
        self.assertEqual(self.commander.call_count, 1)
        reservations = module.read(self.root/'admission/pre_reservations.json')
        self.assertEqual(reservations[task['task_id']]['at_epoch'], 1000)
        self.assertFalse((replacement/'admission/pre_reservations.json').exists())
        self.assertEqual({str(Path(path)):module.sha(Path(path)) for path in before},before)

    def accounted_publication_peer(self):
        directory = self.root.parent / 'bsm_publication_recovery'
        directory.mkdir(exist_ok=True)
        program, proof = directory / 'finalizer.sh', directory / 'recovery_proof.json'
        program.write_text('# reviewed finalizer of already generated sources\n')
        module.save(proof, {'accounted_campaign': 'meson', 'source_seal': 'a' * 64})
        expected = dict(Owner='jniedzie', JobUniverse=5, Iwd=str(directory),
            Cmd=str(program), Args='m0p25_lab_5m ' + 'a' * 64,
            DAGManJobId=999, ShiftBSMPoint='m0p25_lab_5m')
        binding = dict(name='meson_finalizer', accounted_campaign='meson', expected_native=expected,
            frozen_input_sha256={str(program): module.sha(program), str(proof): module.sha(proof)})
        quota = self.observation | {'remote_account_peer_writer_rows': [],
            'external_storage_commitments': {'stream_capacity': {
                'allowed_bsm_directories': ['/sealed/meson'], 'active_campaign_names': ['meson'],
                'required_absent_stream_campaigns': ['old_preview']}}}
        return binding, expected | dict(ClusterId=777, ProcId=0, JobStatus=2), quota

    def test_already_accounted_publication_extension_remains_a_scientific_writer(self):
        binding, ad, quota = self.accounted_publication_peer()
        policy = self.policy | {'accounted_peer_publication_workers': [binding]}
        result = module.verify_stream_peer_writers(policy, quota, [ad])
        self.assertTrue(result['stream_peer_writers_verified'])
        self.assertEqual(result['verified_BSM_writers'], 1)
        self.assertEqual(result['verified_accounted_peer_publication_workers'], ['meson_finalizer'])
        self.assertEqual(result['verified_nonpublishing_peer_controllers'], [])
        self.assertFalse(module.verify_stream_peer_writers(self.policy, quota, [ad])['stream_peer_writers_verified'])
        # A future exact COLLECT node is independently pinned to the same DAG;
        # it uses the real 'all' marker rather than silently changing its point.
        collect = binding | dict(name='meson_collect', expected_native=binding['expected_native'] |
            dict(Args='a' * 64, ShiftBSMPoint='all'))
        collector = ad | dict(Args='a' * 64, ShiftBSMPoint='all', ClusterId=778)
        both = policy | {'accounted_peer_publication_workers': [binding, collect]}
        remote = quota | {'remote_account_peer_writer_rows': [collector]}
        result = module.verify_stream_peer_writers(both, remote, [ad])
        self.assertTrue(result['stream_peer_writers_verified'])
        self.assertEqual(result['verified_BSM_writers'], 2)
        self.assertEqual(result['verified_accounted_peer_publication_workers'], ['meson_finalizer', 'meson_collect'])

    def test_accounted_publication_extension_requires_exact_dag_point_command_and_active_claim(self):
        binding, ad, quota = self.accounted_publication_peer()
        policy = self.policy | {'accounted_peer_publication_workers': [binding]}
        for change in (dict(Cmd='/other/finalizer.sh'), dict(Args=ad['Args'] + ' changed'),
                       dict(DAGManJobId=1000), dict(ShiftBSMPoint='another_point'),
                       dict(Owner='another_user'), dict(Iwd='/sealed/meson/unreviewed'),
                       dict(Args='--action controller')):
            with self.subTest(change=change):
                self.assertFalse(module.verify_stream_peer_writers(policy, quota, [ad | change])['stream_peer_writers_verified'])
        inactive = quota | {'external_storage_commitments': {'stream_capacity':
            quota['external_storage_commitments']['stream_capacity'] | {'active_campaign_names': ['main']}}}
        with self.assertRaisesRegex(ValueError, 'outside the active storage campaigns'):
            module.verify_stream_peer_writers(policy, inactive, [ad])
        for path in binding['frozen_input_sha256']:
            target, data = Path(path), Path(path).read_bytes()
            target.write_bytes(data + b'changed')
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, 'frozen input changed'):
                module.verify_stream_peer_writers(policy, quota, [ad])
            target.write_bytes(data)

    def test_accounted_publication_does_not_skip_deferred_full_storage_or_controller_checks(self):
        binding, ad, quota = self.accounted_publication_peer()
        policy = self.policy | {'accounted_peer_publication_workers': [binding]}
        capacity = quota['external_storage_commitments']['stream_capacity'] | {
            'deferred_bsm_directories': [ad['Iwd']]}
        result = module.verify_stream_peer_writers(policy, quota | {
            'external_storage_commitments': {'stream_capacity': capacity}}, [ad])
        self.assertTrue(result['stream_peer_writers_verified'])
        self.assertTrue(result['stream_requires_full_peer_mode'])
        self.assertEqual(result['verified_deferred_science_writers'], 1)
        # Publication bindings cannot authorize a replacement science manager.
        controller = ad | {'Args': '--config /another/controller.json'}
        misleading = binding | {'expected_native': binding['expected_native'] | {'Args': controller['Args']}}
        result = module.verify_stream_peer_writers(policy | {
            'accounted_peer_publication_workers': [misleading]}, quota, [controller])
        self.assertFalse(result['stream_peer_writers_verified'])
        self.assertIn('BSM controller', result['stream_peer_writer_error'])

    def test_supervisor_can_add_accounted_publication_binding_without_changing_the_storage_claim(self):
        binding, ad, quota = self.accounted_publication_peer()
        module.save(self.root / 'ledger.json', {})
        before = module.sha(self.root / 'admission/policy.json')
        replacement = self.root.parent / 'publication_binding_supervision'
        preparation.adopt(replacement, self.root, {'accounted_peer_publication_workers': [binding]})
        adopted = module.StreamController(replacement, self.reader, self.commander)
        self.assertEqual(module.sha(self.root / 'admission/policy.json'), before)
        self.assertEqual(adopted.admission_policy, self.controller.admission_policy)
        self.assertTrue(module.verify_stream_peer_writers(adopted.policy, quota, [ad])['stream_peer_writers_verified'])

    def test_deferred_science_writer_requires_full_future_mode_but_waiting_manager_does_not(self):
        expected = dict(Iwd='/sealed/supplement', Cmd='/usr/bin/python3',
            Args='/sealed/supplement/run.py --config /sealed/supplement/config.json',
            ShiftDPStatisticsTask='/sealed/supplement')
        quota = self.observation | {'remote_account_peer_writer_rows': [],
            'external_storage_commitments': {'stream_capacity': {
                'allowed_bsm_directories': ['/sealed/supplement'],
                'deferred_bsm_directories': ['/sealed/supplement'],
                'required_absent_stream_campaigns': ['old_preview'],
                'controller_bindings': [dict(name='supplement', expected_native=expected)]}}}
        manager = expected | dict(JobUniverse=5, JobStatus=2, JobBatchName='SHIFT_DP_statistics_supervisor')
        self.assertFalse(module.verify_stream_peer_writers(self.policy, quota, [manager])['stream_requires_full_peer_mode'])
        child = dict(JobUniverse=5, JobStatus=5, ShiftBSMPoint='m10', Iwd='/sealed/supplement/waves/w000/production')
        result = module.verify_stream_peer_writers(self.policy, quota, [manager, child])
        self.assertTrue(result['stream_peer_writers_verified'])
        self.assertTrue(result['stream_requires_full_peer_mode'])
        self.assertEqual(result['verified_deferred_science_writers'], 1)

    def test_task_or_frozen_input_cannot_change_after_observation(self):
        task = self.task()
        self.step()
        task['completion_checks']['events'] = 9
        module.save(self.root/'tasks/source0_reco.json', task)
        with self.assertRaisesRegex(ValueError, 'Previously seen streaming task changed'):
            self.step()

    def test_shared_inputs_are_hashed_once_per_task_load_and_again_next_poll(self):
        shared = self.root / 'runtime.tar.gz'
        shared.write_bytes(b'one shared immutable runtime')
        for identity in ('source0_reco', 'source1_reco'):
            task = self.task(identity=identity)
            task['input_sha256'][str(shared)] = module.sha(shared)
            module.save(self.root / 'tasks' / (identity + '.json'), task)
        with mock.patch.object(module, 'sha', wraps=module.sha) as digest:
            module.load_tasks(self.root, {})
            self.assertEqual(sum(call.args == (str(shared),) for call in digest.call_args_list), 1)
            module.load_tasks(self.root, {})
            self.assertEqual(sum(call.args == (str(shared),) for call in digest.call_args_list), 2)
        shared.write_bytes(b'changed after the first poll')
        with self.assertRaisesRegex(ValueError, 'Frozen task input changed'):
            module.load_tasks(self.root, {})

    def test_shared_input_cache_still_checks_each_tasks_expected_digest(self):
        shared = self.root / 'runtime.tar.gz'
        shared.write_bytes(b'one shared immutable runtime')
        for index in range(2):
            task = self.task(identity='source' + str(index) + '_reco')
            task['input_sha256'][str(shared)] = module.sha(shared) if index == 0 else 'f'*64
            module.save(self.root / 'tasks' / (task['task_id'] + '.json'), task)
        with self.assertRaisesRegex(ValueError, 'Frozen task input changed'):
            module.load_tasks(self.root, {})

    def test_cycle_is_rejected(self):
        self.task(dependencies=['other'])
        self.task(identity='other', dependencies=['source0_reco'])
        with self.assertRaisesRegex(ValueError, 'cycle'):
            module.load_tasks(self.root, {})

    def test_shared_gen_protection_uses_future_wave_not_current_jobs(self):
        observation = dict(free_bytes=153, free_files=1000,
                           external_storage_commitments={'required_free_bytes': 60})
        protected = module.reserve_other_gen_slots(self.admission, observation,
            dict(reserved_bytes_per_worker=20, reserved_files_per_worker=3, worker_ceiling=200))
        self.assertEqual(protected['protected_gen_worker_slots'], 4)
        self.assertEqual(protected['free_bytes'], 73)
        self.assertEqual(protected['free_files'], 988)

    def closed_gen(self):
        """The exact fields emitted by the two maintained final accounting tools."""
        base = self.root / 'gen'
        profile, tag = 'shift-qcd-mu-enriched-cylinder-v1', 'gen'
        jobs = [dict(id='production_' + str(index), stage='production',
                     stratum='bin' + str(index), trials=10) for index in range(5)]
        low_manifest = dict(tag=tag, generator_profile=profile,
                            jobs=jobs + [dict(id='pilot', stage='canary')])
        high_manifest = dict(tag=tag, generator_profile=profile)
        module.save(base / 'ordinary/manifest.json', low_manifest)
        module.save(base / 'weighted/manifest.json', high_manifest)
        low_sha = module.sha(base / 'ordinary/manifest.json')
        high_sha = module.sha(base / 'weighted/manifest.json')
        module.save(base / 'weighted/scientific_gate.json', {'pass': True, 'failures': [],
            'manifest_sha256': high_sha, 'generator_profile': profile,
            'production_plan': {'high': {'chunks': 1, 'trials': 20}}})
        module.save(base / 'ordinary/production_complete.json', dict(
            schema='shift-mu-enriched-ordinary-accounting-v1', complete=True, failures=[],
            stage='production', manifest_sha256=low_sha, generator_profile=profile,
            pilots_excluded_from_production=True, physics_valid=False, normalization_ready=False,
            strata={row['stratum']: dict(chunks=1, framework_source_events=10,
                event_weight_pb_by_chunk={row['id']: 1.0}, no_second_filter_efficiency=True)
                for row in jobs}))
        module.save(base / 'weighted/production_complete.json', dict(
            schema='shift-weighted-high-bin-final-accounting-v1', complete=True, failures=[],
            manifest_sha256=high_sha, physics_valid=False, normalization_ready=False,
            strata={'high': dict(chunks=1, trials=20, generator_profile=profile,
                                 effective_target_met=True, pilots_excluded=True)}))
        (base / 'production.dag').write_text(''.join('JOB N' + str(i) + ' worker.sub\n' for i in range(8)))
        status = '[\n Type = "DagStatus";\n NodesTotal = 8;\n NodesDone = 8;\n'
        status += ''.join(' ' + key + ' = 0;\n' for key in (
            'NodesPre', 'NodesQueued', 'NodesPost', 'NodesReady', 'NodesUnready',
            'NodesFutile', 'NodesFailed', 'JobProcsHeld', 'JobProcsIdle'))
        (base / 'nodes.status').write_text(status + ']\n')
        for path in (base / 'nodes.status', base / 'ordinary/production_complete.json',
                     base / 'weighted/production_complete.json'):
            os.utime(path, (900, 900))
        authority = dict(schema='shift-stream-gen-completion-authority-v1',
            manager_directory=str(base), manager_cluster=456,
            files={name: module.sha(base / name) for name in (
                'ordinary/manifest.json', 'weighted/manifest.json',
                'weighted/scientific_gate.json', 'production.dag')})
        gen_policy = dict(suite_tag=tag, reserved_bytes_per_worker=20, reserved_files_per_worker=3)
        return base, authority, gen_policy

    def test_complete_gen_releases_reserve_after_accounting_queue_and_fresh_quota(self):
        base, authority, gen_policy = self.closed_gen()
        completion = module.gen_completion_state(authority, gen_policy, [], self.observation)
        self.assertTrue(completion['complete'], completion)
        protected = module.reserve_other_gen_slots(self.admission, self.observation, gen_policy, completion)
        self.assertEqual(protected['protected_gen_worker_slots'], 0)
        self.assertEqual(protected['free_bytes'], self.observation['free_bytes'])
        self.assertEqual(protected['free_files'], self.observation['free_files'])
        self.assertEqual(len(completion['evidence_sha256']), 3)

    def test_completed_gen_still_reserves_for_manager_children_and_tagged_held_jobs(self):
        base, authority, gen_policy = self.closed_gen()
        for ad in ({'ClusterId': 456}, {'DAGManJobId': 456},
                   {'ShiftSuiteTag': 'gen', 'JobStatus': 5}):
            with self.subTest(ad=ad):
                completion = module.gen_completion_state(authority, gen_policy, [ad], self.observation)
                self.assertFalse(completion['complete'])
                self.assertIn('queue records', completion['reason'])
                protected = module.reserve_other_gen_slots(self.admission, self.observation,
                                                          gen_policy, completion)
                self.assertGreater(protected['protected_gen_worker_slots'], 0)

    def test_completed_gen_waits_for_quota_started_after_last_publication_and_dag(self):
        base, authority, gen_policy = self.closed_gen()
        old_quota = self.observation | {'quota_observation_started_at_epoch': 900}
        completion = module.gen_completion_state(authority, gen_policy, [], old_quota)
        self.assertFalse(completion['complete'])
        self.assertIn('after GEN closed', completion['reason'])

    def test_gen_reserve_is_not_released_for_missing_partial_or_unfinished_evidence(self):
        base, authority, gen_policy = self.closed_gen()
        low = base / 'ordinary/production_complete.json'
        good = low.read_text()
        low.unlink()
        self.assertFalse(module.gen_completion_state(authority, gen_policy, [], self.observation)['complete'])
        low.write_text(good)
        incomplete = json.loads(good)
        incomplete['strata']['bin0']['chunks'] = 0
        module.save(low, incomplete)
        result = module.gen_completion_state(authority, gen_policy, [], self.observation)
        self.assertFalse(result['complete'])
        self.assertIn('planned source', result['reason'])
        low.write_text(good)
        status = base / 'nodes.status'
        status.write_text(status.read_text().replace('NodesPre = 0;', 'NodesPre = 1;'))
        result = module.gen_completion_state(authority, gen_policy, [], self.observation)
        self.assertFalse(result['complete'])
        self.assertIn('unfinished work', result['reason'])
        (base / 'production.dag').write_text('JOB replacement worker.sub\n')
        result = module.gen_completion_state(authority, gen_policy, [], self.observation)
        self.assertFalse(result['complete'])
        self.assertIn('Frozen GEN authority changed', result['reason'])

    def test_controller_records_closed_gen_evidence_and_releases_its_reserve(self):
        base, authority, gen_policy = self.closed_gen()
        module.save(self.root / 'gen_admission.json', gen_policy)
        self.controller.policy.update(parallel_gen_admission_policy=str(self.root / 'gen_admission.json'),
                                      parallel_gen_completion=authority)
        result = self.step()
        self.assertTrue(result['gen_completion']['complete'], result)
        self.assertEqual(result['protected_gen_worker_slots'], 0)

    def test_completed_reservations_persist_until_quota_started_after_disappearance(self):
        mapped = module.mapped_ads([dict(JobUniverse=5, JobStatus=2, ShiftStreamCampaign='stream',
                                        ShiftStreamTask='source0_reco')], 'stream')
        self.assertTrue(mapped[0]['ShiftMuEnrichedQCD'])
        result = module.decision(self.admission, self.observation, [],
            {'completed': {'at_epoch': 998, 'seen_queued': True}}, 'next', 1000, 10000)
        self.assertIn('completed', result['reservations'])
        later = self.observation | {'quota_observation_started_at_epoch': 1001,
                                   'quota_checked_at_epoch': 1002, 'remote_account_checked_at_epoch': 1002}
        result = module.decision(self.admission, later, [], result['reservations'], 'next', 1002, 10000)
        self.assertNotIn('completed', result['reservations'])

    def completed_pre_claim(self):
        task = self.task()
        task['result_path'] = str(Path(task['working_directory']) / 'result.json')
        module.save(self.root / 'tasks' / (task['task_id'] + '.json'), task)
        self.step()
        self.successful_status(task)
        module.save(task['result_path'], {'complete': True})
        self.events(task, '001 (123.000.000) execution\n...\n005 (123.000.000) exit\n'
                         '    (1) Normal termination (return value 0)\n...\n')
        for key in ('status_path', 'result_path', 'event_log'):
            os.utime(task[key], (990, 990))
        self.step()
        tasks = module.load_tasks(self.root, self.controller.ledger)
        reservations = module.read(self.root / 'admission/pre_reservations.json')
        self.assertNotIn('seen_queued', reservations[task['task_id']])
        return task, tasks, reservations

    def test_completed_unseen_pre_waits_for_quota_after_all_completion_evidence(self):
        task, tasks, reservations = self.completed_pre_claim()
        retained, released = module.release_completed_pre_reservations(
            'stream', tasks, self.controller.ledger, [], self.observation, reservations)
        self.assertEqual(retained, reservations)
        self.assertEqual(released, {})
        newer = self.observation | {'quota_observation_started_at_epoch': 1001, 'quota_checked_at_epoch': 1002}
        retained, released = module.release_completed_pre_reservations(
            'stream', tasks, self.controller.ledger, [], newer, reservations)
        self.assertEqual(retained, {})
        self.assertEqual(released[task['task_id']]['closed_at_epoch'], 1000)
        self.assertEqual(reservations[task['task_id']], {'at_epoch': 1000})
        for invalid in (newer | {'complete': False},
                        newer | {'quota_observation_started_at_epoch': None},
                        newer | {'quota_checked_at_epoch': 900}):
            with self.subTest(invalid_quota=invalid):
                retained, released = module.release_completed_pre_reservations(
                    'stream', tasks, self.controller.ledger, [], invalid, reservations)
                self.assertEqual(retained, reservations)
                self.assertFalse(released)
        for key in ('status_path', 'result_path', 'event_log'):
            with self.subTest(newer_file=key):
                os.utime(task[key], (1001, 1001))
                retained, released = module.release_completed_pre_reservations(
                    'stream', tasks, self.controller.ledger, [], newer, reservations)
                self.assertEqual(retained, reservations)
                self.assertFalse(released)
                os.utime(task[key], (990, 990))

    def test_completed_pre_keeps_claim_for_live_held_or_matching_retried_task(self):
        task, tasks, reservations = self.completed_pre_claim()
        newer = self.observation | {'quota_observation_started_at_epoch': 1001, 'quota_checked_at_epoch': 1002}
        for ad in ({'ClusterId': 123, 'ProcId': 0, 'JobStatus': 2},
                   {'ClusterId': 123, 'ProcId': 0, 'JobStatus': 5},
                   {'ClusterId': 999, 'JobStatus': 5, 'ShiftStreamCampaign': 'stream',
                    'ShiftStreamTask': task['task_id']},
                   {'JobStatus': 2, 'Iwd': task['working_directory']}):
            with self.subTest(ad=ad):
                retained, released = module.release_completed_pre_reservations(
                    'stream', tasks, self.controller.ledger, [ad], newer, reservations)
                self.assertEqual(retained, reservations)
                self.assertFalse(released)

    def test_completed_pre_keeps_claim_for_changed_status_native_exit_or_unknown_times(self):
        task, tasks, reservations = self.completed_pre_claim()
        newer = self.observation | {'quota_observation_started_at_epoch': 1001, 'quota_checked_at_epoch': 1002}
        self.events(task, '001 (123.000.000) execution\n...\n005 (123.000.000) exit\n'
                         '    (1) Normal termination (return value 1)\n...\n')
        retained, released = module.release_completed_pre_reservations(
            'stream', tasks, self.controller.ledger, [], newer, reservations)
        self.assertEqual(retained, reservations)
        self.assertFalse(released)
        self.events(task, '001 (123.000.000) execution\n...\n005 (123.000.000) exit\n'
                         '    (1) Normal termination (return value 0)\n...\n')
        os.utime(task['event_log'], (990, 990))
        status = module.read(task['status_path'])
        status['complete'] = False
        module.save(task['status_path'], status)
        os.utime(task['status_path'], (990, 990))
        # Even a matching hash cannot replace the required semantic success.
        self.controller.ledger[task['task_id']]['status_sha256'] = module.sha(task['status_path'])
        retained, released = module.release_completed_pre_reservations(
            'stream', tasks, self.controller.ledger, [], newer, reservations)
        self.assertEqual(retained, reservations)
        self.assertFalse(released)
        self.successful_status(task)
        os.utime(task['status_path'], (990, 990))
        self.controller.ledger[task['task_id']]['status_sha256'] = module.sha(task['status_path'])
        self.controller.ledger[task['task_id']]['completed_at_epoch'] = None
        retained, released = module.release_completed_pre_reservations(
            'stream', tasks, self.controller.ledger, [], newer, reservations)
        self.assertEqual(retained, reservations)
        self.assertFalse(released)

    def test_stream_persists_exact_completed_pre_release_receipt(self):
        task, tasks, reservations = self.completed_pre_claim()
        self.observation.update(quota_observation_started_at_epoch=1001, quota_checked_at_epoch=1002)
        with mock.patch.object(module, 'refresh_quota', return_value=self.observation), \
                mock.patch.object(module, 'afs_headroom', return_value=10000):
            heartbeat = self.controller.step(now=1003)
        self.assertIn(task['task_id'], heartbeat['released_completed_PREs'])
        self.assertEqual(module.read(self.root / 'admission/pre_reservations.json'), {})
        proof = module.read(self.root / 'admission/completed_pre_releases.json')[task['task_id'] + ':123']
        self.assertEqual(proof['status_sha256'], self.controller.ledger[task['task_id']]['status_sha256'])
        self.assertEqual(self.commander.call_count, 1)

    def test_scheduler_descriptions_have_persistent_iwd_and_seven_days(self):
        for role in ('controller', 'watchdog'):
            text = (self.root / (role+'.sub')).read_text()
            self.assertIn('initialdir = '+str(self.root), text)
            self.assertIn('+MaxRuntime = 604800', text)
            self.assertIn('+ShiftStreamController = true', text)

    def test_supervisor_adoption_preserves_running_tasks_ledger_nonce_and_campaign_state(self):
        task = self.task()
        self.step()
        record = dict(self.controller.ledger[task['task_id']])
        self.reader.return_value = [dict(ClusterId=123, ProcId=0, JobStatus=2,
            JobCurrentStartDate=999, Iwd=task['working_directory'], ShiftStreamCampaign='stream',
            ShiftStreamTask=task['task_id'], ShiftStreamNonce=record['nonce'])]
        before = {path: module.sha(path) for path in (
            self.root / 'policy.json', self.root / 'deployment.json',
            self.root / 'tasks' / (task['task_id'] + '.json'), Path(task['submit_file']))}
        replacement = self.root.parent / 'replacement_supervision'
        preparation.adopt(replacement, self.root)
        adopted = module.StreamController(replacement, self.reader, self.commander)
        self.assertEqual(adopted.root, self.root)
        self.assertEqual(adopted.supervision_root, replacement)
        self.assertEqual(adopted.ledger[task['task_id']]['nonce'], record['nonce'])
        with mock.patch.object(module, 'refresh_quota', return_value=self.observation) as quota, \
                mock.patch.object(module, 'afs_headroom', return_value=10000):
            result = adopted.step(now=1000)
        quota.assert_called_once_with(self.root / 'admission')
        self.assertEqual(result['submitted_this_poll'], 0)
        self.assertEqual(self.commander.call_count, 1)
        self.assertEqual(adopted.ledger[task['task_id']]['job'], [123, 0])
        self.assertTrue((self.root / 'heartbeat.json').is_file())
        self.assertFalse((replacement / 'heartbeat.json').exists())
        self.assertEqual({path: module.sha(path) for path in before}, before)
        self.assertIn('initialdir = ' + str(replacement), (replacement / 'controller.sub').read_text())

    def test_new_source_sampling_revision_is_pinned_and_declared(self):
        module.save(self.root/'ledger.json', {})
        module.save(self.root/'factory_policy.json', {'original': True})
        module.save(self.root/'source_plan.json', {'source_slots': []})
        factory = self.root.parent/'factory.json'
        module.save(factory, {'selection_policy': dict(schema='shift-stream-GEN-selection-v1',
            mode='GEN-stratified-weight-aware', seed='new-selection', training_seed='old-training',
            training_folds=10, minimum_probability=.01, maximum_variance_inflation=2.)})
        revision = dict(schema='shift-unselected-source-sampling-revision-v1',
            existing_selections_unchanged=True, previous_factory_sha256=module.sha(self.root/'factory_policy.json'),
            source_plan_sha256=module.sha(self.root/'source_plan.json'),
            factory_policy=dict(path=str(factory), sha256=module.sha(factory)))
        replacement = self.root.parent/'new_selection_supervisor'
        preparation.adopt(replacement, self.root, dict(selection_revision=revision,
            prepare_command=['python3', 'preparer.py', '--policy', str(factory)]))
        adopted = module.StreamController(replacement, self.reader, self.commander)
        self.assertTrue(adopted.policy['adoption']['science_inputs_changed'])
        self.assertFalse(adopted.policy['adoption']['existing_task_inputs_changed'])
        module.save(factory, {'selection_policy': {}})
        with self.assertRaisesRegex(ValueError, 'exact frozen preparer policy'):
            module.StreamController(replacement, self.reader, self.commander)

    def test_supervisor_adoption_never_retries_an_ambiguous_submission(self):
        task = self.task()
        self.commander.side_effect = module.NativeCondorError('lost response')
        with self.assertRaises(module.NativeCondorError):
            self.step()
        nonce = self.controller.ledger[task['task_id']]['nonce']
        replacement = self.root.parent / 'replacement_supervision'
        preparation.adopt(replacement, self.root)
        self.commander.side_effect = None
        adopted = module.StreamController(replacement, self.reader, self.commander)
        with mock.patch.object(module, 'refresh_quota', return_value=self.observation), \
                mock.patch.object(module, 'afs_headroom', return_value=10000):
            adopted.step(now=1000)
        self.assertEqual(adopted.ledger[task['task_id']]['nonce'], nonce)
        self.assertEqual(adopted.ledger[task['task_id']]['phase'], 'submitting')
        self.assertEqual(self.commander.call_count, 1)

    def test_peer_authority_only_adoption_uses_supervisor_quota_and_campaign_reservations(self):
        import read_shift_external_storage_commitments as external
        task = self.task()
        self.step()
        record = copy.deepcopy(self.controller.ledger[task['task_id']])
        self.reader.return_value = [dict(ClusterId=123, ProcId=0, JobStatus=2,
            JobCurrentStartDate=999, Iwd=task['working_directory'], ShiftStreamCampaign='stream',
            ShiftStreamTask=task['task_id'], ShiftStreamNonce=record['nonce'])]
        authority = {'schema': 'shift-statistics-peer-storage-authority-v1', 'name': 'old'}
        replacement_authority = authority | {'name': 'replacement'}
        self.policy['admission']['external_storage_reservations'] = [authority]
        module.save(self.root/'policy.json', self.policy)
        deployment = module.read(self.root/'deployment.json')
        deployment['files']['policy.json'] = module.sha(self.root/'policy.json')
        module.save(self.root/'deployment.json', deployment)
        paths = [self.root/'policy.json', self.root/'admission/policy.json',
                 self.root/'admission/pre_reservations.json',
                 self.root/'tasks/source0_reco.json', Path(task['submit_file'])]
        before = {path: module.sha(path) for path in paths}
        replacement = self.root.parent/'peer_authority_supervision'
        with mock.patch.object(external, 'reviewed_peer_authority_replacement',
                               return_value=replacement_authority):
            preparation.adopt(replacement, self.root,
                              {'peer_storage_authority': replacement_authority})
            adopted = module.StreamController(replacement, self.reader, self.commander)
        self.assertNotIn('additional_peer_meson_campaigns', adopted.policy)
        self.assertEqual(module.read(replacement/'admission/policy.json')[
            'external_storage_reservations'], [replacement_authority])
        module.save(replacement/'admission/last_quota.json', self.observation)
        for dry_run in (False, True):
            with self.subTest(dry_run=dry_run), \
                    mock.patch.object(module, 'refresh_quota', return_value=self.observation) as audit:
                result = adopted.step(now=1000, dry_run=dry_run)
                if dry_run:
                    audit.assert_not_called()
                else:
                    audit.assert_called_once_with(replacement/'admission')
                self.assertEqual(result['submitted_this_poll'], 0)
                self.assertEqual(adopted.ledger[task['task_id']]['nonce'], record['nonce'])
                self.assertEqual(adopted.ledger[task['task_id']]['job'], record['job'])
                self.assertEqual({path: module.sha(path) for path in paths}, before)
        self.assertEqual(self.commander.call_count, 1)
        self.assertFalse((replacement/'admission/pre_reservations.json').exists())

    def test_supervisor_adoption_forbids_changing_science_or_admission(self):
        module.save(self.root / 'ledger.json', {})
        for key in ('tag', 'admission', 'operational_gates', 'worker_limits'):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'worker or admission'):
                preparation.adopt(self.root.parent / 'replacement', self.root, {key: 'changed'})

    def test_peer_authority_override_uses_reviewed_guard_and_preserves_campaign_admission(self):
        import read_shift_external_storage_commitments as external
        old = {'schema': 'shift-statistics-peer-storage-authority-v1', 'name': 'previous'}
        replacement = old | {'name': 'replacement'}
        original = self.admission | {'external_storage_reservations': [old]}
        policy = self.policy | {'admission': original, 'peer_storage_authority': replacement}
        with mock.patch.object(external, 'reviewed_peer_authority_replacement',
                               return_value=replacement) as review:
            result = module.stream_admission_policy(policy)
        review.assert_called_once_with(old, replacement)
        self.assertEqual(result['external_storage_reservations'], [replacement])
        self.assertEqual(original['external_storage_reservations'], [old])
        self.assertIn('peer_storage_authority', module.SUPERVISOR_OVERRIDES)

    def test_peer_authority_override_never_accepts_ambiguous_or_unreviewed_contracts(self):
        import read_shift_external_storage_commitments as external
        for authorities in ([], [{}, {}]):
            with self.subTest(authorities=authorities), self.assertRaisesRegex(ValueError, 'one complete'):
                module.stream_admission_policy(self.policy | {'peer_storage_authority': {},
                    'admission': self.admission | {'external_storage_reservations': authorities}})
        with mock.patch.object(external, 'reviewed_peer_authority_replacement',
                               side_effect=ValueError('changed science')):
            with self.assertRaisesRegex(ValueError, 'changed science'):
                module.stream_admission_policy(self.policy | {'peer_storage_authority': {},
                    'admission': self.admission | {'external_storage_reservations': [{}]}})

    def test_future_statistics_monitor_needs_exact_readonly_launch_and_production(self):
        root = self.root.parent/'future_statistics'
        monitor = root/'monitor'
        monitor.mkdir(parents=True)
        controller_path = root/'config.json'
        module.save(controller_path, {'directory': str(self.root), 'worklist': '/science/worklist.json'})
        program = monitor/'watch_dark_photon_statistics.py'
        program.write_text('# Synthetic frozen read-only watcher.\n')
        path = monitor/'config.json'
        config = dict(schema='shift-dp-statistics-watchdog-v1', no_job_mutations=True,
            production_directory=str(self.root), worklist='/science/worklist.json',
            supervisor_deployment_directory=str(root), supervisor_receipt=str(root/'submission.json'),
            resolve_supervisor_from_receipt=True,
            dependencies={str(controller_path): module.sha(controller_path), str(program): module.sha(program)})
        module.save(path, config)
        args = str(program)+' --config '+str(path)
        submit = monitor/'watchdog.sub'
        submit.write_text('initialdir = '+str(monitor)+'\nexecutable = /usr/bin/python3\narguments = '+args
            +'\n+ShiftDPStatisticsReadOnly = True\n+ShiftDPStatisticsWatchdog = True\n')
        expected = dict(Owner='jniedzie', JobUniverse=5, Iwd=str(monitor), Cmd='/usr/bin/python3',
            Args=args, ShiftDPStatisticsReadOnly=True, ShiftDPStatisticsWatchdog=True,
            ShiftDPStatisticsTask=str(monitor))
        binding = dict(name='future_statistics', audited_purpose='read_only_statistics_watchdog',
            expected_native=expected, controller_config=dict(path=str(controller_path),sha256=module.sha(controller_path)),
            submit=dict(path=str(submit),sha256=module.sha(submit)),
            frozen_input_sha256={str(p):module.sha(p) for p in (controller_path,path,program,submit)})
        policy = self.policy | {'nonpublishing_peer_controllers': [binding]}
        quota = self.observation | {'remote_account_peer_writer_rows': [],
            'external_storage_commitments': {'stream_capacity': dict(allowed_bsm_directories=[str(self.root)],
                controller_bindings=[], required_absent_stream_campaigns=['old'])}}
        native = expected | {'ClusterId': 456, 'ProcId': 0, 'JobStatus': 2}
        self.assertTrue(module.verify_stream_peer_writers(policy,quota,[native])['stream_peer_writers_verified'])
        self.assertFalse(module.verify_stream_peer_writers(policy,quota,[native | {'Args': args+' --mutate'}])[
            'stream_peer_writers_verified'])
        config['no_job_mutations'] = False
        module.save(path,config)
        binding['frozen_input_sha256'][str(path)] = module.sha(path)
        with self.assertRaisesRegex(ValueError, 'no-mutation'):
            module.nonpublishing_peer_bindings(policy)

    def test_capacity_rollover_keeps_existing_job_and_accounts_for_other_workers(self):
        task = self.task()
        self.step()
        record = dict(self.controller.ledger[task['task_id']])
        queue = dict(ClusterId=123, ProcId=0, JobStatus=2, JobUniverse=5,
            JobCurrentStartDate=999, Iwd=task['working_directory'], ShiftStreamCampaign='stream',
            ShiftStreamTask=task['task_id'], ShiftStreamNonce=record['nonce'])
        self.reader.return_value = [queue]
        for index in range(12):
            self.task('new_reco_' + str(index))
        pins = {path: module.sha(path) for path in (
            self.root/'policy.json', self.root/'admission/policy.json',
            self.root/'tasks'/(task['task_id']+'.json'), Path(task['submit_file']))}
        replacement = self.root.parent/'capacity_supervision'
        preparation.adopt(replacement, self.root,
            {'admission_worker_ceiling': 900, 'maximum_submissions_per_poll': 100})
        adopted = module.StreamController(replacement, self.reader, self.commander)
        self.assertEqual(adopted.admission_policy['worker_ceiling'], 900)
        self.assertEqual(adopted.admission_policy['account_ceiling'], 1000)
        with mock.patch.object(module, 'refresh_quota', return_value=self.observation), \
                mock.patch.object(module, 'afs_headroom', return_value=10000):
            result = adopted.step(now=1000)
        self.assertEqual(result['submitted_this_poll'], 12)
        self.assertEqual(adopted.ledger[task['task_id']]['job'], [123, 0])
        self.assertEqual(adopted.ledger[task['task_id']]['nonce'], record['nonce'])
        self.assertEqual({path: module.sha(path) for path in pins}, pins)
        self.assertEqual(module.read(replacement/'admission/policy.json')['worker_ceiling'], 900)
        # A streaming task uses its real reservation, rather than demanding
        # all 900 possible slots be free before the first task can start.
        remote = [dict(ClusterId=index, JobStatus=2) for index in range(101)]
        crowded = self.observation | {'remote_account_workers': remote}
        result = module.decision(adopted.admission_policy, crowded, [], {}, 'next', 1000, 10000)
        self.assertTrue(result['admitted'])
        crowded['remote_account_workers'] = [dict(ClusterId=index, JobStatus=2) for index in range(1000)]
        result = module.decision(adopted.admission_policy, crowded, [], {}, 'next', 1000, 10000)
        self.assertFalse(result['admitted'])
        self.assertIn('insufficient worker capacity', result['reason'])
        result = module.decision(adopted.admission_policy, crowded | {
            'remote_account_coverage_verified': False}, [], {}, 'next', 1000, 10000)
        self.assertFalse(result['admitted'])
        self.assertIn('coverage is unknown', result['reason'])

    def test_capacity_rollover_rejects_invalid_ceilings_and_poll_limits(self):
        module.save(self.root/'ledger.json', {})
        for changes in ({'admission_worker_ceiling': 1001}, {'admission_worker_ceiling': True},
                {'maximum_submissions_per_poll': 0}, {'maximum_submissions_per_poll': 5}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                preparation.adopt(self.root.parent/'invalid_capacity', self.root, changes)

    def test_freezer_uses_the_split_account_and_external_commitment_auditor(self):
        self.assertEqual(module.sha(self.root/'run_shift_quota_audit.py'),
                         module.sha(SCRIPTS/'run_shift_night_quota_audit.py'))
        self.assertNotEqual(module.sha(self.root/'run_shift_quota_audit.py'),
                            module.sha(SCRIPTS/'run_shift_quota_audit.py'))

    def test_freezer_can_preserve_explicit_old_helpers_without_changing_defaults_or_science(self):
        old = self.root.parent/'old_native.py'
        old.write_text('# Synthetic previous native client, deliberately distinct.\n')
        target = self.root.parent/'explicit_helper_deployment'
        preparation.prepare(target,self.policy,helper_sources={'shift_condor_native.py':old})
        self.assertEqual(module.sha(target/'shift_condor_native.py'),module.sha(old))
        self.assertEqual(module.sha(target/'run_shift_stream_controller.py'),
                         module.sha(SCRIPTS/'run_shift_stream_controller.py'))
        with self.assertRaisesRegex(ValueError,'only known controller helpers'):
            preparation.prepare(self.root.parent/'bad_helpers',self.policy,
                                helper_sources={'arbitrary_worker.py':old})

    def captured_quota(self):
        path=Path(__file__).resolve().parent/'fixtures/shift_quota_observation.py'
        spec=importlib.util.spec_from_file_location('captured_stream_quota',path)
        fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)
        return fixture.OBSERVATION,fixture.ADMISSION_POLICY

    def test_actual_native_audit_fixture_passes_stream_admission(self):
        observation,policy=self.captured_quota()
        policy.update(worker_ceiling=16,reserved_bytes_per_worker=32*1024**2,
                      suite_tag='stream')
        result=module.decision(policy,observation,[],{},'scan',observation['checked_at_epoch'],10**9)
        self.assertTrue(result['admitted'],result)
        old_schema={key:value for key,value in observation.items()
                    if not key.startswith('remote_account') and key!='quota_observation_started_at_epoch'}
        old_schema.update(account_coverage_verified=True,account_workers=[],
                          account_checked_at_epoch=observation['checked_at_epoch'])
        result=module.decision(policy,old_schema,[],{},'scan',observation['checked_at_epoch'],10**9)
        self.assertFalse(result['admitted'])
        self.assertIn('missing or stale',result['reason'])

    def test_real_frozen_auditor_result_reaches_the_admission_guard(self):
        captured,policy=self.captured_quota()
        spec=importlib.util.spec_from_file_location('actual_stream_quota_launcher',self.root/'run_shift_ntuple_controller.py')
        launcher=importlib.util.module_from_spec(spec);spec.loader.exec_module(launcher)
        module.save(self.root/'admission/policy.json',policy)
        with mock.patch.object(launcher,'run_condor',return_value='789.0 - 789.0\n'):
            pending=launcher.launch_quota_audit(self.root/'admission',persistent=True)
        folder=Path(pending['folder'])
        request=json.loads((folder/'quota_request.json').read_text())
        self.assertEqual(request['local_schedd_name'],policy['schedd_name'])
        self.assertEqual(request['external_storage_reservations'],policy['external_storage_reservations'])
        self.assertEqual(module.sha(folder/'audit.py'),module.sha(SCRIPTS/'run_shift_night_quota_audit.py'))
        self.assertIn('read_shift_external_storage_commitments.py',(folder/'quota.sub').read_text())
        self.assertIn('+MaxRuntime = 1200', (folder/'quota.sub').read_text())
        self.assertIn('+SendCredential = True', (folder/'quota.sub').read_text())
        spec=importlib.util.spec_from_file_location('actual_stream_auditor',folder/'audit.py')
        auditor=importlib.util.module_from_spec(spec);spec.loader.exec_module(auditor)
        external=json.loads(json.dumps(captured['external_storage_commitments']))
        now=captured['checked_at_epoch']+1
        external['checked_at_epoch']=now-1
        def native_command(arguments,**options):
            if arguments[0]=='/usr/bin/python3':
                self.assertEqual(options['timeout'], 600)
                Path('external_commitment_result.json').write_text(json.dumps(external))
                return subprocess.CompletedProcess(arguments,0,'','')
            self.assertEqual(arguments[0],'/usr/bin/eos')
            quota=('space=/eos/user/j/jniedzie/ maxlogicalbytes=1000000000000 '
                f"usedlogicalbytes={1000000000000-captured['free_bytes']} "
                f"maxfiles=100000 usedfiles={100000-captured['free_files']}\n")
            return subprocess.CompletedProcess(arguments,0,quota,'')
        old_directory=Path.cwd()
        try:
            os.chdir(folder)
            with mock.patch.object(auditor.subprocess,'run',side_effect=native_command), \
                    mock.patch.object(auditor,'_account_schedds',return_value=set(captured['account_schedds'])), \
                    mock.patch.object(auditor,'query',return_value=[]) as remote_query, \
                    mock.patch.object(auditor.time,'time',return_value=now):
                result=auditor.audit(request)
        finally:
            os.chdir(old_directory)
        self.assertTrue(result['remote_account_coverage_verified'],result)
        self.assertEqual(result['excluded_local_schedd'],policy['schedd_name'])
        self.assertEqual(result['external_storage_commitments'],external)
        self.assertEqual(result['free_bytes'],captured['free_bytes'])
        self.assertTrue(all(call.kwargs['schedd']!=policy['schedd_name'] for call in remote_query.call_args_list))
        module.save(folder/'quota_result.json',result)
        with mock.patch.object(launcher.time,'time',return_value=now):
            observed=launcher.refresh_quota(self.root/'admission')
        self.assertEqual(observed,result)
        admitted=module.decision(policy,observed,[],{},'scan',now,10**9)
        self.assertTrue(admitted['admitted'],admitted)


if __name__ == '__main__':
    unittest.main()
