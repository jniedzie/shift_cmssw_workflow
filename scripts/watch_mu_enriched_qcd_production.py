#!/usr/bin/env python3
# Code style: AI-code. AI-generated read-only detached GEN supervision.
"""Record missing managers, held workers and stalled GEN stage transitions.

This supervisor only reads the local scheduler and campaign receipts. It cannot
submit, release, retry, remove or change a scientific job or an analysis alias.
"""
import argparse
import fcntl
import json
from pathlib import Path
import time

from shift_condor_native import query


def save(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def read_optional(path):
    return json.loads(path.read_text()) if path.is_file() else {}


def completed_receipts(directory, cache):
    """Read changed receipts once; large arrays are not reread every minute."""
    updated = {}
    for group in ('ordinary', 'weighted'):
        for path in (directory / group / 'results').glob('*.json'):
            name = str(path.relative_to(directory))
            stat = path.stat()
            previous = cache.get(name, {})
            if previous.get('mtime_ns') == stat.st_mtime_ns and previous.get('bytes') == stat.st_size:
                updated[name] = previous
                continue
            record = json.loads(path.read_text())
            updated[name] = dict(mtime_ns=stat.st_mtime_ns, bytes=stat.st_size,
                complete=record.get('complete') is True,
                publication_complete=record.get('publication_complete') is True,
                stage=record.get('request', {}).get('stage'),
                event_count=record.get('event_count', record.get('semantic_audit', {}).get('events', 0)),
                error=record.get('error'))
    return updated


def assess(policy, ads, receipts, documents, previous, now):
    manager = [ad for ad in ads if ad.get('ClusterId') == policy['manager_cluster']
               and ad.get('ProcId') == 0]
    payloads = [ad for ad in ads if ad.get('ShiftMuEnrichedQCD') is True
                and ad.get('ShiftSuiteTag') == policy['suite_tag']
                and ad.get('DAGManJobId') == policy['manager_cluster']]
    live = [ad for ad in payloads if ad.get('JobStatus') in (1, 2, 6, 7)]
    held = [ad for ad in payloads if ad.get('JobStatus') == 5]
    completed = [record for record in receipts.values()
                 if record.get('complete') and record.get('publication_complete')]
    failures = [name for name, record in receipts.items() if record.get('error')]
    gates = {name:documents.get(name, {}) for name in
             ('ordinary_gate', 'weighted_gate', 'ordinary_complete', 'weighted_complete')}
    final = all(gates[key].get('complete') is True for key in ('ordinary_complete', 'weighted_complete'))
    signature = [len(completed), len(failures), len(held),
                 sorted((ad.get('ClusterId'), ad.get('ProcId'), ad.get('JobStatus'),
                         ad.get('NumJobStarts', 0)) for ad in live),
                 [bool(gates[key]) for key in gates]]
    # JSON round trips convert tuples to lists, so use one normalized form.
    signature = json.loads(json.dumps(signature))
    last_progress = now if signature != previous.get('progress_signature') else previous.get('last_progress_at_epoch', now)
    problems = []
    if not manager and not final:
        problems.append('GEN manager is missing before both final accounting records completed')
    elif manager:
        ad = manager[0]
        if ad.get('JobStatus') in (3, 4, 5) and not final:
            problems.append('GEN manager is held or ended before final accounting completed')
        if ad.get('Iwd') != policy['campaign_directory']:
            problems.append('GEN manager working directory differs from the persistent deployment')
        if ad.get('MaxRuntime', 0) < policy['required_manager_runtime_seconds']:
            problems.append('GEN manager runtime limit is shorter than the required production lifetime')
    if held:
        problems.append('GEN payload workers are held; no automatic science retry is authorized')
    if failures:
        problems.append('Returned GEN worker receipts contain failures')
    if gates['ordinary_gate'] and gates['ordinary_gate'].get('complete') is not True:
        problems.append('Ordinary low-bin canary accounting gate did not pass')
    if gates['weighted_gate'] and gates['weighted_gate'].get('pass') is not True:
        problems.append('Weighted high-bin scientific gate did not pass; preserve ordinary branch progress')
    if not live and not final and now - last_progress > policy['no_child_timeout_seconds']:
        problems.append('GEN manager has no active payload workers and no recent stage progress')
    heartbeat = documents.get('admission', {})
    if not live and not final and now - heartbeat.get('checked_at_epoch', last_progress) > policy['no_child_timeout_seconds']:
        problems.append('GEN admission heartbeat is stale while no payload workers are active')
    return dict(checked_at_epoch=now, scope='GEN production only',
        phase='complete' if final else ('attention_required' if problems else 'observing'),
        attention_required=bool(problems), problems=problems,
        manager=manager, live_payloads=len(live), held_payloads=len(held),
        held_job_ids=[str(ad['ClusterId']) + '.' + str(ad['ProcId']) for ad in held],
        completed_publication_jobs=len(completed), completed_events=sum(record.get('event_count', 0) or 0 for record in completed),
        failed_receipts=failures, gates=gates, admission=heartbeat,
        progress_signature=signature, last_progress_at_epoch=last_progress,
        worker_action='none; read-only supervision', analysis_release=False)


def run(directory, once=False):
    policy = json.loads((directory / 'policy.json').read_text())
    campaign = Path(policy['campaign_directory'])
    if not campaign.is_absolute() or campaign != campaign.resolve():
        raise ValueError('Require a persistent absolute GEN deployment directory')
    with (directory / 'supervisor.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        previous = read_optional(directory / 'heartbeat.json')
        cache = read_optional(directory / 'receipt_cache.json')
        while True:
            now = time.time()
            try:
                constraint = '(ClusterId == ' + str(policy['manager_cluster']) + ') || (ShiftMuEnrichedQCD == true && DAGManJobId == ' + str(policy['manager_cluster']) + ' && ShiftSuiteTag == ' + json.dumps(policy['suite_tag']) + ')'
                ads = query(constraint, ['ClusterId', 'ProcId', 'JobStatus', 'NumJobStarts',
                    'Iwd', 'MaxRuntime', 'HoldReason', 'ShiftMuEnrichedQCD', 'ShiftSuiteTag', 'DAGManJobId'],
                    timeout=30, local_schedd=True)
                cache = completed_receipts(campaign, cache)
                paths = dict(admission='admission/heartbeat.json',
                    ordinary_gate='ordinary/canary_gate.json', weighted_gate='weighted/scientific_gate.json',
                    ordinary_complete='ordinary/production_complete.json', weighted_complete='weighted/production_complete.json')
                documents = {name:read_optional(campaign / path) for name, path in paths.items()}
                result = assess(policy, ads, cache, documents, previous, now)
                save(directory / 'receipt_cache.json', cache)
            except Exception as error:
                result = previous | dict(checked_at_epoch=now, scope='GEN production only',
                    phase='observer_fault', attention_required=True,
                    problems=['Scheduler or receipt state is unknown: ' + repr(error)],
                    worker_action='none; read-only supervision', analysis_release=False)
            save(directory / 'heartbeat.json', result)
            if result['attention_required']:
                save(directory / 'attention_required.json', result)
            elif (directory / 'attention_required.json').exists():
                save(directory / 'attention_cleared.json', dict(checked_at_epoch=now, phase=result['phase']))
            previous = result
            if once or result['phase'] == 'complete':
                return 0
            time.sleep(policy['poll_seconds'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--once', action='store_true')
    arguments = parser.parse_args()
    raise SystemExit(run(arguments.directory.resolve(), arguments.once))
