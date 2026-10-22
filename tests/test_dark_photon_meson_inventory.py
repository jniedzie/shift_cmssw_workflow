# Code style: AI-code. AI-generated diagnostic worker boundary controls.
from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from run_dark_photon_meson_inventory import bounded_log, pin, run, sha


class InventoryWorkerTest(unittest.TestCase):
    def test_condor_scratch_cwd_is_followed_by_explicit_persistent_chdir(self):
        directory = Path('/persistent/census/c000')
        request = dict(schema='shift-dp-parent-census-request-v1',directory=str(directory),
            worker_sha256='worker',source_collisions=1000,seed=42,source_timeout_seconds=3600,
            worker_timeout_seconds=5400,maximum_result_bytes=4*1024*1024,model={},tools_archive={})
        with patch('run_dark_photon_meson_inventory.sha',side_effect=['request','worker']), \
             patch.object(Path,'read_text',return_value=json.dumps(request)), \
             patch.object(Path,'is_dir',return_value=True), \
             patch.object(Path,'cwd',return_value=Path('/pool/condor/dir_test')), \
             patch('run_dark_photon_meson_inventory.os.chdir') as change, \
             patch('run_dark_photon_meson_inventory.pin',side_effect=RuntimeError('boundary reached')):
            with self.assertRaisesRegex(RuntimeError,'boundary reached'):
                run(directory/'request.json','request')
            change.assert_called_once_with(directory)

    def test_changed_request_is_rejected_before_any_execution_or_status_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'request.json'
            path.write_text('{}')
            with self.assertRaisesRegex(ValueError,'request changed'):
                run(path,'0'*64)
            self.assertEqual(sorted(p.name for p in path.parent.iterdir()),['request.json'])

    def test_runtime_or_code_input_requires_its_exact_byte_authority(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'frozen_input'
            path.write_bytes(b'qualified bytes')
            authority = dict(path=str(path),sha256=sha(path))
            self.assertEqual(pin(authority),path)
            path.write_bytes(b'changed bytes')
            with self.assertRaisesRegex(ValueError,'Changed frozen census input'):
                pin(authority)

    def test_persistent_log_is_bounded_and_keeps_original_failure_tail(self):
        with tempfile.TemporaryDirectory() as temporary:
            source,target = Path(temporary)/'source.log',Path(temporary)/'native.log'
            source.write_bytes(b'FIRST'+b'x'*1000000+b'FAILURE TAIL')
            bounded_log(source,target,1024)
            result = target.read_bytes()
            self.assertTrue(result.startswith(b'FIRST'))
            self.assertTrue(result.endswith(b'FAILURE TAIL'))
            self.assertLess(len(result),1200)
            self.assertIn(b'intermediate log bytes omitted',result)


if __name__=='__main__':
    unittest.main()
