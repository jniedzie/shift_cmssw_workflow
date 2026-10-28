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
from dark_photon_senscalc import REFERENCE_MECHANISMS
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


MECHANISMS = ('eta-etaprime-radiative', 'meson-radiative', *REFERENCE_MECHANISMS)


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


def directory_condition(directory, attribute='Iwd'):
    """Match native paths without double-escaping regex backslashes in ClassAds."""
    path = str(directory)
    if attribute == 'Iwd':
        prefix = path+'/'
        return '('+attribute+' == '+json.dumps(path)+' || substr('+attribute+',0,'+str(len(prefix))+') == '+json.dumps(prefix)+')'
    if attribute != 'Args' or not re.fullmatch(r'[A-Za-z0-9_./-]+', path):
        raise ValueError('Unsupported native directory argument match')
    return 'regexp('+json.dumps(path.replace('.', '[.]'))+',Args)'


def source_mechanism(config):
    mechanism = config.get('production_mechanism', 'eta-etaprime-radiative')
    if mechanism not in MECHANISMS:
        raise ValueError('Unsupported meson source mechanism')
    return mechanism


def reference_source(config):
    """Check the reference backend's independent source and GEN authorities."""
    if source_mechanism(config) not in REFERENCE_MECHANISMS:
        return None
    if config.get('jaw_source_profile') is None:
        raise ValueError('Reference detector production requires the pinned jaw-placement profile before GEN')
    pinned_path(config['jaw_source_profile'])
    settings=config['reference_source']
    profile=read_pinned(settings['runtime_profile'])
    source_settings=pinned_path(settings['settings'])
    from dark_photon_detector_runtime import validate_profile
    validate_profile(config['detector_runtime'])
    if (profile.get('schema')!='shift-senscalc-source-capsule-v1' or
            settings.get('rate_profile') not in ('pointlike','measured-form-factor') or
            type(settings.get('proposal_probability')) not in (int,float) or
            not 1.e-6<=settings['proposal_probability']<=.01):
        raise ValueError('Require the explicit qualified source capsule, rate profile and proposal')
    if source_mechanism(config)==REFERENCE_MECHANISMS[1]:
        from dark_photon_bremsstrahlung import SourceSampler
        if not settings.get('bremsstrahlung_profiles'):
            raise ValueError('Combined reference source requires frozen bremsstrahlung profiles')
        for authority in settings['bremsstrahlung_profiles'].values():
            path=pinned_path(authority);SourceSampler(read(path),path.parent)
    return dict(settings=settings,profile=profile,source_settings=str(source_settings))


def reference_bank_arguments(reference,batch):
    """Reuse a pinned bank without adding exposure, or request independent draws."""
    settings=reference['settings']
    bank=settings.get('saved_banks',{}).get(batch['batch'])
    if bank is not None:
        directory=Path(bank['directory'])
        manifest=read_pinned(bank['manifest'])
        if (not directory.is_absolute() or Path(bank['manifest']['path'])!=directory/'manifest.json' or
                bank.get('additional_source_collisions')!=0 or
                manifest.get('seed')!=batch['source_seed'] or manifest.get('mass_gev')!=batch['mass_gev'] or
                manifest.get('source_trials')!=batch['source_collisions']):
            raise ValueError('Reused reference bank differs from its original source exposure')
        return ['--sample',str(directory)]
    return ['--source-runtime-profile',str(pinned_path(settings['runtime_profile'])),
        '--source-settings',reference['source_settings'],'--source-collisions',str(batch['source_collisions']),
        '--source-seed',str(batch['source_seed']),'--mass',str(batch['mass_gev']),
        '--proposal-probability',str(settings['proposal_probability'])]


def fresh_production_authority(config, reader=None):
    """Verify a new campaign without inheriting cancelled exposure or claims."""
    authority = config.get('fresh_production_authority')
    if authority is None:
        return None
    read_record = reader or read_pinned
    record = read_record(authority)
    forbidden = ('prior_meson_publication', 'prior_meson_controller_directory',
                 'adaptive_directory', 'adaptive_terminal_completion',
                 'reviewed_prior_meson_retirement')
    if (record.get('schema') != 'shift-fresh-dp-production-authority-v1'
            or record.get('campaign_prefix') != config['campaign_prefix']
            or record.get('cancelled_exposure_credit') != 0
            or record.get('resume_cancelled_deployments') is not False
            or record.get('preserve_previous_ready_outputs') is not True
            or any(key in config for key in forbidden)):
        raise ValueError('Fresh production must preserve previous outputs without inheriting cancelled work')
    registration = read_record(record['storage_registration'])
    claims = registration.get('peer_reservations', [])
    if (registration.get('schema') != 'shift-fresh-dp-storage-registration-v1'
            or registration.get('complete') is not True
            or registration.get('campaign_prefix') != config['campaign_prefix']
            or not isinstance(claims, list)
            or len({row.get('name') for row in claims}) != len(claims)
            or any(not row.get('name') or type(row.get('reserved_bytes')) is not int
                   or row['reserved_bytes'] < 0 for row in claims)
            or not registration.get('native_queue_evidence')
            or not registration.get('registered_controller_directories')):
        raise ValueError('Fresh production requires its verified shared-storage registration')
    if reader is None:
        pinned_path(registration['native_queue_evidence'])
    else:
        reader(registration['native_queue_evidence'])
    return dict(authority=record, storage_registration=registration,
                reserved_peer_bytes=sum(row['reserved_bytes'] for row in claims))


def prior_meson_configuration(config):
    """The previous model owns each point until all its batches close."""
    if source_mechanism(config) != 'meson-radiative':
        return None
    if fresh_production_authority(config) is not None:
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
        previous_reference = read_pinned(previous['lifetime_reference_authority'])
        current_reference = read_pinned(config['lifetime_reference_authority'])
        for row in config['batches']:
            if row['seed'] in old_seeds or row['run_number'] in old_runs:
                raise ValueError('Replacement meson sources reuse prior seeds or runs')
            expected = point_from_reference(previous_reference, row)
            actual = point_from_reference(current_reference, row)
            if expected != actual:
                raise ValueError('Replacement meson model changed its physical point or LAB reference')
    return previous


def prior_publication_ready(config, batch):
    """Check fresh prior ownership without granting credit for another point."""
    previous = prior_meson_configuration(config)
    if previous is None:
        fresh = fresh_production_authority(config)
        allowed = (fresh['authority'].get('allowed_previous_campaigns', {}).get(batch['point'], [])
                   if fresh is not None else [])
        if (not isinstance(allowed, list) or any(not isinstance(value, str) or not value for value in allowed)):
            raise ValueError('Fresh publication requires explicit prior alias ownership')
        return True, dict(allowed_previous_campaigns=allowed,
                          fresh_campaign_without_prior_exposure=fresh is not None)
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
        alternatives.append(directory_condition(directory))
        alternatives.append(directory_condition(directory, 'Args'))
    for cluster in sorted({row['ClusterId'] for row in records}):
        alternatives += ['ClusterId == '+str(cluster), 'DAGManJobId == '+str(cluster)]
    if check_queue and durable.jobs(config, ' || '.join(alternatives)):
        raise RuntimeError('Retired meson controller, publisher or worker is still live')
    return dict(campaigns=campaigns, published_batches=published_batches,
                retired_directories=sorted(roots), allocation_canceled=True)


def reviewed_source_closure(config, authority, expected):
    """Cancel only explicitly closed, never-submitted source allocations."""
    proof = read_pinned(authority)
    peer = read_pinned(expected)
    root = Path(peer['directory'])
    owner_pin = proof['controller_configuration']
    owner = read_pinned(owner_pin)
    owner_directory = pinned_path(owner_pin).parent
    identity = meson_controller_configuration(peer, owner_directory)
    receipt = read_pinned(proof['controller_submission'])
    names = proof.get('canceled_batches')
    batches = {row['batch']: row for row in peer['batches']}
    if (proof.get('schema') != 'shift-reviewed-meson-source-closure-v1'
            or proof.get('scope') != 'future-unsubmitted-sources-only'
            or proof.get('peer_configuration') != expected
            or proof.get('source_namespaces_remain_reserved') is not True
            or owner.get('source_admission_closed') is not True or owner.get('directory') != str(root)
            or receipt.get('task') != str(owner_directory)
            or receipt.get('submit_sha256') != durable.sha(identity['submit'])
            or pinned_path(proof['controller_submission']) != owner_directory/'submission.json'
            or not isinstance(names, list) or not names or len(set(names)) != len(names)
            or not set(names).issubset(batches)):
        raise ValueError('Source closure differs from its exact closed controller or source allocation')
    for name in names:
        directory = root/'batches'/name
        paths = [directory/'source_status.json', directory/'source_heartbeat.json']
        paths += [directory/stage/file for stage in ('', 'production', 'handoff')
                  for file in ('submission.json', 'submission_intent.json')]
        if any(path.exists() for path in paths):
            raise ValueError('Canceled source batch was already attempted: '+name)
    constraint = directory_condition(root)+' || '+directory_condition(root, 'Args')
    for row in durable.jobs(config, constraint):
        path = Path(row['Iwd'])
        if not path.is_relative_to(root) and path != owner_directory:
            raise RuntimeError('Unexpected live owner prevents closed-source storage credit')
        if any(path == root/'batches'/name or path.is_relative_to(root/'batches'/name) for name in names):
            raise RuntimeError('Canceled source batch has a live job')
    return names


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
             'mean_lab_flight_is_estimate')
    if any(existing.get(key) != sample.get(key) for key in exact):
        raise ValueError('Existing global sample anchor has different physical point or LAB reference')
    for key in ('proper_ctau_mm',):
        value = existing.get(key)
        if (type(value) not in (int, float) or not math.isfinite(value)
                or not math.isclose(value, sample[key], rel_tol=1.e-10, abs_tol=0.)):
            raise ValueError('Existing global sample anchor has a different physical lifetime/LAB reference')
    # Independent source calibrations have different mean boosts. Preserve the
    # original LAB anchor when mass, epsilon, proper lifetime and selector agree;
    # check each estimate against its own calibration instead of equating them.
    for record in (existing, sample):
        boost = record.get('calibration_mean_beta_gamma')
        flight = record.get('reference_mean_lab_flight_m')
        if (type(boost) not in (int, float) or not math.isfinite(boost) or boost <= 0
                or type(flight) not in (int, float) or not math.isfinite(flight)
                or not math.isclose(flight, boost*record['proper_ctau_mm']/1000.,
                                    rel_tol=1.e-10, abs_tol=0.)):
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
    if not matched and config.get('historical_anchor_calibration'):
        authority=config['historical_anchor_calibration']
        calibration=read_pinned(authority)
        references=[row for row in calibration.get('points',[]) if row.get('mass_gev')==sample['mass_gev']]
        if (creator==calibration.get('production_mechanism') and existing.get('calibration_sha256')==authority['sha256']
                and len(references)==1):
            reference=references[0]
            expected_ctau=reference['physical_width_reference']['ctau_mm']*(reference['reference_epsilon']/sample['epsilon'])**2
            matched=(math.isclose(existing['calibration_mean_beta_gamma'],reference['mean_beta_gamma_reference'],rel_tol=1.e-10)
                     and math.isclose(existing['proper_ctau_mm'],expected_ctau,rel_tol=1.e-10))
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
    return point_from_reference(authority, batch)


def point_from_reference(authority, batch):
    """Check a physical point after its immutable reference was read once."""
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


def reviewed_coverage_recovery(config):
    """Adopt a separately executed audit repair; retain the failed original."""
    authority = config.get('reviewed_coverage_recovery')
    if authority is None:
        return None
    proof = read_pinned(authority)
    root = Path(config['directory'])
    original = read_pinned(proof['original_submission'])
    failed = read_pinned(proof['original_status'])
    native = read_pinned(proof['original_native_failure'])
    receipt = read_pinned(proof['replacement_submission'])
    directory = Path(receipt['task'])
    if (proof.get('schema') != 'shift-reviewed-meson-coverage-recovery-v1'
            or proof.get('scope') != 'coverage-only'
            or proof.get('worklist_sha256') != durable.sha(root/'config.json')
            or pinned_path(proof['original_submission']) != root/'coverage/submission.json'
            or pinned_path(proof['original_status']) != root/'coverage/coverage_status.json'
            or failed.get('complete') is not False or not failed.get('error')
            or native.get('ClusterId') != original['cluster'] or native.get('ProcId') != 0
            or native.get('Iwd') != original['task'] or native.get('JobStatus') not in (3, 4, 5)
            or (not native.get('ExitBySignal') and native.get('ExitCode', 0) == 0)
            or directory == root/'coverage' or not directory.is_relative_to(root.parent)
            or pinned_path(proof['replacement_submission']) != directory/'submission.json'
            or receipt.get('submit_sha256') != durable.sha(directory/'worker.sub')
            or pinned_path(proof['replacement_auditor']).parent != directory
            or pinned_path(proof['replacement_status']).parent != directory):
        raise ValueError('Reviewed coverage repair differs from its failed audit or frozen science')
    if durable.jobs(config, 'ClusterId == '+str(original['cluster'])+
                    ' || DAGManJobId == '+str(original['cluster'])):
        raise RuntimeError('Original failed coverage audit is still live')
    return proof


def coverage_result(config):
    directory = Path(config['directory']) / 'coverage'
    recovery = reviewed_coverage_recovery(config)
    status = (read_pinned(recovery['replacement_status']) if recovery else
              read(directory / 'coverage_status.json'))
    report = read_pinned(status['report'])
    request = coverage_request(config)
    scope = completion_scope(config)
    if (status.get('complete') is not True
            or report.get('schema') != 'shift-dark-photon-meson-coverage-v1'
            or report.get('model_sha256') != durable.sha(config['model_card'])
            or report.get('particle_data_sha256') != request['particle_data']['sha256']
            or report.get('source_inventory_sha256') != request.get('source_inventory', {}).get('sha256')
            or report.get('code_sha256') != (recovery['replacement_auditor']['sha256'] if recovery else
                durable.sha(Path(config['tools']) / 'audit_dark_photon_meson_coverage.py'))
            or report.get('mass_points_gev') != request['masses_gev']
            or report.get('included_channels') != scope['included_channels']
            or report.get('remaining_channels') != scope['remaining_channels']
            or (recovery and (report.get('completion_scope_sha256') != scope['scope_sha256']
                             or not isinstance(report.get('inventory_remaining_channels'), list)))
            or report.get('physics_complete') is not False):
        raise ValueError('Detached meson coverage result differs from its reviewed scope and inputs')
    return dict(complete=True, report=status['report'],
                candidate_transitions=len(report['candidate_transitions']),
                remaining_channels=report['remaining_channels'], physics_complete=False,
                inventory_remaining_channels=report.get('inventory_remaining_channels', report['remaining_channels']),
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
        if config.get('meson_completion_scope') is not None:
            argv.extend(['--completion-scope', str(pinned_path(config['meson_completion_scope']))])
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
                                   'reviewed_prior_meson_retirement', 'source_admission_closed',
                                   'reviewed_coverage_recovery', 'operational_handoffs', 'regroup_complete_points',
                                   'merge_source_histograms', 'aggregate_group_events',
                                   'aggregate_storage_reservation_bytes', 'aggregation_storage_registration',
                                   'historical_anchor_calibration', 'operational_stage_timeouts_seconds',
                                   'reviewed_regrouping_canary_recovery', 'future_detector_runtime',
                                   'reviewed_runtime_recovery', 'reviewed_remaining_storage_authority',
                                   'reviewed_unsubmitted_runtime_recoveries',
                                   'qualified_global_detector_canary', 'maximum_new_detector_handoffs_per_poll'}:
        raise ValueError('Replacement controller added unreviewed production settings')
    if 'source_admission_closed' in config and config['source_admission_closed'] is not True:
        raise ValueError('A source-admission closure may only stop future submissions')
    stage_budgets = config.get('operational_stage_timeouts_seconds')
    if stage_budgets is not None and stage_budgets != [43200,7200,14400,14400]:
        raise ValueError('Unreviewed operational detector stage budgets')
    if ('maximum_new_detector_handoffs_per_poll' in config
            and (type(config['maximum_new_detector_handoffs_per_poll']) is not int
                 or config['maximum_new_detector_handoffs_per_poll'] != 10)):
        raise ValueError('Unreviewed detector handoff start throttle')
    global_canary=config.get('qualified_global_detector_canary')
    if global_canary is not None:
        from prepare_dark_photon_production import qualified_detector_gates
        authority=read_pinned(global_canary)
        profile=read_pinned(config['future_detector_runtime']['profile'])
        qualified_detector_gates(dict(points=[],detector_runtime=profile),None,pinned_path(global_canary))
        scheduling=authority.get('operational_scheduling',{})
        for key in ('simultaneous_detector_points','maximum_active_detector_workers',
                    'maximum_new_detector_handoffs_per_poll','dag_max_idle'):
            if scheduling.get(key)!=config.get(key):
                raise ValueError('Global detector canary scheduling authority differs: '+key)
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
    from prepare_dark_photon_meson_production import source_execution_limits, detector_point_limit
    source_limit=source_execution_limits(original)['maximum_simultaneous_sources']
    for key,maximum in [('simultaneous_sources',source_limit),
                        ('simultaneous_detector_points',detector_point_limit(original))]:
        if key in original and (type(config.get(key)) is not int or not 1<=config[key]<=maximum):
            raise ValueError('Replacement controller scheduling limit is outside the reviewed grid: '+key)
    aggregation_reservation(config)
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
            added = set(row)-set(old)
            if (len(added) != 1 or not added <= {'retirement_authority', 'source_closure_authority'}
                    or {key:value for key,value in row.items() if key not in added} != old):
                raise ValueError('Meson registration changed or repeated an existing protected claim')
            expected = row.get('peer_configuration') or row.get('completion_authority')
            if 'retirement_authority' in added:
                reviewed_meson_retirement(candidate, row['retirement_authority'], expected, check_queue=False)
            else:
                reviewed_source_closure(candidate, row['source_closure_authority'], expected)
    additions = [row for row in after if row['name'] not in {old['name'] for old in before}]
    directories = set()
    for row in before:
        authority = row.get('peer_configuration', row.get('completion_authority', {}))
        if authority.get('path'):
            directories.add(read_pinned(authority).get('directory'))
    for row in additions:
        if any(row.get(key) is not None for key in
               ('completion_authority', 'retirement_authority', 'source_closure_authority')):
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
    if mechanism in REFERENCE_MECHANISMS:
        reference=reference_source(config)
        source_manifest=read(gen/'source_manifest.json')
        if (source_manifest.get('seed')!=batch['source_seed'] or
                source_manifest.get('settings_sha256')!=reference['settings']['settings']['sha256'] or
                source_manifest.get('runtime_receipt_sha256')!=reference['profile']['runtime_receipt_sha256'] or
                source_manifest.get('proposal_probability')!=reference['settings']['proposal_probability'] or
                contract.get('rate_profile')!=reference['settings']['rate_profile'] or
                contract.get('radiative_model_sha256')!=durable.sha(config['model_card'])):
            raise ValueError('Reference source differs from its frozen bank prescription')
        if mechanism==REFERENCE_MECHANISMS[1]:
            path=pinned_path(reference['settings']['bremsstrahlung_profiles'][str(batch['mass_gev'])])
            expected=read(path);expected['table_directory']='bremsstrahlung_inputs'
            if read(gen/'bremsstrahlung.json')!=expected:
                raise ValueError('Reference source changed its frozen bremsstrahlung component')
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
                or path.name in ('meson_source_ledger.jsonl', 'meson_source_ledger.jsonl.gz', 'source_ledger.jsonl')):
            continue
        if path.stat().st_size > 10000000:
            raise ValueError('Unexpected large file in compact GEN metadata: ' + str(path))
        result.append(path)
    if sum(path.stat().st_size for path in result) > 64000000:
        raise ValueError('Compact GEN metadata exceeds its reviewed AFS allowance')
    return result


def retain_source_failure(config,batch,gen,directory):
    """Keep the actual failed scientific inputs before worker scratch is removed."""
    from export_dark_photon_cross_sections import publish_file_rpc
    gen=Path(gen);directory=Path(directory)
    archive=gen.parent/'failed_GEN.tar.gz'
    with tarfile.open(archive,'w:gz') as target:
        for path in sorted(gen.rglob('*')):
            if path.is_file() and '__pycache__' not in path.parts and path.suffix!='.pyc':
                target.add(path,arcname=str(path.relative_to(gen)),recursive=False)
    receipt=dict(archive_sha256=durable.sha(archive),bytes=archive.stat().st_size,
        source_collisions_redrawn=0,point=batch['point'],original_run_number=batch['run_number'],
        gen_sha256=durable.sha(gen/'gen.root') if (gen/'gen.root').is_file() else None)
    try:
        namespace=hashlib.sha256(config['directory'].encode()).hexdigest()[:12]
        destination=config['failure_base'].rstrip('/')+'/'+namespace+'/'+batch['batch']+'/failed_GEN.tar.gz'
        receipt['publication']=publish_file_rpc(archive,destination)
    except Exception as error:
        receipt['publication_error']=repr(error)
        # A rare EOS outage must not discard the sole failing event with the
        # TemporaryDirectory. Keep it persistently until reviewed and migrated.
        fallback=directory/'failed_GEN.tar.gz';shutil.copy2(archive,fallback)
        if durable.sha(fallback)!=receipt['archive_sha256']:
            raise ValueError('Failed GEN fallback changed while copying') from error
        receipt.update(retained_local_archive=str(fallback),
            cleanup_condition='Review the failure, then upload and independently verify the archive before deleting this fallback')
    durable.save(directory/'GEN_failure_retention.json',receipt)


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
            reference=reference_source(config)
            release, runtime_archive = ((None,None) if reference else unpack_gen_runtime(config, scratch))
            gen = scratch / 'gen'
            probe.update(gen=gen,phase='GEN')
            runner = Path(config['tools']) / ('run_dark_photon_senscalc_gen.py' if reference else 'run_dark_photon_meson_gen.py')
            argv = ['python3', str(runner), '--mass', str(batch['mass_gev']), '--epsilon', str(batch['epsilon']),
                '--source-collisions', str(batch['source_collisions']), '--seed', str(batch['seed']),
                '--run-number', str(batch['run_number']), '--model-card', config['model_card'],
                '--darkcast-path', config['darkcast_path'], '--darkcast-source-receipt', config['darkcast_source_receipt'],
                '--timeout', str(config['gen_timeout_seconds']), '--output', str(gen)]
            if config.get('jaw_source_profile') is not None:
                argv.extend(['--jaw-profile', str(pinned_path(config['jaw_source_profile']))])
            if reference:
                profile_path=scratch/'detector_runtime_profile.json'
                durable.new(profile_path,config['detector_runtime'])
                argv=['/usr/bin/python3',str(Path(config['tools'])/'dark_photon_detector_runtime.py'),
                    '--profile',str(profile_path),'--entry',str(runner),'--',
                    *reference_bank_arguments(reference,batch),
                    '--rate-profile',reference['settings']['rate_profile'],
                    '--epsilon',str(batch['epsilon']),'--seed',str(batch['seed']),'--run-number',str(batch['run_number']),
                    '--radiative-model',config['model_card'],'--darkcast-path',config['darkcast_path'],
                    '--darkcast-source-receipt',config['darkcast_source_receipt'],
                    '--detector-runtime-profile',str(profile_path),'--output',str(gen),
                    '--timeout',str(config['gen_timeout_seconds'])]
                if source_mechanism(config)==REFERENCE_MECHANISMS[1]:
                    authority=reference['settings']['bremsstrahlung_profiles'][str(batch['mass_gev'])]
                    argv.extend(['--bremsstrahlung-profile',str(pinned_path(authority))])
                if config.get('jaw_source_profile') is not None:
                    argv.extend(['--jaw-profile',str(pinned_path(config['jaw_source_profile']))])
            rename = ''
            if runtime_archive is not None:
                runtime = gen_runtime(config)
                argv.extend(['--runtime-provenance', runtime['provenance']['path'],
                             '--runtime-archive', str(runtime_archive)])
                rename = ' && scram b ProjectRename'
            shell = (shlex.join(argv) if reference else
                'export SCRAM_ARCH=el9_amd64_gcc13 && source /cvmfs/cms.cern.ch/cmsset_default.sh && cd ' +
                shlex.quote(str(release / 'src')) + rename +
                ' && eval "$(scram runtime -sh)" && cd ' + shlex.quote(str(scratch)) +
                ' && export PYTHONDONTWRITEBYTECODE=1 && exec ' + shlex.join(argv))
            with (directory / 'logs/GEN.log').open('x') as log:
                result = subprocess.run(['/bin/bash', '-c', shell], cwd=directory,
                    env=stage.native_env(), stdout=log, stderr=subprocess.STDOUT,
                    timeout=config['gen_timeout_seconds'] + 900, check=False)
            if reference:
                # Preserve the original bytes of native and auxiliary proposals;
                # later runtimes audit these inputs instead of replacing them.
                from dark_photon_senscalc import ledger_digest
                for name in ('source_ledger.jsonl','bremsstrahlung_proposals.jsonl'):
                    ledger=gen/'source_bank'/name
                    if not ledger.is_file():continue
                    compressed=ledger.with_suffix('.jsonl.gz')
                    with ledger.open('rb') as incoming,compressed.open('xb') as raw:
                        with gzip.GzipFile(fileobj=raw,mode='wb',mtime=0) as outgoing:
                            shutil.copyfileobj(incoming,outgoing,1048576)
                    if ledger_digest(compressed)!=durable.sha(ledger):
                        raise ValueError('Source-ledger compression changed original proposal bytes')
                    ledger.unlink()
                duplicate=gen/'source_ledger.jsonl'
                if duplicate.is_file():duplicate.unlink()
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
            try:
                result.check_returncode()
                row = adopted_row(batch, gen, config)
                probe['phase']='GEN_publication'
                row['gen_argv'] = ['python3', str(runner)]
                publication, parent = stage.publish_gen(row, dict(config, scratch_base=str(scratch),
                    retain_local_gen_root=False, eos_quota_space='/eos/user/j/jniedzie/'))
            except Exception:
                if gen.is_dir():retain_source_failure(config,batch,gen,directory)
                raise
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
            if reference:
                status['new_source_collisions']=(0 if batch['batch'] in reference['settings'].get('saved_banks',{})
                                                 else batch['source_collisions'])
    except Exception as error:
        status.update(error=repr(error), traceback=traceback.format_exc()[-10000:])
        raise
    finally:
        stop.set()
        observer.join(timeout=10)
        durable.save(status_path, status)
    return status


def prepare_material_admission(config, batch, status):
    """Use the qualified capsule once, before selected detector chunks exist."""
    from prepare_dark_photon_material_source import immutable
    directory = batch_directory(config, batch)
    gen = directory/'gen'
    settings = config.get('material_admission')
    if not isinstance(settings,dict) or config.get('detector_runtime') is None:
        raise ValueError('New DP adoption needs shared material admission and its pinned detector runtime')
    request = dict(parent=status['parent'], gen_directory=str(gen),
        contract_sha256=durable.sha(gen/'contract.json'),
        gen_publication=status['publication'], output_base=batch['final_output_base'],
        geometry=copy.deepcopy(settings['geometry']), frame=copy.deepcopy(settings['frame']),
        cohort_id=batch['campaign']+':'+batch['batch'],
        selection_seed=settings.get('selection_seed','dp-material-'+batch['campaign'])+':'+batch['batch'])
    if 'material_event_timeout_seconds' in settings:
        request['material_event_timeout_seconds']=settings['material_event_timeout_seconds']
    request_path=directory/'material_admission_request.json'
    profile_path=directory/'material_runtime_profile.json'
    immutable(request_path,request)
    immutable(profile_path,config['detector_runtime'])
    tools=Path(config['tools'])
    from dark_photon_detector_runtime import clean_environment
    invocation=['/usr/bin/python3',str(tools/'dark_photon_detector_runtime.py'),
        '--profile',str(profile_path),'--entry',str(tools/'prepare_dark_photon_material_source.py'),
        '--','--request',str(request_path)]
    # This entry needs native FWLite/ROOT. Its private runtime does not load the
    # mutable shared release or alter the already saved lifetime/source draws.
    with (directory/'logs/material_admission.log').open('a') as log:
        subprocess.run(invocation,cwd=directory,check=True,
            stdout=log,stderr=subprocess.STDOUT,env=clean_environment(),
            timeout=config.get('material_timeout_seconds',3600))
    descriptor_path=gen/'material_selected_source.json'
    receipt=read(gen/'material_selection_receipt.json')
    if receipt.get('complete') is not True or receipt['descriptor_sha256'] != durable.sha(descriptor_path):
        raise ValueError('Material admission did not finish its immutable publication')
    return read(descriptor_path)


def qualify_gen_source(config, batch):
    """Audit one independent mass cohort and freeze admission without SIM."""
    from dark_photon_material_selection import bind
    directory = batch_directory(config, batch)
    status = read(directory/'source_status.json')
    if status.get('complete') is not True:
        raise ValueError('GEN qualification requires complete published source metadata')
    for path, expected in status['metadata_sha256'].items():
        if durable.sha(path) != expected:
            raise ValueError('GEN qualification metadata changed')
    row = adopted_row(batch, directory/'gen', config)
    descriptor = prepare_material_admission(config, batch, status)
    row = bind(row, status['parent'], descriptor)
    proof = dict(complete=True, source_status_sha256=durable.sha(directory/'source_status.json'),
        material_receipt_sha256=durable.sha(directory/'gen/material_selection_receipt.json'),
        full_gen_events=row['full_gen_events'], selected_events=row['events'],
        model_card_sha256=durable.sha(config['model_card']),
        detector_runtime_sha256=config['detector_runtime']['runtime_sha256'],
        gen_runtime_sha256=config['gen_runtime']['sha256'],
        source_qualification_only=True, zero_admission_is_valid=row['events'] == 0)
    path = directory/'gen_qualification.json'
    if path.exists():
        if read(path) != proof:
            raise ValueError('GEN qualification receipt changed')
    else:
        durable.new(path, proof)
    return proof


def adoption_sources(config, names, operational_names, previous=None):
    """Keep scientific sources original and freeze orchestration from this owner.

    An interrupted handoff keeps its already pinned operational helpers, even
    when a later controller resumes it from another sealed directory.
    """
    frozen = {}
    old = (previous or {}).get('frozen_dependencies', {}).get('sources', {})
    for name in sorted(names):
        if name in operational_names and name in old:
            item = old[name]
            path = Path(item['frozen_copy'])
            if not path.is_absolute() or path.name != name or durable.sha(path) != item['sha256']:
                raise ValueError('Previously adopted operational helper changed: '+name)
            frozen[name] = copy.deepcopy(item)
            continue
        source = (Path(__file__).resolve().parent if name in operational_names else Path(config['tools']))/name
        frozen[name] = dict(frozen_copy=str(source), sha256=durable.sha(source))
    return frozen


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
    from dark_photon_material_selection import bind
    descriptor=prepare_material_admission(config,batch,status)
    row=bind(row,status['parent'],descriptor)
    row['material_selected_source_sha256']=durable.sha(directory/'gen/material_selected_source.json')
    actual_source_storage(config, batch, row, status)
    chunk_size=batch.get('events_per_job',config['events_per_job'])
    if math.ceil(row['events']/chunk_size) + 1 > batch['detector_job_capacity']:
        raise ValueError('Actual N needs a separately reviewed larger detector namespace; do not truncate it')
    physical = dict(points=[row], production_mechanism=source_mechanism(config),
        physical_width_preflight_passed=True)
    if source_mechanism(config) in REFERENCE_MECHANISMS:
        physical['senscalc_width_qualification']=grid.senscalc_width_qualification([row])
    else:
        physical['meson_width_qualification']=grid.meson_width_qualification([row])
    plan = grid.plan_detector_stage(physical, batch['campaign'], config['eos_base'],
        events_per_job=chunk_size, group_events=config['group_events'],
        detector_job_base=batch['detector_job_base'])
    plan['runtime'] = dict(cmssw_base=config['cmssw_base'])
    if config.get('detector_runtime') is not None:
        # The ordinary preparer validates this separately qualified capsule.
        plan['detector_runtime']=copy.deepcopy(config['detector_runtime'])
    from run_dark_photon_meson_gen import SOURCES as MESON_SOURCES
    from prepare_dark_photon_material_source import DEPENDENCIES as MATERIAL_SOURCES
    sources=set(grid.SOURCES)|set(MESON_SOURCES)|set(MATERIAL_SOURCES)
    if source_mechanism(config) in REFERENCE_MECHANISMS:
        from run_dark_photon_senscalc_gen import SOURCES as REFERENCE_SOURCES
        sources.update(REFERENCE_SOURCES)
    preparation = directory / 'preparation.json'
    previous = read(preparation) if preparation.exists() else None
    plan['frozen_dependencies'] = dict(sources=adoption_sources(
        config,sources,set(grid.RUNTIME_HELPER_SOURCES),previous),
        templates={name:dict(frozen_copy=str(path),sha256=durable.sha(path))
            for path in sorted((Path(config['directory'])/'templates').glob('step*.py')) for name in [path.name]})
    # Generic downstream workers consume the real audited GEN source directly.
    row = plan['points'][0]
    entry='run_dark_photon_senscalc_gen.py' if source_mechanism(config) in REFERENCE_MECHANISMS else 'run_dark_photon_meson_gen.py'
    row['gen_argv'] = ['python3', str(Path(config.get('physics_tools',config['tools']))/entry)]
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
    if (source_mechanism(config) != 'meson-radiative' or batch['operational_canary']
            or batch.get('gen_qualification_only') is True):
        return None
    from prepare_dark_photon_meson_production import meson_storage_limit
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
            or type(total) is not int or not 0 < total <= meson_storage_limit(config)
            or sum(forecasts.values()) != total):
        raise ValueError('Actual source budget requires the exact reviewed bulk storage gate')
    publication = status.get('ledger_publication')
    receipt_path = batch_directory(config, batch)/'gen/meson_ledger_publication.json'
    if (not isinstance(publication, dict) or publication != read(receipt_path)
            or publication.get('encoding') != 'gzip' or type(publication.get('bytes')) is not int
            or publication['bytes'] <= 0 or publication.get('sha256') != publication.get('readback_sha256')):
        raise ValueError('Actual source budget requires its verified complete compressed ledger')
    gen_bytes = status['publication'].get('bytes') if row.get('material_selection') is not None else 0
    if type(gen_bytes) is not int or gen_bytes < 0 or (row.get('material_selection') is not None and not gen_bytes):
        raise ValueError('Actual source storage needs the published full GEN byte count')
    required = row['events']*326000 + publication['bytes'] + gen_bytes + 5000000
    result = dict(point=batch['point'], batch=batch['batch'], actual_signal_events=row['events'],
        full_gen_events=row.get('full_gen_events', row['events']), full_gen_bytes=gen_bytes,
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


def detector_gate_arguments(config, batch):
    """Keep local canaries unless this bulk handoff has explicit qualification."""
    authority = config.get('qualified_global_detector_canary')
    if authority is None or batch.get('operational_canary'):
        return ['--canary-points', batch['point']]
    if config.get('future_detector_runtime') is None:
        raise ValueError('Global detector canary requires the qualified future runtime gate')
    path = pinned_path(authority)
    return ['--qualified-global-canary', str(path)]


def handoff_prior_inventories(config, batch):
    """Adopt reviewed inventory corrections without editing frozen science.

    Corrected rows must be exact copies of pinned audited GEN inventories and
    protect every previously reserved run and seed. They cannot add exposure.
    """
    result = list(config['prior_inventories'])
    authority = config.get('operational_handoffs', {}).get('reviewed_failures', {}).get(batch['batch'])
    proof = read_pinned(authority) if authority else {}
    for correction in proof.get('prior_inventory_corrections', []):
        old_path = pinned_path(correction['original'])
        new_path = pinned_path(correction['replacement'])
        if str(old_path) not in result:
            raise ValueError('Inventory correction does not belong to this handoff')
        old = read(old_path)['points']
        old_rows = list(old.values()) if isinstance(old, dict) else old
        new_rows = list(read(new_path)['points'].values())
        identity = lambda row: (row['seed'], row['run_number'])
        expected = [row for pin in correction['audited_inventories'] for row in read_pinned(pin)['points'].values()]
        if (not expected or len({identity(row) for row in expected}) != len(expected)
                or {identity(row) for row in old_rows} != {identity(row) for row in new_rows}
                or sorted(new_rows, key=identity) != sorted(expected, key=identity)
                or any('event_ids' not in row or not row['event_ids']
                       or any(event[0] != row['run_number'] for event in row['event_ids']) for row in new_rows)
                or any('event_ids' in old_row and old_row['event_ids'] != next(
                    row['event_ids'] for row in new_rows if identity(row) == identity(old_row)) for old_row in old_rows)):
            raise ValueError('Inventory correction changes reserved identities or audited GEN rows')
        result[result.index(str(old_path))] = str(new_path)
    return result


def handoff(config, batch):
    directory = batch_directory(config, batch)
    plan = adopt(config, batch)
    production = directory / 'production'
    recovery = config.get('reviewed_unsubmitted_runtime_recoveries', {}).get(batch['batch'])
    if recovery is not None:
        receipt = submit_unsubmitted_runtime_recovery(config, batch, recovery)
        export_original_rates(config, directory, production)
        return receipt
    operations=handoff_directory(config,batch) if config.get('future_detector_runtime') is not None else None
    preparation=directory/'preparation.json'
    if not production.exists():
        if config.get('future_detector_runtime') is not None:
            from dark_photon_runtime_recovery import future_preparation
            preparation=future_preparation(preparation,config['future_detector_runtime'],operations)
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
            '--preparation', preparation, '--template', config['qualified_template'],
            '--gen-inventory', directory/'gen_inventory.json', '--gen-publication', directory/'gen_publication.json',
            '--storage-budget', directory/'storage_budget.json', '--output', production,
            '--prior-inventories', *handoff_prior_inventories(config, batch), *detector_gate_arguments(config, batch),
            '--failure-base', config['failure_base'], '--detector-workers',
            str(config.get('detector_workers_per_point', 100)), '--finalizers', '2',
            '--publication-profile', 'nano-metadata-v1', '--publication-canary', config['publication_canary'],
            '--event-stall-timeout','7200','--first-event-stall-timeout','7200','--detector-timeout','43200',
            *(['--main-histogrammer',str(pinned_path(config['main_histogrammer']))]
              if config.get('main_histogrammer') is not None else []),
            *(['--absolute-stage-timeouts', *map(str,config['operational_stage_timeouts_seconds'])]
              if config.get('operational_stage_timeouts_seconds') is not None else [])],
            directory/'prepare_production.log', timeout=1800)
    if config.get('future_detector_runtime') is not None:
        from dark_photon_runtime_recovery import future_preflight
        future_preflight(production,config['future_detector_runtime'],operations,durable,config)
    receipt = (existing_receipt(production, production/'pipeline.dag.condor.sub')
               if (production/'submission.json').is_file() else durable.submit_dag(
                   config, production, config.get('detector_workers_per_point', 100) + 2))
    export_original_rates(config, directory, production)
    authority = config.get('reviewed_detector_recoveries', {}).get(batch['batch'])
    if authority is not None:
        return reviewed_detector_recovery(config, batch, receipt, authority)
    return receipt


def export_original_rates(config, directory, production):
    """Keep the original physical source/rate authority for runtime repairs."""
    rates = directory / 'rates.json'
    if not rates.exists():
        durable.run_logged([os.sys.executable, Path(config['tools'])/'export_dark_photon_cross_sections.py',
            '--plan', production/'plan.json', '--output', directory/'rates', '--publish', '--eos-rpc'],
            directory/'export_rates.log', timeout=1800)
        durable.new(rates, dict(complete=True, production_plan_sha256=durable.sha(production/'plan.json')))


def prepare_handoff_worker(config, batch, operational_directory=None, original_seal=None):
    """Freeze an independent preparation worker before the science seal exists."""
    directory=(Path(operational_directory)/'handoffs'/batch['batch'] if operational_directory
               else batch_directory(config,batch)/'handoff')
    directory.mkdir(parents=True)
    script=(Path(operational_directory)/'tools' if operational_directory else Path(config['tools']))/'run_dark_photon_meson_production.py'
    options = (' --seal '+shlex.quote(original_seal)+' --controller-config '+
        shlex.quote(str(Path(operational_directory)/'config.json'))+' --controller-seal "$1"'
        if operational_directory else ' --seal "$1"')
    wrapper=directory/'run.sh'
    wrapper.write_text('''#!/bin/bash
# Code style: AI-code. AI-generated independent meson detector preparation.
set -eo pipefail
cd '''+shlex.quote(str(directory))+'''
unset PYTHONPATH PYTHONHOME LD_LIBRARY_PATH LD_PRELOAD ROOTSYS
export PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
source /cvmfs/sft.cern.ch/lcg/views/LCG_108/x86_64-el9-gcc13-opt/setup.sh
exec python3 '''+shlex.quote(str(script))+' --directory '+shlex.quote(config['directory'])+
        options+' --action handoff --batch '+shlex.quote(batch['batch'])+'\n')
    wrapper.chmod(0o755)
    worker_runtime=config.get('handoff_worker_runtime_seconds',86400)
    if type(worker_runtime) is not int or not 3600<=worker_runtime<=86400:
        raise ValueError('Detector preparation needs a frozen worker lifetime of one to24hours')
    descriptor=f'''# Code style: AI-code. AI-generated independent meson handoff worker.
universe = vanilla
executable = {wrapper}
arguments = {'OPERATIONS_SEAL_DIGEST' if operational_directory else 'SEAL_DIGEST'}
initialdir = {directory}
getenv = False
should_transfer_files = YES
transfer_output_files = ""
request_cpus = 1
request_memory = 3000MB
request_disk = 4000MB
priority = 1000000
+JobFlavour = "tomorrow"
+MaxRuntime = {worker_runtime}
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


def handoff_directory(config, batch, queue_snapshot=None):
    """Use sealed replacement launchers without editing original workers or GEN."""
    original = batch_directory(config,batch)/'handoff'
    operational = config.get('operational_handoffs')
    if operational is None:
        return original
    preserved=operational.get('preserved_directories',{}).get(batch['batch'])
    directory = (Path(preserved) if preserved is not None else
                 Path(operational['directory'])/batch['batch'])
    if not directory.is_absolute() or not (directory/'worker.sub').is_file():
        raise ValueError('Operational handoff is not a frozen persistent worker')
    seal_path=directory.parents[1]/'seal.json'
    seal_digest=durable.sha(seal_path)
    frozen=read(seal_path)['files']
    for path in (directory/'run.sh',directory/'worker.sub.in'):
        if frozen.get(str(path)) != durable.sha(path):
            raise ValueError('Operational handoff launcher changed after freezing')
    if (directory/'worker.sub').read_text() != (directory/'worker.sub.in').read_text().replace('OPERATIONS_SEAL_DIGEST',seal_digest):
        raise ValueError('Operational handoff differs from its sealed submit template')
    authority=operational.get('reviewed_failures',{}).get(batch['batch'])
    proof=read_pinned(authority) if authority is not None else None
    if proof is not None and proof.get('original_handoff_directory') is not None:
        original=Path(proof['original_handoff_directory'])
        owner_seal=pinned_path(proof['original_controller_seal'])
        if (not original.is_absolute() or original.resolve()!=original
                or original.parent.name!='handoffs'
                or owner_seal!=original.parents[1]/'seal.json'):
            raise ValueError('Reviewed failed handoff is outside its sealed owner')
        owner_files=read(owner_seal)['files']
        for path in (original/'run.sh',original/'worker.sub.in'):
            if owner_files.get(str(path))!=durable.sha(path):
                raise ValueError('Reviewed failed handoff launcher changed')
        if (original/'worker.sub').read_text()!=(original/'worker.sub.in').read_text().replace(
                'OPERATIONS_SEAL_DIGEST',durable.sha(owner_seal)):
            raise ValueError('Reviewed failed handoff submit description changed')
    if (original/'submission.json').exists():
        receipt = existing_receipt(original,original/'worker.sub')
        if proof is None:
            raise ValueError('Operational handoff requires its reviewed failed launch')
        if (proof.get('schema') != 'shift-meson-handoff-recovery-v1'
                or proof.get('original_submission') != receipt
                or proof.get('source_status_sha256') != durable.sha(batch_directory(config,batch)/'source_status.json')
                or proof.get('gen_or_detector_rerun_allowed') is not False):
            raise ValueError('Operational handoff does not match its reviewed failed launch')
        try:
            native_status(config,receipt,queue_snapshot)
        except MesonJobFailure:
            pass
        else:
            raise RuntimeError('Original handoff is active or successful; preserve its owner')
    return directory


def reconcile_handoff_failure(config,state,batch,entry,receipt):
    """Archive only the exact old preparation failure after its successor passes."""
    pin=config.get('operational_handoffs',{}).get('reviewed_failures',{}).get(batch['batch'])
    if pin is None:
        return
    proof=read_pinned(pin)
    old=proof['original_submission']
    if receipt['cluster']==old['cluster']:
        return
    failures=state.get('failed_batches',{}).get(batch['batch'],[])
    for failure in list(failures):
        if (failure.get('stage')!='detector_preparation' or failure.get('cluster')!=old['cluster']
                or any(row.get('ClusterId')!=old['cluster'] or row.get('ProcId')!=0
                       for row in failure.get('native_jobs',[]))):
            continue
        state.setdefault('resolved_failed_batches',{}).setdefault(batch['batch'],[]).append(
            dict(failure=copy.deepcopy(failure),handoff_recovery=pin,
                 replacement_submission=copy.deepcopy(receipt)))
        failures.remove(failure)
        if entry.get('failure')==failure:
            entry.pop('failure')
    if not failures:
        state.get('failed_batches',{}).pop(batch['batch'],None)


def parallel_handoff(config,state,batch,entry,queue_snapshot):
    directory=handoff_directory(config,batch,queue_snapshot)
    description=directory/'worker.sub'
    new_submission=not (directory/'submission.json').is_file() and not (directory/'submission_intent.json').is_file()
    if new_submission:
        starts=state.get('detector_handoff_starts_this_poll',0)
        limit=config.get('maximum_new_detector_handoffs_per_poll')
        if limit is not None and starts>=limit:
            entry['stage']='waiting_for_detector_handoff_start'
            return 'waiting',None
    receipt=(existing_receipt(directory,description) if (directory/'submission.json').is_file()
             else durable.submission(config,directory,description))
    if new_submission and config.get('maximum_new_detector_handoffs_per_poll') is not None:
        state['detector_handoff_starts_this_poll']=state.get('detector_handoff_starts_this_poll',0)+1
    entry.update(handoff_cluster=receipt['cluster'],stage='detector_preparation')
    status=batch_job_status(config,state,batch,entry,receipt,'detector_preparation',queue_snapshot)
    if status!='complete':
        return status,None
    production=batch_directory(config,batch)/'production'
    recovery=config.get('reviewed_unsubmitted_runtime_recoveries',{}).get(batch['batch'])
    if recovery is not None:
        proof=validate_unsubmitted_runtime_recovery(production,batch,recovery)
        replacement=Path(proof['recovery_directory'])
        detector=existing_receipt(replacement,replacement/'pipeline.dag.condor.sub')
    else:
        detector=existing_receipt(production,production/'pipeline.dag.condor.sub')
    rates=batch_directory(config,batch)/'rates.json'
    if (not rates.is_file() or read(rates)!=dict(complete=True,
            production_plan_sha256=durable.sha(production/'plan.json'))):
        raise RuntimeError('Preparation worker exited without its complete rate export')
    reconcile_handoff_failure(config,state,batch,entry,receipt)
    if batch['batch'] in config.get('reviewed_detector_recoveries',{}):
        detector=reviewed_detector_recovery(config,batch,detector,
                                           config['reviewed_detector_recoveries'][batch['batch']])
    return status,detector


def unsubmitted_runtime_output_paths(plan):
    """List the canonical products that must be absent before first admission."""
    paths = set()
    for point in plan['points']:
        paths.add(point['merged_histograms'])
        for chunk in point['chunks']:
            paths.update((chunk['nano'], chunk['metadata']+'/complete.json',
                          chunk['metadata']+'/metadata.tar.gz'))
        for group in point['groups']:
            paths.update((group['nano'], group['histograms']))
    return sorted(paths)


def validate_unsubmitted_runtime_recovery(production, batch, proof_or_pin):
    """Validate one immutable first-admission repair without scheduler actions."""
    import dark_photon_runtime_recovery as adapter
    production = Path(production)
    proof = read_pinned(proof_or_pin) if set(proof_or_pin) == {'path', 'sha256'} else proof_or_pin
    if (proof.get('schema') != 'shift-meson-unsubmitted-runtime-recovery-v1'
            or proof.get('batch') != batch['batch'] or proof.get('point') != batch['point']
            or proof.get('original_directory') != str(production)
            or not production.is_absolute() or production.resolve() != production
            or any((production/name).exists() for name in ('submission.json', 'submission_intent.json'))):
        raise ValueError('Runtime first admission differs from its unsubmitted original cohort')
    for key, name in (('original_plan_sha256', 'plan.json'), ('original_seal_sha256', 'seal.json'),
                      ('original_inventory_sha256', 'gen_inventory.json')):
        if proof.get(key) != durable.sha(production/name):
            raise ValueError('Runtime first admission changed its original population: '+name)
    seal = read(production/'seal.json')
    required_originals = {str(production/name) for name in ('plan.json', 'gen_inventory.json',
        'pipeline.dag.template', 'detector.sub', 'finalizer.sub', 'collect.sub')}
    if not required_originals.issubset(seal['files']):
        raise ValueError('Runtime first admission omitted an original sealed dependency')
    for path, expected in seal['files'].items():
        if durable.sha(path) != expected:
            raise ValueError('Runtime first admission changed a sealed dependency: '+path)
    if (production/'pipeline.dag').read_text() != (production/'pipeline.dag.template').read_text().replace(
            'SEAL_DIGEST', proof['original_seal_sha256']):
        raise ValueError('Original detector DAG differs from its sealed dependency graph')
    plan = read(production/'plan.json')
    inventory = read(production/'gen_inventory.json')
    source_path = production.parent/'source_status.json'
    source = read(source_path)
    if (proof.get('source_status_sha256') != durable.sha(source_path)
            or source.get('complete') is not True or source.get('batch') != batch['batch']
            or source.get('point') != batch['point'] or set(inventory['points']) != {batch['point']}
            or source['parent']['gen_sha256'] != inventory['points'][batch['point']]['gen_sha256']
            or source['parent']['event_ids'] != inventory['points'][batch['point']]['event_ids']):
        raise ValueError('Runtime first admission lost its complete original GEN source')
    view = proof['runtime_view']
    view_root = Path(view['directory'])
    recovery = Path(proof['recovery_directory'])
    for path in (view_root, recovery):
        if (not path.is_absolute() or path.resolve() != path or path.is_relative_to(production)
                or not path.is_relative_to(production.parents[3])):
            raise ValueError('Runtime first admission requires a separate persistent recovery directory')
    if (view.get('wrapper') != str(view_root/'detector_worker.sh')
            or view.get('policy') != str(view_root/'stage_budget_policy.json')
            or durable.sha(view_root/'seal.json') != view.get('seal_sha256')):
        raise ValueError('Runtime first admission changed its qualified runtime view')
    view_seal = read(view_root/'seal.json')
    policy = read(view['policy'])
    if (view_seal.get('original_production') != str(production)
            or view_seal.get('original_seal_sha256') != proof['original_seal_sha256']
            or view_seal.get('authority') != proof['runtime_authority']
            or policy.get('allowed_slices') != 'all_planned'
            or policy.get('production_directory') != str(production)
            or policy.get('runtime_override', {}).get('authority') != proof['runtime_authority']):
        raise ValueError('Runtime first admission must cover every original planned chunk')
    for path, expected in view_seal['files'].items():
        if durable.sha(path) != expected:
            raise ValueError('Runtime first admission view changed: '+path)
    adapter.validate_override(policy)
    files = proof.get('recovery_files', {})
    required = {str(recovery/name) for name in ('pipeline.dag', 'detector.sub')}
    if set(files) != required or any(durable.sha(path) != expected for path, expected in files.items()):
        raise ValueError('Runtime first admission must pin its exact DAG and detector descriptor')
    old = str(production/'detector.sub')
    graph = (production/'pipeline.dag').read_text()
    executable = 'executable = '+str(production/'detector_worker.sh')
    descriptor = (production/'detector.sub').read_text()
    if (graph.count(old) != sum(len(row['chunks']) for row in plan['points'])
            or descriptor.count(executable) != 1
            or (recovery/'pipeline.dag').read_text() != graph.replace(old, str(recovery/'detector.sub'))
            or (recovery/'detector.sub').read_text() != descriptor.replace(
                executable, 'executable = '+view['wrapper'])):
        raise ValueError('Runtime first admission may change only the qualified detector executable')
    handoff = read_pinned(proof['failed_handoff_submission'])
    failed = read_pinned(proof['failed_preflight_receipt'])
    failure_root = Path(proof['failed_preflight_receipt']['path']).parent
    review = read_pinned(proof['native_review'])
    handoff_ad, preflight_ad = review['failed_handoff'], review['failed_preflight']
    if (Path(proof['failed_handoff_submission']['path']) != Path(handoff['task'])/'submission.json'
            or handoff.get('submit_sha256') != durable.sha(Path(handoff['task'])/'worker.sub')
            or failure_root != Path(handoff['task'])/'runtime_preflight'
            or failed.get('complete') is not False or not failed.get('error')
            or failed.get('original_plan_sha256') != proof['original_plan_sha256']
            or failed.get('original_seal_sha256') != proof['original_seal_sha256']
            or review.get('schema') != 'shift-meson-unsubmitted-runtime-native-review-v1'
            or review.get('original_directory') != str(production) or review.get('original_detector_queue') != []
            or handoff_ad.get('ClusterId') != handoff['cluster'] or handoff_ad.get('Iwd') != handoff['task']
            or handoff_ad.get('Cmd') != str(Path(handoff['task'])/'run.sh')
            or preflight_ad.get('Iwd') != str(failure_root)
            or preflight_ad.get('Cmd') != str(failure_root/'worker.sh')):
        raise ValueError('Runtime first admission lost its exact preserved failed preparation')
    old_preflight = read(failure_root/'submission.json')
    if (old_preflight.get('task') != str(failure_root)
            or old_preflight.get('submit_sha256') != durable.sha(failure_root/'worker.sub')
            or preflight_ad.get('ClusterId') != old_preflight.get('cluster')):
        raise ValueError('Runtime first admission lost its exact failed converter job')
    for native in (handoff_ad, preflight_ad):
        removed_failed_hold=(native.get('JobStatus')==3 and native.get('LastJobStatus')==5
            and native.get('LastHoldReasonCode')==3 and native.get('NumHolds',0)>=1
            and 'OnExitHold' in native.get('LastHoldReason',''))
        if (native.get('ProcId') != 0 or (native.get('JobStatus') != 5 and not removed_failed_hold)
                or type(native.get('ExitCode')) is not int or native['ExitCode'] == 0
                or native.get('ExitBySignal') is not False):
            raise ValueError('Runtime first admission requires its pinned native failed holds')
    absent = read_pinned(proof['output_absence'])
    expected_paths = unsubmitted_runtime_output_paths(plan)
    if (absent.get('schema') != 'shift-meson-unsubmitted-output-absence-v1'
            or absent.get('original_directory') != str(production)
            or absent.get('absent_paths') != expected_paths):
        raise ValueError('Runtime first admission lacks its complete original output-absence review')
    return proof


def submit_unsubmitted_runtime_recovery(config, batch, authority):
    """Admit a qualified new owner once; never submit the failed original DAG."""
    from run_dark_photon_runtime_recovery import verify_output_absence
    production = batch_directory(config, batch)/'production'
    proof = validate_unsubmitted_runtime_recovery(production, batch, authority)
    replacement = Path(proof['recovery_directory'])
    receipt = (existing_receipt(replacement, replacement/'pipeline.dag.condor.sub')
               if (replacement/'submission.json').is_file() else None)
    if receipt is None and (replacement/'submission_intent.json').is_file():
        receipt = durable.submission(config, replacement, replacement/'pipeline.dag.condor.sub')
    native_review = read_pinned(proof['native_review'])
    old_ids = [native_review[key]['ClusterId'] for key in ('failed_handoff', 'failed_preflight')]
    constraint = ('Iwd == '+json.dumps(str(production))+
        ' || member(ClusterId,{'+','.join(map(str, old_ids))+'})'+
        ' || Iwd == '+json.dumps(str(replacement)))
    if receipt is not None:
        constraint += ' || ClusterId == '+str(receipt['cluster'])+' || DAGManJobId == '+str(receipt['cluster'])
    rows = durable.jobs(config, constraint)
    for row in rows:
        if row['ClusterId'] in old_ids:
            previous = next(native_review[key] for key in ('failed_handoff', 'failed_preflight')
                            if native_review[key]['ClusterId'] == row['ClusterId'])
            if (row.get('ProcId') != 0 or row.get('Iwd') != previous['Iwd']
                    or row.get('JobStatus') != 5):
                raise RuntimeError('Original failed preparation is active or changed; preserve its owner')
        elif receipt is None or not ((row['ClusterId'] == receipt['cluster']
                and row.get('ProcId') == 0 and row.get('Iwd') == str(replacement))
                or (row.get('DAGManJobId') == receipt['cluster'] and row.get('Iwd') == str(production))):
            raise RuntimeError('Original detector population has an unexpected live owner')
    if receipt is None:
        verify_output_absence(unsubmitted_runtime_output_paths(read(production/'plan.json')))
        receipt = durable.submit_dag(config, replacement, config.get('detector_workers_per_point', 100)+2)
    return receipt


def prepare_unsubmitted_runtime_recovery(production, batch, runtime_authority, runtime_view,
        output, *, failed_handoff_submission, failed_preflight_receipt, native_review, output_absence):
    """Freeze a reviewed new DAG without modifying or submitting the original."""
    production, output = Path(production), Path(output)
    if output.exists() or output.is_relative_to(production):
        raise ValueError('Runtime first admission needs a new separate recovery directory')
    output.mkdir(parents=True)
    graph = (production/'pipeline.dag').read_text().replace(
        str(production/'detector.sub'), str(output/'detector.sub'))
    descriptor = (production/'detector.sub').read_text().replace(
        'executable = '+str(production/'detector_worker.sh'), 'executable = '+runtime_view['wrapper'])
    (output/'pipeline.dag').write_text(graph)
    (output/'detector.sub').write_text(descriptor)
    proof = dict(schema='shift-meson-unsubmitted-runtime-recovery-v1', batch=batch['batch'], point=batch['point'],
        original_directory=str(production), original_plan_sha256=durable.sha(production/'plan.json'),
        original_seal_sha256=durable.sha(production/'seal.json'),
        original_inventory_sha256=durable.sha(production/'gen_inventory.json'),
        source_status_sha256=durable.sha(production.parent/'source_status.json'),
        runtime_authority=runtime_authority, runtime_view=runtime_view, recovery_directory=str(output),
        recovery_files={str(output/name): durable.sha(output/name) for name in ('pipeline.dag', 'detector.sub')},
        failed_handoff_submission=failed_handoff_submission, failed_preflight_receipt=failed_preflight_receipt,
        native_review=native_review, output_absence=output_absence)
    validate_unsubmitted_runtime_recovery(production, batch, proof)
    path = output/'review.json'
    durable.new(path, proof)
    return dict(path=str(path), sha256=durable.sha(path))


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
            if authority.get('source_closure_authority'):
                canceled = reviewed_source_closure(config, authority['source_closure_authority'], peer_pin)
                closed_bytes = sum(amounts[name] for name in canceled)
                if closed_bytes > amount:
                    raise ValueError('Closed source allocation overlaps published storage credit')
                amount -= closed_bytes
                detail.update(remaining_bytes=amount, canceled_unsubmitted_source_bytes=closed_bytes,
                              source_closure_verified=True)
        total += amount
        claim = dict(name=authority['name'], reserved_bytes=amount,
                           forecast_sha256=authority['forecast']['sha256'],
                           **{key:value for key,value in detail.items() if key != 'name'})
        if peer_pin is None:
            claim['completed_peer_batches_release_no_credit'] = True
        claims.append(claim)
    return total, claims


def adaptive_reservation(config):
    fresh = fresh_production_authority(config)
    if fresh is not None:
        return fresh['reserved_peer_bytes']
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


def aggregation_reservation(config, reader=None):
    """Protect the independently registered final Nano regrouping allocation."""
    if config.get('regroup_complete_points') is not True:
        return 0
    reserved=config.get('aggregate_storage_reservation_bytes')
    if (type(reserved) is not int or reserved <= 0
            or config.get('merge_source_histograms') is not False
            or config.get('aggregate_group_events') != 4000):
        raise ValueError('Final regrouping needs its explicit storage and 4000-event grouping policy')
    proof=(reader or read_pinned)(config['aggregation_storage_registration'])
    if (proof.get('schema') != 'shift-dp-shared-aggregation-reservation-v1'
            or proof.get('complete') is not True
            or proof.get('science_directory') != config['directory']
            or proof.get('science_config_sha256') != durable.sha(Path(config['directory'])/'config.json')
            or proof.get('additional_reserved_bytes') != reserved
            or proof.get('source_envelope_bytes') != config['maximum_meson_forecast_bytes']
            or proof.get('protected_sm_external_envelope_bytes',0) < 150000000000):
        raise ValueError('Final regrouping does not match the registered shared-storage allocation')
    if proof.get('combined_DP_reservation_bytes',0) > proof['protected_sm_external_envelope_bytes']:
        raise ValueError('Final regrouping exceeds the registered shared external envelope')
    if reader is None:
        pinned_path(proof['shared_SM_registration'])
    else:
        reader(proof['shared_SM_registration'])
    return reserved


def point_grouping_readiness(config):
    """Read each compact point receipt once per poll before any EOS downloads."""
    result={}
    if config.get('regroup_complete_points') is not True:
        return result
    for batch in config['batches']:
        if batch.get('gen_qualification_only'):
            continue
        row=result.setdefault(batch['point'],dict(total_sources=0,ready_sources=0,events=0,complete=False))
        row['total_sources']+=1
        path=batch_directory(config,batch)/'production/completed_points'/(batch['point']+'.json')
        if path.is_file():
            report=read(path)
            if report.get('complete') is True:
                row['ready_sources']+=1
                row['events']+=report['events']
    for row in result.values():
        row['complete']=row['ready_sources']==row['total_sources']
        row['target_group_events']=config['aggregate_group_events']
    return result


_REVIEWED_STORAGE_CACHES = {}


def storage_payload(path):
    """Bound AFS reads and identify the failed file without trusting partial data."""
    import errno
    path = Path(path)
    for attempt in range(3):
        try:
            with path.open('rb') as stream:
                return b''.join(iter(lambda: stream.read(65536), b''))
        except OSError as error:
            if error.errno not in (errno.EIO, errno.ESTALE, errno.ETIMEDOUT) or attempt == 2:
                raise OSError(error.errno, 'Storage evidence read failed: '+str(path), str(path)) from error
            time.sleep(attempt+1)


def storage_ledger(path):
    return json.loads(storage_payload(path))


def storage_pinned_read(authority):
    path = Path(authority['path'])
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('Storage authority requires an absolute evidence path')
    payload = storage_payload(path)
    if hashlib.sha256(payload).hexdigest() != authority['sha256']:
        raise ValueError('Pinned storage evidence changed: '+str(path))
    return json.loads(payload)


def storage_closed_primitive(directory, point, events, observed):
    """Use the existing closed-primitive contract with bounded, named reads."""
    root = Path(directory)
    names = [root/'plan.json', root/'seal.json', root/'completed_points'/(point+'.json')]
    if not all(path.is_file() and path.stat().st_mtime <= observed for path in names):
        return False
    payloads = [storage_payload(path) for path in names]
    plan, seal, report = [json.loads(data) for data in payloads]
    plan_hash = hashlib.sha256(payloads[0]).hexdigest()
    if (seal['files'].get(str(names[0])) != plan_hash or report.get('complete') is not True
            or report.get('full_gen_events', report.get('events')) != events or report.get('point') != point
            or report.get('plan_sha256') != plan_hash
            or report.get('seal_sha256') != hashlib.sha256(payloads[1]).hexdigest()):
        raise ValueError('Published primitive differs from its sealed complete population')
    return True


def committed_sm_storage_entry(task, directory):
    """Capture immutable successful chunk evidence, never a reserved-byte charge."""
    root = Path(directory)
    work = Path(task['directory'])
    if not work.is_relative_to(root) or task.get('phase') != 'done' or task.get('native_exit_code') != 0:
        raise ValueError('SM storage credit lacks a closed native producer')
    request, result, status = work/'request.json', work/'result.json', work/'status.json'
    if durable.sha(request) != task['request_sha256'] or durable.sha(result) != task['result_sha256']:
        raise ValueError('SM committed request or result changed')
    evidence, returned = read(result), read(status)
    if (evidence.get('complete') is not True or evidence.get('exit_code') != 0
            or evidence.get('request_sha256') != task['request_sha256']
            or returned.get('complete') is not True or returned.get('exit_code') != 0):
        raise ValueError('SM storage credit lacks successful returned receipts')
    blocks = (work/'events.log').read_text().split('\n...\n')[:-1]
    terminal = next((block for block in reversed(blocks)
        if re.match(r'005 \('+str(task['cluster'])+r'\.000\.', block)), None)
    if terminal is None or 'Normal termination (return value 0)' not in terminal:
        raise ValueError('SM storage credit lacks its exact native zero-exit event')
    objects = {}
    for name in ('raw_publications', 'publications', 'prepared_publications'):
        for item in evidence.get(name, []):
            if item.get('independent_sha256_readback') is True:
                value = {key: item[key] for key in ('path', 'bytes', 'sha256')}
                old = objects.setdefault(value['path'], value)
                if old != value:
                    raise ValueError('One SM output has inconsistent committed identities')
    if (type(evidence.get('nano_bytes')) is int and evidence.get('nano_bytes', 0) > 0
            and evidence.get('nano_sha256') == evidence.get('nano_readback_sha256')):
        value = dict(path=evidence['nano_path'], bytes=evidence['nano_bytes'], sha256=evidence['nano_sha256'])
        old = objects.setdefault(value['path'], value)
        if old != value:
            raise ValueError('One SM Nano has inconsistent committed identities')
    return dict(cluster=task['cluster'], proc=0, iwd=str(work),
        request_sha256=task['request_sha256'], result_sha256=task['result_sha256'],
        finished_epoch=task['finished_epoch'], native_exit_event=terminal,
        objects=list(objects.values()))


def reviewed_sm_storage_credit(config, authority, observed, persist_terminal_cache=True):
    """Reuse terminal proofs; inspect only newly closed chunks on later polls."""
    import shift_condor_native as native
    peer = storage_pinned_read(authority['SM_configuration'])
    directory = Path(authority['SM_directory'])
    if peer.get('campaign_directory') != str(directory) or peer.get('own_reservation_bytes') != 137000000000:
        raise ValueError('Reviewed SM budget or producer directory changed')
    if authority.get('SM_active_supervisor_path'):
        active = storage_ledger(authority['SM_active_supervisor_path'])
        if (active.get('directory') != str(Path(authority['SM_configuration']['path']).parent)
                or active.get('config_sha256') != authority['SM_configuration']['sha256']):
            raise RuntimeError('SM supervisor changed; review its protected storage before new admission')
    cache_key = config['reviewed_remaining_storage_authority']['sha256']
    records = _REVIEWED_STORAGE_CACHES.get(cache_key)
    cache = Path(authority['terminal_cache_directory'])
    if not cache.is_absolute() or not cache.is_relative_to(Path(config['directory'])):
        raise ValueError('Storage terminal cache must have its persistent science directory')
    if records is None:
        records = {}
        for pin in authority.get('terminal_seed_parts', []):
            for row in storage_pinned_read(pin)['producers']:
                if row['cluster'] in records:
                    raise ValueError('SM storage seed repeats a producer')
                records[row['cluster']] = row
        if cache.is_dir():
            for path in cache.glob('*.json'):
                saved = storage_ledger(path)
                row = saved['producer']
                checksum = hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()
                if saved.get('sha256') != checksum:
                    raise ValueError('Immutable SM storage terminal cache checksum differs')
                old = records.setdefault(row['cluster'], row)
                if old != row:
                    raise ValueError('Immutable SM storage terminal cache differs')
        _REVIEWED_STORAGE_CACHES[cache_key] = records
    # A newly completed ad may briefly remain in the queue. Retain that chunk's
    # claim without discarding independently closed chunks' existing credit.
    live = native.query_account(authority['account_owner'], 'true', ['ClusterId', 'ProcId', 'Iwd', 'DAGManJobId'],
                                pool=config['condor_pool'], timeout=45)
    live_ids = {row['ClusterId'] for row in live}
    live_ids.update(row['DAGManJobId'] for row in live if type(row.get('DAGManJobId')) is int)
    ledger = storage_ledger(directory/'ledger.json')
    completed_rows = [task for task in ledger['tasks'].values()
        if task.get('phase') == 'done' and task.get('native_exit_code') == 0
        and task.get('kind') in ('source', 'raw_source', 'detector')
        and task.get('finished_epoch', float('inf')) <= observed]
    completed = {task['cluster']: task for task in completed_rows}
    if len(completed) != len(completed_rows):
        raise ValueError('SM ledger repeats a completed native producer')
    for cluster, task in completed.items():
        if cluster not in records and cluster not in live_ids:
            try:
                row = committed_sm_storage_entry(task, directory)
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                # This chunk retains its future claim. Other exact terminal
                # proofs can still release their independently committed bytes.
                continue
            # A peer admission controller may reuse the accounting without
            # competing with this production's sole persistent cache writer.
            if persist_terminal_cache:
                cache.mkdir(exist_ok=True)
                path = cache/(str(cluster)+'.json')
                saved = dict(producer=row, sha256=hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest())
                if path.exists():
                    if storage_ledger(path) != saved:
                        raise ValueError('Preserve changed SM storage terminal evidence')
                else:
                    try:
                        durable.save(path, saved)
                    except OSError as error:
                        raise OSError(error.errno, 'Storage terminal-cache write failed: '+str(path), str(path)) from error
            records[cluster] = row
    objects = {}
    for cluster, row in records.items():
        task = completed.get(cluster)
        if task is None or cluster in live_ids:
            raise RuntimeError('Credited SM producer is live or no longer verified complete')
        if (row.get('proc') != 0 or row.get('iwd') != task['directory']
                or not Path(row['iwd']).is_relative_to(directory)
                or row.get('request_sha256') != task['request_sha256']
                or row.get('result_sha256') != task['result_sha256']
                or row.get('finished_epoch') != task['finished_epoch']
                or not re.match(r'005 \('+str(cluster)+r'\.000\.', row.get('native_exit_event', ''))
                or 'Normal termination (return value 0)' not in row['native_exit_event']):
            raise ValueError('Cached SM storage proof lost its native identity')
        for item in row['objects']:
            if (not item['path'].startswith('/eos/user/j/jniedzie/')
                    or '..' in Path(item['path']).parts or type(item['bytes']) is not int or item['bytes'] < 0
                    or not re.fullmatch(r'[0-9a-f]{64}', item['sha256'])):
                raise ValueError('Invalid committed EOS object')
            old = objects.setdefault(item['path'], item)
            if old != item:
                raise ValueError('Conflicting duplicate EOS storage credit')
    amount = sum(row['bytes'] for row in objects.values())
    if amount > peer['own_reservation_bytes']:
        raise ValueError('Committed SM objects exceed their registered budget')
    return amount, live_ids, dict(producers=len(records), unique_objects=len(objects), committed_bytes=amount)


def committed_direct_gen_credit(authority, live_ids):
    """Release only already-paid, immutable GEN bytes from the direct budget."""
    pin=authority.get('committed_direct_GEN')
    if pin is None:
        return 0
    evidence=storage_pinned_read(pin)
    if (evidence.get('schema')!='shift-committed-direct-GEN-storage-v1'
            or evidence.get('storage_registration')!=authority['original_storage_registration']
            or evidence.get('reserved_bytes')!=30000000000
            or not evidence.get('gen_managers') or set(evidence['gen_managers']) & live_ids):
        raise ValueError('Direct GEN storage credit lost its closed registered producers')
    objects={}
    for row in evidence['objects']:
        plan=storage_pinned_read(row['plan']);seal=storage_pinned_read(row['seal'])
        manifest=storage_pinned_read(row['manifest']);status=storage_pinned_read(row['status'])
        receipt=storage_pinned_read(row['gen_submission'])
        points=[p for p in plan['points'] if p['point']==row['point']]
        item=row['publication'];path=Path(item['path']);actual=path.stat()
        if (len(points)!=1 or points[0]['gen_remote_path']!=str(path)
                or seal['files'].get(row['plan']['path'])!=row['plan']['sha256']
                or seal['files'].get(row['manifest']['path'])!=row['manifest']['sha256']
                or receipt['cluster'] not in evidence['gen_managers']
                or status.get('complete') is not True or status.get('publication')!=item
                or status['parent']['events']!=points[0]['full_gen_events']
                or status['parent']['manifest_sha256']!=row['manifest']['sha256']
                or not path.is_relative_to(Path('/eos/user/j/jniedzie/shift_cmssw'))
                or '..' in path.parts or path.name!='gen.root'
                or item['sha256']!=item['readback_sha256']
                or item['sha256']!=row['independent_sha256_readback']
                or item['sha256']!=manifest['output_sha256']
                or actual.st_size!=item['bytes'] or actual.st_mtime_ns!=row['eos_mtime_ns']
                or str(path) in objects):
            raise ValueError('Direct GEN storage credit changed its exact published object')
        objects[str(path)]=item['bytes']
    amount=sum(objects.values())
    if amount!=evidence['total_bytes'] or not 0<amount<=evidence['reserved_bytes']:
        raise ValueError('Direct GEN storage credit exceeds its original reservation')
    return amount


def committed_direct_detector_credit(authority, live_ids):
    """Count immutable Nano and metadata files from verified terminal workers."""
    pin=authority.get('committed_direct_detector')
    if pin is None:
        return 0
    evidence=storage_pinned_read(pin)
    if (evidence.get('schema')!='shift-committed-terminal-direct-storage-v1'
            or evidence.get('storage_registration')!=authority['original_storage_registration']
            or evidence.get('reserved_bytes')!=30000000000):
        raise ValueError('Closed direct storage changed its registered budget')
    objects={}
    for wave in evidence['waves']:
        plan=storage_pinned_read(wave['plan']);seal=storage_pinned_read(wave['seal'])
        receipt=storage_pinned_read(wave['submission'])
        if seal['files'].get(wave['plan']['path'])!=wave['plan']['sha256']:
            raise ValueError('Closed direct storage lost its sealed plan')
        native=wave.get('terminal',{})
        if not wave.get('terminal_chunks') and (
                native.get('ClusterId')!=receipt['cluster'] or native.get('ProcId')!=0
                or native.get('Iwd')!=receipt['task'] or native.get('JobStatus')!=4
                or native.get('ExitCode')!=0 or native.get('ExitBySignal') is not False
                or receipt['cluster'] in live_ids):
            raise ValueError('Closed direct storage lost its successful native owner')
        points={p['point']:p for p in plan['points']}
        for row in wave['objects']:
            if wave.get('terminal_chunks'):
                native=row['native'];cluster=native['cluster']
                if (type(cluster) is not int or cluster<=0 or cluster in live_ids
                        or native['submission_event']!=f"D_{row['point']}_{row['chunk']:03d}"
                        or not re.match(r'005 \('+str(cluster)+r'\.000\.000\)',native['terminal_event'])
                        or 'Normal termination (return value 0)' not in native['terminal_event']):
                    raise ValueError('Direct output lacks its exact terminal native worker')
            status=storage_pinned_read(row['status']);point=points[row['point']]
            chunks=[c for c in point['chunks'] if c['chunk']==row['chunk']]
            item=row['publication'];path=Path(item['path']);actual=path.stat()
            expected=(chunks[0]['nano'] if row['kind']=='nanoAOD' else
                      chunks[0]['metadata']+'/metadata.tar.gz') if len(chunks)==1 else None
            if (row['kind'] not in ('nanoAOD','metadata') or str(path)!=expected
                    or status.get('complete') is not True or status.get('point')!=row['point']
                    or status.get('chunk')!=row['chunk'] or status.get('events')!=chunks[0]['count']
                    or status.get('plan_sha256')!=wave['plan']['sha256']
                    or status.get('seal_sha256')!=wave['seal']['sha256']
                    or status.get('published',{}).get(row['kind'])!=item
                    or not path.is_relative_to(Path('/eos/user/j/jniedzie/shift_cmssw'))
                    or '..' in path.parts or str(path) in objects
                    or actual.st_size!=item['bytes'] or actual.st_mtime_ns!=row['eos_mtime_ns']
                    or row['independent_sha256_readback']!=item['sha256']):
                raise ValueError('Closed direct storage changed its exact output')
            objects[str(path)]=item['bytes']
    amount=sum(objects.values())
    if amount!=evidence['total_bytes'] or not 0<amount<=evidence['reserved_bytes']:
        raise ValueError('Closed direct storage exceeds its original reservation')
    return amount


def reviewed_remaining_storage(config, observation, state, persist_terminal_cache=True):
    """Count remaining writes of a closed GEN census; quota already paid for GEN."""
    authority = storage_pinned_read(config['reviewed_remaining_storage_authority'])
    fresh = fresh_production_authority(config, reader=storage_pinned_read)
    original = Path(config['directory'])/'config.json'
    if (authority.get('schema') != 'shift-dp-reviewed-remaining-storage-v1'
            or authority.get('science_configuration') != dict(path=str(original),
                sha256=hashlib.sha256(storage_payload(original)).hexdigest())
            or authority.get('original_storage_registration') != fresh['authority']['storage_registration']
            or authority.get('minimum_eos_free_bytes') != config['minimum_eos_free_bytes']
            or authority.get('aggregate_storage_reservation_bytes') != aggregation_reservation(config, reader=storage_pinned_read)
            or authority.get('bytes_per_selected_event') != 326000
            or authority.get('fixed_bytes_per_batch') != 5000000):
        raise ValueError('Reviewed remaining storage changed its frozen science or safety margins')
    registrations = [storage_pinned_read(pin) for pin in authority['SM_registrations']]
    if (len(registrations) != 2
            or sorted(row['new_SM_reservation_bytes'] for row in registrations) != [62000000000, 75000000000]
            or max(row.get('total_SM_reservation_bytes', 0) for row in registrations) != 137000000000):
        raise ValueError('Reviewed storage must protect both registered SM allocations')
    peer_claims = {row['name']: row['reserved_bytes'] for row in fresh['storage_registration']['peer_reservations']}
    if peer_claims != dict(SM_weekend=75000000000, direct_DY_weekend=30000000000,
                           prior_SM_parent_protection=6144000000):
        raise ValueError('Reviewed storage changed the original protected peer budgets')
    observed = datetime.fromisoformat(observation['observed_utc']).timestamp()
    committed, live_ids, detail = reviewed_sm_storage_credit(config, authority, observed,
        persist_terminal_cache=persist_terminal_cache)
    census = storage_pinned_read(authority['source_census'])
    batches = {row['batch']: row for row in config['batches']}
    if set(census['batches']) != set(batches):
        raise ValueError('Remaining storage census must cover every frozen GEN cohort')
    future = full_gen = selected_bound = completed_selected = 0
    for name, row in census['batches'].items():
        batch = batches[name]
        source = storage_pinned_read(row['source_status'])
        receipt = storage_pinned_read(row['source_submission'])
        if (source.get('complete') is not True or source.get('batch') != name
                or source.get('point') != batch['point'] or source.get('source_collisions') != batch['source_collisions']
                or type(source.get('events')) is not int or source['events'] < 0
                or receipt['cluster'] in live_ids
                or receipt.get('task') != str(batch_directory(config, batch))
                or datetime.fromisoformat(source['finished_utc']).timestamp() > observed):
            raise ValueError('Closed GEN storage census lost its exact source identity')
        full_gen += source['events']
        bound = source['events']
        if row.get('material_source'):
            descriptor = storage_pinned_read(row['material_source'])
            indices = descriptor['selected_indices']
            if (descriptor.get('gen_sha256') != source['parent']['gen_sha256']
                    or len(indices) != len(set(indices)) or any(type(i) is not int or not 0 <= i < bound for i in indices)):
                raise ValueError('Frozen material selection differs from its original GEN')
            bound = len(indices)
        if batch.get('gen_qualification_only') or state.get('batches', {}).get(name, {}).get('published'):
            continue
        production = batch_directory(config, batch)/'production'
        processed = 0
        if storage_closed_primitive(production, batch['point'], source['events'], observed):
            processed = storage_ledger(production/'completed_points'/(batch['point']+'.json'))['events']
            if type(processed) is not int or not 0 <= processed <= bound:
                raise ValueError('Completed Nano exceeds its frozen selection bound')
        selected_bound += bound
        completed_selected += processed
        future += 5000000+(bound-processed)*326000
    direct_committed=committed_direct_gen_credit(authority,live_ids)
    detector_committed=committed_direct_detector_credit(authority,live_ids)
    if direct_committed+detector_committed>peer_claims['direct_DY_weekend']:
        raise ValueError('Combined direct storage credit exceeds its fixed reservation')
    reservation = (137000000000+peer_claims['direct_DY_weekend']+
                   peer_claims['prior_SM_parent_protection']-committed-direct_committed-detector_committed)
    return reservation, future, dict(full_gen_events=full_gen, selected_event_upper_bound=selected_bound,
        completed_selected_events=completed_selected, remaining_own_bytes=future,
        protected_SM_full_bytes=137000000000, protected_direct_bytes=30000000000,
        direct_GEN_committed_bytes=direct_committed,
        direct_detector_committed_bytes=detector_committed,
        SM_committed=detail, no_GEN_or_ledger_future_writes=True)


def storage_gate(config, observation, bulk=False, state=None, persist_terminal_cache=True):
    reviewed = bulk and config.get('reviewed_remaining_storage_authority')
    reservation = 0 if reviewed else adaptive_reservation(config)
    additional = 500000000 if not bulk else None
    if bulk:
        from prepare_dark_photon_meson_production import meson_storage_limit
        path = config.get('bulk_storage_gate')
        if not path or not Path(path).is_file():
            return False, dict(reason='waiting_for_reviewed_bulk_storage_and_seven_mass_GEN_gates')
        gate = read(path)
        audit_flag = ('all_requested_mass_GEN_audits_passed'
                      if source_mechanism(config) in ('meson-radiative',*REFERENCE_MECHANISMS) else 'all_seven_mass_GEN_audits_passed')
        if (gate.get('complete') is not True or gate.get(audit_flag) is not True
                or gate.get('model_card_sha256') != durable.sha(config['model_card'])
                or gate.get('worklist_sha256') != durable.sha(Path(config['directory'])/'config.json')
                or type(gate.get('total_meson_forecast_bytes')) is not int
                or not 0 < gate['total_meson_forecast_bytes'] <= meson_storage_limit(config)):
            raise ValueError('Bulk storage/physics gate differs from this frozen meson worklist')
        forecasts=gate.get('per_batch_forecast_bytes',{})
        if (set(forecasts)!={row['batch'] for row in config['batches']}
                or any(type(value) is not int or value<=0 for value in forecasts.values())
                or sum(forecasts.values())!=gate['total_meson_forecast_bytes']):
            raise ValueError('Bulk storage forecast does not cover every fixed source batch')
        published=(state or {}).get('batches',{})
        additional=sum(value for batch,value in forecasts.items() if not published.get(batch,{}).get('published'))
    accounting = None
    if reviewed:
        import shift_condor_native as native
        try:
            reservation, additional, accounting = reviewed_remaining_storage(config, observation, state or {},
                persist_terminal_cache=persist_terminal_cache)
        except (OSError, native.NativeCondorError) as error:
            return False, dict(reason='waiting_for_verified_remaining_storage', native_or_storage_error=repr(error),
                               evidence_traceback=traceback.format_exc(limit=8))
    reserved, claims = additional_reservations(config)
    regrouping=aggregation_reservation(config)
    required = config['minimum_eos_free_bytes'] + reservation + additional + reserved + regrouping
    return observation['free_bytes'] >= required and observation['free_files'] >= config['minimum_eos_free_files'], dict(
        free_bytes=observation['free_bytes'], protected_adaptive_bytes=reservation,
        meson_forecast_bytes=additional, additional_reserved_bytes=reserved,
        aggregate_storage_reservation_bytes=regrouping,
        additional_reserved_worklists=claims, required_free_bytes=required,
        **({'reviewed_remaining_storage': accounting} if accounting is not None else {}))


def poll_storage_gate(config, state, decisions, bulk):
    """Reuse a full-campaign reservation for at most five minutes of admissions."""
    now = time.monotonic()
    previous = decisions.get(bulk)
    if previous is not None and now-previous[0] < 300:
        return previous[1]
    observation = durable.quota(config)
    result = storage_gate(config, observation, bulk=bulk, state=state)
    if time.monotonic()-now >= 300:
        raise RuntimeError('Storage review exceeded its freshness window; no new admission')
    decisions[bulk] = (now, result)
    return result


def cumulative_result(config,state,batch,transport,primitive):
    """Keep the independent canary population when the bulk batch completes."""
    point=batch['point']
    condition=config['conditions']['sha256']
    for other in config['batches']:
        if other['point'] != point or other.get('gen_qualification_only') is True:
            continue
        root = batch_directory(config, other)
        selected = root/'gen/material_selected_source.json'
        report = root/'production/completed_points'/(point+'.json')
        if selected.is_file() and read(selected).get('selected_indices') == []:
            if not report.is_file() or read(report).get('rate_only') is not True:
                state.setdefault('blocked_points', {})[point] = dict(
                    reason='Empty frozen admission retains its full GEN denominator and needs explicit rate-only completion',
                    batch=other['batch'], material_descriptor_sha256=durable.sha(selected))
                return None
    sources=[]
    for other in config['batches']:
        production=batch_directory(config,other)/'production'
        report=production/'completed_points'/(point+'.json')
        if other['point']!=point or not report.is_file() or read(report).get('complete') is not True:
            continue
        sources.append(durable.authority(production,point,condition))
    if config.get('regroup_complete_points') is True:
        expected=sum(other['point']==point and not other.get('gen_qualification_only') for other in config['batches'])
        if len(sources) != expected:
            if batch.get('operational_canary'):
                return primitive
            state.setdefault('point_grouping',{}).setdefault(point,{}).update(
                ready_sources=len(sources),total_sources=expected,complete=False,stage='waiting_for_all_source_cohorts')
            return None
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


def complete_empty_admission(config, batch, status):
    """Retain a zero detector draw as a full-exposure source in later sums."""
    from dark_photon_material_selection import bind, rate_only_report, validate_rate_only_report
    from prepare_dark_photon_material_source import immutable
    directory=batch_directory(config,batch)
    descriptor_path=directory/'gen/material_selected_source.json'
    receipt=read(directory/'gen/material_selection_receipt.json')
    if (receipt.get('complete') is not True or receipt.get('original_exposure_preserved') is not True
            or receipt.get('descriptor_sha256') != durable.sha(descriptor_path)):
        raise ValueError('Empty admission has no complete immutable selection receipt')
    for path,digest in status['metadata_sha256'].items():
        if durable.sha(path) != digest:
            raise ValueError('Empty admission changed original GEN metadata')
    row=bind(adopted_row(batch,directory/'gen',config),status['parent'],read(descriptor_path))
    if row['events'] != 0 or batch.get('operational_canary'):
        raise ValueError('Only an empty bulk draw may use automatic rate-only completion')
    row.update(final_output_base=batch['final_output_base'],chunks=[],groups=[],
        material_selected_source_sha256=durable.sha(descriptor_path))
    production=directory/'production'
    if production.exists() and not (production/'rate_only_intent.json').exists():
        raise ValueError('Do not replace an existing detector deployment with a rate-only source')
    production.mkdir(exist_ok=True)
    immutable(production/'rate_only_intent.json',dict(source_status_sha256=durable.sha(directory/'source_status.json'),
        descriptor_sha256=durable.sha(descriptor_path),detector_events=0,selection_redrawn=False))
    plan=dict(schema='shift-dark-photon-rate-only-production-v1',rate_only=True,
        campaign=batch['campaign'],points=[row],events=0,full_gen_events=row['full_gen_events'],
        detector_chunks=0,electronics_or_trigger_changes=False)
    inventory=dict(schema='shift-independent-signal-GEN-inventory-v1',complete=True,
        points={batch['point']:status['parent']},each_parent_ledger_counted_once=True)
    publication=dict(schema='shift-stage-GEN-publication-v1',complete=True,inputs=[status['publication']])
    for name,value in [('plan.json',plan),('gen_inventory.json',inventory),('gen_publication.json',publication)]:
        immutable(production/name,value)
    pins=[production/'plan.json',production/'gen_inventory.json',production/'gen_publication.json',
        directory/'source_status.json',descriptor_path,directory/'gen/material_selection_receipt.json']
    pins.extend(Path(path) for path in status['metadata_sha256'])
    immutable(production/'seal.json',dict(schema='shift-dark-photon-rate-only-seal-v1',
        files={str(path):durable.sha(path) for path in pins}))
    report=rate_only_report(row,status['parent'],durable.sha(production/'plan.json'),durable.sha(production/'seal.json'))
    (production/'completed_points').mkdir(exist_ok=True)
    path=production/'completed_points'/(batch['point']+'.json')
    immutable(path,report)
    validate_rate_only_report(read(path),row,status['parent'],report['plan_sha256'],report['seal_sha256'])
    rates=directory/'rates.json'
    if not rates.exists():
        durable.run_logged([os.sys.executable,Path(config['tools'])/'export_dark_photon_cross_sections.py',
            '--plan',production/'plan.json','--output',directory/'rates','--publish','--eos-rpc'],
            directory/'export_rates.log',timeout=1800)
        immutable(rates,dict(complete=True,production_plan_sha256=report['plan_sha256']))
    elif read(rates) != dict(complete=True,production_plan_sha256=report['plan_sha256']):
        raise ValueError('Rate-only source has changed rate-export evidence')
    return dict(complete=True,rate_only=True,point=batch['point'],events=0,
        full_gen_events=row['full_gen_events'],point_receipt_path=str(path),point_receipt_sha256=durable.sha(path))


def close_empty_admission(config,state,batch,entry,queue_snapshot):
    """Finish expected empty draws, preserving a failed old launcher if present."""
    descriptor=batch_directory(config,batch)/'gen/material_selected_source.json'
    if not descriptor.is_file() or read(descriptor).get('selected_indices') != []:
        return False
    if batch.get('operational_canary'):
        raise ValueError('Empty canary draw cannot qualify the weighted detector chain')
    handoff=handoff_directory(config,batch,queue_snapshot)
    if (handoff/'submission.json').exists():
        receipt=existing_receipt(handoff,handoff/'worker.sub')
        try:
            if native_status(config,receipt,queue_snapshot)=='active':
                entry['stage']='waiting_for_empty_admission_worker'
                return True
        except MesonJobFailure as error:
            entry['preserved_empty_admission_attempt']=dict(submission=receipt,error=str(error),native_jobs=error.jobs)
    proof=complete_empty_admission(config,batch,read(batch_directory(config,batch)/'source_status.json'))
    entry.update(stage='rate_only_complete',detector_native_complete=True,rate_only_completion=proof)
    state.get('blocked_points',{}).pop(batch['point'],None)
    # When this is the last cohort, refresh the already-ready histogram too:
    # otherwise its denominator would miss this newly completed empty draw.
    candidates=[other for other in config['batches'] if other['point']==batch['point']
        and not other.get('gen_qualification_only') and other['batch'] != batch['batch']]
    candidates.sort(key=lambda other: bool(other.get('operational_canary')))
    for other in candidates:
        report=batch_directory(config,other)/'production/completed_points'/(batch['point']+'.json')
        if report.is_file() and read(report).get('complete') and not read(report).get('rate_only'):
            result=publish_batch(config,state,other)
            if result is None:
                entry['stage']='waiting_for_rate_only_histogram_update'
                return True
            proof=dict(proof,cumulative_histogram=result)
            break
    entry['published']=proof
    return True


def regrouping_canary_directory(config):
    original=Path(config['directory'])/'regrouping_canary/v0001'
    authority=config.get('reviewed_regrouping_canary_recovery')
    if authority is None:
        return original
    proof=read_pinned(authority)
    replacement=Path(proof.get('replacement_directory',''))
    if (proof.get('schema')!='shift-reviewed-regrouping-canary-recovery-v1'
            or proof.get('original_directory')!=str(original)
            or replacement!=original.with_name('v0002')
            or proof.get('GEN_or_detector_rerun_allowed') is not False
            or proof.get('source_exposure_unchanged') is not True
            or durable.sha(original/'plan.json')!=proof.get('original_plan_sha256')
            or durable.sha(original/'seal.json')!=proof.get('original_seal_sha256')):
        raise ValueError('Regrouping recovery changed its source or original failed authority')
    native=read_pinned(proof['native_failure'])
    receipt=existing_receipt(original,original/'aggregate.sub')
    if (len(native)!=1 or native[0].get('ClusterId')!=receipt['cluster']
            or native[0].get('Iwd')!=str(original) or native[0].get('ExitCode')!=1
            or native[0].get('JobStatus')!=5):
        raise ValueError('Regrouping recovery has no exact reviewed failed native attempt')
    if (replacement/'plan.json').is_file():
        old=read(original/'plan.json');new=read(replacement/'plan.json')
        if (new['points'][0]['sources']!=old['points'][0]['sources']
                or new.get('histogram_only') is not False or new.get('group_events')!=4000
                or new.get('single_source_regrouping_canary') is not True):
            raise ValueError('Regrouping retry changed its exact source cohorts or grouping contract')
    return replacement


def qualify_regrouping_canary(config,state):
    """Exercise real Nano regrouping/scoring/publication before any bulk GEN."""
    from watch_dark_photon_production import EosRpc, completed_histogram
    previous=state.get('regrouping_canary',{})
    if previous.get('complete'):
        if durable.sha(previous['point_receipt_path']) != previous['point_receipt_sha256']:
            raise ValueError('Qualified regrouping-canary receipt changed')
        return True
    batch=next(row for row in config['batches'] if row.get('operational_canary') and not row.get('gen_qualification_only'))
    production=batch_directory(config,batch)/'production'
    source=durable.authority(production,batch['point'],config['conditions']['sha256'])
    directory=regrouping_canary_directory(config)
    if not directory.exists():
        settings=dict(config,campaign_prefix=config['campaign_prefix']+'-regroup-canary',
            aggregation_guard=str(production),merge_source_histograms=False,aggregate_group_events=4000,
            single_source_regrouping_canary=True)
        durable.aggregate_plan(settings,batch['point'],[source],directory)
        durable.new(directory/'aggregate_intent.json',durable.aggregate_record(directory,batch['point'],[source]))
    record=durable.aggregate_record(directory,batch['point'],[source])
    plan=read(directory/'plan.json')
    if (plan.get('histogram_only') is not False or plan.get('group_events') != 4000
            or plan.get('single_source_regrouping_canary') is not True):
        raise ValueError('Regrouping canary must run the complete Nano and histogram chain')
    receipt=(existing_receipt(directory,directory/'aggregate.sub') if (directory/'submission.json').is_file()
             else durable.submission(config,directory,directory/'aggregate.sub'))
    state['regrouping_canary']=dict(record,cluster=receipt['cluster'],complete=False)
    durable.save(Path(config['directory'])/'state.json',state)
    if native_status(config,receipt) != 'complete':
        return False
    transport=EosRpc('root://eosuser.cern.ch',directory/'readback')
    try:
        result=completed_histogram(dict(source=dict(directory=directory,campaign=plan['campaign'],
            plan_sha256=durable.sha(directory/'plan.json'),seal_sha256=durable.sha(directory/'seal.json')),
            row=plan['points'][0],output=Path(plan['points'][0]['final_output_base'])),transport)
        if result is None:
            raise ValueError('Regrouping-canary worker exited without complete histogram readback')
        state['regrouping_canary']=dict(record,**result,cluster=receipt['cluster'],complete=True,
            group_events=4000,histogram_only=False,latest_alias_changed=False)
        return True
    finally:
        for path in (directory/'readback').glob('*'):
            if path.is_file():
                path.unlink()


def histogram_promotion_floor(config, batch, result):
    """Use completed histogram input counts, never requested or selected counts."""
    floors = config.get('minimum_histogram_events_before_latest')
    if floors is None:
        return None
    points = {row['point'] for row in config['batches']}
    if (not isinstance(floors, dict) or set(floors)-points
            or any(type(value) is not int or value < 0 for value in floors.values())):
        raise ValueError('Latest histogram floors require known points and nonnegative integer counts')
    events = result.get('events')
    if type(events) is not int or events < 0:
        raise ValueError('Latest promotion requires the validated histogram input count')
    minimum = floors.get(batch['point'], 0)
    if config.get('regroup_complete_points') is True and not batch.get('operational_canary'):
        expected={str(batch_directory(config,other)/'production/plan.json') for other in config['batches']
            if other['point']==batch['point'] and not other.get('gen_qualification_only')}
        if events > 0 and set(result.get('source_plans',[])) == expected:
            return dict(minimum_events=minimum,validated_histogram_events=events,eligible=True,
                final_planned_point_complete=True,reason='Complete new-campaign point supersedes the legacy count floor')
    return dict(minimum_events=minimum, validated_histogram_events=events,
                eligible=events >= minimum)


def prior_cumulative_campaign(config, state, batch, result, transport):
    """Authorize only an audited earlier aggregate of this same production."""
    previous = state.get('published', {}).get(batch['point'], {})
    if not previous or previous.get('campaign') == result['campaign']:
        return None
    path = Path(previous.get('point_receipt_path', ''))
    root = Path(config['directory'])/'aggregates'/batch['point']
    if (path.parent.name != 'completed_points' or path.name != batch['point']+'.json'
            or path.parent.parent.parent != root
            or not re.fullmatch(r'v[0-9]+', path.parent.parent.name)
            or durable.sha(path) != previous.get('point_receipt_sha256')
            or not previous.get('source_plans')
            or not set(previous['source_plans']).issubset(result.get('source_plans', []))):
        raise ValueError('Previous cumulative publication differs from this production')
    from watch_dark_photon_production import completed_histogram
    directory = path.parent.parent
    plan = read(directory/'plan.json')
    ready = completed_histogram(dict(source=dict(directory=directory, campaign=plan['campaign'],
        plan_sha256=durable.sha(directory/'plan.json'), seal_sha256=durable.sha(directory/'seal.json')),
        row=plan['points'][0], output=Path(plan['points'][0]['final_output_base'])), transport)
    if (ready is None or any(ready[key] != previous.get(key) for key in
            ('campaign', 'histogram_path', 'histogram_sha256', 'events',
             'point_receipt_path', 'point_receipt_sha256'))
            or previous['events'] > result['events']):
        raise ValueError('Previous cumulative histogram changed or lost input events')
    return ready['campaign']


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
        unified = source_mechanism(config) in ('meson-radiative',*REFERENCE_MECHANISMS)
        prior_campaigns = []
        private_canary = (unified and batch['operational_canary']
                          and config.get('publish_canary_aliases', False) is False)
        promotion = None if private_canary else histogram_promotion_floor(config, batch, result)
        deferred_alias = promotion is not None and not promotion['eligible']
        if unified and not private_canary and not deferred_alias:
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
        if unified and not private_canary and not deferred_alias:
            if source_mechanism(config) in REFERENCE_MECHANISMS:
                anchor = dict(scope='Per-campaign sample_parameters.json owns epsilon, proper lifetime and calibration; the LAB directory is a selector',
                              campaign_parameters_authoritative=True)
            else:
                anchor = common_sample_anchor(config, batch, sample, sample_path, transport)
        elif not unified and not deferred_alias:
            publish_file_rpc(sample_path,config['eos_base']+'/darkphoton_'+batch['point']+'/sample.json')
        parameters=dict(sample,mean_lab_flight_reference_estimate_m=contract['mean_lab_flight_reference_estimate_m'],
            mean_beta_gamma_reference=contract['mean_beta_gamma_reference'],
            mean_beta_gamma_reference_standard_error=contract['mean_beta_gamma_reference_standard_error'],
            reference_definition='Independent fixed-M source estimate; shared LAB selector is the calibration anchor',
            fixed_source_collisions=batch['source_collisions'],accepted_signal_events=contract['requested_events'])
        if unified:
            parameters['global_anchor_reference'] = (anchor if anchor is not None else dict(
                scope=('Histogram input count is below the latest-promotion floor; global sample anchor and latest alias are not promoted'
                       if deferred_alias else
                       'Operational canary campaign only; global sample anchor and latest alias are not promoted')))
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
        if promotion is not None:
            result['latest_promotion'] = promotion
        if deferred_alias:
            result.update(published_utc=datetime.now(timezone.utc).isoformat(), batch=batch['batch'],
                          histogram_ready=True, alias_published=False, campaign_parameters_published=True)
            durable.save(directory/'publication.json', result)
            if result.get('sources_sha256'):
                state.setdefault('published', {})[batch['point']] = result
                state.setdefault('aggregates', {}).pop(batch['point'], None)
            return result
        # Replacing another production mechanism requires an explicit reviewed
        # alias target in the request, rather than silently combining models.
        alias = config['eos_base']+'/darkphoton_'+batch['point']+'/latest'
        old = transport.alias_target(alias)
        allowed = list(config.get('allowed_previous_aliases',{}).get(batch['point'], []))
        allowed.extend(prior_campaigns)
        allowed.extend(row['campaign'] for row in config['batches'] if row['point']==batch['point'])
        previous_campaign = (prior_cumulative_campaign(config, state, batch, result, transport)
                             if promotion is not None else None)
        if previous_campaign is not None:
            allowed.append(previous_campaign)
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
    constraint=directory_condition(config['directory'])
    before=time.time()
    output=durable.run_condor(['/usr/bin/condor_q',*durable.scheduler_arguments(config),
        '-constraint',constraint,'-json','-attributes',
        'ClusterId,ProcId,DAGManJobId,JobStatus,Iwd,NumJobStarts,ExitCode,ExitBySignal,ShiftDPStatisticsTask'],
        timeout=45)
    return dict(before_epoch=before,rows=durable.parse_ads(output))


def main_histogram_preflight_child(directory,child,cluster):
    """Recognize only the sealed main-stage converter node's persistent Iwd."""
    root=Path(directory);preflight=root/'primary_preflight'
    if child.get('Iwd')!=str(preflight) or child.get('DAGManJobId')!=cluster:
        return False
    plan_path=root/'plan.json';seal=read(root/'seal.json')
    if seal['files'].get(str(plan_path))!=durable.sha(plan_path):
        raise ValueError('Main-stage plan differs from its science seal')
    plan=read(plan_path)
    if plan.get('schema')!='shift-dark-photon-main-histograms-v1' or plan.get('main_histogrammer') is not True:
        return False
    preparation=read(preflight/'preparation.json')
    for name,filename in (('worker','worker.sh'),('submit','worker.sub'),('config','preflight.json')):
        authority=preparation[name]
        if Path(authority['path'])!=preflight/filename or durable.sha(authority['path'])!=authority['sha256']:
            raise ValueError('Declared converter node changed before monitoring')
    return preparation.get('complete') is True


def native_status(config, receipt, queue_snapshot=None):
    payload_directory = receipt['task']
    for name, authority in config.get('reviewed_unsubmitted_runtime_recoveries', {}).items():
        proof = read_pinned(authority)
        if proof['recovery_directory'] == receipt['task']:
            proof = validate_unsubmitted_runtime_recovery(proof['original_directory'],
                dict(batch=name, point=proof['point']), authority)
            payload_directory = proof['original_directory']
            break
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
        if any(row.get('Iwd')!=payload_directory and not
               main_histogram_preflight_child(payload_directory,row,receipt['cluster']) for row in children):
            raise RuntimeError('Meson child has an unexpected persistent working directory')
        directory=Path(receipt['task'])
        health_path=directory/'manager_health.json'
        health=read(health_path) if health_path.exists() else {}
        if (directory/'pipeline.dag').is_file():
            from watch_dark_photon_statistics import execute_events
            health.update(executed_children=execute_events(Path(payload_directory)),children=len(children),
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


def reconcile_runtime_failures(config, state, batch, entry, receipt, queue_snapshot=None):
    """Archive only held-child failures with a completed, reviewed native retry."""
    failures=state.get('failed_batches',{}).get(batch['batch'],[])
    authority=config.get('reviewed_runtime_recovery')
    if not failures or authority is None:
        return
    guard=read_pinned(authority)
    if guard.get('schema')!='shift-dp-runtime-recovery-controller-v1':
        raise ValueError('Unknown reviewed runtime-recovery controller')
    dependencies=guard['dependencies']
    for path,digest in dependencies.items():
        if durable.sha(path)!=digest:
            raise ValueError('Reviewed runtime-recovery dependency changed')
    modules={}
    for name in ('dark_photon_runtime_recovery', 'run_dark_photon_runtime_recovery'):
        matches=[path for path in dependencies if Path(path).name==name+'.py']
        if len(matches)!=1:
            raise ValueError('Meson retry validator is missing from its frozen guard')
        modules[name]=durable.module(Path(matches[0]), '_meson_guard_'+name)
    # A newer supervisor may use a newer adapter for unrelated first admissions.
    # Validate completed retries with the guard's exact original implementation.
    adapter=modules['dark_photon_runtime_recovery']
    recovery=modules['run_dark_photon_runtime_recovery']
    recovery.original_pinned=adapter.pinned
    recovery.original_read=adapter.read
    recovery.original_sha=adapter.sha
    production=batch_directory(config,batch)/'production'
    if not any(production.is_relative_to(Path(root)) for root in guard['production_directories']):
        raise ValueError('Meson production is outside the reviewed retry scope')
    guard_directory=Path(guard['directory'])
    if not guard_directory.is_absolute() or str(guard_directory).startswith('/tmp/'):
        raise ValueError('Reviewed retry state needs its persistent directory')
    attempts=read(guard_directory/'state.json').get('attempts',{})
    resolved=[]
    for failure in failures:
        if failure.get('stage')!='detector' or failure.get('cluster')!=receipt['cluster']:
            continue
        held=[row for row in failure.get('native_jobs',[]) if row.get('JobStatus') in (3,5)]
        if not held:
            continue
        proofs={}
        for original in held:
            cluster=original.get('ClusterId');attempt=attempts.get(str(cluster))
            if (original.get('JobStatus')!=5 or original.get('ExitCode')!=1
                    or original.get('ExitBySignal') is not False or original.get('ProcId')!=0
                    or original.get('DAGManJobId')!=receipt['cluster'] or original.get('Iwd')!=str(production)
                    or attempt is None or attempt.get('phase')!='complete'):
                break
            review=attempt['review'];old=review['original_native']
            if (review.get('production')!=str(production) or review.get('point')!=batch['point']
                    or review.get('plan_sha256')!=durable.sha(production/'plan.json')
                    or review.get('seal_sha256')!=durable.sha(production/'seal.json')
                    or any(old.get(key)!=original.get(key) for key in
                        ('ClusterId','ProcId','DAGManJobId','Iwd','NumJobStarts','ExitCode','ExitBySignal','JobStatus'))
                    or durable.sha(review['status_path'])!=attempt.get('complete_status_sha256')):
                raise ValueError('Completed retry differs from the recorded meson failure')
            status=recovery.completed_attempt(attempt,attempt['native_terminal'])
            if status.get('nano_sha256')!=attempt.get('nano_sha256'):
                raise ValueError('Completed retry Nano digest changed')
            proofs[str(cluster)]=copy.deepcopy(attempt)
        else:
            # A successful cached exit is usable only after a fresh queue absence.
            completed=max(datetime.fromisoformat(a['completed_utc']).timestamp() for a in proofs.values())
            if (queue_snapshot is not None and queue_snapshot['before_epoch']>=completed
                    and production.is_relative_to(Path(config['directory']))):
                live=[row for row in queue_snapshot['rows'] if str(row['ClusterId']) in proofs]
            else:
                live=durable.jobs(config,'member(ClusterId,{'+','.join(proofs)+'})')
            if live:
                raise RuntimeError('A completed reviewed meson retry is still live')
            resolved.append(dict(failure=copy.deepcopy(failure),runtime_recovery=authority,
                attempts=proofs,resolved_utc=datetime.now(timezone.utc).isoformat()))
    if resolved:
        archive=state.setdefault('resolved_failed_batches',{}).setdefault(batch['batch'],[])
        for record in resolved:
            archive.append(record)
            failures.remove(record['failure'])
            if entry.get('failure')==record['failure']:
                entry.pop('failure')
        if not failures:
            state['failed_batches'].pop(batch['batch'])


def batch_job_status(config, state, batch, entry, receipt, stage, queue_snapshot=None):
    """Keep a failed batch visible while independent batches can progress.

    Only a verified native failure is isolated. Scheduler errors, changed
    identities and missing receipts still stop the controller's poll.
    No failed job is released, replaced or counted as completed here.
    """
    try:
        result=(native_status(config,receipt,queue_snapshot) if queue_snapshot is not None
                else native_status(config,receipt))
        reconcile_runtime_failures(config,state,batch,entry,receipt,queue_snapshot)
        return result
    except MesonJobFailure as error:
        failure = dict(stage=stage, cluster=receipt['cluster'], error=str(error),
                       native_jobs=error.jobs)
        failures = state.setdefault('failed_batches', {}).setdefault(batch['batch'], [])
        if failure not in failures:
            failures.append(failure)
        entry.update(stage=stage+'_failed', failure=failure)
        return 'failed'


def detector_slot_claim(config, batch):
    """Reserve the actual random-N chunk count plus two downstream workers."""
    path = batch_directory(config, batch)/'source_status.json'
    status = read(path)
    events = status.get('events')
    chunk = batch.get('events_per_job', config.get('events_per_job', 20))
    workers = config.get('detector_workers_per_point', 100)
    if (status.get('complete') is not True or type(events) is not int or events < 1
            or type(chunk) is not int or chunk < 1
            or type(workers) is not int or workers < 1):
        raise ValueError('Detector admission requires the complete audited actual GEN count')
    return min(workers, math.ceil(events/chunk)) + 2


def detector_admission_pending(config, batch, entry):
    if entry.get('published') or entry.get('detector_native_complete'):
        return False
    root = batch_directory(config, batch)
    directories=[root/'handoff',root/'production']
    operational=config.get('operational_handoffs')
    if operational is not None:
        directories.append(Path(operational.get('preserved_directories',{}).get(batch['batch'],
            str(Path(operational['directory'])/batch['batch']))))
    recovery=config.get('reviewed_unsubmitted_runtime_recoveries',{}).get(batch['batch'])
    if recovery is not None:
        proof=validate_unsubmitted_runtime_recovery(root/'production',batch,recovery)
        directories.append(Path(proof['recovery_directory']))
    return any((directory/name).is_file() for directory in directories
               for name in ('submission.json', 'submission_intent.json'))


def detector_reservations(config, entries):
    """Count all earlier admissions before admitting an earlier worklist row."""
    if 'maximum_active_detector_workers' not in config:
        return None
    from prepare_dark_photon_meson_production import detector_point_limit
    detector_point_limit(config)
    claims = {row['batch']: detector_slot_claim(config, row) for row in config['batches']
              if detector_admission_pending(config, row, entries.get(row['batch'], {}))}
    return claims


def step(config,state):
    state.update(checked_utc=datetime.now(timezone.utc).isoformat(),heartbeat=state.get('heartbeat',0)+1)
    state['detector_handoff_starts_this_poll']=0
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
        recovery = reviewed_coverage_recovery(config)
        receipt = (read_pinned(recovery['replacement_submission']) if recovery else
                  existing_receipt(directory, directory / 'coverage.sub')
                   if (directory / 'submission.json').is_file()
                   else durable.submission(config, directory, directory / 'coverage.sub'))
        state['coverage_audit'] = dict(cluster=receipt['cluster'], stage='running_or_queued')
        state['complete'] = False
        if native_status(config, receipt) == 'active':
            return state
        state['coverage_audit'] = dict(cluster=receipt['cluster'], **coverage_result(config))
    queue_snapshot=production_queue_snapshot(config)
    storage_decisions={}
    point_readiness=point_grouping_readiness(config)
    if point_readiness:
        state['point_grouping']=point_readiness
    aggregate_attempted=set()
    source_active, detector_active=0,0
    detector_claims=detector_reservations(config, entries)
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
        if batch.get('gen_qualification_only') is True:
            entry['gen_qualification'] = qualify_gen_source(config, batch)
            entry.update(stage='GEN_qualified', published=dict(source_qualification_only=True,
                batch=batch['batch'], point=batch['point'], complete=True))
            durable.save(Path(config['directory'])/'state.json', state)
            continue
        if close_empty_admission(config,state,batch,entry,queue_snapshot):
            if detector_claims is not None and entry.get('detector_native_complete'):
                detector_claims.pop(batch['batch'],None)
            durable.save(Path(config['directory'])/'state.json',state)
            continue
        production=directory/'production'
        admitted = ((batch['batch'] in detector_claims or entry.get('detector_native_complete')) if detector_claims is not None else
                    (production/'submission.json').exists() or (production/'submission_intent.json').exists())
        new_claim = None
        if not admitted:
            if detector_claims is None:
                capacity = detector_active < config['simultaneous_detector_points']
            else:
                new_claim = detector_slot_claim(config, batch)
                capacity = (len(detector_claims) < config['simultaneous_detector_points']
                    and sum(detector_claims.values()) + new_claim <= config['maximum_active_detector_workers'])
                state['detector_worker_admission'] = dict(
                    reserved_workers=sum(detector_claims.values()),
                    maximum_workers=config['maximum_active_detector_workers'],
                    admitted_source_batches=len(detector_claims), next_batch_workers=new_claim)
            if not capacity:
                entry['stage']='waiting_for_detector_capacity'
                continue
            if scope is not None:
                if config.get('batch_native_queries') is True:
                    allowed,evidence=poll_storage_gate(config,state,storage_decisions,not batch['operational_canary'])
                else:
                    observation = durable.quota(config)
                    allowed, evidence = storage_gate(config, observation,
                        bulk=not batch['operational_canary'], state=state)
                state['storage_admission'] = evidence
                if not allowed:
                    entry['stage'] = 'waiting_for_detector_storage'
                    continue
            if detector_claims is not None:
                detector_claims[batch['batch']] = new_claim
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
        else:
            entry['detector_native_complete']=True
            if detector_claims is not None:
                detector_claims.pop(batch['batch'], None)
        if config.get('regroup_complete_points') is True and not batch.get('operational_canary'):
            if not point_readiness[batch['point']]['complete']:
                entry['stage']='waiting_for_complete_point_grouping'
                continue
            if batch['point'] in aggregate_attempted:
                entry['stage']='waiting_for_complete_point_grouping'
                continue
            aggregate_attempted.add(batch['point'])
        published=publish_batch(config,state,batch)
        if published:
            stage = 'histograms_ready' if published.get('alias_published') is False else 'published'
            entry.update(stage=stage,published=published)
            if config.get('regroup_complete_points') is True and not batch.get('operational_canary'):
                expected={str(batch_directory(config,other)/'production/plan.json') for other in config['batches']
                    if other['point']==batch['point'] and not other.get('gen_qualification_only')}
                if set(published.get('source_plans',[])) != expected:
                    raise ValueError('Final grouped histogram does not include every frozen source cohort')
                for other in config['batches']:
                    if other['point']==batch['point'] and not other.get('operational_canary') and not other.get('gen_qualification_only'):
                        entries[other['batch']].update(stage=stage,published=published,detector_native_complete=True)
            durable.save(Path(config['directory'])/'state.json',state)
    canaries=[row for row in config['batches'] if row['operational_canary']]
    canary_ready=all(entries[row['batch']].get('published') for row in canaries)
    if canary_ready and config.get('regroup_complete_points') is True:
        canary_ready=qualify_regrouping_canary(config,state)
    state['canary_published']=canary_ready
    if config.get('automatic_bulk_admission') is not None and (config.get('regroup_complete_points') is not True or canary_ready):
        from dark_photon_automatic_admission import maybe_create_bulk_gate
        state['automatic_bulk_admission'] = maybe_create_bulk_gate(config, state)
    observation=durable.quota(config) if config.get('batch_native_queries') is not True else None
    if config.get('source_admission_closed') is True:
        state['source_admission_closed']=True
    for batch in config['batches']:
        entry=entries[batch['batch']]
        if entry.get('source_cluster'):
            continue
        if config.get('source_admission_closed') is True:
            continue
        if not batch['operational_canary'] and not batch.get('gen_qualification_only') and not canary_ready:
            continue
        if source_active>=config['simultaneous_sources']:
            break
        if config.get('batch_native_queries') is True:
            allowed,evidence=poll_storage_gate(config,state,storage_decisions,not (batch['operational_canary'] or batch.get('gen_qualification_only')))
        else:
            allowed,evidence=storage_gate(config,observation,bulk=not (batch['operational_canary'] or batch.get('gen_qualification_only')),state=state)
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
    os.chdir(config['directory'])
    if args.controller_config is not None:
        if args.action not in ('controller','handoff') or args.controller_seal is None:
            raise ValueError('Only controller and handoff workers may adopt a sealed operational configuration')
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
            durable.persist_controller_state(path,state)
            if args.once or state.get('complete'):
                return
            time.sleep(60)


if __name__=='__main__':
    main()
