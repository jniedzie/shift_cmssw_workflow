# Code style: AI-code. AI-generated tests for the one-time weekend handoff.
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import run_shift_stream_transition as module


class TransitionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='20261016-stream-transition-', dir='/tmp')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.old = self.root / 'old'
        self.bulk = self.root / 'bulk'
        (self.old / 'tasks').mkdir(parents=True)
        self.bulk.mkdir()
        self.config = {'owner': 'owner', 'pinned_files': {},
            'prepared_at_epoch': time.time(), 'canary_campaign': {'directory': str(self.root / 'canary')},
            'canary_gate': {'path': str(self.root / 'gate.json'), 'expected': {'checks.good': True}},
            'old_campaign': {'directory': str(self.old), 'tag': 'old', 'managers': [
                {'job': [11, 0], 'directory': str(self.old), 'watchdog': True},
                {'job': [10, 0], 'directory': str(self.old), 'watchdog': False}]},
            'bulk_campaign': {'directory': str(self.bulk), 'tag': 'bulk'}}
        for role in ('controller', 'watchdog'):
            (self.bulk / (role + '.sub')).write_text('initialdir = ' + str(self.bulk) + '\n')
        self.commander = mock.Mock(return_value='40.0 - 40.0\n')
        self.reader = mock.Mock(return_value=[])
        self.transition = module.Transition(self.root, self.config, self.reader, self.commander)

    def idle(self, starts=0, status=1):
        directory = self.old / 'cohorts' / 'source' / 'source_reco_000'
        directory.mkdir(parents=True)
        task = {'working_directory': str(directory), 'stage': 'reco', 'event_log': str(directory / 'events.log')}
        module.save(self.old / 'tasks/source_reco_000.json', task)
        ledger = {'source_reco_000': {'phase': 'submitted', 'job': [20, 0]}}
        module.save(self.old / 'ledger.json', ledger)
        return {'ClusterId': 20, 'ProcId': 0, 'Owner': 'owner', 'Iwd': str(directory),
            'JobStatus': status, 'NumJobStarts': starts, 'ShiftStreamStage': 'reco',
            'ShiftStreamTask': 'source_reco_000', 'ShiftStreamCampaign': 'old'}, ledger

    def test_no_gate_causes_no_scheduler_mutation(self):
        self.assertEqual(self.transition.step()['phase'], 'waiting_for_batched_canary')
        self.commander.assert_not_called()
        self.reader.assert_not_called()

    def test_waiting_heartbeat_can_record_actual_canary_stage_counts(self):
        self.config['record_canary_native_progress'] = True
        self.config['canary_campaign']['tag'] = 'canary'
        self.reader.return_value = [dict(ClusterId=80, ProcId=0, JobUniverse=5,
            JobStatus=2, ShiftStreamCampaign='canary', ShiftStreamStage='reco',
            ShiftNtupleTier='SIM', ShiftNtupleStageRecordsStarted=7,
            ShiftNtupleGENEvents=20, ShiftNtupleSIMEvents=0, ShiftNtupleProgressEpoch=3000),
            dict(ClusterId=81, ProcId=0, JobUniverse=5, ShiftStreamCampaign='unrelated')]
        result = self.transition.step()
        progress = result['canary_progress']['native_workers']
        self.assertEqual(len(progress), 1)
        self.assertEqual(progress[0]['ShiftNtupleStageRecordsStarted'], 7)
        self.assertEqual(progress[0]['ShiftNtupleGENEvents'], 20)
        self.commander.assert_not_called()

    def test_gate_rejects_changed_status(self):
        status = self.root / 'status.json'
        status.write_text('{}')
        module.save(self.root / 'gate.json', {'passed': True, 'checks': {'good': True},
            'native_task_proofs': [{'status_path': str(status), 'status_sha256': 'wrong'}]})
        with self.assertRaisesRegex(ValueError, 'status changed'):
            module.ready_gate(self.config)

    def test_failed_and_stale_canary_stop_handoff(self):
        directory = Path(self.config['canary_campaign']['directory'])
        module.save(directory / 'heartbeat.json', {'checked_at_epoch': 1000, 'problems': []})
        with self.assertRaisesRegex(ValueError, 'stale'):
            module.canary_healthy(self.config, now=3000)
        module.save(directory / 'heartbeat.json', {'checked_at_epoch': 3000, 'problems': ['held reco']})
        with self.assertRaisesRegex(ValueError, 'failed stage'):
            module.canary_healthy(self.config, now=3001)

    def test_old_running_and_previously_started_idle_survive(self):
        ad, ledger = self.idle(starts=1)
        self.assertEqual(module.obsolete_idle(self.config, [ad], ledger), [])
        ad['NumJobStarts'], ad['JobStatus'] = 0, 2
        self.assertEqual(module.obsolete_idle(self.config, [ad], ledger), [])

    def test_only_exact_never_started_idle_is_eligible(self):
        ad, ledger = self.idle()
        self.assertEqual(module.obsolete_idle(self.config, [ad], ledger), [ad])
        ad['Iwd'] = '/pool/condor/dir_wrong'
        with self.assertRaises(ValueError):
            module.obsolete_idle(self.config, [ad], ledger)

    def test_native_execute_protects_idle_job_with_lagging_start_counter(self):
        ad, ledger = self.idle()
        task = module.read(self.old / 'tasks/source_reco_000.json')
        Path(task['event_log']).write_text('001 (20.000.000) execution\n...\n')
        self.assertEqual(module.obsolete_idle(self.config, [ad], ledger), [])

    def test_mismatched_ledger_blocks_cleanup(self):
        ad, ledger = self.idle()
        ledger['source_reco_000']['job'] = [99, 0]
        with self.assertRaisesRegex(ValueError, 'science ledger'):
            module.obsolete_idle(self.config, [ad], ledger)

    def test_unknown_queue_blocks_all_removal(self):
        self.reader.side_effect = module.NativeCondorError('unknown scheduler')
        with self.assertRaises(module.NativeCondorError):
            self.transition.retire_old()
        self.commander.assert_not_called()

    def test_temporary_native_read_failure_waits_without_any_job_mutation(self):
        self.config['record_canary_native_progress'] = True
        self.config['canary_campaign']['tag'] = 'canary'
        self.reader.side_effect = module.NativeCondorError('SECMAN:2007:Read failure during security negotiation')
        heartbeat = self.transition.poll()
        self.assertEqual(heartbeat['phase'], 'waiting_for_scheduler')
        self.assertEqual(heartbeat['scheduler_state'], 'unknown')
        self.assertIn('SECMAN:2007', heartbeat['error'])
        self.assertEqual(heartbeat['consecutive_scheduler_failures'], 1)
        self.assertFalse(heartbeat['scientific_payloads_retried'])
        self.assertTrue((self.root / 'scheduler_observation_failure.json').is_file())
        self.commander.assert_not_called()

    def test_read_recovery_resets_consecutive_failure_count(self):
        self.config['record_canary_native_progress'] = True
        self.config['canary_campaign']['tag'] = 'canary'
        self.reader.side_effect = [module.NativeCondorError('read failed'), [],
                                   module.NativeCondorError('another read failed')]
        self.transition.poll()
        recovered = self.transition.poll()
        self.assertEqual(recovered['phase'], 'waiting_for_batched_canary')
        self.assertEqual(recovered['scheduler_recovered_after_failures'], 1)
        self.assertEqual(self.transition.scheduler_failures, 0)
        self.assertEqual(self.transition.poll()['consecutive_scheduler_failures'], 1)
        self.commander.assert_not_called()

    def test_persistent_unknown_scheduler_stops_at_reviewed_limit(self):
        self.config['record_canary_native_progress'] = True
        self.config['canary_campaign']['tag'] = 'canary'
        self.config['maximum_consecutive_scheduler_failures'] = 2
        transition = module.Transition(self.root, self.config, self.reader, self.commander)
        self.reader.side_effect = module.NativeCondorError('read failed')
        self.assertEqual(transition.poll()['consecutive_scheduler_failures'], 1)
        with self.assertRaisesRegex(module.SchedulerObservationError, '2 consecutive observations'):
            transition.poll()
        self.assertEqual(module.read(self.root / 'scheduler_observation_failure.json')
                         ['consecutive_scheduler_failures'], 2)
        self.commander.assert_not_called()

    def test_observation_wait_does_not_retry_an_ambiguous_submit(self):
        self.commander.side_effect = module.NativeCondorError('submit response unknown')
        self.transition.step = lambda: self.transition.start_role('controller')
        with self.assertRaisesRegex(module.NativeCondorError, 'submit response unknown'):
            self.transition.poll()
        self.assertEqual(self.transition.scheduler_failures, 0)
        with self.assertRaisesRegex(module.NativeCondorError, 'no automatic resubmission'):
            self.transition.poll()
        self.assertEqual(self.commander.call_count, 1)

    def test_non_observation_failure_remains_terminal(self):
        self.transition.step = mock.Mock(side_effect=ValueError('Frozen handoff input changed'))
        with self.assertRaisesRegex(ValueError, 'input changed'):
            self.transition.poll()
        self.assertFalse((self.root / 'scheduler_observation_failure.json').exists())

    def test_invalid_scheduler_failure_limit_is_rejected(self):
        self.config['maximum_consecutive_scheduler_failures'] = True
        with self.assertRaisesRegex(ValueError, 'positive integer'):
            module.Transition(self.root, self.config, self.reader, self.commander)

    def test_unreviewed_old_manager_blocks_all_removal(self):
        self.reader.return_value = [dict(ClusterId=99, ProcId=0,
            ShiftStreamCampaign='old', ShiftStreamController=True)]
        with self.assertRaisesRegex(ValueError, 'unreviewed old campaign manager'):
            self.transition.retire_old()
        self.commander.assert_not_called()

    def test_watchdog_removed_before_controller_and_idle_is_conditional(self):
        idle, _ = self.idle()
        managers = [dict(ClusterId=job, ProcId=0, Owner='owner', Iwd=str(self.old),
            ShiftStreamController=True, ShiftStreamWatchdog=watchdog,
            ShiftStreamCampaign='old') for job, watchdog in [(10, False), (11, True)]]
        self.reader.side_effect = [managers + [idle], [idle], []]
        self.assertTrue(self.transition.retire_old())
        calls = [call.args[0] for call in self.commander.call_args_list]
        self.assertEqual(calls[0][-1], '11.0')
        self.assertEqual(calls[1][-1], '10.0')
        self.assertIn('JobStatus == 1 && NumJobStarts == 0', calls[2][-1])
        self.assertTrue((self.root / 'old_retirement.json').is_file())

    def test_ambiguous_submit_is_never_repeated(self):
        self.commander.return_value = 'unexpected scheduler response'
        with self.assertRaises(module.NativeCondorError):
            self.transition.start_role('controller')
        self.assertEqual(module.read(self.root / 'controller_submission.json')['phase'], 'submitting')
        with self.assertRaisesRegex(module.NativeCondorError, 'no automatic resubmission'):
            self.transition.start_role('controller')
        self.assertEqual(self.commander.call_count, 1)

    def test_idle_start_race_preserves_started_payload(self):
        idle, _ = self.idle()
        running = dict(idle, JobStatus=2, NumJobStarts=1)
        self.reader.side_effect = [[idle], [idle], [running], [running]]
        self.commander.side_effect = module.NativeCondorError('No jobs matched')
        self.assertTrue(self.transition.retire_old())
        self.assertEqual(module.read(self.root / 'old_retirement.json')['remaining_old_jobs'], [running])

    def test_still_idle_payload_is_not_silently_claimed_removed(self):
        idle, _ = self.idle()
        self.reader.side_effect = [[idle], [idle], [idle]]
        self.assertFalse(self.transition.retire_old())
        self.assertFalse((self.root / 'old_retirement.json').exists())
        self.assertTrue((self.root / 'old_retirement_pending.json').exists())

    def test_unknown_removal_without_start_proof_fails(self):
        idle, _ = self.idle()
        self.reader.side_effect = [[idle], [idle], []]
        self.commander.side_effect = module.NativeCondorError('Unknown remove response')
        with self.assertRaises(module.NativeCondorError):
            self.transition.retire_old()
        self.assertFalse((self.root / 'old_retirement.json').exists())

    def test_past_manager_execute_does_not_prove_current_running(self):
        ads = []
        for cluster, role in [(40, 'controller'), (41, 'watchdog')]:
            record = dict(phase='submitted', nonce=role, job=[cluster, 0])
            module.save(self.root / (role + '_submission.json'), record)
            (self.bulk / (role + '.events')).write_text('001 ('+str(cluster)+'.000.000) execution\n...\n')
            ads.append(dict(ClusterId=cluster, ProcId=0, Iwd=str(self.bulk), JobStatus=1,
                NumJobStarts=1, MaxRuntime=604800, ShiftTransitionNonce=role))
        self.reader.return_value = ads
        result = self.transition.execution()
        self.assertFalse(result['complete'])
        self.assertEqual(result['managers'], [])

    def test_recorded_submit_is_not_repeated(self):
        first = self.transition.start_role('controller')
        self.assertEqual(first, self.transition.start_role('controller'))
        self.assertEqual(first['job'], [40, 0])
        self.assertEqual(module.read(self.bulk / 'submission.json')['controller_cluster'], 40)
        self.assertEqual(self.commander.call_count, 1)
        self.assertEqual(self.commander.call_args.kwargs['cwd'], self.bulk)
        self.assertIn('+ShiftTransitionNonce', self.commander.call_args.args[0][-1])

    def test_unrecorded_existing_manager_blocks_submit(self):
        self.reader.return_value = [{'ShiftStreamCampaign': 'bulk',
            'ShiftStreamController': True, 'ShiftStreamWatchdog': False}]
        with self.assertRaisesRegex(module.NativeCondorError, 'already exists'):
            self.transition.start_role('controller')
        self.commander.assert_not_called()

    def test_changed_frozen_manager_submit_is_rejected(self):
        name = str(self.bulk / 'controller.sub')
        self.config['pinned_files'][name] = module.sha(name)
        Path(name).write_text('changed')
        with self.assertRaisesRegex(ValueError, 'input changed'):
            self.transition.step()
        self.commander.assert_not_called()

    def canary_report(self, before=1000000):
        root = Path(self.config['canary_campaign']['directory'])
        report = root / 'result.json'
        module.save(report, {'complete': True, 'events': 20,
            'nano_sha256': 'a'*64, 'nano_readback_sha256': 'a'*64,
            'publication_bytes_before_receipt': before})
        module.save(root / 'tasks/source_reco_000.json', {'result_path': str(report)})
        self.config['canary_publication_budget'] = {'maximum_bytes': 6*1024**2,
            'events_per_job': 20, 'source_ids': ['source']}
        return report

    def test_small_slot_requires_total_publication_including_report_and_margin(self):
        report = self.canary_report()
        result = module.canary_publication_sizes(self.config)
        self.assertEqual(result['measurements'][0]['total_reserved_bytes'],
                         1000000 + report.stat().st_size + 65536)

    def test_real_transition_receipts_satisfy_the_bulk_controller_gate_contract(self):
        from run_shift_stream_controller import operational_gates
        self.canary_report()
        sizes = self.root / 'canary_publication_budget.json'
        module.save(sizes, module.canary_publication_sizes(self.config))
        self.assertTrue(self.transition.old_payloads_drained())
        drain = self.root / 'old_payload_drain.json'
        gates = {'operational_gates': [
            {'path': str(path), 'expected': {'complete': True}} for path in (sizes, drain)]}
        self.assertTrue(operational_gates(gates))
        failed = module.read(drain)
        failed.update(complete=False, passed=False)
        module.save(drain, failed)
        self.assertFalse(operational_gates(gates))

    def test_oversized_real_canary_fails_instead_of_starting_bulk(self):
        self.canary_report(before=6*1024**2-50000)
        with self.assertRaisesRegex(ValueError, 'exceeds the proposed RECO budget'):
            module.canary_publication_sizes(self.config)

    def test_missing_canary_publication_accounting_fails(self):
        report = self.canary_report()
        value = module.read(report)
        del value['publication_bytes_before_receipt']
        module.save(report, value)
        with self.assertRaisesRegex(ValueError, 'complete publication bytes'):
            module.canary_publication_sizes(self.config)

    def test_held_old_payload_blocks_new_sole_ownership(self):
        ad, _ = self.idle(status=5)
        ad['JobUniverse'] = 5
        self.reader.return_value = [ad]
        self.assertFalse(self.transition.old_payloads_drained())
        self.commander.assert_not_called()
        self.assertFalse((self.root / 'old_payload_drain.json').exists())

    def test_untagged_old_quota_payload_also_blocks_until_native_absence(self):
        self.reader.return_value = [dict(ClusterId=50, ProcId=0, JobUniverse=5,
            JobStatus=2, Iwd=str(self.old / 'admission/quota_audits/audit'))]
        self.assertFalse(self.transition.old_payloads_drained())
        self.reader.return_value = []
        self.assertTrue(self.transition.old_payloads_drained())
        self.assertTrue(module.read(self.root / 'old_payload_drain.json')['complete'])

    def test_unknown_queue_cannot_prove_old_payloads_drained(self):
        self.reader.side_effect = module.NativeCondorError('unknown scheduler')
        with self.assertRaises(module.NativeCondorError):
            self.transition.old_payloads_drained()
        self.assertFalse((self.root / 'old_payload_drain.json').exists())

    def test_completed_canary_payload_must_also_leave_native_queue(self):
        directory = self.root / 'completed_canary'
        self.config['additional_retired_campaigns'] = [{'tag': 'canary', 'directory': str(directory), 'managers': []}]
        self.reader.return_value = [dict(ClusterId=77, ProcId=0, JobUniverse=5,
            JobStatus=5, Iwd=str(directory / 'admission/audit'), ShiftStreamCampaign='canary')]
        self.assertFalse(self.transition.old_payloads_drained())
        self.reader.return_value = []
        self.assertTrue(self.transition.old_payloads_drained())


if __name__ == '__main__':
    unittest.main()
