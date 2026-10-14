#!/usr/bin/env python3
# Code style: AI-code. AI-generated durable scheduler-universe night reconstruction controller.
"""Run immutable saved-GEN job lists at night; keep analysis release closed."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import time
import uuid

from shift_condor_native import NativeCondorError, native_env, query, run_condor
from shift_night_plan import load_epoch, load_plan, pinned_path, sha, valid_sha
from shift_night_policy import (LIVE_STATUSES, NIGHT_HOLD_REASON, capacity_decision,
                                night_window, observations_safe, validate_policy)

ATTRIBUTES = ['ClusterId', 'ProcId', 'JobStatus', 'JobUniverse', 'Owner', 'HoldReason',
              'NumJobStarts', 'ShiftNightCampaign', 'ShiftNightWaveNonce', 'ShiftNightEpoch',
              'ShiftNightJob', 'ShiftNightAdmissionStartEpoch', 'ShiftNightAdmissionStopEpoch',
              'ShiftNightAdmissionLeaseEpoch', 'ExitCode', 'ExitBySignal']


def read(path):
    return json.loads(Path(path).read_text())


def save(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + str(os.getpid()) + '.tmp')
    with temporary.open('w') as stream:
        json.dump(data, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def event_log(path):
    """Use only complete Condor event records; retained exit evidence survives history expiry."""
    path = Path(path)
    if not path.exists():
        return {'clusters': set(), 'terminal': {}}
    text = path.read_text()
    blocks = text.split('\n...\n')
    clusters, terminal = set(), {}
    for block in blocks[:-1]:
        match = re.match(r'\s*(\d{3}) \((\d+)\.(\d+)\.\d+\)', block)
        if not match:
            continue
        kind, cluster, process = int(match[1]), int(match[2]), int(match[3])
        clusters.add(cluster)
        if kind == 5:
            exit_match = re.search(r'Normal termination \(return value (\d+)\)', block)
            terminal[(cluster, process)] = int(exit_match[1]) if exit_match else 'signal_or_unknown'
        elif kind == 9:
            terminal[(cluster, process)] = 'removed'
    return {'clusters': clusters, 'terminal': terminal}


def validate_result(result, job, plan):
    """A process exit or output filename alone is never a completed original."""
    if result.get('job') != job['job'] or result.get('epoch') != job['epoch']:
        return 'Completion identity does not match frozen job'
    if result.get('night_admission_deferred'):
        return 'Admission deferred before reconstruction'
    if result.get('exit_code') != 0 or not result.get('complete'):
        return 'Worker did not finish successfully'
    if not result.get('exact_double_event_audit_passed'):
        return 'Exact double-weight/event audit is missing'
    for key, expected in [('selection_sha256', job['selection_sha256']),
                          ('event_contract_sha256', job['event_contract_sha256']),
                          ('source_gen_sha256', job['source_gen_sha256']),
                          ('response_contract_sha256', plan['data']['response_contract_sha256'])]:
        if result.get(key) != expected:
            return 'Completion authority mismatch: ' + key
    if result.get('events') != job['count'] or result.get('validated_tier_events') != {
            name: job['count'] for name in ('GEN', 'SIM', 'DIGIHLT', 'RECO', 'NANO')}:
        return 'Completed detector tiers do not cover every planned original'
    output_base = plan['data'].get('output_base',
                                  '/eos/user/j/jniedzie/shift_cmssw_support/ntuple_production/qcd_night_reco_20261014_v1')
    for kind in ('nano', 'evidence', 'receipt'):
        if not isinstance(result.get(kind + '_path'), str) or not result[kind + '_path'].startswith(output_base + '/'):
            return 'Output is outside the versioned support namespace: ' + kind
        expected = result.get(kind + '_sha256')
        if not valid_sha(expected) or result.get(kind + '_readback_sha256') != expected:
            return 'Whole-file EOS SHA readback failed or missing: ' + kind
    if result.get('analysis_release') is not False:
        return 'Detector-study production must not claim analysis release'
    return None


class NativeBackend:
    """Scheduling always happens on the scheduler host; audits only observe."""
    def __init__(self, root, policy):
        self.root, self.policy = root, policy

    def ads(self, constraint):
        return query(constraint, ATTRIBUTES, local_schedd=True, timeout=30)

    def command(self, command, cwd=None):
        return run_condor(command, local_schedd=True, timeout=30, cwd=cwd or self.root)

    def observation(self):
        # Only this helper is reused. It launches/polls one durable read-only
        # audit, whose frozen copy counts all vanilla jobs for this account.
        from run_shift_ntuple_controller import refresh_quota
        return refresh_quota(self.root)

    def afs_headroom(self):
        output = subprocess.run(['/usr/bin/fs', 'listquota', '-path', str(self.root)],
                                capture_output=True, text=True, timeout=20,
                                check=True, env=native_env(local_schedd=True)).stdout
        for line in output.splitlines():
            match = re.match(r'\s*\S+\s+(\d+)\s+(\d+)\s+', line)
            if match and int(match[1]) > 0:
                return max(0, int(match[1]) - int(match[2])) * 1024
        raise ValueError('Cannot verify AFS volume quota headroom')


class NightController:
    def __init__(self, root, backend=None):
        self.root = Path(root).resolve()
        self.deployment = read(self.root / 'deployment.json')
        for name, expected in self.deployment['files'].items():
            pinned_path(self.root, name, expected)
        self.policy = validate_policy(read(self.root / 'policy.json'))
        self.plan = load_plan(self.deployment['night_plan_path'])
        if self.plan['sha256'] != self.deployment['night_plan_sha256']:
            raise ValueError('Scientific night plan differs from frozen deployment')
        self.tag = self.plan['data']['tag']
        self.owner = self.policy['account_owner']
        self.backend = backend or NativeBackend(self.root, self.policy)
        self.state_path = self.root / 'controller_state.json'
        self.state = read(self.state_path) if self.state_path.exists() else {
            'phase': 'prepared', 'plan_sha256': self.plan['sha256'], 'completed_epochs': [],
            'capacity': {}, 'analysis_release': False}
        if self.state.get('plan_sha256') != self.plan['sha256']:
            raise ValueError('Controller state belongs to another scientific plan')
        self.epoch = None
        self.epoch_number = None

    def constraint(self):
        return 'Owner == ' + json.dumps(self.owner) + ' && ShiftNightCampaign == ' + json.dumps(self.tag)

    def persist(self, now, **updates):
        self.state.update(updates, checked_at_epoch=now, pid=os.getpid(), host=socket.gethostname(),
                          plan_sha256=self.plan['sha256'], analysis_release=False)
        save(self.state_path, self.state)

    def current_epoch(self):
        completed = self.state.get('completed_epochs', [])
        if completed != list(range(1, len(completed) + 1)):
            raise ValueError('Only a fully completed epoch prefix may advance')
        for number in completed:
            receipt = read(self.root / ('epoch_completions/epoch%02d.json' % number))
            if not receipt.get('operational_complete') or receipt.get('plan_sha256') != self.plan['sha256']:
                raise ValueError('Completed epoch has lost its pinned completion gate')
        number = len(completed) + 1
        if number > len(self.plan['data']['epochs']):
            return None
        if number != self.epoch_number:
            self.epoch = load_epoch(self.plan, number)
            self.epoch_number = number
        return self.epoch

    def waves(self, epoch, ads):
        """Keep unknown submission intents reserved; never infer that they failed."""
        answer, uncertain = [], []
        folder = self.root / ('waves/epoch%02d' % epoch['metadata']['epoch'])
        if not folder.exists():
            return answer, uncertain
        for path in sorted(folder.glob('*/intent.json')):
            intent = read(path)
            if intent.get('plan_sha256') != self.plan['sha256'] or intent.get('epoch') != self.epoch_number:
                raise ValueError('Submission intent belongs to another frozen plan')
            records = event_log(path.parent / 'events.log')
            submitted_path = path.parent / 'submission.json'
            submitted = read(submitted_path) if submitted_path.exists() else {}
            cluster = submitted.get('cluster')
            if cluster is None:
                candidates = {ad['ClusterId'] for ad in ads if ad.get('ShiftNightWaveNonce') == intent['nonce']}
                candidates.update(records['clusters'])
                if len(candidates) == 1:
                    cluster = candidates.pop()
                    save(submitted_path, {'cluster': cluster, 'nonce': intent['nonce'],
                                          'reconciled_from_retained_evidence': True})
                else:
                    uncertain.append(intent['nonce'])
            if cluster is not None and records['clusters'] - {cluster}:
                raise ValueError('Wave event log contains an unexpected cluster')
            answer.append({'intent': intent, 'cluster': cluster, 'records': records,
                           'folder': path.parent})
        return answer, uncertain

    def completion(self, epoch, waves, ads):
        completed, failed, deferred, pending_exit = {}, {}, set(), set()
        assigned = set()
        ad_by_identity = {(ad['ClusterId'], ad['ProcId']): ad for ad in ads}
        for wave in waves:
            for process, job_id in enumerate(wave['intent']['jobs']):
                if job_id not in epoch['by_job'] or job_id in assigned:
                    raise ValueError('An immutable job was submitted twice or outside its epoch')
                assigned.add(job_id)
                if wave['cluster'] is None:
                    continue
                job = epoch['by_job'][job_id]
                identity = (wave['cluster'], process)
                ad = ad_by_identity.get(identity)
                if ad is not None and (ad.get('ShiftNightJob') != job_id or ad.get('ShiftNightWaveNonce') != wave['intent']['nonce']):
                    raise ValueError('Scheduler job identity differs from its frozen submission wave')
                status_path = self.root / ('results/status%d.json' % job_id)
                terminal = wave['records']['terminal'].get(identity)
                if status_path.exists():
                    result = read(status_path)
                    if (result.get('night_admission_deferred') is True and result.get('exit_code') == 75 and
                            result.get('processed_events') == 0 and result.get('job') == job_id and
                            result.get('epoch') == self.epoch_number and
                            result.get('selection_sha256') == job['selection_sha256'] and ad is not None and
                            result.get('condor_num_job_starts') == ad.get('NumJobStarts')):
                        deferred.add(identity)
                        continue
                    error = validate_result(result, job, self.plan)
                    if error:
                        failed[job_id] = error
                    elif terminal == 0:
                        completed[job_id] = {'status_sha256': sha(status_path),
                                             'nano_path': result['nano_path'], 'nano_sha256': result['nano_sha256'],
                                             'events': job['count'], 'epoch': self.epoch_number,
                                             'event_contract_sha256': job['event_contract_sha256'],
                                             'cluster': identity[0], 'process': identity[1]}
                    else:
                        pending_exit.add(job_id)
                elif terminal is not None and terminal != 0:
                    failed[job_id] = 'Terminal Condor failure without a validated completion receipt'
                elif ad is not None and ad.get('JobStatus') in (3, 5) and ad.get('HoldReason') != NIGHT_HOLD_REASON:
                    failed[job_id] = 'Worker removed or held outside the night admission policy'
        return completed, failed, deferred, pending_exit, assigned

    def renew_admissions(self, ads, deferred, window, now, eligible_budget):
        """Update only idle or proved unprocessed calendar deferrals; never touch running jobs."""
        eligible = []
        for ad in ads:
            identity = (ad['ClusterId'], ad['ProcId'])
            if ad.get('JobStatus') == 1 or (ad.get('JobStatus') == 5 and (
                    ad.get('HoldReason') == NIGHT_HOLD_REASON or identity in deferred)):
                eligible.append(ad)
        eligible.sort(key=lambda ad: ad.get('ShiftNightJob', -1))
        denied = eligible[max(0, eligible_budget):]
        eligible = eligible[:max(0, eligible_budget)]
        for start in range(0, len(denied), 100):
            identities = ' || '.join('(ClusterId == %d && ProcId == %d)' % (ad['ClusterId'], ad['ProcId'])
                                     for ad in denied[start:start + 100])
            self.backend.command(['/usr/bin/condor_qedit', '-constraint',
                                  self.constraint() + ' && (' + identities + ') && JobStatus == 1',
                                  'ShiftNightAdmissionLeaseEpoch', '0'])
        for start in range(0, len(eligible), 100):
            batch = eligible[start:start + 100]
            identities = ' || '.join('(ClusterId == %d && ProcId == %d)' % (ad['ClusterId'], ad['ProcId']) for ad in batch)
            allowed_hold = 'HoldReason == ' + json.dumps(NIGHT_HOLD_REASON)
            proved = [ad for ad in batch if (ad['ClusterId'], ad['ProcId']) in deferred]
            if proved:
                allowed_hold += ' || (' + ' || '.join('(ClusterId == %d && ProcId == %d && NumJobStarts == %d)' % (
                    ad['ClusterId'], ad['ProcId'], ad['NumJobStarts']) for ad in proved) + ')'
            constraint = self.constraint() + ' && (' + identities + ') && (JobStatus == 1 || (JobStatus == 5 && (' + allowed_hold + ')))'
            values = {'ShiftNightAdmissionStartEpoch': window['start_epoch'],
                      'ShiftNightAdmissionStopEpoch': window['stop_epoch'],
                      'ShiftNightAdmissionLeaseEpoch': min(window['stop_epoch'], int(now) + self.policy['lease_seconds'])}
            intent_path = self.root / 'admission_operation.json'
            save(intent_path, {'operation': 'renew_admission', 'attempted_at_epoch': now,
                               'identities': [[ad['ClusterId'], ad['ProcId']] for ad in batch], 'values': values})
            command = ['/usr/bin/condor_qedit', '-constraint', constraint]
            for name, value in values.items():
                command.extend([name, str(value)])
            try:
                self.backend.command(command)
            except NativeCondorError:
                # Readback, rather than blind repetition, resolves an ambiguous edit.
                observed = self.backend.ads(self.constraint() + ' && (' + identities + ')')
                if any(ad.get('JobStatus') in (1, 5) and any(ad.get(name) != value for name, value in values.items()) for ad in observed):
                    raise
            observed = self.backend.ads(self.constraint() + ' && (' + identities + ')')
            release = [ad for ad in observed if ad.get('JobStatus') == 5 and
                       all(ad.get(name) == value for name, value in values.items()) and (
                           ad.get('HoldReason') == NIGHT_HOLD_REASON or (ad['ClusterId'], ad['ProcId']) in deferred)]
            for ad in release:
                release_constraint = self.constraint() + ' && ClusterId == %d && ProcId == %d && JobStatus == 5' % (ad['ClusterId'], ad['ProcId'])
                if ad.get('HoldReason') == NIGHT_HOLD_REASON:
                    release_constraint += ' && HoldReason == ' + json.dumps(NIGHT_HOLD_REASON)
                else:
                    release_constraint += ' && NumJobStarts == %d' % ad['NumJobStarts']
                save(intent_path, {'operation': 'release_unprocessed_admission', 'attempted_at_epoch': now,
                                   'identity': [ad['ClusterId'], ad['ProcId']]})
                self.backend.command(['/usr/bin/condor_release', '-constraint', release_constraint])
            save(intent_path, {'operation': 'complete', 'checked_at_epoch': now})

    def submit_wave(self, jobs, window, now):
        """Freeze exact jobs and submission intent before contacting Condor once."""
        nonce = uuid.uuid4().hex
        folder = self.root / ('waves/epoch%02d' % self.epoch_number) / nonce
        folder.mkdir(parents=True, exist_ok=False)
        job_list = folder / 'jobs.txt'
        lines = []
        for job in jobs:
            selection = job['selection_absolute_path']
            if any(character.isspace() for character in selection):
                raise ValueError('Condor sparse selection paths must not contain whitespace')
            lines.append('%d %d %d %d %d %s %s %s %s' % (
                job['job'], job['epoch'], job['source'], job['count'], job['seed'],
                selection, Path(selection).name, job['selection_sha256'], job['event_contract_sha256']))
        job_list.write_text('\n'.join(lines) + '\n')
        replacements = {'JOB_LIST': str(job_list), 'WAVE_NONCE': nonce,
                        'START_EPOCH': str(window['start_epoch']), 'STOP_EPOCH': str(window['stop_epoch']),
                        'LEASE_EPOCH': str(min(window['stop_epoch'], int(now) + self.policy['lease_seconds'])),
                        'EVENT_LOG': str(folder / 'events.log'), 'SUPERVISOR_ROOT': str(self.root),
                        'CAMPAIGN_TAG': self.tag}
        description = (self.root / 'worker/worker.sub.in').read_text()
        for key, value in replacements.items():
            description = description.replace('@' + key + '@', value)
        if re.search(r'@[A-Z_]+@', description):
            raise ValueError('Worker submit template has unresolved controller fields')
        (folder / 'wave.sub').write_text(description)
        intent = {'nonce': nonce, 'epoch': self.epoch_number, 'plan_sha256': self.plan['sha256'],
                  'jobs': [job['job'] for job in jobs], 'created_at_epoch': now,
                  'job_list_sha256': sha(job_list), 'submit_sha256': sha(folder / 'wave.sub')}
        save(folder / 'intent.json', intent)
        response = self.backend.command(['/usr/bin/condor_submit', '-terse', str(folder / 'wave.sub')], cwd=folder)
        match = re.fullmatch(r'(\d+)\.(\d+)\s*-\s*(\d+)\.(\d+)\s*', response)
        if not match or match[1] != match[3] or int(match[2]) != 0 or int(match[4]) + 1 != len(jobs):
            raise NativeCondorError('Ambiguous wave submission; its immutable intent remains reserved')
        save(folder / 'submission.json', {'cluster': int(match[1]), 'nonce': nonce, 'submitted_at_epoch': now})

    def step(self, now=None, *, dry_run=False):
        now = time.time() if now is None else now
        epoch = self.current_epoch()
        if epoch is None:
            self.persist(now, phase='complete', health='operational_complete', analysis_release=False)
            return True
        window = night_window(now, self.policy)
        ads = self.backend.ads(self.constraint())
        waves, uncertain = self.waves(epoch, ads)
        completed, failed, deferred, pending_exit, assigned = self.completion(epoch, waves, ads)
        expected = set(epoch['by_job'])
        if set(completed) == expected:
            completion = {'operational_complete': True, 'epoch': self.epoch_number,
                          'plan_sha256': self.plan['sha256'], 'draw_manifest_sha256': epoch['metadata']['draw_manifest_sha256'],
                          'completed_at_epoch': now, 'events': epoch['metadata']['work_events'],
                          'jobs': len(completed), 'completed_jobs': completed,
                          'analysis_release': False, 'normalization_ready': False,
                          'physics_limitation': 'The fixed V10 parent-hadron transport is not qualified for physical closure.'}
            save(self.root / ('epoch_completions/epoch%02d.json' % self.epoch_number), completion)
            self.state['completed_epochs'].append(self.epoch_number)
            self.persist(now, phase='epoch_complete', epoch=self.epoch_number, health='healthy')
            return False
        observation = self.backend.observation() if not dry_run and (window['inside_night'] or window['start_epoch'] - now <= 900) else (
            read(self.root / 'last_quota.json') if (self.root / 'last_quota.json').exists() else {})
        verified, observation_reason = observations_safe(observation, self.policy, now,
                                                        reserved_workers=self.policy['worker_ceiling'])
        external = 0
        if verified:
            if observation.get('account_owner') != self.owner or observation.get('account_pool') != self.policy['account_pool']:
                verified, observation_reason = False, 'Quota account identity differs from this deployment'
            else:
                external = sum(ad.get('JobStatus') in LIVE_STATUSES and ad.get('ShiftNightCampaign') != self.tag
                               for ad in observation['account_workers'])
        if verified and self.policy.get('minimum_AFS_free_bytes', 0):
            cache_path = self.root / 'last_AFS_quota.json'
            cache = read(cache_path) if cache_path.exists() else {}
            if not 0 <= now - cache.get('checked_at_epoch', 0) <= 300:
                cache = {'checked_at_epoch': now, 'free_bytes': self.backend.afs_headroom()}
                save(cache_path, cache)
            if cache['free_bytes'] < self.policy['minimum_AFS_free_bytes']:
                verified, observation_reason = False, 'AFS quota cannot safely retain compact statuses and controller state'
        # Reserve all assigned originals that lack terminal proof, even when
        # absent from a queue snapshot. Unknown disappearance cannot free slots.
        unresolved = assigned - set(completed) - set(failed)
        live_by_job = {ad.get('ShiftNightJob'): ad for ad in ads if ad.get('JobStatus') in LIVE_STATUSES}
        reservations = [live_by_job.get(job, {'JobStatus': 1}) for job in unresolved]
        capacity = capacity_decision(self.state.get('capacity', {}), self.policy, now=now,
                                     completed_jobs=sum(epoch['metadata']['work_jobs'] for epoch in self.plan['data']['epochs']
                                                        if epoch['epoch'] in self.state['completed_epochs']) + len(completed),
                                     own_ads=reservations, external_workers=external,
                                     observations_verified=verified, healthy=not failed and not uncertain)
        allow = window['admissions_allowed'] and verified and not failed and not uncertain and not (self.root / 'stop_requested.json').exists()
        phase = 'night_running' if allow else ('draining' if window['inside_night'] else 'waiting_for_night')
        if failed or uncertain:
            phase = 'attention_required'
        self.persist(now, phase=phase, epoch=self.epoch_number, window=window, capacity=capacity,
                     health='healthy' if allow else 'admissions_paused', observation_reason=observation_reason,
                     completed_jobs=len(completed), expected_jobs=len(expected),
                     completed_events=sum(row['events'] for row in completed.values()),
                     failed_jobs=failed, uncertain_submission_nonces=uncertain,
                     pending_terminal_evidence=sorted(pending_exit), submitted_jobs=len(assigned),
                     queued_or_running_workers=len(live_by_job), active_admission_deferrals=len(deferred), dry_run=dry_run)
        if allow and not dry_run:
            own_running = sum(ad.get('JobStatus') in (2, 6, 7) for ad in ads)
            missing_reservations = len(unresolved - set(live_by_job) - {
                ad.get('ShiftNightJob') for ad in ads if ad.get('JobStatus') == 5})
            eligible_budget = max(0, min(capacity['current_capacity'], self.policy['worker_ceiling'] - external)
                                  - own_running - missing_reservations)
            self.renew_admissions(ads, deferred, window, now, eligible_budget)
            count = capacity['available_new_slots']
            pending = [job for job in epoch['jobs'] if job['job'] not in assigned]
            if count and pending:
                self.submit_wave(pending[:count], window, now)
        return False


def watchdog(root, policy):
    """Report supervisor faults; expiring worker leases enforce the admission stop."""
    state_path = root / 'controller_state.json'
    while True:
        now = time.time()
        state = read(state_path) if state_path.exists() else {}
        stale = now - state.get('checked_at_epoch', now) > policy['lease_seconds']
        save(root / 'watchdog_state.json', {'checked_at_epoch': now,
                                           'controller_heartbeat_epoch': state.get('checked_at_epoch'),
                                           'controller_phase': state.get('phase'), 'controller_stale': stale,
                                           'worker_action': 'none; expired admissions prevent new starts',
                                           'analysis_release': False})
        if state.get('phase') == 'complete':
            return
        time.sleep(policy['poll_seconds'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('supervisor', type=Path)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--watchdog', action='store_true')
    args = parser.parse_args()
    root = args.supervisor.resolve()
    role = 'watchdog' if args.watchdog else 'controller'
    lock = (root / (role + '.lock')).open('a+')
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    def handoff(signum, frame):
        save(root / (role + '_handoff.json'), {'signal': signum, 'at_epoch': time.time(),
                                               'worker_action': 'none; running jobs drain'})
        raise SystemExit(85)
    signal.signal(signal.SIGTERM, handoff)
    try:
        controller = NightController(root)
        if args.watchdog:
            watchdog(root, controller.policy)
            return
        while True:
            try:
                complete = controller.step(dry_run=args.dry_run)
            except Exception as error:
                controller.persist(time.time(), phase='observer_fault', health='unknown',
                                   observer_error=repr(error), worker_action='none; leases expire')
                if args.once:
                    raise
                complete = False
            if complete or args.once:
                return
            time.sleep(controller.policy['poll_seconds'])
    except (ValueError, OSError) as error:
        save(root / (role + '_startup_fault.json'), {'error': repr(error), 'at_epoch': time.time(),
                                                     'worker_action': 'none; leases expire'})
        raise SystemExit(78)
    finally:
        lock.close()


if __name__ == '__main__':
    main()
