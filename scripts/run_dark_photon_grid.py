#!/usr/bin/env python3
"""Run one frozen, fully reconstructed small-grid signal point.

No builds or submissions. Retirement is performed separately after the worker
is terminal, using exact validated outputs and fresh live-job/process checks.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def sha(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1048576),b''):value.update(block)
    return value.hexdigest()


def load(path):
    return json.loads(Path(path).read_text())


def validate_plan(plan, point):
    if plan.get('schema')!='shift-dark-photon-grid-plan-v1' or not plan.get('prepared'):
        raise ValueError('Require a prepared immutable physical grid')
    points=plan['points']
    if not 1<=len(points)<=30 or len({r['point'] for r in points})!=len(points):
        raise ValueError('Invalid grid point ownership')
    found=[r for r in points if r['point']==point]
    if len(found)!=1:raise ValueError('Unknown grid point')
    row=found[0]
    if not 1<=row['events']<=20 or row['events']!=row['detector_count']:
        raise ValueError('Kinematics pilot requires all 1..20 GEN events reconstructed')
    for category in ('sources','templates'):
        for record in plan['frozen_dependencies'][category].values():
            if sha(record['frozen_copy'])!=record['sha256']:
                raise ValueError('Frozen dependency changed: '+record['frozen_copy'])
    record=plan['frozen_dependencies']['references']
    if sha(record['frozen_copy'])!=record['sha256'] or sha(row['signal_contract'])!=row['signal_contract_sha256']:
        raise ValueError('Frozen reference/point contract changed')
    return row


def verify_runtime(path):
    value=load(path)
    for name,record in value['files'].items():
        if sha(name)!=record['sha256']:
            raise ValueError('Shared runtime changed: '+name)
    return sha(path)


def validate_gen(directory,row):
    manifest=load(directory/'manifest.json');audit=load(directory/'validation.json');contract=load(directory/'contract.json')
    if not manifest['status'].startswith('GEN runtime audit passed') or not audit.get('runtime_validated'):
        raise ValueError('Incomplete GEN runtime audit')
    if not (audit['events']==row['events']==contract['requested_events'] and
            contract['mass_gev']==row['mass_gev'] and contract['epsilon']==row['epsilon'] and
            contract['seed']==row['seed'] and sha(directory/'gen.root')==manifest['output_sha256']):
        raise ValueError('GEN source differs from frozen point')
    if audit['proposal_ledger']['n_accepted']!=row['events']:
        raise ValueError('GEN technical trial ledger lost exposure')
    if contract!=load(row['signal_contract']):
        raise ValueError('Actual GEN physics/settings contract differs from frozen point')
    if audit['event_ids']!=[[row['run_number'],1,i+1] for i in range(row['events'])]:
        raise ValueError('GEN identities differ from the frozen run namespace')
    frozen=Path(row['gen_argv'][1]).parent
    for name,digest in manifest['source_sha256'].items():
        if sha(directory/name)!=digest or sha(frozen/name)!=digest:
            raise ValueError('Copied GEN source differs from frozen generator: '+name)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan',type=Path,required=True)
    parser.add_argument('--point',required=True)
    parser.add_argument('--analysis-tools',type=Path,required=True)
    parser.add_argument('--workspace',type=Path,required=True)
    parser.add_argument('--minimum-free-mb',type=int,default=300)
    args=parser.parse_args()
    workspace=args.workspace.resolve();plan_path=args.plan.resolve();tools=args.analysis_tools.resolve()
    plan=load(plan_path);row=validate_plan(plan,args.point)
    runtime_path=plan_path.parent/'runtime_fingerprint.json'
    runtime_hash=verify_runtime(runtime_path)
    tool_manifest=load(tools/'manifest.json')
    for name,record in tool_manifest.items():
        if sha(tools/name)!=record['sha256']:raise ValueError('Frozen analysis tool changed: '+name)
    gen=Path(row['gen_directory']);detector=Path(row['detector_directory']);point=gen.parent
    path=point/'point_report.json';point.mkdir(parents=True,exist_ok=True)
    if path.exists() and load(path).get('complete'):
        previous=load(path)
        if not (previous['plan_sha256']==sha(plan_path) and previous['runtime_fingerprint_sha256']==runtime_hash
                and previous['analysis_tool_manifest_sha256']==sha(tools/'manifest.json')
                and previous['runner_sha256']==sha(__file__)
                and previous['physical_mass_gev']==row['mass_gev'] and previous['physical_epsilon']==row['epsilon']
                and previous['generated_events']==previous['detector_events']==row['events']):
            raise ValueError('Completed point belongs to another frozen contract/runtime')
        if (sha(detector/'nano.root')!=previous['nano_sha256'] or
                sha(detector/'histograms.root')!=previous['histogram_sha256'] or
                sha(detector/'dark_photon_dimuons.json')!=previous['diagnostic_sha256']):
            raise ValueError('Completed point final outputs changed')
        for name,digest in previous['receipt_sha256'].items():
            if sha(name)!=digest:raise ValueError('Completed point receipt changed: '+name)
        print(json.dumps(dict(complete=True,reused=True,point=args.point)))
        return
    quota=subprocess.check_output(['fs','listquota','-path',str(workspace)],text=True,timeout=30).splitlines()[1].split()
    free_mb=(int(quota[1])-int(quota[2]))/1024
    if free_mb<args.minimum_free_mb+50:
        raise ValueError(f'Insufficient starting AFS margin: {free_mb:.1f} MB')
    report=dict(schema='shift-dark-photon-small-grid-point-v1',complete=False,point=args.point,
                physical_mass_gev=row['mass_gev'],physical_epsilon=row['epsilon'],
                generated_events=row['events'],detector_events=row['detector_count'],
                plan_sha256=sha(plan_path),runtime_fingerprint_sha256=runtime_hash,
                analysis_tool_manifest_sha256=sha(tools/'manifest.json'),runner_sha256=sha(__file__),
                physics_valid=False,normalization_ready=False,phases={},started_epoch=time.time())
    def save():path.write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    def run(name,command):
        report['phase']=name;save()
        with (point/(name+'_runner.log')).open('w') as log:
            subprocess.run(command,check=True,stdout=log,stderr=subprocess.STDOUT,timeout=10800)
        report['phases'][name]=dict(complete=True,log_sha256=sha(point/(name+'_runner.log')));save()
    try:
        save()
        if not gen.exists():run('gen',row['gen_argv'])
        validate_gen(gen,row)
        if not detector.exists():run('detector',row['detector_argv'])
        signal,common=[load(detector/name) for name in ('signal_report.json','report.json')]
        audit=load(gen/'validation.json');manifest=load(gen/'manifest.json')
        if not (signal.get('complete') and common.get('complete')
                and signal['requested_event_ids']==audit['event_ids']
                and common['source_gen_sha256']==manifest['output_sha256']
                and common['source_receipt_sha256']==sha(gen/'manifest.json')
                and signal['source_manifest_sha256']==sha(gen/'manifest.json')
                and signal['signal_contract']==load(gen/'contract.json')
                and common['nano_sha256']==sha(detector/'nano.root')):
            raise ValueError('Incomplete/full-coverage detector replay')
        hist=detector/'histogram_report.json'
        if not hist.exists():
            run('histogram',[sys.executable,str(tools/'run_dark_photon_histograms.py'),str(detector),
                '--freeze',str(workspace/'validation/bsm_darkphoton_20261009/histogram_freeze'),
                '--runtime',str(workspace/'validation/histogram_hold_20261007/tea_runtime'),
                '--frozen-bin',str(workspace/'validation/histogram_hold_20261007/worker_payload/bin'),
                '--source-manifest',str(workspace/'validation/histogram_stall_20261007/local_runtime_source_checksums.json')])
        hist_report=load(hist)
        if not (hist_report.get('complete') and hist_report['input_event_ids']==signal['requested_event_ids']
                and hist_report['input_sha256']==common['nano_sha256']==sha(detector/'nano.root')
                and hist_report['histogram_sha256']==sha(detector/'histograms.root')
                and hist_report['native_event_weight_sum']==row['events']
                and hist_report['source_signal_report_sha256']==sha(detector/'signal_report.json')
                and hist_report['source_chain_report_sha256']==sha(detector/'report.json')):
            raise ValueError('Incomplete or stale frozen histogram replay')
        diagnostic=detector/'dark_photon_dimuons.json'
        if not diagnostic.exists():
            run('diagnostic',[sys.executable,str(tools/'audit_dark_photon_dimuons.py'),
                '--input',str(detector/'nano.root'),'--output',str(diagnostic),'--max-events',str(row['events'])])
        diag=load(diagnostic)
        if not (diag.get('complete') and diag['summary']['events']==row['events']
                and diag['input_sha256']==common['nano_sha256']
                and [r['event_id'] for r in diag['events']]==signal['requested_event_ids']
                and [r['native_weight'] for r in diag['events']]==[1.]*row['events']):
            raise ValueError('Incomplete signal-specific diagnostics')
        verify_runtime(runtime_path)
        report.update(complete=True,phase='completed',finished_epoch=time.time(),
                      nano_sha256=sha(detector/'nano.root'),histogram_sha256=sha(detector/'histograms.root'),
                      diagnostic_sha256=sha(diagnostic),signal_diagnostic_summary=diag['summary'],
                      receipt_sha256={str(p):sha(p) for p in
                        [gen/'manifest.json',gen/'contract.json',gen/'validation.json',detector/'source.json',
                         detector/'signal_report.json',detector/'report.json',hist,diagnostic]},
                      intermediate_retirement_pending=True)
    except Exception as error:
        report['error']=repr(error)
        raise
    finally:save()
    print(json.dumps(dict(complete=True,point=args.point,events=row['events'],nano=str(detector/'nano.root'))))


if __name__=='__main__':main()
