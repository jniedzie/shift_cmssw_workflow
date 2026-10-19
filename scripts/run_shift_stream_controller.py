#!/usr/bin/env python3
# Code style: AI-code. AI-generated durable per-cohort pipeline supervision.
"""Run immutable RECO, histogram and merge tasks as their inputs become ready.

The preparation helper owns scientific selections and complete cohort membership.
This controller owns only scheduling, dependency checks and durable execution state.
It never retries science automatically or promotes a partial cohort to an analysis.
"""
import argparse
import copy
from datetime import datetime
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import subprocess
import time
import uuid

from run_mu_enriched_qcd_admission import afs_headroom, decision, validate_policy
from run_shift_ntuple_controller import problem, refresh_quota
from shift_condor_native import NativeCondorError, native_env, query, run_condor


LIVE = (1, 2, 6, 7)
STAGES = ('prepare', 'reco', 'histogram', 'merge', 'publication')
READONLY_COMPANIONS = ('read_only_meson_coverage', 'read_only_meson_watchdog',
                       'read_only_statistics_watchdog')
SUPERVISOR_OVERRIDES = ('parallel_gen_completion', 'poll_seconds',
    'bound_gen_reserve_by_unfinished_sources', 'watchdog_timeout_seconds',
    'prepare_command', 'prepare_timeout_seconds', 'manager_runtime_seconds',
    'admission_worker_ceiling', 'maximum_submissions_per_poll', 'maximum_idle_workers',
    'nonpublishing_peer_controllers', 'accounted_peer_publication_workers',
    'additional_peer_meson_campaigns', 'peer_storage_authority', 'prioritize_source_prefix',
    'selection_revision')
ATTRIBUTES = ['ClusterId', 'ProcId', 'GlobalJobId', 'JobStatus', 'JobUniverse', 'Owner', 'Iwd',
    'QDate', 'EnteredCurrentStatus', 'JobCurrentStartDate', 'NumJobStarts',
    'ExitCode', 'ExitBySignal', 'HoldReason', 'HoldReasonCode', 'HoldReasonSubCode',
    'LastHoldReason', 'LastHoldReasonCode', 'LastHoldReasonSubCode', 'MaxRuntime', 'DAGManJobId',
    'ShiftStreamCampaign', 'ShiftStreamTask', 'ShiftStreamNonce',
    'ShiftNtupleStageDeadlineEpoch', 'ShiftNtupleProgressEpoch', 'ShiftNtupleStageTimeout',
    'ShiftMuEnrichedQCD', 'ShiftSuiteTag', 'ShiftMuEnrichedNodeId',
    'JobBatchName', 'Cmd', 'Args', 'ShiftSignalValidationOnly', 'ShiftBSMPoint', 'ShiftDPStatisticsTask',
    'ShiftDPStatisticsReadOnly', 'ShiftDPStatisticsWatchdog']


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


def stream_admission_policy(policy):
    """Permit an explicit worker cap without changing sealed storage authorities."""
    if type(policy.get('prioritize_source_prefix', False)) is not bool:
        raise ValueError('prioritize_source_prefix needs an explicit boolean')
    admission = copy.deepcopy(policy['admission'])
    replacement = policy.get('peer_storage_authority')
    if replacement is not None:
        from read_shift_external_storage_commitments import reviewed_peer_authority_replacement
        authorities = admission.get('external_storage_reservations', [])
        if len(authorities) != 1:
            raise ValueError('Peer supervision replacement needs one complete shared authority')
        admission['external_storage_reservations'] = [
            reviewed_peer_authority_replacement(authorities[0], replacement)]
    additions = policy.get('additional_peer_meson_campaigns', [])
    if additions:
        authorities = admission.get('external_storage_reservations', [])
        if (not isinstance(additions, list) or len(additions) != 1
                or len(authorities) != 1
                or authorities[0].get('schema') != 'shift-statistics-peer-storage-authority-v1'
                or not isinstance(authorities[0].get('constrained_stream'), dict)
                or authorities[0]['constrained_stream'].get('additional_meson_campaigns')):
            raise ValueError('Additional meson peers require one unchanged complete constrained authority')
        authorities[0]['constrained_stream']['additional_meson_campaigns'] = copy.deepcopy(additions)
    admission.setdefault('reserve_full_worker_ceiling', False)
    if 'admission_worker_ceiling' in policy:
        admission['worker_ceiling'] = policy['admission_worker_ceiling']
    idle_limit = policy.get('maximum_idle_workers')
    if idle_limit is not None and (type(idle_limit) is not int
                                  or not 1 <= idle_limit <= admission['worker_ceiling']):
        raise ValueError('Maximum idle workers must be a positive integer within the worker ceiling')
    return validate_policy(admission)


def task_scheduling_key(task, policy):
    """Finish early source prefixes across bins before later high-bin batches."""
    stage = task['stage']
    priority = -task.get('priority', 0)
    if not policy.get('prioritize_source_prefix', False) or stage in ('histogram', 'merge', 'publication'):
        return (-STAGES.index(stage), 0, priority, task['task_id'])
    match = re.fullmatch(r'source_.+_(\d+)', task['cohort_id'])
    if match is None:
        raise ValueError('Source-prefix scheduling needs a numbered source cohort')
    # A later reconstruction batch must not prevent a missing low-bin scan.
    # Existing GEN-tail priorities still break ties within each source index.
    return (-1, int(match[1]), priority, task['task_id'])


def idle_worker_capacity(policy, ads, reservations, ledger, candidate, submitted_this_poll=()):
    """Bound cold starts separately from the production's worker ceiling.

    A fresh native observation supplies idle jobs. Submitted jobs and PREs not
    yet visible in that observation conservatively occupy an idle slot too.
    Successful submissions in this poll count immediately, including dry runs.
    Running and held jobs keep their ordinary CPU/storage reservations.
    """
    limit = policy.get('maximum_idle_workers')
    if limit is None:
        return None
    own = [ad for ad in ads if ad.get('JobUniverse') == 5
           and ad.get('ShiftStreamCampaign') == policy['tag']]
    queued, idle = set(), set()
    for ad in own:
        identity = ad.get('ShiftStreamTask')
        if not plain(identity):
            raise ValueError('Native stream worker lacks its exact task identity')
        queued.add(identity)
        if ad.get('JobStatus') == 1:
            idle.add(identity)
    pending = {identity for identity in reservations if identity not in queued
               and ledger.get(identity, {}).get('phase') not in ('complete', 'failed')}
    pending.update(identity for identity, record in ledger.items()
                   if record.get('phase') in ('submitting', 'submitted') and identity not in queued)
    reserved = idle | pending | set(submitted_this_poll)
    proposed = len(reserved | {candidate})
    return {'admitted': proposed <= limit, 'reason': 'This production has reached its idle-worker ceiling'
            if proposed > limit else 'Idle-worker capacity available',
            'native_idle_workers': len(idle), 'pending_idle_workers': len(pending),
            'reserved_idle_workers': len(reserved), 'proposed_idle_workers': proposed,
            'maximum_idle_workers': limit}


def stage_publication_bounds(policy, admission):
    """Keep larger source merges explicit without reserving 900 large slots."""
    bounds = policy.get('stage_publication_bounds', {})
    if not isinstance(bounds, dict) or set(bounds) - {'histogram', 'merge'}:
        raise ValueError('Publication overrides may name only histogram and merge stages')
    baseline = admission['reserved_bytes_per_worker']
    if any(type(bound) is not int or not baseline <= bound <= 512*1024**2
           for bound in bounds.values()):
        raise ValueError('Stage publication bounds must cover the baseline and be at most 512 MiB')
    return bounds


def reserve_stage_publications(policy, admission, observation, tasks, ads, reservations, candidate):
    """Declare the extra bytes of larger live or still-reserved stages.

    The unchanged admission helper reserves the baseline for every worker.
    Completed PREs retain their extra bytes until the existing native-exit,
    readback and fresh-quota release proof removes their persistent claim.
    """
    bounds = stage_publication_bounds(policy, admission)
    if not bounds:
        return observation
    identities = set(reservations)
    for ad in ads:
        if (ad.get('ShiftStreamCampaign') == policy['tag'] and ad.get('JobUniverse') == 5
                and ad.get('JobStatus') in (1, 2, 5, 6, 7)):
            identity = ad.get('ShiftStreamTask')
            if not isinstance(identity, str):
                raise ValueError('Cannot reserve storage for an unidentified streaming worker')
            identities.add(identity)
    identities.add(candidate)
    baseline = admission['reserved_bytes_per_worker']
    extra = 0
    for identity in identities:
        task = tasks.get(identity)
        if not task or task.get('stage') not in STAGES:
            raise ValueError('Cannot reserve publication for an unknown streaming task: ' + str(identity))
        bound = bounds.get(task['stage'], baseline)
        declared = task.get('publication_reservation_bytes', baseline)
        if type(declared) is not int or declared <= 0 or declared > bound:
            raise ValueError('Task publication bound exceeds its reserved stage: ' + identity)
        extra += bound - baseline
    return dict(observation, extra_stage_reserved_bytes=extra,
                stage_reservation_task_ids=sorted(identities))


def verify_pinned_peer_launch(expected, pins, label):
    """Require the audited program as well as every declared frozen input."""
    if not isinstance(pins, dict) or not pins:
        raise ValueError(label + ' has no frozen input map')
    arguments = shlex.split(expected['Args'])
    program = expected['Cmd']
    if program not in pins:
        if not re.fullmatch(r'(python(?:[0-9]+(?:\.[0-9]+)*)?|bash|sh)', Path(program).name):
            raise ValueError(label + ' executable is not among its frozen inputs')
        while arguments and arguments[0] in ('-u', '-B', '-E', '-s', '-I', '-S', '-e'):
            arguments.pop(0)
        if not arguments or not Path(arguments[0]).is_absolute() or arguments[0] not in pins:
            raise ValueError(label + ' launched program is not pinned')
    for filename, digest in pins.items():
        if (not isinstance(filename, str) or not Path(filename).is_absolute()
                or '..' in Path(filename).parts or not isinstance(digest, str)
                or re.fullmatch(r'[0-9a-f]{64}', digest) is None
                or sha(filename) != digest):
            raise ValueError(label + ' frozen input changed: ' + str(filename))


def verify_meson_readonly_companion(binding):
    """Bind future inventory/monitor jobs to one sealed meson deployment."""
    expected, pins = binding['expected_native'], binding['frozen_input_sha256']
    config_pin, seal_pin = binding['meson_config'], binding['meson_seal']
    for pin in (config_pin, seal_pin, binding['submit']):
        if pins.get(pin['path']) != pin['sha256']:
            raise ValueError('Meson companion authority is outside its frozen input map')
    config, seal = read(config_pin['path']), read(seal_pin['path'])
    root = Path(config_pin['path']).parent
    if (config.get('schema') != 'shift-dp-meson-production-v1'
            or config.get('production_mechanism') != 'meson-radiative'
            or config.get('directory') != str(root)
            or seal_pin['path'] != str(root / 'seal.json')
            or seal['files'].get(config_pin['path']) != config_pin['sha256']
            or expected.get('ShiftDPStatisticsReadOnly') is not True
            or expected.get('ShiftDPStatisticsTask') != expected['Iwd']
            or expected['Cmd'] != '/usr/bin/python3'):
        raise ValueError('Meson companion lost its sealed source or read-only identity')
    fields = {}
    for line in Path(binding['submit']['path']).read_text().splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key, value = line.split('=', 1)
            fields[key.strip().lower()] = value.strip()
    if (fields.get('initialdir') != expected['Iwd'] or fields.get('executable') != expected['Cmd']
            or fields.get('arguments') != expected['Args']
            or fields.get('+shiftdpstatisticsreadonly', '').lower() != 'true'):
        raise ValueError('Meson companion launch differs from its pinned descriptor')
    arguments = shlex.split(expected['Args'])
    if binding['audited_purpose'] == 'read_only_meson_coverage':
        program = str(Path(config['tools']) / 'run_dark_photon_meson_production.py')
        if (expected['Iwd'] != str(root / 'coverage')
                or arguments != [program, '--directory', str(root), '--seal', seal_pin['sha256'],
                                 '--action', 'coverage']
                or expected.get('ShiftDPStatisticsWatchdog') is True
                or seal['files'].get(program) != pins.get(program)):
            raise ValueError('Coverage companion must run only the sealed coverage action')
        for pin in [config['coverage_audit']['particle_data'],
                    dict(path=config['model_card'], sha256=seal['files'].get(config['model_card']))]:
            if pins.get(pin['path']) != pin['sha256']:
                raise ValueError('Coverage companion omitted its model or particle-table input')
    else:
        if (len(arguments) != 3 or Path(arguments[0]).name != 'watch_dark_photon_statistics.py'
                or arguments[1] != '--config' or expected.get('ShiftDPStatisticsWatchdog') is not True
                or fields.get('+shiftdpstatisticswatchdog', '').lower() != 'true'):
            raise ValueError('Meson watchdog companion must run its exact read-only monitor')
        monitor = read(arguments[2])
        if (arguments[2] not in pins or monitor.get('schema') != 'shift-dp-statistics-watchdog-v1'
                or monitor.get('no_job_mutations') is not True
                or monitor.get('production_directory') != str(root)
                or monitor.get('worklist') != config_pin['path']
                or not monitor.get('dependencies')
                or any(pins.get(path) != digest for path, digest in monitor['dependencies'].items())):
            raise ValueError('Meson watchdog companion lost its exact frozen inputs or no-mutation policy')


def verify_statistics_readonly_companion(binding):
    """Register one future read-only monitor against its pinned replacement."""
    expected, pins = binding['expected_native'], binding['frozen_input_sha256']
    config_pin, submit_pin = binding['controller_config'], binding['submit']
    for pin in (config_pin, submit_pin):
        if pins.get(pin['path']) != pin['sha256']:
            raise ValueError('Statistics monitor authority is outside its frozen input map')
    controller = read(config_pin['path'])
    root = Path(config_pin['path']).parent
    arguments = shlex.split(expected['Args'])
    fields = {}
    for line in Path(submit_pin['path']).read_text().splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key, value = line.split('=', 1)
            fields[key.strip().lower()] = value.strip()
    if (expected['Cmd'] != '/usr/bin/python3' or len(arguments) != 3
            or Path(arguments[0]).name != 'watch_dark_photon_statistics.py'
            or arguments[1] != '--config' or arguments[2] not in pins
            or expected.get('ShiftDPStatisticsReadOnly') is not True
            or expected.get('ShiftDPStatisticsWatchdog') is not True
            or expected.get('ShiftDPStatisticsTask') != expected['Iwd']
            or expected['Iwd'] != str(root/'monitor')
            or fields.get('initialdir') != expected['Iwd']
            or fields.get('executable') != expected['Cmd'] or fields.get('arguments') != expected['Args']
            or fields.get('+shiftdpstatisticsreadonly', '').lower() != 'true'
            or fields.get('+shiftdpstatisticswatchdog', '').lower() != 'true'):
        raise ValueError('Statistics companion must run its exact read-only monitor descriptor')
    monitor = read(arguments[2])
    worklist = controller.get('worklist', str(Path(controller['directory'])/'config.json'))
    if (monitor.get('schema') != 'shift-dp-statistics-watchdog-v1'
            or monitor.get('no_job_mutations') is not True
            or monitor.get('production_directory') != controller['directory']
            or monitor.get('worklist') != worklist
            or monitor.get('supervisor_deployment_directory') != str(root)
            or monitor.get('supervisor_receipt') != str(root/'submission.json')
            or monitor.get('resolve_supervisor_from_receipt') is not True
            or not monitor.get('dependencies')
            or monitor['dependencies'].get(config_pin['path']) != config_pin['sha256']
            or any(pins.get(path) != digest for path, digest in monitor['dependencies'].items())):
        raise ValueError('Statistics companion lost its pinned production or no-mutation policy')


def nonpublishing_peer_bindings(policy):
    """Check the exact reviewed programs of read-only monitors and host guards.

    A controller's directory does not establish its role. The reviewed purpose,
    full native launch identity and all frozen inputs must agree. Science
    controllers cannot use this scheduling-only exception.
    """
    bindings = policy.get('nonpublishing_peer_controllers', [])
    if not isinstance(bindings, list):
        raise ValueError('Nonpublishing peer controllers need an explicit binding list')
    required = {'Owner', 'JobUniverse', 'Iwd', 'Cmd', 'Args'}
    names, identities = set(), set()
    for binding in bindings:
        if not isinstance(binding, dict):
            raise ValueError('Invalid nonpublishing peer controller binding')
        name = binding.get('name')
        purpose = binding.get('audited_purpose')
        expected = binding.get('expected_native', {})
        pins = binding.get('frozen_input_sha256', {})
        companion = purpose in READONLY_COMPANIONS
        if (not plain(name) or name in names
                or purpose not in ('read_only_watchdog', 'worker_host_guard') + READONLY_COMPANIONS
                or not isinstance(expected, dict) or not required.issubset(expected)
                or not isinstance(pins, dict) or not pins):
            raise ValueError('Nonpublishing peer lacks a unique audited purpose, exact launch or frozen inputs')
        identity = ((expected.get('Iwd'), expected.get('Cmd'), expected.get('Args')) if companion
                    else (expected.get('ClusterId'), expected.get('ProcId')))
        if ((not companion and (type(identity[0]) is not int or identity[0] <= 0
                               or type(identity[1]) is not int or identity[1] < 0))
                or (companion and {'ClusterId', 'ProcId', 'GlobalJobId'}.intersection(expected))
                or identity in identities
                or expected.get('JobUniverse') != 5
                or not isinstance(expected.get('Owner'), str) or not expected['Owner']
                or not isinstance(expected.get('Args'), str)
                or any(not isinstance(expected.get(key), str)
                       or not Path(expected[key]).is_absolute() or '..' in Path(expected[key]).parts
                       for key in ('Iwd', 'Cmd'))
                or set(expected) - set(ATTRIBUTES)):
            raise ValueError('Nonpublishing peer has an incomplete or ambiguous native identity')
        # Bind the actual launched program, rather than just a configuration
        # file whose contents could be shared by a science controller.
        verify_pinned_peer_launch(expected, pins, 'Nonpublishing peer')
        if purpose == 'read_only_statistics_watchdog':
            verify_statistics_readonly_companion(binding)
        elif companion:
            verify_meson_readonly_companion(binding)
        if (expected.get('ShiftStreamTask') or expected.get('ShiftMuEnrichedQCD') is True
                or expected.get('ShiftBSMPoint') or expected.get('ShiftSignalValidationOnly') is True
                or expected.get('DAGManJobId')):
            raise ValueError('A scientific worker cannot be a nonpublishing peer controller')
        names.add(name)
        identities.add(identity)
    return bindings


def accounted_publication_bindings(policy, capacity=None):
    """Bind recovery publication workers to an unchanged active storage claim.

    These remain scientific writers. The binding only identifies an exact
    reviewed publication stage outside its original campaign directory.
    """
    bindings = policy.get('accounted_peer_publication_workers', [])
    if not isinstance(bindings, list):
        raise ValueError('Accounted peer publication workers need an explicit binding list')
    required = {'Owner', 'JobUniverse', 'Iwd', 'Cmd', 'Args', 'DAGManJobId', 'ShiftBSMPoint'}
    names, identities = set(), set()
    for binding in bindings:
        if not isinstance(binding, dict):
            raise ValueError('Invalid accounted peer publication binding')
        expected = binding.get('expected_native', {})
        name, campaign = binding.get('name'), binding.get('accounted_campaign')
        if (not plain(name) or name in names or not plain(campaign)
                or not isinstance(expected, dict) or not required.issubset(expected)
                or set(expected) - set(ATTRIBUTES)
                or expected.get('JobUniverse') != 5
                or not isinstance(expected.get('Owner'), str) or not expected['Owner']
                or not isinstance(expected.get('Args'), str)
                or not plain(expected.get('ShiftBSMPoint'))
                or type(expected.get('DAGManJobId')) is not int or expected['DAGManJobId'] <= 0
                or any(not isinstance(expected.get(key), str)
                       or not Path(expected[key]).is_absolute() or '..' in Path(expected[key]).parts
                       for key in ('Iwd', 'Cmd'))):
            raise ValueError('Accounted publication worker lacks its exact DAG, point, launch or campaign')
        if capacity is not None and campaign not in capacity.get('active_campaign_names', []):
            raise ValueError('Accounted publication worker is outside the active storage campaigns: ' + campaign)
        identity = tuple(expected[key] for key in sorted(required))
        if identity in identities:
            raise ValueError('Duplicate accounted peer publication worker identity')
        verify_pinned_peer_launch(expected, binding.get('frozen_input_sha256'), 'Accounted publication worker')
        names.add(name)
        identities.add(identity)
    return bindings


def verify_stream_peer_writers(policy, observation, ads):
    """Verify the native writers covered by the shared QCD allowance."""
    capacity = observation.get('external_storage_commitments', {}).get('stream_capacity')
    if capacity is None:
        return observation
    roots = capacity['allowed_bsm_directories']
    bindings = capacity.get('controller_bindings', [])
    absent = set(capacity['required_absent_stream_campaigns'])
    if (not roots or any(not isinstance(root, str) or not Path(root).is_absolute() for root in roots)
            or not isinstance(observation.get('remote_account_peer_writer_rows'), list)):
        raise ValueError('Shared peer writer authority is incomplete')
    result = dict(observation, stream_peer_writers_verified=False)
    nonpublishing = nonpublishing_peer_bindings(policy)
    publications = accounted_publication_bindings(policy, capacity)
    count = 0
    deferred_writers = 0
    verified_nonpublishing = []
    verified_publications = []
    rows = [(ad, False) for ad in ads] + [(ad, True) for ad in observation['remote_account_peer_writer_rows']]
    for ad, remote in rows:
        if ad.get('JobUniverse') != 5 or ad.get('JobStatus') not in (1, 2, 5, 6, 7):
            continue
        tag = ad.get('ShiftStreamCampaign')
        if remote and tag == policy['tag']:
            return result | {'stream_peer_writer_error': 'This stream has an unexpected writer on a remote scheduler'}
        if tag in absent or (tag and tag != policy['tag']) or ad.get('ShiftMuEnrichedQCD') is True:
            return result | {'stream_peer_writer_error': 'An old or unreviewed SM writer remains queued'}
        peers = [binding for binding in nonpublishing
                 if ((binding['audited_purpose'] in READONLY_COMPANIONS
                      and ad.get('Iwd') == binding['expected_native']['Iwd'])
                     or (binding['audited_purpose'] not in READONLY_COMPANIONS
                         and (ad.get('ClusterId'), ad.get('ProcId')) ==
                             (binding['expected_native']['ClusterId'], binding['expected_native']['ProcId'])))]
        if peers:
            if (not any(all(ad.get(key) == value for key, value in binding['expected_native'].items())
                        for binding in peers)
                    or ad.get('ShiftStreamTask') or ad.get('ShiftBSMPoint')
                    or ad.get('ShiftSignalValidationOnly') is True or ad.get('DAGManJobId')
                    or peers[0]['name'] in verified_nonpublishing):
                return result | {'stream_peer_writer_error': 'Nonpublishing peer native identity or audited role changed'}
            verified_nonpublishing.append(peers[0]['name'])
            continue
        publication_peers = [binding for binding in publications
            if ad.get('Iwd') == binding['expected_native']['Iwd']
            or ad.get('DAGManJobId') == binding['expected_native']['DAGManJobId']]
        publication = next((binding for binding in publication_peers
            if all(ad.get(key) == value for key, value in binding['expected_native'].items())), None)
        if publication_peers and publication is None:
            return result | {'stream_peer_writer_error': 'Accounted publication worker DAG, point or launch changed'}
        directory = ad.get('Iwd') or ''
        batch = ad.get('JobBatchName') or ''
        arguments = ad.get('Args') or ''
        bsm = (ad.get('ShiftSignalValidationOnly') is True or ad.get('ShiftBSMPoint')
               or ad.get('ShiftDPStatisticsTask') or 'BSM' in batch
               or '/bsm_' in directory)
        if bsm:
            controller = ('supervisor' in batch.lower() or '/supervisor' in directory
                          or '--config ' in arguments or '--controller-config ' in arguments
                          or '--action controller' in arguments)
            if controller and not any(all(ad.get(key) == value for key, value in binding['expected_native'].items())
                                      for binding in bindings):
                return result | {'stream_peer_writer_error': 'An unreviewed BSM controller is not bound to its pinned guard'}
            if not controller and any(directory == root or directory.startswith(root.rstrip('/') + '/')
                                      for root in capacity.get('deferred_bsm_directories', [])):
                deferred_writers += 1
            if (not directory or '..' in Path(directory).parts
                    or (publication is None and not any(directory == root or directory.startswith(root.rstrip('/') + '/')
                                                        for root in roots))):
                return result | {'stream_peer_writer_error': 'An unreviewed BSM writer is outside the pinned peers'}
            count += 1
            if publication is not None:
                verified_publications.append(publication['name'])
    return result | {'stream_peer_writers_verified': True, 'verified_BSM_writers': count,
                     'stream_requires_full_peer_mode': deferred_writers > 0,
                     'verified_deferred_science_writers': deferred_writers,
                     'verified_nonpublishing_peer_controllers': verified_nonpublishing,
                     'verified_accounted_peer_publication_workers': verified_publications}


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
    return _exit_event_blocks(Path(path).read_text().split('\n...\n')[:-1], cluster, process)


def _exit_event_blocks(blocks, cluster, process):
    """Read a retained complete event prefix without changing its log file."""
    result = {'executed': False, 'terminal': None}
    for block in blocks:
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


def isolated_detector_failure(task, proof):
    """Recognize a bounded detector crash after the exact GEN slice was checked.

    Configuration, identity/weight audits, publication, credentials, unknown
    exits and missing reports remain global faults. Only a native nonzero
    detector execution or timeout may quarantine one source without a retry.
    """
    if (task['stage'] != 'reco' or proof.get('executed') is not True
            or type(proof.get('terminal')) is not int or proof['terminal'] == 0):
        return False
    path = Path(task.get('result_path', Path(task['working_directory'])/'result.json'))
    if not path.is_file():
        return False
    report = read(path)
    checks = task['completion_checks']
    if (any(key not in checks for key in ('events', 'source_descriptor_sha256', 'selected_event_ids_sha256'))
            or report.get('schema') != 'shift-gen-to-nano-receipt-v1'
            or report.get('complete') is not False
            or report.get('current_tier') not in ('SIM', 'DIGIHLT', 'RECO', 'NANO')
            or report.get('validated_tier_events', {}).get('GEN') != checks.get('events')
            or any(report.get(key) != checks.get(key) for key in (
                'events', 'source_descriptor_sha256', 'selected_event_ids_sha256'))):
        return False
    error = report.get('error', '')
    return (error.startswith('TimeoutError(')
            or (error.startswith(('TimeoutExpired(', 'CalledProcessError(')) and 'cmsRun' in error))


def held_detector_exit(task, record, ad, policy):
    """Prove an unsuccessful OnExitHold exit, which has no 005 log event.

    A generic hold is not an exit. Require the exact native job, a matching
    OnExitHold event and the worker's independently checked GEN-slice receipts.
    Unknown, signalled, manual and publication failures stay global faults.
    """
    path = Path(task['event_log'])
    blocks = path.read_text().split('\n...\n')[:-1] if path.is_file() else []
    return _held_detector_exit_blocks(task, record, ad, policy, blocks)


def _held_detector_exit_blocks(task, record, ad, policy, blocks):
    """Apply the unchanged held-exit requirements to an explicit event prefix."""
    pair = [ad.get('ClusterId'), ad.get('ProcId')]
    if (task['stage'] != 'reco' or pair != record.get('job')
            or ad.get('Owner') != policy['admission']['account_owner']
            or ad.get('JobUniverse') != 5 or ad.get('JobStatus') != 5
            or ad.get('Iwd') != task['working_directory']
            or ad.get('ShiftStreamCampaign') != policy['tag']
            or ad.get('ShiftStreamTask') != task['task_id']
            or not record.get('nonce') or ad.get('ShiftStreamNonce') != record['nonce']
            or ad.get('ExitBySignal') is not False
            or type(ad.get('ExitCode')) is not int or ad['ExitCode'] <= 0
            or ad.get('HoldReasonCode') != 3 or ad.get('HoldReasonSubCode') != 0
            or 'OnExitHold' not in ad.get('HoldReason', '')):
        return None
    global_id = ad.get('GlobalJobId', '')
    if (not isinstance(global_id, str)
            or not re.fullmatch(r'[^#]+#'+str(pair[0])+r'\.'+str(pair[1])+r'#\d+', global_id)
            or record.get('queue', {}).get('GlobalJobId', global_id) != global_id):
        return None
    submit = {}
    for line in Path(task['submit_file']).read_text().splitlines():
        key, separator, value = line.partition('=')
        if separator:
            submit[key.strip().lower()] = value.strip()
    if (ad.get('Cmd') != submit.get('executable')
            or not ad.get('Cmd') or ad.get('Args') != submit.get('arguments')):
        return None
    proof = _exit_event_blocks(blocks, *pair)
    if proof != {'executed': True, 'terminal': None}:
        return None
    last_control_event = None
    for block in blocks:
        match = re.match(r'\s*(\d{3}) \((\d+)\.(\d+)\.\d+\)', block)
        if match and (int(match[2]), int(match[3])) == tuple(pair):
            if int(match[1]) in (1, 5, 9, 12, 13):
                last_control_event = block
    if (last_control_event is None or not last_control_event.lstrip().startswith('012 ')
            or 'OnExitHold' not in last_control_event
            or not re.search(r'Code\s+3\s+Subcode\s+0\b', last_control_event)):
        return None
    result = Path(task.get('result_path', Path(task['working_directory'])/'result.json'))
    status_path = Path(task['status_path'])
    payload = Path(task['working_directory'])/'return_payload.tar.gz'
    if not all(path.is_file() for path in (result, status_path, payload)):
        return None
    report, status = read(result), read(status_path)
    checks = task['completion_checks']
    if (report.get('exit_code') != ad['ExitCode'] or status.get('exit_code') != ad['ExitCode']
            or status.get('complete') is not False or status.get('stage') != 'reco'
            or status.get('cohort_id') != task['cohort_id']
            or any(status.get(key) != report.get(key) for key in (
                'events', 'source_descriptor_sha256', 'selected_event_ids_sha256',
                'validated_tier_events', 'error'))
            or any(status.get(key) != checks.get(key) for key in (
                'events', 'source_descriptor_sha256', 'selected_event_ids_sha256'))
            or status.get('return_payload_sha256') != sha(payload)):
        return None
    proof = dict(executed=True, terminal=ad['ExitCode'], held_on_exit=True,
                 native_job=pair, global_job_id=global_id, event_log_sha256=sha(task['event_log']))
    return proof if isolated_detector_failure(task, proof) else None


def _review_removed_held_detector_exit(task, record, policy, history_pin, now):
    """Cold-only proof that the site removed an already failed, unreleased job."""
    if (not isinstance(history_pin, dict) or set(history_pin) != {'path', 'sha256'}
            or not isinstance(history_pin['path'], str)
            or not Path(history_pin['path']).is_absolute()
            or not isinstance(history_pin['sha256'], str)
            or not re.fullmatch('[0-9a-f]{64}', history_pin['sha256'])
            or sha(history_pin['path']) != history_pin['sha256']):
        raise ValueError('Removed held-exit review needs its pinned native history observation')
    snapshot = read(history_pin['path'])
    observed = snapshot.get('checked_at_epoch')
    if observed is None:
        stamp = datetime.fromisoformat(str(snapshot.get('checked_at_utc', '')).replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            raise ValueError('Native history observation needs an explicit timezone')
        observed = stamp.timestamp()
    command, pair = snapshot.get('command'), record.get('job')
    identity = '%d.%d' % tuple(pair)
    if (snapshot.get('known') is not True
            or type(observed) not in (int, float) or not math.isfinite(observed)
            or not 0 <= now-observed <= 60
            or not isinstance(command, list) or not command or command[0] != '/usr/bin/condor_history'
            or identity not in command or '-json' not in command
            or len([value for value in command if re.fullmatch(r'\d+\.\d+', str(value))]) != 1
            or not any(option in command and command.index(option)+1 < len(command)
                       and command[command.index(option)+1] == '1' for option in ('-limit', '-match'))
            or any(option in command for option in ('-constraint', '-all'))
            or not isinstance(snapshot.get('rows'), list) or len(snapshot['rows']) != 1):
        raise ValueError('Removed held-exit review needs a fresh exact native history query')
    historical, held = snapshot['rows'][0], record.get('queue', {})
    reason = 'Job removed by SYSTEM_PERIODIC_REMOVE due to wall time exceeded allowed max.'
    fields = ('ClusterId', 'ProcId', 'GlobalJobId', 'Owner', 'JobUniverse', 'Iwd', 'Cmd', 'Args',
              'ShiftStreamCampaign', 'ShiftStreamTask', 'ShiftStreamNonce', 'ExitCode',
              'ExitBySignal', 'QDate', 'NumJobStarts')
    if (historical.get('JobStatus') != 3 or historical.get('LastJobStatus') != 5
            or historical.get('RemoveReason') != reason
            or any(key not in held or key not in historical or historical[key] != held[key]
                   or type(historical[key]) is not type(held[key]) for key in fields)
            or type(held.get('NumJobStarts')) is not int or held['NumJobStarts'] < 1):
        raise ValueError('Removed job differs from its original unsuccessful held execution')
    text = Path(task['event_log']).read_text()
    blocks = text.split('\n...\n')[:-1]
    controls = []
    for index, block in enumerate(blocks):
        match = re.match(r'\s*(\d{3}) \((\d+)\.(\d+)\.\d+\)', block)
        if match and (int(match[2]), int(match[3])) == tuple(pair) and int(match[1]) in (1, 4, 5, 9, 12, 13):
            controls.append((index, int(match[1]), block))
    if (len(controls) < 3 or [row[1] for row in controls[-3:]] != [1, 12, 9]
            or reason not in controls[-1][2] or 'OnExitHold' not in controls[-2][2]
            or controls[-1][0] != len(blocks)-1):
        raise ValueError('Removed job lacks the exact held-then-site-removal event sequence')
    prefix = blocks[:controls[-1][0]]
    proof = _held_detector_exit_blocks(task, record, held, policy, prefix)
    if proof is None:
        raise ValueError('Removed job lacks the original proven held detector failure')
    proof.update(removed_after_held_exit=True, removal_reason=reason,
        held_event_prefix_sha256=hashlib.sha256(('\n...\n'.join(prefix)+'\n...\n').encode()).hexdigest(),
        native_history_path=history_pin['path'], native_history_sha256=history_pin['sha256'],
        native_history_checked_at_epoch=observed)
    return proof, historical, held


def review_failed_cohort_reclassification(root, policy, tasks, ledger, native_ads,
                                        expected_sha256, checked_at_epoch, now=None,
                                        removed_job_history=None):
    """Build an explicit cold recovery receipt without writing or retrying work.

    The caller must first stop the old writer and obtain a fresh native account
    query. Every selected failure and its empty or nonempty return archive must
    be pinned by the reviewer. Only its scope changes; phase remains failed.
    A separately pinned exact history observation may prove a site-removed
    already-held failure. This exception never applies to normal reconciliation.
    """
    now = time.time() if now is None else now
    if (type(checked_at_epoch) not in (int, float) or not math.isfinite(checked_at_epoch)
            or not 0 <= now-checked_at_epoch <= 60
            or policy.get('isolate_failed_cohorts') is not True
            or not isinstance(native_ads, list)):
        raise ValueError('Cold failure review needs a fresh known native observation')
    if any(ad.get('ShiftStreamCampaign') == policy['tag']
           and (ad.get('ShiftStreamController') is True or ad.get('JobUniverse') == 7)
           for ad in native_ads):
        raise ValueError('Stop the old stream controller before cold failure review')
    root = Path(root)
    ledger_path = root/'ledger.json'
    if (set(expected_sha256) != {'ledger', 'tasks'}
            or not expected_sha256['tasks'] or sha(ledger_path) != expected_sha256['ledger']
            or read(ledger_path) != ledger):
        raise ValueError('Cold failure review ledger differs from the reviewed snapshot')
    updated, reviewed = copy.deepcopy(ledger), {}
    removed_job_history = {} if removed_job_history is None else removed_job_history
    if (not isinstance(removed_job_history, dict)
            or set(removed_job_history) - set(expected_sha256['tasks'])):
        raise ValueError('Removed-job history must name only explicitly reviewed failures')
    for identity, pins in expected_sha256['tasks'].items():
        task, record = tasks[identity], ledger[identity]
        directory = Path(task['working_directory'])
        paths = dict(task_manifest=root/'tasks'/(identity+'.json'),
            result=Path(task.get('result_path', directory/'result.json')),
            status=Path(task['status_path']), event_log=Path(task['event_log']),
            return_payload=directory/'return_payload.tar.gz')
        if (set(pins) != set(paths) or record.get('phase') != 'failed'
                or record.get('failure_scope') != 'global'
                or any(sha(path) != pins[key] for key, path in paths.items())
                or pins['task_manifest'] != record.get('task_sha256')
                or read(paths['task_manifest']) != {key: value for key, value in task.items()
                    if key not in ('manifest_sha256', 'manifest_path')}
                or any(sha(path) != digest for path, digest in task['input_sha256'].items())):
            raise ValueError('Cold failure review evidence changed: '+identity)
        matches = [ad for ad in native_ads
                   if [ad.get('ClusterId'), ad.get('ProcId')] == record.get('job')]
        historical, cached_held = None, None
        if identity in removed_job_history:
            if matches or any(ad.get('ShiftStreamCampaign') == policy['tag']
                              and ad.get('ShiftStreamTask') == identity for ad in native_ads):
                raise ValueError('Removed-job review found the original job or a replacement in the fresh queue')
            proof, historical, cached_held = _review_removed_held_detector_exit(
                task, record, policy, removed_job_history[identity], now)
        else:
            proof = held_detector_exit(task, record, matches[0], policy) if len(matches) == 1 else None
        if proof is None:
            raise ValueError('Cold failure review lacks a proven held detector exit: '+identity)
        updated[identity].update(failure_scope='cohort', failed_result_path=str(paths['result']),
            failed_result_sha256=pins['result'], native_exit=proof,
            reviewed_failure_reclassification_at_epoch=now)
        reviewed[identity] = dict(expected_sha256=pins, native_ad=historical if historical is not None else matches[0], native_exit=proof,
            original_record=record, reviewed_record=updated[identity])
        if historical is not None:
            reviewed[identity].update(cached_original_held_ad=cached_held,
                                     native_history=copy.deepcopy(removed_job_history[identity]))
    receipt = dict(schema='shift-stream-reviewed-failure-quarantine-v1', complete=True,
        checked_at_epoch=checked_at_epoch, reviewed_at_epoch=now,
        original_ledger_sha256=expected_sha256['ledger'], tasks=reviewed,
        task_phases_changed=False, scientific_retries=0, analysis_release=False)
    return updated, receipt


def quarantined_tasks(tasks, ledger):
    """Keep failed sources and every downstream snapshot out of ready work."""
    cohorts = {tasks[identity]['cohort_id'] for identity, record in ledger.items()
               if record.get('phase') == 'failed' and record.get('failure_scope') == 'cohort'}
    blocked = {identity for identity, task in tasks.items() if task['cohort_id'] in cohorts}
    while True:
        downstream = {identity for identity, task in tasks.items()
                      if any(dependency in blocked for dependency in task['depends_on'])}
        added = downstream - blocked
        if not added:
            return cohorts, blocked
        blocked.update(added)


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
        revision = self.policy.get('selection_revision')
        if revision is not None:
            if (revision.get('schema') != 'shift-unselected-source-sampling-revision-v1'
                    or revision.get('existing_selections_unchanged') is not True
                    or sha(self.root/'factory_policy.json') != revision.get('previous_factory_sha256')
                    or sha(self.root/'source_plan.json') != revision.get('source_plan_sha256')):
                raise ValueError('Sampling revision changed its original source or factory authority')
            replacement = revision['factory_policy']
            if (sha(replacement['path']) != replacement['sha256']
                    or replacement['path'] not in self.policy.get('prepare_command', [])):
                raise ValueError('Sampling revision needs the exact frozen preparer policy')
            from shift_streaming_qcd_inputs import validate_policy as validate_selection_policy
            validate_selection_policy(read(replacement['path'])['selection_policy'])
        self.reader, self.commander = reader, commander
        self.gen_publication_cache = {}
        self.admission_policy = stream_admission_policy(self.policy)
        stage_publication_bounds(self.policy, self.admission_policy)
        nonpublishing_peer_bindings(self.policy)
        accounted_publication_bindings(self.policy)
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
                if (record.get('failure_scope') == 'cohort'
                        and sha(record['failed_result_path']) != record['failed_result_sha256']):
                    raise ValueError('Quarantined failure evidence changed: ' + identity)
                continue
            matches = [ad for ad in ads if ad.get('ShiftStreamCampaign') == self.policy['tag']
                       and ad.get('ShiftStreamTask') == identity
                       and ad.get('ShiftStreamNonce') == record['nonce']]
            if len(matches) > 1:
                raise ValueError('Duplicate streaming task execution identity: ' + identity)
            issue = None
            if matches:
                ad = matches[0]
                pair = [ad['ClusterId'], ad['ProcId']]
                if record.get('job') not in (None, pair):
                    raise ValueError('Streaming task changed native job identity')
                record.update(job=pair, phase='submitted', queue=ad)
                if ad.get('Iwd') != task['working_directory']:
                    problems.append(identity + ': queued Iwd differs from persistent task directory')
                issue = problem(ad, now, self.policy['worker_limits'])
            pair = record.get('job')
            proof = exit_event(task['event_log'], *pair) if pair else {'executed': False, 'terminal': None}
            if proof['executed']:
                record['execution_verified'] = True
            if matches and self.policy.get('isolate_failed_cohorts') is True:
                held_proof = held_detector_exit(task, record, matches[0], self.policy)
                if held_proof is not None:
                    proof = held_proof
            if proof['terminal'] not in (None, 0):
                isolate = (self.policy.get('isolate_failed_cohorts') is True
                           and isolated_detector_failure(task, proof))
                record.update(phase='failed', error='Native unsuccessful exit: ' + str(proof['terminal']),
                              failure_scope='cohort' if isolate else 'global')
                if isolate:
                    result = Path(task.get('result_path', Path(task['working_directory'])/'result.json'))
                    record.update(failed_result_path=str(result), failed_result_sha256=sha(result),
                                  native_exit=proof)
                else:
                    problems.append(identity + ': ' + record['error'])
            elif issue:
                record.update(phase='failed', error=issue, failure_scope='global')
                problems.append(identity + ': ' + issue)
            if record['phase'] == 'failed':
                continue
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
        fixed_test_clock = now is not None
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
        # A peer-only adoption leaves the original task/PRE ledger intact.
        # Its new audit policy must be used by a real fresh quota worker.
        admission_root = self.root / 'admission'
        quota_root = (self.supervision_root if (self.policy.get('additional_peer_meson_campaigns')
                      or self.policy.get('peer_storage_authority') is not None)
                      else self.root) / 'admission'
        quota = refresh_quota(quota_root) if not dry_run else read(quota_root / 'last_quota.json')
        # Preparation and credentialed audits may take minutes. Admission must
        # use a queue snapshot and clock read after that work, not step start.
        ads = self.observe()
        if not fixed_test_clock:
            now = time.time()
        problems.extend(self.reconcile(tasks, ads, now))
        quota = verify_stream_peer_writers(self.policy, quota, ads)
        passed = operational_gates(self.policy)
        quarantined_cohorts, blocked = quarantined_tasks(tasks, self.ledger)
        candidates = [task for identity, task in tasks.items()
            if self.ledger[identity]['phase'] == 'waiting' and dependencies_complete(task, self.ledger)
            and identity not in blocked and (passed or task.get('operational_canary') is True)]
        candidates.sort(key=lambda task: task_scheduling_key(task, self.policy))
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
        submitted_tasks = set()
        if any(r['phase'] == 'failed' and r.get('failure_scope') != 'cohort'
               for r in self.ledger.values()):
            problems.append('A scientific task failed; preserve its cohort and do not omit it from rates')
        if not problems and not (self.root / 'stop_requested.json').exists():
            for task in candidates[:self.policy.get('maximum_submissions_per_poll', 4)]:
                if not fixed_test_clock:
                    now = time.time()
                idle_capacity = idle_worker_capacity(self.policy, ads, reservations,
                    self.ledger, task['task_id'], submitted_tasks)
                if idle_capacity is not None and not idle_capacity['admitted']:
                    capacity = idle_capacity
                    break
                stage_quota = reserve_stage_publications(self.policy, self.admission_policy, quota,
                    tasks, ads, reservations, task['task_id'])
                capacity = decision(self.admission_policy, stage_quota,
                    mapped_ads(ads, self.policy['tag']), reservations, task['task_id'], now,
                    afs_headroom(self.root))
                if self.policy.get('stage_publication_bounds'):
                    capacity['extra_stage_reserved_bytes'] = stage_quota['extra_stage_reserved_bytes']
                if idle_capacity is not None:
                    capacity['idle_capacity'] = idle_capacity
                reservations = capacity.pop('reservations', reservations)
                if not dry_run:
                    save(reservations_path, reservations)
                if not capacity['admitted']:
                    break
                if not dry_run:
                    self.submit(task, now)
                submitted += 1
                submitted_tasks.add(task['task_id'])
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
            'quarantined_cohorts': sorted(quarantined_cohorts), 'quarantined_tasks': len(blocked),
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
