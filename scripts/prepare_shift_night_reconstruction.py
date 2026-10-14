#!/usr/bin/env python3
# Code style: AI-code. AI-generated frozen scheduler deployment preparer for night reconstruction.
"""Prepare one separate supervisor without submitting jobs or changing scientific selections."""
import argparse
import json
from pathlib import Path
import shutil

from shift_night_plan import load_plan, pinned_path, sha
from shift_night_policy import validate_policy


DEFAULT_POLICY = dict(
    timezone='Europe/Zurich', window_start='22:00', window_end='06:00',
    stop_launches_before_end_seconds=7200, worker_ceiling=1000, initial_workers=50,
    lease_seconds=180, poll_seconds=60, observation_max_age_seconds=600,
    growth_interval_seconds=1200, growth_minimum_completions=10,
    growth_max_idle_fraction=.25, minimum_free_bytes=50 * 1024**3, minimum_free_files=20000,
    reserved_bytes_per_worker=128 * 1024**2, reserved_files_per_worker=8,
    maximum_wave_jobs=100, minimum_AFS_free_bytes=512 * 1024**2,
    account_owner='jniedzie', account_pool='tweetybird04.cern.ch', schedd_name='bigbird26.cern.ch',
    tag='night_reconstruction', asynchronous_audits=True)


def prepare(plan_path, worker_directory, destination, overrides=None):
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
    destination.mkdir(parents=True, exist_ok=False)
    for folder in ('worker', 'results', 'waves', 'epoch_completions'):
        (destination / folder).mkdir()
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
    common = f'''# Code style: AI-code. AI-generated frozen scheduler-universe night supervisor.
universe = scheduler
initialdir = {destination}
executable = /usr/bin/python3
getenv = False
notification = Never
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
    args = parser.parse_args()
    overrides = json.loads(args.policy_json.read_text()) if args.policy_json else None
    result = prepare(args.night_plan, args.worker_directory, args.destination, overrides)
    print(json.dumps({'destination': str(args.destination.resolve()), 'files': len(result['files']),
                      'night_plan_sha256': result['night_plan_sha256'], 'prepared_only': True}, indent=2))


if __name__ == '__main__':
    main()
