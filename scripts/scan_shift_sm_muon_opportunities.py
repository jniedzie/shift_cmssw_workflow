#!/usr/bin/env python3
# Code style: AI-code.
"""Describe already-present OS GEN muons; never select or reweight originals.

The input is a request object with ``sources`` and ``map_pin``. Every source
binds original_full_exposure, source_authority, feature_pin, prepared_GEN_pin,
and event_authority{event_ids,native_weights}. Native authority and the complete
ordered feature census must agree. ROOT files are not downloaded. Features are
loaded sequentially, one source at a time, with declared compressed/JSON bounds.

An empty transport-parent list is a description, not proof of prompt origin.
Unknown ancestry is retained explicitly. A broad two-station radial miss <=500
cm only limits this descriptive report; omitted pairs are counted. Exact layer
footprints rank that same pair. Neither geometry predicts measured efficiency,
nor do mass, pT, energy or material quantities impose physics cuts here.
"""
import argparse
from collections import Counter
import gzip
import itertools
import json
import math
from pathlib import Path
import struct
import tempfile

from run_shift_sm_postprocessing import digest, download, save
from shift_csc_active_geometry import ray_intersections
from shift_sm_response_features import _station_misses


def load_pin(pin, scratch, maximum_bytes, maximum_json_bytes):
    """Authenticate a bounded JSON/gzip file, deleting only our remote copy."""
    expected = pin.get('bytes')
    if expected is not None and (type(expected) is not int or not 0 <= expected <= maximum_bytes):
        raise ValueError('Pinned file exceeds the declared byte bound')
    source = Path(pin['path'])
    remote = str(source).startswith('/eos/') or str(pin['path']).startswith('root://')
    temporary = remote or not source.is_file()
    path = Path(scratch)/'download' if temporary else source
    try:
        if temporary:
            download(pin['path'], path, pin['sha256'])
        size = path.stat().st_size
        if size > maximum_bytes or (expected is not None and size != expected):
            raise ValueError('Pinned file byte count differs or exceeds bound')
        if digest(path) != pin['sha256']:
            raise ValueError('Pinned file SHA-256 differs')
        with path.open('rb') as stream:
            compressed = stream.read(2) == b'\x1f\x8b'
        opener = gzip.open if compressed else open
        with opener(path, 'rb') as stream:
            data = stream.read(maximum_json_bytes+1)
        if len(data) > maximum_json_bytes:
            raise ValueError('Expanded JSON exceeds the declared byte bound')
        return json.loads(data)
    finally:
        if temporary:
            path.unlink(missing_ok=True)


def validate_census(source, authority, features):
    """Bind full original exposure, native identity/weight, and prepared bytes."""
    if (authority.get('complete') is not True or authority.get('exit_code') != 0
            or authority.get('source_id') != source['source_id']
            or authority.get('source') != source['original_full_exposure']):
        raise ValueError('Successful native source authority/exposure differs')
    for name in ('feature_pin', 'prepared_GEN_pin'):
        pin = source[name]
        if not any(all(row.get(k) == pin[k] for k in ('path', 'sha256', 'bytes'))
                   for row in authority.get('prepared_publications', [])):
            raise ValueError('Prepared publication pin absent from native source authority')
    saved = source['original_full_exposure']['saved_events']
    census = source['event_authority']
    ids, weights = census['event_ids'], census['native_weights']
    events = features['events']
    if (type(saved) is not int or saved < 0 or not len(ids) == len(weights) == len(events) == saved
            or len({tuple(row) for row in ids}) != saved):
        raise ValueError('Full native census count or unique identities differ')
    prepared = source['prepared_GEN_pin']['sha256']
    raw = source['original_full_exposure']['gen_sha256']
    reads = features.get('summary', {}).get('source_read_counts', [])
    headers = features.get('sources', [])
    if (features.get('schema') != 'shift-gen-material-features-v1'
            or features.get('maximum_events_per_source') is not None
            or features.get('prefix_requested') is not False
            or features.get('zero_GEN_muon_records_retained') is not True
            or features.get('summary', {}).get('events') != saved
            or len(headers) != 1 or headers[0].get('sha256') != prepared
            or headers[0].get('source_file_event_count') != saved
            or len(reads) != 1 or reads[0].get('source_file_sha256') != prepared
            or reads[0].get('entire_input_file_read') is not True
            or reads[0].get('events_read') != saved
            or reads[0].get('source_file_event_count') != saved):
        raise ValueError('Features are not the complete prepared GEN census')
    for index, event in enumerate(events):
        actual, expected = event['native_weight'], weights[index]
        if (event['event_id'] != ids[index] or any(type(x) is not int for x in ids[index])
                or len(ids[index]) != 3
                or any(isinstance(x, bool) or not isinstance(x, (int, float))
                       or not math.isfinite(x) for x in (actual, expected))
                or struct.pack('!d', actual) != struct.pack('!d', expected)):
            raise ValueError('Ordered native event identity or exact weight differs')
        if (event.get('source_id') != source['source_id']
                or event.get('original_source_identity_known') is not True
                or event.get('original_source_sha256') != raw
                or event.get('source_file_sha256') != prepared):
            raise ValueError('Raw/prepared GEN event identity differs')
        muons = event['muons']
        if (event.get('GEN_muons') != len(muons)
                or len({m['barcode'] for m in muons}) != len(muons)
                or any(abs(m['pdg']) != 13 for m in muons)):
            raise ValueError('Complete unique GEN muon inventory differs')


def ancestry_description(event, muon, nodes):
    """Describe known ancestry and preserve missing or unclassified links."""
    ancestors = muon.get('ancestor_barcodes')
    first = muon.get('first_non_muon_parent_barcodes')
    reasons = []
    if not isinstance(ancestors, list) or not isinstance(first, list):
        reasons.append('missing saved ancestor inventory')
    numbers = set(ancestors or []) | set(first or []) | set(muon.get('immediate_parent_barcodes', []))
    missing = sorted(numbers-set(nodes))
    if missing:
        reasons.append('ancestor nodes missing')
    unknown = event.get('unknown_transport_ancestor_barcodes')
    if not isinstance(unknown, list):
        reasons.append('missing unknown-transport inventory')
    unclassified = sorted(numbers.intersection(unknown or []))
    if unclassified:
        reasons.append('unclassified transport ancestry')
    return dict(reasons=reasons, missing_ancestor_barcodes=missing,
        unknown_transport_ancestor_barcodes=unclassified,
        first_non_muon_ancestors=[nodes[n] for n in first or [] if n in nodes],
        ancestor_nodes=[nodes[n] for n in sorted(numbers) if n in nodes],
        no_listed_transport_parent_is_not_proof_of_prompt_origin=True)


def compact_crossings(geometry, muon):
    result = ray_intersections(geometry, muon['birth_cm'], muon['momentum_GeV'])
    counts = Counter((h['endcap'], h['station'], h['ring'], h['chamber']) for h in result['hits'])
    return dict(by_endcap={side: {k: v for k, v in row.items() if k != 'layer_ids'}
                           for side, row in result['by_endcap'].items()},
        chamber_layer_counts=[dict(endcap=k[0], station=k[1], ring=k[2], chamber=k[3], layer_count=v)
                              for k, v in sorted(counts.items())],
        best_layer_clearance_by_station=result['best_layer_clearance_by_station'],
        two_stations_each_endcap=result['two_stations_each_endcap'])


def describe_event(event, geometry):
    """Keep full candidate-muon records once per event and compact pair links."""
    muons = event['muons']
    counts = Counter(events=1, GEN_muons=len(muons), zero_muon_events=int(not muons))
    counts['all_OS_pairs'] = sum(m['pdg'] == 13 for m in muons)*sum(m['pdg'] == -13 for m in muons)
    available = []
    for muon in muons:
        parents = muon.get('transport_parent_barcodes')
        if parents == []:
            available.append(muon)
        elif isinstance(parents, list) and parents:
            counts['muons_with_listed_transport_parents'] += 1
        else:
            counts['muons_unknown_transport_parent_inventory'] += 1
    counts['muons_without_listed_transport_parents'] = len(available)
    nodes = {row['barcode']: row for row in event.get('ancestor_nodes', [])}
    if len(nodes) != len(event.get('ancestor_nodes', [])):
        raise ValueError('Duplicate ancestor node barcode')
    pairs, omitted, records = [], [], {}
    rays = {muon['barcode']: _station_misses(muon) for muon in available}
    for left, right in itertools.combinations(available, 2):
        if left['pdg']*right['pdg'] >= 0:
            continue
        counts['no_listed_transport_parent_OS_pairs'] += 1
        pair = [left, right]
        misses = [rays[m['barcode']] for m in pair]
        if any(row is None or row['two_station_miss'] is None for row in misses):
            counts['omitted_unknown_or_no_two_forward_stations'] += 1
            omitted.append(dict(barcodes=[m['barcode'] for m in pair], reason='unknown or no two forward stations'))
            continue
        miss = max(row['two_station_miss'] for row in misses)
        if miss > 500.:
            counts['omitted_outside_broad_500cm_support'] += 1
            continue
        for muon, broad in zip(pair, misses):
            if muon['barcode'] not in records:
                if (not math.isfinite(muon['energy_GeV']) or muon['energy_GeV'] < 0
                        or not math.isfinite(muon['p_GeV']) or muon['p_GeV'] <= 0):
                    raise ValueError('Candidate requires finite positive total momentum and nonnegative energy')
                records[muon['barcode']] = dict(GEN=muon, broad_station_misses_cm=broad,
                    ancestry=ancestry_description(event, muon, nodes),
                    active_CSC=compact_crossings(geometry, muon))
        crossings = [records[m['barcode']]['active_CSC'] for m in pair]
        energy = math.fsum(m['energy_GeV'] for m in pair)
        momentum = [math.fsum(m['momentum_GeV'][i] for m in pair) for i in range(3)]
        mass2 = energy*energy-math.fsum(v*v for v in momentum)
        rank = [min(row['by_endcap'][side]['station_count'] for row in crossings for side in ('plus', 'minus')),
                min(row['by_endcap'][side]['layer_count'] for row in crossings for side in ('plus', 'minus'))]
        pairs.append(dict(barcodes=[m['barcode'] for m in pair], broad_two_station_miss_cm=miss,
            mass_GeV=math.sqrt(mass2) if mass2 >= 0 else None, mass_squared_GeV2=mass2,
            pt_GeV=math.hypot(*momentum[:2]), energy_GeV=energy, momentum_GeV=momentum,
            descriptive_rank_min_stations_then_layers=rank,
            both_muons_cross_two_stations_each_endcap=all(row['two_stations_each_endcap'] for row in crossings),
            ancestry_unknown=any(records[m['barcode']]['ancestry']['reasons'] for m in pair)))
    counts['reported_pairs'] = len(pairs)
    counts['events_with_reported_pairs'] = int(bool(pairs))
    counts['reported_pairs_with_unknown_ancestry'] = sum(row['ancestry_unknown'] for row in pairs)
    pairs.sort(key=lambda row: row['descriptive_rank_min_stations_then_layers'], reverse=True)
    detail = None
    if pairs or omitted:
        detail = dict(event_id=event['event_id'], native_weight=event['native_weight'],
            graph_sha256=event.get('graph_sha256'), candidate_muons=list(records.values()),
            pairs=pairs, unranked_pairs=omitted)
    return counts, detail


def scan(request):
    """Sequential authenticated scan; original exposure and W are descriptive."""
    sources = request['sources']
    if not sources:
        raise ValueError('At least one complete source is required')
    if len({source['source_id'] for source in sources}) != len(sources):
        raise ValueError('Duplicate source/exposure')
    if len({source['original_full_exposure']['gen_sha256'] for source in sources}) != len(sources):
        raise ValueError('Duplicate raw GEN exposure')
    if len({source['prepared_GEN_pin']['sha256'] for source in sources}) != len(sources):
        raise ValueError('Duplicate prepared GEN exposure')
    maximum = request.get('maximum_feature_bytes', 32*1048576)
    expanded = request.get('maximum_feature_json_bytes', 256*1048576)
    output_bound = request.get('maximum_report_bytes', 16*1048576)
    report = dict(schema='shift-existing-GEN-muon-opportunities-v1', complete=False,
        diagnostic_only=True, map_pin=request['map_pin'], sources=[], totals={},
        no_admission_or_weight_changes=True, no_muon_pt_or_mass_cut=True,
        descriptive_broad_two_station_miss_margin_cm=500.,
        no_listed_transport_parent_is_not_proof_of_prompt_origin=True,
        geometry_is_straight_active_layer_footprints_not_measured_reco_efficiency=True,
        scattering_field_losses_electronics_and_reconstruction_not_predicted=True,
        material_is_saved_external_path_description_not_survival_weight=True)
    total = Counter()
    with tempfile.TemporaryDirectory(prefix='shift-muon-opportunities-') as scratch:
        geometry = load_pin(request['map_pin'], scratch, 2*1048576, 32*1048576)
        if geometry.get('schema') != 'shift-CSC-active-layer-surfaces-v1' or not geometry.get('layers'):
            raise ValueError('Require a pinned active CSC layer map')
        report['map_source_pins'] = geometry.get('source_pins')
        for source in sources:
            authority = load_pin(source['source_authority'], scratch, 16*1048576, 64*1048576)
            features = load_pin(source['feature_pin'], scratch, maximum, expanded)
            validate_census(source, authority, features)
            counts, details = Counter(), []
            for event in features['events']:
                values, detail = describe_event(event, geometry)
                counts.update(values)
                if detail is not None:
                    details.append(detail)
            total.update(counts)
            report['sources'].append(dict(source_id=source['source_id'], stratum=source['stratum'],
                original_full_exposure=source['original_full_exposure'],
                source_authority=source['source_authority'], feature_pin=source['feature_pin'],
                prepared_GEN_pin=source['prepared_GEN_pin'], full_ordered_native_census_verified=True,
                counts=dict(counts), events=details))
            del features, authority
            if len(json.dumps(report, indent=2, allow_nan=False).encode()) > output_bound:
                raise ValueError('Opportunity report exceeds declared byte bound; split input sources')
    report.update(complete=True, totals=dict(total))
    if len(json.dumps(report, indent=2, allow_nan=False).encode())+1 > output_bound:
        raise ValueError('Opportunity report exceeds declared byte bound; split input sources')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('request', type=Path)
    parser.add_argument('--output', type=Path, default=Path('report.json'))
    args = parser.parse_args()
    try:
        request = json.loads(args.request.read_text())
        report = scan(request)
        report['request_sha256'] = digest(args.request)
        bound = request.get('maximum_report_bytes', 16*1048576)
        if len(json.dumps(report, indent=2, allow_nan=False).encode())+1 > bound:
            raise ValueError('Opportunity report including request hash exceeds declared byte bound')
        save(args.output, report)
    except Exception as error:
        save(args.output, dict(complete=False, request_sha256=digest(args.request), error=str(error)))
        raise


if __name__ == '__main__':
    main()
