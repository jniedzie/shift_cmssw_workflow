#!/usr/bin/env python3
"""Prepare a frozen merged-ntuple score/histogram test without submitting it."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import shutil

import prepare_dimuon_score_production as io

WORKSPACE = Path(__file__).resolve().parents[2]
EOS = '/eos/user/j/jniedzie/shift_cmssw'
HISTORICAL_ANALYSIS_SOURCES = (
    'utils/shift_bdt_histogrammer.py', 'configs/shift_bdt_histograms.py',
)


def historical_analysis_dependencies(reuse_completed_plan=None):
    """Resolve this dated worker's analysis from its exact frozen predecessor."""
    if reuse_completed_plan is None:
        dependencies = {'tea/' + name: WORKSPACE / 'tea_shift_cmssw' / name
                        for name in HISTORICAL_ANALYSIS_SOURCES}
        if any(not path.is_file() for path in dependencies.values()):
            raise ValueError(
                'This dated v1 preparer requires a prior frozen analysis plan '
                '(--reuse-completed-plan). New studies use the main SHIFT '
                'histogrammer and plotter configurations.')
        return dependencies

    previous_path = Path(reuse_completed_plan).resolve()
    previous = json.loads(previous_path.read_text())
    if previous.get('schema') != 'shift-merged-dimuon-bdt-plan-v1':
        raise ValueError('Historical analysis requires a frozen merged BDT v1 plan')
    dependencies = {}
    for name in HISTORICAL_ANALYSIS_SOURCES:
        key = 'tea/' + name
        record = previous.get('dependencies', {}).get(key)
        if not isinstance(record, dict) or not record.get('path') or not record.get('sha256'):
            raise ValueError('Previous plan lacks a frozen analysis dependency: ' + key)
        path = Path(record['path'])
        if path.resolve() != (previous_path.parent / 'frozen' / key).resolve():
            raise ValueError('Previous analysis dependency is not its exact frozen path: ' + key)
        if not path.is_file() or io.digest(path) != record['sha256']:
            raise ValueError('Frozen historical analysis checksum differs: ' + key)
        dependencies[key] = path
    return dependencies


def prepare(inventory, output, model, campaign, batch_size=5, reuse_completed_plan=None):
    inventory, output, model = map(lambda p: Path(p).resolve(), (inventory, output, model))
    if output.exists():
        raise FileExistsError('Refuse to overwrite a frozen merged scoring deployment')
    if not re.fullmatch(r'[A-Za-z0-9_./-]+', str(output)) or not re.fullmatch(r'[A-Za-z0-9_-]*20261009[A-Za-z0-9_-]*', campaign):
        raise ValueError('Safe deployment path and dated campaign required')
    historical_analysis = historical_analysis_dependencies(reuse_completed_plan)
    source = json.loads(inventory.read_text())
    if source.get('schema') != 'shift-merged-dimuon-score-source-inventory-v1' or source.get('complete') is not True:
        raise ValueError('Require the completed existing-merged-source inventory')
    if not 1 <= batch_size <= 10:
        raise ValueError('Use one to ten merged files per batch')
    completed = source['storage_complete']
    if io.digest(completed['path']) != completed['sha256']:
        raise ValueError('Completed storage receipt changed')
    resolver = io.storage_helper(source['storage_resolver']['path'])
    if io.digest(source['storage_resolver']['path']) != source['storage_resolver']['sha256']:
        raise ValueError('Canonical resolver changed')
    standard = (WORKSPACE / 'validation/histogram_rerun_20261009/histogram.sh').read_text()
    old_url = re.search(r'tea_url="([^"]+)"', standard).group(1)
    runtime_url = resolver.resolve_storage_path(old_url, source['storage_map']['path'])
    if io.digest(source['storage_map']['path']) != source['storage_map']['sha256']:
        raise ValueError('Audited migration map changed')
    classifier = WORKSPACE / 'CMSSW_17_0_0_pre4/src/PhysicsTools/ShiftDimuonClassifier'
    io.validate_export(model, model.with_suffix('.json.receipt.json'), classifier)
    deployment_model = output / 'frozen/classifier/model.json'
    dependencies = {
        'workflow/scripts/run_merged_dimuon_bdt.py': Path(__file__).with_name('run_merged_dimuon_bdt.py'),
        'workflow/scripts/prepare_dimuon_score_production.py': Path(io.__file__),
        'classifier/model.json': model,
        'classifier/model.json.receipt.json': model.with_suffix('.json.receipt.json'),
        'source_inventory.json': inventory,
        'storage_paths.py': Path(source['storage_resolver']['path']),
        'analysis_payload.tar.gz': WORKSPACE / 'validation/histogram_rerun_20261009/analysis_payload.tar.gz',
        'standard_worker.sh': WORKSPACE / 'validation/histogram_rerun_20261009/histogram.sh',
        'storage_complete.json': Path(completed['path']),
    }
    for name in (*io.SOURCES, 'labels.py'):
        dependencies['classifier/' + name] = classifier / name
    dependencies.update(historical_analysis)
    hashes = {name: io.digest(path) for name, path in dependencies.items()}
    frozen = output / 'frozen'
    frozen.mkdir(parents=True, exist_ok=False)
    snapshot = {}
    for name, path in dependencies.items():
        target = frozen / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        if io.digest(target) != hashes[name] or io.digest(path) != hashes[name]:
            raise ValueError('Source changed during snapshot: ' + name)
        snapshot[name] = dict(path=str(target), sha256=hashes[name])
    files, seen = [], set()
    for original in source['sm'] + source['signals']:
        row = {k: original[k] for k in ('source_transport_path', 'source_logical_path',
            'source_bytes', 'expected_events', 'source_sha256')}
        row.update({k: original[k] for k in ('process', 'stratum', 'point', 'mass_gev',
            'epsilon', 'proper_ctau_mm', 'source_adler32') if k in original})
        row['process'] = 'signal' if original['kind'] == 'signal' else original['process']
        row['index'] = len(files)
        if original['kind'] == 'sm':
            base = resolver.campaign_directory(EOS, original['process'], original['bin'], campaign)
            group = Path(original['source_transport_path']).stem
            row['weight_branch'] = 'shiftSamplingGenWeight'
        else:
            base = Path(EOS) / ('darkphoton_' + original['point']) / campaign
            group = 'ntuple_0'
            row['weight_branch'] = 'genWeight'
        metadata = base / 'metadata/bdt_merged' / group
        row.update(output_nano=str(base / 'nanoAOD_merged' / (group + '.root')),
            output_histograms=str(base / 'histograms' / (group + '.root')),
            complete_marker=str(metadata / 'complete.json'), score_receipt=str(metadata / 'score.json'),
            bdt_hist_receipt=str(metadata / 'bdt_hist.json'), score_log=str(metadata / 'score.log'),
            histogram_log=str(metadata / 'histogram.log'))
        if row['source_transport_path'] in seen or row['output_nano'] == row['source_transport_path']:
            raise ValueError('Duplicate source or destination aliases a source')
        seen.add(row['source_transport_path']); files.append(row)
    if len({r['output_nano'] for r in files}) != len(files):
        raise ValueError('Merged destinations collide')
    standard = (frozen / 'standard_worker.sh').read_text()
    prefix = standard[:standard.index('tea_phase="analysis payload validation"')]
    prefix = prefix.replace(old_url, runtime_url)
    prefix = prefix.replace('[[ $# == 1 && "$1" =~ ^[0-9]+$ ]]', '[[ $# == 2 && "$1" =~ ^[0-9]+$ ]]')
    prefix = prefix.replace('job_sandbox="${_CONDOR_SCRATCH_DIR:-$PWD}"',
        'job_sandbox="${_CONDOR_SCRATCH_DIR:-$PWD}"\n'
        '/usr/bin/python3 - "$job_sandbox" <<\'INITIAL\'\n'
        'import json,sys,tarfile\nfrom pathlib import Path\n'
        'p=Path(sys.argv[1])\n(p/"batch_status.json").write_text(json.dumps({"complete":False,"phase":"bootstrap"})+"\\n")\n'
        'with tarfile.open(p/"batch_failure.tar.gz","w:gz"):pass\nINITIAL')
    pilot = [next(r['index'] for r in files if r['process'] == process)
             for process in ('qcd', 'jpsi', 'dy', 'signal') if any(r['process'] == process for r in files)]
    plan = dict(schema='shift-merged-dimuon-bdt-plan-v1', selection_applied=False,
        mc_to_data_normalization_measured=False, campaign=campaign,
        source_inventory=str(frozen / 'source_inventory.json'), input_campaign_complete=source['input_campaign_complete'],
        missing_detector_jobs=source['missing_detector_jobs'], model=str(deployment_model), model_sha256=hashes['classifier/model.json'],
        classifier_directory=str(frozen / 'classifier'),
        bdt_histogrammer=str(frozen / 'tea/utils/shift_bdt_histogrammer.py'),
        analysis_archive=snapshot['analysis_payload.tar.gz'], dependencies=snapshot,
        storage_contract=dict(version=2, direct_canonical_sources=True, migration_map=source['storage_map'],
            canonical_resolver=source['storage_resolver'], runtime_archive_url=runtime_url),
        files=files, pilot=pilot, batches=[list(range(i, min(i + batch_size, len(files))))
            for i in range(0, len(files), batch_size)])
    if reuse_completed_plan is not None:
        previous_path = Path(reuse_completed_plan).resolve()
        previous = json.loads(previous_path.read_text())
        if (previous.get('schema') != plan['schema'] or previous.get('selection_applied') is not False
                or previous.get('model_sha256') != plan['model_sha256']
                or previous.get('files') != plan['files'] or previous.get('batches') != plan['batches']):
            raise ValueError('Recovery changed the input/output/model/weight population contract')
        # Only the file-writing implementation and its strict resume handling
        # may change. Predictors, labels, weights, standard app and histograms
        # must stay byte-identical before completed publications are accepted.
        allowed_changes = {'classifier/add_bdt_score.py', 'workflow/scripts/run_merged_dimuon_bdt.py'}
        if set(previous['dependencies']) != set(plan['dependencies']):
            raise ValueError('Recovery dependency inventory changed')
        for name, current in plan['dependencies'].items():
            if name not in allowed_changes and previous['dependencies'][name]['sha256'] != current['sha256']:
                raise ValueError('Recovery changed a physics/analysis dependency: ' + name)
        previous_hash = io.digest(previous_path)
        target = frozen / 'previous_plan.json'
        shutil.copy2(previous_path, target)
        if io.digest(target) != previous_hash:
            raise ValueError('Previous plan changed while freezing compatibility proof')
        plan['compatible_previous_plans'] = {previous_hash: dict(path=str(target),
            sha256=previous_hash, source=str(previous_path),
            contract='Identical sources/destinations/model/predictors/labels/weights/histograms; file-writer repair only')}
        plan['dependencies']['previous_plan.json'] = dict(path=str(target), sha256=previous_hash)
    io.write_json(output / 'plan.json', plan)
    plan_hash = io.digest(output / 'plan.json')
    command = ['python3', str(frozen / 'workflow/scripts/run_merged_dimuon_bdt.py'),
        '--plan', str(output / 'plan.json'), '--plan-sha256', plan_hash]
    worker = prefix + '\nset +u\nsource ' + str(io.LCG_SETUP) + '\nset -u\nexport OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1\n' + \
        'extra=(); if [[ "${2:-}" == pilot ]]; then extra+=(--pilot); fi\n' + \
        'exec ' + shlex.join(command) + ' --batch "$1" --runtime-cache "$tea_cache" "${extra[@]}"\n'
    (output / 'worker.sh').write_text(worker); (output / 'worker.sh').chmod(0o755)
    for name in ('logs', 'results'):
        (output / name).mkdir()
    for name, batches, mode, materialize in (('pilot', [0], 'pilot', 1),
                                            ('production', list(range(len(plan['batches']))), 'production', 20)):
        submit = f'''universe = vanilla
initialdir = {output}
executable = {output / 'worker.sh'}
arguments = $(batch) {mode}
getenv = False
should_transfer_files = YES
when_to_transfer_output = ON_EXIT
transfer_executable = True
transfer_output_files = batch_status.json,batch_failure.tar.gz
transfer_output_remaps = "batch_status.json={output}/results/batch$(batch)_$(Cluster)_$(Process).json;batch_failure.tar.gz={output}/results/batch$(batch)_$(Cluster)_$(Process)_failure.tar.gz"
request_cpus = 1
request_memory = 4500
request_disk = 10000000
max_materialize = {materialize}
max_idle = {min(5, materialize)}
+JobFlavour = "longlunch"
+JobBatchName = "SHIFT_merged_dimuon_bdt_20261009"
on_exit_hold = (ExitBySignal =?= True) || (ExitCode =!= 0)
periodic_release = False
periodic_remove = False
output = {output}/logs/batch$(batch)_$(Cluster)_$(Process).out
error = {output}/logs/batch$(batch)_$(Cluster)_$(Process).err
log = {output}/events.log
queue batch from (
''' + '\n'.join(map(str, batches)) + '\n)\n'
        (output / (name + '.sub')).write_text(submit)
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--campaign', default='20261009-dimuon-bdt-merged-first-test-v1')
    parser.add_argument('--batch-size', type=int, default=5)
    parser.add_argument('--reuse-completed-plan', type=Path,
                        help='Freeze and validate a prior identical data/model contract; preserve completed outputs')
    args = parser.parse_args()
    plan = prepare(args.inventory, args.output, args.model, args.campaign, args.batch_size,
                   args.reuse_completed_plan)
    print(json.dumps(dict(prepared=True, submitted=False, files=len(plan['files']), batches=len(plan['batches']))))


if __name__ == '__main__':
    main()
