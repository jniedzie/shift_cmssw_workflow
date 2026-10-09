#!/usr/bin/env python3
"""Monitor one submitted small-grid DAG and retire terminal validated points.

Never submits/retries/cancels jobs. Intermediate removal is explicit and only
follows a successful exact point ClassAd, ROOT/ledger validation, fresh global
queue inventory and the independent retirement tool's reference checks.
"""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def read(path):return json.loads(Path(path).read_text())


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1048576),b''):h.update(block)
    return h.hexdigest()


ATTRIBUTES='ClusterId,ProcId,Owner,AccountingGroupUser,JobStatus,ExitBySignal,ExitCode,ExitSignal,HoldReason,RemoveReason,Cmd,Args,Arguments,Iwd,TransferInput,TransferOutput,TransferInputFiles,TransferOutputFiles,ShiftBSMGrid,ShiftSuiteTag,ShiftBSMPoint,DAGManJobId'


def native_query(command):
    env={k:v for k,v in os.environ.items() if k not in
         ('LD_LIBRARY_PATH','LD_PRELOAD','PYTHONPATH','PYTHONHOME','ROOTSYS')}
    env['PATH']='/usr/bin:/bin'
    result=subprocess.run(command,capture_output=True,text=True,env=env,timeout=180)
    if result.returncode or result.stderr:raise RuntimeError('Scheduler query failed: '+result.stderr[-1000:])
    rows=json.loads(result.stdout)
    if not isinstance(rows,list):raise ValueError('Unknown scheduler JSON format')
    return rows


def successful_terminal(ad,point,dag_cluster,account):
    return (ad.get('Owner')==account and ad.get('DAGManJobId')==dag_cluster
            and ad.get('ShiftBSMGrid') is True and ad.get('ShiftBSMPoint')==point
            and ad.get('JobStatus')==4 and ad.get('ExitBySignal') is False and ad.get('ExitCode')==0)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base',type=Path,required=True)
    parser.add_argument('--workspace',type=Path,required=True)
    parser.add_argument('--dag-cluster',type=int,required=True)
    parser.add_argument('--retire-intermediates',action='store_true')
    parser.add_argument('--poll-seconds',type=int,default=30)
    parser.add_argument('--timeout-seconds',type=int,default=28800)
    args=parser.parse_args()
    if args.poll_seconds<10 or args.timeout_seconds<=0:parser.error('Invalid bounded monitor intervals')
    base=args.base.resolve();workspace=args.workspace.resolve();account=getpass.getuser()
    plan_path=base/'preparation_v2/plan.json';plan=read(plan_path)
    state_path=base/'monitor_state.json'
    state=read(state_path) if state_path.exists() else dict(schema='shift-dark-photon-grid-monitor-v1',
        complete=False,dag_cluster=args.dag_cluster,plan_sha256=sha(plan_path),points={},started_epoch=time.time(),errors=[])
    if state['dag_cluster']!=args.dag_cluster or state['plan_sha256']!=sha(plan_path):
        raise ValueError('Existing monitor belongs to another DAG/plan')
    state['complete']=False
    def save():
        state['updated_epoch']=time.time();temporary=state_path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(state,indent=2,allow_nan=False)+'\n');os.replace(temporary,state_path)
    for name,row in read(base/'retirement_tools/manifest.json').items():
        if sha(base/'retirement_tools'/name)!=row['sha256']:raise ValueError('Frozen retirement input changed: '+name)
    deadline=time.monotonic()+args.timeout_seconds
    constraint=f'Owner == "{account}" && ShiftSuiteTag == "bsm_grid_production_20261009" && DAGManJobId == {args.dag_cluster}'
    while time.monotonic()<deadline:
        try:
            live=native_query(['condor_q','-constraint',constraint,'-json','-attributes',ATTRIBUTES])
            history=native_query(['condor_history','-constraint',constraint,
                                  '-since',f'ClusterId < {args.dag_cluster}',
                                  '-match','30','-json','-attributes',ATTRIBUTES])
            changed=False
            for point in plan['points']:
                try:
                    name=point['point'];folder=Path(point['gen_directory']).parent
                    record=state['points'].setdefault(name,{})
                    if record.get('retirement_complete'):
                        detector=Path(point['detector_directory'])
                        retirement=read(detector/'retirement_plan.json')
                        journal=[json.loads(line) for line in (detector/'retirement_progress.jsonl').read_text().splitlines()]
                        if (sha(detector/'retirement_plan.json')!=record['retirement_plan_sha256'] or
                                not journal or journal[-1]['action']!='complete' or
                                journal[-1]['plan_sha256']!=record['retirement_plan_sha256']):
                            raise ValueError('Retirement completion journal changed: '+name)
                        for protected in retirement['protected']:
                            if sha(protected['path'])!=protected['sha256']:
                                raise ValueError('Retired point protected output/receipt changed: '+protected['path'])
                        record['terminal_validated']=True
                        continue
                    record['terminal_validated']=False
                    if name=='m15_prompt':
                        # This one point was completed on lxplus before submission.
                        proof=read(base/'local_canary_qualification.json')
                        if not proof.get('complete'):raise ValueError('Local canary lacks terminal qualification')
                        record['terminal_proof']=proof
                    else:
                        active=[ad for ad in live if ad.get('ShiftBSMPoint')==name]
                        if any(ad.get('JobStatus')!=4 for ad in active):continue
                        ads=[ad for ad in [*active,*history] if ad.get('ShiftBSMPoint')==name]
                        identities={(ad['ClusterId'],ad['ProcId']) for ad in ads}
                        if len(identities)>1:raise ValueError('Multiple job identities own point '+name)
                        if not ads:continue
                        ad=ads[-1];record['job']=[ad['ClusterId'],ad['ProcId']];record['job_status']=ad['JobStatus']
                        if not successful_terminal(ad,name,args.dag_cluster,account):continue
                        record['terminal_proof']=ad
                    point_report=read(folder/'point_report.json')
                    if not (point_report.get('complete') and point_report['point']==name
                            and point_report['plan_sha256']==state['plan_sha256']
                            and point_report['physical_mass_gev']==point['mass_gev']
                            and point_report['physical_epsilon']==point['epsilon']
                            and point_report['generated_events']==point_report['detector_events']==point['events']):
                        raise ValueError('Terminal worker receipt differs from frozen point: '+name)
                    detector=Path(point['detector_directory'])
                    if sha(detector/'nano.root')!=point_report['nano_sha256'] or sha(detector/'histograms.root')!=point_report['histogram_sha256']:
                        raise ValueError('Terminal point final payload changed: '+name)
                    if args.retire_intermediates:
                        global_constraint=f'Owner == "{account}" || AccountingGroupUser == "{account}"'
                        command=['condor_q','-global','-constraint',global_constraint,'-json','-attributes',ATTRIBUTES]
                        ads=native_query(command)
                        proof=dict(schema='shift-dark-photon-retirement-scheduler-proof-v1',account=account,
                            account_wide=True,complete=True,captured_epoch=time.time(),
                            query=dict(returncode=0,constraint=global_constraint,command=command,stderr=''),ads=ads)
                        directory=base/'retirement_queue_proofs';directory.mkdir(exist_ok=True)
                        proof_path=directory/f'{name}_{time.time_ns()}.json';proof_path.write_text(json.dumps(proof,indent=2)+'\n')
                        command=[sys.executable,str(base/'retirement_tools/retire_dark_photon_intermediates.py'),
                            '--workspace',str(workspace),'--gen-directory',point['gen_directory'],
                            '--detector-directory',point['detector_directory'],'--diagnostic',str(detector/'dark_photon_dimuons.json'),
                            '--scheduler-proof',str(proof_path),'--process-exceptions-proof',str(base/'retirement_tools/retirement_process_coverage.json')]
                        with (folder/'retirement_controller.log').open('a') as log:
                            if not (detector/'retirement_plan.json').exists():
                                subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=240)
                            subprocess.run([*command,'--execute'],stdout=log,stderr=subprocess.STDOUT,check=True,timeout=240)
                        record.update(retirement_complete=True,retirement_plan_sha256=sha(detector/'retirement_plan.json'),
                                      final_nano_sha256=sha(detector/'nano.root'),terminal_validated=True)
                    else:record['terminal_validated']=True
                    changed=True;save()
                except Exception as error:
                    record=state['points'].setdefault(point['point'],{})
                    record['terminal_validated']=False
                    record.setdefault('errors',[]).append(dict(epoch=time.time(),error=repr(error)))
                    save()
                    print('Point action held:',point['point'],repr(error),flush=True)
            done=sum(row.get('terminal_validated',False) for row in state['points'].values())
            state['completed_points']=done
            coverage=sorted(name for name,row in state['points'].items() if row.get('terminal_validated'))
            if coverage and state.get('plotted_points')!=coverage:
                output=base/'kinematics'/f'checkpoint_{time.time_ns()}'
                descriptors=[]
                for point in plan['points']:
                    if point['point'] not in coverage:continue
                    target=point['target_mean_lab_flight_m']
                    descriptors.append(dict(id=point['point'],mass_gev=point['mass_gev'],epsilon=point['epsilon'],
                        lifetime_label='prompt' if target is None else f'mean_lab_{target:g}m',
                        generated_events=point['events'],nano_path=str(Path(point['detector_directory'])/'nano.root')))
                selection=base/'kinematics'/f'selection_{time.time_ns()}.json';selection.parent.mkdir(exist_ok=True)
                selection.write_text(json.dumps(dict(samples=descriptors,source_plan_sha256=state['plan_sha256']),indent=2)+'\n')
                subprocess.run([sys.executable,str(base/'analysis_tools/plot_dark_photon_kinematics.py'),
                                '--plan',str(selection),'--output-dir',str(output)],check=True,timeout=240)
                receipt=read(output/'kinematics_receipt.json')
                if not receipt.get('complete') or sorted(row['id'] for row in receipt['samples'])!=coverage:
                    raise ValueError('Kinematic plot coverage differs from validated points')
                state.update(latest_kinematics=str(output),plotted_points=coverage,
                             plot_receipt_sha256=sha(output/'kinematics_receipt.json'));save()
            if done==len(plan['points']) and state.get('plotted_points')==coverage:
                if sha(Path(state['latest_kinematics'])/'kinematics_receipt.json')!=state['plot_receipt_sha256']:
                    raise ValueError('Completed plot receipt changed')
                state.update(complete=True,finished_epoch=time.time());save();print(json.dumps(state));return
            save()
        except Exception as error:
            state['errors'].append(dict(epoch=time.time(),error=repr(error)));save()
            print('Monitor holds the affected action:',repr(error),flush=True)
        time.sleep(args.poll_seconds)
    state.update(status='bounded monitor timeout; retained resumable state');save()


if __name__=='__main__':main()
