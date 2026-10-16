#!/usr/bin/env python3
# Code style: AI-code. AI-generated explicit cold histogram retry preparation.
"""Retry one reviewed failed histogram without changing its logical membership.

This never submits, removes or releases jobs. Stop the supervisors and remove
the known failed queue record first. Preserve the failed working directory.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import time

from prepare_shift_stream_tasks import make_task
from run_shift_stream_controller import load_tasks, read, save, sha

QUEUE_ATTRIBUTES = {'ClusterId', 'ProcId', 'JobStatus', 'JobUniverse', 'Owner', 'Iwd',
                    'ShiftStreamCampaign', 'ShiftStreamController', 'ShiftStreamWatchdog'}


def replace_exact(source, target):
    temporary = target.with_name(target.name + '.' + str(os.getpid()) + '.tmp')
    try:
        with temporary.open('wb') as stream:
            stream.write(Path(source).read_bytes())
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def retry_histogram(campaign, identity, worker_policy, queue_receipt, reason, now=None):
    campaign = Path(campaign).resolve()
    now = time.time() if now is None else now
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError('An explicit reviewed reason is required')
    queue_path = Path(queue_receipt).resolve()
    observed = read(queue_path)
    if not 0 <= now - observed.get('checked_at_epoch', 0) <= 60:
        raise ValueError('Cold retry needs a fresh successful native full-owner queue receipt')
    policy = read(campaign / 'policy.json')
    owner = policy['admission']['account_owner']
    if (observed.get('schema') != 'shift-native-owner-queue-receipt-v1'
            or observed.get('complete') is not True or observed.get('owner') != owner
            or observed.get('schedd_name') != policy['admission']['schedd_name']
            or observed.get('constraint') != 'Owner == ' + json.dumps(owner)
            or not QUEUE_ATTRIBUTES <= set(observed.get('attributes', []))
            or type(observed.get('native_exit_code')) is not int
            or observed['native_exit_code'] != 0 or observed.get('native_stderr', '').strip()):
        raise ValueError('Queue receipt must verify the exact native owner-wide query and schedd coverage')
    ads = observed['ads']
    if not isinstance(ads, list) or any(ad.get('Owner') != policy['admission']['account_owner'] for ad in ads):
        raise ValueError('Queue receipt does not cover the expected owner')
    if any((ad.get('ShiftStreamCampaign') == policy['tag']
            and ad.get('ShiftStreamController') is True)
           or (ad.get('JobUniverse') == 7 and ad.get('Iwd') == str(campaign)) for ad in ads):
        raise ValueError('Stop and verify both old supervisors before preparing a retry')
    with (campaign / 'controller.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        ledger = read(campaign / 'ledger.json')
        record = ledger[identity]
        job = record.get('job')
        if job and any([ad.get('ClusterId'), ad.get('ProcId')] == job for ad in ads):
            raise ValueError('The old failed job must be absent before preparing its retry')
        task_path = campaign / 'tasks' / (identity + '.json')
        task = read(task_path)
        if task.get('stage') != 'histogram' or task.get('task_id') != identity:
            raise ValueError('Only an explicitly failed histogram can be retried by this tool')
        attempts = record.get('attempts', [])
        attempt = len(attempts) + 2
        archive = campaign / 'failed_attempts' / identity / ('attempt' + str(attempt - 1))
        intent_path = Path(record.get('retry_intent', archive / 'retry_intent.json'))
        # A crash between the two atomic replacements remains resumable. It
        # never supplies a false completion or permits an automatic submit.
        if intent_path.exists():
            intent = read(intent_path)
            if intent.get('reason') != reason or sha(worker_policy['histogram_worker']['tools_archive']) != intent['new_tools_sha256']:
                raise ValueError('Existing retry intent uses different reviewed inputs')
            old_job = intent['old_record'].get('job')
            if old_job and any([ad.get('ClusterId'), ad.get('ProcId')] == old_job for ad in ads):
                raise ValueError('The old failed job must remain absent')
        else:
            if record.get('phase') != 'failed' or not record.get('execution_verified'):
                raise ValueError('Retry requires a failed record with native execution evidence')
            load_tasks(campaign, ledger)
            status = read(task['status_path'])
            result = read(task['result_path'])
            native = record.get('queue', {})
            if (status.get('complete') is not False or result.get('complete') is not False
                    or type(status.get('exit_code')) is not int or status['exit_code'] == 0
                    or native.get('ExitBySignal') is not False
                    or native.get('ExitCode') != status['exit_code']):
                raise ValueError('Preserve an ambiguous or completed scientific attempt for review')
            if worker_policy['tag'] != policy['tag'] or 'histogram_worker' not in worker_policy:
                raise ValueError('Retry needs the same campaign and an explicit corrected histogram worker')
            directory = Path(task['working_directory'])
            request = read(directory / 'request.json')
            transfer = re.search(r'^transfer_input_files\s*=\s*(.*)$',
                                 Path(task['submit_file']).read_text(), re.MULTILINE)
            if transfer is None:
                raise ValueError('Missing frozen transfer input list')
            inputs = []
            old_archives = []
            for name in transfer[1].split(','):
                path = Path(name.strip())
                if str(path) not in task['input_sha256']:
                    raise ValueError('Transfer input was not pinned by the old task')
                if path == directory / 'request.json':
                    continue
                if path.name == 'frozen_tools.tar.gz':
                    old_archives.append(path)
                else:
                    inputs.append(path)
            if len(old_archives) != 1 or not inputs:
                raise ValueError('Retry needs the exact old descriptor inputs and one tools archive')
            archive.mkdir(parents=True, exist_ok=False)
            shutil.copy2(task_path, archive / 'task.json')
            prior = dict(record)
            prior.pop('attempts', None)
            save(archive / 'ledger_record.json', prior)
            evidence = {str(path): sha(path) for path in directory.iterdir() if path.is_file()}
            next_directory = directory.with_name(directory.name + '_attempt' + str(attempt))
            replacement = make_task(campaign, worker_policy, identity, task['stage'], task['cohort_id'],
                request, inputs, task['depends_on'],
                checks={key: value for key, value in task['completion_checks'].items()
                        if key not in ('complete', 'exit_code', 'cohort_id', 'stage')},
                canary=task.get('operational_canary') is True,
                matching=task.get('matching_status_fields', []),
                manifest_path=next_directory / 'task.json', working_directory=next_directory)
            replacement_path = next_directory / 'task.json'
            next_record = dict(phase='waiting', task_sha256=sha(replacement_path),
                first_seen_at_epoch=now, retry_intent=str(intent_path),
                attempts=attempts + [dict(attempt=attempt - 1, failed_record=prior,
                    task_manifest=str(archive / 'task.json'), task_sha256=sha(archive / 'task.json'),
                    evidence_sha256=evidence, reason=reason)])
            intent = dict(schema='shift-stream-histogram-retry-v1', task_id=identity,
                reason=reason, old_task_sha256=sha(task_path), new_task_sha256=sha(replacement_path),
                replacement_task=str(replacement_path), old_record=record, new_record=next_record,
                queue_receipt=str(queue_path), queue_receipt_sha256=sha(queue_path),
                new_tools_sha256=worker_policy['histogram_worker']['tools_sha256'],
                applied=False, science_membership_changed=False, submission_performed=False)
            save(intent_path, intent)
        if sha(task_path) not in (intent['old_task_sha256'], intent['new_task_sha256']):
            raise ValueError('Task changed outside the explicit retry intent')
        if record not in (intent['old_record'], intent['new_record']):
            raise ValueError('Ledger changed outside the explicit retry intent')
        replacement = read(intent['replacement_task'])
        if sha(intent['replacement_task']) != intent['new_task_sha256']:
            raise ValueError('Prepared replacement task changed')
        replace_exact(intent['replacement_task'], task_path)
        ledger[identity] = intent['new_record']
        save(campaign / 'ledger.json', ledger)
        intent.update(applied=True, applied_at_epoch=now)
        save(intent_path, intent)
        return intent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('campaign', type=Path)
    parser.add_argument('task_id')
    parser.add_argument('--factory-policy', type=Path, required=True)
    parser.add_argument('--queue-receipt', type=Path, required=True)
    parser.add_argument('--reason', required=True)
    args = parser.parse_args()
    result = retry_histogram(args.campaign, args.task_id, read(args.factory_policy),
                             args.queue_receipt, args.reason)
    print('Prepared explicit histogram retry:', result['task_id'], 'applied=', result['applied'])


if __name__ == '__main__':
    main()
