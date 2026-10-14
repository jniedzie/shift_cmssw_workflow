# Code style: AI-code. AI-generated regressions for durable night submission and release gates.
import gzip
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from prepare_shift_night_reconstruction import DEFAULT_POLICY, prepare
from run_shift_night_reconstruction import NightController, event_log, validate_result
from shift_condor_native import NativeCondorError
from shift_night_plan import event_contract, load_epoch, load_plan, sha

NOW = 1792008000  # 2026-10-14 22:00 Zurich.


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2) + '\n')


class FakeBackend:
    def __init__(self, root, now=NOW):
        self.root, self.now, self.rows, self.calls = root, now, [], []
        self.submit_error = False
        self.observation_calls = 0

    def ads(self, constraint):
        return [dict(row) for row in self.rows]

    def observation(self):
        self.observation_calls += 1
        return dict(complete=True, account_coverage_verified=True, account_owner='jniedzie',
                    account_pool='tweetybird04.cern.ch', quota_checked_at_epoch=self.now,
                    account_checked_at_epoch=self.now, free_bytes=10**15, free_files=10**8,
                    account_workers=[])

    def command(self, command, cwd=None):
        self.calls.append(command)
        if command[0] == '/usr/bin/condor_submit':
            if self.submit_error:
                raise NativeCondorError('Response lost after possible submission')
            count = len((Path(cwd) / 'jobs.txt').read_text().splitlines())
            return '42.0 - 42.%d\n' % (count - 1)
        if command[0] == '/usr/bin/condor_qedit':
            values = dict(zip(command[3::2], command[4::2]))
            for row in self.rows:
                if row.get('JobStatus') in (1, 5):
                    row.update({key: int(value) for key, value in values.items()})
        elif command[0] == '/usr/bin/condor_release':
            for row in self.rows:
                if row.get('JobStatus') == 5:
                    row['JobStatus'] = 1
        return ''


class NightReconstructionTests(unittest.TestCase):
    def fixture(self, directory):
        root = Path(directory)
        draws = root / 'draws'
        draws.mkdir()
        jobs = []
        for index in range(2):
            selection = draws / ('selection%d.json' % index)
            write_json(selection, {'immutable_selection': index})
            row = dict(job=2000000 + index, epoch=1, source=index, skip=0, count=1,
                       seed=430000000 + index * 100, selection_path=str(selection),
                       selection_sha256=sha(selection), source_gen_sha256=('%064x' % (10 + index)),
                       raw_indices=[index], event_ids=[[1 + index, 1, 1]],
                       native_weights=[1.2345678901234567], sampling_probabilities=[.10009000000000001])
            row['event_contract_sha256'] = event_contract(row, 'a' * 64)
            jobs.append(row)
        jobs_path = draws / 'jobs.jsonl.gz'
        with gzip.open(jobs_path, 'wt') as stream:
            for row in jobs:
                stream.write(json.dumps(row) + '\n')
        selected = draws / 'selected.jsonl.gz'
        with gzip.open(selected, 'wt') as stream:
            stream.write('{"full_population_draw_retained":true}\n')
        plan = dict(schema='shift-night-reconstruction-plan-v1', campaign='test', tag='test_night',
                    response_contract_sha256='a' * 64, normalization_authority_sha256='b' * 64,
                    base_bundle_sha256='c' * 64,
                    output_base='/eos/user/j/jniedzie/shift_cmssw_support/ntuple_production/test',
                    epochs=[dict(epoch=1, q=.01, seed_hex256='d' * 64, population_originals=100,
                                 population_sources=2, draw_manifest_sha256='e' * 64,
                                 selection_ledger_path=str(selected), selection_ledger_sha256=sha(selected),
                                 jobs_path=str(jobs_path), jobs_sha256=sha(jobs_path), work_events=2,
                                 work_jobs=2, first_job=2000000, last_job=2000001)])
        plan_path = draws / 'night_plan.json'
        write_json(plan_path, plan)
        worker = root / 'worker'
        worker.mkdir()
        (worker / 'bootstrap.sh').write_text('# Code style: AI-code. Synthetic test worker.\n')
        write_json(worker / 'bootstrap.json', {'synthetic': True})
        template = '''# Code style: AI-code. Synthetic submit fixture.
universe = vanilla
initialdir = @SUPERVISOR_ROOT@
executable = @SUPERVISOR_ROOT@/worker/bootstrap.sh
+ShiftNightCampaign = "@CAMPAIGN_TAG@"
+ShiftNightWaveNonce = "@WAVE_NONCE@"
+ShiftNightAdmissionStartEpoch = @START_EPOCH@
+ShiftNightAdmissionStopEpoch = @STOP_EPOCH@
+ShiftNightAdmissionLeaseEpoch = @LEASE_EPOCH@
log = @EVENT_LOG@
queue job,epoch,source,count,seed,selection,selection_basename,selection_sha256,event_contract_sha256 from @JOB_LIST@
'''
        (worker / 'worker.sub.in').write_text(template)
        write_json(worker / 'worker_manifest.json', {'files': {name: sha(worker / name) for name in ('bootstrap.sh', 'bootstrap.json', 'worker.sub.in')}})
        supervisor = root / 'supervisor'
        prepare(plan_path, worker, supervisor, {'minimum_free_bytes': 0, 'minimum_free_files': 0,
                                               'reserved_bytes_per_worker': 1, 'reserved_files_per_worker': 1,
                                               'minimum_AFS_free_bytes': 0})
        return supervisor, jobs, plan

    def wave(self, supervisor):
        return next((supervisor / 'waves').glob('epoch01/*/intent.json')).parent

    def success(self, supervisor, job, plan):
        result = dict(job=job['job'], epoch=job['epoch'], exit_code=0, complete=True,
                      exact_double_event_audit_passed=True, events=job['count'],
                      validated_tier_events={key: job['count'] for key in ('GEN', 'SIM', 'DIGIHLT', 'RECO', 'NANO')},
                      response_contract_sha256=plan['response_contract_sha256'], analysis_release=False,
                      selection_sha256=job['selection_sha256'], event_contract_sha256=job['event_contract_sha256'],
                      source_gen_sha256=job['source_gen_sha256'])
        for name in ('nano', 'evidence', 'receipt'):
            result[name + '_path'] = plan['output_base'] + '/' + name
            result[name + '_sha256'] = result[name + '_readback_sha256'] = 'f' * 64
        write_json(supervisor / ('results/status%d.json' % job['job']), result)
        return result

    def test_daytime_never_submits_or_launches_a_quota_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            supervisor, _, _ = self.fixture(directory)
            backend = FakeBackend(supervisor)
            controller = NightController(supervisor, backend)
            controller.step(NOW - 8 * 3600)
            self.assertEqual(backend.calls, [])
            self.assertEqual(backend.observation_calls, 0)
            self.assertEqual(controller.state['phase'], 'waiting_for_night')

    def test_night_submits_an_exact_bounded_wave_with_expiring_start_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            supervisor, jobs, _ = self.fixture(directory)
            backend = FakeBackend(supervisor)
            controller = NightController(supervisor, backend)
            controller.step(NOW + 1)
            folder = self.wave(supervisor)
            intent = json.loads((folder / 'intent.json').read_text())
            self.assertEqual(intent['jobs'], [row['job'] for row in jobs])
            self.assertEqual(intent['submit_sha256'], sha(folder / 'wave.sub'))
            text = (folder / 'wave.sub').read_text()
            self.assertIn('ShiftNightAdmissionLeaseEpoch = %d' % (NOW + 181), text)
            self.assertNotIn('@STOP_EPOCH@', text)
            self.assertEqual(backend.calls[0][0], '/usr/bin/condor_submit')

    def test_lost_submit_response_does_not_duplicate_the_intended_jobs(self):
        with tempfile.TemporaryDirectory() as directory:
            supervisor, jobs, _ = self.fixture(directory)
            backend = FakeBackend(supervisor)
            backend.submit_error = True
            controller = NightController(supervisor, backend)
            with self.assertRaises(NativeCondorError):
                controller.step(NOW + 1)
            folder = self.wave(supervisor)
            nonce = read_json(folder / 'intent.json')['nonce']
            backend.rows = [dict(ClusterId=42, ProcId=i, JobStatus=2, ShiftNightJob=job['job'],
                                 ShiftNightWaveNonce=nonce, ShiftNightCampaign='test_night') for i, job in enumerate(jobs)]
            restarted = NightController(supervisor, backend)
            restarted.step(NOW + 61)
            self.assertEqual(sum(call[0] == '/usr/bin/condor_submit' for call in backend.calls), 1)
            self.assertEqual(read_json(folder / 'submission.json')['cluster'], 42)

    def test_unknown_submission_without_queue_or_event_log_proof_stays_reserved(self):
        with tempfile.TemporaryDirectory() as directory:
            supervisor, _, _ = self.fixture(directory)
            backend = FakeBackend(supervisor)
            backend.submit_error = True
            controller = NightController(supervisor, backend)
            with self.assertRaises(NativeCondorError):
                controller.step(NOW + 1)
            controller.step(NOW + 61)
            self.assertEqual(len(backend.calls), 1)
            self.assertEqual(controller.state['phase'], 'attention_required')
            self.assertEqual(len(controller.state['uncertain_submission_nonces']), 1)

    def test_valid_receipts_need_terminal_condor_success_before_epoch_advance(self):
        with tempfile.TemporaryDirectory() as directory:
            supervisor, jobs, plan = self.fixture(directory)
            backend = FakeBackend(supervisor)
            controller = NightController(supervisor, backend)
            controller.step(NOW + 1)
            for job in jobs:
                self.success(supervisor, job, plan)
            controller.step(NOW + 61)
            self.assertEqual(controller.state['completed_jobs'], 0)
            self.assertEqual(controller.state['completed_epochs'], [])
            (self.wave(supervisor) / 'events.log').write_text(''.join(
                '005 (42.%03d.000) 10/14 22:30:00 Job terminated.\n\t(1) Normal termination (return value 0)\n...\n' % i
                for i in range(2)))
            controller.step(NOW + 121)
            self.assertEqual(controller.state['completed_epochs'], [1])
            gate = read_json(supervisor / 'epoch_completions/epoch01.json')
            self.assertTrue(gate['operational_complete'])
            self.assertFalse(gate['analysis_release'])
            self.assertFalse(gate['normalization_ready'])
            self.assertTrue(NightController(supervisor, backend).step(NOW + 181))

    def test_missing_eos_readback_never_completes_a_job(self):
        with tempfile.TemporaryDirectory() as directory:
            supervisor, jobs, plan = self.fixture(directory)
            result = self.success(supervisor, jobs[0], plan)
            del result['nano_readback_sha256']
            self.assertIn('readback', validate_result(result, jobs[0], load_plan(supervisor.parent / 'draws/night_plan.json')))

    def test_stale_unprocessed_deferral_does_not_authorize_a_worker_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            supervisor, jobs, _ = self.fixture(directory)
            backend = FakeBackend(supervisor)
            controller = NightController(supervisor, backend)
            controller.step(NOW + 1)
            nonce = read_json(self.wave(supervisor) / 'intent.json')['nonce']
            backend.rows = [dict(ClusterId=42, ProcId=0, JobStatus=5, NumJobStarts=2,
                                 ShiftNightJob=jobs[0]['job'], ShiftNightWaveNonce=nonce,
                                 ShiftNightCampaign='test_night', HoldReason='Exit75')]
            write_json(supervisor / ('results/status%d.json' % jobs[0]['job']), dict(
                job=jobs[0]['job'], epoch=1, selection_sha256=jobs[0]['selection_sha256'],
                exit_code=75, night_admission_deferred=True, processed_events=0, condor_num_job_starts=1))
            controller.step(NOW + 61)
            self.assertFalse(any(call[0] == '/usr/bin/condor_release' for call in backend.calls))
            self.assertEqual(controller.state['phase'], 'attention_required')

    def test_running_jobs_are_never_edited_or_held_at_daybreak(self):
        with tempfile.TemporaryDirectory() as directory:
            supervisor, jobs, _ = self.fixture(directory)
            backend = FakeBackend(supervisor)
            controller = NightController(supervisor, backend)
            controller.step(NOW + 1)
            nonce = read_json(self.wave(supervisor) / 'intent.json')['nonce']
            backend.rows = [dict(ClusterId=42, ProcId=i, JobStatus=2, ShiftNightJob=job['job'],
                                 ShiftNightWaveNonce=nonce, ShiftNightCampaign='test_night') for i, job in enumerate(jobs)]
            backend.calls.clear()
            controller.step(NOW + 6 * 3600)
            self.assertEqual(backend.calls, [])
            self.assertEqual(controller.state['phase'], 'draining')

    def test_changed_native_double_cannot_keep_the_same_event_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            supervisor, jobs, _ = self.fixture(directory)
            plan = load_plan(supervisor.parent / 'draws/night_plan.json')
            jobs[0]['native_weights'][0] = 1.2345678806304932  # float32-rounding corruption.
            jobs_path = Path(plan['data']['epochs'][0]['jobs_path'])
            with gzip.open(jobs_path, 'wt') as stream:
                for row in jobs:
                    stream.write(json.dumps(row) + '\n')
            plan['data']['epochs'][0]['jobs_sha256'] = sha(jobs_path)
            with self.assertRaisesRegex(ValueError, 'event contract'):
                load_epoch(plan, 1)

    def test_duplicate_originals_across_jobs_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            supervisor, jobs, _ = self.fixture(directory)
            plan = load_plan(supervisor.parent / 'draws/night_plan.json')
            jobs[1]['source_gen_sha256'] = jobs[0]['source_gen_sha256']
            jobs[1]['event_ids'] = jobs[0]['event_ids']
            jobs[1]['event_contract_sha256'] = event_contract(jobs[1], plan['data']['response_contract_sha256'])
            jobs_path = Path(plan['data']['epochs'][0]['jobs_path'])
            with gzip.open(jobs_path, 'wt') as stream:
                for row in jobs:
                    stream.write(json.dumps(row) + '\n')
            plan['data']['epochs'][0]['jobs_sha256'] = sha(jobs_path)
            with self.assertRaisesRegex(ValueError, 'scheduled twice'):
                load_epoch(plan, 1)

    def test_incomplete_event_log_line_is_not_exit_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'events.log'
            path.write_text('005 (42.000.000) 10/14 22:30:00 Job terminated.\n\t(1) Normal termination (return value 0)\n')
            self.assertEqual(event_log(path)['terminal'], {})


def read_json(path):
    return json.loads(path.read_text())


if __name__ == '__main__':
    unittest.main()
