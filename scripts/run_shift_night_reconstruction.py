#!/usr/bin/env python3
# Code style: AI-code. AI-generated durable scheduler-universe night reconstruction controller.
"""Run immutable saved-GEN job lists at night; keep analysis release closed."""
import argparse
import fcntl
from itertools import zip_longest
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import tarfile
import time
import uuid

from shift_condor_native import (NativeCondorError, SCHEDD_ADDRESS_FILE, SCHEDD_AD_FILE,
                                 native_env, query, run_condor)
from shift_night_plan import load_epoch, load_plan, pinned_path, sha, valid_sha
from shift_night_policy import (LIVE_STATUSES, NIGHT_HOLD_REASON, capacity_decision,
                                night_window, observations_safe, validate_policy)

ATTRIBUTES = ['ClusterId', 'ProcId', 'JobStatus', 'JobUniverse', 'Owner', 'HoldReason',
              'HoldReasonCode',
              'NumJobStarts', 'ShiftNightCampaign', 'ShiftNightWaveNonce', 'ShiftNightEpoch',
              'ShiftNightJob', 'ShiftNightAdmissionStartEpoch', 'ShiftNightAdmissionStopEpoch',
              'ShiftNightAdmissionLeaseEpoch', 'ExitCode', 'ExitBySignal', 'StageOutFinish',
              'ShiftNightReturnedBytes', 'ShiftNightFailureKind', 'ShiftNightAdmissionDeferred',
              'ShiftNightProcessedEvents', 'ShiftNightDeferredNumJobStarts',
              'ShiftNightSelectionSHA256', 'ShiftNightEventContractSHA256', 'TransferOutputStats',
              'ShiftNightScienceStarted', 'ShiftNightBootstrapInProgress',
              'ShiftNightPublishedOutputBytes', 'ShiftNightPublishedOutputFiles',
              'ShiftNightSourceGENSHA256', 'ShiftNightResponseContractSHA256']


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


def source_round_robin(jobs):
    """Change execution order only, spreading work across original GEN files."""
    groups = {}
    for job in jobs:
        groups.setdefault(job['source'], []).append(job)
    # The frozen source catalogue lists harder bins last. Start there while
    # visiting every source before taking a second block from the same file.
    ordered = [groups[source] for source in sorted(groups, reverse=True)]
    return [job for row in zip_longest(*ordered) for job in row if job is not None]


def operational_gates(root):
    """An absent, false, mismatched or no-longer-reproducible proof never arms workers."""
    root = Path(root)
    path = root / 'required_operational_gates.json'
    if not path.is_file():
        return False, {'policy': 'Required operational gate policy is missing'}
    try:
        policy = read(path)
        if policy.get('schema') != 'shift-night-required-operational-gates-v1' or not policy.get('gates'):
            raise ValueError('Invalid or empty operational gate policy')
        problems = {}
        for gate in policy['gates']:
            name = gate['name']
            receipt_path = Path(gate['receipt'])
            if receipt_path.is_absolute() or '..' in receipt_path.parts:
                raise ValueError('Operational gate receipt must stay inside its deployment')
            try:
                receipt = read(root / receipt_path)
                if (receipt.get('schema') != 'shift-night-operational-gate-v1' or
                        receipt.get('gate') != name or receipt.get('passed') is not True):
                    raise ValueError('Receipt does not explicitly pass this named gate')
                for key, expected in gate['expected_authorities'].items():
                    if not valid_sha(expected) or receipt.get('authorities', {}).get(key) != expected:
                        raise ValueError('Receipt authority mismatch: ' + key)
                for key in gate['required_checks']:
                    if receipt.get('checks', {}).get(key) is not True:
                        raise ValueError('Required native check did not pass: ' + key)
                evidence = receipt.get('evidence', {})
                for key in gate['required_evidence']:
                    item = evidence.get(key, {})
                    if not valid_sha(item.get('sha256')):
                        raise ValueError('Required hashed evidence is missing: ' + key)
                    pinned_path(root, item.get('path', ''), item['sha256'])
            except (OSError, ValueError, TypeError, KeyError) as error:
                problems[name] = str(error)
        return not problems, problems
    except (OSError, ValueError, TypeError, KeyError) as error:
        return False, {'policy': str(error)}


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


def isolated_computation_failure(result, job, plan, terminal):
    """Quarantine only a proved computation failure with durable recovery evidence."""
    if (terminal in (None, 0) or result.get('failure_kind') != 'computation' or
            result.get('resolved_response_config_passed') is not True or
            result.get('job') != job['job'] or result.get('epoch') != job['epoch']):
        return False
    for key, expected in [('selection_sha256', job['selection_sha256']),
                          ('event_contract_sha256', job['event_contract_sha256']),
                          ('source_gen_sha256', job['source_gen_sha256']),
                          ('response_contract_sha256', plan['data']['response_contract_sha256'])]:
        if result.get(key) != expected:
            return False
    output_base = plan['data'].get('output_base',
                                  '/eos/user/j/jniedzie/shift_cmssw_support/ntuple_production/qcd_night_reco_20261014_v1')
    kinds = ['evidence', 'receipt']
    if result.get('validated_tier_events', {}).get('RECO') == job['count']:
        kinds.append('reco')
    for kind in kinds:
        if (not isinstance(result.get(kind + '_path'), str) or
                not result[kind + '_path'].startswith(output_base + '/') or
                not valid_sha(result.get(kind + '_sha256')) or
                result.get(kind + '_readback_sha256') != result[kind + '_sha256']):
            return False
    return True


class NativeBackend:
    """Scheduling always happens on the scheduler host; audits only observe."""
    def __init__(self, root, policy):
        self.root, self.policy = root, policy
        self.local = Path(SCHEDD_ADDRESS_FILE).is_file() and Path(SCHEDD_AD_FILE).is_file()

    def ads(self, constraint):
        for attempt in range(3):
            try:
                return query(constraint, ATTRIBUTES, local_schedd=self.local, timeout=10,
                             schedd=None if self.local else self.policy['schedd_name'])
            except NativeCondorError:
                if attempt == 2:
                    raise
                time.sleep(1)

    def command(self, command, cwd=None, timeout=30):
        if not self.local:
            raise NativeCondorError('Night scheduling mutations require the scheduler-universe host')
        return run_condor(command, local_schedd=True, timeout=timeout, cwd=cwd or self.root)

    def observation(self):
        # Only this helper is reused. It launches/polls one durable read-only
        # audit, whose frozen copy counts all vanilla jobs for this account.
        from run_shift_ntuple_controller import refresh_quota
        if not self.local:
            raise NativeCondorError('Night quota scheduling requires the scheduler-universe host')
        observation = dict(refresh_quota(self.root))
        # Discovery needs the execute-side credentials. Local queue access
        # needs the verified scheduler address; keep those operations apart.
        if observation.get('complete') and observation.get('remote_account_coverage_verified'):
            if observation.get('excluded_local_schedd') != self.policy['schedd_name']:
                raise NativeCondorError('Account audit excluded a different local scheduler')
            workers = self.ads('Owner == ' + json.dumps(self.policy['account_owner']) + ' && ' +
                'JobUniverse == 5 && (JobStatus == 1 || JobStatus == 2 || JobStatus == 6 || JobStatus == 7)',
                )
            observation.update(account_coverage_verified=True,
                               account_checked_at_epoch=min(time.time(), observation['remote_account_checked_at_epoch']),
                               account_workers=workers + observation['remote_account_workers'])
        return observation

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
        self.epochs = {}
        self.epoch_number = None
        self.proved_completions = {}
        self.retained_completions = {}
        retained_path = self.root / 'retained_completions.json'
        if retained_path.exists():
            if 'retained_completions.json' not in self.deployment['files']:
                raise ValueError('Retained completions must be frozen in the deployment manifest')
            retained = read(retained_path)
            if retained.get('plan_sha256') != self.plan['sha256']:
                raise ValueError('Retained completions belong to another frozen plan')
            for item in retained['completions']:
                job = self.current_epoch(item['epoch'])['by_job'][item['job']]
                result = read(pinned_path(self.root, item['status_path'], item['status_sha256']))
                retrieval = read(pinned_path(self.root, item['retrieval_path'], item['retrieval_sha256']))
                native = retrieval['completed_ad']
                if validate_result(result, job, self.plan) or not retrieval.get('download_complete') or (
                        retrieval.get('status_sha256') != item['status_sha256'] or
                        native.get('ShiftNightJob') != job['job'] or
                        native.get('ShiftNightSelectionSHA256') != job['selection_sha256'] or
                        native.get('ExitBySignal') is True or native.get('JobStatus') != 4):
                    raise ValueError('Retained completion lost exact science or native retrieval proof')
                if native.get('ExitCode') != 0 and not (
                        native.get('ExitCode') == 73 and result.get('chirp_reporting_error') and
                        result.get('durable_EOS_publication_passed') is True and
                        retrieval.get('rescue_members') == []):
                    raise ValueError('Only a proved final-report-only failure can reuse nonzero terminal science')
                self.retained_completions[job['job']] = {
                    'status_sha256': item['status_sha256'], 'nano_path': result['nano_path'],
                    'nano_sha256': result['nano_sha256'], 'events': job['count'], 'epoch': job['epoch'],
                    'event_contract_sha256': job['event_contract_sha256'],
                    'cluster': native['ClusterId'], 'process': native['ProcId'],
                    'retained_science_not_repeated': True,
                    'native_reporting_only_exit': native.get('ExitCode')}

    def constraint(self):
        return ('Owner == ' + json.dumps(self.owner) + ' && ShiftNightCampaign == ' + json.dumps(self.tag) +
                ' && JobUniverse == 5 && ShiftNightJob =!= UNDEFINED')

    def persist(self, now, **updates):
        self.state.update(updates, checked_at_epoch=now, pid=os.getpid(), host=socket.gethostname(),
                          plan_sha256=self.plan['sha256'], analysis_release=False)
        save(self.state_path, self.state)

    def current_epoch(self, number=None):
        completed = self.state.get('completed_epochs', [])
        if completed != sorted(set(completed)) or any(
                type(value) is not int or not 1 <= value <= len(self.plan['data']['epochs']) for value in completed):
            raise ValueError('Operational completed-epoch identities are invalid')
        for completed_number in completed:
            receipt = read(self.root / ('epoch_completions/epoch%02d.json' % completed_number))
            if not receipt.get('operational_complete') or receipt.get('plan_sha256') != self.plan['sha256']:
                raise ValueError('Completed epoch has lost its pinned completion gate')
        number = number if number is not None else self.state.get('highest_admitted_epoch', 1)
        if number > len(self.plan['data']['epochs']):
            return None
        if number not in self.epochs:
            self.epochs[number] = load_epoch(self.plan, number)
        return self.epochs[number]

    def waves(self, epoch, ads):
        """Keep unknown submission intents reserved; never infer that they failed."""
        answer, uncertain = [], []
        folder = self.root / ('waves/epoch%02d' % epoch['metadata']['epoch'])
        if not folder.exists():
            return answer, uncertain
        for path in sorted(folder.glob('*/intent.json')):
            intent = read(path)
            if intent.get('plan_sha256') != self.plan['sha256'] or intent.get('epoch') != epoch['metadata']['epoch']:
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

    def retrieve_outputs(self, ads, now):
        """Pull one proved, size-bounded completed sandbox at a time from the schedd.

        Publication failures can retain RECO in a large rescue archive. Unknown
        sizes and insufficient AFS room leave it in Condor's spool; a controller
        interruption must never trigger a bulk download or science rerun.
        """
        blocked, errors = {}, {}
        deadline = time.monotonic() + self.policy.get('output_retrieval_time_budget_seconds', 30)
        candidates = [ad for ad in ads if ad.get('JobStatus') == 4]
        candidates.sort(key=lambda ad: ad.get('ShiftNightJob', -1))
        count = 0
        for ad in candidates:
            job_id, epoch_number = ad.get('ShiftNightJob'), ad.get('ShiftNightEpoch')
            if type(job_id) is not int or type(epoch_number) is not int:
                raise ValueError('Spooled worker has no frozen job/epoch identity')
            epoch = self.current_epoch(epoch_number)
            job = epoch['by_job'].get(job_id)
            if job is None:
                raise ValueError('Spooled worker is outside its immutable epoch')
            matching = [wave for wave in self.waves(epoch, ads)[0]
                        if wave['cluster'] == ad['ClusterId'] and
                        wave['intent']['nonce'] == ad.get('ShiftNightWaveNonce') and
                        len(wave['intent']['jobs']) > ad['ProcId'] and
                        wave['intent']['jobs'][ad['ProcId']] == job_id]
            if len(matching) != 1 or ad.get('Owner') != self.owner:
                raise ValueError('Spooled worker differs from its retained submission intent')
            path = self.root / ('spool_retrievals/job%d.json' % job_id)
            previous = read(path) if path.exists() else {}
            if previous.get('download_complete'):
                continue
            size = ad.get('ShiftNightReturnedBytes')
            native_stats = ad.get('TransferOutputStats')
            if isinstance(native_stats, dict):
                native_sizes = [native_stats.get(key) for key in
                                ('CedarSizeBytesTotal', 'CedarSizeBytesLastRun')]
                if all(type(value) is int and value >= 0 for value in native_sizes):
                    native_size = max(native_sizes)
                    size = max(native_size, size) if type(size) is int and size >= 0 else native_size
            failure = ad.get('ShiftNightFailureKind')
            if failure not in (None, '', 'none', 'computation'):
                errors[job_id] = 'Worker reports a common failure: ' + str(failure)
            published_bytes, published_files = ad.get('ShiftNightPublishedOutputBytes'), ad.get('ShiftNightPublishedOutputFiles')
            if ((type(published_bytes) is int and published_bytes > self.policy['reserved_bytes_per_worker']) or
                    (type(published_files) is int and published_files > self.policy['reserved_files_per_worker'])):
                errors[job_id] = 'Published worker output exceeded the frozen quota reservation; review sizing before admissions'
            if type(size) is not int or size < 0:
                blocked[job_id] = 'Returned sandbox size is unknown; retained in scheduler spool'
                continue
            if count >= self.policy.get('maximum_output_retrievals_per_poll', 8) or time.monotonic() >= deadline:
                continue
            free = self.backend.afs_headroom()
            reserve = max(size, self.policy.get('minimum_output_retrieval_reserve_bytes', 2 * 1024**2))
            if free < reserve + self.policy['minimum_AFS_free_bytes']:
                blocked[job_id] = 'AFS cannot reserve this returned sandbox; retained in scheduler spool'
                continue
            receipt = {'job': job_id, 'epoch': epoch_number, 'wave_nonce': ad['ShiftNightWaveNonce'],
                       'cluster': ad['ClusterId'], 'process': ad['ProcId'],
                       'selection_sha256': job['selection_sha256'],
                       'event_contract_sha256': job['event_contract_sha256'],
                       'completed_ad': ad, 'reserved_bytes': reserve, 'afs_free_before_bytes': free,
                       'attempted_at_epoch': now, 'download_complete': False}
            save(path, receipt)
            identity = '%d.%d' % (ad['ClusterId'], ad['ProcId'])
            # Precisely one cluster.proc: never -all, account-wide, or a cluster.
            self.backend.command(['/usr/bin/condor_transfer_data', identity],
                                 timeout=max(1, min(30, deadline - time.monotonic())))
            status = self.root / ('results/status%d.json' % job_id)
            if not status.is_file():
                raise ValueError('Output retrieval did not return the exact worker status')
            result = read(status)
            if result.get('job') != job_id or result.get('selection_sha256') != job['selection_sha256']:
                raise ValueError('Retrieved status differs from its immutable job')
            rescue = self.root / ('results/job%d_rescue_payload.tar.gz' % job_id)
            if not rescue.is_file():
                raise ValueError('Output retrieval did not preserve the worker rescue archive')
            with tarfile.open(rescue, 'r:gz') as archive:
                rescue_members = [member.name for member in archive.getmembers()]
            if result.get('complete') and rescue_members:
                raise ValueError('Successful worker unexpectedly retained unpublished rescue tiers')
            receipt.update(download_complete=True, downloaded_at_epoch=time.time(),
                           status_sha256=sha(status), rescue_sha256=sha(rescue),
                           rescue_bytes=rescue.stat().st_size, rescue_members=rescue_members,
                           rescue_path=str(rescue), afs_free_after_bytes=self.backend.afs_headroom())
            save(path, receipt)
            count += 1
        return blocked, errors

    def reap_verified_outputs(self, completed, ads, now):
        """Retire only exact completed queue records whose science is durable on EOS."""
        ad_by_job = {ad.get('ShiftNightJob'): ad for ad in ads if ad.get('JobStatus') == 4}
        count = 0
        for job_id, proof in completed.items():
            ad = ad_by_job.get(job_id)
            if ad is None or count >= self.policy.get('maximum_completed_reaps_per_poll', 20):
                continue
            retrieval_path = self.root / ('spool_retrievals/job%d.json' % job_id)
            if not retrieval_path.exists():
                continue
            retrieval = read(retrieval_path)
            if (not retrieval.get('download_complete') or retrieval.get('status_sha256') != proof['status_sha256'] or
                    ad.get('ExitCode') != 0 or ad.get('ExitBySignal') is True or
                    (retrieval.get('cluster'), retrieval.get('process')) != (ad['ClusterId'], ad['ProcId'])):
                raise ValueError('Completed scheduler retirement lacks exact retained retrieval proof')
            path = self.root / ('scheduler_retirements/job%d.json' % job_id)
            previous = read(path) if path.exists() else {}
            if previous.get('acknowledged'):
                continue
            receipt = {'job': job_id, 'cluster': ad['ClusterId'], 'process': ad['ProcId'],
                       'completed_ad': ad, 'retrieval_receipt_sha256': sha(retrieval_path),
                       'status_sha256': proof['status_sha256'], 'nano_path': proof['nano_path'],
                       'nano_sha256': proof['nano_sha256'], 'attempted_at_epoch': now,
                       'acknowledged': False, 'physics_outputs_removed': False}
            save(path, receipt)
            identity = '%d.%d' % (ad['ClusterId'], ad['ProcId'])
            # A lost response is reconciled by the next complete queue snapshot;
            # never remove any running, held, or unsuccessful job.
            response = self.backend.command(['/usr/bin/condor_rm', identity])
            if 'Job ' + identity + ' marked for removal' not in response:
                raise NativeCondorError('Completed record removal was not acknowledged for the exact identity')
            receipt.update(acknowledged=True, response=response, acknowledged_at_epoch=time.time())
            save(path, receipt)
            count += 1

    def completion(self, epoch, waves, ads):
        completed, failed, quarantined, deferred, pending_exit = {}, {}, {}, set(), set()
        assigned = set()
        for job_id, proof in self.retained_completions.items():
            if proof['epoch'] == epoch['metadata']['epoch']:
                assigned.add(job_id)
                completed[job_id] = proof
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
                retrieval_path = self.root / ('spool_retrievals/job%d.json' % job_id)
                if retrieval_path.exists():
                    retrieval = read(retrieval_path)
                    if (retrieval.get('cluster'), retrieval.get('process'), retrieval.get('wave_nonce'),
                            retrieval.get('selection_sha256')) != (
                            identity[0], identity[1], wave['intent']['nonce'], job['selection_sha256']):
                        raise ValueError('Retained output-retrieval evidence differs from its wave')
                    completed_ad = retrieval['completed_ad']
                    if completed_ad.get('JobStatus') == 4 and completed_ad.get('ExitBySignal') is not True:
                        terminal = completed_ad.get('ExitCode')
                elif ad is not None and ad.get('JobStatus') == 4 and ad.get('ExitBySignal') is not True:
                    terminal = ad.get('ExitCode')
                if (ad is not None and ad.get('JobStatus') == 5 and
                        ad.get('ShiftNightAdmissionDeferred') is True and
                        ad.get('ShiftNightProcessedEvents') == 0 and
                        ad.get('ShiftNightDeferredNumJobStarts') == ad.get('NumJobStarts') and
                        type(ad.get('NumJobStarts')) is int and
                        ad.get('ShiftNightSelectionSHA256') == job['selection_sha256'] and
                        ad.get('ShiftNightEventContractSHA256') == job['event_contract_sha256']):
                    deferred.add(identity)
                    continue
                if job_id in self.proved_completions:
                    completed[job_id] = self.proved_completions[job_id]
                    continue
                if status_path.exists():
                    result = read(status_path)
                    if (result.get('night_admission_deferred') is True and result.get('exit_code') == 75 and
                            result.get('processed_events') == 0 and result.get('job') == job_id and
                            result.get('epoch') == job['epoch'] and
                            result.get('selection_sha256') == job['selection_sha256'] and ad is not None and
                            result.get('condor_num_job_starts') == ad.get('NumJobStarts')):
                        deferred.add(identity)
                        continue
                    error = validate_result(result, job, self.plan)
                    if error:
                        if isolated_computation_failure(result, job, self.plan, terminal):
                            quarantined[job_id] = {'reason': error, 'status_sha256': sha(status_path),
                                                   'epoch': job['epoch'], 'selection_sha256': job['selection_sha256'],
                                                   'event_contract_sha256': job['event_contract_sha256'],
                                                   'receipt_path': result['receipt_path'],
                                                   'receipt_sha256': result['receipt_sha256'],
                                                   'science_failure_automatically_retried': False}
                        else:
                            failed[job_id] = error
                    elif terminal == 0:
                        completed[job_id] = {'status_sha256': sha(status_path),
                                             'nano_path': result['nano_path'], 'nano_sha256': result['nano_sha256'],
                                             'events': job['count'], 'epoch': job['epoch'],
                                             'event_contract_sha256': job['event_contract_sha256'],
                                             'cluster': identity[0], 'process': identity[1]}
                        self.proved_completions[job_id] = completed[job_id]
                    else:
                        pending_exit.add(job_id)
                elif ad is not None and ad.get('JobStatus') == 4:
                    # Unretrieved outputs remain reserved, even on a nonzero
                    # exit; their exact computation/publication proof matters.
                    pending_exit.add(job_id)
                elif (ad is not None and ad.get('JobStatus') == 5 and
                      ad.get('HoldReason') == 'Spooling input data files' and
                      ad.get('NumJobStarts') == 0 and ad.get('ShiftNightScienceStarted') is False):
                    # condor_submit -spool briefly exposes this internal hold.
                    # It remains reserved; the schedd releases it after transfer.
                    continue
                elif ad is not None and ad.get('JobStatus') in (1, 5) and ad.get('ShiftNightScienceStarted') is True:
                    failed[job_id] = 'Reconstruction already started before this idle/held state; explicit recovery required'
                elif terminal is not None and terminal != 0:
                    failed[job_id] = 'Terminal Condor failure without a validated completion receipt'
                elif ad is not None and ad.get('JobStatus') in (3, 5) and ad.get('HoldReason') != NIGHT_HOLD_REASON:
                    failed[job_id] = 'Worker removed or held outside the night admission policy'
        return completed, failed, quarantined, deferred, pending_exit, assigned

    def renew_admissions(self, ads, deferred, window, now, eligible_budget, bootstrap_budget):
        """Renew start permission only; never edit a worker that started reconstruction."""
        self.renew_bootstrap_admissions(ads, window, now, bootstrap_budget)
        eligible = []
        for ad in ads:
            identity = (ad['ClusterId'], ad['ProcId'])
            if ad.get('ShiftNightScienceStarted') is True:
                continue
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

    def renew_bootstrap_admissions(self, ads, window, now, eligible_budget):
        """A claimed slot may need more than three minutes to stage its inputs.

        Only live, pinned workers that positively report no reconstruction can
        receive a fresh lease. The schedd rechecks the science-start flag inside
        the edit constraint, protecting the transition to actual reconstruction.
        """
        eligible = []
        for ad in ads:
            if not (ad.get('JobStatus') == 2 and ad.get('ShiftNightBootstrapInProgress') is True and
                    ad.get('ShiftNightScienceStarted') is False and ad.get('ShiftNightProcessedEvents') == 0):
                continue
            epoch = self.current_epoch(ad.get('ShiftNightEpoch'))
            job = epoch['by_job'].get(ad.get('ShiftNightJob'))
            if job is None or ad.get('ShiftNightSelectionSHA256') != job['selection_sha256'] or (
                    ad.get('ShiftNightEventContractSHA256') != job['event_contract_sha256']) or (
                    ad.get('ShiftNightSourceGENSHA256') != job['source_gen_sha256']) or (
                    ad.get('ShiftNightResponseContractSHA256') != self.plan['data']['response_contract_sha256']):
                raise ValueError('Running bootstrap lacks its pinned no-reconstruction authority')
            eligible.append(ad)
        eligible.sort(key=lambda ad: ad['ShiftNightJob'])
        budget = max(0, eligible_budget)
        permitted = eligible[:budget]
        denied = eligible[budget:]
        permission = min(window['stop_epoch'], int(now) + self.policy['lease_seconds'])
        for group, lease in ((permitted, permission), (denied, 0)):
            for start in range(0, len(group), 50):
                batch = group[start:start + 50]
                clauses = []
                for ad in batch:
                    clauses.append('(ClusterId == %d && ProcId == %d && ShiftNightJob == %d' % (
                        ad['ClusterId'], ad['ProcId'], ad['ShiftNightJob']) +
                        ' && ShiftNightSelectionSHA256 == ' + json.dumps(ad['ShiftNightSelectionSHA256']) +
                        ' && ShiftNightEventContractSHA256 == ' + json.dumps(ad['ShiftNightEventContractSHA256']) +
                        ' && ShiftNightSourceGENSHA256 == ' + json.dumps(ad['ShiftNightSourceGENSHA256']) +
                        ' && ShiftNightResponseContractSHA256 == ' + json.dumps(ad['ShiftNightResponseContractSHA256']) +
                        ' && ShiftNightWaveNonce == ' + json.dumps(ad['ShiftNightWaveNonce']) + ')')
                constraint = (self.constraint() + ' && (' + ' || '.join(clauses) + ')' +
                              ' && JobStatus == 2 && ShiftNightBootstrapInProgress == true' +
                              ' && ShiftNightScienceStarted == false && ShiftNightProcessedEvents == 0')
                path = self.root / 'bootstrap_admission_operation.json'
                save(path, {'operation': 'renew_proved_unprocessed_bootstrap', 'at_epoch': now,
                            'identities': [[ad['ClusterId'], ad['ProcId']] for ad in batch], 'lease_epoch': lease})
                self.backend.command(['/usr/bin/condor_qedit', '-constraint', constraint,
                                      'ShiftNightAdmissionLeaseEpoch', str(lease)])

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
            lines.append('%d %d %d %d %d %s %s %s %s %s' % (
                job['job'], job['epoch'], job['source'], job['count'], job['seed'],
                selection, Path(selection).name, job['selection_sha256'], job['event_contract_sha256'],
                job['source_gen_sha256']))
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
        response = self.backend.command(['/usr/bin/condor_submit', '-spool', '-terse', str(folder / 'wave.sub')], cwd=folder)
        match = re.fullmatch(r'(\d+)\.(\d+)\s*-\s*(\d+)\.(\d+)\s*', response)
        if not match or match[1] != match[3] or int(match[2]) != 0 or int(match[4]) + 1 != len(jobs):
            raise NativeCondorError('Ambiguous wave submission; its immutable intent remains reserved')
        save(folder / 'submission.json', {'cluster': int(match[1]), 'nonce': nonce, 'submitted_at_epoch': now})

    def step(self, now=None, *, dry_run=False):
        now = time.time() if now is None else now
        if len(self.state['completed_epochs']) == len(self.plan['data']['epochs']):
            ads = self.backend.ads(self.constraint())
            proofs = {}
            for number in self.state['completed_epochs']:
                receipt = read(self.root / ('epoch_completions/epoch%02d.json' % number))
                if receipt.get('plan_sha256') != self.plan['sha256'] or not receipt.get('operational_complete'):
                    raise ValueError('Final scheduler retirement lost its completed-epoch authority')
                proofs.update({int(job): proof for job, proof in receipt['completed_jobs'].items()})
            if not dry_run:
                self.reap_verified_outputs(proofs, ads, now)
            self.persist(now, phase='finalizing_scheduler' if ads else 'complete',
                         health='operational_complete', analysis_release=False)
            return not ads
        window = night_window(now, self.policy)
        ads = self.backend.ads(self.constraint())
        retrieval_blocked, retrieval_errors = ({}, {}) if dry_run else self.retrieve_outputs(ads, now)
        highest = self.state.get('highest_admitted_epoch', 1)
        completed, failed, quarantined, deferred, pending_exit, assigned, uncertain = {}, {}, {}, set(), set(), set(), []
        epoch_progress = {}
        frontier, frontier_pending = None, []
        number = 1
        while number <= highest:
            if number in self.state['completed_epochs']:
                number += 1
                continue
            epoch = self.current_epoch(number)
            waves, unknown = self.waves(epoch, ads)
            done, bad, isolated, postponed, wait, submitted = self.completion(epoch, waves, ads)
            completed.update(done)
            failed.update(bad)
            quarantined.update(isolated)
            deferred.update(postponed)
            pending_exit.update(wait)
            assigned.update(submitted)
            uncertain.extend(unknown)
            pending = [job for job in epoch['jobs'] if job['job'] not in submitted]
            epoch_progress[str(number)] = {'completed_jobs': len(done), 'expected_jobs': len(epoch['jobs']),
                                           'assigned_jobs': len(submitted), 'unassigned_jobs': len(pending),
                                           'quarantined_jobs': len(isolated), 'common_failures': len(bad)}
            if len(done) == len(epoch['jobs']):
                completion = {'operational_complete': True, 'epoch': number,
                              'plan_sha256': self.plan['sha256'], 'draw_manifest_sha256': epoch['metadata']['draw_manifest_sha256'],
                              'completed_at_epoch': now, 'events': epoch['metadata']['work_events'],
                              'jobs': len(done), 'completed_jobs': done,
                              'analysis_release': False, 'normalization_ready': False,
                              'physics_limitation': 'The fixed V10 parent-hadron transport is not qualified for physical closure.'}
                save(self.root / ('epoch_completions/epoch%02d.json' % number), completion)
                self.state['completed_epochs'] = sorted(self.state['completed_epochs'] + [number])
            elif pending and frontier is None:
                frontier = number
                frontier_pending = source_round_robin(pending) if self.policy.get('job_order') == 'source_round_robin_descending' else pending
            if number == highest and not pending and highest < len(self.plan['data']['epochs']):
                # Every later draw and mask was frozen before outcomes. Its
                # work can run while earlier failures remain quarantined.
                highest += 1
            number += 1
        failed.update(retrieval_errors)
        if not dry_run:
            self.reap_verified_outputs(completed, ads, now)
        prefix = 0
        while prefix + 1 in self.state['completed_epochs']:
            prefix += 1
        self.state.update(highest_admitted_epoch=highest, completed_prefix_epochs=prefix)
        previous_complete_jobs = sum(epoch['work_jobs'] for epoch in self.plan['data']['epochs']
                                     if epoch['epoch'] in self.state['completed_epochs'])
        previous_complete_events = sum(epoch['work_events'] for epoch in self.plan['data']['epochs']
                                       if epoch['epoch'] in self.state['completed_epochs'])
        # Jobs just added to a completed epoch must not be counted twice.
        open_completed = {job: row for job, row in completed.items() if row['epoch'] not in self.state['completed_epochs']}
        total_completed = previous_complete_jobs + len(open_completed)
        outcomes = total_completed + len(quarantined)
        failure_limit = self.policy.get('maximum_quarantined_computation_jobs', 20)
        rate_limit = self.policy.get('maximum_computation_failure_fraction', .05)
        rate_minimum = self.policy.get('failure_rate_minimum_outcomes', 20)
        burst = len(quarantined) > failure_limit or (
            outcomes >= rate_minimum and len(quarantined) / outcomes > rate_limit)
        observation = self.backend.observation() if not dry_run and (window['inside_night'] or window['start_epoch'] - now <= 900) else (
            read(self.root / 'last_quota.json') if (self.root / 'last_quota.json').exists() else {})
        verified, observation_reason = observations_safe(observation, self.policy, now,
                                                        reserved_workers=0)
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
        unresolved = assigned - set(completed) - set(failed) - set(quarantined)
        live_by_job = {ad.get('ShiftNightJob'): ad for ad in ads if ad.get('JobStatus') in LIVE_STATUSES}
        reserved = unresolved | set(live_by_job)
        reservations = [live_by_job.get(job, {'JobStatus': 1}) for job in reserved]
        quota_worker_limit = min(
            max(0, (observation.get('free_bytes', 0) - self.policy['minimum_free_bytes']) // self.policy['reserved_bytes_per_worker']),
            max(0, (observation.get('free_files', 0) - self.policy['minimum_free_files']) // self.policy['reserved_files_per_worker'])) if verified else 0
        gates_passed, gate_problems = operational_gates(self.root)
        capacity = capacity_decision(self.state.get('capacity', {}), self.policy, now=now,
                                     completed_jobs=total_completed,
                                     own_ads=reservations, external_workers=external,
                                     quota_worker_limit=quota_worker_limit,
                                     observations_verified=verified, healthy=gates_passed and not failed and not uncertain and not burst and not retrieval_blocked)
        allow = gates_passed and window['admissions_allowed'] and verified and quota_worker_limit > 0 and not failed and not uncertain and not burst and not retrieval_blocked and not (self.root / 'stop_requested.json').exists()
        phase = 'night_running' if allow else ('draining' if window['inside_night'] else 'waiting_for_night')
        if not gates_passed:
            phase = 'waiting_for_operational_validation'
        if failed or uncertain or burst or retrieval_blocked:
            phase = 'attention_required'
        self.persist(now, phase=phase, epoch=frontier or highest, window=window, capacity=capacity,
                     health='healthy' if allow else 'admissions_paused', observation_reason=observation_reason,
                     completed_jobs=total_completed, expected_jobs=sum(row['work_jobs'] for row in self.plan['data']['epochs']),
                     completed_events=previous_complete_events + sum(row['events'] for row in open_completed.values()),
                     failed_jobs=failed, uncertain_submission_nonces=uncertain,
                     spool_retrieval_blocked=retrieval_blocked, quota_worker_limit=quota_worker_limit,
                     operational_gates_passed=gates_passed, operational_gate_problems=gate_problems,
                     pending_terminal_evidence=sorted(pending_exit), submitted_jobs=len(assigned),
                     quarantined_computation_jobs=quarantined, computation_failure_burst=burst,
                     per_epoch=epoch_progress, queued_or_running_workers=len(live_by_job),
                     active_admission_deferrals=len(deferred), dry_run=dry_run)
        if allow and not dry_run:
            own_running = sum(ad.get('JobStatus') in (2, 6, 7) for ad in ads)
            missing_reservations = len(unresolved - set(live_by_job) - {
                ad.get('ShiftNightJob') for ad in ads if ad.get('JobStatus') == 5})
            admitted_budget = min(capacity['current_capacity'], self.policy['worker_ceiling'] - external,
                                  quota_worker_limit)
            eligible_budget = max(0, admitted_budget
                                  - own_running - missing_reservations)
            bootstrap_running = sum(ad.get('JobStatus') == 2 and ad.get('ShiftNightBootstrapInProgress') is True and
                                    ad.get('ShiftNightScienceStarted') is False and ad.get('ShiftNightProcessedEvents') == 0
                                    for ad in ads)
            bootstrap_budget = max(0, admitted_budget - (own_running - bootstrap_running) - missing_reservations)
            self.renew_admissions(ads, deferred, window, now, eligible_budget, bootstrap_budget)
            count = capacity['available_new_slots']
            if count and frontier_pending:
                self.epoch_number = frontier
                self.submit_wave(frontier_pending[:count], window, now)
        return False


def watchdog(root, policy):
    """Report supervisor faults; expiring worker leases enforce the admission stop."""
    state_path = root / 'controller_state.json'
    idle_since = None
    while True:
        now = time.time()
        state = read(state_path) if state_path.exists() else {}
        stale = now - state.get('checked_at_epoch', now) > policy['lease_seconds']
        missing_workers = state.get('phase') == 'night_running' and state.get('queued_or_running_workers') == 0
        idle_since = (idle_since or now) if missing_workers else None
        handoff_stalled = idle_since is not None and now - idle_since > 2 * policy['poll_seconds']
        attention = stale or handoff_stalled or state.get('phase') in ('observer_fault', 'attention_required')
        save(root / 'watchdog_state.json', {'checked_at_epoch': now,
                                           'controller_heartbeat_epoch': state.get('checked_at_epoch'),
                                           'controller_phase': state.get('phase'), 'controller_stale': stale,
                                           'manager_without_workers_since_epoch': idle_since,
                                           'downstream_handoff_stalled': handoff_stalled,
                                           'attention_required': attention,
                                           'worker_action': 'none; expired admissions prevent new starts',
                                           'analysis_release': False})
        if attention:
            save(root / 'watchdog_failure.json', {'checked_at_epoch': now,
                'controller_stale': stale, 'downstream_handoff_stalled': handoff_stalled,
                'controller_phase': state.get('phase'), 'observer_error': state.get('observer_error'),
                'worker_action': 'none; expired admissions prevent new starts'})
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
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit(78)
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
