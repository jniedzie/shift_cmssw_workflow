#!/usr/bin/env python3
# Code style: AI-code. AI-generated durable adaptive DP production supervision.
"""Run a frozen statistics worklist through GEN, grouping and published histograms.

Each wave has independent seeds and a bounded storage reservation. Failed waves
remain recorded and do not prevent unrelated complete points from publishing.
The operational canary must publish before the controller admits bulk work.
"""
import argparse
import ast
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


def validate_capacity(config):
    workers = config.get('detector_workers_per_wave', 100)
    waves = config['simultaneous_waves']
    if (type(workers) is not int or type(waves) is not int or
            not 1 <= workers <= 300 or not 1 <= waves <= 8 or workers * waves > 2400):
        raise ValueError('Reviewed capacity permits at most eight waves and 2400 detector workers')
    breakdown = config.get('fixed_publication_breakdown', {})
    if 'global_2400_inflight_detector_bound_bytes' in breakdown:
        if (breakdown['global_2400_inflight_detector_bound_bytes'] < workers * waves * 9900801
                or sum(breakdown.values()) != config['fixed_publication_bytes']):
            raise ValueError('Reviewed global in-flight publication allowance is too small')


def configuration(path):
    result = read(path)
    if result.get('schema') != 'shift-dp-statistics-controller-v1':
        raise ValueError('Unsupported statistics-controller configuration')
    for name, expected in result['dependencies'].items():
        if sha(name) != expected:
            raise ValueError('Frozen controller dependency changed: ' + name)
    validate_capacity(result)
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
    max_idle = config.get('dag_max_idle', cap)
    if type(max_idle) is not int or not 1 <= max_idle <= cap:
        raise ValueError('DAG ready queue limit must be between one and its worker cap')
    submit_file = directory / 'pipeline.dag.condor.sub'
    if not submit_file.is_file():
        run_condor(['/usr/bin/condor_submit_dag', '-no_submit', '-maxjobs', str(cap),
            '-maxidle', str(max_idle), '-append', 'initialdir = ' + str(directory),
            '-append', '+MaxRuntime = 604800',
            '-append', '+ShiftDPStatisticsTask = ' + json.dumps(str(directory)),
            str(directory / 'pipeline.dag')], cwd=directory, timeout=60)
    return submission(config, directory, submit_file)


def run_logged(argv, log, *, timeout=3600):
    argv = list(map(str, argv))
    environment = dict(os.environ)
    if argv[0] == '/usr/bin/python3':
        # An LCG controller uses another Python version. Its HOME and module
        # path must not leak into the explicit system-Python subprocess.
        for name in ('PYTHONHOME', 'PYTHONPATH', 'LD_LIBRARY_PATH', 'LD_PRELOAD', 'ROOTSYS'):
            environment.pop(name, None)
        environment['PATH'] = '/usr/bin:/bin'
    log = Path(log)
    attempt = 0
    while True:
        candidate = log if attempt == 0 else log.with_name(log.name + '.attempt' + str(attempt))
        try:
            output = candidate.open('x')
            break
        except FileExistsError:
            attempt += 1
    with output:
        subprocess.run(argv, check=True, timeout=timeout, env=environment,
                       stdout=output, stderr=subprocess.STDOUT)


def quota(config):
    observed_utc = datetime.now(timezone.utc).isoformat()
    text = run_condor(['/usr/bin/eos', 'root://eosuser.cern.ch', 'quota', '-m'], timeout=60)
    rows = [dict(token.split('=', 1) for token in line.split() if '=' in token) for line in text.splitlines()]
    rows = [row for row in rows if row.get('space') == '/eos/user/j/jniedzie/' and 'maxlogicalbytes' in row]
    if len(rows) != 1:
        raise ValueError('Unknown EOS quota')
    row = rows[0]
    return dict(observed_utc=observed_utc, free_bytes=int(row['maxlogicalbytes']) - int(row['usedlogicalbytes']),
                free_files=int(row['maxfiles']) - int(row['usedfiles']), raw=row)


def wave_directory(config, index):
    return Path(config['directory']) / 'waves' / f'w{index:03d}'


def gen_stage_directory(config, index):
    original = wave_directory(config,index)/'gen_stage'
    authority = config.get('reviewed_gen_stage_overrides', {}).get(str(index))
    if authority is None:
        return original
    proof = pinned_json(authority['review'])
    if (config['dependencies'].get(authority['review']['path']) != authority['review']['sha256']
            or proof.get('schema') != 'shift-reviewed-partial-GEN-recovery-v1'
            or proof.get('original_stage') != str(original)
            or proof.get('replacement_stage') != authority['directory']
            or proof.get('physics_seeds_exposure_unchanged') is not True):
        raise ValueError('GEN stage override lacks its exact reviewed partial recovery')
    return Path(authority['directory'])


def pinned_json(authority):
    path = Path(authority['path'])
    if not path.is_absolute() or sha(path) != authority['sha256']:
        raise ValueError('Supplement admission authority changed: ' + str(path))
    return read(path)


def healthy_peer_state(directory, maximum_age, observed):
    try:
        state = read(Path(directory) / 'state.json')
        checked = datetime.fromisoformat(state['checked_utc']).timestamp()
    except (FileNotFoundError, ValueError, KeyError, TypeError):
        return None
    if state.get('error') or state.get('failures') or not -60 <= observed - checked <= maximum_age:
        return None
    return state


def terminal_statistics_peer(authority, directory, worklist, observed):
    """Use a reviewed final publication after its controller heartbeat stops.

    A final heartbeat is expected to become stale. The separate pinned review
    preserves that state and its failed, superseded attempts; it cannot turn an
    unfinished or failed population into storage credit. Native live ownership
    is checked again on every call, using the cached successful history record.
    """
    proof = pinned_json(authority)
    root = Path(directory)
    if (proof.get('schema') != 'shift-statistics-terminal-storage-authority-v1'
            or proof.get('directory') != str(root) or proof.get('worklist') != worklist
            or not root.is_absolute() or root != root.resolve()):
        raise ValueError('Terminal statistics review differs from its protected allocation')
    controller = pinned_json(proof['controller_config'])
    receipt = pinned_json(proof['controller_receipt'])
    task = Path(receipt['task'])
    if (controller.get('directory') != str(root) or controller.get('worklist') != worklist['path']
            or proof['controller_config']['path'] != str(task/'config.json')
            or sha(task/'controller.sub') != receipt['submit_sha256']
            or type(receipt.get('cluster')) is not int or receipt['cluster'] <= 0):
        raise ValueError('Terminal statistics controller identity changed')
    native = proof['terminal_native_ad']
    if (native.get('ClusterId') != receipt['cluster'] or native.get('ProcId') != 0
            or native.get('Iwd') != str(task) or native.get('JobStatus') != 4
            or native.get('ExitBySignal') is not False or native.get('ExitCode') != 0
            or not 0 < native.get('JobStartDate', 0) <= native.get('CompletionDate', 0) <= observed):
        raise ValueError('Terminal statistics controller has no successful native closure')
    if proof['state']['path'] != str(root/'state.json'):
        raise ValueError('Terminal statistics state is outside its protected directory')
    state = pinned_json(proof['state'])
    if (state.get('finished') is not True or state.get('error') or state.get('aggregates')
            or state.get('unpublished_points') != []
            or datetime.fromisoformat(state['checked_utc']).timestamp() > observed):
        raise ValueError('Terminal statistics state still has unpublished work')
    reviewed = {}
    for pin in controller.get('retired_aggregate_attempts', []):
        retirement = pinned_json(pin)
        key = 'aggregate_'+retirement['point']
        if (retirement.get('schema') != 'shift-reviewed-aggregate-retirement-v1'
                or retirement.get('original_frozen_attempt_unchanged') is not True
                or retirement.get('payload_started') is not False
                or retirement.get('prior_state_entry') != state.get('failures', {}).get(key)):
            raise ValueError('Terminal statistics failure is not an exact reviewed retirement')
        reviewed[key] = retirement['prior_state_entry']
    if state.get('failures', {}) != reviewed:
        raise ValueError('Terminal statistics has an unreviewed failed attempt')
    allocation = pinned_json(worklist)
    waves = allocation['waves']
    if set(state.get('waves', {})) != {str(wave['wave_index']) for wave in waves}:
        raise ValueError('Terminal statistics waves differ from their complete worklist')
    total = 0
    verified_publications = set()
    for wave in waves:
        native_wave = state['waves'][str(wave['wave_index'])]
        if (native_wave.get('terminal') is not True or native_wave.get('successful') is not True
                or native_wave.get('exit_code') != 0):
            raise ValueError('Terminal statistics has an unfinished source wave')
        production = root/'waves'/f'w{wave["wave_index"]:03d}'/'production'
        plan_path = production/'plan.json'
        actual = {row['point']:row for row in read(plan_path)['points']}
        if set(actual) != {row['point'] for row in wave['batches']}:
            raise ValueError('Terminal statistics source plan changed its point allocation')
        for row in wave['batches']:
            if (type(row['events']) is not int or row['events'] <= 0
                    or any(actual[row['point']][key] != row[key] for key in ('events','seed','run_number'))
                    or not closed_primitive(production, row['point'], row['events'], observed)):
                raise ValueError('Terminal statistics source has no sealed complete population')
            population = state['published'][row['point']]
            report_path = Path(population['point_receipt_path'])
            if (str(plan_path) not in population.get('source_plans', [])
                    or not report_path.is_absolute() or report_path != report_path.resolve()
                    or not report_path.is_relative_to(root/'aggregates')
                    or datetime.fromisoformat(population['published_utc']).timestamp() > observed):
                raise ValueError('Terminal statistics source is absent from its final publication')
            if row['point'] not in verified_publications:
                if sha(report_path) != population['point_receipt_sha256']:
                    raise ValueError('Terminal statistics final histogram receipt changed')
                report = read(report_path)
                marker = report.get('score_histograms', {}).get('marker', {})
                histogram = marker.get('published', {}).get('histograms', {})
                if (report.get('complete') is not True or report.get('point') != row['point']
                        or report.get('events') != population['events'] or marker.get('complete') is not True
                        or histogram.get('path') != population['histogram_path']
                        or histogram.get('sha256') != population['histogram_sha256']):
                    raise ValueError('Terminal statistics final histogram receipt changed')
                verified_publications.add(row['point'])
            total += row['events']
    if type(allocation.get('total_additional_events')) is not int or total != allocation['total_additional_events']:
        raise ValueError('Terminal statistics closure omits part of the full source budget')
    # Read-only monitors and duplicate cleanup are not output producers. Any
    # controller, source, detector or aggregate still live prevents the credit.
    constraint = ('ClusterId == '+str(receipt['cluster'])+' || DAGManJobId == '+str(receipt['cluster'])+
        ' || Iwd == '+json.dumps(str(task))+' || regexp('+
        json.dumps('^'+re.escape(str(root))+'/(waves|aggregates)/')+',Iwd)')
    if jobs(controller, constraint):
        return None
    return state


def closed_primitive(directory, point, events, before):
    """Credit only a sealed complete original whose output predates the quota."""
    directory = Path(directory)
    names = [directory/'plan.json', directory/'seal.json', directory/'completed_points'/f'{point}.json']
    if not all(path.is_file() and path.stat().st_mtime <= before for path in names):
        return False
    plan, seal, report = map(read, names)
    plan_hash = sha(names[0])
    if (seal['files'].get(str(names[0])) != plan_hash or report.get('complete') is not True
            or report.get('full_gen_events',report.get('events')) != events or report.get('point') != point
            or report.get('plan_sha256') != plan_hash or report.get('seal_sha256') != sha(names[1])):
        raise ValueError('Published primitive differs from its sealed complete population')
    return True


def meson_published_batch(directory, batch, published, observed):
    """Bind credit to this source and its verified final histogram receipt."""
    if not isinstance(published, dict) or published.get('batch') != batch['batch']:
        return False
    if batch.get('gen_qualification_only') or published.get('source_qualification_only'):
        return False  # GEN checks have no final histograms and release no storage credit.
    if datetime.fromisoformat(published['published_utc']).timestamp() > observed:
        return False
    directory = Path(directory)
    production = directory/'batches'/batch['batch']/'production'
    report_path = Path(published['point_receipt_path'])
    primitive = production/'completed_points'/(batch['point']+'.json')
    if (not report_path.is_absolute() or '..' in report_path.parts
            or not report_path.is_relative_to(directory)
            or report_path.name != batch['point']+'.json'
            or (report_path != primitive and str(production/'plan.json') not in published.get('source_plans', []))
            or sha(report_path) != published['point_receipt_sha256']):
        raise ValueError('Meson published receipt changed ownership, source batch or checksum')
    report = read(report_path)
    marker = report.get('score_histograms', {}).get('marker', {})
    histogram = marker.get('published', {}).get('histograms', {})
    histogram_path = Path(published.get('histogram_path', ''))
    campaign = published.get('campaign')
    if (report.get('complete') is not True or report.get('point') != batch['point']
            or report.get('events') != published.get('events') or marker.get('complete') is not True
            or histogram.get('path') != published.get('histogram_path')
            or histogram.get('sha256') != published.get('histogram_sha256')
            or not re.fullmatch(r'[0-9a-f]{64}', histogram.get('sha256', ''))
            or not isinstance(campaign, str) or not campaign
            or histogram_path.parent.name != 'histograms_merged'
            or histogram_path.parent.parent.name != campaign
            or histogram_path.parent.parent.parent.name != 'darkphoton_'+batch['point']
            or report.get('final_output_base') != str(histogram_path.parent.parent)):
        raise ValueError('Meson published ROOT receipt differs from its complete point')
    source = read(production.parent/'source_status.json')
    if source.get('complete') is not True or source['source_collisions'] != batch['source_collisions']:
        raise ValueError('Meson storage credit changed the fixed full source exposure')
    return closed_primitive(production, batch['point'], source['events'], observed)


def supplement_campaign_claim(authority, observed, maximum_age, observer_config=None):
    allocation = pinned_json(authority['worklist'])
    directory = Path(authority['directory'])
    state = healthy_peer_state(directory, maximum_age, observed)
    terminal = False
    if authority.get('terminal_completion'):
        state = terminal_statistics_peer(authority['terminal_completion'], directory,
                                         authority['worklist'], observed)
        terminal = state is not None
    if authority['kind'] == 'statistics':
        total = allocation['total_additional_events']
        batches = [row for wave in allocation['waves'] for row in wave['batches']]
        counts = [row['events'] for row in batches]
        coefficient = authority['bytes_per_event']
        fixed = authority.get('fixed_bytes', 0)
        if (type(total) is not int or any(type(n) is not int or n <= 0 for n in counts)
                or sum(counts) != total or type(coefficient) is not int or coefficient <= 0
                or type(fixed) is not int or fixed < 0):
            raise ValueError('Supplement full statistics allocation is inconsistent')
        credited = 0
        for wave in allocation['waves'] if state else []:
            production = directory/'waves'/f'w{wave["wave_index"]:03d}'/'production'
            populations = state.get('published', {})
            if not all(str(production/'plan.json') in populations.get(row['point'],{}).get('source_plans', [])
                       and datetime.fromisoformat(populations[row['point']]['published_utc']).timestamp() <= observed
                       for row in wave['batches']):
                continue
            actual = {row['point']:row for row in read(production/'plan.json')['points']}
            for row in wave['batches']:
                if any(actual[row['point']][key] != row[key] for key in ('events','seed','run_number')):
                    raise ValueError('Supplement credit differs from its fixed source allocation')
            if all(closed_primitive(production,row['point'],row['events'],observed) for row in wave['batches']):
                credited += sum(row['events'] for row in wave['batches'])
        return dict(name=authority['name'],total_events=total,credited_events=credited,
                    remaining_bytes=fixed+(total-credited)*coefficient,peer_healthy=state is not None,
                    terminal_completion_verified=terminal)
    if authority['kind'] != 'meson':
        raise ValueError('Unknown supplement peer allocation kind')
    forecast = pinned_json(authority['forecast'])
    amounts = forecast['per_batch_forecast_bytes']
    if (forecast['worklist_sha256'] != authority['worklist']['sha256']
            or allocation.get('directory') != str(directory)
            or (allocation.get('model_card') is not None
                and forecast.get('model_card_sha256') != sha(allocation['model_card']))
            or set(amounts) != {row['batch'] for row in allocation['batches']}
            or any(type(n) is not int or n <= 0 for n in amounts.values())
            or sum(amounts.values()) != forecast['total_meson_forecast_bytes']):
        raise ValueError('Supplement meson forecast differs from the full fixed source allocation')
    if authority.get('controller_directory') and observer_config is not None:
        from run_dark_photon_meson_production import terminal_meson_peer_state
        terminal_state = terminal_meson_peer_state(observer_config, authority, observed)
        if terminal_state is not None:
            state, terminal = terminal_state, True
    credited = 0
    for batch in allocation['batches'] if state else []:
        published = state.get('batches',{}).get(batch['batch'],{}).get('published')
        if meson_published_batch(directory, batch, published, observed):
            credited += amounts[batch['batch']]
    return dict(name=authority['name'],remaining_bytes=sum(amounts.values())-credited,
                credited_bytes=credited,peer_healthy=state is not None,
                terminal_completion_verified=terminal)


def supplement_canaries(config, policy, policy_hash):
    """Automatically verify all qualified final histograms once they are ready."""
    from watch_dark_photon_production import EosRpc, completed_histogram
    import tempfile
    cohorts = policy.get('required_cohorts', [])
    reports, candidates = {}, []
    for authority in cohorts:
        directory = Path(authority['directory'])
        if sha(directory/'plan.json') != authority['plan_sha256'] or sha(directory/'seal.json') != authority['seal_sha256']:
            raise ValueError('Required detector canary deployment changed')
        for path in (directory/'statuses').glob('*.json'):
            if read(path).get('error'):
                raise RuntimeError('Required detector canary failed; preserve original: ' + str(path))
        plan = read(directory/'plan.json')
        for row in plan['points']:
            path = directory/'completed_points'/f'{row["point"]}.json'
            if not path.is_file() or read(path).get('complete') is not True:
                return False
            reports[str(path)] = sha(path)
            candidates.append(dict(source=dict(directory=directory,campaign=plan['campaign'],
                plan_sha256=authority['plan_sha256'],seal_sha256=authority['seal_sha256']),
                row=row,output=Path(row['final_output_base'])))
    cache = Path(config['directory'])/'supplement_canaries_verified.json'
    expected = dict(policy_sha256=policy_hash,point_receipts=reports)
    if cache.is_file() and read(cache) == expected:
        return True
    with tempfile.TemporaryDirectory(prefix='dp-supplement-canaries-',dir=os.environ.get('_CONDOR_SCRATCH_DIR','/tmp')) as scratch:
        transport = EosRpc('root://eosuser.cern.ch',Path(scratch))
        for candidate in candidates:
            if completed_histogram(candidate,transport) is None:
                return False
    save(cache, expected)
    return True


def supplement_admission(config, observation):
    authority = config['supplement_admission']
    if config['dependencies'].get(authority['path']) != authority['sha256']:
        raise ValueError('Supplement admission policy is outside frozen dependencies')
    policy = pinned_json(authority)
    if policy['schema'] != 'shift-dp-supplement-admission-v1':
        raise ValueError('Unsupported supplement admission policy')
    metadata = policy.get('required_sample_metadata')
    if metadata:
        path = Path(metadata['path'])
        if not path.is_file():
            return False,dict(reason='waiting_for_canonical_sample_parameters')
        receipt = read(path)
        points = receipt.get('points', [])
        if (receipt.get('schema') != 'shift-dark-photon-sample-metadata-publication-v1'
                or receipt.get('complete') is not True
                or sorted(row['point'] for row in points) != sorted(metadata['expected_points'])
                or any(not re.fullmatch(r'[0-9a-f]{64}',row.get('sha256',''))
                       or row['sha256'] != row.get('readback_sha256') or row.get('bytes',0) <= 0 for row in points)):
            raise ValueError('Canonical signal sample parameters are incomplete or inconsistent')
    if not supplement_canaries(config,policy,authority['sha256']):
        return False,dict(reason='waiting_for_complete_detector_canary_histogram_readbacks')
    observed = datetime.fromisoformat(observation['observed_utc']).timestamp()
    age = datetime.now(timezone.utc).timestamp()-observed
    if not 0 <= age <= policy.get('maximum_quota_age_seconds',300):
        return False,dict(reason='waiting_for_fresh_quota')
    campaigns = policy['campaigns']
    if policy.get('shared_capacity_review'):
        capacity = pinned_json(policy['shared_capacity_review'])
        own = next(row for row in campaigns if row['directory'] == config['directory'])
        if (policy['minimum_free_bytes'] < capacity['total_extra_reservation_bytes']
                or config['simultaneous_waves'] * config.get('detector_workers_per_wave',100)
                   > capacity[own['name']+'_detector_workers']):
            raise ValueError('Supplement concurrency exceeds its reviewed shared storage bound')
    roots = [row['directory'] for row in campaigns]
    if len(set(roots)) != len(roots) or config['directory'] not in roots:
        raise ValueError('Supplement admission omits its own campaign or repeats a peer')
    claims = [supplement_campaign_claim(dict(row, **(
        {'controller_directory': config['meson_peer_controller_directories'][row['name']]}
        if row['kind'] == 'meson' and row['name'] in config.get('meson_peer_controller_directories', {}) else {})),
        observed,policy.get('maximum_peer_age_seconds',1200), config) for row in campaigns]
    required = policy['minimum_free_bytes']+sum(row['remaining_bytes'] for row in claims)
    evidence = dict(reason='admitted' if observation['free_bytes'] >= required else 'waiting_for_shared_storage',
                    observed_utc=observation['observed_utc'],free_bytes=observation['free_bytes'],
                    required_free_bytes=required,claims=claims,retirement_credit_bytes=0)
    return observation['free_bytes'] >= required and observation['free_files'] >= policy['minimum_free_files'],evidence


def prepare_gen(config, config_path, index):
    directory = wave_directory(config, index)
    preparation_receipt = directory / 'preparation_receipt.json'
    if preparation_receipt.is_file():
        prepared = read(preparation_receipt)
    else:
        helper = module(Path(config['tools']) / 'prepare_dark_photon_statistics.py', '_dp_prepare')
        if config.get('gen_execution_scripts'):
            prepared = helper.prepare_wave(config['worklist'], index, directory,
                                           physics_scripts=config['gen_execution_scripts'])
        else:
            prepared = helper.prepare_wave(config['worklist'], index, directory)
    stage = gen_stage_directory(config,index)
    if not stage.is_dir():
        handoff_record = dict(handoff_argvs=[['/bin/bash', config['lcg_wrapper'],
            '--config', str(config_path), '--handoff-wave', str(index)]],
            retain_local_gen_root=False, minimum_eos_free_bytes=config['minimum_free_bytes'],
            minimum_eos_free_files=config['minimum_free_files'], afs_metadata_reservation_mb=128,
            private_gen_failure_base=config['failure_base'])
        # Operational rollovers keep the scientific wave and its failed
        # handoff intact. Bind a fresh immutable handoff to these exact tools.
        handoff_digest = hashlib.sha256(json.dumps(handoff_record, sort_keys=True).encode()).hexdigest()
        handoff = directory / ('handoff.' + handoff_digest[:16] + '.json')
        if handoff.exists():
            if read(handoff) != handoff_record:
                raise ValueError('Existing GEN handoff differs from its frozen operational owner')
        else:
            new(handoff, handoff_record)
        run_logged(['/usr/bin/python3', Path(config['tools']) / 'prepare_dark_photon_gen_stage.py',
            '--plan', prepared['preparation'], '--runtime-fingerprint', config['runtime_fingerprint'],
            '--handoff-config', handoff, '--handoff-dependencies', config_path,
            *config['dependencies'].keys(), '--prior-inventories', *config['prior_inventories'],
            '--workspace', config['workspace'], '--output', stage, '--gen-workers', str(config.get('gen_workers_per_wave',10))],
            directory / 'prepare_gen.log')
    return submit_dag(config, stage, config.get('gen_workers_per_wave',10)+2)


def production_budget(config, preparation):
    budget = copy.deepcopy(read(config['storage_budget_template']))
    budget.update(events=preparation['events'], detector_chunks=preparation['detector_chunks'],
        points=len(preparation['points']), external_reservations=[],
        fixed_reservation_bytes=config['fixed_publication_bytes'],
        gen_group_file_allowance=4000, safety_free_floor_files=5000)
    budget['fixed_reservation_breakdown'] = config['fixed_publication_breakdown']
    budget['publication_reservation_scope'] = 'One adaptive wave plus protected peer-wave and analysis headroom'
    return budget


def selected_preparation(config, directory, prepared):
    """Bind frozen material admission before replanning selected detector offsets."""
    original=read(prepared['preparation'])
    index=int(directory.name[1:])
    stage=gen_stage_directory(config,index)
    if stage != directory/'gen_stage':
        original=read(stage/'plan.json')
    if original.get('private_gen_runtime') is None:
        return prepared
    from dark_photon_material_selection import bind, validate_plan
    from prepare_dark_photon_material_source import immutable
    import prepare_dark_photon_grid as grid
    inventory=read(stage/'gen_inventory.json')
    publications={row['point']:row for row in read(stage/'gen_publication.json')['inputs']}
    destination=directory/'material_preparation.json'
    if destination.exists():
        plan=read(destination)
        validate_plan(plan,inventory)
    else:
        settings=config['material_admission']
        profile=directory/'material_runtime_profile.json'
        immutable(profile,original['detector_runtime'])
        bound=[]
        for row in original['points']:
            parent=inventory['points'][row['point']]
            gen=Path(row['gen_directory'])
            request=dict(parent=parent,gen_directory=str(gen),
                contract_sha256=sha(gen/'contract.json'),gen_publication=publications[row['point']],
                output_base=row['final_output_base'],geometry=copy.deepcopy(settings['geometry']),
                frame=copy.deepcopy(settings['frame']),cohort_id=original['campaign']+':'+row['point'],
                selection_seed=settings['selection_seed']+':'+original['campaign']+':'+row['point'])
            request_path=gen/'material_admission_request.json'
            immutable(request_path,request)
            tools=Path(config['tools'])
            run_logged(['/usr/bin/python3',tools/'dark_photon_detector_runtime.py',
                '--profile',profile,'--entry',tools/'prepare_dark_photon_material_source.py',
                '--','--request',request_path],
                directory/(row['point']+'_material.log'),timeout=3600)
            descriptor=gen/'material_selected_source.json'
            selected=bind(row,parent,read(descriptor))
            selected['material_selected_source_sha256']=sha(descriptor)
            bound.append(selected)
        plan=grid.plan_detector_stage(dict(original,points=bound),original['campaign'],
            read(config['worklist'])['eos_base'],events_per_job=original['chunk_size'],
            group_events=original['group_events'],
            detector_job_base=min(chunk['job'] for row in original['points'] for chunk in row['chunks']))
        by_point={row['point']:row for row in original['points']}
        for row in plan['points']:
            previous=by_point[row['point']]
            base=previous['chunks'][0]['job']
            for chunk in row['chunks']:
                chunk['job']=base+chunk['chunk']
            row['detector_job']=previous['detector_job']
        validate_plan(plan,inventory)
        new(destination,plan)
    return dict(prepared,preparation=str(destination),preparation_sha256=sha(destination),
        events=plan['events'],detector_chunks=plan['detector_chunks'])


def handoff(config, index):
    directory = wave_directory(config, index)
    with (directory / 'handoff.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        stage, production = gen_stage_directory(config,index), directory / 'production'
        operations = Path(config.get('handoff_operations', directory))
        if config.get('handoff_operations'):
            operations = operations / f'w{index:03d}'
        operations.mkdir(parents=True, exist_ok=True)
        if not read(stage / 'gen_inventory.json')['complete'] or not read(stage / 'gen_publication.json')['complete']:
            raise ValueError('Detector handoff requires the complete published GEN population')
        if not production.exists():
            prepared = selected_preparation(config,directory,read(directory / 'preparation_receipt.json'))
            plan = read(prepared['preparation'])
            budget = operations / 'storage_budget.json'
            expected_budget = production_budget(config, plan)
            if budget.exists():
                if read(budget) != expected_budget:
                    raise ValueError('Handoff storage budget differs from its frozen controller')
            else:
                new(budget, expected_budget)
            command = ['/usr/bin/python3', Path(config['tools']) / 'prepare_dark_photon_production.py',
                '--preparation', prepared['preparation'], '--template', config['qualified_template'],
                '--gen-inventory', stage / 'gen_inventory.json', '--gen-publication', stage / 'gen_publication.json',
                '--storage-budget', budget, '--output', production, '--prior-inventories', *config['prior_inventories'],
                '--canary-points', *([row['point'] for row in plan['points']] if index == 0 else
                                      [plan['points'][0]['point']]), '--failure-base', config['failure_base'],
                '--detector-workers', str(config.get('detector_workers_per_wave', 100)), '--finalizers', '2', '--publication-profile', 'nano-metadata-v1',
                '--publication-canary', config['publication_canary'], '--event-stall-timeout', '7200',
                '--first-event-stall-timeout', '7200', '--detector-timeout', '43200']
            budgets=config.get('absolute_stage_timeouts')
            if budgets is not None:
                if (not isinstance(budgets,list) or len(budgets)!=4
                        or any(type(value) is not int or not 7200<=value<=43200 for value in budgets)):
                    raise ValueError('Direct handoff requires four explicit qualified absolute stage budgets')
                command.extend(['--absolute-stage-timeouts',*map(str,budgets)])
            # Qualification imports uproot; keep its known LCG interpreter here.
            command[0] = sys.executable
            run_logged(command, operations / 'prepare_production.log')
        receipt = submit_dag(config, production, config.get('detector_workers_per_wave', 100) + 2)
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


def additional_statistics_ready(source):
    """Delay only a recovery whose standard rate sidecars are still publishing."""
    name=source.get('readiness_receipt')
    if name is None:
        return True
    path=Path(name)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('Additional statistics readiness needs an explicit absolute receipt')
    if not path.is_file():
        return False
    proof=read(path)
    if proof.get('complete') is not True or proof.get('published') is not True:
        return False
    points=proof.get('points',[])
    if (proof.get('schema')!='shift-dark-photon-rate-retention-v1' or
            proof.get('plan_sha256')!=source['plan_sha256'] or
            sorted(item.get('point','') for item in points)!=sorted(source.get('expected_ready_points',[]))):
        raise ValueError('Recovery rate readiness differs from the exact plan and points')
    for item in points:
        artifacts=item.get('artifacts',[])
        readbacks=item.get('publication_readbacks',[])
        receipt=item.get('receipt_publication',{})
        if (item.get('published') is not True or not artifacts or len(artifacts)!=len(readbacks) or
                not receipt.get('sha256') or receipt.get('readback_sha256')!=receipt.get('sha256')):
            raise ValueError('Recovery rate readiness lacks complete publication readbacks')
        for artifact,checked in zip(artifacts,readbacks):
            if (any(artifact.get(key)!=checked.get(key) for key in ('path','bytes','sha256')) or
                    checked.get('readback_sha256')!=artifact.get('sha256')):
                raise ValueError('Recovery rate sidecar readback differs from its retained input')
    return True


def available_sources(config):
    result = copy.deepcopy(read(config['baseline_sources'])['points'])
    result = {point: [source] for point, source in result.items()}
    if config.get('fresh_production'):
        if result:
            raise ValueError('Fresh reconstruction production cannot credit old baselines')
        result={point:[] for point in read(config['worklist'])['points']}
    condition = config['conditions']['sha256']
    # Primitive source populations only: an aggregate must never be counted twice.
    directories = [Path(config['previous_statistics'])]
    directories.extend(path / 'production' for path in sorted((Path(config['directory']) / 'waves').glob('w[0-9]*')))
    additional=config.get('additional_previous_statistics',[])
    if not isinstance(additional,list):
        raise ValueError('Additional previous statistics must be an explicit pinned list')
    seen={str(path) for path in directories}
    for source in additional:
        directory=Path(source['directory'])
        if (not directory.is_absolute() or '..' in directory.parts or str(directory) in seen
                or str(directory)!=source['directory']):
            raise ValueError('Additional statistics require distinct canonical absolute directories')
        for name,key in [('plan.json','plan_sha256'),('seal.json','seal_sha256')]:
            path=str(directory/name)
            if (config.get('dependencies',{}).get(path)!=source.get(key)
                    or sha(path)!=source.get(key)):
                raise ValueError('Additional statistics authority is changed or outside frozen dependencies')
        if read(directory/'seal.json')['files'].get(str(directory/'plan.json'))!=source['plan_sha256']:
            raise ValueError('Additional statistics plan is outside its production seal')
        seen.add(str(directory))
        if not additional_statistics_ready(source):
            continue
        directories.append(directory)
    for directory in directories:
        if not (directory / 'plan.json').is_file() or not (directory / 'seal.json').is_file():
            continue
        for row in read(directory / 'plan.json')['points']:
            point = row['point']
            report = directory / 'completed_points' / (point + '.json')
            if point in result and report.is_file() and read(report).get('complete') is True:
                result[point].append(authority(directory, point, condition))
    return result


def aggregate_helper_names(config):
    """Freeze the model's closure from its own physics tools, including imports."""
    helpers = ['aggregate_dark_photon_samples.py', 'dark_photon_production_support.py',
               'export_dark_photon_cross_sections.py', 'pythia_generation_ledger.py']
    if config.get('production_mechanism') in ('eta-etaprime-radiative', 'meson-radiative'):
        helpers += ['dark_photon_meson.py', 'dark_photon_meson_source.py']
        if config.get('model_card'):
            decay_types = {row.get('decay_type') for parent in read(config['model_card'])['parents']
                           for row in parent.get('exclusive_channels', [parent])}
            if decay_types & {'vector-pseudoscalar', 'pseudoscalar-dipion', 'vector-dipion', 'charged-vector-dipion'}:
                helpers += ['dark_photon_vector_meson.py']
            if decay_types & {'pseudoscalar-dipion', 'vector-dipion', 'charged-vector-dipion'}:
                helpers += ['dark_photon_dipion_meson.py']
            # The charged kernel also imports the neutral kernel's numerical guards.
            if decay_types & {'vector-dipion', 'charged-vector-dipion'}:
                helpers += ['dark_photon_rho_radiative.py']
            if 'charged-vector-dipion' in decay_types:
                helpers += ['dark_photon_charged_rho_radiative.py']
        elif config.get('production_mechanism') == 'meson-radiative':
            raise ValueError('Meson aggregate requires its actual model card to freeze decay kernels')
    for name in helpers:
        source = Path(config['tools'])/name
        if not source.is_file():
            raise FileNotFoundError('Required aggregate helper is missing: '+str(source))
        # Freeze imports inside functions too: rate export resolves kaon and
        # other model kernels lazily. A top-level-only scan misses that closure.
        for statement in ast.walk(ast.parse(source.read_text())):
            modules = ([row.name for row in statement.names] if isinstance(statement, ast.Import)
                       else [statement.module] if isinstance(statement, ast.ImportFrom) else [])
            for module in modules:
                if module and (module.startswith('dark_photon_') or module in
                               ('shift_production_selection', 'shift_material_preselection')):
                    dependency = module.split('.')[0]+'.py'
                    if dependency not in helpers:
                        helpers.append(dependency)
    return tuple(helpers)


def aggregate_plan(config, point, sources, directory):
    histogram_only = config.get('merge_source_histograms', True)
    group_events = config.get('aggregate_group_events', 2000)
    single_canary = config.get('single_source_regrouping_canary', False)
    if type(single_canary) is not bool or (single_canary and (histogram_only is not False or len(sources) != 1)):
        raise ValueError('Single-source canary requires exactly one source and full Nano regrouping')
    if type(histogram_only) is not bool or type(group_events) is not int or group_events < 1:
        raise ValueError('Aggregation requires a boolean histogram mode and positive integer group size')
    directory = Path(directory)
    helpers = aggregate_helper_names(config)
    directory.mkdir(parents=True)
    for name in ('logs', 'completed_points', 'failures'):
        (directory / name).mkdir()
    dependencies = {}
    for name in helpers:
        target = directory / name
        source = (Path(__file__).with_name(name) if single_canary and name == 'aggregate_dark_photon_samples.py'
                  else Path(config['tools']) / name)
        shutil.copy2(source, target)
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
        full_gen_events=sum(item.get('full_gen_events', item['events']) for item in source_rows),
        target_mean_lab_flight_m=source_rows[0].get('target_mean_lab_flight_m'),
        sources=sources, final_output_base=prefix)
    plan = dict(schema='shift-dark-photon-additive-plan-v1', campaign=campaign,
        histogram_only=histogram_only, points=[row], events=row['events'], conditions=config['conditions'],
        dependencies=dependencies, group_events=group_events, electronics_or_trigger_changes=False,
        scoring_deployment=dict(path=str(guard), seal_sha256=sha(guard / 'seal.json')),
        publication_guard=dict(deployment_path=str(guard), seal_sha256=sha(guard / 'seal.json'),
                               point=read(guard / 'plan.json')['points'][0]['point']))
    if single_canary:
        plan['single_source_regrouping_canary'] = True
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
    # A standalone aggregate must request its own delegated EOS credentials.
    # Hold failures for review instead of allowing CERN to requeue them.
    submit = re.sub(r'^(?:\+SendCredential|\+WantIOProxy|on_exit_hold|on_exit_remove)\s*=.*\n?',
                    '', submit, flags=re.MULTILINE | re.IGNORECASE)
    submit = re.sub(r'^arguments = .*$', 'arguments = ' + point + ' SEAL_DIGEST', submit, flags=re.MULTILINE)
    submit = submit.replace('\nqueue', '\n+SendCredential = True\n+WantIOProxy = True' +
        '\non_exit_hold = (ExitBySignal == True) || (ExitCode != 0)' +
        '\non_exit_remove = (ExitBySignal == False) && (ExitCode == 0)' +
        '\n+ShiftDPStatisticsTask = ' + json.dumps(str(directory)) +
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
            or type(plan.get('histogram_only')) is not bool
            or type(plan.get('group_events',2000)) is not int or plan.get('group_events',2000) < 1
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
    single_canary = plan.get('single_source_regrouping_canary',False)
    single_allowed = (single_canary is True and plan['histogram_only'] is False and len(sources) == 1)
    if (type(single_canary) is not bool or (single_canary and not single_allowed)
            or (len(sources) < 2 and not single_allowed)
            or len({json.dumps(item, sort_keys=True) for item in sources}) != len(sources)
            or any(item not in populations for item in sources)):
        raise ValueError('Existing aggregate source identity changed: ' + str(directory))
    return dict(directory=str(directory),
        sources_sha256=hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest(),
        source_plans=[item['plan_path'] for item in sources], plan_sha256=sha(plan_path),
        seal_sha256=sha(seal_path), submit_sha256=sha(submit))


def retired_aggregate_attempt(config, directory, point, record):
    """Skip only an explicitly reviewed, pinned terminal operational failure."""
    for authority in config.get('retired_aggregate_attempts',[]):
        path=Path(authority['path'])
        if (not path.is_absolute() or '..' in path.parts or
                config.get('dependencies',{}).get(str(path))!=authority.get('sha256') or
                sha(path)!=authority.get('sha256')):
            raise ValueError('Retired aggregate proof is outside frozen dependencies')
        proof=read(path)
        if proof.get('directory')!=str(directory):
            continue
        native=proof.get('terminal_native_ad',{})
        submission=read(directory/'submission.json')
        if (proof.get('schema')!='shift-reviewed-aggregate-retirement-v1' or
                proof.get('point')!=point or proof.get('aggregate_record')!=record or
                proof.get('payload_started') is not False or proof.get('queue_absent') is not True or
                native.get('Iwd')!=str(directory) or native.get('JobStatus')!=4 or
                native.get('ClusterId')!=submission.get('cluster') or native.get('ProcId')!=0 or
                native.get('ExitBySignal') is not False or native.get('ExitCode') in (None,0)):
            raise ValueError('Retired aggregate differs from its exact failed operational attempt')
        for name,expected in proof.get('failure_evidence_sha256',{}).items():
            if sha(name)!=expected:
                raise ValueError('Retired aggregate failure evidence changed')
        if jobs(config,'ClusterId == '+str(native['ClusterId'])):
            raise RuntimeError('Retired aggregate still has a live or held native job')
        return True
    return False


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
        if retired_aggregate_attempt(config,directory,point,record):
            continue
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
    if config.get('fresh_production'):
        from watch_dark_photon_production import EosRpc,completed_histogram
        production=Path(source_plan).parent
        if not Path(source_plan).is_file() or not (production/'seal.json').is_file():
            return False
        plan=read(source_plan)
        saved=state.get('operational_canary_qualification')
        if saved:
            if (saved['plan_sha256'] != sha(source_plan) or saved['seal_sha256'] != sha(production/'seal.json')
                    or any(sha(item['point_receipt_path']) != item['point_receipt_sha256'] for item in saved['points'])):
                raise ValueError('Qualified canary evidence changed')
            return True
        transport=EosRpc('root://eosuser.cern.ch',Path(config['directory'])/'readbacks')
        points=[]
        for point in names:
            row,=[row for row in plan['points'] if row['point']==point]
            candidate=dict(source=dict(directory=production,campaign=plan['campaign'],
                plan_sha256=sha(source_plan),seal_sha256=sha(production/'seal.json')),
                row=row,output=Path(row['final_output_base']))
            ready=completed_histogram(candidate,transport)
            if ready is None:
                return False
            points.append(ready)
        state['operational_canary_qualification']=dict(plan_sha256=sha(source_plan),
            seal_sha256=sha(production/'seal.json'),points=points,latest_aliases_unchanged=True)
        return True
    return all(source_plan in state.get('published', {}).get(point, {}).get('source_plans', [])
               for point in names)


def publish_ready_baselines(config, state):
    """Expose qualified pilot histograms while additional statistics wait for storage."""
    if not config.get('publish_ready_baselines', False):
        return
    from watch_dark_photon_production import EosRpc, completed_histogram
    import tempfile
    sources = read(config['baseline_sources'])['points']
    metadata = config['baseline_sample_metadata']
    if config['dependencies'].get(metadata['path']) != metadata['sha256']:
        raise ValueError('Baseline sample metadata is outside frozen dependencies')
    receipt = pinned_json(metadata)
    cards = receipt.get('points', [])
    if (receipt.get('schema') != 'shift-dark-photon-sample-metadata-publication-v1'
            or receipt.get('complete') is not True
            or sorted(row['point'] for row in cards) != sorted(sources)
            or any(not re.fullmatch(r'[0-9a-f]{64}', row.get('sha256', ''))
                   or row['sha256'] != row.get('readback_sha256') or row.get('bytes', 0) <= 0
                   for row in cards)):
        raise ValueError('Baseline sample metadata lacks exact complete publication readbacks')
    owner_path = config['publication_owner_receipt']
    if not Path(owner_path).is_file():
        return
    if config['dependencies'].get(owner_path) != sha(owner_path):
        raise ValueError('Baseline publication ownership changed')
    owner = read(owner_path)
    if (owner.get('owner') != config['directory'] or owner.get('exclusive_supplement_namespace') is not True
            or sorted(owner.get('points', [])) != sorted(sources)):
        raise ValueError('Baseline publication requires its exact exclusive point namespace')
    # Keep pilot receipts separate: ordinary published entries always identify an
    # aggregate, and each baseline remains exactly one primitive population.
    records = state.setdefault('baseline_publications', {})
    with tempfile.TemporaryDirectory(prefix='dp-ready-baselines-', dir=os.environ.get('_CONDOR_SCRATCH_DIR', '/tmp')) as scratch:
        transport = EosRpc('root://eosuser.cern.ch', Path(scratch))
        for point, authority in sources.items():
            if point in state.get('published', {}):
                continue  # Never move an aggregate alias back to its pilot.
            plan_path = Path(authority['plan_path'])
            directory = plan_path.parent
            if (authority['point'] != point or authority['conditions_sha256'] != config['conditions']['sha256']
                    or config['dependencies'].get(str(plan_path)) != authority['plan_sha256']
                    or config['dependencies'].get(str(directory / 'seal.json')) != authority['seal_sha256']
                    or sha(plan_path) != authority['plan_sha256']
                    or sha(directory / 'seal.json') != authority['seal_sha256']
                    or read(directory / 'seal.json')['files'].get(str(plan_path)) != authority['plan_sha256']):
                raise ValueError('Baseline publication authority changed: ' + point)
            plan = read(plan_path)
            rows = [row for row in plan['points'] if row['point'] == point]
            if len(rows) != 1:
                raise ValueError('Baseline publication needs one exact source point: ' + point)
            previous = records.get(point)
            report_path = directory / 'completed_points' / (point + '.json')
            if previous is None and (not report_path.is_file() or read(report_path).get('complete') is not True):
                continue
            alias = config['eos_base'] + '/darkphoton_' + point + '/latest'
            current = transport.alias_target(alias)
            if current not in (None, plan['campaign']):
                raise ValueError('Baseline publication would replace a different campaign: ' + point)
            if previous:
                if (previous.get('source') != authority or not report_path.is_file()
                        or previous.get('point_receipt_path') != str(report_path)
                        or previous.get('point_receipt_sha256') != sha(report_path)
                        or previous.get('campaign') != plan['campaign']
                        or previous.get('events') != rows[0]['events']
                        or previous.get('histogram_sha256') != read(report_path).get('score_histograms', {})
                            .get('marker', {}).get('published', {}).get('histograms', {}).get('sha256')):
                    raise ValueError('Previously published baseline changed: ' + point)
                if current == plan['campaign']:
                    continue  # Its immutable receipt, verified histogram and alias are unchanged.
            candidate = dict(source=dict(directory=directory, campaign=plan['campaign'],
                plan_sha256=authority['plan_sha256'], seal_sha256=authority['seal_sha256']),
                row=rows[0], output=Path(rows[0]['final_output_base']))
            ready = completed_histogram(candidate, transport)
            if ready is None:
                continue
            if previous and (previous.get('source') != authority
                    or previous.get('point_receipt_sha256') != ready['point_receipt_sha256']):
                raise ValueError('Previously published baseline changed: ' + point)
            transport.publish_alias(alias, ready['campaign'], [None, ready['campaign']])
            if previous is None:
                ready.update(source=authority, published_utc=datetime.now(timezone.utc).isoformat())
                records[point] = ready
                save(Path(config['directory']) / 'state.json', state)


def publish(config, state):
    from watch_dark_photon_production import EosRpc, completed_histogram
    transport = EosRpc('root://eosuser.cern.ch', Path(config['directory']) / 'readbacks')
    sources = available_sources(config)
    adaptive_prefix = str(Path(config['directory']) / 'waves') + '/'
    recovery_plans={str(Path(source['directory'])/'plan.json')
                    for source in config.get('additional_previous_statistics',[])}
    sources = {point: populations for point, populations in sources.items()
               if any(item['plan_path'].startswith(adaptive_prefix) or item['plan_path'] in recovery_plans
                      for item in populations)}
    canary_points = [row['point'] for row in read(config['worklist'])['waves'][0]['batches']]
    sources = dict(sorted(sources.items(), key=lambda item: (item[0] not in canary_points, item[0])))
    aggregate_jobs = jobs(config, 'regexp(' + json.dumps(str(Path(config['directory']) / 'aggregates')) +
                           ',ShiftDPStatisticsTask)')
    active = sum(row['JobStatus'] in (1, 2, 6, 7) for row in aggregate_jobs)
    for point, populations in sources.items():
        if len(populations) < 2:
            if config.get('fresh_production') and populations:
                source=populations[0];production=Path(source['plan_path']).parent
                plan=read(source['plan_path'])
                row,=[row for row in plan['points'] if row['point']==point]
                if row.get('full_gen_events',row['events']) < config.get('minimum_publish_gen_events',1000):
                    continue
                signature=hashlib.sha256(json.dumps(populations,sort_keys=True).encode()).hexdigest()
                if state.get('published',{}).get(point,{}).get('sources_sha256')==signature:
                    continue
                candidate=dict(source=dict(directory=production,campaign=plan['campaign'],
                    plan_sha256=source['plan_sha256'],seal_sha256=source['seal_sha256']),
                    row=row,output=Path(row['final_output_base']))
                ready=completed_histogram(candidate,transport)
                if ready and Path(config['publication_owner_receipt']).is_file():
                    alias=config['eos_base']+'/darkphoton_'+point+'/latest'
                    current=transport.alias_target(alias)
                    transport.publish_alias(alias,ready['campaign'],[current,ready['campaign']])
                    ready['primitive_receipt_path']=ready.pop('point_receipt_path')
                    ready['primitive_receipt_sha256']=ready.pop('point_receipt_sha256')
                    ready.update(sources_sha256=signature,source_plans=[source['plan_path']],
                        published_utc=datetime.now(timezone.utc).isoformat())
                    state.setdefault('published',{})[point]=ready
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


def adopt_reviewed_gen_handoffs(config, config_path, state):
    """Reuse published GEN after a reviewed orchestration-only failure."""
    for authority in config.get('reviewed_gen_handoffs', []):
        proof_path = authority['path']
        if (config['dependencies'].get(proof_path) != authority['sha256']
                or sha(proof_path) != authority['sha256']):
            raise ValueError('Reviewed GEN handoff authority changed')
        proof = read(proof_path)
        index = proof['wave_index']
        entry = state['waves'][str(index)]
        if entry.get('reviewed_handoff_sha256') == authority['sha256']:
            continue
        directory = wave_directory(config, index)
        stage = directory / 'gen_stage'
        cluster = proof['gen_cluster']
        if (entry['gen_cluster'] != cluster or proof['schema'] != 'shift-reviewed-gen-handoff-v1'
                or proof.get('science_rerun') is not False):
            raise ValueError('Reviewed GEN handoff differs from the admitted source population')
        for name in ('gen_inventory.json', 'gen_publication.json'):
            if sha(stage / name) != proof['files'][name] or read(stage / name).get('complete') is not True:
                raise ValueError('Reviewed published GEN authority changed')
        if jobs(config, f'ClusterId == {cluster} || DAGManJobId == {cluster}'):
            raise RuntimeError('Reviewed GEN source still has a live manager or child')
        history = parse_ads(run_condor(['/usr/bin/condor_history', *scheduler_arguments(config),
            str(cluster) + '.0', '-limit', '1', '-json'], timeout=45))
        budget_upgrade=proof.get('failure_kind')=='detector_stage_budget_upgrade_before_SIM'
        replacement=None
        if budget_upgrade:
            native=proof['terminal_native_ad']
            if (proof.get('detector_submission_absent') is not True
                    or proof.get('operational_cancel') is not True
                    or proof.get('absolute_stage_timeouts') != [43200,7200,14400,14400]
                    or proof.get('absolute_stage_timeouts') != config.get('absolute_stage_timeouts')
                    or len(history)!=1 or any(history[0].get(key)!=native.get(key)
                        for key in ('ClusterId','ProcId','Iwd','JobStatus','ExitCode','ExitBySignal'))
                    or native.get('ClusterId')!=cluster or native.get('ProcId')!=0
                    or native.get('Iwd')!=str(stage) or native.get('JobStatus') not in (3,4)
                    or (native.get('JobStatus')==4 and native.get('ExitCode') in (None,0))):
                raise RuntimeError('Budget handoff rollover differs from its exact reviewed terminal source manager')
            for name,digest in proof.get('preserved_material_descriptors',{}).items():
                if (not Path(name).is_absolute() or not Path(name).is_relative_to(directory)
                        or sha(name)!=digest):
                    raise ValueError('Reviewed handoff changed an existing frozen material admission')
            replacement=Path(proof['replacement_handoff_directory'])
            if (not replacement.is_absolute() or replacement.name!=f'w{index:03d}'
                    or not replacement.is_relative_to(Path(config['handoff_operations']))):
                raise ValueError('Budget handoff replacement must use its pinned persistent operational directory')
            description=replacement/'worker.sub'
            if config['dependencies'].get(str(description))!=sha(description):
                raise ValueError('Budget handoff submit description must be pinned')
            receipt=submission(config,replacement,description)
        else:
            if (len(history) != 1 or history[0].get('ExitCode') != 1
                    or history[0].get('ExitBySignal') is not False
                    or history[0].get('Iwd') != str(stage)):
                raise RuntimeError('Reviewed GEN handoff lacks its exact terminal native failure')
            recovery_log = Path(config['handoff_operations']) / f'reviewed_w{index:03d}.log'
            recovery_log.parent.mkdir(parents=True, exist_ok=True)
            run_logged(['/bin/bash', config['lcg_wrapper'], '--config', str(config_path),
                        '--handoff-wave', str(index)], recovery_log)
        previous = copy.deepcopy(entry)
        state.setdefault('reviewed_handoff_recoveries', []).append(dict(
            wave_index=index, previous=previous, authority=authority))
        for key in ('terminal', 'successful', 'exit_code', 'queue'):
            entry.pop(key, None)
        entry['reviewed_handoff_sha256'] = authority['sha256']
        if replacement is not None:
            entry.update(replacement_handoff_directory=str(replacement),replacement_handoff_cluster=receipt['cluster'])
        state.setdefault('failures', {}).pop('wave_' + str(index), None)
        # Persist the new handoff owner before the next reconciliation poll.
        state.update(checked_utc=datetime.now(timezone.utc).isoformat(),
                     heartbeat=state.get('heartbeat', 0) + 1)
        save(Path(config['directory']) / 'state.json', state)



def adopt_reviewed_detector_stages(config, state):
    """Replace only an audited pre-SIM shell failure; keep its scientific seal."""
    for index_text, authority in config.get('reviewed_detector_stage_overrides', {}).items():
        proof_path=authority['review']['path']
        digest=authority['review']['sha256']
        if config['dependencies'].get(proof_path)!=digest or sha(proof_path)!=digest:
            raise ValueError('Reviewed detector replacement authority changed')
        proof=read(proof_path);entry=state['waves'][index_text]
        if entry.get('reviewed_detector_sha256')==digest:
            continue
        original=wave_directory(config,int(index_text))/'production'
        replacement=Path(authority['directory'])
        native=proof['terminal_native_ad'];cluster=proof['detector_cluster']
        if (proof.get('schema')!='shift-reviewed-detector-stage-replacement-v1'
                or proof.get('failure_kind')!='malformed_budget_preflight_shell_before_SIM'
                or proof.get('science_rerun') is not False or proof.get('detector_payload_started') is not False
                or proof.get('wave_index')!=int(index_text) or proof.get('directory')!=str(replacement)
                or proof.get('original_directory')!=str(original)
                or not replacement.is_absolute() or replacement.name!=f'w{int(index_text):03d}'
                or not replacement.is_relative_to(Path(config['tools']).parent)
                or read(original/'submission.json')['cluster']!=cluster
                or sha(original/'plan.json')!=proof.get('original_plan_sha256')
                or sha(original/'seal.json')!=proof.get('original_seal_sha256')):
            raise ValueError('Detector replacement differs from its original sealed population')
        required={str(replacement/name) for name in ('pipeline.dag','detector.sub','detector_worker.sh')}
        if sha(original/'stage_budget_policy.json')!=proof.get('original_budget_policy_sha256'):
            raise ValueError('Original absolute stage budget changed')
        if not required.issubset(proof['files']):
            raise ValueError('Detector replacement must pin its DAG, executable and existing budget policy')
        for name,expected in read(original/'seal.json')['files'].items():
            if sha(name)!=expected:
                raise ValueError('Original scientific deployment changed before operational recovery')
        for name,expected in proof['files'].items():
            if config['dependencies'].get(name)!=expected or sha(name)!=expected:
                raise ValueError('Reviewed operational detector file changed')
        expected_dag=(original/'pipeline.dag').read_text().replace(str(original/'detector.sub'),str(replacement/'detector.sub'))
        expected_submit=(original/'detector.sub').read_text().replace(
            'executable = '+str(original/'detector_worker.sh'),'executable = '+str(replacement/'detector_worker.sh'))
        if ((replacement/'pipeline.dag').read_text()!=expected_dag
                or (replacement/'detector.sub').read_text()!=expected_submit):
            raise ValueError('Detector replacement may change only the executable, preserving all node arguments')
        if jobs(config,f'ClusterId == {cluster} || DAGManJobId == {cluster}'):
            raise RuntimeError('Original malformed detector stage still has a live manager or child')
        history=parse_ads(run_condor(['/usr/bin/condor_history',*scheduler_arguments(config),
            str(cluster)+'.0','-limit','1','-json'],timeout=45))
        fields=('ClusterId','ProcId','Iwd','JobStatus','ExitCode','ExitBySignal')
        if (len(history)!=1 or any(history[0].get(k)!=native.get(k) for k in fields)
                or native.get('ClusterId')!=cluster or native.get('ProcId')!=0
                or native.get('Iwd')!=str(original) or native.get('JobStatus')!=4
                or native.get('ExitCode')!=1 or native.get('ExitBySignal') is not False):
            raise RuntimeError('Detector replacement lacks its exact terminal operational failure')
        receipt=submit_dag(config,replacement,config.get('detector_workers_per_wave',100)+2)
        state.setdefault('reviewed_detector_recoveries',[]).append(dict(previous=copy.deepcopy(entry),authority=authority))
        for key in ('terminal','successful','exit_code','queue'):
            entry.pop(key,None)
        entry.update(reviewed_detector_sha256=digest,replacement_detector_directory=str(replacement),
            replacement_detector_cluster=receipt['cluster'])
        for key in ('wave_'+index_text,'held_wave_'+index_text):
            state.setdefault('failures',{}).pop(key,None)
        save(Path(config['directory'])/'state.json',state)


def fresh_storage_admission(config, observation):
    """Protect all future DP exposure and credit only closed published batches."""
    allocation=read(config['worklist'])
    observed=datetime.fromisoformat(observation['observed_utc']).timestamp()
    own_total=allocation['total_additional_events']
    own_credit=0
    for wave in allocation['waves']:
        production=wave_directory(config,wave['wave_index'])/'production'
        for row in wave['batches']:
            if closed_primitive(production,row['point'],row['events'],observed):
                own_credit+=row['events']
    peer=config['fresh_meson_peer']
    science=pinned_json(peer['configuration'])
    root=Path(science['directory'])
    peer_state=read(root/'state.json') if (root/'state.json').exists() else {}
    # Read native state before releasing any reservation; a failed query leaves
    # admission unknown. Completed receipts keep their hashes after heartbeat stops.
    from run_dark_photon_meson_production import directory_condition
    live=jobs(config,directory_condition(root))
    meson_credit=0
    for batch in science['batches']:
        prefix=str(root/'batches'/batch['batch'])
        if any(row.get('Iwd','').startswith(prefix) for row in live):
            continue
        published=peer_state.get('batches',{}).get(batch['batch'],{}).get('published')
        if meson_published_batch(root,batch,published,observed):
            meson_credit+=peer['forecast_bytes'][batch['batch']]
    own_remaining=config['fresh_own_fixed_bytes']+(own_total-own_credit)*326000
    meson_remaining=peer['reservation_bytes']-meson_credit
    regrouping=0
    authority=config.get('fresh_shared_aggregation_reservation')
    if authority is not None:
        reservation=pinned_json(authority)
        regrouping=reservation.get('additional_reserved_bytes')
        if (config['dependencies'].get(authority['path']) != authority['sha256']
                or reservation.get('schema') != 'shift-dp-shared-aggregation-reservation-v1'
                or reservation.get('complete') is not True
                or reservation.get('science_directory') != str(root)
                or reservation.get('science_config_sha256') != sha(root/'config.json')
                or reservation.get('source_envelope_bytes') != peer['reservation_bytes']
                or type(regrouping) is not int or regrouping <= 0
                or reservation.get('direct_DY_envelope_bytes',0) < config['fresh_own_fixed_bytes']+own_total*326000
                or reservation.get('combined_DP_reservation_bytes') !=
                   reservation['source_envelope_bytes']+reservation['direct_DY_envelope_bytes']+regrouping
                or config['directory'] not in reservation.get('registered_controller_directories',[])):
            raise ValueError('Shared regrouping reservation differs from its registered production')
    required=config['minimum_free_bytes']+own_remaining+meson_remaining+regrouping
    return observation['free_bytes']>=required,dict(reason='shared_storage_checked',
        required_free_bytes=required,free_bytes=observation['free_bytes'],
        own_future_bytes=own_remaining,meson_future_bytes=meson_remaining,
        shared_aggregation_reserved_bytes=regrouping,
        own_completed_GEN_events=own_credit,meson_closed_bytes=meson_credit)


def step(config, config_path, state):
    now = datetime.now(timezone.utc).isoformat()
    state.update(checked_utc=now, heartbeat=state.get('heartbeat', 0) + 1)
    adopt_reviewed_gen_handoffs(config, config_path, state)
    adopt_reviewed_detector_stages(config, state)
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
        detector_stage=Path(entry.get('replacement_detector_directory',str(production)))
        stage = detector_stage if (detector_stage / 'submission.json').is_file() else (
            Path(entry['replacement_handoff_directory']) if entry.get('replacement_handoff_directory')
            else gen_stage_directory(config,index))
        receipt = read(stage / 'submission.json')
        rows = jobs(config, 'ClusterId == ' + str(receipt['cluster']))
        entry.update(stage='detector' if stage == detector_stage else 'handoff' if entry.get('replacement_handoff_directory') else 'GEN',
                     cluster=receipt['cluster'], queue=rows)
        if not rows:
            history = parse_ads(run_condor(['/usr/bin/condor_history', *scheduler_arguments(config),
                str(receipt['cluster']) + '.0', '-limit', '1', '-json'], timeout=45))
            if len(history) != 1:
                raise RuntimeError('Missing manager in both queue and history')
            if stage != detector_stage and (directory / 'handoff_complete.json').is_file():
                active += 1
                continue  # Next pass switches to the submitted detector manager.
            entry.update(terminal=True, exit_code=history[0].get('ExitCode'),
                         successful=history[0].get('ExitCode') == 0 and stage == detector_stage)
            if not entry['successful']:
                state.setdefault('failures', {})['wave_' + index_text] = copy.deepcopy(entry)
        else:
            if any(row['JobStatus'] == 5 for row in rows):
                state.setdefault('failures', {})['held_wave_' + index_text] = copy.deepcopy(entry)
            active += 1
    publish_ready_baselines(config, state)
    if ((wave_directory(config, 0) / 'production' / 'plan.json').is_file()
            or config.get('additional_previous_statistics')):
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
        if config.get('fresh_production'):
            allowed,evidence=fresh_storage_admission(config,observation)
            state['fresh_storage_admission']=evidence
            if not allowed:
                state['admission']='waiting_for_shared_storage'
                break
        if config.get('supplement_admission'):
            observation = quota(config)
            state['quota'] = observation
            allowed,evidence = supplement_admission(config,observation)
            state['supplement_admission'] = evidence
            if not allowed:
                state['admission'] = evidence['reason']
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
