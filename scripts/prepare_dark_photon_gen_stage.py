#!/usr/bin/env python3
# Code style: AI-code. AI-generated detached, immutable full-grid GEN stage preparation.
"""Prepare a GEN DAG and sealed downstream commands without launching anything."""
import argparse
import copy
import json
from pathlib import Path
import re
import shutil

from run_dark_photon_gen_stage import read, sha256, write_new


def combine_plans(paths, output):
    """Keep heterogeneous event budgets and every immutable physics authority."""
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError('Refuse to overwrite an existing combined preparation')
    plans = [read(path) for path in paths]
    if not plans:
        raise ValueError('Require at least one qualified physical preparation')
    result = copy.deepcopy(plans[0])
    result['points'] = []
    result['preparation_authorities'] = []
    result['statistics_extension_physics_authorities'] = []
    result['frozen_dependencies']['additional_references'] = []
    shared = ('campaign', 'group_events', 'chunk_size', 'event_budget_mode',
              'native_width_preflight_passed', 'detector_stage_prepared')
    for path, plan in zip(paths, plans):
        if any(plan[key] != result[key] for key in shared):
            raise ValueError('Physical preparations have incompatible stage conditions')
        for key in ('cmssw_base', 'cmssw_version', 'scram_arch', 'cmssw_git_head'):
            if plan['runtime'][key] != result['runtime'][key]:
                raise ValueError('Physical preparations use incompatible CMSSW runtimes')
        result['points'].extend(copy.deepcopy(plan['points']))
        result['preparation_authorities'].append(dict(path=str(Path(path).resolve()), sha256=sha256(path)))
        authority = plan.get('statistics_extension_physics_authority')
        if authority:
            result['statistics_extension_physics_authorities'].append(copy.deepcopy(authority))
        for category in ('sources', 'templates'):
            for name, record in plan['frozen_dependencies'][category].items():
                previous = result['frozen_dependencies'][category].get(name)
                if previous and previous['sha256'] != record['sha256']:
                    raise ValueError('Same-named frozen source/template differs across physical preparations: ' + name)
                result['frozen_dependencies'][category].setdefault(name, copy.deepcopy(record))
        result['frozen_dependencies']['additional_references'].append(
            copy.deepcopy(plan['frozen_dependencies']['references']))
    rows = result['points']
    for key in ('point', 'seed', 'run_number'):
        if len({row[key] for row in rows}) != len(rows):
            raise ValueError('Combined GEN preparations reuse ' + key)
    jobs = [chunk['job'] for row in rows for chunk in row['chunks']]
    if len(set(jobs)) != len(jobs):
        raise ValueError('Combined detector preparations reuse random-seed jobs')
    result.update(events=sum(row['events'] for row in rows), detector_chunks=len(jobs),
                  gen_budget_is_per_point=True, qualified_physics_backends=['native', 'spectral'])
    result.pop('statistics_extension_physics_authority', None)
    frozen_dependencies(result)
    output.mkdir(parents=True)
    write_new(output / 'plan.json', result)
    write_new(output / 'combination_receipt.json', dict(complete=True, submitted=False,
        points=len(rows), events=result['events'], detector_chunks=len(jobs),
        plan_sha256=sha256(output / 'plan.json'), preparation_authorities=result['preparation_authorities']))
    return dict(plan=str(output / 'plan.json'), points=len(rows), events=result['events'], detector_chunks=len(jobs))


def validate_plan(plan, prior_inventories, reuse_points=()):
    if (plan.get('schema') != 'shift-dark-photon-grid-plan-v1' or
            plan.get('native_width_preflight_passed') is not True or
            plan.get('detector_stage_prepared') is not True or
            plan.get('event_budget_mode') != 'outputAccepted'):
        raise ValueError('GEN orchestration requires a full accepted-event, width-qualified stage')
    names, runs, seeds, old_runs, old_seeds = set(), set(), set(), set(), set()
    for path in prior_inventories:
        for row in read(path)['points'].values():
            old_runs.update(identity[0] for identity in row['event_ids'])
            if 'seed' in row:
                old_seeds.add(row['seed'])
    for row in plan['points']:
        point = row['point']
        if not re.fullmatch(r'[A-Za-z0-9_-]+', point) or point in names:
            raise ValueError('GEN point names are invalid or repeated')
        if (type(row['events']) is not int or not 1 <= row['events'] <= 10000 or
                type(row['seed']) is not int or not 1 <= row['seed'] <= 899999823 or
                type(row['run_number']) is not int or not 1 <= row['run_number'] <= 4294967295 or
                row['seed'] in seeds | old_seeds or row['run_number'] in runs | old_runs):
            raise ValueError('GEN budget or independent run/seed namespace is invalid')
        if Path(row['gen_directory']).exists() and point not in reuse_points:
            raise FileExistsError('New GEN stage would reuse an existing output directory')
        contract = read(row['signal_contract'])
        if sha256(row['signal_contract']) != row['signal_contract_sha256']:
            raise ValueError('Frozen physical contract changed')
        if (contract['event_budget_mode'] != 'outputAccepted' or contract['requested_events'] != row['events'] or
                any(contract[key] != row[key] for key in ('mass_gev', 'epsilon', 'seed', 'run_number'))):
            raise ValueError('GEN row differs from its frozen accepted-event contract')
        names.add(point); seeds.add(row['seed']); runs.add(row['run_number'])
    if not names or len(names) > 30:
        raise ValueError('Require 1..30 distinct independent GEN populations')
    return dict(points=len(names), events=sum(row['events'] for row in plan['points']),
                runs=sorted(runs), seeds=sorted(seeds))


def frozen_dependencies(plan):
    files = {}
    for category in ('sources', 'templates'):
        for record in plan['frozen_dependencies'][category].values():
            files[record['frozen_copy']] = record['sha256']
    record = plan['frozen_dependencies']['references']
    files[record['frozen_copy']] = record['sha256']
    for record in plan['frozen_dependencies'].get('additional_references', []):
        files[record['frozen_copy']] = record['sha256']
    for record in plan.get('preparation_authorities', []):
        files[record['path']] = record['sha256']
    for record in plan.get('statistics_extension_physics_authorities', []):
        files[record['path']] = record['sha256']
    if plan.get('private_gen_runtime') is not None:
        for authority in (plan['private_gen_runtime']['provenance'],plan['detector_runtime']['manifest']):
            files[authority['path']]=authority['sha256']
    for row in plan['points']:
        files[row['signal_contract']] = row['signal_contract_sha256']
        # The spectral runner will validate its pinned package again.
        option = '--darkcast-source-receipt'
        if option in row['gen_argv']:
            path = Path(row['gen_argv'][row['gen_argv'].index(option) + 1])
            files[str(path)] = sha256(path)
            for relative, expected in read(path)['files'].items():
                files[str(path.parent / relative)] = expected
    authority = plan.get('statistics_extension_physics_authority')
    if authority:
        files[authority['path']] = authority['sha256']
    for path, expected in files.items():
        if sha256(path) != expected:
            raise ValueError('Prepared GEN dependency changed: ' + path)
    return files


def prepare(args):
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError('Refuse to overwrite an existing GEN stage')
    plan = copy.deepcopy(read(args.plan))
    reuse_stage = getattr(args, 'reuse_gen_stage', None)
    reused = {}
    if reuse_stage is not None:
        from run_dark_photon_gen_stage import validate_population
        old_plan = read(reuse_stage/'plan.json')
        old_rows = {row['point']:row for row in old_plan['points']}
        for row in plan['points']:
            status_path = reuse_stage/'statuses'/(row['point']+'.json')
            if status_path.is_file() and read(status_path).get('complete') is True:
                if row != old_rows.get(row['point']):
                    raise ValueError('Reused successful GEN row differs from its exact original plan')
                validate_population(row)
                reused[row['point']] = status_path
    summary = validate_plan(plan, args.prior_inventories, reused)
    files = frozen_dependencies(plan)
    for path in reused.values():
        files[str(path.resolve())] = sha256(path)
    configuration = read(args.handoff_config)
    if reused:
        configuration['reused_gen_statuses'] = {point:dict(path=str(path.resolve()),sha256=sha256(path))
                                              for point,path in reused.items()}
    argv_list = configuration.get('handoff_argvs', [])
    if not argv_list or any(not isinstance(argv, list) or not argv or
                            any(not isinstance(item, str) for item in argv) or
                            not Path(argv[0]).is_absolute() for argv in argv_list):
        raise ValueError('A full-chain GEN stage requires explicit absolute downstream commands')
    if not 1 <= args.gen_workers <= 300:
        raise ValueError('GEN worker cap must be 1..300')
    configuration.update(workspace=str(args.workspace.resolve()),
        scratch_base=str(args.scratch_base.resolve()),
        prior_inventories=[str(path.resolve()) for path in args.prior_inventories],
        gen_worker_timeout_seconds=args.gen_worker_timeout,
        publication_timeout_seconds=args.publication_timeout,
        handoff_timeout_seconds=args.handoff_timeout,
        minimum_afs_free_mb=args.minimum_afs_free_mb)
    for key, default in [('eos_quota_space', '/eos/user/j/jniedzie/'),
                         ('minimum_eos_free_bytes', 50000000000), ('minimum_eos_free_files', 1000)]:
        configuration.setdefault(key, default)
    if (type(configuration['minimum_eos_free_bytes']) is not int or
            configuration['minimum_eos_free_bytes'] <= 0 or
            type(configuration['minimum_eos_free_files']) is not int or
            configuration['minimum_eos_free_files'] <= 0):
        raise ValueError('GEN publication requires a reviewed positive storage reservation')
    for path in [args.plan, args.runtime_fingerprint, args.handoff_config,
                 *args.prior_inventories, *args.handoff_dependencies]:
        files[str(path.resolve())] = sha256(path)
    for path, record in read(args.runtime_fingerprint)['files'].items():
        if sha256(path) != record['sha256']:
            raise ValueError('Qualified GEN runtime file changed: ' + path)
    output.mkdir(parents=True)
    (output / 'logs').mkdir(); (output / 'statuses').mkdir()
    worker = output / 'gen_worker.py'
    shutil.copy2(Path(__file__).with_name('run_dark_photon_gen_stage.py'), worker)
    write_new(output / 'plan.json', plan)
    write_new(output / 'config.json', configuration)
    if plan.get('private_gen_runtime') is not None:
        write_new(output/'private_runtime_profile.json',plan['detector_runtime'])
        files[str(output/'private_runtime_profile.json')]=sha256(output/'private_runtime_profile.json')
    shutil.copy2(args.runtime_fingerprint, output / 'runtime_fingerprint.json')
    lines = []
    parents = []
    for row in plan['points']:
        if row['point'] in reused:
            shutil.copy2(reused[row['point']],output/'statuses'/(row['point']+'.json'))
            continue
        node = 'G_' + row['point']; parents.append(node)
        lines.extend([f'JOB {node} {output}/worker.sub',
                      f'VARS {node} action="run" point="{row["point"]}" seal="SEAL_DIGEST"',
                      f'CATEGORY {node} GEN'])
    lines.extend([f'JOB COLLECT {output}/worker.sub',
                  'VARS COLLECT action="collect" point="all" seal="SEAL_DIGEST"',
                  *([f'PARENT {" ".join(parents)} CHILD COLLECT'] if parents else []),
                  f'JOB HANDOFF {output}/worker.sub',
                  'VARS HANDOFF action="handoff" point="all" seal="SEAL_DIGEST"',
                  'PARENT COLLECT CHILD HANDOFF', f'MAXJOBS GEN {args.gen_workers}'])
    dag = '\n'.join(lines) + '\n'
    common = f'''universe = vanilla
executable = /usr/bin/python3
arguments = {worker} --directory {output} --seal $(seal) --action $(action) --point $(point)
initialdir = {output}
should_transfer_files = NO
getenv = False
environment = "PATH=/usr/bin:/bin"
request_cpus = 1
request_memory = 2500MB
request_disk = 6000MB
+JobFlavour = "tomorrow"
+JobBatchName = "SHIFT_DarkPhoton_GEN_scale"
+SHIFTGenStageDirectory = "{output}"
output = {output}/logs/$(action)_$(point).$(ClusterId).$(ProcId).out
error = {output}/logs/$(action)_$(point).$(ClusterId).$(ProcId).err
log = {output}/jobs.log
+SendCredential = True
+MaxRuntime = {args.gen_worker_timeout + args.handoff_timeout + 1800}
on_exit_hold = (ExitBySignal == True) || (ExitCode != 0)
on_exit_remove = (ExitBySignal == False) && (ExitCode == 0)
periodic_release = False
queue
'''
    (output / 'worker.sub').write_text(common)
    (output / 'pipeline.dag.in').write_text(dag)
    for path in [worker, output / 'plan.json', output / 'config.json',
                 output / 'runtime_fingerprint.json', output / 'worker.sub', output / 'pipeline.dag.in']:
        files[str(path)] = sha256(path)
    write_new(output / 'seal.json', dict(schema='shift-dark-photon-GEN-stage-seal-v1', files=files))
    seal = sha256(output / 'seal.json')
    (output / 'pipeline.dag').write_text(dag.replace('SEAL_DIGEST', seal))
    write_new(output / 'preparation_receipt.json', dict(summary, complete=True, submitted=False,
              seal_sha256=seal, plan_sha256=sha256(output / 'plan.json'),
              gen_workers=args.gen_workers, downstream_commands=len(argv_list)))
    return dict(summary, directory=str(output), seal_sha256=seal, submitted=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--reuse-gen-stage', type=Path, help='Reviewed recovery: reuse exact validated successful GEN rows without regeneration')
    parser.add_argument('--combine-only', action='store_true')
    parser.add_argument('--plans', type=Path, nargs='+')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--runtime-fingerprint', type=Path)
    parser.add_argument('--handoff-config', type=Path)
    parser.add_argument('--handoff-dependencies', type=Path, nargs='*', default=[])
    parser.add_argument('--prior-inventories', type=Path, nargs='+')
    parser.add_argument('--workspace', type=Path)
    parser.add_argument('--scratch-base', type=Path, default=Path('/tmp'))
    parser.add_argument('--gen-workers', type=int, default=3)
    parser.add_argument('--gen-worker-timeout', type=int, default=14400)
    parser.add_argument('--publication-timeout', type=int, default=600)
    parser.add_argument('--handoff-timeout', type=int, default=3600)
    parser.add_argument('--minimum-afs-free-mb', type=int, default=1000)
    args = parser.parse_args()
    if args.combine_only:
        if not args.plans:
            parser.error('--combine-only requires --plans')
        print(json.dumps(combine_plans(args.plans, args.output)))
        return
    if any(value is None for value in (args.plan, args.runtime_fingerprint, args.handoff_config,
                                       args.prior_inventories, args.workspace)):
        parser.error('GEN DAG preparation requires plan, runtime, handoff, prior inventories and workspace')
    print(json.dumps(prepare(args)))


if __name__ == '__main__':
    main()
