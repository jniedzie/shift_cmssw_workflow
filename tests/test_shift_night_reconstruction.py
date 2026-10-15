# Code style: AI-code. AI-generated regressions for durable night submission and release gates.
import gzip
import json
import re
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from prepare_shift_night_reconstruction import DEFAULT_POLICY, prepare
from run_shift_night_reconstruction import NativeBackend, NightController, event_log, operational_gates, source_round_robin, validate_result
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
        self.free_afs_bytes = 10**12
        self.returned_statuses = {}
        self.free_eos_bytes, self.free_eos_files = 10**15, 10**8

    def ads(self, constraint):
        return [dict(row) for row in self.rows]

    def observation(self):
        self.observation_calls += 1
        return dict(complete=True, account_coverage_verified=True, account_owner='jniedzie',
                    account_pool='tweetybird04.cern.ch', quota_checked_at_epoch=self.now,
                    account_checked_at_epoch=self.now, free_bytes=self.free_eos_bytes, free_files=self.free_eos_files,
                    account_workers=[])

    def afs_headroom(self):
        return self.free_afs_bytes

    def command(self, command, cwd=None, timeout=30):
        self.calls.append(command)
        if command[0] == '/usr/bin/condor_submit':
            if self.submit_error:
                raise NativeCondorError('Response lost after possible submission')
            count = len((Path(cwd) / 'jobs.txt').read_text().splitlines())
            return '42.0 - 42.%d\n' % (count - 1)
        if command[0] == '/usr/bin/condor_qedit':
            values = dict(zip(command[3::2], command[4::2]))
            identities = {(int(cluster), int(process)) for cluster, process in
                          re.findall(r'ClusterId == (\d+) && ProcId == (\d+)', command[2])}
            statuses = {int(status) for status in re.findall(r'JobStatus == (\d+)', command[2])}
            for row in self.rows:
                if (row['ClusterId'], row['ProcId']) in identities and row.get('JobStatus') in statuses:
                    row.update({key: int(value) for key, value in values.items()})
        elif command[0] == '/usr/bin/condor_release':
            identities = {(int(cluster), int(process)) for cluster, process in
                          re.findall(r'ClusterId == (\d+) && ProcId == (\d+)', command[2])}
            for row in self.rows:
                if (row['ClusterId'], row['ProcId']) in identities and row.get('JobStatus') == 5:
                    row['JobStatus'] = 1
        elif command[0] == '/usr/bin/condor_transfer_data':
            result = self.returned_statuses[command[1]]
            write_json(self.root / ('results/status%d.json' % result['job']), result)
            with tarfile.open(self.root / ('results/job%d_rescue_payload.tar.gz' % result['job']), 'w:gz'):
                pass
        elif command[0] == '/usr/bin/condor_rm':
            identity = command[1]
            self.rows = [row for row in self.rows if '%d.%d' % (row['ClusterId'], row['ProcId']) != identity]
            return 'Job %s marked for removal\n' % identity
        return ''


class NightReconstructionTests(unittest.TestCase):
    def test_detached_account_coverage_combines_local_address_with_worker_remote_audit(self):
        backend = NativeBackend(Path('/persistent'), dict(account_owner='jniedzie', schedd_name='local'))
        backend.local = True
        remote = dict(complete=True, remote_account_coverage_verified=True,
            excluded_local_schedd='local', remote_account_checked_at_epoch=100,
            remote_account_workers=[dict(ClusterId=8, ProcId=0)])
        with patch('run_shift_ntuple_controller.refresh_quota', return_value=remote), \
                patch('run_shift_night_reconstruction.query', return_value=[dict(ClusterId=9, ProcId=0)]) as query:
            result = backend.observation()
        self.assertTrue(result['account_coverage_verified'])
        self.assertEqual(len(result['account_workers']), 2)
        self.assertIsNone(query.call_args.kwargs['schedd'])
        self.assertTrue(query.call_args.kwargs['local_schedd'])
        self.assertEqual(result['account_checked_at_epoch'], 100)

    def test_execution_order_spreads_sources_without_changing_membership(self):
        jobs = [dict(job=0, source=1), dict(job=1, source=2), dict(job=2, source=2),
                dict(job=3, source=3), dict(job=4, source=3)]
        ordered = source_round_robin(jobs)
        self.assertEqual([job['job'] for job in ordered], [3, 1, 0, 4, 2])
        self.assertEqual({job['job'] for job in ordered}, {job['job'] for job in jobs})

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
        write_json(worker / 'bootstrap.json', {'synthetic': True, 'night_plan_sha256': sha(plan_path),
                   'bundle_sha256': '1' * 64, 'worker_sha256': '2' * 64,
                   'response_contract_sha256': plan['response_contract_sha256'],
                   'base_bundle_sha256': plan['base_bundle_sha256']})
        (worker / 'shift_night_worker.py').write_text('# Code style: AI-code. Synthetic worker helper.\n')
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
queue job,epoch,source,count,seed,selection,selection_basename,selection_sha,event_contract_sha,source_gen_sha from @JOB_LIST@
'''
        (worker / 'worker.sub.in').write_text(template)
        write_json(worker / 'worker_manifest.json', {'night_plan_sha256': sha(plan_path),
                   'files': {name: sha(worker / name) for name in ('bootstrap.sh', 'bootstrap.json', 'worker.sub.in', 'shift_night_worker.py')}})
        supervisor = root / 'supervisor'
        prepare(plan_path, worker, supervisor, {'minimum_free_bytes': 0, 'minimum_free_files': 0,
                                               'reserved_bytes_per_worker': 1, 'reserved_files_per_worker': 1,
                                               'minimum_AFS_free_bytes': 0})
        self.arm_gates(supervisor)
        return supervisor, jobs, plan

    def arm_gates(self, supervisor):
        policy = read_json(supervisor / 'required_operational_gates.json')
        evidence = supervisor / 'operational_gates/synthetic_native_evidence.json'
        write_json(evidence, {'synthetic_native_probe': True})
        for gate in policy['gates']:
            write_json(supervisor / gate['receipt'], {
                'schema': 'shift-night-operational-gate-v1', 'gate': gate['name'], 'passed': True,
                'authorities': gate['expected_authorities'],
                'checks': {name: True for name in gate['required_checks']},
                'evidence': {name: {'path': str(evidence), 'sha256': sha(evidence)}
                             for name in gate['required_evidence']}})

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

    def second_epoch(self, supervisor, jobs, plan):
        draws = supervisor.parent / 'draws'
        later = []
        for index, old in enumerate(jobs):
            row = {**old, 'job': old['job'] + 100000, 'epoch': 2,
                   'seed': old['seed'] + 10000000, 'raw_indices': [index + 2],
                   'event_ids': [[index + 1, 1, 3]]}
            selection = draws / ('later_selection%d.json' % index)
            write_json(selection, {'later_original': index})
            row.update(selection_path=str(selection), selection_sha256=sha(selection))
            row['event_contract_sha256'] = event_contract(row, plan['response_contract_sha256'])
            later.append(row)
        path = draws / 'later_jobs.jsonl.gz'
        with gzip.open(path, 'wt') as stream:
            for row in later:
                stream.write(json.dumps(row) + '\n')
        ledger = draws / 'later_selected.jsonl.gz'
        with gzip.open(ledger, 'wt') as stream:
            stream.write('{"independent_later_selection":true}\n')
        metadata = {**plan['epochs'][0], 'epoch': 2, 'seed_hex256': 'f' * 64,
                    'draw_manifest_sha256': '0' * 64, 'jobs_path': str(path), 'jobs_sha256': sha(path),
                    'selection_ledger_path': str(ledger), 'selection_ledger_sha256': sha(ledger),
                    'first_job': later[0]['job'], 'last_job': later[-1]['job']}
        plan['epochs'].append(metadata)
        write_json(draws / 'night_plan.json', plan)
        deployment = read_json(supervisor / 'deployment.json')
        deployment['night_plan_sha256'] = sha(draws / 'night_plan.json')
        policy_path = supervisor / 'required_operational_gates.json'
        policy = read_json(policy_path)
        for gate in policy['gates']:
            gate['expected_authorities']['night_plan_sha256'] = deployment['night_plan_sha256']
        write_json(policy_path, policy)
        deployment['files']['required_operational_gates.json'] = sha(policy_path)
        write_json(supervisor / 'deployment.json', deployment)
        self.arm_gates(supervisor)
        return later

    def computation_failure(self, supervisor, job, plan):
        result = self.success(supervisor, job, plan)
        result.update(exit_code=1, complete=False, failure_kind='computation',
                      resolved_response_config_passed=True)
        result['validated_tier_events']['NANO'] = 0
        result.update(reco_path=plan['output_base'] + '/failure/step3.root',
                      reco_sha256='f' * 64, reco_readback_sha256='f' * 64)
        write_json(supervisor / ('results/status%d.json' % job['job']), result)
        return result

    def spooled_ad(self, supervisor, job, process=0, **overrides):
        nonce = read_json(self.wave(supervisor) / 'intent.json')['nonce']
        return dict(ClusterId=42, ProcId=process, Owner='jniedzie', JobStatus=4,
                    JobUniverse=5, ShiftNightJob=job['job'], ShiftNightEpoch=job['epoch'],
                    ShiftNightWaveNonce=nonce, ShiftNightCampaign='test_night',
                    ShiftNightReturnedBytes=2**20, ExitCode=0, ExitBySignal=False, **overrides)

    def test_daytime_never_submits_or_launches_a_quota_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            supervisor, _, _ = self.fixture(directory)
            backend = FakeBackend(supervisor)
            controller = NightController(supervisor, backend)
            controller.step(NOW - 8 * 3600)
            self.assertEqual(backend.calls, [])
            self.assertEqual(backend.observation_calls, 0)
            self.assertEqual(controller.state['phase'], 'waiting_for_night')

    def test_retained_final_report_failure_reuses_science_without_resubmission(self):
        with tempfile.TemporaryDirectory() as directory:
            supervisor, jobs, plan = self.fixture(directory)
            result = self.success(supervisor, jobs[0], plan)
            result.update(chirp_reporting_error='TimeoutExpired final progress', durable_EOS_publication_passed=True)
            status = supervisor / ('results/status%d.json' % jobs[0]['job'])
            write_json(status, result)
            retrieval = supervisor / 'retained_retrieval.json'
            write_json(retrieval, {'download_complete': True, 'status_sha256': sha(status), 'rescue_members': [],
                'completed_ad': dict(ClusterId=12, ProcId=0, JobStatus=4, ExitCode=73, ExitBySignal=False,
                    ShiftNightJob=jobs[0]['job'], ShiftNightSelectionSHA256=jobs[0]['selection_sha256'])})
            retained = supervisor / 'retained_completions.json'
            write_json(retained, {'plan_sha256': sha(supervisor.parent / 'draws/night_plan.json'),
                'completions': [dict(job=jobs[0]['job'], epoch=1, status_path=str(status), status_sha256=sha(status),
                                    retrieval_path=str(retrieval), retrieval_sha256=sha(retrieval))]})
            deployment = read_json(supervisor / 'deployment.json')
            deployment['files']['retained_completions.json'] = sha(retained)
            write_json(supervisor / 'deployment.json', deployment)
            backend = FakeBackend(supervisor)
            controller = NightController(supervisor, backend)
            controller.step(NOW + 1)
            self.assertEqual(controller.state['completed_jobs'], 1)
            self.assertEqual(read_json(self.wave(supervisor) / 'intent.json')['jobs'], [jobs[1]['job']])

    def test_prepared_supervisor_has_explicit_long_runtime_and_persistent_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            supervisor, _, _ = self.fixture(directory)
            for role in ('controller', 'watchdog'):
                text = (supervisor / (role + '.sub')).read_text()
                self.assertIn('initialdir = ' + str(supervisor), text)
                self.assertIn('+MaxRuntime = 2592000', text)

    def test_transient_input_spooling_hold_is_reserved_without_science_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            supervisor, jobs, _ = self.fixture(directory)
            backend = FakeBackend(supervisor)
            controller = NightController(supervisor, backend)
            controller.step(NOW)
            nonce = read_json(self.wave(supervisor) / 'intent.json')['nonce']
            backend.rows = [dict(ClusterId=42, ProcId=0, Owner='jniedzie', JobStatus=5,
                ShiftNightJob=jobs[0]['job'], ShiftNightEpoch=1, ShiftNightWaveNonce=nonce,
                HoldReason='Spooling input data files', NumJobStarts=0,
                ShiftNightScienceStarted=False)]
            controller.step(NOW + 60)
            self.assertEqual(controller.state['failed_jobs'], {})
            self.assertEqual(controller.state['submitted_jobs'], 2)

    def test_missing_operational_gate_receipt_blocks_science_but_keeps_controller_alive(self):
        with tempfile.TemporaryDirectory() as directory:
            supervisor, _, _ = self.fixture(directory)
            (supervisor / 'operational_gates/native_admission_guard.json').unlink()
            backend = FakeBackend(supervisor)
            controller = NightController(supervisor, backend)
            controller.step(NOW + 1)
            self.assertEqual(backend.calls, [])
            self.assertFalse(controller.state['operational_gates_passed'])
            self.assertEqual(controller.state['phase'], 'waiting_for_operational_validation')
            self.assertEqual(controller.state['capacity']['available_new_slots'], 0)
            self.arm_gates(supervisor)
            controller.step(NOW + 61)
            self.assertTrue(controller.state['operational_gates_passed'])
            self.assertTrue(any(call[0] == '/usr/bin/condor_submit' for call in backend.calls))

    def test_false_or_wrong_worker_native_guard_receipt_never_arms_science(self):
        with tempfile.TemporaryDirectory() as directory:
            supervisor, _, _ = self.fixture(directory)
            receipt_path = supervisor / 'operational_gates/native_admission_guard.json'
            receipt = read_json(receipt_path)
            receipt['passed'] = False
            write_json(receipt_path, receipt)
            self.assertFalse(operational_gates(supervisor)[0])
            receipt['passed'] = True
            receipt['authorities']['worker_helper_sha256'] = '0' * 64
            write_json(receipt_path, receipt)
            self.assertFalse(operational_gates(supervisor)[0])

    def test_changed_evidence_or_unproved_check_closes_an_existing_operational_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            supervisor, _, _ = self.fixture(directory)
            receipt_path = supervisor / 'operational_gates/native_admission_guard.json'
            receipt = read_json(receipt_path)
            receipt['checks']['pre_science_admission_guard_passed'] = False
            write_json(receipt_path, receipt)
            self.assertFalse(operational_gates(supervisor)[0])
            self.arm_gates(supervisor)
            (supervisor / 'operational_gates/synthetic_native_evidence.json').write_text('changed evidence')
            self.assertFalse(operational_gates(supervisor)[0])

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
            self.assertIn('-spool', backend.calls[0])

    def test_native_spool_bytes_allow_single_job_retrieval_and_exact_retirement(self):
        with tempfile.TemporaryDirectory() as directory:
            supervisor, jobs, plan = self.fixture(directory)
            backend = FakeBackend(supervisor)
            controller = NightController(supervisor, backend)
            controller.step(NOW + 1)
            result = self.success(supervisor, jobs[0], plan)
            (supervisor / ('results/status%d.json' % jobs[0]['job'])).unlink()
            backend.returned_statuses['42.0'] = result
            ad = self.spooled_ad(supervisor, jobs[0])
            del ad['ShiftNightReturnedBytes']
            ad['TransferOutputStats'] = {'CedarSizeBytesTotal': 1234, 'CedarSizeBytesLastRun': 1200}
            backend.rows = [ad]
            controller.step(NOW + 61)
            receipt = read_json(supervisor / ('spool_retrievals/job%d.json' % jobs[0]['job']))
            self.assertTrue(receipt['download_complete'])
            self.assertEqual(receipt['completed_ad']['TransferOutputStats']['CedarSizeBytesTotal'], 1234)
            self.assertEqual(controller.state['completed_jobs'], 1)
            self.assertIn(['/usr/bin/condor_transfer_data', '42.0'], backend.calls)
            self.assertIn(['/usr/bin/condor_rm', '42.0'], backend.calls)
            retirement = read_json(supervisor / ('scheduler_retirements/job%d.json' % jobs[0]['job']))
            self.assertFalse(retirement['physics_outputs_removed'])
            self.assertTrue(retirement['acknowledged'])

    def test_unknown_spooled_output_size_keeps_payload_and_stops_admissions(self):
        with tempfile.TemporaryDirectory() as directory:
            supervisor, jobs, _ = self.fixture(directory)
            backend = FakeBackend(supervisor)
            controller = NightController(supervisor, backend)
            controller.step(NOW + 1)
            ad = self.spooled_ad(supervisor, jobs[0])
            del ad['ShiftNightReturnedBytes']
            backend.rows = [ad]
            backend.calls.clear()
            controller.step(NOW + 61)
            self.assertEqual(backend.calls, [])
            self.assertIn(jobs[0]['job'], controller.state['spool_retrieval_blocked'])
            self.assertEqual(controller.state['phase'], 'attention_required')
            self.assertEqual(controller.state['capacity']['available_new_slots'], 0)

    def test_large_failure_rescue_stays_spooled_when_afs_cannot_reserve_it(self):
        with tempfile.TemporaryDirectory() as directory:
            supervisor, jobs, _ = self.fixture(directory)
            backend = FakeBackend(supervisor)
            controller = NightController(supervisor, backend)
            controller.step(NOW + 1)
            ad = self.spooled_ad(supervisor, jobs[0])
            ad.update(ShiftNightReturnedBytes=2**30, ShiftNightFailureKind='publication', ExitCode=1)
            backend.rows = [ad]
            backend.free_afs_bytes = 2**29
            backend.calls.clear()
            controller.step(NOW + 61)
            self.assertEqual(backend.calls, [])
            self.assertIn(jobs[0]['job'], controller.state['failed_jobs'])
            self.assertIn(jobs[0]['job'], controller.state['spool_retrieval_blocked'])
            self.assertEqual(controller.state['capacity']['available_new_slots'], 0)

    def test_calendar_deferral_uses_fresh_spooled_ad_proof_without_downloading_held_job(self):
        with tempfile.TemporaryDirectory() as directory:
            supervisor, jobs, _ = self.fixture(directory)
            backend = FakeBackend(supervisor)
            controller = NightController(supervisor, backend)
            controller.step(NOW + 1)
            ad = self.spooled_ad(supervisor, jobs[0])
            ad.update(JobStatus=5, ExitCode=75, NumJobStarts=2, HoldReason='Exit75',
                      ShiftNightAdmissionDeferred=True, ShiftNightProcessedEvents=0,
                      ShiftNightDeferredNumJobStarts=2,
                      ShiftNightSelectionSHA256=jobs[0]['selection_sha256'],
                      ShiftNightEventContractSHA256=jobs[0]['event_contract_sha256'])
            backend.rows = [ad]
            controller.step(NOW + 61)
            self.assertTrue(any(call[0] == '/usr/bin/condor_release' for call in backend.calls))
            self.assertFalse(any(call[0] == '/usr/bin/condor_transfer_data' for call in backend.calls))

    def test_failure_output_is_never_retired_from_the_scheduler(self):
        with tempfile.TemporaryDirectory() as directory:
            supervisor, jobs, plan = self.fixture(directory)
            backend = FakeBackend(supervisor)
            controller = NightController(supervisor, backend)
            controller.step(NOW + 1)
            result = self.computation_failure(supervisor, jobs[0], plan)
            (supervisor / ('results/status%d.json' % jobs[0]['job'])).unlink()
            backend.returned_statuses['42.0'] = result
            ad = self.spooled_ad(supervisor, jobs[0])
            ad.update(ShiftNightFailureKind='computation', ExitCode=1)
            backend.rows = [ad]
            controller.step(NOW + 61)
            self.assertIn(jobs[0]['job'], controller.state['quarantined_computation_jobs'])
            self.assertFalse(any(call[0] == '/usr/bin/condor_rm' for call in backend.calls))

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

    def test_only_proved_unprocessed_running_bootstrap_gets_a_live_lease(self):
        with tempfile.TemporaryDirectory() as directory:
            supervisor, jobs, plan = self.fixture(directory)
            backend = FakeBackend(supervisor)
            controller = NightController(supervisor, backend)
            controller.step(NOW + 1)
            first = self.spooled_ad(supervisor, jobs[0])
            first.update(JobStatus=2, ShiftNightScienceStarted=False, ShiftNightBootstrapInProgress=True,
                         ShiftNightProcessedEvents=0, ShiftNightSelectionSHA256=jobs[0]['selection_sha256'],
                         ShiftNightEventContractSHA256=jobs[0]['event_contract_sha256'],
                         ShiftNightSourceGENSHA256=jobs[0]['source_gen_sha256'],
                         ShiftNightResponseContractSHA256=plan['response_contract_sha256'])
            second = self.spooled_ad(supervisor, jobs[1], process=1)
            second.update(JobStatus=2, ShiftNightScienceStarted=True, ShiftNightBootstrapInProgress=False)
            backend.rows = [first, second]
            backend.calls.clear()
            controller.step(NOW + 61)
            edits = [call for call in backend.calls if call[0] == '/usr/bin/condor_qedit']
            self.assertEqual(len(edits), 1)
            condition = edits[0][2]
            self.assertIn('ProcId == 0', condition)
            self.assertNotIn('ProcId == 1', condition)
            self.assertIn('ShiftNightScienceStarted == false', condition)
            self.assertIn('ShiftNightProcessedEvents == 0', condition)
            self.assertIn(jobs[0]['source_gen_sha256'], condition)
            self.assertEqual(edits[0][-2:], ['ShiftNightAdmissionLeaseEpoch', str(NOW + 241)])

    def test_running_bootstrap_with_wrong_source_cannot_get_new_permission(self):
        with tempfile.TemporaryDirectory() as directory:
            supervisor, jobs, plan = self.fixture(directory)
            backend = FakeBackend(supervisor)
            controller = NightController(supervisor, backend)
            controller.step(NOW + 1)
            ad = self.spooled_ad(supervisor, jobs[0])
            ad.update(JobStatus=2, ShiftNightScienceStarted=False, ShiftNightBootstrapInProgress=True,
                      ShiftNightProcessedEvents=0, ShiftNightSelectionSHA256=jobs[0]['selection_sha256'],
                      ShiftNightEventContractSHA256=jobs[0]['event_contract_sha256'],
                      ShiftNightSourceGENSHA256='0' * 64,
                      ShiftNightResponseContractSHA256=plan['response_contract_sha256'])
            backend.rows = [ad]
            backend.calls.clear()
            with self.assertRaisesRegex(ValueError, 'pinned'):
                controller.step(NOW + 61)
            self.assertEqual(backend.calls, [])

    def test_quota_shrink_releases_only_one_of_two_proved_pre_science_deferrals(self):
        with tempfile.TemporaryDirectory() as directory:
            supervisor, jobs, _ = self.fixture(directory)
            backend = FakeBackend(supervisor)
            controller = NightController(supervisor, backend)
            controller.step(NOW + 1)
            backend.rows = []
            for index, job in enumerate(jobs):
                ad = self.spooled_ad(supervisor, job, process=index)
                ad.update(JobStatus=5, NumJobStarts=1, HoldReason='Exit75', ShiftNightAdmissionDeferred=True,
                          ShiftNightProcessedEvents=0, ShiftNightDeferredNumJobStarts=1,
                          ShiftNightSelectionSHA256=job['selection_sha256'],
                          ShiftNightEventContractSHA256=job['event_contract_sha256'])
                backend.rows.append(ad)
            backend.free_eos_bytes = backend.free_eos_files = 1
            backend.calls.clear()
            controller.step(NOW + 61)
            self.assertEqual(controller.state['quota_worker_limit'], 1)
            releases = [call for call in backend.calls if call[0] == '/usr/bin/condor_release']
            self.assertEqual(len(releases), 1)
            self.assertIn('ProcId == 0', releases[0][2])
            self.assertEqual(backend.rows[1]['JobStatus'], 5)
            self.assertFalse(any(call[0] == '/usr/bin/condor_submit' for call in backend.calls))

    def test_quota_shrink_invalidates_only_excess_bootstrap_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            supervisor, jobs, plan = self.fixture(directory)
            backend = FakeBackend(supervisor)
            controller = NightController(supervisor, backend)
            controller.step(NOW + 1)
            backend.rows = []
            for index, job in enumerate(jobs):
                ad = self.spooled_ad(supervisor, job, process=index)
                ad.update(JobStatus=2, ShiftNightScienceStarted=False, ShiftNightBootstrapInProgress=True,
                          ShiftNightProcessedEvents=0, ShiftNightSelectionSHA256=job['selection_sha256'],
                          ShiftNightEventContractSHA256=job['event_contract_sha256'],
                          ShiftNightSourceGENSHA256=job['source_gen_sha256'],
                          ShiftNightResponseContractSHA256=plan['response_contract_sha256'])
                backend.rows.append(ad)
            backend.free_eos_bytes = backend.free_eos_files = 1
            backend.calls.clear()
            controller.step(NOW + 61)
            self.assertEqual(backend.rows[0]['ShiftNightAdmissionLeaseEpoch'], NOW + 241)
            self.assertEqual(backend.rows[1]['ShiftNightAdmissionLeaseEpoch'], 0)
            self.assertFalse(any(call[0] in ('/usr/bin/condor_hold', '/usr/bin/condor_rm', '/usr/bin/condor_release')
                                 for call in backend.calls))

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

    def test_slow_last_worker_does_not_block_later_immutable_epoch(self):
        with tempfile.TemporaryDirectory() as directory:
            supervisor, jobs, plan = self.fixture(directory)
            later = self.second_epoch(supervisor, jobs, plan)
            backend = FakeBackend(supervisor)
            controller = NightController(supervisor, backend)
            controller.step(NOW + 1)
            nonce = read_json(self.wave(supervisor) / 'intent.json')['nonce']
            backend.rows = [dict(ClusterId=42, ProcId=0, JobStatus=2, ShiftNightJob=jobs[0]['job'],
                                 ShiftNightWaveNonce=nonce, ShiftNightCampaign='test_night')]
            self.success(supervisor, jobs[1], plan)
            (self.wave(supervisor) / 'events.log').write_text(
                '005 (42.001.000) 10/14 22:30:00 Job terminated.\n\t(1) Normal termination (return value 0)\n...\n')
            controller.step(NOW + 61)
            wave = next((supervisor / 'waves/epoch02').glob('*/intent.json'))
            self.assertEqual(read_json(wave)['jobs'], [row['job'] for row in later])
            self.assertEqual(controller.state['completed_epochs'], [])
            self.assertEqual(controller.state['highest_admitted_epoch'], 2)
            self.assertFalse(any(call[0] in ('/usr/bin/condor_hold', '/usr/bin/condor_release') for call in backend.calls))

    def test_quarantined_computation_failure_allows_later_completion_but_no_prefix(self):
        with tempfile.TemporaryDirectory() as directory:
            supervisor, jobs, plan = self.fixture(directory)
            later = self.second_epoch(supervisor, jobs, plan)
            backend = FakeBackend(supervisor)
            controller = NightController(supervisor, backend)
            controller.step(NOW + 1)
            first = self.wave(supervisor)
            self.computation_failure(supervisor, jobs[0], plan)
            self.success(supervisor, jobs[1], plan)
            (first / 'events.log').write_text(''.join(
                '005 (42.%03d.000) 10/14 22:30:00 Job terminated.\n\t(1) Normal termination (return value %d)\n...\n' % (i, 1 if i == 0 else 0)
                for i in range(2)))
            controller.step(NOW + 61)
            self.assertIn(jobs[0]['job'], controller.state['quarantined_computation_jobs'])
            second = next((supervisor / 'waves/epoch02').glob('*/intent.json')).parent
            for job in later:
                self.success(supervisor, job, plan)
            (second / 'events.log').write_text(''.join(
                '005 (42.%03d.000) 10/14 22:40:00 Job terminated.\n\t(1) Normal termination (return value 0)\n...\n' % i
                for i in range(2)))
            controller.step(NOW + 121)
            self.assertEqual(controller.state['completed_epochs'], [2])
            self.assertEqual(controller.state['completed_prefix_epochs'], 0)
            self.assertFalse(controller.state['analysis_release'])
            self.assertFalse(any(call[0] == '/usr/bin/condor_release' for call in backend.calls))

    def test_publication_failure_stops_new_waves_without_killing_running_workers(self):
        with tempfile.TemporaryDirectory() as directory:
            supervisor, jobs, plan = self.fixture(directory)
            self.second_epoch(supervisor, jobs, plan)
            backend = FakeBackend(supervisor)
            controller = NightController(supervisor, backend)
            controller.step(NOW + 1)
            result = self.computation_failure(supervisor, jobs[0], plan)
            result['failure_kind'] = 'publication'
            write_json(supervisor / ('results/status%d.json' % jobs[0]['job']), result)
            (self.wave(supervisor) / 'events.log').write_text(
                '005 (42.000.000) 10/14 22:30:00 Job terminated.\n\t(1) Normal termination (return value 1)\n...\n')
            backend.calls.clear()
            controller.step(NOW + 61)
            self.assertEqual(backend.calls, [])
            self.assertEqual(controller.state['phase'], 'attention_required')
            self.assertIn(jobs[0]['job'], controller.state['failed_jobs'])

    def test_computation_failure_burst_stops_admissions(self):
        with tempfile.TemporaryDirectory() as directory:
            supervisor, jobs, plan = self.fixture(directory)
            self.second_epoch(supervisor, jobs, plan)
            backend = FakeBackend(supervisor)
            controller = NightController(supervisor, backend)
            controller.policy['maximum_quarantined_computation_jobs'] = 1
            controller.step(NOW + 1)
            for job in jobs:
                self.computation_failure(supervisor, job, plan)
            (self.wave(supervisor) / 'events.log').write_text(''.join(
                '005 (42.%03d.000) 10/14 22:30:00 Job terminated.\n\t(1) Normal termination (return value 1)\n...\n' % i
                for i in range(2)))
            backend.calls.clear()
            controller.step(NOW + 61)
            self.assertTrue(controller.state['computation_failure_burst'])
            self.assertEqual(backend.calls, [])


def read_json(path):
    return json.loads(path.read_text())


if __name__ == '__main__':
    unittest.main()
