#!/usr/bin/env python3
# Code style: AI-code. AI-generated reviewed, one-time streaming campaign handoff.
"""Wait for a real canary, retire old managers, then start a frozen bulk pass.

The handoff never retries science, removes a started payload, or changes weights.
An ambiguous scheduler response leaves a durable intent and fails closed.
Temporary read-only scheduler failures pause the handoff without changing jobs.
"""
import argparse
import fcntl
import json
from pathlib import Path
import re
import time
import uuid

from run_shift_stream_controller import field, read, save, sha, exit_event
from shift_condor_native import NativeCondorError, query, run_condor


ATTRIBUTES = ['ClusterId', 'ProcId', 'Owner', 'Iwd', 'JobStatus', 'NumJobStarts',
    'JobUniverse', 'MaxRuntime', 'ShiftStreamCampaign', 'ShiftStreamController',
    'ShiftStreamWatchdog', 'ShiftStreamTask', 'ShiftStreamStage', 'ShiftTransitionNonce',
    'ShiftNtupleTier', 'ShiftNtupleStageRecordsStarted', 'ShiftNtupleProgressEpoch',
    'ShiftNtupleGENEvents', 'ShiftNtupleSIMEvents', 'ShiftNtupleDIGIHLTEvents',
    'ShiftNtupleRECOEvents', 'ShiftNtupleNANOEvents']


class SchedulerObservationError(NativeCondorError):
    """Only a failed native read may wait for a fresh observation."""


def verify_pins(config):
    for name, checksum in config['pinned_files'].items():
        if sha(name) != checksum:
            raise ValueError('Frozen handoff input changed: ' + name)


def ready_gate(config):
    path = Path(config['canary_gate']['path'])
    if not path.exists():
        return False
    gate = read(path)
    if gate.get('passed') is not True:
        raise ValueError('Canary gate exists without a passing result')
    for name, value in config['canary_gate']['expected'].items():
        if field(gate, name) != value:
            raise ValueError('Canary authority differs: ' + name)
    for proof in gate['native_task_proofs']:
        if sha(proof['status_path']) != proof['status_sha256']:
            raise ValueError('Canary status changed after its gate')
    for source in gate.get('canary_source_ids', []):
        task = read(Path(config['canary_campaign']['directory']) / 'tasks' / (source + '_histogram.json'))
        publication = read(Path(task['working_directory']) / 'plots_published.json')
        if publication.get('complete') is not True or not publication.get('files'):
            raise ValueError('Canary project plot publication is incomplete')
        for output in publication['files']:
            if sha(output['path']) != output['sha256']:
                raise ValueError('Canary project plot readback changed')
    return True


def canary_healthy(config, now=None):
    now = time.time() if now is None else now
    root = Path(config['canary_campaign']['directory'])
    path = root / 'heartbeat.json'
    if not path.exists():
        if now - config['prepared_at_epoch'] > 600:
            raise ValueError('Canary controller never wrote its heartbeat')
        return
    heartbeat = read(path)
    if now - heartbeat['checked_at_epoch'] > config.get('watchdog_timeout_seconds', 1800):
        raise ValueError('Canary controller heartbeat is stale')
    if heartbeat.get('problems'):
        raise ValueError('Canary controller reported a failed stage: ' + repr(heartbeat['problems']))


def canary_publication_sizes(config):
    """Prove the complete published payload fits the proposed small RECO slot."""
    budget = config.get('canary_publication_budget')
    if budget is None:
        return None
    limit, events = budget['maximum_bytes'], budget['events_per_job']
    if type(limit) is not int or limit <= 65536 or type(events) is not int or events < 1:
        raise ValueError('Invalid measured canary publication budget')
    root = Path(config['canary_campaign']['directory'])
    rows = []
    for source_id in budget['source_ids']:
        task = read(root / 'tasks' / (source_id + '_reco_000.json'))
        report_path = Path(task['result_path'])
        report = read(report_path)
        if (report.get('complete') is not True or report.get('events') != events
                or report.get('nano_sha256') != report.get('nano_readback_sha256')
                or not re.fullmatch(r'[0-9a-f]{64}', report.get('nano_sha256', ''))):
            raise ValueError('Canary Nano publication/count/readback is incomplete: ' + source_id)
        before = report.get('publication_bytes_before_receipt')
        if type(before) is not int or before < 1:
            raise ValueError('Canary did not report its complete publication bytes: ' + source_id)
        total = before + report_path.stat().st_size + 65536
        if total > limit:
            raise ValueError('Canary total publication exceeds the proposed RECO budget: '
                             + source_id + ' ' + str(total) + ' > ' + str(limit))
        rows.append({'source_id': source_id, 'events': events, 'result_path': str(report_path),
            'result_sha256': sha(report_path), 'publication_bytes_before_receipt': before,
            'serialized_report_bytes': report_path.stat().st_size, 'receipt_margin_bytes': 65536,
            'total_reserved_bytes': total, 'maximum_bytes': limit})
    if not rows:
        raise ValueError('A measured canary budget requires designated source IDs')
    return {'complete': True, 'passed': True, 'checked_at_epoch': time.time(), 'measurements': rows}


def obsolete_idle(config, ads, ledger):
    """Select only exact ledger-matched payloads which have never executed."""
    old = config['old_campaign']
    root = Path(old['directory'])
    selected = []
    for ad in ads:
        if (ad.get('ShiftStreamCampaign') != old['tag']
                or ad.get('JobStatus') != 1 or ad.get('NumJobStarts') != 0
                or ad.get('ShiftStreamStage') not in ('prepare', 'reco')):
            continue
        identity = ad.get('ShiftStreamTask')
        record = ledger.get(identity, {})
        if record.get('phase') != 'submitted' or record.get('job') != [ad['ClusterId'], ad['ProcId']]:
            raise ValueError('Old idle job does not match its science ledger')
        task = read(root / 'tasks' / (identity + '.json'))
        if (ad.get('Owner') != config['owner'] or ad.get('Iwd') != task['working_directory']
                or task['stage'] != ad['ShiftStreamStage']
                or root / 'cohorts' not in Path(ad['Iwd']).parents):
            raise ValueError('Old idle job has an unexpected identity or working directory')
        if (record.get('execution_verified') is True
                or exit_event(task['event_log'], ad['ClusterId'], ad['ProcId'])['executed']):
            continue
        selected.append(ad)
    return selected


class Transition:
    def __init__(self, root, config, reader=query, commander=run_condor):
        self.root, self.config = Path(root), config
        self.reader, self.commander = reader, commander
        self.scheduler_failures = 0
        self.maximum_scheduler_failures = config.get('maximum_consecutive_scheduler_failures', 10)
        if type(self.maximum_scheduler_failures) is not int or self.maximum_scheduler_failures < 1:
            raise ValueError('Scheduler failure limit must be a positive integer')

    def observe(self):
        try:
            return self.reader('Owner == ' + json.dumps(self.config['owner']),
                attributes=ATTRIBUTES, local_schedd=True)
        except NativeCondorError as error:
            raise SchedulerObservationError(str(error)) from error

    def poll(self):
        """Recover reads only; an ambiguous submit/removal still stops here.

        A failed read unwinds the current step before another command can run.
        The next poll starts again with native observations and the same durable
        submission records. It never retries a scientific payload.
        """
        try:
            heartbeat = self.step()
        except SchedulerObservationError as error:
            self.scheduler_failures += 1
            record = {'checked_at_epoch': time.time(), 'phase': 'waiting_for_scheduler',
                'scheduler_state': 'unknown', 'command': '/usr/bin/condor_q',
                'operation': 'read-only native observation', 'error': str(error),
                'consecutive_scheduler_failures': self.scheduler_failures,
                'maximum_consecutive_scheduler_failures': self.maximum_scheduler_failures,
                'worker_action': 'none; wait for a fresh native observation',
                'scientific_payloads_retried': False}
            save(self.root / 'scheduler_observation_failure.json', record)
            if self.scheduler_failures >= self.maximum_scheduler_failures:
                raise SchedulerObservationError('Native scheduler remained unknown for '
                    + str(self.scheduler_failures) + ' consecutive observations: ' + str(error)) from error
            return record
        if self.scheduler_failures:
            heartbeat['scheduler_recovered_after_failures'] = self.scheduler_failures
            save(self.root / 'scheduler_observation_recovery.json',
                {'checked_at_epoch': time.time(), 'prior_consecutive_failures': self.scheduler_failures,
                 'phase': heartbeat['phase'], 'native_observation_restored': True})
        self.scheduler_failures = 0
        return heartbeat

    def command(self, argv, cwd=None):
        return self.commander(argv, local_schedd=True, timeout=60, cwd=cwd or self.root)

    def canary_progress(self):
        """Keep real stage counters visible while the slow physics gate runs."""
        tag = self.config['canary_campaign']['tag']
        workers = []
        for ad in self.observe():
            if ad.get('ShiftStreamCampaign') != tag or ad.get('JobUniverse') != 5:
                continue
            workers.append({key: value for key, value in ad.items() if key in (
                'ClusterId', 'ProcId', 'JobStatus', 'ShiftStreamTask', 'ShiftStreamStage',
                'ShiftNtupleTier', 'ShiftNtupleStageRecordsStarted', 'ShiftNtupleProgressEpoch',
                'ShiftNtupleGENEvents', 'ShiftNtupleSIMEvents', 'ShiftNtupleDIGIHLTEvents',
                'ShiftNtupleRECOEvents', 'ShiftNtupleNANOEvents')})
        return {'checked_at_epoch': time.time(), 'native_workers': workers[:20]}

    def retire_old(self):
        config = self.config
        old = config['old_campaign']
        def started(ad):
            task = read(Path(old['directory']) / 'tasks' / (ad['ShiftStreamTask'] + '.json'))
            return (ad.get('NumJobStarts', 0) > 0
                    or exit_event(task['event_log'], ad['ClusterId'], ad['ProcId'])['executed'])
        before = self.observe()  # Unknown state permits no mutation.
        save(self.root / 'old_queue_before.json', {'checked_at_epoch': time.time(), 'ads': before})
        reviewed_managers = {tuple(manager['job']) for manager in old['managers']}
        if any(ad.get('ShiftStreamCampaign') == old['tag'] and ad.get('ShiftStreamController')
               and (ad['ClusterId'], ad['ProcId']) not in reviewed_managers for ad in before):
            raise ValueError('An unreviewed old campaign manager is live; do not change ownership')
        # Stop the read-only watchdog first, then its controller. Each exact
        # scheduler identity has been reviewed before this detached handoff.
        for manager in old['managers']:
            matches = [ad for ad in before if [ad['ClusterId'], ad['ProcId']] == manager['job']]
            if not matches:
                continue
            ad = matches[0]
            if (ad.get('ShiftStreamController') is not True or ad.get('Iwd') != manager['directory']
                    or ad.get('ShiftStreamWatchdog') != manager['watchdog']
                    or ad.get('ShiftStreamCampaign') != old['tag']):
                raise ValueError('Reviewed old manager identity changed')
            self.command(['/usr/bin/condor_rm', str(ad['ClusterId']) + '.' + str(ad['ProcId'])])
        after = self.observe()
        if any(ad.get('ShiftStreamCampaign') == old['tag'] and ad.get('ShiftStreamController') for ad in after):
            return False  # Native removal can take a short time; do not submit yet.
        ledger = read(Path(old['directory']) / 'ledger.json')
        review_path = self.root / 'obsolete_idle_review.json'
        if review_path.exists():
            idle = read(review_path)['jobs']
        else:
            idle = obsolete_idle(config, after, ledger)
            save(review_path, {'checked_at_epoch': time.time(),
                'ledger_sha256': sha(Path(old['directory']) / 'ledger.json'), 'jobs': idle})
        for ad in idle:
            # Recheck execution state inside the scheduler's removal constraint;
            # a job that started since observation must survive this cleanup.
            constraint = ('ClusterId == ' + str(ad['ClusterId']) + ' && ProcId == '
                + str(ad['ProcId']) + ' && JobStatus == 1 && NumJobStarts == 0')
            current = [row for row in after if [row['ClusterId'], row['ProcId']] == [ad['ClusterId'], ad['ProcId']]]
            if not current or started(current[0]):
                continue
            try:
                self.command(['/usr/bin/condor_rm', '-constraint', constraint])
            except NativeCondorError:
                # Condor returns an error for a zero-match removal. Only a
                # fresh exact observation or native terminal event can prove
                # that the reviewed payload started during this benign race.
                fresh = self.observe()
                matches = [row for row in fresh if [row['ClusterId'], row['ProcId']] == [ad['ClusterId'], ad['ProcId']]]
                task = read(Path(old['directory']) / 'tasks' / (ad['ShiftStreamTask'] + '.json'))
                proof = exit_event(task['event_log'], ad['ClusterId'], ad['ProcId'])
                if not ((len(matches) == 1 and started(matches[0]))
                        or (not matches and proof['executed'] and proof['terminal'] is not None)):
                    raise
        final = self.observe()
        reviewed = {(ad['ClusterId'], ad['ProcId']) for ad in idle}
        unresolved = [ad for ad in final if (ad['ClusterId'], ad['ProcId']) in reviewed and not started(ad)]
        if unresolved:
            # A native removal can remain queued briefly. Keep the immutable
            # review and observe again; never broaden its removal targets.
            save(self.root / 'old_retirement_pending.json', {'checked_at_epoch': time.time(), 'jobs': unresolved})
            return False
        save(self.root / 'old_retirement.json', {'complete': True, 'checked_at_epoch': time.time(),
            'reviewed_idle_job_ids': [[ad['ClusterId'], ad['ProcId']] for ad in idle],
            'remaining_old_jobs': [ad for ad in final if ad.get('ShiftStreamCampaign') == old['tag']],
            'started_payloads_preserved': True, 'scientific_files_removed': False})
        return True

    def start_role(self, role):
        config, root = self.config, self.root
        bulk = config['bulk_campaign']
        path = root / (role + '_submission.json')
        if path.exists():
            record = read(path)
            if record.get('phase') != 'submitted':
                raise NativeCondorError('Unresolved prior manager submission; no automatic resubmission')
            self.record_bulk_submission(role, record)
            return record
        ads = self.observe()
        if any(ad.get('ShiftStreamCampaign') == bulk['tag'] and ad.get('ShiftStreamController')
               and ad.get('ShiftStreamWatchdog') == (role == 'watchdog') for ad in ads):
            raise NativeCondorError('An unrecorded bulk manager already exists; do not duplicate it')
        submit = str(Path(bulk['directory']) / (role + '.sub'))
        record = {'phase': 'submitting', 'nonce': uuid.uuid4().hex, 'submitted_at_epoch': time.time(),
            'submit_file': submit, 'submit_sha256': sha(submit)}
        save(path, record)
        output = self.command(['/usr/bin/condor_submit', '-terse', submit,
            '-append', '+ShiftTransitionNonce = ' + json.dumps(record['nonce'])],
            cwd=Path(bulk['directory']))
        match = re.fullmatch(r'(\d+)\.0\s*-\s*\1\.0\s*', output)
        if not match:
            raise NativeCondorError('Ambiguous manager submission; durable intent preserved')
        record.update(phase='submitted', job=[int(match[1]), 0])
        save(path, record)
        self.record_bulk_submission(role, record)
        return record

    def old_payloads_drained(self):
        campaigns = [self.config['old_campaign']] + self.config.get('additional_retired_campaigns', [])
        ads = self.observe()
        tags = {old['tag'] for old in campaigns}
        roots = [Path(old['directory']) for old in campaigns]
        if any(ad.get('ShiftStreamCampaign') in tags and ad.get('ShiftStreamController') for ad in ads):
            raise ValueError('Old campaign manager reappeared after its reviewed retirement')
        payloads = [ad for ad in ads if ad.get('JobUniverse') == 5 and (
            ad.get('ShiftStreamCampaign') in tags
            or any(Path(ad.get('Iwd', '.')) == root or root in Path(ad.get('Iwd', '.')).parents
                   for root in roots))]
        proof = {'complete': not payloads, 'passed': not payloads, 'checked_at_epoch': time.time(),
                 'native_payloads': payloads, 'held_jobs_are_blocking': True,
                 'all_statuses_require_native_absence': True}
        save(self.root / ('old_payload_drain.json' if not payloads else 'old_payload_drain_wait.json'), proof)
        return not payloads

    def record_bulk_submission(self, role, record):
        path = Path(self.config['bulk_campaign']['directory']) / 'submission.json'
        receipt = read(path) if path.exists() else {}
        key = role + '_cluster'
        if receipt.get(key) not in (None, record['job'][0]):
            raise ValueError('Canonical bulk submission receipt disagrees with durable handoff')
        receipt.update({key: record['job'][0], role + '_nonce': record['nonce'],
                        'transition_directory': str(self.root)})
        save(path, receipt)

    def execution(self):
        bulk = self.config['bulk_campaign']
        ads = self.observe()
        managers = []
        for role in ('controller', 'watchdog'):
            record = read(self.root / (role + '_submission.json'))
            matches = [ad for ad in ads if [ad['ClusterId'], ad['ProcId']] == record['job']]
            if len(matches) != 1:
                raise NativeCondorError('Submitted bulk manager is missing from the native queue')
            ad = matches[0]
            if (ad.get('Iwd') != bulk['directory'] or ad.get('MaxRuntime', 0) < 604800
                    or ad.get('ShiftTransitionNonce') != record['nonce']):
                raise ValueError('Bulk manager working directory, runtime or nonce differs')
            if ad['JobStatus'] == 5:
                raise ValueError('Bulk manager is held; preserve failed handoff')
            proof = exit_event(str(Path(bulk['directory']) / (role + '.events')), *record['job'])
            if proof['executed'] and ad['JobStatus'] == 2 and ad.get('NumJobStarts', 0) > 0:
                managers.append({'job': record['job'], 'role': role, 'native_execution': proof})
        workers = []
        stage = self.config.get('worker_execution_stage', 'reco')
        for ad in ads:
            if (ad.get('ShiftStreamCampaign') == bulk['tag'] and ad.get('ShiftStreamStage') == stage
                    and ad.get('JobStatus') == 2):
                task = read(Path(bulk['directory']) / 'tasks' / (ad['ShiftStreamTask'] + '.json'))
                proof = exit_event(task['event_log'], ad['ClusterId'], ad['ProcId'])
                if proof['executed'] and ad['Iwd'] == task['working_directory']:
                    workers.append({'job': [ad['ClusterId'], ad['ProcId']], 'task': ad['ShiftStreamTask'],
                                    'native_execution': proof})
        return {'complete': len(managers) == 2 and bool(workers), 'checked_at_epoch': time.time(),
                'managers': managers, 'worker_execution_stage': stage,
                'reconstruction_workers' if stage == 'reco' else 'orchestration_workers': workers[:10]}

    def step(self):
        verify_pins(self.config)
        if not ready_gate(self.config):
            canary_healthy(self.config)
            heartbeat = {'phase': 'waiting_for_batched_canary', 'checked_at_epoch': time.time()}
            if self.config.get('record_canary_native_progress', False):
                heartbeat['canary_progress'] = self.canary_progress()
            return heartbeat
        sizes = canary_publication_sizes(self.config)
        if sizes is not None:
            save(self.root / 'canary_publication_budget.json', sizes)
        if not (self.root / 'old_retirement.json').exists() and not self.retire_old():
            return {'phase': 'waiting_for_old_manager_removal', 'checked_at_epoch': time.time()}
        for index, old in enumerate(self.config.get('additional_retired_campaigns', [])):
            directory = self.root / ('additional_retirement_' + str(index))
            directory.mkdir(exist_ok=True)
            if not (directory / 'old_retirement.json').exists():
                replacement = dict(self.config, old_campaign=old)
                if not Transition(directory, replacement, self.reader, self.commander).retire_old():
                    return {'phase': 'waiting_for_completed_canary_manager_removal', 'checked_at_epoch': time.time()}
        if (self.config.get('require_old_payload_drain', self.config.get('wait_for_old_payload_drain', False))
                and not self.old_payloads_drained()):
            return {'phase': 'waiting_for_all_old_payloads_to_drain', 'checked_at_epoch': time.time()}
        for role in ('controller', 'watchdog'):
            self.start_role(role)
        proof = self.execution()
        if proof['complete']:
            save(self.root / 'handoff_complete.json', proof)
        return {'phase': 'complete' if proof['complete'] else 'waiting_for_real_bulk_execution',
                'checked_at_epoch': time.time(), 'execution': proof}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--config-sha256', required=True)
    parser.add_argument('--watchdog', action='store_true')
    args = parser.parse_args()
    root = args.directory.resolve()
    if str(root).startswith(('/tmp/', '/pool/condor/dir_')):
        raise ValueError('Transition needs an absolute persistent working directory')
    if sha(root / 'config.json') != args.config_sha256:
        raise ValueError('Transition configuration changed')
    config = read(root / 'config.json')
    role = 'watchdog' if args.watchdog else 'transition'
    with (root / (role + '.lock')).open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        transition = Transition(root, config)
        while True:
            try:
                if args.watchdog:
                    if (root / 'handoff_complete.json').exists():
                        return
                    if (root / 'failure.json').exists():
                        raise ValueError('Transition has a persistent failure record')
                    path = root / 'heartbeat.json'
                    observed = read(path) if path.exists() else {'checked_at_epoch': config['prepared_at_epoch']}
                    if time.time() - observed['checked_at_epoch'] > config.get('watchdog_timeout_seconds', 1800):
                        raise ValueError('Transition heartbeat is missing or stale')
                    heartbeat = {'phase': 'watching', 'checked_at_epoch': time.time(),
                                 'transition_phase': observed.get('phase', 'starting')}
                else:
                    heartbeat = transition.poll()
                save(root / ('watchdog_heartbeat.json' if args.watchdog else 'heartbeat.json'), heartbeat)
                if heartbeat['phase'] == 'complete':
                    return
            except Exception as error:
                save(root / ('watchdog_failure.json' if args.watchdog else 'failure.json'),
                    {'failed_at_epoch': time.time(), 'error': str(error),
                    'automatic_retry': False, 'scientific_payloads_removed': False})
                raise
            time.sleep(config.get('poll_seconds', 30))


if __name__ == '__main__':
    main()
