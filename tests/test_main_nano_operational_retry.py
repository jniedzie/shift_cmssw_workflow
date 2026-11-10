# Code style: AI-code.
"""A reviewed operational retry must preserve science and original finalizer pins."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import dark_photon_production_support as support


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


class MainNanoRetry(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = root = Path(self.temporary.name)
        (root/'statuses').mkdir()
        source = dict(output_base='/eos/user/test/detector', gen_sha256='gen',
                      event_ids=[[1, 1, 1]], selected_indices=[0])
        write(root/'source.json', source)
        write(root/'plan.json', dict(points=[dict(point='point', chunks=[dict(job=5, skip=0, count=1)])],
                                    detector_runtime=dict(runtime_sha256='runtime')))
        write(root/'gen_inventory.json', dict(points=dict(point=dict(gen_sha256='gen', events=1))))
        write(root/'main_histogrammer.json', dict(detector_stage_timeouts=[3600, 900, 1800, 900],
              detector_timeout_seconds=7200, worker_runtime_seconds=10800,
              detector_first_event_stall_timeout_seconds=3600, detector_event_stall_timeout_seconds=3600))
        write(root/'seal.json', dict(files={str(root/'source.json'):support.sha256(root/'source.json')}))
        write(root/'primary_preflight/receipt.json', dict(complete=True,
              original_plan_sha256=support.sha256(root/'plan.json'),
              original_seal_sha256=support.sha256(root/'seal.json'),
              original_inventory_sha256=support.sha256(root/'gen_inventory.json'),
              replacement_runtime_sha256='runtime',
              points=dict(point=dict(gen_sha256='gen', full_count=1, converted_count=1))))
        names = ('seal.json', 'plan.json', 'source.json', 'gen_inventory.json',
                 'primary_preflight/receipt.json', 'main_histogrammer.json')
        pins = {name:support.sha256(root/name) for name in names}
        self.failure = dict(complete=False, action='detector', plan_sha256=pins['plan.json'],
            seal_sha256=pins['seal.json'], primary_preflight_receipt_sha256=pins['primary_preflight/receipt.json'],
            detector_stage_timeouts=[3600, 900, 1800, 900], detector_timeout_seconds=7200,
            requested_worker_runtime_seconds=10800, detector_first_event_stall_timeout_seconds=3600,
            detector_event_stall_timeout_seconds=3600, payload=dict(complete=False,
            source_descriptor_sha256=pins['source.json'], job=5, source_skip=0, events=1,
            current_tier='NANO', stages={'1':{}, '2':{}, '3':{}},
            error="TimeoutExpired(['cmsRun', '/scratch/step4_cfg.py'], 900)"))
        self.absence = dict(schema='shift-main-detector-output-absence-v1', complete=True,
            chunk=0, job=5, source_descriptor_sha256=pins['source.json'],
            absent_paths=['/eos/user/test/detector/job0000005'], checked_utc='2026-11-10T09:00:00Z')
        self.policy = dict(schema='shift-main-nano-timeout-retry-v1', original_files=pins,
                           replacement_helper_sha256=support.sha256(support.__file__), allowed_chunks={})
        self.refresh()

    def refresh(self):
        root = self.root
        write(root/'archive/first_failure.json', self.failure)
        write(root/'archive/absence.json', self.absence)
        self.policy['allowed_chunks']['0'] = {
            name:dict(path=str(root/path), sha256=support.sha256(root/path))
            for name,path in [('first_failure','archive/first_failure.json'), ('output_absence','archive/absence.json')]}
        write(root/'retry_policy.json', self.policy)
        self.reference = dict(path=str(root/'retry_policy.json'), sha256=support.sha256(root/'retry_policy.json'))

    def test_retry_uses_only_operational_budget_and_original_finalizer_identity(self):
        def complete(argv, **kwargs):
            self.assertEqual(argv[argv.index('--stage-timeouts')+1], '3600,900,1800,3600')
            self.assertEqual(argv[argv.index('--first-event-stall-timeout')+1], '3600')
            self.assertEqual(argv[argv.index('--event-stall-timeout')+1], '3600')
            self.assertEqual(kwargs['timeout'], 9900)
            write(Path('report.json'), dict(complete=True, job=5, source_skip=0, events=1,
                source_descriptor_sha256=support.sha256(self.root/'source.json'), source_gen_sha256='gen',
                nano=dict(event_ids=[[1,1,1]], events=1), nano_readback_sha256='nano', nano_sha256='nano'))
            return subprocess.CompletedProcess(argv, 0)
        with patch('dark_photon_stage_budget.recorded_primary_environment',return_value={}), \
             patch.object(support.subprocess, 'run', side_effect=complete):
            support.main_histogram_stage(self.root, 'detector', 0, retry_policy=self.reference)
        result = support.read(self.root/'statuses/chunk_0.json')
        self.assertTrue(result['complete'])
        for key,name in [('plan_sha256','plan.json'), ('seal_sha256','seal.json'),
                         ('primary_preflight_receipt_sha256','primary_preflight/receipt.json')]:
            self.assertEqual(result[key], self.policy['original_files'][name])
        self.assertEqual(result['retry_policy']['sha256'],self.reference['sha256'])
        (self.root/'statuses/chunk_0.json').unlink()
        with self.assertRaises(FileExistsError):
            support.main_histogram_stage(self.root, 'detector', 0, retry_policy=self.reference)

    def test_physics_failures_published_outputs_and_second_attempts_are_rejected(self):
        changes = [dict(error="TimeoutExpired(['cmsRun', '/scratch/step1_cfg.py'], 900)"),
                   dict(error="TimeoutExpired(['cmsRun', '/scratch/step4_cfg.py'], 3600)"),
                   dict(error="ValueError('bad particle')"), dict(current_tier='RECO'),
                   dict(nano_path='/eos/output'), dict(source_skip=1), dict(complete=True)]
        original = copy.deepcopy(self.failure)
        for change in changes:
            with self.subTest(change=change):
                self.failure = copy.deepcopy(original)
                self.failure['payload'].update(change)
                self.refresh()
                with self.assertRaises(ValueError):
                    support.main_nano_retry_policy(self.root,'detector',0,self.reference)
        self.failure = original
        self.failure['retry_policy'] = dict(previous_attempt=True)
        self.refresh()
        with self.assertRaises(ValueError):
            support.main_nano_retry_policy(self.root,'detector',0,self.reference)

    def test_changed_evidence_policy_source_and_wrong_action_cannot_start(self):
        for mutate in ('policy', 'failure', 'absence', 'source'):
            with self.subTest(mutate=mutate):
                files = {'policy':'retry_policy.json','failure':'archive/first_failure.json',
                         'absence':'archive/absence.json','source':'source.json'}
                path = self.root/files[mutate]
                original = path.read_bytes()
                path.write_bytes(original+b' ')
                with self.assertRaises(ValueError):
                    support.main_histogram_stage(self.root,'detector',0,retry_policy=self.reference)
                path.write_bytes(original)
        with self.assertRaises(ValueError):
            support.main_nano_retry_policy(self.root,'finalize',0,self.reference)
        self.absence['absent_paths'] = ['/eos/user/test/detector/job0000006']
        self.refresh()
        with self.assertRaises(ValueError):
            support.main_nano_retry_policy(self.root,'detector',0,self.reference)


if __name__ == '__main__':
    unittest.main()
