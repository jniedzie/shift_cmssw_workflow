#!/usr/bin/env python3
# Code style: AI-code.
"""Verify returned data shards without reading candidate branches."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
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
    return dict(path=str(root_path), origin_uri=request['source_url'],
                root_uuid=identity['root_uuid'], sha256=sha256(root_path),
                bytes=root_path.stat().st_size,
                producer_receipt=dict(path=str(result_path), sha256=sha256(result_path)),
                expected_event_count=len(expected), request_sha256=request_sha256,
                source_offset=request['source_offset'],
                archive_sha256=sha256(archive_path), native_exit_verified=True)


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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--submission', type=Path, action='append', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    collect(args.plan, args.submission, args.output)


if __name__ == '__main__':
    main()
