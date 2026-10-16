#!/usr/bin/env python3
# Code style: AI-code. AI-generated one-shot saved-GEN admission and submission.
"""Admit one saved GEN publication, then submit one fresh worker exactly once.

This control runs on the original scheduler. It never generates events, edits
old jobs or their clocks, or marks an original DAG node successful.
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
import uuid


JOB = '12967824.0'
NODE = 'production_qcd_10to20_0209'
SUITE = 'qcd_mu_enriched_20261015_v1'
ATTRIBUTES = ['ClusterId', 'ProcId', 'Owner', 'JobUniverse', 'JobStatus', 'Iwd',
    'Cmd', 'Args', 'TransferInput', 'TransferOutput', 'TransferOutputRemaps',
    'WhenToTransferOutput', 'RequestCpus', 'RequestMemory', 'RequestDisk',
    'MaxRuntime', 'DAGManJobId', 'DAGNodeName', 'ShiftMuEnrichedQCD',
    'ShiftSuiteTag', 'ShiftMuEnrichedNodeId', 'ShiftMuPublicationRecovery',
    'ShiftMuRecoveryOriginalJobId', 'ShiftMuRecoveryNonce']


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


def verify_old_terminal(config):
    """Shared DAG events retain the actual aborted exit; no synthetic history."""
    blocks = [part for part in Path(config['original_event_log']).read_text().split('...\n')
              if '(12967824.000.000)' in part]
    if (not blocks or not blocks[-1].lstrip().startswith('009 ')
            or 'SYSTEM_PERIODIC_REMOVE' not in blocks[-1]
            or 'wall time exceeded allowed max' not in blocks[-1]
            or sum(part.lstrip().startswith('001 ') for part in blocks) != 1):
        raise ValueError('Original native abort history changed or is incomplete')
    status = Path(config['original_node_status']).read_text()
    match = re.search(r'\[\s*Type\s*=\s*"NodeStatus";\s*Node\s*=\s*"L0614";.*?\]', status, re.S)
    if not match or not re.search(r'NodeStatus\s*=\s*6\s*;', match[0]):
        raise ValueError('Original DAG node must retain its real ERROR state')
    return dict(events=blocks, node_status_block=match[0])


def no_existing_source(native):
    rows = native.query('Owner == "jniedzie"', ATTRIBUTES, timeout=30, local_schedd=True)
    for ad in rows:
        if (ad.get('ClusterId') == 12967824 and ad.get('ProcId') == 0
                or ad.get('ShiftMuEnrichedNodeId') == NODE
                or ad.get('ShiftMuRecoveryOriginalJobId') == JOB):
            raise ValueError('An original or recovery source job remains queued, including held jobs')
    return dict(owner='jniedzie', constraint='Owner == "jniedzie"',
                returned_jobs=len(rows), checked_at_epoch=time.time())


def check_fresh_ad(ad, expected, nonce):
    if (ad.get('ProcId') != 0 or ad.get('JobStatus') not in (1, 2, 4, 5, 6, 7)
            or any(ad.get(key) != value for key, value in expected.items())
            or ad.get('ShiftMuRecoveryNonce') != nonce
            or ad.get('DAGManJobId') is not None or ad.get('DAGNodeName') is not None):
        raise ValueError('Fresh publication job did not read back its exact independent contract')


def control(config, guard, native, receipt_path):
    """A durable submit intent is a hard replay stop, even after a lost response."""
    if Path(receipt_path).exists() and read(receipt_path).get('submit_attempted'):
        raise ValueError('Prior submit intent exists; reconcile its nonce before any new control')
    receipt = dict(schema='shift-fresh-GEN-publication-submission-v1', original_job_id=JOB,
        node=NODE, started_at_epoch=time.time(), phase='checking', admission_succeeded=False,
        submit_attempted=False, submitted=False, science_success_verified=False)
    save(receipt_path, receipt)
    try:
        if config['original_job_id'] != JOB or config['original_node_id'] != NODE:
            raise ValueError('Only the reviewed first saved source is allowed')
        receipt['original_native_failure'] = verify_old_terminal(config)
        receipt['queue_before_admission'] = no_existing_source(native)
        manager = Path(config['manager_directory'])
        receipt.update(phase='admission_intent', admission_started_at_epoch=time.time())
        save(receipt_path, receipt)
        result = guard.admit(manager, NODE)  # unchanged helper owns its actual lock
        receipt['admission_return_code'] = result
        if result == 75:
            receipt.update(phase='deferred', reason='Original GEN guard deferred; no worker submitted')
            save(receipt_path, receipt)
            return 75
        if result != 0:
            raise ValueError('Original GEN guard returned an unknown result')
        decision = read(manager/'admission/decisions'/(NODE+'.json'))
        pending = read(manager/'admission/pre_reservations.json').get(NODE, {})
        policy = read(manager/'admission/policy.json')
        now = time.time()
        if (decision.get('node') != NODE or decision.get('admitted') is not True
                or decision.get('checked_at_epoch', 0) < receipt['admission_started_at_epoch']
                or pending.get('at_epoch', 0) < receipt['admission_started_at_epoch']
                or not 0 <= now-pending['at_epoch'] < policy['pre_reservation_seconds']):
            raise ValueError('Admission lacks its fresh original-node 1 GB reservation')
        receipt.update(admission_succeeded=True, admission_decision=decision,
            pending_reservation=pending, queue_before_submit=no_existing_source(native))
        nonce = uuid.uuid4().hex
        receipt.update(phase='submit_intent', submit_attempted=True, nonce=nonce,
                       submission_started_at_epoch=time.time())
        save(receipt_path, receipt)
        response = native.run_condor(['/usr/bin/condor_submit', '-terse', config['worker_submit'],
            '-append', '+ShiftMuRecoveryNonce = '+json.dumps(nonce)], timeout=30,
            local_schedd=True, cwd=config['worker_directory'])
        receipt['native_submit_stdout'] = response
        match = re.fullmatch(r'(\d+)\.0\s*-\s*\1\.0\s*', response)
        if not match:
            raise ValueError('Ambiguous native submit response; do not submit again')
        cluster = int(match[1])
        receipt.update(submitted=True, new_job_id=str(cluster)+'.0', phase='submitted_readback_pending')
        save(receipt_path, receipt)
        ads = native.query('ClusterId == '+str(cluster)+' && ProcId == 0', ATTRIBUTES,
                           timeout=30, local_schedd=True)
        if len(ads) != 1:
            raise ValueError('Fresh job is missing or ambiguous; reconcile before any new attempt')
        check_fresh_ad(ads[0], config['expected_worker_attributes'], nonce)
        receipt.update(queued_readback=ads[0], phase='submitted_execution_unverified')
        save(receipt_path, receipt)
        return 0
    except Exception as error:
        receipt.update(phase='attention_required', error=repr(error),
            submission_outcome_unknown=receipt['submit_attempted'] and not receipt['submitted'],
            rollback='Never replay after submit intent. Query the nonce and native event log first. A booked PRE reservation remains with the unchanged GEN guard until observed queued or expired against fresh quota. No old job, DAG status or source event was changed.')
        save(receipt_path, receipt)
        return 1


def run_deployment(root, expected_config_sha256):
    config_path = root/'control.json'
    if str(root).startswith(('/tmp/', '/pool/condor/dir_')) or sha(config_path) != expected_config_sha256:
        raise ValueError('Require the pinned persistent deployment')
    config = read(config_path)
    if config.get('schema') != 'shift-fresh-GEN-publication-control-deployment-v1':
        raise ValueError('Wrong recovery-control schema')
    for path, expected in config['input_sha256'].items():
        if sha(path) != expected:
            raise ValueError('Pinned recovery input changed: '+path)
    if sha(__file__) != config['control_sha256']:
        raise ValueError('Recovery submission control changed')
    sys.path.insert(0, config['manager_directory'])
    native = importlib.import_module('shift_condor_native')
    guard = importlib.import_module('run_mu_enriched_qcd_admission')
    address, ad_path = Path(native.SCHEDD_ADDRESS_FILE), Path(native.SCHEDD_AD_FILE)
    if not address.is_file() or not ad_path.is_file():
        raise ValueError('Run on the original scheduler, not lxplus')
    match = re.search(r'^\s*Name\s*=\s*"([^"]+)"', ad_path.read_text(), re.MULTILINE)
    if not match or match[1] != config['schedd_name']:
        raise ValueError('Wrong native local scheduler')
    return control(config, guard, native, root/'transition.json')


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
            return 75
        return run_deployment(root, args.expected_config_sha256)


if __name__ == '__main__':
    raise SystemExit(main())
