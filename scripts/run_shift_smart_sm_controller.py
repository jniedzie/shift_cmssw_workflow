#!/usr/bin/env python3
# Code style: AI-code. AI-generated detached SM source-to-Nano controller.
"""Advance independent SM chunks after native source and detector checks.

Failed bins stop independently. Uncertain submissions are never repeated.
Published GEN denominators remain complete, including chunks with no selection.
"""
from collections import Counter
import copy
import fcntl
import hashlib
import json
from pathlib import Path
import re
import sys
import time

from shift_condor_native import NativeCondorError, query, run_condor


def read(path):
    return json.loads(Path(path).read_text())


def save(path, value):
    path = Path(path)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False)+'\n')
    temporary.replace(path)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def native_exit(path, cluster):
    if not path.exists():
        return None
    blocks = path.read_text(errors='replace').split('\n...\n')[:-1]
    for block in reversed(blocks):
        if re.match(r'005 \('+str(cluster)+r'\.000\.', block):
            match = re.search(r'Normal termination \(return value (\d+)\)', block)
            return int(match[1]) if match else -1
    return None


def observe(task, ads):
    """Credit success only after exact native closure and returned evidence."""
    if task['phase'] in ('done', 'failed', 'ready'):
        return
    if task['phase'] == 'submitting':
        raise RuntimeError('Uncertain submission needs native review: '+task['directory'])
    directory = Path(task['directory'])
    live = [ad for ad in ads if ad['ClusterId'] == task['cluster'] and ad['ProcId'] == 0]
    if live:
        if len(live) != 1 or live[0]['Iwd'] != str(directory):
            raise ValueError('Queued worker lost its persistent directory')
        task['execution_seen'] = task.get('execution_seen', False) or live[0].get('NumJobStarts', 0) > 0
        if live[0]['JobStatus'] == 5:
            task.update(phase='failed', error=live[0].get('HoldReason', 'Held worker'))
        return
    terminal = native_exit(directory/'events.log', task['cluster'])
    if terminal is None:
        task.setdefault('missing_since', time.time())
        if time.time()-task['missing_since'] > 600:
            task.update(phase='failed', error='Absent worker without native terminal event')
        return
    status, result = directory/'status.json', directory/'result.json'
    if not status.exists() or not result.exists():
        task.update(phase='failed', error='Native exit without returned stage receipts')
        return
    receipt, evidence = read(status), read(result)
    if (terminal != 0 or receipt.get('complete') is not True or receipt.get('exit_code') != 0
            or evidence.get('request_sha256') != task['request_sha256']
            or evidence.get('complete') is not True):
        task.update(phase='failed', error=evidence.get('error', 'Native/receipt completion mismatch'))
        return
    task.update(phase='done', result_sha256=sha(result), native_exit_code=terminal,
                finished_epoch=time.time(),
                published_bytes=evidence.get('published_storage_bytes',
                    evidence.get('publication_bytes_before_receipt', task['bound_bytes'])+65536))


def make_task(root, config, name, request, kind, stratum):
    directory = root/'jobs'/name
    directory.mkdir(parents=True, exist_ok=False)
    save(directory/'request.json', request)
    checksum = sha(directory/'request.json')
    text = config['worker_template'].replace('@DIRECTORY@', str(directory))
    text = text.replace('@REQUEST_SHA@', checksum)
    if kind == 'quota':
        text = text.replace('+MaxRuntime = 45000', '+MaxRuntime = 900')
    elif kind == 'inventory':
        text = text.replace('+MaxRuntime = 45000', '+MaxRuntime = 1800')
    (directory/'worker.sub').write_text(text)
    return dict(directory=str(directory), phase='ready', request_sha256=checksum,
                kind=kind, stratum=stratum, bound_bytes=request.get('reservation_bytes', 16777216))


def detector_tasks(root, config, state, source_task):
    result = read(Path(source_task['directory'])/'result.json')
    source = result.get('selected_source')
    if source is None:
        return
    count = len(source['selected_indices'])
    stride = 1 if source_task['stratum'].endswith('20toinf') else 5
    for start in range(0, count, stride):
        job = state['next_detector_job']
        state['next_detector_job'] += 1
        request = dict(schema='shift-stream-payload-v1', environment='cmssw',
            stage='smart_sm_detector', cohort_id=config['campaign'],
            argv=['python3', '@TOOLS@/scripts/run_shift_smart_sm_detector.py', 'request.json'],
            application_timeout_seconds=45000, result_path='report.json',
            return_files=[], status_fields={}, detector_source=source,
            selected_skip=start, selected_count=min(stride, count-start), detector_job=job,
            reservation_bytes=16777216)
        name='detector_'+str(job).zfill(7)
        state['tasks'][name] = make_task(root, config, name, request, 'detector', source_task['stratum'])


def source_task(root, config, state, stratum, index):
    request = copy.deepcopy(config['source_templates'][stratum])
    row = request['source_request']
    row.update(id=stratum+'_source'+str(index).zfill(4),
               run=row['run']+index, seed=row['seed']+index*50,
               events_or_trials=config['production'][stratum]['chunk_events_or_trials'])
    request['publication_directory'] = config['source_publication_base']+'/'+row['id']
    request['source_publication_bound_bytes'] = 67108864
    request['reservation_bytes'] = 134217728
    name='source_'+stratum+'_'+str(index).zfill(4)
    state['tasks'][name] = make_task(root, config, name, request, 'source', stratum)


def step(root, config, state):
    # One account query; no full status-tree scan for each completed event.
    ads = query('Owner == "jniedzie"', ['ClusterId', 'ProcId', 'JobStatus', 'JobUniverse',
        'Iwd', 'NumJobStarts', 'HoldReason'], timeout=45)
    for task in list(state['tasks'].values()):
        observe(task, ads)
        if task['phase'] == 'done' and task['kind'] == 'source' and not task.get('children_created'):
            detector_tasks(root, config, state, task)
            task['children_created'] = True
    failures = {t['stratum'] for t in state['tasks'].values()
                if t['phase'] == 'failed' and t['kind'] in ('source','detector')}
    families = {}
    for family, path in config['combined_canaries'].items():
        directory = Path(path)
        status = directory/'status.json'
        families[family] = bool(status.exists() and read(status).get('complete') is True
            and native_exit(directory/'events.log', config['combined_clusters'][family]) == 0)
    fresh_detector_families = {family:any(t['kind']=='detector' and t['phase']=='done'
        and t['stratum'].startswith(family+'_') for t in state['tasks'].values())
        for family in families}
    quota_tasks = [t for t in state['tasks'].values() if t['kind'] == 'quota']
    done_quotas = [t for t in quota_tasks if t['phase'] == 'done']
    quota = read(Path(done_quotas[-1]['directory'])/'result.json') if done_quotas else {}
    quota_fresh = time.time()-quota.get('checked_epoch', 0) < 1800
    quota_retry_ready = not quota_tasks or time.time()-quota_tasks[-1].get('submitted_epoch', 0)>600
    if (not quota_fresh and quota_retry_ready
            and not any(t['phase'] in ('ready','submitted') for t in quota_tasks)):
        name = 'quota_'+str(len(quota_tasks)).zfill(5)
        request = dict(schema='shift-stream-payload-v1', environment='cmssw', stage='smart_sm_quota',
            cohort_id=config['campaign'], argv=['python3', '@TOOLS@/scripts/run_shift_smart_sm_detector.py',
            'request.json'], application_timeout_seconds=300, result_path='report.json',
            return_files=[], status_fields={}, reservation_bytes=0)
        state['tasks'][name] = make_task(root, config, name, request, 'quota', '')
    finished_sources = [t for t in state['tasks'].values() if t['kind']=='source' and t['phase']=='done']
    finished_nanos = [t for t in state['tasks'].values() if t['kind']=='detector' and t['phase']=='done']
    inventory_change = len(finished_sources)+len(finished_nanos)
    all_sources_returned = len(finished_sources) == sum(a['chunks'] for a in config['production'].values())
    all_science_complete = all_sources_returned and all(t['phase']=='done'
        for t in state['tasks'].values() if t['kind'] in ('source','detector'))
    inventory_due = (inventory_change-state.get('last_inventory_count', 0) >= 250
                     or (finished_nanos and not state.get('last_inventory_count'))
                     or (all_science_complete and inventory_change != state.get('last_inventory_count', 0)))
    if inventory_due and not any(t['kind']=='inventory' and t['phase'] in ('ready','submitted')
                                 for t in state['tasks'].values()):
        sources = [read(Path(t['directory'])/'result.json')['source'] for t in finished_sources]
        if len({s['gen_sha256'] for s in sources}) != len(sources):
            raise ValueError('Duplicate GEN exposure in production inventory')
        nanos = []
        for task in finished_nanos:
            result = read(Path(task['directory'])/'result.json')
            nanos.append(dict(stratum=task['stratum'], path=result['nano_path'],
                sha256=result['nano_sha256'], events=result['events'],
                source_descriptor_sha256=result['source_descriptor_sha256']))
        inventory = dict(schema='shift-smart-SM-published-inventory-v1', campaign=config['campaign'],
            sources=sources, nano_outputs=nanos, full_GEN_exposure_counted_once=True,
            includes_zero_admission_sources=True, physics_normalization_ready=False,
            histograms_complete=False, complete=all_science_complete and not failures,
            checked_epoch=time.time())
        save(root/'source_inventory.json', inventory)
        name='inventory_'+str(inventory_change).zfill(7)
        request=dict(schema='shift-stream-payload-v1', environment='cmssw',
            stage='smart_sm_inventory', cohort_id=config['campaign'],
            argv=['python3','@TOOLS@/scripts/run_shift_smart_sm_detector.py','request.json'],
            application_timeout_seconds=900, result_path='report.json', return_files=[], status_fields={},
            inventory=inventory, reservation_bytes=16777216,
            publication_directory=config['source_publication_base']+'/inventories/'+name)
        state['tasks'][name] = make_task(root, config, name, request, 'inventory', '')
        state['last_inventory_count'] = inventory_change
    for stratum, allocation in config['production'].items():
        own = [t for t in state['tasks'].values() if t['stratum'] == stratum and t['kind']=='source']
        if stratum in failures or not any(t['phase']=='done' for t in own):
            continue
        if (families[stratum.split('_')[0]] and len(own) < allocation['chunks']
                and not any(t['phase']=='ready' for t in own)):
            source_task(root, config, state, stratum, len(own))
    workers = [ad for ad in ads if ad.get('JobUniverse')==5 and ad['JobStatus'] in (1,2,6,7)]
    running = sum(ad['JobStatus']==2 for ad in workers)
    idle = sum(ad['JobStatus']==1 for ad in workers)
    admitted = 0
    for name, task in sorted(state['tasks'].items(),
            key=lambda pair: (dict(quota=0,inventory=1,detector=2,source=3)[pair[1]['kind']], pair[0])):
        if task['phase'] != 'ready' or task['stratum'] in failures:
            continue
        if task['kind'] != 'quota':
            if not quota_fresh:
                continue
            if task['kind'] != 'inventory' and not families.get(task['stratum'].split('_')[0], False):
                continue
            if task['kind']=='source' and not fresh_detector_families[task['stratum'].split('_')[0]]:
                continue
            in_flight = sum(t['bound_bytes'] for t in state['tasks'].values() if t['phase']=='submitted')
            spent = sum(t.get('published_bytes', 0) for t in state['tasks'].values() if t['phase']=='done')
            if spent+in_flight+task['bound_bytes'] > config['own_reservation_bytes']:
                continue
            required = config['external_future_reservation_bytes']+config['safety_floor_bytes']+in_flight+task['bound_bytes']
            if quota['free_bytes'] < required or quota['free_files'] < 30000:
                continue
        if (len(workers)+admitted >= config['account_worker_ceiling']
                or idle+admitted >= config['maximum_idle_workers']
                or admitted >= config['maximum_starts_per_poll']):
            break
        task.update(phase='submitting', intent_epoch=time.time())
        save(root/'ledger.json', state)
        output = run_condor(['/usr/bin/condor_submit', '-terse',
            str(Path(task['directory'])/'worker.sub')], cwd=task['directory'], timeout=45)
        match = re.fullmatch(r'(\d+)\.0 - \d+\.0\s*', output)
        if not match:
            raise RuntimeError('Unknown native submit result: '+output)
        task.update(phase='submitted', cluster=int(match[1]), submitted_epoch=time.time())
        save(root/'ledger.json', state)
        admitted += 1
    save(root/'ledger.json', state)
    record = dict(checked_epoch=time.time(), phase='running',
        task_counts=dict(Counter(t['phase'] for t in state['tasks'].values())),
        completed_by_stage=dict(Counter(t['kind'] for t in state['tasks'].values() if t['phase']=='done')),
        failed_bins=sorted(failures), combined_family_checks=families, quota_fresh=quota_fresh,
        fresh_GEN_to_Nano_checks=fresh_detector_families,
        account_running_workers=running, submitted_this_poll=admitted)
    sources_finished = all(sum(t['kind']=='source' and t['stratum']==stratum and t['phase']=='done'
        for t in state['tasks'].values()) == allocation['chunks']
        for stratum, allocation in config['production'].items())
    if sources_finished and all(t['phase']=='done' for t in state['tasks'].values()):
        record.update(phase='ntuples_complete', histograms_complete=False,
                      grouped_ntuples_complete=False)
        save(root/'completion.json', record)
    if failures:
        save(root/'attention_required.json', record)
    save(root/'heartbeat.json', record)
    return record


def watch(root):
    record = read(root/'heartbeat.json') if (root/'heartbeat.json').exists() else {}
    if record.get('phase') == 'ntuples_complete':
        save(root/'watchdog_heartbeat.json', dict(checked_epoch=time.time(), terminal=True))
        return True
    ads = query('Iwd == '+json.dumps(str(root))+' && ShiftSmartSMController =?= True',
                ['ClusterId','JobStatus','NumJobStarts'], timeout=45)
    problems = []
    if not ads or any(ad['JobStatus']==5 for ad in ads):
        problems.append('Controller absent or held')
    if time.time()-record.get('checked_epoch', 0) > 300:
        problems.append('Controller heartbeat stale')
    if (root/'controller_failure.json').exists():
        problems.append('Controller failure receipt needs review')
    if record.get('phase') == 'scheduler_unknown':
        problems.append('Scheduler state unknown; admission paused')
    value = dict(checked_epoch=time.time(), problems=problems, controller_ads=ads)
    save(root/'watchdog_heartbeat.json', value)
    if problems:
        save(root/'watchdog_attention_required.json', value)
    return False


def main():
    root = Path(sys.argv[1]).resolve()
    if str(root).startswith(('/tmp/', '/pool/')):
        raise ValueError('Persistent campaign directory required')
    config = read(root/'config.json')
    if sha(root/'config.json') != sys.argv[2]:
        raise ValueError('Frozen controller configuration changed')
    for name, expected in config.get('deployment_files', {}).items():
        if sha(root/name) != expected:
            raise ValueError('Frozen deployment changed: '+name)
    if '--watchdog' in sys.argv:
        while True:
            try:
                if watch(root):
                    return
            except NativeCondorError as error:
                save(root/'watchdog_scheduler_unknown.json', dict(epoch=time.time(), error=repr(error)))
            if '--once' in sys.argv:
                return
            time.sleep(60)
    with (root/'controller.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = read(root/'ledger.json')
        while True:
            try:
                record = step(root, config, state)
            except NativeCondorError as error:
                if any(t['phase']=='submitting' for t in state['tasks'].values()):
                    save(root/'controller_failure.json', dict(epoch=time.time(), error=repr(error),
                        action='Uncertain submission requires native review; never repeat it'))
                    raise
                record = dict(checked_epoch=time.time(), phase='scheduler_unknown', error=repr(error))
                save(root/'heartbeat.json', record)
                save(root/'scheduler_unknown.json', record)
            except BaseException as error:
                save(root/'controller_failure.json', dict(epoch=time.time(), error=repr(error)))
                raise
            if '--once' in sys.argv:
                break
            if record['phase'] == 'ntuples_complete':
                break
            time.sleep(30)


if __name__ == '__main__':
    main()
