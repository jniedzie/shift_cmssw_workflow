#!/usr/bin/env python3
# Code style: AI-code. AI-generated bounded, qualified recovery of held DP workers.
"""Retry only the identified native-precision failure, once, in its original DAG.

No GEN or material draw is repeated. Original failure evidence stays immutable;
the replacement worker reports its actual qualified runtime in every receipt.
"""
import argparse
import datetime
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tarfile
import time

from shift_condor_native import query, run_condor, parse_ads
from dark_photon_runtime_recovery import pinned as original_pinned, prepare_view, read as original_read, sha as original_sha

SCHEMA = 'shift-dp-runtime-recovery-controller-v1'
SIGNATURE = 'Preassigned source is off shell beyond its event-scale numerical precision'
ATTRIBUTES = ['ClusterId', 'ProcId', 'DAGManJobId', 'JobStatus', 'Iwd', 'Args', 'Cmd',
              'Out', 'Err', 'ExitCode', 'ExitBySignal', 'NumJobStarts', 'PeriodicRelease',
              'TransferOutput', 'TransferOutputRemaps', 'TransferExecutable', 'JobStartDate',
              'JobCurrentStartDate', 'NumShadowStarts', 'CompletionDate', 'ShiftNtupleProgressEpoch']


def shared_io(operation, *arguments):
    """Wait out only transient shared-storage errors without repeating native work."""
    delay=5
    while True:
        for attempt in range(4):
            try:
                return operation(*arguments)
            except OSError as error:
                if error.errno not in (errno.ETIMEDOUT,errno.ESTALE,errno.EIO):
                    raise
                if attempt<3:
                    time.sleep(attempt+1)
                else:
                    print('DP recovery is waiting for shared storage: '+repr(error),file=sys.stderr,flush=True)
        time.sleep(delay)
        delay=min(60,delay*2)


def read(path):return shared_io(original_read,path)
def sha(path):return shared_io(original_sha,path)
def pinned(authority):return shared_io(original_pinned,authority)


def save(path, value):
    from run_dark_photon_statistics import persist_controller_state
    persist_controller_state(Path(path),value)


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def native(config, constraint):
    options = {} if Path('/var/lib/condor/spool/.schedd_address').is_file() else dict(
        schedd=config['condor_schedd'], pool=config['condor_pool'])
    return query('Owner == ' + json.dumps(config['owner']) + ' && (' + constraint + ')',
                 ATTRIBUTES, **options, timeout=45)


def scheduler_arguments(config):
    if Path('/var/lib/condor/spool/.schedd_address').is_file():
        return []
    return ['-pool', config['condor_pool'], '-name', config['condor_schedd']]


def command(config, executable, arguments):
    return run_condor(['/usr/bin/' + executable, *scheduler_arguments(config), *arguments], timeout=45)


def exact_job(config, cluster):
    rows = native(config, 'ClusterId == ' + str(cluster))
    if len(rows) != 1 or rows[0].get('ProcId') != 0:
        raise RuntimeError('Retry requires its exact single native job: ' + str(cluster))
    return rows[0]


def validate_roots(config):
    roots = [Path(name) for name in config['production_directories']]
    if not roots or any(not p.is_absolute() or '..' in p.parts for p in roots):
        raise ValueError('Recovery requires explicit absolute production scopes')
    return roots


def reviewed_timeout(config,row,production,point,index,expected,parent,chunk,archive,report,text):
    """Accept one explicitly pinned timeout review; never classify timeouts generally."""
    authority=config.get('reviewed_timeout_recoveries',{}).get(str(row['ClusterId']))
    if authority is None:
        raise ValueError('Failure is not the specifically qualified SIM precision exception')
    proof=pinned(authority)
    if (proof.get('schema')!='shift-reviewed-same-seed-SIM-timeout-recovery-v1'
            or proof.get('complete') is not True or proof.get('single_reviewed_retry') is not True
            or proof.get('original_cluster')!=row['ClusterId'] or proof.get('production')!=str(production)
            or proof.get('point')!=point or proof.get('chunk')!=index
            or proof.get('plan_sha256')!=sha(production/'plan.json')
            or proof.get('seal_sha256')!=sha(production/'seal.json')
            or proof.get('gen_sha256')!=parent['gen_sha256'] or proof.get('expected_event_ids')!=expected
            or proof.get('failure_archive')!=dict(path=str(archive),sha256=sha(archive))
            or proof.get('failure_report_sha256')!=hashlib.sha256(json.dumps(report,sort_keys=True).encode()).hexdigest()
            or proof.get('failure_step1_log_sha256')!=hashlib.sha256(text.encode()).hexdigest()
            or proof.get('retry_stage_timeouts_seconds')!=[43200,7200,14400,14400]
            or proof.get('original_event_progress_guard_seconds')!=7200
            or proof.get('timeout_cause_identified') is not False):
        raise ValueError('SIM timeout lacks its exact reviewed failure/unchanged budget contract')
    for key in ('ClusterId','ProcId','DAGManJobId','Iwd','Args','Cmd','Out','Err',
                'NumJobStarts','JobCurrentStartDate','NumShadowStarts'):
        if row.get(key)!=proof['original_native'].get(key):
            raise ValueError('Reviewed timeout native identity changed: '+key)
    if (report.get('error')!="TimeoutError('SIM made no event progress for 7200 seconds; records started=1')"
            or report.get('current_tier')!='SIM' or report.get('stage_records_started')!=1
            or report.get('validated_tier_events',{}).get('SIM')!=0 or SIGNATURE in text):
        raise ValueError('Different timeout or native failure; no automatic retry')
    comparison=pinned(proof['same_seed_comparison'])
    if (comparison.get('schema')!='shift-same-seed-timeout-control-comparison-v1'
            or comparison.get('complete') is not True or comparison.get('original_cluster')!=row['ClusterId']
            or comparison.get('job')!=chunk['job'] or comparison.get('seed')!=230000000+100*chunk['job']
            or comparison.get('original_event_id')!=expected[0]
            or comparison.get('original_full_GEN_sha256')!=parent['gen_sha256']
            or comparison.get('mismatches')!=[] or comparison.get('exact_original_GEN_selection_and_seed') is not True
            or comparison.get('all_tiers_exact_identity') is not True or comparison.get('detector_electronics_unchanged') is not True
            or any(not 0.<comparison.get(key,0.)<7200. for key in ('original_SIM_seconds','v3_SIM_seconds'))):
        raise ValueError('SIM timeout requires exact successful same-seed valid-Nano controls')
    native=pinned(proof['native_test_completion'])
    cases=comparison['receipts']
    if native.get('complete') is not True or len(native.get('jobs',[]))!=2 or len(cases)!=2:
        raise ValueError('Timeout controls lack both native terminal proofs')
    for case,job in zip(cases,native['jobs']):
        result=pinned(case)
        if (result.get('complete') is not True or job.get('Iwd')!=str(Path(case['path']).parent)
                or job.get('JobStatus')!=4 or job.get('ExitCode')!=0 or job.get('ExitBySignal') is not False
                or job.get('ProcId')!=0 or type(job.get('ClusterId')) is not int):
            raise ValueError('A timeout qualification job is incomplete or changed')
    return authority


def inspect_failure(config, row):
    """Identify the precise old attempt; classify archived logs without extraction."""
    production = Path(row['Iwd'])
    roots = validate_roots(config)
    if (not any(production.is_relative_to(root) for root in roots)
            or production.name != 'production' or not production.is_absolute()
            or row.get('ProcId') != 0 or row.get('JobStatus') != 5
            or row.get('ExitCode') != 1 or row.get('ExitBySignal') is not False
            or row.get('PeriodicRelease') is not False
            or type(row.get('NumJobStarts')) is not int or row['NumJobStarts'] < 1
            or type(row.get('DAGManJobId')) is not int):
        raise ValueError('Job is outside the reviewed held-detector contract')
    args = shlex.split(row['Args'])
    if len(args) != 3 or not re.fullmatch(r'm[0-9p]+_(?:prompt|lab_[0-9p]+m)', args[0]):
        raise ValueError('Held worker has a different point/chunk/seal invocation')
    point, index_text, seal_digest = args
    index = int(index_text)
    if index < 0 or sha(production/'seal.json') != seal_digest:
        raise ValueError('Held worker differs from its original scientific seal')
    plan = read(production/'plan.json')
    seal = read(production/'seal.json')
    for filename in ('plan.json', 'gen_inventory.json', 'chunk_worker.py'):
        path = production/filename
        if seal['files'].get(str(path)) != sha(path):
            raise ValueError('Original recovery input changed: ' + str(path))
    model, = [r for r in plan['points'] if r['point'] == point]
    chunk, = [c for c in model['chunks'] if c['chunk'] == index]
    parent = read(production/'gen_inventory.json')['points'][point]
    ids = parent['event_ids']
    selection = model.get('material_selection')
    if selection is not None:
        ids = [ids[i] for i in selection['selected_indices']]
    expected = ids[chunk['skip']:chunk['skip'] + chunk['count']]
    status_path = production/'statuses'/f'{point}_{index:03d}.json'
    status = read(status_path)
    if (status.get('complete') is not False or status.get('published') != {}
            or status.get('point') != point or status.get('chunk') != index
            or status.get('seal_sha256') != seal_digest
            or status.get('plan_sha256') != sha(production/'plan.json')
            or status.get('parent_gen_sha256') != parent['gen_sha256']
            or status.get('expected_event_ids') != expected or len(expected) != chunk['count']):
        raise ValueError('Failed receipt is incomplete, partial-published, or a different population')
    if row.get('TransferOutput') != 'failure.tar.gz':
        raise ValueError('Held worker has an unexpected transfer inventory')
    remap = row.get('TransferOutputRemaps', '')
    if not remap.startswith('failure.tar.gz=') or ';' in remap:
        raise ValueError('Held worker has an ambiguous failure return path')
    archive = Path(remap.split('=', 1)[1])
    if not archive.is_relative_to(production/'failures') or archive.stat().st_size > 5*1024**2:
        raise ValueError('Failure evidence is outside its bounded original directory')
    with tarfile.open(archive) as source:
        report_name = f'{point}_{index:03d}/detector/report.json'
        log_name = f'{point}_{index:03d}/detector/step1.log'
        def bounded(name):
            item = source.getmember(name)
            if not item.isfile() or item.size > 4*1024**2:
                raise ValueError('Missing or excessive exact failure evidence')
            return source.extractfile(item).read()
        report = json.loads(bounded(report_name))
        text = bounded(log_name).decode(errors='replace')
    precision=(SIGNATURE in text and report.get('current_tier')=='SIM' and
        re.search(r"CalledProcessError\(66, \['cmsRun', .*step1_cfg[.]py'\]\)",report.get('error','')))
    timeout_authority=None
    if not precision:
        timeout_authority=reviewed_timeout(config,row,production,point,index,expected,parent,chunk,archive,report,text)
    runtime = plan['detector_runtime']['runtime_sha256']
    authority_pin = config['runtime_authorities'].get(runtime)
    if authority_pin is None:
        raise ValueError('Old detector runtime has no qualified replacement')
    # A template is an immutable old/new profile mapping. Each production has
    # its own frozen manifest path, so bind that exact old profile here.
    authority = pinned(authority_pin)
    if pinned(authority['old_profile'])['runtime_sha256'] != runtime:
        raise ValueError('Replacement template changes its original runtime identity')
    for name in ('Out', 'Err', 'Cmd'):
        path = Path(row[name])
        if not path.is_absolute() or not path.is_file():
            raise ValueError('Missing persistent original worker evidence: ' + name)
    return dict(production=str(production), point=point, chunk=index,
        status_path=str(status_path), status_sha256=sha(status_path),
        expected_event_ids=expected, plan_sha256=sha(production/'plan.json'),
        seal_sha256=seal_digest, gen_sha256=parent['gen_sha256'], authority=authority_pin,
        runtime_sha256=runtime, original_native=row,
        failure_kind='reviewed_SIM_progress_timeout' if timeout_authority else 'native_precision',
        timeout_authority=timeout_authority,
        evidence_sha256={str(path):sha(path) for path in
            (status_path, archive, Path(row['Out']), Path(row['Err']), Path(row['Cmd']))},
        destinations=[chunk['nano'], chunk['metadata']+'/metadata.tar.gz', chunk['metadata']+'/complete.json'])


def verify_output_absence(paths):
    for path in paths:
        if not isinstance(path, str) or not path.startswith('/eos/user/'):
            raise ValueError('Recovery output must stay in the original EOS namespace')
        result = subprocess.run(['/usr/bin/xrdfs', 'root://eosuser.cern.ch', 'stat', path],
            capture_output=True, text=True, timeout=60)
        if result.returncode == 0:
            raise RuntimeError('Canonical output already exists; preserve it: ' + path)
        if '[3011]' not in result.stderr and 'No such file' not in result.stderr:
            raise RuntimeError('EOS output state is unknown: ' + result.stderr[:1000])


def production_preflight(config, state, review):
    """Run one real full-census conversion per immutable cohort, before retries."""
    from prepare_dark_photon_kinematics_preflight import prepare
    from run_dark_photon_statistics import submission
    root = Path(config['directory'])
    authority = pinned(review['authority'])
    profile_pin = authority['new_profile']
    # A physical point is an independent GEN cohort. Another mass in the same
    # scheduling wave must not block this point's complete, checked census.
    scope = [review['point']]
    identity = hashlib.sha256((review['production'] + profile_pin['sha256'] +
                               ':' + ','.join(scope)).encode()).hexdigest()
    scans = state.setdefault('preflights', {})
    directory = root/'preflights'/identity
    if identity not in scans:
        scans[identity] = dict(production=review['production'], profile=profile_pin, point_scope=scope,
                               directory=str(directory), phase='preparation_intent')
        save(root/'state.json', state)
        # An interrupted preparation is preserved and requires review. It is
        # never mistaken for a submitted scan or a successful qualification.
        scans[identity]['preparation'] = prepare(review['production'], profile_pin, directory,
                                                point_scope=scope)
        scans[identity]['phase'] = 'prepared'
        save(root/'state.json', state)
    scan = scans[identity]
    if (scan['production'] != review['production'] or scan['profile'] != profile_pin
            or scan.get('point_scope') != scope):
        raise ValueError('Preflight cache changed its physical point or qualified runtime')
    directory = Path(scan['directory'])
    if not directory.is_relative_to(root/'preflights'):
        migration=pinned(config.get('state_migration',{}))
        previous_config=pinned(migration['original_configuration'])
        previous=pinned(migration['original_state_snapshot']).get('preflights',{}).get(identity)
        if (migration.get('schema')!='shift-reviewed-runtime-supervisor-migration-v1'
                or migration.get('replacement_directory')!=str(root) or previous is None
                or not directory.is_relative_to(Path(previous_config['directory'])/'preflights')
                or previous.get('phase')!='complete' or scan.get('phase')!='complete'
                or any(scan.get(key)!=previous.get(key) for key in
                    ('production','profile','point_scope','directory','submission','receipt','native_terminal','phase'))):
            raise ValueError('Preflight cache moved outside its pinned reviewed migration')
    if scan['phase'] == 'preparation_intent':
        raise RuntimeError('Interrupted preflight preparation needs review: ' + str(directory))
    if scan['phase'] == 'complete':
        if native(config, 'ClusterId == ' + str(scan['submission']['cluster'])):
            raise RuntimeError('A completed preflight unexpectedly became live again')
        pinned(scan['receipt'])
        return scan['receipt']
    prepared = scan['preparation']
    for key in ('config', 'worker', 'submit'):
        path = Path(prepared[key]['path'])
        if sha(path) != prepared[key]['sha256']:
            raise ValueError('Prepared converter preflight changed: ' + str(path))
    if 'submission' not in scan:
        scan['submission'] = submission(config, directory, prepared['submit']['path'])
        scan['phase'] = 'submitted'
        save(root/'state.json', state)
    cluster = scan['submission']['cluster']
    rows = native(config, 'ClusterId == ' + str(cluster))
    if rows:
        if len(rows) != 1 or rows[0].get('Iwd') != str(directory) or rows[0].get('ProcId') != 0:
            raise RuntimeError('Preflight native identity differs from its persistent submission')
        if rows[0]['JobStatus'] == 5:
            raise RuntimeError('Full-GEN converter preflight failed and remains held: ' + str(cluster))
        if rows[0]['JobStatus'] not in (1, 2, 6, 7):
            raise RuntimeError('Unexpected preflight native queue state')
        scan['queue_status'] = rows[0]['JobStatus']
        scan['payload_started'] = rows[0].get('NumJobStarts', 0) > 0
        return None
    history = parse_ads(command(config, 'condor_history', [str(cluster)+'.0', '-limit', '1', '-json',
                                        '-attributes', ','.join(ATTRIBUTES)]))
    if (len(history) != 1 or history[0].get('ClusterId') != cluster
            or history[0].get('ProcId') != 0 or history[0].get('Iwd') != str(directory)
            or history[0].get('JobStatus') != 4 or history[0].get('ExitCode') != 0
            or history[0].get('ExitBySignal') is not False):
        raise RuntimeError('Preflight lacks exact successful terminal native evidence')
    receipt_path = Path(prepared['receipt'])
    receipt = read(receipt_path)
    if (receipt.get('complete') is not True
            or receipt.get('original_plan_sha256') != review['plan_sha256']
            or receipt.get('original_seal_sha256') != review['seal_sha256']
            or receipt.get('replacement_runtime_sha256') != pinned(profile_pin)['runtime_sha256']):
        raise ValueError('Converter preflight does not cover this exact original cohort/runtime')
    scan.update(phase='complete', native_terminal=history[0], completed_utc=now(),
                receipt=dict(path=str(receipt_path), sha256=sha(receipt_path)))
    save(root/'state.json', state)
    return scan['receipt']


def prepare_attempt(config, review, directory, preflight_pin):
    production = Path(review['production'])
    directory.mkdir(parents=True, exist_ok=False)
    authority = dict(pinned(review['authority']))
    old_profile = production/'frozen/detector_runtime.json'
    authority['old_profile'] = dict(path=str(old_profile), sha256=sha(old_profile))
    authority['production_preflight'] = preflight_pin
    authority_path = directory/'runtime_authority.json'
    save(authority_path, authority)
    pin = dict(path=str(authority_path), sha256=sha(authority_path))
    original = review['original_native']
    template = dict(path=original['Cmd'], sha256=review['evidence_sha256'][original['Cmd']])
    view = prepare_view(production, pin, directory/'worker',
                        allowed_slices=[dict(point=review['point'], chunk=review['chunk'])],
                        wrapper_template=template)
    (directory/'logs').mkdir()
    edits = dict(Cmd=view['wrapper'], TransferExecutable=False,
        Out=str(directory/'logs/retry.out'), Err=str(directory/'logs/retry.err'),
        TransferOutputRemaps='failure.tar.gz='+str(directory/'retry_failure.tar.gz'))
    return dict(review=review, worker=view, authority=pin, edits=edits,
                phase='prepared', prepared_utc=now())


def same_original_job(row, attempt):
    original = attempt['review']['original_native']
    for key in ('ClusterId', 'ProcId', 'DAGManJobId', 'Iwd', 'Args'):
        if row.get(key) != original.get(key):
            raise RuntimeError('Retry changed exact native/source identity: ' + key)


def recover(config, state, cluster):
    root = Path(config['directory'])
    attempt = state['attempts'][str(cluster)]
    directory = root/'attempts'/str(cluster)
    row = exact_job(config, cluster)
    same_original_job(row, attempt)
    if row['JobStatus'] != 5:
        if attempt['phase'] == 'release_intent' and row['JobStatus'] in (1, 2, 6, 7):
            if any(row.get(k) != v for k,v in attempt['edits'].items()):
                raise RuntimeError('Unreconciled release changed its prepared worker')
            attempt.update(phase='released', released_utc=now(), recovered_release_intent=True)
            save(root/'state.json', state)
            return
        raise RuntimeError('Job left its held state before the reviewed release')
    if attempt['phase']=='release_intent':
        raise RuntimeError('Held release intent is ambiguous; never issue a second release')
    original=attempt['review']['original_native']
    if (row.get('NumJobStarts') != original['NumJobStarts']
            or any(key in original and row.get(key)!=original[key]
                   for key in ('JobCurrentStartDate','NumShadowStarts'))):
        raise RuntimeError('A retry has already run; never release it again')
    parent = exact_job(config, row['DAGManJobId'])
    if parent['JobStatus'] != 2:
        raise RuntimeError('Original DAG is not running; do not create a competing owner')
    for name, digest in attempt['review']['evidence_sha256'].items():
        if name == attempt['review']['status_path'] and attempt['phase'] != 'prepared':
            continue
        if sha(name) != digest:
            raise ValueError('Original failure evidence changed: ' + name)
    verify_output_absence(attempt['review']['destinations'])
    status = Path(attempt['review']['status_path'])
    archived = directory/'original_status.json'
    edits = []
    for name, value in attempt['edits'].items():
        edits.extend([name, json.dumps(value)])
    if attempt['phase'] == 'prepared':
        attempt['edit_dry_run'] = command(config, 'condor_qedit', ['-dry-run', str(cluster)+'.0', *edits])
        if status.exists():
            if archived.exists():
                raise RuntimeError('Ambiguous canonical and archived failed statuses')
            status.replace(archived)
        if not archived.is_file() or sha(archived) != attempt['review']['status_sha256']:
            raise RuntimeError('Original failure status was not preserved exactly')
        attempt.update(phase='status_archived', archived_status=str(archived))
        save(root/'state.json', state)
    if status.exists():
        raise RuntimeError('Another worker wrote this slice; do not overwrite or release')
    # One scheduler transaction changes all fields. No automatic release is enabled.
    attempt['edit_output'] = command(config, 'condor_qedit', [str(cluster)+'.0', *edits])
    checked = exact_job(config, cluster)
    same_original_job(checked, attempt)
    if (checked['JobStatus'] != 5 or checked.get('PeriodicRelease') is not False
            or checked.get('NumJobStarts') != attempt['review']['original_native']['NumJobStarts']
            or any(checked.get(k) != v for k,v in attempt['edits'].items())):
        raise RuntimeError('Native retry edit did not read back exactly')
    attempt.update(phase='release_intent', release_requested_utc=now())
    save(root/'state.json', state)
    attempt['release_output'] = command(config, 'condor_release', [str(cluster)+'.0'])
    attempt.update(phase='released', released_utc=now())
    save(root/'state.json', state)


def retry_execution_seen(attempt, native_record):
    """CERN may retain NumJobStarts when a held job is released.

    The unique edited entry and receipt are checked by the caller. For an
    unchanged counter, require a fresh execution timestamp, not the old start.
    One controller release is enforced by the persisted phase, not this counter.
    """
    before = attempt['review']['original_native']['NumJobStarts']
    after = native_record.get('NumJobStarts')
    if type(after) is not int or after not in (before, before + 1):
        return False
    if after == before + 1:
        return True
    prepared = attempt.get('release_requested_utc', attempt.get('prepared_utc'))
    if not prepared:
        return False
    epoch = datetime.datetime.fromisoformat(prepared).timestamp()
    return any(type(native_record.get(key)) in (int, float) and native_record[key] > epoch
               for key in ('JobCurrentStartDate', 'ShiftNtupleProgressEpoch'))


def completed_attempt(attempt, native_record):
    review = attempt['review']
    status = read(review['status_path'])
    profile = pinned(pinned(attempt['authority'])['new_profile'])
    if (status.get('complete') is not True
            or status.get('expected_event_ids') != review['expected_event_ids']
            or status.get('plan_sha256') != review['plan_sha256']
            or status.get('seal_sha256') != review['seal_sha256']
            or status.get('parent_gen_sha256') != review['gen_sha256']
            or status.get('private_detector_runtime_sha256') != profile['runtime_sha256']
            or status.get('original_private_detector_runtime_sha256') != review['runtime_sha256']
            or status.get('runtime_override', {}).get('authority') != attempt['authority']
            or status.get('events') != len(review['expected_event_ids'])
            or native_record.get('JobStatus') != 4 or native_record.get('ExitCode') != 0
            or native_record.get('ExitBySignal') is not False
            or not retry_execution_seen(attempt, native_record)
            or any(native_record.get(k) != v for k,v in attempt['edits'].items())):
        raise ValueError('Completed retry lacks exact identities, actual runtime or successful native exit')
    same_original_job(native_record, attempt)
    return status


def reconcile_released(config, state, held_ids):
    pending = {key:value for key,value in state['attempts'].items()
               if value['phase'] in ('released', 'release_intent') and int(key) not in held_ids}
    if not pending:
        return
    rows = native(config, 'member(ClusterId,{'+','.join(pending)+'})')
    present = {str(row['ClusterId']):row for row in rows}
    for key, attempt in pending.items():
        try:
            row = present.get(key)
            if row is not None and row['JobStatus'] in (1, 2, 6, 7):
                same_original_job(row, attempt)
                if any(row.get(k) != v for k,v in attempt['edits'].items()):
                    raise RuntimeError('Released job differs from its exact qualified native edit')
                attempt.update(phase='released', queue_status=row['JobStatus'],
                    actual_payload_started=retry_execution_seen(attempt, row))
                continue
            if row is None:
                text = command(config, 'condor_history', [key+'.0','-limit','1','-json',
                    '-attributes',','.join(ATTRIBUTES)])
                history = parse_ads(text)
                if len(history) != 1:
                    raise RuntimeError('Released retry is absent but lacks exact terminal native evidence')
                row = history[0]
            status = completed_attempt(attempt, row)
            attempt.update(phase='complete', completed_utc=now(), native_terminal=row,
                complete_status_sha256=sha(attempt['review']['status_path']),
                nano_sha256=status['nano_sha256'])
            previous = state.setdefault('failures', {}).pop(key, None)
            if previous:
                attempt.setdefault('resolved_operational_errors', []).append(previous)
        except Exception as error:
            state.setdefault('failures', {})[key] = repr(error)


def poll(config, state):
    root = Path(config['directory'])
    roots = validate_roots(config)
    parts = ['substr(Iwd,0,'+str(len(str(p)+'/'))+') == '+json.dumps(str(p)+'/') for p in roots]
    held = native(config, 'JobStatus == 5 && ('+' || '.join(parts)+')')
    reconcile_released(config, state, {row['ClusterId'] for row in held})
    for row in held:
        key = str(row['ClusterId'])
        if config.get('allowed_clusters') is not None and row['ClusterId'] not in config['allowed_clusters']:
            continue
        if key in state.setdefault('attempts', {}):
            if state['attempts'][key]['phase'] in ('released', 'complete', 'preparation_failed'):
                state.setdefault('failures', {})[key] = 'Reviewed retry is held or blocked; no second release'
                continue
        else:
            if len(state['attempts']) >= config.get('maximum_retries', 128):
                state.setdefault('failures', {})[key] = 'Reviewed retry ceiling reached'
                continue
            try:
                review = inspect_failure(config, row)
                preflight = production_preflight(config, state, review)
                if preflight is None:
                    state.setdefault('waiting_for_preflight', {})[key] = review['production']
                    continue
                directory = root/'attempts'/key
                try:
                    state['attempts'][key] = prepare_attempt(config, review, directory, preflight)
                except Exception as error:
                    state['attempts'][key] = dict(phase='preparation_failed', review=review, error=repr(error))
                    save(root/'state.json', state)
                    raise
                state.setdefault('waiting_for_preflight', {}).pop(key, None)
                save(root/'state.json', state)
            except Exception as error:
                state.setdefault('unreviewed', {})[key] = repr(error)
                continue
        try:
            recover(config, state, row['ClusterId'])
            state.setdefault('unreviewed', {}).pop(key, None)
        except Exception as error:
            state.setdefault('failures', {})[key] = repr(error)
    if state.get('failures') or state.get('unreviewed'):
        state['error'] = 'DP recovery needs review: ' + json.dumps(dict(
            failures=state.get('failures', {}), unreviewed=state.get('unreviewed', {})), sort_keys=True)[:4000]
    # Remain available through the two main controllers' downstream publication.
    completions = config.get('completion_states', [])
    if completions and not held and not state.get('error'):
        terminal = [read(item['path']).get(item['complete_key']) is True
                    for item in completions if Path(item['path']).is_file()]
        state['complete'] = (len(terminal) == len(completions) and all(terminal)
            and all(a['phase'] == 'complete' for a in state.get('attempts', {}).values()))
    state.update(checked_utc=now(), heartbeat=state.get('heartbeat', 0)+1)
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    config = read(args.config)
    if config.get('schema') != SCHEMA:
        raise ValueError('Unknown reviewed runtime-recovery controller schema')
    for path, digest in config['dependencies'].items():
        if sha(path) != digest:
            raise ValueError('Frozen runtime-recovery dependency changed: ' + path)
    root = Path(config['directory'])
    if not root.is_absolute() or str(root).startswith('/tmp/'):
        raise ValueError('Detached runtime recovery needs its persistent absolute directory')
    shared_io(os.chdir,root)
    controller_loop(config,args.once)


def controller_loop(config,once=False):
    root=Path(config['directory'])
    with shared_io(Path.open,root/'controller.lock','a') as lock:
        shared_io(fcntl.flock,lock,fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = read(root/'state.json') if shared_io(Path.is_file,root/'state.json') else dict(attempts={})
        while True:
            try:
                state.pop('error', None)
                state = poll(config, state)
            except Exception as error:
                state.update(error=repr(error), checked_utc=now())
            save(root/'state.json', state)
            if once or state.get('complete') is True:
                print(json.dumps(state))
                return
            time.sleep(config.get('poll_seconds', 120))


if __name__ == '__main__':
    main()
