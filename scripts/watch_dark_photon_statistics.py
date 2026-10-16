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
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import time

from shift_condor_native import query, run_condor, parse_ads


ATTRIBUTES = ['ClusterId', 'ProcId', 'DAGManJobId', 'JobStatus', 'Iwd', 'NumJobStarts',
              'QDate', 'JobStartDate', 'EnteredCurrentStatus', 'RemoteHost', 'HoldReason',
              'ExitCode', 'ExitBySignal', 'CompletionDate', 'Args', 'Out',
              'ShiftDPStatisticsWatchdog', 'RemoteUserCpu', 'RemoteSysCpu']


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.next')
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n')
    os.replace(temporary, path)


def timestamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def manager_receipts(root, supervisor_receipt=None):
    explicit_supervisor = supervisor_receipt is not None
    supervisor_receipt = Path(supervisor_receipt) if supervisor_receipt else root / 'submission.json'
    if not supervisor_receipt.is_absolute():
        raise ValueError('Supervisor receipt must use an absolute persistent path')
    if explicit_supervisor and not supervisor_receipt.is_file():
        raise ValueError('Explicit supervisor receipt is missing: ' + str(supervisor_receipt))
    paths = [supervisor_receipt]
    paths += sorted((root / 'waves').glob('w*/gen_stage/submission.json'))
    paths += sorted((root / 'waves').glob('w*/production/submission.json'))
    paths += sorted((root / 'batches').glob('*/submission.json'))
    paths += sorted((root / 'batches').glob('*/production/submission.json'))
    paths += sorted((root / 'aggregates').glob('*/v*/submission.json'))
    result = {}
    for path in paths:
        if not path.is_file():
            continue
        receipt = read(path)
        cluster = receipt.get('cluster')
        if type(cluster) is not int or cluster <= 0:
            raise ValueError('Invalid submission receipt: ' + str(path))
        result[cluster] = dict(directory=str(path.parent), receipt=str(path),
                               submitted=timestamp(receipt['utc']), kind=(
                                   'supervisor' if path == supervisor_receipt else
                                   'aggregate' if 'aggregates' in path.parts else
                                   'meson_source' if path.parent.parent == root / 'batches' else path.parent.name))
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


def assess(config, previous, rows, history, receipts, now):
    root = Path(config['production_directory'])
    issues, stages = [], {}
    known_execute = set(previous.get('executed_jobs', []))

    def issue(code, level, message, **details):
        issues.append(dict(code=code, severity=level, message=message, **details))

    def age_issue(code, age, warning, critical, message, **details):
        if age >= warning:
            issue(code, 'critical' if age >= critical else 'warning', message,
                  age_seconds=round(age), **details)

    queued = {row['ClusterId']: row for row in rows if row.get('ProcId', 0) == 0}
    ended = {row['ClusterId']: row for row in history if row.get('ProcId', 0) == 0}
    for row in rows:
        if row.get('ShiftDPStatisticsWatchdog'):
            continue
        cluster, status = row['ClusterId'], row['JobStatus']
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
    if controller.get('checked_utc'):
        age_issue('supervisor_heartbeat', now - timestamp(controller['checked_utc']),
                  config['heartbeat_warning_seconds'], config['heartbeat_warning_seconds'] * 2,
                  'Supervisor heartbeat is stale')
    elif supervisor and supervisor.get('JobStatus') == 2:
        age_issue('supervisor_heartbeat_missing', now - supervisor.get('JobStartDate', now), 1200, 3600,
                  'Supervisor started but has not written its first heartbeat')
    if controller.get('error'):
        issue('supervisor_error', 'critical', 'Supervisor reports an error', error=controller['error'])
    if controller.get('failures'):
        issue('supervisor_failure_records', 'warning', 'Supervisor has failure records requiring reconciliation',
              records=sorted(controller['failures']))
    worklist = read(config['worklist'])
    published = {point: row.get('events', 0) for point, row in controller.get('published', {}).items()}
    meson = worklist.get('schema') == 'shift-dp-meson-production-v1'
    if meson:
        names = {row['batch']: row['point'] for row in worklist['batches']}
        published = {names[batch]: row['published']['events']
                     for batch, row in controller.get('batches', {}).items() if row.get('published')}
    finished = controller.get('finished', controller.get('complete', False))
    severity = 'critical' if any(item['severity'] == 'critical' for item in issues) else 'warning' if issues else 'ok'
    report = dict(schema='shift-dp-statistics-watchdog-health-v1', checked_utc=datetime.fromtimestamp(now, timezone.utc).isoformat(),
                  health=severity, scheduler_state='verified', issues=issues, stages=stages,
                  executed_jobs=sorted(known_execute), actual_planned_progress=dict(
                      authorized_new_events=(None if meson else worklist['total_additional_events']),
                      gen_completed_events=sum(row['counts']['completed_events'] for row in stages.values()
                                               if row['kind'] in ('gen_stage', 'meson_source')),
                      detector_completed_events=sum(row['counts']['completed_events'] for row in stages.values() if row['kind'] == 'production'),
                      published_total_events_by_point=published,
                      canary_published=controller.get('canary_published', False)),
                  supervisor_heartbeat=controller.get('checked_utc'), controller_finished=finished,
                  observed_state=('finished' if finished else 'payload_execution_verified'
                      if any(row['execution_verified'] for row in stages.values()
                             if row['kind'] in ('gen_stage', 'meson_source', 'production')) else 'awaiting_payload_execution'),
                  no_job_mutations=True)
    if meson:
        report['actual_planned_progress'].update(
            authorized_source_collisions=sum(row['source_collisions'] for row in worklist['batches']),
            expected_signal_events=sum(row['expected_signal_events'] for row in worklist['batches']))
    return report


def poll(config, previous):
    root = Path(config['production_directory'])
    receipts = manager_receipts(root, config.get('supervisor_receipt'))
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
            previous = read(directory / 'health.json') if (directory / 'health.json').is_file() else {}
            try:
                report, snapshot = poll(config, previous)
            except Exception as error:
                report = dict(schema='shift-dp-statistics-watchdog-health-v1', health='unknown', scheduler_state='unknown',
                              checked_utc=datetime.now(timezone.utc).isoformat(),
                              issues=[dict(code='unknown_state', severity='critical', message=str(error))],
                              no_job_mutations=True)
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
