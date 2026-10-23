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
from run_dark_photon_meson_production import (source_mechanism, completion_scope, reference_point,
    additional_reservations, gen_runtime, coverage_request, prior_meson_configuration,
    fresh_production_authority)
from prepare_dark_photon_material_source import DEPENDENCIES as MATERIAL_SOURCES


TOOLS = tuple(sorted(set(DETECTOR_SOURCES + MESON_SOURCES + MATERIAL_SOURCES + (
    'prepare_dark_photon_meson_production.py', 'run_dark_photon_meson_production.py',
    'dark_photon_automatic_admission.py',
    'prepare_dark_photon_production.py', 'dark_photon_production_support.py',
    'dark_photon_publication_quota.py', 'run_dark_photon_gen_stage.py',
    'run_dark_photon_statistics.py', 'shift_condor_native.py', 'watch_dark_photon_production.py',
    'aggregate_dark_photon_samples.py', 'watch_dark_photon_statistics.py',
    'audit_dark_photon_meson_coverage.py'))))


def source_execution_limits(config):
    """Read explicit frozen source limits; old campaigns retain their bounds."""
    defaults = dict(schema='shift-meson-source-execution-policy-v1',
        maximum_simultaneous_sources=7, maximum_batches=100,
        maximum_source_collisions_per_batch=10000000,
        maximum_canary_source_collisions=10000000,
        detector_job_capacity_per_batch=1000)
    if 'source_execution_policy' not in config:
        return defaults
    policy = config['source_execution_policy']
    if (not isinstance(policy, dict) or set(policy) != set(defaults)
            or policy.get('schema') != defaults['schema']):
        raise ValueError('Require a complete explicit frozen source execution policy')
    limits = dict(maximum_simultaneous_sources=10000, maximum_batches=100000,
                  maximum_source_collisions_per_batch=10000000,
                  maximum_canary_source_collisions=10000000,
                  detector_job_capacity_per_batch=1000)
    for name, maximum in limits.items():
        if type(policy[name]) is not int or not 1 <= policy[name] <= maximum:
            raise ValueError('Invalid frozen source execution limit: ' + name)
    if policy['maximum_canary_source_collisions'] > policy['maximum_source_collisions_per_batch']:
        raise ValueError('Canary exposure cannot exceed the frozen source-batch limit')
    return copy.deepcopy(policy)


def split_source_exposure(collisions, maximum):
    """Balance independent fixed-exposure shards without a one-collision tail."""
    if type(collisions) is not int or type(maximum) is not int or not 2 <= maximum or collisions < 2:
        raise ValueError('Source exposure and shard limit require integer counts of at least two')
    shards = math.ceil(collisions / maximum)
    count, remainder = divmod(collisions, shards)
    return [count + (index < remainder) for index in range(shards)]


def detector_point_limit(config):
    """Only a new frozen worker budget permits more than 35 source handoffs."""
    maximum = config.get('maximum_active_detector_workers')
    if maximum is None:
        return 35
    if type(maximum) is not int or not 1 <= maximum <= 2400:
        raise ValueError('The frozen detector-worker budget requires 1..2400 slots')
    return maximum


def meson_storage_limit(config):
    maximum = config.get('maximum_meson_forecast_bytes', 30000000000)
    if type(maximum) is not int or not 1 <= maximum <= 100000000000:
        raise ValueError('Require an explicit frozen meson storage ceiling of at most 100 GB')
    return maximum


def validate_batches(rows, prior_inventories, seed_base=761216001,
                     run_base=1761216001, detector_job_base=5500000,
                     mechanism='eta-etaprime-radiative', execution_limits=None):
    limits = source_execution_limits({}) if execution_limits is None else source_execution_limits(
        dict(source_execution_policy=execution_limits))
    if not isinstance(rows, list) or not 2 <= len(rows) <= limits['maximum_batches']:
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
        maximum = limits['maximum_canary_source_collisions'] if row.get('operational_canary', False) else limits['maximum_source_collisions_per_batch']
        if type(count) is not int or not 2 <= count <= maximum:
            raise ValueError('Fixed source exposure exceeds the frozen per-batch limit')
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
        # Small source shards can reserve fewer IDs. Actual detector preparation
        # still fails closed if a random-N source exceeds its frozen capacity.
        capacity = limits['detector_job_capacity_per_batch']
        job = detector_job_base + capacity*index
        if 230000000 + (job + capacity - 1)*100 + 40 > 899999900:
            raise ValueError('Detector namespace exceeds the unchanged RNG bounds')
        row.update(batch=batch, point=point, seed=seed, run_number=run,
                   detector_job_base=job, detector_job_capacity=capacity,
                   operational_canary=row.get('operational_canary', False))
        result.append(row)
        identifiers.add(batch); seeds.add(seed); runs.add(run)
    canaries = [row for row in result if row['operational_canary']]
    if mechanism == 'eta-etaprime-radiative' and (len(canaries) != 2
            or not any(row['mass_gev'] < .548 for row in canaries)
            or not any(row['mass_gev'] > .548 for row in canaries)):
        raise ValueError('Require distinct eta and eta-prime-only full-chain canaries')
    if mechanism == 'meson-radiative' and (len(canaries) != 2
            or len({row['point'] for row in canaries}) != 2):
        raise ValueError('Require two distinct declared unified-source full-chain canaries')
    if mechanism not in ('eta-etaprime-radiative', 'meson-radiative'):
        raise ValueError('Unsupported meson source mechanism')
    return result


def prepare(configuration, output, scripts=None):
    # Controller-only imports must work with old pinned GEN runners. This newer
    # model-freezing API is needed only while preparing new science.
    from run_dark_photon_meson_gen import freeze_meson_model
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError('Refuse to overwrite a meson deployment')
    config = json.loads(Path(configuration).read_text())
    if config.get('schema') != 'shift-dp-meson-production-request-v1':
        raise ValueError('Unsupported meson production request')
    if type(config.get('parallel_handoffs', False)) is not bool:
        raise ValueError('Parallel handoffs require an explicit boolean')
    mechanism = source_mechanism(config)
    requirements = ''
    if mechanism == 'meson-radiative':
        hosts = config.get('excluded_hosts', [])
        if (not isinstance(hosts, list) or any(not isinstance(host, str)
                or not re.fullmatch(r'[A-Za-z0-9.-]+\.cern\.ch', host) for host in hosts)):
            raise ValueError('Excluded hosts require explicit CERN machine names')
        if hosts:
            requirements = 'requirements = ' + ' && '.join(
                '(TARGET.Machine != ' + json.dumps(host) + ')' for host in hosts) + '\n'
    execution_limits = source_execution_limits(config)
    detector_limit = detector_point_limit(config)
    meson_storage_limit(config)
    batches = validate_batches(config['batches'], config['prior_inventories'],
        config.get('seed_base',761216001),config.get('run_base',1761216001),
        config.get('detector_job_base',5500000), mechanism, execution_limits)
    campaign_pattern = (r'[A-Za-z0-9_-]*20[0-9]{6}[A-Za-z0-9_-]*'
                        if mechanism == 'meson-radiative' else r'[A-Za-z0-9_-]*20261016[A-Za-z0-9_-]*')
    if (not re.fullmatch(campaign_pattern, config['campaign_prefix']) or
            config['eos_base'] != '/eos/user/j/jniedzie/shift_cmssw' or
            type(config.get('simultaneous_sources', 2)) is not int or
            not 1 <= config.get('simultaneous_sources', 2) <= execution_limits['maximum_simultaneous_sources'] or
            type(config.get('simultaneous_detector_points', 2)) is not int or
            not 1 <= config.get('simultaneous_detector_points', 2) <= detector_limit):
        raise ValueError('Require explicit production namespaces and bounded concurrency')
    if mechanism == 'meson-radiative':
        config.setdefault('publish_canary_aliases', False)
        if config['publish_canary_aliases'] is not False:
            raise ValueError('Unified operational canaries must leave global aliases and sample anchors unchanged')
        fresh = fresh_production_authority(config)
        if fresh is None:
            previous = prior_meson_configuration(config)
            controller = config['prior_meson_publication'].get('controller_receipt')
            if not isinstance(controller, dict) or sha(controller['path']) != controller.get('sha256'):
                raise ValueError('Prior meson publication requires its pinned terminal-controller receipt')
            native = json.loads(Path(controller['path']).read_text())
            if (type(native.get('cluster')) is not int or native['cluster'] <= 0
                    or not Path(native.get('task', '')).is_absolute()
                    or json.loads((Path(native['task'])/'config.json').read_text()).get('directory') != previous['directory']
                    or sha(Path(native['task'])/'controller.sub') != native.get('submit_sha256')):
                raise ValueError('Pinned prior meson controller does not own the original production')
        completion_scope(config)
        for row in batches:
            reference_point(config, row)
        additional_reservations(config)
        gen_runtime(config)
        coverage_request(config)
        minimum_floor = 50000000000 if fresh is not None else 90000000000
        if config.get('minimum_eos_free_bytes', 100000000000) < minimum_floor:
            raise ValueError('The unified-source storage floor is below the shared protected minimum')
        if (type(config.get('detector_workers_per_point', 10)) is not int
                or not 1 <= config.get('detector_workers_per_point', 10) <= 100
                or type(config.get('dag_max_idle', 5)) is not int
                or not 1 <= config.get('dag_max_idle', 5) <= 5):
            raise ValueError('Unified source requires bounded workers and at most five ready nodes')
        config.setdefault('detector_workers_per_point', 10)
        config.setdefault('dag_max_idle', 5)
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
        if key=='model_card':
            _,inputs=freeze_meson_model(source,target)
            for relative,expected in inputs.items():files[str(output/relative)]=expected
        else:shutil.copy2(source, target)
        config[key] = str(target)
        files[str(target)] = sha(target)
    for key in ('jaw_source_profile',):
        authority = config.get(key)
        if authority is None:
            continue
        source = Path(authority['path']).resolve()
        if sha(source) != authority['sha256']:
            raise ValueError('Source-placement profile changed')
        target = output / (key + '.json')
        shutil.copy2(source, target)
        config[key] = dict(path=str(target), sha256=sha(target))
        files[str(target)] = sha(target)
    calibration = config.get('calibration_authority')
    if calibration:
        if sha(calibration['path']) != calibration['sha256']:
            raise ValueError('Meson calibration authority changed')
        target = output/'calibration.json'
        shutil.copy2(calibration['path'], target)
        config['calibration_authority'] = dict(path=str(target), sha256=sha(target))
        files[str(target)] = sha(target)
    for key in ('lifetime_reference_authority', 'meson_completion_scope'):
        authority = config.get(key)
        if authority is None:
            continue
        source = Path(authority['path']).resolve()
        if sha(source) != authority['sha256']:
            raise ValueError('Pinned meson scope or lifetime authority changed: ' + key)
        target = output / (key + '.json')
        shutil.copy2(source, target)
        config[key] = dict(path=str(target), sha256=sha(target))
        files[str(target)] = sha(target)
    if mechanism == 'meson-radiative':
        if config.get('fresh_production_authority') is None:
            previous = config['prior_meson_publication']
            source = Path(previous['path']).resolve()
            if sha(source) != previous['sha256']:
                raise ValueError('Prior meson publication configuration changed during preparation')
            target = output/'prior_meson_publication.json'
            shutil.copy2(source, target)
            config['prior_meson_publication'] = dict(previous, path=str(target), sha256=sha(target))
            files[str(target)] = sha(target)
            authority = previous['controller_receipt']
            source = Path(authority['path']).resolve()
            if sha(source) != authority['sha256']:
                raise ValueError('Prior meson controller receipt changed during preparation')
            target = output/'prior_meson_controller_receipt.json'
            shutil.copy2(source, target)
            config['prior_meson_publication']['controller_receipt'] = dict(path=str(target), sha256=sha(target))
            files[str(target)] = sha(target)
        authority = config['gen_runtime']['provenance']
        source = Path(authority['path']).resolve()
        if sha(source) != authority['sha256']:
            raise ValueError('Frozen GEN runtime provenance changed')
        target = output / 'gen_runtime_provenance.json'
        shutil.copy2(source, target)
        config['gen_runtime']['provenance'] = dict(path=str(target), sha256=sha(target))
        files[str(target)] = sha(target)
    authorities=[]
    for index,authority in enumerate(config.get('operational_authorities',[])):
        if sha(authority['path'])!=authority['sha256']:
            raise ValueError('Operational qualification authority changed')
        target=output/'gates'/f'{index:02d}_{Path(authority["path"]).name}'
        target.parent.mkdir(exist_ok=True)
        shutil.copy2(authority['path'],target)
        authorities.append(dict(path=str(target),sha256=sha(target)))
        files[str(target)]=sha(target)
    config['operational_authorities']=authorities
    for key in ('qualified_template', 'reference_preparation', 'cmssw_base',
                'darkcast_path', 'darkcast_source_receipt') + (() if config.get('fresh_production_authority') else ('adaptive_directory',)):
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
    dependencies = [*config['prior_inventories'], config['darkcast_source_receipt'],
                    str(Path(config['qualified_template']) / 'seal.json')]
    if config.get('fresh_production_authority'):
        authority = config['fresh_production_authority']
        registration = fresh_production_authority(config)['authority']['storage_registration']
        dependencies.extend([authority['path'], registration['path']])
        dependencies.append(fresh_production_authority(config)['storage_registration']['native_queue_evidence']['path'])
    else:
        dependencies.append(str(Path(config['adaptive_directory']) / 'worklist.json'))
    for path in dependencies:
        files[str(Path(path).resolve())] = sha(path)
    receipt = Path(config['darkcast_source_receipt'])
    for relative, expected in json.loads(receipt.read_text())['files'].items():
        path = Path(config['darkcast_path']) / relative
        if sha(path) != expected:
            raise ValueError('Spectral width authority changed')
        files[str(path)] = expected
    config.update(schema='shift-dp-meson-production-v1', directory=str(output), tools=str(tools),
                  production_mechanism=mechanism,
                  batches=batches, events_per_job=20, group_events=config.get('group_events',4000),
                  gen_timeout_seconds=43200, publication_timeout_seconds=1800,
                  minimum_afs_free_mb=1024, minimum_eos_free_bytes=config.get('minimum_eos_free_bytes',100000000000),
                  minimum_eos_free_files=20000, simultaneous_sources=config.get('simultaneous_sources', 2),
                  simultaneous_detector_points=config.get('simultaneous_detector_points', 2),
                  no_automatic_science_retry=True)
    if mechanism == 'meson-radiative':
        terminal_seed = output / 'terminal_native_seed.json'
        new(terminal_seed, dict(schema='shift-reviewed-meson-successful-native-seed-v1', jobs={}))
        config['reviewed_terminal_native_jobs'] = dict(path=str(terminal_seed), sha256=sha(terminal_seed))
        files[str(terminal_seed)] = sha(terminal_seed)
        directory = output / 'coverage'
        directory.mkdir()
        descriptor = f'''# Code style: AI-code. AI-generated finite meson coverage audit.
universe = vanilla
executable = /usr/bin/python3
arguments = {tools}/run_dark_photon_meson_production.py --directory {output} --seal SEAL_DIGEST --action coverage
initialdir = {directory}
getenv = False
environment = "PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1"
should_transfer_files = NO
request_cpus = 1
request_memory = 1000MB
request_disk = 1000MB
{requirements}+JobFlavour = "workday"
+MaxRuntime = 3600
+SendCredential = True
+WantIOProxy = True
+ShiftDPStatisticsTask = "{directory}"
+ShiftDPStatisticsReadOnly = True
+JobBatchName = "SHIFT_DP_meson_coverage"
output = {directory}/audit.$(ClusterId).$(ProcId).out
error = {directory}/audit.$(ClusterId).$(ProcId).err
log = {directory}/events.log
on_exit_hold = (ExitBySignal == True) || (ExitCode != 0)
on_exit_remove = (ExitBySignal == False) && (ExitCode == 0)
periodic_release = False
queue
'''
        path = directory / 'coverage.sub.in'
        path.write_text(descriptor)
        files[str(path)] = sha(path)
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
{requirements}+JobFlavour = "tomorrow"
+MaxRuntime = 86400
+SendCredential = True
+WantIOProxy = True
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
        if config.get('parallel_handoffs', False):
            from run_dark_photon_meson_production import prepare_handoff_worker
            for path in prepare_handoff_worker(config, row):
                files[str(path)] = sha(path)
    new(output / 'config.json', config)
    files[str(output / 'config.json')] = sha(output / 'config.json')
    wrapper = output / 'lcg_controller.sh'
    wrapper.write_text(f'''#!/bin/bash
# Code style: AI-code. AI-generated frozen meson controller environment.
set -eo pipefail
cd {output}
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
        if config.get('parallel_handoffs', False):
            handoff = path / 'handoff'
            (handoff / 'worker.sub').write_text((handoff / 'worker.sub.in').read_text().replace('SEAL_DIGEST', seal))
    if mechanism == 'meson-radiative':
        path = output / 'coverage'
        (path / 'coverage.sub').write_text((path / 'coverage.sub.in').read_text().replace('SEAL_DIGEST', seal))
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
{requirements}+JobFlavour = "nextweek"
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
