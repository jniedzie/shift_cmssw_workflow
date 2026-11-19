#!/usr/bin/env python3
# Code style: AI-code.
"""AI-generated complete RAW-header census for predefined certified lumisections.

Run in an initialized CMSSW environment. Every catalogued input is scanned to
EOF using EventAuxiliary only. There is no event cap or previous-event removal:
previously processed originals must remain in the expected full-LS population.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import time


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def identity(row):
    values = tuple(row[name] for name in ('run', 'luminosityBlock', 'event'))
    require(all(type(value) is int and 0 < value < 2**bits
                for value, bits in zip(values, (32, 32, 64))),
            'Invalid original event identity')
    return values


def identity_digest(identities):
    digest = hashlib.sha256()
    for item in sorted(identities):
        digest.update(struct.pack('>IIQ', *item))
    return digest.hexdigest()


def raw_header_reader(source):
    """Return original RAW metadata and a lazy EventAuxiliary-only iterator."""
    import ROOT
    from DataFormats.FWLite import Events

    file = ROOT.TFile.Open(source['source_url'], 'READ')
    require(file and not file.IsZombie() and not file.TestBit(ROOT.TFile.kRecovered),
            'Required RAW source is not ordinarily readable')
    try:
        tree = file.Get('Events')
        require(tree and tree.InheritsFrom('TTree'), 'Required RAW source has no Events tree')
        branches = [branch.GetName() for branch in tree.GetListOfBranches()
                    if branch.GetName().startswith('FEDRawDataCollection_')]
        require(branches, 'Required source lacks a full FED RAW collection')
        metadata = dict(events_tree_entries=int(tree.GetEntries()),
                        root_uuid=file.GetUUID().AsString(),
                        raw_collection_branches=branches)
    finally:
        file.Close()

    def headers():
        events = Events(source['source_url'])
        for event in events:
            aux = event.eventAuxiliary()
            yield dict(run=int(aux.id().run()),
                       luminosityBlock=int(aux.luminosityBlock()),
                       event=int(aux.id().event()), is_real_data=bool(aux.isRealData()))

    return metadata, headers()


def scan_catalog(catalog, certification, header_reader, *, catalog_sha256,
                 certification_sha256, progress=None):
    """Read the full declared file closure, preserving globally unique originals."""
    from prepare_shift_data_lumisections import validate_catalog

    selected_lumis, required_sources = validate_catalog(catalog, certification)
    memberships = {}
    for record in catalog['records']:
        lumi = record['run'], record['luminosityBlock']
        for file in record['files']:
            key = record['dataset'], file['lfn']
            memberships.setdefault(key, set()).add(lumi)
    rows, sources, seen = [], [], set()
    started = time.time()
    for required in required_sources:
        source_index = len(sources)
        metadata, headers = header_reader(required)
        entries = metadata['events_tree_entries']
        require(type(entries) is int and entries >= 0, 'Invalid RAW entry count')
        require(isinstance(metadata.get('root_uuid'), str) and metadata['root_uuid'],
                'RAW source lacks its original UUID')
        source = dict(required, source_index=source_index, **metadata,
                      headers_read=0, selected_headers=0,
                      duplicate_selected_headers=0, accepted_originals=0)
        source_memberships = memberships[required['dataset'], required['lfn']]
        for source_entry, header in enumerate(headers):
            source['headers_read'] += 1
            require(source['headers_read'] <= entries,
                    'RAW header iteration exceeds its original entry count')
            require(header.get('is_real_data') is True,
                    'Required source contains a non-collision-data header')
            original = identity(header)
            run, lumi, event_number = original
            if (run, lumi) not in selected_lumis:
                continue
            require((run, lumi) in source_memberships,
                    'A selected RAW lumisection is absent from its file catalog')
            source['selected_headers'] += 1
            if original in seen:
                source['duplicate_selected_headers'] += 1
                continue
            seen.add(original)
            source['accepted_originals'] += 1
            rows.append(dict(source_index=source_index, source_entry=source_entry,
                             source_dataset=required['dataset'],
                             source_url=required['source_url'],
                             run=run, luminosityBlock=lumi, event=event_number))
        require(source['headers_read'] == entries,
                'Required RAW file ended before all original headers were read')
        require(source['selected_headers'] == source['accepted_originals']
                + source['duplicate_selected_headers'], 'Selected header accounting differs')
        sources.append(source)
        if progress is not None:
            progress(dict(source=source_index, dataset=source['dataset'],
                          headers_read=source['headers_read'],
                          selected_headers=source['selected_headers'],
                          accepted_originals=source['accepted_originals'],
                          cumulative_unique_originals=len(rows)))

    rows.sort(key=lambda row: (row['run'], row['luminosityBlock'],
                              row['source_dataset'], row['source_url'], row['source_entry']))
    for offset, row in enumerate(rows):
        row['globalcohort_entry'] = offset
    ordered_ids = [(row['run'], row['luminosityBlock'], row['event']) for row in rows]
    return dict(schema='shift-data-input-header-census-v2',
                era='Run2023D', datasets=sorted(catalog['datasets']),
                selected_lumis=catalog['lumimask'],
                selected_lumisection_count=len(selected_lumis),
                full_lumisection_catalog_sha256=catalog_sha256,
                certification_sha256=certification_sha256,
                all_required_files_scanned_to_eof=True,
                event_limit_applied=False, prior_events_excluded=False,
                header_only=True, product_handles_requested=False,
                candidate_branches_read=False, all_headers_is_real_data=True,
                unique_originals=len(rows), sources=sources, events=rows,
                processed_identity_sha256=hashlib.sha256(
                    json.dumps(ordered_ids, separators=(',', ':')).encode()).hexdigest(),
                expected_identity_sha256=identity_digest(seen),
                started_at_utc=started, completed_at_utc=time.time(),
                ordering='Run/LS, first catalog owner by dataset/LFN, source URL and original entry; no event cap or prior exclusion')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, required=True)
    parser.add_argument('--certification', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    require(args.output.resolve() not in (args.catalog.resolve(), args.certification.resolve()),
            'The header census must not replace an input file')
    if args.output.exists():
        raise FileExistsError('Preserve the existing header census; choose a new output')
    catalog_bytes, certification_bytes = args.catalog.read_bytes(), args.certification.read_bytes()
    catalog, certification = json.loads(catalog_bytes), json.loads(certification_bytes)
    catalog_sha = hashlib.sha256(catalog_bytes).hexdigest()
    certification_sha = hashlib.sha256(certification_bytes).hexdigest()
    result = scan_catalog(catalog, certification, raw_header_reader,
                          catalog_sha256=catalog_sha, certification_sha256=certification_sha,
                          progress=lambda row: print(json.dumps(row), flush=True))
    require(sha256(args.catalog) == catalog_sha and sha256(args.certification) == certification_sha,
            'The selected catalog or certification changed during scanning')
    result.update(full_lumisection_catalog_path=str(args.catalog.resolve()),
                  certification_path=str(args.certification.resolve()),
                  scanner_sha256=sha256(__file__))
    args.output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = args.output.with_name(args.output.name + '.new')
    with temporary.open('x') as output:
        json.dump(result, output, indent=2, allow_nan=False)
        output.write('\n')
    temporary.replace(args.output)
    print(json.dumps(dict(complete=True, unique_originals=result['unique_originals'],
                          source_files=len(result['sources']),
                          selected_lumisections=result['selected_lumisection_count'],
                          output_sha256=sha256(args.output))))


if __name__ == '__main__':
    main()
