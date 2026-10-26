#!/usr/bin/env python3
# Code style: AI-code. AI-generated detached SM source-to-Nano controller.
"""Advance independent SM chunks after native source and detector checks.

Failed sources stop their bin. Failed detector cohorts remain incomplete while
independent work continues. Uncertain submissions are never repeated.
Published GEN denominators remain complete, including chunks with no selection.
"""
from collections import Counter
import copy
import errno
import fcntl
import hashlib
import heapq
import json
from pathlib import Path
import re
import sys
import time

from shift_condor_native import NativeCondorError, query, run_condor

TEMPORARY_STORAGE_ERRORS={errno.EIO,errno.ESTALE,errno.ETIMEDOUT,errno.ENOSPC,errno.EDQUOT}


def read(path):
    # Large single AFS reads can return EIO while the rolling ledger grows.
    for attempt in range(3):
        try:
            with Path(path).open('rb') as stream:
                return json.loads(b''.join(iter(lambda:stream.read(1048576),b'')))
        except OSError as error:
            if error.errno not in (errno.EIO,errno.ESTALE) or attempt==2:raise
            time.sleep(1)


def save(path, value):
    path = Path(path)
    temporary = path.with_suffix('.tmp')
    data = (json.dumps(value, indent=2, allow_nan=False)+'\n').encode()
    while True:
        try:
            with temporary.open('wb') as stream:
                for start in range(0, len(data), 1048576):
                    stream.write(data[start:start+1048576])
            temporary.replace(path)
            return
        except OSError as error:
            if error.errno not in (errno.EIO, errno.ESTALE, errno.ETIMEDOUT):
                raise
            # Retry these exact bytes before any further scheduler action.
            # Quota, permission and semantic failures remain visible.
            time.sleep(10)


def sha(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1048576),b''):digest.update(block)
    return digest.hexdigest()


def native_exit(path, cluster):
    if not path.exists():
        return None
    blocks = path.read_text(errors='replace').split('\n...\n')[:-1]
    for block in reversed(blocks):
        if re.match(r'005 \('+str(cluster)+r'\.000\.', block):
            match = re.search(r'Normal termination \(return value (\d+)\)', block)
            return int(match[1]) if match else -1
    return None


def native_exit_hold(path, cluster, ad):
    """OnExitHold records event 012, without the successful-job 005 event."""
    if native_exit(path,cluster)==1:
        return True
    if (ad.get('JobStatus')!=5 or ad.get('ExitCode')!=1
            or ad.get('ExitBySignal') is not False or ad.get('HoldReasonCode')!=3):
        return False
    blocks=Path(path).read_text(errors='replace').split('\n...\n')[:-1]
    return any(re.match(r'012 \('+str(cluster)+r'\.000\.',block)
               and 'Code 3 Subcode 0' in block for block in blocks)


def publication_charge(evidence, reserved):
    """Keep an upper storage bound without charging empty GEN the whole reserve."""
    if 'published_storage_bytes' in evidence:
        amount = evidence['published_storage_bytes']
    elif 'publication_bytes_before_receipt' in evidence:
        amount = evidence['publication_bytes_before_receipt']+65536
    elif (evidence.get('schema') == 'shift-smart-SM-source-v1'
            and evidence.get('empty_source') is True):
        if (evidence.get('selected_events') != 0
                or evidence.get('source', {}).get('saved_events') != 0):
            raise ValueError('Empty source storage claim contradicts its event census')
        files = evidence['raw_publications']
        if (not files or len({row['name'] for row in files}) != len(files)
                or not all(type(row.get('bytes')) is int and row['bytes'] >= 0
                           and row.get('independent_sha256_readback') is True for row in files)):
            raise ValueError('Empty source lacks verified native publication sizes')
        audit = next(row for row in files if row['name'] == 'validation.json')
        # The original native receipt embeds that audit and the publication
        # table. Bound the extra indentation/JSON wrapper conservatively;
        # keep 64KiB overhead as well. No receipt is removed or exposure lost.
        receipt_bound = 65536+16*audit['bytes']+4*len(json.dumps(files,indent=2).encode())
        amount = min(reserved+65536, sum(row['bytes'] for row in files)+receipt_bound)
    else:
        amount = reserved+65536
    if type(amount) is not int or amount < 0:
        raise ValueError('Invalid publication storage charge')
    return amount


def observe(task, ads):
    """Credit success only after exact native closure and returned evidence."""
    if task['phase'] in ('done', 'failed', 'ready','replaced'):
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
                published_bytes=publication_charge(evidence, task['bound_bytes']))
    if task.get('raw_storage_parent_sha256'):
        if evidence['source']['gen_sha256']!=task['raw_storage_parent_sha256']:
            raise ValueError('Prepared supply changed its raw storage owner')
        task['published_bytes']-=sum(row['bytes'] for row in evidence['raw_publications'])
        if task['published_bytes']<0:raise ValueError('Invalid reused raw storage charge')
    if 'source' in evidence:
        task.update(raw_gen_sha256=evidence['source']['gen_sha256'],
                    saved_GEN_events=evidence['source']['saved_events'])
    if evidence.get('generated_only'):
        task.update(raw_receipt_url=evidence['raw_source_receipt_url'],
                    raw_receipt_sha256=evidence['raw_source_receipt_sha256'])


def make_task(root, config, name, request, kind, stratum):
    # AFS limits the size of one directory independently of volume quota.
    # Existing ledger paths stay fixed; only newly created tasks use shards.
    shard = hashlib.sha256(name.encode()).hexdigest()[:2]
    directory = root/'jobs_sharded'/shard/name
    payload=json.dumps(request,indent=2,allow_nan=False)+'\n'
    checksum=hashlib.sha256(payload.encode()).hexdigest()
    text = config['worker_template'].replace('@DIRECTORY@', str(directory))
    text = text.replace('@REQUEST_SHA@', checksum)
    if kind == 'quota':
        text=text.replace('queue 1','priority = 1000\nqueue 1')
        text = text.replace('+MaxRuntime = 45000', '+MaxRuntime = 900')
        text = text.replace('request_memory = 6000', 'request_memory = 1000')
        text = text.replace('request_disk = 12000000', 'request_disk = 1000000')
    elif kind == 'inventory':
        text = text.replace('+MaxRuntime = 45000', '+MaxRuntime = 1800')
    if request.get('same_seed_CPU_recovery'):
        text=text.replace('+MaxRuntime = 45000','+MaxRuntime = 86400')
    directory.parent.mkdir(parents=True,exist_ok=True)
    intent=directory.parent/('.'+name+'.preparing.json')
    expected=dict(request_sha256=checksum,submit_sha256=hashlib.sha256(text.encode()).hexdigest())
    if intent.exists() and read(intent)!=expected:
        raise ValueError('Interrupted task preparation changed its request: '+name)
    if directory.exists():
        # Every actual submission first persists the full task with phase
        # "submitting". Recovery reloads that checkpoint and refuses ambiguous
        # submissions before reaching here. Also require fresh queue absence.
        if query('Iwd == '+json.dumps(str(directory)),['ClusterId','Iwd'],timeout=45):
            raise ValueError('Interrupted task already has a queued worker: '+name)
        allowed={'request.json','request.tmp','worker.sub','worker.tmp'}
        files=list(directory.iterdir())
        if any(p.name not in allowed or p.is_symlink() or not p.is_file() for p in files):
            raise ValueError('Interrupted task has execution evidence: '+name)
        complete=((directory/'request.json').exists() and (directory/'worker.sub').exists()
                  and (directory/'request.json').read_text()==payload
                  and (directory/'worker.sub').read_text()==text)
        if not complete and not intent.exists():
            raise ValueError('Partial task lacks its original preparation intent: '+name)
        for path,wanted in [(directory/'request.json',payload),(directory/'worker.sub',text)]:
            if path.exists() and path.read_text()!=wanted:
                raise ValueError('Interrupted task content changed: '+name)
    save(intent,expected)
    directory.mkdir(exist_ok=True)
    save(directory/'request.json',request)
    temporary=directory/'worker.tmp'
    temporary.write_text(text)
    temporary.replace(directory/'worker.sub')
    intent.unlink()
    return dict(directory=str(directory), phase='ready', request_sha256=checksum,
                kind=kind, stratum=stratum, bound_bytes=request.get('reservation_bytes', 16777216))


def detector_tasks(root, config, state, source_task):
    result = read(Path(source_task['directory'])/'result.json')
    source = result.get('selected_source')
    if source is None:
        return
    count = len(source['selected_indices'])
    stride = (config.get('supply_detector_group_size',20) if source_task.get('source_group')=='supply'
              else (1 if source_task['stratum'].endswith('20toinf') else 5))
    for start in range(0, count, stride):
        job = state['next_detector_job']
        state['next_detector_job'] += 1
        request = dict(schema='shift-stream-payload-v1', environment='cmssw',
            stage='smart_sm_detector', cohort_id=config['campaign'],
            argv=['python3', '@TOOLS@/scripts/run_shift_smart_sm_detector.py', 'request.json'],
            application_timeout_seconds=45000, result_path='report.json',
            return_files=[], failure_return_files=['evidence.tar.gz'], status_fields={}, detector_source=source,
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


def advance_gen_supply(root,config,state,failures):
    """Build a raw GEN reserve; prepare its oldest cohorts as reco slots clear."""
    supply=config.get('gen_supply')
    if not supply:return
    tasks=state['tasks']
    ready_detectors=sum(t['kind']=='detector' and t['phase']=='ready' for t in tasks.values())
    minimums_done=all(sum(t['kind']=='raw_source' and t['stratum']==st and t['phase']=='done'
        for t in tasks.values())>=a['minimum_chunks'] for st,a in supply['bins'].items())
    goal_missing=gen_supply_progress(config,tasks)['QCD_saved_events']<supply['target_QCD_saved_events']
    for stratum,allocation in supply['bins'].items():
        if stratum in failures:continue
        raw=[t for t in tasks.values() if t['kind']=='raw_source' and t['stratum']==stratum]
        limit=(allocation['maximum_chunks'] if minimums_done and
               (goal_missing or time.time()<supply['continue_until_epoch'])
               else allocation['minimum_chunks'])
        if len(raw)<limit and not any(t['phase']=='ready' for t in raw):
            index=len(raw);request=copy.deepcopy(config['source_templates'][stratum]);row=request['source_request']
            row.update(id='supply_'+stratum+'_'+str(index).zfill(5),
                run=allocation['run_base']+index,seed=allocation['seed_base']+50*index,
                events_or_trials=supply['chunk_events_or_trials'])
            request.update(generate_only=True,publication_directory=supply['publication_base']+'/'+row['id'],
                source_publication_bound_bytes=67108864,reservation_bytes=134217728)
            name='raw_'+stratum+'_'+str(index).zfill(5)
            tasks[name]=make_task(root,config,name,request,'raw_source',stratum)
        # A prefix independent of event contents. Never choose only successful
        # or nonempty physics cohorts and silently drop zero-event exposure.
        prepared=[t for t in tasks.values() if t['kind']=='source' and t.get('source_group')=='supply'
                  and t['stratum']==stratum]
        index=len(prepared)
        if (ready_detectors>=config.get('supply_preparation_backlog_limit',500)
                or sum(t['phase'] in ('ready','submitted') for t in prepared)>=2 or index>=len(raw)):
            continue
        original=raw[index]
        if original['phase']!='done':continue
        directory=Path(original['directory']);result=read(directory/'result.json')
        request=read(directory/'request.json');request.pop('generate_only')
        request.update(resume_raw=dict(url=result['raw_source_receipt_url'],sha256=result['raw_source_receipt_sha256']),
            publication_directory=config['source_publication_base']+'/supply_prepared/'+request['source_request']['id'],
            source_publication_bound_bytes=268435456,reservation_bytes=536870912)
        name='prepared_'+stratum+'_'+str(index).zfill(5)
        tasks[name]=make_task(root,config,name,request,'source',stratum)
        tasks[name]['source_group']='supply'
        tasks[name]['raw_storage_parent_sha256']=result['source']['gen_sha256']


def gen_supply_progress(config,tasks):
    """Count saved native events once; raw cohorts are not detector exposure."""
    counts=Counter();seen=set()
    for task in tasks.values():
        checksum=task.get('raw_gen_sha256')
        if task['phase']=='done' and checksum and checksum not in seen:
            seen.add(checksum);counts[task['stratum']]+=task['saved_GEN_events']
    supply=config.get('gen_supply');finished=True
    if supply:
        for stratum,allocation in supply['bins'].items():
            raw=[t for t in tasks.values() if t['kind']=='raw_source' and t['stratum']==stratum]
            minimum=allocation['minimum_chunks']
            if time.time()<supply['continue_until_epoch']:minimum=allocation['maximum_chunks']
            finished=finished and sum(t['phase']=='done' for t in raw)>=minimum
        finished=finished and sum(n for s,n in counts.items() if s.startswith('qcd_'))>=supply['target_QCD_saved_events']
    return dict(saved_events_by_bin=dict(counts),QCD_saved_events=sum(n for s,n in counts.items() if s.startswith('qcd_')),
                target_QCD_saved_events=supply['target_QCD_saved_events'] if supply else None,
                raw_supply_finished=bool(finished),raw_supply_is_detector_exposure=False)


def closed_tasks(tasks):
    def closed(name,ancestors):
        if name in ancestors or name not in tasks:return False
        task=tasks[name]
        if task['phase']=='done':return True
        children=task.get('replacement_tasks',[])
        return task['phase']=='replaced' and bool(children) and all(
            closed(child,ancestors|{name}) for child in children)
    return all(closed(name,set()) for name in tasks)


def retry_stalled_sim(root,config,state,ads):
    """Reviewed SIM timeout: replay the same event order and transport seed once."""
    if not config.get('reviewed_SIM_timeout_retry'):return
    for name,task in list(state['tasks'].items()):
        if task['kind']!='detector' or task['phase']!='failed' or task.get('retry_level',0):continue
        directory=Path(task['directory']);result_path=directory/'result.json'
        if not result_path.exists():continue
        result=read(result_path)
        if not str(result.get('error','')).startswith("TimeoutError('SIM made no event progress for "):continue
        live=[a for a in ads if a['ClusterId']==task['cluster'] and a.get('ProcId')==0]
        if (len(live)!=1 or live[0]['JobStatus']!=5 or live[0].get('ExitCode')!=1
                or live[0].get('RemoteUserCpu',0)<3600 or live[0]['Iwd']!=str(directory)
                or not native_exit_hold(directory/'events.log',task['cluster'],live[0])):continue
        request=read(directory/'request.json')
        descriptor=dict(request['detector_source'],gen_path_resolved=True)
        start,count=request['selected_skip'],request['selected_count']
        fingerprint=hashlib.sha256((json.dumps(descriptor,indent=2)+'\n').encode()).hexdigest()
        if (sha(directory/'request.json')!=task['request_sha256']
                or result.get('request_sha256')!=task['request_sha256']
                or result.get('source_descriptor_sha256')!=fingerprint
                or result.get('source_skip')!=request['selected_skip']
                or result.get('events')!=request['selected_count']
                or not 0<=start<start+count<=len(descriptor['selected_indices'])
                or result.get('source_indices')!=descriptor['selected_indices'][start:start+count]
                or result.get('current_tier')!='SIM' or result.get('validated_tier_events',{}).get('NANO')!=0
                or any(k in result for k in ('nano_path','nano_sha256'))):
            raise ValueError('SIM timeout recovery lost native input/no-publication proof')
        child=cpu_retry_request(request);child_name='cpu_retry_'+name
        state['tasks'][child_name]=make_task(root,config,child_name,child,'detector',task['stratum'])
        state['tasks'][child_name].update(retry_level=1,reviewed_retry_of=task['cluster'])
        children=[child_name]
        state.setdefault('reviewed_cpu_retries',{})[name]=dict(original_task=copy.deepcopy(task),
            native_failure_result_sha256=sha(result_path),replacement_tasks=children,
            all_frozen_offsets_W_probabilities_and_transport_seed_preserved=True,new_GEN_exposure=0)
        task.update(phase='replaced',replacement_tasks=children)


def cpu_retry_request(request):
    """Change CPU allowances only; job identity fixes every original stage seed."""
    child=copy.deepcopy(request)
    code="""# Code style: AI-code. AI-generated same-seed CPU-only recovery entry.
import json,sys,subprocess
from pathlib import Path
r=json.loads(Path('request.json').read_text())
Path('selected_source.json').write_text(json.dumps(dict(r['detector_source'],gen_path_resolved=True),indent=2)+'\\n')
subprocess.run([sys.executable,'@TOOLS@/scripts/run_shift_gen_to_nano.py','selected_source.json',
 '--skip',str(r['selected_skip']),'--count',str(r['selected_count']),'--job',str(r['detector_job']),
 '--templates','@TOOLS@/templates','--publish','--remove-consumed-intermediates',
 '--absolute-stage-timeouts','--stage-timeouts','43200,3600,5400,10800',
 '--first-event-stall-timeout','10800','--event-stall-timeout','10800'],check=True,timeout=85000)
"""
    child.update(argv=['python3','-c',code],same_seed_CPU_recovery=True,
        application_timeout_seconds=86400,failure_return_files=['evidence.tar.gz'])
    return child


def admission_order(tasks):
    """Prefetch two GEN chunks per bin; balance new detector slots by bin."""
    sources, raw_sources, detectors = Counter(), Counter(), Counter()
    ready = []
    for name, task in tasks.items():
        if task['phase'] == 'submitted':
            if task['kind'] == 'source':
                sources[task['stratum']] += 1
            elif task['kind']=='raw_source':raw_sources[task['stratum']]+=1
            elif task['kind'] == 'detector':
                detectors[task['stratum']] += 1
        elif task['phase'] == 'ready':
            ready.append((name, task))
    result = sorted((item for item in ready if item[1]['kind'] in ('quota','inventory')),
                    key=lambda item: (item[1]['kind']!='quota', item[0]))
    for name, task in sorted(ready):
        if task['kind']=='source' and sources[task['stratum']] < 2:
            result.append((name,task))
            sources[task['stratum']] += 1
    for name,task in sorted(ready):
        if task['kind']=='raw_source' and raw_sources[task['stratum']]<2:
            result.append((name,task));raw_sources[task['stratum']]+=1
    groups = {}
    for name, task in sorted(ready, reverse=True):
        if task['kind']=='detector':
            groups.setdefault(task['stratum'], []).append((name,task))
    heap = [(detectors[stratum],stratum) for stratum in groups]
    heapq.heapify(heap)
    while heap:
        count, stratum = heapq.heappop(heap)
        result.append(groups[stratum].pop())
        if groups[stratum]:
            heapq.heappush(heap,(count+1,stratum))
    return result


def postprocessing_charge(config):
    directory = config.get('postprocessing_directory')
    if not directory:
        return 0, 0
    policy = read(config.get('postprocessing_policy', str(Path(directory) / 'config.json')))
    ledger = read(Path(directory) / 'state.json')
    spent = sum(t.get('published_bytes', 0) for t in ledger['tasks'].values())
    reserved = sum(policy['worker_publication_bound_bytes'] for t in ledger['tasks'].values()
                   if t['phase'] in ('submitted', 'submitting'))
    return spent, reserved


def step(root, config, state):
    # One account query; no full status-tree scan for each completed event.
    ads = query('Owner == "jniedzie"', ['ClusterId', 'ProcId', 'JobStatus', 'JobUniverse',
        'Iwd', 'DAGManJobId', 'NumJobStarts', 'HoldReason','ExitCode','RemoteUserCpu','ExitBySignal','HoldReasonCode'], timeout=45)
    for task in list(state['tasks'].values()):
        observe(task, ads)
        if task['phase'] == 'done' and task['kind'] == 'source' and not task.get('children_created'):
            detector_tasks(root, config, state, task)
            task['children_created'] = True
    retry_stalled_sim(root,config,state,ads)
    failures = {t['stratum'] for t in state['tasks'].values()
                if t['phase'] == 'failed' and t['kind'] in ('source','raw_source','detector')}
    admission_failures = {t['stratum'] for t in state['tasks'].values()
                         if t['phase']=='failed' and t['kind'] in ('source','raw_source')}
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
    quota_refresh_due = time.time()-quota.get('checked_epoch', 0) >= 900
    quota_retry_ready = not quota_tasks or time.time()-quota_tasks[-1].get('submitted_epoch', 0)>600
    if (quota_refresh_due and quota_retry_ready
            and not any(t['phase'] in ('ready','submitted') for t in quota_tasks)):
        name = 'quota_'+str(len(quota_tasks)).zfill(5)
        request = dict(schema='shift-stream-payload-v1', environment='cmssw', stage='smart_sm_quota',
            cohort_id=config['campaign'], argv=['python3', '@TOOLS@/scripts/run_shift_smart_sm_detector.py',
            'request.json'], application_timeout_seconds=300, result_path='report.json',
            return_files=[], status_fields={}, reservation_bytes=0)
        state['tasks'][name] = make_task(root, config, name, request, 'quota', '')
    finished_sources = [t for t in state['tasks'].values() if t['kind']=='source' and t['phase']=='done']
    finished_nanos = [t for t in state['tasks'].values() if t['kind']=='detector' and t['phase']=='done']
    finished_raw = [t for t in state['tasks'].values() if t['kind']=='raw_source' and t['phase']=='done']
    inventory_change = len(finished_sources)+len(finished_nanos)+len(finished_raw)
    all_sources_returned = all(sum(t['stratum']==st and t.get('source_group')!='supply'
        for t in finished_sources)>=a['chunks'] for st,a in config['production'].items())
    all_science_complete = all_sources_returned and closed_tasks({n:t for n,t in state['tasks'].items()
        if t['kind'] in ('source','detector')})
    inventory_due = (inventory_change-state.get('last_inventory_count', 0) >= config.get('inventory_interval',250)
                     or (finished_nanos and not state.get('last_inventory_count'))
                     or (all_science_complete and inventory_change != state.get('last_inventory_count', 0)))
    if inventory_due and not any(t['kind']=='inventory' and t['phase'] in ('ready','submitted')
                                 for t in state['tasks'].values()):
        epoch=state.get('inventory_preparation_epoch',{})
        if epoch.get('change')!=inventory_change:
            epoch=dict(change=inventory_change,checked_epoch=time.time())
            state['inventory_preparation_epoch']=epoch
            save(root/'ledger.json',state)
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
            raw_GEN_reserve=[dict(source_GEN_sha256=t['raw_gen_sha256'],saved_events=t['saved_GEN_events'],
                receipt_url=t['raw_receipt_url'],receipt_sha256=t['raw_receipt_sha256']) for t in finished_raw],
            raw_GEN_reserve_is_detector_exposure=False,
            checked_epoch=epoch['checked_epoch'])
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
        own = [t for t in state['tasks'].values() if t['stratum'] == stratum and t['kind']=='source'
               and t.get('source_group')!='supply']
        if stratum in admission_failures or not any(t['phase']=='done' for t in own):
            continue
        if (families[stratum.split('_')[0]] and len(own) < allocation['chunks']
                and not any(t['phase']=='ready' for t in own)):
            source_task(root, config, state, stratum, len(own))
    advance_gen_supply(root,config,state,admission_failures)
    workers = [ad for ad in ads if ad.get('JobUniverse')==5 and ad['JobStatus'] in (1,2,6,7)]
    running = sum(ad['JobStatus']==2 for ad in workers)
    idle = sum(ad['JobStatus']==1 for ad in workers)
    admitted = 0
    storage_blocks=Counter()
    extra_spent, extra_reserved = postprocessing_charge(config)
    for name, task in admission_order(state['tasks']):
        if task['phase'] != 'ready' or task['stratum'] in admission_failures:
            continue
        if task['kind'] != 'quota':
            if not quota_fresh:
                continue
            if task['kind'] != 'inventory' and not families.get(task['stratum'].split('_')[0], False):
                continue
            if task['kind']=='source' and not fresh_detector_families[task['stratum'].split('_')[0]]:
                continue
            in_flight = sum(t['bound_bytes'] for t in state['tasks'].values() if t['phase']=='submitted') + extra_reserved
            spent = sum(t.get('published_bytes', 0) for t in state['tasks'].values() if t['phase']=='done') + extra_spent
            if spent+in_flight+task['bound_bytes'] > config['own_reservation_bytes']:
                storage_blocks['total_reservation']+=1
                continue
            category='raw_supply' if task['kind']=='raw_source' else 'detector_production'
            if category in config.get('storage_partitions',{}):
                amount=sum((t.get('published_bytes',0) if t['phase']=='done' else
                    t['bound_bytes'] if t['phase']=='submitted' else 0)
                    for t in state['tasks'].values() if (t['kind']=='raw_source')==(category=='raw_supply'))
                if category == 'detector_production':
                    amount += extra_spent + extra_reserved
                if amount+task['bound_bytes']>config['storage_partitions'][category]:
                    storage_blocks[category]+=1;continue
            required = config['external_future_reservation_bytes']+config['safety_floor_bytes']+in_flight+task['bound_bytes']
            if quota['free_bytes'] < required or quota['free_files'] < 30000:
                storage_blocks['shared_storage_floor']+=1
                continue
        if (len(workers)+admitted >= config['account_worker_ceiling']
                or idle+admitted >= config['maximum_idle_workers']
                or admitted >= config['maximum_starts_per_poll']):
            break
        description = Path(task.get('submit_description', str(Path(task['directory'])/'worker.sub')))
        if 'submit_description' in task and sha(description) != task.get('submit_description_sha256'):
            raise ValueError('Reviewed replacement submit description changed')
        task.update(phase='submitting', intent_epoch=time.time())
        save(root/'ledger.json', state)
        output = run_condor(['/usr/bin/condor_submit', '-terse',
            str(description)], cwd=task['directory'], timeout=45)
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
        failed_by_stage=dict(Counter(t['kind'] for t in state['tasks'].values() if t['phase']=='failed')),
        failed_bins=sorted(failures), combined_family_checks=families, quota_fresh=quota_fresh,
        fresh_GEN_to_Nano_checks=fresh_detector_families,
        account_running_workers=running, submitted_this_poll=admitted)
    progress=gen_supply_progress(config,state['tasks'])
    record.update(gen_supply=progress,storage_admission_blocks=dict(storage_blocks))
    sources_finished = all(sum(t['kind']=='source' and t.get('source_group')!='supply'
        and t['stratum']==stratum and t['phase']=='done'
        for t in state['tasks'].values()) == allocation['chunks']
        for stratum, allocation in config['production'].items())
    raw_count=sum(t['kind']=='raw_source' for t in state['tasks'].values())
    prepared_count=sum(t['kind']=='source' and t.get('source_group')=='supply' for t in state['tasks'].values())
    if (sources_finished and progress['raw_supply_finished'] and raw_count==prepared_count
            and closed_tasks(state['tasks'])):
        record.update(phase='ntuples_complete', histograms_complete=False,
                      grouped_ntuples_complete=False)
        save(root/'completion.json', record)
    own_clusters={t.get('cluster') for t in state['tasks'].values() if t['phase']=='submitted'}
    if storage_blocks and not admitted and not any(ad['ClusterId'] in own_clusters for ad in workers):
        record['phase']='waiting_for_storage'
    if failures:
        save(root/'attention_required.json', record)
    save(root/'heartbeat.json', record)
    return record


def watch(root, controller_iwd=None):
    record = read(root/'heartbeat.json') if (root/'heartbeat.json').exists() else {}
    if record.get('phase') == 'ntuples_complete':
        save(root/'watchdog_heartbeat.json', dict(checked_epoch=time.time(), terminal=True))
        return True
    ads = query('Iwd == '+json.dumps(str(controller_iwd or root))+' && ShiftSmartSMController =?= True',
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
    if record.get('phase') == 'storage_retry':
        problems.append('Temporary storage failure; controller is retrying safely')
    if record.get('failed_bins'):
        problems.append('Failed bins require review: '+', '.join(record['failed_bins']))
    if record.get('failed_by_stage'):
        problems.append('Failed stages require review: '+json.dumps(record['failed_by_stage'],sort_keys=True))
    if record.get('phase')=='waiting_for_storage':
        problems.append('Admission waiting for storage; workers and full exposure preserved')
    value = dict(checked_epoch=time.time(), problems=problems, controller_ads=ads)
    save(root/'watchdog_heartbeat.json', value)
    if problems:
        save(root/'watchdog_attention_required.json', value)
    return False


def run_controller(root,config,once=False):
    """Reload committed state after a temporary failure; never replay a submit."""
    state=None
    while True:
        try:
            if state is None:
                state=read(root/'ledger.json')
                if any(t['phase']=='submitting' for t in state['tasks'].values()):
                    raise RuntimeError('Uncertain persisted submission needs native review')
            record=step(root,config,state)
        except (NativeCondorError,OSError) as error:
            if isinstance(error,OSError) and error.errno not in TEMPORARY_STORAGE_ERRORS:
                save(root/'controller_failure.json',dict(epoch=time.time(),error=repr(error)))
                raise
            if state and any(t['phase']=='submitting' for t in state['tasks'].values()):
                save(root/'controller_failure.json',dict(epoch=time.time(),error=repr(error),
                    action='Uncertain submission requires native review; never repeat it'))
                raise
            # A previous poll may have made uncommitted directories or observed
            # finished workers. Rebuild from the durable ledger, never that
            # partially mutated state. Exact unsubmitted task files are reusable.
            state=None
            phase='scheduler_unknown' if isinstance(error,NativeCondorError) else 'storage_retry'
            record=dict(checked_epoch=time.time(),phase=phase,error=repr(error))
            print(json.dumps(record),file=sys.stderr,flush=True)
            try:
                save(root/'heartbeat.json',record)
                save(root/('scheduler_unknown.json' if phase=='scheduler_unknown' else
                           'controller_transient.json'),record)
            except OSError as report_error:
                if report_error.errno not in TEMPORARY_STORAGE_ERRORS:raise
                print('Storage also prevented writing the recovery heartbeat: '+repr(report_error),
                      file=sys.stderr,flush=True)
        except BaseException as error:
            save(root/'controller_failure.json',dict(epoch=time.time(),error=repr(error)))
            raise
        if once or record['phase']=='ntuples_complete':
            return record
        time.sleep(30)


def run_watchdog(root,controller_iwd=None,once=False):
    while True:
        try:
            if watch(root,controller_iwd):return
        except (NativeCondorError,OSError) as error:
            if isinstance(error,OSError) and error.errno not in TEMPORARY_STORAGE_ERRORS:raise
            record=dict(epoch=time.time(),error=repr(error))
            print(json.dumps(record),file=sys.stderr,flush=True)
            path=root/('watchdog_scheduler_unknown.json' if isinstance(error,NativeCondorError)
                       else 'watchdog_storage_retry.json')
            try:save(path,record)
            except OSError as report_error:
                if report_error.errno not in TEMPORARY_STORAGE_ERRORS:raise
                print('Watchdog storage unavailable: '+repr(report_error),file=sys.stderr,flush=True)
        if once:return
        time.sleep(60)


def main():
    root = Path(sys.argv[1]).resolve()
    if str(root).startswith(('/tmp/', '/pool/')):
        raise ValueError('Persistent campaign directory required')
    deployment = root
    config = read(deployment/'config.json')
    if sha(deployment/'config.json') != sys.argv[2]:
        raise ValueError('Frozen controller configuration changed')
    for name, expected in config.get('deployment_files', {}).items():
        if sha(deployment/name) != expected:
            raise ValueError('Frozen deployment changed: '+name)
    root = Path(config.get('campaign_directory', str(deployment))).resolve()
    if '--watchdog' in sys.argv:
        run_watchdog(root,config.get('controller_iwd'),once='--once' in sys.argv)
        return
    with (root/'controller.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run_controller(root,config,once='--once' in sys.argv)


if __name__ == '__main__':
    main()
