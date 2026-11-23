#!/usr/bin/env python3
# Code style: AI-code.
"""Bounded scheduler retry for a data publisher's explicit AFS-timeout exit."""

import argparse
import getpass
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import time


def release_expression(deadline):
    if type(deadline) is not int or deadline <= 0:
        raise ValueError('Require the exact publisher deadline as an epoch')
    return ('JobStatus == 5 && HoldReasonCode == 3 && HoldReasonSubCode == 0 '
            '&& ExitBySignal =?= false && ExitCode =?= 75 '
            '&& NumJobStarts >= 1 && NumJobStarts < 4 '
            '&& time() - EnteredCurrentStatus >= 90 '
            '&& time() + 1800 < JobStartDate + MaxRuntime '
            f'&& time() + 1800 < {deadline}')


def native(*arguments):
    return subprocess.run(arguments, capture_output=True, check=True,
                          text=True, timeout=30).stdout


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def policy(job):
    output = native('condor_q', job, '-long', '-attributes', 'PeriodicRelease')
    match = re.fullmatch(r'\s*PeriodicRelease\s*=\s*(.+?)\s*', output)
    if not match:
        raise ValueError('Could not read a unique live release policy')
    return match[1]


def normalized(expression):
    return re.sub(r'\s', '', expression).lower()


def install(directory, wrapper_sha256, verified_canary):
    directory = directory.resolve(strict=True)
    if not directory.is_relative_to(Path('/afs/cern.ch/')):
        raise ValueError('Require the publisher’s persistent AFS directory')
    receipt = json.loads((directory / 'finalizer_submission.json').read_text())
    cluster = receipt['cluster_id']
    if type(cluster) is not int or cluster <= 0 or receipt.get('proc_id', 0) != 0:
        raise ValueError('Require the exact finalizer submission receipt')
    job = f'{cluster}.0'
    attributes = 'ClusterId,ProcId,Owner,JobUniverse,JobStatus,Iwd,Cmd,JobBatchName,Args,Arguments'
    observed = json.loads(native('condor_q', job, '-json', '-attributes', attributes) or '[]')
    if len(observed) != 1:
        raise ValueError('Native ownership is missing or ambiguous')
    ad = observed[0]
    wrapper = directory / 'finalize.sh'
    batch = ad['JobBatchName']
    if (ad['ClusterId'] != cluster or ad['ProcId'] != 0 or ad['Owner'] != getpass.getuser()
            or ad['JobUniverse'] != 5 or ad['JobStatus'] not in (1, 2)
            or Path(ad['Iwd']) != directory or Path(ad['Cmd']) != wrapper
            or not (batch.startswith('DATA_Run2023D_') and 'Finalize' in batch)
            or batch != receipt.get('batch_name', batch) or sha256(wrapper) != wrapper_sha256):
        raise ValueError('Publisher ownership, state or reviewed wrapper changed')
    arguments = shlex.split(ad.get('Arguments') or ad['Args'])
    worklist = directory / 'finalizer_worklist.json'
    if (len(arguments) != 4 or Path(arguments[0]) != directory or Path(arguments[1]) != worklist
            or sha256(worklist) != arguments[2]
            or receipt.get('worklist_sha256', arguments[2]) != arguments[2]):
        raise ValueError('The live publisher does not own the exact frozen worklist')
    deadline = int(arguments[3])
    if deadline <= time.time() + 1800:
        raise ValueError('The live publisher is too close to its deadline for a retry')
    expression = release_expression(deadline)
    proof = json.loads(verified_canary.read_text())
    if (proof.get('native_same_id_released_and_completed') is not True
            or proof.get('ordinary_readback_after_retry') is not True
            or proof.get('unknown_exit_stayed_held') is not True
            or proof.get('expression') != release_expression(proof.get('deadline'))):
        raise ValueError('The exact policy needs a successful native recovery qualification')

    previous = policy(job)
    if normalized(previous) not in ('false', normalized(expression)):
        raise ValueError('An existing release policy needs reconciliation')
    if normalized(previous) == 'false':
        constraint = (f'ClusterId == {cluster} && ProcId == 0 && (JobStatus == 1 || JobStatus == 2) '
                      f'&& Owner == {json.dumps(ad["Owner"])} && Iwd == {json.dumps(str(directory))} '
                      f'&& Cmd == {json.dumps(str(wrapper))} && JobBatchName == {json.dumps(batch)}')
        native('condor_qedit', '-constraint', constraint, 'PeriodicRelease', expression)
    installed = policy(job)
    if normalized(installed) != normalized(expression):
        raise ValueError('The scheduler did not persist the required release policy')
    return dict(job=job, batch=batch, worklist_sha256=arguments[2], wrapper_sha256=wrapper_sha256,
                checked_at_epoch=time.time(), previous=previous, installed=installed,
                verified_canary_sha256=sha256(verified_canary))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest='mode', required=True)
    expression = modes.add_parser('expression')
    expression.add_argument('deadline', type=int)
    configure = modes.add_parser('install')
    configure.add_argument('directory', type=Path)
    configure.add_argument('--wrapper-sha256', required=True)
    configure.add_argument('--verified-canary', type=Path, required=True)
    args = parser.parse_args()
    if args.mode == 'expression':
        print(release_expression(args.deadline))
    else:
        report = install(args.directory, args.wrapper_sha256, args.verified_canary)
        print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
