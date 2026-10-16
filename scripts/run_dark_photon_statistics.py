#!/usr/bin/env python3
# Code style: AI-code. AI-generated durable adaptive DP production supervision.
"""Run a frozen statistics worklist through GEN, grouping and published histograms.

Each wave has independent seeds and a bounded storage reservation. Failed waves
remain recorded and do not prevent unrelated complete points from publishing.
The operational canary must publish before the controller admits bulk work.
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
import shlex
import shutil
import subprocess
import sys
import time

from shift_condor_native import native_env, parse_ads, query, run_condor


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            result.update(block)
    return result.hexdigest()


def save(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.next')
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n')
    os.replace(temporary, path)


def new(path, value):
    with Path(path).open('x') as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write('\n')


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def configuration(path):
    result = read(path)
    if result.get('schema') != 'shift-dp-statistics-controller-v1':
        raise ValueError('Unsupported statistics-controller configuration')
    for name, expected in result['dependencies'].items():
        if sha(name) != expected:
            raise ValueError('Frozen controller dependency changed: ' + name)
    if not Path(result['directory']).is_absolute():
        raise ValueError('Controller requires a persistent absolute directory')
    return result


def scheduler_arguments(config):
    # A scheduler-universe controller must use its verified local schedd files.
    if Path('/var/lib/condor/spool/.schedd_address').is_file():
        return []
    return ['-pool', config['condor_pool'], '-name', config['condor_schedd']]


def jobs(config, constraint):
    arguments = scheduler_arguments(config)
    output = run_condor(['/usr/bin/condor_q', *arguments, '-constraint', constraint,
                         '-json', '-attributes',
                         'ClusterId,ProcId,JobStatus,Iwd,NumJobStarts,ExitCode,ExitBySignal,ShiftDPStatisticsTask'], timeout=45)
    return parse_ads(output)


def verify_submitted_job(config, directory, rows, cluster=None):
    """Accept one exact native job, including after a controller restart."""
    identity = str(directory)
    if not rows:
        if cluster is not None:
            selection = [str(cluster) + '.0', '-limit', '1']
        else:
            constraint = 'ShiftDPStatisticsTask == ' + json.dumps(identity)
            selection = ['-constraint', constraint, '-limit', '2']
        rows = parse_ads(run_condor(['/usr/bin/condor_history', *scheduler_arguments(config),
            *selection, '-json'], timeout=45))
    if len(rows) != 1:
        raise RuntimeError('Ambiguous submitted job state; preserve intent for review: ' + identity)
    row = rows[0]
    if row.get('Iwd') != identity:
        raise RuntimeError('Submitted job does not use its persistent working directory')
    if row.get('ShiftDPStatisticsTask') != identity:
        raise RuntimeError('Submitted job has a different statistics task identity')
    native_cluster = row.get('ClusterId')
    if (type(native_cluster) is not int or native_cluster <= 0
            or type(row.get('ProcId')) is not int or row['ProcId'] != 0
            or (cluster is not None and native_cluster != cluster)):
        raise RuntimeError('Submitted job differs from its exact native cluster and process')
    return native_cluster


def submission(config, directory, submit_file):
    """Reconcile a durable intent; never repeat an ambiguous submission."""
    directory, submit_file = Path(directory), Path(submit_file)
    receipt, intent = directory / 'submission.json', directory / 'submission_intent.json'
    identity = str(directory)
    constraint = 'ShiftDPStatisticsTask == ' + json.dumps(identity)
    rows = jobs(config, constraint)
    if receipt.is_file():
        result = read(receipt)
        if result.get('task') != identity or result['submit_sha256'] != sha(submit_file):
            raise ValueError('Submitted description changed: ' + identity)
        if type(result.get('cluster')) is not int or result['cluster'] <= 0:
            raise ValueError('Submission receipt has no exact native cluster')
        verify_submitted_job(config, directory, rows, result['cluster'])
        return result
    if intent.is_file():
        result = read(intent)
        if result.get('task') != identity or result['submit_sha256'] != sha(submit_file):
            raise ValueError('Submission intent description changed')
        cluster = result.get('cluster')
        if cluster is not None and (type(cluster) is not int or cluster <= 0):
            raise ValueError('Submission intent has an invalid native cluster')
        result.update(cluster=verify_submitted_job(config, directory, rows, cluster), recovered=True)
        new(receipt, result)
        return result
    if rows:
        raise RuntimeError('Unexpected existing owner of ' + identity)
    run_condor(['/usr/bin/condor_submit', *scheduler_arguments(config),
                '-dry-run', str(directory / 'submit.dryrun.ads'), str(submit_file)], cwd=directory, timeout=60)
    result = dict(task=identity, submit_sha256=sha(submit_file), utc=datetime.now(timezone.utc).isoformat())
    new(intent, result)
    output = run_condor(['/usr/bin/condor_submit', *scheduler_arguments(config), str(submit_file)],
                        cwd=directory, timeout=60)
    match = re.search(r'submitted to cluster (\d+)', output)
    if not match:
        raise RuntimeError('Submission returned no cluster; inspect intent before retrying')
    result.update(cluster=int(match.group(1)), output=output)
    save(intent, result)
    queued = jobs(config, 'ClusterId == ' + str(result['cluster']))
    verify_submitted_job(config, directory, queued, result['cluster'])
    new(receipt, result)
    return result


def submit_dag(config, directory, cap):
    directory = Path(directory)
    submit_file = directory / 'pipeline.dag.condor.sub'
    if not submit_file.is_file():
        run_condor(['/usr/bin/condor_submit_dag', '-no_submit', '-maxjobs', str(cap),
            '-maxidle', str(cap), '-append', 'initialdir = ' + str(directory),
            '-append', '+MaxRuntime = 604800',
            '-append', '+ShiftDPStatisticsTask = ' + json.dumps(str(directory)),
            str(directory / 'pipeline.dag')], cwd=directory, timeout=60)
    return submission(config, directory, submit_file)


def run_logged(argv, log, *, timeout=3600):
    with Path(log).open('x') as output:
        subprocess.run(list(map(str, argv)), check=True, timeout=timeout,
                       stdout=output, stderr=subprocess.STDOUT)


def quota(config):
    text = run_condor(['/usr/bin/eos', 'root://eosuser.cern.ch', 'quota', '-m'], timeout=60)
    rows = [dict(token.split('=', 1) for token in line.split() if '=' in token) for line in text.splitlines()]
    rows = [row for row in rows if row.get('space') == '/eos/user/j/jniedzie/' and 'maxlogicalbytes' in row]
    if len(rows) != 1:
        raise ValueError('Unknown EOS quota')
    row = rows[0]
    return dict(free_bytes=int(row['maxlogicalbytes']) - int(row['usedlogicalbytes']),
                free_files=int(row['maxfiles']) - int(row['usedfiles']), raw=row)


def wave_directory(config, index):
    return Path(config['directory']) / 'waves' / f'w{index:03d}'


def prepare_gen(config, config_path, index):
    directory = wave_directory(config, index)
    preparation_receipt = directory / 'preparation_receipt.json'
    if preparation_receipt.is_file():
        prepared = read(preparation_receipt)
    else:
        helper = module(Path(config['tools']) / 'prepare_dark_photon_statistics.py', '_dp_prepare')
        prepared = helper.prepare_wave(config['worklist'], index, directory)
    stage = directory / 'gen_stage'
    if not stage.is_dir():
        handoff = directory / 'handoff.json'
        new(handoff, dict(handoff_argvs=[['/bin/bash', config['lcg_wrapper'],
            '--config', str(config_path), '--handoff-wave', str(index)]],
            retain_local_gen_root=False, minimum_eos_free_bytes=config['minimum_free_bytes'],
            minimum_eos_free_files=config['minimum_free_files'], afs_metadata_reservation_mb=128))
        run_logged(['/usr/bin/python3', Path(config['tools']) / 'prepare_dark_photon_gen_stage.py',
            '--plan', prepared['preparation'], '--runtime-fingerprint', config['runtime_fingerprint'],
            '--handoff-config', handoff, '--handoff-dependencies', config_path,
            *config['dependencies'].keys(), '--prior-inventories', *config['prior_inventories'],
            '--workspace', config['workspace'], '--output', stage, '--gen-workers', '3'],
            directory / 'prepare_gen.log')
    return submit_dag(config, stage, 5)


def production_budget(config, preparation):
    budget = copy.deepcopy(read(config['storage_budget_template']))
    budget.update(events=preparation['events'], detector_chunks=preparation['detector_chunks'],
        points=len(preparation['points']), external_reservations=[],
        fixed_reservation_bytes=config['fixed_publication_bytes'],
        gen_group_file_allowance=4000, safety_free_floor_files=5000)
    budget['fixed_reservation_breakdown'] = config['fixed_publication_breakdown']
    budget['publication_reservation_scope'] = 'One adaptive wave plus protected peer-wave and analysis headroom'
    return budget


def handoff(config, index):
    directory = wave_directory(config, index)
    with (directory / 'handoff.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        stage, production = directory / 'gen_stage', directory / 'production'
        if not read(stage / 'gen_inventory.json')['complete'] or not read(stage / 'gen_publication.json')['complete']:
            raise ValueError('Detector handoff requires the complete published GEN population')
        if not production.exists():
            prepared = read(directory / 'preparation_receipt.json')
            plan = read(prepared['preparation'])
            budget = directory / 'storage_budget.json'
            new(budget, production_budget(config, plan))
            command = ['/usr/bin/python3', Path(config['tools']) / 'prepare_dark_photon_production.py',
                '--preparation', prepared['preparation'], '--template', config['qualified_template'],
                '--gen-inventory', stage / 'gen_inventory.json', '--gen-publication', stage / 'gen_publication.json',
                '--storage-budget', budget, '--output', production, '--prior-inventories', *config['prior_inventories'],
                '--canary-points', *([row['point'] for row in plan['points']] if index == 0 else
                                      [plan['points'][0]['point']]), '--failure-base', config['failure_base'],
                '--detector-workers', '100', '--finalizers', '2', '--publication-profile', 'nano-metadata-v1',
                '--publication-canary', config['publication_canary'], '--event-stall-timeout', '7200',
                '--first-event-stall-timeout', '7200', '--detector-timeout', '43200']
            # Qualification imports uproot; keep its known LCG interpreter here.
            command[0] = sys.executable
            run_logged(command, directory / 'prepare_production.log')
        receipt = submit_dag(config, production, 102)
        rates = directory / 'rates.json'
        if not rates.exists():
            run_logged([sys.executable, Path(config['tools']) / 'export_dark_photon_cross_sections.py',
                '--plan', production / 'plan.json', '--output', directory / 'rate_retention', '--publish', '--eos-rpc'],
                directory / 'retain_rates.log')
            new(rates, dict(complete=True, production_plan_sha256=sha(production / 'plan.json')))
        save(directory / 'handoff_complete.json', dict(complete=True, detector_manager=receipt['cluster'],
            plan_sha256=sha(production / 'plan.json'), utc=datetime.now(timezone.utc).isoformat()))


def authority(directory, point, condition):
    directory = Path(directory)
    return dict(plan_path=str(directory / 'plan.json'), plan_sha256=sha(directory / 'plan.json'),
                seal_sha256=sha(directory / 'seal.json'), point=point, conditions_sha256=condition)


def available_sources(config):
    result = copy.deepcopy(read(config['baseline_sources'])['points'])
    result = {point: [source] for point, source in result.items()}
    condition = config['conditions']['sha256']
    # Primitive source populations only: an aggregate must never be counted twice.
    directories = [Path(config['previous_statistics'])]
    directories.extend(path / 'production' for path in sorted((Path(config['directory']) / 'waves').glob('w[0-9]*')))
    for directory in directories:
        if not (directory / 'plan.json').is_file() or not (directory / 'seal.json').is_file():
            continue
        for row in read(directory / 'plan.json')['points']:
            point = row['point']
            report = directory / 'completed_points' / (point + '.json')
            if point in result and report.is_file() and read(report).get('complete') is True:
                result[point].append(authority(directory, point, condition))
    return result


def aggregate_plan(config, point, sources, directory):
    directory = Path(directory)
    directory.mkdir(parents=True)
    for name in ('logs', 'completed_points', 'failures'):
        (directory / name).mkdir()
    dependencies = {}
    helpers = ('aggregate_dark_photon_samples.py', 'dark_photon_production_support.py',
               'export_dark_photon_cross_sections.py', 'pythia_generation_ledger.py')
    if config.get('production_mechanism') == 'eta-etaprime-radiative':
        helpers += ('dark_photon_meson.py', 'dark_photon_meson_source.py')
    for name in helpers:
        target = directory / name
        shutil.copy2(Path(config['tools']) / name, target)
        dependencies[str(target)] = sha(target)
    source_rows = []
    for source in sources:
        plan = read(source['plan_path'])
        source_rows.append(next(row for row in plan['points'] if row['point'] == source['point']))
    models = [{key: row[key] for key in ('mass_gev', 'epsilon', 'proper_ctau_mm', 'sampling_epsilon')}
              for row in source_rows]
    if any(model != models[0] for model in models):
        raise ValueError('Cumulative histogram sources change the physical point')
    guard = Path(config.get('aggregation_guard', wave_directory(config, 0) / 'production'))
    campaign = config['campaign_prefix'] + '-' + point + '-v' + directory.name[1:]
    prefix = config['eos_base'] + '/darkphoton_' + point + '/' + campaign
    row = dict(point=point, **models[0], events=sum(item['events'] for item in source_rows),
        target_mean_lab_flight_m=source_rows[0].get('target_mean_lab_flight_m'),
        sources=sources, final_output_base=prefix)
    plan = dict(schema='shift-dark-photon-additive-plan-v1', campaign=campaign,
        histogram_only=True, points=[row], events=row['events'], conditions=config['conditions'],
        dependencies=dependencies, group_events=2000, electronics_or_trigger_changes=False,
        scoring_deployment=dict(path=str(guard), seal_sha256=sha(guard / 'seal.json')),
        publication_guard=dict(deployment_path=str(guard), seal_sha256=sha(guard / 'seal.json'),
                               point=read(guard / 'plan.json')['points'][0]['point']))
    new(directory / 'plan.json', plan)
    plan_hash = sha(directory / 'plan.json')
    original = (guard / 'finalizer_worker.sh').read_text()
    invocation = ('python3 ' + shlex.quote(str(directory / 'aggregate_dark_photon_samples.py')) +
        ' --plan ' + str(directory / 'plan.json') + ' --plan-sha256 ' + plan_hash +
        ' --seal-sha256 "$2" --point "$1" --runtime-cache "$tea_cache"' +
        ' --scratch "$_CONDOR_SCRATCH_DIR" --results-directory ' + str(directory))
    wrapper, count = re.subn(r'^python3 .*?/finalize_point\.py[^\n]*$', invocation, original, flags=re.MULTILINE)
    if count != 1:
        raise ValueError('Qualified finalizer entry point changed')
    target = directory / 'aggregate_worker.sh'
    target.write_text(wrapper)
    target.chmod(0o750)
    submit = (guard / 'finalizer.sub').read_text().replace(str(guard), str(directory))
    submit = submit.replace('finalizer_worker.sh', 'aggregate_worker.sh')
    # CERN supplies the EL9 requirement. Repeating this inherited expression
    # makes standalone condor_submit warn after committing the job.
    submit = re.sub(r'^requirements\s*=\s*\(TARGET\.OpSysMajorVer\s*==\s*9\)\s*$',
                    '', submit, flags=re.MULTILINE | re.IGNORECASE)
    submit = re.sub(r'^arguments = .*$', 'arguments = ' + point + ' SEAL_DIGEST', submit, flags=re.MULTILINE)
    submit = submit.replace('\nqueue', '\n+ShiftDPStatisticsTask = ' + json.dumps(str(directory)) +
        '\npoint = ' + point + '\nkind = aggregate\nchunk = all\nqueue')
    # Freeze the description template, then bind the seal outside its own hash.
    (directory / 'aggregate.sub.in').write_text(submit)
    new(directory / 'seal.json', dict(schema='shift-additive-signal-production-seal-v1',
        files={str(path): sha(path) for path in directory.iterdir() if path.is_file()}))
    (directory / 'aggregate.sub').write_text(submit.replace('SEAL_DIGEST', sha(directory / 'seal.json')))
    return row


def aggregate_record(directory, point, populations):
    """Recover the source identity from the sealed aggregate, not live state."""
    directory = Path(directory)
    plan_path, seal_path = directory / 'plan.json', directory / 'seal.json'
    plan, seal = read(plan_path), read(seal_path)
    if (plan.get('schema') != 'shift-dark-photon-additive-plan-v1'
            or plan.get('histogram_only') is not True
            or len(plan.get('points', [])) != 1 or plan['points'][0].get('point') != point):
        raise ValueError('Invalid existing aggregate plan: ' + str(directory))
    if seal.get('files', {}).get(str(plan_path)) != sha(plan_path):
        raise ValueError('Existing aggregate plan is outside its seal: ' + str(directory))
    for path, digest in seal['files'].items():
        if Path(path).parent != directory or sha(path) != digest:
            raise ValueError('Existing aggregate input changed: ' + path)
    template = directory / 'aggregate.sub.in'
    if seal['files'].get(str(template)) != sha(template):
        raise ValueError('Existing aggregate submit template is outside its seal')
    submit = directory / 'aggregate.sub'
    if submit.read_text() != template.read_text().replace('SEAL_DIGEST', sha(seal_path)):
        raise ValueError('Existing aggregate submit description changed')
    clusters = set()
    for name in ('submission_intent.json', 'submission.json'):
        path = directory / name
        if not path.exists():
            continue
        submitted = read(path)
        if (submitted.get('task') != str(directory)
                or submitted.get('submit_sha256') != sha(submit)):
            raise ValueError('Existing aggregate submission identity changed: ' + str(path))
        cluster = submitted.get('cluster')
        if cluster is not None:
            if type(cluster) is not int or cluster <= 0:
                raise ValueError('Invalid aggregate native cluster: ' + str(path))
            clusters.add(cluster)
        elif name == 'submission.json':
            raise ValueError('Aggregate submission receipt has no native cluster')
    if len(clusters) > 1:
        raise ValueError('Aggregate submission intent and receipt disagree about the native cluster')
    sources = plan['points'][0].get('sources', [])
    if (len(sources) < 2 or len({json.dumps(item, sort_keys=True) for item in sources}) != len(sources)
            or any(item not in populations for item in sources)):
        raise ValueError('Existing aggregate source identity changed: ' + str(directory))
    return dict(directory=str(directory),
        sources_sha256=hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest(),
        source_plans=[item['plan_path'] for item in sources], plan_sha256=sha(plan_path),
        seal_sha256=sha(seal_path), submit_sha256=sha(submit))


def reconcile_aggregate(config, state, point, populations):
    """One unresolved on-disk version owns a point, including lost-state restarts."""
    root = Path(config['directory']) / 'aggregates' / point
    pending = state.setdefault('aggregates', {}).get(point)
    published = state.setdefault('published', {}).get(point, {})
    published_version = None
    if published.get('point_receipt_path'):
        receipt = Path(published['point_receipt_path'])
        directory = receipt.parent.parent
        if (directory.parent != root or receipt.parent.name != 'completed_points'
                or receipt.name != point + '.json' or not re.fullmatch(r'v[0-9]+', directory.name)
                or published.get('point_receipt_sha256') != sha(receipt)):
            raise ValueError('Published aggregate receipt changed: ' + point)
        published_version = int(directory.name[1:])
    candidates = []
    for directory in sorted(root.glob('v*')):
        if not directory.is_dir() or not re.fullmatch(r'v[0-9]+', directory.name):
            raise ValueError('Unexpected aggregate version: ' + str(directory))
        record = aggregate_record(directory, point, populations)
        if published_version is not None and int(directory.name[1:]) <= published_version:
            if not set(record['source_plans']).issubset(published.get('source_plans', [])):
                raise ValueError('Older aggregate has unpublished sources: ' + str(directory))
            continue
        intent = directory / 'aggregate_intent.json'
        if intent.is_file() and read(intent) != record:
            raise ValueError('Existing aggregate intent changed: ' + str(directory))
        candidates.append(record)
    if len(candidates) > 1:
        raise RuntimeError('Ambiguous existing aggregate versions; preserve intents for review: ' + point)
    if not candidates:
        if pending:
            raise RuntimeError('Pending aggregate has no unresolved sealed version: ' + point)
        return None
    record = candidates[0]
    if pending and any(pending.get(key) != value for key, value in record.items()):
        raise ValueError('Pending aggregate differs from its sealed version: ' + point)
    if pending and 'cluster' in pending:
        record['cluster'] = pending['cluster']
    intent = Path(record['directory']) / 'aggregate_intent.json'
    if not intent.exists():
        new(intent, {key: value for key, value in record.items() if key != 'cluster'})
    state['aggregates'][point] = record
    save(Path(config['directory']) / 'state.json', state)
    return record


def published_canary(config, state):
    worklist = read(config['worklist'])
    wave = worklist['waves'][0]
    names = [row['point'] for row in wave['batches']]
    source_plan = str(wave_directory(config, 0) / 'production' / 'plan.json')
    return all(source_plan in state.get('published', {}).get(point, {}).get('source_plans', [])
               for point in names)


def publish(config, state):
    from watch_dark_photon_production import EosRpc, completed_histogram
    transport = EosRpc('root://eosuser.cern.ch', Path(config['directory']) / 'readbacks')
    sources = available_sources(config)
    adaptive_prefix = str(Path(config['directory']) / 'waves') + '/'
    sources = {point: populations for point, populations in sources.items()
               if any(item['plan_path'].startswith(adaptive_prefix) for item in populations)}
    canary_points = [row['point'] for row in read(config['worklist'])['waves'][0]['batches']]
    sources = dict(sorted(sources.items(), key=lambda item: (item[0] not in canary_points, item[0])))
    aggregate_jobs = jobs(config, 'regexp(' + json.dumps(str(Path(config['directory']) / 'aggregates')) +
                           ',ShiftDPStatisticsTask)')
    active = sum(row['JobStatus'] in (1, 2, 6, 7) for row in aggregate_jobs)
    for point, populations in sources.items():
        if len(populations) < 2:
            continue
        signature = hashlib.sha256(json.dumps(populations, sort_keys=True).encode()).hexdigest()
        old = state.setdefault('published', {}).get(point, {})
        pending = reconcile_aggregate(config, state, point, populations)
        if old.get('sources_sha256') == signature:
            if pending:
                raise RuntimeError('Unresolved aggregate duplicates an already published population: ' + point)
            continue
        root = Path(config['directory']) / 'aggregates' / point
        if pending:
            directory = Path(pending['directory'])
            for name, key in (('plan.json', 'plan_sha256'), ('seal.json', 'seal_sha256'),
                              ('aggregate.sub', 'submit_sha256')):
                if pending.get(key) != sha(directory / name):
                    raise ValueError('Pending aggregate input changed: ' + str(directory / name))
            if pending.get('cluster') is None:
                receipt = submission(config, directory, directory / 'aggregate.sub')
                pending['cluster'] = receipt['cluster']
                save(Path(config['directory']) / 'state.json', state)
                active += 1
                continue
            plan = read(directory / 'plan.json')
            candidate = dict(source=dict(directory=directory, campaign=plan['campaign'],
                plan_sha256=sha(directory / 'plan.json'), seal_sha256=sha(directory / 'seal.json')),
                row=plan['points'][0], output=Path(plan['points'][0]['final_output_base']))
            ready = completed_histogram(candidate, transport)
            if ready:
                if not Path(config['publication_owner_receipt']).is_file():
                    continue  # Wait for the reviewed handover from the old publisher.
                alias = config['eos_base'] + '/darkphoton_' + point + '/latest'
                current = transport.alias_target(alias)
                transport.publish_alias(alias, ready['campaign'], [current, ready['campaign']])
                base_events = next(row['events'] for row in read(populations[0]['plan_path'])['points']
                                   if row['point'] == populations[0]['point'])
                ready.update(sources_sha256=pending['sources_sha256'], new_events=ready['events'] - base_events,
                             source_plans=pending['source_plans'],
                             published_utc=datetime.now(timezone.utc).isoformat())
                state['published'][point] = ready
                state['aggregates'].pop(point)
                continue
            cluster_rows = [row for row in aggregate_jobs if row['ClusterId'] == pending['cluster']]
            if not cluster_rows or any(row['JobStatus'] == 5 for row in cluster_rows):
                state.setdefault('failures', {})['aggregate_' + point] = pending
            continue
        if active >= config['aggregate_workers']:
            continue
        version = max((int(path.name[1:]) for path in root.glob('v*')), default=0) + 1
        directory = root / f'v{version:04d}'
        aggregate_plan(config, point, populations, directory)
        pending = dict(directory=str(directory), sources_sha256=signature,
            source_plans=[item['plan_path'] for item in populations],
            plan_sha256=sha(directory / 'plan.json'), seal_sha256=sha(directory / 'seal.json'),
            submit_sha256=sha(directory / 'aggregate.sub'))
        new(directory / 'aggregate_intent.json', pending)
        state['aggregates'][point] = pending
        save(Path(config['directory']) / 'state.json', state)
        receipt = submission(config, directory, directory / 'aggregate.sub')
        pending['cluster'] = receipt['cluster']
        save(Path(config['directory']) / 'state.json', state)
        active += 1
    # Each verified histogram readback is disposable, including after an error.
    for path in transport.cache_directory.iterdir():
        if path.is_file():
            path.unlink()
    state['unpublished_points'] = [point for point, populations in sources.items() if len(populations) > 1 and
        hashlib.sha256(json.dumps(populations, sort_keys=True).encode()).hexdigest() !=
        state.get('published', {}).get(point, {}).get('sources_sha256')]


def step(config, config_path, state):
    now = datetime.now(timezone.utc).isoformat()
    state.update(checked_utc=now, heartbeat=state.get('heartbeat', 0) + 1)
    observation = quota(config)
    state['quota'] = observation
    worklist = read(config['worklist'])
    # Reconcile every admitted wave before admitting another one.
    active = 0
    for index_text, entry in state.setdefault('waves', {}).items():
        if entry.get('terminal'):
            continue
        index = int(index_text)
        directory = wave_directory(config, index)
        production = directory / 'production'
        stage = production if (production / 'submission.json').is_file() else directory / 'gen_stage'
        receipt = read(stage / 'submission.json')
        rows = jobs(config, 'ClusterId == ' + str(receipt['cluster']))
        entry.update(stage='detector' if stage == production else 'GEN',
                     cluster=receipt['cluster'], queue=rows)
        if not rows:
            history = parse_ads(run_condor(['/usr/bin/condor_history', *scheduler_arguments(config),
                str(receipt['cluster']) + '.0', '-limit', '1', '-json'], timeout=45))
            if len(history) != 1:
                raise RuntimeError('Missing manager in both queue and history')
            if stage != production and (directory / 'handoff_complete.json').is_file():
                active += 1
                continue  # Next pass switches to the submitted detector manager.
            entry.update(terminal=True, exit_code=history[0].get('ExitCode'),
                         successful=history[0].get('ExitCode') == 0 and stage == production)
            if not entry['successful']:
                state.setdefault('failures', {})['wave_' + index_text] = copy.deepcopy(entry)
        else:
            if any(row['JobStatus'] == 5 for row in rows):
                state.setdefault('failures', {})['held_wave_' + index_text] = copy.deepcopy(entry)
            active += 1
    if (wave_directory(config, 0) / 'production' / 'plan.json').is_file():
        publish(config, state)
    canary_ready = published_canary(config, state)
    state['canary_published'] = canary_ready
    for index in range(len(worklist['waves'])):
        if str(index) in state['waves']:
            continue
        if index and not canary_ready:
            break
        if active >= config['simultaneous_waves']:
            break
        if observation['free_bytes'] < config['minimum_free_bytes'] or observation['free_files'] < config['minimum_free_files']:
            state['admission'] = 'waiting_for_storage'
            break
        receipt = prepare_gen(config, config_path, index)
        state['waves'][str(index)] = dict(gen_cluster=receipt['cluster'], admitted_utc=now)
        active += 1
        # Persist immediately after each scheduler mutation, before more work.
        save(Path(config['directory']) / 'state.json', state)
    state['finished'] = (len(state['waves']) == len(worklist['waves']) and
                         all(row.get('terminal') for row in state['waves'].values()) and
                         not state.get('aggregates') and not state.get('unpublished_points'))
    state['complete'] = state['finished'] and not state.get('failures')
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--handoff-wave', type=int)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    config_path = args.config.resolve()
    config = configuration(config_path)
    if args.handoff_wave is not None:
        handoff(config, args.handoff_wave)
        return
    directory = Path(config['directory'])
    with (directory / 'controller.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state_path = directory / 'state.json'
        state = read(state_path) if state_path.is_file() else dict(started_utc=datetime.now(timezone.utc).isoformat())
        while True:
            try:
                state.pop('error', None)
                state = step(config, config_path, state)
            except Exception as error:
                state.update(error=repr(error), checked_utc=datetime.now(timezone.utc).isoformat())
            save(state_path, state)
            if args.once or state.get('finished'):
                return
            time.sleep(config.get('poll_seconds', 120))


if __name__ == '__main__':
    main()
