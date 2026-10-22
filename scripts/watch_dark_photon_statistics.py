#!/usr/bin/env python3
# Code style: AI-code. AI-generated read-only adaptive DP production watchdog.
"""Inspect scheduler, execution evidence and production receipts; never mutate jobs.

Queue failures mean unknown state. A running ClassAd alone is not evidence of a
payload execution: DAG execute events or a worker's started/completed receipt
must establish that separately. Health records are bounded, persistent JSON.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import errno
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time

from shift_condor_native import query, run_condor, parse_ads


ATTRIBUTES = ['ClusterId', 'ProcId', 'DAGManJobId', 'JobStatus', 'Iwd', 'NumJobStarts',
              'QDate', 'JobStartDate', 'EnteredCurrentStatus', 'RemoteHost', 'HoldReason',
              'ExitCode', 'ExitBySignal', 'CompletionDate', 'Args', 'Out',
              'ShiftDPStatisticsWatchdog', 'RemoteUserCpu', 'RemoteSysCpu', 'Cmd']


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.next')
    body = json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n'
    for attempt in range(4):
        try:
            temporary.write_text(body)
            os.replace(temporary, path)
            return
        except OSError as error:
            if error.errno not in (errno.ETIMEDOUT, errno.ESTALE, errno.EIO) or attempt == 3:
                raise
            time.sleep(2 ** attempt)


def timestamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def record_epoch(record, *keys):
    for key in keys:
        value = record.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            return timestamp(value)
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError('Invalid timestamp in ' + key)
        return float(value)
    return None


def operational_receipts(specifications):
    result = {}
    names = set()
    for specification in specifications:
        name = specification['name']
        path = Path(specification['receipt'])
        directory = Path(specification['iwd'])
        if (not re.fullmatch(r'[A-Za-z0-9_-]+', name) or not path.is_absolute()
                or not directory.is_absolute() or str(directory).startswith(('/tmp/', '/pool/'))):
            raise ValueError('Operational monitor needs exact persistent receipt and Iwd')
        if name in names:
            raise ValueError('Operational monitor repeats a job name')
        names.add(name)
        if not path.is_file() and specification.get('receipt_optional'):
            continue
        receipt = read(path)
        cluster = receipt['cluster']
        if (type(cluster) is not int or cluster <= 0
                or specification.get('expected_cluster', cluster) != cluster
                or receipt.get('Iwd', str(directory)) != str(directory)
                or specification.get('receipt_sha256', sha(path)) != sha(path)):
            raise ValueError('Operational receipt differs from its exact configured identity')
        submitted = record_epoch(receipt, 'utc', 'submitted_epoch')
        if submitted is None:
            raise ValueError('Operational receipt lacks its submission time')
        if cluster in result:
            raise ValueError('Operational monitor repeats a native job')
        result[cluster] = dict(directory=str(directory), receipt=str(path),
                               submitted=submitted, kind='operation', operation_name=name,
                               receipt_sha256=sha(path), external_prerequisite=True)
    return result


def manager_receipts(root, supervisor_receipt=None, external_productions=(), operational_jobs=()):
    explicit_supervisor = supervisor_receipt is not None
    supervisor_receipt = Path(supervisor_receipt) if supervisor_receipt else root / 'submission.json'
    if not supervisor_receipt.is_absolute():
        raise ValueError('Supervisor receipt must use an absolute persistent path')
    if explicit_supervisor and not supervisor_receipt.is_file():
        raise ValueError('Explicit supervisor receipt is missing: ' + str(supervisor_receipt))
    paths = [supervisor_receipt]
    paths.append(root / 'coverage' / 'submission.json')
    paths += sorted((root / 'waves').glob('w*/gen_stage/submission.json'))
    paths += sorted((root / 'waves').glob('w*/production/submission.json'))
    paths += sorted((root / 'batches').glob('*/submission.json'))
    paths += sorted((root / 'batches').glob('*/production/submission.json'))
    paths += sorted((root / 'aggregates').glob('*/v*/submission.json'))
    external = {Path(directory) for directory in external_productions}
    for directory in external:
        if not directory.is_absolute() or not (directory / 'submission.json').is_file():
            raise ValueError('External prerequisite needs a persistent production receipt')
        paths.append(directory / 'submission.json')
    result = {}
    for path in dict.fromkeys(paths):
        if not path.is_file():
            continue
        receipt = read(path)
        cluster = receipt.get('cluster')
        if type(cluster) is not int or cluster <= 0:
            raise ValueError('Invalid submission receipt: ' + str(path))
        result[cluster] = dict(directory=str(path.parent), receipt=str(path),
                               external_prerequisite=path.parent in external,
                               submitted=record_epoch(receipt, 'utc', 'submitted_epoch'), kind=(
                                   'supervisor' if path == supervisor_receipt else
                                   'production' if path.parent in external else
                                   'aggregate' if 'aggregates' in path.parts else
                                   'meson_source' if path.parent.parent == root / 'batches' else path.parent.name))
    operations = operational_receipts(operational_jobs)
    if set(result) & set(operations):
        raise ValueError('Operational job duplicates another monitored manager')
    result.update(operations)
    return result


def scheduler_options(config):
    if (Path('/var/lib/condor/spool/.schedd_address').is_file()
            and Path('/var/lib/condor/spool/.schedd_classad').is_file()):
        return dict(local_schedd=True), []
    return dict(schedd=config['condor_schedd'], pool=config['condor_pool']), [
        '-pool', config['condor_pool'], '-name', config['condor_schedd']]


def scheduler_snapshot(config, receipts, previous):
    options, cli = scheduler_options(config)
    ids = sorted(set(receipts) | {config['initial_gen_manager_cluster'], config['initial_supervisor_cluster']}
                 | {int(cluster) for cluster in config.get('retired_supervisors', {})})
    alternatives = ['ClusterId == ' + str(cluster) for cluster in ids]
    alternatives += ['DAGManJobId == ' + str(cluster) for cluster in ids]
    alternatives.append('regexp(' + json.dumps(re.escape(config['production_directory'])) + ',Iwd)')
    constraint = 'Owner == ' + json.dumps(config['owner']) + ' && (' + ' || '.join(alternatives) + ')'
    rows = query(constraint, ATTRIBUTES, timeout=45, **options)
    present = {row['ClusterId'] for row in rows}
    history = [row for row in previous.get('terminal_history', []) if row['ClusterId'] not in present]
    known_terminal = {row['ClusterId'] for row in history}
    # A sealed, previously read native terminal record is immutable. Reuse it
    # rather than rescanning the same old failed manager on a fresh monitor.
    for cluster, reviewed in reviewed_terminal_failures(config).items():
        if cluster not in present and cluster not in known_terminal:
            history.append(reviewed['native'])
            known_terminal.add(cluster)
    missing = [cluster for cluster in receipts if cluster not in present and cluster not in known_terminal]
    for cluster in missing:
        # Asking for more matches than can exist scans the complete account
        # history. One exact manager identity and one result stay bounded.
        text = run_condor(['/usr/bin/condor_history', *cli, str(cluster) + '.0',
            '-limit', '1', '-json', '-attributes', ','.join(ATTRIBUTES)], timeout=30)
        history.extend(parse_ads(text))
    return rows, history, constraint


def execute_events(directory):
    path = Path(directory) / 'pipeline.dag.dagman.out'
    if not path.is_file():
        return []
    # Read a bounded tail; previously observed execute events remain in state.
    with path.open('rb') as stream:
        stream.seek(max(0, path.stat().st_size - 2 * 1024 * 1024))
        text = stream.read().decode('utf-8', errors='replace')
    return sorted({f'{cluster}.{proc}' for cluster, proc in re.findall(
        r'ULOG_EXECUTE[^\n]*\((\d+)\.(\d+)\.\d+\)', text)})


def cached_execution_jobs(previous):
    """Reuse observed child execute events, never migrated manager cluster IDs."""
    result = set()
    for stage in previous.get('stages', {}).values():
        for job in stage.get('observed_execute_events', []):
            if not isinstance(job, str) or not re.fullmatch(r'\d+\.\d+', job):
                raise ValueError('Cached execution evidence needs exact cluster.proc strings')
            result.add(job)
    return result


def polling_state(directory):
    """Recover the last native cache when an older unknown report discarded it."""
    directory = Path(directory)
    path = directory / 'health.json'
    previous = read(path) if path.is_file() else {}
    verified_path = directory / 'last_verified_health.json'
    if (previous.get('scheduler_state') == 'unknown' and 'terminal_history' not in previous
            and verified_path.is_file()):
        verified = read(verified_path)
        if verified.get('scheduler_state') == 'verified':
            for key in ('terminal_history', 'stages'):
                previous[key] = verified.get(key, [] if key == 'terminal_history' else {})
    return previous


def unknown_report(previous, error):
    # Keep native records and observed progress for the next poll. This cache
    # gives no health/completion credit while the scheduler remains unknown.
    return dict(schema='shift-dp-statistics-watchdog-health-v1', health='unknown', scheduler_state='unknown',
                checked_utc=datetime.now(timezone.utc).isoformat(),
                issues=[dict(code='unknown_state', severity='critical', message=str(error))],
                terminal_history=previous.get('terminal_history', []),
                stages=previous.get('stages', {}), no_job_mutations=True)


def stage_counts(directory):
    directory = Path(directory)
    plan_path = directory / 'plan.json'
    plan = read(plan_path) if plan_path.is_file() else {}
    counts = dict(planned_events=plan.get('events', 0), completed_events=0,
                  started_receipts=0, failed_receipts=0, complete_receipts=0,
                  completed_points=0, failed_files=[], latest_receipt_epoch=0)
    for path in sorted((directory / 'statuses').glob('*.json')):
        item = read(path)
        counts['latest_receipt_epoch'] = max(counts['latest_receipt_epoch'], path.stat().st_mtime)
        if item.get('started_utc'):
            counts['started_receipts'] += 1
        if item.get('complete') is True:
            counts['complete_receipts'] += 1
            counts['completed_events'] += item.get('events', item.get('parent', {}).get('events', 0))
        elif item.get('error'):
            counts['failed_receipts'] += 1
            counts['failed_files'].append(str(path))
    for path in sorted((directory / 'completed_points').glob('*.json')):
        if read(path).get('complete') is True:
            counts['completed_points'] += 1
            counts['latest_receipt_epoch'] = max(counts['latest_receipt_epoch'], path.stat().st_mtime)
    source_path = directory / 'source_status.json'
    if source_path.is_file():
        item = read(source_path)
        counts['planned_source_collisions'] = item['source_collisions']
        counts['latest_receipt_epoch'] = max(counts['latest_receipt_epoch'], source_path.stat().st_mtime)
        if item.get('complete') is True:
            counts['complete_receipts'] += 1
            counts['completed_events'] += item['events']
        elif item.get('error'):
            counts['failed_receipts'] += 1
            counts['failed_files'].append(str(source_path))
        heartbeat = directory / 'source_heartbeat.json'
        if heartbeat.is_file():
            progress = read(heartbeat)
            counts['source_heartbeat_epoch'] = timestamp(progress['heartbeat_utc'])
            counts['source_ledger_bytes'] = progress.get('source_ledger_bytes', 0)
            counts['source_phase'] = progress.get('phase')
            counts['source_gen_status'] = progress.get('GEN_status')
            counts['started_receipts'] += int(counts['source_ledger_bytes'] > 0)
    return counts


def reviewed_terminal_failures(config):
    """Keep reviewed failed attempts visible without treating them as active failures."""
    result = {}
    for authority in config.get('retired_aggregate_attempts', []):
        path = Path(authority['path'])
        if (not path.is_absolute() or config.get('dependencies', {}).get(str(path)) != authority['sha256']
                or sha(path) != authority['sha256']):
            raise ValueError('Reviewed failure is outside the frozen watchdog authority')
        proof = read(path)
        native = proof['terminal_native_ad']
        directory = Path(proof['directory'])
        record = proof['aggregate_record']
        if (proof.get('schema') != 'shift-reviewed-aggregate-retirement-v1'
                or proof.get('payload_started') is not False or proof.get('queue_absent') is not True
                or native.get('JobStatus') != 4 or native.get('ExitBySignal') is not False
                or native.get('ExitCode') in (None, 0) or native.get('ProcId') != 0
                or native.get('Iwd') != str(directory) or record.get('directory') != str(directory)
                or read(directory / 'submission.json').get('cluster') != native.get('ClusterId')):
            raise ValueError('Reviewed failure differs from its exact terminal attempt')
        evidence = proof.get('failure_evidence_sha256', {})
        if not evidence:
            raise ValueError('Reviewed failure lacks its original error evidence')
        for name, key in [('plan.json', 'plan_sha256'), ('seal.json', 'seal_sha256'),
                          ('aggregate.sub', 'submit_sha256')]:
            if sha(directory / name) != record[key]:
                raise ValueError('Reviewed failed aggregate changed')
        if any(sha(name) != digest for name, digest in evidence.items()):
            raise ValueError('Reviewed failure evidence changed')
        result[native['ClusterId']] = dict(authority=authority, native=native)
    for authority in config.get('reviewed_gen_handoffs', []):
        path = Path(authority['path'])
        if (not path.is_absolute() or config.get('dependencies', {}).get(str(path)) != authority['sha256']
                or sha(path) != authority['sha256']):
            raise ValueError('Reviewed GEN handoff is outside frozen watchdog authority')
        proof = read(path)
        index = proof['wave_index']
        wave = Path(config['production_directory']) / 'waves' / f'w{index:03d}'
        directory = wave / 'gen_stage'
        native = proof['terminal_native_ad']
        if (proof.get('schema') != 'shift-reviewed-gen-handoff-v1'
                or proof.get('failure_kind') != 'downstream_prepare_ImportError_only'
                or proof.get('science_rerun') is not False or native.get('JobStatus') != 4
                or native.get('ExitBySignal') is not False or native.get('ExitCode') != 1
                or native.get('ProcId') != 0 or native.get('Iwd') != str(directory)
                or native.get('ClusterId') != proof['gen_cluster']
                or read(directory / 'submission.json').get('cluster') != proof['gen_cluster']):
            raise ValueError('Reviewed GEN failure differs from its exact downstream handoff')
        for name in ('gen_inventory.json', 'gen_publication.json'):
            if sha(directory / name) != proof['files'][name] or read(directory / name).get('complete') is not True:
                raise ValueError('Reviewed GEN population is changed or unfinished')
        result[native['ClusterId']] = dict(authority=authority, native=native,
            recovery_submission=str(wave / 'production' / 'submission.json'))
    for authority in config.get('reviewed_meson_finalizer_recoveries', []):
        path = Path(authority['path'])
        if (not path.is_absolute() or config.get('dependencies', {}).get(str(path)) != authority['sha256']
                or sha(path) != authority['sha256']):
            raise ValueError('Reviewed meson recovery is outside frozen watchdog authority')
        proof = read(path)
        original = proof['original_submission']
        directory = Path(original['task'])
        cached = proof['terminal_native_review']
        cache_path = Path(cached['path'])
        if (proof.get('schema') != 'shift-meson-finalizer-recovery-v1'
                or proof.get('allowed_stages') != ['finalizer', 'collect']
                or proof.get('gen_or_detector_jobs_allowed') is not False
                or read(directory/'submission.json') != original
                or sha(directory/'plan.json') != proof['original_plan_sha256']
                or sha(directory/'seal.json') != proof['original_seal_sha256']
                or not cache_path.is_absolute()
                or config.get('dependencies', {}).get(str(cache_path)) != cached['sha256']
                or sha(cache_path) != cached['sha256']):
            raise ValueError('Reviewed meson recovery changed its original population or native record')
        terminal = read(cache_path)
        manager = terminal['history'].get(str(original['cluster']), [])
        finalizer = terminal['history'].get(str(proof['original_finalizer_cluster']), [])
        for rows, cluster, code in [(manager, original['cluster'], proof['original_manager_exit_code']),
                                    (finalizer, proof['original_finalizer_cluster'], proof['original_finalizer_exit_code'])]:
            if (terminal.get('queue') != [] or len(rows) != 1 or rows[0].get('ClusterId') != cluster
                    or rows[0].get('ProcId') != 0 or rows[0].get('Iwd') != str(directory)
                    or rows[0].get('ExitBySignal') is not False or rows[0].get('ExitCode') != code
                    or type(code) is not int or code == 0):
                raise ValueError('Reviewed meson failure differs from its terminal native history')
        if any(sha(name) != digest for name, digest in proof['recovery_files'].items()):
            raise ValueError('Reviewed meson finalizer recovery files changed')
        recovery = Path(proof['recovery_directory'])/'submission.json'
        result[original['cluster']] = dict(authority=authority, native=manager[0],
            failed_finalizer_native=finalizer[0], kind='meson_finalizer_recovery',
            original_directory=str(directory), recovery_submission=str(recovery))
    return result


def assess(config, previous, rows, history, receipts, now):
    root = Path(config['production_directory'])
    issues, stages = [], {}
    known_execute = cached_execution_jobs(previous)
    reviewed = reviewed_terminal_failures(config)

    def issue(code, level, message, **details):
        issues.append(dict(code=code, severity=level, message=message, **details))

    def age_issue(code, age, warning, critical, message, **details):
        if age >= warning:
            issue(code, 'critical' if age >= critical else 'warning', message,
                  age_seconds=round(age), **details)

    queued = {row['ClusterId']: row for row in rows if row.get('ProcId', 0) == 0}
    ended = {row['ClusterId']: row for row in history if row.get('ProcId', 0) == 0}
    retired_meson_directories = {value['original_directory']: cluster for cluster, value in reviewed.items()
                                if value.get('kind') == 'meson_finalizer_recovery'}
    for row in rows:
        if row.get('ShiftDPStatisticsWatchdog'):
            continue
        cluster, status = row['ClusterId'], row['JobStatus']
        retired_meson = retired_meson_directories.get(row.get('Iwd'))
        if cluster in reviewed or retired_meson is not None or row.get('DAGManJobId') in reviewed:
            issue('reviewed_failure_active_' + str(cluster), 'critical',
                  'A reviewed terminal job has reappeared in the queue', job=cluster, status=status)
            continue
        retired = config.get('retired_supervisors', {}).get(str(cluster))
        if retired is not None:
            if row.get('Iwd') != retired or row.get('ProcId', 0) != 0 or status not in (3, 4, 5):
                issue('retired_supervisor_' + str(cluster), 'critical',
                      'Retired supervisor is active again or has an unexpected identity', job=cluster,
                      actual_iwd=row.get('Iwd'), expected_iwd=retired, status=status)
            continue
        parent = row.get('DAGManJobId')
        expected = receipts.get(parent if parent in receipts else cluster)
        if expected and row.get('Iwd') != expected['directory']:
            issue('wrong_iwd_' + str(cluster), 'critical', 'Job does not use its exact persistent working directory',
                  job=cluster, actual=row.get('Iwd'), expected=expected['directory'])
        if status == 5:
            issue('held_' + str(cluster), 'critical', 'Production job is held', job=cluster, reason=row.get('HoldReason'))
        if status == 1:
            age = now - row.get('QDate', row.get('EnteredCurrentStatus', now))
            age_issue('queue_' + str(cluster), age, config['queue_warning_seconds'], config['queue_critical_seconds'],
                      'Job has waited for execution; this is not a running payload', job=cluster,
                      starts=row.get('NumJobStarts', 0))
    for cluster, receipt in receipts.items():
        directory = Path(receipt['directory'])
        current, terminal = queued.get(cluster), ended.get(cluster)
        old = previous.get('stages', {}).get(str(cluster), {})
        stamps = {name: (directory / name).stat().st_mtime_ns if (directory / name).exists() else 0
                  for name in ('plan.json', 'statuses', 'completed_points', 'source_status.json', 'source_heartbeat.json')}
        counts = old['counts'] if old.get('directory_stamps') == stamps else stage_counts(directory)
        executed = (old.get('observed_execute_events', []) if terminal and old else execute_events(directory)) \
            if receipt['kind'] in ('gen_stage', 'production') else []
        known_execute.update(executed)
        signature = json.dumps({key: value for key, value in counts.items()
                               if key not in ('failed_files', 'source_heartbeat_epoch')}, sort_keys=True)
        changed = signature != old.get('progress_signature')
        progress_time = (counts['latest_receipt_epoch'] or receipt['submitted']) if not old else (
            now if changed else old.get('last_progress_epoch', receipt['submitted']))
        children = [row for row in rows if row.get('DAGManJobId') == cluster]
        starts = [row for row in children if f"{row['ClusterId']}.{row.get('ProcId', 0)}" in known_execute]
        summary = dict(receipt, counts=counts, children=len(children),
                       children_status=dict(Counter(str(row['JobStatus']) for row in children)),
                       execute_event_children=len(starts), observed_execute_events=executed,
                       progress_signature=signature, last_progress_epoch=progress_time, directory_stamps=stamps,
                       queue_status=current.get('JobStatus') if current else None,
                       terminal_exit=terminal.get('ExitCode') if terminal else None,
                       execution_verified=bool(starts or counts['started_receipts'] or counts['complete_receipts']))
        stages[str(cluster)] = summary
        if cluster in reviewed:
            expected = reviewed[cluster]['native']
            if terminal and any(terminal.get(key) != expected[key]
                    for key in ('ClusterId', 'ProcId', 'Iwd', 'ExitCode', 'ExitBySignal')):
                raise ValueError('Native history conflicts with the reviewed failed aggregate')
            summary['reviewed_terminal_failure'] = reviewed[cluster]
            recovery = reviewed[cluster].get('recovery_submission')
            if reviewed[cluster].get('kind') == 'meson_finalizer_recovery':
                issue('reviewed_meson_failure_' + str(cluster), 'warning',
                      'Original runtime download failed; its reviewed finalizer recovery is tracked separately',
                      job=cluster, original_exit=expected['ExitCode'])
            if recovery and not Path(recovery).is_file():
                issue('reviewed_handoff_pending_' + str(cluster), 'warning',
                      'Retained inputs have no submitted recovery manager', job=cluster)
            continue
        if counts['failed_receipts']:
            issue('failed_receipts_' + str(cluster), 'critical', 'Worker recorded a failed original; no automatic retry is permitted',
                  manager=cluster, files=counts['failed_files'])
        if terminal:
            if terminal.get('ExitBySignal') or terminal.get('ExitCode') != 0:
                issue('failed_manager_' + str(cluster), 'critical', 'Manager or aggregate finished unsuccessfully',
                      job=cluster, exit_code=terminal.get('ExitCode'))
            continue
        if not current:
            age_issue('missing_manager_' + str(cluster), now - receipt['submitted'], 300, 900,
                      'Receipt has no matching queue/history record; state needs review', job=cluster)
            continue
        if current['JobStatus'] == 2 and receipt['kind'] in ('gen_stage', 'production'):
            active_age = now - current.get('JobStartDate', receipt['submitted'])
            if not children:
                since = old.get('no_children_since_epoch') or (current.get('JobStartDate', receipt['submitted'])
                    if not counts['complete_receipts'] and not known_execute else now)
                summary['no_children_since_epoch'] = since
                age_issue('no_children_' + str(cluster), now - since, 900, 3600,
                          'Active DAG manager has no child jobs', job=cluster)
            limit = config['gen_progress_warning_seconds'] if receipt['kind'] == 'gen_stage' else config['detector_progress_warning_seconds']
            if starts or counts['started_receipts'] or counts['complete_receipts']:
                age_issue('stale_progress_' + str(cluster), now - progress_time, limit, limit * 2,
                          'Started production has no new completed-event/point receipts', job=cluster)
        if current['JobStatus'] == 2 and receipt['kind'] == 'meson_source':
            start = current.get('JobStartDate', receipt['submitted'])
            age_issue('source_heartbeat_' + str(cluster), now - counts.get('source_heartbeat_epoch', start),
                      config['heartbeat_warning_seconds'], config['heartbeat_warning_seconds'] * 2,
                      'Executing meson source heartbeat is stale or missing', job=cluster)
            if summary['execution_verified']:
                limit = config['gen_progress_warning_seconds']
                age_issue('stale_source_progress_' + str(cluster), now - progress_time, limit, limit * 2,
                          'Meson source has no new ledger growth or stage transition', job=cluster)
    controller_state = root / 'state.json'
    controller = read(controller_state) if controller_state.is_file() else {}
    supervisor = queued.get(config['initial_supervisor_cluster'])
    controller_epoch = record_epoch(controller, 'checked_utc', 'checked_epoch')
    heartbeat_path = config.get('supervisor_heartbeat_path')
    if heartbeat_path and Path(heartbeat_path).is_file():
        heartbeat_epoch = record_epoch(read(heartbeat_path), 'checked_utc', 'checked_epoch')
        controller_epoch = max(value for value in (controller_epoch, heartbeat_epoch) if value is not None)
    if controller_epoch is not None:
        age_issue('supervisor_heartbeat', now - controller_epoch,
                  config['heartbeat_warning_seconds'], config['heartbeat_warning_seconds'] * 2,
                  'Supervisor heartbeat is stale')
    elif supervisor and supervisor.get('JobStatus') == 2:
        age_issue('supervisor_heartbeat_missing', now - supervisor.get('JobStartDate', now), 1200, 3600,
                  'Supervisor started but has not written its first heartbeat')
    if controller.get('error'):
        issue('supervisor_error', 'critical', 'Supervisor reports an error', error=controller['error'])
    if controller.get('native_error') or controller.get('native_unknown'):
        issue('supervisor_native_unknown', 'warning', 'Supervisor is waiting for a fresh verified scheduler state',
              error=controller.get('native_error') or controller.get('native_unknown'))
    active_failures = {name: value for name, value in controller.get('failures', {}).items()
                       if value.get('cluster') not in reviewed}
    if active_failures:
        issue('supervisor_failure_records', 'warning', 'Supervisor has failure records requiring reconciliation',
              records=sorted(active_failures))
    worklist = read(config['worklist'])
    published = {point: row.get('events', 0) for point, row in controller.get('published', {}).items()}
    meson = worklist.get('schema') == 'shift-dp-meson-production-v1'
    if meson:
        names = {row['batch']: row['point'] for row in worklist['batches']}
        published = {names[batch]: row['published']['events']
                     for batch, row in controller.get('batches', {}).items() if row.get('published')}
    operations = {}
    operation_receipts = {value['operation_name']: (cluster, value) for cluster, value in receipts.items()
                          if value['kind'] == 'operation'}
    for specification in config.get('operational_jobs', []):
        name = specification['name']
        known = operation_receipts.get(name)
        state_path = Path(specification['state_path']) if specification.get('state_path') else None
        state = read(state_path) if state_path and state_path.is_file() else {}
        heartbeat_path = Path(specification['heartbeat_path']) if specification.get('heartbeat_path') else None
        heartbeat = read(heartbeat_path) if heartbeat_path and heartbeat_path.is_file() else {}
        epochs = [record_epoch(value, 'checked_utc', 'checked_epoch') for value in (state, heartbeat)]
        heartbeat_epoch = max((value for value in epochs if value is not None), default=None)
        summary = dict(receipt_pending=known is None, complete=state.get('complete') is True,
                       phase=state.get('phase'), heartbeat_epoch=heartbeat_epoch,
                       native_success=False)
        operations[name] = summary
        if known is None:
            continue
        cluster, authority = known
        current, terminal = queued.get(cluster), ended.get(cluster)
        actual = current or terminal
        summary.update(cluster=cluster, queue_status=current.get('JobStatus') if current else None,
                       receipt_sha256=authority['receipt_sha256'],
                       state_checked_epoch=epochs[0], native_success=bool(terminal and
                           terminal.get('JobStatus') == 4 and terminal.get('ExitBySignal') is False
                           and terminal.get('ExitCode') == 0))
        old = previous.get('operations', {}).get(name, {})
        if old.get('cluster') not in (None, cluster) or old.get('receipt_sha256') not in (None, authority['receipt_sha256']):
            raise ValueError('Previously observed operational authority changed: ' + name)
        if actual and (actual.get('Iwd') != specification['iwd'] or
                       (specification.get('cmd') and actual.get('Cmd') != specification['cmd'])):
            issue('operation_identity_' + name, 'critical', 'Operational job has an unexpected Iwd or executable', job=cluster)
        if state.get('error'):
            issue('operation_error_' + name, 'critical', 'Operational controller reports an error',
                  job=cluster, error=state['error'])
        if state.get('native_error') or state.get('native_unknown'):
            issue('operation_native_unknown_' + name, 'warning', 'Operational controller is waiting for verified scheduler state',
                  job=cluster, error=state.get('native_error') or state.get('native_unknown'))
        if current and current['JobStatus'] == 2 and not summary['complete'] and heartbeat_path:
            age = now - (heartbeat_epoch or current.get('JobStartDate', authority['submitted']))
            limit = specification.get('heartbeat_warning_seconds', config['heartbeat_warning_seconds'])
            age_issue('operation_heartbeat_' + name, age, limit, limit * 2,
                      'Operational controller heartbeat is stale or missing', job=cluster)
        if terminal and summary['native_success'] and specification.get('require_state_complete') and not summary['complete']:
            age_issue('operation_completion_' + name, now - terminal.get('CompletionDate', now), 300, 900,
                      'Operational job exited without its required final completion record', job=cluster)
    for specification in config.get('operational_jobs', []):
        name = specification['name']
        upstream = operations.get(specification.get('after_job'))
        if operations[name]['receipt_pending'] and upstream and upstream['complete']:
            age_issue('operation_handoff_' + name, now - (upstream.get('state_checked_epoch') or now), 300, 900,
                      'Completed admission has no downstream submission receipt')
    supervisor_finished = controller.get('finished', controller.get('complete', False))
    required = config.get('finish_requires_operational_jobs', [])
    if any(name not in operations for name in required):
        raise ValueError('Unknown required operational completion job')
    finished = supervisor_finished and all(operations[name]['complete'] and operations[name]['native_success']
                                            for name in required)
    severity = 'critical' if any(item['severity'] == 'critical' for item in issues) else 'warning' if issues else 'ok'
    report = dict(schema='shift-dp-statistics-watchdog-health-v1', checked_utc=datetime.fromtimestamp(now, timezone.utc).isoformat(),
                  health=severity, scheduler_state='verified', issues=issues, stages=stages,
                  executed_jobs=sorted(known_execute), actual_planned_progress=dict(
                      authorized_new_events=(None if meson else worklist['total_additional_events']),
                      gen_completed_events=sum(row['counts']['completed_events'] for row in stages.values()
                                               if row['kind'] in ('gen_stage', 'meson_source')
                                               and not row.get('external_prerequisite')),
                      detector_completed_events=sum(row['counts']['completed_events'] for row in stages.values()
                          if row['kind'] == 'production' and not row.get('external_prerequisite')),
                      published_total_events_by_point=published,
                      canary_published=controller.get('canary_published', False)),
                  supervisor_heartbeat=(datetime.fromtimestamp(controller_epoch, timezone.utc).isoformat()
                                         if controller_epoch is not None else None),
                  supervisor_finished=supervisor_finished, operations=operations, controller_finished=finished,
                  observed_state=('finished' if finished else 'payload_execution_verified'
                      if any(row['execution_verified'] for row in stages.values()
                             if row['kind'] in ('gen_stage', 'meson_source', 'production')
                             and not row.get('external_prerequisite')) else 'awaiting_payload_execution'),
                  no_job_mutations=True)
    report['reviewed_terminal_failures'] = list(reviewed.values())
    report['reviewed_controller_failure_records'] = {
        name: value for name, value in controller.get('failures', {}).items()
        if value.get('cluster') in reviewed}
    if meson:
        report['actual_planned_progress'].update(
            authorized_source_collisions=sum(row['source_collisions'] for row in worklist['batches']),
            expected_signal_events=sum(row['expected_signal_events'] for row in worklist['batches']))
    return report


def poll(config, previous):
    if config.get('resolve_supervisor_from_receipt'):
        # Freeze a monitor before its controller has a scheduler job number.
        # Resolve only the receipt for this exact sealed submit description.
        root = Path(config['production_directory'])
        deployment = Path(config.get('supervisor_deployment_directory', root))
        receipt_path = Path(config['supervisor_receipt'])
        receipt = read(receipt_path)
        cluster = receipt.get('cluster')
        if (not deployment.is_absolute()
                or receipt_path != deployment / 'submission.json' or receipt.get('task') != str(deployment)
                or type(cluster) is not int or cluster <= 0
                or receipt.get('submit_sha256') != sha(deployment / 'controller.sub')):
            raise ValueError('Dynamic watchdog supervisor receipt changed its exact launch identity')
        if deployment != root:
            authority = str(deployment / 'config.json')
            if (config.get('dependencies', {}).get(authority) != sha(authority)
                    or read(authority).get('directory') != str(root)):
                raise ValueError('Operational watchdog supervisor changed its production authority')
        config = dict(config, initial_supervisor_cluster=cluster, initial_gen_manager_cluster=cluster)
    root = Path(config['production_directory'])
    receipts = manager_receipts(root, config.get('supervisor_receipt'), config.get('external_productions', []),
                                config.get('operational_jobs', []))
    if (config.get('supervisor_receipt') and
            receipts.get(config['initial_supervisor_cluster'], {}).get('kind') != 'supervisor'):
        raise ValueError('Explicit supervisor receipt differs from the configured native cluster')
    rows, history, constraint = scheduler_snapshot(config, receipts, previous)
    report = assess(config, previous, rows, history, receipts, time.time())
    report['query_constraint'] = constraint
    report['terminal_history'] = history
    return report, dict(queue=rows, history=history)


def record(config, previous, report, snapshot):
    directory = Path(config['state_directory'])
    old_issues = {item['code']: item for item in previous.get('issues', [])}
    for issue in report.get('issues', []):
        issue['first_seen_utc'] = old_issues.get(issue['code'], {}).get('first_seen_utc', report['checked_utc'])
    save(directory / 'health.json', report)
    save(directory / 'scheduler_snapshot.json', snapshot)
    if report['scheduler_state'] == 'verified':
        save(directory / 'last_verified_health.json', report)
    if report['health'] in ('critical', 'unknown'):
        save(directory / 'failure_capture.json', dict(health=report, scheduler=snapshot))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    config = read(args.config)
    if config.get('schema') != 'shift-dp-statistics-watchdog-v1':
        raise ValueError('Unsupported watchdog configuration')
    for path, expected in config['dependencies'].items():
        if sha(path) != expected:
            raise ValueError('Frozen watchdog dependency changed: ' + path)
    directory = Path(config['state_directory'])
    if not directory.is_absolute() or str(directory).startswith('/tmp/'):
        raise ValueError('Watchdog needs an absolute persistent state directory')
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'watchdog.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        while True:
            previous = polling_state(directory)
            try:
                report, snapshot = poll(config, previous)
            except Exception as error:
                report = unknown_report(previous, error)
                snapshot = dict(error=repr(error))
            record(config, previous, report, snapshot)
            if report['health'] != previous.get('health') or args.once:
                print(json.dumps(dict(checked_utc=report['checked_utc'], health=report['health'],
                                      issues=report['issues'], actual_planned_progress=report.get('actual_planned_progress'))), flush=True)
            if args.once or report.get('controller_finished'):
                return
            time.sleep(config.get('poll_seconds', 120))


if __name__ == '__main__':
    main()
