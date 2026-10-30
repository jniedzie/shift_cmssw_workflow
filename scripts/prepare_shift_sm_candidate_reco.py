#!/usr/bin/env python3
# Code style: AI-code.
"""Freeze a bounded QCD response draw from completed prepared GEN cohorts.

Every known shallow pointing pair is retained, together with random controls
in every other nonempty source/region cell. Original admission p and native W
are unchanged. The extra diagnostic q is kept separate from Nano W/p. This
preparer neither submits jobs nor qualifies a production or its normalization.
"""
import argparse
import copy
import gzip
import hashlib
import itertools
import json
import math
from pathlib import Path

from prepare_shift_sm_prospective_benchmark import (CONTROL_COUNTS, prepare_benchmark,
                                                  prepare_selection_comparison)
from run_shift_sm_response_collection import verify_exposure
from scan_shift_sm_muon_opportunities import parent_union_description, validate_census
from shift_sm_response_features import _column_ratio, _station_misses, feature_values
from shift_production_selection import bind_source, prepare_default_selection


def fresh_census_authority(record, authority, features):
    """Declare first SIM on an untouched source, preserving its unused old mask."""
    if record.get('first_sim_source') is not True or record.get('preparation_mask_unused') is not True:
        raise ValueError('Fresh census requires explicit first-SIM and unused preparation-mask evidence')
    descriptor = copy.deepcopy(authority['selected_source'])
    if descriptor.get('decay_proposal') is not None:
        raise ValueError('Fresh response census does not reopen a decay proposal')
    for name in ('selected_indices', 'sampling_probabilities', 'sampling', 'production_selection'):
        descriptor.pop(name, None)
    source = authority['source']
    sid = source['source_id']
    old_pin = dict(path=str(Path(record['result_path']).resolve()), sha256=record['result_sha256'])
    events = [dict(source_id=sid, original_source_sha256=source['gen_sha256'],
        feature_source_sha256=descriptor['gen_sha256'], event_id=row['event_id'],
        graph_sha256=row['graph_sha256'], native_weight=row['native_weight'],
        sampling_probability=1., previous_selection_domains=[], proposal_kind='ordinary',
        proposal_draw_id='unchanged-prepared-GEN:'+row['graph_sha256'],
        proposal_density_ratio=1., proposal_correction=1.,
        proposal_provenance_sha256=record['result_sha256'], saved_decays_unchanged=True)
        for row in features['events']]
    inputs = dict(schema='shift-production-material-inputs-v1',
        cohort_id=record.get('response_cohort_id', 'SM_response_census_20261029_v1')+':'+sid,
        selection_seed='full-census-no-additional-draw', policy_variant='sm-response-census-v1',
        features=features, authority=dict(schema='shift-material-admission-authority-v1',
            population_scope='representative_census', sources=[dict(source_id=sid,
                original_source_sha256=source['gen_sha256'],
                feature_source_sha256s=[descriptor['gen_sha256']], full_gen_exposure=source,
                generator_profile='ordinary', decay_dependent_filter=False)], events=events))
    ledger = prepare_default_selection(inputs)
    selected = bind_source(descriptor, ledger)
    if (selected['selected_indices'] != list(range(source['saved_events']))
            or selected['sampling_probabilities'] != [1.]*source['saved_events']):
        raise ValueError('Fresh response census must retain every original with p=1')
    updated = copy.deepcopy(authority)
    updated.update(selected_source=selected, selected_events=source['saved_events'],
        response_census_selection=dict(schema='shift-sm-first-response-census-v1',
            first_SIM_on_this_source=True, unused_preparation_authority=old_pin,
            original_unused_selected_source=authority['selected_source'],
            GEN_unchanged=True, original_exposure_unchanged=True))
    return updated, ledger, old_pin


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            result.update(block)
    return result.hexdigest()


def load_record(path, checksum, maximum_bytes=32*1048576):
    """Bound local inputs and authenticate the actual bytes before decoding."""
    path = Path(path)
    if path.stat().st_size > maximum_bytes or digest(path) != checksum:
        raise ValueError('Input size or SHA-256 differs: '+str(path))
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'rb') as stream:
        data = stream.read(512*1048576+1)
    if len(data) > 512*1048576:
        raise ValueError('Expanded feature JSON exceeds 512 MiB')
    return json.loads(data)


def joint_opportunities(event):
    """Require direction and known parent paths on the same opposite-sign pair."""
    parents = {row['barcode']: row for row in event['parent_flights']}
    nodes = {row['barcode']: row for row in event['ancestor_nodes']}
    if len(parents) != len(event['parent_flights']) or len(nodes) != len(event['ancestor_nodes']):
        raise ValueError('Duplicate parent or ancestor barcode')
    rays = {row['barcode']: _station_misses(row) for row in event['muons']}
    counts = dict(n_joint_pointing_pairs=0, n_credible_shallow_pairs=0,
        n_shallow_parent_unknown_column_pairs=0, n_unknown_parent_shallow_prefix_pairs=0)
    examples, known_pointing_depths = [], []
    for pair in itertools.combinations(event['muons'], 2):
        if pair[0]['pdg']*pair[1]['pdg'] >= 0:
            continue
        projections = [rays[row['barcode']] for row in pair]
        if any(row is None or row['two_station_miss'] is None for row in projections):
            continue
        miss = max(row['two_station_miss'] for row in projections)
        if miss > 500.:
            continue
        counts['n_joint_pointing_pairs'] += 1
        paths = parent_union_description(pair, parents,
            event.get('unknown_transport_ancestor_barcodes'), nodes)
        if not paths['all_parent_paths_known_and_fully_covered']:
            counts['n_unknown_parent_shallow_prefix_pairs'] += 1
            continue
        known_pointing_depths.append(paths['known_union_depth'])
        if paths['known_union_depth'] > 1.:
            continue
        ratios = [_column_ratio(row) for row in pair]
        known_columns = None not in ratios
        key = 'n_credible_shallow_pairs' if known_columns else 'n_shallow_parent_unknown_column_pairs'
        counts[key] += 1
        examples.append(dict(muon_barcodes=[row['barcode'] for row in pair],
            two_station_miss_cm=miss, known_union_parent_depth=paths['known_union_depth'],
            min_muon_p_GeV=min(row['p_GeV'] for row in pair),
            max_muon_column_loss_over_p=max(ratios) if known_columns else None))
    return dict(counts, candidate_pair_examples=examples[:8],
        minimum_known_two_station_pointing_parent_depth=(min(known_pointing_depths)
            if known_pointing_depths else None))


def source_rows(record):
    """Check the complete census, then address the original selected offsets."""
    authority = load_record(record['result_path'], record['result_sha256'])
    features = load_record(record['feature_path'], record['feature_sha256'])
    descriptor = authority['selected_source']
    exposure = authority['source']
    if not exposure['stratum'].startswith('qcd_'):
        raise ValueError('This response draw is for QCD sources only')
    publications = authority['prepared_publications']
    feature_pin = next((row for row in publications
        if row['sha256'] == record['feature_sha256'] and row.get('name') == 'qcd_features.json.gz'), None)
    gen_pin = next((row for row in publications if row['sha256'] == descriptor['gen_sha256']
        and row['path'] == descriptor['gen']), None)
    if feature_pin is None or gen_pin is None or descriptor['normalization_record'] != exposure:
        raise ValueError('Features, prepared GEN or normalization differ from source authority')
    if Path(record['feature_path']).stat().st_size != feature_pin['bytes']:
        raise ValueError('Feature byte count differs from prepared source authority')
    validate_census(dict(source_id=exposure['source_id'], original_full_exposure=exposure,
        feature_pin=feature_pin, prepared_GEN_pin=gen_pin,
        event_authority=dict(event_ids=descriptor['event_ids'], native_weights=descriptor['weights'])),
        authority, features)
    fresh = None
    if record.get('fresh_census') is True:
        authority, ledger, old_pin = fresh_census_authority(record, authority, features)
        descriptor = authority['selected_source']
        fresh = dict(authority=authority, ledger=ledger, original_unused_preparation_pin=old_pin)
    indices = descriptor['selected_indices']
    probabilities = descriptor['sampling_probabilities']
    if (len(indices) != len(probabilities) or len(indices) != len(set(indices))
            or any(type(i) is not int or not 0 <= i < exposure['saved_events'] for i in indices)
            or any(isinstance(p, bool) or not math.isfinite(p) or not 0 < p <= 1 for p in probabilities)):
        raise ValueError('Frozen admission indices or probabilities are invalid')
    positions = {index: position for position, index in enumerate(indices)}
    rows, missed = [], []
    for index, event in enumerate(features['events']):
        joint = joint_opportunities(event)
        row = dict(source_id=exposure['source_id'], stratum=exposure['stratum'],
            original_index=index, event_id=event['event_id'], native_weight=event['native_weight'],
            gen_sha256=exposure['gen_sha256'], prepared_gen_sha256=descriptor['gen_sha256'],
            features=feature_values(event), joint=joint)
        if index not in positions:
            if joint['n_credible_shallow_pairs'] or joint['n_shallow_parent_unknown_column_pairs']:
                missed.append(row)
            continue
        position = positions[index]
        row.update(selected_position=position, existing_sampling_probability=probabilities[position])
        # The region classifier's depth must describe the same two-station
        # candidate scope, rather than a different one-station minimum.
        if joint['n_joint_pointing_pairs'] and not any(joint[key] for key in (
                'n_credible_shallow_pairs', 'n_shallow_parent_unknown_column_pairs',
                'n_unknown_parent_shallow_prefix_pairs')):
            row['features']['min_parent_prefix_depth_among_pointing_pairs'] = joint[
                'minimum_known_two_station_pointing_parent_depth']
        rows.append(row)
    pin = dict(source_id=exposure['source_id'], stratum=exposure['stratum'],
        original_result_path=str(Path(record['result_path']).resolve()),
        original_result_sha256=record['result_sha256'], original_raw_gen_sha256=exposure['gen_sha256'],
        prepared_gen_sha256=descriptor['gen_sha256'], prepared_gen_path=descriptor['gen'],
        full_original_exposure=copy.deepcopy(exposure), original_selected_source=copy.deepcopy(descriptor),
        selected_descriptor_canonical_json_sha256=hashlib.sha256(json.dumps(descriptor,
            sort_keys=True, separators=(',', ':')).encode()).hexdigest())
    return rows, missed, pin, fresh


def prepare_draw(records, seed, controls_per_cell=4, maximum_events=2000, comparison=None):
    """Retain every genuine candidate and positive support in each source cell."""
    if type(controls_per_cell) is not int or controls_per_cell < 1:
        raise ValueError('Require a positive control count per source/region cell')
    if type(maximum_events) is not int or maximum_events < 1 or not isinstance(seed, str) or not seed:
        raise ValueError('Require an explicit seed and positive event budget')
    selected, strata, exposures, pins, missed = [], [], [], [], []
    comparison_rows = []
    source_ids, raw_hashes, fresh_authorities = set(), set(), {}
    for record in records:
        if comparison is not None and not all(record.get(key) is True for key in
                ('fresh_census', 'first_sim_source', 'preparation_mask_unused')):
            raise ValueError('Comparison requires an untouched first-SIM full census for every source')
        rows, omitted, pin, fresh = source_rows(record)
        sid, raw = pin['source_id'], pin['original_raw_gen_sha256']
        if sid in source_ids or raw in raw_hashes:
            raise ValueError('Duplicate original source/exposure')
        source_ids.add(sid)
        raw_hashes.add(raw)
        if fresh is not None:
            fresh_authorities[sid] = fresh
        if comparison is not None:
            comparison_rows.extend(rows)
        draw = (prepare_benchmark(rows, seed, maximum_events,
            controls={name: controls_per_cell for name in CONTROL_COUNTS})
            if comparison is None else dict(selected=[], strata=[]))
        selected.extend(draw['selected'])
        strata.extend(dict(cell, source_id=sid) for cell in draw['strata'])
        missed.extend(omitted)
        if draw['selected'] or comparison is not None:
            pins.append(pin)
        exposure = pin['full_original_exposure']
        exposures.append(dict(source_id=sid, stratum=pin['stratum'],
            original_raw_gen_sha256=raw, prepared_gen_sha256=pin['prepared_gen_sha256'],
            original_result_path=pin['original_result_path'], original_result_sha256=pin['original_result_sha256'],
            original_full_exposure=exposure, original_saved_events=exposure['saved_events'],
            old_selected_events=len(rows)))
    comparison_plan = None
    if comparison is not None:
        comparison_plan = prepare_selection_comparison(comparison_rows, seed, **comparison)
        selected, strata = comparison_plan['selected'], comparison_plan['strata']
        for source in exposures:
            source['comparison_arm'] = comparison_plan['source_assignments'][source['source_id']]
    if len(selected) > maximum_events:
        raise ValueError('All candidates and controls exceed the declared event budget')
    plan = dict(schema='shift-sm-prospective-response-benchmark-v1', proposal_only=True,
        selection_seed=seed, population_events=sum(s['old_selected_events'] for s in exposures),
        selected_events=len(selected), maximum_events=maximum_events, selected=selected, strata=strata,
        controls_per_source_region=controls_per_cell, draw_frozen=True,
        old_native_weights_and_sampling_probabilities_unchanged=True,
        benchmark_q_is_separate_from_original_frozen_p=True,
        worker_Nano_convention='Original W/p unchanged; diagnostic response uses W/(p*q) once.',
        no_muon_energy_or_pt_cut=True, positive_q_for_every_nonempty_population_stratum=True,
        full_original_GEN_exposure_must_be_preserved=True, detector_jobs_submitted=False,
        ordinary_campaign_publication_qualified=False, bulk_production_policy_qualified=False)
    if comparison_plan is not None:
        plan.update(comparison_plan, maximum_events=maximum_events, draw_frozen=True)
        plan.pop('controls_per_source_region')
    exposure = dict(schema='shift-sm-response-full-population-exposure-v1', sources=exposures,
        original_saved_GEN_events=sum(s['original_saved_events'] for s in exposures),
        includes_sources_with_no_benchmark_event_drawn=True)
    verify_exposure(exposure, plan)
    return dict(plan=plan, exposure=exposure, source_pins=dict(
        schema='shift-sm-prospective-source-pins-v1', sources=pins), missed_candidates=missed,
        fresh_authorities=fresh_authorities)


def materialize_fresh_authorities(result, directory):
    """Persist new first-SIM authorities; never overwrite original preparation."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for sid, fresh in result.get('fresh_authorities', {}).items():
        if not sid or Path(sid).name != sid:
            raise ValueError('Invalid source ID for persistent authority')
        target = directory/sid
        target.mkdir(exist_ok=True)
        for name, value in [('result.json', fresh['authority']), ('selection_ledger.json', fresh['ledger'])]:
            data = json.dumps(value, indent=2, allow_nan=False)+'\n'
            path = target/name
            if path.exists() and path.read_text() != data:
                raise ValueError('Refuse to overwrite a frozen response authority')
            path.write_text(data)
        authority_path = target/'result.json'
        for row in result['source_pins']['sources']+result['exposure']['sources']:
            if row['source_id'] == sid:
                row.update(original_result_path=str(authority_path.resolve()),
                    original_result_sha256=digest(authority_path))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('configuration', type=Path, help='JSON with sources, seed and optional control/event budgets')
    parser.add_argument('output_directory', type=Path)
    args = parser.parse_args()
    config = json.loads(args.configuration.read_text())
    if args.output_directory.exists() and any(args.output_directory.iterdir()):
        raise ValueError('Refuse to overwrite or redraw a frozen response cohort')
    result = prepare_draw(config['sources'], config['seed'], config.get('controls_per_cell', 4),
        config.get('maximum_events', 2000), comparison=config.get('comparison'))
    args.output_directory.mkdir(parents=True, exist_ok=True)
    materialize_fresh_authorities(result, args.output_directory/'response_authority')
    plan = args.output_directory/'plan.json'
    plan.write_text(json.dumps(result['plan'], indent=2, allow_nan=False)+'\n')
    result['source_pins'].update(frozen_benchmark_path=str(plan.resolve()),
        frozen_benchmark_sha256=digest(plan))
    for name in ('exposure', 'source_pins', 'missed_candidates'):
        (args.output_directory/(name+'.json')).write_text(json.dumps(result[name], indent=2, allow_nan=False)+'\n')
    print(json.dumps(dict(selected_events=result['plan']['selected_events'],
        original_saved_GEN_events=result['exposure']['original_saved_GEN_events'],
        missed_candidates=len(result['missed_candidates']))))


if __name__ == '__main__':
    main()
