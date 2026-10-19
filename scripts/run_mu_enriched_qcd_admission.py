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
import hashlib
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
    if not policy['worker_ceiling'] <= policy['account_ceiling'] <= 10000:
        raise ValueError('Reviewed admission account ceiling must be at most 10000')
    cap = policy.get('maximum_outstanding_publication_bytes')
    if cap is not None and (type(cap) is not int or cap <= 0):
        raise ValueError('The optional outstanding-publication cap needs a positive integer')
    if type(policy.get('reserve_full_worker_ceiling', True)) is not bool:
        raise ValueError('reserve_full_worker_ceiling needs an explicit boolean')
    for key in ('account_owner', 'account_pool', 'schedd_name', 'suite_tag'):
        if not isinstance(policy.get(key), str) or not policy[key]:
            raise ValueError('Admission policy needs explicit ' + key)
    return policy


def decision(policy, observation, local_ads, reservations, node, now, afs_free):
    """Compute an admission from verified observations without mutating state."""
    result = {'node': node, 'checked_at_epoch': now, 'admitted': False}
    reserve_full_ceiling = policy.get('reserve_full_worker_ceiling', True)
    if type(reserve_full_ceiling) is not bool:
        raise ValueError('reserve_full_worker_ceiling needs an explicit boolean')
    for key in ('quota_observation_started_at_epoch', 'quota_checked_at_epoch',
                'remote_account_checked_at_epoch'):
        stamp = observation.get(key)
        if (type(stamp) not in (int, float) or not math.isfinite(stamp)
                or not 0 <= now - stamp <= policy['observation_max_age_seconds']):
            return result | {'reason': 'Storage/account observation is missing or stale'}
    if observation['quota_observation_started_at_epoch'] > observation['quota_checked_at_epoch']:
        return result | {'reason': 'EOS observation times are inconsistent'}
    minimum_bytes, minimum_files = policy['minimum_free_bytes'], policy['minimum_free_files']
    shared_capacity = None
    if policy.get('external_storage_reservations'):
        peer_commitments = observation.get('external_storage_commitments', {})
        stamp = peer_commitments.get('checked_at_epoch')
        if (peer_commitments.get('complete') is not True
                or peer_commitments.get('authority_contracts_sha256') != hashlib.sha256(
                    json.dumps(policy['external_storage_reservations'], sort_keys=True).encode()).hexdigest()
                or type(stamp) not in (int, float) or not math.isfinite(stamp)
                or not 0 <= now - stamp <= policy['observation_max_age_seconds']
                or stamp > observation['quota_observation_started_at_epoch']
                or type(peer_commitments.get('required_free_bytes')) is not int
                or type(peer_commitments.get('required_free_files')) is not int
                or peer_commitments['required_free_bytes'] < 0 or peer_commitments['required_free_files'] < 0):
            return result | {'reason': 'External storage commitments are missing, stale or inconsistent'}
        # The external sealed guard already includes its own safety floors.
        # Keep both floors by taking their maximum, rather than adding twice.
        minimum_bytes = max(minimum_bytes, peer_commitments['required_free_bytes'])
        minimum_files = max(minimum_files, peer_commitments['required_free_files'])
        shared_capacity = peer_commitments.get('stream_capacity')
        if shared_capacity is not None and observation.get('stream_peer_writers_verified') is not True:
            return result | {'reason': 'Shared peer writer coverage or old-SM drain is unknown',
                             'peer_writer_error': observation.get('stream_peer_writer_error')}
    result.update(required_free_bytes=minimum_bytes, required_free_files=minimum_files)
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
    own_live = [ad for ad in live if ad.get('ShiftMuEnrichedQCD') is True
                and ad.get('ShiftSuiteTag') == policy['suite_tag']]
    own_held = [ad for ad in local_ads if ad.get('JobUniverse') == 5 and ad.get('JobStatus') == 5
                and ad.get('ShiftMuEnrichedQCD') is True and ad.get('ShiftSuiteTag') == policy['suite_tag']]
    # A held payload keeps its whole output claim until explicitly retired.
    # Count it toward our own cap conservatively, but not as external CPU use.
    own = own_live + own_held
    external = len(live) - len(own_live) + len(remote_ads)
    queued_nodes = {ad.get('ShiftMuEnrichedNodeId') for ad in own
                    if isinstance(ad.get('ShiftMuEnrichedNodeId'), str)}
    retained, pending, completed_since_quota = {}, set(), set()
    for key, value in reservations.items():
        if not isinstance(value, dict) or type(value.get('at_epoch')) not in (int, float):
            raise ValueError('Invalid persistent PRE reservation')
        record = dict(value)
        if key in queued_nodes:
            record.update(seen_queued=True, last_seen_at_epoch=now)
            record.pop('missing_since_epoch', None)
        elif (not record.get('seen_queued')
              and 0 <= now - record['at_epoch'] < policy['pre_reservation_seconds']):
            pending.add(key)
        else:
            # A disappearing worker may just have published ROOT files. Its
            # bytes cannot be reused against an older cached EOS observation.
            # Even a very short worker that escaped our queue observations is
            # retained until a quota read after its PRE reservation expires.
            record.setdefault('missing_since_epoch', now)
            if observation['quota_observation_started_at_epoch'] > record['missing_since_epoch']:
                continue
            completed_since_quota.add(key)
        retained[key] = record
    for key in queued_nodes:
        retained.setdefault(key, {'at_epoch': now})
        retained[key].update(seen_queued=True, last_seen_at_epoch=now)
    reserved = len(own) + len(pending) + int(node not in pending and node not in queued_nodes)
    storage_reserved = reserved + len(completed_since_quota - {node})
    result.update(external_workers=external, own_workers=len(own),
                  pending_PRE_reservations=len(pending), reserved_workers=reserved,
                  completed_since_quota_reservations=len(completed_since_quota),
                  storage_reserved_workers=storage_reserved, reservations=retained)
    if reserved > policy['worker_ceiling']:
        return result | {'reason': 'This production has reached its worker ceiling'}
    remote_states_known = all(isinstance(ad, dict) and type(ad.get('JobStatus')) is int
                              and ad['JobStatus'] in (1, 2, 6, 7) for ad in remote_ads)
    other_local = [ad for ad in live if not (ad.get('ShiftMuEnrichedQCD') is True
                   and ad.get('ShiftSuiteTag') == policy['suite_tag'])]
    external_running = (sum(ad['JobStatus'] in (2, 6, 7) for ad in other_local)
                        + sum(ad['JobStatus'] in (2, 6, 7) for ad in remote_ads)
                        if remote_states_known else None)
    result.update(reserve_full_worker_ceiling=reserve_full_ceiling,
                  external_queued_workers=external,
                  external_running_or_transitioning_workers=external_running,
                  external_idle_workers=(external - external_running
                                         if external_running is not None else None))
    # Existing DAGs reserve their whole possible wave. Streaming admits one
    # task at a time, counting our queued/held/pending claims conservatively.
    # Other productions' idle jobs do not consume the site's running ceiling.
    if not reserve_full_ceiling and external_running is None:
        return result | {'reason': 'Remote account execution capacity is unknown'}
    external_capacity = external if reserve_full_ceiling else external_running
    account_reservation = policy['worker_ceiling'] if reserve_full_ceiling else reserved
    result.update(external_capacity_workers=external_capacity,
                  account_reserved_workers=external_capacity + account_reservation)
    if external_capacity + account_reservation > policy['account_ceiling']:
        return result | {'reason': 'Other account jobs leave insufficient worker capacity'}
    extra = observation.get('extra_stage_reserved_bytes', 0)
    if type(extra) is not int or extra < 0:
        return result | {'reason': 'Additional stage publication reservation is unknown'}
    outstanding = storage_reserved * policy['reserved_bytes_per_worker'] + extra
    result['outstanding_publication_bytes'] = outstanding
    cap = policy.get('maximum_outstanding_publication_bytes')
    if cap is not None and outstanding > cap:
        return result | {'reason': 'This production has reached its outstanding-publication byte cap',
                         'maximum_outstanding_publication_bytes': cap}
    for key in ('free_bytes', 'free_files'):
        if type(observation.get(key)) is not int or observation[key] < 0:
            return result | {'reason': 'Storage headroom is unknown'}
    result['storage_mode'] = 'full'
    if (shared_capacity is not None
            and observation['free_bytes'] < minimum_bytes + outstanding):
        if observation.get('stream_requires_full_peer_mode') is True:
            return result | {'reason': 'An active supplement writer requires full-future peer headroom'}
        constrained = shared_capacity.get('constrained_required_free_bytes')
        protected = shared_capacity.get('protected_stream_bytes')
        if (type(constrained) is not int or constrained < 0 or constrained > peer_commitments['required_free_bytes']
                or type(protected) is not int or protected <= 0
                or cap is None or cap > protected or outstanding > protected):
            return result | {'reason': 'Shared constrained-publication authority is inconsistent'}
        result.update(storage_mode='constrained_within_peer_QCD_allowance',
                      full_peer_required_free_bytes=peer_commitments['required_free_bytes'])
        minimum_bytes = max(policy['minimum_free_bytes'], constrained)
        result['required_free_bytes'] = minimum_bytes
    if observation['free_bytes'] < minimum_bytes + outstanding:
        return result | {'reason': 'EOS byte reservation would consume the retained reserve'}
    if observation['free_files'] < minimum_files + storage_reserved * policy['reserved_files_per_worker']:
        return result | {'reason': 'EOS file reservation would consume the retained reserve'}
    if type(afs_free) is not int or afs_free < policy['minimum_AFS_free_bytes']:
        return result | {'reason': 'AFS free space is missing or below its retained reserve'}
    if node not in queued_nodes:
        retained[node] = {'at_epoch': now}
    return result | {'admitted': True, 'reason': 'Fresh account and storage checks pass',
                     'reservations': retained, 'AFS_free_bytes': afs_free,
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
        deadline = time.monotonic() + 5
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    return 75
                time.sleep(0.05)
        now = time.time()
        try:
            if (directory / 'stop_requested.json').exists() or (root / 'stop_requested.json').exists():
                raise ValueError('Production admission has an explicit stop request')
            observation = refresh_quota(root)
            local_ads = query('Owner == ' + json.dumps(policy['account_owner'])
                              + ' && JobUniverse == 5 && (JobStatus == 1 || JobStatus == 2 || JobStatus == 5 || JobStatus == 6 || JobStatus == 7)',
                              ['ClusterId', 'ProcId', 'JobUniverse', 'JobStatus',
                               'ShiftMuEnrichedQCD', 'ShiftSuiteTag', 'ShiftMuEnrichedNodeId'],
                              timeout=20, local_schedd=True)
            reservation_path = root / 'pre_reservations.json'
            reservations = json.loads(reservation_path.read_text()) if reservation_path.exists() else {}
            result = decision(policy, observation, local_ads, reservations, node,
                              now, afs_headroom(directory))
            if 'reservations' in result:
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
