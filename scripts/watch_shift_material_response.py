#!/usr/bin/env python3
# Code style: AI-code. AI-generated read-only material-response DAG supervision.
"""Keep a persistent heartbeat; never retry, remove or release science jobs."""
import argparse
from collections import Counter
import json
from pathlib import Path
import time

from shift_condor_native import query


def save(path, data):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, indent=2, allow_nan=False)+'\n')
    temporary.replace(path)


def step(directory, cluster, previous, local_schedd=None, dag_directory=None, qualification_cluster=None,
         preserved_manager=None):
    dag_directory = directory if dag_directory is None else dag_directory
    constraint = f'ClusterId == {cluster} || DAGManJobId == {cluster}'
    if qualification_cluster is not None:
        constraint += f' || ClusterId == {qualification_cluster}'
    if preserved_manager is not None:
        constraint += f' || DAGManJobId == {preserved_manager}'
    ads = query(constraint,
                ['ClusterId', 'ProcId', 'JobStatus', 'Iwd', 'HoldReason',
                 'NumJobStarts', 'RemoteUserCpu', 'RemoteSysCpu', 'JobCurrentStartDate'],
                local_schedd=local_schedd, timeout=45)
    now = time.time()
    managers = [ad for ad in ads if ad['ClusterId'] == cluster]
    children = [ad for ad in ads if ad['ClusterId'] not in (cluster, qualification_cluster)]
    qualification = [ad for ad in ads if ad['ClusterId'] == qualification_cluster]
    cache, errors = {}, []
    for path in (directory/'jobs').glob('*/status.json'):
        stat = path.stat()
        old = previous.get('receipts', {}).get(path.parent.name, {})
        if old.get('mtime_ns') == stat.st_mtime_ns and old.get('bytes') == stat.st_size:
            cache[path.parent.name] = old
        else:
            status = json.loads(path.read_text())
            cache[path.parent.name] = dict(mtime_ns=stat.st_mtime_ns, bytes=stat.st_size,
                complete=status.get('complete') is True, error=status.get('error'))
    failures = [name for name, value in cache.items() if not value['complete']]
    held = [ad for ad in ads if ad['JobStatus'] == 5]
    if held:
        errors.append('Held jobs require review; no automatic retry.')
    if failures:
        errors.append('Returned worker failures: '+', '.join(failures))
    if managers and managers[0]['Iwd'] != str(dag_directory):
        errors.append('DAG manager lost its persistent working directory.')
    missing_since = previous.get('no_children_since', now) if not children else now
    if managers and not children and now-missing_since > 600:
        errors.append('DAG manager has no children and has not advanced for ten minutes.')
    metrics_path = dag_directory/'production.dag.metrics'
    metrics = json.loads(metrics_path.read_text()) if metrics_path.exists() else {}
    native_terminal = (not managers and not children and str(metrics.get('dagman_id')) == str(cluster)
                       and metrics.get('end_time', 0) > 0)
    summary = directory/'summary.json'
    complete = bool(native_terminal and metrics.get('exitcode') == 0 and summary.exists()
                    and json.loads(summary.read_text()).get('complete'))
    if qualification_cluster is not None:
        evidence = directory/'qualification.json'
        complete = bool(complete and not qualification and evidence.exists()
                        and json.loads(evidence.read_text()).get('complete'))
    if not managers and not native_terminal and not children:
        errors.append('Manager is absent without matching native terminal evidence.')
    if native_terminal and not complete and not qualification:
        errors.append('DAG ended without the full response comparison.')
    record = dict(checked_at_epoch=now, manager_cluster=cluster,
        phase='complete' if complete else ('attention_required' if errors else 'running'),
        terminal=native_terminal and not qualification, complete=complete, problems=errors,
        queue_counts=dict(Counter(str(ad['JobStatus']) for ad in ads)),
        native_execution_seen=any(ad.get('NumJobStarts', 0) > 0 for ad in children),
        live_jobs=ads, receipts=cache, successful_jobs=sum(x['complete'] for x in cache.values()),
        no_children_since=missing_since, automatic_science_actions=False)
    save(directory/'heartbeat.json', record)
    if errors:
        save(directory/'attention_required.json', record)
    return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('cluster', type=int)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--dag-directory', type=Path)
    parser.add_argument('--qualification-cluster', type=int)
    parser.add_argument('--preserved-manager', type=int)
    args = parser.parse_args()
    directory, previous = args.directory.resolve(), {}
    failures = 0
    while True:
        try:
            previous = step(directory, args.cluster, previous, local_schedd=not args.once,
                            dag_directory=args.dag_directory, qualification_cluster=args.qualification_cluster,
                            preserved_manager=args.preserved_manager)
            failures = 0
        except Exception as error:
            failures += 1
            save(directory/'monitor_error.json', dict(checked_at_epoch=time.time(),
                 error=repr(error), scheduler_state='unknown', consecutive_errors=failures))
            if failures >= 5 or args.once:
                raise
        if args.once or previous.get('terminal'):
            break
        time.sleep(60)
