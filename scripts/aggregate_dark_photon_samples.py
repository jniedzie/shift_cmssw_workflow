#!/usr/bin/env python3
# Code style: AI-code. AI-generated additive same-point Nano and histogram aggregation.
"""Combine independent ready GEN populations without replacing their records.

This worker consumes a sealed additive plan. It never generates events, submits
jobs, filters events or changes detector settings. Complete unscored Nano files
are grouped before the existing frozen scoring/histogram pipeline is called.
Recovery views of the same GEN ledger count once. The worker averages repeated
estimates of one proposal; technical trial counts are never luminosity. The
separate accounting helper prepares sums of parent-disjoint component rates
and keeps their individual GEN denominators. Composite publication still needs
its own qualified execution path.
Plans with histogram_only=true sum already-validated batch histograms while
retaining their complete Nano groups at the original published locations.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import subprocess
import sys

from export_dark_photon_cross_sections import rate_record
from pythia_generation_ledger import read_pythia_generation_ledger, same_ledger_content
from dark_photon_material_selection import (
    population as admitted_population, validate_nano, WEIGHT_BRANCH, merged_cutflow_sumw,
    validate_rate_only_report,
)


SCHEMA = 'shift-dark-photon-additive-plan-v1'
SAFE_NAME = re.compile(r'[A-Za-z0-9_-]+')
MODEL_FIELDS = ('mass_gev', 'epsilon', 'proper_ctau_mm', 'sampling_epsilon')


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1048576), b''):
            value.update(block)
    return value.hexdigest()


def checked(path, digest):
    if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
        raise ValueError('Missing pinned checksum: ' + str(path))
    if sha(path) != digest:
        raise ValueError('Frozen aggregate input changed: ' + str(path))
    return Path(path)


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as target:
        target.write(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n')


def validate_event_rows(identities, weights, expected):
    if (identities != expected or len({tuple(row) for row in identities}) != len(expected)
            or len(weights) != len(expected)):
        raise ValueError('Nano does not contain the complete ordered GEN identity union exactly once')
    if any(not math.isfinite(weight) or weight != 1. for weight in weights):
        raise ValueError('Native GEN weights changed or are nonfinite')


def event_rows(path):
    """Read identity and weight branches with the already-qualified ROOT runtime."""
    import ROOT
    root = ROOT.TFile.Open(str(path))
    if not root or root.IsZombie() or root.TestBit(ROOT.TFile.kRecovered):
        raise ValueError('Unreadable or recovered Nano: ' + str(path))
    try:
        tree = root.Get('Events')
        if not tree or any(not tree.GetBranch(name) for name in ('run', 'luminosityBlock', 'event', 'genWeight')):
            raise ValueError('Missing Nano identity/weight branches')
        tree.SetBranchStatus('*', 0)
        for name in ('run', 'luminosityBlock', 'event', 'genWeight'):
            tree.SetBranchStatus(name, 1)
        identities, weights = [], []
        for index in range(tree.GetEntries()):
            if tree.GetEntry(index) <= 0:
                raise ValueError('Cannot read a Nano event')
            identities.append([int(tree.run), int(tree.luminosityBlock), int(tree.event)])
            weights.append(float(tree.genWeight))
        return identities, weights
    finally:
        root.Close()


def group_inputs(inputs, target_events=2000):
    """Use whole Nano files, coalescing a short final group with its neighbour."""
    if type(target_events) is not int or target_events <= 0 or not inputs:
        raise ValueError('Require positive grouping target and complete inputs')
    groups, current, events = [], [], 0
    for item in inputs:
        count = item.get('events')
        if type(count) is not int or not 0 < count <= target_events:
            raise ValueError('Input Nano must be positive and at most the grouping target')
        current.append(item)
        events += count
        if events >= target_events:
            groups.append(dict(inputs=current, events=events))
            current, events = [], 0
    if current:
        if groups and events < target_events / 2 and groups[-1]['events'] + events <= 2 * target_events:
            groups[-1]['inputs'].extend(current)
            groups[-1]['events'] += events
        else:
            groups.append(dict(inputs=current, events=events))
    return [dict(group=index, **group) for index, group in enumerate(groups)]


def unique_populations(populations):
    """Deduplicate recovery views, rejecting overlapping independent populations."""
    result, by_gen, all_ids, all_runs, all_seeds = [], {}, set(), set(), set()
    model = None
    for population in populations:
        parent, record = population['parent'], population['rate']
        physical = tuple(record[key] for key in MODEL_FIELDS)
        if model is None:
            model = physical
        elif physical != model:
            raise ValueError('Different physical points or generation proposals cannot be aggregated')
        identities = parent['event_ids']
        validate_event_rows(identities, [1.] * len(identities), identities)
        if (parent['native_sumw'] != len(identities) or parent['native_sumw2'] != len(identities)
                or record['full_gen']['accepted_events'] != len(identities)
                or record['full_gen']['event_ids_sha256'] != hashlib.sha256(
                    json.dumps(identities, separators=(',', ':')).encode()).hexdigest()):
            raise ValueError('Full GEN population denominator or identity digest differs')
        key = parent['gen_sha256']
        if key in by_gen:
            previous = by_gen[key]
            if (previous['parent']['event_ids'] != identities
                    or previous['rate']['physical_selected_cross_sections_pb'] != record['physical_selected_cross_sections_pb']
                    or previous['rate']['proposal_ledger'] != record['proposal_ledger']
                    or previous.get('selected_event_ids') != population.get('selected_event_ids')
                    or previous.get('sampling_probabilities') != population.get('sampling_probabilities')
                    or previous.get('rate_only',False) != population.get('rate_only',False)):
                raise ValueError('Recovery views disagree about the same GEN population')
            previous.setdefault('duplicate_views', []).append(population['source'])
            continue
        runs = {identity[0] for identity in identities}
        seed = record['seed']
        if (all_ids.intersection(map(tuple, identities)) or all_runs.intersection(runs)
                or seed in all_seeds):
            raise ValueError('Independent GEN populations reuse event identities, runs or seeds')
        by_gen[key] = population
        result.append(population)
        all_ids.update(map(tuple, identities))
        all_runs.update(runs)
        all_seeds.add(seed)
    if not result:
        raise ValueError('No complete GEN populations to aggregate')
    return result


def combined_rate(populations, *, point, campaign):
    """Average identical-proposal estimates by their phase-space trial counts.

    nTried is a technical integration count, not target exposure. We preserve
    each authoritative GenRunInfo estimate and propagate independent errors.
    Different proposals require a separate reviewed combination prescription.
    """
    populations = unique_populations(populations)
    first = populations[0]['rate']
    meson = first.get('mechanism') in ('eta-etaprime-radiative', 'meson-radiative')
    fields = ('mechanism', 'model', 'cross_section_convention', 'physical_br_mumu',
              'sampling_epsilon', 'beam_energy_GeV', 'source_model',
              'generated_mass_support_gev')
    # A meson correction contains the measured all-collision mean probability,
    # which fluctuates between independent source populations. The model card
    # and source definition must agree; the measured means need not be equal.
    fields += ('meson_model_card', 'rate_scope') if meson else ('corrections',)
    if any(any(population['rate'].get(key) != first.get(key) for key in fields)
           for population in populations[1:]):
        raise ValueError('Rate estimates require identical physics and generation proposals')
    rates, trials = [], []
    for population in populations:
        record = population['rate']
        if len(record['physical_selected_cross_sections_pb']) != 1:
            raise ValueError('A per-run trial ledger is required for multi-run GEN files')
        rate = record['physical_selected_cross_sections_pb'][0]
        trial = record['proposal_ledger']['n_tried']
        if meson:
            source = record['source_exposure']
            trial = source['fixed_source_collisions']
            if (source['source_collisions'] != trial or source['native_n_accepted'] != trial or
                    record['full_gen']['source_collisions'] != trial or
                    source['accepted_signal_events'] != record['full_gen']['accepted_events']):
                raise ValueError('Meson source exposure differs from its complete signal population')
        if (type(trial) is not int or trial <= 0 or not math.isfinite(rate['value_pb'])
                or rate['value_pb'] <= 0 or not math.isfinite(rate['error_pb']) or rate['error_pb'] < 0):
            raise ValueError('Invalid authoritative cross section or GEN trial count')
        rates.append(rate)
        trials.append(trial)
    total_trials = sum(trials)
    fractions = [count / total_trials for count in trials]
    estimate = dict(value_pb=math.fsum(weight * rate['value_pb'] for weight, rate in zip(fractions, rates)),
                    error_pb=math.sqrt(math.fsum((weight * rate['error_pb'])**2 for weight, rate in zip(fractions, rates))))
    identities = [identity for population in populations for identity in population['parent']['event_ids']]
    counts = {key: sum(population['rate']['proposal_ledger'][key] for population in populations)
              for key in ('n_tried', 'n_selected', 'n_accepted')}
    result = dict(schema='shift-dark-photon-additive-cross-sections-v1', point=point, campaign=campaign,
        **{key: first[key] for key in MODEL_FIELDS},
        physical_br_mumu=first['physical_br_mumu'], mechanism=first['mechanism'], model=first['model'],
        cross_section_convention=first['cross_section_convention'],
        physical_selected_cross_sections_pb=[estimate],
        physical_inclusive_cross_sections_pb=[dict(value_pb=estimate['value_pb']/first['physical_br_mumu'],
            error_pb=estimate['error_pb']/first['physical_br_mumu'])],
        combination=dict(method='Independent identical-proposal GenRunInfo estimates weighted by technical n_tried',
            normalized_weights=fractions, extra_decay_branching_fraction_applied=False,
            cross_sections_summed=False, n_tried_is_luminosity=False),
        full_gen=dict(accepted_events=len(identities), sum_weights=float(len(identities)),
            sum_weights_squared=float(len(identities)), **counts,
            event_ids_sha256=hashlib.sha256(json.dumps(identities, separators=(',', ':')).encode()).hexdigest(),
            selection='none; all independent GEN populations including outside-envelope decays'),
        per_population=[dict(source=population['source'], gen_sha256=population['parent']['gen_sha256'],
            record=population['rate'], duplicate_views=population.get('duplicate_views', [])) for population in populations],
        parent_gen_ledger_count=len(populations), normalization_ready=False, physics_valid=False,
        production_rate_validated=False)
    if meson:
        result['combination']['method'] = 'Independent identical meson-source estimates weighted by fixed source-collision exposure'
        result['combination']['source_collisions_is_luminosity'] = False
        result['full_gen']['source_collisions'] = total_trials
        result['source_exposure'] = dict(schema='shift-additive-meson-source-exposure-v1',
            fixed_source_collisions=total_trials, source_collisions=total_trials,
            accepted_signal_events=len(identities),
            **{key: math.fsum(population['rate']['source_exposure'][key] for population in populations)
               for key in ('coefficient_sum', 'coefficient_sum_squared',
                           'physical_probability_sum', 'physical_probability_sum_squared')})
        result['meson_model_card'] = first['meson_model_card']
        result['rate_scope'] = first['rate_scope']
    return result


def disjoint_parent_component_rates(components, *, point, campaign):
    """Prepare rates for separately normalized, parent-disjoint meson sources.

    Repeated runs of each source are pooled by ``combined_rate`` first. Sources
    with different rare-decay parents then add. This deliberately rejects two
    cards that share a parent: distinct channel names alone do not prove that
    their amplitudes or cascades are independent. It is currently suitable for
    the existing light-meson source plus its physical-kaon complement.

    This prepares accounting only. It does not merge files, certify a source
    model, or publish an alias. Native GEN weights remain unchanged. The extra
    histogram scales describe an optional weighted histogram representation,
    whose subsequent ordinary normalization is total_sigma / total_GEN_sumw.
    """
    if not isinstance(components, list) or len(components) < 2:
        raise ValueError('Require at least two independently normalized components')
    rows, names, parent_owners = [], set(), {}
    all_ids, all_runs, all_seeds, all_gen = set(), set(), set(), set()
    physical, conditions = None, None
    common_fields = ('mass_gev', 'epsilon', 'proper_ctau_mm', 'physical_br_mumu',
                     'beam_energy_GeV', 'source_model', 'cross_section_convention',
                     'generated_mass_support_gev')
    for component in components:
        name = component.get('name')
        digest = component.get('conditions_sha256')
        if not isinstance(name, str) or not SAFE_NAME.fullmatch(name) or name in names:
            raise ValueError('Component names must be unique safe names')
        if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
            raise ValueError('Require pinned detector/source conditions for every component')
        if conditions is None:
            conditions = digest
        elif digest != conditions:
            raise ValueError('Components have different detector/source conditions')
        populations = unique_populations(component['populations'])
        first = populations[0]['rate']
        if first.get('mechanism') != 'meson-radiative':
            raise ValueError('Parent-disjoint combination currently requires meson sources')
        current = {field: first[field] for field in common_fields}
        if physical is None:
            physical = current
        elif current != physical:
            raise ValueError('Components describe different physical points or source conventions')
        pooled = combined_rate(populations, point=point, campaign=campaign)
        parents = first.get('meson_model_card', {}).get('parents')
        if not isinstance(parents, list) or not parents:
            raise ValueError('Every component needs an explicit rare-parent inventory')
        owned = set()
        for parent in parents:
            pdg = parent.get('pdg_id')
            if type(pdg) is not int or pdg == 0:
                raise ValueError('Invalid rare-parent PDG identity')
            if pdg in parent_owners:
                raise ValueError('Components share a rare parent; amplitude ownership needs review')
            owned.add(pdg)
        for population in populations:
            parent, record = population['parent'], population['rate']
            identities = set(map(tuple, parent['event_ids']))
            runs = {identity[0] for identity in identities}
            seed, gen = record['seed'], parent['gen_sha256']
            if (identities & all_ids or runs & all_runs or seed in all_seeds or gen in all_gen):
                raise ValueError('Components reuse GEN identities, runs, seeds or populations')
            all_ids.update(identities)
            all_runs.update(runs)
            all_seeds.add(seed)
            all_gen.add(gen)
        names.add(name)
        parent_owners.update({pdg: name for pdg in owned})
        rate = pooled['physical_selected_cross_sections_pb'][0]
        denominator = pooled['full_gen']['sum_weights']
        if not math.isfinite(denominator) or denominator <= 0:
            raise ValueError('Every component needs a positive full GEN denominator')
        rows.append(dict(name=name, parent_pdg_ids=sorted(owned), rate=pooled,
            full_gen_weight_sum=denominator,
            selected_cross_section_pb=rate['value_pb'],
            cross_section_per_gen_weight_pb=rate['value_pb'] / denominator))
    total_sigma = math.fsum(row['selected_cross_section_pb'] for row in rows)
    total_sumw = math.fsum(row['full_gen_weight_sum'] for row in rows)
    reference = total_sigma / total_sumw
    for row in rows:
        row['histogram_scale'] = row['cross_section_per_gen_weight_pb'] / reference
    return dict(schema='shift-dark-photon-disjoint-parent-component-rates-v1',
        point=point, campaign=campaign, physical_point=physical,
        conditions_sha256=conditions, components=rows,
        physical_selected_cross_sections_pb=[dict(value_pb=total_sigma,
            error_pb=math.sqrt(math.fsum(
                row['rate']['physical_selected_cross_sections_pb'][0]['error_pb']**2 for row in rows)))],
        combination=dict(method='Pool repeated identical proposals within each source; sum parent-disjoint source rates',
            cross_sections_summed=True, source_collisions_is_luminosity=False,
            extra_decay_branching_fraction_applied=False,
            uncertainty_scope='Independent Monte Carlo errors only; common theory/source uncertainties are not bounded'),
        histogram_normalization=dict(reference_gen_weight_sum=total_sumw,
            cross_section_per_stored_weight_pb=reference,
            expected_full_gen_stored_sumw=math.fsum(
                row['histogram_scale'] * row['full_gen_weight_sum'] for row in rows),
            expected_full_gen_stored_sumw2=math.fsum(
                row['histogram_scale']**2 * row['full_gen_weight_sum'] for row in rows),
            native_gen_weights_changed=False, scale_flow_bins=True, scale_sumw2_by_squared_factor=True,
            shape_normalization='Sum physical components first; normalize the resulting shape once'),
        normalization_ready=False, physics_valid=False, production_rate_validated=False)


def load_source(source, expected_model, conditions_digest):
    plan_path = checked(source['plan_path'], source['plan_sha256'])
    plan, directory = read(plan_path), plan_path.parent
    source_seal = read(checked(directory / 'seal.json', source['seal_sha256']))
    if source.get('conditions_sha256') != conditions_digest:
        raise ValueError('Source conditions do not match the reviewed aggregate conditions')
    rows = [row for row in plan['points'] if row['point'] == source['point']]
    if len(rows) != 1:
        raise ValueError('Missing or duplicate source physical point')
    row = rows[0]
    if any(row[key] != expected_model[key] for key in MODEL_FIELDS):
        raise ValueError('Source plan changes the selected physical point or proposal')
    inventory_path = directory / 'gen_inventory.json'
    checked(inventory_path, source_seal['files'].get(str(inventory_path)))
    if source_seal['files'].get(str(plan_path)) != source['plan_sha256']:
        raise ValueError('Source plan is outside its immutable production seal')
    inventory = read(inventory_path)
    if inventory.get('complete') is not True:
        raise ValueError('Incomplete source GEN inventory')
    parent = inventory['points'][source['point']]
    gen = Path(row['gen_directory'])
    contracts = {}
    for name, key in [('contract.json', 'contract_sha256'), ('validation.json', 'validation_sha256'),
                      ('manifest.json', 'manifest_sha256')]:
        contracts[name] = read(checked(gen / name, parent[key]))
    contract, audit, manifest = [contracts[name] for name in ('contract.json', 'validation.json', 'manifest.json')]
    meson = contract.get('production_mechanism') in ('eta-etaprime-radiative', 'meson-radiative')
    native_count = contract['source_exposure']['source_collisions'] if meson else row.get('full_gen_events', row['events'])
    fresh_ledger = read_pythia_generation_ledger(gen / 'cmsRun.log', expected_accepted=native_count)
    # GEN logs move from worker scratch into retained production storage.
    # Their path is provenance; content hashes and every counter remain exact.
    ledger_matches = same_ledger_content(fresh_ledger, audit['proposal_ledger'])
    if (audit['event_ids'] != parent['event_ids'] or audit['sum_weights'] != parent['native_sumw']
            or audit['sum_weights_squared'] != parent['native_sumw2']
            or manifest['output_sha256'] != parent['gen_sha256']
            or not ledger_matches):
        raise ValueError('Source full GEN audit differs from its inventory')
    rate = rate_record(contract, audit, point=source['point'], campaign=plan['campaign'],
        source_hashes={name: sha(gen / name) for name in ('contract.json', 'validation.json', 'manifest.json', 'cmsRun.log')},
        gen_path=parent['gen_path'], gen_sha256=parent['gen_sha256'], source_directory=gen)
    report_path = Path(source.get('completed_point_path', directory / 'completed_points' / (source['point'] + '.json')))
    if source.get('completed_point_sha256'):
        checked(report_path, source['completed_point_sha256'])
    report = read(report_path)
    if (report.get('complete') is not True or report.get('point') != source['point']
            or report.get('events') != row['events'] or report.get('plan_sha256') != source['plan_sha256']
            or report.get('seal_sha256') != source['seal_sha256']
            or report.get('final_output_base') != row['final_output_base']):
        raise ValueError('Source point completion does not match its sealed production')
    union = report['union']
    selected_ids, probabilities = admitted_population(row, parent)
    if (union.get('complete') is not True or union.get('parent_gen_ledger_count') != 1
            or union['parent_gen'] != parent or union['event_ids'] != selected_ids
            or union['native_sumw'] != len(selected_ids) or union['native_sumw2'] != len(selected_ids)):
        raise ValueError('Source Nano union is not its complete single GEN population')
    weighted = row.get('material_selection') is not None
    if weighted and (report.get('material_selection') != row['material_selection']
            or report.get('full_gen_events') != parent['events']
            or report.get('weight_branch') != WEIGHT_BRANCH
            or report.get('histogram_sumw') != math.fsum(1./p for p in probabilities)):
        raise ValueError('Source histogram changed its frozen sampling weights')
    rate_only = report.get('rate_only') is True
    if rate_only:
        validate_rate_only_report(report,row,parent,source['plan_sha256'],source['seal_sha256'])
        groups=[]
    else:
        if not selected_ids:
            raise ValueError('Empty material admission needs an explicit rate-only completion')
        groups = union.get('groups') or [dict(group=0, events=row['events'], event_ids=selected_ids, grouped=union['grouped'])]
    inputs, all_ids = [], []
    for group in groups:
        ids = group['event_ids']
        if group['events'] != len(ids):
            raise ValueError('Source Nano group count differs')
        item = group['grouped']
        if Path(item['path']).parent != Path(row['final_output_base']) / 'nanoAOD_merged_unscored':
            raise ValueError('Aggregate must consume complete unscored Nano inputs')
        start = len(all_ids)
        inputs.append(dict(**item, events=len(ids), event_ids=ids,
            sampling_probabilities=probabilities[start:start+len(ids)], material_selected=weighted))
        all_ids.extend(ids)
    validate_event_rows(all_ids, [1.] * len(all_ids), selected_ids)
    return dict(source=dict(source, completed_point_path=str(report_path), completed_point_sha256=sha(report_path)),
                parent=parent, rate=rate, inputs=inputs, contract=contract, report=report,
                selected_event_ids=selected_ids, sampling_probabilities=probabilities,
                material_selected=weighted,rate_only=rate_only)


def completed_histogram_input(population, model_hash):
    """Bind one source histogram to its already-validated complete Nano union."""
    report = population['report']
    marker = report.get('score_histograms', {}).get('marker', {})
    item = marker.get('published', {}).get('histograms', {})
    expected = report['final_output_base'] + '/histograms_merged/ntuple_0.root'
    count = report.get('events', len(population['parent']['event_ids']))
    if (marker.get('complete') is not True or marker.get('events') != count
            or marker.get('selection_applied') is not False
            or marker.get('model_sha256') != model_hash
            or type(marker.get('pairs')) is not int or marker['pairs'] < 0
            or item.get('path') != expected
            or type(item.get('bytes')) is not int or item['bytes'] <= 0
            or not re.fullmatch('[0-9a-f]{64}', item.get('sha256', ''))):
        raise ValueError('Source histogram does not match its complete population and classifier')
    return dict(item, events=count, sumw=report.get('histogram_sumw', float(count)),
                standard_cutflow_sumw=report.get('standard_cutflow_sumw',float(count)),
                pairs=marker['pairs'], source=population['source'],
                gen_sha256=population['parent']['gen_sha256'])


def load_plan(path, digest):
    plan = read(checked(path, digest))
    if plan.get('schema') != SCHEMA or not SAFE_NAME.fullmatch(plan.get('campaign', '')):
        raise ValueError('Invalid additive production plan')
    if 'histogram_only' in plan and type(plan['histogram_only']) is not bool:
        raise ValueError('histogram_only must be an explicit boolean')
    condition = plan['conditions']
    checked(condition['path'], condition['sha256'])
    for path, checksum in plan.get('dependencies', {}).items():
        checked(path, checksum)
    deployment = Path(plan['scoring_deployment']['path'])
    checked(deployment / 'seal.json', plan['scoring_deployment']['seal_sha256'])
    seal = read(deployment / 'seal.json')
    dependencies = set(seal['verification_groups']['scoring']) | set(seal['verification_groups']['common'])
    for path in dependencies:
        checked(path, seal['files'][path])
    return plan


def verify_aggregate_seal(plan_path, plan_digest, seal_digest):
    plan_path = Path(plan_path).resolve()
    seal_path = plan_path.parent / 'seal.json'
    seal = read(checked(seal_path, seal_digest))
    files = seal.get('files', {})
    if files.get(str(plan_path)) != plan_digest:
        raise ValueError('Aggregate plan is outside its immutable deployment seal')
    for path, digest in files.items():
        checked(path, digest)
    return seal


def qualified_finalizer(deployment):
    """Load the pinned original validators rather than a multi-group wrapper."""
    path = Path(deployment) / 'qualified_finalizer.py'
    specification = importlib.util.spec_from_file_location(
        '_shift_aggregate_qualified_' + sha(path)[:16], path)
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    if not callable(getattr(module, 'validate_histogram_axes', None)):
        raise ValueError('Qualified finalizer has no canonical histogram axis validator')
    return module


def execute_histogram_only(plan, row, populations, rate, identities,
                           plan_digest, seal_digest, scratch, results_directory):
    """Sum complete batch histograms while retaining their Nano groups in place."""
    deployment = Path(plan['scoring_deployment']['path'])
    sys.path.insert(0, str(deployment / 'frozen/scoring/workflow/scripts'))
    import run_merged_dimuon_bdt as scorer
    from dark_photon_production_support import validate_histogram_merge
    scoreplan = read(deployment / 'scoring_components.json')
    sources = [completed_histogram_input(population, scoreplan['model_sha256'])
               for population in populations if not population.get('rate_only')]
    guard_spec = plan['publication_guard']
    guard_directory = Path(guard_spec['deployment_path'])
    checked(guard_directory / 'seal.json', guard_spec['seal_sha256'])
    sys.path.insert(0, str(guard_directory))
    import chunk_worker as guard
    qualified = qualified_finalizer(deployment)
    guard.verify_seal(guard_spec['seal_sha256'], guard_spec['point'], scoring=True)
    guard.prepare_publication_guard(guard_spec['seal_sha256'])
    guard.check_headroom()
    if not sources:
        raise ValueError('Rate-only populations have no histogram to publish; retain their exposure receipt')
    io = scorer.io
    work = Path(scratch) / 'histogram_aggregate'
    work.mkdir(exist_ok=False)

    def stage(item, destination):
        io.get_remote(item['path'], destination)
        if sha(destination) != item['sha256'] or destination.stat().st_size != item['bytes']:
            raise ValueError('Source histogram or publication checksum/size changed')

    def put(path, destination):
        guard.check_headroom()
        if io.remote_exists(destination):
            raise FileExistsError('Aggregate output exists; preserve it for reviewed recovery: ' + destination)
        io.transfer(['/usr/bin/xrdfs', 'root://eosuser.cern.ch', 'mkdir', '-p', str(Path(destination).parent)])
        io.put_remote(path, destination)
        item = dict(path=destination, sha256=sha(path), bytes=path.stat().st_size)
        readback = work / ('readback_' + hashlib.sha256(destination.encode()).hexdigest()[:12])
        stage(item, readback)
        readback.unlink()
        return item

    histograms = []
    for index, source in enumerate(sources):
        path = work / ('source_' + str(index) + '.root')
        stage(source, path)
        qualified.validate_histogram_axes(path, deployment / 'frozen/scoring/sm_axis_authority.root')
        scorer.check_standard_histograms(path, source['events'], source['standard_cutflow_sumw'])
        histograms.append(path)
    merged = work / 'histograms.root'
    with (work / 'merge.log').open('x') as log:
        subprocess.run(['hadd', str(merged), *map(str, histograms)], check=True,
                       stdout=log, stderr=subprocess.STDOUT, timeout=900)
    audit = validate_histogram_merge(histograms, merged)
    qualified.validate_histogram_axes(merged, deployment / 'frozen/scoring/sm_axis_authority.root')
    scorer.check_standard_histograms(merged, len(identities), merged_cutflow_sumw(histograms))
    prefix = row['final_output_base']
    histogram = put(merged, prefix + '/histograms_merged/ntuple_0.root')
    write(work / 'cross_sections.json', rate)
    estimate = rate['physical_selected_cross_sections_pb'][0]
    with (work / 'cross_sections.txt').open('x') as target:
        target.write('# physical selected dimuon rate [pb], independent-run statistical error [pb]\n')
        target.write(f"{estimate['value_pb']:.17g} {estimate['error_pb']:.17g}\n")
    rate_items = {name: put(work / name, prefix + '/' + name)
                  for name in ('cross_sections.json', 'cross_sections.txt')}
    groups = []
    for population in populations:
        for item in population['inputs']:
            groups.append(dict(group=len(groups), events=item['events'], event_ids=item['event_ids'],
                native_sumw=float(item['events']), native_sumw2=float(item['events']),
                grouped={key: item[key] for key in ('path', 'sha256', 'bytes')},
                population_gen_sha256=population['parent']['gen_sha256']))
    union = dict(schema='shift-additive-signal-histogram-union-v1', complete=True,
        point=row['point'], events=len(identities), event_ids=identities,
        native_sumw=float(len(identities)), native_sumw2=float(len(identities)), groups=groups,
        parent_gen_populations=[population['parent'] for population in populations],
        parent_gen_ledger_count=len(populations), sources=[population['source'] for population in populations],
        histogramming_started_after_complete_grouping=True, source_groups_retained_in_place=True,
        new_nano_files_created=False, plan_sha256=plan_digest, seal_sha256=seal_digest)
    report = dict(schema='shift-independent-signal-point-complete-v1', complete=True,
        point=row['point'], events=len(identities), union=union, groups=[],
        source_histograms=sources, histogram_only=True, sample_role='validation_only',
        score_histograms=dict(complete=True, marker=dict(complete=True, events=len(identities),
            pairs=sum(source['pairs'] for source in sources), selection_applied=False,
            model_sha256=scoreplan['model_sha256'], published=dict(histograms=histogram))),
        histogram_merge=audit, cross_sections=rate_items, final_output_base=prefix,
        canonical_sm_histogram_axes_verified=1081,
        canonical_sm_histogram_authority_sha256=sha(deployment / 'frozen/scoring/sm_axis_authority.root'),
        plan_sha256=plan_digest, seal_sha256=seal_digest, created_utc=datetime.now(timezone.utc).isoformat())
    report.update(full_gen_events=sum(p['parent'].get('events',len(p['parent']['event_ids'])) for p in populations),
        histogram_sumw=math.fsum(source['sumw'] for source in sources),
        standard_cutflow_sumw=merged_cutflow_sumw(histograms),
        weight_branch=WEIGHT_BRANCH if any(p.get('material_selected') for p in populations) else 'genWeight')
    write(work / 'union.json', union)
    put(work / 'union.json', prefix + '/metadata/grouping/union.json')
    write(work / 'point_receipt.json', report)
    put(work / 'point_receipt.json', prefix + '/metadata/point_receipt.json')
    write(Path(results_directory) / 'completed_points' / (row['point'] + '.json'), report)
    return report



def validate_aggregate_population_count(plan, row, populations):
    single = plan.get('single_source_regrouping_canary', False)
    if type(single) is not bool:
        raise ValueError('Single-source regrouping canary must be an explicit boolean')
    if single:
        if plan.get('histogram_only') is not False or len(row['sources']) != 1 or len(populations) != 1:
            raise ValueError('Single-source canary requires one actual parent and full Nano regrouping')
    elif len(populations) < 2:
        raise ValueError('Additive production requires at least two independent GEN populations')


def execute(plan_path, plan_digest, seal_digest, point, cache, scratch, results_directory):
    verify_aggregate_seal(plan_path, plan_digest, seal_digest)
    plan = load_plan(plan_path, plan_digest)
    rows = [row for row in plan['points'] if row['point'] == point]
    if len(rows) != 1:
        raise ValueError('Unknown or duplicate aggregate physical point')
    row = rows[0]
    prefix = row['final_output_base']
    if (not prefix.startswith('/eos/user/') or '..' in Path(prefix).parts
            or Path(prefix).name != plan['campaign'] or Path(prefix).parent.name != 'darkphoton_' + point):
        raise ValueError('Aggregate output is outside its canonical point/campaign namespace')
    populations = unique_populations([load_source(source, row, plan['conditions']['sha256']) for source in row['sources']])
    validate_aggregate_population_count(plan,row,populations)
    settings = ('common', 'cp5', 'beam_settings', 'process_settings', 'source_settings', 'source_z_mm',
                'source_sigma_z_mm', 'source_model', 'timing', 'hadron_decay_policy',
                'signal_decay_policy', 'lifetime_owner', 'signal_parent_transport')
    first_contract = populations[0]['contract']
    if any(any(population['contract'].get(key) != first_contract.get(key) for key in settings)
           for population in populations[1:]):
        raise ValueError('Source generator, lifetime, beam or source settings differ')
    rate = combined_rate(populations, point=point, campaign=plan['campaign'])
    full_identities = [identity for population in populations for identity in population['parent']['event_ids']]
    identities = [identity for population in populations
                  for identity in population.get('selected_event_ids',population['parent']['event_ids'])]
    if (len(full_identities) != row.get('full_gen_events',row['events'])
            or len(identities) != row['events']):
        raise ValueError('Aggregate does not cover its full requested base-plus-new population')
    if not identities:
        if any(not population.get('rate_only') for population in populations):
            raise ValueError('Empty aggregate contains a cohort without a rate-only completion')
        report=dict(schema='shift-dark-photon-additive-rate-only-complete-v1',complete=True,
            rate_only=True,point=point,events=0,full_gen_events=len(full_identities),
            parent_gen_populations=[population['parent'] for population in populations],
            parent_gen_ledger_count=len(populations),sources=[population['source'] for population in populations],
            full_gen_rate=rate,histogram_sumw=0.,standard_cutflow_sumw=0.,
            weight_branch=WEIGHT_BRANCH,final_output_base=prefix,
            plan_sha256=plan_digest,seal_sha256=seal_digest)
        write(Path(results_directory)/'completed_points'/(point+'.json'),report)
        return report
    if plan.get('histogram_only') is True:
        return execute_histogram_only(plan, row, populations, rate, identities,
            plan_digest, seal_digest, scratch, results_directory)
    inputs = [item for population in populations for item in population['inputs']]
    groups = group_inputs(inputs, plan.get('group_events', 2000))
    deployment = Path(plan['scoring_deployment']['path'])
    sys.path.insert(0, str(deployment / 'frozen/scoring/workflow/scripts'))
    import run_merged_dimuon_bdt as scorer
    from dark_photon_production_support import validate_histogram_merge
    io = scorer.io
    guard_spec = plan['publication_guard']
    guard_directory = Path(guard_spec['deployment_path'])
    checked(guard_directory / 'seal.json', guard_spec['seal_sha256'])
    sys.path.insert(0, str(guard_directory))
    import chunk_worker as guard
    qualified = qualified_finalizer(deployment)
    guard.verify_seal(guard_spec['seal_sha256'], guard_spec['point'], scoring=True)
    guard.prepare_publication_guard(guard_spec['seal_sha256'])
    guard.check_headroom()
    original_put = io.put_remote
    axes_checked = set()
    def guarded_put(local, remote):
        histogram = Path(local).parent / 'histograms.root'
        if histogram.is_file() and str(histogram) not in axes_checked:
            qualified.validate_histogram_axes(histogram, deployment / 'frozen/scoring/sm_axis_authority.root')
            axes_checked.add(str(histogram))
        guard.check_headroom()
        return original_put(local, remote)
    io.put_remote = guarded_put
    work = Path(scratch) / 'aggregate'
    work.mkdir(exist_ok=False)
    def stage(item, destination):
        io.get_remote(item['path'], destination)
        if sha(destination) != item['sha256'] or destination.stat().st_size != item['bytes']:
            raise ValueError('Aggregate source or publication checksum/size changed')
    def put(path, destination):
        if io.remote_exists(destination):
            raise FileExistsError('Aggregate output exists; preserve it for reviewed recovery: ' + destination)
        io.transfer(['/usr/bin/xrdfs', 'root://eosuser.cern.ch', 'mkdir', '-p', str(Path(destination).parent)])
        io.put_remote(path, destination)
        item = dict(path=destination, sha256=sha(path), bytes=path.stat().st_size)
        readback = work / ('readback_' + hashlib.sha256(destination.encode()).hexdigest()[:12])
        stage(item, readback)
        readback.unlink()
        return item
    grouped, combined_ids = [], []
    for group in groups:
        directory = work / ('g' + str(group['group']))
        directory.mkdir()
        paths, ids, weights, probabilities, selected_flags = [], [], [], [], []
        for index, item in enumerate(group['inputs']):
            path = directory / ('source_' + str(index) + '.root')
            stage(item, path)
            source_ids, source_weights = event_rows(path)
            validate_event_rows(source_ids, source_weights, item['event_ids'])
            if item.get('material_selected'):
                validate_nano(path, source_ids, item['sampling_probabilities'])
            probabilities.extend(item.get('sampling_probabilities', [1.] * len(source_ids)))
            selected_flags.append(item.get('material_selected', False))
            ids.extend(source_ids)
            weights.extend(source_weights)
            paths.append(path)
        output = directory / 'grouped.root'
        with (directory / 'merge.log').open('x') as log:
            subprocess.run(['hadd', str(output), *map(str, paths)], check=True,
                           stdout=log, stderr=subprocess.STDOUT, timeout=1800)
        validate_event_rows(*event_rows(output), ids)
        if any(selected_flags) and not all(selected_flags):
            raise ValueError('Do not mix native-only and material-selected Nano schemas in one group')
        if any(selected_flags):
            validate_nano(output, ids, probabilities)
        item = put(output, prefix + '/nanoAOD_merged_unscored/ntuple_' + str(group['group']) + '.root')
        grouped.append(dict(group=group['group'], events=len(ids), event_ids=ids,
            native_sumw=float(len(ids)), native_sumw2=float(len(ids)), grouped=item, local_path=str(output),
            sampling_probabilities=probabilities, material_selected=any(selected_flags)))
        combined_ids.extend(ids)
        for path in paths:
            path.unlink()
    validate_event_rows(combined_ids, [1.] * len(combined_ids), identities)
    # All complete unscored groups have passed semantic checks before any scoring.
    scoreplan = read(deployment / 'scoring_components.json')
    analysis = scorer.prepare_analysis(scoreplan, Path(scratch), Path(cache))
    scored, histograms, total_pairs = [], [], 0
    for group in grouped:
        index = group['group']
        metadata = prefix + '/metadata/bdt_grouped/group_' + str(index)
        score_row = dict(index=index, source_transport_path=group['local_path'],
            source_logical_path=group['grouped']['path'], source_sha256=group['grouped']['sha256'],
            source_bytes=group['grouped']['bytes'], expected_events=group['events'],
            weight_branch=WEIGHT_BRANCH if group['material_selected'] else 'genWeight',
            output_nano=prefix + '/nanoAOD_merged/ntuple_' + str(index) + '.root',
            output_histograms=prefix + '/histograms/ntuple_' + str(index) + '.root',
            complete_marker=metadata + '/complete.json', score_receipt=metadata + '/score.json',
            bdt_hist_receipt=metadata + '/bdt_hist.json', score_log=metadata + '/score.log',
            histogram_log=metadata + '/histogram.log')
        result = scorer.run_file(scoreplan, score_row, work / ('score_' + str(index)),
                                 Path(cache), analysis, seal_digest)
        scored.append(result)
        total_pairs += result['marker']['pairs']
        histogram = work / ('histogram_' + str(index) + '.root')
        stage(result['marker']['published']['histograms'], histogram)
        if group['material_selected']:
            validate_nano(work / ('score_' + str(index)) / 'scored.root',
                          group['event_ids'], group['sampling_probabilities'])
        scorer.check_standard_histograms(histogram, group['events'],
            scorer.legacy_weight_sum(work / ('score_' + str(index)) / 'scored.root',
                                     WEIGHT_BRANCH if group['material_selected'] else 'genWeight')
            if group['material_selected'] else group['native_sumw'])
        histograms.append(histogram)
    merged = work / 'histograms.root'
    with (work / 'histogram_merge.log').open('x') as log:
        subprocess.run(['hadd', str(merged), *map(str, histograms)], check=True,
                       stdout=log, stderr=subprocess.STDOUT, timeout=900)
    histogram_audit = validate_histogram_merge(histograms, merged)
    scorer.check_standard_histograms(merged, len(identities), merged_cutflow_sumw(histograms))
    final_histogram = put(merged, prefix + '/histograms_merged/ntuple_0.root')
    write(work / 'cross_sections.json', rate)
    with (work / 'cross_sections.txt').open('x') as target:
        estimate = rate['physical_selected_cross_sections_pb'][0]
        target.write('# physical selected dimuon rate [pb], independent-run statistical error [pb]\n')
        target.write(f"{estimate['value_pb']:.17g} {estimate['error_pb']:.17g}\n")
    rate_items = {name: put(work / name, prefix + '/' + name) for name in ('cross_sections.json', 'cross_sections.txt')}
    union = dict(schema='shift-additive-signal-union-v1', complete=True, point=point, events=len(identities),
        event_ids=identities, native_sumw=float(len(identities)), native_sumw2=float(len(identities)),
        groups=[{key: value for key, value in group.items() if key != 'local_path'} for group in grouped],
        parent_gen_populations=[population['parent'] for population in populations],
        parent_gen_ledger_count=len(populations), sources=[population['source'] for population in populations],
        histogramming_started_after_complete_grouping=True, plan_sha256=plan_digest, seal_sha256=seal_digest)
    report = dict(schema='shift-independent-signal-point-complete-v1', complete=True, point=point,
        events=len(identities), union=union, groups=scored, sample_role='validation_only',
        score_histograms=dict(complete=True, marker=dict(complete=True, events=len(identities), pairs=total_pairs,
            selection_applied=False, model_sha256=scoreplan['model_sha256'], published=dict(histograms=final_histogram))),
        histogram_merge=histogram_audit, cross_sections=rate_items, final_output_base=prefix,
        canonical_sm_histogram_axes_verified=1081,
        canonical_sm_histogram_authority_sha256=sha(deployment / 'frozen/scoring/sm_axis_authority.root'),
        plan_sha256=plan_digest, seal_sha256=seal_digest, created_utc=datetime.now(timezone.utc).isoformat())
    report.update(full_gen_events=len(full_identities),
        histogram_sumw=math.fsum(1./p for group in grouped for p in group['sampling_probabilities']),
        standard_cutflow_sumw=merged_cutflow_sumw(histograms),
        weight_branch=WEIGHT_BRANCH if any(group['material_selected'] for group in grouped) else 'genWeight')
    write(work / 'union.json', union)
    put(work / 'union.json', prefix + '/metadata/grouping/union.json')
    write(work / 'point_receipt.json', report)
    put(work / 'point_receipt.json', prefix + '/metadata/point_receipt.json')
    write(Path(results_directory) / 'completed_points' / (point + '.json'), report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', required=True, type=Path)
    parser.add_argument('--plan-sha256', required=True)
    parser.add_argument('--seal-sha256', required=True)
    parser.add_argument('--point', required=True)
    parser.add_argument('--runtime-cache', required=True, type=Path)
    parser.add_argument('--scratch', required=True, type=Path)
    parser.add_argument('--results-directory', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(execute(args.plan, args.plan_sha256, args.seal_sha256, args.point,
        args.runtime_cache, args.scratch, args.results_directory)))


if __name__ == '__main__':
    main()
