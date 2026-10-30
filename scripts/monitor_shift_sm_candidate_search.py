#!/usr/bin/env python3
# Code style: AI-code.
"""Read-only aggregate supervision for the finite SM GEN candidate search.

One native queue observation covers preparation, handoffs and detector jobs.
Source watchdogs retain detailed failures; this monitor also detects missing
stage transitions. Counts describe processing, never an unmeasured physics rate.
"""
import argparse
from collections import Counter
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import time

from monitor_shift_sm_response_benchmark import write_snapshot
from shift_condor_native import NativeCondorError, query


SCHEMA = 'shift-sm-candidate-search-watchdog-v1'
MAXIMUM_WALL_SECONDS = 7*86400
ATTRIBUTES = ['ClusterId','ProcId','Owner','Iwd','JobStatus','JobUniverse',
    'JobBatchName','GlobalJobId','DAGManJobId','DAG_Status','DAG_NodesFailed','HoldReason','ExitCode','ExitBySignal']


def read(path):
    with Path(path).open('rb') as stream:
        return json.load(stream)


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            value.update(block)
    return value.hexdigest()


def persistent(value):
    path = Path(value)
    if not path.is_absolute() or str(path).startswith(('/tmp/','/pool/','/run/','/var/tmp/')):
        raise ValueError('Aggregate watchdog requires absolute persistent paths')
    return path


def within(path, directory):
    """Literal path membership avoids regex and prefix-neighbour mistakes."""
    return isinstance(path, str) and (path == str(directory) or path.startswith(str(directory).rstrip('/')+'/'))


def optional(path):
    try:
        return read(path)
    except FileNotFoundError:
        return None


def validate(config):
    if config.get('schema') != SCHEMA:
        raise ValueError('Unsupported aggregate watchdog configuration')
    for key in ('working_directory','root_directory','state_path','heartbeat_path','failure_path'):
        persistent(config[key])
    if not 30 <= config.get('poll_seconds', 60) <= 60:
        raise ValueError('Aggregate polls must be separated by 30 to 60 seconds')
    if not 60 <= config.get('transition_grace_seconds', 900) <= 7200:
        raise ValueError('Use an explicit bounded transition grace')
    wall_seconds = config.get('maximum_wall_seconds', 86400)
    if type(wall_seconds) is not int or not 60 <= wall_seconds <= MAXIMUM_WALL_SECONDS:
        raise ValueError('Use an explicit integer monitor lifetime of at most seven days')
    pin = config['plan']
    persistent(pin['path'])
    if digest(pin['path']) != pin['sha256']:
        raise ValueError('Candidate search plan changed')
    for path, expected in config.get('helper_sha256s', {}).items():
        if digest(persistent(path)) != expected:
            raise ValueError('Frozen aggregate watchdog helper changed: '+path)
    owner = config.get('owner', pwd.getpwuid(os.getuid()).pw_name)
    if not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9.-]*', owner):
        raise ValueError('Invalid native account owner')
    ids = []
    for manager in config['managers']:
        if type(manager['cluster_id']) is not int or manager['cluster_id'] <= 0:
            raise ValueError('Require exact native manager/job IDs')
        persistent(manager['directory'])
        persistent(manager['event_log'])
        ids.append(manager['cluster_id'])
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate aggregate manager identity')
    return owner


def native_terminal(manager, state):
    """Cache bounded native event-log proof; never repeatedly scan history."""
    cache = state.setdefault('native_terminals', {})
    key = str(manager['cluster_id'])
    if key in cache:
        if cache[key]['directory'] != manager['directory']:
            raise ValueError('Cached native manager directory changed')
        return cache[key]
    path = Path(manager['event_log'])
    if not path.exists():
        return None
    if path.stat().st_size > 8*1048576:
        raise ValueError('Native manager event log exceeds its bounded scan')
    text = path.read_text(errors='replace')
    pattern = r'(?m)^(005|009) \('+str(manager['cluster_id'])+r'\.000\.000\)([^\n]*)\n(.*?)(?=^\.\.\.|\Z)'
    records = list(re.finditer(pattern, text, flags=re.S))
    if not records:
        return None
    final = records[-1]
    code = re.search(r'Normal termination \(return value (-?\d+)\)', final.group(3))
    successful = final.group(1) == '005' and code is not None and int(code.group(1)) == 0
    result = dict(cluster_id=manager['cluster_id'], directory=manager['directory'],
        event_log=str(path), event_log_sha256=digest(path), successful=successful,
        removed=final.group(1) == '009', exit_code=int(code.group(1)) if code else None)
    cache[key] = result
    return result


def elapsed(state, key, now):
    return now-state.setdefault('transition_started', {}).setdefault(key, now)


def complete_result(value):
    return value is not None and value.get('complete') is True and value.get('exit_code') == 0


def source_owner(source_id, output, prepared, submission, config, ads):
    """Bind either a private DAG or an explicitly declared shared wave."""
    manager = submission['manager']
    cluster = manager['cluster_id']
    directory = persistent(manager['binding']['working_directory'])
    if type(cluster) is not int or cluster <= 0:
        raise ValueError('Source manager identity is invalid')
    native_job = manager['native_job']
    if native_job.get('ClusterId') != cluster or native_job.get('Iwd') != str(directory):
        raise ValueError('Saved native source manager binding differs')
    node = prepared['collector']['node']
    if submission.get('shared_wave') is True:
        if (submission.get('source_id') != source_id
                or submission.get('shared_wave_directory') != str(directory)
                or not within(str(directory), config['root_directory'])):
            raise ValueError('Shared wave source/directory binding differs')
        source_pin, wave_pin = submission['source_prepared_manifest'], submission['wave_prepared_manifest']
        if (source_pin['path'] != str(output/'prepared.json')
                or digest(source_pin['path']) != source_pin['sha256']
                or Path(wave_pin['path']).parent != directory
                or digest(wave_pin['path']) != wave_pin['sha256']):
            raise ValueError('Shared wave frozen source/manifest changed')
        wave = read(wave_pin['path'])
        members = [row for row in wave['sources'] if row['source_id'] == source_id]
        if (len(members) != 1 or members[0]['prepared_manifest'] != source_pin
                or members[0]['source_job_nodes'] != submission['source_job_nodes']
                or members[0]['collector'] != prepared['collector']):
            raise ValueError('Source is not an exact member of its shared wave')
        node = submission['source_job_nodes'][node]
    elif directory != output:
        raise ValueError('A private source manager cannot own another directory')
    live = [ad for ad in ads if ad.get('ClusterId') == cluster and ad.get('ProcId') == 0]
    if len(live) > 1:
        raise ValueError('Ambiguous source manager queue record')
    if live:
        if (live[0].get('Iwd') != str(directory)
                or (manager.get('native_job', {}).get('GlobalJobId')
                    and live[0].get('GlobalJobId') != manager['native_job']['GlobalJobId'])):
            raise ValueError('Actual source manager identity/directory differs')
    for ad in ads:
        if within(ad.get('Iwd'), output/'jobs') and ad.get('JobUniverse') == 5:
            if ad.get('DAGManJobId') != cluster:
                raise ValueError('Source payload is owned by another native DAG manager')
    return dict(cluster_id=cluster, directory=str(directory), collector_node=node,
        manager_output=str(directory/'response.dag.dagman.out'))


def native_collector_closure(source_id, output, prepared, submission, collector, config, state, ads, owner=None):
    """Prove collection without one scheduler-universe watcher per source."""
    owner = owner or source_owner(source_id, output, prepared, submission, config, ads)
    directory = output/'jobs/collect'
    record = prepared['collector']
    if (record['job_directory'] != str(directory)
            or digest(directory/'request.json') != record['request_sha256']
            or collector.get('request_sha256') != record['request_sha256']):
        raise ValueError('Collector request binding differs from its frozen source')
    status = optional(directory/'status.json')
    if not complete_result(status):
        return None
    active = [ad for ad in ads if within(ad.get('Iwd'), output/'jobs')
              and ad.get('JobUniverse') == 5 and ad.get('JobStatus') in (1,2,3,5,6,7)]
    if active:
        return None
    path = directory/'events.log'
    if not path.exists():
        return None
    if path.stat().st_size > 8*1048576:
        raise ValueError('Collector event log exceeds bounded native review')
    ids = set(re.findall(r'(?m)^000 \((\d+)\.000\.000\)', path.read_text(errors='replace')))
    if len(ids) != 1:
        raise ValueError('Collector event log has no unique native job identity')
    cluster = int(ids.pop())
    terminal = native_terminal(dict(cluster_id=cluster, directory=str(directory), event_log=str(path)), state)
    if terminal is None:
        return None
    if not terminal['successful']:
        raise ValueError('Collector native job did not terminate successfully')
    # DAGMan's own event reports bind the terminal worker to its exact node.
    # This remains available after the worker leaves the queue, without a
    # history scan or waiting for unrelated sources in the shared wave.
    native = Path(owner['manager_output'])
    if not native.exists():
        return None
    if native.stat().st_size > 32*1048576:
        raise ValueError('Source manager output exceeds bounded ownership review')
    pattern = (r'ULOG_(?:SUBMIT|EXECUTE|TERMINATED) for HTCondor Node '
               + re.escape(owner['collector_node'])+r' \('+str(cluster)+r'\.0\.0\)')
    if not re.search(pattern, native.read_text(errors='replace')):
        raise ValueError('Native collector is not bound to its source DAG node')
    return dict(owner=owner, collector_terminal=terminal,
        collector_result_sha256=digest(directory/'result.json'),
        collector_status_sha256=digest(directory/'status.json'))


def source_status(source, config, state, ads, now):
    sid = source['source_id']
    preparation = Path(source['preparation_directory'])
    output = Path(source['reconstruction_directory'])
    handoff_directory = source.get('handoff_directory',
        str(Path(config['root_directory'])/'handoff'/sid))
    local = [ad for ad in ads if within(ad.get('Iwd'), preparation) or within(ad.get('Iwd'), output)
        or within(ad.get('Iwd'), handoff_directory)]
    failures = []
    result = optional(preparation/'result.json')
    prepared_ok = complete_result(result)
    if result is not None and result.get('exit_code') not in (None, 0):
        failures.append('GEN preparation failed')
    if source.get('preparation_request_sha256') and prepared_ok:
        if result.get('request_sha256') != source['preparation_request_sha256']:
            failures.append('GEN preparation request binding differs')
            prepared_ok = False
    prepared = optional(output/'prepared.json')
    submission = optional(output/'submission.json')
    collector = optional(output/'jobs/collect/result.json')
    individual_watchdogs = config.get('individual_source_watchdogs', True)
    heartbeat = optional(output/'watchdog/heartbeat.json') if individual_watchdogs else None
    handoff_live = any(within(ad.get('Iwd'), handoff_directory)
        and ad.get('JobStatus') in (1,2,6,7) for ad in local)
    grace = config.get('transition_grace_seconds',900)
    if prepared_ok and submission is None and not handoff_live:
        if elapsed(state, 'handoff:'+sid, now) > grace:
            failures.append('Successful GEN preparation has no downstream handoff after grace')
    if prepared is not None and submission is None and not handoff_live:
        if elapsed(state, 'submission:'+sid, now) > grace:
            failures.append('Frozen response draw has no submitted detector DAG after grace')
    if collector is not None and collector.get('exit_code') not in (None,0):
        failures.append('Final response collector failed')
    if heartbeat is not None:
        failures.extend('Source watchdog: '+str(problem) for problem in heartbeat.get('problems', []))
    owner = None
    if submission is not None and prepared is not None and not individual_watchdogs:
        try:
            owner = source_owner(sid, output, prepared, submission, config, ads)
        except (KeyError,TypeError,ValueError) as error:
            failures.append('Source ownership binding failed: '+str(error))
    if submission is not None and not complete_result(collector) and individual_watchdogs:
        if heartbeat is None:
            if elapsed(state, 'watchdog:'+sid, now) > grace:
                failures.append('Submitted detector DAG has no watchdog heartbeat after grace')
        elif now-heartbeat.get('checked_epoch',0) > grace:
            failures.append('Source watchdog heartbeat is stale')
    collected = False
    if complete_result(collector):
        if prepared is None or collector.get('benchmark_plan_sha256') != prepared['benchmark_plan']['sha256']:
            failures.append('Final collector lacks its matching frozen response draw')
        elif (collector.get('complete_frozen_draw_collected') is not True
                or collector.get('events') != len(prepared['detector_jobs'])):
            failures.append('Final collector does not cover the complete frozen draw')
        elif not individual_watchdogs:
            if submission is None:
                failures.append('Complete collector lacks native source ownership')
            elif owner is not None:
                try:
                    closure = native_collector_closure(sid, output, prepared, submission, collector,
                        config, state, ads, owner)
                except (KeyError,TypeError,ValueError) as error:
                    failures.append('Native collector closure failed: '+str(error))
                    closure = None
                if closure is not None:
                    collected = True
                    state.setdefault('source_collector_closures', {})[sid] = closure
                elif elapsed(state, 'closure:'+sid, now) > grace:
                    failures.append('Complete collector lacks successful native closure after grace')
        elif heartbeat is not None and heartbeat.get('terminal') is True and not heartbeat.get('problems'):
            terminal = heartbeat.get('manager_terminal', {})
            if (terminal.get('ExitCode') == 0 and terminal.get('ExitBySignal') is False
                    and terminal.get('DAG_NodesFailed',0) == 0 and terminal.get('DAG_Status',0) == 0):
                collected = True
            else:
                failures.append('Source manager terminal record is unsuccessful')
        elif elapsed(state, 'closure:'+sid, now) > grace:
            failures.append('Complete collector lacks source manager/watchdog terminal closure')
    if not prepared_ok and not local:
        if elapsed(state, 'preparation_absent:'+sid, now) > grace:
            # Ready work can legitimately wait in the parent DAG. Parent-manager
            # liveness and its child counts are checked separately below.
            waiting = True
        else:
            waiting = False
    else:
        waiting = False
    return dict(source_id=sid, preparation_complete=prepared_ok,
        handoff_submitted=submission is not None, collector_complete=collected,
        detector_jobs=len(prepared['detector_jobs']) if prepared else None,
        collected_events=collector['events'] if collected else None,
        active_jobs=len(local), waiting_for_preparation=waiting,
        failures=failures)


def observe(config, state, now=None, query_jobs=query):
    now = time.time() if now is None else now
    owner = config.get('owner', pwd.getpwuid(os.getuid()).pw_name)
    root = Path(config['root_directory'])
    all_ads = query_jobs('Owner == '+json.dumps(owner), ATTRIBUTES, timeout=60)
    ads = [ad for ad in all_ads if within(ad.get('Iwd'), root)]
    problems = []
    for ad in ads:
        if ad.get('Owner') != owner:
            raise ValueError('Native queue returned another account within this search')
        if ad.get('JobStatus') == 5:
            problems.append('Held job '+str(ad['ClusterId'])+'.'+str(ad.get('ProcId',0))+': '+str(ad.get('HoldReason','unknown')))
        if ad.get('JobStatus') == 3:
            problems.append('Removed job '+str(ad['ClusterId']))
        if ad.get('DAG_NodesFailed',0) or ad.get('DAG_Status',0):
            problems.append('Manager '+str(ad['ClusterId'])+' reports failed DAG nodes/status')
    managers = []
    unfinished_preparation = any(within(ad.get('Iwd'), root/'preparation'/'sources')
        and ad.get('JobStatus') in (1,2,6,7) for ad in ads)
    for manager in config['managers']:
        live = [ad for ad in ads if ad.get('ClusterId') == manager['cluster_id'] and ad.get('ProcId') == 0]
        terminal = None
        if len(live) > 1:
            raise ValueError('Duplicate native manager records')
        if live:
            if live[0].get('Iwd') != manager['directory']:
                raise ValueError('Native manager persistent Iwd differs')
            children = [ad for ad in ads if ad.get('DAGManJobId') == manager['cluster_id']]
            if not children and not unfinished_preparation:
                if elapsed(state, 'idle_manager:'+str(manager['cluster_id']), now) > config.get('transition_grace_seconds',900):
                    problems.append('Idle manager '+manager['name']+' has no child jobs after grace')
            else:
                state.setdefault('transition_started',{}).pop('idle_manager:'+str(manager['cluster_id']),None)
        else:
            terminal = native_terminal(manager,state)
            if terminal is None:
                if elapsed(state, 'missing_manager:'+str(manager['cluster_id']), now) > config.get('transition_grace_seconds',900):
                    problems.append('Manager '+manager['name']+' absent without native terminal evidence')
            elif not terminal['successful']:
                problems.append('Manager '+manager['name']+' terminated unsuccessfully')
        managers.append(dict(name=manager['name'], live=bool(live), terminal=terminal))
    plan = read(config['plan']['path'])
    sources = [dict(source_id=row['source_id'],preparation_directory=row['directory'],
        preparation_request_sha256=row['request_sha256'],
        reconstruction_directory=str(root/'reconstruction'/row['source_id']),
        handoff_directory=str(root/'handoff'/row['source_id'])) for row in plan['sources']]
    sources.extend(config.get('extra_sources',[]))
    if len({row['source_id'] for row in sources}) != len(sources):
        raise ValueError('Duplicate expected source in aggregate supervision')
    rows = []
    for source in sources:
        cached = state.setdefault('collected_sources',{}).get(source['source_id'])
        row = cached or source_status(source,config,state,ads,now)
        rows.append(row)
        prep_closed = any(manager['directory'] == str(root/'preparation')
            and observed['terminal'] is not None for manager,observed in zip(config['managers'],managers))
        if not row['preparation_complete'] and prep_closed:
            if elapsed(state,'missing_preparation_receipt:'+source['source_id'],now) > config.get('transition_grace_seconds',900):
                problems.append(source['source_id']+': Closed preparation manager lacks a successful source receipt')
        problems.extend(source['source_id']+': '+failure for failure in row['failures'])
        if row['collector_complete']:
            state['collected_sources'][source['source_id']] = row
    science = [ad for ad in ads if ad.get('JobUniverse') == 5
        and (within(ad.get('Iwd'),root/'preparation'/'sources')
             or (within(ad.get('Iwd'),root/'reconstruction') and '/jobs/' in ad.get('Iwd','')))]
    complete = (len(rows)>0 and all(row['collector_complete'] for row in rows)
        and not science and not problems and all(m['terminal'] and m['terminal']['successful'] for m in managers))
    counts = dict(expected_sources=len(rows), prepared_sources=sum(r['preparation_complete'] for r in rows),
        submitted_sources=sum(r['handoff_submitted'] for r in rows),
        collected_sources=sum(r['collector_complete'] for r in rows),
        planned_detector_jobs=sum(r['detector_jobs'] or 0 for r in rows),
        collected_events=sum(r['collected_events'] or 0 for r in rows),
        queue_jobs=len(ads), queued_science_workers=len(science),
        queue_status_counts=dict(Counter(ad.get('JobStatus') for ad in ads)))
    return dict(schema=SCHEMA,checked_epoch=now,terminal=complete,
        phase='complete' if complete else ('attention_required' if problems else 'running'),
        problems=problems,counts=counts,managers=managers,sources=rows,
        physics_rates_measured=False,job_mutations=False)


def run_once(config,state,now=None,observer=observe,writer=write_snapshot):
    now = time.time() if now is None else now
    started = state.setdefault('started_epoch',now)
    if now-started >= config.get('maximum_wall_seconds',86400):
        result = dict(schema=SCHEMA,checked_epoch=now,phase='watchdog_runtime_limit',terminal=False,
            problems=['Aggregate watchdog lifetime reached before verified completion'])
    else:
        try:
            result = observer(config,state,now=now)
        except NativeCondorError as error:
            result = dict(schema=SCHEMA,checked_epoch=now,phase='scheduler_unknown',terminal=False,problems=[str(error)])
        except (OSError,ValueError,KeyError,TypeError) as error:
            result = dict(schema=SCHEMA,checked_epoch=now,phase='monitor_error',terminal=False,problems=[repr(error)])
    state['last_observation'] = result
    if result['problems']:
        state.setdefault('first_failure',result)
    writer(config['state_path'],state)
    writer(config['heartbeat_path'],result)
    if result['problems']:
        writer(config['failure_path'],dict(first_failure=state['first_failure'],latest=result))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('configuration',type=Path)
    parser.add_argument('--config-sha256',required=True)
    parser.add_argument('--once',action='store_true')
    args = parser.parse_args()
    persistent(args.configuration)
    if digest(args.configuration) != args.config_sha256:
        raise ValueError('Aggregate watchdog configuration changed')
    config = read(args.configuration)
    validate(config)
    os.chdir(config['working_directory'])
    with Path(config['state_path']).with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        state = optional(config['state_path']) or {}
        if state.get('configuration_sha256',args.config_sha256) != args.config_sha256:
            raise ValueError('Aggregate watchdog state belongs to another configuration')
        state['configuration_sha256'] = args.config_sha256
        while True:
            result = run_once(config,state)
            if result['phase'] == 'watchdog_runtime_limit':
                raise SystemExit('Aggregate watchdog lifetime exceeded; incomplete state preserved')
            if args.once or result['terminal']:
                return
            time.sleep(config.get('poll_seconds',60))


if __name__ == '__main__':
    main()
