#!/usr/bin/env python3
# Code style: AI-code. AI-generated operational launcher for existing stage-budget arguments.
"""Run unchanged frozen DP workers with explicitly approved absolute wall budgets.

Only the common runner's existing absolute-stage-timeout arguments are added.
GEN, selected identities, probabilities, seeds, templates and transport stay
owned by the original sealed worker. A retry policy can restrict exact slices.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys


SCHEMA='shift-dp-operational-stage-budget-v1'
PRIMARY_AUTHORITY_ENV=('SHIFT_RECORDED_PRIMARY_AUTHORITY',
    'SHIFT_RECORDED_PRIMARY_AUTHORITY_SHA256','SHIFT_RECORDED_PRIMARY_GEN_SHA256')


def recorded_primary_environment(profile, gen_sha256):
    """Bind an exact particle authority to its local capsule and original GEN."""
    authority=profile.get('recorded_primary_authority')
    if authority is None:
        return {}
    if (not isinstance(gen_sha256,str) or len(gen_sha256)!=64
            or any(c not in '0123456789abcdef' for c in gen_sha256)):
        raise ValueError('Recorded-primary authority requires the original full GEN SHA')
    relative='CMSSW_17_0_0_pre4/src/SimG4Core/Generators/data/recorded_primary_authority_v4.json'
    if authority.get('relative_path')!=relative:
        raise ValueError('Recorded-primary authority must be the declared capsule member')
    release=Path(os.environ.get('CMSSW_BASE','')).resolve()
    if release.name!='CMSSW_17_0_0_pre4':
        raise ValueError('Recorded-primary authority requires the active private CMSSW capsule')
    manifest_path=release.parent/'manifest.json'
    if sha(manifest_path)!=profile['manifest']['sha256']:
        raise ValueError('Recorded-primary capsule manifest changed')
    manifest=json.loads(manifest_path.read_text());path=release.parent/relative
    member=manifest['files'].get(relative,{})
    if (path.resolve()!=path or member.get('sha256')!=authority.get('sha256')
            or sha(path)!=authority.get('sha256') or path.stat().st_size!=member.get('bytes')):
        raise ValueError('Recorded-primary particle authority differs from its capsule')
    data=json.loads(path.read_text())
    if (data.get('schema')!='shift-recorded-primary-particle-authority-v1'
            or data.get('complete') is not True or data.get('source_GEN_unchanged') is not True):
        raise ValueError('Recorded-primary particle authority is incomplete')
    return dict(zip(PRIMARY_AUTHORITY_ENV,(str(path),authority['sha256'],gen_sha256)))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_policy(path):
    path=Path(path).resolve();policy=json.loads(path.read_text())
    budgets=policy.get('stage_timeouts_seconds')
    directory=Path(policy.get('production_directory',''))
    if (policy.get('schema')!=SCHEMA or not directory.is_absolute()
            or not isinstance(budgets,list) or len(budgets)!=4
            or any(type(value) is not int or not 7200<=value<=43200 for value in budgets)
            or policy.get('absolute_stage_timeouts') is not True
            or sha(directory/'plan.json')!=policy.get('plan_sha256')):
        raise ValueError('Operational stage budget differs from its explicit frozen authority')
    for filename,key in [('chunk_worker.py','worker_sha256'),
            ('frozen/sources/run_dark_photon_detector.py','detector_sha256'),
            ('frozen/sources/run_shift_gen_to_nano.py','common_runner_sha256')]:
        if sha(directory/filename)!=policy.get(key):
            raise ValueError('Operational stage launcher changed an original worker source: '+filename)
    if policy.get('runtime_override') is not None:
        if sha(Path(__file__))!=policy.get('stage_budget_helper_sha256'):
            raise ValueError('Frozen stage-budget helper changed')
        from dark_photon_runtime_recovery import validate_override
        validate_override(policy)
    return policy


def common_command(command,policy):
    """Return an exact argv extension; leave all other subprocesses untouched."""
    if not isinstance(command,(list,tuple)) or len(command)<2 or Path(str(command[1])).name!='run_shift_gen_to_nano.py':
        return command
    if sha(command[1])!=policy['common_runner_sha256']:
        raise ValueError('Staged common runner differs from its unchanged qualified source')
    if '--absolute-stage-timeouts' in command or '--stage-timeouts' in command:
        raise ValueError('Refuse to replace another explicit stage-budget policy')
    return [*command,'--absolute-stage-timeouts','--stage-timeouts',
            ','.join(map(str,policy['stage_timeouts_seconds']))]


def worker_command(command,policy,policy_path):
    expected=Path(policy['production_directory'])/'frozen/sources/run_dark_photon_detector.py'
    if not isinstance(command,(list,tuple)) or len(command)<2 or str(command[1])!=str(expected):
        return command
    if sha(expected)!=policy['detector_sha256']:
        raise ValueError('Original detector entry changed')
    return [command[0],str(Path(__file__).resolve()),'--policy',str(policy_path),'--mode','detector','--',*command[1:]]


def execute(mode,policy_path,arguments):
    policy_path=Path(policy_path).resolve();policy=load_policy(policy_path)
    directory=Path(policy['production_directory'])
    original_run=subprocess.run
    if mode=='worker':
        if len(arguments)!=3:
            raise ValueError('Worker needs exact point, chunk and original production seal')
        point,index,seal=arguments;index=int(index)
        if sha(directory/'seal.json')!=seal:
            raise ValueError('Original production seal changed')
        allowed=policy.get('allowed_slices')
        if allowed!='all_planned' and dict(point=point,chunk=index) not in allowed:
            raise ValueError('Slice is outside this bounded reviewed retry')
        sys.path.insert(0,str(directory))
        worker_path=directory/'chunk_worker.py'
        if policy.get('runtime_override') is not None:
            from dark_photon_runtime_recovery import validate_override
            worker_path=validate_override(policy)
        spec=importlib.util.spec_from_file_location('_dp_original_budgeted_worker',worker_path)
        worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
        if policy.get('runtime_override') is not None:
            worker.B=directory
        profile=(json.loads(Path(policy['runtime_override']['profile']['path']).read_text())
                 if policy.get('runtime_override') else json.loads((directory/'plan.json').read_text()).get('detector_runtime',{}))
        authority_env={}
        if profile.get('recorded_primary_authority') is not None:
            inventory=json.loads((directory/'gen_inventory.json').read_text())
            authority_env=recorded_primary_environment(profile,inventory['points'][point]['gen_sha256'])
        previous_authority={name:os.environ.get(name) for name in PRIMARY_AUTHORITY_ENV}
        for name in PRIMARY_AUTHORITY_ENV:os.environ.pop(name,None)
        os.environ.update(authority_env)
        subprocess.run=lambda command,*args,**kwargs: original_run(worker_command(command,policy,policy_path),*args,**kwargs)
        try:
            return worker.run(point,index,seal)
        finally:
            subprocess.run=original_run
            for name,value in previous_authority.items():
                if value is None:os.environ.pop(name,None)
                else:os.environ[name]=value
    if not arguments or Path(arguments[0])!=directory/'frozen/sources/run_dark_photon_detector.py':
        raise ValueError('Detector dispatch must use its exact original script')
    sys.path.insert(0,str(directory/'frozen/sources'))
    subprocess.run=lambda command,*args,**kwargs: original_run(common_command(command,policy),*args,**kwargs)
    previous_argv=sys.argv
    sys.argv=list(arguments)
    try:
        runpy.run_path(arguments[0],run_name='__main__')
    finally:
        sys.argv=previous_argv
        subprocess.run=original_run


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--policy',type=Path,required=True)
    parser.add_argument('--mode',choices=('worker','detector'),required=True)
    parser.add_argument('arguments',nargs=argparse.REMAINDER)
    args=parser.parse_args();arguments=args.arguments[1:] if args.arguments[:1]==['--'] else args.arguments
    result=execute(args.mode,args.policy,arguments)
    if result is not None:
        print(json.dumps({key:result[key] for key in ('complete','point','chunk','events','nano_sha256')}))


if __name__=='__main__':main()
