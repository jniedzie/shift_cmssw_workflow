# Code style: AI-code.
import unittest
import sys
import hashlib
import json
import os
from pathlib import Path
import tempfile
import gzip
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from shift_eos_storage import eos_path, eos_endpoint, xrootd_url, is_support_path, GROUP_ROOT
from shift_eos_storage import detector_source

class EOSStorageTest(unittest.TestCase):
    def test_compressed_source_keeps_descriptor_and_requires_native_preflight(self):
        original = Path.cwd()
        with tempfile.TemporaryDirectory() as temporary:
            try:
                os.chdir(temporary)
                descriptor = dict(event_ids=[[1,1,17]], weights=[1.e-15],
                    selected_indices=[0], sampling_probabilities=[1.e-5])
                authority = dict(complete=True, primary_preflight=dict(complete=True),
                    selected_source=descriptor)
                def request():
                    data = gzip.compress(json.dumps(authority).encode())
                    Path('source_authority.json.gz').write_bytes(data)
                    return dict(detector_source_ref=dict(
                        path='/afs/cern.ch/work/j/jniedzie/private/shift_cmssw/validation/test/source_authority.json.gz',
                        sha256=hashlib.sha256(data).hexdigest(), encoding='gzip-source-authority-v1'))
                self.assertEqual(detector_source(request()), descriptor)
                authority['primary_preflight']['complete'] = False
                with self.assertRaisesRegex(ValueError, 'preflight'):
                    detector_source(request())
            finally:
                os.chdir(original)
    def test_existing_user_storage_keeps_its_original_endpoint(self):
        self.assertEqual(xrootd_url('/eos/user/j/jniedzie/shift_cmssw_support/a.root'),
            'root://eosuser.cern.ch//eos/user/j/jniedzie/shift_cmssw_support/a.root')

    def test_group_files_never_use_the_eosuser_redirector(self):
        path=GROUP_ROOT+'/support/20261107/nano.root'
        self.assertEqual(eos_endpoint(path),'root://eoscms.cern.ch')
        self.assertEqual(eos_path(xrootd_url(path)),path)
        self.assertTrue(is_support_path(path))
        self.assertFalse(is_support_path(GROUP_ROOT+'/qcd/nano.root'))

    def test_traversal_and_unqualified_group_roots_are_rejected(self):
        for path in [GROUP_ROOT+'/support/../other', '/eos/cms/store/group/other/file', '/tmp/output.root']:
            with self.assertRaises(ValueError):xrootd_url(path)
        self.assertFalse(is_support_path(GROUP_ROOT+'_other/support/file'))

    def test_reference_urls_cannot_smuggle_query_options(self):
        with self.assertRaises(ValueError):eos_path('root://eoscms.cern.ch/'+GROUP_ROOT+'?authz=unexpected')

    def test_source_descriptor_resolves_from_worker_scratch_and_rejects_changed_bytes(self):
        original = Path.cwd()
        with tempfile.TemporaryDirectory() as temporary:
            try:
                os.chdir(temporary)
                data = json.dumps(dict(event_ids=[[1,1,10]], weights=[2.5])).encode()
                Path('detector_source.json').write_bytes(data)
                request = dict(detector_source_ref=dict(
                    path='/afs/cern.ch/work/j/jniedzie/private/shift_cmssw/validation/test/detector_source.json',
                    sha256=hashlib.sha256(data).hexdigest()))
                self.assertEqual(detector_source(request), json.loads(data))
                Path('detector_source.json').write_bytes(data+b' ')
                with self.assertRaisesRegex(ValueError, 'changed'):
                    detector_source(request)
                request['detector_source_ref']['path']='/tmp/detector_source.json'
                with self.assertRaisesRegex(ValueError, 'persistent'):
                    detector_source(request)
            finally:
                os.chdir(original)
        self.assertEqual(detector_source(dict(detector_source={'old':'inline'})), {'old':'inline'})
