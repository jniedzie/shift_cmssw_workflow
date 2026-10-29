#!/usr/bin/env python3
# Code style: AI-code. AI-generated detached rolling SM publication controller.
"""Follow verified SM Nano completions and publish meaningful per-bin updates."""
from collections import defaultdict
import argparse
import fcntl
import errno
import hashlib
import json
from pathlib import Path
import re
import sys
import time

from run_shift_sm_postprocessing import (read, save, exposure, selected_rows, digest,
                                        read_response_pin, response_sampling_contract)
from shift_condor_native import query, run_condor, NativeCondorError


def descriptor_hashes(source):
    variants = [source, dict(source, gen_path_resolved=True)]
    return [hashlib.sha256((json.dumps(s, indent=2) + ending).encode()).hexdigest()
            for s in variants for ending in ('', '\n')]


def compact_source(source):
    selected = selected_rows(source)
    if source.get('response_sampling') is not None:
        keys = ('event_ids','weights','selected_indices','sampling_probabilities',
                'gen_sha256','normalization_record','response_sampling')
        return json.loads(json.dumps({key:source[key] for key in keys},allow_nan=False))
    if source.get('decay_proposal') is not None:
        # The optional authority covers rejected originals as well as selected rows.
        keys = ('event_ids', 'weights', 'selected_indices', 'sampling_probabilities', 'gen_sha256',
                'normalization_record', 'sampling', 'production_selection', 'decay_proposal')
        return json.loads(json.dumps({key: source[key] for key in keys}, allow_nan=False))
    return dict(event_ids=[r['event_id'] for r in selected],
                weights=[r['native_weight'] for r in selected],
                selected_indices=list(range(len(selected))),
                sampling_probabilities=[r['sampling_probability'] for r in selected])


def response_closed_source(prepared_manifest, collector_result):
    """Adapt one native-qualified response collector to ordinary snapshot input.

    The supervisor supplies pinned terminal receipts only after its native job
    checks. Pending or partial sources never acquire exposure by this route.
    Existing production source and discovery paths remain unchanged.
    """
    prepared = read_response_pin(prepared_manifest)
    if prepared.get('schema') != 'shift-sm-candidate-handoff-v1':
        raise ValueError('Response discovery requires the candidate source authority')
    collector = prepared['collector']
    request_pin = dict(path=str(Path(collector['job_directory'])/'request.json'),
                       sha256=collector['request_sha256'])
    request = read_response_pin(request_pin)
    if (Path(collector_result['path']) != Path(collector['job_directory'])/'result.json'
            or prepared['benchmark_plan'] != request['benchmark_plan']):
        raise ValueError('Response collector is outside its prepared source authority')
    plan = read_response_pin(request['benchmark_plan'])
    full = read_response_pin(request['full_population_exposure'])['sources']
    if len(full) != 1 or full[0]['source_id'] != prepared['source_id']:
        raise ValueError('Response prepared source has ambiguous full GEN exposure')
    normalization=full[0]['original_full_exposure']; selected=plan['selected']
    descriptor=dict(event_ids=[row['event_id'] for row in selected],
        weights=[row['native_weight'] for row in selected],selected_indices=list(range(len(selected))),
        sampling_probabilities=[row['existing_sampling_probability'] for row in selected],
        gen_sha256=full[0]['prepared_gen_sha256'],normalization_record=normalization,
        response_sampling=dict(collector_request=request_pin,collector_result=collector_result))
    _,_,nanos=response_sampling_contract(descriptor)
    return dict(normalization=normalization,selected=descriptor,nano_inputs=nanos,
        descriptor_hashes=sorted({row['source_descriptor_sha256'] for row in nanos}),
        original_result_path=collector_result['path'],original_result_sha256=collector_result['sha256'])



def response_source_cache(config, state, ads):
    """Discover complete sources from an explicitly pinned response monitor.

    One fresh account queue observation is shared with publication admission.
    Cached sources keep their exact native closure and immutable receipt pins;
    pending, held or replaying source payloads never add publication exposure.
    The existing worker independently checks every selected producer again.
    """
    from monitor_shift_sm_candidate_search import (source_owner,
        native_collector_closure, complete_result, within)
    monitor = read_response_pin(config['response_monitor_config'])
    if (monitor.get('schema') != 'shift-sm-candidate-search-watchdog-v1'
            or monitor.get('individual_source_watchdogs', True)):
        raise ValueError('Response publication requires aggregate native collector authority')
    plan = read_response_pin(monitor['plan'])
    root = Path(monitor['root_directory'])
    expected = [dict(source_id=row['source_id'],
        reconstruction_directory=str(root/'reconstruction'/row['source_id']))
        for row in plan['sources']]
    expected.extend(monitor.get('extra_sources', []))
    ids = [row['source_id'] for row in expected]
    if len(ids) != len(set(ids)):
        raise ValueError('Response publication source authority repeats an original source')
    cache = state.setdefault('response_sources', {})
    if not set(cache) <= set(ids):
        raise ValueError('A cached response source disappeared from the frozen worklist')
    groups = defaultdict(list)
    for row in expected:
        sid = row['source_id']; output = Path(row['reconstruction_directory'])
        if (not output.is_absolute() or str(output).startswith(('/tmp/', '/pool/'))
                or not within(str(output), root/'reconstruction')):
            raise ValueError('Response source is outside its persistent reconstruction authority')
        # Check the fresh queue even after native proof has been cached. A new
        # replay under the same source must be reviewed rather than counted twice.
        active = [ad for ad in ads if within(ad.get('Iwd'), output/'jobs')
            and ad.get('JobUniverse') == 5 and ad.get('JobStatus') in (1,2,3,5,6,7)]
        if active:
            if sid in cache:
                raise ValueError('A native-qualified published source has an active replay: '+sid)
            continue
        cached = cache.get(sid)
        if cached is not None:
            for pin in cached['pins']:
                if digest(pin['path']) != pin['sha256']:
                    raise ValueError('Cached response native closure changed: '+pin['path'])
            prepared = read(output/'prepared.json'); submission = read(output/'submission.json')
            owner = source_owner(sid, output, prepared, submission, monitor, ads)
            if owner != cached['native_closure']['owner']:
                raise ValueError('Cached response source native owner changed')
            groups[cached['source']['normalization']['stratum']].append(cached['source'])
            continue
        paths = [output/'prepared.json', output/'submission.json', output/'jobs/collect/result.json']
        if not all(path.exists() for path in paths):
            continue
        prepared, submission, collector = [read(path) for path in paths]
        if not complete_result(collector):
            if collector.get('exit_code') not in (None, 0):
                raise ValueError('Response collector failed: '+sid)
            continue
        if (collector.get('benchmark_plan_sha256') != prepared['benchmark_plan']['sha256']
                or collector.get('complete_frozen_draw_collected') is not True
                or collector.get('events') != len(prepared['detector_jobs'])):
            raise ValueError('Response collector does not close its entire frozen source: '+sid)
        closure = native_collector_closure(sid, output, prepared, submission, collector,
            monitor, state.setdefault('response_native', {}), ads)
        if closure is None:
            continue
        pins = [dict(path=str(path), sha256=digest(path)) for path in paths]
        pins.extend(dict(path=str(output/'jobs/collect'/name),
            sha256=digest(output/'jobs/collect'/name)) for name in ('request.json','status.json','events.log'))
        source = response_closed_source(pins[0], pins[2])
        if source['normalization']['source_id'] != sid:
            raise ValueError('Response source normalization differs from its frozen worklist')
        cache[sid] = dict(pins=pins, native_closure=closure, source=source, closed_epoch=time.time())
        groups[source['normalization']['stratum']].append(source)
    return groups


def closed_sources(sources, nanos):
    """Never publish a convenient subset of one source's frozen selection."""
    by_hash = defaultdict(list)
    for row in nanos:
        by_hash[row['source_descriptor_sha256']].append(row)
    closed = defaultdict(list)
    for source in sources:
        rows = [r for h in source['descriptor_hashes'] for r in by_hash.get(h, [])]
        paths = [r['path'] for r in rows]
        if len(paths) != len(set(paths)):
            raise ValueError('Duplicate Nano in closed-source census')
        count = sum(r['events'] for r in rows)
        selection = source['selected']
        expected = len(selection['selected_indices'] if selection.get('decay_proposal') is not None
                       else selection['event_ids'])
        if count > expected:
            raise ValueError('Nano source over-coverage')
        if count == expected:
            closed[source['normalization']['stratum']].append(dict(source, nano_inputs=rows))
    return closed


def meaningful_update(previous, current):
    old = set(previous)
    new = set(current)
    if not old <= new:
        raise ValueError('A previously published original source disappeared')
    return bool(new - old) and (not old or len(new - old) >= 10 or len(new) >= 1.2 * len(old))


def source_cache(parent, state):
    for name, task in parent['tasks'].items():
        if task['kind'] != 'source' or task['phase'] != 'done':
            continue
        key = task['result_sha256']
        if key in state['sources']:
            continue
        path = Path(task['directory']) / 'result.json'
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != key:
            raise ValueError('Parent source receipt changed')
        result = json.loads(raw)
        source = result.get('selected_source')
        if source is None:
            if result.get('empty_source') is not True or result.get('selected_events') != 0:
                raise ValueError('Completed source lacks its frozen selection: ' + name)
            # A genuinely empty native GEN cohort still owns its original
            # trial exposure. It has no detector descriptor or child jobs.
            state['sources'][key] = dict(normalization=result['source'],
                selected=dict(event_ids=[], weights=[], selected_indices=[], sampling_probabilities=[]),
                descriptor_hashes=[], original_result_sha256=key, original_result_path=str(path))
            continue
        state['sources'][key] = dict(normalization=result['source'],
            selected=compact_source(source), descriptor_hashes=descriptor_hashes(source),
            original_result_sha256=key, original_result_path=str(path))


def nano_cache(parent, inventory, state):
    published = {Path(r['path']).name: r for r in inventory['nano_outputs']}
    for name, task in parent['tasks'].items():
        if task['kind'] != 'detector' or task['phase'] != 'done':
            continue
        key = task['result_sha256']
        if key in state['nanos']:
            continue
        suffix = name.rsplit('_', 1)[-1]
        row = published.get('nano_job' + suffix.zfill(7) + '.root') if suffix.isdigit() else None
        if row is None or row['stratum'] != task['stratum']:
            status = read(Path(task['directory']) / 'status.json')
            if not status['complete'] or status['exit_code'] != 0:
                raise ValueError('Completed parent Nano lacks successful status')
            row = dict(path=status['nano_path'], sha256=status['nano_sha256'],
                       events=status['events'], stratum=task['stratum'],
                       source_descriptor_sha256=status['source_descriptor_sha256'])
        state['nanos'][key] = dict(row, original_result_sha256=key)


def snapshot(root, config, stratum, sources):
    sources = sorted(sources, key=lambda s: s['normalization']['source_id'])
    normalizations = [s['normalization'] for s in sources]
    exposure(normalizations, stratum)
    identities = [tuple(row['event_id']) for source in sources for row in selected_rows(source['selected'])]
    if len(identities) != len(set(identities)):
        raise ValueError('Repeated selected original between source cohorts')
    inputs = [r for s in sources for r in s['nano_inputs']]
    if not inputs:
        return None
    body = dict(schema='shift-smart-SM-postprocessing-v1', campaign=config['campaign'],
                stratum=stratum, complete_source_selections_only=True,
                normalization_sources=normalizations,
                selected_sources=[s['selected'] for s in sources], nano_inputs=inputs,
                histogram_runtime=config['histogram_runtime'],
                maximum_publication_bytes=config['worker_publication_bound_bytes'])
    checksum = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    directory = root / 'jobs' / (stratum + '_' + checksum[:12])
    timestamp = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())
    proc, bounds = stratum.split('_')
    sample = proc + ('_mHat' if proc == 'dy' else '_pThat') + bounds.replace('.', 'p') + 'GeV'
    body.update(sample_directory=config['output_base'] + '/' + sample + '/' + config['campaign'],
                snapshot_name='ntuple_ready_' + timestamp + '_' + checksum[:12])
    directory.mkdir(parents=True, exist_ok=False)
    save(directory / 'request.json', body)
    text = config['worker_template'].replace('@DIRECTORY@', str(directory))
    text = text.replace('@REQUEST_SHA@', digest(directory / 'request.json'))
    (directory / 'worker.sub').write_text(text)
    return dict(directory=str(directory), phase='ready', stratum=stratum,
                request_sha256=digest(directory / 'request.json'),
                source_ids=[s['source_id'] for s in normalizations], events=len(identities))




def science_write_reservation(parent_config, state, quota_epoch):
    """Keep full allocation; credit future writes only after whole-source closure.

    A quota observation newer than our exact native collection proof already
    includes that cohort's completed GEN/Nano/collector publications. Partial
    cohorts, missing bounds and closures newer than quota keep their full claim.
    """
    total = parent_config.get('reserved_science_bytes', 0)
    bounds = parent_config.get('science_source_write_bounds', {})
    if (type(total) is not int or total < 0 or not isinstance(bounds, dict)
            or any(not isinstance(sid,str) or type(value) is not int or value < 0
                   for sid,value in bounds.items()) or sum(bounds.values()) > total):
        raise ValueError('Invalid full science allocation or source write bounds')
    closed = state.get('response_sources', {})
    credit = sum(value for sid,value in bounds.items() if sid in closed
        and closed[sid].get('closed_epoch', float('inf')) <= quota_epoch)
    return total, total-credit


def refresh_quota(root, config, ads):
    """Own a quota-only ledger for a new response publisher, never an old campaign.

    This uses the normal smart-SM quota payload and native observer. It cannot
    admit physics jobs or share a writable ledger with another controller.
    """
    from run_shift_smart_sm_controller import make_task, observe, save as save_ledger
    directory = Path(config['parent_state'])
    if directory != root/'quota':
        raise ValueError('Publisher quota authority must be its own private quota directory')
    directory.mkdir(exist_ok=True)
    parent_config = read(config['parent_config'])
    path = directory/'ledger.json'
    parent = read(path) if path.exists() else dict(tasks={})
    tasks = list(parent['tasks'].values())
    if any(task['kind'] != 'quota' for task in tasks):
        raise ValueError('Publisher cannot refresh another production ledger')
    for task in tasks:
        observe(task, ads)
    save_ledger(path, parent)
    if any(task['phase'] == 'failed' for task in tasks):
        raise ValueError('Native quota worker failed; review before retry')
    completed = [task for task in tasks if task['phase'] == 'done']
    latest = max(completed, key=lambda task:task['finished_epoch']) if completed else None
    quota = read(Path(latest['directory'])/'result.json') if latest else {}
    if (time.time()-quota.get('checked_epoch', 0) >= 900
            and not any(task['phase'] in ('ready','submitted','submitting') for task in tasks)):
        name = 'quota_'+str(len(tasks)).zfill(5)
        request = dict(schema='shift-stream-payload-v1', environment='cmssw', stage='smart_sm_quota',
            cohort_id=config['campaign'], argv=['python3','@TOOLS@/scripts/run_shift_smart_sm_detector.py',
            'request.json'], application_timeout_seconds=300, result_path='report.json',
            return_files=[], status_fields={}, reservation_bytes=0)
        parent['tasks'][name] = make_task(directory, parent_config, name, request, 'quota', '')
        save_ledger(path, parent)
    for task in parent['tasks'].values():
        if task['phase'] != 'ready':
            continue
        task.update(phase='submitting', intent_epoch=time.time())
        save_ledger(path, parent)
        answer = run_condor(['/usr/bin/condor_submit','-terse',
            str(Path(task['directory'])/'worker.sub')],cwd=task['directory'],timeout=45)
        match = re.fullmatch(r'(\d+)\.0 - \d+\.0\s*',answer)
        if not match:
            raise ValueError('Uncertain native quota submission requires review')
        task.update(phase='submitted', cluster=int(match[1]),submitted_epoch=time.time())
        save_ledger(path, parent)
    save_ledger(directory/'heartbeat.json',dict(checked_epoch=time.time(),
        phase='running',quota_only=True))


def poll(root, config, state):
    ads = query('Owner == "jniedzie"', ['ClusterId', 'ProcId', 'JobStatus', 'JobUniverse',
                'Iwd', 'DAGManJobId', 'Owner', 'GlobalJobId', 'NumJobStarts', 'HoldReason'], timeout=45)
    live = {a['ClusterId']: a for a in ads}
    for name, task in state['tasks'].items():
        if task['phase'] not in ('submitted', 'submitting'):
            continue
        if task['phase'] == 'submitting':
            raise ValueError('Uncertain submission requires review: ' + name)
        if task['cluster'] in live:
            if live[task['cluster']]['JobStatus'] == 5:
                task.update(phase='failed', error='Native worker is held; no automatic retry')
            continue
        result = Path(task['directory']) / 'result.json'
        if not result.exists():
            raise ValueError('Absent worker lacks returned result: ' + name)
        terminal = json.loads(run_condor(['/usr/bin/condor_history', str(task['cluster']),
            '-limit', '1', '-json', '-attributes', 'ClusterId,ProcId,Iwd,ExitCode,ExitBySignal'], timeout=45))
        if (len(terminal) != 1 or terminal[0].get('ClusterId') != task['cluster']
                or terminal[0].get('ProcId') != 0 or terminal[0].get('Iwd') != task['directory']
                or terminal[0].get('ExitCode') != 0 or terminal[0].get('ExitBySignal')):
            task.update(phase='failed', error='Native terminal failure retained', native=terminal)
            continue
        receipt = read(result)
        if (not receipt['complete'] or receipt['request_sha256'] != task['request_sha256']
                or receipt['events'] != task['events']):
            raise ValueError('Returned worker receipt differs from frozen request')
        task.update(phase='done', native=terminal[0], result_sha256=digest(result),
                    published_bytes=sum(r['bytes'] for r in receipt['published'].values())
                    + result.stat().st_size + 65536, finished_epoch=time.time())
        state['latest'][task['stratum']] = task['source_ids']
    if config.get('refresh_quota') is True:
        refresh_quota(root, config, ads)
    parent = read(Path(config['parent_state']) / 'ledger.json')
    if config.get('response_monitor_config') is not None:
        groups = response_source_cache(config, state, ads)
    else:
        inventory = read(Path(config['parent_state']) / 'source_inventory.json')
        source_cache(parent, state)
        nano_cache(parent, inventory, state)
        groups = closed_sources(state['sources'].values(), state['nanos'].values())
    busy = {t['stratum'] for t in state['tasks'].values() if t['phase'] in ('ready', 'submitted', 'submitting')}
    failed = {t['stratum'] for t in state['tasks'].values() if t['phase'] == 'failed'}
    # At most one immutable snapshot per bin at a time; meaningful changes only.
    for stratum, sources in groups.items():
        if stratum in busy or stratum in failed:
            continue
        ids = [s['normalization']['source_id'] for s in sources]
        previous = state['latest'].get(stratum, [])
        last_finished = max((t.get('finished_epoch', 0) for t in state['tasks'].values()
                             if t['stratum'] == stratum and t['phase'] == 'done'), default=0)
        hourly_update = set(ids) > set(previous) and time.time() - last_finished > 3600
        if not meaningful_update(previous, ids) and not hourly_update:
            continue
        task = snapshot(root, config, stratum, sources)
        if task:
            state['tasks'][Path(task['directory']).name] = task
            save(root / 'state.json', state)
    # This is an extension of the registered SM production, not another GEN or
    # detector production. Keep its extra writes bounded and protect both the
    # parent's full allocation and all of the parent's external reservations.
    submitted = 0
    spent = sum(t.get('published_bytes', 0) for t in state['tasks'].values())
    reserved = sum(config['worker_publication_bound_bytes'] for t in state['tasks'].values()
                   if t['phase'] in ('submitted', 'submitting'))
    # Scheduler hosts have no EOS client. Reuse the main production's actual
    # authenticated quota worker and its pinned native-success receipt.
    observations = [t for t in parent['tasks'].values()
                    if t['kind'] == 'quota' and t['phase'] == 'done']
    if not observations:
        save(root / 'state.json', state)
        save(root / 'heartbeat.json', dict(checked_epoch=time.time(), phase='waiting_for_quota',
            reason='Waiting for the first authenticated native quota worker; no publication admission'))
        return
    observation = max(observations, key=lambda t: t['finished_epoch'])
    result = Path(observation['directory']) / 'result.json'
    if digest(result) != observation['result_sha256'] or observation.get('native_exit_code') != 0:
        raise ValueError('Parent quota authority changed or lacks native success')
    quota = read(result)
    if not quota.get('complete') or time.time() - quota['checked_epoch'] > 1800:
        save(root / 'state.json', state)
        save(root / 'heartbeat.json', dict(checked_epoch=time.time(), phase='waiting_for_quota',
             reason='Waiting for the parent authenticated quota refresh; no new admission'))
        return
    parent_since = sum(t.get('published_bytes', 0) for t in parent['tasks'].values()
                       if t['phase'] == 'done' and t.get('finished_epoch', 0) > quota['checked_epoch'])
    own_since = sum(t.get('published_bytes', 0) for t in state['tasks'].values()
                    if t.get('finished_epoch', 0) > quota['checked_epoch'])
    free = quota['free_bytes'] - parent_since - own_since
    parent_config = read(config['parent_config'])
    if config.get('refresh_quota') is True and 'reserved_science_bytes' not in parent_config:
        raise ValueError('Private quota publication requires an explicit full science write reservation')
    science_reserve, science_future = science_write_reservation(parent_config,state,quota['checked_epoch'])
    parent_committed = sum(t.get('published_bytes', 0) for t in parent['tasks'].values() if t['phase'] == 'done')
    parent_in_flight = sum(t.get('bound_bytes', 0) for t in parent['tasks'].values()
                           if t['phase'] in ('submitted', 'submitting'))
    # The quota-only ledger does not own physics task bookkeeping. Preserve
    # the entire registered allocation, while fresh quota can release future
    # writes for exact, wholly completed source cohorts.
    parent_future = parent_in_flight + science_future
    parent_remaining = max(0, parent_config['own_reservation_bytes'] - parent_committed)
    floor = parent_config['external_future_reservation_bytes'] + parent_config['safety_floor_bytes']
    account_workers = sum(a.get('JobUniverse') == 5 and a['JobStatus'] in (1, 2, 6, 7) for a in ads)
    for task in sorted(state['tasks'].values(), key=lambda t: t['events']):
        if task['phase'] != 'ready':
            continue
        bound = config['worker_publication_bound_bytes']
        if spent + reserved + bound > config['maximum_publication_bytes']:
            break
        if parent_committed + parent_in_flight + science_reserve + spent + reserved + bound > parent_config['own_reservation_bytes']:
            break
        if free - reserved - bound < floor + parent_future:
            break
        if account_workers + submitted >= parent_config['account_worker_ceiling']:
            break
        # A small canary uses the exact real publication route; independent
        # bins are released together once that result has passed.
        if not state['latest'] and any(t['phase'] == 'submitted' for t in state['tasks'].values()):
            break
        task['phase'] = 'submitting'
        save(root / 'state.json', state)
        answer = run_condor(['/usr/bin/condor_submit', '-terse',
                            str(Path(task['directory']) / 'worker.sub')], cwd=task['directory'], timeout=45)
        match = re.fullmatch(r'(\d+)\.0 - \d+\.0\s*', answer)
        if not match:
            raise ValueError('Uncertain native submit result')
        task.update(phase='submitted', cluster=int(match[1]), submitted_epoch=time.time())
        save(root / 'state.json', state)
        reserved += bound
        submitted += 1
    waiting = any(t['phase'] == 'ready' for t in state['tasks'].values()) and not any(
        t['phase'] in ('submitted', 'submitting') for t in state['tasks'].values())
    save(root / 'heartbeat.json', dict(checked_epoch=time.time(), submitted_this_poll=submitted,
        phase='waiting_for_storage' if waiting else 'running',
        publication_budget_remaining_bytes=config['maximum_publication_bytes'] - spent - reserved,
        ready_sources={s: len(v) for s, v in groups.items()},
        tasks={phase: sum(t['phase'] == phase for t in state['tasks'].values())
               for phase in ('ready', 'submitted', 'done', 'failed')},
        EOS_free_bytes=free, protected_parent_remaining_bytes=parent_remaining,
        protected_external_and_floor_bytes=floor,
        protected_science_allocation_bytes=science_reserve,protected_science_future_bytes=science_future))
    save(root / 'state.json', state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--watchdog', action='store_true')
    parser.add_argument('--config-sha256')
    args = parser.parse_args()
    deployment = args.directory.resolve()
    config = read(deployment / 'config.json')
    if args.config_sha256 and digest(deployment / 'config.json') != args.config_sha256:
        raise ValueError('Frozen controller configuration changed')
    root = Path(config.get('state_directory', str(deployment)))
    if str(root).startswith(('/tmp/', '/pool/')):
        raise ValueError('A detached controller needs persistent storage')
    if args.watchdog:
        while True:
            try:
                ads = query('Iwd == ' + json.dumps(str(deployment)) +
                    ' && ShiftSMPostprocessingController =?= True',
                    ['ClusterId', 'JobStatus', 'Iwd'], timeout=45)
                heartbeat = read(root / 'heartbeat.json')
                problems = []
                if not ads or any(a['JobStatus'] == 5 for a in ads):
                    problems.append('Postprocessing controller missing or held')
                if time.time() - heartbeat.get('checked_epoch', 0) > 600:
                    problems.append('Postprocessing controller heartbeat stale')
                if heartbeat.get('tasks', {}).get('failed'):
                    problems.append('Failed histogram workers require review')
                record = dict(checked_epoch=time.time(), problems=problems, controller_ads=ads)
                save(root / 'watchdog_heartbeat.json', record)
                if problems:
                    save(root / 'watchdog_attention_required.json', record)
            except (OSError, NativeCondorError) as error:
                save(root / 'watchdog_query_failure.json', dict(checked_epoch=time.time(), error=repr(error)))
            if args.once:
                return
            time.sleep(60)
    with (root / 'controller.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for name, checksum in config['frozen_files'].items():
            if digest(deployment / name) != checksum:
                raise ValueError('Frozen postprocessing deployment changed: ' + name)
        state = read(root / 'state.json') if (root / 'state.json').exists() else dict(
            sources={}, nanos={}, tasks={}, latest={})
        while True:
            try:
                poll(root, config, state)
            except (OSError, NativeCondorError) as error:
                if isinstance(error, OSError) and error.errno not in (errno.EIO, errno.ESTALE, errno.ETIMEDOUT):
                    raise
                if any(t['phase'] == 'submitting' for t in state['tasks'].values()):
                    save(root / 'attention_required.json', dict(error=repr(error),
                        action='Uncertain submit; review native queue before recovery', checked_epoch=time.time()))
                    raise
                save(root / 'state.json', state)
                save(root / 'transient_failure.json', dict(error=repr(error), checked_epoch=time.time()))
                time.sleep(30)
                continue
            except Exception as error:
                save(root / 'attention_required.json', dict(error=repr(error), checked_epoch=time.time()))
                raise
            if args.once:
                break
            time.sleep(180)


if __name__ == '__main__':
    main()
