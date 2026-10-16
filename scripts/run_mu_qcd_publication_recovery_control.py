#!/usr/bin/env python3
# Code style: AI-code. AI-generated one-shot saved-GEN recovery control.
"""Use the unchanged GEN guard before releasing one publication-only retry.

Run on the original scheduler. This control does not generate or publish data,
retry automatically, or declare the science job successful.
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


JOB = '12967824.0'
NODE = 'production_qcd_10to20_0209'
PARENT = 12958654
ATTRIBUTES = ['ClusterId', 'ProcId', 'Owner', 'JobUniverse', 'JobStatus', 'NumJobStarts',
    'ExitCode', 'DAGManJobId', 'DAGNodeName', 'Iwd', 'Cmd', 'Args', 'TransferInput',
    'TransferOutput', 'TransferOutputRemaps', 'WhenToTransferOutput', 'MaxRuntime',
    'RequestMemory', 'RequestDisk', 'Out', 'Err', 'UserLog', 'ShiftMuEnrichedQCD',
    'ShiftSuiteTag', 'ShiftMuEnrichedNodeId', 'ShiftMuPublicationRecovery',
    'ShiftMuOriginalFailedReceiptSHA256']


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def save(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix+'.tmp')
    with temporary.open('w') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')
        stream.flush()
        import os
        os.fsync(stream.fileno())
    temporary.replace(path)


def exact_job(native):
    rows = native.query('ClusterId == 12967824 && ProcId == 0', ATTRIBUTES,
                        timeout=30, local_schedd=True)
    if len(rows) != 1:
        raise ValueError('The exact original recovery job is missing or ambiguous')
    return rows[0]


def check_original(ad, row, original):
    expected = dict(row['expected_current_state'], DAGManJobId=PARENT,
        DAGNodeName=row['DAG_node'], JobUniverse=5, Owner='jniedzie',
        ShiftMuEnrichedNodeId=NODE, ShiftMuEnrichedQCD=True,
        ShiftSuiteTag='qcd_mu_enriched_20261015_v1')
    if any(ad.get(key) != value for key, value in expected.items()):
        raise ValueError('Original held job identity/state changed')
    for key in ('Cmd', 'Iwd', 'Args', 'TransferInput', 'TransferOutput',
                'TransferOutputRemaps', 'WhenToTransferOutput', 'MaxRuntime',
                'RequestMemory', 'RequestDisk', 'Out', 'Err', 'UserLog'):
        if ad.get(key) != original.get(key):
            raise ValueError('Original held job attribute changed: '+key)


def control(manager, row, overrides, original, guard, native, receipt_path):
    """One transition only; dependency injection supports bounded safety tests."""
    manager = Path(manager)
    receipt = dict(schema='shift-one-shot-GEN-publication-recovery-control-v1',
        job_id=JOB, node=NODE, started_at_epoch=time.time(), phase='checking',
        admission_succeeded=False, edit_attempted=False, applied=False,
        release_attempted=False, released=False, science_success_verified=False)
    save(receipt_path, receipt)
    try:
        if row['job_id'] != JOB or row['node_id'] != NODE or row['DAG_manager'] != PARENT:
            raise ValueError('This one-shot control is restricted to the first reviewed source')
        allowed = {'Args', 'TransferInput', 'TransferOutputRemaps', 'WhenToTransferOutput',
                   'Out', 'Err', 'ShiftMuPublicationRecovery', 'ShiftMuOriginalFailedReceiptSHA256'}
        if set(overrides) != allowed or '--publish' not in overrides['Args'].split():
            raise ValueError('Recovery overrides must use only reviewed publication-only fields')
        ad = exact_job(native)
        check_original(ad, row, original)
        parents = native.query('ClusterId == 12958654 && ProcId == 0',
            ['ClusterId', 'ProcId', 'JobStatus', 'Iwd'], timeout=30, local_schedd=True)
        if (len(parents) != 1 or parents[0].get('JobStatus') != 2
                or parents[0].get('Iwd') != str(manager)):
            raise ValueError('Original parent DAG is not executing in its persistent directory')
        receipt.update(before=ad, phase='admission_intent', admission_started_at_epoch=time.time())
        save(receipt_path, receipt)
        # This is the unchanged frozen PRE entry point, including its actual
        # flock, authenticated quota refresh, pending PREs and full1GB claim.
        status = guard.admit(manager, NODE)
        receipt['admission_return_code'] = status
        if status == 75:
            receipt.update(phase='deferred', reason='Original GEN guard deferred admission; no edit or release')
            save(receipt_path, receipt)
            return 75
        if status != 0:
            raise ValueError('Original GEN admission returned an unknown result: '+str(status))
        decision = read(manager/'admission/decisions'/(NODE+'.json'))
        reservations = read(manager/'admission/pre_reservations.json')
        policy = read(manager/'admission/policy.json')
        pending = reservations.get(NODE, {})
        if (decision.get('admitted') is not True or decision.get('node') != NODE
                or decision.get('checked_at_epoch', 0) < receipt['admission_started_at_epoch']
                or pending.get('at_epoch', 0) < receipt['admission_started_at_epoch']
                or time.time()-pending['at_epoch'] >= policy['pre_reservation_seconds']):
            raise ValueError('Successful admission lacks its fresh original-node reservation')
        receipt.update(admission_succeeded=True, admission_decision=decision,
                       pending_reservation=pending, phase='admitted_held')
        save(receipt_path, receipt)
        check_original(exact_job(native), row, original)
        receipt['dry_run_stdout'] = native.edit(JOB, overrides, dry_run=True, timeout=30, local_schedd=True)
        receipt.update(edit_attempted=True, phase='edit_intent')
        save(receipt_path, receipt)
        receipt['edit_stdout'] = native.edit(JOB, overrides, timeout=30, local_schedd=True)
        receipt['applied'] = True
        save(receipt_path, receipt)
        after = exact_job(native)
        if (after.get('JobStatus') != 5 or after.get('NumJobStarts') != 1
                or any(after.get(key) != value for key, value in overrides.items())):
            raise ValueError('Recovery edits did not read back exactly; leave the job held')
        for key in ('Cmd', 'Iwd', 'DAGManJobId', 'DAGNodeName', 'MaxRuntime',
                    'RequestMemory', 'RequestDisk', 'TransferOutput', 'UserLog'):
            if after.get(key) != ad.get(key):
                raise ValueError('Recovery changed a protected native job field: '+key)
        receipt.update(edited_readback=after, release_attempted=True, phase='release_intent')
        save(receipt_path, receipt)
        receipt['release_stdout'] = native.run_condor(['/usr/bin/condor_release', JOB],
                                                     timeout=30, local_schedd=True)
        receipt.update(released=True, phase='released_execution_unverified')
        save(receipt_path, receipt)
        receipt['after_release'] = exact_job(native)
        save(receipt_path, receipt)
        return 0
    except Exception as error:
        receipt.update(phase='attention_required', error=repr(error),
            release_outcome_unknown=receipt['release_attempted'] and not receipt['released'],
            rollback='Do not release an unchanged GEN worker. If still held, review actual attributes and restore original literals only after native readback. Keep the booked PRE claim; the unchanged guard expires it and requires a fresh quota before reuse. Do not replay this control after a release intent without reconciling the exact job.')
        try:
            receipt['last_native_readback'] = exact_job(native)
        except Exception as query_error:
            receipt['last_native_readback_error'] = repr(query_error)
        save(receipt_path, receipt)
        return 1


def run_deployment(root, expected_config_sha256):
    config_path = root/'control.json'
    if (str(root).startswith(('/tmp/', '/pool/condor/dir_'))
            or sha(config_path) != expected_config_sha256):
        raise ValueError('Require the pinned persistent recovery-control deployment')
    previous_path = root/'transition.json'
    if previous_path.exists():
        previous = read(previous_path)
        if previous.get('edit_attempted') or previous.get('release_attempted'):
            raise ValueError('Prior control reached an edit/release intent; preserve it and reconcile before any new attempt')
    config = read(config_path)
    if config.get('schema') != 'shift-one-shot-GEN-recovery-control-deployment-v1':
        raise ValueError('Wrong recovery-control configuration')
    for name, expected in config['input_sha256'].items():
        if sha(name) != expected:
            raise ValueError('Pinned control or original recovery input changed: '+name)
    if sha(__file__) != config['control_sha256']:
        raise ValueError('Recovery control code changed')
    manager = Path(config['manager_directory'])
    sys.path.insert(0, str(manager))
    native = importlib.import_module('shift_condor_native')
    guard = importlib.import_module('run_mu_enriched_qcd_admission')
    address, ad_path = Path(native.SCHEDD_ADDRESS_FILE), Path(native.SCHEDD_AD_FILE)
    if not address.is_file() or not ad_path.is_file():
        raise ValueError('Run this control on the original scheduler, not lxplus')
    match = re.search(r'^\s*Name\s*=\s*"([^"]+)"', ad_path.read_text(), re.MULTILINE)
    if not match or match[1] != config['schedd_name']:
        raise ValueError('Native local scheduler differs from the original GEN scheduler')
    plan = read(config['recovery_manifest'])
    overrides = read(config['job_overrides'])['jobs'][JOB]['attribute_edits']
    original = next(ad for ad in read(config['original_held_ads'])
                    if ad['ClusterId'] == 12967824 and ad['ProcId'] == 0)
    row = next(row for row in plan['jobs'] if row['job_id'] == JOB)
    return control(manager, row, overrides, original, guard, native, root/'transition.json')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('deployment', type=Path)
    parser.add_argument('--expected-config-sha256', required=True)
    args = parser.parse_args()
    root = args.deployment.resolve()
    with (root/'control.lock').open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('Another invocation owns this one-shot recovery control; no action')
            return 75
        return run_deployment(root, args.expected_config_sha256)


if __name__ == '__main__':
    raise SystemExit(main())
