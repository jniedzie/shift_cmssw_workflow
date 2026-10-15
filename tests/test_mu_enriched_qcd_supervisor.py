# Code style: AI-code. AI-generated detached GEN supervision regression tests.
import importlib.util
from pathlib import Path
import sys
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('mu_supervisor', SCRIPTS / 'watch_mu_enriched_qcd_production.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.policy = dict(manager_cluster=123, campaign_directory='/persistent/gen',
            required_manager_runtime_seconds=604800, suite_tag='muqcd', no_child_timeout_seconds=900)
        self.manager = dict(ClusterId=123, ProcId=0, JobStatus=2, Iwd='/persistent/gen', MaxRuntime=604800)

    def assess(self, ads=None, receipts=None, docs=None, previous=None, now=1000):
        return module.assess(self.policy, [self.manager] if ads is None else ads,
                             receipts or {}, docs or {}, previous or {}, now)

    def test_missing_manager_is_not_empty_success(self):
        result = self.assess(ads=[])
        self.assertTrue(result['attention_required'])
        self.assertIn('manager is missing', result['problems'][0])

    def test_held_manager_requires_attention(self):
        self.manager['JobStatus'] = 5
        self.assertTrue(self.assess()['attention_required'])

    def test_bad_working_directory_and_runtime_are_detected(self):
        self.manager.update(Iwd='/pool/condor/dir_42', MaxRuntime=86400)
        result = self.assess()
        self.assertEqual(len(result['problems']), 2)

    def test_startup_without_children_has_a_bounded_grace_period(self):
        first = self.assess()
        self.assertFalse(first['attention_required'])
        late = self.assess(previous=first, now=1901)
        self.assertTrue(late['attention_required'])

    def test_held_payload_is_never_released_or_retried(self):
        worker = dict(ClusterId=124, ProcId=0, JobStatus=5,
                      ShiftMuEnrichedQCD=True, ShiftSuiteTag='muqcd', DAGManJobId=123)
        result = self.assess(ads=[self.manager, worker])
        self.assertEqual(result['held_job_ids'], ['124.0'])
        self.assertIn('read-only', result['worker_action'])

    def test_predecessor_workers_cannot_hide_a_stalled_replacement(self):
        first = self.assess()
        predecessor = dict(ClusterId=122, ProcId=0, JobStatus=2,
                           ShiftMuEnrichedQCD=True, ShiftSuiteTag='muqcd', DAGManJobId=120)
        result = self.assess(ads=[self.manager, predecessor], previous=first, now=1901)
        self.assertEqual(result['live_payloads'], 0)
        self.assertTrue(result['attention_required'])
        self.assertTrue(any('no active payload workers' in problem for problem in result['problems']))

    def test_failed_high_gate_preserves_ordinary_progress(self):
        result = self.assess(docs={'weighted_gate': {'pass': False}})
        self.assertTrue(result['attention_required'])
        self.assertIn('preserve ordinary', result['problems'][0])

    def test_both_final_accounting_records_are_required(self):
        result = self.assess(ads=[], docs={'ordinary_complete': {'complete': True}})
        self.assertNotEqual(result['phase'], 'complete')
        result = self.assess(ads=[], docs={'ordinary_complete': {'complete': True},
                                         'weighted_complete': {'complete': True}})
        self.assertEqual(result['phase'], 'complete')

    def test_progress_resets_the_no_children_clock(self):
        first = self.assess()
        receipt = {'one': {'complete': True, 'publication_complete': True, 'event_count': 20}}
        result = self.assess(receipts=receipt, previous=first, now=2000)
        self.assertFalse(result['attention_required'])
        self.assertEqual(result['completed_events'], 20)
        self.assertEqual(result['last_progress_at_epoch'], 2000)


if __name__ == '__main__':
    unittest.main()
