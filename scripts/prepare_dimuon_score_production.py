#!/usr/bin/env python3
"""Freeze a simulation-only V10 Nano scoring plan; never submit or publish it.

The optional generated worker stages with XRootD and publishes distinct scored
copies after checksum/content verification. It is invoked only by an explicit
later worker launch. Every input, including files without dimuons, is retained.
"""
import argparse
import ast
from collections import Counter
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tarfile

WORKSPACE = Path(__file__).resolve().parents[2]
SOURCE_ROOT = '/eos/user/j/jniedzie/shift_cmssw/ntuple_production/shift_detector_representative_20261007_v10'
OUTPUT_ROOT = SOURCE_ROOT + '_bdt_v1'
EOS_BASE = '/eos/user/j/jniedzie/shift_cmssw'
DEFAULT_SCORE_CAMPAIGN = 'shift_detector_20261009_v10_bdt_v2'
DEFAULT_MIGRATION_MANIFEST = WORKSPACE / 'validation/storage_reorganization_20261009/path_map.json'
DEFAULT_STORAGE_HELPER = WORKSPACE / 'tea_shift_cmssw/configs/shift_storage_paths.py'
LCG_SETUP = Path('/cvmfs/sft.cern.ch/lcg/views/LCG_108/x86_64-el9-gcc13-opt/setup.sh')
SOURCES = ('features.py', 'portable_inference.py', 'add_bdt_score.py')
STRATA = frozenset(
    ['qcd_' + b for b in ('0to1', '1to2', '2to5', '5to10', '10to20', '20toinf')]
    + ['jpsi_' + b for b in ('0to1', '1to2', '2to5', '5to10', '10to20', '20toinf')]
    + ['dy_' + b for b in ('0.211317to0.5', '0.5to1', '1to2', '2to5', '5to10', '10to20', '20to-1')])
PLAN_SCHEMA = 'shift-dimuon-score-production-plan-v1'
MARKER_SCHEMA = 'shift-dimuon-scored-nano-complete-v1'


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1048576), b''):
            value.update(chunk)
    return value.hexdigest()


def canonical_eos(value):
    if not isinstance(value, str) or not value.startswith('/') or any(ord(c) < 32 for c in value):
        raise ValueError('Expected an absolute EOS path without control characters')
    if '..' in value.split('/') or '.' in value.split('/') or '//' in value or '\\' in value:
        raise ValueError('EOS paths must be normalized without traversal')
    value = value.replace('/eos/home-j/jniedzie/', '/eos/user/j/jniedzie/', 1)
    if value != EOS_BASE and not value.startswith(EOS_BASE + '/'):
        raise ValueError('Only the explicit private SHIFT EOS namespace is supported')
    return value.rstrip('/')


def validate_output_root(value):
    value = canonical_eos(value)
    source, output = PurePosixPath(SOURCE_ROOT), PurePosixPath(value)
    if source == output or source in output.parents or output in source.parents:
        raise ValueError('Scored output root must be distinct from the V10 input tree')
    return value


def storage_helper(path):
    spec = importlib.util.spec_from_file_location('_shift_score_storage_paths', Path(path))
    if spec is None or spec.loader is None:
        raise ValueError('Cannot load the canonical storage resolver')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def storage_contract(migration_manifest, helper_path):
    """Reject an active migration even when a caller omitted the new option."""
    configured = os.environ.get('SHIFT_STORAGE_MIGRATION_MANIFEST')
    guard = Path(configured) if configured else DEFAULT_MIGRATION_MANIFEST
    if guard.exists():
        record = json.loads(guard.read_text())
        if record.get('schema') != 'shift-storage-path-map-v1' or record.get('complete') is not True:
            raise RuntimeError('Storage migration is in progress or unvalidated; preparation/run is blocked')
        if migration_manifest is None:
            raise RuntimeError('Migrated storage requires an explicit completed --migration-manifest')
    if migration_manifest is None:
        return None, None
    manifest = Path(migration_manifest).resolve()
    resolver = storage_helper(helper_path)
    resolver.load_migration_map(manifest)
    return resolver, manifest


def read_inventory(path, *, require_all_strata=True):
    rows, seen_paths, seen_jobs = [], set(), set()
    for number, line in enumerate(Path(path).read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = ast.literal_eval(line)
        except (ValueError, SyntaxError) as error:
            raise ValueError(f'Invalid literal inventory line {number}') from error
        if (not isinstance(value, tuple) or len(value) != 3
                or not all(isinstance(item, str) for item in value) or value[1] != ''):
            raise ValueError(f'Expected (nano_path, empty_selection, histogram_path) on line {number}')
        source = canonical_eos(value[0])
        prefix = SOURCE_ROOT + '/'
        if not source.startswith(prefix):
            raise ValueError('Input is outside the explicit corrected V10 campaign')
        parts = source[len(prefix):].split('/')
        if (len(parts) != 3 or parts[0] not in STRATA or parts[2] != 'nano.root'
                or not re.fullmatch(r'job[0-9]{7}', parts[1])):
            raise ValueError('Invalid V10 process-bin/job/Nano identity: ' + source)
        if source in seen_paths or parts[1] in seen_jobs:
            raise ValueError('Duplicate canonical Nano path or V10 job identity')
        seen_paths.add(source); seen_jobs.add(parts[1])
        rows.append(dict(index=len(rows), source=source, source_url='root://eosuser.cern.ch/' + source,
                         source_receipt=source.rsplit('/', 1)[0] + '/complete.json',
                         stratum=parts[0], job=parts[1], original_inventory_path=value[0]))
    if not rows or (require_all_strata and {r['stratum'] for r in rows} != STRATA):
        raise ValueError('Inventory must contain all 19 SM bins and at least one Nano file')
    return rows


def validate_export(model_path, receipt_path, scorer_directory):
    model = json.loads(Path(model_path).read_text())
    receipt = json.loads(Path(receipt_path).read_text())
    if model.get('schema') != 'shift-dimuon-uniform-bdt-json-v1':
        raise ValueError('Only the explicit portable UniformityBDT JSON format is supported')
    for flag in ('requires_gen', 'selection_applied', 'physics_ready'):
        if model.get(flag) is not False:
            raise ValueError('Model must remain reconstructed-only and provisional: ' + flag)
    threshold = model.get('threshold_metadata')
    if type(threshold) not in (int, float) or not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError('Invalid historical threshold metadata')
    if (receipt.get('schema') != 'shift-dimuon-bdt-export-receipt-v1'
            or receipt.get('model_sha256') != digest(model_path)
            or receipt.get('production_scoring_parity_validated') is not True
            or receipt.get('selection_applied') is not False or receipt.get('physics_ready') is not False
            or receipt.get('provenance') != model.get('provenance')):
        raise ValueError('Exporter receipt/model digest, provenance or provisional flags differ')
    validation = receipt.get('validation', {})
    difference = validation.get('maximum_absolute_score_difference')
    if (type(validation.get('rows')) is not int or validation['rows'] <= 0
            or validation.get('bitwise_score_equality') is not True
            or validation.get('threshold_decision_equality') is not True
            or type(difference) not in (int, float) or difference != 0
            or validation.get('score_dtype') != 'float64'
            or validation.get('selection_applied') is not False):
        raise ValueError('Explicit nonempty Float64 score/decision parity evidence is required')
    names = model.get('feature_names', [])
    if (not isinstance(names, list) or not names or not all(isinstance(n, str) for n in names)
            or len(set(names)) != len(names)):
        raise ValueError('Invalid reconstructed feature contract')
    contract_hash = hashlib.sha256(json.dumps(names, separators=(',', ':')).encode()).hexdigest()
    if model.get('feature_contract_sha256') != contract_hash:
        raise ValueError('Feature contract digest differs')
    provenance = model.get('provenance', {}).get('source_sha256', {})
    for name in SOURCES:
        if not (Path(scorer_directory) / name).is_file():
            raise ValueError('Missing scoring dependency: ' + name)
    for name in ('features.py', 'portable_inference.py'):
        if provenance.get(name) != digest(Path(scorer_directory) / name):
            raise ValueError('Current scoring source differs from the parity-validated export: ' + name)
    return model, receipt


def write_json(path, value):
    with Path(path).open('x') as target:
        target.write(json.dumps(value, indent=2, allow_nan=False) + '\n')


def prepare_plan(inventory, model_path, output, *, scorer_directory, output_root=None,
                 export_receipt=None, runtime_setup=LCG_SETUP, files_per_batch=50,
                 chunk_events=1000, expected_files=None, prepare_condor=False,
                 migration_manifest=None, storage_helper_path=DEFAULT_STORAGE_HELPER,
                 campaign=DEFAULT_SCORE_CAMPAIGN):
    resolver, migration_manifest = storage_contract(migration_manifest, storage_helper_path)
    if (type(files_per_batch) is not int or not 1 <= files_per_batch <= 100
            or type(chunk_events) is not int or not 1 <= chunk_events <= 10000):
        raise ValueError('Require 1..100 files/batch and 1..10000 events/read chunk')
    inventory, model_path, scorer_directory, output = [Path(p).resolve() for p in
                                                       (inventory, model_path, scorer_directory, output)]
    runtime_setup = Path(runtime_setup).absolute()
    export_receipt = Path(export_receipt).resolve() if export_receipt else model_path.with_suffix('.json.receipt.json')
    if output.exists():
        raise FileExistsError('Refuse to overwrite an existing production plan')
    if prepare_condor and not re.fullmatch(r'[A-Za-z0-9_./-]+', str(output)):
        raise ValueError('Condor preparation requires a path without whitespace or submit-language metacharacters')
    if resolver:
        output_root = canonical_eos(output_root or EOS_BASE)
        if output_root != EOS_BASE:
            raise ValueError('Migrated output must use the canonical process-bin EOS data base')
    else:
        output_root = validate_output_root(output_root or OUTPUT_ROOT)
    rows = read_inventory(inventory)
    source_transports, receipt_transports = set(), set()
    for row in rows:
        row['transport_source'] = canonical_eos(resolver.resolve_storage_path(row['source'], migration_manifest)) if resolver else row['source']
        row['transport_source_receipt'] = canonical_eos(resolver.resolve_storage_path(row['source_receipt'], migration_manifest)) if resolver else row['source_receipt']
        if resolver and any(row[key].startswith(SOURCE_ROOT + '/') for key in ('transport_source', 'transport_source_receipt')):
            raise RuntimeError('Completed migration map lacks an exact source/receipt relocation: ' + row['source'])
        if row['transport_source'] in source_transports or row['transport_source_receipt'] in receipt_transports:
            raise ValueError('Migration map aliases distinct jobs to the same source/receipt')
        source_transports.add(row['transport_source']); receipt_transports.add(row['transport_source_receipt'])
        row['transport_source_url'] = 'root://eosuser.cern.ch/' + row['transport_source']
        if resolver:
            process, bounds = row['stratum'].split('_', 1)
            campaign_path = resolver.campaign_directory(output_root, process, bounds, campaign)
            metadata = campaign_path / 'metadata/bdt_scores' / row['job']
            row.update(output=str(campaign_path / 'nanoAOD' / ('nano_' + row['job'] + '.root')),
                       receipt=str(metadata / 'bdt_score.json'), log=str(metadata / 'bdt_score.log'),
                       complete_marker=str(metadata / 'complete.json'))
        else:
            destination = output_root + '/' + row['stratum'] + '/' + row['job']
            row.update(output=destination + '/nano.root', receipt=destination + '/bdt_score.json',
                       log=destination + '/bdt_score.log', complete_marker=destination + '/complete.json')
        if row['output'] in source_transports or row['output'] == row['source']:
            raise ValueError('Scored destination aliases an original source payload')
    if any(row['output'] in source_transports or row['receipt'] in receipt_transports
           or row['complete_marker'] in receipt_transports for row in rows):
        raise ValueError('Scored destination aliases an original source payload/receipt')
    if expected_files is not None and (type(expected_files) is not int or expected_files < len(rows)):
        raise ValueError('Expected file count must be an integer at least as large as the inventory')
    model, receipt = validate_export(model_path, export_receipt, scorer_directory)
    if (not runtime_setup.is_file() or 'LCG_108' not in runtime_setup.parts
            or runtime_setup.parent.name != 'x86_64-el9-gcc13-opt' or runtime_setup.name != 'setup.sh'):
        raise ValueError('The pinned LCG_108 EL9 GCC13 view setup is required')
    dependencies = {'inventory.txt': inventory, 'model.json': model_path,
                    'export_receipt.json': export_receipt,
                    'prepare_dimuon_score_production.py': Path(__file__).resolve()}
    dependencies.update({name: scorer_directory / name for name in SOURCES})
    if resolver:
        dependencies.update({'shift_storage_paths.py': Path(storage_helper_path).resolve(),
                             'storage_path_map.json': migration_manifest})
    hashes = {name: digest(path) for name, path in dependencies.items()}
    bundle_hash = hashlib.sha256(json.dumps(hashes, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    runtime_hash = digest(runtime_setup)
    output.mkdir(parents=True, exist_ok=False)
    frozen = output / 'frozen'; frozen.mkdir()
    for name, path in dependencies.items():
        shutil.copy2(path, frozen / name)
        if digest(frozen / name) != hashes[name] or digest(path) != hashes[name]:
            raise ValueError('Dependency changed during snapshot: ' + name)
    original_fields = ('index', 'source', 'source_url', 'source_receipt', 'stratum', 'job', 'original_inventory_path')
    if read_inventory(frozen / 'inventory.txt') != [{k: row[k] for k in original_fields} for row in rows]:
        raise ValueError('Inventory changed between parsing and snapshot')
    frozen_model, frozen_receipt = validate_export(frozen / 'model.json', frozen / 'export_receipt.json', frozen)
    if frozen_model != model or frozen_receipt != receipt:
        raise ValueError('Model/export receipt changed between validation and snapshot')
    if resolver:
        frozen_resolver = storage_helper(frozen / 'shift_storage_paths.py')
        frozen_resolver.load_migration_map(frozen / 'storage_path_map.json')
        for row in rows:
            for logical, physical in (('source', 'transport_source'), ('source_receipt', 'transport_source_receipt')):
                if canonical_eos(frozen_resolver.resolve_storage_path(row[logical], frozen / 'storage_path_map.json')) != row[physical]:
                    raise RuntimeError('Storage mapping changed between resolution and snapshot')
        if digest(migration_manifest) != hashes['storage_path_map.json']:
            raise RuntimeError('Storage mapping changed before sealing')
    if digest(runtime_setup) != runtime_hash:
        raise ValueError('Pinned runtime setup changed during preparation')
    for row in rows:
        row['score_argv'] = ['python3', str(frozen / 'add_bdt_score.py'), '--input', row['transport_source'],
                             '--output', row['output'], '--model', str(frozen / 'model.json'),
                             '--sample-kind', 'simulation', '--receipt', row['receipt'],
                             '--chunk-events', str(chunk_events)]
        row['score_command'] = shlex.join(row['score_argv'])
    batches = [dict(index=i // files_per_batch, file_indices=list(range(i, min(i + files_per_batch, len(rows)))))
               for i in range(0, len(rows), files_per_batch)]
    pilot_indices = [next(r['index'] for r in rows if r['stratum'].startswith(process + '_'))
                     for process in ('qcd', 'jpsi', 'dy')]
    result = dict(schema=PLAN_SCHEMA, prepared=True, launched=False, sample_kind='simulation',
                  selection_applied=False, physics_ready=False, normalization_transfer_validated=False,
                  source_root=SOURCE_ROOT, output_root=output_root,
                  storage_contract=dict(version=2 if resolver else 1,
                      layout='dated-process-bin-direct-tiers' if resolver else 'legacy-before-migration',
                      campaign=campaign if resolver else None, migration_complete=True if resolver else None,
                      migration_manifest_source=str(migration_manifest) if resolver else None,
                      migration_manifest_mtime_ns=migration_manifest.stat().st_mtime_ns if resolver else None,
                      migration_manifest_bytes=migration_manifest.stat().st_size if resolver else None,
                      migration_manifest=str(frozen / 'storage_path_map.json') if resolver else None,
                      resolver=str(frozen / 'shift_storage_paths.py') if resolver else None,
                      historical_receipt_paths_unchanged=True),
                  inventory_files=len(rows), expected_files=expected_files,
                  missing_from_expected=None if expected_files is None else expected_files - len(rows),
                  missing_count_scope='Frozen inventory count only; missing source jobs are not produced or inferred complete',
                  files_per_batch=files_per_batch, chunk_events=chunk_events,
                  retain_zero_pair_files=True, source_population_filtered=False,
                  per_stratum=dict(Counter(r['stratum'] for r in rows)),
                  model_name=model['provenance'].get('model_name'), model_sha256=hashes['model.json'],
                  export_parity=receipt['validation'], threshold_metadata=model['threshold_metadata'],
                  score_storage='Float64', scoring_requires_gen=False,
                  source_preservation='Existing Nano, original branches/keys, all events and candidates remain unchanged',
                  runtime=dict(view='LCG_108', architecture='x86_64-el9-gcc13-opt',
                               setup=str(runtime_setup), setup_sha256=runtime_hash,
                               export_runtime=model['provenance'].get('export_runtime', {})),
                  frozen_dependencies={name: dict(source=str(path), path=str(frozen / name), sha256=hashes[name])
                                       for name, path in dependencies.items()},
                  frozen_bundle_sha256=bundle_hash,
                  worker_io='XRootD stage; validated distinct score copy; checksum readback; complete marker last',
                  files=rows, batches=batches,
                  pilot_batches=[dict(index=0, file_indices=pilot_indices)],
                  pilot_selection='First explicit inventory file per SM process; no observable or pair-count selection',
                  condor_prepared=bool(prepare_condor))
    write_json(output / 'plan.json', result)
    batch_dir = output / 'batches'; batch_dir.mkdir()
    for batch in batches:
        write_json(batch_dir / f'batch{batch["index"]:04d}.json',
                   dict(plan_sha256=digest(output / 'plan.json'), **batch,
                        files=[rows[i] for i in batch['file_indices']]))
    if prepare_condor:
        prepare_submit(output, result)
    return result


def prepare_submit(output, plan):
    if not re.fullmatch(r'[A-Za-z0-9_./-]+', str(output)):
        raise ValueError('Condor preparation requires a path without whitespace or submit-language metacharacters')
    plan_path = output / 'plan.json'
    worker = output / 'worker.sh'
    script = ('#!/usr/bin/env bash\nset -eo pipefail\n'
              'export PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1\n'
              'unset LD_LIBRARY_PATH LD_PRELOAD PYTHONPATH PYTHONHOME ROOTSYS CMSSW_BASE CMSSW_RELEASE_BASE\n'
              f'/usr/bin/python3 {shlex.quote(str(output / "frozen/prepare_dimuon_score_production.py"))} '
              f'verify {shlex.quote(str(plan_path))} --plan-sha256 {digest(plan_path)}\n'
              f'source {shlex.quote(plan["runtime"]["setup"])}\nset -u\n'
              'export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1\n'
              'extra=()\ncase "${2:-production}" in\n'
              '  production) ;;\n  pilot) extra+=(--pilot) ;;\n  *) exit 2 ;;\nesac\n'
              f'exec python3 {shlex.quote(str(output / "frozen/prepare_dimuon_score_production.py"))} '
              f'run-batch {shlex.quote(str(plan_path))} --plan-sha256 {digest(plan_path)} --batch "$1" "${{extra[@]}}"\n')
    with worker.open('x') as target:
        target.write(script)
    worker.chmod(0o755)
    (output / 'logs').mkdir(); (output / 'results').mkdir()
    submit = f'''universe = vanilla
executable = {worker}
arguments = $(batch)
initialdir = {output}
getenv = False
should_transfer_files = YES
when_to_transfer_output = ON_EXIT
transfer_executable = True
transfer_output_files = batch_status.json,batch_failure.tar.gz
transfer_output_remaps = "batch_status.json={output}/results/batch$(batch)_$(Cluster)_$(Process).json;batch_failure.tar.gz={output}/results/batch$(batch)_$(Cluster)_$(Process)_failure.tar.gz"
request_cpus = 1
request_memory = 2000
request_disk = 2000000
max_materialize = 20
max_idle = 5
+JobFlavour = "microcentury"
+JobBatchName = "SHIFT_v10_dimuon_bdt_scores"
on_exit_hold = (ExitBySignal =?= True) || (ExitCode =!= 0)
periodic_release = False
periodic_remove = False
output = {output}/logs/batch$(batch)_$(Cluster)_$(Process).out
error = {output}/logs/batch$(batch)_$(Cluster)_$(Process).err
log = {output}/events.log
queue batch from (
{chr(10).join(str(b['index']) for b in plan['batches'])}
)
'''
    with (output / 'score.sub').open('x') as target:
        target.write(submit)
    pilot_submit = submit[:submit.index('queue batch from (')].replace('arguments = $(batch)', 'arguments = $(batch) pilot')
    pilot_submit = pilot_submit.replace('max_materialize = 20', 'max_materialize = 1').replace('max_idle = 5', 'max_idle = 1')
    with (output / 'pilot.sub').open('x') as target:
        target.write(pilot_submit + 'queue batch from (\n0\n)\n')
    write_json(output / 'condor_receipt.json', dict(submitted=False, plan_sha256=digest(plan_path),
                worker_sha256=digest(worker), submit_sha256=digest(output / 'score.sub'),
                pilot_submit_sha256=digest(output / 'pilot.sub'), pilot_files=3,
                request_cpus=1, memory_mb=2000, scratch_kb=2000000, max_materialize=20,
                job_flavour='microcentury', required_before_scale='one complete file canary with published readback'))


def verify_plan(path, expected_sha):
    if digest(path) != expected_sha:
        raise ValueError('Frozen production plan changed')
    plan = json.loads(Path(path).read_text())
    if (plan.get('schema') != PLAN_SCHEMA or plan.get('sample_kind') != 'simulation'
            or plan.get('selection_applied') is not False or plan.get('physics_ready') is not False
            or plan.get('normalization_transfer_validated') is not False):
        raise ValueError('Invalid provisional simulation scoring plan')
    storage = plan.get('storage_contract', {'version': 1})
    if storage['version'] == 1:
        storage_contract(None, DEFAULT_STORAGE_HELPER)
        validate_output_root(plan['output_root'])
    elif storage['version'] != 2 or plan['output_root'] != EOS_BASE:
        raise ValueError('Unsupported dated storage contract')
    for name, row in plan['frozen_dependencies'].items():
        if digest(row['path']) != row['sha256']:
            raise ValueError('Frozen scoring dependency changed: ' + name)
    hashes = {name: row['sha256'] for name, row in plan['frozen_dependencies'].items()}
    if hashlib.sha256(json.dumps(hashes, sort_keys=True, separators=(',', ':')).encode()).hexdigest() != plan['frozen_bundle_sha256']:
        raise ValueError('Frozen scoring bundle digest differs')
    if digest(plan['runtime']['setup']) != plan['runtime']['setup_sha256']:
        raise ValueError('Pinned runtime setup changed')
    verify_storage(plan)
    return plan


def verify_storage(plan):
    storage = plan.get('storage_contract', {'version': 1})
    if storage['version'] == 1:
        storage_contract(None, DEFAULT_STORAGE_HELPER)
        return
    manifest = Path(storage['migration_manifest_source'])
    resolver, _ = storage_contract(manifest, storage['resolver'])
    expected = plan['frozen_dependencies']['storage_path_map.json']['sha256']
    if digest(manifest) != expected:
        raise RuntimeError('Completed migration map changed; prepare a new deployment')
    resolver.load_migration_map(storage['migration_manifest'])
    for row in plan['files']:
        for logical, physical in (('source', 'transport_source'), ('source_receipt', 'transport_source_receipt')):
            if canonical_eos(resolver.resolve_storage_path(row[logical], storage['migration_manifest'])) != row[physical]:
                raise ValueError('Frozen transport path differs from the completed migration map')


def verify_storage_gate(plan):
    """Cheap per-file gate; full map digests/resolution are verified at startup."""
    storage = plan.get('storage_contract', {'version': 1})
    if storage['version'] == 1:
        storage_contract(None, DEFAULT_STORAGE_HELPER)
        return
    source = Path(storage['migration_manifest_source'])
    info = source.stat()
    if (info.st_mtime_ns != storage['migration_manifest_mtime_ns']
            or info.st_size != storage['migration_manifest_bytes']):
        raise RuntimeError('Storage migration map changed during this batch; no further file can run')
    configured = os.environ.get('SHIFT_STORAGE_MIGRATION_MANIFEST')
    guard = Path(configured) if configured else DEFAULT_MIGRATION_MANIFEST
    if guard.exists() and guard.resolve() != source.resolve():
        record = json.loads(guard.read_text())
        if record.get('schema') != 'shift-storage-path-map-v1' or record.get('complete') is not True:
            raise RuntimeError('Another storage migration is in progress; no further file can run')


def clean_transfer_environment():
    return {k: v for k, v in os.environ.items() if k not in
            ('LD_LIBRARY_PATH', 'LD_PRELOAD', 'PYTHONPATH', 'PYTHONHOME', 'ROOTSYS')}


def transfer(argv):
    return subprocess.run(argv, check=True, timeout=600, env=clean_transfer_environment())


def remote_exists(path):
    result = subprocess.run(['/usr/bin/xrdfs', 'root://eosuser.cern.ch', 'stat', path],
                            capture_output=True, text=True, timeout=60, env=clean_transfer_environment())
    if result.returncode == 0:
        return True
    message = result.stdout + result.stderr
    if '[3011]' in message and re.search(r'No such file|does not exist', message, re.IGNORECASE):
        return False
    raise RuntimeError('EOS target state is unknown: ' + message[-1000:])


def get_remote(path, local):
    transfer(['/usr/bin/xrdcp', '--silent', '--cksum', 'adler32', 'root://eosuser.cern.ch/' + path, str(local)])


def put_remote(local, path):
    # No --force: a race or an existing artifact must never be overwritten.
    transfer(['/usr/bin/xrdcp', '--silent', '--posc', '--cksum', 'adler32', str(local), 'root://eosuser.cern.ch/' + path])


def run_file(plan, plan_sha, row, work):
    work.mkdir(exist_ok=False)
    source, original_receipt = work / 'source.root', work / 'source_complete.json'
    get_remote(row.get('transport_source_receipt', row['source_receipt']), original_receipt)
    original = json.loads(original_receipt.read_text())
    if original.get('complete') is not True or canonical_eos(original.get('nano_path')) != row['source']:
        raise ValueError('Source lacks its exact completed V10 publication receipt')
    get_remote(row.get('transport_source', row['source']), source)
    source_sha = digest(source)
    if source_sha != original.get('nano_sha256'):
        raise ValueError('Source Nano differs from its published V10 checksum')
    if remote_exists(row['complete_marker']):
        marker_path = work / 'existing_complete.json'; get_remote(row['complete_marker'], marker_path)
        marker = json.loads(marker_path.read_text())
        if (marker.get('schema') != MARKER_SCHEMA or marker.get('complete') is not True
                or marker.get('plan_sha256') != plan_sha or marker.get('model_sha256') != plan['model_sha256']
                or marker.get('source_sha256') != source_sha or marker.get('source') != row['source']
                or marker.get('output') != row['output'] or marker.get('selection_applied') is not False
                or marker.get('score_log') != row['log']
                or marker.get('physics_ready') is not False or marker.get('normalization_transfer_validated') is not False):
            raise ValueError('Existing scored publication does not match the frozen plan')
        for remote, name, key in ((row['output'], 'existing_nano.root', 'output_sha256'),
                                  (row['receipt'], 'existing_bdt.json', 'score_receipt_sha256'),
                                  (row['log'], 'existing_score.log', 'score_log_sha256')):
            get_remote(remote, work / name)
            if digest(work / name) != marker.get(key):
                raise ValueError('Existing scored publication failed checksum readback')
        receipt = json.loads((work / 'existing_bdt.json').read_text())
        if (receipt.get('complete') is not True or receipt.get('source') != row['source']
                or receipt.get('output') != row['output'] or receipt.get('source_sha256') != source_sha
                or receipt.get('model_sha256') != plan['model_sha256']
                or receipt.get('output_sha256') != marker['output_sha256']
                or receipt.get('score_log') != row['log'] or receipt.get('score_log_sha256') != marker['score_log_sha256']
                or receipt.get('events') != original.get('events')
                or receipt.get('selection_applied') is not False or receipt.get('physics_ready') is not False
                or receipt.get('normalization_transfer_validated') is not False
                or receipt.get('verification', {}).get('all_original_content_equal') is not True
                or receipt.get('verification', {}).get('all_scores_recomputed_equal') is not True):
            raise ValueError('Existing scored receipt lacks complete unchanged-content verification')
        return dict(index=row['index'], complete=True, reused=True, marker=marker)
    if remote_exists(row['output']) or remote_exists(row['receipt']) or remote_exists(row['log']):
        raise FileExistsError('Incomplete scored publication exists; preserve it for recovery')
    output, receipt_path = work / 'nano.root', work / 'bdt_score.json'
    argv = [sys.executable, plan['frozen_dependencies']['add_bdt_score.py']['path'],
            '--input', str(source), '--output', str(output), '--model', plan['frozen_dependencies']['model.json']['path'],
            '--sample-kind', 'simulation', '--receipt', str(receipt_path), '--chunk-events', str(plan['chunk_events'])]
    with (work / 'score.log').open('x') as log:
        subprocess.run(argv, check=True, timeout=900, stdout=log, stderr=subprocess.STDOUT)
    receipt = json.loads(receipt_path.read_text())
    if (receipt.get('complete') is not True or receipt.get('source_sha256') != source_sha
            or receipt.get('model_sha256') != plan['model_sha256'] or receipt.get('output_sha256') != digest(output)
            or receipt.get('selection_applied') is not False or receipt.get('physics_ready') is not False
            or receipt.get('normalization_transfer_validated') is not False
            or receipt.get('events') != original.get('events')
            or receipt.get('verification', {}).get('all_original_content_equal') is not True
            or receipt.get('verification', {}).get('all_scores_recomputed_equal') is not True):
        raise ValueError('Scorer content/count/provenance verification failed')
    receipt['staged_paths'] = {k: receipt[k] for k in ('source', 'output')}
    receipt.update(source=row['source'], output=row['output'], source_url=row['source_url'],
                   source_transport=row.get('transport_source', row['source']),
                   source_transport_url=row.get('transport_source_url', row['source_url']),
                   output_url='root://eosuser.cern.ch/' + row['output'], plan_sha256=plan_sha,
                   original_publication_receipt=row['source_receipt'],
                   original_publication_receipt_sha256=digest(original_receipt),
                   score_log=row['log'], score_log_sha256=digest(work / 'score.log'))
    receipt_path.write_text(json.dumps(receipt, indent=2, allow_nan=False) + '\n')
    for directory in sorted({row[key].rsplit('/', 1)[0] for key in ('output', 'receipt', 'log', 'complete_marker')}):
        transfer(['/usr/bin/xrdfs', 'root://eosuser.cern.ch', 'mkdir', '-p', directory])
    for local, remote in ((output, row['output']), (receipt_path, row['receipt']), (work / 'score.log', row['log'])):
        put_remote(local, remote)
        readback = work / ('readback_' + local.name); get_remote(remote, readback)
        if digest(readback) != digest(local):
            raise ValueError('Scored publication checksum readback failed')
    marker = dict(schema=MARKER_SCHEMA, complete=True, plan_sha256=plan_sha,
                  source=row['source'], source_sha256=source_sha, output=row['output'], output_sha256=digest(output),
                  source_transport=row.get('transport_source', row['source']),
                  score_receipt_sha256=digest(receipt_path), model_sha256=plan['model_sha256'],
                  score_log=row['log'], score_log_sha256=digest(work / 'score.log'),
                  events=receipt['events'], retained_pairs=receipt['retained_pairs'],
                  selection_applied=False, physics_ready=False, normalization_transfer_validated=False)
    marker_path = work / 'complete.json'; write_json(marker_path, marker)
    put_remote(marker_path, row['complete_marker'])
    get_remote(row['complete_marker'], work / 'readback_complete.json')
    if digest(work / 'readback_complete.json') != digest(marker_path):
        raise ValueError('Completion marker checksum readback failed')
    return dict(index=row['index'], complete=True, reused=False, marker=marker)


def run_batch(path, expected_sha, batch_index, scratch, *, pilot=False):
    plan = verify_plan(path, expected_sha)
    batches = plan['pilot_batches'] if pilot else plan['batches']
    if type(batch_index) is not int or not 0 <= batch_index < len(batches):
        raise ValueError('Invalid frozen batch index')
    scratch = Path(scratch).resolve()
    if not scratch.is_dir():
        raise ValueError('Existing worker scratch directory required')
    mode = 'pilot' if pilot else 'production'
    work = scratch / f'score_{mode}_batch{batch_index:04d}'; work.mkdir(exist_ok=False)
    status_path = scratch / 'batch_status.json'
    status = dict(plan_sha256=expected_sha, batch=batch_index, mode=mode, complete=False, files=[],
                  expected_file_indices=batches[batch_index]['file_indices'], selection_applied=False)
    status_path.write_text(json.dumps(status, indent=2) + '\n')
    with tarfile.open(scratch / 'batch_failure.tar.gz', 'w:gz'):
        pass
    active = None
    try:
        for index in status['expected_file_indices']:
            # Refuse another file if a new migration starts during this batch.
            verify_storage_gate(plan)
            active = work / f'file{index:05d}'
            status['files'].append(run_file(plan, expected_sha, plan['files'][index], active))
            status_path.write_text(json.dumps(status, indent=2) + '\n')
            # Only validated study-owned scratch can be retired, one exact directory.
            shutil.rmtree(active)
            active = None
        status['complete'] = True
    except Exception as error:
        status.update(error=repr(error), failed_file_index=plan['files'][index]['index'])
        raise
    finally:
        status['pending_file_indices'] = [i for i in status['expected_file_indices']
                                          if i not in {r['index'] for r in status['files']}]
        status_path.write_text(json.dumps(status, indent=2, allow_nan=False) + '\n')
        with tarfile.open(scratch / 'batch_failure.tar.gz', 'w:gz') as archive:
            if active and active.exists():
                archive.add(active, arcname=active.name)
    return status


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ('verify', 'run-batch'):
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument('operation', choices=('verify', 'run-batch'))
        parser.add_argument('plan', type=Path)
        parser.add_argument('--plan-sha256', required=True)
        parser.add_argument('--batch', type=int)
        parser.add_argument('--pilot', action='store_true')
        args = parser.parse_args()
        if args.operation == 'verify':
            verify_plan(args.plan, args.plan_sha256)
        else:
            if args.batch is None or not os.environ.get('_CONDOR_SCRATCH_DIR'):
                parser.error('run-batch requires --batch and _CONDOR_SCRATCH_DIR')
            def terminate(signum, frame):
                raise TimeoutError('Score worker stopped by signal ' + str(signum))
            signal.signal(signal.SIGTERM, terminate)
            run_batch(args.plan, args.plan_sha256, args.batch, os.environ['_CONDOR_SCRATCH_DIR'], pilot=args.pilot)
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', required=True, type=Path)
    parser.add_argument('--model', required=True, type=Path)
    parser.add_argument('--export-receipt', type=Path)
    parser.add_argument('--scorer-directory', type=Path, default=WORKSPACE / 'CMSSW_17_0_0_pre4/src/PhysicsTools/ShiftDimuonClassifier')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--eos-output-root', help='EOS data base after migration, distinct legacy root before migration')
    parser.add_argument('--migration-manifest', type=Path, help='Required explicit completed storage path map after relocation')
    parser.add_argument('--storage-helper', type=Path, default=DEFAULT_STORAGE_HELPER)
    parser.add_argument('--campaign', default=DEFAULT_SCORE_CAMPAIGN)
    parser.add_argument('--files-per-batch', type=int, default=50)
    parser.add_argument('--chunk-events', type=int, default=1000)
    parser.add_argument('--expected-files', type=int)
    parser.add_argument('--prepare-condor', action='store_true')
    args = parser.parse_args()
    plan = prepare_plan(args.inventory, args.model, args.output, scorer_directory=args.scorer_directory,
                        output_root=args.eos_output_root, export_receipt=args.export_receipt,
                        files_per_batch=args.files_per_batch, chunk_events=args.chunk_events,
                        expected_files=args.expected_files, prepare_condor=args.prepare_condor,
                        migration_manifest=args.migration_manifest, storage_helper_path=args.storage_helper,
                        campaign=args.campaign)
    print(json.dumps(dict(prepared=True, launched=False, files=plan['inventory_files'],
                          batches=len(plan['batches']), output=str(args.output),
                          selection_applied=False, condor_prepared=plan['condor_prepared'])))


if __name__ == '__main__':
    main()
