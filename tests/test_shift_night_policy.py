# Code style: AI-code. AI-generated regressions for real night scheduling failure modes.
import sys
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from shift_night_policy import admission_valid, capacity_decision, night_window, observations_safe, validate_policy


def policy():
    return dict(timezone='Europe/Zurich', window_start='22:00', window_end='06:00',
                stop_launches_before_end_seconds=7200, worker_ceiling=1000, initial_workers=50,
                lease_seconds=180, poll_seconds=60, observation_max_age_seconds=600,
                growth_interval_seconds=1200, growth_minimum_completions=10,
                growth_max_idle_fraction=.25, minimum_free_bytes=10000, minimum_free_files=100,
                reserved_bytes_per_worker=1000, reserved_files_per_worker=10, maximum_wave_jobs=100)


def epoch(text):
    return datetime.fromisoformat(text).replace(tzinfo=ZoneInfo('Europe/Zurich')).timestamp()


class NightPolicyTests(unittest.TestCase):
    def test_evening_and_after_midnight_belong_to_same_night(self):
        evening = night_window(epoch('2026-10-14T23:00:00'), policy())
        morning = night_window(epoch('2026-10-15T02:00:00'), policy())
        self.assertEqual(evening['local_date'], morning['local_date'])
        self.assertTrue(morning['admissions_allowed'])

    def test_stops_at_four_without_claiming_running_jobs_are_finished(self):
        before = night_window(epoch('2026-10-15T03:59:59'), policy())
        stop = night_window(epoch('2026-10-15T04:00:00'), policy())
        self.assertTrue(before['admissions_allowed'])
        self.assertFalse(stop['admissions_allowed'])
        self.assertTrue(stop['inside_night'])
        self.assertEqual(stop['next_admission_epoch'], epoch('2026-10-15T22:00:00'))

    def test_daytime_returns_the_next_admission(self):
        result = night_window(epoch('2026-10-15T12:00:00'), policy())
        self.assertFalse(result['inside_night'])
        self.assertEqual(result['start_epoch'], epoch('2026-10-15T22:00:00'))

    def test_autumn_night_is_nine_hours(self):
        result = night_window(epoch('2026-10-25T01:00:00'), policy())
        self.assertEqual(result['duration_seconds'], 9 * 3600)
        self.assertEqual(result['stop_epoch'], epoch('2026-10-25T04:00:00'))

    def test_spring_night_is_seven_hours(self):
        result = night_window(epoch('2027-03-28T01:00:00'), policy())
        self.assertEqual(result['duration_seconds'], 7 * 3600)
        self.assertEqual(result['stop_epoch'], epoch('2027-03-28T04:00:00'))

    def test_nonexistent_user_window_is_rejected(self):
        settings = policy()
        settings['window_end'] = '02:30'
        settings['stop_launches_before_end_seconds'] = 0
        with self.assertRaisesRegex(ValueError, 'does not exist'):
            night_window(epoch('2027-03-28T01:00:00'), settings)

    def test_short_window_cannot_have_two_hour_stop_margin(self):
        settings = policy()
        settings.update(window_start='01:00', window_end='02:00')
        with self.assertRaisesRegex(ValueError, 'no usable'):
            night_window(epoch('2026-10-15T01:30:00'), settings)

    def test_expired_controller_lease_refuses_start_inside_night(self):
        ad = dict(ShiftNightAdmissionStartEpoch=100, ShiftNightAdmissionStopEpoch=1000,
                  ShiftNightAdmissionLeaseEpoch=200)
        self.assertTrue(admission_valid(ad, 199))
        self.assertFalse(admission_valid(ad, 200))
        self.assertFalse(admission_valid(ad, 99))
        self.assertFalse(admission_valid({**ad, 'ShiftNightAdmissionLeaseEpoch': None}, 150))

    def test_other_campaigns_consume_the_same_capacity(self):
        settings = {**policy(), 'worker_ceiling': 50, 'maximum_wave_jobs': 50}
        result = capacity_decision({}, settings, now=100, completed_jobs=0,
                                   own_ads=[{'JobStatus': 2}] * 20, external_workers=25,
                                   observations_verified=True, healthy=True)
        self.assertEqual(result['available_new_slots'], 5)

    def test_initial_night_ramp_can_start_beside_larger_existing_campaign(self):
        result = capacity_decision({}, policy(), now=100, completed_jobs=0,
                                   own_ads=[], external_workers=193,
                                   observations_verified=True, healthy=True)
        self.assertEqual(result['available_new_slots'], 50)

    def test_new_reservations_never_exceed_wave_bound(self):
        result = capacity_decision({'current_capacity': 1000}, policy(), now=100,
                                   completed_jobs=0, own_ads=[], external_workers=0,
                                   observations_verified=True, healthy=True)
        self.assertEqual(result['available_new_slots'], 100)

    def test_unknown_observation_does_not_admit_or_grow(self):
        result = capacity_decision({'last_capacity_change_epoch': 0, 'completed_at_change': 0},
                                   policy(), now=2000, completed_jobs=100, own_ads=[],
                                   external_workers=0, observations_verified=False, healthy=True)
        self.assertEqual(result['current_capacity'], 50)
        self.assertEqual(result['available_new_slots'], 0)

    def test_current_quota_budget_limits_admissions_without_reserving_future_thousand_workers(self):
        result = capacity_decision({'current_capacity': 1000}, policy(), now=100,
                                   completed_jobs=0, own_ads=[{'JobStatus': 2}] * 35,
                                   external_workers=193, quota_worker_limit=60,
                                   observations_verified=True, healthy=True)
        self.assertEqual(result['available_new_slots'], 25)
        self.assertEqual(result['quota_worker_limit'], 60)

    def test_exhausted_current_quota_never_admits_or_kills_live_workers(self):
        result = capacity_decision({'current_capacity': 1000}, policy(), now=100,
                                   completed_jobs=0, own_ads=[{'JobStatus': 2}] * 50,
                                   external_workers=0, quota_worker_limit=0,
                                   observations_verified=True, healthy=True)
        self.assertEqual(result['available_new_slots'], 0)
        self.assertEqual(result['own_live_workers'], 50)

    def test_actual_completions_and_elapsed_time_allow_one_step(self):
        result = capacity_decision({'last_capacity_change_epoch': 0, 'completed_at_change': 0},
                                   policy(), now=2000, completed_jobs=10,
                                   own_ads=[{'JobStatus': 2}] * 50, external_workers=0,
                                   observations_verified=True, healthy=True)
        self.assertEqual(result['current_capacity'], 100)
        self.assertEqual(result['available_new_slots'], 50)

    def test_failures_freeze_admissions_without_shrinking_live_workers(self):
        result = capacity_decision({'current_capacity': 400}, policy(), now=2000,
                                   completed_jobs=100, own_ads=[{'JobStatus': 2}] * 400,
                                   external_workers=0, observations_verified=True, healthy=False)
        self.assertEqual(result['current_capacity'], 400)
        self.assertEqual(result['available_new_slots'], 0)

    def test_quota_reserves_all_authorized_outputs(self):
        observation = dict(complete=True, account_coverage_verified=True,
                           quota_checked_at_epoch=100, account_checked_at_epoch=100,
                           free_bytes=100999, free_files=1100, account_workers=[])
        self.assertFalse(observations_safe(observation, policy(), 150, reserved_workers=100)[0])
        observation['free_bytes'] = 110000
        self.assertTrue(observations_safe(observation, policy(), 150, reserved_workers=100)[0])
        observation['account_checked_at_epoch'] = -500
        self.assertFalse(observations_safe(observation, policy(), 150, reserved_workers=100)[0])

    def test_invalid_capacity_or_lease_is_rejected(self):
        for change in ({'worker_ceiling': 1001}, {'lease_seconds': 10}, {'window_start': '25:00'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_policy({**policy(), **change})
        self.assertEqual(validate_policy(policy()), policy())


if __name__ == '__main__':
    unittest.main()
