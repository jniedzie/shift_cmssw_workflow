#!/usr/bin/env python3
# Code style: AI-code. AI-generated read-only SM reconstruction-response census.
"""Reuse frozen source/Nano receipts without restarting production controllers.

Original source cohorts stay explicit, including missing detector responses.
The default fast inventory chooses one verified grouped Nano per process/bin.
Optional enrichment binds prepared GEN and includes remaining closed cohorts'
small Nano files. It never edits campaign state or treats a missing job as zero.
"""
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
import errno
import hashlib
import json
import math
from pathlib import Path
import re
import time

from run_shift_sm_postprocessing import selected_rows
from run_shift_smart_sm_controller import native_exit


SCHEMA = 'shift-sm-response-inventory-v1'
SPLIT_SEED = 'sm-response-heldout-20261026-v1'


def read_record(path, expected_sha256=None):
    """Read large AFS JSON in bounded chunks; verify existing frozen hashes."""
    path = Path(path)
    for attempt in range(3):
        try:
            with path.open('rb') as stream:
                data = b''.join(iter(lambda: stream.read(1048576), b''))
            break
        except OSError as error:
            if error.errno not in (errno.EIO, errno.ESTALE, errno.ETIMEDOUT) or attempt == 2:
                raise
            time.sleep(1)
    actual = hashlib.sha256(data).hexdigest()
    if expected_sha256 is not None and actual != expected_sha256:
        raise ValueError('Frozen receipt changed: ' + str(path))
    return json.loads(data)


def source_record(cached):
    norm = cached['normalization']
    source_id = norm['source_id']
    rows = selected_rows(cached['selected'])
    identities = [tuple(row['event_id']) for row in rows]
    if len(identities) != len(set(identities)):
        raise ValueError('Repeated selected original in source: ' + source_id)
    split_key = SPLIT_SEED + ':' + source_id + ':' + norm['gen_sha256']
    fold = int(hashlib.sha256(split_key.encode()).hexdigest()[:16], 16) % 5
    events = []
    for row in rows:
        weight, probability = row['native_weight'], row['sampling_probability']
        if not math.isfinite(weight) or not 0 < probability <= 1:
            raise ValueError('Invalid frozen W/p in source: ' + source_id)
        events.append(dict(event_id=row['event_id'], original_index=None,
            native_weight=weight, sampling_probability=probability))
    return dict(source_id=source_id, stratum=norm['stratum'],
        original_raw_gen_sha256=norm['gen_sha256'], prepared_gen_sha256=None,
        feature_path=None, full_original_exposure=norm,
        selected_events=events, selected_event_count=len(events),
        descriptor_hashes=cached['descriptor_hashes'],
        original_result_path=cached['original_result_path'],
        original_result_sha256=cached['original_result_sha256'],
        source_fold=fold, study_split='validation' if fold == 0 else 'development')


def expected_events(sources):
    """The raw-original hash is the stable identity; prepared hash is separate."""
    result = []
    seen = set()
    for source in sources:
        for row in source['selected_events']:
            key = (source['original_raw_gen_sha256'], tuple(row['event_id']))
            if key in seen:
                raise ValueError('Duplicate original across response cohorts')
            seen.add(key)
            result.append(dict(row, gen_sha256=key[0], source_id=source['source_id']))
    return result


def choose_grouped(candidates):
    """Choose one largest snapshot per bin; never add overlapping snapshots."""
    grouped = defaultdict(list)
    for row in candidates:
        grouped[row['stratum']].append(row)
    return [max(rows, key=lambda r: (len(r['source_ids']), r['events'], r['finished_epoch']))
            for _, rows in sorted(grouped.items())]


def enrich_source(source, load=read_record):
    result = load(source['original_result_path'], source['original_result_sha256'])
    if result.get('complete') is not True or result.get('exit_code') != 0:
        raise ValueError('Source receipt is not successful')
    if result['source'] != source['full_original_exposure']:
        raise ValueError('Original source exposure changed')
    descriptor = result.get('selected_source')
    if descriptor is None:
        if result.get('empty_source') is not True or source['selected_event_count'] != 0:
            raise ValueError('Nonempty source lacks frozen descriptor')
        return source
    selected = selected_rows(descriptor)
    old = [(r['event_id'], r['native_weight'], r['sampling_probability']) for r in source['selected_events']]
    new = [(r['event_id'], r['native_weight'], r['sampling_probability']) for r in selected]
    if old != new:
        raise ValueError('Selected source IDs or weights changed')
    updated = dict(source)
    updated['selected_events'] = [dict(row, original_index=index)
        for row, index in zip(source['selected_events'], descriptor['selected_indices'])]
    updated['prepared_gen_sha256'] = descriptor['gen_sha256']
    updated['prepared_gen_path'] = descriptor['gen']
    family = source['stratum'].split('_', 1)[0]
    updated['feature_path'] = descriptor['gen'].rsplit('/', 1)[0] + '/' + family + '_features.json.gz'
    updated['feature_preparation_receipt_path'] = descriptor['gen'].rsplit('/', 1)[0] + '/complete.json'
    updated['feature_preparation_receipt_sha256'] = result.get('source_preparation_receipt_sha256')
    # The gzip's pinned SHA is in that receipt's material_feature_census.
    # Do not present a newly observed feature-file checksum as prior authority.
    updated['feature_sha256'] = None
    updated['sampling_plan_sha256'] = descriptor.get('sampling', {}).get('plan_sha256')
    return updated


def completed_grouped_tasks(publication, sources, load=read_record):
    candidates = []
    for name, task in publication['tasks'].items():
        directory = Path(task['directory'])
        is_done = task['phase'] == 'done'
        if not is_done:
            if task['phase'] != 'submitted' or not (directory/'result.json').exists():
                continue
            if native_exit(directory/'events.log', task['cluster']) != 0:
                continue
        result = load(directory/'result.json', task.get('result_sha256'))
        request = load(directory/'request.json', task['request_sha256'])
        if (result.get('complete') is not True
                or result['request_sha256'] != task['request_sha256']
                or result['source_ids'] != task['source_ids']):
            raise ValueError('Grouped response receipt mismatch: ' + name)
        cohort_sources = [sources[s] for s in task['source_ids']]
        expected = expected_events(cohort_sources)
        if result['events'] != len(expected) or request['stratum'] != task['stratum']:
            raise ValueError('Grouped response event coverage mismatch')
        if len(cohort_sources) != len(request['selected_sources']):
            raise ValueError('Grouped snapshot source count changed')
        for source, selected in zip(cohort_sources, request['selected_sources']):
            a = [(r['event_id'], r['native_weight'], r['sampling_probability'])
                 for r in source['selected_events']]
            b = [(r['event_id'], r['native_weight'], r['sampling_probability'])
                 for r in selected_rows(selected)]
            if a != b:
                raise ValueError('Grouped snapshot changed source identities/W/p')
        published = result['published']['merged_nano']
        candidates.append(dict(name=name, stratum=task['stratum'], source_ids=task['source_ids'],
            path=published['path'], sha256=published['sha256'], bytes=published['bytes'],
            events=result['events'], expected_events=expected,
            finished_epoch=task.get('finished_epoch', result.get('checked_epoch', 0)),
            request_path=str(directory/'request.json'), request_sha256=task['request_sha256'],
            result_path=str(directory/'result.json'), result_sha256=task.get('result_sha256'),
            native_qualification='verified_saved_success' if is_done else 'new_native_exit_zero'))
    return choose_grouped(candidates)


def detector_completions(parent, publication, load=read_record):
    """Adopt only validated terminal results into this separate read-only view."""
    nanos = {key: dict(value) for key, value in publication['nanos'].items()}
    for task in parent['tasks'].values():
        if task['kind'] != 'detector' or task['phase'] not in ('done', 'submitted'):
            continue
        key = task.get('result_sha256')
        if key in nanos:
            nanos[key]['original_result_path'] = str(Path(task['directory'])/'result.json')
            continue
        directory = Path(task['directory'])
        if not (directory/'status.json').exists():
            continue
        status = load(directory/'status.json')
        if status.get('complete') is not True or status.get('exit_code') != 0:
            continue
        if task['phase'] != 'done' and native_exit(directory/'events.log', task['cluster']) != 0:
            continue
        result = load(directory/'result.json', key)
        if (result.get('complete') is not True or result.get('exit_code') != 0
                or result['request_sha256'] != task['request_sha256']
                or status['source_descriptor_sha256'] != result['source_descriptor_sha256']
                or status['nano_sha256'] != status['nano_readback_sha256']
                or status['events'] != result['events']):
            raise ValueError('Terminal detector receipt mismatch: ' + str(directory))
        tiers = status['validated_tier_events']
        if set(tiers) != {'GEN', 'SIM', 'DIGIHLT', 'RECO', 'NANO'} or any(
                count != status['events'] for count in tiers.values()):
            raise ValueError('Terminal detector tier census mismatch')
        actual_key = hashlib.sha256((directory/'result.json').read_bytes()).hexdigest()
        nanos[actual_key] = dict(path=status['nano_path'], sha256=status['nano_sha256'],
            events=status['events'], stratum=task['stratum'],
            source_descriptor_sha256=status['source_descriptor_sha256'],
            original_result_sha256=actual_key, original_result_path=str(directory/'result.json'),
            native_qualification='verified_saved_success' if task['phase'] == 'done' else 'new_native_exit_zero')
    return list(nanos.values())


def classify_response_coverage(sources, nanos):
    by_hash = {}
    for source in sources.values():
        for key in source['descriptor_hashes']:
            if key in by_hash and by_hash[key] != source['source_id']:
                raise ValueError('Source descriptor hash collision')
            by_hash[key] = source['source_id']
    by_source = defaultdict(list)
    seen = set()
    for nano in nanos:
        if nano['path'] in seen:
            raise ValueError('Duplicate completed Nano path')
        seen.add(nano['path'])
        source_id = by_hash.get(nano['source_descriptor_sha256'])
        if source_id is None:
            raise ValueError('Completed Nano outside frozen source census')
        by_source[source_id].append(dict(nano, source_id=source_id))
    for source_id, source in sources.items():
        count = sum(nano['events'] for nano in by_source[source_id])
        if count > source['selected_event_count']:
            raise ValueError('Completed Nano over-covers original source')
        source['completed_response_events'] = count
        source['missing_selected_events'] = source['selected_event_count'] - count
        source['complete_response'] = count == source['selected_event_count']
    return by_source


def raw_response_file(nano, source, load=read_record):
    result = load(nano['original_result_path'], nano['original_result_sha256'])
    if result['source_gen_sha256'] != source['prepared_gen_sha256']:
        raise ValueError('Detector used another prepared GEN source')
    identities = result['stages']['4']['audit']['event_ids']
    if len(identities) != nano['events'] or len(set(map(tuple, identities))) != len(identities):
        raise ValueError('Detector event identity census mismatch')
    expected = {tuple(row['event_id']): row for row in expected_events([source])}
    rows = [expected[tuple(identity)] for identity in identities]
    return dict(nano, gen_sha256=source['original_raw_gen_sha256'],
        prepared_gen_sha256=source['prepared_gen_sha256'], expected_events=rows)


def validate_file_identities(sources, files):
    """Reject duplicated originals or convenient partial raw-file cohorts."""
    by_source = defaultdict(set)
    seen = set()
    for file in files:
        for row in file['expected_events']:
            key = (row['gen_sha256'], tuple(row['event_id']))
            if key in seen:
                raise ValueError('Repeated original between response Nano files')
            seen.add(key)
            by_source[row['source_id']].add(tuple(row['event_id']))
    for source_id, identities in by_source.items():
        expected = {tuple(row['event_id']) for row in sources[source_id]['selected_events']}
        if identities != expected:
            raise ValueError('Response files do not cover complete frozen source')


def detector_cost_record(result, nano, source):
    """Bind measured stage wall time to originals; never call this CPU time.

    Multi-event workers do not time each event separately. Their equal-share
    costs are explicitly allocated proxies, whereas one-event workers provide
    an actual event-specific stage measurement. Summing all shares recovers
    the measured worker total; pairs must not receive separate copies.
    """
    if result.get('complete') is not True or result.get('exit_code') != 0:
        raise ValueError('Cost requires a successful detector result')
    if (result['source_gen_sha256'] != source['prepared_gen_sha256']
            or result['normalization_record']['source_id'] != source['source_id']
            or result['normalization_record']['gen_sha256'] != source['original_raw_gen_sha256']
            or result['nano_sha256'] != nano['sha256']):
        raise ValueError('Cost receipt source/Nano binding changed')
    identities = result['stages']['4']['audit']['event_ids']
    count = len(identities)
    if not count or count != nano['events'] or count != result['events']:
        raise ValueError('Cost receipt event count changed')
    if len(set(map(tuple, identities))) != count:
        raise ValueError('Repeated original in detector cost receipt')
    stages = {}
    for number in ('1', '2', '3', '4'):
        stage = result['stages'][number]
        value = stage['seconds']
        if not math.isfinite(value) or value < 0 or stage['audit']['event_ids'] != identities:
            raise ValueError('Invalid stage wall time or identity census')
        stages[number] = value
    wall = result['wall_seconds']
    if not math.isfinite(wall) or wall < 0:
        raise ValueError('Invalid payload wall time')
    expected = {tuple(row['event_id']): row for row in expected_events([source])}
    rows = []
    kind = 'actual_single_event_stage_wall' if count == 1 else 'allocated_multievent_stage_wall_proxy'
    for identity in identities:
        row = expected[tuple(identity)]
        rows.append(dict(row, SIM_stage_wall_seconds=stages['1']/count,
            payload_wall_seconds=wall/count, cost_kind=kind))
    return dict(nano_path=nano['path'], nano_sha256=nano['sha256'],
        source_id=source['source_id'], stratum=source['stratum'],
        events_in_worker=count, event_cost_kind=kind, measured_unit='wall_seconds',
        SIM_stage_wall_seconds=stages['1'], payload_wall_seconds=wall,
        stage_wall_seconds=stages, event_costs=rows)


def summarize(sources, grouped_files, raw_files=()):
    covered = {s for group in grouped_files for s in group['source_ids']}
    covered.update(row['source_id'] for row in raw_files)
    summary = defaultdict(Counter)
    for source in sources.values():
        row = summary[source['stratum']]
        row['original_sources'] += 1
        row['saved_GEN_events'] += source['full_original_exposure']['saved_events']
        row['selected_events'] += source['selected_event_count']
        if 'completed_response_events' in source:
            row['completed_response_events'] += source['completed_response_events']
            row['missing_selected_events'] += source['missing_selected_events']
            row['complete_response_sources'] += int(source['complete_response'])
        if source['source_id'] in covered:
            row['response_sources'] += 1
            row['response_events'] += source['selected_event_count']
        else:
            row['excluded_or_missing_sources'] += 1
            row['excluded_or_missing_selected_events'] += source['selected_event_count']
    return {key: dict(value) for key, value in sorted(summary.items())}


def build_fast_inventory(publication, *, load=read_record):
    sources = {}
    for cache in publication['sources'].values():
        source = source_record(cache)
        if source['source_id'] in sources:
            raise ValueError('Repeated source ID')
        sources[source['source_id']] = source
    # Check identity collisions even in unobserved cohorts; repeated source
    # hashes/IDs must not be credited as independent generated exposure.
    expected_events(list(sources.values()))
    grouped = completed_grouped_tasks(publication, sources, load)
    return dict(schema=SCHEMA, created_epoch=time.time(),
        split_seed=SPLIT_SEED, validation_fold=0, source_fold_count=5,
        sources=list(sources.values()), grouped_files=grouped, raw_files=[],
        summary=summarize(sources, grouped),
        missing_response_is_physics_rejection=False,
        complete_cohort_conditioning_may_be_informative=True,
        physics_normalization_ready=False,
        original_campaign_files_modified=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('campaign_directory', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--enrich-sources', action='store_true')
    parser.add_argument('--include-raw', action='store_true',
                        help='Include ungrouped Nano only for complete original source cohorts')
    parser.add_argument('--workers', type=int, default=8)
    parser.add_argument('--publication-state', type=Path)
    parser.add_argument('--detector-ledger', type=Path)
    args = parser.parse_args()
    if not 1 <= args.workers <= 16:
        raise ValueError('Use between one and sixteen bounded AFS readers')
    publication_path = args.publication_state
    if publication_path is None:
        pointer = read_record(args.campaign_directory/'active_postprocessing_supervisor.json')
        publication_path = Path(pointer['state_directory'])/'state.json'
    publication = read_record(publication_path)
    inventory = build_fast_inventory(publication)
    if args.include_raw and not args.enrich_sources:
        raise ValueError('--include-raw requires --enrich-sources for prepared-GEN binding')
    if args.enrich_sources:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            inventory['sources'] = list(pool.map(enrich_source, inventory['sources']))
        by_id = {s['source_id']: s for s in inventory['sources']}
        for row in inventory['grouped_files']:
            row['expected_events'] = expected_events([by_id[s] for s in row['source_ids']])
    if args.include_raw:
        parent_path = args.detector_ledger
        if parent_path is None:
            pointer = read_record(args.campaign_directory/'active_supervisor.json')
            parent_path = Path(pointer['state_directory'])/'ledger.json'
        parent = read_record(parent_path)
        sources = {s['source_id']: s for s in inventory['sources']}
        nanos = detector_completions(parent, publication)
        by_source = classify_response_coverage(sources, nanos)
        grouped_ids = {s for row in inventory['grouped_files'] for s in row['source_ids']}
        candidates = [(nano, source) for source_id, source in sources.items()
                      if source_id not in grouped_ids and source['complete_response']
                      for nano in by_source[source_id]]
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            inventory['raw_files'] = list(pool.map(lambda item: raw_response_file(*item), candidates))
        validate_file_identities(sources, inventory['grouped_files'] + inventory['raw_files'])
        inventory['summary'] = summarize(sources, inventory['grouped_files'], inventory['raw_files'])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix+'.tmp')
    data = (json.dumps(inventory, separators=(',', ':'), allow_nan=False)+'\n').encode()
    with temporary.open('wb') as stream:
        for start in range(0, len(data), 1048576):
            stream.write(data[start:start+1048576])
    temporary.replace(args.output)
    print(json.dumps(dict(output=str(args.output), grouped_files=len(inventory['grouped_files']),
        grouped_events=sum(r['events'] for r in inventory['grouped_files']), summary=inventory['summary'])))


if __name__ == '__main__':
    main()
