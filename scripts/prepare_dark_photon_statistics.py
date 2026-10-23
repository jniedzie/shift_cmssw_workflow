#!/usr/bin/env python3
# Code style: AI-code. AI-generated bounded, immutable DP statistics preparation.
"""Freeze an efficiency allocation and prepare independent, small GEN waves.

This tool never submits jobs. It inherits qualified generator physics exactly;
only event budgets, random seeds, run numbers and output namespaces change.
Wave zero is a real full-chain operational canary, charged to the allocation.
The owning controller must verify its publication before admitting bulk waves.
"""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import shlex
import shutil

import prepare_dark_photon_grid as grid
from prepare_dark_photon_gen_stage import combine_plans, validate_plan


MC_FIELDS = {'requested_events', 'maximum_framework_events', 'seed', 'run_number'}
SCHEMA = 'shift-dp-adaptive-statistics-worklist-v1'


def read(path):
    return json.loads(Path(path).read_text())


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    with Path(path).open('x') as handle:
        json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write('\n')


def physics_digest(contract):
    physics={key:copy.deepcopy(value) for key,value in contract.items() if key not in MC_FIELDS}
    if physics.get('source_placement') is not None:
        for key in ('event_positions','selection_seed','original_source_sha256'):
            physics['source_placement'].pop(key,None)
    return grid.content_digest(physics)


def extend_contract(contract, events, seed, run_number, jaw_profile=None):
    if (contract.get('schema') != 'shift-dark-photon-gen-v1'
            or contract.get('event_budget_mode') != 'outputAccepted'
            or type(events) is not int or not 1 <= events <= 2000):
        raise ValueError('Require a qualified accepted-event contract and a 1..2000 event batch')
    result = copy.deepcopy(contract)
    result.update(requested_events=events, maximum_framework_events=10 * events,
                  seed=seed, run_number=run_number)
    if result.get('source_placement') is not None:
        if jaw_profile is None:
            raise ValueError('A new source population needs its frozen jaw profile')
        from dark_photon_source_placement import place_contract
        placement=result.pop('source_placement')
        result['source_model']=placement['preplacement_source_model']
        result=place_contract(result,jaw_profile)
    if physics_digest(result) != physics_digest(contract):
        raise ValueError('Statistics extension changed generator physics')
    return result


def build_waves(allocation, canary_points, batch_events=2000, points_per_wave=10):
    if not 20 <= batch_events <= 2000 or not 1 <= points_per_wave <= 10:
        raise ValueError('Require 20..2000 events per batch and 1..10 points per wave')
    points = allocation['points']
    names = [row['point'] for row in points]
    if len(set(names)) != len(names) or len(set(canary_points)) != len(canary_points):
        raise ValueError('Allocation/canary point identities must be unique')
    remaining = {}
    for row in points:
        count = row['vertex_z_plan']['planned_next_stage_additional_events']
        if type(count) is not int or count < 0:
            raise ValueError('Require explicit nonnegative planned additional events')
        remaining[row['point']] = count
    if not canary_points or not set(canary_points) <= set(names):
        raise ValueError('Canary points must belong to the approved allocation')
    canary = []
    for point in canary_points:
        if remaining[point] < 20:
            raise ValueError('Canary requires twenty allocated events per selected point')
        canary.append(dict(point=point, events=20))
        remaining[point] -= 20
    waves = [dict(wave_index=0, operational_canary=True, batches=canary)]
    # Every active point receives one independent batch per round. Finishing a
    # small point never waits for all of the much larger points in the campaign.
    order = sorted(names, key=lambda name: (remaining[name], names.index(name)))
    while any(remaining.values()):
        batch = []
        for point in order:
            if remaining[point] == 0:
                continue
            count = min(batch_events, remaining[point])
            remaining[point] -= count
            batch.append(dict(point=point, events=count))
            if len(batch) == points_per_wave:
                waves.append(dict(wave_index=len(waves), operational_canary=False, batches=batch))
                batch = []
        if batch:
            waves.append(dict(wave_index=len(waves), operational_canary=False, batches=batch))
    expected = {row['point']: row['vertex_z_plan']['planned_next_stage_additional_events'] for row in points}
    actual = {name: sum(batch['events'] for wave in waves for batch in wave['batches'] if batch['point'] == name) for name in names}
    if actual != expected:
        raise ValueError('Wave partition changed the exact approved per-point allocation')
    return waves


def namespace_audit(waves, prior_paths, prior_plans, reserved_seeds, reserved_runs):
    old_seeds, old_runs, old_jobs = set(reserved_seeds), set(reserved_runs), set()
    authorities = []
    for path in [*prior_paths, *prior_plans]:
        data = read(path)
        rows = data['points'].values() if isinstance(data['points'], dict) else data['points']
        for row in rows:
            if 'seed' in row:
                old_seeds.add(row['seed'])
            if 'run_number' in row:
                old_runs.add(row['run_number'])
            old_runs.update(identity[0] for identity in row.get('event_ids', []))
            if 'detector_job' in row:
                old_jobs.add(row['detector_job'])
            old_jobs.update(chunk['job'] for chunk in row.get('chunks', []))
        authorities.append(dict(path=str(Path(path).resolve()), sha256=digest(path)))
    seeds, runs, jobs = [], [], []
    for wave in waves:
        for batch in wave['batches']:
            seeds.append(batch['seed']); runs.append(batch['run_number'])
            jobs.extend(range(batch['detector_job_base'], batch['detector_job_base'] + batch['detector_chunks']))
            jobs.append(batch['detector_audit_job'])
    for name, values, previous in [('seeds', seeds, old_seeds), ('runs', runs, old_runs), ('detector_jobs', jobs, old_jobs)]:
        if len(set(values)) != len(values) or set(values).intersection(previous):
            raise ValueError('New namespace overlaps another population: ' + name)
    if not seeds or min(seeds) < 1 or max(seeds) > 899999823 or min(runs) < 1 or max(runs) > 4294967295:
        raise ValueError('GEN namespace exceeds the qualified seed/run bounds')
    if 230000000 + max(jobs) * 100 + 40 > 899999900:
        raise ValueError('Detector namespace exceeds its qualified random-seed bounds')
    return dict(complete=True, overlapping_seeds=[], overlapping_runs=[], overlapping_detector_jobs=[],
                prior_authorities=authorities, old_seed_count=len(old_seeds), old_run_count=len(old_runs),
                old_detector_job_count=len(old_jobs), reserved_seeds=sorted(reserved_seeds), reserved_runs=sorted(reserved_runs),
                new_seed_range=[min(seeds), max(seeds)], new_run_range=[min(runs), max(runs)],
                new_detector_job_range=[min(jobs), max(jobs)])


def copy_checked(source, destination, files):
    source, destination = Path(source), Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    if digest(source) != digest(destination):
        raise ValueError('Frozen copy differs: ' + str(source))
    files[str(destination)] = digest(destination)


def prepare_worklist(args):
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError('Refuse to overwrite an existing statistics worklist')
    allocation = read(args.allocation)
    waves = build_waves(allocation, args.canary_points, args.batch_events, args.points_per_wave)
    batch_index, job = 0, args.detector_job_base
    for wave in waves:
        wave['campaign'] = args.campaign + f'-w{wave["wave_index"]:03d}'
        for batch in wave['batches']:
            chunk_size=1 if wave['operational_canary'] else args.events_per_job
            chunks = math.ceil(batch['events'] / chunk_size)
            batch.update(batch_index=batch_index, seed=args.seed_base + batch_index,
                         run_number=args.run_base + batch_index, detector_job_base=job,
                         detector_chunks=chunks, detector_audit_job=job + chunks)
            batch_index += 1
            job += chunks + 1
        wave['events'] = sum(batch['events'] for batch in wave['batches'])
        wave['detector_chunks'] = sum(batch['detector_chunks'] for batch in wave['batches'])
    proof = namespace_audit(waves, args.prior_inventories, args.prior_plans,
                            args.reserved_seeds, args.reserved_runs)
    if (not re.fullmatch(r'[A-Za-z0-9_-]+', args.campaign)
            or not re.search(r'(?<![0-9])20[0-9]{6}(?![0-9])',args.campaign)):
        raise ValueError('Require an explicit dated campaign base')
    output.mkdir(parents=True)
    files, sources, points, physics_sources = {}, {}, {}, {}
    fresh_profile=getattr(args,'runtime_profile',None)
    jaw_profile=getattr(args,'jaw_profile',None)
    private_gen=getattr(args,'gen_runtime',None)
    if bool(fresh_profile)!=bool(jaw_profile) or bool(fresh_profile)!=bool(private_gen):
        raise ValueError('Fresh source production requires private detector/GEN runtimes and jaw profile together')
    if fresh_profile:
        for original,name in ((fresh_profile,'detector_runtime.json'),(private_gen,'gen_runtime.json'),
                              (jaw_profile,'jaw_profile.json')):
            copy_checked(original,output/name,files)
        jaw_profile=output/'jaw_profile.json'
        for authority in (read(output/'detector_runtime.json')['manifest'],
                          read(output/'gen_runtime.json')['provenance']):
            if digest(authority['path']) != authority['sha256']:
                raise ValueError('Fresh runtime authority changed')
            files[authority['path']]=authority['sha256']
    copy_checked(args.allocation, output / 'allocation.json', files)
    for index, path in enumerate(args.physical_preparations):
        previous = read(path)
        if previous.get('native_width_preflight_passed') is not True:
            raise ValueError('Physical source lacks a qualified width preflight')
        label = f'backend_{index}'
        source_dir = output / 'physical_sources' / label
        source_plan = source_dir / 'plan.json'
        copy_checked(path, source_plan, files)
        reference = source_dir / 'references.json'
        copy_checked(previous['frozen_dependencies']['references']['frozen_copy'], reference, files)
        templates = source_dir / 'templates'
        for name, item in previous['frozen_dependencies']['templates'].items():
            if digest(item['frozen_copy']) != item['sha256']:
                raise ValueError('Qualified detector template changed')
            copy_checked(item['frozen_copy'], templates / name, files)
        for name, item in previous['frozen_dependencies']['sources'].items():
            if digest(item['frozen_copy']) != item['sha256']:
                raise ValueError('Qualified generator source changed: ' + name)
            if name in physics_sources and digest(physics_sources[name]) != item['sha256']:
                raise ValueError('Qualified source snapshots disagree: ' + name)
            physics_sources.setdefault(name, item['frozen_copy'])
        spectral = previous['frozen_dependencies'].get('spectral_width_source')
        spectral_directory = None
        if spectral:
            original = Path(path).parent / 'width_inputs'
            spectral_directory = source_dir / 'width_inputs'
            receipt = read(original / 'source_receipt.json')
            for name, expected in receipt['files'].items():
                if digest(original / name) != expected:
                    raise ValueError('Qualified spectral width provider changed')
                copy_checked(original / name, spectral_directory / name, files)
            copy_checked(original / 'source_receipt.json', spectral_directory / 'source_receipt.json', files)
        sources[label] = dict(plan=str(source_plan), references=str(reference), templates=str(templates),
                              spectral_directory=str(spectral_directory) if spectral_directory else None,
                              original_plan=str(Path(path).resolve()), original_plan_sha256=digest(path))
        for row in previous['points']:
            name = row.get('original_point', row['point'])
            if name in points:
                raise ValueError('Ambiguous original physical point: ' + name)
            contract = read(row['signal_contract'])
            if digest(row['signal_contract']) != row['signal_contract_sha256']:
                raise ValueError('Qualified physical contract changed: ' + name)
            contract_path = source_dir / 'contracts' / (name + '.json')
            if fresh_profile:
                from dark_photon_source_placement import place_contract
                contract=place_contract(contract,jaw_profile)
                # This is a physics reference, not an executable event map.
                # Each new wave regenerates positions for its own seed/run.
                contract['source_placement']['event_positions']=[]
                contract_path.parent.mkdir(parents=True,exist_ok=True)
                write(contract_path,contract)
                files[str(contract_path)]=digest(contract_path)
            else:
                copy_checked(row['signal_contract'], contract_path, files)
            points[name] = dict(source=label, row=copy.deepcopy(row), contract=str(contract_path),
                                physics_sha256=physics_digest(contract))
    requested = {row['point'] for row in allocation['points']}
    if not requested <= set(points):
        raise ValueError('Allocation is missing an inherited physical point')
    # Planning/naming updates are separate from the qualified physics workers.
    here = Path(__file__).resolve().parent
    physics_sources['dark_photon_sample_names.py'] = str(here / 'dark_photon_sample_names.py')
    operational_overrides = {}
    if fresh_profile:
        from run_dark_photon_spectral_gen import SOURCES as SPECTRAL_SOURCES
        from prepare_dark_photon_material_source import DEPENDENCIES as MATERIAL_SOURCES
        for name in set(grid.SOURCES)|set(SPECTRAL_SOURCES)|set(MATERIAL_SOURCES):
            replacement=here/name
            if name in physics_sources:
                operational_overrides[name]=dict(qualified_sha256=digest(physics_sources[name]),
                    replacement_sha256=digest(replacement),
                    reason='Fresh jaw/material production; native GEN and weighted full-chain gates before bulk')
            physics_sources[name]=str(replacement)
    if args.detector_runner is not None:
        original = physics_sources['run_dark_photon_detector.py']
        operational_overrides['run_dark_photon_detector.py'] = dict(
            qualified_sha256=digest(original), replacement_sha256=digest(args.detector_runner),
            reason='Reviewed exact selected-index FWLite audit; detector physics unchanged')
        physics_sources['run_dark_photon_detector.py'] = str(args.detector_runner.resolve())
    for name, source in physics_sources.items():
        copy_checked(source, output / 'physics_scripts' / name, files)
        copy_checked(source, output / 'tools' / name, files)
    for name in ('prepare_dark_photon_statistics.py', 'prepare_dark_photon_grid.py',
                 'prepare_dark_photon_gen_stage.py', 'run_dark_photon_gen_stage.py'):
        copy_checked(here / name, output / 'tools' / name, files)
    worklist = dict(schema=SCHEMA, created_utc=datetime.now(timezone.utc).isoformat(), submitted=False,
                    total_additional_events=sum(wave['events'] for wave in waves),
                    expected_existing_completed_events=allocation['totals']['completed_events'],
                    allocation_path=str(output / 'allocation.json'), allocation_sha256=digest(args.allocation),
                    events_per_job=args.events_per_job, group_events=2000, batch_events=args.batch_events,
                    points_per_wave=args.points_per_wave, eos_base=args.eos_base,
                    event_stall_timeout_seconds=7200, first_event_stall_timeout_seconds=7200,
                    gen_timeout_seconds=21600, physics_sources=sources, points={name: points[name] for name in requested},
                    physics_scripts=str(output / 'physics_scripts'), physics_script_names=sorted(physics_sources),
                    operational_overrides=operational_overrides, namespace_audit=proof, waves=waves,
                    bulk_requires_complete_canary_histogram_and_publication=True,
                    known_limitations=['Unchanged detector/reconstruction may still encounter slow originals.',
                                       'Existing failed original events remain separate unresolved work; they are not replaced or credited.',
                                       'Physics and real trigger/recording acceptance remain provisional.'])
    if fresh_profile:
        worklist.update(detector_runtime=read(output/'detector_runtime.json'),
            private_gen_runtime=read(output/'gen_runtime.json'),jaw_profile=str(jaw_profile),
            prior_completed_events_are_exposure_credit=False,
            fresh_source_placement_requires_fullchain_canary=True)
    if worklist['total_additional_events'] != allocation['totals']['planned_next_stage_additional_events']:
        raise ValueError('Worklist changed the authorized allocation')
    write(output / 'worklist.json', worklist)
    files[str(output / 'worklist.json')] = digest(output / 'worklist.json')
    write(output / 'seal.json', dict(schema='shift-dp-statistics-worklist-seal-v1', files=files))
    return dict(worklist=str(output / 'worklist.json'), events=worklist['total_additional_events'],
                waves=len(waves), batches=batch_index, detector_chunks=sum(wave['detector_chunks'] for wave in waves))


def prepare_wave(worklist_path, wave_index, output):
    worklist_path, output = Path(worklist_path).resolve(), Path(output).resolve()
    worklist = read(worklist_path)
    if worklist.get('schema') != SCHEMA:
        raise ValueError('Unsupported adaptive statistics worklist')
    for path, expected in read(worklist_path.parent / 'seal.json')['files'].items():
        if digest(path) != expected:
            raise ValueError('Frozen worklist input changed: ' + path)
    if type(wave_index) is not int or not 0 <= wave_index < len(worklist['waves']):
        raise ValueError('Unknown wave index')
    if output.exists():
        raise FileExistsError('Refuse to overwrite a prepared wave')
    output.mkdir(parents=True)
    wave = worklist['waves'][wave_index]
    by_source = {}
    for batch in wave['batches']:
        record = worklist['points'][batch['point']]
        by_source.setdefault(record['source'], []).append((batch, record))
    plans = []
    for source_name, members in by_source.items():
        source = worklist['physics_sources'][source_name]
        previous = read(source['plan'])
        plan = copy.deepcopy(previous)
        plan['points'] = []
        for batch, record in members:
            row = copy.deepcopy(record['row'])
            contract = extend_contract(read(record['contract']), batch['events'], batch['seed'], batch['run_number'], worklist.get('jaw_profile'))
            if physics_digest(contract) != record['physics_sha256']:
                raise ValueError('Wave changed an inherited physical point')
            for key in ('chunks', 'groups', 'gen_argv', 'gen_command', 'detector_argv', 'detector_command',
                        'gen_directory', 'detector_directory', 'final_output_base', 'merged_histograms', 'gen_remote_path'):
                row.pop(key, None)
            row.update(events=batch['events'], seed=batch['seed'], run_number=batch['run_number'],
                       detector_count=min(2, batch['events']), signal_contract=contract,
                       statistics_extension_physics_sha256=record['physics_sha256'])
            plan['points'].append(row)
        plan.update(gen_timeout_seconds=worklist['gen_timeout_seconds'],
                    event_stall_timeout_seconds=worklist['event_stall_timeout_seconds'],
                    first_event_stall_timeout_seconds=worklist['first_event_stall_timeout_seconds'],
                    statistics_extension_physics_authority=dict(path=source['plan'], sha256=digest(source['plan'])))
        plan = grid.plan_detector_stage(plan, wave['campaign'], worklist['eos_base'],
                                        (1 if wave['operational_canary'] else worklist['events_per_job']), worklist['group_events'],
                                        min(batch['detector_job_base'] for batch, _ in members))
        batches = {batch['point']: batch for batch, _ in members}
        for row in plan['points']:
            batch = batches[row.get('original_point', row['point'])]
            if len(row['chunks']) != batch['detector_chunks'] or len(row['groups']) != 1:
                raise ValueError('Wave partition differs from the reviewed worklist')
            for chunk in row['chunks']:
                chunk['job'] = batch['detector_job_base'] + chunk['chunk']
            row['detector_job'] = batch['detector_audit_job']
        if worklist.get('private_gen_runtime') is not None:
            plan['private_gen_runtime']=copy.deepcopy(worklist['private_gen_runtime'])
            plan['detector_runtime']=copy.deepcopy(worklist['detector_runtime'])
        runtime = copy.deepcopy(previous['runtime'])
        runtime['source_preflight_sha256'] = {name: digest(Path(worklist['physics_scripts']) / name) for name in worklist['physics_script_names']}
        runtime['width_preflight'] = ('Inherited qualified width/coupling contracts; fresh qualified jaw law and private runtime'
            if worklist.get('private_gen_runtime') else 'Exact inherited qualified physics contracts; only MC count/run/seed changed')
        destination = output / source_name
        grid.SOURCES = tuple(worklist['physics_script_names'])
        frozen = grid.write_plan(destination, source['references'], source['templates'], plan, runtime,
                                 scripts=worklist['physics_scripts'], executable=runtime['python_executable'])
        if source['spectral_directory']:
            external = Path(source['spectral_directory'])
            frozen['frozen_dependencies']['spectral_width_source'] = previous['frozen_dependencies']['spectral_width_source']
            for row in frozen['points']:
                row['gen_argv'][1] = str(Path(row['gen_argv'][1]).with_name('run_dark_photon_spectral_gen.py'))
                row['gen_argv'].remove('--accepted-events')
                row['gen_argv'].extend(['--darkcast-path', str(external), '--darkcast-source-receipt', str(external / 'source_receipt.json')])
                row['gen_command'] = shlex.join(row['gen_argv'])
            (destination / 'plan.json').write_text(json.dumps(frozen, indent=2, allow_nan=False) + '\n')
        if worklist.get('jaw_profile'):
            for row in frozen['points']:
                row['gen_argv'].extend(['--jaw-profile',worklist['jaw_profile']])
                row['gen_command']=shlex.join(row['gen_argv'])
            (destination/'plan.json').write_text(json.dumps(frozen,indent=2,allow_nan=False)+'\n')
        plans.append(destination / 'plan.json')
    combined = combine_plans(plans, output / 'combined')
    plan_path = Path(combined['plan'])
    summary = validate_plan(read(plan_path), [])
    receipt = dict(schema='shift-dp-statistics-wave-preparation-v1', worklist=str(worklist_path),
                   worklist_sha256=digest(worklist_path), wave_index=wave_index, campaign=wave['campaign'],
                   operational_canary=wave['operational_canary'], submitted=False,
                   preparation=str(plan_path), preparation_sha256=digest(plan_path),
                   events=summary['events'], points=summary['points'], detector_chunks=combined['detector_chunks'])
    if receipt['events'] != wave['events'] or receipt['detector_chunks'] != wave['detector_chunks']:
        raise ValueError('Prepared wave changed its approved population')
    write(output / 'preparation_receipt.json', receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    create = commands.add_parser('prepare-worklist')
    create.add_argument('--allocation', type=Path, required=True)
    create.add_argument('--physical-preparations', type=Path, nargs='+', required=True)
    create.add_argument('--prior-inventories', type=Path, nargs='+', required=True)
    create.add_argument('--prior-plans', type=Path, nargs='+', required=True)
    create.add_argument('--output', type=Path, required=True)
    create.add_argument('--campaign', required=True)
    create.add_argument('--eos-base', default='/eos/user/j/jniedzie/shift_cmssw')
    create.add_argument('--seed-base', type=int, required=True)
    create.add_argument('--run-base', type=int, required=True)
    create.add_argument('--detector-job-base', type=int, required=True)
    create.add_argument('--events-per-job', type=int, default=20, choices=range(1, 21))
    create.add_argument('--batch-events', type=int, default=2000)
    create.add_argument('--points-per-wave', type=int, default=10)
    create.add_argument('--canary-points', nargs='+', default=['m2_prompt', 'm15_prompt', 'm70_lab_1000m'])
    create.add_argument('--reserved-seeds', nargs='+', type=int, default=[761016001, 761016002])
    create.add_argument('--reserved-runs', nargs='+', type=int, default=[1761016001, 1761016002])
    create.add_argument('--detector-runner', type=Path)
    create.add_argument('--runtime-profile',type=Path)
    create.add_argument('--gen-runtime',type=Path)
    create.add_argument('--jaw-profile',type=Path)
    wave = commands.add_parser('prepare-wave')
    wave.add_argument('--worklist', type=Path, required=True)
    wave.add_argument('--wave', type=int, required=True)
    wave.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = prepare_worklist(args) if args.command == 'prepare-worklist' else prepare_wave(args.worklist, args.wave, args.output)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
