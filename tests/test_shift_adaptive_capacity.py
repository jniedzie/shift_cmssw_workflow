import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from shift_adaptive_capacity import decide, allocations, safe_limits, storage_reservation


class CapacityTest(unittest.TestCase):
    def setUp(self):
        self.previous = dict(target=50, changed_at=0, baseline_completed=100)
        self.health = dict(healthy=True, checked_at_epoch=1300, completed_chunks=120,
                           quota=dict(free_bytes=200000000000, free_files=100000))
        self.queue = dict(verified=True, running=50, idle=0, held=0, failed_nodes=0)

    def test_success_grows_one_tier_only(self):
        self.assertEqual(decide(self.previous, self.health, self.queue, 1300, {})['target'], 75)

    def test_unknown_or_stale_services_do_not_increase_capacity(self):
        self.queue['verified'] = False
        self.assertEqual(decide(self.previous, self.health, self.queue, 1300, {})['target'], 50)
        self.queue['verified'] = True
        self.assertEqual(decide(self.previous, self.health, self.queue, 4000, {})['target'], 50)

    def test_idle_queue_waits_for_allocation(self):
        self.queue['idle'] = 30
        self.assertEqual(decide(self.previous, self.health, self.queue, 1300, {})['target'], 50)

    def test_failure_or_quota_stops_new_admissions(self):
        self.queue['held'] = 1
        self.assertTrue(decide(self.previous, self.health, self.queue, 1300, {})['stop_admissions'])
        self.queue['held'] = 0
        self.health['quota']['free_bytes'] = 100000000000
        self.assertTrue(decide(self.previous, self.health, self.queue, 1300, {})['stop_admissions'])

    def test_failed_audit_with_verified_failure_stops(self):
        self.health.update(healthy=False, failed_receipts=['broken.json'])
        self.assertTrue(decide(self.previous, self.health, self.queue, 1300, {})['stop_admissions'])

    def test_insufficient_running_allocation_does_not_grow(self):
        self.queue.update(running=10, idle=10)
        self.assertEqual(decide(self.previous, self.health, self.queue, 1300, {})['target'], 50)

    def test_confirmed_integrity_failure_stops_but_service_error_freezes(self):
        self.health.update(healthy=False, error='XRootD unavailable')
        self.assertFalse(decide(self.previous, self.health, self.queue, 1300, {})['stop_admissions'])
        self.health['integrity_failure'] = True
        self.assertTrue(decide(self.previous, self.health, self.queue, 1300, {})['stop_admissions'])

    def test_dual_managers_share_budget(self):
        managers = [dict(name='low', active=True, weight=2, ceiling=300),
                    dict(name='high', active=True, weight=1, ceiling=20)]
        assigned = allocations(150, managers)
        self.assertEqual(assigned['high'], 20)
        self.assertEqual(sum(assigned.values()), 150)
        managers[1]['ready'] = False
        self.assertEqual(allocations(150, managers), {'low': 150})
        managers[1]['ready'] = True
        with self.assertRaises(ValueError):
            allocations(1, managers)

    def test_new_manager_only_gets_headroom_after_existing_jobs(self):
        managers = [dict(name='low', active_workers=98), dict(name='high', active_workers=0)]
        desired = dict(low=67, high=33)
        limits = safe_limits(100, desired, managers)
        self.assertEqual(limits, dict(low=67, high=2))
        self.assertEqual(sum(max(m['active_workers'], limits[m['name']] or 0) for m in managers), 100)
        managers[0]['active_workers'] = 100
        self.assertEqual(safe_limits(100, desired, managers), dict(low=67, high=None))
        managers[0]['active_workers'] = 70
        self.assertEqual(safe_limits(100, desired, managers), dict(low=67, high=30))

    def test_existing_overshoot_drains_without_replenishment(self):
        managers = [dict(name='low', active_workers=98), dict(name='high', active_workers=8)]
        self.assertEqual(safe_limits(100, dict(low=67, high=33), managers), dict(low=67, high=8))

    def test_weighted_worker_storage_size_used_at_full_ceiling(self):
        managers = [dict(name='low', ceiling=300, weight=2, peak_bytes_per_worker=250000000),
                    dict(name='high', ceiling=80, weight=1, peak_bytes_per_worker=1000000000)]
        self.assertEqual(storage_reservation({}, managers), 185000000000)


if __name__ == '__main__':
    unittest.main()
