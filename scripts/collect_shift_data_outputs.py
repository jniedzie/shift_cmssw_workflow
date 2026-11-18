#!/usr/bin/env python3
# Code style: AI-code.
"""Verify returned data shards without reading candidate branches."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import tarfile
import time

from run_shift_data_reconstruction import audit_identities, validate_request


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    temporary = path.with_suffix(path.suffix + '.new')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.chmod(0o600)
    temporary.replace(path)


def native_success(directory, cluster, proc):
    """A returned file alone is insufficient: require the native exit event."""
    text = (directory / 'events.log').read_text()
    identity = rf'{cluster:03d}\.{proc:03d}\.\d+'
    records = re.split(r'(?m)(?=^\d{3} \()', text)
    successful = [record for record in records
                  if re.match(rf'005 \({identity}\)', record)
                  and re.search(r'Normal termination \(return value 0\)', record)]
    return bool(successful)


def stage_output(directory, request_sha256, audit=audit_identities):
    request_path = directory / 'request.json'
    if sha256(request_path) != request_sha256:
        raise ValueError('The producer request changed')
    request = json.loads(request_path.read_text())
    expected = validate_request(request)
    result_path = directory / 'result.json'
    result = json.loads(result_path.read_text())
    if not (result.get('schema') == 'shift-data-worker-result-v1'
            and result.get('request_sha256') == request_sha256
            and result.get('input_origin') == 'collision-data'
            and result.get('complete') is True and result.get('exit_code') == 0
            and result.get('native', {}).get('exit_code') == 0
            and result.get('original_events') == len(expected)
            and result.get('candidate_branches_read') is False):
        raise ValueError('The returned producer is not a completed data response')
    if request['schema'] == 'shift-data-reconstruction-job-v2':
        provenance = dict(dataset=request['dataset'], era=request['era'], source_url=request['source_url'],
                          original_runs=sorted({identity[0] for identity in expected}))
        if any(result.get(key) != value for key, value in provenance.items()):
            raise ValueError('The returned producer changed its data source provenance')
    archive_path = directory / 'return_payload.tar.gz'
    root_path = directory / 'restricted_nano.root'
    with tarfile.open(archive_path) as archive:
        members = archive.getmembers()
        names = [member.name for member in members]
        if len(names) != len(set(names)):
            raise ValueError('Duplicate returned archive members')
        receipt = archive.getmember('result.json')
        root = archive.getmember('restricted_nano.root')
        if not receipt.isfile() or not root.isfile() or root.size > 100 * 1024**2:
            raise ValueError('Unsafe returned archive members')
        if archive.extractfile(receipt).read() != result_path.read_bytes():
            raise ValueError('Archive and transferred producer receipts disagree')
        if root.size != result['output']['bytes']:
            raise ValueError('Archived ROOT size mismatch')
        digest = hashlib.sha256()
        with archive.extractfile(root) as source:
            for block in iter(lambda: source.read(1024 * 1024), b''):
                digest.update(block)
        if digest.hexdigest() != result['output']['sha256']:
            raise ValueError('Archived ROOT checksum mismatch')
        if root_path.exists():
            if sha256(root_path) != result['output']['sha256']:
                raise ValueError('Existing canonical ROOT changed')
        else:
            temporary = root_path.with_suffix('.root.new')
            with archive.extractfile(root) as source, temporary.open('xb') as target:
                shutil.copyfileobj(source, target)
            temporary.chmod(0o600)
            if sha256(temporary) != result['output']['sha256']:
                raise ValueError('Returned ROOT checksum mismatch')
            temporary.replace(root_path)
    if root_path.stat().st_size != result['output']['bytes']:
        raise ValueError('Returned ROOT size mismatch')
    identity = audit(root_path, expected)
    if (identity['root_uuid'] != result['output']['root_uuid']
            or identity != result['identity_audit']):
        raise ValueError('Independent identity readback differs from producer')
    staged = dict(path=str(root_path), origin_uri=request['source_url'],
                root_uuid=identity['root_uuid'], sha256=sha256(root_path),
                bytes=root_path.stat().st_size,
                producer_receipt=dict(path=str(result_path), sha256=sha256(result_path)),
                expected_event_count=len(expected), request_sha256=request_sha256,
                source_offset=request['source_offset'],
                archive_sha256=sha256(archive_path), native_exit_verified=True)
    if request['schema'] == 'shift-data-reconstruction-job-v2':
        staged.update(dataset=request['dataset'], era=request['era'],
                      original_runs=sorted({identity[0] for identity in expected}))
    return staged


def collect(plan_path, submission_paths, output_path):
    plan = json.loads(plan_path.read_text())
    assignments = {}
    for submission_path in submission_paths:
        receipt = json.loads(submission_path.read_text())
        for proc, name in enumerate(receipt['job_names']):
            if name in assignments:
                raise ValueError('Duplicate producer ownership in submissions')
            assignments[name] = (receipt['cluster_id'], proc)
    ready, pending, failures = [], [], []
    for item in plan['jobs']:
        directory = Path(item['directory'])
        name = item['name']
        if name not in assignments:
            pending.append(dict(job_name=name, reason='not_submitted'))
            continue
        cluster, proc = assignments[name]
        if not (directory / 'events.log').exists() or not native_success(directory, cluster, proc):
            pending.append(dict(job_name=name, cluster_id=cluster, proc_id=proc,
                                reason='native_success_not_yet_observed'))
            continue
        try:
            output = stage_output(directory, item['request_sha256'])
        except (OSError, ValueError, KeyError, tarfile.TarError) as error:
            failures.append(dict(job_name=name, error_type=type(error).__name__))
            continue
        ready.append(dict(output, job_name=name, cluster_id=cluster, proc_id=proc))
    result = dict(schema='shift-data-verified-staged-outputs-v1', time_utc=time.time(),
                  plan=dict(path=str(plan_path), sha256=sha256(plan_path)),
                  ready=ready, pending=pending, failures=failures,
                  completed_originals=sum(row['expected_event_count'] for row in ready),
                  candidate_branches_read=False)
    save(output_path, result)
    print(json.dumps({name: result[name] for name in ('completed_originals', 'candidate_branches_read')}
                     | dict(ready_jobs=len(ready), pending_jobs=len(pending), failed_readbacks=len(failures))))
    return result


def build_publication_inventory(base, staged, census):
    if not (base.get('schema') == 'shift-data-staged-publication-inputs-v1'
            and base.get('dataset') == '/ZeroBias/Run2023D-v1/RAW'
            and base.get('era') == 'Run2023D' and base.get('run') == 369943
            and base.get('expected_unique_events') == 20
            and sum(row['expected_event_count'] for row in base['inputs']) == 20
            and census.get('header_only') is True
            and census.get('all_headers_is_real_data') is True):
        raise ValueError('Require the qualified first20 data inventory and real-data census')
    headers = {row['source_entry']: (row['run'], row['luminosityBlock'], row['event'])
               for row in census['events']}
    def digest(entries):
        identities = sorted(headers[entry] for entry in entries)
        if len(set(identities)) != len(entries):
            raise ValueError('Publication census contains duplicate event identities')
        return hashlib.sha256(b''.join(struct.pack('>IIQ', *row) for row in identities)).hexdigest()
    entries = set(range(20))
    if digest(entries) != base['expected_identity_sha256']:
        raise ValueError('The qualified first20 inventory has a different identity ledger')
    rows = list(base['inputs'])
    keys = ('path', 'origin_uri', 'root_uuid', 'sha256', 'bytes',
            'producer_receipt', 'expected_event_count')
    for row in sorted(staged['ready'], key=lambda item: item['source_offset']):
        offset, count = row['source_offset'], row['expected_event_count']
        if (type(offset) is not int or type(count) is not int or count <= 0
                or offset < 20 or offset + count > 1000
                or row.get('native_exit_verified') is not True):
            raise ValueError('Invalid or unverified canonical data ownership')
        new_entries = set(range(offset, offset + count))
        if entries & new_entries:
            raise ValueError('Overlapping canonical data ownership')
        entries.update(new_entries)
        rows.append({key: row[key] for key in keys})
    return dict(base, inputs=rows, expected_unique_events=len(entries),
                expected_identity_sha256=digest(entries))


def build_cohort_publication_inventory(staged, census, event_budget):
    """Bind one completed canonical subset to its full original header census."""
    if (census.get('schema') != 'shift-data-input-header-census-v2'
            or census.get('header_only') is not True or census.get('all_headers_is_real_data') is not True
            or census.get('era') != 'Run2023D' or type(event_budget) is not int
            or not 0 < event_budget <= 100000):
        raise ValueError('Require a supported real-data header census and bounded event budget')
    headers = {row['globalcohort_entry']: row for row in census['events']}
    original_ids = {(row['run'], row['luminosityBlock'], row['event']) for row in census['events']}
    if (len(headers) != len(census['events']) or len(headers) != census.get('unique_originals')
            or len(original_ids) != len(headers) or set(headers) != set(range(len(headers)))
            or len(headers) > event_budget):
        raise ValueError('The original header census has duplicate or excess ownership')
    identities, entries, rows, datasets, runs = [], set(), [], set(), set()
    keys = ('path', 'origin_uri', 'root_uuid', 'sha256', 'bytes',
            'producer_receipt', 'expected_event_count')
    for row in sorted(staged['ready'], key=lambda item: item['source_offset']):
        offset, count = row['source_offset'], row['expected_event_count']
        if (type(offset) is not int or type(count) is not int or count <= 0 or offset < 0
                or row.get('native_exit_verified') is not True or row.get('era') != census['era']):
            raise ValueError('Invalid or unverified canonical data ownership')
        selected = set(range(offset, offset + count))
        if entries & selected or not selected <= headers.keys():
            raise ValueError('Overlapping or unknown canonical data ownership')
        originals = [headers[index] for index in sorted(selected)]
        if (any(item['source_dataset'] != row['dataset'] or item['source_url'] != row['origin_uri']
                for item in originals)
                or sorted({item['run'] for item in originals}) != row.get('original_runs')):
            raise ValueError('Canonical data provenance differs from original headers')
        entries.update(selected)
        identities.extend((item['run'], item['luminosityBlock'], item['event']) for item in originals)
        datasets.add(row['dataset'])
        runs.update(row['original_runs'])
        rows.append({key: row[key] for key in keys})
    if not rows or len(identities) != len(set(identities)):
        raise ValueError('Publication needs completed unique canonical originals')
    digest = hashlib.sha256(b''.join(struct.pack('>IIQ', *item) for item in sorted(identities))).hexdigest()
    return dict(schema='shift-data-staged-publication-inputs-v2', datasets=sorted(datasets),
                era=census['era'], runs=sorted(runs), event_budget=event_budget,
                expected_unique_events=len(identities), expected_identity_sha256=digest, inputs=rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--submission', type=Path, action='append', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--base-inventory', type=Path)
    parser.add_argument('--census', type=Path)
    parser.add_argument('--publication-inventory', type=Path)
    args = parser.parse_args()
    if bool(args.census) != bool(args.publication_inventory) or (args.base_inventory and not args.census):
        parser.error('Publication inventory requires the census and output path; a legacy base is optional')
    staged = collect(args.plan, args.submission, args.output)
    if args.census:
        plan = json.loads(args.plan.read_text())
        if sha256(args.census) != plan['source_census_sha256']:
            raise ValueError('The original data header census changed')
        census = json.loads(args.census.read_text())
        if args.base_inventory:
            inventory = build_publication_inventory(json.loads(args.base_inventory.read_text()), staged, census)
        else:
            inventory = build_cohort_publication_inventory(staged, census, plan['event_budget'])
        save(args.publication_inventory, inventory)


if __name__ == '__main__':
    main()
