#!/usr/bin/env python3
# Code style: AI-code. AI-generated bounded parallel coordinator for validated raw Nano retirement.
"""Run four independent point checks using the unchanged reviewed retirement guard.

Only the parent updates shared state and freed-space credit. Each bounded child
owns one target's requests, exact manifest and deletion receipt. The first
reviewed manual canary remains in the shared record directory and is adopted
before scheduling. Immutable v1 guard code is copied byte-for-byte into v2.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import copy
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time

import retire_grouped_dark_photon_chunks as guard


def verified_config(deployment,seal):
    deployment=Path(deployment).resolve()
    authority=guard.checked_local(deployment/'seal.json',seal)
    for name,digest in authority['files'].items():
        guard.require(guard.support.sha256(name) == digest,'Frozen parallel retirement dependency changed: '+name)
    config=guard.read(deployment/'config.json')
    guard.require(config['controller_deployment'] == str(deployment) and config['workers'] == 4,
                  'Parallel retirement worker identity or bound changed')
    return config


def adopt_activation(config,state,seal):
    for item in config['authorities']:
        guard.require(guard.artifact(item['path']) == item,'Original authorized source worklist changed')
    path=Path(config['activation_gate'])
    if not path.is_file():
        state['phase']='waiting_for_root_reviewed_deletion_canary'
        return False
    gate=guard.read(path)
    guard.require(gate.get('schema') == 'shift-dp-raw-nano-retirement-activation-v1'
        and gate.get('complete') is True and gate.get('controller_seal_sha256') == seal
        and isinstance(gate.get('initial_receipts'),dict) and gate['initial_receipts'],
        'Parallel activation lacks its reviewed exact deletion canary')
    targets={target['key']:target for target in config['targets']}
    entries=state.setdefault('targets',{})
    for key,item in gate['initial_receipts'].items():
        guard.require(key in targets and guard.artifact(item['path']) == item,'Reviewed initial receipt changed')
        receipt=guard.read(item['path'])
        target=targets[key]
        guard.require(receipt.get('complete') is True and receipt.get('retained_readbacks_verified') is True
            and receipt.get('point') == target['point'] and receipt.get('production') == target['production']
            and guard.support.sha256(receipt['plan_path']) == receipt['plan_sha256']
            and receipt['plan_sha256'] == gate.get('reviewed_canary_plan_sha256'),
            'Initial receipt differs from the exact reviewed canary')
        entries[key]=dict(retired=True,phase='retired',receipt=item['path'],initial_reviewed_canary=True)
    return True


def worker(config,request,scratch):
    targets={target['key']:target for target in config['targets']}
    guard.require(request['target'] in targets,'Worker target is outside its frozen grid')
    target=targets[request['target']]
    directory,row=guard.validate_target(target)
    record=Path(config['directory'])/'records'/target['key']
    record.mkdir(parents=True,exist_ok=True)
    manifest=record/'retirement_plan.json'
    if request['action'] == 'review':
        if not manifest.exists():
            plan=guard.prepare(directory,target['point'],guard.support.sha256(directory/'seal.json'),
                               request['proof'],scratch)
            guard.write(manifest,plan)
        value=guard.read(manifest)
        guard.require(value.get('runner_sha256') == guard.support.sha256(guard.__file__),
                      'Existing point manifest belongs to a different guard')
        return dict(complete=True,target=target['key'],manifest=str(manifest),
                    manifest_sha256=guard.support.sha256(manifest),bytes=value['total_candidate_bytes'])
    guard.require(request['action'] == 'execute','Unknown bounded worker action')
    guard.require(str(manifest) == request['manifest'],'Worker manifest escaped its exact target directory')
    proof=record/('scheduler_execute_'+str(time.time_ns())+'.json.gz')
    guard.capture_account_proof(proof)
    receipt=guard.execute(manifest,request['manifest_sha256'],proof,scratch)
    return dict(complete=True,target=target['key'],removed_bytes=receipt['removed_bytes'],
                receipt=str(manifest.with_name('retirement_plan_deletion_receipt.json')))


def launch_worker(config,target,action,proof=None,review=None):
    """Threads only wait for independent, bounded interpreter process groups."""
    record=Path(config['directory'])/'records'/target['key']
    record.mkdir(parents=True,exist_ok=True)
    attempt=action+'_'+str(time.time_ns())
    request=dict(action=action,target=target['key'],proof=str(proof) if proof else None)
    if review:
        request.update(manifest=review['manifest'],manifest_sha256=review['manifest_sha256'])
    source,result=record/(attempt+'_request.json'),record/(attempt+'_result.json')
    guard.write(source,request)
    command=[sys.executable,str(Path(__file__).resolve()),'--deployment',config['controller_deployment'],
             '--seal',config['seal_sha256'],'--scratch',config['scratch'],
             '--worker-request',str(source),'--worker-result',str(result)]
    started=time.time()
    child=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
    try:
        stdout,stderr=child.communicate(timeout=config['operation_timeout_seconds'])
    except subprocess.TimeoutExpired:
        os.killpg(child.pid,signal.SIGKILL)
        stdout,stderr=child.communicate(timeout=10)
        guard.write(record/(attempt+'_failure.json'),dict(command=command,started_epoch=started,
            stopped_epoch=time.time(),error='Bounded point operation timed out',stdout=stdout[-2000:],stderr=stderr[-5000:]))
        raise RuntimeError('Retirement '+action+' timed out for '+target['key'])
    if child.returncode != 0 or not result.is_file():
        guard.write(record/(attempt+'_failure.json'),dict(command=command,returncode=child.returncode,
            started_epoch=started,stopped_epoch=time.time(),stdout=stdout[-2000:],stderr=stderr[-5000:]))
        raise RuntimeError('Retirement '+action+' failed for '+target['key']+': '+stderr[-1000:])
    value=guard.read(result)
    guard.require(value.get('complete') is True and value.get('target') == target['key'],
                  'Bounded worker returned a foreign or incomplete result')
    return value


def cohort_step(config,state,seal,launch=launch_worker):
    guard.require(not state.get('error'), 'Preserve the existing fatal retirement error for review')
    state.update(checked_utc=datetime.now(timezone.utc).isoformat())
    if not adopt_activation(config,state,seal):
        return state
    root=Path(config['directory'])
    entries=state.setdefault('targets',{})
    number=state.get('cohort_index',0)+1
    state['cohort_index']=number
    guard.write(root/'state.json',state)
    cohort=root/'cohorts'/f'c{number:05d}'
    cohort.mkdir(parents=True,exist_ok=False)
    proof=cohort/'scheduler_eligibility.json.gz'
    def wait_for_scheduler(stage,error,target=None):
        # No review or execution has begun. This disposable cohort proof has
        # no downstream consumer; retain one compact uncertainty record instead.
        state.update(phase='waiting_for_verified_scheduler',complete=False,native_unknown=repr(error),
            last_unknown_scheduler=dict(checked_utc=datetime.now(timezone.utc).isoformat(),
                                        stage=stage,target=target,error=repr(error)))
        if proof.exists():
            proof.unlink()
        cohort.rmdir()
        return state
    try:
        observation=guard.capture_account_proof(proof)
    except guard.native.NativeCondorError as error:
        return wait_for_scheduler('account_coverage',error)
    selected=[]
    for target in config['targets']:
        entry=entries.setdefault(target['key'],{})
        if entry.get('retired'):
            continue
        directory=Path(target['production'])
        marker=directory/'completed_points'/(target['point']+'.json')
        if not marker.is_file() or guard.read(marker).get('complete') is not True:
            continue
        directory,row=guard.validate_target(target)
        scope=dict(gen_directory=row['final_output_base']+'/nanoAOD',detector_directory=str(directory))
        if any(guard.guards.point_referenced(list(ad.values()),ad['Iwd'],scope) for ad in observation['ads']):
            entry['phase']='waiting_for_all_native_jobs_in_deployment_to_finish'
            continue
        try:
            entry['native_manager']=guard.native_closed_manager(config,directory)
        except guard.native.NativeCondorError as error:
            return wait_for_scheduler('manager_history',error,target['key'])
        selected.append(target)
        if len(selected) == config['workers']:
            break
    state.pop('native_unknown',None)
    if not selected:
        proof.unlink()
        cohort.rmdir()
        state.update(phase='watching_for_complete_points',complete=all(
            entries.get(target['key'],{}).get('retired') for target in config['targets']))
        guard.publish_freed_gate(config,state,seal)
        return state
    state.update(phase='reviewing_parallel_cohort',active_targets=[target['key'] for target in selected])
    guard.write(root/'state.json',state)
    reviews={}
    with ThreadPoolExecutor(max_workers=config['workers']) as pool:
        futures={pool.submit(launch,config,target,'review',proof):target for target in selected}
        for future in as_completed(futures):
            target=futures[future]
            reviews[target['key']]=future.result()
            entries[target['key']]['review']=reviews[target['key']]
            guard.write(root/'state.json',state)
    removed=sum(guard.read(entry['receipt'])['removed_bytes'] for entry in entries.values() if entry.get('retired'))
    guard.require(removed+sum(item['bytes'] for item in reviews.values()) <= config['maximum_retired_bytes'],
                  'Parallel cohort exceeds the exact complete-grid retirement bound')
    state['phase']='executing_verified_parallel_cohort'
    guard.write(root/'state.json',state)
    errors=[]
    with ThreadPoolExecutor(max_workers=config['workers']) as pool:
        futures={pool.submit(launch,config,target,'execute',None,reviews[target['key']]):target for target in selected}
        for future in as_completed(futures):
            target=futures[future]
            try:
                value=future.result()
                entries[target['key']].update(retired=True,phase='retired',receipt=value['receipt'])
                guard.publish_freed_gate(config,state,seal)
            except Exception as error:
                entries[target['key']]['error']=repr(error)
                errors.append(error)
            guard.write(root/'state.json',state)
    state.pop('active_targets',None)
    if errors:
        raise RuntimeError('One or more exact target operations failed; preserve their intents and receipts: '+repr(errors[0]))
    state['phase']='watching_for_complete_points'
    return state


def run_controller(config,seal,scratch):
    root=Path(config['directory'])
    config=dict(config,seal_sha256=seal,scratch=str(guard.local_scratch(scratch)))
    stop=threading.Event()
    def heartbeat():
        while not stop.is_set():
            value=dict(checked_utc=datetime.now(timezone.utc).isoformat(),pid=os.getpid(),
                       deployment=config['controller_deployment'],workers=config['workers'])
            guard.write(root/'heartbeat.json',value)
            guard.write(Path(config['controller_deployment'])/'heartbeat.json',value)
            stop.wait(60)
    observer=threading.Thread(target=heartbeat,daemon=True)
    observer.start()
    try:
        with (root/'controller.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            state=guard.read(root/'state.json') if (root/'state.json').exists() else {}
            while True:
                try:
                    state=cohort_step(config,state,seal)
                except Exception as error:
                    if not state.get('error'):
                        state['error']=repr(error)
                    state['checked_utc']=datetime.now(timezone.utc).isoformat()
                    guard.write(root/'state.json',state)
                    raise
                guard.write(root/'state.json',state)
                if state.get('complete'):
                    return
                time.sleep(config['poll_seconds'])
    finally:
        stop.set()
        observer.join(timeout=5)


def prepare(output,template,template_seal):
    output,template=Path(output).resolve(),Path(template).resolve()
    guard.require(not output.exists(),'Preserve the previous immutable parallel deployment')
    authority=guard.checked_local(template/'seal.json',template_seal)
    for name,digest in authority['files'].items():
        guard.require(guard.support.sha256(name) == digest,'Validated v1 dependency changed: '+name)
    config=copy.deepcopy(guard.read(template/'config.json'))
    output.mkdir(parents=True)
    (output/'tools').mkdir()
    files={}
    for name,digest in authority['files'].items():
        source=Path(name)
        if source.parent != template/'tools':
            continue
        target=output/'tools'/source.name
        shutil.copy2(source,target)
        files[str(target)]=guard.support.sha256(target)
        guard.require(files[str(target)] == digest,'V1 guard changed during copying')
    coordinator=output/'tools'/Path(__file__).name
    shutil.copy2(__file__,coordinator)
    files[str(coordinator)]=guard.support.sha256(coordinator)
    config.update(schema='shift-dp-parallel-raw-nano-retirement-v1',controller_deployment=str(output),
                  workers=4,operation_timeout_seconds=3600,
                  original_guard=dict(directory=str(template),seal_sha256=template_seal))
    guard.write(output/'config.json',config)
    wrapper='\n'.join(['#!/bin/bash','# Code style: AI-code. AI-generated bounded parallel retirement controller.',
        'set -eo pipefail','unset PYTHONPATH PYTHONHOME LD_LIBRARY_PATH LD_PRELOAD ROOTSYS',
        'export PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1',
        'source /cvmfs/sft.cern.ch/lcg/views/LCG_108/x86_64-el9-gcc13-opt/setup.sh',
        'exec python3 '+shlex.quote(str(coordinator))+' --deployment '+shlex.quote(str(output))+
        ' --seal "$1" --scratch "$_CONDOR_SCRATCH_DIR"',''])
    (output/'controller.sh').write_text(wrapper)
    (output/'controller.sh').chmod(0o755)
    descriptor=(template/'controller.sub.in').read_text().replace(str(template),str(output))
    descriptor=descriptor.replace('request_cpus = 1','request_cpus = 4')
    descriptor=descriptor.replace('CONTROLLER_SEAL_DIGEST','PARALLEL_SEAL_DIGEST')
    (output/'controller.sub.in').write_text(descriptor)
    for path in [template/'seal.json',output/'config.json',output/'controller.sh',output/'controller.sub.in']:
        files[str(path)]=guard.support.sha256(path)
    files.update({item['path']:item['sha256'] for item in config['authorities']})
    guard.write(output/'seal.json',dict(schema='shift-parallel-retirement-controller-seal-v1',files=files))
    seal=guard.support.sha256(output/'seal.json')
    (output/'controller.sub').write_text(descriptor.replace('PARALLEL_SEAL_DIGEST',seal))
    return dict(complete=True,directory=str(output),state_directory=config['directory'],seal_sha256=seal,
                workers=4,validated_guard_sha256=files[str(output/'tools'/'retire_grouped_dark_photon_chunks.py')])


def admission_config(directory,seal):
    directory=Path(directory).resolve()
    authority=guard.checked_local(directory/'seal.json',seal)
    for path,digest in authority['files'].items():
        guard.require(guard.support.sha256(path)==digest,'Frozen admission dependency changed: '+path)
    config=guard.read(directory/'config.json')
    guard.require(config['directory']==str(directory),'Admission working directory changed')
    guard.require((directory/'admission.sub').read_text()==(directory/'admission.sub.in').read_text().replace('ADMISSION_SEAL',seal),
                  'Rendered admission submit descriptor differs from its sealed template')
    return config


def qualification_ready(config):
    """Require real execution, all four semantic reports and native success."""
    directory=Path(config['qualification']['directory'])
    authority=guard.checked_local(directory/'seal.json',config['qualification']['seal_sha256'])
    for path,digest in authority['files'].items():
        guard.require(guard.support.sha256(path)==digest,'Standalone qualification dependency changed: '+path)
    cluster=config['qualification']['cluster']
    attributes=['ClusterId','ProcId','Iwd','JobStatus','HoldReason','ExitCode','ExitBySignal',
                'LastRemoteHost','RemoteHost','JobStartDate','CompletionDate','SendCredential']
    rows=guard.native.query(f'ClusterId == {cluster} && ProcId == 0',attributes,
        schedd=config['condor_schedd'],pool=config['condor_pool'],timeout=60)
    if rows:
        guard.require(len(rows)==1 and rows[0]['Iwd']==str(directory),'Qualification native identity changed')
        guard.require(rows[0]['JobStatus'] not in (3,5),'Qualification failed, held or is being removed: '+str(rows[0]))
        return False
    command=['/usr/bin/condor_history','-name',config['condor_schedd'],'-pool',config['condor_pool'],
             str(cluster)+'.0','-limit','1','-json','-attributes',','.join(attributes)]
    rows=guard.native.parse_ads(guard.native.run_condor(command,timeout=60))
    if not rows:
        return False
    row=rows[0]
    guard.require(len(rows)==1 and row['ClusterId']==cluster and row['ProcId']==0
        and row['Iwd']==str(directory) and row.get('ExitCode')==0 and not row.get('ExitBySignal')
        and row.get('SendCredential') is True and row.get('LastRemoteHost')
        and type(row.get('JobStartDate')) is int and row['JobStartDate']>0
        and row.get('CompletionDate',0)>=row['JobStartDate'],
        'Qualification native completion is failed or does not prove delegated execution')
    event=(directory/'qualify.events.log').read_text()
    guard.require(re.search(r'^001 \('+str(cluster)+r'\.000\.000\)',event,re.MULTILINE),
                  'Qualification has no real execute event')
    report=guard.read(directory/'result.json')
    guard.require(report.get('complete') is True and report.get('read_only') is True
        and report.get('deleted_bytes')==0 and report.get('activation_written') is False
        and report.get('storage_credit_written') is False,'Qualification changed or performed an unapproved action')
    expected=set(guard.read(directory/'config.json')['targets'])
    results=report.get('results',[])
    guard.require(len(expected)==4 and len(results)==4 and {row['target'] for row in results}==expected
        and len({row['pid'] for row in results})==4,'Qualification did not run the exact four independent children')
    guard.require(max(row['started_epoch'] for row in results)<min(row['closed_epoch'] for row in results),
                  'Qualification children did not execute concurrently')
    target_config=verified_config(config['controller']['directory'],config['controller']['seal_sha256'])
    targets={row['key']:row for row in target_config['targets']}
    for result in results:
        guard.require(result.get('complete') is True and result.get('all_raw_candidates_still_present') is True
            and result.get('deleted_bytes')==0 and result.get('account_schedds'),
            'An actual qualification child is incomplete')
        item=result['semantic_plan']
        guard.require(guard.artifact(item['path'])==item,'Qualification semantic manifest changed')
        plan=guard.read(item['path'])
        target=targets[result['target']]
        guard.require(plan.get('complete') is True and plan['point']==target['point']
            and plan['production']==target['production'] and plan['runner_sha256']==guard.support.sha256(guard.__file__)
            and plan['events']==result['checked_events']
            and all(value.get('complete') is True and value.get('edm_metadata_values_preserved') is True
                    for value in plan['all_branch_equivalence'])
            and sum(value['events'] for value in plan['all_branch_equivalence'])==plan['events'],
            'Qualification full branch/CMSSW metadata equivalence is incomplete')
        proof=directory/'records'/result['target']/'account_scheduler_proof.json.gz'
        # The frozen qualifier validated complete, fresh account coverage when
        # it made each manifest. Preserve its exact proof bytes after it closes.
        guard.require(guard.artifact(proof)==plan['scheduler_review'],'Qualification account proof changed')
    return dict(native=row,result=guard.artifact(directory/'result.json'))


def admission_readback(config,cluster):
    attributes=['ClusterId','ProcId','Iwd','Cmd','Args','Arguments','JobStatus','RequestCpus','RequestMemory',
                'MaxRuntime','SendCredential','WantIOProxy','OnExitHold','PeriodicRelease','JobPrio','Requirements','PreLocalRequirements',
                'PreAfsRequirements','PreCvmfsRequirements','PreVerRequirements']
    rows=guard.native.query(f'ClusterId == {cluster} && ProcId == 0',attributes,
        schedd=config['condor_schedd'],pool=config['condor_pool'],timeout=60)
    if not rows:
        raise guard.native.NativeCondorError('Submitted retirement native readback is not visible yet')
    guard.require(len(rows)==1,'Submitted retirement native readback is ambiguous')
    row=rows[0]
    controller=config['controller']
    guard.require(row['Iwd']==controller['directory'] and row['Cmd']==str(Path(controller['directory'])/'controller.sh')
        and row.get('Args',row.get('Arguments','')).strip()==controller['seal_sha256']
        and row['RequestCpus']==4 and row['RequestMemory']>=8000 and row['MaxRuntime']>=604800
        and row['SendCredential'] is True and row.get('WantIOProxy') is True
        and row.get('PeriodicRelease') is False and row['JobPrio']>=1000000,'Retirement queued identity or resources changed')
    hold=re.sub(r'\s+','',str(row.get('OnExitHold',''))).lower()
    guard.require('exitbysignal==true' in hold and 'exitcode!=0' in hold and '||' in hold,
                  'Retirement native failure-hold condition changed')
    expression=' '.join(str(row.get(name,'')) for name in attributes if 'Requirements' in name)
    guard.require(all(host in expression for host in config['excluded_hosts']),'Retirement failed-host exclusions are missing')
    guard.require(row['JobStatus'] not in (3,5),'Retirement controller failed or is held')
    return row


def admission_step(config,state):
    """At most one native submission attempt, after the exact qualification."""
    root=Path(config['directory'])
    qualified=qualification_ready(config)
    if not qualified:
        state.update(phase='waiting_for_actual_standalone_qualification',checked_epoch=time.time())
        return state
    target_config=verified_config(config['controller']['directory'],config['controller']['seal_sha256'])
    controller=Path(config['controller']['directory'])
    guard.require((controller/'controller.sub').read_text()==(controller/'controller.sub.in').read_text().replace(
        'PARALLEL_SEAL_DIGEST',config['controller']['seal_sha256']),
        'Rendered retirement submit descriptor differs from its sealed template')
    authorization=guard.checked_local(config['authorization']['path'],config['authorization']['sha256'])
    scope=authorization['approved_scope']
    observed=hashlib.sha256(json.dumps(target_config['targets'],sort_keys=True,separators=(',',':')).encode()).hexdigest()
    guard.require(authorization.get('complete') is True and authorization.get('user_answer')=='Approve verified duplicate cleanup'
        and scope['target_count']==len(target_config['targets'])==264 and scope['maximum_retired_bytes']==target_config['maximum_retired_bytes']==50000000000
        and scope['targets_sha256']==observed and scope['controller_seal_sha256']==config['controller']['seal_sha256'],
        'User authorization does not cover this exact frozen retirement scope')
    observation=guard.capture_account_proof(root/'account_last.json.gz')
    existing=[ad for ad in observation['ads'] if ad.get('Iwd')==config['controller']['directory']]
    guard.require(len(existing)<=1,'Duplicate retirement controllers found: do not submit')
    intent_path=root/'submission_intent.json'
    submission_path=Path(config['controller']['directory'])/'submission.json'
    if existing:
        guard.require(intent_path.is_file(),'An unreviewed retirement controller already exists')
        cluster=existing[0]['ClusterId']
    elif submission_path.exists():
        raise RuntimeError('Preserve existing submitted retirement; its current native state requires review')
    elif intent_path.exists():
        state.update(phase='waiting_for_ambiguous_attempt_native_evidence_do_not_resubmit',checked_epoch=time.time())
        return state
    else:
        receipt=guard.checked_local(config['initial_receipt']['path'],config['initial_receipt']['sha256'])
        guard.require(receipt['plan_sha256']==config['initial_plan_sha256'] and receipt.get('complete') is True,
                      'Exact reviewed initial deletion receipt changed')
        gate=dict(schema='shift-dp-raw-nano-retirement-activation-v1',complete=True,
            controller_seal_sha256=config['controller']['seal_sha256'],reviewed_canary_plan_sha256=config['initial_plan_sha256'],
            initial_receipts={config['initial_key']:config['initial_receipt']},
            user_authorization=config['authorization'],qualification=qualified,admitted_epoch=time.time())
        activation=Path(target_config['activation_gate'])
        guard.require(not activation.exists(),'Preserve an existing activation instead of overwriting it')
        guard.write(activation,gate)
        adopt_activation(target_config,{},config['controller']['seal_sha256'])
        requirements='Requirements = '+ ' && '.join('(TARGET.Machine != '+json.dumps(host)+')' for host in config['excluded_hosts'])
        command=['/usr/bin/condor_submit','-name',config['condor_schedd'],'-pool',config['condor_pool'],
            '-append',requirements,'-append','priority = 1000000',
            str(Path(config['controller']['directory'])/'controller.sub')]
        # Persist before touching the scheduler. If a crash makes the outcome
        # ambiguous, a later pass may adopt the exact native job but never retry.
        guard.write(intent_path,dict(command=command,cwd=config['controller']['directory'],attempted_epoch=time.time(),
                                     qualification=qualified,authorization=config['authorization']))
        stdout=guard.native.run_condor(command,cwd=config['controller']['directory'],timeout=120)
        match=re.search(r'1 job\(s\) submitted to cluster (\d+)\.',stdout)
        guard.require(match is not None,'Unrecognized submit result; preserve intent and never blindly retry: '+stdout)
        cluster=int(match.group(1))
        guard.write(root/'native_submission_result.json',dict(cluster=cluster,stdout=stdout,checked_epoch=time.time()))
    readback=admission_readback(config,cluster)
    submission=dict(cluster=cluster,Iwd=config['controller']['directory'],seal_sha256=config['controller']['seal_sha256'],
        qualification=qualified,authorization=config['authorization'],native_readback=readback,
        admitted_by=config['directory'],submitted_epoch=time.time())
    if not submission_path.exists():guard.write(submission_path,submission)
    guard.write(root/'admission_complete.json',submission)
    state.update(phase='submitted_exact_reviewed_retirement',complete=True,cluster=cluster,checked_epoch=time.time())
    return state


def admission_observe(config,state):
    try:
        return admission_step(config,state)
    except guard.native.NativeCondorError as error:
        state.update(phase='waiting_for_known_native_state',native_unknown=str(error)[:2000],checked_epoch=time.time())
        return state


def run_admission(config):
    root=Path(config['directory'])
    stop=threading.Event()
    state={}
    def heartbeat():
        while not stop.is_set():
            guard.write(root/'heartbeat.json',dict(checked_epoch=time.time(),
                checked_utc=datetime.now(timezone.utc).isoformat(),pid=os.getpid(),phase=state.get('phase')))
            stop.wait(60)
    observer=threading.Thread(target=heartbeat,daemon=True)
    observer.start()
    try:
        with (root/'admission.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            started=time.monotonic()
            while time.monotonic()-started < 590000:
                try:
                    state=admission_observe(config,state)
                except Exception as error:
                    state.update(error=repr(error)[:5000],checked_epoch=time.time())
                    guard.write(root/'state.json',state)
                    raise
                guard.write(root/'state.json',state)
                if state.get('complete'):return
                time.sleep(120)
            raise RuntimeError('Qualification admission reached its finite runtime; preserve state for review')
    finally:
        stop.set();observer.join(timeout=5)


def prepare_admission(output,controller,controller_seal,qualification,qualification_seal,authorization,initial_receipt,initial_plan):
    output,controller,qualification=map(lambda p:Path(p).resolve(),(output,controller,qualification))
    guard.require(not output.exists(),'Preserve the immutable admission deployment')
    target_config=verified_config(controller,controller_seal)
    qualify_authority=guard.checked_local(qualification/'seal.json',qualification_seal)
    for path,digest in qualify_authority['files'].items():
        guard.require(guard.support.sha256(path)==digest,'Frozen standalone qualification changed')
    job=guard.read(qualification/'submission.json')
    output.mkdir(parents=True)
    (output/'tools').mkdir()
    dependencies={}
    for source in (controller/'tools').glob('*.py'):
        target=output/'tools'/source.name
        shutil.copy2(__file__ if source.name==Path(__file__).name else source,target)
        dependencies[str(target)]=guard.support.sha256(target)
    initial=guard.read(initial_receipt)
    keys=[row['key'] for row in target_config['targets'] if row['point']==initial['point'] and row['production']==initial['production']]
    guard.require(len(keys)==1 and initial['plan_sha256']==initial_plan,'Initial canary is outside the frozen target grid')
    config=dict(schema='shift-retirement-qualification-admission-v1',directory=str(output),
        controller=dict(directory=str(controller),seal_sha256=controller_seal),
        qualification=dict(directory=str(qualification),seal_sha256=qualification_seal,cluster=job['cluster']),
        authorization=guard.artifact(authorization),initial_receipt=guard.artifact(initial_receipt),initial_key=keys[0],
        initial_plan_sha256=initial_plan,condor_schedd=target_config['condor_schedd'],condor_pool=target_config['condor_pool'],
        excluded_hosts=['b9p13p2979.cern.ch','b9p20p4677.cern.ch','b9p22p1144.cern.ch'])
    guard.write(output/'config.json',config)
    wrapper='\n'.join(['#!/bin/bash','# Code style: AI-code. AI-generated qualification-aware one-shot admission.',
        'set -eo pipefail','unset PYTHONPATH PYTHONHOME LD_LIBRARY_PATH LD_PRELOAD ROOTSYS',
        'export PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1',
        'exec /usr/bin/python3 '+shlex.quote(str(output/'tools'/Path(__file__).name))+
        ' --admission '+shlex.quote(str(output))+' --seal "$1"',''])
    (output/'admission.sh').write_text(wrapper);(output/'admission.sh').chmod(0o755)
    descriptor='\n'.join(['# Code style: AI-code. AI-generated persistent qualification admission.','universe = vanilla',
        'executable = '+str(output/'admission.sh'),'arguments = ADMISSION_SEAL','initialdir = '+str(output),
        'getenv = False','should_transfer_files = NO','request_cpus = 1','request_memory = 1000MB','request_disk = 1000MB',
        '+JobFlavour = "nextweek"','+MaxRuntime = 604800','+SendCredential = True','+WantIOProxy = True','priority = 1000000',
        'Requirements = '+ ' && '.join('(TARGET.Machine != '+json.dumps(host)+')' for host in config['excluded_hosts']),
        '+JobBatchName = "SHIFT_DP_qualification_admission"','+ShiftDPStatisticsTask = '+json.dumps(str(output)),
        'output = '+str(output/'admission.$(ClusterId).$(ProcId).out'),'error = '+str(output/'admission.$(ClusterId).$(ProcId).err'),
        'log = '+str(output/'admission.events.log'),'on_exit_hold = (ExitBySignal == True) || (ExitCode != 0)',
        'on_exit_remove = (ExitBySignal == False) && (ExitCode == 0)','periodic_release = False','queue',''])
    (output/'admission.sub.in').write_text(descriptor)
    for path in [output/'config.json',output/'admission.sh',output/'admission.sub.in',controller/'seal.json',qualification/'seal.json',
                 Path(authorization),Path(initial_receipt),qualification/'submission.json']:
        dependencies[str(path.resolve())]=guard.support.sha256(path)
    guard.write(output/'seal.json',dict(schema='shift-retirement-admission-seal-v1',files=dependencies))
    seal=guard.support.sha256(output/'seal.json')
    (output/'admission.sub').write_text(descriptor.replace('ADMISSION_SEAL',seal))
    return dict(directory=str(output),seal_sha256=seal,qualification_cluster=job['cluster'],targets=len(target_config['targets']))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare',type=Path)
    parser.add_argument('--template',type=Path)
    parser.add_argument('--template-seal')
    parser.add_argument('--deployment',type=Path)
    parser.add_argument('--seal')
    parser.add_argument('--scratch',type=Path)
    parser.add_argument('--worker-request',type=Path)
    parser.add_argument('--worker-result',type=Path)
    parser.add_argument('--admission',type=Path)
    parser.add_argument('--prepare-admission',type=Path)
    parser.add_argument('--qualification',type=Path)
    parser.add_argument('--qualification-seal')
    parser.add_argument('--authorization',type=Path)
    parser.add_argument('--initial-receipt',type=Path)
    parser.add_argument('--initial-plan-sha')
    args=parser.parse_args()
    if args.prepare_admission:
        print(json.dumps(prepare_admission(args.prepare_admission,args.template,args.template_seal,
            args.qualification,args.qualification_seal,args.authorization,args.initial_receipt,args.initial_plan_sha)))
        return
    if args.admission:
        run_admission(admission_config(args.admission,args.seal))
        return
    if args.prepare:
        guard.require(args.template and args.template_seal,'Preparation requires the exact validated v1 template')
        print(json.dumps(prepare(args.prepare,args.template,args.template_seal)))
        return
    guard.require(args.deployment and args.seal and args.scratch,'A worker requires its persistent deployment, seal and scratch')
    config=verified_config(args.deployment,args.seal)
    if args.worker_request:
        guard.require(args.worker_result,'Bounded worker needs a result file')
        request=guard.read(args.worker_request)
        started=time.time()
        value=worker(config,request,guard.local_scratch(args.scratch))
        value.update(started_epoch=started,closed_epoch=time.time())
        guard.write(args.worker_result,value)
        return
    run_controller(config,args.seal,args.scratch)


if __name__=='__main__':
    main()
