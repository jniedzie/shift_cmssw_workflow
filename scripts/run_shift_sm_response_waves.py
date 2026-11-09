#!/usr/bin/env python3
# Code style: AI-code.
"""Recover reviewed launcher-lock failures and share the SM wave worker budget.

This operates existing frozen source draws. It never generates events, redraws
selection, releases held physics jobs, or retries an unclassified failure.
"""
import argparse
from collections import Counter
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
import time

from monitor_shift_sm_response_benchmark import write_snapshot, wait_for_lock
from shift_condor_native import query, NativeCondorError


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def limits(counts, budget, active_count=4):
    """Reserve live children once; let overlarge waves drain without eviction."""
    if type(budget) is not int or budget < 1 or type(active_count) is not int or active_count < 1:
        raise ValueError('Positive worker and active-wave budgets required')
    if any(type(v) is not int or v < 0 for v in counts.values()):
        raise ValueError('Invalid native worker census')
    result = {k: 1 for k in counts}
    if not counts:
        return result
    if sum(max(1,value) for value in counts.values()) > budget:
        return result
    active = list(counts)[:active_count]
    desired = max(1, (budget-(len(counts)-len(active)))//len(active))
    for key in active:
        result[key] = min(max(counts[key], 1), desired)
    free = budget-sum(max(counts[k],result[k]) for k in counts)
    while free > 0:
        eligible = [k for k in active if result[k] < desired]
        if not eligible:
            break
        each = max(1, free//len(eligible))
        for key in eligible:
            extra = min(free, each, desired-result[key])
            result[key] += extra
            free -= extra
    return result


def native_launch_failure(directory):
    """Only the reviewed suffix-collision traceback, before any wave ownership."""
    directory = Path(directory)
    log, error = directory/'events.log', directory/'worker.err'
    if not log.exists() or not error.exists():
        return False
    text, stderr = log.read_text(), error.read_text()
    return ('Normal termination (return value 1)' in text
        and 'BlockingIOError: [Errno 11]' in stderr
        and 'prepare_wave' in stderr and 'fcntl.flock' in stderr)


def discover(config, state):
    from run_shift_sm_candidate_handoff import prepare_wave, submit_wave
    manifest = Path(config['wave_manifest']['path'])
    if sha(manifest) != config['wave_manifest']['sha256']:
        raise ValueError('Frozen source-wave worklist changed')
    root = Path(config['root_directory'])
    found = {}
    recovered_this_poll = 0
    for row in read(manifest)['waves']:
        wid = row['wave_id']
        if sha(row['configuration']['path']) != row['configuration']['sha256']:
            raise ValueError('Frozen wave configuration changed')
        original = read(row['configuration']['path'])
        output = Path(original['output_directory'])
        submitted = output/'submission.json'
        if submitted.exists():
            receipt = read(submitted)
            manager = receipt['manager']
            if (manager['binding']['working_directory'] != str(output)
                    or manager['native_job']['Iwd'] != str(output)):
                raise ValueError('Native wave owner differs from its persistent directory')
            found[wid] = dict(cluster=manager['cluster_id'], directory=str(output))
            continue
        launch = root/'factory_v2/wave_launch'/wid
        if not native_launch_failure(launch) or recovered_this_poll >= config.get('recoveries_per_poll',1):
            continue
        if config.get('afs_quota_path'):
            quota = subprocess.run(['/usr/bin/fs','listquota','-path',config['afs_quota_path']],
                capture_output=True,text=True,check=True,timeout=30)
            fields=quota.stdout.strip().splitlines()[-1].split()
            free=(int(fields[1])-int(fields[2]))*1024
            if free < config['minimum_afs_free_bytes']:
                state['preparation_waiting_for_AFS']=dict(free_bytes=free,checked_epoch=time.time())
                continue
        recovered_this_poll += 1
        directory = Path(config['working_directory'])/'recovery'/wid
        record = state.setdefault('recoveries',{}).get(wid)
        if record and record.get('phase') == 'attention_required':
            continue
        if not record:
            if output.exists():
                raise ValueError('Partial wave ownership requires review: '+wid)
            actual = read(launch/'actual_wave_config.json')
            actual.update(maximum_running_jobs=config['worker_budget'], maximum_idle_jobs=50,
                          maximum_submits_per_interval=4,initial_running_jobs=1,initial_idle_jobs=1)
            directory.mkdir(parents=True)
            write_snapshot(directory/'config.json',actual)
            record = dict(phase='qualified_retry', original_error_sha256=sha(launch/'worker.err'),
                configuration_sha256=sha(directory/'config.json'), same_frozen_source_draws=True)
            state['recoveries'][wid]=record
            write_snapshot(config['state_path'],state)
        actual = read(directory/'config.json')
        try:
            prepared = prepare_wave(actual,record['configuration_sha256'])
            receipt = submit_wave(actual,prepared)
        except Exception as error:
            record.update(phase='attention_required',error=repr(error))
            write_snapshot(config['state_path'],state)
            continue
        record.update(phase='submitted',cluster=receipt['manager']['cluster_id'])
        write_snapshot(config['state_path'],state)
        found[wid]=dict(cluster=record['cluster'],directory=str(output))
    return found


def rebalance(config, waves):
    # Native scheduler bindings include the RPC throttle method; the older
    # frontend bindings do not. This supervisor must run in scheduler universe.
    import htcondor2
    if not hasattr(htcondor2.DAGMan,'throttle'):
        raise RuntimeError('Native DAGMan throttle API is unavailable')
    if str(htcondor2.param.get('DAGMAN_REMOVE_JOBS_AFTER_LIMIT_CHANGE','False')).lower() not in ('false','0'):
        raise RuntimeError('Refuse resource changes that could evict healthy workers')
    ads=query('Owner == "jniedzie"', ['ClusterId','ProcId','Iwd','DAGManJobId','JobStatus','JobUniverse','DAG_NodesReady'])
    byid={ad['ClusterId']:ad for ad in ads if ad.get('JobUniverse')==7}
    healthy={wid:row for wid,row in waves.items()
        if row['cluster'] in byid and byid[row['cluster']]['JobStatus']==2}
    counts=Counter(ad.get('DAGManJobId') for ad in ads
        if ad.get('JobUniverse')==5 and ad.get('JobStatus') in (1,2,5,6,7))
    occupied_other=sum(counts[row['cluster']] for wid,row in waves.items() if wid not in healthy)
    ordered={wid:counts[row['cluster']] for wid,row in healthy.items()}
    # A wave blocked on a held preflight must not retain an unused large claim.
    # DAGMan reports ready nodes independently of running/held children.
    ready={wid:int(byid[row['cluster']].get('DAG_NodesReady',1)) for wid,row in healthy.items()}
    candidates=[wid for wid in healthy if ready[wid]>0]
    capacities={}
    for wid,row in healthy.items():
        text=(Path(row['directory'])/'dagman.config').read_text()
        match=re.search(r'(?m)^DAGMAN_MAX_JOBS_SUBMITTED\s*=\s*(\d+)',text)
        if match is None:raise ValueError('Unknown configured wave capacity')
        capacities[wid]=int(match.group(1))
    # Prefer at most four large streams, adding smaller waves when needed to
    # fill the budget. New managers start with one node until this admission.
    selected=[];total=0
    for wid in candidates:
        selected.append(wid);total+=capacities[wid]
        if len(selected)>=config.get('active_waves',4) and total>=config['worker_budget']:
            break
    available=max(1,config['worker_budget']-occupied_other)
    active_counts={wid:ordered[wid] for wid in selected}
    inactive_counts={wid:ordered[wid] for wid in ordered if wid not in selected}
    protected=sum(max(1,v) for v in inactive_counts.values())
    targets={wid:1 for wid in inactive_counts}
    targets.update(limits(active_counts,max(1,available-protected),len(selected) or 1))
    replies={};errors={}
    for wid,row in healthy.items():
        target=targets[wid]
        submits=max(1,config.get('submits_per_interval_budget',4)//max(1,len(selected))) if wid in selected else 1
        try:
            answer=htcondor2.DAGMan(row['cluster']).throttle(
                max_nodes=target,max_idle=min(target,50),max_submits=submits)
            if isinstance(answer,str):
                raise RuntimeError('Native throttle refused: '+answer)
            replies[wid]=str(answer)
        except Exception as error:
            errors[wid]=repr(error)
    return dict(waves=waves, live_children=ordered, ready_nodes=ready,limits=targets,
        throttle_replies=replies,throttle_errors=errors,
        worker_budget=config['worker_budget'],nonhealthy_occupied_workers=occupied_other,
        healthy_workers_evicted=0,held_physics_workers_released=0)


def isolated_poll(configuration, config, command=None):
    """Keep the native binding out of the persistent admission supervisor.

    Only the observed CPython/native-binding abort is retried. A failed poll
    cannot redraw events: the next process reconciles the same durable intents.
    """
    argv = command or [sys.executable, str(Path(__file__).resolve()),
                       str(configuration), '--once']
    result = subprocess.run(argv, capture_output=True, text=True,
        timeout=config.get('maximum_poll_seconds',1800))
    if result.returncode == 0:
        return dict(complete=True,returncode=0)
    error = result.stderr[-8000:]
    known = result.returncode == -6 and 'none_dealloc' in error
    return dict(complete=False,returncode=result.returncode,error=error,
                reviewed_native_binding_abort=known)


def supervise(configuration, config, command=None, maximum_polls=None):
    """Restart a known crashed poll, with a separate durable supervisor lock."""
    directory = Path(config['working_directory'])
    with (directory/'admission_supervisor.lock').open('a') as lock:
        wait_for_lock(lock)
        started = time.monotonic()
        failures = 0
        polls = 0
        while time.monotonic()-started < config.get('maximum_wall_seconds',604800):
            result = isolated_poll(configuration,config,command)
            polls += 1
            if not result['complete']:
                failures += 1
                result.update(checked_epoch=time.time(),attempt=failures,
                    phase='recovering_known_native_abort' if result['reviewed_native_binding_abort']
                    else 'attention_required')
                write_snapshot(directory/('native_abort_%02d.json'%failures),result)
                if not result['reviewed_native_binding_abort'] or failures > config.get('maximum_native_abort_restarts',3):
                    write_snapshot(config['failure_path'],result)
                    raise RuntimeError('Admission poll failed; preserved durable failure: '+str(result))
            write_snapshot(directory/'admission_supervisor_heartbeat.json',dict(
                checked_epoch=time.time(),phase='running',completed_polls=polls,
                reviewed_native_abort_restarts=failures,last_poll=result))
            if maximum_polls is not None and polls >= maximum_polls:
                return
            time.sleep(config.get('recovery_poll_seconds',3) if not result['complete']
                       else config.get('poll_seconds',60))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('configuration',type=Path)
    parser.add_argument('--once',action='store_true')
    args=parser.parse_args()
    config=read(args.configuration)
    os.chdir(config['working_directory'])
    if not args.once:
        supervise(args.configuration.resolve(),config)
        return
    for path,digest in config['frozen_files'].items():
        if sha(path)!=digest:
            raise ValueError('Frozen supervisor helper changed: '+path)
    with Path(config['state_path']).with_suffix('.lock').open('a') as lock:
        wait_for_lock(lock)
        state=read(config['state_path']) if Path(config['state_path']).exists() else {}
        start=time.monotonic()
        while time.monotonic()-start<config.get('maximum_wall_seconds',604800):
            try:
                waves=discover(config,state)
                record=rebalance(config,waves)
                record.update(checked_epoch=time.time(),phase='running',recoveries=state.get('recoveries',{}))
            except (OSError,NativeCondorError,RuntimeError,ValueError) as error:
                record=dict(checked_epoch=time.time(),phase='attention_required',error=repr(error),
                            recoveries=state.get('recoveries',{}))
            write_snapshot(config['heartbeat_path'],record)
            if record['phase']=='attention_required':
                write_snapshot(config['failure_path'],record)
            if args.once:
                return
            time.sleep(config.get('poll_seconds',60))


if __name__=='__main__':
    main()
