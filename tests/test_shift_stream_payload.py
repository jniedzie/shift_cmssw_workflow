# Code style: AI-code. AI-generated streaming completion contract tests.
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import subprocess

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import run_shift_stream_payload as payload


class PayloadTests(unittest.TestCase):
    def test_tea_imports_its_storage_helper_before_the_workflow_facade(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('scripts', 'tea/configs', 'tea/bin'):
                (root / name).mkdir(parents=True)
            (root / 'scripts/shift_storage_paths.py').write_text('def resolve_storage_path(path): return path\n')
            (root / 'tea/configs/shift_storage_paths.py').write_text(
                '_canonical_path = "analysis helper"\nresolve_storage_path = None\ntier_directory = None\n')
            env = payload.payload_environment('tea', root, {'PATH': os.environ['PATH'],
                                                          'PYTHONPATH': str(root / 'scripts')})
            result = subprocess.run([sys.executable, '-c',
                'from shift_storage_paths import _canonical_path; print(_canonical_path)'],
                env=env, check=True, capture_output=True, text=True)
            self.assertEqual(result.stdout.strip(), 'analysis helper')
            self.assertEqual(env['PYTHONPATH'].split(os.pathsep)[:3],
                             [str(root / 'tea/configs'), str(root / 'tea/bin'), str(root / 'scripts')])

    def test_cmssw_retains_the_frozen_workflow_helper_first(self):
        env = payload.payload_environment('cmssw', '/frozen/tools', {'PYTHONPATH': '/cmssw/python'})
        self.assertEqual(env['PYTHONPATH'], '/frozen/tools/scripts:/cmssw/python')

    def exercise(self, receipt, exit_code=0, extra=None):
        with tempfile.TemporaryDirectory() as directory:
            previous = Path.cwd()
            try:
                os.chdir(directory)
                if receipt is not None:
                    Path('report.json').write_text(json.dumps(receipt))
                result = payload.finish(dict(result_path='report.json', cohort_id='source_A',
                    stage='reco', status_fields=extra or {}), exit_code)
                return result, json.loads(Path('status.json').read_text())
            finally:
                os.chdir(previous)

    def test_process_success_requires_successful_semantic_receipt(self):
        for receipt in (None, {}, {'complete': False}):
            code, status = self.exercise(receipt)
            self.assertNotEqual(code, 0)
            self.assertFalse(status['complete'])

    def test_late_worker_failure_cannot_release_a_completed_receipt(self):
        code, status = self.exercise({'complete': True, 'nano_sha256': 'a'*64}, 42)
        self.assertEqual(code, 42)
        self.assertFalse(status['complete'])

    def test_preserves_measured_readback_and_actual_stage_counts(self):
        code, status = self.exercise(dict(complete=True, events=1, nano_sha256='a'*64,
            nano_readback_sha256='a'*64, validated_tier_events={'NANO': 1}))
        self.assertEqual(code, 0)
        self.assertTrue(status['complete'])
        self.assertEqual(status['nano_sha256'], status['nano_readback_sha256'])
        self.assertEqual(status['validated_tier_events'], {'NANO': 1})

    def test_request_cannot_replace_physical_evidence(self):
        with self.assertRaisesRegex(ValueError, 'cannot override'):
            self.exercise({'complete': True, 'events': 1}, extra={'events': 100})


if __name__ == '__main__':
    unittest.main()
