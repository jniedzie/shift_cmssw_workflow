#!/usr/bin/env python3
# Code style: AI-code. AI-generated bounded GEN admission and quota guard.
"""Defer a DAG payload until account, EOS and AFS observations allow it.

An authenticated vanilla audit observes EOS and other account schedulers. This
PRE script reads the local scheduler through its verified local address. A
short reservation covers the gap between a successful PRE and worker submission.
Return 75 means temporary deferral; return 99 preserves the weighted budget skip.
"""
import argparse
import fcntl
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import time

from run_shift_ntuple_controller import refresh_quota
from shift_condor_native import native_env, query


def save(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def validate_policy(policy):
    for key in ('worker_ceiling', 'account_ceiling', 'minimum_free_bytes',
                'minimum_free_files', 'minimum_AFS_free_bytes',
                'reserved_bytes_per_worker', 'reserved_files_per_worker',
                'observation_max_age_seconds', 'pre_reservation_seconds'):
        if type(policy.get(key)) is not int or policy[key] <= 0:
            raise ValueError('Admission policy needs a positive integer: ' + key)
    if not policy['worker_ceiling'] <= policy['account_ceiling'] <= 1000:
        raise ValueError('Admission account ceiling must be at most 1000')
    for key in ('account_owner', 'account_pool', 'schedd_name', 'suite_tag'):
        if not isinstance(policy.get(key), str) or not policy[key]:
            raise ValueError('Admission policy needs explicit ' + key)
    return policy


def decision(policy, observation, local_ads, reservations, node, now, afs_free):
    """Compute an admission from verified observations without mutating state."""
    result = {'node': node, 'checked_at_epoch': now, 'admitted': False}
    for key in ('quota_checked_at_epoch', 'remote_account_checked_at_epoch'):
        stamp = observation.get(key)
        if (type(stamp) not in (int, float) or not math.isfinite(stamp)
                or not 0 <= now - stamp <= policy['observation_max_age_seconds']):
            return result | {'reason': 'Storage/account observation is missing or stale'}
    if (observation.get('complete') is not True
            or observation.get('remote_account_coverage_verified') is not True
            or observation.get('excluded_local_schedd') != policy['schedd_name']
            or policy['schedd_name'] not in observation.get('account_schedds', [])):
        return result | {'reason': 'Account scheduler coverage is unknown'}
    remote_ads = observation.get('remote_account_workers')
    if not isinstance(remote_ads, list):
        return result | {'reason': 'Remote account worker list is unknown'}
    live = [ad for ad in local_ads if ad.get('JobUniverse') == 5
            and ad.get('JobStatus') in (1, 2, 6, 7)]
    own = [ad for ad in live if ad.get('ShiftMuEnrichedQCD') is True
           and ad.get('ShiftSuiteTag') == policy['suite_tag']]
    external = len(live) - len(own) + len(remote_ads)
    # Reserve the entire possible DAG wave against other productions. This
    # avoids admitting an extra wave while many PRE scripts are still pending.
    if external + policy['worker_ceiling'] > policy['account_ceiling']:
        return result | {'reason': 'Other account jobs leave insufficient worker capacity',
                         'external_workers': external}
    queued_nodes = {ad.get('ShiftMuEnrichedNodeId') for ad in own}
    pending = {key: value for key, value in reservations.items()
               if isinstance(value, dict) and type(value.get('at_epoch')) in (int, float)
               and 0 <= now - value['at_epoch'] < policy['pre_reservation_seconds']
               and key not in queued_nodes}
    reserved = len(own) + len(pending) + int(node not in pending and node not in queued_nodes)
    result.update(external_workers=external, own_workers=len(own),
                  pending_PRE_reservations=len(pending), reserved_workers=reserved)
    if reserved > policy['worker_ceiling']:
        return result | {'reason': 'This production has reached its worker ceiling'}
    for key in ('free_bytes', 'free_files'):
        if type(observation.get(key)) is not int or observation[key] < 0:
            return result | {'reason': 'Storage headroom is unknown'}
    if observation['free_bytes'] < policy['minimum_free_bytes'] + reserved * policy['reserved_bytes_per_worker']:
        return result | {'reason': 'EOS byte reservation would consume the retained reserve'}
    if observation['free_files'] < policy['minimum_free_files'] + reserved * policy['reserved_files_per_worker']:
        return result | {'reason': 'EOS file reservation would consume the retained reserve'}
    if type(afs_free) is not int or afs_free < policy['minimum_AFS_free_bytes']:
        return result | {'reason': 'AFS free space is missing or below its retained reserve'}
    pending[node] = {'at_epoch': now}
    return result | {'admitted': True, 'reason': 'Fresh account and storage checks pass',
                     'reservations': pending, 'AFS_free_bytes': afs_free,
                     'EOS_free_bytes': observation['free_bytes'],
                     'EOS_free_files': observation['free_files']}


def afs_headroom(directory):
    completed = subprocess.run(['/usr/bin/fs', 'listquota', '-path', str(directory)],
                               capture_output=True, text=True, timeout=20,
                               check=True, env=native_env(local_schedd=True))
    for line in completed.stdout.splitlines():
        match = re.match(r'\s*\S+\s+(\d+)\s+(\d+)\s+', line)
        if match and int(match[1]) > 0:
            return max(0, int(match[1]) - int(match[2])) * 1024
    raise ValueError('Cannot read the AFS volume quota')


def admit(directory, node):
    root = directory / 'admission'
    (root / 'decisions').mkdir(parents=True, exist_ok=True)
    policy = validate_policy(json.loads((root / 'policy.json').read_text()))
    with (root / 'admission.lock').open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 75
        now = time.time()
        try:
            if (directory / 'stop_requested.json').exists() or (root / 'stop_requested.json').exists():
                raise ValueError('Production admission has an explicit stop request')
            observation = refresh_quota(root)
            local_ads = query('Owner == ' + json.dumps(policy['account_owner'])
                              + ' && JobUniverse == 5 && (JobStatus == 1 || JobStatus == 2 || JobStatus == 6 || JobStatus == 7)',
                              ['ClusterId', 'ProcId', 'JobUniverse', 'JobStatus',
                               'ShiftMuEnrichedQCD', 'ShiftSuiteTag', 'ShiftMuEnrichedNodeId'],
                              timeout=20, local_schedd=True)
            reservation_path = root / 'pre_reservations.json'
            reservations = json.loads(reservation_path.read_text()) if reservation_path.exists() else {}
            result = decision(policy, observation, local_ads, reservations, node,
                              now, afs_headroom(directory))
            if result['admitted']:
                save(reservation_path, result.pop('reservations'))
        except Exception as error:
            result = {'node': node, 'checked_at_epoch': now, 'admitted': False,
                      'reason': 'Observation failed; no payload may start', 'error': repr(error)}
        save(root / 'decisions' / (node + '.json'), result)
        save(root / 'heartbeat.json', result)
        print(json.dumps(result))
        return 0 if result['admitted'] else 75


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--node', required=True)
    parser.add_argument('--weighted-manifest', type=Path)
    parser.add_argument('--expected-sha256')
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_-]+', args.node):
        raise ValueError('PRE node identity must be a plain filename component')
    directory = args.directory.resolve()
    if args.weighted_manifest is not None:
        if not args.expected_sha256:
            raise ValueError('Weighted PRE requires its frozen manifest digest')
        script = args.weighted_manifest.resolve().parent / 'shift_weighted_gen_pre.py'
        budget = subprocess.run(['/usr/bin/python3', str(script), str(args.weighted_manifest.resolve()),
                                 args.expected_sha256, args.node], timeout=30)
        if budget.returncode != 0:
            return budget.returncode
    return admit(directory, args.node)


if __name__ == '__main__':
    raise SystemExit(main())
