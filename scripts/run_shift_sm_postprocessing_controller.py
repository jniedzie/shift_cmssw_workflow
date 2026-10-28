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

from run_shift_sm_postprocessing import read, save, exposure, selected_rows, digest
from shift_condor_native import query, run_condor, NativeCondorError


def descriptor_hashes(source):
    variants = [source, dict(source, gen_path_resolved=True)]
    return [hashlib.sha256((json.dumps(s, indent=2) + ending).encode()).hexdigest()
            for s in variants for ending in ('', '\n')]


def compact_source(source):
    selected = selected_rows(source)
    if source.get('decay_proposal') is not None:
        # The optional authority covers rejected originals as well as selected rows.
        keys = ('event_ids', 'weights', 'selected_indices', 'sampling_probabilities', 'gen_sha256',
                'normalization_record', 'sampling', 'production_selection', 'decay_proposal')
        return json.loads(json.dumps({key: source[key] for key in keys}, allow_nan=False))
    return dict(event_ids=[r['event_id'] for r in selected],
                weights=[r['native_weight'] for r in selected],
                selected_indices=list(range(len(selected))),
                sampling_probabilities=[r['sampling_probability'] for r in selected])


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
        expected = len(source['selected']['event_ids'])
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
    identities = [tuple(e) for s in sources for e in s['selected']['event_ids']]
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


def poll(root, config, state):
    ads = query('Owner == "jniedzie"', ['ClusterId', 'ProcId', 'JobStatus', 'JobUniverse',
                'Iwd', 'DAGManJobId'], timeout=45)
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
    parent = read(Path(config['parent_state']) / 'ledger.json')
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
    quota = read(Path(config['parent_state']) / 'heartbeat.json')
    submitted = 0
    spent = sum(t.get('published_bytes', 0) for t in state['tasks'].values())
    reserved = sum(config['worker_publication_bound_bytes'] for t in state['tasks'].values()
                   if t['phase'] in ('submitted', 'submitting'))
    # Scheduler hosts have no EOS client. Reuse the main production's actual
    # authenticated quota worker and its pinned native-success receipt.
    observations = [t for t in parent['tasks'].values()
                    if t['kind'] == 'quota' and t['phase'] == 'done']
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
    parent_committed = sum(t.get('published_bytes', 0) for t in parent['tasks'].values() if t['phase'] == 'done')
    parent_in_flight = sum(t.get('bound_bytes', 0) for t in parent['tasks'].values()
                           if t['phase'] in ('submitted', 'submitting'))
    parent_remaining = max(0, parent_config['own_reservation_bytes'] - parent_committed)
    floor = parent_config['external_future_reservation_bytes'] + parent_config['safety_floor_bytes']
    account_workers = sum(a.get('JobUniverse') == 5 and a['JobStatus'] in (1, 2, 6, 7) for a in ads)
    for task in sorted(state['tasks'].values(), key=lambda t: t['events']):
        if task['phase'] != 'ready':
            continue
        bound = config['worker_publication_bound_bytes']
        if spent + reserved + bound > config['maximum_publication_bytes']:
            break
        if parent_committed + parent_in_flight + spent + reserved + bound > parent_config['own_reservation_bytes']:
            break
        if free - reserved - bound < floor + parent_in_flight:
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
        protected_external_and_floor_bytes=floor))
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
