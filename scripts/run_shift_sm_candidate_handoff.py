#!/usr/bin/env python3
# Code style: AI-code.
"""Prepare and optionally submit one frozen, source-independent SM response DAG.

Run this after a successful GEN-preparation job. Original admission and Nano
weights stay unchanged; the additional candidate/control draw keeps q separate.
Submission intents are durable: an uncertain submission is never repeated.
"""
import argparse
import copy
from contextlib import ExitStack
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import time

from monitor_shift_sm_response_benchmark import write_snapshot
from prepare_shift_sm_candidate_reco import materialize_fresh_authorities, prepare_draw
from prepare_shift_sm_response_jobs import (detector_request, envelope, persistent_directory,
    preflight_request, read, sha, submit_text)
from shift_condor_native import native_env, query, run_condor
from run_shift_sm_response_collection import verify_exposure


MIB = 1048576
SCHEMA = 'shift-sm-candidate-handoff-v1'
BUDGET_SCHEMA = 'shift-sm-candidate-global-admission-budget-v1'
WAVE_SCHEMA = 'shift-sm-candidate-wave-handoff-v1'


def pin(path):
    return dict(path=str(path), sha256=sha(path))


def save(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    write_snapshot(path, value)


def checked(path, expected):
    path = Path(path)
    if not path.is_file() or path.is_symlink() or sha(path) != expected:
        raise ValueError('Frozen input changed: '+str(path))
    return path


def reserve_budget(path, source_id, plan_sha256, count):
    """Reserve one complete source draw, without silently dropping its controls."""
    path = persistent_directory(path)
    with path.with_suffix(path.suffix+'.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = read(path)
        if (state.get('schema') != BUDGET_SCHEMA
                or type(state.get('maximum_detector_jobs')) is not int
                or state['maximum_detector_jobs'] < 1 or type(count) is not int or count < 0):
            raise ValueError('Invalid global detector admission budget')
        reservations = state['reservations']
        record = dict(plan_sha256=plan_sha256, detector_jobs=count)
        if source_id in reservations:
            previous = reservations[source_id]
            if {key: previous[key] for key in record} != record:
                raise ValueError('Existing source admission reservation differs')
            return previous
        used = sum(row['detector_jobs'] for row in reservations.values())
        if used+count > state['maximum_detector_jobs']:
            raise ValueError('Global detector budget exhausted; preserve this complete source draw')
        if 'next_detector_job' in state:
            base, limit = state['next_detector_job'], state['detector_job_limit']
            if (type(base) is not int or type(limit) is not int
                    or not 100000 <= base <= base+count < limit <= 6600000):
                raise ValueError('Reserved detector identity pool is exhausted or invalid')
            record['detector_job_base'] = base
            state['next_detector_job'] = base+max(count, 1)
        reservations[source_id] = record
        save(path, state)
        return record


def feature_record(config, result, temporary):
    """Use hash-bound local/returned features, or download the published census."""
    family = result['source']['stratum'].split('_', 1)[0]
    if family not in ('qcd', 'dy', 'jpsi'):
        raise ValueError('Require a supported ordinary SM source family')
    published = [row for row in result['prepared_publications']
        if row.get('name') == family+'_features.json.gz']
    if len(published) != 1 or published[0]['bytes'] > 32*MIB:
        raise ValueError('Require one bounded published full feature census')
    expected = published[0]
    target = Path(temporary)/'features.json.gz'
    if config.get('feature_path'):
        source = checked(config['feature_path'], expected['sha256'])
        shutil.copyfile(source, target)
    else:
        directory = Path(config['preparation_directory'])
        archive = directory/'return_payload.tar.gz'
        status = read(directory/'status.json')
        if archive.exists():
            checked(archive, status['return_payload_sha256'])
            with tarfile.open(archive) as stream:
                members = [m for m in stream.getmembers() if m.name == 'features.json.gz']
                if len(members) > 1:
                    raise ValueError('Duplicate returned feature census')
                if members:
                    member = members[0]
                    if not member.isfile() or member.size != expected['bytes']:
                        raise ValueError('Returned feature census is not the bounded regular publication')
                    with stream.extractfile(member) as source, target.open('wb') as output:
                        shutil.copyfileobj(source, output)
        if not target.exists():
            path = expected['path']
            if not path.startswith('/eos/user/j/jniedzie/shift_cmssw_support/'):
                raise ValueError('Feature publication lies outside authorized support storage')
            subprocess.run(['/usr/bin/xrdcp', '--silent', '--cksum', 'adler32',
                'root://eosuser.cern.ch/'+path, str(target)],
                env=native_env(), check=True, timeout=900, capture_output=True, text=True)
    checked(target, expected['sha256'])
    if target.stat().st_size != expected['bytes']:
        raise ValueError('Published feature size differs')
    return dict(feature_path=str(target), feature_sha256=expected['sha256'],
        published_feature=expected)


def validate_preparation(config):
    directory = persistent_directory(config['preparation_directory'])
    request = checked(directory/'request.json', config['preparation_request_sha256'])
    result, status = read(directory/'result.json'), read(directory/'status.json')
    for record in (result, status):
        if record.get('complete') is not True or record.get('exit_code') != 0:
            raise ValueError('GEN preparation did not complete successfully')
    empty = (result.get('empty_source') is True and result.get('selected_events') == 0
             and result.get('source', {}).get('saved_events') == 0
             and result['source'].get('full_source_trials', 0) > 0)
    if result.get('request_sha256') != sha(request) or not (result.get('selected_source') or empty):
        raise ValueError('GEN preparation result belongs to another request or lacks admission')
    return directory, result


def validate_frozen(config):
    frozen = config['frozen']
    checked(frozen['capsule'], frozen['capsule_sha256'])
    checked(frozen['bootstrap'], frozen['bootstrap_sha256'])
    helpers = persistent_directory(config['helpers_directory'])
    for name, expected in config['helper_sha256s'].items():
        checked(helpers/name, expected)
    for name in ('monitor_shift_sm_response_benchmark.py', 'shift_condor_native.py'):
        if name not in config['helper_sha256s']:
            raise ValueError('The watchdog helper closure is not pinned')
    return frozen


def verify_prepared(record, config_sha256):
    if record['configuration_sha256'] != config_sha256:
        raise ValueError('A prepared source cannot be redrawn with another configuration')
    for item in record['frozen_files']:
        checked(item['path'], item['sha256'])
    return record


def frozen_source_draw(bundle, preparation, maximum):
    """Project a frozen cohort draw onto one source without redrawing q."""
    data = {name: read(checked(value['path'], value['sha256'])) for name, value in bundle.items()}
    plan, exposure, pins = data['plan'], data['exposure'], data['source_pins']
    comparison = plan.get('comparison_kind') == 'disjoint-source-smart-versus-random-v1'
    cohort = plan.get('controls_scope') == 'cohort' and plan.get('comparison_kind') is None
    if (not (comparison or cohort)
            or plan.get('draw_frozen') is not True
            or pins.get('frozen_benchmark_sha256') != bundle['plan']['sha256']):
        raise ValueError('Require an intact globally frozen selection comparison')
    verify_exposure(exposure, plan)
    sid = preparation['source_id']
    sources = [row for row in pins['sources'] if row['source_id'] == sid]
    originals = [row for row in exposure['sources'] if row['source_id'] == sid]
    if len(sources) != 1 or len(originals) != 1:
        raise ValueError('Global comparison does not uniquely cover this original source')
    source, original = sources[0], originals[0]
    descriptor = source['original_selected_source']
    authority = read(checked(source['original_result_path'], source['original_result_sha256']))
    if (source['full_original_exposure'] != preparation['source']
            or authority['selected_source'] != descriptor
            or descriptor['gen_sha256'] != preparation['selected_source']['gen_sha256']
            or descriptor['event_ids'] != preparation['selected_source']['event_ids']
            or descriptor['weights'] != preparation['selected_source']['weights']
            or descriptor['selected_indices'] != list(range(preparation['source']['saved_events']))
            or descriptor['sampling_probabilities'] != [1.]*preparation['source']['saved_events']):
        raise ValueError('Globally frozen source differs from the actual complete GEN preparation')
    selected = [row for row in plan['selected'] if row['source_id'] == sid]
    if len(selected) > maximum:
        raise ValueError('Frozen source exceeds the detector event budget')
    if comparison and any(row['comparison_arm'] != plan['source_assignments'][sid] for row in selected):
        raise ValueError('Frozen comparison arm differs from its source assignment')
    # The arm budget and global cell counts describe the global experiment,
    # not a new per-source draw. Keep that immutable authority by reference.
    local_plan = copy.deepcopy({name: value for name, value in plan.items() if name not in
        ('comparison_kind', 'comparison_budgets', 'source_assignments', 'strata', 'selected')})
    local_plan.update(population_events=original['old_selected_events'], selected=selected,
        selected_events=len(selected), maximum_events=maximum, strata=[],
        source_projection_does_not_redraw=True)
    local_plan['global_comparison_plan' if comparison else 'global_response_plan'] = bundle['plan']
    local_exposure = dict(exposure, sources=[original],
                          original_saved_GEN_events=original['original_saved_events'])
    verify_exposure(local_exposure, local_plan)
    feature = next(row for row in preparation['prepared_publications'] if row['name']=='qcd_features.json.gz')
    return dict(plan=local_plan, exposure=local_exposure,
        source_pins=dict(schema='shift-sm-prospective-source-pins-v1', sources=[source]),
        missed_candidates=[], fresh_authorities={}), dict(published_feature=feature)


def prepare(config, config_sha256):
    if config.get('schema') != SCHEMA:
        raise ValueError('Unsupported candidate handoff configuration')
    if config.get('fresh_census') and config.get('frozen_response_draw'):
        raise ValueError('Fresh first-SIM census cannot inherit an older frozen response draw')
    output = persistent_directory(config['output_directory'])
    if (output/'prepared.json').exists():
        return verify_prepared(read(output/'prepared.json'), config_sha256)
    if output.exists() and any(output.iterdir()):
        raise ValueError('Incomplete existing handoff requires review; never redraw or overwrite')
    directory, result = validate_preparation(config)
    frozen = validate_frozen(config)
    batch = config['job_batch_name']
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', batch):
        raise ValueError('Use a meaningful plain job batch name')
    eos = config['output_eos_directory'].rstrip('/')
    if not eos.startswith('/eos/user/j/jniedzie/shift_cmssw_support/') or '..' in Path(eos).parts:
        raise ValueError('Candidate outputs must stay in authorized support storage')
    maximum = config.get('maximum_events', 1000)
    if type(maximum) is not int or not 1 <= maximum <= 1000:
        raise ValueError('Each source draw must have an explicit bound of at most 1000 originals')
    if result.get('empty_source') is True:
        source = result['source']
        original = dict(source_id=result['source_id'],stratum=source['stratum'],
            original_raw_gen_sha256=source['gen_sha256'],prepared_gen_sha256=source['gen_sha256'],
            original_result_path=str(directory/'result.json'),original_result_sha256=sha(directory/'result.json'),
            original_full_exposure=source,original_saved_events=0,old_selected_events=0)
        draw = dict(plan=dict(schema='shift-sm-prospective-response-benchmark-v1',
            selected=[],selected_events=0,population_events=0,maximum_events=maximum,
            selection_seed=config['selection_seed'],draw_frozen=True,strata=[]),
            exposure=dict(schema='shift-sm-response-full-population-exposure-v1',
                sources=[original],original_saved_GEN_events=0,
                includes_sources_with_no_benchmark_event_drawn=True),
            source_pins=dict(schema='shift-sm-prospective-source-pins-v1',sources=[]),
            missed_candidates=[],fresh_authorities={})
        verify_exposure(draw['exposure'],draw['plan'])
        features = dict(published_feature=None)
    elif config.get('frozen_response_draw'):
        draw, features = frozen_source_draw(config['frozen_response_draw'], result, maximum)
    else:
        with tempfile.TemporaryDirectory(prefix='shift-sm-candidate-') as temporary:
            features = feature_record(config, result, temporary)
            record = dict(result_path=str(directory/'result.json'),
                result_sha256=sha(directory/'result.json'), **features)
            if config.get('fresh_census'):
                if (config.get('first_sim_source') is not True
                        or config.get('preparation_mask_unused') is not True):
                    raise ValueError('Fresh census requires an explicitly unused pre-SIM source')
                record.update(fresh_census=True, first_sim_source=True, preparation_mask_unused=True)
            draw = prepare_draw([record], config['selection_seed'],
                controls_per_cell=config.get('controls_per_cell', 1), maximum_events=maximum,
                maximum_candidates_per_source=config.get('maximum_candidates_per_source'))
    if draw['missed_candidates']:
        raise ValueError('Original frozen admission omitted credible candidates; review before handoff')
    output.mkdir(parents=True)
    if config.get('archive_census_ledgers_to'):
        from run_shift_sm_postprocessing import publish
        destination = config['archive_census_ledgers_to']
        if not destination.startswith(eos+'/'):
            raise ValueError('Census archive must remain inside this source output')
        with tempfile.TemporaryDirectory(prefix='sm-census-ledger-') as temporary:
            ledger_dir = Path(temporary)/'ledgers'
            materialize_fresh_authorities(draw, output/'response_authority', ledger_dir)
            archive = Path(temporary)/'full_census_ledgers.tar.gz'
            members = []
            with tarfile.open(archive, 'w:gz') as stream:
                for path in sorted(ledger_dir.glob('*/selection_ledger.json')):
                    name = str(path.relative_to(ledger_dir))
                    members.append(dict(name=name,bytes=path.stat().st_size,sha256=sha(path)))
                    stream.add(path,arcname=name,recursive=False)
            if archive.stat().st_size > 4*MIB:
                raise ValueError('Census archive exceeds its source storage allowance')
            publication = publish(archive,destination)
            save(output/'census_ledger_archive.json',dict(publication=publication,members=members))
    else:
        materialize_fresh_authorities(draw, output/'response_authority')
    save(output/'plan.json', draw['plan'])
    plan_pin = pin(output/'plan.json')
    save(output/'full_population_exposure.json', draw['exposure'])
    save(output/'source_pins.json', draw['source_pins'])
    source_id = result['source_id']
    selected = draw['plan']['selected']
    reservation = reserve_budget(config['admission_budget_path'], source_id,
        plan_pin['sha256'], len(selected))
    jobs, detectors, frozen_files = [], [], []
    if (output/'census_ledger_archive.json').exists():
        frozen_files.append(pin(output/'census_ledger_archive.json'))
    if config.get('frozen_response_draw'):
        frozen_files.extend(config['frozen_response_draw'].values())
        frozen_files.extend(dict(path=source['original_result_path'], sha256=source['original_result_sha256'])
                            for source in draw['source_pins']['sources'])
    dag = ['# Code style: AI-code. Complete original GEN gate before independent candidate/control jobs.',
        'CONFIG '+str(output/'dagman.config')]

    def add(node, request, priority=0):
        working = output/'jobs'/node.lower()
        request['cohort_id'] = config['campaign']+':'+source_id
        save(working/'request.json', request)
        text = submit_text(working, request, frozen, priority).replace(
            'SM_response_prospective_20261027_v1', batch+'_'+node)
        (working/'worker.sub').write_text(text)
        entry = dict(node=node, stage=request['stage'], job_directory=str(working),
            request_sha256=sha(working/'request.json'))
        jobs.append(entry)
        frozen_files.extend([pin(working/'request.json'), pin(working/'worker.sub')])
        dag.append('JOB '+node+' '+str(working/'worker.sub')+' DIR '+str(working))
        if priority:
            dag.append('PRIORITY '+node+' '+str(priority))
        return entry

    sources = draw['source_pins']['sources']
    if selected:
        if len(sources) != 1 or sources[0]['source_id'] != source_id:
            raise ValueError('Source-independent handoff received another cohort')
        source = sources[0]
        gate = add('PREFLIGHT', preflight_request(plan_pin, source, frozen), 100)
        base = reservation.get('detector_job_base', config['detector_job_base'])
        if type(base) is not int or base < 100000 or base+len(selected) >= 6600000:
            raise ValueError('Invalid reserved detector job/seed domain')
        for number, row in enumerate(selected):
            request = detector_request(plan_pin, source, row, base+number, eos)
            bound = config.get('maximum_publication_bytes', 4*MIB)
            if type(bound) is not int or bound < 4*MIB:
                raise ValueError('Detector publication bound must cover Nano and evidence')
            request['reservation_bytes'] = bound
            request['detector_source']['maximum_publication_bytes'] = bound
            entry = add('DET_'+str(number).zfill(4), request,
                50 if row['benchmark_region'] == 'credible_shallow_pointing' else 0)
            entry['selected'] = row
            detectors.append(entry)
            dag.append('PARENT '+gate['node']+' CHILD '+entry['node'])
    collector_bound = config.get('collector_publication_bytes', 2*MIB)
    request = envelope('sm_response_collect', plan_pin, collector_bound)
    request.update(argv=['python3', '@TOOLS@/scripts/run_shift_sm_response_collection.py', 'request.json'],
        benchmark_plan=plan_pin, full_population_exposure=pin(output/'full_population_exposure.json'),
        job_records=[{k:r[k] for k in ('job_directory','request_sha256','selected')} for r in detectors],
        maximum_publication_bytes=collector_bound, output_directory=eos+'/evidence')
    collector = add('COLLECT', request, 100)
    if detectors:
        dag.append('PARENT '+' '.join(r['node'] for r in detectors)+' CHILD COLLECT')
    (output/'response.dag').write_text('\n'.join(dag)+'\n')
    (output/'dagman.config').write_text('# Code style: AI-code. Pace starts; allow independent workers.\n'
        'DAGMAN_MAX_JOBS_SUBMITTED = 2000\nDAGMAN_MAX_JOBS_IDLE = 2000\n'
        'DAGMAN_MAX_SUBMITS_PER_INTERVAL = 20\nDAGMAN_USER_LOG_SCAN_INTERVAL = 1\n'
        'DAGMAN_SUBMIT_DELAY = 0\n')
    watchdog = output/'watchdog'
    watchdog.mkdir()
    helper_directory = Path(config['helpers_directory'])
    for name in ('monitor_shift_sm_response_benchmark.py','shift_condor_native.py'):
        shutil.copyfile(helper_directory/name, watchdog/name)
        frozen_files.append(pin(watchdog/name))
    watchdog_submit = '\n'.join(['# Code style: AI-code. Read-only source response watchdog.',
        'universe = scheduler', 'executable = /usr/bin/python3',
        'arguments = -u '+str(watchdog/'monitor_shift_sm_response_benchmark.py')+' '+str(watchdog/'config.json'),
        'initialdir = '+str(watchdog), 'getenv = False', 'environment = "PYTHONDONTWRITEBYTECODE=1"',
        '+SendCredential = True', '+MaxRuntime = 86400', '+JobBatchName = "'+batch+'_watchdog"',
        'output = worker.out', 'error = worker.err', 'log = events.log',
        'on_exit_hold = (ExitBySignal == True) || (ExitCode != 0)', 'periodic_release = False', 'queue 1', ''])
    (watchdog/'worker.sub').write_text(watchdog_submit)
    frozen_files.extend(pin(output/name) for name in
        ('plan.json','full_population_exposure.json','source_pins.json','response.dag','dagman.config'))
    frozen_files.extend(pin(path) for path in sorted((output/'response_authority').rglob('*.json')))
    frozen_files.append(pin(watchdog/'worker.sub'))
    manifest = dict(schema=SCHEMA, configuration_sha256=config_sha256,
        source_id=source_id, original_preparation_result=pin(directory/'result.json'),
        original_preparation_status=pin(directory/'status.json'),
        source_feature_publication=features['published_feature'],
        working_directory=str(output), benchmark_plan=plan_pin,
        detector_jobs=detectors, jobs=jobs, collector=collector,
        admission_reservation=reservation, frozen_files=frozen_files,
        full_original_saved_events=draw['exposure']['original_saved_GEN_events'],
        jobs_submitted=False, scientific_gain_qualified=False)
    save(output/'prepared.json', manifest)
    return manifest


def retire_returned_features(config, manifest):
    """Remove only our redundant successful feature return after freezing its draw."""
    if config.get('retire_preparation_feature_archive') is not True:
        return
    directory = Path(config['preparation_directory'])
    output = Path(manifest['working_directory'])
    receipt = output/'feature_return_retirement.json'
    archive = directory/'return_payload.tar.gz'
    if receipt.exists():
        previous = read(receipt)
        if previous['configuration_sha256'] != manifest['configuration_sha256']:
            raise ValueError('Feature retirement belongs to another handoff')
        if not archive.exists():
            return
    verify_prepared(manifest, manifest['configuration_sha256'])
    checked(manifest['original_preparation_result']['path'],
        manifest['original_preparation_result']['sha256'])
    status = read(checked(manifest['original_preparation_status']['path'],
        manifest['original_preparation_status']['sha256']))
    publication = manifest['source_feature_publication']
    if publication.get('independent_sha256_readback') is not True:
        raise ValueError('Feature retirement needs the retained EOS publication readback proof')
    archive = checked(archive, status['return_payload_sha256'])
    with tarfile.open(archive) as stream:
        members = stream.getmembers()
        if (len(members) != 1 or members[0].name != 'features.json.gz'
                or not members[0].isfile() or members[0].size != publication['bytes']):
            raise ValueError('Do not retire a return archive containing other evidence')
        with stream.extractfile(members[0]) as source:
            digest = hashlib.sha256()
            for block in iter(lambda: source.read(MIB), b''):
                digest.update(block)
        if digest.hexdigest() != publication['sha256']:
            raise ValueError('Returned feature bytes differ from the retained EOS publication')
    events = directory/'events.log'
    if events.stat().st_size > 8*MIB:
        raise ValueError('Preparation event log exceeds its bounded terminal review')
    terminal = re.findall(r'(?m)^005 \((\d+)\.000\.000\).*Job terminated\.\n'
        r'\s*\(1\) Normal termination \(return value 0\)', events.read_text())
    if not terminal:
        raise ValueError('Feature retirement needs a native successful preparation termination')
    record = dict(configuration_sha256=manifest['configuration_sha256'],
        archive=pin(archive), removed_bytes=archive.stat().st_size,
        retained_feature=publication, native_preparation_cluster_id=int(terminal[-1]),
        preparation_result=manifest['original_preparation_result'],
        preparation_status=manifest['original_preparation_status'],
        frozen_draw=manifest['benchmark_plan'], removed=False)
    save(receipt, record)
    archive.unlink()
    record['removed'] = not archive.exists()
    save(receipt, record)


def submit_once(submit_path, batch, working, label, config_sha256):
    """Submit once; preserve uncertainty instead of producing duplicate jobs."""
    receipt_path, intent_path = working/(label+'_submission.json'), working/(label+'_intent.json')
    binding = dict(submit=pin(submit_path), working_directory=str(working),
        job_batch_name=batch, configuration_sha256=config_sha256)
    if receipt_path.exists():
        receipt = read(receipt_path)
        if receipt['binding'] != binding:
            raise ValueError('Previous submission binding differs')
        return receipt
    if intent_path.exists():
        intent = read(intent_path)
        if intent['binding'] != binding:
            raise ValueError('Previous submission intent differs')
        raise ValueError('Uncertain previous submission requires native queue/history review; not resubmitted')
    ads = query('Iwd == '+json.dumps(str(working))+' && JobBatchName == '+json.dumps(batch),
        ['ClusterId','ProcId','Iwd','JobBatchName','GlobalJobId','JobStatus'], timeout=45)
    if ads:
        raise ValueError('A job already owns this handoff directory; no duplicate submission')
    save(intent_path, dict(binding=binding, intent_epoch=time.time()))
    stdout = run_condor(['/usr/bin/condor_submit','-terse',str(submit_path)], cwd=working, timeout=60)
    match = re.fullmatch(r'\s*(\d+)\.0\s*-\s*\1\.0\s*', stdout)
    if not match:
        raise ValueError('Unrecognized native submission response; preserved intent needs review')
    cluster = int(match.group(1))
    ads = query('ClusterId == '+str(cluster),
        ['ClusterId','ProcId','Iwd','JobBatchName','GlobalJobId','JobStatus'], timeout=45)
    if (len(ads) != 1 or ads[0].get('ProcId') != 0 or ads[0].get('Iwd') != str(working)
            or ads[0].get('JobBatchName') != batch):
        raise ValueError('Actual submitted identity, Iwd or batch name differs')
    receipt = dict(binding=binding, cluster_id=cluster, native_job=ads[0],
        submitted_epoch=time.time(), execution_verified=False)
    save(receipt_path, receipt)
    return receipt


def submit(config, manifest):
    verify_prepared(manifest, manifest['configuration_sha256'])
    validate_frozen(config)
    output = Path(manifest['working_directory'])
    if (output/'shared_wave_ownership.json').exists():
        raise ValueError('A shared wave owns this source; do not submit an individual DAG')
    manager = output/'response.dag.condor.sub'
    if not manager.exists():
        run_condor(['/usr/bin/condor_submit_dag','-no_submit','-maxjobs','2000','-maxidle','2000',
            '-autorescue','0','-notification','Never','-batch-name',config['job_batch_name'],
            '-append','+MaxRuntime = 86400','-append','initialdir = '+str(output),
            '-append','+SendCredential = True',
            '-append','on_exit_hold = (ExitBySignal == True) || (ExitCode != 0)',
            '-append','periodic_release = False',str(output/'response.dag')], cwd=output, timeout=60)
    launched = submit_once(manager, config['job_batch_name'], output, 'manager',
        manifest['configuration_sha256'])
    watchdog = output/'watchdog'
    watcher_config = dict(manager_id=launched['cluster_id'], manager_iwd=str(output),
        manager_global_job_id=launched['native_job']['GlobalJobId'],
        working_directory=str(watchdog), final_result_path=manifest['collector']['job_directory']+'/result.json',
        expected_plan_sha256=manifest['benchmark_plan']['sha256'],
        heartbeat_path=str(watchdog/'heartbeat.json'), failure_path=str(watchdog/'failure.json'),
        state_path=str(watchdog/'state.json'), owner=pwd.getpwuid(os.getuid()).pw_name,
        poll_seconds=30, maximum_wall_seconds=86400)
    if (watchdog/'config.json').exists() and read(watchdog/'config.json') != watcher_config:
        raise ValueError('Existing watchdog binding differs')
    save(watchdog/'config.json', watcher_config)
    watcher = submit_once(watchdog/'worker.sub', config['job_batch_name']+'_watchdog',
        watchdog, 'watchdog', manifest['configuration_sha256'])
    handoff = dict(schema=SCHEMA, source_id=manifest['source_id'], manager=launched,
        watchdog=watcher, detector_jobs=len(manifest['detector_jobs']),
        queued_Iwd_verified=True, execution_verified=False, no_automatic_retry=True)
    save(output/'submission.json', handoff)
    return handoff


def wave_sources(config):
    """Authenticate already prepared sources; never redraw or reprepare them."""
    pins = config['source_configurations']
    maximum = config.get('maximum_sources', 32)
    if type(maximum) is not int or not 1 <= maximum <= 128 or not 1 <= len(pins) <= maximum:
        raise ValueError('Require a bounded nonempty source wave')
    rows, seen = [], set()
    for item in pins:
        source_config = read(checked(item['path'], item['sha256']))
        if source_config.get('schema') != SCHEMA:
            raise ValueError('Wave input is not a source handoff configuration')
        validate_frozen(source_config)
        directory = persistent_directory(source_config['output_directory'])
        manifest_path = directory/'prepared.json'
        manifest = verify_prepared(read(manifest_path), item['sha256'])
        if manifest['working_directory'] != str(directory):
            raise ValueError('Source prepared manifest belongs to another directory')
        sid = manifest['source_id']
        if sid in seen:
            raise ValueError('Duplicate original source in shared wave')
        seen.add(sid)
        rows.append(dict(source_id=sid, configuration=dict(item),
            prepared_manifest=pin(manifest_path), working_directory=str(directory),
            manifest=manifest, config=source_config))
    return rows


def source_wave_claim(row, output, config_sha256, wave_id):
    return dict(schema=WAVE_SCHEMA, wave_id=wave_id, wave_directory=str(output),
        configuration_sha256=config_sha256, source_id=row['source_id'],
        source_prepared_manifest=row['prepared_manifest'])


def check_source_owner(row, claim, allow_same_wave=False):
    """Do not adopt a live, failed, complete or uncertain individual owner."""
    directory = Path(row['working_directory'])
    ownership = directory/'shared_wave_ownership.json'
    if ownership.exists() and read(ownership) != claim:
        raise ValueError('A different shared wave already owns source '+row['source_id'])
    submission = directory/'submission.json'
    if submission.exists():
        prior = read(submission)
        if (not allow_same_wave or prior.get('shared_wave') is not True
                or prior.get('shared_wave_directory') != claim['wave_directory']
                or prior.get('source_prepared_manifest') != row['prepared_manifest']):
            raise ValueError('Source already has a submitted owner: '+row['source_id'])
    for name in ('manager_intent.json', 'manager_submission.json'):
        if (directory/name).exists():
            raise ValueError('Individual source submission or uncertainty requires review: '+row['source_id'])


def prefixed_dag(path, prefix, expected_jobs):
    """Preserve every source dependency and job DIR; reject unsupported grammar."""
    names = {row['node'] for row in expected_jobs}
    mapping = {name: prefix+name for name in names}
    lines, jobs = [], set()
    for text in Path(path).read_text().splitlines():
        if not text.strip() or text.lstrip().startswith('#'):
            continue
        words = shlex.split(text)
        command = words[0]
        if command == 'CONFIG' and len(words) == 2:
            continue
        if command == 'JOB' and len(words) == 5 and words[3] == 'DIR':
            name = words[1]
            if name not in names or name in jobs:
                raise ValueError('Unexpected or duplicate source DAG job')
            original = next(row for row in expected_jobs if row['node'] == name)
            if (words[2] != str(Path(original['job_directory'])/'worker.sub')
                    or words[4] != original['job_directory']):
                raise ValueError('Source DAG changed an original job submit file or DIR')
            jobs.add(name)
            words[1] = mapping[name]
        elif command == 'PRIORITY' and len(words) == 3:
            words[1] = mapping[words[1]]
            int(words[2])
        elif command == 'PARENT' and words.count('CHILD') == 1:
            boundary = words.index('CHILD')
            if boundary < 2 or boundary == len(words)-1:
                raise ValueError('Empty source DAG dependency')
            words = ['PARENT']+[mapping[name] for name in words[1:boundary]]+['CHILD']+[
                mapping[name] for name in words[boundary+1:]]
        else:
            raise ValueError('Unsupported source DAG command: '+command)
        lines.append(' '.join(words))
    if jobs != names:
        raise ValueError('Shared wave would omit a source job')
    return lines, mapping


def manager_runtime_seconds(config):
    """Allow a weekend owner without copying that lifetime to physics workers."""
    seconds = config.get('manager_max_runtime_seconds', 86400)
    if type(seconds) is not int or not 3600 <= seconds <= 7*86400:
        raise ValueError('Use an integer manager lifetime between one hour and seven days')
    return seconds


def prepare_wave(config, config_sha256):
    """Group frozen source DAGs under one manager, preserving their ownership."""
    if config.get('schema') != WAVE_SCHEMA:
        raise ValueError('Unsupported candidate wave configuration')
    manager_runtime_seconds(config)
    output = persistent_directory(config['output_directory'])
    wave_id, batch = config['wave_id'], config['job_batch_name']
    if not all(re.fullmatch(r'[A-Za-z0-9_.-]+', value) for value in (wave_id, batch)):
        raise ValueError('Use meaningful plain wave and batch names')
    rows = wave_sources(config)
    previous = output/'prepared.json'
    if previous.exists():
        manifest = verify_prepared(read(previous), config_sha256)
        for row in rows:
            check_source_owner(row, source_wave_claim(row, output, config_sha256, wave_id), True)
        return manifest
    if output.exists() and any(output.iterdir()):
        raise ValueError('Partial wave preparation requires review; never repeat ownership mutations')
    with ExitStack() as locks:
        for row in sorted(rows, key=lambda value: value['working_directory']):
            lock = locks.enter_context(Path(row['working_directory']).with_suffix('.handoff.lock').open('a'))
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            check_source_owner(row, source_wave_claim(row, output, config_sha256, wave_id))
        output.mkdir(parents=True)
        intent = dict(schema=WAVE_SCHEMA, configuration_sha256=config_sha256,
            wave_id=wave_id, sources=[dict(source_id=row['source_id'],
                prepared_manifest=row['prepared_manifest']) for row in rows])
        save(output/'preparation_intent.json', intent)
        lines = ['# Code style: AI-code. Shared manager; frozen per-source jobs and gates unchanged.',
            'CONFIG '+str(output/'dagman.config')]
        sources, frozen = [], []
        for index, row in enumerate(rows):
            claim = source_wave_claim(row, output, config_sha256, wave_id)
            owner_path = Path(row['working_directory'])/'shared_wave_ownership.json'
            save(owner_path, claim)
            source_lines, mapping = prefixed_dag(Path(row['working_directory'])/'response.dag',
                'S'+str(index).zfill(3)+'_', row['manifest']['jobs'])
            lines.extend(source_lines)
            sources.append(dict(source_id=row['source_id'], configuration=row['configuration'],
                prepared_manifest=row['prepared_manifest'], working_directory=row['working_directory'],
                ownership=pin(owner_path), source_job_nodes=mapping,
                collector=row['manifest']['collector'], detector_jobs=len(row['manifest']['detector_jobs'])))
            frozen.extend([row['configuration'], row['prepared_manifest'], pin(owner_path)])
            frozen.extend(row['manifest']['frozen_files'])
        (output/'response.dag').write_text('\n'.join(lines)+'\n')
        (output/'dagman.config').write_text('# Code style: AI-code. Independent source gates; pace starts.\n'
            'DAGMAN_MAX_JOBS_SUBMITTED = 2000\nDAGMAN_MAX_JOBS_IDLE = 2000\n'
            'DAGMAN_MAX_SUBMITS_PER_INTERVAL = 20\nDAGMAN_USER_LOG_SCAN_INTERVAL = 1\n'
            'DAGMAN_SUBMIT_DELAY = 0\n')
        frozen.extend(pin(output/name) for name in ('response.dag','dagman.config','preparation_intent.json'))
        manifest = dict(schema=WAVE_SCHEMA, configuration_sha256=config_sha256,
            wave_id=wave_id, working_directory=str(output), sources=sources,
            detector_jobs=sum(row['detector_jobs'] for row in sources),
            wave_manager_output=str(output/'response.dag.dagman.out'), frozen_files=frozen,
            per_source_jobs_and_probabilities_unchanged=True, new_GEN_exposure=0,
            per_source_watchdogs=False, jobs_submitted=False)
        save(previous, manifest)
        return manifest


def submit_wave(config, manifest):
    """Submit one manager; recover only source receipts from its known proof."""
    verify_prepared(manifest, manifest['configuration_sha256'])
    maximum_runtime = manager_runtime_seconds(config)
    rows = wave_sources(config)
    output = Path(manifest['working_directory'])
    for row in rows:
        check_source_owner(row, source_wave_claim(row, output, manifest['configuration_sha256'],
            config['wave_id']), True)
    launched_path = output/'manager_submission.json'
    if not launched_path.exists():
        # A source job can exist even if another submitter lost its receipt.
        # This fresh account query must succeed before a new wave submission.
        ads = query('Owner == '+json.dumps(pwd.getpwuid(os.getuid()).pw_name),
            ['ClusterId','ProcId','Iwd','DAGManJobId','JobStatus'], timeout=45)
        directories = [row['working_directory'] for row in rows]
        if any(isinstance(ad.get('Iwd'), str) and any(ad['Iwd'] == directory
                or ad['Iwd'].startswith(directory+'/') for directory in directories) for ad in ads):
            raise ValueError('A native job already occupies a shared source; review its owner')
    manager = output/'response.dag.condor.sub'
    if not manager.exists():
        run_condor(['/usr/bin/condor_submit_dag','-no_submit','-maxjobs','2000','-maxidle','2000',
            '-autorescue','0','-notification','Never','-batch-name',config['job_batch_name'],
            '-append','+MaxRuntime = '+str(maximum_runtime),'-append','initialdir = '+str(output),
            '-append','+SendCredential = True',
            '-append','on_exit_hold = (ExitBySignal == True) || (ExitCode != 0)',
            '-append','periodic_release = False',str(output/'response.dag')], cwd=output, timeout=60)
    launched = submit_once(manager, config['job_batch_name'], output, 'manager',
        manifest['configuration_sha256'])
    # Pin the wave before changing any per-source submission receipt. A resumed
    # call may repair only these records using the same durable native manager.
    wave_pin = pin(output/'prepared.json')
    handoff = dict(schema=WAVE_SCHEMA, shared_wave=True, wave_id=config['wave_id'],
        manager=launched, sources=[row['source_id'] for row in manifest['sources']],
        detector_jobs=manifest['detector_jobs'], per_source_watchdogs=False,
        wave_prepared_manifest=wave_pin, wave_manager_output=manifest['wave_manager_output'],
        queued_Iwd_verified=True, execution_verified=False, no_automatic_retry=True)
    save(output/'submission.json', handoff)
    for row in manifest['sources']:
        record = dict(schema='shift-sm-candidate-wave-source-submission-v1', shared_wave=True,
            source_id=row['source_id'], wave_id=config['wave_id'], shared_wave_directory=str(output),
            manager=launched, wave_prepared_manifest=wave_pin,
            source_prepared_manifest=row['prepared_manifest'], source_job_nodes=row['source_job_nodes'],
            collector=row['collector'], detector_jobs=row['detector_jobs'],
            wave_manager_output=manifest['wave_manager_output'],
            queued_Iwd_verified=True, execution_verified=False, no_automatic_retry=True)
        destination = Path(row['working_directory'])/'submission.json'
        if destination.exists() and read(destination) != record:
            raise ValueError('Source submission belongs to another manager; never overwrite it')
        save(destination, record)
    return handoff


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('configuration', type=Path)
    parser.add_argument('--config-sha256', required=True)
    parser.add_argument('--submit', action='store_true')
    args = parser.parse_args()
    config = read(checked(args.configuration, args.config_sha256))
    output = persistent_directory(config['output_directory'])
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.with_suffix(output.suffix+'.handoff.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if config.get('schema') == WAVE_SCHEMA:
            manifest = prepare_wave(config, args.config_sha256)
            answer = submit_wave(config, manifest) if args.submit else dict(
                working_directory=manifest['working_directory'], detector_jobs=manifest['detector_jobs'],
                sources=len(manifest['sources']), jobs_submitted=False)
        else:
            manifest = prepare(config, args.config_sha256)
            retire_returned_features(config, manifest)
            answer = submit(config, manifest) if args.submit else dict(
                working_directory=manifest['working_directory'], detector_jobs=len(manifest['detector_jobs']),
                jobs_submitted=False)
    print(json.dumps(answer, allow_nan=False))


if __name__ == '__main__':
    main()
