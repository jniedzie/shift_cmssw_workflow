"""Completion and shutdown contracts for the Nano pipeline supervisor."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import watch_shift_decay_dag as watchdog


class DecayWatchdogTest(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)
        self.write('submission.json',dict(dagman_cluster=17,tag='test'))
        (self.root/'sampling_plan.json').write_text('{}\n')
        self.manifest=dict(jobs=1,events=2,strata={'qcd_0to1':2},eos_output='/eos/user/test/campaign',
            sources=[dict(index=0,stratum='qcd_0to1',events=2)],
            detector_sampling=dict(plan_sha256=hashlib.sha256(b'{}\n').hexdigest()))
        self.write('manifest.json',self.manifest)
        (self.root/'all_jobs.txt').write_text('0 0 2 0\n')
        self.receipt=dict(job=0,events=2,complete=True,exit_code=0,source_stratum='qcd_0to1',
            validated_tier_events={tier:2 for tier in ('GEN','SIM','DIGIHLT','RECO','NANO')},
            nano_path='/eos/user/test/campaign/qcd_0to1/job0000000/nano.root',nano_bytes=10,
            report_sha256='a'*64)
        self.write('results/g0/status0.json',self.receipt)

    def write(self,name,value):
        path=self.root/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(value))

    def nodes(self,nano=5,histogram=0):
        (self.root/'node_status').write_text(
            f'[ Node = "N00000"; NodeStatus = {nano}; ]\n'
            f'[ Node = "H00000"; NodeStatus = {histogram}; ]\n')

    def metrics(self,cluster=17,exitcode=1):
        self.write('production.dag.metrics',dict(dagman_id=str(cluster),end_time=10,exitcode=exitcode))

    def test_nano_completion_does_not_wait_for_histograms(self):
        self.nodes()
        with patch.object(watchdog,'query',return_value=[dict(ClusterId=17,JobStatus=2)]):
            status=watchdog.step(self.root)
        self.assertTrue(status['nano_complete'])
        self.assertFalse(status['terminal'])
        self.assertEqual(json.loads((self.root/'production_complete.json').read_text())['events'],2)

    def test_corrupt_success_receipt_cannot_publish_completion(self):
        self.nodes()
        self.receipt['nano_path']='/wrong/file.root'
        self.write('results/g0/status0.json',self.receipt)
        with patch.object(watchdog,'query',return_value=[]),self.assertRaisesRegex(ValueError,'receipt'):
            watchdog.step(self.root)
        self.assertFalse((self.root/'production_complete.json').exists())

    def test_failed_dag_stops_and_records_missing_nano_without_success_marker(self):
        self.nodes(nano=6,histogram=7)
        self.metrics()
        with patch.object(watchdog,'query',return_value=[]):
            status=watchdog.step(self.root)
        self.assertEqual(status['health'],'failed')
        self.assertTrue(status['terminal'])
        self.assertEqual(status['failed_nano_jobs'],[0])
        self.assertTrue((self.root/'production_failed.json').exists())
        self.assertFalse((self.root/'production_complete.json').exists())

    def test_absent_dag_with_no_matching_terminal_evidence_keeps_monitoring(self):
        self.nodes(nano=6)
        self.metrics(cluster=16)
        with patch.object(watchdog,'query',return_value=[]):
            status=watchdog.step(self.root)
        self.assertFalse(status['terminal'])
        self.assertFalse((self.root/'production_failed.json').exists())

    def test_workers_still_draining_prevent_monitor_exit(self):
        self.nodes(nano=6)
        self.metrics()
        with patch.object(watchdog,'query',return_value=[dict(ClusterId=18,JobStatus=2)]):
            status=watchdog.step(self.root)
        self.assertEqual(status['health'],'draining')
        self.assertFalse(status['terminal'])

    def test_all_audits_complete_stops_monitor(self):
        self.nodes(histogram=5)
        self.metrics(exitcode=0)
        self.write('final_complete.json',dict(complete=True))
        with patch.object(watchdog,'query',return_value=[]):
            self.assertEqual(watchdog.step(self.root)['health'],'complete')

    def test_query_errors_are_bounded_without_claiming_production_failure(self):
        with patch.object(sys,'argv',['watchdog',str(self.root)]), \
                patch.object(watchdog,'query',side_effect=RuntimeError('scheduler unavailable')) as query, \
                patch.object(watchdog.time,'sleep'):
            watchdog.main()
        self.assertEqual(query.call_count,5)
        self.assertEqual(json.loads((self.root/'live_status.json').read_text())['health'],'monitor_error')
        self.assertFalse((self.root/'production_failed.json').exists())

    def test_missing_dag_without_terminal_evidence_does_not_wait_forever(self):
        self.nodes(nano=6)
        with patch.object(sys,'argv',['watchdog',str(self.root)]), \
                patch.object(watchdog,'query',return_value=[]) as query, \
                patch.object(watchdog.time,'sleep'):
            watchdog.main()
        self.assertEqual(query.call_count,5)
        self.assertEqual(json.loads((self.root/'live_status.json').read_text())['health'],'monitor_error')
        self.assertFalse((self.root/'production_failed.json').exists())


if __name__=='__main__':
    unittest.main()
