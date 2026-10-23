#!/usr/bin/env python3
# Code style: AI-code. AI-generated immutable signal planning utilities.
"""Freeze a bounded physical mass/epsilon pilot grid; never launch jobs.

Default columns are prompt, approximately 5 m and 30 m mean LAB flight.
The estimate is <beta*gamma>*c*tau, with boosts from an explicit MC reference.
Every decay still follows its full exponential; the lab flight is not fixed.
Lifetime is derived from the natural native physical width, never overridden.
"""
import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
from datetime import datetime
import shlex
import shutil
import subprocess
import sys

from dark_photon_generation import check_width_table, contract, production_settings
from dark_photon_model import validate_native_proposal_support, validate_native_widths
from dark_photon_sample_names import set_lab_point_name
from shift_production_selection import DEPENDENCIES as MATERIAL_SELECTION_SOURCES


REFERENCE_SCHEMA = 'shift-dark-photon-grid-reference-v1'
SOURCES = ('prepare_dark_photon_grid.py', 'run_dark_photon_gen.py', 'dark_photon_gen_cfg.py',
           'dark_photon_generation.py', 'dark_photon_model.py', 'dark_photon_pythia.py',
           'fixed_target_generation.py', 'audit_dark_photon_gen.py', 'run_dark_photon_detector.py',
           'run_shift_gen_to_nano.py', 'shift_storage_paths.py', 'shift_muon_decay_replay.py',
           'pythia_generation_ledger.py', 'dark_photon_sample_names.py',
           'dark_photon_detector_runtime.py') + MATERIAL_SELECTION_SOURCES
VALIDATION_REQUIREMENTS = (
    'GEN audit must pass with all generated identities, weights and out-of-envelope decays retained.',
    'Compare measured <beta gamma> and lab-flight distributions with the reference estimate; do not condition on detector acceptance.',
    'Check physical and sampling width/BR closure, pole support and sampling-rate convergence independently.',
    'Replay bounded events through the exact frozen V10 templates; verify timed graphs/ancestry/weights through every tier.',
    'Keep fixed electronics, trigger, reconstruction and pair-retention acceptance separate from classifier efficiency.',
    'Score the frozen SM classifier using reconstructed quantities only; physically strip truth and verify identical scores.',
    'Include zero-pair Nano events and all accepted GEN events; keep technical Pythia trial counts separate from luminosity.',
    'Require supported mass/lifetime acceptance closure before normalization transfer; two-event detector pilots cannot establish it.',
    'Scale beyond bounded pilots only after event-level runtime/configuration and physical-model checks; no final rate is approved.',
)


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def content_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def repartition_detector_chunks(chunks, event_ids, retained_chunks, events_per_job,
                                output_base, job_base):
    """Split unstarted slices while retaining audited or live chunk descriptors.

    This function only prepares descriptors. The caller must reconcile scheduler
    and publication state before selecting retained chunks or launching a new
    deployment. Chunk identifiers are stable identities, not list offsets.
    Finalizers must merge this entire ordered partition before histogramming.
    """
    if type(events_per_job) is not int or not 1 <= events_per_job <= 20:
        raise ValueError('Detector chunks require 1..20 events per job')
    if type(job_base) is not int or job_base < 1:
        raise ValueError('Replacement detector job namespace must be positive')
    if not isinstance(output_base, str) or not output_base.startswith('/eos/'):
        raise ValueError('Replacement detector outputs require an absolute EOS namespace')
    if any(character.isspace() for character in output_base) or '..' in Path(output_base).parts:
        raise ValueError('Invalid replacement detector output namespace')
    output_base = output_base.rstrip('/')
    if output_base == '/eos' or str(Path(output_base)) != output_base:
        raise ValueError('Replacement detector output namespace must be a canonical EOS directory')
    if not isinstance(event_ids, (list, tuple)) or not event_ids:
        raise ValueError('Require the complete parent GEN event identities')
    if any(not isinstance(value, (list, tuple)) or len(value) != 3 or
           any(type(component) is not int for component in value) for value in event_ids):
        raise ValueError('Invalid parent GEN event identity')
    if len({tuple(value) for value in event_ids}) != len(event_ids):
        raise ValueError('Duplicate parent GEN event identity')
    if not isinstance(chunks, (list, tuple)) or not chunks:
        raise ValueError('Require an existing complete detector partition')
    identifiers, jobs, paths, offset = set(), set(), set(), 0
    for row in chunks:
        if not isinstance(row, dict) or any(type(row.get(key)) is not int
                for key in ('chunk', 'skip', 'count', 'job')):
            raise ValueError('Invalid original detector chunk descriptor')
        if (row['chunk'] < 0 or row['job'] < 1 or not 1 <= row['count'] <= 20 or
                row['skip'] != offset or row['chunk'] in identifiers or row['job'] in jobs):
            raise ValueError('Original detector chunks do not form an ordered unique partition')
        for key in ('nano', 'metadata'):
            path = row.get(key)
            if (not isinstance(path, str) or not path.startswith('/eos/') or path in paths or
                    any(character.isspace() for character in path) or '..' in Path(path).parts or
                    str(Path(path)) != path):
                raise ValueError('Invalid or duplicate original detector output path')
            paths.add(path)
        if str(Path(row['nano']).parent.parent) == output_base:
            raise ValueError('Replacement detector outputs must use a new namespace')
        identifiers.add(row['chunk'])
        jobs.add(row['job'])
        offset += row['count']
    if offset != len(event_ids):
        raise ValueError('Original detector partition differs from the complete GEN population')
    if (not isinstance(retained_chunks, (list, tuple, set, frozenset)) or
            any(type(value) is not int for value in retained_chunks) or
            len(set(retained_chunks)) != len(retained_chunks) or
            not set(retained_chunks) <= identifiers):
        raise ValueError('Retained chunk identities must be unique members of the original partition')
    retained_chunks = set(retained_chunks)
    next_identifier, next_job = max(identifiers) + 1, job_base
    result = []
    for row in chunks:
        if row['chunk'] in retained_chunks:
            result.append(copy.deepcopy(row))
            continue
        for skip in range(row['skip'], row['skip'] + row['count'], events_per_job):
            if next_job in jobs:
                raise ValueError('Replacement detector job namespace overlaps original jobs')
            name = f'chunk_{next_identifier:03d}'
            result.append(dict(chunk=next_identifier, skip=skip,
                count=min(events_per_job, row['skip'] + row['count'] - skip),
                job=next_job, nano=output_base + '/nanoAOD/' + name + '.root',
                metadata=output_base + '/metadata/' + name, source_chunk=row['chunk']))
            next_identifier += 1
            next_job += 1
    ordered_ids = [identity for row in result
                   for identity in event_ids[row['skip']:row['skip'] + row['count']]]
    if ordered_ids != list(event_ids):
        raise ValueError('Replacement detector chunks changed complete GEN event coverage')
    return result


def meson_width_qualification(points):
    """Bind the physical spectral widths to already-audited meson GEN files.

    Meson injection does not use a native Pythia dark-photon resonance. Keep
    its qualification distinct from the native-resonance preflight flag.
    This validates runtime evidence, not the absolute hadron-production model.
    """
    qualified = {}
    for row in points:
        gen = Path(row['gen_directory'])
        contract_path = Path(row['signal_contract'])
        contract = json.loads(contract_path.read_text())
        audit_path = gen / 'validation.json'
        audit = json.loads(audit_path.read_text())
        width = contract['physical_width_table']
        total = width['total_width_gev']
        partials = width['partial_widths_gev']
        if (contract.get('production_mechanism') not in ('eta-etaprime-radiative', 'meson-radiative') or
                contract.get('filtering') != 'none' or audit.get('runtime_validated') is not True or
                audit.get('events') != row['events'] or contract.get('requested_events') != row['events'] or
                any(contract[key] != row[key] for key in ('mass_gev', 'epsilon', 'seed', 'run_number')) or
                contract != json.loads((gen / 'contract.json').read_text()) or
                audit.get('source_exposure') != contract.get('source_exposure') or
                width.get('spectral_source') != contract.get('width_authority')):
            raise ValueError('Meson GEN lacks its exact physical-width/runtime qualification')
        if (type(total) not in (int, float) or not math.isfinite(total) or total <= 0 or
                not partials or any(not math.isfinite(value) or value < 0 for value in partials.values()) or
                not math.isclose(math.fsum(partials.values()), total, rel_tol=1.e-10) or
                not math.isclose(width['ctau_mm'] * total, 1.973269804e-13, rel_tol=1.e-10) or
                width['mass_gev'] != row['mass_gev'] or width['epsilon'] != row['epsilon'] or
                ('proper_ctau_mm' in row and not math.isclose(row['proper_ctau_mm'], width['ctau_mm'], rel_tol=1.e-10)) or
                not math.isclose(width['br_mumu'], partials['mu_mu'] / total, rel_tol=1.e-10)):
            raise ValueError('Meson physical partial widths, lifetime or dimuon BR do not close')
        source = contract['source_exposure']
        if (source.get('source_collisions') != source.get('fixed_source_collisions') or
                type(source.get('source_collisions')) is not int or source['source_collisions'] < 2 or
                source.get('accepted_signal_events') != row['events'] or
                source.get('native_n_accepted') != source['source_collisions']):
            raise ValueError('Meson source exposure was confused with its conditional output count')
        if row['point'] in qualified:
            raise ValueError('Meson physical points are repeated')
        qualified[row['point']] = dict(contract_sha256=digest(contract_path),
            validation_sha256=digest(audit_path), model_card_sha256=contract['meson_model_card_sha256'])
    if not qualified:
        raise ValueError('Require a nonempty audited meson population')
    return dict(schema='shift-meson-physical-width-preflight-v1', points=qualified,
                absolute_production_rate_validated=False)


def validate_physical_preflight(plan):
    if not isinstance(plan, dict):
        raise ValueError('Require a qualified physical GEN plan')
    if plan.get('native_width_preflight_passed') is True:
        if plan.get('production_mechanism') in ('eta-etaprime-radiative', 'meson-radiative'):
            raise ValueError('Meson injection cannot claim native-resonance qualification')
        return
    if (plan.get('production_mechanism') not in ('eta-etaprime-radiative', 'meson-radiative') or
            plan.get('physical_width_preflight_passed') is not True or
            plan.get('meson_width_qualification') != meson_width_qualification(plan['points'])):
        raise ValueError('Detector stages require completed physical-width preflight')


def plan_detector_stage(physical_plan, campaign, eos_base, events_per_job=5,
                        group_events=2000, detector_job_base=1000000):
    """Prepare complete detector slices and same-point Nano groups, without I/O.

    The full native-width-preflighted GEN budget remains authoritative. Runtime
    workers must bind these ordered slices to the audited GEN identities and
    weights, and group all slices before starting any histogramming. A group
    boundary may shorten a slice; different physical points never share a file.
    """
    validate_physical_preflight(physical_plan)
    if not isinstance(campaign, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', campaign):
        raise ValueError('Require a valid detector stage campaign name')
    dates = re.findall(r'(?<![0-9])(20[0-9]{6})(?![0-9])', campaign)
    if not dates:
        raise ValueError('Detector stage campaign must contain its YYYYMMDD date')
    for date in dates:
        datetime.strptime(date, '%Y%m%d')
    if (not isinstance(eos_base, str) or not eos_base.startswith('/eos/user/') or
            str(Path(eos_base)) != eos_base or '..' in Path(eos_base).parts or
            any(character.isspace() for character in eos_base)):
        raise ValueError('Detector stages require a canonical absolute EOS user base')
    if type(events_per_job) is not int or not 1 <= events_per_job <= 20:
        raise ValueError('Detector stage chunks require 1..20 events per job')
    if type(group_events) is not int or group_events < events_per_job:
        raise ValueError('Nano group size must be at least the detector chunk size')
    if type(detector_job_base) is not int or detector_job_base < 1:
        raise ValueError('Detector stage job namespace must be positive')
    result = copy.deepcopy(physical_plan)
    rows = result.get('points')
    if not isinstance(rows, list) or not rows:
        raise ValueError('Require a nonempty physical point plan')
    names, runs = set(), set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('Invalid physical point descriptor')
        if not isinstance(row.get('point'), str) or not re.fullmatch(r'[A-Za-z0-9_-]+', row['point']):
            raise ValueError('Invalid physical point name')
        set_lab_point_name(row)
        point = row.get('point')
        if (not isinstance(point, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', point) or
                point in names or type(row.get('events')) is not int or row['events'] < 1 or
                type(row.get('run_number')) is not int or row['run_number'] < 1 or
                row['run_number'] in runs):
            raise ValueError('Require unique physical points/runs and complete positive GEN budgets')
        if isinstance(row.get('signal_contract'), dict):
            if row['signal_contract'].get('requested_events') != row['events']:
                raise ValueError('Detector stage GEN budget differs from its physical contract')
        names.add(point)
        runs.add(row['run_number'])
    next_job = detector_job_base
    for row in result['points']:
        prefix = f"{eos_base}/darkphoton_{row['point']}/{campaign}"
        chunks, groups = [], []
        for group_skip in range(0, row['events'], group_events):
            count = min(group_events, row['events'] - group_skip)
            identifiers = []
            for skip in range(group_skip, group_skip + count, events_per_job):
                chunk_id = len(chunks)
                name = f'chunk_{chunk_id:03d}'
                chunks.append(dict(chunk=chunk_id, skip=skip,
                    count=min(events_per_job, group_skip + count - skip), job=next_job,
                    nano=prefix + '/nanoAOD/' + name + '.root',
                    metadata=prefix + '/metadata/' + name))
                identifiers.append(chunk_id)
                next_job += 1
            group_id = len(groups)
            groups.append(dict(group=group_id, skip=group_skip, count=count, chunks=identifiers,
                nano=prefix + f'/nanoAOD_merged_unscored/ntuple_{group_id}.root',
                histograms=prefix + f'/histograms/ntuple_{group_id}.root'))
        row.update(chunks=chunks, groups=groups, final_output_base=prefix,
                   merged_histograms=prefix + '/histograms_merged/ntuple_0.root')
    # Canary simulation uses a distinct job/seed from every production slice.
    for row in result['points']:
        row['detector_job'] = next_job
        next_job += 1
    if 230000000 + (next_job - 1) * 100 + 40 > 899999900:
        raise ValueError('Detector stage job namespace exceeds the CMSSW random seed range')
    result.update(campaign=campaign, chunk_size=events_per_job, group_events=group_events,
        detector_stage_prepared=True, detector_chunks=next_job - detector_job_base - len(rows),
        events=sum(row['events'] for row in rows),
        processing_order=['GEN and complete identity/weight ledger audit', 'disjoint detector chunks',
            'complete same-point Nano grouping', 'histogramming grouped Nano files',
            'same-point histogram merging'],
        grouping_requires_complete_point=True, source_gen_ledger_count_per_point=1,
        electronics_or_trigger_changes=False, submitted=False)
    return result


def normalized_width(native, mass, epsilon):
    table = copy.deepcopy(native)
    if not math.isclose(float(table['mass_gev']), mass, rel_tol=1e-12):
        raise ValueError('Native width mass differs from physical point')
    table.update(epsilon=epsilon, total_width_gev=float(table['m_width_gev']),
                 ctau_mm=float(table['proper_length_mm']),
                 br_mumu=math.fsum(float(row['branching_fraction']) for row in table['channels']
                                  if sorted(row['products']) == [-13, 13]))
    check_width_table(table, mass, epsilon)
    table['independent_fermion_width_closure'] = validate_native_widths(table, epsilon)
    return table


def validate_references(reference):
    if reference.get('schema') != REFERENCE_SCHEMA or reference.get('sample_kind') != 'simulation_only':
        raise ValueError('Require an explicit simulation native-width/boost reference')
    for key in ('common', 'cp5'):
        if not isinstance(reference.get(key), list) or not all(isinstance(x, str) for x in reference[key]):
            raise ValueError('Reference must freeze common and CP5 setting lists')
    result = {}
    for row in reference.get('references', []):
        mass, epsilon, boost = (float(row[k]) for k in ('mass_gev', 'epsilon', 'mean_beta_gamma'))
        production_settings(mass, epsilon)
        if mass in result or not math.isfinite(boost) or boost <= 0 or not row.get('boost_source'):
            raise ValueError('Require unique masses, positive measured boosts and their provenance')
        table = row['width_authority']
        check_width_table(table, mass, epsilon)
        if float(table['pythia_version']) != 8.317:
            raise ValueError('Width reference must use pinned Pythia 8.317')
        validate_native_widths(table, epsilon)
        result[mass] = row
    if not result:
        raise ValueError('No native width references')
    return result


def plan_points(reference, masses=(15., 30., 50.), mean_lab_flights_m=(5., 30.),
                epsilons=None, prompt_epsilon=.001, sampling_epsilon=.005,
                events=100, detector_count=2, seed_base=24693357, detector_job_base=910001,
                accepted_events=False):
    """Select physical epsilon; scaling is only an initial native-width estimate."""
    refs = validate_references(reference)
    masses = tuple(map(float, masses))
    if not masses or len(set(masses)) != len(masses) or any(m not in refs for m in masses):
        raise ValueError('Require unique masses covered by the frozen references')
    if type(events) is not int or not 1 <= events <= 10000:
        raise ValueError('Bounded grid accepts 1..10000 GEN events per point')
    if type(detector_count) is not int or not 1 <= detector_count <= min(20, events):
        raise ValueError('Detector canary accepts 1..20 events and cannot exceed GEN count')
    if epsilons is not None:
        epsilons = tuple(map(float, epsilons))
        if not epsilons or len(set(epsilons)) != len(epsilons):
            raise ValueError('Require unique explicit epsilon columns')
        columns = [('epsilon_' + format(e, '.8g').replace('.', 'p'), e, None) for e in epsilons]
        mode = 'cartesian-mass-epsilon'
    else:
        flights = tuple(map(float, mean_lab_flights_m))
        if not flights or len(set(flights)) != len(flights) or any(not math.isfinite(x) or x <= 0 for x in flights):
            raise ValueError('Require unique positive target mean lab flights')
        columns = [('prompt', prompt_epsilon, None)] + [('lab_' + format(x, '.8g').replace('.', 'p') + 'm', None, x) for x in flights]
        mode = 'mass-by-approximate-mean-lab-flight'
    n_points = len(masses) * len(columns)
    if n_points > 30 or type(seed_base) is not int or not 1 <= seed_base <= 899999900 - n_points + 1:
        raise ValueError('Require at most 30 points and a valid unique seed namespace')
    if type(detector_job_base) is not int or detector_job_base < 1:
        raise ValueError('Detector job namespace must be positive')
    points = []
    for mass in masses:
        ref = refs[mass]
        for name, epsilon, target in columns:
            if target is not None:
                desired_ctau = target * 1000 / float(ref['mean_beta_gamma'])
                epsilon = abs(float(ref['epsilon'])) * math.sqrt(float(ref['width_authority']['ctau_mm']) / desired_ctau)
            epsilon = float(epsilon)
            if epsilon <= 0 or not math.isfinite(sampling_epsilon) or epsilon > sampling_epsilon:
                raise ValueError('Physical epsilon must be positive and no larger than the sampling amplitude')
            production_settings(mass, epsilon, sampling_epsilon / epsilon)
            i = len(points)
            points.append(dict(point='m' + format(mass, '.8g').replace('.', 'p') + '_' + name,
                               mass_gev=mass, epsilon=epsilon, target_mean_lab_flight_m=target,
                               mean_beta_gamma_reference=float(ref['mean_beta_gamma']),
                               boost_reference_source=ref['boost_source'],
                               proper_ctau_reference_estimate_mm=float(ref['width_authority']['ctau_mm']) * (float(ref['epsilon']) / epsilon) ** 2,
                               lifetime_estimate_only=True, sampling_epsilon=sampling_epsilon,
                               events=events, detector_count=detector_count, seed=seed_base + i,
                               run_number=seed_base + i, detector_job=detector_job_base + i))
    if len({row['point'] for row in points}) != len(points):
        raise ValueError('Grid points collide in their eight-digit display names; choose distinct pilot columns')
    result = dict(grid_mode=mode, points=points, lab_flight_is_fixed=False,
                reference_content_sha256=content_digest(reference),
                prompt_definition='physical epsilon ' + format(prompt_epsilon, '.8g') + '; lifetime derived from native width',
                assumption='Default displaced columns mean approximately 5 m and 30 m LAB flight; configurable before GEN')
    if accepted_events:
        result['event_budget_mode'] = 'outputAccepted'
    return result


def reuse_physical_points(plan, previous, previous_path=None):
    """Keep the exact old physics point while adding a new independent population.

    Reference boosts must not silently turn a statistics extension into a new
    lifetime point. New event counts, run numbers and random seeds stay separate.
    A fresh width preflight still checks every copied physical lifetime.
    """
    if previous.get('native_width_preflight_passed') is not True:
        raise ValueError('Reused physics requires a previously width-qualified plan')
    old_rows = {row['point']: row for row in previous['points']}
    if len(old_rows) != len(previous['points']):
        raise ValueError('Previous physical point names are ambiguous')
    # New LAB names retain the selector used by older frozen productions.
    for old in previous['points']:
        original = old.get('original_point', old['point'])
        if original in old_rows and old_rows[original] is not old:
            raise ValueError('Previous physical point selectors are ambiguous')
        old_rows[original] = old
    result = copy.deepcopy(plan)
    for row in result['points']:
        old = old_rows.get(row['point'])
        if old is None or any(old[key] != row[key] for key in
                              ('mass_gev', 'target_mean_lab_flight_m', 'sampling_epsilon')):
            raise ValueError('Statistics extension does not match the old point: ' + row['point'])
        for key in ('epsilon', 'mean_beta_gamma_reference', 'boost_reference_source'):
            row[key] = copy.deepcopy(old[key])
        proper = float(old['proper_ctau_mm'])
        if not math.isfinite(proper) or proper <= 0:
            raise ValueError('Old point has no valid physical proper lifetime')
        row['proper_ctau_reference_estimate_mm'] = proper
        row['statistics_extension_expected_ctau_mm'] = proper
        row['statistics_extension_source_point'] = old['point']
    result['statistics_extension_physics_content_sha256'] = content_digest(previous)
    if previous_path is not None:
        result['statistics_extension_physics_authority'] = dict(
            path=str(Path(previous_path).resolve()), sha256=digest(previous_path))
    return result


def check_reused_lifetime(row, proper_ctau_mm):
    expected = row.get('statistics_extension_expected_ctau_mm')
    if expected is not None and not math.isclose(proper_ctau_mm, expected, rel_tol=1e-10):
        raise ValueError('Fresh width changed the reused physical lifetime: ' + row['point'])


def preflight_points(plan, reference, native_probe, relative_flight_tolerance=1e-4):
    """Probe natural physical widths and common-amplitude sampling before launch."""
    if not math.isfinite(relative_flight_tolerance) or not 0 < relative_flight_tolerance < 1:
        raise ValueError('Require finite relative lab-flight preflight tolerance')
    result = copy.deepcopy(plan)
    for row in result['points']:
        mass, epsilon = row['mass_gev'], row['epsilon']
        width = normalized_width(native_probe(mass, epsilon, 1.), mass, epsilon)
        check_reused_lifetime(row, width['ctau_mm'])
        scale = row['sampling_epsilon'] / epsilon
        sampling = copy.deepcopy(native_probe(mass, epsilon, scale)) if scale != 1 else copy.deepcopy(width)
        sampling['total_width_gev'] = float(sampling['m_width_gev'])
        sampling['pole_support_audit'] = validate_native_proposal_support(sampling, .99 * mass, 1.01 * mass)
        if scale == 1:
            # The runtime uses the physical width as the proposal in this case.
            width['pole_support_audit'] = copy.deepcopy(sampling['pole_support_audit'])
        data = contract(mass, epsilon, width, 'mumu', row['events'], row['seed'], row['run_number'],
                        reference['common'], reference['cp5'], sampling, scale,
                        accepted_events=plan.get('event_budget_mode') == 'outputAccepted')
        lab = row['mean_beta_gamma_reference'] * width['ctau_mm'] / 1000
        target = row['target_mean_lab_flight_m']
        if target is not None and not math.isclose(lab, target, rel_tol=relative_flight_tolerance):
            raise ValueError('Native width does not close the requested approximate lab-flight target')
        row.update(proper_ctau_mm=width['ctau_mm'], mean_lab_flight_reference_estimate_m=lab,
                   total_native_physical_width_gev=width['total_width_gev'], br_mumu=width['br_mumu'],
                   lifetime_estimate_only=False, lifetime_derived_from_native_width=True,
                   mean_lab_flight_is_estimate=True, signal_contract=data)
    result['native_width_preflight_passed'] = True
    result['relative_lab_flight_preflight_tolerance'] = relative_flight_tolerance
    return result


def write_plan(output, reference_path, template_directory, plan, runtime, scripts=None, executable=None):
    """Freeze new sources/templates/contracts without creating runtime directories."""
    output, template_directory = Path(output).resolve(), Path(template_directory).resolve()
    if output.exists():
        raise FileExistsError('Refuse to overwrite an existing grid preparation')
    if plan.get('native_width_preflight_passed') is not True:
        raise ValueError('Cannot freeze executable commands without native-width preflight')
    plan = copy.deepcopy(plan)
    for row in plan['points']:
        set_lab_point_name(row)
    if len({row['point'] for row in plan['points']}) != len(plan['points']):
        raise ValueError('Physical points collide in their rounded LAB names')
    scripts = Path(scripts or Path(__file__).resolve().parent)
    sources = {name: scripts / name for name in SOURCES}
    templates = {f'step{i}.py': template_directory / f'step{i}.py' for i in range(1, 5)}
    for path in list(sources.values()) + list(templates.values()) + [Path(reference_path)]:
        if not path.is_file():
            raise ValueError('Missing frozen dependency: ' + str(path))
    if content_digest(json.loads(Path(reference_path).read_text())) != plan['reference_content_sha256']:
        raise ValueError('Width/boost reference changed during planning')
    for name, expected in runtime.get('source_preflight_sha256', {}).items():
        if name not in sources or digest(sources[name]) != expected:
            raise ValueError('Native preflight source changed before freezing: ' + name)
    output.mkdir(parents=True, exist_ok=False)
    frozen = output / 'frozen_workflow' / 'scripts'
    frozen.mkdir(parents=True)
    frozen_templates = output / 'templates'
    frozen_templates.mkdir()
    pinned = {}
    for category, items, target in (('sources', sources, frozen), ('templates', templates, frozen_templates)):
        pinned[category] = {}
        for name, source in items.items():
            destination = target / name
            shutil.copy2(source, destination)
            if digest(source) != digest(destination):
                raise ValueError('Frozen copy differs: ' + name)
            pinned[category][name] = dict(source=str(source.resolve()), frozen_copy=str(destination), sha256=digest(destination))
    reference_copy = output / 'references.json'
    shutil.copy2(reference_path, reference_copy)
    if digest(reference_copy) != digest(reference_path):
        raise ValueError('Frozen width/boost reference copy differs')
    pinned['references'] = dict(source=str(Path(reference_path).resolve()), frozen_copy=str(reference_copy), sha256=digest(reference_copy))
    result = copy.deepcopy(plan)
    result.update(schema='shift-dark-photon-grid-plan-v1', prepared=True, launched=False,
                  physics_valid=False, production_rate_validated=False, normalization_ready=False,
                  detector_acceptance_validated=False, classifier_transfer_validated=False,
                  frozen_dependencies=pinned, runtime=runtime,
                  validation_requirements=list(VALIDATION_REQUIREMENTS),
                  detector_scope='Bounded unchanged V10 no-pileup/Fake2-HLT background-chain replay; no final recorded-event acceptance')
    python = str(executable or sys.executable)
    contracts = output / 'points'
    contracts.mkdir()
    for row in result['points']:
        point = contracts / row['point']
        point.mkdir()
        contract_path = point / 'contract.json'
        point_contract = row.pop('signal_contract')
        contract_path.write_text(json.dumps(point_contract, indent=2, allow_nan=False) + '\n')
        gen = output / 'runs' / row['point'] / 'gen'
        detector = output / 'runs' / row['point'] / 'detector'
        row.update(signal_contract=str(contract_path), signal_contract_sha256=digest(contract_path),
                   gen_directory=str(gen), detector_directory=str(detector))
        row['gen_argv'] = [python, str(frozen / 'run_dark_photon_gen.py'), '--mass', repr(row['mass_gev']),
                           '--epsilon', repr(row['epsilon']), '--sampling-epsilon', repr(row['sampling_epsilon']),
                           '--events', str(row['events']), '--seed', str(row['seed']),
                           '--run-number', str(row['run_number']), '--output', str(gen)]
        if result.get('event_budget_mode') == 'outputAccepted':
            row['gen_argv'].append('--accepted-events')
        if point_contract.get('experimental_large_epsilon') is True:
            row['gen_argv'].append('--experimental-large-epsilon')
        if point_contract.get('experimental_low_mass_dy') is True:
            row['gen_argv'].append('--experimental-low-mass-dy')
        if 'gen_timeout_seconds' in result:
            row['gen_argv'].extend(['--timeout', str(result['gen_timeout_seconds'])])
        row['detector_argv'] = [python, str(frozen / 'run_dark_photon_detector.py'), str(gen),
                                '--count', str(row['detector_count']), '--job', str(row['detector_job']),
                                '--templates', str(frozen_templates), '--output', str(detector)]
        if not result.get('detector_stage_prepared'):
            # The 1..20-event model/transport reference deliberately keeps all
            # originals. New full detector stages use material admission.
            row['detector_argv'].append('--legacy-replay')
        row['gen_command'] = shlex.join(row['gen_argv'])
        row['detector_command'] = shlex.join(row['detector_argv'])
        row['launch_order'] = 'GEN and full-GEN audit first; detector canary only after completed GEN validation'
    (output / 'plan.json').write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--references', required=True, type=Path)
    parser.add_argument('--templates', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--masses', nargs='+', type=float, default=[15., 30., 50.])
    columns = parser.add_mutually_exclusive_group()
    columns.add_argument('--mean-lab-flights-m', nargs='+', type=float)
    columns.add_argument('--epsilons', nargs='+', type=float)
    parser.add_argument('--prompt-epsilon', type=float, default=.001)
    parser.add_argument('--sampling-epsilon', type=float, default=.005)
    parser.add_argument('--events', type=int, default=100)
    parser.add_argument('--gen-timeout', type=int, default=600)
    parser.add_argument('--accepted-events', action='store_true',
                        help='Budget accepted GEN outputs, retaining original IDs and all Pythia trial counters')
    parser.add_argument('--detector-count', type=int, default=2)
    parser.add_argument('--seed-base', type=int, default=24693357)
    parser.add_argument('--detector-job-base', type=int, default=910001)
    parser.add_argument('--reuse-physical-plan', type=Path,
                        help='Add independent statistics at exactly the physical points in this old plan')
    stage = parser.add_argument_group('Complete detector stage (preparation only)')
    stage.add_argument('--campaign', help='New dated EOS campaign for the complete detector stage')
    stage.add_argument('--eos-base', help='Canonical /eos/user/... data base')
    stage.add_argument('--events-per-job', type=int, help='Stage detector slice size (default 5)')
    stage.add_argument('--group-events', type=int, help='Maximum events per same-point Nano group (default 2000)')
    args = parser.parse_args()
    stage_requested = any(value is not None for value in
                          (args.campaign, args.eos_base, args.events_per_job, args.group_events))
    if stage_requested and (args.campaign is None or args.eos_base is None):
        parser.error('Complete detector stages require both --campaign and --eos-base')
    if args.output.exists():
        parser.error('Refuse to overwrite an existing grid preparation')
    if not 1 <= args.gen_timeout <= 21600:
        parser.error('GEN timeout must be 1..21600 seconds')
    if not os.environ.get('CMSSW_BASE') or not shutil.which('cmsRun'):
        parser.error('Enter the existing built CMSSW runtime; no build or generation is performed')
    reference = json.loads(args.references.read_text())
    plan = plan_points(reference, args.masses, args.mean_lab_flights_m or (5., 30.), args.epsilons,
                       args.prompt_epsilon, args.sampling_epsilon, args.events, args.detector_count,
                       args.seed_base, args.detector_job_base, accepted_events=args.accepted_events)
    if args.reuse_physical_plan is not None:
        plan = reuse_physical_points(plan, json.loads(args.reuse_physical_plan.read_text()),
                                    args.reuse_physical_plan)
    plan['gen_timeout_seconds'] = args.gen_timeout
    from Configuration.Generator.Pythia8CommonSettings_cfi import pythia8CommonSettingsBlock
    from Configuration.Generator.MCTunes2017.PythiaCP5Settings_cfi import pythia8CP5SettingsBlock
    from fixed_target_generation import canonical_settings
    current_common = list(pythia8CommonSettingsBlock.pythia8CommonSettings)
    current_cp5 = list(pythia8CP5SettingsBlock.pythia8CP5Settings)
    for current, expected in ((current_common, reference['common']), (current_cp5, reference['cp5'])):
        if canonical_settings(current) != canonical_settings(expected):
            raise ValueError('Frozen reference tune differs from the current CMSSW runtime')
    # Match the existing GEN runner's native loader isolation; no shared files change.
    os.environ['LD_LIBRARY_PATH'] = ':'.join(p for p in os.environ.get('LD_LIBRARY_PATH', '').split(':') if '/biglib/' not in p)
    from dark_photon_pythia import measure_widths
    source_preflight = {name: digest(Path(__file__).resolve().parent / name) for name in SOURCES}
    def native_probe(mass, epsilon, scale):
        return measure_widths(current_common + current_cp5 + production_settings(mass, epsilon, scale)
                              + ['Beams:frameType = 2', 'Beams:eA = 0.', 'Beams:eB = 6800.'])
    plan = preflight_points(plan, reference, native_probe)
    if stage_requested:
        plan = plan_detector_stage(plan, args.campaign, args.eos_base,
            5 if args.events_per_job is None else args.events_per_job,
            2000 if args.group_events is None else args.group_events, args.detector_job_base)
    base = Path(os.environ['CMSSW_BASE'])
    runtime = dict(cmssw_base=str(base), cmssw_version=os.environ.get('CMSSW_VERSION'),
                   scram_arch=os.environ.get('SCRAM_ARCH'), python=sys.version, python_executable=sys.executable,
                   source_preflight_sha256=source_preflight,
                   cmssw_git_head=subprocess.check_output(['git', '-C', str(base / 'src'), 'rev-parse', 'HEAD'], text=True).strip(),
                   pythia_tool=subprocess.check_output(['scram', 'tool', 'info', 'pythia8'], cwd=base / 'src', text=True),
                   width_preflight='native initialized Pythia widths with ProcessLevel:all=off; zero generated events')
    result = write_plan(args.output, args.references, args.templates, plan, runtime)
    print(json.dumps(dict(output=str(args.output.resolve()), points=len(result['points']),
                          grid_mode=result['grid_mode'], native_width_preflight_passed=True,
                          detector_stage_prepared=result.get('detector_stage_prepared', False),
                          launched=False, physics_valid=False, normalization_ready=False)), flush=True)


if __name__ == '__main__':
    main()
