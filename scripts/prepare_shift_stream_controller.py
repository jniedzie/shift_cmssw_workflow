#!/usr/bin/env python3
# Code style: AI-code. AI-generated immutable streaming controller deployment.
"""Freeze controller helpers and persistent scheduler descriptions; never submit."""
import argparse
import json
from pathlib import Path
import shutil

from run_shift_stream_controller import (SUPERVISOR_OVERRIDES, load_tasks, read,
    save, sha, stage_publication_bounds, stream_admission_policy)


HELPERS = ('run_shift_stream_controller.py', 'run_mu_enriched_qcd_admission.py',
    'run_shift_ntuple_controller.py',
    'shift_condor_native.py', 'read_shift_external_storage_commitments.py')


def prepare(destination, policy, extra_files=None, helper_sources=None):
    destination = Path(destination).resolve()
    if (destination.exists() or str(destination).startswith(('/tmp/', '/pool/condor/dir_'))
            or any(c.isspace() for c in str(destination))):
        raise ValueError('Use a new persistent absolute deployment directory without whitespace')
    admission = stream_admission_policy(policy)
    stage_publication_bounds(policy, admission)
    if policy['tag'] != admission['suite_tag']:
        raise ValueError('Controller and admission identities differ')
    if policy.get('manager_runtime_seconds', 604800) < 604800:
        raise ValueError('Stream managers need at least seven days including downstream queue time')
    submissions = policy.get('maximum_submissions_per_poll', 4)
    if type(submissions) is not int or not 1 <= submissions <= admission['worker_ceiling']:
        raise ValueError('Submissions per poll need a positive integer within the worker ceiling')
    if type(policy.get('isolate_failed_cohorts', False)) is not bool:
        raise ValueError('isolate_failed_cohorts needs an explicit boolean')
    for key in ('queue_timeout_seconds', 'worker_timeout_seconds', 'setup_timeout_seconds'):
        if type(policy.get('worker_limits', {}).get(key)) is not int or policy['worker_limits'][key] <= 0:
            raise ValueError('Explicit positive worker limit required: ' + key)
    destination.mkdir(parents=True, exist_ok=False)
    for name in ('admission', 'tasks', 'cohorts', 'operational_gates'):
        (destination / name).mkdir()
    scripts = Path(__file__).resolve().parent
    helper_sources = helper_sources or {}
    if set(helper_sources) - set(HELPERS) - {'run_shift_quota_audit.py'}:
        raise ValueError('Explicit helper sources may select only known controller helpers')
    for name in HELPERS:
        shutil.copy2(helper_sources.get(name, scripts / name), destination / name)
    # Admission requires the split local/remote account observation and sealed
    # external storage commitments. The older general ntuple auditor returns
    # a different schema and can never satisfy that guard. Freeze the same
    # maintained helper used by the proven GEN/night deployment, under the
    # sibling filename expected by launch_quota_audit().
    shutil.copy2(helper_sources.get('run_shift_quota_audit.py', scripts/'run_shift_night_quota_audit.py'),
                 destination/'run_shift_quota_audit.py')
    for name, source in (extra_files or {}).items():
        relative = Path(name)
        if (relative.is_absolute() or '..' in relative.parts or name in HELPERS
                or name == 'run_shift_quota_audit.py'):
            raise ValueError('Extra frozen files need safe, distinct relative names')
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    save(destination / 'policy.json', policy)
    save(destination / 'admission/policy.json', admission)
    common = f'''# Code style: AI-code. AI-generated persistent streaming scheduler handoff.
universe = scheduler
initialdir = {destination}
executable = /usr/bin/python3
getenv = False
notification = Never
+SendCredential = True
+MaxRuntime = {policy.get('manager_runtime_seconds', 604800)}
+ShiftStreamCampaign = {json.dumps(policy['tag'])}
+ShiftStreamController = true
on_exit_hold = (ExitBySignal == True) || (ExitCode != 0)
periodic_release = False
'''
    for role in ('controller', 'watchdog'):
        suffix = ' --watchdog' if role == 'watchdog' else ''
        (destination / (role + '.sub')).write_text(common + f'''arguments = {destination}/run_shift_stream_controller.py {destination}{suffix}
+ShiftStreamWatchdog = {str(role == 'watchdog').lower()}
+JobBatchName = "{policy['tag']}_{role}"
output = {role}.out
error = {role}.err
log = {role}.events
queue 1
''')
    files = {str(path.relative_to(destination)): sha(path)
             for path in destination.rglob('*') if path.is_file()}
    deployment = {'schema': 'shift-stream-controller-deployment-v1', 'files': files,
        'prepared_only': True, 'analysis_release': False, 'normalization_ready': False}
    save(destination / 'deployment.json', deployment)
    save(destination / 'submission.json', {'controller_cluster': None, 'watchdog_cluster': None})
    return deployment


def adopt(destination, campaign, overrides=None, extra_files=None, helper_sources=None):
    """Freeze new control code while preserving every existing task and nonce.

    Preparation is read-only for the old campaign. Its controller and watchdog
    must be stopped and verified absent before the replacement is submitted.
    Both generations use the same campaign locks as an additional safeguard.
    """
    campaign = Path(campaign).resolve()
    changes = overrides or {}
    if set(changes) - set(SUPERVISOR_OVERRIDES):
        raise ValueError('Supervisor adoption cannot change worker or admission settings')
    policy = read(campaign / 'policy.json')
    if policy.get('campaign_directory'):
        raise ValueError('Adopt the original campaign state directory')
    ledger = read(campaign / 'ledger.json')
    tasks = load_tasks(campaign, ledger)
    policy.update(changes)
    policy.update(campaign_directory=str(campaign), adoption={
        'schema': 'shift-stream-supervisor-adoption-v1',
        'campaign_policy_sha256': sha(campaign / 'policy.json'),
        'campaign_deployment_sha256': sha(campaign / 'deployment.json'),
        'ledger_sha256_at_preparation': sha(campaign / 'ledger.json'),
        'task_manifest_count_at_preparation': len(tasks),
        'science_inputs_changed': 'selection_revision' in changes,
        'existing_task_inputs_changed': False, 'submission_performed': False})
    return prepare(destination, policy, extra_files, helper_sources)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    authority = parser.add_mutually_exclusive_group(required=True)
    authority.add_argument('--policy', type=Path)
    authority.add_argument('--adopt-campaign', type=Path,
        help='Original persistent campaign state; stop its old supervisors before submitting')
    parser.add_argument('--supervisor-overrides', type=Path,
        help='Optional scheduling-only JSON changes when adopting an existing campaign')
    parser.add_argument('--extra-files', type=Path,
        help='JSON mapping deployment relative names to existing source files')
    parser.add_argument('--helper-sources', type=Path,
        help='Explicit frozen source mapping for known controller helper filenames')
    args = parser.parse_args()
    if args.adopt_campaign:
        result = adopt(args.directory, args.adopt_campaign,
                       read(args.supervisor_overrides) if args.supervisor_overrides else None,
                       read(args.extra_files) if args.extra_files else None,
                       read(args.helper_sources) if args.helper_sources else None)
    else:
        if args.supervisor_overrides:
            parser.error('--supervisor-overrides requires --adopt-campaign')
        result = prepare(args.directory, read(args.policy),
                         read(args.extra_files) if args.extra_files else None,
                         read(args.helper_sources) if args.helper_sources else None)
    print(json.dumps({'prepared_only': True, 'files': len(result['files']),
                      'directory': str(args.directory.resolve())}))


if __name__ == '__main__':
    main()
