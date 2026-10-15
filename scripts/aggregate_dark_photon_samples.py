#!/usr/bin/env python3
# Code style: AI-code. AI-generated additive same-point Nano and histogram aggregation.
"""Combine independent ready GEN populations without replacing their records.

This worker consumes a sealed additive plan. It never generates events, submits
jobs, filters events or changes detector settings. Complete unscored Nano files
are grouped before the existing frozen scoring/histogram pipeline is called.
Recovery views of the same GEN ledger count once. Rate estimates are averaged,
not summed; their technical trial counts are never interpreted as luminosity.
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
from pythia_generation_ledger import read_pythia_generation_ledger


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
                    or previous['rate']['proposal_ledger'] != record['proposal_ledger']):
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
    fields = ('mechanism', 'model', 'cross_section_convention', 'physical_br_mumu',
              'sampling_epsilon', 'beam_energy_GeV', 'source_model', 'corrections',
              'generated_mass_support_gev')
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
    return dict(schema='shift-dark-photon-additive-cross-sections-v1', point=point, campaign=campaign,
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
    if (audit['event_ids'] != parent['event_ids'] or audit['sum_weights'] != parent['native_sumw']
            or audit['sum_weights_squared'] != parent['native_sumw2']
            or manifest['output_sha256'] != parent['gen_sha256']
            or read_pythia_generation_ledger(gen / 'cmsRun.log', expected_accepted=row['events']) != audit['proposal_ledger']):
        raise ValueError('Source full GEN audit differs from its inventory')
    rate = rate_record(contract, audit, point=source['point'], campaign=plan['campaign'],
        source_hashes={name: sha(gen / name) for name in ('contract.json', 'validation.json', 'manifest.json', 'cmsRun.log')},
        gen_path=parent['gen_path'], gen_sha256=parent['gen_sha256'])
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
    if (union.get('complete') is not True or union.get('parent_gen_ledger_count') != 1
            or union['parent_gen'] != parent or union['event_ids'] != parent['event_ids']
            or union['native_sumw'] != parent['native_sumw'] or union['native_sumw2'] != parent['native_sumw2']):
        raise ValueError('Source Nano union is not its complete single GEN population')
    groups = union.get('groups') or [dict(group=0, events=row['events'], event_ids=parent['event_ids'], grouped=union['grouped'])]
    inputs, all_ids = [], []
    for group in groups:
        ids = group['event_ids']
        if group['events'] != len(ids):
            raise ValueError('Source Nano group count differs')
        item = group['grouped']
        if Path(item['path']).parent != Path(row['final_output_base']) / 'nanoAOD_merged_unscored':
            raise ValueError('Aggregate must consume complete unscored Nano inputs')
        inputs.append(dict(**item, events=len(ids), event_ids=ids))
        all_ids.extend(ids)
    validate_event_rows(all_ids, [1.] * len(all_ids), parent['event_ids'])
    return dict(source=dict(source, completed_point_path=str(report_path), completed_point_sha256=sha(report_path)),
                parent=parent, rate=rate, inputs=inputs, contract=contract)


def load_plan(path, digest):
    plan = read(checked(path, digest))
    if plan.get('schema') != SCHEMA or not SAFE_NAME.fullmatch(plan.get('campaign', '')):
        raise ValueError('Invalid additive production plan')
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
    if len(populations) < 2:
        raise ValueError('Additive production requires at least two independent GEN populations')
    settings = ('common', 'cp5', 'beam_settings', 'process_settings', 'source_z_mm',
                'source_sigma_z_mm', 'source_model', 'timing', 'hadron_decay_policy',
                'signal_decay_policy', 'lifetime_owner', 'signal_parent_transport')
    first_contract = populations[0]['contract']
    if any(any(population['contract'].get(key) != first_contract.get(key) for key in settings)
           for population in populations[1:]):
        raise ValueError('Source generator, lifetime, beam or source settings differ')
    rate = combined_rate(populations, point=point, campaign=plan['campaign'])
    identities = [identity for population in populations for identity in population['parent']['event_ids']]
    if len(identities) != row['events']:
        raise ValueError('Aggregate does not cover its full requested base-plus-new population')
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
        paths, ids, weights = [], [], []
        for index, item in enumerate(group['inputs']):
            path = directory / ('source_' + str(index) + '.root')
            stage(item, path)
            source_ids, source_weights = event_rows(path)
            validate_event_rows(source_ids, source_weights, item['event_ids'])
            ids.extend(source_ids)
            weights.extend(source_weights)
            paths.append(path)
        output = directory / 'grouped.root'
        with (directory / 'merge.log').open('x') as log:
            subprocess.run(['hadd', str(output), *map(str, paths)], check=True,
                           stdout=log, stderr=subprocess.STDOUT, timeout=1800)
        validate_event_rows(*event_rows(output), ids)
        item = put(output, prefix + '/nanoAOD_merged_unscored/ntuple_' + str(group['group']) + '.root')
        grouped.append(dict(group=group['group'], events=len(ids), event_ids=ids,
            native_sumw=float(len(ids)), native_sumw2=float(len(ids)), grouped=item, local_path=str(output)))
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
            source_bytes=group['grouped']['bytes'], expected_events=group['events'], weight_branch='genWeight',
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
        scorer.check_standard_histograms(histogram, group['events'], group['native_sumw'])
        histograms.append(histogram)
    merged = work / 'histograms.root'
    with (work / 'histogram_merge.log').open('x') as log:
        subprocess.run(['hadd', str(merged), *map(str, histograms)], check=True,
                       stdout=log, stderr=subprocess.STDOUT, timeout=900)
    histogram_audit = validate_histogram_merge(histograms, merged)
    scorer.check_standard_histograms(merged, len(identities), float(len(identities)))
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
