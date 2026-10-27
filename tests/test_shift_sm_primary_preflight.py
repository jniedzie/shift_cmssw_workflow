# Code style: AI-code. AI-generated guards for the frozen-runtime SM preflight.
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1]/'scripts/run_shift_sm_primary_preflight.py'
SPEC = importlib.util.spec_from_file_location('sm_preflight', SCRIPT)
PREFLIGHT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PREFLIGHT)


class SourceCensusTest(unittest.TestCase):
    def test_direct_and_frozen_streaming_envelopes(self):
        direct = dict(schema='shift-SM-primary-preflight-request-v1')
        self.assertIs(PREFLIGHT.validate_request(direct), direct)
        streamed = dict(schema='shift-stream-payload-v1', stage='sm_primary_preflight',
            environment='cmssw', cohort_id='full-original-census', result_path='report.json',
            argv=['python3', '@TOOLS@/scripts/run_shift_sm_primary_preflight.py', '@SCRATCH@/request.json'],
            application_timeout_seconds=1800, return_files=['report.json'])
        self.assertIs(PREFLIGHT.validate_request(streamed), streamed)
        for field, value in [('stage', 'nano'), ('stage', 'sm_response_primary_preflight'),
                             ('environment', 'tea'), ('schema', 'unknown')]:
            bad = dict(streamed, **{field: value})
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                PREFLIGHT.validate_request(bad)

    def source(self):
        descriptor = dict(gen='/eos/full.root', gen_sha256='a'*64,
            event_ids=[[5, 1, 2], [5, 1, 7]], weights=[0.25, 1.5],
            selected_indices=[1], production_selection=dict(physics_valid=False))
        return dict(source_id='qcd_5to10_source1', gen_path=descriptor['gen'],
                    gen_sha256=descriptor['gen_sha256'], full_event_ids=copy.deepcopy(descriptor['event_ids']),
                    source_descriptor=descriptor)

    def test_full_originals_including_unselected_are_required(self):
        source = self.source()
        self.assertIs(PREFLIGHT.validate_source(source), source['source_descriptor'])
        source['full_event_ids'] = [source['full_event_ids'][1]]
        with self.assertRaisesRegex(ValueError, 'complete ordered original'):
            PREFLIGHT.validate_source(source)

    def test_native_weights_cover_the_full_originals(self):
        source = self.source()
        source['source_descriptor']['weights'] = [1.5]
        with self.assertRaisesRegex(ValueError, 'all original'):
            PREFLIGHT.validate_source(source)

    def test_changed_gen_and_duplicate_identity_rejected(self):
        source = self.source()
        source['gen_sha256'] = 'b'*64
        with self.assertRaisesRegex(ValueError, 'frozen source'):
            PREFLIGHT.validate_source(source)
        source = self.source()
        source['full_event_ids'][1] = source['full_event_ids'][0]
        source['source_descriptor']['event_ids'] = source['full_event_ids']
        with self.assertRaisesRegex(ValueError, 'complete ordered original'):
            PREFLIGHT.validate_source(source)


class WorkerInstallTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='shift-sm-preflight-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.release = self.root/'worker/runtime/CMSSW'
        self.library = self.release/'lib/el9_amd64_gcc13'
        self.library.mkdir(parents=True)
        self.tools = self.root/'tools'
        self.plugin = self.tools/'plugin'/PREFLIGHT.PLUGIN
        self.plugin.parent.mkdir(parents=True)
        self.plugin.write_bytes(b'synthetic addon fixture')
        self.cache = self.library/'.edmplugincache'
        self.original_cache = 'existing.so ExistingModule CMS%EDM%Framework%Module\n'
        self.cache.write_text(self.original_cache)
        old_libraries = {}
        for name in PREFLIGHT.LIBRARIES:
            path = self.library/name
            path.write_bytes(('synthetic old ABI '+name).encode())
            old_libraries[name] = dict(bytes=path.stat().st_size, sha256=PREFLIGHT.sha(path))
        self.receipt = dict(runtime_sha256='a'*64, old_libraries=old_libraries,
            plugin_sha256=PREFLIGHT.sha(self.plugin), plugin_source_sha256='b'*64,
            addon_plugin_cache_lines=PREFLIGHT.CACHE_LINES)
        receipt_path = self.tools/'plugin/build_receipt.json'
        receipt_path.write_text(json.dumps(self.receipt))
        (self.tools/'runtimes.json').write_text(json.dumps(dict(cmssw=dict(sha256='a'*64))))
        self.request = dict(runtime_sha256='a'*64, preflight_addon=dict(
            path='plugin/'+PREFLIGHT.PLUGIN, sha256=PREFLIGHT.sha(self.plugin),
            build_receipt=dict(path='plugin/build_receipt.json', sha256=PREFLIGHT.sha(receipt_path))))
        self.environment = dict(_CONDOR_SCRATCH_DIR=str(self.root/'worker'),
                                CMSSW_BASE=str(self.release), SCRAM_ARCH='el9_amd64_gcc13')

    def test_preserves_old_cache_and_libraries_and_is_idempotent(self):
        with patch.dict(os.environ, self.environment):
            PREFLIGHT.install_addon(self.request, self.tools)
            first = self.cache.read_text()
            PREFLIGHT.install_addon(self.request, self.tools)
            self.assertEqual(first, self.cache.read_text())
            PREFLIGHT.check_libraries(self.release, self.receipt)
        self.assertEqual(first, self.original_cache+'\n'.join(PREFLIGHT.CACHE_LINES)+'\n')
        self.assertEqual(PREFLIGHT.sha(self.library/PREFLIGHT.PLUGIN), PREFLIGHT.sha(self.plugin))

    def test_refuses_shared_or_nonlocal_runtime_before_mutation(self):
        for runtime, scratch in [('/afs/shared/CMSSW', str(self.root/'worker')),
                                 ('/afs/shared/CMSSW', '/afs/shared'),
                                 (str(self.release), '')]:
            with self.subTest(runtime=runtime, scratch=scratch):
                environment = dict(self.environment, CMSSW_BASE=runtime, _CONDOR_SCRATCH_DIR=scratch)
                with patch.dict(os.environ, environment), self.assertRaises(ValueError):
                    PREFLIGHT.install_addon(self.request, self.tools)
        self.assertFalse((self.library/PREFLIGHT.PLUGIN).exists())
        self.assertEqual(self.cache.read_text(), self.original_cache)

    def test_library_mismatch_refuses_before_install_or_cache_change(self):
        (self.library/PREFLIGHT.LIBRARIES[0]).write_bytes(b'wrong ABI')
        with patch.dict(os.environ, self.environment), self.assertRaisesRegex(ValueError, 'library differs'):
            PREFLIGHT.install_addon(self.request, self.tools)
        self.assertFalse((self.library/PREFLIGHT.PLUGIN).exists())
        self.assertEqual(self.cache.read_text(), self.original_cache)

    def test_addon_cannot_escape_frozen_tools(self):
        for path in ['/tmp/external.so', '../external.so']:
            with self.subTest(path=path), self.assertRaises(ValueError):
                PREFLIGHT.tools_file(self.tools, path)
        external = self.root/'external.so'
        external.write_bytes(b'outside tools')
        (self.tools/'escape.so').symlink_to(external)
        with self.assertRaises(ValueError):
            PREFLIGHT.tools_file(self.tools, 'escape.so')

    def test_wrong_receipt_runtime_or_addon_refuses(self):
        for field in ['runtime_sha256', 'sha256', 'receipt_sha256']:
            request = copy.deepcopy(self.request)
            if field == 'runtime_sha256':
                request[field] = 'c'*64
            elif field == 'sha256':
                request['preflight_addon'][field] = 'c'*64
            else:
                request['preflight_addon']['build_receipt']['sha256'] = 'c'*64
            with self.subTest(field=field), patch.dict(os.environ, self.environment), self.assertRaises(ValueError):
                PREFLIGHT.install_addon(request, self.tools)
        self.assertFalse((self.library/PREFLIGHT.PLUGIN).exists())
        self.assertEqual(self.cache.read_text(), self.original_cache)

    def test_converter_failure_stays_visible_and_preserves_input(self):
        source = SourceCensusTest().source()
        payload = b'synthetic original GEN'
        source['gen_sha256'] = source['source_descriptor']['gen_sha256'] = hashlib.sha256(payload).hexdigest()
        request = dict(self.request, schema='shift-SM-primary-preflight-request-v1', sources=[source])
        work = self.root/'result'
        work.mkdir()
        request_path = work/'request.json'
        request_path.write_text(json.dumps(request))

        def subprocess_result(command, **kwargs):
            if command[0] == 'xrdcp':
                Path(command[-1]).write_bytes(payload)
            elif command[0] == 'cmsRun':
                raise subprocess.CalledProcessError(66, command)

        previous = Path.cwd()
        try:
            os.chdir(work)
            with patch.dict(os.environ, self.environment), patch.object(PREFLIGHT, 'verify_originals'), \
                    patch.object(PREFLIGHT.subprocess, 'run', side_effect=subprocess_result), \
                    self.assertRaises(subprocess.CalledProcessError):
                PREFLIGHT.run(request_path, self.tools)
        finally:
            os.chdir(previous)
        report = json.loads((work/'report.json').read_text())
        self.assertFalse(report['complete'])
        self.assertFalse(report['physics_valid'])
        self.assertEqual(report['failed_source_id'], source['source_id'])
        self.assertIn('CalledProcessError', report['error'])
        self.assertEqual((work/'original_0000.root').read_bytes(), payload)


if __name__ == '__main__':
    unittest.main()
