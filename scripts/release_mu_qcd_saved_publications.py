#!/usr/bin/env python3
# Code style: AI-code. AI-generated one-shot release of exact saved-GEN workers.
"""Admit and release six initially-held publication-only replacements once.

Run on the original scheduler after the six obsolete original holds are removed.
The original locked GEN guard owns every storage claim. No generation, submit,
old-job edits, timing edits or synthetic DAG completion occurs here.
"""
import argparse
import fcntl
import hashlib
import importlib
import json
from pathlib import Path
import re
import sys
import time


ORIGINALS = {
    'production_qcd_10to20_0049':'12967850.0',
    'production_qcd_10to20_0112':'12967851.0',
    'production_qcd_10to20_0321':'12967893.0',
    'production_qcd_10to20_0074':'12967927.0',
    'production_qcd_10to20_0260':'12967928.0',
    'production_qcd_10to20_0173':'12967959.0',
}
ATTRIBUTES = ['ClusterId', 'ProcId', 'Owner', 'JobUniverse', 'JobStatus',
    'NumJobStarts', 'CurrentHosts', 'QDate', 'JobStartDate', 'JobCurrentStartDate',
    'RemoteWallClockTime', 'Iwd', 'Cmd', 'Args', 'TransferInput', 'TransferOutput',
    'TransferOutputRemaps', 'WhenToTransferOutput', 'RequestCpus', 'RequestMemory',
    'RequestDisk', 'MaxRuntime', 'DAGManJobId', 'DAGNodeName', 'ShiftMuEnrichedQCD',
    'ShiftSuiteTag', 'ShiftMuEnrichedNodeId', 'ShiftMuPublicationRecovery',
    'ShiftMuRecoveryOriginalJobId', 'ShiftMuRecoveryNonce', 'PeriodicRelease',
    'Out', 'Err', 'UserLog', 'HoldReason', 'HoldReasonCode']


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def save(path, value):
    import os
    path = Path(path)
    temporary = path.with_suffix(path.suffix+'.tmp')
    with temporary.open('w') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def validate_bindings(bindings):
    jobs = bindings.get('jobs', {})
    if (bindings.get('schema') != 'shift-held-GEN-publication-recovery-bindings-v1'
            or set(jobs) != set(ORIGINALS)
            or any(not isinstance(job, str) or not re.fullmatch(r'[1-9][0-9]*\.0', job)
                   for job in jobs.values())
            or len(set(jobs.values())) != 6
            or set(jobs.values()) & (set(ORIGINALS.values()) | {'12967824.0','12969425.0'})):
        raise ValueError('Bindings must identify exactly six distinct fresh jobs for the fixed sources')
    return jobs


def identity(ad):
    return str(ad.get('ClusterId'))+'.'+str(ad.get('ProcId'))


def owner_queue(native):
    return native.query('Owner == "jniedzie"', ATTRIBUTES, timeout=30, local_schedd=True)


def verify_held(ad, row, transfer_mode=None):
    expected = dict(row['expected_attributes'])
    expected.pop('WhenToTransferOutput', None)
    if (ad.get('JobStatus') != 5 or ad.get('NumJobStarts', 0) != 0
            or ad.get('CurrentHosts', 0) != 0 or ad.get('RemoteWallClockTime', 0) != 0
            or ad.get('DAGManJobId') is not None or ad.get('DAGNodeName') is not None
            or any(ad.get(key) != value for key, value in expected.items())):
        raise ValueError('Fresh job is not the exact never-started held replacement')
    if ad.get('WhenToTransferOutput') not in ('ON_EXIT', 'ON_EXIT_OR_EVICT'):
        raise ValueError('Unknown site transfer mode')
    if transfer_mode and ad.get('WhenToTransferOutput') != transfer_mode:
        raise ValueError('Transfer-mode edit did not read back exactly')
    if str(ad['Iwd']).startswith(('/tmp/', '/pool/condor/dir_')):
        raise ValueError('Recovery requires a persistent working directory')


def exact_held_from_queue(native, row, jobs):
    ads = owner_queue(native)
    if any(identity(ad) in ORIGINALS.values() for ad in ads):
        raise ValueError('An obsolete original job is still queued; preserve it and stop')
    node, job = row['node_id'], jobs[row['node_id']]
    matches = [ad for ad in ads if identity(ad) == job]
    if len(matches) != 1:
        raise ValueError('Exact fresh held job is missing or ambiguous')
    for ad in ads:
        if identity(ad) != job and (ad.get('ShiftMuEnrichedNodeId') == node
                or ad.get('ShiftMuRecoveryOriginalJobId') == ORIGINALS[node]
                or ad.get('ShiftMuRecoveryNonce') == row['nonce']):
            raise ValueError('A duplicate or unresolved source attempt is still queued')
    verify_held(matches[0], row)
    return matches[0]


def control(config, bindings, guard, native, receipt_path):
    """One pass; a prior edit/release intent stops all automatic replay."""
    jobs = validate_bindings(bindings)
    if set(row['node_id'] for row in config['workers']) != set(ORIGINALS):
        raise ValueError('Control must cover exactly the reviewed six saved sources')
    if Path(receipt_path).exists():
        old = read(receipt_path)
        if any(row.get('edit_attempted') or row.get('release_attempted')
               for row in old.get('jobs', {}).values()):
            raise ValueError('Prior edit/release intent exists; reconcile exact jobs before a new control')
    result = dict(schema='shift-six-saved-GEN-publication-release-v1',
        started_at_epoch=time.time(), phase='preflight', jobs={},
        submitted_new_jobs=False, science_success_verified=False)
    save(receipt_path, result)
    try:
        # Validate every binding before the first mutation. Later checks repeat
        # each exact held source after potentially slow quota observations.
        for row in config['workers']:
            exact_held_from_queue(native, row, jobs)
        deferred = False
        for row in config['workers']:
            node, job = row['node_id'], jobs[row['node_id']]
            entry = dict(job_id=job, original_job_id=ORIGINALS[node], nonce=row['nonce'],
                phase='checking', admission_succeeded=False, edit_attempted=False,
                release_attempted=False, released=False)
            result['jobs'][node] = entry
            entry['before'] = exact_held_from_queue(native, row, jobs)
            entry.update(phase='admission_intent', admission_started_at_epoch=time.time())
            save(receipt_path, result)
            manager = Path(config['manager_directory'])
            status = guard.admit(manager, node)
            entry['admission_return_code'] = status
            if status == 75:
                entry.update(phase='deferred', reason='Original GEN guard deferred; no edit or release')
                deferred = True
                save(receipt_path, result)
                continue
            if status != 0:
                raise ValueError('Unknown original GEN admission result')
            decision = read(manager/'admission/decisions'/(node+'.json'))
            pending = read(manager/'admission/pre_reservations.json').get(node, {})
            policy = read(manager/'admission/policy.json')
            if (decision.get('node') != node or decision.get('admitted') is not True
                    or decision.get('checked_at_epoch', 0) < entry['admission_started_at_epoch']
                    or pending.get('at_epoch', 0) < entry['admission_started_at_epoch']
                    or not 0 <= time.time()-pending['at_epoch'] < policy['pre_reservation_seconds']):
                raise ValueError('Admission lacks its fresh original-node 1 GB reservation')
            entry.update(admission_succeeded=True, decision=decision, pending_reservation=pending)
            before = exact_held_from_queue(native, row, jobs)
            entry.update(edit_attempted=True, phase='transfer_edit_intent')
            save(receipt_path, result)
            entry['edit_stdout'] = native.edit(job, {'WhenToTransferOutput':'ON_EXIT'},
                                               timeout=30, local_schedd=True)
            after = exact_held_from_queue(native, row, jobs)
            verify_held(after, row, transfer_mode='ON_EXIT')
            for key in ATTRIBUTES:
                if key not in ('WhenToTransferOutput',) and after.get(key) != before.get(key):
                    raise ValueError('A protected native job field changed during transfer-only edit: '+key)
            # A reservation may expire during a slow native operation. Do not
            # release unless the actual guard's claim still covers the gap.
            current = read(manager/'admission/pre_reservations.json').get(node, {})
            if (current.get('at_epoch') != pending['at_epoch']
                    or not 0 <= time.time()-current['at_epoch'] < policy['pre_reservation_seconds']):
                raise ValueError('Reservation changed or expired before release; leave held')
            entry.update(edited_readback=after, release_attempted=True, phase='release_intent')
            save(receipt_path, result)
            entry['release_stdout'] = native.run_condor(['/usr/bin/condor_release', job],
                                                       timeout=30, local_schedd=True)
            entry.update(released=True, phase='released_execution_unverified')
            save(receipt_path, result)
        result['phase'] = 'partial_deferral' if deferred else 'released_execution_unverified'
        save(receipt_path, result)
        return 75 if deferred else 0
    except Exception as error:
        result.update(phase='attention_required', error=repr(error),
            rollback='No automatic replay after any edit/release intent. Query every bound job and native events. Leave unreleased jobs held. Original GEN guard retains or expires its normal PRE claims against fresh quota. No old native clock, exit or DAG status was edited.')
        save(receipt_path, result)
        return 1


def run_deployment(root, config_sha256, bindings_path, bindings_sha256):
    if str(root).startswith(('/tmp/', '/pool/condor/dir_')) or sha(root/'control.json') != config_sha256:
        raise ValueError('Require the pinned persistent six-worker deployment')
    config = read(root/'control.json')
    if config.get('schema') != 'shift-six-saved-GEN-release-deployment-v1':
        raise ValueError('Wrong six-worker release deployment')
    for path, expected in config['input_sha256'].items():
        if sha(path) != expected:
            raise ValueError('Pinned saved input or control changed: '+path)
    if sha(__file__) != config['control_sha256'] or sha(bindings_path) != bindings_sha256:
        raise ValueError('Control or actual fresh-job bindings changed')
    sys.path.insert(0, config['manager_directory'])
    native = importlib.import_module('shift_condor_native')
    guard = importlib.import_module('run_mu_enriched_qcd_admission')
    if not Path(native.SCHEDD_ADDRESS_FILE).is_file() or not Path(native.SCHEDD_AD_FILE).is_file():
        raise ValueError('Release control must run on the original scheduler')
    match = re.search(r'^\s*Name\s*=\s*"([^"]+)"', Path(native.SCHEDD_AD_FILE).read_text(), re.M)
    if not match or match[1] != config['schedd_name']:
        raise ValueError('Wrong local scheduler')
    return control(config, read(bindings_path), guard, native, root/'transition.json')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('deployment', type=Path)
    parser.add_argument('--expected-config-sha256', required=True)
    parser.add_argument('--bindings', required=True, type=Path)
    parser.add_argument('--expected-bindings-sha256', required=True)
    args = parser.parse_args()
    root = args.deployment.resolve()
    with (root/'control.lock').open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 75
        return run_deployment(root, args.expected_config_sha256,
                              args.bindings.resolve(), args.expected_bindings_sha256)


if __name__ == '__main__':
    raise SystemExit(main())
