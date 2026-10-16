#!/usr/bin/env python3
# Code style: AI-code. AI-generated durable per-cohort pipeline supervision.
"""Run immutable RECO, histogram and merge tasks as their inputs become ready.

The preparation helper owns scientific selections and complete cohort membership.
This controller owns only scheduling, dependency checks and durable execution state.
It never retries science automatically or promotes a partial cohort to an analysis.
"""
import argparse
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time
import uuid

from run_mu_enriched_qcd_admission import afs_headroom, decision, validate_policy
from run_shift_ntuple_controller import problem, refresh_quota
from shift_condor_native import NativeCondorError, native_env, query, run_condor


LIVE = (1, 2, 6, 7)
STAGES = ('prepare', 'reco', 'histogram', 'merge', 'publication')
SUPERVISOR_OVERRIDES = ('parallel_gen_completion', 'poll_seconds',
    'bound_gen_reserve_by_unfinished_sources', 'watchdog_timeout_seconds',
    'prepare_command', 'prepare_timeout_seconds', 'manager_runtime_seconds')
ATTRIBUTES = ['ClusterId', 'ProcId', 'JobStatus', 'JobUniverse', 'Owner', 'Iwd',
    'QDate', 'EnteredCurrentStatus', 'JobCurrentStartDate', 'NumJobStarts',
    'ExitCode', 'ExitBySignal', 'HoldReason', 'MaxRuntime', 'DAGManJobId',
    'ShiftStreamCampaign', 'ShiftStreamTask', 'ShiftStreamNonce',
    'ShiftNtupleStageDeadlineEpoch', 'ShiftNtupleProgressEpoch', 'ShiftNtupleStageTimeout',
    'ShiftMuEnrichedQCD', 'ShiftSuiteTag', 'ShiftMuEnrichedNodeId']


def read(path):
    return json.loads(Path(path).read_text())


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + str(os.getpid()) + '.tmp')
    with temporary.open('w') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def plain(value):
    return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.-]+', value) is not None


def campaign_root(supervision, policy):
    """Keep mutable campaign state in place during a supervisor-only rollover."""
    root = Path(policy.get('campaign_directory', supervision))
    if (not root.is_absolute() or root != root.resolve()
            or str(root).startswith(('/tmp/', '/pool/condor/dir_'))
            or any(c.isspace() for c in str(root))):
        raise ValueError('Campaign state needs its original persistent absolute directory')
    return root


def field(record, dotted):
    for part in dotted.split('.'):
        if not isinstance(record, dict) or part not in record:
            raise ValueError('Missing completion field: ' + dotted)
        record = record[part]
    return record


def load_tasks(root, ledger):
    """A task's source membership, inputs and submit description cannot drift."""
    tasks = {}
    input_digests = {}
    for path in sorted((root / 'tasks').glob('*.json')):
        data = read(path)
        identity = data.get('task_id')
        if (data.get('schema') != 'shift-stream-task-v1' or not plain(identity)
                or path.stem != identity or identity in tasks
                or data.get('stage') not in STAGES or not plain(data.get('cohort_id'))):
            raise ValueError('Invalid or duplicate streaming task: ' + str(path))
        expected = data.get('completion_checks', {})
        if (not isinstance(expected, dict) or expected.get('complete') is not True
                or expected.get('exit_code') != 0):
            raise ValueError('Task completion must explicitly require complete and exit_code=0')
        if not isinstance(data.get('depends_on'), list) or any(not plain(k) for k in data['depends_on']):
            raise ValueError('Task needs explicit dependency identities')
        for key in ('submit_file', 'status_path', 'event_log', 'working_directory'):
            candidate = Path(data[key])
            if (not candidate.is_absolute() or candidate != candidate.resolve()
                    or any(c.isspace() for c in str(candidate))
                    or str(candidate).startswith(('/tmp/', '/pool/condor/dir_'))):
                raise ValueError('Task needs a persistent absolute ' + key)
        pins = data.get('input_sha256', {})
        if not isinstance(pins, dict) or data['submit_file'] not in pins:
            raise ValueError('Task must pin at least its immutable submit description')
        for filename, expected_sha in pins.items():
            if (not Path(filename).is_absolute() or not isinstance(expected_sha, str)
                    or not re.fullmatch('[0-9a-f]{64}', expected_sha)):
                raise ValueError('Frozen task input changed: ' + filename)
            if filename not in input_digests:
                input_digests[filename] = sha(filename)
            if input_digests[filename] != expected_sha:
                raise ValueError('Frozen task input changed: ' + filename)
        digest = sha(path)
        prior = ledger.get(identity, {})
        if prior and prior.get('task_sha256') != digest:
            raise ValueError('Previously seen streaming task changed: ' + identity)
        tasks[identity] = data | {'manifest_sha256': digest, 'manifest_path': str(path)}
    missing = set(ledger) - set(tasks)
    if missing:
        raise ValueError('Previously seen task manifests disappeared: ' + ', '.join(sorted(missing)))
    # Dependencies may be prepared later, but cycles and self-dependencies may not.
    def visit(identity, ancestors):
        if identity in ancestors:
            raise ValueError('Streaming task dependency cycle')
        if identity not in tasks:
            return
        for dependency in tasks[identity]['depends_on']:
            visit(dependency, ancestors | {identity})
    for identity in tasks:
        visit(identity, set())
    return tasks


def exit_event(path, cluster, process):
    """Retain native execution/exit evidence even after queue/history expiry."""
    if not Path(path).is_file():
        return {'executed': False, 'terminal': None}
    result = {'executed': False, 'terminal': None}
    for block in Path(path).read_text().split('\n...\n')[:-1]:
        match = re.match(r'\s*(\d{3}) \((\d+)\.(\d+)\.\d+\)', block)
        if not match or (int(match[2]), int(match[3])) != (cluster, process):
            continue
        kind = int(match[1])
        if kind == 1:
            result['executed'] = True
            result['terminal'] = None
        elif kind == 5:
            code = re.search(r'Normal termination \(return value (\d+)\)', block)
            result['terminal'] = int(code[1]) if code else 'signal_or_unknown'
        elif kind == 9:
            result['terminal'] = 'removed'
    return result


def validate_completion(task, status):
    for name, expected in task['completion_checks'].items():
        actual = field(status, name)
        if type(actual) is not type(expected) or actual != expected:
            raise ValueError('Completion check failed: ' + name)
    if status.get('analysis_release') is True:
        raise ValueError('A worker cannot release an analysis')
    for left, right in task.get('matching_status_fields', []):
        a, b = field(status, left), field(status, right)
        if not isinstance(a, str) or not re.fullmatch('[0-9a-f]{64}', a) or a != b:
            raise ValueError('Whole-file readback mismatch: ' + left)
    return True


def dependencies_complete(task, ledger):
    return all(ledger.get(name, {}).get('phase') == 'complete' for name in task['depends_on'])


def operational_gates(policy):
    """Future canary receipts may be added without editing the frozen policy."""
    gates = policy.get('operational_gates', [])
    if not gates:
        return False
    for item in gates:
        path = Path(item['path'])
        if not path.is_file():
            return False
        if item.get('sha256') is not None and sha(path) != item['sha256']:
            return False
        receipt = read(path)
        expected = item.get('expected', {})
        if receipt.get('passed') is not True or not expected:
            return False
        try:
            if any(type(field(receipt, key)) is not type(value) or field(receipt, key) != value
                   for key, value in expected.items()):
                return False
        except ValueError:
            return False
    return True


def load_gen_authority(authority, gen_policy):
    """Read the exact manifests and fixed gate used by the protected GEN jobs."""
    base = Path(authority['manager_directory'])
    manager = authority['manager_cluster']
    names = {'ordinary/manifest.json', 'weighted/manifest.json',
             'weighted/scientific_gate.json', 'production.dag'}
    if (authority.get('schema') != 'shift-stream-gen-completion-authority-v1'
            or not base.is_absolute() or base != base.resolve()
            or type(manager) is not int or manager <= 0
            or set(authority['files']) != names):
        raise ValueError('Invalid pinned GEN completion authority')
    frozen = {}
    for name, expected in authority['files'].items():
        payload = (base / name).read_bytes()
        if (not isinstance(expected, str) or not re.fullmatch('[0-9a-f]{64}', expected)
                or hashlib.sha256(payload).hexdigest() != expected):
            raise ValueError('Frozen GEN authority changed: ' + name)
        frozen[name] = payload
    ordinary = json.loads(frozen['ordinary/manifest.json'])
    weighted = json.loads(frozen['weighted/manifest.json'])
    gate = json.loads(frozen['weighted/scientific_gate.json'])
    tag, profile = gen_policy['suite_tag'], ordinary['generator_profile']
    if (ordinary['tag'] != tag or weighted['tag'] != tag
            or weighted['generator_profile'] != profile
            or gate.get('pass') is not True or gate.get('failures') != []
            or gate.get('manifest_sha256') != authority['files']['weighted/manifest.json']
            or gate.get('generator_profile') != profile):
        raise ValueError('GEN campaign and fixed production gate do not agree')
    return base, ordinary, weighted, gate, frozen


def gen_completion_state(authority, gen_policy, ads, observation):
    """Release only a closed, pinned GEN campaign with no work left to write.

    This is an operational storage check. It does not promote the accounting
    records' deliberately provisional physics or normalization flags.
    """
    result = {'complete': False, 'reason': 'GEN completion authority is not configured'}
    if authority is None:
        return result
    try:
        base, ordinary, weighted, gate, frozen = load_gen_authority(authority, gen_policy)
        manager = authority['manager_cluster']
        tag, profile = gen_policy['suite_tag'], ordinary['generator_profile']
        final_paths = [base / name / 'production_complete.json' for name in ('ordinary', 'weighted')]
        low, high = [read(path) for path in final_paths]
        for record, schema, manifest in (
            (low, 'shift-mu-enriched-ordinary-accounting-v1', 'ordinary/manifest.json'),
            (high, 'shift-weighted-high-bin-final-accounting-v1', 'weighted/manifest.json')):
            if (record.get('schema') != schema or record.get('complete') is not True
                    or record.get('failures') != []
                    or record.get('manifest_sha256') != authority['files'][manifest]):
                raise ValueError('Final GEN accounting is incomplete or uses another manifest')
        if (low.get('stage') != 'production' or low.get('generator_profile') != profile
                or low.get('pilots_excluded_from_production') is not True):
            raise ValueError('Ordinary final accounting does not close production')
        groups = {}
        for row in ordinary['jobs']:
            if row['stage'] == 'production':
                groups.setdefault(row['stratum'], []).append(row)
        if len(groups) != 5 or set(low['strata']) != set(groups):
            raise ValueError('Ordinary final accounting omits a planned bin')
        for name, rows in groups.items():
            summary = low['strata'][name]
            if (summary.get('chunks') != len(rows)
                    or summary.get('framework_source_events') != sum(row['trials'] for row in rows)
                    or set(summary.get('event_weight_pb_by_chunk', {})) != {row['id'] for row in rows}
                    or summary.get('no_second_filter_efficiency') is not True):
                raise ValueError('Ordinary final accounting omits a planned source')
        if not gate['production_plan'] or set(high['strata']) != set(gate['production_plan']):
            raise ValueError('Weighted final accounting omits the fixed production budget')
        for name, allocation in gate['production_plan'].items():
            summary = high['strata'][name]
            if (summary.get('chunks') != allocation['chunks']
                    or summary.get('trials') != allocation['trials']
                    or summary.get('generator_profile') != profile
                    or summary.get('effective_target_met') is not True
                    or summary.get('pilots_excluded') is not True):
                raise ValueError('Weighted final accounting has an unfinished fixed budget')
        status_path = base / 'nodes.status'
        block = status_path.read_text().split(']', 1)[0]
        if not re.search(r'\bType\s*=\s*"DagStatus"\s*;', block):
            raise ValueError('Missing native DAG status')
        counts = {key: int(value) for key, value in re.findall(r'\b(\w+)\s*=\s*(\d+)\s*;', block)}
        total = len(re.findall(r'^(?:JOB|SUBDAG\s+EXTERNAL)\s+',
                               frozen['production.dag'].decode(), re.MULTILINE))
        pending = ('NodesPre', 'NodesQueued', 'NodesPost', 'NodesReady',
                   'NodesUnready', 'NodesFutile', 'NodesFailed', 'JobProcsHeld', 'JobProcsIdle')
        if (not total or counts.get('NodesTotal') != total or counts.get('NodesDone') != total
                or any(counts.get(key) != 0 for key in pending)):
            raise ValueError('GEN DAG still has unfinished work')
        # The caller supplies its successful, fresh full-owner local-schedd read.
        # Include held and completed-but-not-transferred records conservatively.
        if any(ad.get('ShiftSuiteTag') == tag or ad.get('ClusterId') == manager
               or ad.get('DAGManJobId') == manager for ad in ads):
            raise ValueError('GEN manager or child queue records still exist')
        closed_at = max(path.stat().st_mtime for path in final_paths + [status_path])
        if observation.get('quota_observation_started_at_epoch', 0) <= closed_at:
            raise ValueError('Wait for a quota observation started after GEN closed')
        result.update(complete=True, reason='Pinned GEN accounting and native DAG are closed; queue is empty',
                      closed_at_epoch=closed_at, manager_cluster=manager,
                      evidence_sha256={str(path): sha(path) for path in final_paths + [status_path]})
    except (OSError, ValueError, KeyError, TypeError) as error:
        result['reason'] = str(error)
    return result


def published_gen_before_quota(path, request, manifest, expected_sha, cache):
    """Validate a canonical publication once until its receipt inode changes."""
    stat = path.stat()
    fingerprint = (expected_sha, stat.st_dev, stat.st_ino, stat.st_size,
                   stat.st_mtime_ns, stat.st_ctime_ns)
    cached = cache.get(str(path))
    if cached and cached['fingerprint'] == fingerprint:
        return cached['proof']
    proof = {'verified': False}
    try:
        payload = path.read_bytes()
        record = json.loads(payload)
        audit = record['semantic_audit']
        ordinary = manifest['schema'] == 'shift-mu-enriched-ordinary-manager-v1'
        schema = 'shift-mu-enriched-ordinary-gen-receipt-v1' if ordinary else 'shift-weighted-gen-receipt-v3'
        if (record.get('schema') != schema or record.get('complete') is not True
                or record.get('publication_complete') is not True or record.get('generated_edm') is not True
                or type(record.get('exit_status')) is not int or record['exit_status'] != 0
                or record.get('request') != request or record.get('manifest_sha256') != expected_sha
                or record.get('source_inputs') != manifest['inputs']
                or record.get('generator_profile') != manifest['generator_profile']
                or audit.get('generator_profile') != manifest['generator_profile']
                or audit.get('source_model_settings_sha256') != manifest['source_model_settings_sha256']
                or audit.get('complete') is not True or audit.get('healthy') is not True):
            raise ValueError('Source lacks a complete matching audited publication')
        destination = request['output_base'] + '/' + request['id']
        expected_names = ({'gen.root', 'resolved_gen_cfg.py', 'semantic_audit.json', 'cmsRun.log'}
                          if ordinary else {'gen.root', 'resolved_gen_cfg.py', 'semantic_audit.json', 'proposal_ledger.json'})
        files = {row['name']: row for row in record['publication']}
        if (record.get('publication_directory') != destination or set(files) != expected_names
                or len(files) != len(record['publication'])):
            raise ValueError('Publication names differ from the closed canonical output set')
        for name, row in files.items():
            artifact = record['artifacts'][name]
            if (row.get('independent_sha256_readback') is not True
                    or row.get('path') != destination + '/' + name
                    or type(row.get('bytes')) is not int or row['bytes'] <= 0
                    or row['bytes'] != artifact['bytes'] or row.get('sha256') != artifact['sha256']
                    or not re.fullmatch('[0-9a-f]{64}', row.get('sha256', ''))):
                raise ValueError('Canonical publication lacks its exact size/hash readback')
        if files['gen.root']['sha256'] != audit.get('input_sha256'):
            raise ValueError('GEN publication differs from the audited source')
        after = path.stat()
        if (after.st_ino, after.st_mtime_ns, after.st_ctime_ns) != (
                stat.st_ino, stat.st_mtime_ns, stat.st_ctime_ns):
            raise ValueError('Publication receipt changed while checking it')
        proof = dict(verified=True, closed_at_epoch=max(stat.st_mtime, stat.st_ctime),
                     receipt_sha256=hashlib.sha256(payload).hexdigest())
    except (OSError, ValueError, KeyError, TypeError) as error:
        proof['reason'] = str(error)
    cache[str(path)] = {'fingerprint': fingerprint, 'proof': proof}
    return proof


def remaining_gen_budget(authority, gen_policy, ads, observation, cache=None):
    """Bound future canonical writes by every unfinished fixed GEN source.

    Validation sources count too. Held, missing, invalid and after-quota
    publications retain one full worker claim. Unexpected writers or altered
    authorities fall back to the original whole-wave protection.
    """
    result = {'verified': False, 'reason': 'Pinned GEN budget is not configured'}
    if authority is None:
        return result
    cache = {} if cache is None else cache
    try:
        base, ordinary, weighted, gate, frozen = load_gen_authority(authority, gen_policy)
        if (ordinary['schema'] != 'shift-mu-enriched-ordinary-manager-v1'
                or weighted['schema'] != 'shift-weighted-high-bin-manager-v2'):
            raise ValueError('Unsupported GEN worker contract')
        started = observation['quota_observation_started_at_epoch']
        if type(started) not in (int, float) or not math.isfinite(started) or started <= 0:
            raise ValueError('Missing quota observation start time')
        planned = {}
        for kind, manifest in (('ordinary', ordinary), ('weighted', weighted)):
            workers = (('run_mu_enriched_qcd_gen.py', 'run_shift_weighted_gen.py')
                       if kind == 'ordinary' else ('run_shift_weighted_gen.py',))
            if (any(sha(base / kind / worker) != manifest['inputs'][worker] for worker in workers)
                    or manifest['peak_bytes_per_worker'] > gen_policy['reserved_bytes_per_worker']
                    or gen_policy['reserved_files_per_worker'] < 8):
                raise ValueError('Frozen GEN worker or publication bound changed')
            for row in manifest['jobs']:
                if row['stage'] not in (('canary', 'production') if kind == 'ordinary'
                                        else ('closure', 'pilot', 'production')):
                    raise ValueError('Unexpected source stage in GEN manifest')
                if (kind == 'weighted' and row['stage'] == 'production'
                        and row['chunk_index'] >= gate['production_plan'][row['stratum']]['chunks']):
                    continue
                if row['id'] in planned or not plain(row['id']) or row.get('publish', True) is not True:
                    raise ValueError('Ambiguous or unpublished fixed GEN source')
                planned[row['id']] = (kind, row, manifest)
            allowed = {identity for identity, item in planned.items() if item[0] == kind}
            if any(path.stem not in allowed for path in (base / kind / 'results').glob('*.json')):
                raise ValueError('Unexpected result outside the fixed GEN output budget')
        for name, allocation in gate['production_plan'].items():
            rows = [row for kind, row, manifest in planned.values()
                    if kind == 'weighted' and row['stage'] == 'production' and row['stratum'] == name]
            if len(rows) != allocation['chunks'] or sum(row['trials'] for row in rows) != allocation['trials']:
                raise ValueError('Fixed weighted production budget is incomplete')
        live = set()
        for ad in ads:
            writer = ad.get('ShiftMuEnrichedQCD') is True
            related = (ad.get('ShiftSuiteTag') == gen_policy['suite_tag']
                       or ad.get('DAGManJobId') == authority['manager_cluster']
                       or ad.get('Iwd') in (str(base / 'ordinary'), str(base / 'weighted')))
            if writer or (related and ad.get('JobUniverse') != 7):
                identity = ad.get('ShiftMuEnrichedNodeId')
                if not writer or ad.get('ShiftSuiteTag') != gen_policy['suite_tag'] or identity not in planned:
                    raise ValueError('Native queue contains an unbounded or unidentified GEN writer')
                if (ad.get('Iwd') != str(base / planned[identity][0])
                        or ad.get('DAGManJobId') != authority['manager_cluster']):
                    raise ValueError('Native GEN writer differs from the pinned manager and directory')
                live.add(identity)
        pending, counts, production = [], {}, 0
        for identity, (kind, row, manifest) in planned.items():
            production += row['stage'] == 'production'
            path = base / kind / 'results' / (identity + '.json')
            if identity in live:
                reason = 'queued_or_held'
            elif not path.exists():
                reason = 'missing_publication'
            else:
                proof = published_gen_before_quota(path, row, manifest,
                    authority['files'][kind + '/manifest.json'], cache)
                reason = ('unverified_publication' if not proof['verified'] else
                          'closed_after_quota' if proof['closed_at_epoch'] >= started else None)
            if reason:
                pending.append(identity)
                counts[reason] = counts.get(reason, 0) + 1
        result.update(verified=True, reason='Every unfinished fixed source retains its full publication claim',
            planned_sources=len(planned), production_sources=production,
            validation_sources=len(planned) - production,
            published_before_quota=len(planned) - len(pending), unfinished_sources=len(pending),
            pending_by_reason=counts, pending_source_ids=sorted(pending),
            quota_observation_started_at_epoch=started)
    except (OSError, ValueError, KeyError, TypeError) as error:
        result['reason'] = str(error)
    return result


def reserve_other_gen_slots(policy, observation, gen_policy, completion=None, remaining=None):
    """Leave room for every slot the unchanged GEN guard can still admit.

    The live GEN guard does not read this downstream controller's reservations.
    Reserving only current GEN workers would race later GEN admissions. Preserve
    its entire possible byte wave; use only the fractional remainder for Nano.
    A later fresh EOS observation can account for already published Nano bytes.
    """
    result = dict(observation)
    external = observation.get('external_storage_commitments', {})
    floor = max(policy['minimum_free_bytes'], external.get('required_free_bytes', 0))
    headroom = max(0, observation.get('free_bytes', 0) - floor)
    gen_bytes = gen_policy['reserved_bytes_per_worker']
    # Also cover completed GEN reservations retained against a cached quota.
    # Reserving every possible whole slot is deliberately conservative.
    count = 0 if completion and completion.get('complete') is True else headroom // gen_bytes
    if remaining and remaining.get('verified') is True:
        count = min(count, remaining['unfinished_sources'])
    result['free_bytes'] = max(0, observation.get('free_bytes', 0) - count * gen_bytes)
    result['free_files'] = max(0, observation.get('free_files', 0)
                              - count * gen_policy['reserved_files_per_worker'])
    result['protected_gen_worker_slots'] = count
    if completion is not None:
        result['gen_completion'] = completion
    if remaining is not None:
        result['gen_remaining_budget'] = remaining
    return result


def release_completed_pre_reservations(tag, tasks, ledger, ads, observation, reservations):
    """Drop only proven finished stream claims covered by a newer quota read.

    A short task can start and finish between admission polls, so the shared
    GEN guard may never have seen its queue record. The stream has stronger
    task-specific native/status evidence; leave the shared guard unchanged.
    The caller must supply its successful fresh full-owner scheduler read.
    """
    retained, released = dict(reservations), {}
    started = observation.get('quota_observation_started_at_epoch')
    checked = observation.get('quota_checked_at_epoch')
    if (observation.get('complete') is not True
            or type(started) not in (int, float) or not math.isfinite(started) or started <= 0
            or type(checked) not in (int, float) or not math.isfinite(checked) or checked < started
            or any(type(observation.get(key)) is not int or observation[key] < 0
                   for key in ('free_bytes', 'free_files'))):
        return retained, released
    for identity, reservation in reservations.items():
        try:
            task, record = tasks[identity], ledger[identity]
            job = record['job']
            recorded_exit = record.get('native_exit', {})
            if (record.get('phase') != 'complete' or record.get('execution_verified') is not True
                    or recorded_exit.get('executed') is not True
                    or type(recorded_exit.get('terminal')) is not int or recorded_exit['terminal'] != 0
                    or record.get('task_sha256') != task['manifest_sha256']
                    or record.get('status_path') != task['status_path']
                    or not isinstance(job, list) or len(job) != 2
                    or any(type(value) is not int for value in job) or job[0] <= 0 or job[1] < 0):
                continue
            if any(ad.get('ClusterId') == job[0]
                   or (ad.get('ShiftStreamCampaign') == tag and ad.get('ShiftStreamTask') == identity)
                   or ad.get('Iwd') == task['working_directory'] for ad in ads):
                continue
            status_path, result_path, event_path = [Path(task[key])
                for key in ('status_path', 'result_path', 'event_log')]
            if sha(status_path) != record['status_sha256']:
                continue
            validate_completion(task, read(status_path))
            if read(result_path).get('complete') is not True:
                continue
            native = exit_event(event_path, *job)
            if native != {'executed': True, 'terminal': 0}:
                continue
            times = [record['completed_at_epoch']] + [path.stat().st_mtime
                for path in (status_path, result_path, event_path)]
            admitted_at = reservation['at_epoch']
            if (any(type(value) not in (int, float) or not math.isfinite(value) or value <= 0 for value in times)
                    or type(admitted_at) not in (int, float) or not math.isfinite(admitted_at)
                    or not 0 < admitted_at <= record['completed_at_epoch']):
                continue
            closed_at = max(times)
            if started <= closed_at:
                continue
            released[identity] = dict(job=job, task_sha256=record['task_sha256'],
                status_sha256=record['status_sha256'], result_sha256=sha(result_path),
                event_log_sha256=sha(event_path),
                native_exit=native, closed_at_epoch=closed_at,
                quota_observation_started_at_epoch=started)
            del retained[identity]
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            # Unknown or changed evidence keeps its entire claim.
            continue
    return retained, released


def mapped_ads(ads, tag):
    """Reuse the proven admission reservation algorithm for downstream jobs."""
    result = []
    for ad in ads:
        row = dict(ad)
        if ad.get('ShiftStreamCampaign') == tag:
            row.update(ShiftMuEnrichedQCD=True, ShiftSuiteTag=tag,
                       ShiftMuEnrichedNodeId=ad.get('ShiftStreamTask'))
        else:
            row['ShiftMuEnrichedQCD'] = False
        result.append(row)
    return result


class StreamController:
    def __init__(self, root, reader=query, commander=run_condor):
        self.supervision_root = Path(root).resolve()
        self.policy = read(self.supervision_root / 'policy.json')
        self.root = campaign_root(self.supervision_root, self.policy)
        self.reader, self.commander = reader, commander
        self.gen_publication_cache = {}
        self.admission_policy = validate_policy(self.policy['admission'])
        if self.admission_policy['suite_tag'] != self.policy['tag']:
            raise ValueError('Stream and admission tags must agree')
        self.ledger = read(self.root / 'ledger.json') if (self.root / 'ledger.json').exists() else {}
        deployments = [self.supervision_root]
        if self.root != self.supervision_root:
            adoption = self.policy.get('adoption', {})
            if (adoption.get('schema') != 'shift-stream-supervisor-adoption-v1'
                    or sha(self.root / 'policy.json') != adoption.get('campaign_policy_sha256')
                    or sha(self.root / 'deployment.json') != adoption.get('campaign_deployment_sha256')):
                raise ValueError('Adopted campaign authority changed')
            old_policy = read(self.root / 'policy.json')
            unchanged = dict(self.policy)
            for key in ('campaign_directory', 'adoption') + SUPERVISOR_OVERRIDES:
                unchanged.pop(key, None)
            for key in SUPERVISOR_OVERRIDES:
                old_policy.pop(key, None)
            if unchanged != old_policy:
                raise ValueError('Supervisor adoption changed a worker, admission or sampling policy')
            deployments.append(self.root)
        for deployment in deployments:
            for name, digest in read(deployment / 'deployment.json')['files'].items():
                if sha(deployment / name) != digest:
                    raise ValueError('Frozen stream deployment changed: ' + name)

    def persist(self):
        save(self.root / 'ledger.json', self.ledger)

    def observe(self):
        owner = self.admission_policy['account_owner']
        return self.reader('Owner == ' + json.dumps(owner), ATTRIBUTES,
                           timeout=30, local_schedd=True)

    def reconcile(self, tasks, ads, now):
        problems = []
        for identity, task in tasks.items():
            record = self.ledger.setdefault(identity, {'phase': 'waiting',
                'task_sha256': task['manifest_sha256'], 'first_seen_at_epoch': now})
            if record['phase'] == 'waiting':
                continue
            if record['phase'] in ('complete', 'failed'):
                continue
            matches = [ad for ad in ads if ad.get('ShiftStreamCampaign') == self.policy['tag']
                       and ad.get('ShiftStreamTask') == identity
                       and ad.get('ShiftStreamNonce') == record['nonce']]
            if len(matches) > 1:
                raise ValueError('Duplicate streaming task execution identity: ' + identity)
            if matches:
                ad = matches[0]
                pair = [ad['ClusterId'], ad['ProcId']]
                if record.get('job') not in (None, pair):
                    raise ValueError('Streaming task changed native job identity')
                record.update(job=pair, phase='submitted', queue=ad)
                if ad.get('Iwd') != task['working_directory']:
                    problems.append(identity + ': queued Iwd differs from persistent task directory')
                issue = problem(ad, now, self.policy['worker_limits'])
                if issue:
                    record.update(phase='failed', error=issue)
                    problems.append(identity + ': ' + issue)
            pair = record.get('job')
            proof = exit_event(task['event_log'], *pair) if pair else {'executed': False, 'terminal': None}
            if proof['executed']:
                record['execution_verified'] = True
            if proof['terminal'] not in (None, 0):
                record.update(phase='failed', error='Native unsuccessful exit: ' + str(proof['terminal']))
                problems.append(identity + ': ' + record['error'])
            if proof['terminal'] == 0:
                if not Path(task['status_path']).exists():
                    record.setdefault('awaiting_status_since_epoch', now)
                    if now - record['awaiting_status_since_epoch'] > self.policy.get('status_transfer_grace_seconds', 300):
                        problems.append(identity + ': successful worker has no transferred completion status')
                    continue
                try:
                    validate_completion(task, read(task['status_path']))
                except (ValueError, KeyError, TypeError) as error:
                    record.update(phase='failed', error=str(error))
                    problems.append(identity + ': ' + str(error))
                    continue
                record.update(phase='complete', completed_at_epoch=now,
                    status_path=task['status_path'], status_sha256=sha(task['status_path']),
                    native_exit=proof, execution_verified=True)
            elif not matches and now - record['submitted_at_epoch'] > 180:
                problems.append(identity + ': submission or disappearance is unresolved; no automatic retry')
        self.persist()
        return problems

    def submit(self, task, now):
        identity = task['task_id']
        record = self.ledger[identity]
        if record['phase'] != 'waiting':
            raise ValueError('Science task may be submitted only once')
        nonce = uuid.uuid4().hex
        record.update(phase='submitting', nonce=nonce, submitted_at_epoch=now, job=None)
        self.persist()
        command = ['/usr/bin/condor_submit', '-terse', task['submit_file']]
        additions = {'ShiftStreamCampaign': self.policy['tag'],
                     'ShiftStreamTask': identity, 'ShiftStreamNonce': nonce}
        for key, value in additions.items():
            command.extend(['-append', '+' + key + ' = ' + json.dumps(value)])
        response = self.commander(command, timeout=30, local_schedd=True,
                                  cwd=task['working_directory'])
        match = re.fullmatch(r'(\d+)\.0\s*-\s*\1\.0\s*', response)
        if not match:
            raise NativeCondorError('Ambiguous submission; durable intent remains reserved')
        record.update(job=[int(match[1]), 0], phase='submitted')
        self.persist()

    def step(self, now=None, dry_run=False):
        now = time.time() if now is None else now
        ads = self.observe()  # No mutations after an unknown scheduler read.
        tasks = load_tasks(self.root, self.ledger)
        problems = self.reconcile(tasks, ads, now)
        # A deterministic frozen helper may discover newly complete GEN files
        # and freeze downstream tasks. It cannot submit jobs itself.
        command = self.policy.get('prepare_command')
        if command and not dry_run:
            subprocess.run(command, cwd=self.root, env=native_env(local_schedd=True),
                check=True, timeout=self.policy.get('prepare_timeout_seconds', 120))
            tasks = load_tasks(self.root, self.ledger)
            problems.extend(self.reconcile(tasks, ads, now))
        passed = operational_gates(self.policy)
        candidates = [task for identity, task in tasks.items()
            if self.ledger[identity]['phase'] == 'waiting' and dependencies_complete(task, self.ledger)
            and (passed or task.get('operational_canary') is True)]
        candidates.sort(key=lambda task: (STAGES.index(task['stage']) * -1,
                         task.get('priority', 0) * -1, task['task_id']))
        admission_root = self.root / 'admission'
        quota = refresh_quota(admission_root) if not dry_run else read(admission_root / 'last_quota.json')
        gen_policy_path = self.policy.get('parallel_gen_admission_policy')
        if gen_policy_path:
            gen_policy = read(gen_policy_path)
            completion = gen_completion_state(self.policy.get('parallel_gen_completion'),
                                              gen_policy, ads, quota)
            remaining = None
            if self.policy.get('bound_gen_reserve_by_unfinished_sources') is True:
                remaining = remaining_gen_budget(self.policy.get('parallel_gen_completion'),
                    gen_policy, ads, quota, self.gen_publication_cache)
            quota = reserve_other_gen_slots(self.admission_policy, quota, gen_policy, completion, remaining)
        reservations_path = admission_root / 'pre_reservations.json'
        reservations = read(reservations_path) if reservations_path.exists() else {}
        reservations, released_pre = release_completed_pre_reservations(
            self.policy['tag'], tasks, self.ledger, ads, quota, reservations)
        if released_pre and not dry_run:
            path = admission_root / 'completed_pre_releases.json'
            receipts = read(path) if path.exists() else {}
            for identity, proof in released_pre.items():
                key = identity + ':' + str(proof['job'][0])
                receipts.setdefault(key, proof)
            save(path, receipts)
            save(reservations_path, reservations)
        capacity = {'admitted': False, 'reason': 'No task is ready'}
        submitted = 0
        if any(r['phase'] == 'failed' for r in self.ledger.values()):
            problems.append('A scientific task failed; preserve its cohort and do not omit it from rates')
        if not problems and not (self.root / 'stop_requested.json').exists():
            for task in candidates[:self.policy.get('maximum_submissions_per_poll', 4)]:
                capacity = decision(self.admission_policy, quota,
                    mapped_ads(ads, self.policy['tag']), reservations, task['task_id'], now,
                    afs_headroom(self.root))
                reservations = capacity.pop('reservations', reservations)
                if not dry_run:
                    save(reservations_path, reservations)
                if not capacity['admitted']:
                    break
                if not dry_run:
                    self.submit(task, now)
                submitted += 1
        counts = {stage: {} for stage in STAGES}
        for identity, task in tasks.items():
            phase = self.ledger[identity]['phase']
            counts[task['stage']][phase] = counts[task['stage']].get(phase, 0) + 1
        heartbeat = {'checked_at_epoch': now, 'phase': 'attention_required' if problems else 'running',
            'scheduler_state': 'known', 'problems': problems, 'tasks_by_stage': counts,
            'operational_gates_passed': passed, 'submitted_this_poll': submitted,
            'admission': capacity, 'protected_gen_worker_slots': quota.get('protected_gen_worker_slots'),
            'gen_completion': quota.get('gen_completion'),
            'gen_remaining_budget': quota.get('gen_remaining_budget'),
            'released_completed_PREs': released_pre,
            'analysis_release': False, 'normalization_ready': False}
        if not tasks or (candidates and not submitted and not problems):
            heartbeat['phase'] = 'waiting_for_inputs_or_quota'
        save(self.root / 'heartbeat.json', heartbeat)
        if problems:
            save(self.root / 'attention_required.json', heartbeat)
        return heartbeat


def watch(root, policy):
    """Independently detect a dead/idle scheduler manager without changing jobs."""
    now = time.time()
    ads = query('ShiftStreamCampaign == ' + json.dumps(policy['tag']),
        ATTRIBUTES + ['ShiftStreamController', 'ShiftStreamWatchdog'],
        timeout=30, local_schedd=True)
    managers = [ad for ad in ads if ad.get('ShiftStreamController') is True
                and ad.get('ShiftStreamWatchdog') is not True]
    problems = []
    if len(managers) != 1 or managers[0].get('JobStatus') != 2:
        problems.append('Expected exactly one executing stream controller')
    elif (managers[0].get('Iwd') != str(root)
          or managers[0].get('MaxRuntime', 0) < policy.get('manager_runtime_seconds', 604800)):
        problems.append('Controller Iwd or runtime differs from its persistent contract')
    campaign = campaign_root(root, policy)
    path = campaign / 'heartbeat.json'
    heartbeat = read(path) if path.exists() else {}
    if now - heartbeat.get('checked_at_epoch', 0) > policy.get('watchdog_timeout_seconds', 300):
        problems.append('Stream controller heartbeat is missing or stale')
    if heartbeat.get('phase') in ('controller_fault', 'attention_required'):
        problems.append('Stream controller reports a fault or blocked scientific task')
    record = {'checked_at_epoch': now, 'phase': 'attention_required' if problems else 'observing',
              'problems': problems, 'manager': managers,
              'worker_action': 'none; read-only watchdog', 'analysis_release': False}
    save(campaign / 'watchdog_heartbeat.json', record)
    if problems:
        save(campaign / 'watchdog_attention_required.json', record)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--watchdog', action='store_true')
    args = parser.parse_args()
    root = args.directory.resolve()
    if str(root).startswith(('/tmp/', '/pool/condor/dir_')):
        raise ValueError('Controller requires an absolute persistent working directory')
    campaign = campaign_root(root, read(root / 'policy.json'))
    with (campaign / ('watchdog.lock' if args.watchdog else 'controller.lock')).open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        controller = StreamController(root)
        while True:
            try:
                heartbeat = watch(root, controller.policy) if args.watchdog else controller.step(dry_run=args.dry_run)
            except Exception as error:
                heartbeat = {'checked_at_epoch': time.time(), 'phase': 'controller_fault',
                    'scheduler_state': 'unknown', 'error': repr(error), 'analysis_release': False,
                    'worker_action': 'none; preserve all running workers and ambiguous submissions'}
                save(campaign / ('watchdog_heartbeat.json' if args.watchdog else 'heartbeat.json'), heartbeat)
                save(campaign / ('watchdog_attention_required.json' if args.watchdog else 'attention_required.json'), heartbeat)
            if args.once or args.dry_run:
                print(json.dumps(heartbeat))
                return 0 if heartbeat['phase'] != 'controller_fault' else 1
            time.sleep(controller.policy.get('poll_seconds', 30))


if __name__ == '__main__':
    raise SystemExit(main())
