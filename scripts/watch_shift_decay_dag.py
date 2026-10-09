#!/usr/bin/env python3
"""Keep a durable pipeline heartbeat independently of the login session."""
from collections import Counter
import argparse
import hashlib
import json
from pathlib import Path
import re
import time

from shift_condor_native import query


def complete_nano_inventory(root):
    """Publish Nano completion independently of later histogram/merge stages."""
    manifest=json.loads((root/'manifest.json').read_text())
    sources={s['index']:s for s in manifest['sources']}
    totals=Counter()
    rows=[tuple(map(int,line.split())) for line in (root/'all_jobs.txt').read_text().splitlines()]
    if (len(rows)!=manifest['jobs'] or any(len(r)!=4 for r in rows) or
            {r[3] for r in rows}!=set(range(manifest['jobs']))):
        raise ValueError('Final Nano job inventory differs from frozen plan')
    tiers=('GEN','SIM','DIGIHLT','RECO','NANO')
    for source,skip,count,job in rows:
        path=root/'results'/f'g{job//500}'/f'status{job}.json'
        row=json.loads(path.read_text());stratum=sources[source]['stratum']
        nano=str(Path(manifest['eos_output'])/stratum/f'job{job:07d}'/'nano.root')
        if (count<=0 or skip<0 or skip+count>sources[source]['events'] or
                row.get('job')!=job or row.get('events')!=count or row.get('exit_code')!=0 or
                row.get('complete') is not True or row.get('source_stratum')!=stratum or
                row.get('validated_tier_events')!={tier:count for tier in tiers} or
                row.get('nano_path')!=nano or not isinstance(row.get('nano_bytes'),int) or
                row['nano_bytes']<=0 or not re.fullmatch('[0-9a-f]{64}',row.get('report_sha256') or '')):
            raise ValueError('Final Nano receipt failed exact validation: '+str(path))
        totals[stratum]+=count
    if dict(totals)!=manifest['strata']:
        raise ValueError('Final Nano stratum counts differ from plan')
    if manifest['detector_sampling']['plan_sha256']!=hashlib.sha256(
            (root/'sampling_plan.json').read_bytes()).hexdigest():
        raise ValueError('Sampling plan differs from frozen production')
    record=dict(complete=True,jobs=manifest['jobs'],events=manifest['events'],
        tier_events={stratum:{tier:count for tier in tiers} for stratum,count in totals.items()},
        completed_at_epoch=time.time(),result_layout='results/g<job//500>/status<job>.json')
    temporary=root/'production_complete.json.tmp'
    temporary.write_text(json.dumps(record,indent=2)+'\n')
    temporary.replace(root/'production_complete.json')


def save(path, record):
    temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(record,indent=2)+'\n')
    temporary.replace(path)


def step(root, *, local_schedd=True):
    submission=json.loads((root/'submission.json').read_text())
    cluster=submission['dagman_cluster']
    ads=query(f'ClusterId == {cluster} || DAGManJobId == {cluster}',
              ['ClusterId','ProcId','JobStatus','DAGManJobId'],
              local_schedd=local_schedd,timeout=45)
    result=dict(checked_at_epoch=time.time(),health='running',dagman_cluster=cluster,
                terminal=False,queue_status_counts=dict(Counter(str(ad.get('JobStatus')) for ad in ads)))
    nodes={}
    node=root/'node_status'
    if node.exists():
        for block in re.findall(r'\[([^]]+)\]',node.read_text()):
            name=re.search(r'Node\s*=\s*"([^"]+)"',block)
            status=re.search(r'NodeStatus\s*=\s*(\d+)',block)
            if name and status:
                if name[1] in nodes:
                    raise ValueError('Duplicate node in DAG status: '+name[1])
                nodes[name[1]]=int(status[1])
        result['node_status_counts']=dict(Counter(str(s) for s in nodes.values()))
    manifest=json.loads((root/'manifest.json').read_text())
    nano={name:status for name,status in nodes.items() if re.fullmatch(r'N\d+',name)}
    result.update(nano_jobs_expected=manifest['jobs'],nano_jobs_done=sum(s==5 for s in nano.values()),
                  failed_nano_jobs=sorted(int(n[1:]) for n,s in nano.items() if s==6))
    expected={f'N{job:05d}' for job in range(manifest['jobs'])}
    if set(nano)==expected and all(s==5 for s in nano.values()):
        if not (root/'production_complete.json').exists():
            complete_nano_inventory(root)
        result['nano_complete']=True
    for phase in ('gate','final'):
        path=root/(phase+'_complete.json')
        if path.exists():
            record=json.loads(path.read_text())
            result[phase+'_complete']=bool(record.get('complete'))
            if record.get('error'):
                result[phase+'_error']=record['error']
    if not ads:
        # Absence from condor_q alone is not proof of termination. Metrics
        # must belong to this submission and show a completed DAGMan run.
        metrics_path=root/'production.dag.metrics'
        metrics=json.loads(metrics_path.read_text()) if metrics_path.exists() else {}
        if str(metrics.get('dagman_id'))==str(cluster) and metrics.get('end_time',0)>0:
            result.update(terminal=True,dag_exit_code=metrics['exitcode'])
            if metrics['exitcode']==0 and result.get('nano_complete') and result.get('final_complete'):
                result['health']='complete'
            else:
                result['health']='failed'
                result['error']='DAG ended before all production stages completed; inspect failed nodes and retained logs.'
                save(root/'production_failed.json',result)
        else:
            result['health']='dagman_not_live'
    elif not any(ad['ClusterId']==cluster for ad in ads):
        result['health']='draining'
    save(root/'live_status.json',result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root',type=Path)
    parser.add_argument('--once',action='store_true')
    args=parser.parse_args()
    root=args.root.resolve()
    errors=0
    missing=0
    while True:
        try:
            result=step(root,local_schedd=not args.once)
            errors=0
            missing=missing+1 if result['health']=='dagman_not_live' else 0
            if missing>=5:
                result.update(health='monitor_error',error='No associated jobs or matching terminal DAG metrics after five checks.')
                save(root/'live_status.json',result)
                return
        except Exception as error:
            errors+=1
            result=dict(checked_at_epoch=time.time(),health='monitor_error',error=repr(error),
                        consecutive_errors=errors,terminal=False)
            save(root/'live_status.json',result)
            # Do not mislabel a scheduler-query error as production failure.
            # Stop an unusable monitor after bounded retries, keeping its status.
            if errors>=5:
                return
        if args.once:
            print(json.dumps(result,indent=2))
            return
        if result.get('terminal'):
            return
        time.sleep(60)


if __name__=='__main__':
    main()
