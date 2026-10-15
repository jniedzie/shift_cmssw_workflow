# Code style: AI-code. AI-generated admission safety regression tests.
import importlib.util
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

    def test_expired_pre_reservation_does_not_block_forever(self):
        result = self.decide(reservations={'expired': {'at_epoch': 99}})
        self.assertTrue(result['admitted'])
        self.assertNotIn('expired', result['reservations'])

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

    def test_account_limit_above_1000_is_rejected(self):
        self.policy['account_ceiling'] = 1001
        with self.assertRaises(ValueError):
            module.validate_policy(self.policy)

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


if __name__ == '__main__':
    unittest.main()
