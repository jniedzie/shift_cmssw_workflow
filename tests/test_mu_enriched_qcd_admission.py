# Code style: AI-code. AI-generated admission safety regression tests.
import importlib.util
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('mu_admission', SCRIPTS / 'run_mu_enriched_qcd_admission.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.policy = dict(worker_ceiling=200, account_ceiling=1000,
            minimum_free_bytes=50, minimum_free_files=20, minimum_AFS_free_bytes=10,
            reserved_bytes_per_worker=10, reserved_files_per_worker=2,
            observation_max_age_seconds=600, pre_reservation_seconds=900,
            account_owner='jniedzie', account_pool='pool', schedd_name='local', suite_tag='mu_qcd')
        self.observation = dict(complete=True, quota_checked_at_epoch=1000,
            quota_observation_started_at_epoch=999,
            remote_account_checked_at_epoch=1000, remote_account_coverage_verified=True,
            excluded_local_schedd='local', account_schedds=['local', 'remote'],
            remote_account_workers=[], free_bytes=10000, free_files=10000)

    def decide(self, ads=None, reservations=None, node='one', now=1000, afs=100):
        return module.decision(self.policy, self.observation, ads or [],
                               reservations or {}, node, now, afs)

    def own(self, node):
        return dict(JobUniverse=5, JobStatus=1, ShiftMuEnrichedQCD=True,
                    ShiftSuiteTag='mu_qcd', ShiftMuEnrichedNodeId=node)

    def test_fresh_observations_admit_and_reserve(self):
        result = self.decide()
        self.assertTrue(result['admitted'])
        self.assertEqual(result['reservations'], {'one': {'at_epoch': 1000}})

    def test_old_or_future_observation_defers(self):
        for value in [399, 1001, float('nan'), None]:
            with self.subTest(value=value):
                self.observation['quota_checked_at_epoch'] = value
                self.assertFalse(self.decide()['admitted'])

    def test_partial_or_wrong_scheduler_coverage_defers(self):
        for key, value in [('complete', False), ('remote_account_coverage_verified', False),
                           ('excluded_local_schedd', 'other'), ('account_schedds', ['remote']),
                           ('remote_account_workers', None)]:
            with self.subTest(key=key):
                saved = self.observation[key]
                self.observation[key] = value
                self.assertFalse(self.decide()['admitted'])
                self.observation[key] = saved

    def test_other_production_growth_reserves_the_full_wave(self):
        self.observation['remote_account_workers'] = [{}] * 801
        self.assertFalse(self.decide()['admitted'])
        self.observation['remote_account_workers'] = [{}] * 800
        self.assertTrue(self.decide()['admitted'])

    def stream_policy(self):
        self.policy.update(worker_ceiling=900, account_ceiling=1607,
                           reserve_full_worker_ceiling=False)

    def test_stream_admits_one_with_790_external_workers_and_900_own_limit(self):
        self.stream_policy()
        self.observation['remote_account_workers'] = [{'JobStatus': 2}]*790
        result = self.decide()
        self.assertTrue(result['admitted'], result)
        self.assertEqual(result['account_reserved_workers'], 791)
        self.assertEqual(result['external_capacity_workers'], 790)
        self.policy['reserve_full_worker_ceiling'] = True
        result = self.decide()
        self.assertFalse(result['admitted'])
        self.assertEqual(result['account_reserved_workers'], 1690)

    def test_stream_stops_one_extra_at_the_actual_account_capacity(self):
        self.stream_policy()
        self.observation['remote_account_workers'] = [{'JobStatus': 2}]*790
        own = [self.own('own_'+str(index)) for index in range(816)]
        self.assertTrue(self.decide(ads=own)['admitted'])
        own.append(self.own('own_816'))
        result = self.decide(ads=own)
        self.assertFalse(result['admitted'])
        self.assertEqual(result['account_reserved_workers'], 1608)
        self.assertIn('insufficient worker capacity', result['reason'])

    def test_external_idle_queue_does_not_consume_the_stream_running_budget(self):
        self.stream_policy()
        self.observation['remote_account_workers'] = [{'JobStatus': 2}]*500 + [{'JobStatus': 1}]*1000
        result = self.decide(ads=[dict(JobUniverse=5, JobStatus=1)]*1000)
        self.assertTrue(result['admitted'], result)
        self.assertEqual(result['external_queued_workers'], 2500)
        self.assertEqual(result['external_idle_workers'], 2000)
        self.assertEqual(result['external_capacity_workers'], 500)
        self.observation['remote_account_workers'] = [{'JobStatus': 2}]*1607
        self.assertFalse(self.decide()['admitted'])

    def test_stream_counts_running_transferring_and_suspended_external_workers(self):
        self.stream_policy()
        self.observation['remote_account_workers'] = [{'JobStatus': s} for s in (1, 2, 6, 7)]
        ads = [dict(JobUniverse=5, JobStatus=s) for s in (1, 2, 6, 7)]
        result = self.decide(ads=ads)
        self.assertTrue(result['admitted'])
        self.assertEqual(result['external_capacity_workers'], 6)
        self.assertEqual(result['external_idle_workers'], 2)

    def test_stream_unknown_remote_execution_state_defers_instead_of_counting_zero(self):
        self.stream_policy()
        for ad in ({}, {'JobStatus': None}, {'JobStatus': '2'},
                   {'JobStatus': 5}, {'JobStatus': 8}, {'JobStatus': True}, None):
            with self.subTest(ad=ad):
                self.observation['remote_account_workers'] = [ad]
                result = self.decide()
                self.assertFalse(result['admitted'])
                self.assertIn('execution capacity is unknown', result['reason'])

    def test_stream_own_held_and_pending_claims_still_keep_the_900_limit(self):
        self.stream_policy()
        held = [self.own('held_'+str(index)) | {'JobStatus': 5} for index in range(899)]
        pending = {'pending': {'at_epoch': 999}}
        result = self.decide(ads=held, reservations=pending)
        self.assertFalse(result['admitted'])
        self.assertEqual(result['reserved_workers'], 901)
        self.assertIn('worker ceiling', result['reason'])
        # Rechecking the same PRE does not create a second reservation.
        result = self.decide(ads=held, reservations=pending, node='pending')
        self.assertTrue(result['admitted'])
        self.assertEqual(result['reserved_workers'], 900)
        self.assertEqual(result['storage_reserved_workers'], 900)
        # A queued node and its persistent PRE still count only once.
        result = self.decide(ads=[self.own('pending')], reservations=pending, node='pending')
        self.assertEqual(result['reserved_workers'], 1)

    def test_stream_does_not_release_completed_storage_claims_or_bypass_coverage(self):
        self.stream_policy()
        previous = {'finished': {'at_epoch': 900, 'seen_queued': True}}
        self.observation['free_bytes'] = 69
        result = self.decide(reservations=previous)
        self.assertFalse(result['admitted'])
        self.assertEqual(result['reserved_workers'], 1)
        self.assertEqual(result['storage_reserved_workers'], 2)
        self.assertIn('EOS byte reservation', result['reason'])
        self.observation.update(free_bytes=10000, remote_account_coverage_verified=False)
        self.assertIn('coverage is unknown', self.decide()['reason'])

    def test_pending_reservations_are_validated_before_the_full_wave_capacity_check(self):
        self.observation['remote_account_workers'] = [{}]*801
        with self.assertRaisesRegex(ValueError, 'persistent PRE reservation'):
            self.decide(reservations={'bad': {'at_epoch': 'unknown'}})

    def test_full_wave_policy_option_requires_a_boolean(self):
        for invalid in (None, 0, 1, 'false'):
            with self.subTest(invalid=invalid):
                self.policy['reserve_full_worker_ceiling'] = invalid
                with self.assertRaisesRegex(ValueError, 'explicit boolean'):
                    module.validate_policy(self.policy)
                with self.assertRaisesRegex(ValueError, 'explicit boolean'):
                    self.decide()

    def test_local_other_jobs_count_towards_global_limit(self):
        ads = [dict(JobUniverse=5, JobStatus=2)] * 801
        self.assertFalse(self.decide(ads=ads)['admitted'])

    def test_held_jobs_do_not_consume_execution_capacity(self):
        ads = [dict(JobUniverse=5, JobStatus=5)] * 1001
        self.assertTrue(self.decide(ads=ads)['admitted'])

    def test_pending_pre_and_queued_worker_are_counted_once(self):
        result = self.decide(ads=[self.own('one')], reservations={'one': {'at_epoch': 999}})
        self.assertTrue(result['admitted'])
        self.assertEqual(result['reserved_workers'], 1)

    def test_pending_pre_reserves_space_before_queue_submission(self):
        self.observation['free_bytes'] = 69
        result = self.decide(reservations={'pending': {'at_epoch': 999}})
        self.assertFalse(result['admitted'])
        self.assertEqual(result['reserved_workers'], 2)

    def test_expired_pre_reservation_waits_for_a_new_quota_measurement(self):
        result = self.decide(reservations={'expired': {'at_epoch': 99}})
        self.assertTrue(result['admitted'])
        self.assertIn('expired', result['reservations'])
        self.assertEqual(result['storage_reserved_workers'], 2)
        self.observation.update(quota_observation_started_at_epoch=1001,
                                quota_checked_at_epoch=1002,
                                remote_account_checked_at_epoch=1002)
        result = self.decide(reservations=result['reservations'], now=1003)
        self.assertNotIn('expired', result['reservations'])

    def test_completed_publication_keeps_bytes_and_files_until_quota_remeasured(self):
        previous = {'finished': {'at_epoch': 900, 'seen_queued': True, 'last_seen_at_epoch': 998}}
        self.observation['free_bytes'] = 69
        result = self.decide(reservations=previous)
        self.assertFalse(result['admitted'])
        self.assertEqual(result['storage_reserved_workers'], 2)
        self.assertEqual(result['reservations']['finished']['missing_since_epoch'], 1000)
        self.observation['free_bytes'] = 10000
        self.observation['free_files'] = 23
        result = self.decide(reservations=previous)
        self.assertFalse(result['admitted'])
        self.assertIn('file reservation', result['reason'])

    def test_quota_reply_after_completion_does_not_release_inflight_measurement(self):
        previous = {'finished': {'at_epoch': 900, 'seen_queued': True,
                                'missing_since_epoch': 998}}
        self.observation.update(quota_observation_started_at_epoch=997,
                                quota_checked_at_epoch=1000)
        result = self.decide(reservations=previous)
        self.assertIn('finished', result['reservations'])
        self.assertEqual(result['storage_reserved_workers'], 2)

    def test_observation_started_after_missing_job_releases_its_storage_claim(self):
        previous = {'finished': {'at_epoch': 900, 'seen_queued': True,
                                'missing_since_epoch': 998}}
        result = self.decide(reservations=previous)
        self.assertNotIn('finished', result['reservations'])
        self.assertEqual(result['storage_reserved_workers'], 1)

    def test_eos_file_and_afs_reserves_are_enforced(self):
        self.observation['free_files'] = 21
        self.assertFalse(self.decide()['admitted'])
        self.observation['free_files'] = 10000
        self.assertFalse(self.decide(afs=9)['admitted'])

    def test_own_worker_cap_is_enforced(self):
        ads = [self.own('node' + str(index)) for index in range(200)]
        self.assertFalse(self.decide(ads=ads)['admitted'])

    def test_unknown_byte_headroom_is_not_zero_or_unlimited(self):
        self.observation['free_bytes'] = None
        self.assertFalse(self.decide()['admitted'])

    def test_account_limit_above_10000_is_rejected(self):
        self.policy['account_ceiling'] = 10001
        with self.assertRaises(ValueError):
            module.validate_policy(self.policy)

    def test_explicit_4000_account_capacity_keeps_900_stream_slots_with_2400_external_jobs(self):
        self.policy.update(worker_ceiling=900, account_ceiling=4000)
        module.validate_policy(self.policy)
        self.observation['remote_account_workers'] = [{'ClusterId': index} for index in range(2400)]
        self.assertTrue(self.decide()['admitted'])
        self.observation['remote_account_workers'].extend({'ClusterId': index} for index in range(2400, 3100))
        self.assertTrue(self.decide()['admitted'])
        self.observation['remote_account_workers'].append({'ClusterId': 3100})
        rejected = self.decide()
        self.assertFalse(rejected['admitted'])
        self.assertIn('insufficient worker capacity', rejected['reason'])

    def test_larger_account_capacity_does_not_bypass_storage_or_unknown_account_coverage(self):
        self.policy.update(worker_ceiling=900, account_ceiling=4000, minimum_free_bytes=6000)
        self.observation['remote_account_workers'] = [{'ClusterId': index} for index in range(2400)]
        self.observation['free_bytes'] = 6000 + self.policy['reserved_bytes_per_worker'] - 1
        rejected = self.decide()
        self.assertFalse(rejected['admitted'])
        self.assertIn('EOS byte reservation', rejected['reason'])
        self.observation.update(free_bytes=10000, remote_account_coverage_verified=False)
        rejected = self.decide()
        self.assertFalse(rejected['admitted'])
        self.assertIn('coverage is unknown', rejected['reason'])

    def test_reviewed_10000_account_limit_covers_all_6200_peer_writers_and_still_stops_at_capacity(self):
        self.policy.update(worker_ceiling=900, account_ceiling=10000)
        module.validate_policy(self.policy)
        self.observation['remote_account_workers'] = [{}]*6200
        self.assertTrue(self.decide()['admitted'])
        self.observation['remote_account_workers'] = [{}]*9101
        self.assertFalse(self.decide()['admitted'])

    def test_global_publication_cap_covers_histogram_extra_pending_and_completed_claims(self):
        self.policy.update(worker_ceiling=900, account_ceiling=10000,
            reserved_bytes_per_worker=6*1024**2, maximum_outstanding_publication_bytes=6144000000)
        self.observation.update(free_bytes=10**12, free_files=100000,
                                extra_stage_reserved_bytes=(512-6)*1024**2)
        workers = [self.own('reco_'+str(index)) for index in range(899)]
        self.assertIn('byte cap', self.decide(ads=workers, node='histogram')['reason'])
        result = self.decide(ads=workers[:891], node='histogram')
        self.assertTrue(result['admitted'])
        self.assertEqual(result['outstanding_publication_bytes'], 892*6*1024**2+(512-6)*1024**2)
        # A finished worker absent from the old quota still occupies one slot.
        completed = {'finished': {'at_epoch': 900, 'seen_queued': True, 'missing_since_epoch': 1000}}
        self.assertIn('byte cap', self.decide(ads=workers[:891], reservations=completed, node='histogram')['reason'])

    def test_held_own_source_keeps_its_complete_stage_claim_after_a_new_quota(self):
        self.policy.update(reserved_bytes_per_worker=6*1024**2, maximum_outstanding_publication_bytes=6144000000)
        self.observation.update(free_bytes=10**12, extra_stage_reserved_bytes=(512-6)*1024**2)
        held = self.own('held_histogram') | {'JobStatus': 5}
        result = self.decide(ads=[held], node='held_histogram',
            reservations={'held_histogram': {'at_epoch': 1, 'seen_queued': True, 'missing_since_epoch': 2}})
        self.assertTrue(result['admitted'])
        self.assertEqual(result['outstanding_publication_bytes'], 512*1024**2)
        self.assertEqual(result['reserved_workers'], 1)
        self.assertIn('held_histogram', result['reservations'])
        self.observation['extra_stage_reserved_bytes'] = 0
        result = self.decide(ads=[held], node='held_histogram', reservations=result['reservations'])
        self.assertEqual(result['outstanding_publication_bytes'], 6*1024**2)

    def test_conditional_peer_mode_keeps_full_claims_and_needs_verified_writers_and_byte_cap(self):
        self.external_commitments(required_bytes=300, required_files=30)
        self.policy['maximum_outstanding_publication_bytes'] = 60
        self.observation.update(free_bytes=260, stream_peer_writers_verified=True)
        self.observation['external_storage_commitments']['stream_capacity'] = dict(
            constrained_required_free_bytes=240, protected_stream_bytes=60)
        result = self.decide()
        self.assertTrue(result['admitted'])
        self.assertEqual(result['storage_mode'], 'constrained_within_peer_QCD_allowance')
        self.assertEqual(result['full_peer_required_free_bytes'], 300)
        self.assertEqual(result['required_free_bytes'], 240)
        self.observation['free_bytes'] = 320
        self.assertEqual(self.decide()['storage_mode'], 'full')
        for free in (260, 320):
            self.observation.update(free_bytes=free, stream_peer_writers_verified=False)
            self.assertFalse(self.decide()['admitted'])
        self.observation.update(free_bytes=260, stream_peer_writers_verified=True)
        self.policy['maximum_outstanding_publication_bytes'] = 61
        self.assertFalse(self.decide()['admitted'])

    def external_commitments(self, required_bytes=100, required_files=30):
        self.policy['external_storage_reservations'] = [{'production_directory': '/sealed/other'}]
        self.observation['external_storage_commitments'] = dict(complete=True,
            checked_at_epoch=998, required_free_bytes=required_bytes, required_free_files=required_files,
            authority_contracts_sha256=hashlib.sha256(json.dumps(
                self.policy['external_storage_reservations'], sort_keys=True).encode()).hexdigest())

    def test_external_commitments_keep_bytes_and_files_reserved(self):
        self.external_commitments()
        self.observation['free_bytes'] = 109
        self.assertFalse(self.decide()['admitted'])
        self.observation['free_bytes'] = 1000
        self.observation['free_files'] = 31
        self.assertFalse(self.decide()['admitted'])

    def test_shared_safety_floors_are_not_added_twice(self):
        self.external_commitments()
        self.observation.update(free_bytes=110, free_files=32)
        result = self.decide()
        self.assertTrue(result['admitted'])
        self.assertEqual(result['required_free_bytes'], 100)
        self.assertEqual(result['required_free_files'], 30)

    def test_unknown_external_commitment_defers(self):
        self.policy['external_storage_reservations'] = [{'production_directory': '/sealed/other'}]
        self.assertFalse(self.decide()['admitted'])

    def test_external_credit_measured_after_old_eos_snapshot_is_refused(self):
        self.external_commitments()
        self.observation['external_storage_commitments']['checked_at_epoch'] = 1000
        self.assertFalse(self.decide()['admitted'])

    def test_different_external_authority_cannot_authorize_admission(self):
        self.external_commitments()
        self.observation['external_storage_commitments']['authority_contracts_sha256'] = '0' * 64
        self.assertFalse(self.decide()['admitted'])

    def admission_directory(self, directory):
        root = Path(directory)
        (root / 'admission').mkdir()
        (root / 'admission' / 'policy.json').write_text(json.dumps(self.policy))
        return root

    def test_detached_integration_uses_only_local_scheduler_address(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.admission_directory(directory)
            with mock.patch.object(module, 'refresh_quota', return_value=self.observation), \
                    mock.patch.object(module, 'query', return_value=[]) as reader, \
                    mock.patch.object(module, 'afs_headroom', return_value=100), \
                    mock.patch.object(module.time, 'time', return_value=1000):
                self.assertEqual(module.admit(root, 'one'), 0)
            options = reader.call_args.kwargs
            self.assertTrue(options['local_schedd'])
            self.assertNotIn('schedd', options)
            self.assertNotIn('pool', options)
            self.assertIn('Owner == "jniedzie"', reader.call_args.args[0])
            saved = json.loads((root / 'admission' / 'pre_reservations.json').read_text())
            self.assertEqual(saved, {'one': {'at_epoch': 1000}})

    def test_failed_audit_never_launches_a_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.admission_directory(directory)
            with mock.patch.object(module, 'refresh_quota', side_effect=RuntimeError('unknown account')):
                self.assertEqual(module.admit(root, 'one'), 75)
            result = json.loads((root / 'admission' / 'heartbeat.json').read_text())
            self.assertFalse(result['admitted'])
            self.assertIn('unknown account', result['error'])
            self.assertFalse((root / 'admission' / 'pre_reservations.json').exists())

    def test_stop_marker_prevents_even_new_auditor_submission(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.admission_directory(directory)
            (root / 'stop_requested.json').write_text('{}')
            with mock.patch.object(module, 'refresh_quota') as audit:
                self.assertEqual(module.admit(root, 'one'), 75)
                audit.assert_not_called()

    def test_duplicate_pre_poll_keeps_one_reservation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.admission_directory(directory)
            with mock.patch.object(module, 'refresh_quota', return_value=self.observation), \
                    mock.patch.object(module, 'query', return_value=[]), \
                    mock.patch.object(module, 'afs_headroom', return_value=100), \
                    mock.patch.object(module.time, 'time', return_value=1000):
                self.assertEqual(module.admit(root, 'one'), 0)
                self.assertEqual(module.admit(root, 'one'), 0)
            state = json.loads((root / 'admission' / 'pre_reservations.json').read_text())
            self.assertEqual(len(state), 1)

    def test_busy_pre_lock_has_a_bounded_wait_without_submitting_an_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.admission_directory(directory)
            with mock.patch.object(module.fcntl, 'flock', side_effect=BlockingIOError), \
                    mock.patch.object(module.time, 'monotonic', side_effect=[0, 0, 6]), \
                    mock.patch.object(module.time, 'sleep') as sleep, \
                    mock.patch.object(module, 'refresh_quota') as auditor:
                self.assertEqual(module.admit(root, 'one'), 75)
                auditor.assert_not_called()
                sleep.assert_called_once_with(0.05)


if __name__ == '__main__':
    unittest.main()
