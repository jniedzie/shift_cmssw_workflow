#!/usr/bin/env python3
# Code style: AI-code. AI-generated fixed-source meson production preparation.
"""Freeze a meson worklist and its downstream handoff; never submit jobs."""
import argparse
import copy
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import shutil

from dark_photon_sample_names import physical_point_name
from prepare_dark_photon_grid import SOURCES as DETECTOR_SOURCES
from run_dark_photon_meson_gen import SOURCES as MESON_SOURCES
from run_dark_photon_statistics import sha, new


TOOLS = tuple(sorted(set(DETECTOR_SOURCES + MESON_SOURCES + (
    'prepare_dark_photon_meson_production.py', 'run_dark_photon_meson_production.py',
    'prepare_dark_photon_production.py', 'dark_photon_production_support.py',
    'dark_photon_publication_quota.py', 'run_dark_photon_gen_stage.py',
    'run_dark_photon_statistics.py', 'shift_condor_native.py', 'watch_dark_photon_production.py',
    'aggregate_dark_photon_samples.py', 'watch_dark_photon_statistics.py'))))


def validate_batches(rows, prior_inventories, seed_base=761216001,
                     run_base=1761216001, detector_job_base=5500000):
    if not isinstance(rows, list) or not 2 <= len(rows) <= 100:
        raise ValueError('Require two canaries and a bounded fixed-source worklist')
    old_seeds, old_runs = set(), set()
    for path in prior_inventories:
        data = json.loads(Path(path).read_text())['points']
        for row in data.values() if isinstance(data, dict) else data:
            if 'seed' in row:
                old_seeds.add(row['seed'])
            if 'run_number' in row:
                old_runs.add(row['run_number'])
            old_runs.update(item[0] for item in row.get('event_ids', []))
    result, identifiers, seeds, runs = [], set(), set(), set()
    for index, original in enumerate(rows):
        row = copy.deepcopy(original)
        mass, epsilon = row['mass_gev'], row['epsilon']
        if not 2*.1056583755 < mass < .958 or not math.isfinite(epsilon) or not 0 < epsilon <= .01:
            raise ValueError('Require an open dimuon meson mass and physical epsilon')
        count = row['source_collisions']
        if type(count) is not int or not 2 <= count <= 10000000:
            raise ValueError('Source M is a fixed collision exposure, bounded at ten million')
        if type(row.get('events_per_job',20)) is not int or not 1<=row.get('events_per_job',20)<=20:
            raise ValueError('Detector chunks require1..20 actual signal events')
        target = row.get('target_mean_lab_flight_m')
        prompt = row.get('prompt', False)
        point = physical_point_name(mass, target, prompt=prompt)
        if row.get('point', point) != point:
            raise ValueError('Point name differs from its explicit LAB selector')
        batch = row.get('batch', f'b{index:03d}')
        if not re.fullmatch(r'[A-Za-z0-9_-]+', batch) or batch in identifiers:
            raise ValueError('Batch identities must be unique safe names')
        seed, run = row.get('seed', seed_base + index), row.get('run_number', run_base + index)
        if (type(seed) is not int or not 1 <= seed <= 899999800 or seed in seeds | old_seeds or
                type(run) is not int or not 1 <= run <= 4294967295 or run in runs | old_runs):
            raise ValueError('Independent meson seeds or runs overlap existing populations')
        # Reserve 1000 detector jobs per random-N source, including its audit.
        job = detector_job_base + 1000*index
        if 230000000 + (job + 999)*100 + 40 > 899999900:
            raise ValueError('Detector namespace exceeds the unchanged RNG bounds')
        row.update(batch=batch, point=point, seed=seed, run_number=run,
                   detector_job_base=job, detector_job_capacity=1000,
                   operational_canary=row.get('operational_canary', False))
        result.append(row)
        identifiers.add(batch); seeds.add(seed); runs.add(run)
    canaries = [row for row in result if row['operational_canary']]
    if (len(canaries) != 2 or not any(row['mass_gev'] < .548 for row in canaries)
            or not any(row['mass_gev'] > .548 for row in canaries)):
        raise ValueError('Require distinct eta and eta-prime-only full-chain canaries')
    return result


def prepare(configuration, output, scripts=None):
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError('Refuse to overwrite a meson deployment')
    config = json.loads(Path(configuration).read_text())
    if config.get('schema') != 'shift-dp-meson-production-request-v1':
        raise ValueError('Unsupported meson production request')
    batches = validate_batches(config['batches'], config['prior_inventories'],
        config.get('seed_base',761216001),config.get('run_base',1761216001),
        config.get('detector_job_base',5500000))
    if (not re.fullmatch(r'[A-Za-z0-9_-]*20261016[A-Za-z0-9_-]*', config['campaign_prefix']) or
            config['eos_base'] != '/eos/user/j/jniedzie/shift_cmssw' or
            not 1 <= config.get('simultaneous_sources', 2) <= 3 or
            not 1 <= config.get('simultaneous_detector_points', 2) <= 3):
        raise ValueError('Require explicit production namespaces and bounded concurrency')
    scripts = Path(scripts or Path(__file__).resolve().parent)
    output.mkdir(parents=True)
    tools = output / 'tools'
    tools.mkdir()
    files = {}
    for name in TOOLS:
        shutil.copy2(scripts / name, tools / name)
        files[str(tools / name)] = sha(tools / name)
    conditions=config['conditions']
    if sha(conditions['path'])!=conditions['sha256']:
        raise ValueError('Meson production conditions authority changed')
    target=output/'conditions.json'
    shutil.copy2(conditions['path'],target)
    config['conditions']=dict(path=str(target),sha256=sha(target))
    files[str(target)]=sha(target)
    for key in ('model_card', 'runtime_fingerprint', 'storage_budget_template', 'publication_canary'):
        source = Path(config[key]).resolve()
        target = output / source.name
        shutil.copy2(source, target)
        config[key] = str(target)
        files[str(target)] = sha(target)
    calibration = config.get('calibration_authority')
    if calibration:
        if sha(calibration['path']) != calibration['sha256']:
            raise ValueError('Meson calibration authority changed')
        target = output/'calibration.json'
        shutil.copy2(calibration['path'], target)
        config['calibration_authority'] = dict(path=str(target), sha256=sha(target))
        files[str(target)] = sha(target)
    for key in ('qualified_template', 'reference_preparation', 'cmssw_base', 'adaptive_directory',
                'darkcast_path', 'darkcast_source_receipt'):
        if not Path(config[key]).is_absolute():
            raise ValueError('Require an explicit absolute dependency: ' + key)
    reference = json.loads(Path(config['reference_preparation']).read_text())
    template_files = reference['frozen_dependencies']['templates']
    (output / 'templates').mkdir()
    for name, record in template_files.items():
        if sha(record['frozen_copy']) != record['sha256']:
            raise ValueError('Qualified detector template changed')
        target = output / 'templates' / name
        shutil.copy2(record['frozen_copy'], target)
        files[str(target)] = sha(target)
    for path in [*config['prior_inventories'], config['darkcast_source_receipt'],
                 str(Path(config['qualified_template']) / 'seal.json'),
                 str(Path(config['adaptive_directory']) / 'worklist.json')]:
        files[str(Path(path).resolve())] = sha(path)
    receipt = Path(config['darkcast_source_receipt'])
    for relative, expected in json.loads(receipt.read_text())['files'].items():
        path = Path(config['darkcast_path']) / relative
        if sha(path) != expected:
            raise ValueError('Spectral width authority changed')
        files[str(path)] = expected
    config.update(schema='shift-dp-meson-production-v1', directory=str(output), tools=str(tools),
                  production_mechanism='eta-etaprime-radiative',
                  batches=batches, events_per_job=20, group_events=config.get('group_events',4000),
                  gen_timeout_seconds=43200, publication_timeout_seconds=1800,
                  minimum_afs_free_mb=1024, minimum_eos_free_bytes=100000000000,
                  minimum_eos_free_files=20000, simultaneous_sources=config.get('simultaneous_sources', 2),
                  simultaneous_detector_points=config.get('simultaneous_detector_points', 2),
                  no_automatic_science_retry=True)
    for row in batches:
        directory = output / 'batches' / row['batch']
        directory.mkdir(parents=True)
        (directory / 'logs').mkdir()
        row['campaign'] = config['campaign_prefix'] + '-' + row['batch']
        row['final_output_base'] = config['eos_base'] + '/darkphoton_' + row['point'] + '/' + row['campaign']
        text = f'''# Code style: AI-code. AI-generated fixed-M meson GEN worker.
universe = vanilla
executable = /usr/bin/python3
arguments = {tools}/run_dark_photon_meson_production.py --directory {output} --seal SEAL_DIGEST --action source --batch {row['batch']}
initialdir = {directory}
getenv = False
environment = "PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1"
should_transfer_files = NO
request_cpus = 1
request_memory = 4000MB
request_disk = 64000MB
+JobFlavour = "tomorrow"
+MaxRuntime = 86400
+SendCredential = True
+ShiftDPStatisticsTask = "{directory}"
+JobBatchName = "SHIFT_DP_meson_fixedM"
output = {directory}/logs/source.$(ClusterId).$(ProcId).out
error = {directory}/logs/source.$(ClusterId).$(ProcId).err
log = {directory}/events.log
on_exit_hold = (ExitBySignal == True) || (ExitCode != 0)
on_exit_remove = (ExitBySignal == False) && (ExitCode == 0)
periodic_release = False
queue
'''
        path = directory / 'source.sub.in'
        path.write_text(text)
        files[str(path)] = sha(path)
    new(output / 'config.json', config)
    files[str(output / 'config.json')] = sha(output / 'config.json')
    wrapper = output / 'lcg_controller.sh'
    wrapper.write_text(f'''#!/bin/bash
# Code style: AI-code. AI-generated frozen meson controller environment.
set -eo pipefail
unset PYTHONPATH PYTHONHOME LD_LIBRARY_PATH LD_PRELOAD ROOTSYS
export PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
source /cvmfs/sft.cern.ch/lcg/views/LCG_108/x86_64-el9-gcc13-opt/setup.sh
exec python3 {tools}/run_dark_photon_meson_production.py --directory {output} --seal "$1" --action controller
''')
    wrapper.chmod(0o750)
    files[str(wrapper)] = sha(wrapper)
    new(output / 'seal.json', dict(schema='shift-dp-meson-production-seal-v1', files=files,
        created_utc=datetime.now(timezone.utc).isoformat(), source_exposure_fixed_before_sampling=True,
        actual_signal_N_adopted_after_full_audit=True, electronics_or_trigger_changes=False))
    seal = sha(output / 'seal.json')
    for row in batches:
        path = output / 'batches' / row['batch']
        (path / 'source.sub').write_text((path / 'source.sub.in').read_text().replace('SEAL_DIGEST', seal))
    (output / 'controller.sub').write_text(f'''# Code style: AI-code. AI-generated persistent meson supervisor.
universe = vanilla
executable = {wrapper}
arguments = {seal}
initialdir = {output}
getenv = False
should_transfer_files = NO
request_cpus = 1
request_memory = 2000MB
request_disk = 4000MB
+JobFlavour = "nextweek"
+MaxRuntime = 604800
+SendCredential = True
+ShiftDPStatisticsTask = "{output}"
+JobBatchName = "SHIFT_DP_meson_supervisor"
output = {output}/controller.$(ClusterId).$(ProcId).out
error = {output}/controller.$(ClusterId).$(ProcId).err
log = {output}/controller.events.log
on_exit_hold = (ExitBySignal == True) || (ExitCode != 0)
on_exit_remove = (ExitBySignal == False) && (ExitCode == 0)
periodic_release = False
queue
''')
    return dict(directory=str(output), seal_sha256=seal, batches=len(batches), submitted=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.config, args.output)))


if __name__ == '__main__':
    main()
