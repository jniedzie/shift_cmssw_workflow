#!/usr/bin/env python3
# Code style: AI-code. AI-generated prospective QCD response-study draw.
"""Sample untouched prepared GEN for a bounded diagnostic, not bulk production.

The original reconstruction admission p and native W stay unchanged. This
separate benchmark draw has probability q. Frozen workers still write W/p;
only the diagnostic response analysis additionally divides by q. The output
does not authorize submission or ordinary campaign normalization.
"""
import argparse
from collections import defaultdict
import gzip
import hashlib
import json
import math
from pathlib import Path
import re

from prepare_shift_sm_response_inventory import read_record


SCHEMA = 'shift-sm-prospective-response-benchmark-v1'
CONTROL_COUNTS = dict(border_pointing=5, deep_pointing=5,
                      no_both_sides_pointing_pair=10, unknown_parent=10)


def identity(row):
    return row['gen_sha256'], tuple(row['event_id'])


def benchmark_region(row):
    """Use joint pair evidence; independent minima cannot prove an opportunity."""
    if row.get('unclassified_physical_model') is True:
        return 'protected_unknown_model'
    joint = row['joint']
    pair_counts = [joint[key] for key in ('n_credible_shallow_pairs',
        'n_shallow_parent_unknown_column_pairs', 'n_unknown_parent_shallow_prefix_pairs',
        'n_joint_pointing_pairs')]
    if any(type(count) is not int or count < 0 for count in pair_counts):
        raise ValueError('Require nonnegative joint-pair census counts')
    # Missing muon column is still a useful response-test case. Credibility
    # here concerns the complete parent path, not a guessed muon survival.
    full_parent_pairs = (joint['n_credible_shallow_pairs']
                         + joint['n_shallow_parent_unknown_column_pairs'])
    if full_parent_pairs + joint['n_unknown_parent_shallow_prefix_pairs'] > joint['n_joint_pointing_pairs']:
        raise ValueError('Shallow pair counts exceed the actual pointing-pair census')
    if full_parent_pairs:
        return 'credible_shallow_pointing'
    if joint['n_unknown_parent_shallow_prefix_pairs']:
        return 'unknown_parent'
    if joint['n_joint_pointing_pairs'] == 0:
        return 'no_both_sides_pointing_pair'
    depth = row['features']['min_parent_prefix_depth_among_pointing_pairs']
    if depth is None or not math.isfinite(depth) or depth <= 1:
        raise ValueError('Pointing pair has inconsistent joint parent evidence')
    return 'border_pointing' if depth <= 10 else 'deep_pointing'


def draw_hash(seed, row):
    key = [seed, row['gen_sha256'], row['event_id']]
    return hashlib.sha256(json.dumps(key, separators=(',', ':')).encode()).hexdigest()


def prepare_benchmark(rows, seed, maximum_events=400, controls=None):
    """Make a deterministic fixed-size random draw within each bin/region.

    For a nonempty control cell, q=n/N. Every credible or explicitly protected
    unknown-model original is retained at q=1. No pT/energy threshold is added.
    The original selected population and already processed events must have
    been checked by the caller's immutable pristine-source census.
    """
    if not isinstance(seed, str) or not seed or maximum_events < 1:
        raise ValueError('Require an explicit draw seed and positive event budget')
    counts = dict(CONTROL_COUNTS)
    if controls is not None:
        if set(controls) != set(counts):
            raise ValueError('Specify all diagnostic control-region budgets')
        counts.update(controls)
    if any(type(value) is not int or value <= 0 for value in counts.values()):
        raise ValueError('Every control stratum needs positive sampling support')
    groups = defaultdict(list)
    seen = set()
    for row in rows:
        if not row['stratum'].startswith('qcd_'):
            raise ValueError('This bounded planner is for ordinary QCD diagnostics')
        key = identity(row)
        if key in seen:
            raise ValueError('Duplicate original GEN identity in benchmark population')
        seen.add(key)
        probability = row['existing_sampling_probability']
        if (not math.isfinite(row['native_weight']) or not math.isfinite(probability)
                or not 0 < probability <= 1):
            raise ValueError('Invalid original native W or frozen admission p')
        groups[(row['stratum'], benchmark_region(row))].append(row)
    selected, cells = [], []
    for (stratum, region), population in sorted(groups.items()):
        number = min(counts[region], len(population)) if region in counts else len(population)
        probability = number/len(population)
        chosen = sorted(population, key=lambda row: draw_hash(seed, row))[:number]
        cells.append(dict(stratum=stratum, benchmark_region=region,
            population_events=len(population), selected_events=number,
            benchmark_inclusion_probability=probability))
        for row in chosen:
            old = row['existing_sampling_probability']
            selected.append(dict(row, benchmark_region=region,
                benchmark_inclusion_probability=probability,
                cumulative_study_probability=old*probability,
                diagnostic_analysis_weight=row['native_weight']/(old*probability),
                original_worker_analysis_weight=row['native_weight']/old,
                draw_hash=draw_hash(seed, row)))
    if len(selected) > maximum_events:
        raise ValueError('Credible/protected cases and controls exceed the declared event budget')
    return dict(schema=SCHEMA, proposal_only=True, selection_seed=seed,
        population_events=len(rows), selected_events=len(selected), maximum_events=maximum_events,
        controls_per_bin=counts, strata=cells, selected=selected,
        old_native_weights_and_sampling_probabilities_unchanged=True,
        benchmark_q_is_separate_from_original_frozen_p=True,
        worker_Nano_convention='Original W/p unchanged; diagnostic response uses W/(p*q) once.',
        no_muon_energy_or_pt_cut=True, positive_q_for_every_nonempty_population_stratum=True,
        full_original_GEN_exposure_must_be_preserved=True,
        ordinary_campaign_publication_qualified=False, bulk_production_policy_qualified=False,
        detector_jobs_submitted=False, actual_primary_converter_preflight_complete=False)


def sha256(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1048576), b''):
            result.update(block)
    return result.hexdigest()


def validate_population(rows, manifest, load=read_record):
    """Bind IDs, indices, W, p and exposure to the original pinned receipt.

    A matching source hash and selected offset alone do not establish that an
    editable census row still contains the original event, weight or draw.
    Read each source receipt once, then check every row against its complete
    frozen selected_source descriptor. The injected loader is for small tests;
    the normal CLI always checks the actual receipt bytes through read_record.
    """
    required = ('every_known_previously_attempted_QCD_request_checked',
                'old_masks_weights_and_campaign_ledger_unchanged',
                'cohort_selection_uses_only_preexisting_attempt_status_not_reconstruction_passes')
    if not all(manifest.get(key) is True for key in required):
        raise ValueError('Require explicit pristine-source and original-admission evidence')
    sources = {source['source_id']: source for source in manifest['sources']}
    if len(sources) != len(manifest['sources']) or len(rows) != manifest['selected_events']:
        raise ValueError('Population does not match the frozen pristine-source census')
    descriptors = {}
    for source_id, source in sources.items():
        path = source.get('original_result_path')
        checksum = source.get('original_result_sha256')
        if (not isinstance(path, str) or not path or not isinstance(checksum, str)
                or not re.fullmatch('[0-9a-f]{64}', checksum)):
            raise ValueError('Require the original source receipt and its pinned SHA-256')
        result = load(path, checksum)
        if result.get('complete') is not True or result.get('exit_code') != 0:
            raise ValueError('Original source receipt is not a successful completed source')
        exposure = source['full_original_exposure']
        if (result.get('source') != exposure or exposure['source_id'] != source_id
                or exposure['stratum'] != source['stratum']
                or exposure['gen_sha256'] != source['original_raw_gen_sha256']):
            raise ValueError('Original full-GEN exposure differs from the pinned source receipt')
        descriptor = result.get('selected_source')
        if (not isinstance(descriptor, dict)
                or descriptor.get('gen_sha256') != source['prepared_gen_sha256']
                or descriptor.get('stratum') != source['stratum']
                or descriptor.get('normalization_record') != exposure):
            raise ValueError('Frozen selected-source authority or normalization differs')
        indices = descriptor['selected_indices']
        probabilities = descriptor['sampling_probabilities']
        identities = descriptor['event_ids']
        weights = descriptor['weights']
        if (len(indices) != source['selected_event_count']
                or len(indices) != len(probabilities) or len(set(indices)) != len(indices)
                or len(identities) != len(weights) or len(identities) != exposure['saved_events']
                or any(type(index) is not int or not 0 <= index < len(identities) for index in indices)
                or any(not math.isfinite(p) or not 0 < p <= 1 for p in probabilities)):
            raise ValueError('Original selected-source descriptor has inconsistent census vectors')
        descriptors[source_id] = descriptor
    positions = defaultdict(set)
    for row in rows:
        source = sources.get(row['source_id'])
        if (source is None or row['stratum'] != source['stratum']
                or row['gen_sha256'] != source['original_raw_gen_sha256']
                or row['prepared_gen_sha256'] != source['prepared_gen_sha256']):
            raise ValueError('Population row is outside the pristine source authority')
        position = row['selected_position']
        if (type(position) is not int or not 0 <= position < source['selected_event_count']
                or position in positions[row['source_id']]):
            raise ValueError('Missing or duplicate original selected position')
        descriptor = descriptors[row['source_id']]
        index = descriptor['selected_indices'][position]
        if type(row['original_index']) is not int or row['original_index'] != index:
            raise ValueError('Original GEN index differs from the frozen selected position')
        if row['event_id'] != descriptor['event_ids'][index]:
            raise ValueError('Original event identity differs from the pinned source receipt')
        if row['native_weight'] != descriptor['weights'][index]:
            raise ValueError('Native GEN weight differs from the pinned source receipt')
        if row['existing_sampling_probability'] != descriptor['sampling_probabilities'][position]:
            raise ValueError('Original sampling probability differs from the frozen draw')
        positions[row['source_id']].add(position)
    for source_id, source in sources.items():
        if len(positions[source_id]) != source['selected_event_count']:
            raise ValueError('Census omitted originals from a declared pristine source')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('population_jsonl', type=Path)
    parser.add_argument('pristine_manifest', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--seed', required=True)
    parser.add_argument('--maximum-events', type=int, default=400)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Refuse to overwrite or silently redraw a saved benchmark')
    manifest = json.loads(args.pristine_manifest.read_text())
    opener = gzip.open if args.population_jsonl.suffix == '.gz' else open
    with opener(args.population_jsonl, 'rt') as source:
        rows = [json.loads(line) for line in source if line.strip()]
    validate_population(rows, manifest)
    result = prepare_benchmark(rows, args.seed, args.maximum_events)
    result.update(population_file=str(args.population_jsonl.resolve()),
        population_sha256=sha256(args.population_jsonl),
        pristine_manifest=str(args.pristine_manifest.resolve()),
        pristine_manifest_sha256=sha256(args.pristine_manifest))
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False)+'\n')


if __name__ == '__main__':
    main()
