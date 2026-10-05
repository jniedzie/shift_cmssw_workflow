import sys
import time
from pathlib import Path
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from run_shift_production_monitor import scheduler_state, audit_contract, audit_interval, limit_arguments, standby_constraint


class MonitorSafetyTest(unittest.TestCase):
    def setUp(self):
        self.registry = dict(schedd='bigbird26.cern.ch', policy={}, managers=[
            dict(name='low', controller=1, tag='low', dag='/campaign/low.dag'),
            dict(name='high', controller=2, tag='high', dag='/campaign/high.dag', isolate_failures=True)])
        self.ads = [
            dict(ClusterId=1, JobStatus=2, JobUniverse=7, Cmd='/usr/bin/condor_dagman',
                 ShiftProductionController=True, Arguments='-Dag /campaign/low.dag'),
            dict(ClusterId=2, JobStatus=2, JobUniverse=7, Cmd='/usr/bin/condor_dagman',
                 ShiftProductionController=True, Arguments='-Dag high.dag', Iwd='/campaign'),
            dict(ClusterId=3, JobStatus=2, ShiftProductionSuite=True, ShiftSuiteTag='low', DAGManJobId=1),
            dict(ClusterId=4, JobStatus=5, ShiftProductionSuite=True, ShiftSuiteTag='high', DAGManJobId=2)]
        self.now = time.time()
        self.health = dict(account_queue=dict(verified=True, checked_at_epoch=self.now, ads=[]))

    def state(self):
        with patch('run_shift_production_monitor.query', return_value=self.ads):
            return scheduler_state(self.registry, self.health, self.now)

    def test_probe_failure_isolated_from_healthy_production(self):
        result = self.state()
        self.assertEqual(result['held'], 0)
        self.assertEqual(result['isolated_failures'], ['high'])
        self.assertFalse(result['managers'][1]['ready'])
        self.ads[2]['JobStatus'] = 5
        self.assertEqual(self.state()['held'], 1)

    def test_unexpected_worker_parent_rejected(self):
        self.ads[2]['DAGManJobId'] = 100
        # Held workers also have to retain their parent identity.
        with self.assertRaises(ValueError):
            self.state()

    def test_stale_or_foreign_account_coverage_rejected(self):
        self.health['account_queue']['checked_at_epoch'] -= 1600
        with self.assertRaises(ValueError):
            self.state()
        self.health['account_queue']['checked_at_epoch'] = self.now
        self.health['account_queue']['ads'] = [dict(JobStatus=2, _queried_schedd='another.cern.ch')]
        with self.assertRaises(ValueError):
            self.state()

    def test_operational_policy_does_not_invalidate_scientific_audit(self):
        managers = [dict(name='low', manifest_sha256='abc', ceiling=50)]
        original = audit_contract(managers, 'auditor')
        managers[0]['ceiling'] = 300
        self.assertEqual(original, audit_contract(managers, 'auditor'))
        managers[0]['manifest_sha256'] = 'changed'
        self.assertNotEqual(original, audit_contract(managers, 'auditor'))

    def test_service_failures_back_off_but_monitoring_can_recover(self):
        self.assertEqual(audit_interval(0, {}), 600)
        self.assertEqual(audit_interval(1, {}), 1200)
        self.assertEqual(audit_interval(3, {}), 3600)
        self.assertEqual(audit_interval(100, {}), 3600)

    def test_capacity_standby_is_not_a_scientific_failure(self):
        self.ads[1].update(JobStatus=5, ShiftCapacityWait=True, ShiftCapacityMonitor=12794348,
                           HoldReason='SHIFT adaptive capacity standby')
        self.ads[3]['JobStatus'] = 2
        result = self.state()
        self.assertEqual(result['held'], 0)
        self.assertEqual(result['isolated_failures'], [])
        self.assertTrue(result['managers'][1]['capacity_standby'])
        self.assertEqual(result['managers'][1]['active_workers'], 1)
        self.ads[3]['JobStatus'] = 5
        self.assertEqual(self.state()['isolated_failures'], ['high'])

    def test_stale_capacity_marker_does_not_authorize_manual_hold_release(self):
        self.ads[1].update(JobStatus=5, ShiftCapacityWait=True, ShiftCapacityMonitor=12794348,
                           HoldReason='Manual scientific review')
        self.ads[3]['JobStatus'] = 2
        result = self.state()
        self.assertFalse(result['managers'][1]['capacity_standby'])
        self.assertEqual(result['isolated_failures'], ['high'])

    def test_recovery_limits_change_only_numeric_scheduling_arguments(self):
        original = '-f -Dag /private/campaign.dag -MaxIdle 50 -MaxJobs 50 -AutoRescue 1'
        expected = '-f -Dag /private/campaign.dag -MaxIdle 50 -MaxJobs 93 -AutoRescue 1'
        self.assertEqual(limit_arguments(original, 93), expected)
        with self.assertRaises(ValueError):
            limit_arguments('-Dag campaign.dag -MaxJobs 50', 93)

    def test_high_readback_failure_holds_only_high_admissions(self):
        self.ads[3]['JobStatus'] = 2
        self.health['manager_failures'] = {'high': 'Published ledger changed'}
        result = self.state()
        self.assertEqual(result['isolated_failures'], ['high'])
        self.assertEqual(result['held'], 0)
        self.assertEqual(result['isolated_active_workers'], 1)

    def test_audit_helper_changes_invalidate_cached_health(self):
        self.assertNotEqual(audit_contract([], 'same', {'weighted.py': 'old'}),
                            audit_contract([], 'same', {'weighted.py': 'new'}))

    def test_release_is_atomically_limited_to_owned_capacity_hold(self):
        constraint = standby_constraint(12, 34)
        for part in ('ClusterId == 12', 'ProcId == 0', 'JobStatus == 5',
                     'ShiftCapacityWait == true', 'ShiftCapacityMonitor == 34',
                     'HoldReason == "SHIFT adaptive capacity standby"'):
            self.assertIn(part, constraint)


if __name__ == '__main__':
    unittest.main()
