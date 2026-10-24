#!/usr/bin/env python3
# Code style: AI-code. AI-generated immutable runtime adapter for reviewed detector retries.
"""Prepare a runtime-only view of an unchanged sealed detector population.

The clone changes receipt provenance only. Original source/GEN/selection/seeds,
worker validation, output namespace and detector arguments remain untouched.
"""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import time

AUTHORITY_SCHEMA='shift-dp-runtime-recovery-authority-v1'
QUALIFICATION_SCHEMA='shift-dp-recorded-primary-precision-qualification-v1'
PROVENANCE_MARKER="'private_detector_runtime_sha256':plan['detector_runtime']['runtime_sha256']"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def pinned(pin):
    path=Path(pin.get('path',''))
    if not path.is_absolute() or '..' in path.parts or sha(path)!=pin.get('sha256'):
        raise ValueError('Runtime recovery authority file changed')
    return read(path)


def provenance(override):
    return json.loads(json.dumps({key:override[key] for key in ('authority','profile','qualification')},sort_keys=True))


def clone_text(original, runtime_sha256, override):
    if original.count(PROVENANCE_MARKER)!=1:
        raise ValueError('Runtime adapter requires the exact original receipt provenance expression')
    replacement=("'private_detector_runtime_sha256':"+repr(runtime_sha256)+
        ",'original_private_detector_runtime_sha256':plan['detector_runtime']['runtime_sha256']"+
        ",'runtime_override':"+repr(provenance(override)))
    return original.replace(PROVENANCE_MARKER,replacement)


def validate_full_gen_preflight(production,profile,preflight):
    production=Path(production)
    inventory=read(production/'gen_inventory.json')
    rows=read(production/'plan.json')['points']
    points={row['point'] for row in rows}
    if (preflight.get('complete') is not True or inventory.get('complete') is not True
            or preflight.get('original_plan_sha256')!=sha(production/'plan.json')
            or preflight.get('original_seal_sha256')!=sha(production/'seal.json')
            or preflight.get('replacement_runtime_sha256')!=profile['runtime_sha256']
            or set(preflight.get('points',{}))!=points):
        raise ValueError('Runtime preflight does not cover this complete original GEN cohort')
    for key in ('converter_source_sha256','configuration_sha256'):
        digest=preflight.get(key,'')
        if not isinstance(digest,str) or len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):
            raise ValueError('Runtime preflight lacks native converter/configuration fingerprints')
    for point in points:
        parent=inventory['points'][point];checked=preflight['points'][point]
        identities=parent['event_ids']
        digest=hashlib.sha256(json.dumps(identities,separators=(',',':')).encode()).hexdigest()
        if (len(identities)!=parent['events'] or checked.get('gen_sha256')!=parent['gen_sha256']
                or checked.get('event_ids_sha256')!=digest
                or type(checked.get('full_count')) is not int or checked['full_count']!=parent['events']
                or type(checked.get('converted_count')) is not int or checked['converted_count']!=parent['events']):
            raise ValueError('Runtime preflight changed or dropped full GEN identities: '+point)


def validate_override(policy):
    """Check qualified mapping and exact deterministic clone before any execution."""
    from dark_photon_detector_runtime import validate_profile, CORE_SOURCES
    production=Path(policy['production_directory']);override=policy['runtime_override']
    if sha(Path(__file__))!=policy.get('runtime_adapter_sha256'):
        raise ValueError('Frozen runtime adapter helper changed')
    authority=pinned(override['authority']);profile=pinned(override['profile']);qualification=pinned(override['qualification'])
    required=('class_layout_unchanged','source_GEN_unchanged','detector_electronics_unchanged','core_sources_unchanged')
    if (authority.get('schema')!=AUTHORITY_SCHEMA or authority.get('complete') is not True
            or authority.get('full_chain_passed') is not True
            or any(authority.get(key) is not True for key in required)
            or authority.get('new_profile')!=override['profile']
            or authority.get('qualification')!=override['qualification']
            or qualification.get('schema')!=QUALIFICATION_SCHEMA or qualification.get('complete') is not True
            or any(qualification.get(key) is not True for key in required)):
        raise ValueError('Runtime replacement lacks its complete unchanged-physics qualification')
    old_profile=pinned(authority['old_profile']);original_profile=read(production/'frozen/detector_runtime.json')
    if (old_profile!=original_profile or original_profile!=read(production/'plan.json')['detector_runtime']
            or sha(production/'frozen/detector_runtime.json')!=override.get('original_profile_sha256')
            or sha(production/'seal.json')!=override.get('original_seal_sha256')):
        raise ValueError('Runtime replacement differs from the original sealed profile or plan')
    original_files=read(production/'seal.json')['files']
    required=['chunk_worker.py','plan.json','gen_inventory.json','frozen/detector_runtime.json','dark_photon_detector_runtime.py']
    required += ['frozen/sources/'+name for name in CORE_SOURCES]
    for name in required:
        path=production/name
        if original_files.get(str(path))!=sha(path):
            raise ValueError('Original sealed worker/runtime dependency changed: '+name)
    mappings=qualification.get('profiles',[])
    if not any(row.get('original_runtime_sha256')==old_profile['runtime_sha256']
            and row.get('replacement_profile')==override['profile'] for row in mappings):
        raise ValueError('Runtime qualification does not cover this exact old/new capsule pair')
    chains=qualification.get('weighted_fullchain')
    if not isinstance(chains,list) or not chains or any(pinned(pin).get('complete') is not True for pin in chains):
        raise ValueError('Runtime replacement requires pinned completed weighted full-chain evidence')
    validate_profile(old_profile);manifest=validate_profile(profile)
    for name in CORE_SOURCES:
        if manifest['files']['workflow_helpers/'+name]['sha256']!=sha(production/'frozen/sources'/name):
            raise ValueError('Replacement capsule changes a frozen core source: '+name)
    validate_full_gen_preflight(production,profile,pinned(authority['production_preflight']))
    clone=Path(override['worker_clone']['path'])
    if not clone.is_absolute() or sha(clone)!=override['worker_clone']['sha256']:
        raise ValueError('Runtime worker clone changed')
    expected=clone_text((production/'chunk_worker.py').read_text(),profile['runtime_sha256'],override)
    if clone.read_text()!=expected:
        raise ValueError('Runtime worker clone changes more than explicit receipt provenance')
    return clone


def prepare_view(production, authority_pin, output, *, allowed_slices='all_planned',
                 stage_timeouts=(43200,7200,14400,14400), wrapper_template=None):
    """Create a new view; never edit the original deployment or existing jobs."""
    from dark_photon_stage_budget import SCHEMA, load_policy
    import dark_photon_stage_budget as budget_helper
    production=Path(production).resolve();output=Path(output).resolve()
    if output.exists() or output.is_relative_to(production):
        raise ValueError('Runtime recovery needs a new directory outside its original deployment')
    authority=pinned(authority_pin);profile=pinned(authority['new_profile'])
    plan=read(production/'plan.json')
    if allowed_slices!='all_planned':
        planned={(row['point'],chunk['chunk']) for row in plan['points'] for chunk in row['chunks']}
        if (not isinstance(allowed_slices,list) or not allowed_slices
                or any(set(item)!= {'point','chunk'} or type(item['chunk']) is not int
                    or (item['point'],item['chunk']) not in planned for item in allowed_slices)
                or len({(item['point'],item['chunk']) for item in allowed_slices})!=len(allowed_slices)):
            raise ValueError('Runtime recovery slices must be unique exact original chunks')
    output.mkdir(parents=True)
    override=dict(authority=authority_pin,profile=authority['new_profile'],qualification=authority['qualification'],
        original_profile_sha256=sha(production/'frozen/detector_runtime.json'),
        original_seal_sha256=sha(production/'seal.json'))
    clone=output/'worker_clone.py';clone.write_text(clone_text((production/'chunk_worker.py').read_text(),profile['runtime_sha256'],override))
    override['worker_clone']=dict(path=str(clone),sha256=sha(clone))
    policy=dict(schema=SCHEMA,production_directory=str(production),plan_sha256=sha(production/'plan.json'),
        worker_sha256=sha(production/'chunk_worker.py'),detector_sha256=sha(production/'frozen/sources/run_dark_photon_detector.py'),
        common_runner_sha256=sha(production/'frozen/sources/run_shift_gen_to_nano.py'),
        stage_timeouts_seconds=list(stage_timeouts),absolute_stage_timeouts=True,
        allowed_slices=allowed_slices,runtime_override=override,
        runtime_adapter_sha256=sha(Path(__file__)),stage_budget_helper_sha256=sha(Path(budget_helper.__file__)))
    policy_path=output/'stage_budget_policy.json';policy_path.write_text(json.dumps(policy,indent=2,sort_keys=True)+'\n')
    for source in [Path(__file__),Path(__file__).with_name('dark_photon_stage_budget.py'),production/'dark_photon_detector_runtime.py']:
        shutil.copy2(source,output/source.name)
    # Validate before rendering/submitting a worker. The old seal remains authoritative.
    load_policy(policy_path)
    template_pin=wrapper_template or dict(path=str(production/'detector_worker.sh'),sha256=sha(production/'detector_worker.sh'))
    template=Path(template_pin['path'])
    if not template.is_absolute() or sha(template)!=template_pin['sha256']:
        raise ValueError('Qualified runtime wrapper template changed')
    lines=template.read_text().splitlines(True)
    entries=[index for index,line in enumerate(lines) if line.startswith('/usr/bin/env ')
        and ' --profile ' in line and ' --entry ' in line]
    if len(entries)!=1:
        raise ValueError('Runtime recovery requires one standalone private-runtime entry')
    index=entries[0];prefix=lines[index].split(' --profile ',1)[0]
    if str(production/'dark_photon_detector_runtime.py') not in prefix:
        raise ValueError('Runtime recovery must retain its original qualified bootstrap')
    invocation=(prefix+' --profile '+shlex.quote(override['profile']['path'])+' --entry '+
        shlex.quote(str(output/'dark_photon_stage_budget.py'))+' -- --policy '+
        shlex.quote(str(policy_path))+' --mode worker -- "$@"\n')
    checks=[(str(path),sha(path)) for path in output.iterdir() if path.is_file()]
    checks.extend((pin['path'],pin['sha256']) for pin in [authority_pin,override['profile'],override['qualification']])
    checks.append((str(production/'dark_photon_detector_runtime.py'),sha(production/'dark_photon_detector_runtime.py')))
    check='/usr/bin/env -u PYTHONHOME -u PYTHONPATH -u LD_LIBRARY_PATH -u LD_PRELOAD /usr/bin/python3 -c '+shlex.quote(
        'import hashlib;from pathlib import Path;assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in '+repr(checks)+')')+'\n'
    failure_id='export BSM_FAILURE_ID="${BSM_FAILURE_ID:?}_runtime_'+authority_pin['sha256'][:12]+'"\n'
    lines[index:index+1]=[check,invocation]
    traps=[i for i,line in enumerate(lines) if line.startswith('trap ') and ' EXIT' in line]
    if len(traps)!=1:
        raise ValueError('Runtime recovery requires its original unique failure-preservation trap')
    lines.insert(traps[0],failure_id)
    wrapper=output/'detector_worker.sh';wrapper.write_text(''.join(lines));wrapper.chmod(0o750)
    subprocess.run(['/bin/bash','-n',str(wrapper)],check=True)
    seal=dict(schema='shift-dp-runtime-recovery-view-v1',original_production=str(production),
        original_seal_sha256=sha(production/'seal.json'),authority=authority_pin,wrapper_template=template_pin,
        files={str(path):sha(path) for path in output.iterdir() if path.is_file()})
    (output/'seal.json').write_text(json.dumps(seal,indent=2,sort_keys=True)+'\n')
    return dict(directory=str(output),wrapper=str(wrapper),policy=str(policy_path),seal_sha256=sha(output/'seal.json'))



def immutable_json(path,value):
    path=Path(path)
    if path.exists():
        if read(path)!=value:
            raise ValueError('Existing operational runtime preparation changed: '+str(path))
        return
    with path.open('x') as output:
        output.write(json.dumps(value,indent=2,sort_keys=True)+'\n')


def future_preparation(preparation,setting,operations):
    """Change only a NEW detector plan's runtime; keep GEN and admission frozen."""
    preparation=Path(preparation)
    if setting is None:
        return preparation
    from dark_photon_detector_runtime import validate_profile, CORE_SOURCES
    original=read(preparation);old=original['detector_runtime']
    profile=pinned(setting['profile']);qualification=pinned(setting['qualification'])
    flags=('class_layout_unchanged','source_GEN_unchanged','detector_electronics_unchanged','core_sources_unchanged')
    if (qualification.get('schema')!=QUALIFICATION_SCHEMA or qualification.get('complete') is not True
            or any(qualification.get(key) is not True for key in flags)
            or not any(row.get('original_runtime_sha256')==old['runtime_sha256']
                and row.get('replacement_profile')==setting['profile'] for row in qualification.get('profiles',[]))):
        raise ValueError('Future detector runtime lacks its exact qualified old/new mapping')
    chains=qualification.get('weighted_fullchain')
    if not isinstance(chains,list) or not chains or any(pinned(pin).get('complete') is not True for pin in chains):
        raise ValueError('Future detector runtime needs completed weighted full-chain evidence')
    old_manifest=validate_profile(old);new_manifest=validate_profile(profile)
    for name in CORE_SOURCES:
        key='workflow_helpers/'+name
        if old_manifest['files'][key]['sha256']!=new_manifest['files'][key]['sha256']:
            raise ValueError('Future detector runtime changes an original core source')
    operations=Path(operations);operations.mkdir(parents=True,exist_ok=True)
    changed=copy.deepcopy(original);changed['detector_runtime']=profile
    output=operations/'runtime_preparation.json';immutable_json(output,changed)
    intent=dict(schema='shift-future-detector-runtime-intent-v1',
        original_preparation=dict(path=str(preparation),sha256=sha(preparation)),
        runtime_preparation=dict(path=str(output),sha256=sha(output)),
        original_runtime_sha256=old['runtime_sha256'],profile=setting['profile'],qualification=setting['qualification'])
    immutable_json(operations/'future_detector_runtime.json',intent)
    return output


def future_preflight(production,setting,operations,durable,config):
    """Complete one detached full-GEN check before admitting a new detector DAG."""
    if setting is None:
        return
    production=Path(production);operations=Path(operations)
    intent_path=operations/'future_detector_runtime.json'
    if not intent_path.exists():
        if (production/'submission.json').exists():
            return  # An older submitted cohort stays under its original frozen runtime.
        raise ValueError('Existing unsubmitted detector production needs a reviewed runtime intent')
    intent=read(intent_path)
    if intent.get('profile')!=setting['profile'] or intent.get('qualification')!=setting['qualification']:
        raise ValueError('Future runtime intent differs from its frozen controller')
    pinned(intent['original_preparation']);prepared=pinned(intent['runtime_preparation'])
    profile=pinned(setting['profile'])
    if (prepared['detector_runtime']!=profile or read(production/'plan.json')['detector_runtime']!=profile):
        raise ValueError('Prepared detector production does not use its qualified future runtime')
    directory=operations/'runtime_preflight';preparation_path=directory/'preparation.json'
    if not preparation_path.exists():
        helper=Path(__file__).with_name('prepare_dark_photon_kinematics_preflight.py')
        spec=importlib.util.spec_from_file_location('_dp_qualified_future_preflight',helper)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        module.prepare(production,setting['profile'],directory)
    prepared_scan=read(preparation_path)
    for name in ('config','worker','submit'):
        pin=prepared_scan[name]
        if sha(pin['path'])!=pin['sha256']:
            raise ValueError('Frozen future preflight entry changed')
    receipt=durable.submission(config,directory,Path(prepared_scan['submit']['path']))
    result_path=Path(prepared_scan['receipt'])
    ready_path=operations/'future_detector_ready.json'
    if ready_path.exists():
        ready=read(ready_path);native=ready.get('terminal_native_ad',{})
        if (ready.get('complete') is not True or ready.get('profile')!=setting['profile']
                or ready.get('production_plan_sha256')!=sha(production/'plan.json')
                or ready.get('production_seal_sha256')!=sha(production/'seal.json')
                or ready.get('production_preflight')!=dict(path=str(result_path),sha256=sha(result_path))
                or native.get('ClusterId')!=receipt['cluster'] or native.get('ProcId')!=0
                or native.get('Iwd')!=str(directory) or native.get('JobStatus')!=4
                or native.get('ExitCode')!=0 or native.get('ExitBySignal') is not False):
            raise ValueError('Earlier successful future preflight authority changed')
        if durable.jobs(config,'ClusterId == '+str(receipt['cluster'])):
            raise RuntimeError('A verified completed preflight unexpectedly became live again')
        validate_full_gen_preflight(production,profile,read(result_path))
        return
    while True:
        report=None
        if result_path.exists():
            try:
                report=read(result_path)
            except json.JSONDecodeError:
                pass  # A worker may still be finishing its first receipt write.
            if report is not None and report.get('complete') is not True:
                raise RuntimeError('Full-GEN converter preflight failed; preserve the original GEN')
        queued=durable.jobs(config,'ClusterId == '+str(receipt['cluster']))
        if any(row['JobStatus'] in (3,5) for row in queued):
            raise RuntimeError('Future converter preflight is removed or held; preserve its attempt')
        if not queued:
            history=durable.parse_ads(durable.run_condor(['/usr/bin/condor_history',*durable.scheduler_arguments(config),
                str(receipt['cluster'])+'.0','-limit','1','-json'],timeout=45))
            if (len(history)!=1 or history[0].get('ClusterId')!=receipt['cluster'] or history[0].get('ProcId')!=0
                    or history[0].get('Iwd')!=str(directory) or history[0].get('JobStatus')!=4
                    or history[0].get('ExitCode')!=0 or history[0].get('ExitBySignal') is not False or report is None):
                raise RuntimeError('Future converter preflight lacks an exact successful native closure')
            validate_full_gen_preflight(production,profile,report)
            terminal={key:history[0][key] for key in ('ClusterId','ProcId','Iwd','JobStatus','ExitCode','ExitBySignal')}
            immutable_json(operations/'future_detector_ready.json',dict(complete=True,profile=setting['profile'],
                production_plan_sha256=sha(production/'plan.json'),production_seal_sha256=sha(production/'seal.json'),
                production_preflight=dict(path=str(result_path),sha256=sha(result_path)),terminal_native_ad=terminal))
            return
        durable.save(directory/'handoff_wait.json',dict(checked_utc=datetime.now(timezone.utc).isoformat(),
            cluster=receipt['cluster'],phase='full_GEN_native_converter_preflight'))
        time.sleep(30)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--production',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--authority',type=Path,required=True);parser.add_argument('--authority-sha256',required=True)
    parser.add_argument('--wrapper-template',type=Path);parser.add_argument('--wrapper-template-sha256')
    args=parser.parse_args();template=None
    if args.wrapper_template is not None:
        template=dict(path=str(args.wrapper_template.resolve()),sha256=args.wrapper_template_sha256)
    print(json.dumps(prepare_view(args.production,dict(path=str(args.authority.resolve()),sha256=args.authority_sha256),
        args.output,wrapper_template=template)))


if __name__=='__main__':main()
