# Code style: AI-code. AI-generated streaming pipeline safety regression tests.
import json
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

    def test_bulk_waits_for_explicit_operational_gate(self):
        self.task(canary=False)
        self.assertEqual(self.step()['submitted_this_poll'], 0)
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

    def test_supervisor_adoption_forbids_changing_science_or_admission(self):
        module.save(self.root / 'ledger.json', {})
        for key in ('tag', 'admission', 'operational_gates', 'worker_limits'):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'worker or admission'):
                preparation.adopt(self.root.parent / 'replacement', self.root, {key: 'changed'})

    def test_freezer_uses_the_split_account_and_external_commitment_auditor(self):
        self.assertEqual(module.sha(self.root/'run_shift_quota_audit.py'),
                         module.sha(SCRIPTS/'run_shift_night_quota_audit.py'))
        self.assertNotEqual(module.sha(self.root/'run_shift_quota_audit.py'),
                            module.sha(SCRIPTS/'run_shift_quota_audit.py'))

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
