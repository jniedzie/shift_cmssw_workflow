#!/usr/bin/env python3
# Code style: AI-code.
"""AI-generated header-only luminosity audit for partial recorded-data cohorts.

Certified lumisections touched by a processed event are coverage, not proof of
the processed sample's effective luminosity. This tool never qualifies MC
normalization from the present deterministic, bounded recorded-stream union.
It reads frozen JSON metadata and optional BRIL CSV, never ROOT candidates.
"""
import argparse
import collections
import csv
import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import struct


MATCHED_FIELDS = ('path', 'sha256', 'root_uuid', 'bytes', 'expected_event_count',
                  'origin_uri', 'producer_receipt')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def pin(path, digest=None):
    path = Path(path)
    return dict(path=str(path.resolve()), bytes=path.stat().st_size,
                sha256=digest or sha256(path))


def load(path):
    with Path(path).open() as source:
        return json.load(source)


def positive_integer(value, name, maximum=None):
    require(type(value) is int and value > 0
            and (maximum is None or value < maximum), 'Invalid ' + name)
    return value


def identity(row):
    return tuple(positive_integer(row[name], name, 2**bits)
                 for name, bits in (('run', 32), ('luminosityBlock', 32), ('event', 64)))


def identity_digest(identities):
    digest = hashlib.sha256()
    for row in sorted(identities):
        digest.update(struct.pack('>IIQ', *row))
    return digest.hexdigest()


def lumimask(lumisections):
    grouped = collections.defaultdict(list)
    for run, lumi in sorted(lumisections):
        ranges = grouped[str(run)]
        if ranges and lumi == ranges[-1][1] + 1:
            ranges[-1][1] = lumi
        else:
            ranges.append([lumi, lumi])
    return dict(grouped)


def certification_ranges(certification):
    require(isinstance(certification, dict) and certification, 'Empty certification mask')
    result = {}
    for run, ranges in certification.items():
        require(isinstance(run, str) and run.isdecimal() and int(run) > 0,
                'Invalid certification run')
        previous = 0
        for pair in ranges:
            require(isinstance(pair, list) and len(pair) == 2, 'Invalid certification range')
            low = positive_integer(pair[0], 'certification start')
            high = positive_integer(pair[1], 'certification end')
            require(previous < low <= high, 'Overlapping or invalid certification ranges')
            previous = high
        result[int(run)] = ranges
    return result


def bind_inventory(inventory, staged, frame_size):
    require(inventory.get('schema') == 'shift-data-staged-publication-inputs-v2',
            'Require a version-two published inventory')
    require(staged.get('schema') == 'shift-data-verified-staged-outputs-v1'
            and staged.get('candidate_branches_read') is False,
            'Require the exact header-only staged-output receipt')
    published = {}
    for row in inventory['inputs']:
        require(row['path'] not in published, 'Duplicate published path ownership')
        published[row['path']] = row
    ready = {}
    for row in staged['ready']:
        require(row['path'] not in ready, 'Duplicate staged path ownership')
        ready[row['path']] = row
    require(published and set(published) == set(ready),
            'Published paths differ from this increment staged paths')
    intervals = []
    for path, row in published.items():
        source = ready[path]
        require(all(row[key] == source[key] for key in MATCHED_FIELDS),
                'Published and staged ROOT metadata differ')
        require(source.get('native_exit_verified') is True,
                'Published input lacks verified native completion')
        count = positive_integer(source['expected_event_count'], 'published input count')
        low = source['source_offset']
        require(type(low) is int and low >= 0 and low + count <= frame_size,
                'Published source range lies outside the assigned frame')
        require(source['era'] == inventory['era'], 'Published era differs from its producer')
        intervals.append((low, low + count, source))
    intervals.sort(key=lambda item: item[0])
    require(all(left[1] <= right[0] for left, right in zip(intervals, intervals[1:])),
            'Duplicate published original ownership')
    total = sum(high - low for low, high, _ in intervals)
    require(total == inventory['expected_unique_events'] == staged['completed_originals'],
            'Published event denominator differs from exact staged ownership')
    return intervals


def audit_headers(inventory, staged, census, certification):
    require(census.get('schema') == 'shift-data-input-header-census-v2'
            and census.get('header_only') is True
            and census.get('candidate_branches_read') is False
            and census.get('all_headers_is_real_data') is True,
            'Require the frozen real-data header census')
    events = census['events']
    require(len(events) == census['unique_originals'] and events,
            'Assigned frame size differs from its original headers')
    require(census['era'] == inventory['era'], 'Assigned and published eras differ')
    certified = certification_ranges(certification)
    intervals = bind_inventory(inventory, staged, len(events))
    assigned_by_ls = collections.Counter()
    published_by_ls = collections.Counter()
    assigned_by_dataset = collections.Counter()
    published_by_dataset = collections.Counter()
    by_dataset_ls = collections.defaultdict(collections.Counter)
    seen = set()
    selected_ids = []
    interval_index = 0
    for offset, event in enumerate(events):
        require(event.get('globalcohort_entry') == offset,
                'Original header order differs from the assigned frame')
        original = identity(event)
        require(original not in seen, 'Duplicate assigned original identity')
        seen.add(original)
        run, lumi, _ = original
        require(any(low <= lumi <= high for low, high in certified.get(run, [])),
                'Original header is outside certified lumisections')
        source_index = event['source_index']
        require(type(source_index) is int and 0 <= source_index < len(census['sources']),
                'Unknown original source index')
        source = census['sources'][source_index]
        dataset = event['source_dataset']
        require(source['dataset'] == dataset and source['source_url'] == event['source_url'],
                'Original header source binding changed')
        assigned_by_ls[run, lumi] += 1
        assigned_by_dataset[dataset] += 1
        while interval_index < len(intervals) and offset >= intervals[interval_index][1]:
            interval_index += 1
        if interval_index == len(intervals) or offset < intervals[interval_index][0]:
            continue
        owner = intervals[interval_index][2]
        require(owner['dataset'] == dataset and owner['origin_uri'] == event['source_url'],
                'Published source range changes dataset or original RAW file')
        selected_ids.append(original)
        published_by_ls[run, lumi] += 1
        published_by_dataset[dataset] += 1
        by_dataset_ls[dataset][run, lumi] += 1
    digest = identity_digest(selected_ids)
    require(digest == inventory['expected_identity_sha256'],
            'Published original identity digest differs from the frozen headers')
    require(sorted(published_by_dataset) == sorted(inventory['datasets'])
            and sorted({run for run, _ in published_by_ls}) == sorted(inventory['runs']),
            'Published dataset or run census differs from its inventory')
    source_partial = [source for source in census['sources']
                      if 'headers_read' in source and 'events_tree_entries' in source
                      and source['headers_read'] < source['events_tree_entries']]
    result = dict(
        assigned_frame=dict(original_events=len(events),
                            original_events_by_dataset=dict(sorted(assigned_by_dataset.items())),
                            lumisections=len(assigned_by_ls), source_files=len(census['sources']),
                            partially_scanned_source_files=len(source_partial)),
        published=dict(original_events=len(selected_ids), input_files=len(intervals),
                       original_events_by_dataset=dict(sorted(published_by_dataset.items())),
                       lumisections=len(published_by_ls), identity_sha256=digest,
                       complete_for_assigned_frame=len(selected_ids) == len(events),
                       pending_assigned_originals=len(events) - len(selected_ids),
                       absolute_lumisection_completeness='unknown'),
        published_lumisections=[dict(run=run, luminosityBlock=lumi,
                                    published_originals=count,
                                    assigned_originals=assigned_by_ls[run, lumi],
                                    complete_for_assigned_frame=count == assigned_by_ls[run, lumi],
                                    original_events_by_dataset={dataset: counts[run, lumi]
                                        for dataset, counts in sorted(by_dataset_ls.items())
                                        if counts[run, lumi]})
                                 for (run, lumi), count in sorted(published_by_ls.items())],
        input_selection=dict(ordering=census.get('ordering'),
                             processing_inclusion_probabilities='not_qualified',
                             complete_lumisection_recording_contract='not_qualified'))
    return result, lumimask(published_by_ls), set(published_by_ls)


def bril_coverage(path, expected_lumisections):
    header = None
    rows = {}
    with Path(path).open(newline='') as source:
        for raw in source:
            stripped = raw.strip()
            if not stripped:
                continue
            if stripped.lower().startswith('#summary'):
                break
            if stripped.startswith('#run:fill') or stripped.startswith('run:fill'):
                require(header is None, 'Repeated BRIL data header')
                header = next(csv.reader([stripped.lstrip('#')]))
                require({'run:fill', 'ls', 'delivered(/pb)', 'recorded(/pb)'} <= set(header),
                        'BRIL CSV must contain per-lumisection luminosity in /pb')
                continue
            if stripped.startswith('#'):
                continue
            require(header is not None, 'BRIL data appears before its by-LS header')
            fields = next(csv.reader([raw]))
            require(len(fields) == len(header), 'Malformed BRIL by-LS row')
            row = dict(zip(header, fields))
            require(re.fullmatch(r'[0-9]+:[0-9]+', row['run:fill'])
                    and re.fullmatch(r'[0-9]+:[0-9]+', row['ls']),
                    'Malformed BRIL run or lumisection')
            # BRIL reports luminometer LS:CMSLS; Golden JSON and event headers
            # use the CMSLS, which need not equal the luminometer counter.
            run = positive_integer(int(row['run:fill'].split(':')[0]), 'BRIL run')
            lumi = positive_integer(int(row['ls'].split(':')[1]), 'BRIL CMS lumisection')
            key = run, lumi
            require(key not in rows, 'Duplicate BRIL lumisection')
            delivered, recorded = (float(row[name]) for name in ('delivered(/pb)', 'recorded(/pb)'))
            require(all(math.isfinite(value) and value >= 0 for value in (delivered, recorded)),
                    'Nonfinite or negative BRIL luminosity')
            rows[key] = delivered, recorded
    require(header is not None, 'BRIL CSV lacks its by-LS header')
    require(set(rows) == expected_lumisections,
            'BRIL rows differ from the exact published lumisection mask')
    return dict(status='coverage_only', unit='pb^-1', lumisections=len(rows),
                delivered_pb_inverse=math.fsum(row[0] for row in rows.values()),
                recorded_pb_inverse=math.fsum(row[1] for row in rows.values()),
                use_for_mc_scaling=False)


def mc_normalization():
    return dict(status='unknown', value_pb_inverse=None, unit='pb^-1',
                qualified_for_mc_scaling=False,
                reasons=['The assigned data are a deterministic bounded file/entry prefix.',
                         'Absolute lumisection completeness is not established for the recorded-stream union.',
                         'Recording/trigger/prescale response and processing inclusion probabilities are not qualified.',
                         'CMS coverage luminosity alone does not establish any upstream SHIFT beam/target normalization.'])


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.new')
    with temporary.open('w') as output:
        json.dump(value, output, indent=2, allow_nan=False)
        output.write('\n')
    temporary.chmod(0o600)
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('inventory', 'staged', 'census', 'certification', 'output', 'lumimask-output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--census-sha256', required=True)
    parser.add_argument('--bril-csv', type=Path)
    parser.add_argument('--normtag', type=Path)
    args = parser.parse_args()
    if bool(args.bril_csv) != bool(args.normtag):
        parser.error('BRIL CSV and its normtag file must be supplied together')
    os.umask(0o077)
    inputs = {name: getattr(args, name) for name in ('inventory', 'staged', 'census', 'certification')}
    if args.bril_csv:
        inputs.update(bril_csv=args.bril_csv, normtag=args.normtag)
    outputs = (args.output.resolve(), args.lumimask_output.resolve())
    require(len(set(outputs)) == 2 and not set(outputs) & {path.resolve() for path in inputs.values()},
            'Audit outputs must differ from each other and all input files')
    actual_census_sha = sha256(args.census)
    require(actual_census_sha == args.census_sha256, 'Frozen census SHA-256 differs')
    census = load(args.census)
    certification_sha = sha256(args.certification)
    if 'certification_sha256' in census:
        require(certification_sha == census['certification_sha256'],
                'Frozen certification SHA-256 differs')
    audit, mask, selected_ls = audit_headers(load(args.inventory), load(args.staged),
                                            census, load(args.certification))
    coverage = dict(status='not_calculated', unit='pb^-1', lumisections=len(selected_ls),
                    delivered_pb_inverse=None, recorded_pb_inverse=None, use_for_mc_scaling=False)
    if args.bril_csv:
        coverage = bril_coverage(args.bril_csv, selected_ls)
    audit.update(schema='shift-data-luminosity-audit-v1',
                 created_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                 inputs={name: pin(path, actual_census_sha if name == 'census'
                                  else certification_sha if name == 'certification' else None)
                         for name, path in inputs.items()},
                 luminosity=dict(mc_normalization=mc_normalization(), covered_lumisections=coverage),
                 lumimask_output=str(args.lumimask_output.resolve()),
                 candidate_branches_read=False, ROOT_files_read=False,
                 coverage_warning='Touched-lumisection luminosity is coverage only; it is not the partial processed sample luminosity.')
    write_json(args.lumimask_output, mask)
    write_json(args.output, audit)
    print(json.dumps(dict(published_originals=audit['published']['original_events'],
                          covered_lumisections=len(selected_ls),
                          mc_normalization_luminosity_pb_inverse=None,
                          coverage_recorded_pb_inverse=coverage['recorded_pb_inverse'])))


if __name__ == '__main__':
    main()
