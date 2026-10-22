#!/usr/bin/env python3
# Code style: AI-code. AI-generated fixed-M meson GEN and production supervisor.
"""Keep fixed source M separate from random signal N through published plots.

Each source runs once, with its complete ledger retained on EOS. A successful
audit determines the detector budget afterwards. Both operational canaries
must reach merged histograms and publication before bulk source admission.
"""
import argparse
import copy
from datetime import datetime, timezone
import fcntl
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
import traceback

import run_dark_photon_statistics as durable


MECHANISMS = ('eta-etaprime-radiative', 'meson-radiative')


class MesonJobFailure(RuntimeError):
    """A verified native failure belongs to one independent production batch."""

    def __init__(self, message, jobs):
        super().__init__(message)
        self.jobs = jobs


def read(path):
    return json.loads(Path(path).read_text())


def pinned_path(authority):
    path = Path(authority['path'])
    if not path.is_absolute() or durable.sha(path) != authority['sha256']:
        raise ValueError('Meson recovery or reservation authority changed: '+str(path))
    return path


def read_pinned(authority):
    return read(pinned_path(authority))


def source_mechanism(config):
    mechanism = config.get('production_mechanism', 'eta-etaprime-radiative')
    if mechanism not in MECHANISMS:
        raise ValueError('Unsupported meson source mechanism')
    return mechanism


def prior_meson_configuration(config):
    """The previous model owns each point until all its batches close."""
    if source_mechanism(config) != 'meson-radiative':
        return None
    previous = read_pinned(config['prior_meson_publication'])
    directory = Path(previous.get('directory', ''))
    batches = previous.get('batches')
    if (previous.get('schema') != 'shift-dp-meson-production-v1'
            or not directory.is_absolute() or '..' in directory.parts
            or not isinstance(batches, list) or not batches
            or any(not isinstance(row, dict) or not row.get('batch') or not row.get('point') for row in batches)
            or len({row['batch'] for row in batches}) != len(batches)):
        raise ValueError('Prior meson publication requires its pinned original production')
    if source_mechanism(previous) == 'meson-radiative':
        # A changed channel model replaces its predecessor. It is never another
        # population of the old proposal for cross-section averaging.
        for candidate in (previous, config):
            pinned_path(candidate['conditions'])
        if previous['conditions']['sha256'] != config['conditions']['sha256']:
            raise ValueError('Prior meson model has different detector conditions')
        completion_scope(previous)
        scope = read_pinned(previous['meson_completion_scope'])
        if scope.get('model_sha256') != durable.sha(previous['model_card']):
            raise ValueError('Prior meson model differs from its pinned coverage audit')
        gen_runtime(previous)
        old_seeds = {row['seed'] for row in batches}
        old_runs = {row['run_number'] for row in batches}
        for row in config['batches']:
            if row['seed'] in old_seeds or row['run_number'] in old_runs:
                raise ValueError('Replacement meson sources reuse prior seeds or runs')
            expected = reference_point(previous, row)
            actual = reference_point(config, row)
            if expected != actual:
                raise ValueError('Replacement meson model changed its physical point or LAB reference')
    return previous


def prior_publication_ready(config, batch):
    """Check fresh prior ownership without granting credit for another point."""
    previous = prior_meson_configuration(config)
    if previous is None:
        return True, {}
    root = Path(previous['directory'])
    expected = [row for row in previous['batches'] if row['point'] == batch['point']]
    evidence = dict(point=batch['point'], prior_directory=str(root),
        prior_config_sha256=config['prior_meson_publication']['sha256'],
        required_batches=[row['batch'] for row in expected], verified_batches=[],
        allowed_previous_campaigns=[])
    if not expected:
        return False, dict(evidence, reason='prior_point_is_not_declared')
    try:
        if config.get('reviewed_prior_meson_retirement'):
            retired = reviewed_meson_retirement(config, config['reviewed_prior_meson_retirement'],
                                                config['prior_meson_publication'])
            return True, dict(evidence, complete=True, reviewed_retirement_verified=True,
                allowed_previous_campaigns=retired['campaigns'].get(batch['point'], []),
                retirement_authority=config['reviewed_prior_meson_retirement'])
        state_bytes = (root/'state.json').read_bytes()
        state = json.loads(state_bytes)
        age = time.time()-datetime.fromisoformat(state['checked_utc']).timestamp()
        terminal = state.get('complete') is True
        if state.get('error') or (not terminal and not 0 <= age < 1200):
            return False, dict(evidence, reason='prior_state_is_unhealthy_or_stale', state_age_seconds=age)
        evidence['prior_state_sha256'] = hashlib.sha256(state_bytes).hexdigest()
        evidence['prior_checked_utc'] = state['checked_utc']
        required = previous['batches'] if terminal else expected
        for row in required:
            published = state.get('batches', {}).get(row['batch'], {}).get('published')
            if not isinstance(published, dict) or published.get('batch') != row['batch']:
                return False, dict(evidence, reason='prior_batch_is_not_published', pending_batch=row['batch'])
            receipt = Path(published.get('point_receipt_path', ''))
            if (not receipt.is_absolute() or '..' in receipt.parts or not receipt.is_relative_to(root)
                    or receipt.name != row['point']+'.json'
                    or durable.sha(receipt) != published.get('point_receipt_sha256')):
                return False, dict(evidence, reason='prior_point_receipt_hash_or_ownership_changed', pending_batch=row['batch'])
            report = read(receipt)
            histogram = report.get('score_histograms', {}).get('marker', {}).get('published', {}).get('histograms', {})
            histogram_path = Path(published.get('histogram_path', ''))
            campaign = published.get('campaign')
            if (report.get('complete') is not True or report.get('point') != row['point']
                    or report.get('events') != published.get('events')
                    or report.get('score_histograms', {}).get('marker', {}).get('complete') is not True
                    or histogram.get('path') != published.get('histogram_path')
                    or histogram.get('sha256') != published.get('histogram_sha256')
                    or not isinstance(campaign, str) or not campaign
                    or histogram_path.parent.name != 'histograms_merged'
                    or histogram_path.parent.parent.name != campaign
                    or histogram_path.parent.parent.parent.name != 'darkphoton_'+row['point']
                    or report.get('final_output_base') != str(histogram_path.parent.parent)):
                return False, dict(evidence, reason='prior_point_report_is_incomplete_or_different', pending_batch=row['batch'])
            evidence['verified_batches'].append(dict(batch=row['batch'],
                point=row['point'], campaign=campaign,
                point_receipt_path=str(receipt), point_receipt_sha256=published['point_receipt_sha256']))
            if row['point'] == batch['point'] and campaign not in evidence['allowed_previous_campaigns']:
                evidence['allowed_previous_campaigns'].append(campaign)
        if terminal:
            authority = config['prior_meson_publication']['controller_receipt']
            replacement = config.get('prior_meson_controller_replacement')
            future = config.get('prior_meson_controller_directory')
            expected_native = None
            if future is not None:
                # The successor registers this source's final config checksum.
                # Pin its directory instead of creating a checksum cycle, then
                # check its seal, unchanged physics and actual native identity.
                if replacement:
                    raise ValueError('Predecessor controller has competing ownership authorities')
                owner = predecessor_controller_configuration(config, future)
                directory, submit_path = Path(future), Path(owner['submit'])
                receipt_path = directory/'submission.json'
                if receipt_path.resolve() != receipt_path:
                    raise ValueError('Future predecessor submission leaves its exact directory')
                receipt = read(receipt_path)
                if receipt.get('task') != str(directory) or receipt.get('submit_sha256') != durable.sha(submit_path):
                    raise ValueError('Future predecessor submission receipt has different ownership')
                receipt_hash = durable.sha(receipt_path)
                expected_native = owner['native_execution']
            elif replacement:
                # Preserve the original physics authority, but follow the exact
                # qualified supervisor that now closes its publication work.
                path = pinned_path(replacement['config'])
                seal_path = pinned_path(replacement['seal'])
                submit_path = pinned_path(replacement['submit'])
                if seal_path != path.parent/'seal.json' or submit_path != path.parent/'controller.sub':
                    raise ValueError('Prior meson replacement is outside its exact sealed deployment')
                operational_configuration(previous, path, replacement['seal']['sha256'])
                receipt_path = path.parent/'submission.json'
                receipt = read(receipt_path)
                if receipt.get('task') != str(path.parent) or receipt.get('submit_sha256') != replacement['submit']['sha256']:
                    raise ValueError('Prior meson replacement submission identity changed')
                receipt_hash = durable.sha(receipt_path)
            else:
                receipt = read_pinned(authority)
                receipt_hash = authority['sha256']
            if (type(receipt.get('cluster')) is not int or receipt['cluster'] <= 0
                    or not Path(receipt.get('task', '')).is_absolute()
                    or durable.sha(Path(receipt['task'])/'controller.sub') != receipt.get('submit_sha256')):
                return False, dict(evidence, reason='prior_terminal_controller_identity_changed')
            constraint = ('ClusterId == '+str(receipt['cluster'])+
                ' || DAGManJobId == '+str(receipt['cluster'])+' || Iwd == '+json.dumps(receipt['task']))
            if durable.jobs(config, constraint) or native_status(config, receipt) != 'complete':
                return False, dict(evidence, reason='prior_terminal_controller_is_still_live')
            if expected_native is not None:
                terminal_record = cached_terminal_record(config, receipt)
                native = terminal_record.get('terminal_native_ad', {}) if terminal_record else {}
                if any(native.get(key) != value for key, value in expected_native.items()):
                    return False, dict(evidence, reason='prior_terminal_controller_execution_identity_changed')
            evidence.update(terminal_closure_verified=True,
                prior_controller_receipt_sha256=receipt_hash, prior_controller_cluster=receipt['cluster'])
    except (OSError, KeyError, TypeError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        return False, dict(evidence, reason='prior_publication_evidence_unavailable', error=repr(error))
    return True, dict(evidence, complete=True)


def reviewed_meson_retirement(config, authority, expected, check_queue=True):
    """Review a stopped allocation without calling its partial output complete."""
    proof = read_pinned(authority)
    expected_config = read_pinned(expected)
    canonical = Path(expected_config['directory'])/'config.json'
    if durable.sha(canonical) != expected['sha256']:
        raise ValueError('Copied predecessor authority differs from its exact canonical science')
    expected = dict(path=str(canonical), sha256=expected['sha256'])
    replacement = read_pinned(proof['replacement_configuration'])
    if (proof.get('schema') != 'shift-reviewed-meson-retirement-v1'
            or proof.get('allocation_canceled') is not True
            or proof['replacement_configuration']['path'] != str(Path(config['directory'])/'config.json')
            or replacement.get('directory') != config['directory']):
        raise ValueError('Meson retirement does not name this exact replacement allocation')
    productions = proof.get('productions', [])
    roots, owners, submitted, campaigns, published_batches = set(), set(), {}, {}, {}
    owner_execution = {}
    found = False
    for entry in productions:
        previous = read_pinned(entry['configuration'])
        root = Path(previous['directory'])
        seal = read_pinned(entry['seal'])
        if (entry['configuration']['path'] != str(root/'config.json')
                or entry['seal']['path'] != str(root/'seal.json')
                or seal.get('files', {}).get(str(root/'config.json')) != entry['configuration']['sha256']
                or not root.is_absolute() or root.resolve() != root or str(root) in roots
                or str(root) == config['directory']):
            raise ValueError('Retirement repeats or changes a frozen original production')
        roots.add(str(root))
        found |= all(entry['configuration'].get(key) == expected.get(key) for key in ('path', 'sha256'))
        final = entry.get('final_state')
        state = read_pinned(final) if final else {}
        if ((root/'state.json').is_file() != bool(final)
                or (final and durable.sha(root/'state.json') != final['sha256'])):
            raise ValueError('Retired production differs from its frozen final state')
        declared = {row['batch']: row for row in previous['batches']}
        if not set(state.get('batches', {})).issubset(declared):
            raise ValueError('Retired final state contains an undeclared source batch')
        records = entry.get('batches', [])
        if len(records) != len(declared) or {row['batch'] for row in records} != set(declared):
            raise ValueError('Retirement omits or repeats a source batch')
        published_batches[str(root)] = []
        for record in records:
            batch = declared[record['batch']]
            directory = root/'batches'/batch['batch']
            status_path = directory/'source_status.json'
            status_pin = record.get('source_status')
            source = read_pinned(status_pin) if status_pin else {}
            if status_path.is_file() != bool(status_pin) or (status_pin and status_pin['path'] != str(status_path)):
                raise ValueError('Retirement omitted the exact source status')
            if source and (source.get('batch') != batch['batch'] or source.get('point') != batch['point']
                           or source.get('source_collisions') != batch['source_collisions']):
                raise ValueError('Retirement changed source exposure or point identity')
            retained = {key: source[key] for key in ('ledger_publication', 'metadata_archive', 'publication') if key in source}
            if record.get('retained_source', {}) != retained:
                raise ValueError('Retirement omitted original GEN, ledger or metadata publication receipts')
            statuses = []
            for pin in record.get('additional_statuses', []):
                if not Path(pin['path']).is_relative_to(directory):
                    raise ValueError('Retirement status leaves its source batch')
                statuses.append(read_pinned(pin))
            published = state.get('batches', {}).get(batch['batch'], {}).get('published')
            disposition = record.get('disposition')
            if published:
                if (disposition != 'published' or record.get('point_receipt') != dict(
                        path=published['point_receipt_path'], sha256=published['point_receipt_sha256'])
                        or not durable.meson_published_batch(root, batch, published, time.time())):
                    raise ValueError('Retirement changed a published histogram or its source population')
                published_batches[str(root)].append(batch['batch'])
                campaigns.setdefault(batch['point'], [])
                if published['campaign'] not in campaigns[batch['point']]:
                    campaigns[batch['point']].append(published['campaign'])
            elif disposition == 'unsubmitted':
                attempts = [directory/name for name in ('submission.json', 'submission_intent.json')]
                attempts += [directory/'production'/name for name in ('submission.json', 'submission_intent.json')]
                if source or any(path.exists() for path in attempts):
                    raise ValueError('Retirement calls an attempted batch unsubmitted')
            elif disposition == 'failed':
                if not source.get('error') and not any(row.get('error') for row in statuses):
                    raise ValueError('Retirement has no preserved failure status')
            elif disposition == 'gen_only':
                if source.get('complete') is not True or record.get('detector_output_used') is not False:
                    raise ValueError('Retained GEN needs explicit unused detector accounting')
            else:
                raise ValueError('Retirement needs an explicit disposition for every batch')
        receipt_paths = [root/'submission.json', root/'coverage/submission.json']
        receipt_paths += list((root/'batches').glob('*/submission.json'))
        receipt_paths += list((root/'batches').glob('*/production/submission.json'))
        receipt_paths += list((root/'aggregates').glob('*/v*/submission.json'))
        for owner in entry.get('controllers', []):
            receipt_path = pinned_path(owner['submission'])
            directory = receipt_path.parent
            if (pinned_path(owner['configuration']) != directory/'config.json'
                    or pinned_path(owner['seal']) != directory/'seal.json'):
                raise ValueError('Retired controller is outside its pinned configuration and seal')
            identity = meson_controller_configuration(previous, directory, allow_science=True)
            receipt = read(receipt_path)
            if receipt.get('task') != str(directory) or receipt.get('submit_sha256') != durable.sha(identity['submit']):
                raise ValueError('Retired controller differs from its sealed native launch')
            owners.add(str(directory))
            owner_execution[receipt['cluster']] = identity['native_execution']
            receipt_paths.append(receipt_path)
        for path in receipt_paths:
            if path.is_file():
                receipt = read(path)
                if type(receipt.get('cluster')) is not int or receipt.get('task') != str(path.parent):
                    raise ValueError('Retired submission changed its exact job identity')
                submitted[(receipt['cluster'], 0)] = receipt['task']
    if not found:
        raise ValueError('Retirement does not include the expected predecessor allocation')
    native = read_pinned(proof['native_snapshot'])
    records = native.get('jobs', [])
    identities = {(row.get('ClusterId'), row.get('ProcId')) for row in records}
    if (native.get('schema') != 'shift-reviewed-meson-retirement-native-v1'
            or native.get('queue_absent') is not True or len(identities) != len(records)
            or not set(submitted).issubset(identities)):
        raise ValueError('Retirement lacks exact terminal records for submitted managers')
    for row in records:
        iwd = row.get('Iwd', '')
        if (type(row.get('ClusterId')) is not int or row['ClusterId'] <= 0
                or type(row.get('ProcId')) is not int or row['ProcId'] < 0
                or row.get('JobStatus') not in (3, 4)
                or not isinstance(iwd, str)
                or not any(iwd == path or iwd.startswith(path+'/') for path in roots | owners)):
            raise ValueError('Retirement native record is active or outside its exact original owners')
        expected_iwd = submitted.get((row['ClusterId'], row['ProcId']))
        execution = owner_execution.get(row['ClusterId'], {})
        if ((expected_iwd is not None and iwd != expected_iwd)
                or any(row.get(key) != value for key, value in execution.items())):
            raise ValueError('Retirement native job differs from its exact submission or sealed launch')
    for pin in native.get('retirement_receipts', []):
        read_pinned(pin)
    alternatives = []
    for directory in sorted(roots | owners):
        alternatives.append('regexp('+json.dumps('^'+re.escape(directory)+'(/|$)')+',Iwd)')
        alternatives.append('regexp('+json.dumps(re.escape(directory)) + ',Args)')
    for cluster in sorted({row['ClusterId'] for row in records}):
        alternatives += ['ClusterId == '+str(cluster), 'DAGManJobId == '+str(cluster)]
    if check_queue and durable.jobs(config, ' || '.join(alternatives)):
        raise RuntimeError('Retired meson controller, publisher or worker is still live')
    return dict(campaigns=campaigns, published_batches=published_batches,
                retired_directories=sorted(roots), allocation_canceled=True)


def predecessor_controller_configuration(config, directory):
    """Validate a named operational owner without changing its science or state."""
    previous = prior_meson_configuration(config)
    return meson_controller_configuration(previous, directory)


def meson_controller_configuration(previous, directory, allow_science=False):
    """Check only this owner's seal and science, never its peers' locators."""
    directory = Path(directory)
    if (previous is None or not directory.is_absolute() or '..' in directory.parts
            or (directory == Path(previous['directory']) and not allow_science)
            or directory.resolve() != directory):
        raise ValueError('Future predecessor controller needs one exact operational directory')
    paths = [directory/name for name in ('config.json', 'seal.json', 'controller.sub', 'lcg_controller.sh')]
    if any(path.resolve() != path for path in paths):
        raise ValueError('Future predecessor controller files leave their exact directory')
    path, seal_path, submit_path, wrapper = paths
    seal_hash = durable.sha(seal_path)
    if directory == Path(previous['directory']):
        if verify(directory, seal_hash) != previous:
            raise ValueError('Canonical meson terminal owner changed its pinned science')
    else:
        operational_configuration(previous, path, seal_hash)
    descriptor = submit_path.read_text()
    def setting(name):
        values = re.findall(r'^'+re.escape(name)+r'\s*=\s*(.*?)\s*$', descriptor, re.MULTILINE)
        if len(values) != 1:
            raise ValueError('Future predecessor submit setting is ambiguous: '+name)
        return values[0]
    if (setting('initialdir') != str(directory) or setting('executable') != str(wrapper)
            or setting('arguments') != seal_hash):
        raise ValueError('Future predecessor submit descriptor has different execution settings')
    return dict(submit=str(submit_path), native_execution=dict(Cmd=str(wrapper), Args=seal_hash))


def validate_meson_peer_locators(config, names):
    locators = config.get('meson_peer_controller_directories', {})
    if not isinstance(locators, dict) or set(locators)-set(names):
        raise ValueError('Meson completion owners must name existing protected peers')
    for value in locators.values():
        path = Path(value)
        if not path.is_absolute() or '..' in path.parts or path.resolve() != path:
            raise ValueError('Meson completion owner needs one exact persistent directory')
    return locators


def terminal_meson_peer_state(config, authority, observed):
    """Keep published credit after a verified owner's final heartbeat stops."""
    try:
        previous = read_pinned(authority['worklist'])
        root = Path(authority['directory'])
        if previous.get('directory') != str(root):
            return None
        state = read(root/'state.json')
        if (state.get('complete') is not True or state.get('error') or state.get('failures')
                or set(state.get('batches', {})) != {row['batch'] for row in previous['batches']}):
            return None
        if not all(durable.meson_published_batch(root, row,
                state['batches'][row['batch']].get('published'), observed) for row in previous['batches']):
            return None
        directory = Path(authority['controller_directory'])
        owner = meson_controller_configuration(previous, directory, allow_science=True)
        receipt = read(directory/'submission.json')
        if (receipt.get('task') != str(directory) or type(receipt.get('cluster')) is not int
                or receipt['cluster'] <= 0 or receipt.get('submit_sha256') != durable.sha(owner['submit'])):
            return None
        constraint = ('ClusterId == '+str(receipt['cluster'])+' || DAGManJobId == '+str(receipt['cluster'])+
            ' || Iwd == '+json.dumps(str(directory))+' || regexp('+
            json.dumps('^'+re.escape(str(root))+'/(batches|aggregates|coverage)/')+',Iwd)')
        if durable.jobs(config, constraint):
            return None
        # Legacy science configs predate the terminal cache. The sealed current
        # owner carries its qualified cache authority; keep the observer's cache
        # directory for newly verified terminal records.
        owner_config = read(directory/'config.json')
        context = dict(config, reviewed_terminal_native_jobs=owner_config['reviewed_terminal_native_jobs'])
        if native_status(context, receipt) != 'complete':
            return None
        record = cached_terminal_record(context, receipt)
        native = record.get('terminal_native_ad', {}) if record else {}
        if any(native.get(key) != value for key, value in owner['native_execution'].items()):
            return None
        return state
    except (OSError, KeyError, TypeError, ValueError, RuntimeError, subprocess.SubprocessError):
        return None


def common_sample_anchor(config, batch, sample, sample_path, transport):
    """Retain a verified common physical anchor; mechanism belongs to campaigns."""
    from export_dark_photon_cross_sections import publish_file_rpc
    destination = config['eos_base']+'/darkphoton_'+batch['point']+'/sample.json'
    metadata = transport.metadata(destination)
    if metadata is None:
        proof = publish_file_rpc(sample_path, destination)
        return dict(proof, retained_existing=False,
            scope='Common mass/epsilon/lifetime/LAB reference; mechanism and rate authority are campaign-specific')
    size = int(metadata.get('size', -1))
    if not 0 < size <= 65536 or metadata.get('status') == 'symlink':
        raise ValueError('Existing global sample anchor is not a bounded regular JSON file')
    path = transport.cache_directory/'existing_global_sample.json'
    if path.exists():
        path.unlink()
    transport.command(['/usr/bin/xrdcp', '--silent', '--posc', '--cksum', 'adler32',
                       transport.host+'/'+destination, str(path)])
    if path.stat().st_size != size:
        raise ValueError('Existing global sample anchor changed during readback')
    existing = read(path)
    exact = ('schema', 'point', 'original_point', 'naming', 'selector_kind', 'mass_gev', 'epsilon',
             'sampling_epsilon', 'target_mean_lab_flight_m', 'mean_lab_flight_m',
             'calibration_mean_beta_gamma', 'mean_lab_flight_is_estimate')
    if any(existing.get(key) != sample.get(key) for key in exact):
        raise ValueError('Existing global sample anchor has different physical point or LAB reference')
    for key in ('proper_ctau_mm', 'reference_mean_lab_flight_m'):
        value = existing.get(key)
        if (type(value) not in (int, float) or not math.isfinite(value)
                or not math.isclose(value, sample[key], rel_tol=1.e-10, abs_tol=0.)):
            raise ValueError('Existing global sample anchor has a different physical lifetime/LAB reference')
    creator = existing.get('production_mechanism')
    candidate = config
    visited = set()
    matched = False
    while candidate is not None:
        authority = candidate.get('calibration_authority', {})
        if (authority and authority.get('sha256') is not None
                and creator == source_mechanism(candidate)
                and existing.get('calibration_sha256') == authority.get('sha256')):
            matched = True
            break
        predecessor = candidate.get('prior_meson_publication', {})
        identity = predecessor.get('path')
        if identity in visited or len(visited) >= 16:
            raise ValueError('Common sample anchor has a cyclic or excessive predecessor chain')
        visited.add(identity)
        candidate = prior_meson_configuration(candidate)
    if not matched:
        raise ValueError('Existing global sample anchor has no matching pinned creator/calibration authority')
    return dict(path=destination, bytes=size, sha256=durable.sha(path),
        readback_sha256=durable.sha(path), retained_existing=True, historical_creator_mechanism=creator,
        scope='Common mass/epsilon/lifetime/LAB anchor only; its historical mechanism does not describe this campaign')


def completion_scope(config):
    """Report the explicitly incomplete channel programme, separately from jobs."""
    if source_mechanism(config) != 'meson-radiative':
        return None
    scope = read_pinned(config['meson_completion_scope'])
    card = read(config['model_card'])
    channels = scope.get('included_channels')
    remaining = scope.get('remaining_channels')
    if (scope.get('schema') not in ('shift-meson-completion-scope-v1',
                                  'shift-dark-photon-meson-coverage-v1')
            or scope.get('physics_complete') is not False
            or not isinstance(channels, list) or not channels
            or any(not isinstance(name, str) or not name for name in channels)
            or len(set(channels)) != len(channels)
            or channels != card.get('channels')
            or not isinstance(remaining, list) or not remaining):
        raise ValueError('Unified meson source requires its explicit incomplete channel scope')
    for row in remaining:
        if (not isinstance(row, dict) or not row.get('channel') or not row.get('reason')
                or row.get('status') not in ('missing_model', 'needs_validation', 'missing_source')):
            raise ValueError('Every remaining meson channel needs an explicit blocking reason')
    return dict(included_channels=channels, remaining_channels=remaining, physics_complete=False,
                scope_sha256=config['meson_completion_scope']['sha256'])


def reference_point(config, batch):
    """Keep the existing epsilon/lifetime, rather than retuning another boost."""
    if source_mechanism(config) != 'meson-radiative':
        return None
    authority = read_pinned(config['lifetime_reference_authority'])
    if authority.get('schema') != 'shift-meson-physical-point-reference-v1':
        raise ValueError('Unified meson source requires a pinned physical point reference')
    point = authority.get('points', {}).get(batch['point'])
    if (not isinstance(point, dict)
            or any(point.get(key) != batch.get(key) for key in
                   ('mass_gev', 'epsilon', 'target_mean_lab_flight_m'))
            or type(point.get('proper_ctau_mm')) not in (int, float)
            or not math.isfinite(point['proper_ctau_mm']) or point['proper_ctau_mm'] <= 0):
        raise ValueError('Unified meson sample differs from its reference epsilon/lifetime point')
    return point


def gen_runtime(config):
    """Require the qualified software archive for every new unified source."""
    if source_mechanism(config) != 'meson-radiative':
        return None
    runtime = config.get('gen_runtime', {})
    provenance = read_pinned(runtime['provenance'])
    if (not isinstance(runtime.get('url'), str)
            or not runtime['url'].startswith('root://eosuser.cern.ch/')
            or runtime.get('cmssw_relative') != 'CMSSW_17_0_0_pre4'
            or runtime.get('scram_arch') != 'el9_amd64_gcc13'
            or type(runtime.get('bytes')) is not int or not 0 < runtime['bytes'] <= 4000000000
            or provenance.get('schema') != 'shift-bsm-gen-runtime-provenance-v1'
            or provenance.get('archive_sha256') != runtime.get('sha256')
            or provenance.get('archive_bytes') != runtime['bytes']
            or provenance.get('scram_arch') != runtime['scram_arch']
            or not provenance.get('cmssw_git_head')
            or not isinstance(provenance.get('cmssw_git_status'), list)):
        raise ValueError('Unified meson source requires its qualified local GEN runtime')
    return runtime


def unpack_gen_runtime(config, scratch):
    """Reuse the existing frozen GEN software, with all extraction on the worker."""
    runtime = gen_runtime(config)
    if runtime is None:
        return Path(config['cmssw_base']), None
    import run_dark_photon_gen_stage as stage
    archive = scratch / 'gen_runtime.tar.gz'
    stage.command(['/usr/bin/xrdcp', '--silent', '--cksum', 'adler32',
                   runtime['url'], str(archive)], timeout=1200)
    if archive.stat().st_size != runtime['bytes'] or durable.sha(archive) != runtime['sha256']:
        raise ValueError('Downloaded GEN runtime archive differs from its pinned authority')
    destination = scratch / 'runtime'
    destination.mkdir()
    with tarfile.open(archive) as payload:
        for member in payload.getmembers():
            if (member.name.startswith('/') or '..' in Path(member.name).parts
                    or (member.issym() and member.linkname.startswith('/afs/'))
                    or Path(member.name).suffix in ('.gdml', '.inp', '.map')
                    or 'data' in Path(member.name).parts):
                raise ValueError('GEN runtime archive contains an unsafe or private payload')
        payload.extractall(destination)
    return destination / runtime['cmssw_relative'], archive


def coverage_request(config):
    request = config['coverage_audit']
    pinned_path(request['particle_data'])
    if request.get('source_inventory'):
        pinned_path(request['source_inventory'])
    masses = request.get('masses_gev')
    expected = sorted({row['mass_gev'] for row in config['batches']})
    if masses != expected:
        raise ValueError('Meson coverage inventory must cover the complete declared mass grid')
    return request


def coverage_result(config):
    directory = Path(config['directory']) / 'coverage'
    status = read(directory / 'coverage_status.json')
    report = read_pinned(status['report'])
    request = coverage_request(config)
    scope = completion_scope(config)
    if (status.get('complete') is not True
            or report.get('schema') != 'shift-dark-photon-meson-coverage-v1'
            or report.get('model_sha256') != durable.sha(config['model_card'])
            or report.get('particle_data_sha256') != request['particle_data']['sha256']
            or report.get('source_inventory_sha256') != request.get('source_inventory', {}).get('sha256')
            or report.get('code_sha256') != durable.sha(Path(config['tools']) / 'audit_dark_photon_meson_coverage.py')
            or report.get('mass_points_gev') != request['masses_gev']
            or report.get('included_channels') != scope['included_channels']
            or report.get('remaining_channels') != scope['remaining_channels']
            or report.get('physics_complete') is not False):
        raise ValueError('Detached meson coverage result differs from its reviewed scope and inputs')
    return dict(complete=True, report=status['report'],
                candidate_transitions=len(report['candidate_transitions']),
                remaining_channels=report['remaining_channels'], physics_complete=False,
                source_sampling_verified=report.get('source_sampling_verified', False),
                resolved_source_configuration=report.get('resolved_source_configuration', False))


def coverage(config):
    """Run the real finite channel inventory before any new MC admission."""
    request = coverage_request(config)
    directory = Path(config['directory']) / 'coverage'
    status_path = directory / 'coverage_status.json'
    if status_path.exists():
        raise RuntimeError('Coverage already started; retain the original attempt for review')
    status = dict(complete=False, started_utc=datetime.now(timezone.utc).isoformat())
    durable.new(status_path, status)
    try:
        report = directory / 'report.json'
        argv = [os.sys.executable, Path(config['tools']) / 'audit_dark_photon_meson_coverage.py',
                '--particle-data', request['particle_data']['path'], '--model', config['model_card'],
                '--masses', *map(str, request['masses_gev']), '--output', report]
        if request.get('source_inventory'):
            argv.extend(['--source-inventory', request['source_inventory']['path']])
        durable.run_logged(argv, directory / 'audit.log', timeout=600)
        status.update(complete=True, report=dict(path=str(report), sha256=durable.sha(report)),
                      finished_utc=datetime.now(timezone.utc).isoformat())
        durable.save(status_path, status)
        return coverage_result(config)
    except Exception as error:
        status.update(complete=False, error=repr(error), traceback=traceback.format_exc()[-10000:])
        durable.save(status_path, status)
        raise


def verify(directory, seal_digest):
    directory = Path(directory)
    if durable.sha(directory / 'seal.json') != seal_digest:
        raise ValueError('Meson deployment seal changed')
    seal = read(directory / 'seal.json')
    for path, expected in seal['files'].items():
        if durable.sha(path) != expected:
            raise ValueError('Frozen meson dependency changed: ' + path)
    config = read(directory / 'config.json')
    if config.get('schema') != 'shift-dp-meson-production-v1' or config['directory'] != str(directory):
        raise ValueError('Wrong meson deployment configuration')
    for batch in config['batches']:
        path=batch_directory(config,batch)
        if (path/'source.sub').read_text()!=(path/'source.sub.in').read_text().replace('SEAL_DIGEST',seal_digest):
            raise ValueError('Source submit description differs from its frozen template')
        if config.get('parallel_handoffs') is True:
            handoff_path=path/'handoff'
            if (handoff_path/'worker.sub').read_text()!=(handoff_path/'worker.sub.in').read_text().replace('SEAL_DIGEST',seal_digest):
                raise ValueError('Handoff submit description differs from its frozen template')
    if source_mechanism(config) == 'meson-radiative':
        path = directory / 'coverage'
        if (path/'coverage.sub').read_text() != (path/'coverage.sub.in').read_text().replace('SEAL_DIGEST', seal_digest):
            raise ValueError('Coverage submit description differs from its frozen template')
    for path, record in read(config['runtime_fingerprint'])['files'].items():
        if durable.sha(path) != record['sha256']:
            raise ValueError('Shared qualified CMSSW runtime changed: ' + path)
    return config


def batch_directory(config, batch):
    return Path(config['directory']) / 'batches' / batch['batch']


def operational_configuration(original,path,expected,_reservation_chain=()):
    """Adopt a separately sealed controller without changing live GEN sources."""
    path=Path(path)
    if str(path) in _reservation_chain or len(_reservation_chain) >= 32:
        raise ValueError('Cyclic or excessive meson reservation registration history')
    seal_path=path.parent/'seal.json'
    if durable.sha(seal_path)!=expected:
        raise ValueError('Replacement controller seal changed')
    seal=read(seal_path)
    if seal['files'].get(str(path))!=durable.sha(path):
        raise ValueError('Replacement configuration is outside its seal')
    for name,digest in seal['files'].items():
        if durable.sha(name)!=digest:
            raise ValueError('Replacement controller dependency changed: '+name)
    config=read(path)
    # Scheduling can use the complete already budgeted grid. Source exposure,
    # seeds, worklist order, physics and storage claims remain frozen.
    operational={'tools','simultaneous_sources','simultaneous_detector_points',
                 'additional_reserved_worklists','additional_reserved_meson_forecasts',
                 'prior_meson_controller_directory', 'meson_peer_controller_directories'}
    immutable=set(original)-operational
    if any(config.get(key)!=original[key] for key in immutable):
        raise ValueError('Replacement controller changed frozen production settings')
    if 'additional_reserved_worklists' in original:
        # A new supervisor may close the same peer allocation. Only its
        # terminal controller evidence changes; every storage claim is kept.
        def claims(rows):
            return [{key:value for key,value in row.items() if key != 'completion_authority'} for row in rows]
        if claims(config.get('additional_reserved_worklists', [])) != claims(original['additional_reserved_worklists']):
            raise ValueError('Replacement controller changed protected peer storage claims')
    if set(config)-set(original)-{'physics_tools', 'reviewed_detector_recoveries',
                                   'additional_reserved_worklists', 'reviewed_terminal_native_jobs',
                                   'adaptive_terminal_completion', 'prior_meson_controller_replacement',
                                   'additional_reserved_meson_forecasts', 'meson_reservation_registration',
                                   'prior_meson_controller_directory', 'meson_peer_controller_directories',
                                   'reviewed_prior_meson_retirement'}:
        raise ValueError('Replacement controller added unreviewed production settings')
    if config.get('physics_tools')!=original['tools']:
        raise ValueError('Replacement controller lost the original GEN source authority')
    if config.get('prior_meson_controller_directory') != original.get('prior_meson_controller_directory'):
        if (not config.get('prior_meson_controller_directory')
                or config.get('prior_meson_controller_replacement')):
            raise ValueError('A predecessor-owner rollover cannot remove or compete with its publication fence')
        predecessor_controller_configuration(config, config['prior_meson_controller_directory'])
    validate_meson_reservation_additions(original, config)
    locators = validate_meson_peer_locators(config,
        [row['name'] for row in config.get('additional_reserved_meson_forecasts', [])])
    if set(original.get('meson_peer_controller_directories', {}))-set(locators):
        raise ValueError('Replacement controller removed a protected peer completion owner')
    registration = config.get('meson_reservation_registration')
    if registration is not None:
        previous = pinned_path(registration['previous_configuration'])
        previous_seal = pinned_path(registration['previous_seal'])
        if previous_seal != previous.parent/'seal.json':
            raise ValueError('Meson registration predecessor is outside its sealed deployment')
        predecessor = operational_configuration(original, previous, registration['previous_seal']['sha256'],
                                                 (*_reservation_chain, str(path)))
        additions = validate_meson_reservation_additions(predecessor, config)
        if registration.get('added_peer_names') != [row['name'] for row in additions]:
            raise ValueError('Meson registration changed its declared added peers')
    from prepare_dark_photon_meson_production import source_execution_limits
    source_limit=source_execution_limits(original)['maximum_simultaneous_sources']
    for key,maximum in [('simultaneous_sources',source_limit),('simultaneous_detector_points',35)]:
        if key in original and (type(config.get(key)) is not int or not 1<=config[key]<=maximum):
            raise ValueError('Replacement controller scheduling limit is outside the reviewed grid: '+key)
    return config


def validate_meson_forecast_peer(authority, owner=None):
    """Bind a new positive reservation to every batch of one frozen peer."""
    peer_pin = authority['peer_configuration']
    peer = read_pinned(peer_pin)
    directory = Path(peer.get('directory', ''))
    batches = peer.get('batches', [])
    forecast = read_pinned(authority['forecast'])
    if (peer.get('schema') != 'shift-dp-meson-production-v1'
            or not directory.is_absolute() or '..' in directory.parts
            or Path(peer_pin['path']) != directory/'config.json'
            or str(directory) == owner
            or not batches or any(not isinstance(row, dict) or not row.get('batch') for row in batches)
            or len({row['batch'] for row in batches}) != len(batches)
            or forecast.get('worklist_sha256') != peer_pin['sha256']
            or set(forecast.get('per_batch_forecast_bytes', {})) != {row['batch'] for row in batches}
            or forecast.get('model_card_sha256') != durable.sha(peer['model_card'])):
        raise ValueError('Added meson reservation differs from its frozen peer worklist or model')
    source_mechanism(peer)
    return str(directory)


def validate_meson_reservation_additions(previous, candidate):
    """Keep claims intact; append peers or pin an explicitly canceled future."""
    before = previous.get('additional_reserved_meson_forecasts', [])
    after = candidate.get('additional_reserved_meson_forecasts', [])
    if not isinstance(before, list) or not isinstance(after, list):
        raise ValueError('Meson reservations require explicit lists of pinned peers')
    names = [row.get('name') for row in after]
    names.extend(row.get('name') for row in candidate.get('additional_reserved_worklists', []))
    if any(not isinstance(name, str) or not name for name in names) or len(names) != len(set(names)):
        raise ValueError('Meson reservations require unique nonempty peer names')
    for old in before:
        matches = [row for row in after if row['name'] == old['name']]
        if len(matches) != 1:
            raise ValueError('Meson registration removed or changed an existing protected claim')
        row = matches[0]
        if row != old:
            if ('retirement_authority' in old or 'retirement_authority' not in row
                    or {key:value for key,value in row.items() if key != 'retirement_authority'} != old):
                raise ValueError('Meson registration changed or repeated an existing protected claim')
            expected = row.get('peer_configuration') or row.get('completion_authority')
            reviewed_meson_retirement(candidate, row['retirement_authority'], expected, check_queue=False)
    additions = [row for row in after if row['name'] not in {old['name'] for old in before}]
    directories = set()
    for row in before:
        authority = row.get('peer_configuration', row.get('completion_authority', {}))
        if authority.get('path'):
            directories.add(read_pinned(authority).get('directory'))
    for row in additions:
        if row.get('completion_authority') is not None or row.get('retirement_authority') is not None:
            raise ValueError('New meson reservations keep their full positive allocation')
        directory = validate_meson_forecast_peer(row, candidate.get('directory'))
        if directory in directories:
            raise ValueError('Meson registration repeats an existing peer production')
        directories.add(directory)
    # Reuse the production admission check for positive amounts and exact sums.
    additional_reservations(dict(additional_reserved_meson_forecasts=additions))
    return additions


def adopted_row(batch, gen, config=None):
    """Adopt all conditional signal events; do not reinterpret source M as N."""
    gen = Path(gen)
    contract, audit = read(gen / 'contract.json'), read(gen / 'validation.json')
    count = contract['requested_events']
    mechanism = source_mechanism(config or {})
    if (contract.get('production_mechanism') != mechanism
            or type(count) is not int or count <= 0 or audit.get('runtime_validated') is not True
            or audit.get('events') != count or len(audit.get('event_ids', [])) != count
            or any(contract[key] != batch[key] for key in ('mass_gev', 'epsilon', 'seed', 'run_number'))
            or contract['source_exposure']['fixed_source_collisions'] != batch['source_collisions']
            or contract['source_exposure']['accepted_signal_events'] != count):
        raise ValueError('Meson source audit or fixed-M/random-N boundary changed')
    if mechanism == 'meson-radiative':
        point = reference_point(config, batch)
        card = read(config['model_card'])
        if (contract.get('meson_model_card_sha256') != durable.sha(config['model_card'])
                or contract.get('meson_model_card') != card
                or not math.isclose(contract['physical_width_table']['ctau_mm'],
                                    point['proper_ctau_mm'], rel_tol=1.e-12, abs_tol=0.)):
            raise ValueError('Unified source changed its qualified card or reference lifetime')
    return dict(point=batch['point'], mass_gev=batch['mass_gev'], epsilon=batch['epsilon'],
        seed=batch['seed'], run_number=batch['run_number'], events=count,
        production_mechanism=mechanism,
        proper_ctau_mm=contract['physical_width_table']['ctau_mm'], sampling_epsilon=batch['epsilon'],
        target_mean_lab_flight_m=batch.get('target_mean_lab_flight_m'),
        mean_beta_gamma_reference=contract['mean_beta_gamma_reference'],
        mean_lab_flight_reference_estimate_m=contract['mean_lab_flight_reference_estimate_m'],
        signal_contract=str(gen / 'contract.json'), signal_contract_sha256=durable.sha(gen / 'contract.json'),
        gen_directory=str(gen), final_output_base=batch['final_output_base'])


def compact_files(gen):
    """The full source ledger and generated payloads never enter an AFS seal."""
    gen=Path(gen)
    contract=gen/'contract.json'
    if contract.is_file():
        for relative,expected in read(contract).get('meson_physics_inputs_sha256',{}).items():
            path=Path(relative)
            if path.is_absolute() or '..' in path.parts or not path.parts or path.parts[0]!='physics_inputs':
                raise ValueError('Compact rate metadata requires its declared frozen relative path')
            if durable.sha(gen/path)!=expected:
                raise ValueError('Pinned rate input changed before compact metadata publication')
    result = []
    for path in sorted(Path(gen).rglob('*')):
        if (not path.is_file() or '__pycache__' in path.parts or path.suffix in ('.root', '.hepmc', '.pyc')
                or path.name in ('meson_source_ledger.jsonl', 'meson_source_ledger.jsonl.gz')):
            continue
        if path.stat().st_size > 10000000:
            raise ValueError('Unexpected large file in compact GEN metadata: ' + str(path))
        result.append(path)
    if sum(path.stat().st_size for path in result) > 64000000:
        raise ValueError('Compact GEN metadata exceeds its reviewed AFS allowance')
    return result


def source(config, seal_digest, batch):
    from export_dark_photon_cross_sections import publish_file_rpc
    import run_dark_photon_gen_stage as stage
    directory = batch_directory(config, batch)
    status_path = directory / 'source_status.json'
    if status_path.exists():
        previous = read(status_path)
        if previous.get('complete') and previous.get('seal_sha256') == seal_digest:
            for path, expected in previous['metadata_sha256'].items():
                if durable.sha(path) != expected:
                    raise ValueError('Completed source metadata changed: ' + path)
            return previous
        raise RuntimeError('This source already started; preserve its intent and failure for review')
    status = dict(schema='shift-dp-meson-source-status-v1', complete=False, batch=batch['batch'],
        point=batch['point'], source_collisions=batch['source_collisions'], seal_sha256=seal_digest,
        started_utc=datetime.now(timezone.utc).isoformat())
    durable.save(status_path, status)
    stop = threading.Event()
    probe = {'gen': None, 'phase': 'starting'}

    def heartbeat():
        while not stop.is_set():
            gen = probe['gen']
            data = dict(batch=batch['batch'], heartbeat_utc=datetime.now(timezone.utc).isoformat(),
                        worker_pid=os.getpid(), phase=probe['phase'])
            if gen is not None:
                data['source_ledger_bytes'] = ((gen/'meson_source_ledger.jsonl').stat().st_size
                    if (gen/'meson_source_ledger.jsonl').is_file() else 0)
                if (gen/'manifest.json').is_file():
                    try:
                        data['GEN_status'] = read(gen/'manifest.json').get('status')
                    except (ValueError, OSError):
                        pass  # A concurrent atomic status write can be incomplete.
            durable.save(directory/'source_heartbeat.json',data)
            stop.wait(30)

    observer = threading.Thread(target=heartbeat,daemon=True)
    observer.start()
    scratch_parent = os.environ.get('_CONDOR_SCRATCH_DIR', '/tmp')
    try:
        stage.check_afs_margin(dict(config, workspace=config['workspace'],
                                   retain_local_gen_root=False, afs_metadata_reservation_mb=128))
        with tempfile.TemporaryDirectory(prefix='dp-meson-source-', dir=scratch_parent) as temporary:
            scratch = Path(temporary)
            os.environ['TMPDIR'] = str(scratch)
            probe['phase'] = 'runtime_download'
            release, runtime_archive = unpack_gen_runtime(config, scratch)
            gen = scratch / 'gen'
            probe.update(gen=gen,phase='GEN')
            runner = Path(config['tools']) / 'run_dark_photon_meson_gen.py'
            argv = ['python3', str(runner), '--mass', str(batch['mass_gev']), '--epsilon', str(batch['epsilon']),
                '--source-collisions', str(batch['source_collisions']), '--seed', str(batch['seed']),
                '--run-number', str(batch['run_number']), '--model-card', config['model_card'],
                '--darkcast-path', config['darkcast_path'], '--darkcast-source-receipt', config['darkcast_source_receipt'],
                '--timeout', str(config['gen_timeout_seconds']), '--output', str(gen)]
            rename = ''
            if runtime_archive is not None:
                runtime = gen_runtime(config)
                argv.extend(['--runtime-provenance', runtime['provenance']['path'],
                             '--runtime-archive', str(runtime_archive)])
                rename = ' && scram b ProjectRename'
            shell = ('export SCRAM_ARCH=el9_amd64_gcc13 && source /cvmfs/cms.cern.ch/cmsset_default.sh && cd ' +
                shlex.quote(str(release / 'src')) + rename +
                ' && eval "$(scram runtime -sh)" && cd ' + shlex.quote(str(scratch)) +
                ' && export PYTHONDONTWRITEBYTECODE=1 && exec ' + shlex.join(argv))
            with (directory / 'logs/GEN.log').open('x') as log:
                result = subprocess.run(['/bin/bash', '-c', shell], cwd=directory,
                    env=stage.native_env(), stdout=log, stderr=subprocess.STDOUT,
                    timeout=config['gen_timeout_seconds'] + 900, check=False)
            # Keep the complete source accounting even if a fixed exposure
            # returns zero signals. A zero source is never rerun automatically.
            ledger = gen / 'meson_source_ledger.jsonl'
            if ledger.is_file():
                probe['phase']='ledger_publication'
                compressed = scratch / 'meson_source_ledger.jsonl.gz'
                with ledger.open('rb') as incoming, compressed.open('xb') as raw:
                    with gzip.GzipFile(fileobj=raw, mode='wb', mtime=0) as outgoing:
                        shutil.copyfileobj(incoming, outgoing, 1048576)
                publication = publish_file_rpc(compressed,
                    batch['final_output_base'] + '/metadata/GEN/meson_source_ledger.jsonl.gz')
                publication.update(schema='shift-meson-source-ledger-publication-v1', encoding='gzip',
                    uncompressed_bytes=ledger.stat().st_size, uncompressed_sha256=durable.sha(ledger))
                # Independent streamed readback of the original, uncompressed bytes.
                digest, size = hashlib.sha256(), 0
                with gzip.open(compressed, 'rb') as incoming:
                    for block in iter(lambda: incoming.read(1048576), b''):
                        digest.update(block); size += len(block)
                if size != publication['uncompressed_bytes'] or digest.hexdigest() != publication['uncompressed_sha256']:
                    raise ValueError('Lossless source ledger compression changed the full source exposure')
                durable.new(gen / 'meson_ledger_publication.json', publication)
                status['ledger_publication'] = publication
                durable.save(status_path, status)
            # Always retain compact source evidence before raising a GEN failure.
            if (gen / 'manifest.json').exists():
                archive = scratch / 'gen_metadata.tar.gz'
                with tarfile.open(archive, 'w:gz') as output:
                    for path in compact_files(gen):
                        output.add(path, arcname=str(path.relative_to(gen)), recursive=False)
                status['metadata_archive'] = publish_file_rpc(archive,
                    batch['final_output_base'] + '/metadata/GEN/gen_metadata.tar.gz')
                durable.save(status_path, status)
            result.check_returncode()
            row = adopted_row(batch, gen, config)
            probe['phase']='GEN_publication'
            row['gen_argv'] = ['python3', str(runner)]
            publication, parent = stage.publish_gen(row, dict(config, scratch_base=str(scratch),
                retain_local_gen_root=False, eos_quota_space='/eos/user/j/jniedzie/'))
            final_gen = directory / 'gen'
            pending_gen = directory / 'gen.next'
            pending_gen.mkdir()
            for path in compact_files(gen):
                target = pending_gen / path.relative_to(gen)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
            proof = read(pending_gen / 'root_publication.json')
            proof['local_path'] = str(final_gen / 'gen.root')
            durable.save(pending_gen / 'root_publication.json', proof)
            os.rename(pending_gen, final_gen)
            status.update(complete=True, events=row['events'], publication=publication, parent=parent,
                metadata_sha256={str(path): durable.sha(path) for path in compact_files(final_gen)},
                finished_utc=datetime.now(timezone.utc).isoformat())
    except Exception as error:
        status.update(error=repr(error), traceback=traceback.format_exc()[-10000:])
        raise
    finally:
        stop.set()
        observer.join(timeout=10)
        durable.save(status_path, status)
    return status


def adopt(config, batch):
    import prepare_dark_photon_grid as grid
    directory = batch_directory(config, batch)
    status = read(directory / 'source_status.json')
    if status.get('complete') is not True:
        raise ValueError('Detector adoption requires a complete audited published source')
    for path, expected in status['metadata_sha256'].items():
        if durable.sha(path) != expected:
            raise ValueError('Published GEN metadata changed before adoption')
    row = adopted_row(batch, directory / 'gen', config)
    actual_source_storage(config, batch, row, status)
    chunk_size=batch.get('events_per_job',config['events_per_job'])
    if math.ceil(row['events']/chunk_size) + 1 > batch['detector_job_capacity']:
        raise ValueError('Actual N needs a separately reviewed larger detector namespace; do not truncate it')
    physical = dict(points=[row], production_mechanism=source_mechanism(config),
        physical_width_preflight_passed=True, meson_width_qualification=grid.meson_width_qualification([row]))
    plan = grid.plan_detector_stage(physical, batch['campaign'], config['eos_base'],
        events_per_job=chunk_size, group_events=config['group_events'],
        detector_job_base=batch['detector_job_base'])
    plan['runtime'] = dict(cmssw_base=config['cmssw_base'])
    if config.get('detector_runtime') is not None:
        # The ordinary preparer validates this separately qualified capsule.
        plan['detector_runtime']=copy.deepcopy(config['detector_runtime'])
    from run_dark_photon_meson_gen import SOURCES as MESON_SOURCES
    sources=set(grid.SOURCES)|set(MESON_SOURCES)
    plan['frozen_dependencies'] = dict(sources={name: dict(frozen_copy=str(Path(config['tools'])/name),
        sha256=durable.sha(Path(config['tools'])/name)) for name in sorted(sources)},
        templates={name:dict(frozen_copy=str(path),sha256=durable.sha(path))
            for path in sorted((Path(config['directory'])/'templates').glob('step*.py')) for name in [path.name]})
    # Generic downstream workers consume the real audited GEN source directly.
    row = plan['points'][0]
    row['gen_argv'] = ['python3', str(Path(config.get('physics_tools',config['tools']))/'run_dark_photon_meson_gen.py')]
    preparation = directory / 'preparation.json'
    if preparation.exists():
        if read(preparation) != plan:
            raise ValueError('An earlier post-GEN detector adoption differs')
    else:
        durable.new(preparation, plan)
    inventory = dict(schema='shift-independent-signal-GEN-inventory-v1', complete=True,
        points={batch['point']:status['parent']}, each_parent_ledger_counted_once=True,
        technical_trials_are_not_collision_exposure=True, source_exposure_fixed_before_signal_sampling=True,
        fixed_unfiltered_source_collisions={batch['point']:batch['source_collisions']})
    publication = dict(schema='shift-stage-GEN-publication-v1', complete=True, inputs=[status['publication']])
    for name, value in [('gen_inventory.json', inventory), ('gen_publication.json', publication)]:
        path = directory / name
        if path.exists():
            if read(path) != value:
                raise ValueError('Previous meson handoff inventory changed')
        else:
            durable.new(path, value)
    return plan


def actual_source_storage(config, batch, row, status):
    """Stop an oversized actual population before detector submission."""
    if source_mechanism(config) != 'meson-radiative' or batch['operational_canary']:
        return None
    gate_path = Path(config['bulk_storage_gate'])
    gate = read(gate_path)
    forecasts = gate.get('per_batch_forecast_bytes', {})
    total = gate.get('total_meson_forecast_bytes')
    if (gate.get('complete') is not True
            or gate.get('all_requested_mass_GEN_audits_passed') is not True
            or gate.get('worklist_sha256') != durable.sha(Path(config['directory'])/'config.json')
            or gate.get('model_card_sha256') != durable.sha(config['model_card'])
            or set(forecasts) != {item['batch'] for item in config['batches']}
            or any(type(value) is not int or value <= 0 for value in forecasts.values())
            or type(total) is not int or not 0 < total <= 30000000000
            or sum(forecasts.values()) != total):
        raise ValueError('Actual source budget requires the exact reviewed bulk storage gate')
    publication = status.get('ledger_publication')
    receipt_path = batch_directory(config, batch)/'gen/meson_ledger_publication.json'
    if (not isinstance(publication, dict) or publication != read(receipt_path)
            or publication.get('encoding') != 'gzip' or type(publication.get('bytes')) is not int
            or publication['bytes'] <= 0 or publication.get('sha256') != publication.get('readback_sha256')):
        raise ValueError('Actual source budget requires its verified complete compressed ledger')
    required = row['events']*326000 + publication['bytes'] + 5000000
    result = dict(point=batch['point'], batch=batch['batch'], actual_signal_events=row['events'],
        compressed_source_ledger_bytes=publication['bytes'], fixed_metadata_bytes=5000000,
        event_storage_coefficient_bytes=326000, required_bytes=required,
        forecast_bytes=forecasts[batch['batch']], storage_gate_sha256=durable.sha(gate_path),
        ledger_publication_sha256=durable.sha(receipt_path), passed=required <= forecasts[batch['batch']],
        source_population_preserved=True, automatic_retry_or_truncation_allowed=False)
    path = batch_directory(config, batch)/'actual_source_storage.json'
    if path.exists():
        if read(path) != result:
            raise ValueError('Earlier actual-source storage decision changed; preserve it for review')
    else:
        durable.new(path, result)
    if not result['passed']:
        raise ValueError('Actual complete source exceeds its reviewed forecast; GEN is preserved for storage review')
    return result


def handoff(config, batch):
    directory = batch_directory(config, batch)
    plan = adopt(config, batch)
    production = directory / 'production'
    if not production.exists():
        budget = copy.deepcopy(read(config['storage_budget_template']))
        budget.update(events=plan['events'], detector_chunks=plan['detector_chunks'], points=1,
            external_reservations=[], fixed_reservation_bytes=config['minimum_eos_free_bytes'],
            gen_group_file_allowance=4000, safety_free_floor_files=5000)
        budget_path = directory/'storage_budget.json'
        if budget_path.exists():
            if read(budget_path) != budget:
                raise ValueError('Earlier detector storage budget changed; preserve the interrupted preparation')
        else:
            durable.new(budget_path, budget)
        # The sealed operational deployment may repair orchestration while
        # adopted GEN physics and its original source helpers stay unchanged.
        durable.run_logged([os.sys.executable, Path(__file__).with_name('prepare_dark_photon_production.py'),
            '--preparation', directory/'preparation.json', '--template', config['qualified_template'],
            '--gen-inventory', directory/'gen_inventory.json', '--gen-publication', directory/'gen_publication.json',
            '--storage-budget', directory/'storage_budget.json', '--output', production,
            '--prior-inventories', *config['prior_inventories'], '--canary-points', batch['point'],
            '--failure-base', config['failure_base'], '--detector-workers',
            str(config.get('detector_workers_per_point', 100)), '--finalizers', '2',
            '--publication-profile', 'nano-metadata-v1', '--publication-canary', config['publication_canary'],
            '--event-stall-timeout','7200','--first-event-stall-timeout','7200','--detector-timeout','43200'],
            directory/'prepare_production.log', timeout=1800)
    receipt = (existing_receipt(production, production/'pipeline.dag.condor.sub')
               if (production/'submission.json').is_file() else durable.submit_dag(
                   config, production, config.get('detector_workers_per_point', 100) + 2))
    rates = directory / 'rates.json'
    if not rates.exists():
        durable.run_logged([os.sys.executable, Path(config['tools'])/'export_dark_photon_cross_sections.py',
            '--plan', production/'plan.json', '--output', directory/'rates', '--publish', '--eos-rpc'],
            directory/'export_rates.log', timeout=1800)
        durable.new(rates, dict(complete=True, production_plan_sha256=durable.sha(production/'plan.json')))
    authority = config.get('reviewed_detector_recoveries', {}).get(batch['batch'])
    if authority is not None:
        return reviewed_detector_recovery(config, batch, receipt, authority)
    return receipt


def prepare_handoff_worker(config, batch):
    """Freeze an independent preparation worker before the science seal exists."""
    directory=batch_directory(config,batch)/'handoff'
    directory.mkdir()
    script=Path(config['tools'])/'run_dark_photon_meson_production.py'
    wrapper=directory/'run.sh'
    wrapper.write_text('''#!/bin/bash
# Code style: AI-code. AI-generated independent meson detector preparation.
set -eo pipefail
unset PYTHONPATH PYTHONHOME LD_LIBRARY_PATH LD_PRELOAD ROOTSYS
export PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
source /cvmfs/sft.cern.ch/lcg/views/LCG_108/x86_64-el9-gcc13-opt/setup.sh
exec python3 '''+shlex.quote(str(script))+' --directory '+shlex.quote(config['directory'])+
        ' --seal "$1" --action handoff --batch '+shlex.quote(batch['batch'])+'\n')
    wrapper.chmod(0o755)
    descriptor=f'''# Code style: AI-code. AI-generated independent meson handoff worker.
universe = vanilla
executable = {wrapper}
arguments = SEAL_DIGEST
initialdir = {directory}
getenv = False
should_transfer_files = YES
transfer_output_files = ""
request_cpus = 1
request_memory = 3000MB
request_disk = 4000MB
+JobFlavour = "tomorrow"
+MaxRuntime = 86400
+SendCredential = True
+ShiftDPStatisticsTask = "{directory}"
+JobBatchName = "SHIFT_DP_meson_detector_preparation"
output = {directory}/worker.$(ClusterId).$(ProcId).out
error = {directory}/worker.$(ClusterId).$(ProcId).err
log = {directory}/events.log
on_exit_hold = (ExitBySignal == True) || (ExitCode != 0)
on_exit_remove = (ExitBySignal == False) && (ExitCode == 0)
periodic_release = False
queue
'''
    (directory/'worker.sub.in').write_text(descriptor)
    return [wrapper,directory/'worker.sub.in']


def parallel_handoff(config,state,batch,entry,queue_snapshot):
    directory=batch_directory(config,batch)/'handoff'
    description=directory/'worker.sub'
    receipt=(existing_receipt(directory,description) if (directory/'submission.json').is_file()
             else durable.submission(config,directory,description))
    entry.update(handoff_cluster=receipt['cluster'],stage='detector_preparation')
    status=batch_job_status(config,state,batch,entry,receipt,'detector_preparation',queue_snapshot)
    if status!='complete':
        return status,None
    production=batch_directory(config,batch)/'production'
    detector=existing_receipt(production,production/'pipeline.dag.condor.sub')
    rates=batch_directory(config,batch)/'rates.json'
    if (not rates.is_file() or read(rates)!=dict(complete=True,
            production_plan_sha256=durable.sha(production/'plan.json'))):
        raise RuntimeError('Preparation worker exited without its complete rate export')
    if batch['batch'] in config.get('reviewed_detector_recoveries',{}):
        detector=reviewed_detector_recovery(config,batch,detector,
                                           config['reviewed_detector_recoveries'][batch['batch']])
    return status,detector


def reviewed_detector_recovery(config, batch, original, authority):
    """Adopt one reviewed finalizer/collect DAG without rerunning detector jobs."""
    proof = read_pinned(authority)
    production = batch_directory(config, batch) / 'production'
    if (proof.get('schema') != 'shift-meson-finalizer-recovery-v1'
            or proof.get('batch') != batch['batch'] or proof.get('point') != batch['point']
            or proof.get('original_submission') != original
            or proof.get('original_plan_sha256') != durable.sha(production/'plan.json')
            or proof.get('original_seal_sha256') != durable.sha(production/'seal.json')
            or proof.get('allowed_stages') != ['finalizer', 'collect']
            or proof.get('gen_or_detector_jobs_allowed') is not False):
        raise ValueError('Reviewed meson recovery differs from its original population')
    # A recorded historical failure is not enough: verify that the original
    # manager and all its children remain absent before using a new owner.
    constraint = ('ClusterId == '+str(original['cluster'])+
                  ' || DAGManJobId == '+str(original['cluster'])+
                  ' || Iwd == '+json.dumps(str(production)))
    if durable.jobs(config, constraint):
        raise RuntimeError('Original meson manager or children are still live')
    # Terminal native history cannot change after exit. Keep its pinned actual
    # readback, while querying live ownership above on every controller pass.
    # Repeating a history scan for the same failed job can exceed 45 seconds.
    terminal = read_pinned(proof['terminal_native_review'])
    history = terminal['history'].get(str(original['cluster']), [])
    if (type(proof.get('original_manager_exit_code')) is not int
            or proof['original_manager_exit_code'] == 0
            or len(history) != 1 or history[0].get('ClusterId') != original['cluster']
            or history[0].get('ProcId') != 0 or history[0].get('Iwd') != str(production)
            or history[0].get('ExitCode') != proof.get('original_manager_exit_code')
            or history[0].get('ExitBySignal')):
        raise RuntimeError('Reviewed failed meson manager has no matching native history')
    finalizer = terminal['history'].get(str(proof['original_finalizer_cluster']), [])
    if (terminal.get('queue') != [] or len(finalizer) != 1
            or finalizer[0].get('ClusterId') != proof['original_finalizer_cluster']
            or finalizer[0].get('ProcId') != 0 or finalizer[0].get('Iwd') != str(production)
            or finalizer[0].get('ExitCode') != proof['original_finalizer_exit_code']
            or finalizer[0].get('ExitBySignal')):
        raise RuntimeError('Reviewed failed meson finalizer has no matching native history')
    directory = Path(proof['recovery_directory'])
    for path, expected in proof['recovery_files'].items():
        if durable.sha(path) != expected:
            raise ValueError('Reviewed finalizer recovery changed: '+path)
    return existing_receipt(directory, directory/'pipeline.dag.condor.sub')


def terminal_peer_completion(config, authority, worklist=None):
    """Credit a closed peer only after every declared population was published."""
    try:
        if worklist is None:
            candidate = dict(config, prior_meson_publication=authority)
            previous = prior_meson_configuration(candidate)
            state = read(Path(previous['directory'])/'state.json')
            if state.get('complete') is not True:
                return False
            ready, proof = prior_publication_ready(candidate, previous['batches'][0])
            return ready and proof.get('terminal_closure_verified') is True
        controller = read_pinned(authority['controller_config'])
        root = Path(controller['directory'])
        if durable.sha(controller['worklist']) != worklist['sha256']:
            return False
        observed = time.time()
        state = read(root/'state.json')
        if (state.get('complete') is not True or state.get('finished') is not True
                or state.get('error') or state.get('failures') or state.get('aggregates')
                or state.get('unpublished_points') != []):
            return False
        allocation = read_pinned(worklist)
        if set(state.get('waves', {})) != {str(wave['wave_index']) for wave in allocation['waves']}:
            return False
        for wave in allocation['waves']:
            native_wave = state['waves'][str(wave['wave_index'])]
            if (native_wave.get('terminal') is not True or native_wave.get('successful') is not True
                    or native_wave.get('exit_code') != 0):
                return False
            plan_path = root/'waves'/f'w{wave["wave_index"]:03d}'/'production/plan.json'
            plan = read(plan_path)
            actual = {row['point']:row for row in plan['points']}
            if len(actual) != len(wave['batches']) or set(actual) != {row['point'] for row in wave['batches']}:
                return False
            for row in wave['batches']:
                if (any(actual[row['point']][key] != row[key] for key in ('events','seed','run_number'))
                        or not durable.closed_primitive(plan_path.parent, row['point'], row['events'], observed)):
                    return False
                published = state.get('published', {}).get(row['point'], {})
                path = Path(published.get('point_receipt_path', ''))
                if (str(plan_path) not in published.get('source_plans', [])
                        or not path.is_absolute() or not path.is_relative_to(root)
                        or path.name != row['point']+'.json'
                        or durable.sha(path) != published.get('point_receipt_sha256')):
                    return False
                report = read(path)
                marker = report.get('score_histograms', {}).get('marker', {})
                histogram = marker.get('published', {}).get('histograms', {})
                if (report.get('complete') is not True or report.get('point') != row['point']
                        or report.get('events') != published.get('events') or marker.get('complete') is not True
                        or histogram.get('path') != published.get('histogram_path')
                        or histogram.get('sha256') != published.get('histogram_sha256')):
                    return False
        if authority.get('current_controller_submit'):
            submit = pinned_path(authority['current_controller_submit'])
            if (submit.name != 'controller.sub'
                    or authority['controller_config']['path'] != str(submit.parent/'config.json')):
                return False
            receipt = read(submit.parent/'submission.json')
            if receipt.get('task') != str(submit.parent) or receipt.get('submit_sha256') != authority['current_controller_submit']['sha256']:
                return False
        else:
            receipt = read_pinned(authority['current_controller_receipt'])
        if (read(Path(receipt['task'])/'config.json')['directory'] != str(root)
                or durable.sha(Path(receipt['task'])/'controller.sub') != receipt['submit_sha256']):
            return False
        constraint = ('ClusterId == '+str(receipt['cluster'])+' || DAGManJobId == '+str(receipt['cluster'])+
                      ' || Iwd == '+json.dumps(receipt['task'])+' || regexp('+
                      json.dumps('^'+re.escape(str(root))+'/(waves|aggregates)/')+',Iwd)')
        return not durable.jobs(config, constraint) and native_status(config, receipt) == 'complete'
    except (OSError, KeyError, TypeError, ValueError, RuntimeError, subprocess.SubprocessError):
        return False


def additional_reservations(config):
    """Protect each full allocation until its published batches are verified."""
    total, claims = 0, []
    for authority in config.get('additional_reserved_worklists', []):
        allocation = read_pinned(authority['worklist'])
        events = allocation['total_additional_events']
        counts = [row['events'] for wave in allocation['waves'] for row in wave['batches']]
        coefficient = authority['bytes_per_event']
        if (type(events) is not int or events <= 0 or sum(counts) != events
                or any(type(count) is not int or count <= 0 for count in counts)
                or type(coefficient) is not int or coefficient < 326000):
            raise ValueError('Additional meson storage reservation is inconsistent')
        amount = events * coefficient
        completed = (terminal_peer_completion(config, authority['completion_authority'], authority['worklist'])
                     if authority.get('completion_authority') else False)
        if completed:
            amount = 0
        total += amount
        claim = dict(name=authority['name'], events=events, reserved_bytes=amount)
        if authority.get('completion_authority'):
            claim['terminal_completion_verified'] = completed
        claims.append(claim)
    retired_peers = set()
    for authority in config.get('additional_reserved_meson_forecasts', []):
        if authority.get('peer_configuration') is not None:
            validate_meson_forecast_peer(authority, config.get('directory'))
        forecast = read_pinned(authority['forecast'])
        amounts = forecast.get('per_batch_forecast_bytes', {})
        amount = forecast.get('total_meson_forecast_bytes')
        if (forecast.get('complete') is not True or type(amount) is not int or amount <= 0
                or not amounts or any(type(value) is not int or value <= 0 for value in amounts.values())
                or sum(amounts.values()) != amount):
            raise ValueError('Additional meson forecast does not cover its complete source allocation')
        peer_pin = authority.get('peer_configuration') or authority.get('completion_authority')
        detail = {}
        if authority.get('retirement_authority'):
            if peer_pin is None:
                raise ValueError('Canceled meson allocation lacks its exact original science')
            peer = read_pinned(peer_pin)
            if peer['directory'] in retired_peers:
                raise ValueError('Meson future allocation was canceled more than once')
            retired = reviewed_meson_retirement(config, authority['retirement_authority'], peer_pin)
            retired_peers.add(peer['directory'])
            credited = sum(amounts[name] for name in retired['published_batches'][peer['directory']])
            detail = dict(remaining_bytes=0, credited_bytes=credited,
                canceled_future_bytes=amount-credited, peer_healthy=False,
                terminal_completion_verified=False, reviewed_retirement_verified=True)
            amount = 0
        elif peer_pin is not None:
            peer = read_pinned(peer_pin)
            claim_authority = dict(kind='meson', name=authority['name'], directory=peer['directory'],
                                   worklist=peer_pin, forecast=authority['forecast'])
            locator = config.get('meson_peer_controller_directories', {}).get(authority['name'])
            if locator:
                claim_authority['controller_directory'] = locator
            detail = durable.supplement_campaign_claim(claim_authority, time.time(), 1200, config)
            amount = detail['remaining_bytes']
        total += amount
        claim = dict(name=authority['name'], reserved_bytes=amount,
                           forecast_sha256=authority['forecast']['sha256'],
                           **{key:value for key,value in detail.items() if key != 'name'})
        if peer_pin is None:
            claim['completed_peer_batches_release_no_credit'] = True
        claims.append(claim)
    return total, claims


def adaptive_reservation(config):
    root = Path(config['adaptive_directory'])
    worklist = read(root/'worklist.json')
    if 'directory' in config:
        expected = read(Path(config['directory'])/'seal.json')['files'].get(str((root/'worklist.json').resolve()))
        if expected != durable.sha(root/'worklist.json'):
            raise ValueError('Adaptive storage worklist differs from its frozen meson authority')
    total = worklist['total_additional_events']
    counts = [row['events'] for wave in worklist['waves'] for row in wave['batches']]
    if (type(total) is not int or total < 0 or any(type(count) is not int or count <= 0 for count in counts)
            or sum(counts) != total):
        raise ValueError('Adaptive full storage allocation is inconsistent')
    full_reservation = 2000000000 + total*326000
    if config.get('adaptive_terminal_completion'):
        pin = dict(path=str(root/'worklist.json'), sha256=durable.sha(root/'worklist.json'))
        terminal = durable.terminal_statistics_peer(config['adaptive_terminal_completion'],
                                                    root, pin, time.time())
        return 2000000000 if terminal is not None else full_reservation
    # A stopped or unhealthy peer cannot free any future storage claim. Its
    # complete original allocation remains protected while this workflow runs.
    try:
        state = read(root/'state.json')
    except (FileNotFoundError, json.JSONDecodeError):
        return full_reservation
    try:
        age = time.time() - datetime.fromisoformat(state['checked_utc']).timestamp()
    except (KeyError, TypeError, ValueError):
        return full_reservation
    if state.get('error') or age > 1200 or age < -60:
        return full_reservation
    # Only closed, published new populations release future storage claims.
    credited=0
    for wave in worklist['waves']:
        plan_path=root/'waves'/f'w{wave["wave_index"]:03d}'/'production/plan.json'
        if not all(str(plan_path) in state.get('published',{}).get(row['point'],{}).get('source_plans',[])
                   for row in wave['batches']):
            continue
        actual={row['point']:row for row in read(plan_path)['points']}
        for row in wave['batches']:
            if any(actual[row['point']][key]!=row[key] for key in ('events','seed','run_number')):
                raise ValueError('Adaptive published primitive differs from its frozen worklist')
        credited+=sum(row['events'] for row in wave['batches'])
    if type(credited) is not int or not 0 <= credited <= total:
        raise ValueError('Adaptive published-event credit is inconsistent')
    return 2000000000 + (total - credited)*326000


def storage_gate(config, observation, bulk=False, state=None):
    reservation = adaptive_reservation(config)
    additional = 500000000 if not bulk else None
    if bulk:
        path = config.get('bulk_storage_gate')
        if not path or not Path(path).is_file():
            return False, dict(reason='waiting_for_reviewed_bulk_storage_and_seven_mass_GEN_gates')
        gate = read(path)
        audit_flag = ('all_requested_mass_GEN_audits_passed'
                      if source_mechanism(config) == 'meson-radiative' else 'all_seven_mass_GEN_audits_passed')
        if (gate.get('complete') is not True or gate.get(audit_flag) is not True
                or gate.get('model_card_sha256') != durable.sha(config['model_card'])
                or gate.get('worklist_sha256') != durable.sha(Path(config['directory'])/'config.json')
                or type(gate.get('total_meson_forecast_bytes')) is not int
                or not 0 < gate['total_meson_forecast_bytes'] <= 30000000000):
            raise ValueError('Bulk storage/physics gate differs from this frozen meson worklist')
        forecasts=gate.get('per_batch_forecast_bytes',{})
        if (set(forecasts)!={row['batch'] for row in config['batches']}
                or any(type(value) is not int or value<=0 for value in forecasts.values())
                or sum(forecasts.values())!=gate['total_meson_forecast_bytes']):
            raise ValueError('Bulk storage forecast does not cover every fixed source batch')
        published=(state or {}).get('batches',{})
        additional=sum(value for batch,value in forecasts.items() if not published.get(batch,{}).get('published'))
    reserved, claims = additional_reservations(config)
    required = config['minimum_eos_free_bytes'] + reservation + additional + reserved
    return observation['free_bytes'] >= required and observation['free_files'] >= config['minimum_eos_free_files'], dict(
        free_bytes=observation['free_bytes'], protected_adaptive_bytes=reservation,
        meson_forecast_bytes=additional, additional_reserved_bytes=reserved,
        additional_reserved_worklists=claims, required_free_bytes=required)


def cumulative_result(config,state,batch,transport,primitive):
    """Keep the independent canary population when the bulk batch completes."""
    point=batch['point']
    condition=config['conditions']['sha256']
    sources=[]
    for other in config['batches']:
        production=batch_directory(config,other)/'production'
        report=production/'completed_points'/(point+'.json')
        if other['point']!=point or not report.is_file() or read(report).get('complete') is not True:
            continue
        sources.append(durable.authority(production,point,condition))
    if len(sources)<2:
        return primitive
    pending=durable.reconcile_aggregate(config,state,point,sources)
    signature=hashlib.sha256(json.dumps(sources,sort_keys=True).encode()).hexdigest()
    if state.get('published',{}).get(point,{}).get('sources_sha256')==signature:
        return state['published'][point]
    if pending is None:
        root=Path(config['directory'])/'aggregates'/point
        version=max((int(path.name[1:]) for path in root.glob('v*')),default=0)+1
        directory=root/f'v{version:04d}'
        guard=batch_directory(config,next(row for row in config['batches'] if row['operational_canary']))/'production'
        durable.aggregate_plan(dict(config,aggregation_guard=str(guard)),point,sources,directory)
        pending=durable.aggregate_record(directory,point,sources)
        durable.new(directory/'aggregate_intent.json',pending)
        state.setdefault('aggregates',{})[point]=pending
        durable.save(Path(config['directory'])/'state.json',state)
    directory=Path(pending['directory'])
    receipt=(existing_receipt(directory,directory/'aggregate.sub') if (directory/'submission.json').is_file()
             else durable.submission(config,directory,directory/'aggregate.sub'))
    pending['cluster']=receipt['cluster']
    durable.save(Path(config['directory'])/'state.json',state)
    if native_status(config,receipt)!='complete':
        return None
    from watch_dark_photon_production import completed_histogram
    plan=read(directory/'plan.json')
    result=completed_histogram(dict(source=dict(directory=directory,campaign=plan['campaign'],
        plan_sha256=durable.sha(directory/'plan.json'),seal_sha256=durable.sha(directory/'seal.json')),
        row=plan['points'][0],output=Path(plan['points'][0]['final_output_base'])),transport)
    if result is None:
        raise ValueError('Cumulative meson manager exited without its complete histogram receipt')
    result.update(sources_sha256=pending['sources_sha256'],source_plans=pending['source_plans'])
    return result


def publish_batch(config, state, batch):
    from watch_dark_photon_production import EosRpc, completed_histogram
    directory = batch_directory(config, batch)
    production = directory/'production'
    if not (production/'plan.json').is_file() or not (directory/'rates.json').is_file():
        return None
    plan = read(production/'plan.json')
    cache = directory/'readback'
    transport = EosRpc('root://eosuser.cern.ch', cache)
    try:
        result = completed_histogram(dict(source=dict(directory=production,campaign=plan['campaign'],
            plan_sha256=durable.sha(production/'plan.json'),seal_sha256=durable.sha(production/'seal.json')),
            row=plan['points'][0],output=Path(plan['points'][0]['final_output_base'])),transport)
        if result is None:
            return None
        result=cumulative_result(config,state,batch,transport,result)
        if result is None:
            return None
        unified = source_mechanism(config) == 'meson-radiative'
        prior_campaigns = []
        private_canary = (unified and batch['operational_canary']
                          and config.get('publish_canary_aliases', False) is False)
        if unified and not private_canary:
            ready, evidence = prior_publication_ready(config, batch)
            entry = state.setdefault('batches', {}).setdefault(batch['batch'], {})
            entry['prior_publication'] = evidence
            if not ready:
                entry.update(stage='waiting_for_prior_publication', ready_histograms=result)
                durable.save(Path(config['directory'])/'state.json', state)
                return None
            prior_campaigns = evidence['allowed_previous_campaigns']
            entry.pop('ready_histograms', None)
        from export_dark_photon_cross_sections import publish_file_rpc
        contract=read(directory/'gen/contract.json')
        selector=batch.get('target_mean_lab_flight_m')
        sample=dict(schema='shift-dark-photon-sample-v1', point=batch['point'], original_point=batch['point'],
            naming='mass-and-mean-lab-flight',selector_kind='mean_lab_flight',mass_gev=batch['mass_gev'],
            epsilon=batch['epsilon'],proper_ctau_mm=contract['physical_width_table']['ctau_mm'],
            sampling_epsilon=batch['epsilon'],target_mean_lab_flight_m=selector,mean_lab_flight_m=selector,
            reference_mean_lab_flight_m=batch['calibration_mean_beta_gamma']*contract['physical_width_table']['ctau_mm']/1000.,
            calibration_mean_beta_gamma=batch['calibration_mean_beta_gamma'],
            mean_lab_flight_is_estimate=selector is not None,
            production_mechanism=source_mechanism(config),
            calibration_sha256=config['calibration_authority']['sha256'])
        sample_path=directory/'sample.json'
        if sample_path.exists() and read(sample_path)!=sample:
            raise ValueError('Existing meson sample parameters changed')
        if not sample_path.exists():
            durable.new(sample_path,sample)
        anchor = None
        if unified and not private_canary:
            anchor = common_sample_anchor(config, batch, sample, sample_path, transport)
        elif not unified:
            publish_file_rpc(sample_path,config['eos_base']+'/darkphoton_'+batch['point']+'/sample.json')
        parameters=dict(sample,mean_lab_flight_reference_estimate_m=contract['mean_lab_flight_reference_estimate_m'],
            mean_beta_gamma_reference=contract['mean_beta_gamma_reference'],
            mean_beta_gamma_reference_standard_error=contract['mean_beta_gamma_reference_standard_error'],
            reference_definition='Independent fixed-M source estimate; shared LAB selector is the calibration anchor',
            fixed_source_collisions=batch['source_collisions'],accepted_signal_events=contract['requested_events'])
        if unified:
            parameters['global_anchor_reference'] = (anchor if anchor is not None else dict(
                scope='Operational canary campaign only; global sample anchor and latest alias are not promoted'))
        parameters_path=directory/'sample_parameters.json'
        if not parameters_path.exists():
            durable.new(parameters_path,parameters)
        elif read(parameters_path)!=parameters:
            raise ValueError('Per-source meson parameters changed')
        publish_file_rpc(parameters_path,batch['final_output_base']+'/sample_parameters.json')
        if private_canary:
            result.update(published_utc=datetime.now(timezone.utc).isoformat(), batch=batch['batch'],
                          alias_published=False, campaign_parameters_published=True)
            durable.save(directory/'publication.json', result)
            return result
        # Replacing another production mechanism requires an explicit reviewed
        # alias target in the request, rather than silently combining models.
        alias = config['eos_base']+'/darkphoton_'+batch['point']+'/latest'
        old = transport.alias_target(alias)
        allowed = list(config.get('allowed_previous_aliases',{}).get(batch['point'], []))
        allowed.extend(prior_campaigns)
        allowed.extend(row['campaign'] for row in config['batches'] if row['point']==batch['point'])
        if old is not None and old != result['campaign'] and old not in allowed:
            raise ValueError('Existing latest target needs reviewed ownership: '+str(old))
        transport.publish_alias(alias,result['campaign'],[None,result['campaign'],*allowed])
        result.update(published_utc=datetime.now(timezone.utc).isoformat(),batch=batch['batch'])
        if unified:
            result.update(alias_published=True, campaign_parameters_published=True,
                          global_anchor_reference=anchor)
        durable.save(directory/'publication.json',result)
        if result.get('sources_sha256'):
            state.setdefault('published',{})[batch['point']]=result
            state.setdefault('aggregates',{}).pop(batch['point'],None)
        return result
    finally:
        for path in cache.glob('*'):
            if path.is_file():
                path.unlink()


def terminal_record(receipt, native):
    record = dict(schema='shift-meson-successful-native-job-v1', submission=receipt,
                  terminal_native_ad=native)
    source = Path(receipt['task'])/'source_status.json'
    if source.is_file():
        record['source_status'] = dict(path=str(source), sha256=durable.sha(source))
    return record


def validate_terminal_record(receipt, record):
    native = record['terminal_native_ad']
    if (record.get('schema') != 'shift-meson-successful-native-job-v1'
            or record.get('submission') != receipt
            or native.get('ClusterId') != receipt['cluster'] or native.get('ProcId') != 0
            or native.get('Iwd') != receipt['task'] or native.get('JobStatus') != 4
            or native.get('ExitBySignal') is not False or native.get('ExitCode') != 0):
        raise ValueError('Cached meson success differs from its exact terminal native job')
    source = Path(receipt['task'])/'source_status.json'
    if source.is_file() or 'source_status' in record:
        authority = record.get('source_status', {})
        if (authority.get('path') != str(source) or durable.sha(source) != authority.get('sha256')
                or read(source).get('complete') is not True):
            raise ValueError('Cached completed meson source changed its native population')
    return record


def cached_terminal_record(config, receipt):
    seed = config.get('reviewed_terminal_native_jobs')
    if seed is None:
        return None
    reviewed = read_pinned(seed)
    if reviewed.get('schema') != 'shift-reviewed-meson-successful-native-seed-v1':
        raise ValueError('Unsupported reviewed meson terminal cache')
    record = reviewed['jobs'].get(str(receipt['cluster']))
    if record is not None:
        return validate_terminal_record(receipt, record)
    path = Path(config['directory'])/'native_terminal_cache'/f'{receipt["cluster"]}.json'
    return validate_terminal_record(receipt, read(path)) if path.is_file() else None


def production_queue_snapshot(config):
    """Read a large campaign once per poll, including its native DAG links."""
    if config.get('batch_native_queries') is not True:
        return None
    root=re.escape(str(Path(config['directory'])))
    constraint='regexp('+json.dumps('^'+root+'/(batches|aggregates|coverage)(/|$)')+',Iwd)'
    before=time.time()
    output=durable.run_condor(['/usr/bin/condor_q',*durable.scheduler_arguments(config),
        '-constraint',constraint,'-json','-attributes',
        'ClusterId,ProcId,DAGManJobId,JobStatus,Iwd,NumJobStarts,ExitCode,ExitBySignal,ShiftDPStatisticsTask'],
        timeout=45)
    return dict(before_epoch=before,rows=durable.parse_ads(output))


def native_status(config, receipt, queue_snapshot=None):
    constraint = 'ClusterId == '+str(receipt['cluster'])+' || DAGManJobId == '+str(receipt['cluster'])
    if config.get('reviewed_terminal_native_jobs') is not None:
        constraint += ' || Iwd == '+json.dumps(receipt['task'])
    # New submissions and external recovery directories need their own fresh
    # query. A snapshot from before submission cannot establish queue absence.
    submitted=receipt.get('utc')
    in_scope=Path(receipt['task']).is_relative_to(Path(config.get('directory','/nonexistent')))
    use_snapshot=(queue_snapshot is not None and in_scope and submitted is not None
        and datetime.fromisoformat(submitted).timestamp()<queue_snapshot['before_epoch'])
    if use_snapshot:
        rows=[row for row in queue_snapshot['rows'] if row['ClusterId']==receipt['cluster']
              or row.get('DAGManJobId')==receipt['cluster']
              or (config.get('reviewed_terminal_native_jobs') is not None
                  and row.get('Iwd')==receipt['task'])]
    else:
        rows=durable.jobs(config, constraint)
    if rows:
        managers=[row for row in rows if row['ClusterId']==receipt['cluster']]
        children=[row for row in rows if row['ClusterId']!=receipt['cluster']]
        if len(managers)!=1 or managers[0].get('Iwd')!=receipt['task'] or managers[0].get('ProcId')!=0:
            raise RuntimeError('Meson manager has an unexpected native identity')
        if any(row['JobStatus'] in (3,5) for row in rows):
            raise MesonJobFailure('Meson manager or child is removed or held: '+str(receipt['cluster']), rows)
        if managers[0]['JobStatus']==4:
            if managers[0].get('ExitBySignal') or managers[0].get('ExitCode')!=0:
                raise MesonJobFailure('Meson manager completed unsuccessfully; no automatic retry', rows)
            return 'complete'
        if any(row.get('Iwd')!=receipt['task'] for row in children):
            raise RuntimeError('Meson child has an unexpected persistent working directory')
        directory=Path(receipt['task'])
        health_path=directory/'manager_health.json'
        health=read(health_path) if health_path.exists() else {}
        if (directory/'pipeline.dag').is_file():
            from watch_dark_photon_statistics import execute_events
            health.update(executed_children=execute_events(directory),children=len(children),
                          checked_utc=datetime.now(timezone.utc).isoformat())
            if managers[0]['JobStatus']==2 and not children:
                since=health.setdefault('no_children_since_epoch',time.time())
                if time.time()-since>3600:
                    raise RuntimeError('Running meson DAG has no child jobs for over an hour')
            else:
                health.pop('no_children_since_epoch',None)
            durable.save(health_path,health)
        return 'active'
    if cached_terminal_record(config, receipt) is not None:
        return 'complete'
    output=durable.run_condor(['/usr/bin/condor_history',*durable.scheduler_arguments(config),
        str(receipt['cluster'])+'.0','-limit','1','-json'],timeout=45)
    rows=durable.parse_ads(output)
    if (len(rows)!=1 or rows[0].get('Iwd')!=receipt['task'] or rows[0].get('ProcId')!=0
            or rows[0].get('ClusterId')!=receipt['cluster']):
        raise RuntimeError('Meson manager is missing or failed; no automatic retry: '+str(receipt['cluster']))
    if rows[0].get('ExitBySignal') or rows[0].get('ExitCode')!=0:
        raise MesonJobFailure('Meson manager completed unsuccessfully; no automatic retry', rows)
    if config.get('reviewed_terminal_native_jobs') is not None:
        record = validate_terminal_record(receipt, terminal_record(receipt, rows[0]))
        path = Path(config['directory'])/'native_terminal_cache'/f'{receipt["cluster"]}.json'
        path.parent.mkdir(exist_ok=True)
        durable.new(path, record)
    return 'complete'


def existing_receipt(directory, description):
    receipt=read(Path(directory)/'submission.json')
    if (receipt.get('task')!=str(directory) or receipt.get('submit_sha256')!=durable.sha(description)
            or type(receipt.get('cluster')) is not int or receipt['cluster']<=0):
        raise ValueError('Existing meson submission receipt changed')
    return receipt


def batch_job_status(config, state, batch, entry, receipt, stage, queue_snapshot=None):
    """Keep a failed batch visible while independent batches can progress.

    Only a verified native failure is isolated. Scheduler errors, changed
    identities and missing receipts still stop the controller's poll.
    No failed job is released, replaced or counted as completed here.
    """
    try:
        return (native_status(config,receipt,queue_snapshot) if queue_snapshot is not None
                else native_status(config,receipt))
    except MesonJobFailure as error:
        failure = dict(stage=stage, cluster=receipt['cluster'], error=str(error),
                       native_jobs=error.jobs)
        failures = state.setdefault('failed_batches', {}).setdefault(batch['batch'], [])
        if failure not in failures:
            failures.append(failure)
        entry.update(stage=stage+'_failed', failure=failure)
        return 'failed'


def step(config,state):
    state.update(checked_utc=datetime.now(timezone.utc).isoformat(),heartbeat=state.get('heartbeat',0)+1)
    scope = completion_scope(config)
    if scope is not None:
        state['meson_completion'] = scope
        # Closing this finite qualified production does not close the missing
        # physics programme recorded by the independently reviewed catalogue.
        state['physics_complete'] = False
    entries=state.setdefault('batches',{})
    if scope is not None:
        for batch in config['batches']:
            entries.setdefault(batch['batch'], {})
        directory = Path(config['directory']) / 'coverage'
        receipt = (existing_receipt(directory, directory / 'coverage.sub')
                   if (directory / 'submission.json').is_file()
                   else durable.submission(config, directory, directory / 'coverage.sub'))
        state['coverage_audit'] = dict(cluster=receipt['cluster'], stage='running_or_queued')
        state['complete'] = False
        if native_status(config, receipt) == 'active':
            return state
        state['coverage_audit'] = dict(cluster=receipt['cluster'], **coverage_result(config))
    queue_snapshot=production_queue_snapshot(config)
    source_active, detector_active=0,0
    for batch in config['batches']:
        entry=entries.setdefault(batch['batch'],{})
        directory=batch_directory(config,batch)
        if entry.get('published'):
            continue
        source_receipt=directory/'submission.json'
        if not source_receipt.exists() and not (directory/'submission_intent.json').exists():
            continue
        source_job=(existing_receipt(directory,directory/'source.sub') if source_receipt.is_file()
                    else durable.submission(config,directory,directory/'source.sub'))
        entry.update(source_cluster=source_job['cluster'],stage='GEN')
        status=batch_job_status(config,state,batch,entry,source_job,'GEN',queue_snapshot)
        if status=='failed':
            continue
        if status=='active':
            heartbeat=directory/'source_heartbeat.json'
            if heartbeat.is_file():
                age=time.time()-datetime.fromisoformat(read(heartbeat)['heartbeat_utc']).timestamp()
                if age>600:
                    raise RuntimeError('Executing source heartbeat is stale: '+batch['batch'])
                entry['source_progress']=read(heartbeat)
            elif time.time()-datetime.fromisoformat(source_job['utc']).timestamp()>21600:
                raise RuntimeError('Source has no execution heartbeat after six hours: '+batch['batch'])
            source_active+=1
            continue
        if not (directory/'source_status.json').is_file() or not read(directory/'source_status.json').get('complete'):
            raise RuntimeError('Native source exited without complete source publication')
        production=directory/'production'
        if not (production/'submission.json').exists() and not (production/'submission_intent.json').exists():
            if detector_active>=config['simultaneous_detector_points']:
                continue
            if scope is not None:
                observation = durable.quota(config)
                allowed, evidence = storage_gate(config, observation,
                    bulk=not batch['operational_canary'], state=state)
                state['storage_admission'] = evidence
                if not allowed:
                    entry['stage'] = 'waiting_for_detector_storage'
                    continue
        if config.get('parallel_handoffs') is True:
            handoff_status,detector_job=parallel_handoff(config,state,batch,entry,queue_snapshot)
            if handoff_status!='complete':
                detector_active+=int(handoff_status=='active')
                continue
        else:
            detector_job=handoff(config,batch)
        entry.update(detector_cluster=detector_job['cluster'],stage='detector')
        if batch['batch'] in config.get('reviewed_detector_recoveries', {}):
            entry['reviewed_detector_recovery'] = config['reviewed_detector_recoveries'][batch['batch']]
        detector_status=batch_job_status(config,state,batch,entry,detector_job,'detector',queue_snapshot)
        if detector_status=='failed':
            continue
        if detector_status=='active':
            detector_active+=1
        published=publish_batch(config,state,batch)
        if published:
            entry.update(stage='published',published=published)
            durable.save(Path(config['directory'])/'state.json',state)
    canaries=[row for row in config['batches'] if row['operational_canary']]
    canary_ready=all(entries[row['batch']].get('published') for row in canaries)
    state['canary_published']=canary_ready
    observation=durable.quota(config)
    for batch in config['batches']:
        entry=entries[batch['batch']]
        if entry.get('source_cluster'):
            continue
        if not batch['operational_canary'] and not canary_ready:
            continue
        if source_active>=config['simultaneous_sources']:
            break
        allowed,evidence=storage_gate(config,observation,bulk=not batch['operational_canary'],state=state)
        state['storage_admission']=evidence
        if not allowed:
            continue
        directory=batch_directory(config,batch)
        receipt=durable.submission(config,directory,directory/'source.sub')
        entry.update(source_cluster=receipt['cluster'],stage='GEN')
        durable.save(Path(config['directory'])/'state.json',state)
        source_active+=1
    state['complete']=all(item.get('published') for item in entries.values())
    return state


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--seal',required=True)
    parser.add_argument('--action',choices=('source','controller','coverage','handoff'),required=True)
    parser.add_argument('--batch')
    parser.add_argument('--controller-config',type=Path)
    parser.add_argument('--controller-seal')
    parser.add_argument('--once',action='store_true')
    args=parser.parse_args()
    config=verify(args.directory.resolve(),args.seal)
    if args.controller_config is not None:
        if args.action!='controller' or args.controller_seal is None:
            raise ValueError('Only the controller may adopt a separately sealed operational configuration')
        config=operational_configuration(config,args.controller_config.resolve(),args.controller_seal)
    if args.action in ('source','handoff'):
        rows=[row for row in config['batches'] if row['batch']==args.batch]
        if len(rows)!=1:
            raise ValueError('Source batch has no unique frozen owner')
        if args.action=='source':
            source(config,args.seal,rows[0])
        else:
            if config.get('parallel_handoffs') is not True:
                raise ValueError('This campaign has no frozen parallel handoff policy')
            handoff(config,rows[0])
        return
    if args.action == 'coverage':
        coverage(config)
        return
    path=args.directory/'state.json'
    with (args.directory/'controller.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        state=read(path) if path.exists() else {}
        while True:
            try:
                state.pop('error',None)
                state=step(config,state)
            except Exception as error:
                state.update(error=repr(error),checked_utc=datetime.now(timezone.utc).isoformat())
            durable.save(path,state)
            if args.once or state.get('complete'):
                return
            time.sleep(60)


if __name__=='__main__':
    main()
