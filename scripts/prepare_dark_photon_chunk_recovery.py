#!/usr/bin/env python3
# Code style: AI-code. AI-generated audited promotion of retained detector chunks.
"""Prepare or run a recovery view of an existing full GEN population.

No GEN or detector event is generated here. A completed bounded Nano replay
is accepted only after fresh audits of its original GEN and all detector tiers.
Original failed receipts remain unchanged. The standard grouped finalizer
consumes the new receipt and the untouched successful original receipts.
"""
import argparse
import copy
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1048576), b''):
            value.update(block)
    return value.hexdigest()


def write(path, value):
    with Path(path).open('x') as output:
        json.dump(value, output, indent=2, sort_keys=True, allow_nan=False)
        output.write('\n')


def checked_extract(archive, member_name, destination, expected):
    """Extract one pinned regular member; never trust archive output paths."""
    if Path(member_name).is_absolute() or '..' in Path(member_name).parts:
        raise ValueError('Unsafe retained archive member')
    member = archive.getmember(member_name)
    if not member.isfile() or member.size != expected['bytes']:
        raise ValueError('Retained ROOT member type or size changed')
    with archive.extractfile(member) as source, Path(destination).open('xb') as output:
        shutil.copyfileobj(source, output, 1048576)
    if sha(destination) != expected['sha256']:
        raise ValueError('Retained ROOT member checksum changed')


def checked_replay_dependency(path, authority, request):
    """Preserve an exact recorded scheduler-only repair, never a physics edit."""
    if path.stat().st_size==authority['bytes'] and sha(path)==authority['sha256']:
        return sha(path)
    repairs=request.get('reviewed_replay_submit_changes',{})
    repair=repairs.get(str(path))
    if path.name!='replay.sub' or not repair or sha(path)!=repair.get('current_sha256'):
        raise ValueError('Replay deployment changed: '+str(path))
    removed='requirements = (TARGET.OpSysMajorVer == 9)\n'
    previous=path.read_text().replace('+JobFlavour',removed+'+JobFlavour',1).encode()
    if (repair.get('removed_line')!=removed or len(previous)!=authority['bytes'] or
            hashlib.sha256(previous).hexdigest()!=authority['sha256']):
        raise ValueError('Replay submit repair changes more than the recorded obsolete requirement')
    return sha(path)


def validate_replay(item, replay, original_row, parent):
    chunk, = [chunk for chunk in original_row['chunks'] if chunk['chunk'] == item['chunk']]
    expected = parent['event_ids'][chunk['skip']:chunk['skip']+chunk['count']]
    if (item['failed_stage'] != 'NANO' or item['expected_event_ids'] != expected or
            replay.get('complete') is not True or replay.get('diagnostic_only') is not True or
            replay.get('point') != original_row['point'] or replay.get('chunk') != chunk['chunk'] or
            replay.get('event_ids') != expected or replay.get('detector_job') != chunk['job'] or
            replay.get('nano_seed') != 230000000+chunk['job']*100+40 or
            replay.get('source_failure_archive') != item['root_failure_archive'] or
            replay.get('input_audit', {}).get('event_ids') != expected or
            replay.get('nano_audit', {}).get('event_ids') != expected or
            replay['nano_audit'].get('sumw') != chunk['count'] or
            len(expected) != chunk['count'] or len(set(map(tuple, expected))) != len(expected)):
        raise ValueError('Replay differs from the exact original slice, seed or complete audit')
    return chunk


def validate_storage(request):
    protection=request.get('storage_protection',{})
    if (not protection or any(type(value) is not int or value<0 for value in protection.values()) or
            request.get('protected_bytes')!=sum(protection.values()) or
            protection.get('free_floor_bytes',0)<90000000000 or
            type(request.get('recovery_forecast_bytes')) is not int or
            not 0<request['recovery_forecast_bytes']<=500000000 or
            type(request.get('audit_scratch_limit_bytes')) is not int or
            not 0<request['audit_scratch_limit_bytes']<=100000000):
        raise ValueError('Recovery storage needs exact protected sums and bounded publication/scratch')


def validate_replay_paths(paths):
    names=[str(Path(path).resolve()) for path in paths]
    if not 1<=len(names)<=8 or len(set(names))!=len(names):
        raise ValueError('Require distinct bounded replay input files')
    return names


def guard_publication(worker, scoring=False):
    """Reserve every new campaign byte before either standard publisher writes.

    A failed write keeps its reservation. This small shared ledger only bounds
    this recovery's output; normal SHA readbacks and success receipts remain
    owned by the qualified publishers.
    """
    plan=worker.read(worker.B/'plan.json')
    limit=plan['maximum_recovery_publication_bytes']

    def reserve(local,remote):
        if not any(remote.startswith(row['final_output_base']+'/') for row in plan['points']):
            raise ValueError('Recovery publication leaves its distinct campaign namespace')
        value=dict(bytes=Path(local).stat().st_size,sha256=sha(local))
        path=worker.B/'publication_reservations.json'
        with (worker.B/'publication_reservations.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            state=read(path) if path.exists() else dict(plan_sha256=sha(worker.B/'plan.json'),files={})
            if state['plan_sha256']!=sha(worker.B/'plan.json'):
                raise ValueError('Recovery publication budget belongs to another plan')
            previous=state['files'].get(remote)
            if previous is not None and previous!=value:
                raise ValueError('Recovery publication reservation changed its payload')
            state['files'][remote]=value
            if sum(item['bytes'] for item in state['files'].values())>limit:
                raise ValueError('Recovery exceeds its bounded total publication allowance')
            pending=path.with_suffix('.json.next')
            pending.write_text(json.dumps(state,indent=2,sort_keys=True)+'\n')
            os.replace(pending,path)

    original=worker.publish
    def publish(local,remote,work):
        reserve(local,remote)
        return original(local,remote,work)
    worker.publish=publish
    if scoring:
        sys.path.insert(0,str(worker.B/'frozen/scoring/workflow/scripts'))
        import run_merged_dimuon_bdt as scorer
        original_score=scorer.io.put_remote
        def put(local,remote):
            reserve(local,remote)
            return original_score(local,remote)
        scorer.io.put_remote=put


def prepare(request_path, output):
    request = read(request_path)
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError('Refuse to overwrite an existing recovery deployment')
    if request.get('schema') != 'shift-dark-photon-chunk-recovery-request-v1':
        raise ValueError('Unknown recovery request')
    validate_storage(request)
    validate_replay_paths(request['replays'])
    original = Path(request['original_production'])
    here = Path(__file__).resolve().parent
    old_plan, old_seal = read(original/'plan.json'), read(original/'seal.json')
    if sha(original/'seal.json') != request['original_seal_sha256']:
        raise ValueError('Original production seal changed')
    for path, checksum in old_seal['files'].items():
        if sha(path) != checksum:
            raise ValueError('Original frozen dependency changed: '+path)
    campaign = request['campaign']
    if not re.fullmatch(r'20261016-[A-Za-z0-9_-]+', campaign) or campaign == old_plan['campaign']:
        raise ValueError('Recovery requires a distinct explicit dated campaign')
    inventory = read(original/'gen_inventory.json')
    failed = read(request['failed_inventory'])['chunks']
    specification=importlib.util.spec_from_file_location('_original_chunk_recovery',original/'chunk_worker.py')
    original_worker=importlib.util.module_from_spec(specification)
    specification.loader.exec_module(original_worker)
    selected, inputs = [], {}
    replay_dependencies={}
    if not 1 <= len(request['replays']) <= 8:
        raise ValueError('Require a bounded explicit replay list')
    for replay_path in request['replays']:
        replay = read(replay_path)
        row, = [row for row in old_plan['points'] if row['point'] == replay['point']]
        item, = [item for item in failed if item['point'] == row['point'] and item['chunk'] == replay['chunk']]
        validate_replay(item, replay, row, inventory['points'][row['point']])
        if replay.get('runtime_fingerprint_sha256')!=sha(original/'frozen/runtime_fingerprint.json'):
            raise ValueError('Replay used a different detector runtime')
        replay_root=Path(replay_path).resolve().parent
        proof=replay_root/'deployment_checksums.json'
        replay_dependencies[str(proof)]=sha(proof)
        for name,authority in read(proof).items():
            path=replay_root/name
            replay_dependencies[str(path)]=checked_replay_dependency(path,authority,request)
        if row['point'] in inputs:
            raise ValueError('This bounded recovery requires one missing chunk per point')
        inputs[row['point']] = dict(item=item, replay=replay, replay_path=str(Path(replay_path).resolve()))
        selected.append(copy.deepcopy(row))
    output.mkdir(parents=True)
    for name in ('logs', 'failures', 'statuses', 'completed_points', 'tools'):
        (output/name).mkdir()
    # Reuse the qualified deployment rather than reconstructing its physics.
    for name in ('canary', 'frozen'):
        shutil.copytree(original/name, output/name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for name in ('chunk_worker.py', 'qualified_finalizer.py', 'finalize_point.py', 'collect_inputs.py',
                 'dark_photon_publication_quota.py',
                 'preserve_failure.py', 'postprocess_canary.json', 'shared_dependency_gate.json'):
        shutil.copy2(original/name, output/name)
    # The maintained reader explicitly handles verified local ROOT files; its
    # group, weight, axis and histogram-sum validation is the qualified one.
    shutil.copy2(here/'dark_photon_production_support.py',output/'dark_photon_production_support.py')
    for name in ('finalizer_worker.sh', 'finalizer.sub', 'scoring_components.json'):
        text = (original/name).read_text().replace(str(original), str(output))
        text = text.replace('requirements = (TARGET.OpSysMajorVer == 9)\n', '')
        if name=='finalizer.sub':
            text=text.replace('on_exit_hold = False','on_exit_hold = (ExitBySignal == True) || (ExitCode != 0)')
            text=text.replace('\nqueue 1','\n+WantIOProxy = True\non_exit_remove = (ExitBySignal == False) && (ExitCode == 0)\nqueue 1')
        (output/name).write_text(text)
        if name.endswith('.sh'):
            (output/name).chmod(0o750)
    for name in ('prepare_dark_photon_chunk_recovery.py', 'run_dark_photon_detector.py',
                 'run_shift_gen_to_nano.py', 'audit_dark_photon_gen.py', 'pythia_generation_ledger.py',
                 'shift_storage_paths.py'):
        shutil.copy2(here/name, output/'tools'/name)
    finalizer=output/'finalize_point.py'
    finalizer.write_text(finalizer.read_text().replace('    args = parser.parse_args()',
        "    args = parser.parse_args()\n    import sys\n    sys.path.insert(0,str(Path(__file__).resolve().parent/'tools'))\n"
        '    from prepare_dark_photon_chunk_recovery import guard_publication\n'
        '    import chunk_worker as worker\n    guard_publication(worker,scoring=True)'))
    plan = copy.deepcopy(old_plan)
    plan.update(campaign=campaign, points=selected, events=sum(row['events'] for row in selected),
        detector_chunks=sum(len(row['chunks']) for row in selected), retained_origins={},
        recovery_of=dict(directory=str(original), plan_sha256=sha(original/'plan.json'),
                         seal_sha256=sha(original/'seal.json')), new_generated_events=0,
        new_detector_events=0, recovery_inputs=inputs, submitted=False,
        maximum_recovery_publication_bytes=request['recovery_forecast_bytes'],
        audit_scratch_limit_bytes=request['audit_scratch_limit_bytes'])
    for row in selected:
        point = row['point']
        prefix = str(Path(row['final_output_base']).parent/campaign)
        row['final_output_base'] = prefix
        row['merged_histograms'] = prefix+'/histograms_merged/ntuple_0.root'
        for group in row['groups']:
            group['nano'] = prefix+f'/nanoAOD_merged_unscored/ntuple_{group["group"]}.root'
        for chunk in row['chunks']:
            path = original/'statuses'/f'{point}_{chunk["chunk"]:03d}.json'
            status = read(path)
            if chunk['chunk'] == inputs[point]['item']['chunk']:
                if status.get('complete') is not False or sha(path) != inputs[point]['item']['status_sha256']:
                    raise ValueError('The original failed attempt changed or is already complete')
                chunk['nano'] = prefix+f'/nanoAOD/chunk_{chunk["chunk"]:03d}.root'
                chunk['metadata'] = prefix+f'/metadata/chunk_{chunk["chunk"]:03d}'
            else:
                if status.get('complete') is not True:
                    raise ValueError('Recovery point has another unfinished original chunk')
                original_worker.validate_chunk_status(old_plan,inventory,point,chunk['chunk'],status,
                    dict(seal_sha256=request['original_seal_sha256'],plan_sha256=sha(original/'plan.json')),
                    read(original/'storage_budget.json')['bytes_per_detector_chunk'])
                plan['retained_origins'][f'{point}/{chunk["chunk"]}'] = dict(status_path=str(path),
                    seal_sha256=request['original_seal_sha256'], plan_sha256=sha(original/'plan.json'))
    parents = copy.deepcopy(inventory)
    parents['points'] = {row['point']:inventory['points'][row['point']] for row in selected}
    parents['events']=plan['events']
    write(output/'plan.json', plan)
    write(output/'gen_inventory.json', parents)
    write(output/'recovery_request.json', request)
    budget = copy.deepcopy(read(original/'storage_budget.json'))
    budget.update(events=plan['events'], detector_chunks=plan['detector_chunks'], points=len(selected),
        external_reservations=[], fixed_reservation_bytes=request['protected_bytes'],
        gen_group_file_allowance=100, safety_free_floor_files=20000)
    write(output/'storage_budget.json', budget)
    wrapper = output/'promote_worker.sh'
    wrapper.write_text('#!/bin/bash\n# Code style: AI-code. AI-generated native recovery audit environment.\n'
        'set -eo pipefail\nunset PYTHONPATH PYTHONHOME LD_LIBRARY_PATH LD_PRELOAD ROOTSYS\n'
        'export PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1\n'
        'source /cvmfs/cms.cern.ch/cmsset_default.sh\ncd '+request['cmssw_base']+'/src\n'
        'eval "$(scram runtime -sh)"\n'
        'exec python3 '+str(output/'tools/prepare_dark_photon_chunk_recovery.py')+' promote '+str(output)+' "$1" "$2"\n')
    wrapper.chmod(0o750)
    (output/'promote.sub').write_text('# Code style: AI-code. AI-generated bounded recovery audit.\n'
        f'universe = vanilla\nexecutable = {wrapper}\narguments = $(point) $(seal)\ninitialdir = {output}\n'
        'getenv = False\nenvironment = "PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1"\nshould_transfer_files = NO\n'
        'request_cpus = 1\nrequest_memory = 4000MB\nrequest_disk = 2000MB\n+JobFlavour = "workday"\n+MaxRuntime = 28800\n+SendCredential = True\n+WantIOProxy = True\n'
        f'+JobBatchName = "SHIFT_DP_cached_recovery"\noutput = {output}/logs/promote_$(point).$(ClusterId).out\n'
        f'error = {output}/logs/promote_$(point).$(ClusterId).err\nlog = {output}/events.log\n'
        'on_exit_hold = (ExitBySignal == True) || (ExitCode != 0)\non_exit_remove = (ExitBySignal == False) && (ExitCode == 0)\nperiodic_release = False\nqueue\n')
    files = {str(path):sha(path) for path in output.rglob('*') if path.is_file()}
    groups = dict(common=sorted(path for path in files if '/frozen/scoring/' not in path),
        scoring=sorted(path for path in files if '/frozen/scoring/' in path), gen={})
    groups['scoring'].append(str(output/'scoring_components.json'))
    for row in selected:
        paths = old_seal['verification_groups']['gen'][row['point']]
        groups['gen'][row['point']] = paths
        for path in paths:
            files[path] = old_seal['files'][path]
        for chunk in row['chunks']:
            origin = plan['retained_origins'].get(f'{row["point"]}/{chunk["chunk"]}')
            if origin:
                files[origin['status_path']] = sha(origin['status_path'])
                groups['common'].append(origin['status_path'])
    for path in [str(Path(request_path).resolve()), str(Path(request['failed_inventory']).resolve()),
                 str(original/'seal.json'), str(original/'plan.json'), *request['replays']]:
        files[path] = sha(path)
        groups['common'].append(path)
    files.update(replay_dependencies)
    groups['common'].extend(replay_dependencies)
    write(output/'seal.json',dict(schema='shift-cached-dark-photon-recovery-v1',files=files,
        verification_groups=groups,created_utc=datetime.now(timezone.utc).isoformat(),
        original_attempts_unchanged=True, original_GEN_parent_counted_once=True))
    seal = sha(output/'seal.json')
    dag = []
    for index, row in enumerate(selected):
        point = row['point']
        dag.extend([f'JOB A{index} promote.sub',f'VARS A{index} point="{point}" seal="{seal}"',
                    f'JOB F{index} finalizer.sub',f'VARS F{index} kind="finalizer" point="{point}" chunk="0" seal="{seal}"',
                    f'PARENT A{index} CHILD F{index}'])
    (output/'pipeline.dag').write_text('\n'.join(dag)+'\n')
    return dict(directory=str(output),seal_sha256=seal,points=[row['point'] for row in selected],submitted=False)


def promote(directory, point, seal):
    directory = Path(directory).resolve()
    sys.path.insert(0, str(directory))
    import chunk_worker as worker
    worker.verify_seal(seal, point)
    guard_publication(worker)
    plan, inventory = read(directory/'plan.json'), read(directory/'gen_inventory.json')
    row, = [row for row in plan['points'] if row['point'] == point]
    item, replay = [plan['recovery_inputs'][point][key] for key in ('item','replay')]
    chunk, = [chunk for chunk in row['chunks'] if chunk['chunk'] == item['chunk']]
    status_path = directory/'statuses'/f'{point}_{chunk["chunk"]:03d}.json'
    if status_path.exists():
        raise FileExistsError('A recovery attempt already exists; never overwrite or retry blindly')
    with tempfile.TemporaryDirectory(prefix='dp-chunk-recovery-',dir=os.environ.get('_CONDOR_SCRATCH_DIR','/tmp')) as temporary:
        work=Path(temporary)
        def check_scratch():
            if sum(path.stat().st_size for path in work.rglob('*') if path.is_file())>plan['audit_scratch_limit_bytes']:
                raise ValueError('Recovery audit exceeds its bounded local scratch allowance')
        for label, authority in [('original',item['root_failure_archive']),('replay',replay['retained_evidence'])]:
            target=work/(label+'.tar.gz')
            worker.xrd(['/usr/bin/xrdcp','--silent','--cksum','adler32',worker.HOST+'/'+authority['path'],str(target)])
            if target.stat().st_size!=authority['bytes'] or sha(target)!=authority['sha256']:
                raise ValueError('Recovery input archive differs from its pinned authority')
            check_scratch()
        with tarfile.open(work/'original.tar.gz') as archive:
            for name in ('step1.root','step2.root','step3.root'):
                member, = [member for member in item['retained_roots'] if member['path'].endswith('/'+name)]
                checked_extract(archive,member['path'],work/name,member)
        with tarfile.open(work/'replay.tar.gz') as archive:
            checked_extract(archive,'nano.root',work/'nano.root',dict(bytes=replay['nano_bytes'],sha256=replay['nano_sha256']))
            member=archive.getmember('step4_cfg.py')
            if not member.isfile():raise ValueError('Replay configuration is not a regular file')
            with archive.extractfile(member) as incoming,(work/'step4_cfg.py').open('xb') as outgoing:
                shutil.copyfileobj(incoming,outgoing)
            if sha(work/'step4_cfg.py')!=replay['configuration_sha256']:
                raise ValueError('Replay resolved configuration changed')
        parent=inventory['points'][point]
        worker.xrd(['/usr/bin/xrdcp','--silent','--cksum','adler32',worker.HOST+'/'+parent['gen_path'],str(work/'gen.root')])
        if sha(work/'gen.root')!=parent['gen_sha256']:raise ValueError('Original full GEN parent changed')
        check_scratch()
        audit=read(Path(row['gen_directory'])/'validation.json')
        contract=read(Path(row['gen_directory'])/'contract.json')
        source=dict(event_ids=audit['event_ids'],source_full_graph_sha256=[event['full_graph_sha256'] for event in audit['event_rows']])
        write(work/'source.json',source)
        write(work/'signal_report.json',dict(complete=False,signal_contract=contract,
            requested_event_ids=item['expected_event_ids'],recovery_provenance=plan['recovery_inputs'][point],
            original_failed_status_preserved=True,physics_valid=False))
        from run_shift_gen_to_nano import audit_edm,audit_nano
        expected=[dict(id=identity,weight=1.) for identity in item['expected_event_ids']]
        tiers={str(index):dict(audit=audit_edm(work/f'step{index}.root',expected)) for index in (1,2,3)}
        nano=audit_nano(work/'nano.root',expected)
        common=dict(schema='shift-gen-to-nano-retained-recovery-v1',complete=True,
            job=chunk['job'],seed=230000000+chunk['job']*100,events=chunk['count'],stages=tiers,
            nano=nano,nano_sha256=sha(work/'nano.root'),source_events=expected,
            recovery_provenance=plan['recovery_inputs'][point],new_detector_events=0,
            full_GEN_denominator_unchanged=True,physics_valid=False)
        write(work/'report.json',common)
        from run_dark_photon_detector import audit_replay
        audit_replay(work)
        signal=read(work/'signal_report.json')
        if signal.get('complete') is not True or len(signal['signal_audit']['edm_tiers'])!=3:
            raise ValueError('Fresh full-tier source graph audit did not close')
        bundle=work/'metadata.tar.gz'
        with tarfile.open(bundle,'w:gz') as archive:
            for name in ('source.json','report.json','signal_report.json','signal_audit.json','step4_cfg.py'):
                archive.add(work/name,arcname=name)
        receipt=dict(schema='shift-independent-signal-chunk-v1',complete=True,point=point,chunk=chunk['chunk'],
            seal_sha256=seal,plan_sha256=sha(directory/'plan.json'),parent_gen_sha256=parent['gen_sha256'],
            expected_event_ids=item['expected_event_ids'],events=chunk['count'],native_sumw=nano['sumw'],
            sample_role='validation_only',selection_applied=False,publication_profile=plan['publication_profile'],
            audited_scratch_tiers={tier:dict(bytes=(work/f'step{index}.root').stat().st_size,sha256=sha(work/f'step{index}.root'))
                for index,tier in [(1,'GEN_SIM'),(2,'DIGI_RAW_HLT'),(3,'AODSIM')]},
            nano_sha256=sha(work/'nano.root'),source_ledger_counted_once=True,
            recovery_provenance=plan['recovery_inputs'][point],shared_runtime_fingerprint_sha256=sha(directory/'frozen/runtime_fingerprint.json'))
        worker.prepare_publication_guard(seal)
        receipt['published']={}
        for name,local,remote in [('nanoAOD',work/'nano.root',chunk['nano']),
                                 ('metadata',bundle,chunk['metadata']+'/metadata.tar.gz')]:
            receipt['published'][name]=worker.publish(local,remote,work)
        worker.validate_chunk_status(plan,inventory,point,chunk['chunk'],receipt,
            dict(seal_sha256=seal,plan_sha256=sha(directory/'plan.json')),
            read(directory/'storage_budget.json')['bytes_per_detector_chunk'])
        marker=work/'complete.json';write(marker,receipt)
        worker.publish(marker,chunk['metadata']+'/complete.json',work)
        write(status_path,receipt)
        return dict(complete=True,point=point,events=chunk['count'],new_generated_events=0,new_detector_events=0)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    actions=parser.add_subparsers(dest='action',required=True)
    command=actions.add_parser('prepare');command.add_argument('request',type=Path);command.add_argument('output',type=Path)
    command=actions.add_parser('promote');command.add_argument('directory',type=Path);command.add_argument('point');command.add_argument('seal')
    args=parser.parse_args()
    result=prepare(args.request,args.output) if args.action=='prepare' else promote(args.directory,args.point,args.seal)
    print(json.dumps(result))


if __name__=='__main__':
    main()
