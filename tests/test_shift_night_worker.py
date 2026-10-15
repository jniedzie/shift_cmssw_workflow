# Code style: AI-code. AI-generated exact-weight, admission and durable-rescue regression tests.
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import time
import types
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/shift_night_worker.py'
SPEC = importlib.util.spec_from_file_location('night_worker', SCRIPT)
worker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(worker)


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.old = Path.cwd()
        os.chdir(self.directory.name)

    def tearDown(self):
        os.chdir(self.old)
        self.directory.cleanup()

    def test_chirp_skips_login_lookup_and_preserves_local_endpoint(self):
        command = Path('condor_chirp').resolve()
        command.write_text('#!/usr/bin/python3\nimport json,os\nprint(json.dumps({k:os.environ.get(k) for k in ("SKIP_LOCAL_CONFIG_FILE","_CONDOR_CHIRP_CONFIG","LD_LIBRARY_PATH")}))\n')
        command.chmod(0o755)
        with patch.dict(os.environ, {'_CONDOR_CHIRP_CONFIG':'/local/chirp.config',
                                     'LD_LIBRARY_PATH':'/incompatible/cmssw'}):
            observed = json.loads(worker.native([str(command)]))
        self.assertEqual(observed['SKIP_LOCAL_CONFIG_FILE'], 'TRUE')
        self.assertEqual(observed['_CONDOR_CHIRP_CONFIG'], '/local/chirp.config')
        self.assertIsNone(observed['LD_LIBRARY_PATH'])

    def test_native_timeout_also_stops_lookup_descendants_holding_pipes(self):
        command = Path('lookup_helper').resolve()
        command.write_text('#!/usr/bin/python3\nimport subprocess,time\nsubprocess.Popen(["/usr/bin/sleep","10"])\ntime.sleep(10)\n')
        command.chmod(0o755)
        started = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired):
            worker.native([str(command)], timeout=0.1)
        self.assertLess(time.monotonic() - started, 2)

    def test_transient_chirp_read_retries_without_starting_science(self):
        failure = subprocess.TimeoutExpired(['condor_chirp'], 10)
        with patch.object(worker, 'native', side_effect=[failure, '12\n']) as call, patch.object(worker.time, 'sleep'):
            self.assertEqual(worker.chirp_command(['get_job_attr', 'NumJobStarts']), '12\n')
        self.assertEqual(call.call_count, 2)
        self.assertFalse(Path('science_started.json').exists())

    def test_persistent_chirp_failure_is_bounded(self):
        with patch.object(worker, 'native', side_effect=subprocess.TimeoutExpired(['condor_chirp'], 10)) as call, patch.object(worker.time, 'sleep'):
            with self.assertRaises(subprocess.TimeoutExpired):
                worker.chirp_command(['get_job_attr', 'NumJobStarts'])
        self.assertEqual(call.call_count, 3)
        self.assertFalse(Path('science_started.json').exists())

    def test_admission_expired_never_starts_science(self):
        Path('job.ad').write_text('ShiftNightAdmissionStartEpoch = 0\nShiftNightAdmissionStopEpoch = 20\nShiftNightAdmissionLeaseEpoch = 15\nNumJobStarts = 3\n')
        with patch.dict(os.environ, {'_CONDOR_JOB_AD': str(Path('job.ad').resolve())}), patch.object(worker.time, 'time', return_value=16), patch.object(worker, 'live_ad', return_value={'ShiftNightAdmissionStartEpoch':0,'ShiftNightAdmissionStopEpoch':20,'ShiftNightAdmissionLeaseEpoch':15,'NumJobStarts':3}), patch.object(worker, 'chirp') as call:
            with self.assertRaises(SystemExit) as error:
                worker.mark_started()
            self.assertEqual(error.exception.code, 75)
            self.assertFalse(Path('science_started.json').exists())
            self.assertIn(unittest.mock.call('ShiftNightDeferredNumJobStarts', 3), call.call_args_list)

    def test_bootstrap_deferral_uses_current_start_count_not_claim_snapshot(self):
        Path('job.ad').write_text('ShiftNightAdmissionStartEpoch = 0\nShiftNightAdmissionStopEpoch = 20\nShiftNightAdmissionLeaseEpoch = 15\nNumJobStarts = 0\n')
        current = {'ShiftNightAdmissionStartEpoch':0,'ShiftNightAdmissionStopEpoch':20,
                   'ShiftNightAdmissionLeaseEpoch':15,'NumJobStarts':1,
                   'ShiftNightScienceStarted':False}
        with patch.dict(os.environ, {'_CONDOR_JOB_AD':str(Path('job.ad').resolve())}), patch.object(worker.time,'time',return_value=16), patch.object(worker,'live_ad',return_value=current), patch.object(worker,'chirp') as call:
            with self.assertRaises(SystemExit) as error:
                worker.bootstrap_started()
        self.assertEqual(error.exception.code,75)
        self.assertIn(unittest.mock.call('ShiftNightDeferredNumJobStarts',1),call.call_args_list)
        self.assertNotIn(unittest.mock.call('ShiftNightBootstrapInProgress',True),call.call_args_list)
        self.assertFalse(Path('science_started.json').exists())

    def test_admission_clears_old_deferred_proof(self):
        Path('job.ad').write_text('ShiftNightAdmissionStartEpoch = 0\nShiftNightAdmissionStopEpoch = 20\nShiftNightAdmissionLeaseEpoch = 15\nNumJobStarts = 3\n')
        with patch.dict(os.environ, {'_CONDOR_JOB_AD': str(Path('job.ad').resolve())}), patch.object(worker.time, 'time', return_value=10), patch.object(worker, 'live_ad', return_value={'ShiftNightAdmissionStartEpoch':0,'ShiftNightAdmissionStopEpoch':20,'ShiftNightAdmissionLeaseEpoch':15,'NumJobStarts':3}), patch.object(worker, 'chirp') as call:
            worker.mark_started()
            self.assertTrue(Path('science_started.json').exists())
            self.assertIn(unittest.mock.call('ShiftNightAdmissionDeferred', False), call.call_args_list)
            self.assertIn(unittest.mock.call('ShiftNightDeferredNumJobStarts', -1), call.call_args_list)

    def test_exact_native_weight_affects_event_contract(self):
        selection = dict(source_gen_sha256='a'*64, raw_indices=[1], event_ids=[[1,2,3]],
                         native_weights=[1.0000000000000002], sampling_probabilities=[.002],
                         seed=429999900, response_contract_sha256='b'*64)
        original = worker.event_contract(selection)
        selection['native_weights'] = [1.]
        self.assertNotEqual(original, worker.event_contract(selection))

    def test_evicted_science_is_never_replayed_automatically(self):
        Path('job.ad').write_text('ShiftNightScienceStarted = true\nShiftNightAdmissionStartEpoch = 0\nShiftNightAdmissionStopEpoch = 20\nShiftNightAdmissionLeaseEpoch = 15\nNumJobStarts = 2\n')
        with patch.dict(os.environ, {'_CONDOR_JOB_AD': str(Path('job.ad').resolve())}), patch.object(worker.time, 'time', return_value=10), patch.object(worker, 'live_ad', return_value={'ShiftNightScienceStarted':True}), patch.object(worker, 'chirp') as call:
            with self.assertRaises(SystemExit) as error:
                worker.mark_started()
            self.assertEqual(error.exception.code, 76)
            self.assertFalse(Path('science_started.json').exists())
            call.assert_not_called()
            self.assertTrue(worker.read('science_restart_rejected.json')['requires_explicit_manual_recovery'])

    def test_renewed_live_lease_can_admit_after_snapshot_expiry(self):
        Path('job.ad').write_text('ShiftNightAdmissionStartEpoch = 0\nShiftNightAdmissionStopEpoch = 100\nShiftNightAdmissionLeaseEpoch = 15\n')
        current = {'ShiftNightScienceStarted':False,'ShiftNightAdmissionStartEpoch':0,'ShiftNightAdmissionStopEpoch':100,'ShiftNightAdmissionLeaseEpoch':90,'NumJobStarts':1}
        with patch.dict(os.environ, {'_CONDOR_JOB_AD':str(Path('job.ad').resolve())}), patch.object(worker.time,'time',return_value=30), patch.object(worker,'live_ad',return_value=current), patch.object(worker,'chirp'):
            worker.mark_started()
            self.assertTrue(Path('science_started.json').exists())

    def test_actual_stop_overrides_unexpired_snapshot_and_lease(self):
        current = {'ShiftNightScienceStarted':False,'ShiftNightAdmissionStartEpoch':0,'ShiftNightAdmissionStopEpoch':20,'ShiftNightAdmissionLeaseEpoch':90,'NumJobStarts':1}
        with patch.object(worker.time,'time',return_value=21), patch.object(worker,'live_ad',return_value=current), patch.object(worker,'chirp'):
            with self.assertRaises(SystemExit) as error:
                worker.mark_started()
            self.assertEqual(error.exception.code,75)
            self.assertFalse(Path('science_started.json').exists())

    def test_live_fields_parse_native_literals_without_eval(self):
        values=['1\n','2\n','3\n','false\n','4\n']
        with patch.object(worker,'native',side_effect=values):
            current=worker.live_ad()
        self.assertEqual(current['ShiftNightAdmissionLeaseEpoch'],3)
        self.assertIs(current['ShiftNightScienceStarted'],False)

    def test_unknown_live_lease_never_starts_science(self):
        with patch.object(worker,'native',side_effect=['1','2','UNDEFINED']):
            with self.assertRaises(ValueError):
                worker.mark_started()
        self.assertFalse(Path('science_started.json').exists())

    def test_publication_failure_preserves_unique_reco_in_rescue(self):
        worker.save('bootstrap.json', {'response_contract_sha256':'a'*64})
        worker.save('selection.json', dict(output_base='/eos/unique', source_gen_sha256='b'*64, count=1, seed=100))
        worker.save('report.json', dict(complete=False, error='cmsRun failed', stages={'1':{}}, validated_tier_events={'RECO':1}))
        worker.save('science_started.json', {'started_at_epoch':0})
        Path('step3.root').write_bytes(b'Unique completed reconstruction')
        Path('evidence.tar.gz').write_bytes(b'Evidence')
        with patch.object(worker, 'native', side_effect=RuntimeError('EOS unavailable')), patch.object(worker, 'chirp') as call:
            result = worker.finalise(1, 2000000, 1, 'c'*64, 'd'*64)
        self.assertEqual(result, 1)
        status = worker.read('status.json')
        self.assertEqual(status['failure_kind'], 'publication')
        self.assertFalse(status['complete'])
        self.assertTrue(status['rescue_contains_unique_tiers'])
        with tarfile.open('rescue_payload.tar.gz') as archive:
            self.assertEqual(archive.extractfile('step3.root').read(), b'Unique completed reconstruction')
        returned = [args.args[1] for args in call.call_args_list if args.args[0]=='ShiftNightReturnedBytes'][0]
        self.assertGreater(returned, Path('rescue_payload.tar.gz').stat().st_size + Path('status.json').stat().st_size)

    def test_bootstrap_failure_always_writes_small_status_and_rescue(self):
        Path('bootstrap.json').write_text('{invalid')
        with patch.object(worker, 'chirp'):
            result = worker.finalise(1, 2000000, 1, 'c'*64, 'd'*64)
        self.assertEqual(result, 1)
        self.assertFalse(worker.read('status.json')['complete'])
        self.assertTrue(Path('rescue_payload.tar.gz').is_file())
        self.assertNotIn('output_base', worker.read('status.json')['error'])
        self.assertEqual(worker.read('status.json')['processed_events'], 0)

    def test_final_progress_timeout_preserves_successful_published_science(self):
        worker.save('bootstrap.json', {'response_contract_sha256': 'a' * 64})
        worker.save('selection.json', dict(output_base='/eos/test', source_gen_sha256='b' * 64,
            count=1, seed=100, event_ids=[[1, 1, 1]], native_weights=[1.], raw_indices=[0],
            sampling_probabilities=[.1]))
        worker.save('report.json', dict(complete=True, source_events=[dict(id=[1, 1, 1], weight=1.)],
            seed=100, source_indices=[0], nano={},
            validated_tier_events={tier: 1 for tier in ('GEN', 'SIM', 'DIGIHLT', 'RECO', 'NANO')}))
        payload = Path('payload/workflow/scripts')
        payload.mkdir(parents=True)
        (payload / 'run_shift_gen_to_nano.py').write_text('def audit_nano(*args): return {}\n')
        Path('nano.root').write_bytes(b'synthetic-audited-nano')
        Path('evidence.tar.gz').write_bytes(b'synthetic-evidence')
        handle = types.SimpleNamespace(Get=lambda name: [], Close=lambda: None)
        root = types.SimpleNamespace(TFile=types.SimpleNamespace(Open=lambda path: handle))
        with patch.dict('sys.modules', {'ROOT': root}), patch.object(worker, 'native'), \
                patch.object(worker, 'upload', return_value='f' * 64), \
                patch.object(worker, 'chirp', side_effect=subprocess.TimeoutExpired(['condor_chirp'], 10)):
            code = worker.finalise(0, 2000000, 1, 'c' * 64, 'd' * 64)
        self.assertEqual(code, 0)
        status = worker.read('status.json')
        self.assertTrue(status['complete'])
        self.assertTrue(status['durable_EOS_publication_passed'])
        self.assertIn('chirp_reporting_error', status)
        self.assertFalse(status['rescue_contains_unique_tiers'])

    def test_integrity_and_configuration_failures_are_not_computation(self):
        for tier in ('SIM_AUDIT','DIGIHLT_AUDIT','RECO_AUDIT','NANO_AUDIT'):
            self.assertEqual(worker.classify_report_failure({'current_tier':tier}),'integrity')
        self.assertEqual(worker.classify_report_failure({'current_tier':'SIM_CONFIG'}),'configuration')
        self.assertEqual(worker.classify_report_failure({'current_tier':'SETUP'}),'input')
        for tier in ('SIM','DIGIHLT','RECO','NANO'):
            self.assertEqual(worker.classify_report_failure({'current_tier':tier}),'computation')


if __name__ == '__main__':
    unittest.main()
