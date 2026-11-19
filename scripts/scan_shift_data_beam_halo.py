#!/usr/bin/env python3
# Code style: AI-code.
"""Census existing AOD CSC halo IDs in complete certified lumisections.

Only EventAuxiliary and BeamHaloSummary are read. Unknown products or conflicting
duplicate decisions prevent closure. A selected-ID list never earns luminosity
credit without complete AOD/RAW identity agreement and completed reconstruction.
"""
import argparse
import collections
import hashlib
import json
import os
from pathlib import Path
import re
import time

from prepare_shift_data_lumisections import canonical_lfn, selected_lumisections
from scan_shift_data_lumisections import identity, identity_digest, require, sha256


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def halo_policy(process='RECO'):
    require(isinstance(process, str) and re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', process),
            'Require an explicit BeamHaloSummary process label')
    return dict(name='Run3-CSC-loose-AOD-v1', product_type='reco::BeamHaloSummary',
                label=['BeamHaloSummary', '', process], predicate='CSCLooseHaloId() == true',
                required_CSC_report_entries=6, true_means='halo-like selected',
                missing_or_incomplete='unknown; block lumisection closure',
                duplicate_disagreement='unknown; block lumisection closure')


def validate_catalog(catalog, certification):
    require(catalog.get('schema') == 'shift-data-full-lumisection-catalog-v1',
            'Require an explicit complete-lumisection catalog')
    selected = selected_lumisections(catalog['lumimask'])
    require(selected and selected <= selected_lumisections(certification),
            'Select nonempty certified lumisections')
    tier = catalog.get('data_tier')
    require(tier in ('AOD', 'RAW-RECO'), 'Halo census requires explicit AOD or RAW-RECO tier')
    datasets = catalog['datasets']
    require(isinstance(datasets, list) and datasets and len(set(datasets)) == len(datasets)
            and all(isinstance(name, str) and re.fullmatch(
                r'/[A-Za-z0-9_]+/Run2023D-[A-Za-z0-9_.-]+/' + re.escape(tier), name)
                for name in datasets), 'Require distinct declared Run2023D AOD datasets')
    expected = {(run, lumi, name) for run, lumi in selected for name in datasets}
    observed, sources, memberships = set(), {}, collections.defaultdict(set)
    for record in catalog['records']:
        key = record['run'], record['luminosityBlock'], record['dataset']
        require(key in expected and key not in observed, 'Repeated or foreign catalog scope')
        require(record.get('query_complete') is True and isinstance(record.get('files'), list),
                'Unknown catalog query cannot establish completeness')
        observed.add(key)
        files_seen = set()
        for file in record['files']:
            lfn, url = file['lfn'], file['source_url']
            require(lfn == canonical_lfn(lfn) == canonical_lfn(url) and lfn not in files_seen,
                    'Repeated or changed catalog source')
            files_seen.add(lfn)
            source_key = record['dataset'], lfn
            value = dict(dataset=record['dataset'], lfn=lfn, source_url=url)
            require(source_key not in sources or sources[source_key] == value,
                    'Catalog source transport changed')
            sources[source_key] = value
            memberships[source_key].add(key[:2])
    require(observed == expected, 'Incomplete lumisection/dataset catalog matrix')
    return selected, [sources[key] for key in sorted(sources)], memberships


def read_halo(event, handle, policy):
    """Missing reports are not the default false returned by the C++ accessor."""
    try:
        event.getByLabel(tuple(policy['label']), handle)
        if not handle.isValid():
            return dict(decision='unknown', reason='missing_BeamHaloSummary')
        summary = handle.product()
        sizes = {name: len(getattr(summary, method)()) for name, method in (
            ('CSC', 'GetCSCHaloReport'), ('global', 'GetGlobalHaloReport'),
            ('ecal', 'GetEcalHaloReport'), ('hcal', 'GetHcalHaloReport'))}
        if sizes['CSC'] < policy['required_CSC_report_entries']:
            return dict(decision='unknown', reason='incomplete_CSC_report', report_sizes=sizes)
        loose = bool(summary.CSCLooseHaloId())
        return dict(decision='selected' if loose else 'rejected', report_sizes=sizes,
                    CSC_loose=loose, CSC_tight=bool(summary.CSCTightHaloId()))
    except Exception as error:
        return dict(decision='unknown', reason='BeamHaloSummary_read_error',
                    error_type=type(error).__name__)


def aod_reader(source, selected, policy):
    import ROOT
    from DataFormats.FWLite import Events, Handle

    file = ROOT.TFile.Open(source['source_url'], 'READ')
    require(file and not file.IsZombie() and not file.TestBit(ROOT.TFile.kRecovered),
            'Required AOD source is not ordinarily readable')
    try:
        tree = file.Get('Events')
        require(tree and tree.InheritsFrom('TTree'), 'Required AOD source has no Events tree')
        metadata = dict(events_tree_entries=int(tree.GetEntries()), root_uuid=file.GetUUID().AsString(),
                        beam_halo_branches=[branch.GetName() for branch in tree.GetListOfBranches()
                                            if 'BeamHaloSummary_' in branch.GetName()])
    finally:
        file.Close()

    def headers():
        handle = Handle(policy['product_type'])
        for event in Events(source['source_url']):
            aux = event.eventAuxiliary()
            row = dict(run=int(aux.id().run()), luminosityBlock=int(aux.luminosityBlock()),
                       event=int(aux.id().event()), is_real_data=bool(aux.isRealData()))
            if (row['run'], row['luminosityBlock']) in selected:
                row['halo'] = read_halo(event, handle, policy)
            yield row

    return metadata, headers()


def assemble(catalog, sources, observations, policy, catalog_sha256, certification_sha256):
    selected = selected_lumisections(catalog['lumimask'])
    sources.sort(key=lambda source: source['source_index'])
    indexed = {source['source_index']: source for source in sources}
    require(len(indexed) == len(sources), 'Repeated scanned source')
    entries, counts = collections.defaultdict(set), collections.defaultdict(collections.Counter)
    memberships = collections.defaultdict(set)
    for record in catalog['records']:
        for file in record['files']:
            memberships[record['dataset'], file['lfn']].add((record['run'], record['luminosityBlock']))
    grouped = collections.defaultdict(list)
    for row in observations:
        original = identity(row)
        index, entry = row['source_index'], row['source_entry']
        require(index in indexed, 'Event references unscanned source')
        source = indexed[index]
        require(type(entry) is int and 0 <= entry < source['events_tree_entries']
                and entry not in entries[index], 'Invalid or repeated source entry')
        require(original[:2] in selected and original[:2] in memberships[source['dataset'], source['lfn']],
                'Event lies outside selected catalog scope')
        entries[index].add(entry)
        decision = row['halo']['decision']
        require(decision in ('selected', 'rejected', 'unknown'), 'Invalid halo decision')
        counts[index][decision] += 1
        grouped[original].append(row)
    for index, source in indexed.items():
        require(source['scanned_to_eof'] is True and source['headers_read'] == source['events_tree_entries']
                and source['selected_lumi_headers'] == len(entries[index])
                and all(source[key] == counts[index][key] for key in ('selected', 'rejected', 'unknown')),
                'Source and event decision accounting differ')
    events = []
    for original in sorted(grouped):
        rows = sorted(grouped[original], key=lambda row: (row['source_index'], row['source_entry']))
        decisions = {row['halo']['decision'] for row in rows}
        decision = next(iter(decisions)) if len(decisions) == 1 else 'unknown'
        reason = 'conflicting_duplicate_flag' if len(decisions) > 1 else None
        events.append(dict(run=original[0], luminosityBlock=original[1], event=original[2],
                           decision=decision, duplicate_conflict=reason,
                           occurrences=[dict(source_index=row['source_index'], source_entry=row['source_entry'],
                                             halo=row['halo']) for row in rows]))
    counts = collections.Counter(row['decision'] for row in events)
    scanned = {source['source_index'] for source in sources}
    required_count = len({(row['dataset'], file['lfn']) for row in catalog['records'] for file in row['files']})
    full_eof = scanned == set(range(required_count))
    lumis = []
    for run, lumi in sorted(selected):
        rows = [row for row in events if (row['run'], row['luminosityBlock']) == (run, lumi)]
        totals = collections.Counter(row['decision'] for row in rows)
        lumis.append(dict(run=run, luminosityBlock=lumi, unique_originals=len(rows),
                          selected=totals['selected'], rejected=totals['rejected'], unknown=totals['unknown'],
                          flag_census_closed=full_eof and not totals['unknown']))
    chosen = [[row['run'], row['luminosityBlock'], row['event']]
              for row in events if row['decision'] == 'selected']
    return dict(schema='shift-data-beam-halo-census-v1', policy=policy, policy_sha256=digest(policy),
                full_lumisection_catalog_sha256=catalog_sha256,
                certification_sha256=certification_sha256, selected_lumis=catalog['lumimask'],
                datasets=sorted(catalog['datasets']), data_tier=catalog['data_tier'],
                required_source_count=required_count, sources=sources, events=events,
                selected_identities=chosen, selected_identity_sha256=identity_digest(chosen),
                original_identity_sha256=identity_digest(grouped),
                unique_originals=len(events), selected=counts['selected'], rejected=counts['rejected'],
                unknown=counts['unknown'], duplicate_observations=len(observations)-len(events),
                lumisections=lumis, all_required_files_scanned_to_eof=full_eof,
                flag_census_closed=full_eof and not counts['unknown'],
                event_limit_applied=False, prior_events_excluded=False,
                all_headers_is_real_data=True,
                candidate_branches_read=False, product_handles_requested=['reco::BeamHaloSummary'],
                luminosity_credit='none until exact RAW identity agreement and all selected responses verify')


def scan_catalog(catalog, certification, reader, *, catalog_sha256, certification_sha256,
                 process='RECO', source_indices=None, progress=None):
    selected, required, memberships = validate_catalog(catalog, certification)
    policy = halo_policy(process)
    indices = list(range(len(required))) if source_indices is None else sorted(source_indices)
    require(len(indices) == len(set(indices)) and all(type(i) is int and 0 <= i < len(required) for i in indices),
            'Invalid or repeated source index')
    observations, sources = [], []
    for index in indices:
        source = required[index]
        metadata, headers = reader(source, selected, policy)
        entries = metadata['events_tree_entries']
        require(type(entries) is int and entries >= 0 and isinstance(metadata.get('root_uuid'), str)
                and metadata['root_uuid'], 'AOD source lacks original entry count or UUID')
        record = dict(source, **metadata, source_index=index, headers_read=0, selected_lumi_headers=0,
                      selected=0, rejected=0, unknown=0, scanned_to_eof=False)
        for entry, header in enumerate(headers):
            record['headers_read'] += 1
            require(record['headers_read'] <= entries, 'AOD iteration exceeds its original entry count')
            require(header.get('is_real_data') is True, 'AOD source has a non-data header')
            original = identity(header)
            if original[:2] not in selected:
                continue
            require(original[:2] in memberships[source['dataset'], source['lfn']],
                    'Selected AOD lumisection absent from file catalog')
            halo = header.get('halo', dict(decision='unknown', reason='missing_halo_read'))
            require(halo.get('decision') in ('selected', 'rejected', 'unknown'), 'Invalid halo decision')
            record['selected_lumi_headers'] += 1
            record[halo['decision']] += 1
            observations.append(dict(run=original[0], luminosityBlock=original[1], event=original[2],
                                     source_index=index, source_entry=entry, halo=halo))
        require(record['headers_read'] == entries, 'AOD file ended before all headers were read')
        record['scanned_to_eof'] = True
        sources.append(record)
        if progress is not None:
            progress(record)
    return assemble(catalog, sources, observations, policy, catalog_sha256, certification_sha256)


def merge_shards(catalog, certification, shards, *, catalog_sha256, certification_sha256, process='RECO'):
    _, required, _ = validate_catalog(catalog, certification)
    policy, sources, observations, seen = halo_policy(process), [], [], set()
    for shard in shards:
        require(shard.get('schema') == 'shift-data-beam-halo-census-v1'
                and shard['full_lumisection_catalog_sha256'] == catalog_sha256
                and shard['certification_sha256'] == certification_sha256
                and shard['policy'] == policy and shard['policy_sha256'] == digest(policy)
                and shard['event_limit_applied'] is False and shard['candidate_branches_read'] is False
                and shard['prior_events_excluded'] is False and shard['all_headers_is_real_data'] is True
                and shard['selected_lumis'] == catalog['lumimask']
                and shard['datasets'] == sorted(catalog['datasets']),
                'Shard policy or input pins differ')
        local = set()
        for source in shard['sources']:
            index = source['source_index']
            require(type(index) is int and 0 <= index < len(required) and index not in seen
                    and all(source[key] == required[index][key] for key in ('dataset', 'lfn', 'source_url'))
                    and source['scanned_to_eof'] is True
                    and source['headers_read'] == source['events_tree_entries'],
                    'Repeated, changed or incomplete shard source')
            seen.add(index)
            local.add(index)
            sources.append(source)
        for row in shard['events']:
            for occurrence in row['occurrences']:
                require(occurrence['source_index'] in local, 'Shard event references foreign source')
                observations.append(dict(run=row['run'], luminosityBlock=row['luminosityBlock'],
                                         event=row['event'], **occurrence))
    return assemble(catalog, sources, observations, policy, catalog_sha256, certification_sha256)


def validate_raw_mapping(census, raw_census):
    require(census.get('flag_census_closed') is True, 'Unknown or partial AOD flags block RAW mapping')
    require(raw_census.get('schema') == 'shift-data-input-header-census-v2'
            and raw_census.get('all_required_files_scanned_to_eof') is True
            and raw_census.get('header_only') is True
            and raw_census.get('all_headers_is_real_data') is True
            and raw_census.get('event_limit_applied') is False
            and raw_census.get('prior_events_excluded') is False,
            'Require complete original RAW header census')
    require(selected_lumisections(census['selected_lumis']) == selected_lumisections(raw_census['selected_lumis']),
            'AOD and RAW lumisections differ')
    raw = {identity(row): row for row in raw_census['events']}
    aod = {identity(row): row for row in census['events']}
    require(len(raw) == len(raw_census['events']) == raw_census['unique_originals']
            and len(aod) == len(census['events']) == census['unique_originals'],
            'Duplicate or changed original identity census')
    require(set(raw) == set(aod), 'AOD/RAW original identity sets differ; no full-LS credit')
    require({name.split('/')[1] for name in census['datasets']}
            == {name.split('/')[1] for name in raw_census['datasets']}, 'AOD/RAW stream unions differ')
    rows = [dict(raw[original], halo_decision='selected') for original in sorted(raw)
            if aod[original]['decision'] == 'selected']
    return dict(schema='shift-data-beam-halo-RAW-mapping-v1', complete=True,
                policy_sha256=census['policy_sha256'], unique_originals=len(raw), selected=len(rows),
                selected_identity_sha256=identity_digest([identity(row) for row in rows]),
                original_identity_sha256=identity_digest(raw), events=rows,
                luminosity_credit='only after all selected original responses verify')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, required=True)
    parser.add_argument('--certification', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--process', default='RECO')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--source-index', type=int, action='append')
    mode.add_argument('--merge-shards', type=Path, nargs='+')
    args = parser.parse_args()
    os.umask(0o077)
    require(not args.output.exists(), 'Preserve existing census; choose a new output')
    inputs = [args.catalog, args.certification] + (args.merge_shards or [])
    require(args.output.resolve() not in [path.resolve() for path in inputs], 'Output would replace an input')
    pins = {str(path.resolve()): sha256(path) for path in inputs}
    catalog, certification = json.loads(args.catalog.read_text()), json.loads(args.certification.read_text())
    keywords = dict(catalog_sha256=pins[str(args.catalog.resolve())],
                    certification_sha256=pins[str(args.certification.resolve())], process=args.process)
    started = time.time()
    if args.merge_shards:
        result = merge_shards(catalog, certification, [json.loads(path.read_text()) for path in args.merge_shards], **keywords)
    else:
        result = scan_catalog(catalog, certification, aod_reader, source_indices=args.source_index,
                              progress=lambda row: print(json.dumps(row), flush=True), **keywords)
    require(all(sha256(path) == pins[str(path.resolve())] for path in inputs), 'A frozen scan input changed')
    result.update(input_pins=pins, scanner_sha256=sha256(__file__),
                  started_epoch=started, completed_epoch=time.time())
    args.output.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    temporary = args.output.with_name(args.output.name + '.new')
    with temporary.open('x') as output:
        json.dump(result, output, indent=2, allow_nan=False)
        output.write('\n')
    temporary.replace(args.output)
    print(json.dumps(dict(flag_census_closed=result['flag_census_closed'],
                          unique_originals=result['unique_originals'], selected=result['selected'],
                          rejected=result['rejected'], unknown=result['unknown'], output_sha256=sha256(args.output))))
    return 0 if result['unknown'] == 0 else 2


if __name__ == '__main__':
    raise SystemExit(main())
