#!/usr/bin/env python3
# Code style: AI-code. AI-generated frozen scheduler deployment preparer for night reconstruction.
"""Prepare one separate supervisor without submitting jobs or changing scientific selections."""
import argparse
import json
from pathlib import Path
import shutil

from shift_night_plan import load_plan, pinned_path, sha
from shift_night_policy import validate_policy


def operational_gate_policy(plan, worker_directory, bootstrap):
    """Pin what a reviewed native guard proof and detector canary must establish."""
    authorities = {
        'night_plan_sha256': plan['sha256'],
        'response_contract_sha256': plan['data']['response_contract_sha256'],
        'base_bundle_sha256': plan['data']['base_bundle_sha256'],
        'science_runtime_sha256': bootstrap['bundle_sha256'],
        'science_worker_sha256': bootstrap['worker_sha256'],
    }
    worker_authorities = {**authorities,
        'worker_manifest_sha256': sha(worker_directory / 'worker_manifest.json'),
        'worker_bootstrap_sha256': sha(worker_directory / 'bootstrap.json'),
        'worker_launcher_sha256': sha(worker_directory / 'bootstrap.sh'),
        'worker_helper_sha256': sha(worker_directory / 'shift_night_worker.py')}
    return {'schema': 'shift-night-required-operational-gates-v1', 'gates': [
        {'name': 'native_admission_guard',
         'receipt': 'operational_gates/native_admission_guard.json',
         'expected_authorities': worker_authorities,
         'required_checks': ['native_chirp_readback_passed', 'pre_science_admission_guard_passed',
                             'deferred_execution_did_not_run_science'],
         'required_evidence': ['native_probe_ad', 'native_probe_report']},
        {'name': 'detector_publication_canary',
         'receipt': 'operational_gates/detector_publication_canary.json',
         'expected_authorities': authorities,
         'required_checks': ['exact_double_event_audit_passed', 'whole_file_publication_readback_passed',
                             'all_detector_tiers_complete', 'detached_manager_started',
                             'downstream_worker_execution_verified', 'persistent_working_directory_verified',
                             'supervisor_runtime_verified', 'detached_account_observation_passed'],
         'required_evidence': ['worker_status', 'native_terminal_ad']}]}


DEFAULT_POLICY = dict(
    timezone='Europe/Zurich', window_start='22:00', window_end='06:00',
    stop_launches_before_end_seconds=7200, worker_ceiling=1000, initial_workers=50,
    lease_seconds=180, poll_seconds=60, observation_max_age_seconds=600,
    growth_interval_seconds=1200, growth_minimum_completions=10,
    growth_max_idle_fraction=.25, minimum_free_bytes=50 * 1024**3, minimum_free_files=2000,
    reserved_bytes_per_worker=32 * 1024**2, reserved_files_per_worker=8,
    maximum_wave_jobs=100, minimum_AFS_free_bytes=512 * 1024**2,
    maximum_output_retrievals_per_poll=8, output_retrieval_time_budget_seconds=30,
    maximum_completed_reaps_per_poll=20, minimum_output_retrieval_reserve_bytes=2 * 1024**2,
    maximum_quarantined_computation_jobs=20, failure_rate_minimum_outcomes=20,
    maximum_computation_failure_fraction=.05,
    account_owner='jniedzie', account_pool='tweetybird04.cern.ch', schedd_name='bigbird26.cern.ch',
    tag='night_reconstruction', asynchronous_audits=True)


def prepare(plan_path, worker_directory, destination, overrides=None, retained_completions=None):
    plan = load_plan(plan_path)
    destination = Path(destination).resolve()
    worker_directory = Path(worker_directory).resolve()
    if destination.exists() or destination == worker_directory or destination in worker_directory.parents:
        raise ValueError('Use a new separate supervisor directory')
    if any(character.isspace() for character in str(destination)):
        raise ValueError('Condor deployment paths must not contain whitespace')
    policy = validate_policy({**DEFAULT_POLICY, **(overrides or {}), 'tag': plan['data']['tag']})
    worker_manifest_path = worker_directory / 'worker_manifest.json'
    worker_manifest = json.loads(worker_manifest_path.read_text())
    if not isinstance(worker_manifest.get('files'), dict) or not worker_manifest['files']:
        raise ValueError('Worker must provide an exact frozen file manifest')
    if not {'bootstrap.sh', 'bootstrap.json', 'worker.sub.in'} <= set(worker_manifest['files']):
        raise ValueError('Worker manifest is missing its executable, policy or submit template')
    if worker_manifest.get('night_plan_sha256') != plan['sha256']:
        raise ValueError('Worker was frozen against a different scientific draw plan')
    worker_files = []
    for name, expected in worker_manifest['files'].items():
        if Path(name).is_absolute() or '..' in Path(name).parts:
            raise ValueError('Worker manifest must contain only relative safe paths')
        worker_files.append((name, pinned_path(worker_directory, name, expected)))
    template = (worker_directory / 'worker.sub.in').read_text()
    for token in ('JOB_LIST', 'WAVE_NONCE', 'START_EPOCH', 'STOP_EPOCH', 'LEASE_EPOCH',
                  'EVENT_LOG', 'SUPERVISOR_ROOT', 'CAMPAIGN_TAG'):
        if '@' + token + '@' not in template:
            raise ValueError('Worker submit template lacks scheduling guard: ' + token)
    bootstrap = json.loads((worker_directory / 'bootstrap.json').read_text())
    for key in ('response_contract_sha256', 'base_bundle_sha256'):
        if bootstrap.get(key) != plan['data'][key]:
            raise ValueError('Worker bootstrap differs from its frozen scientific authority: ' + key)
    if bootstrap.get('night_plan_sha256') != plan['sha256']:
        raise ValueError('Worker bootstrap was frozen against another draw plan')
    destination.mkdir(parents=True, exist_ok=False)
    for folder in ('worker', 'results', 'waves', 'epoch_completions', 'spool_retrievals', 'scheduler_retirements',
                   'operational_gates'):
        (destination / folder).mkdir()
    if retained_completions is not None:
        shutil.copy2(retained_completions, destination / 'retained_completions.json')
    scripts = Path(__file__).resolve().parent
    for name in ('run_shift_night_reconstruction.py', 'shift_night_policy.py', 'shift_night_plan.py',
                 'shift_condor_native.py', 'run_shift_ntuple_controller.py'):
        shutil.copy2(scripts / name, destination / name)
    # The existing durable quota poller resolves this fixed sibling filename.
    # Freeze the night-specific, account-wide auditor under that name.
    shutil.copy2(scripts / 'run_shift_night_quota_audit.py', destination / 'run_shift_quota_audit.py')
    for name, path in worker_files:
        target = destination / 'worker' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    shutil.copy2(worker_manifest_path, destination / 'worker/worker_manifest.json')
    (destination / 'policy.json').write_text(json.dumps(policy, indent=2) + '\n')
    gates = operational_gate_policy(plan, worker_directory, bootstrap)
    (destination / 'required_operational_gates.json').write_text(json.dumps(gates, indent=2) + '\n')
    common = f'''# Code style: AI-code. AI-generated frozen scheduler-universe night supervisor.
universe = scheduler
initialdir = {destination}
executable = /usr/bin/python3
getenv = False
notification = Never
+MaxRuntime = 2592000
on_exit_remove = (ExitCode == 0)
on_exit_hold = (ExitBySignal == True) || (ExitCode == 78)
periodic_release = False
+ShiftNightController = true
+ShiftNightCampaign = "{plan['data']['tag']}"
'''
    for role in ('controller', 'watchdog'):
        arguments = str(destination / 'run_shift_night_reconstruction.py') + ' ' + str(destination)
        if role == 'watchdog':
            arguments += ' --watchdog'
        (destination / (role + '.sub')).write_text(common + f'''arguments = {arguments}
+JobBatchName = "{plan['data']['tag']}_{role}"
output = {role}.out
error = {role}.err
log = {role}.events
queue 1
''')
    frozen = {}
    for path in destination.rglob('*'):
        if path.is_file():
            frozen[str(path.relative_to(destination))] = sha(path)
    deployment = {'schema': 'shift-frozen-night-supervisor-v1', 'night_plan_path': str(plan['path']),
                  'night_plan_sha256': plan['sha256'], 'worker_manifest_sha256': sha(worker_manifest_path),
                  'files': frozen, 'prepared_only': True, 'analysis_release': False,
                  'normalization_ready': False,
                  'physics_limitation': 'Same V10 detector study response; parent-hadron transport is not qualified for physical closure.'}
    (destination / 'deployment.json').write_text(json.dumps(deployment, indent=2) + '\n')
    (destination / 'submission.json').write_text(json.dumps({'controller_cluster': None, 'watchdog_cluster': None}, indent=2) + '\n')
    return deployment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('night_plan', type=Path)
    parser.add_argument('worker_directory', type=Path)
    parser.add_argument('destination', type=Path)
    parser.add_argument('--policy-json', type=Path)
    parser.add_argument('--retained-completions', type=Path)
    args = parser.parse_args()
    overrides = json.loads(args.policy_json.read_text()) if args.policy_json else None
    result = prepare(args.night_plan, args.worker_directory, args.destination, overrides,
                     args.retained_completions)
    print(json.dumps({'destination': str(args.destination.resolve()), 'files': len(result['files']),
                      'night_plan_sha256': result['night_plan_sha256'], 'prepared_only': True}, indent=2))


if __name__ == '__main__':
    main()
