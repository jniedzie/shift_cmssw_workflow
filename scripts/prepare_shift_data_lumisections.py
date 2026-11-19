#!/usr/bin/env python3
# Code style: AI-code.
"""AI-generated full-lumisection ownership plans; preparation never submits jobs."""
import argparse
import collections
import copy
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

from audit_shift_data_luminosity import (
    certification_ranges, identity, identity_digest, lumimask, pin, require, sha256,
    write_json,
)


def selected_lumisections(mask):
    ranges = certification_ranges(mask)
    return {(run, lumi) for run, bounds in ranges.items()
            for low, high in bounds for lumi in range(low, high + 1)}


def canonical_lfn(value):
    require(isinstance(value, str), 'RAW source must be a string')
    parsed = urlsplit(value)
    require(not parsed.query and not parsed.fragment, 'RAW source has URL query or fragment')
    path = '/' + parsed.path.lstrip('/')
    require(path.startswith('/store/') and path.endswith('.root')
            and '..' not in Path(path).parts, 'Invalid original RAW LFN')
    return path


def validate_catalog(catalog, certification):
    """Return selected LS and the complete declared dataset/LFN source union."""
    require(catalog.get('schema') == 'shift-data-full-lumisection-catalog-v1',
            'Require an explicit complete-lumisection catalogue')
    selected = selected_lumisections(catalog['lumimask'])
    require(selected, 'Select at least one complete certified lumisection')
    require(selected <= selected_lumisections(certification), 'Requested lumisection is uncertified')
    datasets = catalog['datasets']
    require(isinstance(datasets, list) and datasets
            and len(set(datasets)) == len(datasets)
            and all(isinstance(value, str) and re.fullmatch(r'/[A-Za-z0-9_]+/Run2023D-v[0-9]+/RAW', value)
                    for value in datasets), 'Require distinct qualified Run2023D RAW datasets')
    expected = {(run, lumi, dataset) for run, lumi in selected for dataset in datasets}
    observed = set()
    sources = {}
    for record in catalog['records']:
        key = record['run'], record['luminosityBlock'], record['dataset']
        require(key in expected and key not in observed, 'Missing, repeated or foreign catalogue scope')
        require(record.get('query_complete') is True and isinstance(record.get('files'), list),
                'Unknown catalogue response cannot establish lumisection completeness')
        observed.add(key)
        files_seen = set()
        for file in record['files']:
            lfn, url = file['lfn'], file['source_url']
            require(lfn == canonical_lfn(lfn) == canonical_lfn(url), 'Catalogue source URL differs from LFN')
            require(lfn not in files_seen, 'Repeated RAW file in catalogue response')
            files_seen.add(lfn)
            source_key = record['dataset'], lfn
            value = dict(dataset=record['dataset'], lfn=lfn, source_url=url)
            require(source_key not in sources or sources[source_key] == value,
                    'One RAW source changed catalogue transport ownership')
            sources[source_key] = value
    require(observed == expected, 'Incomplete lumisection/dataset catalogue matrix')
    return selected, [sources[key] for key in sorted(sources)]


def digest_value(value):
    encoded = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def build_full_lumi_plan(catalog, census, certification, max_events_per_job=100):
    selected, required_sources = validate_catalog(catalog, certification)
    require(type(max_events_per_job) is int and 1 <= max_events_per_job <= 1000,
            'Worker shard size must lie between one and 1000 originals')
    require(census.get('schema') == 'shift-data-input-header-census-v2'
            and census.get('header_only') is True
            and census.get('all_headers_is_real_data') is True
            and census.get('candidate_branches_read') is False,
            'Require a real-data header-only census')
    require(census.get('all_required_files_scanned_to_eof') is True
            and census.get('event_limit_applied') is False
            and census.get('prior_events_excluded') is False,
            'A prefix, partial scan or prior-event exclusion cannot prove full lumisections')
    require(selected_lumisections(census['selected_lumis']) == selected,
            'Header census differs from selected lumisections')
    require(re.fullmatch(r'[0-9a-f]{64}', census.get('full_lumisection_catalog_sha256', '')),
            'Header census lacks its exact catalogue pin')
    sources = census['sources']
    required = {(row['dataset'], row['lfn']): row for row in required_sources}
    memberships = collections.defaultdict(set)
    for record in catalog['records']:
        for file in record['files']:
            memberships[record['dataset'], file['lfn']].add((record['run'], record['luminosityBlock']))
    actual = {}
    for index, source in enumerate(sources):
        key = source['dataset'], source.get('lfn', canonical_lfn(source['source_url']))
        require(key in required and key not in actual, 'Missing, repeated or foreign required RAW source')
        require(source['source_url'] == required[key]['source_url'], 'Scanned RAW source URL changed')
        require(source.get('source_index', index) == index, 'Scanned RAW source index changed')
        for field in ('events_tree_entries', 'headers_read', 'selected_headers',
                      'duplicate_selected_headers', 'accepted_originals'):
            require(type(source.get(field)) is int and source[field] >= 0,
                    'Missing nonnegative per-file header accounting: ' + field)
        require(source['headers_read'] == source['events_tree_entries']
                and source['selected_headers'] <= source['headers_read']
                and source['selected_headers'] == source['accepted_originals']
                    + source['duplicate_selected_headers'],
                'RAW file was truncated or selected-header deduplication is unaccounted')
        actual[key] = index
    require(set(actual) == set(required), 'Not every catalogue RAW source was scanned')
    events = census['events']
    require(len(events) == census['unique_originals'], 'Header census denominator changed')
    seen = set()
    entries = collections.defaultdict(set)
    source_counts = collections.Counter()
    by_ls = collections.defaultdict(list)
    ordered_ids = []
    indexed = []
    for offset, row in enumerate(events):
        require(row.get('globalcohort_entry') == offset, 'Header census ordering changed')
        original = identity(row)
        require(original[:2] in selected and original not in seen,
                'Duplicate identity or event outside requested certified lumisections')
        seen.add(original)
        ordered_ids.append(original)
        index = row['source_index']
        require(type(index) is int and 0 <= index < len(sources), 'Unknown header RAW source')
        source = sources[index]
        require(row['source_dataset'] == source['dataset'] and row['source_url'] == source['source_url'],
                'Header dataset or RAW source binding changed')
        require(original[:2] in memberships[source['dataset'], canonical_lfn(source['source_url'])],
                'RAW header lumisection is absent from its catalogue file scope')
        entry = row['source_entry']
        require(type(entry) is int and 0 <= entry < source['events_tree_entries']
                and entry not in entries[index], 'Repeated or invalid original RAW entry')
        entries[index].add(entry)
        source_counts[index] += 1
        by_ls[original[:2]].append(original)
        indexed.append((offset, row))
    require(all(source_counts[index] == source['accepted_originals'] for index, source in enumerate(sources)),
            'Owned event rows differ from per-file accepted-original accounting')
    ordered_digest = hashlib.sha256(json.dumps(ordered_ids, separators=(',', ':')).encode()).hexdigest()
    if 'processed_identity_sha256' in census:
        require(census['processed_identity_sha256'] == ordered_digest, 'Original ordered identity digest changed')
    if 'expected_identity_sha256' in census:
        require(census['expected_identity_sha256'] == identity_digest(ordered_ids), 'Original identity-set digest changed')
    indexed.sort(key=lambda pair: (pair[1]['run'], pair[1]['luminosityBlock'],
                                   pair[1]['source_dataset'], pair[1]['source_url'], pair[1]['source_entry']))
    normalized = census
    if any(old != new for new, (old, _) in enumerate(indexed)):
        reordered_ids = [ordered_ids[old] for old, _ in indexed]
        normalized = dict(census, events=[dict(row, globalcohort_entry=index)
                                         for index, (_, row) in enumerate(indexed)],
                          processed_identity_sha256=hashlib.sha256(json.dumps(reordered_ids, separators=(',', ':')).encode()).hexdigest(),
                          expected_identity_sha256=identity_digest(reordered_ids),
                          ordering='Run, lumisection, canonical owner dataset, RAW URL, original entry')
    jobs = []
    jobs_by_ls = collections.defaultdict(list)
    offset = 0
    while offset < len(events):
        first = normalized['events'][offset]
        key = first['run'], first['luminosityBlock'], first['source_index']
        end = offset + 1
        while end < len(events) and end - offset < max_events_per_job:
            row = normalized['events'][end]
            if (row['run'], row['luminosityBlock'], row['source_index']) != key:
                break
            end += 1
        rows = normalized['events'][offset:end]
        jobs.append(dict(name=f'r{key[0]:06d}_ls{key[1]:06d}_s{key[2]:04d}_o{offset:09d}',
                         run=key[0], luminosityBlock=key[1], source_index=key[2],
                         source_offset=offset, expected_event_count=end-offset,
                         identity_sha256=identity_digest([identity(row) for row in rows]), events=rows))
        jobs_by_ls[key[:2]].append(jobs[-1]['name'])
        offset = end
    lumisections = [dict(run=run, luminosityBlock=lumi, expected_events=len(by_ls[run, lumi]),
                        identity_sha256=identity_digest(by_ls[run, lumi]),
                        job_names=jobs_by_ls[run, lumi])
                    for run, lumi in sorted(selected)]
    return dict(schema='shift-data-full-lumisection-plan-v1', lumimask=lumimask(selected),
                datasets=sorted(catalog['datasets']), required_sources=required_sources,
                normalized_census=normalized, jobs=jobs, lumisections=lumisections,
                max_events_per_job=max_events_per_job)


def validate_contract(contract):
    require(contract.get('schema') == 'shift-data-full-lumisection-contract-v1'
            and contract.get('preparation_only') is True, 'Require a frozen full-lumisection contract')
    selected = selected_lumisections(contract['lumimask'])
    require(contract.get('datasets') and len(set(contract['datasets'])) == len(contract['datasets']),
            'Invalid contract dataset union')
    for key in ('catalog', 'census', 'certification'):
        entry = contract[key]
        require(Path(entry['path']).is_absolute() and re.fullmatch(r'[0-9a-f]{64}', entry['sha256']),
                'Contract lacks an absolute frozen metadata pin')
    science = {key: contract[key] for key in ('lumimask', 'datasets', 'required_sources',
                                             'lumisections', 'catalog', 'census', 'certification')}
    require(contract.get('cohort_sha256') == digest_value(science), 'Full-lumisection cohort binding changed')
    jobs = {}
    hashes = set()
    for job in contract['jobs']:
        require(job['name'] not in jobs and job['request_sha256'] not in hashes,
                'Duplicate contract worker ownership')
        require(re.fullmatch(r'[0-9a-f]{64}', job['request_sha256'])
                and Path(job['request_path']).is_absolute()
                and (job['run'], job['luminosityBlock']) in selected
                and type(job['expected_event_count']) is int and 1 <= job['expected_event_count'] <= 1000,
                'Invalid full-lumisection worker reference')
        require(re.fullmatch(r'[0-9a-f]{64}', job['identity_sha256']), 'Worker identity digest changed')
        jobs[job['name']] = job
        hashes.add(job['request_sha256'])
    rows = {}
    all_jobs = []
    for row in contract['lumisections']:
        key = row['run'], row['luminosityBlock']
        require(key in selected and key not in rows and type(row['expected_events']) is int
                and row['expected_events'] >= 0 and re.fullmatch(r'[0-9a-f]{64}', row['identity_sha256']),
                'Invalid contract lumisection denominator')
        names = row['job_names']
        require(len(set(names)) == len(names) and all(name in jobs for name in names),
                'Missing or repeated contract lumisection workers')
        require(all((jobs[name]['run'], jobs[name]['luminosityBlock']) == key for name in names)
                and sum(jobs[name]['expected_event_count'] for name in names) == row['expected_events'],
                'Contract worker counts differ from lumisection denominator')
        require(row['expected_events'] != 0 or (not names and row['identity_sha256'] == identity_digest([])),
                'Empty lumisection has unexpected workers or identities')
        rows[key] = row
        all_jobs.extend(names)
    require(set(rows) == selected and len(all_jobs) == len(jobs) and set(all_jobs) == set(jobs),
            'Contract does not cover the exact selected lumisection/worker union')
    return contract


def completed_lumisections(contract, verified_request_sha256s):
    """Credit only full LS whose exact request hashes have verified responses."""
    validate_contract(contract)
    verified = set(verified_request_sha256s)
    jobs = {job['name']: job for job in contract['jobs']}
    require(verified <= {job['request_sha256'] for job in jobs.values()},
            'Verified completion refers to an unknown worker request')
    closed, pending, rows = set(), set(), []
    for row in contract['lumisections']:
        missing = [jobs[name]['request_sha256'] for name in row['job_names']
                   if jobs[name]['request_sha256'] not in verified]
        key = row['run'], row['luminosityBlock']
        (pending if missing else closed).add(key)
        rows.append(dict(row, complete=not missing, missing_request_sha256s=missing))
    return dict(closed_lumimask=lumimask(closed), pending_lumimask=lumimask(pending),
                closed_original_events=sum(row['expected_events'] for row in rows if row['complete']),
                lumisections=rows)


def load_contract(path, expected_sha256, dataset, inputs):
    """Validate the frozen preparation contract and select its certified mask."""
    require(sha256(path) == expected_sha256, 'Full-lumisection contract changed')
    contract = validate_contract(json.loads(Path(path).read_text()))
    require(dataset in contract['datasets'], 'Dataset is outside the full-lumisection cohort')
    for key in ('catalog', 'census', 'certification'):
        require(sha256(contract[key]['path']) == contract[key]['sha256'], 'Frozen contract metadata changed: ' + key)
    catalog = json.loads(Path(contract['catalog']['path']).read_text())
    certification = json.loads(Path(contract['certification']['path']).read_text())
    selected, sources = validate_catalog(catalog, certification)
    require(lumimask(selected) == contract['lumimask'] and sorted(catalog['datasets']) == sorted(contract['datasets'])
            and sources == contract['required_sources'], 'Contract differs from its complete source catalogue')
    allowed = {row['lfn'] for row in sources if row['dataset'] == dataset}
    requested = {canonical_lfn(value) for value in inputs}
    require(inputs and len(requested) == len(inputs) and requested <= allowed,
            'Configuration inputs lie outside the frozen complete source union')
    return contract['lumimask']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('catalog', 'census', 'certification', 'worker-template', 'output-dir'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--max-events-per-job', type=int, default=100)
    parser.add_argument('--campaign-date', default=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d'))
    args = parser.parse_args()
    os.umask(0o077)
    directory = args.output_dir.resolve()
    require(directory.is_absolute() and directory.parts[1] in ('afs', 'eos'),
            'Worker plans need an absolute persistent AFS/EOS directory')
    require(not directory.exists(), 'Preserve an existing immutable preparation directory')
    require(re.fullmatch(r'[0-9]{8}', args.campaign_date), 'Require an explicit YYYYMMDD campaign date')
    catalog = json.loads(args.catalog.read_text())
    census = json.loads(args.census.read_text())
    require(census.get('full_lumisection_catalog_sha256') == sha256(args.catalog),
            'Header census belongs to another catalogue')
    require(census.get('certification_sha256') == sha256(args.certification),
            'Header census certification changed')
    plan = build_full_lumi_plan(catalog, census, json.loads(args.certification.read_text()), args.max_events_per_job)
    template = json.loads(args.worker_template.read_text())
    require(template.get('schema') == 'shift-data-reconstruction-job-v2', 'Require a qualified version-two worker template')
    directory.mkdir(mode=0o700, parents=True)
    census_path = args.census.resolve()
    if plan['normalized_census'] is not census:
        census_path = directory / 'canonical_census.json'
        write_json(census_path, plan['normalized_census'])
    pins = dict(catalog=pin(args.catalog), census=pin(census_path), certification=pin(args.certification))
    science = dict(lumimask=plan['lumimask'], datasets=plan['datasets'],
                   required_sources=plan['required_sources'], lumisections=plan['lumisections'], **pins)
    cohort_sha = digest_value(science)
    references = []
    from run_shift_data_reconstruction import validate_request
    for job in plan['jobs']:
        work = directory / job['name']
        work.mkdir(mode=0o700)
        source = plan['normalized_census']['sources'][job['source_index']]
        request = copy.deepcopy(template)
        primary = source['dataset'].split('/')[1]
        request.update(directory=str(work), job_name=job['name'], dataset=source['dataset'],
                       era='Run2023D', batch_name=f'DATA_Run2023D_{primary}_SHIFTReco_{args.campaign_date}_fullLumis_{cohort_sha[:8]}',
                       source_url=source['source_url'], source_offset=job['source_offset'],
                       events=[{key: row[key] for key in ('run', 'luminosityBlock', 'event')} for row in job['events']],
                       input_census_sha256=pins['census']['sha256'], full_lumisection_cohort_sha256=cohort_sha,
                       exposure_credit='One response per original; luminosity credited only after complete lumisection closure')
        request.pop('source_file_adler32', None)
        if source.get('adler32'):
            request['source_file_adler32'] = source['adler32']
        for key in tuple(request):
            if key.startswith('transport_qualification') or key == 'operational_canary':
                request.pop(key)
        if 'remote_output_root' in request:
            old = Path(request['remote_output_root'])
            path = str(old.parent / (cohort_sha[:12] + '_' + job['name'] + '.root'))
            parsed = urlsplit(request['remote_output_url'])
            request.update(remote_output_root=path, remote_output_url=parsed.scheme + '://' + parsed.netloc + '/' + path)
        validate_request(request)
        request_path = work / 'request.json'
        write_json(request_path, request)
        references.append({key: job[key] for key in ('name', 'run', 'luminosityBlock', 'source_offset',
                                                    'expected_event_count', 'identity_sha256')}
                          | dict(dataset=source['dataset'], lfn=canonical_lfn(source['source_url']),
                                 request_path=str(request_path), request_sha256=sha256(request_path)))
    contract = dict(schema='shift-data-full-lumisection-contract-v1', preparation_only=True,
                    cohort_sha256=cohort_sha, lumimask=plan['lumimask'], datasets=plan['datasets'],
                    required_sources=plan['required_sources'], lumisections=plan['lumisections'],
                    jobs=references, **pins)
    validate_contract(contract)
    output = directory / 'full_lumisection_contract.json'
    write_json(output, contract)
    print(json.dumps(dict(prepared_only=True, contract=str(output), contract_sha256=sha256(output),
                          lumisections=len(plan['lumisections']), jobs=len(references),
                          original_events=len(plan['normalized_census']['events']), submitted=False)))


if __name__ == '__main__':
    main()
