#!/usr/bin/env python3
# Code style: AI-code. AI-generated reusable immutable BSM production preparation.
"""Freeze a qualified GEN-to-merged-histogram deployment; never submit jobs."""
import argparse
import ast
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import subprocess
import shlex

from dark_photon_publication_quota import sha256, external_chunk_reservations
from audit_dark_photon_gen import validate_event_ids
from prepare_dark_photon_grid import validate_physical_preflight
from dark_photon_material_selection import population, validate_plan
from dark_photon_production_support import (
    PROFILES, validate_complete_groups, validate_publication_canary,
    validate_histogram_template,
)


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    with Path(path).open('x') as output:
        json.dump(value, output, indent=2, sort_keys=True, allow_nan=False)
        output.write('\n')


def prepare_main_histograms(args):
    """Use the common detector worker and main histogrammer for flux sources."""
    from dark_photon_detector_runtime import validate_profile
    plan=copy.deepcopy(read(args.preparation));output=args.output.resolve()
    if output.exists():raise FileExistsError('Preserve the existing production: '+str(output))
    validate_physical_preflight(plan)
    inventory=read(args.gen_inventory);validate_plan(plan,inventory)
    check_gen(plan,inventory,read(args.gen_publication),args.prior_inventories)
    settings=read(args.main_histogrammer)
    qualification=settings['qualification'];proof=read(qualification['path'])
    control=proof.get('histogram_positive_control',{})
    if (sha256(qualification['path'])!=qualification['sha256'] or proof.get('complete') is not True or
            proof.get('histogram_runtime')!=settings.get('runtime') or
            control.get('all_seven_logmass_categories_present') is not True or
            control.get('positive_logmass_filled') is not True):
        raise ValueError('Require the exact main histogrammer qualified on a reconstructed positive-mass pair')
    if len(plan['points'])!=1 or plan['events']<=0:
        raise ValueError('Main histogram handoff requires one nonempty complete source cohort')
    row=plan['points'][0];parent=inventory['points'][row['point']]
    source=read(Path(row['gen_directory'])/'material_selected_source.json')
    if (source['event_ids']!=parent['event_ids'] or source['gen_sha256']!=parent['gen_sha256'] or
            len(source['selected_indices'])!=row['events']):
        raise ValueError('Detector handoff differs from the complete frozen admission')
    if row['events']>plan['group_events']:
        raise ValueError('Use smaller source banks to respect the grouped-Nano file budget')
    profile=plan['detector_runtime'];validate_profile(profile)
    output.mkdir(parents=True);frozen=output/'frozen';(frozen/'sources').mkdir(parents=True)
    (frozen/'templates').mkdir();files={}
    for category in ('sources','templates'):
        for name,record in plan['frozen_dependencies'][category].items():
            if sha256(record['frozen_copy'])!=record['sha256']:
                raise ValueError('Changed detector handoff dependency: '+name)
            target=frozen/category/name
            if category=='sources':freeze_detector_source(record['frozen_copy'],target,profile)
            else:shutil.copy2(record['frozen_copy'],target)
            files[str(target)]=sha256(target)
    for name in ('dark_photon_production_support.py','run_shift_sm_postprocessing.py'):
        target=frozen/'sources'/name;shutil.copy2(Path(__file__).with_name(name),target);files[str(target)]=sha256(target)
    integrity=settings['nano_integrity']
    if sha256(integrity['path'])!=integrity['sha256']:raise ValueError('Nano branch-integrity helper changed')
    target=frozen/'sources/shift_nano_integrity.py';shutil.copy2(integrity['path'],target);files[str(target)]=sha256(target)
    gen=Path(row['gen_directory']);contract=read(gen/'contract.json');audit=read(gen/'validation.json')
    source=dict(source,schema='shift-bsm-source-v1',gen_transport='xrootd',gen_path_resolved=True,
        gen=parent['gen_path'],receipt=str(gen/'manifest.json'),receipt_sha256=sha256(gen/'manifest.json'),
        stratum='darkphoton_'+row['point'],output_base=row['final_output_base']+'/detector',
        complete_muon_decays=False,missing_filter_lumi=True,
        signal_contract_sha256=sha256(gen/'contract.json'),
        source_full_graph_sha256=[event['full_graph_sha256'] for event in audit['event_rows']],
        maximum_publication_bytes=settings['maximum_detector_publication_bytes_per_job'],
        predefined_signal_decays=dict(schema='shift-predefined-dark-photon-transport-v1',
            mass_gev=row['mass_gev'],proper_ctau_mm=row['proper_ctau_mm'],
            generated_mass_support_gev=contract['generated_mass_support_gev']))
    write(output/'source.json',source);write(output/'detector_runtime_profile.json',profile)
    write(output/'main_histogrammer.json',settings)
    for name,path in [('gen_inventory.json',args.gen_inventory),('gen_publication.json',args.gen_publication)]:
        write(output/name,read(path))
    plan.update(schema='shift-dark-photon-main-histograms-v1',runtime=plan.get('runtime',{}),
        new_worker_cap=args.detector_workers,main_histogrammer=True,
        original_preparation_sha256=sha256(args.preparation))
    write(output/'plan.json',plan)
    files.update({str(output/name):sha256(output/name) for name in
        ('source.json','detector_runtime_profile.json','main_histogrammer.json','gen_inventory.json','gen_publication.json','plan.json')})
    files[str(qualification['path'])]=qualification['sha256']
    files[profile['manifest']['path']]=profile['manifest']['sha256']
    files.update({str(gen/name):sha256(gen/name) for name in ('contract.json','validation.json','manifest.json')})
    (output/'statuses').mkdir();(output/'completed_points').mkdir();(output/'logs').mkdir()
    helper=frozen/'sources/dark_photon_production_support.py'
    bootstrap=frozen/'sources/dark_photon_detector_runtime.py'
    # Each entry checks the stage seal from a persistent directory, then runs
    # the common payload in worker scratch. Detector and finalizer are ordinary
    # DAG nodes: a failed/missing response cannot trigger histogram publication.
    for action in ('detector','finalize'):
        script=output/(action+'_worker.sh')
        argv=['/usr/bin/python3',str(bootstrap),'--profile',str(output/'detector_runtime_profile.json'),
              '--entry',str(helper),'--','--main-stage',str(output),'--action',action]
        entry=shlex.join(argv)+' "$@"'
        if action=='finalize':
            entry=('unset PYTHONHOME PYTHONPATH LD_LIBRARY_PATH LD_PRELOAD ROOTSYS\n'
                'source /cvmfs/sft.cern.ch/lcg/views/LCG_107/x86_64-el9-gcc14-opt/setup.sh\n'
                'exec python3 '+shlex.join([str(helper),'--main-stage',str(output),'--action',action]))
        else:entry='exec '+entry
        script.write_text('#!/bin/bash\n# Code style: AI-code. Frozen main-histogram production entry.\n'
            'set -eo pipefail\ncd '+shlex.quote(str(output))+'\n'+entry+'\n')
        script.chmod(0o700);subprocess.run(['bash','-n',str(script)],check=True)
        files[str(script)]=sha256(script)
        submit=output/(action+'.sub')
        submit.write_text('universe = vanilla\nexecutable = '+str(script)+'\narguments = '+
            ('--chunk $(chunk)' if action=='detector' else '')+'\ninitialdir = '+str(output)+
            '\ngetenv = False\nshould_transfer_files = NO\nrequest_cpus = 1\nrequest_memory = 4000MB\n'
            'request_disk = 8000MB\n+MaxRuntime = '+str(settings.get('worker_runtime_seconds',43200))+
            '\n+SendCredential = True\n+WantIOProxy = True\n'
            '+ShiftDPStatisticsTask = '+json.dumps(str(output))+'\n+JobBatchName = "SHIFT_DP_main_histograms"\n'
            'output = '+str(output/'logs')+'/'+action+'.$(ClusterId).$(ProcId).out\n'
            'error = '+str(output/'logs')+'/'+action+'.$(ClusterId).$(ProcId).err\nlog = '+str(output/'events.log')+
            '\non_exit_hold = (ExitBySignal == True) || (ExitCode != 0)\nperiodic_release = False\nqueue\n')
        files[str(submit)]=sha256(submit)
    nodes=['JOB P '+str(output/'primary_preflight/worker.sub')];children=[]
    for index,chunk in enumerate(row['chunks']):
        name='D'+str(index);children.append(name)
        nodes.extend(['JOB '+name+' '+str(output/'detector.sub'),
                      'VARS '+name+' chunk="'+str(index)+'"','CATEGORY '+name+' DETECTOR'])
    nodes.extend(['JOB F '+str(output/'finalize.sub'),'CATEGORY F FINALIZER',
        'PARENT P CHILD '+' '.join(children),'PARENT '+' '.join(children)+' CHILD F',
        'MAXJOBS DETECTOR '+str(args.detector_workers),'MAXJOBS FINALIZER 1'])
    (output/'pipeline.dag').write_text('\n'.join(nodes)+'\n')
    files[str(output/'pipeline.dag')]=sha256(output/'pipeline.dag')
    write(output/'seal.json',dict(schema='shift-dark-photon-main-histograms-seal-v1',files=files))
    from prepare_dark_photon_kinematics_preflight import prepare,pin
    prepare(output,pin(output/'detector_runtime_profile.json'),output/'primary_preflight')
    return dict(directory=str(output),prepared=True,submitted=False,detector_chunks=len(children),main_histogrammer=True)


def telemetry_derivation(original, replacement):
    """Permit only the reviewed progress reporter and its empty process cache."""
    def unchanged_body(path, cache_allowed):
        module = ast.parse(Path(path).read_text())
        body, progress, caches = [], [], 0
        for node in module.body:
            if isinstance(node, ast.FunctionDef) and node.name == 'progress':
                progress.append(node)
                continue
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name) and node.targets[0].id == '_last_chirp_values'):
                if not cache_allowed or not isinstance(node.value, ast.Dict) or node.value.keys:
                    raise ValueError('Telemetry derivation requires one empty process-local cache')
                caches += 1
                continue
            body.append(ast.dump(node, include_attributes=False))
        if len(progress) != 1 or caches != int(cache_allowed):
            raise ValueError('Telemetry derivation requires exactly its progress function and cache')
        return body, progress[0]
    before, original_progress = unchanged_body(original, False)
    after, replacement_progress = unchanged_body(replacement, True)
    if (before != after or ast.dump(original_progress.args) != ast.dump(replacement_progress.args)
            or original_progress.decorator_list != replacement_progress.decorator_list):
        raise ValueError('Telemetry derivation changed physics, configuration or audit code')
    return dict(profile='deduplicated-chirp-v1', original_source=str(original),
        original_sha256=sha256(original), replacement_source=str(replacement),
        replacement_sha256=sha256(replacement),
        allowed_ast_changes=['progress', '_last_chirp_values = {}'],
        other_physics_configuration_and_audit_ast_unchanged=True)


def freeze_detector_source(source, target, runtime_profile=None):
    """Keep qualified sources, allowing the reviewed telemetry-only sibling."""
    source, target = Path(source), Path(target)
    selected, derivation = source, None
    if runtime_profile is not None:
        from dark_photon_detector_runtime import CORE_SOURCES, validate_profile
        manifest = validate_profile(runtime_profile)
        if target.name in CORE_SOURCES:
            expected = manifest['files']['workflow_helpers/' + target.name]['sha256']
            if sha256(source) != expected:
                raise ValueError('Detector source differs from its qualified private capsule: ' + target.name)
        shutil.copy2(source, target)
        return dict(source=str(source), sha256=sha256(target),
                    runtime_profile=runtime_profile['schema'],
                    private_runtime_sha256=runtime_profile['runtime_sha256'])
    replacement = Path(__file__).with_name('run_shift_gen_to_nano.py')
    if target.name == replacement.name and sha256(source) != sha256(replacement):
        derivation = telemetry_derivation(source, replacement)
        selected = replacement
    shutil.copy2(selected, target)
    metadata = dict(source=str(selected), sha256=sha256(target))
    if derivation is not None:
        if metadata['sha256'] != derivation['replacement_sha256']:
            raise ValueError('Derived telemetry helper changed while freezing')
        metadata['telemetry_derivation'] = derivation
    return metadata


def declared_source_namespaces(previous):
    """Validate reserved source identities without treating them as GEN events."""
    if previous.get('schema') != 'shift-declared-source-namespace-reservation-v1':
        raise ValueError('Declared source namespaces require their explicit reservation schema')
    expected, authorities = set(), set()
    pins = previous.get('source_authorities', [])
    if not isinstance(pins, list):
        raise ValueError('Declared source namespace authorities require explicit pins')
    for authority in pins:
        if (not isinstance(authority, dict) or set(authority) != {'path', 'sha256'}
                or not isinstance(authority['path'], str)):
            raise ValueError('Malformed declared source namespace authority')
        path = Path(authority['path'])
        if (not path.is_absolute() or '..' in path.parts or str(path) in authorities
                or sha256(path) != authority['sha256']):
            raise ValueError('Declared source namespace authority changed or is ambiguous')
        authorities.add(str(path))
        source = read(path)
        rows = source.get('batches', []) + [row for wave in source.get('waves', []) for row in wave['batches']]
        if not rows:
            raise ValueError('Declared source namespace authority has no source worklist')
        for row in rows:
            expected.add((str(path), row['point'], row['seed'], row['run_number']))
    actual, runs, seeds = set(), set(), set()
    points = previous.get('points')
    if not authorities or not isinstance(points, dict) or not points:
        raise ValueError('Declared source namespaces require pinned nonempty source authorities')
    required = {'declared_source_namespace_only', 'point', 'seed', 'run_number', 'source_configuration'}
    for row in points.values():
        if (not isinstance(row, dict) or set(row) != required
                or row['declared_source_namespace_only'] is not True
                or not isinstance(row['point'], str) or not re.fullmatch(r'[A-Za-z0-9_-]+', row['point'])
                or type(row['seed']) is not int or not 1 <= row['seed'] < 900000000
                or type(row['run_number']) is not int or not 1 <= row['run_number'] <= 4294967295
                or row['run_number'] in runs or row['seed'] in seeds):
            raise ValueError('Malformed declared source namespace; event/exposure fields are not allowed')
        identity = (row['source_configuration'], row['point'], row['seed'], row['run_number'])
        if identity not in expected:
            raise ValueError('Declared source namespace differs from its pinned source worklist')
        actual.add(identity)
        runs.add(row['run_number']); seeds.add(row['seed'])
    if actual != expected:
        raise ValueError('Declared source namespace reservation omits part of its pinned worklists')
    return runs, seeds


def check_gen(plan, inventory, publication, prior_inventories):
    """Bind exact audited full GEN populations and reject reused identities."""
    names = {row['point'] for row in plan['points']}
    published = {row['point']: row for row in publication['inputs']}
    if (inventory.get('complete') is not True or publication.get('complete') is not True or
            set(inventory['points']) != names or set(published) != names or
            len(publication['inputs']) != len(names)):
        raise ValueError('Complete GEN inventory/publication must cover exactly the new points')
    identities, runs, seeds, previous_ids, previous_runs, previous_seeds = set(), set(), set(), set(), set(), set()
    for path in prior_inventories:
        previous = read(path)
        if (previous.get('schema') == 'shift-declared-source-namespace-reservation-v1'
                or any('declared_source_namespace_only' in row for row in previous['points'].values())):
            reserved_runs, reserved_seeds = declared_source_namespaces(previous)
            previous_runs.update(reserved_runs)
            previous_seeds.update(reserved_seeds)
            continue
        for row in previous['points'].values():
            if 'event_ids' not in row:
                raise ValueError('Prior GEN inventory lacks audited event identities or explicit source declarations')
            previous_ids.update(map(tuple, row['event_ids']))
            previous_runs.update(event[0] for event in row['event_ids'])
            if 'seed' in row:
                previous_seeds.add(row['seed'])
    for row in plan['points']:
        point, count = row['point'], row.get('full_gen_events', row['events'])
        parent, payload = inventory['points'][point], published[point]
        actual = list(map(tuple, parent['event_ids']))
        gen = Path(row['gen_directory'])
        audit, contract, manifest = [read(gen / name) for name in
                                    ('validation.json', 'contract.json', 'manifest.json')]
        validate_event_ids(audit['event_ids'], contract)
        if (parent['event_ids'] != audit['event_ids'] or audit.get('runtime_validated') is not True or
                audit['events'] != count or
                audit['sum_weights'] != count or audit['sum_weights_squared'] != count or
                parent['events'] != count or parent['native_sumw'] != count or parent['native_sumw2'] != count or
                any(item['weight'] != 1. for item in audit['event_rows'])):
            raise ValueError('GEN population or native weights changed: ' + point)
        if (identities.intersection(actual) or previous_ids.intersection(actual) or
                row['run_number'] in runs | previous_runs or row['seed'] in seeds | previous_seeds):
            raise ValueError('GEN identities/runs/seeds overlap another population: ' + point)
        identities.update(actual); runs.add(row['run_number']); seeds.add(row['seed'])
        if (contract != read(row['signal_contract']) or contract['requested_events'] != count or
                any(contract[key] != row[key] for key in ('mass_gev', 'epsilon', 'seed', 'run_number')) or
                any(parent[key] != row[key] for key in ('mass_gev', 'epsilon', 'seed', 'run_number'))):
            raise ValueError('Runtime GEN contract differs from native preflight: ' + point)
        for name, key in [('manifest.json', 'manifest_sha256'), ('contract.json', 'contract_sha256'),
                          ('validation.json', 'validation_sha256')]:
            if parent[key] != sha256(gen / name):
                raise ValueError('GEN metadata changed: ' + point + '/' + name)
        recovery = manifest.get('audit_recovery')
        if recovery:
            revision_script = Path(recovery['script'])
            if not revision_script.is_relative_to(gen / 'audit_revisions'):
                raise ValueError('GEN audit revision is outside its retained evidence directory')
            revision = revision_script.parent
            for name, expected in [('audit_dark_photon_gen.py', recovery['sha256']),
                                   ('pythia_generation_ledger.py', recovery['ledger_script_sha256']),
                                   ('previous_manifest.json', recovery['previous_manifest_sha256'])]:
                if sha256(revision / name) != expected:
                    raise ValueError('Recovered GEN audit evidence changed: ' + point + '/' + name)
        destination = row['final_output_base'] + '/GEN/gen.root'
        if (parent['gen_path'] != destination or row.get('gen_remote_path', destination) != destination or
                payload['path'] != destination or payload['sha256'] != parent['gen_sha256'] or
                payload['readback_sha256'] != parent['gen_sha256'] or
                manifest['output_sha256'] != parent['gen_sha256'] or payload['bytes'] <= 0):
            raise ValueError('GEN publication is not the audited canonical payload: ' + point)
    return dict(complete=True, events=len(identities), distinct_parent_events=len(identities),
        overlapping_event_ids=[], overlapping_runs=[], overlapping_seeds=[],
        new_runs=sorted(runs), new_seeds=sorted(seeds),
        prior_gen_authorities=[dict(path=str(Path(path).resolve()), sha256=sha256(path))
                               for path in prior_inventories])


def dag_text(plan, output, gates, detector_cap, finalizer_cap):
    gate_nodes = ['D_' + point + '_000' for point in gates]
    lines, finalizers = [], []
    for row in plan['points']:
        preflight = 'P_' + row['point'] if plan.get('independent_point_preflights') is True else None
        if preflight is not None:
            lines.extend([f'JOB {preflight} {output}/point_preflights/{row["point"]}/worker.sub',
                          f'CATEGORY {preflight} PREFLIGHT'])
        parents = []
        for chunk in row['chunks']:
            node = f"D_{row['point']}_{chunk['chunk']:03d}"
            lines.extend([f'JOB {node} {output}/detector.sub',
                f'VARS {node} point="{row["point"]}" chunk="{chunk["chunk"]}" kind="detector" seal="SEAL_DIGEST"',
                f'CATEGORY {node} DETECTOR'])
            if gate_nodes and node not in gate_nodes:
                lines.append(f'PARENT {" ".join(gate_nodes)} CHILD {node}')
            if preflight is not None:
                lines.append(f'PARENT {preflight} CHILD {node}')
            parents.append(node)
        finalizer = 'F_' + row['point']
        finalizers.append(finalizer)
        lines.extend([f'JOB {finalizer} {output}/finalizer.sub',
            f'VARS {finalizer} point="{row["point"]}" chunk="group" kind="finalizer" seal="SEAL_DIGEST"',
            f'CATEGORY {finalizer} FINALIZER',
            f'PARENT {" ".join(sorted(set(parents + gate_nodes)))} CHILD {finalizer}'])
    lines.extend([f'JOB COLLECT {output}/collect.sub',
        'VARS COLLECT point="all" chunk="all" kind="collect" seal="SEAL_DIGEST"',
        f'PARENT {" ".join(finalizers)} CHILD COLLECT',
        f'MAXJOBS DETECTOR {detector_cap}', f'MAXJOBS FINALIZER {finalizer_cap}'])
    if plan.get('independent_point_preflights') is True:
        lines.append(f'MAXJOBS PREFLIGHT {len(plan["points"])}')
    return '\n'.join(lines) + '\n'


def prepare_independent_point_preflights(production):
    """Prepare independent native scans after the original plan/seal are fixed."""
    import importlib.util
    production = Path(production)
    plan = read(production/'plan.json')
    if plan.get('independent_point_preflights') is not True:
        return None
    helper = production/'independent_preflight_helpers/prepare_dark_photon_kinematics_preflight.py'
    sealed = read(production/'seal.json')['files']
    for path in (helper, helper.with_name('dark_photon_detector_runtime.py')):
        if sealed.get(str(path)) != sha256(path):
            raise ValueError('Per-point preflight helper differs from the production seal')
    spec = importlib.util.spec_from_file_location('_dp_point_gen_preflight', helper)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    profile = dict(path=str(production/'frozen/detector_runtime.json'),
                   sha256=sha256(production/'frozen/detector_runtime.json'))
    points = {}
    for row in plan['points']:
        directory = production/'point_preflights'/row['point']
        marker = directory/'preparation.json'
        if not directory.exists():
            module.prepare(production, profile, directory, point_scope=[row['point']])
        prepared = read(marker)
        for key in ('config', 'worker', 'submit'):
            pin = prepared[key]
            if sha256(pin['path']) != pin['sha256']:
                raise ValueError('Per-point GEN preflight task changed: ' + row['point'])
        config = read(prepared['config']['path'])
        if (config.get('point_scope') != [row['point']]
                or config.get('original_plan', {}).get('sha256') != sha256(production/'plan.json')
                or config.get('original_seal', {}).get('sha256') != sha256(production/'seal.json')
                or config.get('replacement_profile') != profile):
            raise ValueError('Per-point GEN preflight differs from its original physical point')
        points[row['point']] = prepared
    manifest = dict(schema='shift-independent-point-preflights-v1', complete=True,
        original_plan_sha256=sha256(production/'plan.json'),
        original_seal_sha256=sha256(production/'seal.json'),
        original_inventory_sha256=sha256(production/'gen_inventory.json'),
        replacement_runtime_sha256=plan['detector_runtime']['runtime_sha256'],
        pipeline_dag_sha256=sha256(production/'pipeline.dag'), points=points)
    output = production/'point_preflights/manifest.json'
    if output.exists():
        if read(output) != manifest:
            raise ValueError('Earlier per-point GEN preflight preparation changed')
    else:
        write(output, manifest)
    return manifest


def detector_category_ceiling(args):
    """A larger frozen category permits later reviewed live global throttles."""
    ceiling = getattr(args, 'detector_category_ceiling', None)
    if ceiling is None:
        return args.detector_workers
    if type(ceiling) is not int or not args.detector_workers <= ceiling <= 600:
        raise ValueError('Detector category ceiling must cover the initial workers and be at most600')
    return ceiling


def qualified_detector_gates(plan, canary_points, authority_path=None):
    """Reuse a measured global gate only under an explicit frozen authority."""
    names = [row['point'] for row in plan['points']]
    gates = list(canary_points or [])
    if (len(names) != len(set(names)) or len(gates) != len(set(gates))
            or not set(gates) <= set(names)):
        raise ValueError('Require distinct physical points and existing distinct detector gates')
    if authority_path is None:
        if not gates:
            raise ValueError('Require local detector gates or qualified global canary authority')
        return gates, None
    if gates:
        raise ValueError('Choose local detector gates or the qualified global canary, not both')
    dependencies = []
    def pinned(item):
        path = Path(item['path'])
        if not path.is_absolute() or sha256(path) != item['sha256']:
            raise ValueError('Global detector canary dependency changed: ' + str(path))
        dependencies.append(dict(path=str(path), sha256=item['sha256']))
        return read(path)
    authority_path = Path(authority_path).resolve()
    authority = pinned(dict(path=str(authority_path), sha256=sha256(authority_path)))
    if (authority.get('schema') != 'shift-qualified-global-detector-canary-v1'
            or authority.get('complete') is not True
            or authority.get('full_GEN_preflight_required') is not True):
        raise ValueError('Unqualified global detector canary authority')
    runtime = plan.get('detector_runtime', {}).get('runtime_sha256')
    if not runtime or authority.get('runtime_sha256') != runtime:
        raise ValueError('Global detector canary runtime differs from this detector plan')
    qualification = pinned(authority['runtime_qualification'])
    if qualification.get('complete') is not True:
        raise ValueError('Global detector runtime qualification is incomplete')
    profiles = [pinned(row['replacement_profile']) for row in qualification['profiles']]
    if runtime not in {profile['runtime_sha256'] for profile in profiles}:
        raise ValueError('Global detector runtime is absent from its qualification')
    weighted = [pinned(item) for item in qualification['weighted_fullchain']]
    if (not weighted or not any(row.get('complete') is True
            and row.get('replacement_runtime_sha256') == runtime for row in weighted)):
        raise ValueError('Global detector runtime lacks a completed weighted full chain')
    pilot = pinned(authority['handoff_qualification'])
    if (pilot.get('schema') != 'shift-reviewed-meson-handoff-qualification-v1'
            or pilot.get('complete') is not True
            or pilot.get('original_GEN_and_material_selection_unchanged') is not True):
        raise ValueError('Global detector handoff qualification is incomplete')
    for key in ('full_GEN_preflight', 'rates'):
        if pinned(pilot[key]).get('complete') is not True:
            raise ValueError('Global detector pilot stage is incomplete: ' + key)
    pilot_plan = pinned(pilot['detector_plan'])
    if pilot_plan.get('detector_runtime', {}).get('runtime_sha256') != runtime:
        raise ValueError('Global detector pilot used another runtime')
    native = pinned(pilot['replacement_successful_native'])
    if (native.get('ExitCode') != 0 or native.get('ExitBySignal') is not False
            or native.get('JobStatus') != 4):
        raise ValueError('Global detector pilot handoff lacks native success')
    executed = pinned(pilot['real_detector_execute'])
    submission = pinned(pilot['detector_DAG_submission'])
    if not any(row.get('DAGManJobId') == submission.get('cluster')
            and row.get('JobCurrentStartDate', 0) > 0 for row in executed):
        raise ValueError('Global detector pilot lacks an executed detector child')
    if pinned(authority['merged_histogram_canary']).get('complete') is not True:
        raise ValueError('Global detector canary has no complete merged histograms')
    return [], dict(authority=dict(path=str(authority_path), sha256=sha256(authority_path)),
                    dependencies=dependencies)


def extend_worker(text, publication_profile, runtime_profile=None):
    """Version the qualified worker's publication contract in its new copy."""
    old = "    paths = {tier: str(base / tier / name)\n             for tier in ('GEN_SIM', 'DIGI_RAW_HLT', 'AODSIM', 'nanoAOD')}\n"
    replacement = ("    profile = read(B/'plan.json')['publication_profile']\n"
                   "    if profile == 'nano-metadata-v1':\n"
                   "        tiers = ('nanoAOD',)\n"
                   "    elif profile == 'all-tiers-v1':\n"
                   "        tiers = ('GEN_SIM', 'DIGI_RAW_HLT', 'AODSIM', 'nanoAOD')\n"
                   "    else:\n"
                   "        raise ValueError('Unknown signal publication profile')\n"
                   "    paths = {tier:str(base/tier/name) for tier in tiers}\n")
    start, stop = text.index('def publication_paths(chunk):'), text.index('def valid_digest(value):')
    if old not in text[start:stop]:
        raise ValueError('Qualified publication namespace changed; review the new worker contract')
    text = text[:start] + text[start:stop].replace(old, replacement) + text[stop:]
    validation = "    destinations = publication_paths(chunk)\n    published = status.get('published', {})\n"
    if text.count(validation) != 1:
        raise ValueError('Qualified chunk receipt validation changed')
    text = text.replace(validation,
        "    profile = plan.get('publication_profile', 'all-tiers-v1')\n"
        "    if status.get('publication_profile') != profile:\n"
        "        raise ValueError('Detector receipt publication profile changed')\n"
        "    if profile == 'nano-metadata-v1':\n"
        "        scratch = status.get('audited_scratch_tiers', {})\n"
        "        if set(scratch) != {'GEN_SIM', 'DIGI_RAW_HLT', 'AODSIM'}:\n"
        "            raise ValueError('Nano-only receipt lacks full-chain scratch evidence')\n"
        "        for item in scratch.values():\n"
        "            if (not valid_digest(item.get('sha256')) or type(item.get('bytes')) is not int or item['bytes'] <= 0):\n"
        "                raise ValueError('Audited scratch-tier evidence is incomplete')\n"
        + validation)
    files = "        files['metadata'] = bundle\n"
    if text.count(files) != 1:
        raise ValueError('Qualified full-chain publication inventory changed')
    text = text.replace(files,
        "        tiers = plan['publication_tiers']\n"
        "        receipt.update(publication_profile=plan['publication_profile'],\n"
        "            audited_scratch_tiers={tier:dict(sha256=sha(local),bytes=local.stat().st_size)\n"
        "                                  for tier,local in files.items() if tier not in tiers})\n"
        "        files = {tier:local for tier,local in files.items() if tier in tiers}\n"
        + files)
    quota = '    minimum_bytes, minimum_files = quota_requirements(budget, count)\n'
    if text.count(quota) != 1:
        raise ValueError('Qualified worker quota hook changed; review before extending')
    # Extension/recovery templates can already have this hook. Freeze it once.
    if 'from dark_photon_publication_quota import external_chunk_reservations' not in text:
        text = text.replace(quota, quota +
            '    from dark_photon_publication_quota import external_chunk_reservations\n'
            '    extra_bytes, extra_files, external = external_chunk_reservations(budget)\n'
            '    minimum_bytes += extra_bytes\n'
            '    minimum_files += extra_files\n')
        text = text.replace('result.update(credited_complete_chunks=count,',
                            'result.update(external_reservations=external, credited_complete_chunks=count,')
    detector_limit = "'--timeout','21600'"
    process_limit = 'timeout=22000,check=True'
    if text.count(detector_limit) != 1 or text.count(process_limit) != 1:
        raise ValueError('Qualified detector timeout invocation changed; review before extending')
    text = text.replace(detector_limit, "'--timeout',str(plan['detector_timeout_seconds'])")
    text = text.replace(process_limit, "timeout=plan['detector_timeout_seconds']+600,check=True")
    if runtime_profile is not None:
        text = private_runtime_worker(text)
    # The shared worker slices admitted offsets; retain the full parent ledger.
    module = ast.parse(text)
    function, = [node for node in module.body
                 if isinstance(node, ast.FunctionDef) and node.name == 'chunk_spec']
    lines = text.splitlines(keepends=True)
    lines[function.lineno-1:function.end_lineno] = [
        'def chunk_spec(plan, inventory, point, chunk_id):\n',
        '    from dark_photon_material_selection import chunk_spec as selected_chunk_spec\n',
        '    return selected_chunk_spec(plan, inventory, point, chunk_id)\n']
    text = ''.join(lines)
    # A selected source is a sealed input, including its original full cohort.
    stage = "    for name in ('manifest.json', 'contract.json', 'validation.json'):\n"
    if text.count(stage) != 1:
        raise ValueError('Qualified GEN staging metadata hook changed')
    text = text.replace(stage,
        "    names = ['manifest.json', 'contract.json', 'validation.json']\n"
        "    if row.get('material_selection') is not None:\n"
        "        descriptor = Path(row['gen_directory'])/'material_selected_source.json'\n"
        "        if sha(descriptor) != row['material_selected_source_sha256']:\n"
        "            raise ValueError('Material descriptor changed before GEN staging')\n"
        "        names.append('material_selected_source.json')\n"
        "    for name in names:\n")
    return text


def private_runtime_worker(text):
    """Use the independently pinned capsule only under the explicit new profile."""
    original = """    fingerprint=read(B/'frozen/runtime_fingerprint.json')
    for path,row in fingerprint['files'].items():
        if sha(path)!=row['sha256']:raise ValueError('Shared runtime changed: '+path)
"""
    replacement = """    from dark_photon_detector_runtime import validate_profile
    runtime = read(B/'frozen/detector_runtime.json')
    if runtime != read(B/'plan.json')['detector_runtime']:
        raise ValueError('Private detector runtime differs from its sealed plan')
    validate_profile(runtime)
"""
    hook = "        with (work/'launch.log').open('x') as log:\n"
    if text.count(original) != 1 or text.count(hook) != 1:
        raise ValueError('Qualified worker runtime hooks changed; review the private deployment')
    text = text.replace(original, replacement)
    text = text.replace(hook, "        command.append('--transport-predefined-signal-decays')\n" + hook)
    text = text.replace("'shared_runtime_fingerprint_sha256':sha(B/'frozen/runtime_fingerprint.json')",
                        "'template_runtime_fingerprint_sha256':sha(B/'frozen/runtime_fingerprint.json'),\n"
                        "             'private_detector_runtime_sha256':plan['detector_runtime']['runtime_sha256']")
    return text


def private_runtime_shell(text, output, frozen):
    # Keep the shell alive so its EXIT trap returns the bounded failure archive.
    return (text[:text.index('unset PYTHONPATH')]
            + '/usr/bin/env -u PYTHONHOME -u PYTHONPATH -u LD_LIBRARY_PATH -u LD_PRELOAD -u ROOTSYS '
            + '/usr/bin/python3 ' + str(output / 'dark_photon_detector_runtime.py')
            + ' --profile ' + str(frozen / 'detector_runtime.json')
            + ' --entry ' + str(output / 'chunk_worker.py') + ' -- "$@"\n')


def deployment_wrapper(kind):
    """Keep deployed entry points small; freeze their reusable implementation."""
    if kind == 'finalizer':
        return '''#!/usr/bin/env python3
# Code style: AI-code. AI-generated immutable grouped signal finalizer.
import argparse
import json
from pathlib import Path
from dark_photon_production_support import finalize as run

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('point')
    parser.add_argument('seal')
    parser.add_argument('--runtime-cache', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.point, args.seal, args.runtime_cache)))
'''
    return '''#!/usr/bin/env python3
# Code style: AI-code. AI-generated immutable grouped signal collector.
import argparse
from dark_photon_production_support import collect

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('seal')
    collect(parser.parse_args().seal)
'''


def detector_timeout_settings(args):
    """Keep longer operational deadlines separate from unchanged physics."""
    limits = dict(event_stall_timeout_seconds=getattr(args, 'event_stall_timeout', 1800),
                  first_event_stall_timeout_seconds=getattr(args, 'first_event_stall_timeout', 3600),
                  detector_timeout_seconds=getattr(args, 'detector_timeout', 21600))
    if any(type(value) is not int or not 0 < value <= 72000 for value in limits.values()):
        raise ValueError('Detector deadlines must be positive integer seconds up to72000')
    if limits['first_event_stall_timeout_seconds'] < limits['event_stall_timeout_seconds']:
        raise ValueError('First-event deadline cannot be shorter than the event-stall deadline')
    if limits['detector_timeout_seconds'] <= limits['first_event_stall_timeout_seconds']:
        raise ValueError('Detector deadline must exceed the first-event deadline')
    return limits


def freeze_absolute_stage_budget(output, stage_timeouts):
    """Seal an operational argv extension without changing qualified sources."""
    if stage_timeouts is None:
        return
    if (not isinstance(stage_timeouts, list) or len(stage_timeouts) != 4
            or any(type(value) is not int or not 7200 <= value <= 43200 for value in stage_timeouts)):
        raise ValueError('Absolute SIM/DIGI/RECO/Nano budgets must each be7200..43200seconds')
    output = Path(output)
    helper = output/'dark_photon_stage_budget.py'
    shutil.copy2(Path(__file__).with_name(helper.name), helper)
    policy = output/'stage_budget_policy.json'
    write(policy, dict(schema='shift-dp-operational-stage-budget-v1',
        production_directory=str(output.resolve()), plan_sha256=sha256(output/'plan.json'),
        worker_sha256=sha256(output/'chunk_worker.py'),
        detector_sha256=sha256(output/'frozen/sources/run_dark_photon_detector.py'),
        common_runner_sha256=sha256(output/'frozen/sources/run_shift_gen_to_nano.py'),
        stage_timeouts_seconds=stage_timeouts, absolute_stage_timeouts=True,
        allowed_slices='all_planned'))
    wrapper = output/'detector_worker.sh'
    text = wrapper.read_text()
    original = ' --entry '+str(output/'chunk_worker.py')+' -- "$@"'
    if text.count(original) != 1:
        raise ValueError('Absolute stage budget requires the qualified private-runtime entry')
    replacement = (' --entry '+str(helper)+' -- --policy '+str(policy)+
                   ' --mode worker -- "$@"')
    # These files also belong to the production seal; preflight protects the
    # dispatch before the original worker verifies the complete common group.
    check = ("/usr/bin/env -u PYTHONHOME -u PYTHONPATH -u LD_LIBRARY_PATH -u LD_PRELOAD /usr/bin/python3 -c "+__import__('shlex').quote(
        "import hashlib;from pathlib import Path;assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in "+
        repr([(str(helper),sha256(helper)),(str(policy),sha256(policy))])+")")+"\n")
    lines = text.splitlines(keepends=True)
    matches = [index for index,line in enumerate(lines) if original in line]
    if len(matches) != 1 or not lines[matches[0]].startswith('/usr/bin/env '):
        raise ValueError('Expected one standalone private-runtime entry line')
    index = matches[0]
    lines[index] = check + lines[index].replace(original,replacement)
    wrapper.write_text(''.join(lines))
    subprocess.run(['bash','-n',str(wrapper)],check=True,capture_output=True,text=True)


def prepare(args):
    if getattr(args,'main_histogrammer',None) is not None:
        return prepare_main_histograms(args)
    timeouts = detector_timeout_settings(args)
    template, output = args.template.resolve(), args.output.resolve()
    if output.exists():
        raise FileExistsError('Refuse to overwrite an existing BSM deployment')
    if not re.fullmatch(r'/eos/user/[A-Za-z0-9_./-]+', args.failure_base) or '..' in Path(args.failure_base).parts:
        raise ValueError('Failure evidence requires an explicit canonical EOS directory')
    template_seal = read(template / 'seal.json')
    for path, expected in template_seal['files'].items():
        if sha256(path) != expected:
            raise ValueError('Qualified template dependency changed: ' + path)
    histogram_qualification = validate_histogram_template(template)
    plan = copy.deepcopy(read(args.preparation))
    runtime_profile = plan.get('detector_runtime')
    independent_preflights = getattr(args, 'independent_point_preflights', False)
    if independent_preflights and runtime_profile is None:
        raise ValueError('Independent native GEN preflights require a frozen private detector runtime')
    if runtime_profile is not None:
        from dark_photon_detector_runtime import validate_profile
        validate_profile(runtime_profile)
    validate_physical_preflight(plan)
    if plan.get('detector_stage_prepared') is not True:
        raise ValueError('Production requires a prepared complete physical-width-qualified detector grid')
    gates, global_gate = qualified_detector_gates(plan, args.canary_points,
        getattr(args, 'qualified_global_canary', None))
    names = [row['point'] for row in plan['points']]
    template_jobs = {chunk['job'] for row in read(template / 'plan.json')['points'] for chunk in row['chunks']}
    jobs = [chunk['job'] for row in plan['points'] for chunk in row['chunks']]
    if len(jobs) != len(set(jobs)) or template_jobs.intersection(jobs):
        raise ValueError('New detector RNG job namespace overlaps its qualified predecessor')
    inventory, publication = read(args.gen_inventory), read(args.gen_publication)
    independence = check_gen(plan, inventory, publication, args.prior_inventories)
    validate_plan(plan, inventory)
    for row in plan['points']:
        validate_complete_groups(dict(row, group_events=plan['group_events']),
                                 population(row, inventory['points'][row['point']])[0])
        row['gen_remote_path'] = inventory['points'][row['point']]['gen_path']
    budget = copy.deepcopy(read(args.storage_budget))
    if budget['detector_chunks'] != plan['detector_chunks'] or budget['events'] != plan['events']:
        raise ValueError('Reviewed storage budget differs from the new complete partition')
    external_chunk_reservations(budget)  # Verify external authorities before freezing.
    profile = getattr(args, 'publication_profile', 'all-tiers-v1')
    canary_path = getattr(args, 'publication_canary', None)
    if profile not in PROFILES:
        raise ValueError('Unknown signal publication profile')
    qualification = None
    if profile == 'nano-metadata-v1':
        if canary_path is None:
            raise ValueError('Nano-only publication requires a pinned measured canary')
        qualification = validate_publication_canary(canary_path, budget,
            max(chunk['count'] for row in plan['points'] for chunk in row['chunks']))
    elif budget.get('publication_profile', profile) != profile:
        raise ValueError('Reviewed budget publication profile differs from the requested worker')
    if not 1 <= args.detector_workers <= 300 or not 1 <= args.finalizers <= 3:
        raise ValueError('Require bounded concurrency up to300detectorworkers and3finalizers')
    category_ceiling = detector_category_ceiling(args)
    output.mkdir(parents=True, exist_ok=False)
    if independent_preflights:
        helpers = output/'independent_preflight_helpers'
        helpers.mkdir()
        for name in ('prepare_dark_photon_kinematics_preflight.py', 'dark_photon_detector_runtime.py'):
            shutil.copy2(Path(__file__).with_name(name), helpers/name)
    for directory in ('logs', 'failures', 'statuses', 'completed_points'):
        (output / directory).mkdir()
    for directory in ('canary', 'frozen/scoring'):
        shutil.copytree(template / directory, output / directory,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    frozen = output / 'frozen'
    shutil.copy2(template / 'frozen/runtime_fingerprint.json', frozen / 'runtime_fingerprint.json')
    if runtime_profile is not None:
        authority = runtime_profile['manifest']
        target = frozen / 'detector_runtime_manifest.json'
        shutil.copy2(authority['path'], target)
        runtime_profile['manifest'] = dict(path=str(target), sha256=sha256(target))
        write(frozen / 'detector_runtime.json', runtime_profile)
        shutil.copy2(Path(__file__).with_name('dark_photon_detector_runtime.py'),
                     output / 'dark_photon_detector_runtime.py')
    dependencies = {}
    for category in ('sources', 'templates'):
        (frozen / category).mkdir()
        for name, item in plan['frozen_dependencies'][category].items():
            source = Path(item['frozen_copy'])
            if sha256(source) != item['sha256']:
                raise ValueError('Native-qualified frozen dependency changed: ' + str(source))
            target = frozen / category / name
            dependencies[str(target)] = freeze_detector_source(source, target, runtime_profile)
    for name in ('chunk_worker.py', 'finalize_point.py', 'collect_inputs.py', 'preserve_failure.py',
                 'detector_worker.sh', 'finalizer_worker.sh', 'collect_worker.sh',
                 'detector.sub', 'finalizer.sub', 'collect.sub'):
        text = (template / name).read_text().replace(str(template), str(output))
        if name.endswith('.sub'):
            text = re.sub(r'^(?:\+SendCredential|on_exit_hold|on_exit_remove|periodic_release)\s*=.*\n?', '', text, flags=re.MULTILINE | re.IGNORECASE)
            text = text.replace('\nqueue', '\n+SendCredential = True\non_exit_hold = (ExitBySignal == True) || (ExitCode != 0)\non_exit_remove = (ExitBySignal == False) && (ExitCode == 0)\nperiodic_release = False\nqueue')
            text = re.sub(r'^\+JobBatchName = .*$', '+JobBatchName = "SHIFT_BSM_' + plan['campaign'] + '"',
                          text, flags=re.MULTILINE)
            # CERN supplies the supported EL9 requirement. Its submit hook warns
            # after committing jobs that also request this obsolete attribute.
            text = text.replace('requirements = (TARGET.OpSysMajorVer == 9)\n', '')
        if name == 'preserve_failure.py':
            text = re.sub(r'^REMOTE_BASE = .*$', 'REMOTE_BASE = ' + repr(args.failure_base), text,
                          flags=re.MULTILINE)
        if name == 'chunk_worker.py':
            text = extend_worker(text, profile, runtime_profile)
        elif name == 'detector_worker.sh' and runtime_profile is not None:
            text = private_runtime_shell(text, output, frozen)
        elif name == 'finalize_point.py':
            # Retain the exact qualified stage/weight/scoring validators in the
            # new namespace; the new entry point supports all bounded groups.
            (output / 'qualified_finalizer.py').write_text(text)
            text = deployment_wrapper('finalizer')
        elif name == 'collect_inputs.py':
            text = deployment_wrapper('collector')
        if name == 'detector.sub' and timeouts['detector_timeout_seconds'] > 21600:
            # Set the matching request before submission; a running job cannot
            # safely acquire a longer enforced lifetime through a later qedit.
            text = re.sub(r'^\+JobFlavour = .*$', '+JobFlavour = "tomorrow"', text, flags=re.MULTILINE)
            text = text.replace('\nqueue', '\n+MaxRuntime = 86400\nqueue')
        if name == 'detector.sub':
            # Permit condor_chirp to publish the worker's actual stage progress.
            if re.search(r'^\+?WantIOProxy\s*=', text, flags=re.MULTILINE | re.IGNORECASE):
                text = re.sub(r'^\+?WantIOProxy\s*=.*$', '+WantIOProxy = True', text,
                              flags=re.MULTILINE | re.IGNORECASE)
            else:
                text = text.replace('\nqueue', '\n+WantIOProxy = True\nqueue')
        (output / name).write_text(text)
        if name.endswith('.sh'):
            (output / name).chmod(0o750)
    shutil.copy2(Path(__file__).with_name('dark_photon_publication_quota.py'),
                 output / 'dark_photon_publication_quota.py')
    shutil.copy2(Path(__file__).with_name('dark_photon_production_support.py'),
                 output / 'dark_photon_production_support.py')
    for name in ('dark_photon_material_selection.py', 'shift_production_selection.py',
                 'shift_material_preselection.py'):
        shutil.copy2(Path(__file__).with_name(name), output / name)
    if qualification is not None:
        write(output / 'publication_qualification.json', qualification)
    for name in ('postprocess_canary.json', 'shared_dependency_gate.json'):
        shutil.copy2(template / name, output / name)
    score = (template / 'scoring_components.json').read_text().replace(str(template), str(output))
    score = json.loads(score)
    score['standard_weight_precision'] = histogram_qualification['weight_precision']
    write(output / 'scoring_components.json', score)
    plan.update(schema='shift-bdt-independent-signal-stage-v1', prepared=True, submitted=False,
        standard_histogram_qualification=histogram_qualification,
        sample_role='validation_only', new_worker_cap=args.detector_workers,
        finalizer_concurrency=args.finalizers, detector_gate_points=gates,
        detector_gate_chunk=(0 if gates else None), **timeouts,
        frozen_dependencies=dependencies, selection_applied=False, model_training_on_signals=False,
        reference_preparation=str(args.preparation.resolve()), reference_preparation_sha256=sha256(args.preparation),
        runtime_fingerprint=str(frozen / 'runtime_fingerprint.json'),
        runtime_fingerprint_sha256=sha256(frozen / 'runtime_fingerprint.json'),
        publication_profile=profile, publication_tiers=list(PROFILES[profile]),
        detector_intermediates_policy=('Audited in worker scratch; retain all non-ROOT audit metadata and tier SHA-256/bytes'
                                      if profile == 'nano-metadata-v1' else 'Retain all audited detector ROOT tiers'),
        finalizer_contract='Complete ordered GEN union; all bounded Nano groups precede histogramming; validated histogram sum')
    if global_gate is not None:
        plan['qualified_global_detector_canary'] = global_gate['authority']
    if independent_preflights:
        plan['independent_point_preflights'] = True
    if getattr(args, 'detector_category_ceiling', None) is not None:
        plan['detector_category_ceiling'] = category_ceiling
    write(output / 'plan.json', plan)
    write(output / 'gen_inventory.json', inventory)
    write(output / 'gen_publication.json', publication)
    budget['stage2_plan_sha256'] = sha256(output / 'plan.json')
    budget['stage2_gen_inventory_sha256'] = sha256(output / 'gen_inventory.json')
    write(output / 'storage_budget.json', budget)
    independence.update(plan_sha256=sha256(output / 'plan.json'),
                        actual_gen_inventory_sha256=sha256(output / 'gen_inventory.json'))
    write(output / 'independence_gate.json', independence)
    write(output / 'historical_qualification.json', dict(qualification_role=(
        'Qualified global weighted full chain; this cohort still requires its complete GEN preflight'
        if global_gate else 'Earlier recipe qualification; new detector gates are required'),
        template_directory=str(template), template_seal_sha256=sha256(template / 'seal.json'),
        new_detector_gates_complete=False, dag_gates=gates,
        qualified_global_detector_canary=(global_gate['authority'] if global_gate else None),
        final_recording_acceptance_validated=False))
    freeze_absolute_stage_budget(output, getattr(args, 'absolute_stage_timeouts', None))
    dag = dag_text(plan, output, gates, category_ceiling, args.finalizers)
    (output / 'pipeline.dag.template').write_text(dag)
    common = [path for path in output.iterdir() if path.is_file()]
    if independent_preflights:
        common.extend(path for path in (output/'independent_preflight_helpers').iterdir() if path.is_file())
    common.extend(path for path in (output / 'canary').rglob('*') if path.is_file())
    common.extend(path for category in ('sources', 'templates')
                  for path in (frozen / category).rglob('*') if path.is_file())
    common.extend([frozen / 'runtime_fingerprint.json', args.preparation.resolve()])
    if runtime_profile is not None:
        common.extend([frozen / 'detector_runtime.json', frozen / 'detector_runtime_manifest.json'])
    if qualification is not None:
        common.extend(Path(item['path']) for item in qualification['dependencies'])
    if global_gate is not None:
        common.extend(Path(item['path']) for item in global_gate['dependencies'])
    scoring = [output / 'scoring_components.json']
    scoring.extend(path for path in (frozen / 'scoring').rglob('*') if path.is_file())
    gen = {row['point']: sorted(str(path) for path in Path(row['gen_directory']).rglob('*')
                               if path.is_file() and path.suffix != '.root' and '__pycache__' not in path.parts)
           for row in plan['points']}
    paths = set(common + scoring)
    paths.update(Path(path) for values in gen.values() for path in values)
    seal = dict(schema='shift-independent-signal-production-seal-v1', campaign=plan['campaign'],
        created_utc=datetime.now(timezone.utc).isoformat(), files={str(path): sha256(path) for path in sorted(paths)},
        verification_groups=dict(common=sorted(set(map(str, common))), gen=gen,
                                 scoring=sorted(set(map(str, scoring)))),
        events=plan['events'], signal_points=len(names), detector_jobs=plan['detector_chunks'], finalizer_jobs=len(names),
        selection_applied=False, sample_role='validation_only',
        detector_bulk_gated_by_new_native_canaries=bool(gates),
        qualified_global_detector_canary=(global_gate['authority'] if global_gate else None),
        canonical_GEN_payloads_pinned_by_inventory_and_verified_on_worker_stage_in=True)
    write(output / 'seal.json', seal)
    (output / 'pipeline.dag').write_text(dag.replace('SEAL_DIGEST', sha256(output / 'seal.json')))
    if independent_preflights:
        prepare_independent_point_preflights(output)
    return dict(output=str(output), campaign=plan['campaign'], events=plan['events'], points=len(names),
        detector_jobs=plan['detector_chunks'], seal_sha256=sha256(output / 'seal.json'), submitted=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('preparation', 'template', 'gen-inventory', 'gen-publication', 'storage-budget', 'output'):
        parser.add_argument('--' + name, required=True, type=Path)
    parser.add_argument('--prior-inventories', required=True, nargs='+', type=Path)
    parser.add_argument('--canary-points', nargs='+')
    parser.add_argument('--qualified-global-canary', type=Path,
                        help='Pinned measured runtime/full-chain/handoff authority replacing repeated local canary barriers')
    parser.add_argument('--independent-point-preflights', action='store_true',
                        help='Require each physical point full-GEN scan before its own detector chunks')
    parser.add_argument('--failure-base', required=True)
    parser.add_argument('--detector-workers', type=int, default=100)
    parser.add_argument('--detector-category-ceiling', type=int,
                        help='Frozen category ceiling for reviewed live worker redistribution; initial workers stay unchanged')
    parser.add_argument('--finalizers', type=int, default=3)
    parser.add_argument('--event-stall-timeout', type=int, default=1800)
    parser.add_argument('--first-event-stall-timeout', type=int, default=3600)
    parser.add_argument('--detector-timeout', type=int, default=21600,
                        help='Total detector worker deadline in seconds, at most72000; physics is unchanged')
    parser.add_argument('--absolute-stage-timeouts', type=int, nargs=4,
                        help='Explicit absolute SIM/DIGI/RECO/Nano wall budgets; physics is unchanged')
    parser.add_argument('--publication-profile', choices=tuple(PROFILES), default='all-tiers-v1')
    parser.add_argument('--publication-canary', type=Path,
                        help='Pinned measured full-chain Nano/metadata canary required for nano-metadata-v1')
    parser.add_argument('--main-histogrammer',type=Path,
                        help='Qualified main histogrammer runtime and Nano integrity helper; bypass legacy BDT scoring')
    print(json.dumps(prepare(parser.parse_args())))


if __name__ == '__main__':
    main()
