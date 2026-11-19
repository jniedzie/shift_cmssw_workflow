# Code style: AI-code.
"""AI-generated preservation and bounded recovery for one owned data shard."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tarfile
import time


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def snapshot_previous(persistent, work):
    """Preserve returned files before a restarted wrapper creates new ones."""
    persistent, work = Path(persistent), Path(work)
    origin = next((directory for directory in (work, persistent)
                   if (directory / 'return_payload.tar.gz').is_file()), None)
    if origin is None:
        return None
    archive = origin / 'return_payload.tar.gz'
    digest = sha256(archive)
    destination = persistent / 'attempt_history' / digest
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    for name in ('return_payload.tar.gz', 'result.json', 'status.json', 'attempt_started.json'):
        source = origin / name
        if name == 'attempt_started.json':
            source = persistent / name
        if source.is_file():
            target = destination / name
            if target.exists():
                if sha256(source) != sha256(target):
                    raise ValueError('Previous attempt snapshot changed')
            else:
                shutil.copyfile(source, target)
    if sha256(destination / archive.name) != digest:
        raise ValueError('Previous attempt archive readback failed')
    pointer = dict(directory=str(destination), archive_sha256=digest)
    (persistent / 'previous_attempt.json').write_text(json.dumps(pointer, indent=2) + '\n')
    return destination


def restore_root(snapshot, work, request):
    destination = Path(work) / 'restricted_nano.root'
    with tarfile.open(Path(snapshot) / 'return_payload.tar.gz') as archive:
        try:
            member = archive.getmember('restricted_nano.root')
        except KeyError:
            from shift_data_remote_output import restore_completed
            previous = json.loads((Path(snapshot) / 'result.json').read_text())
            return restore_completed(request, previous, destination)
        if not member.isfile() or member.size > 100 * 1024**2:
            raise ValueError('Unsafe previous ROOT archive member')
        with archive.extractfile(member) as source, destination.open('wb') as target:
            shutil.copyfileobj(source, target)
    return destination


def reviewed_timeout_recovery(persistent, request, request_sha256, snapshot,
                              pointer, previous_guard, previous, receipt_path,
                              receipt_sha256):
    """Permit one reviewed CPU-timeout retry without changing the frozen request."""
    receipt_path = Path(receipt_path)
    if sha256(receipt_path) != receipt_sha256:
        raise ValueError('Reviewed timeout recovery receipt changed')
    receipt = json.loads(receipt_path.read_text())
    if (receipt.get('schema') != 'shift-data-timeout-recovery-v1'
            or receipt.get('request_sha256') != request_sha256
            or receipt.get('directory') != str(persistent)
            or not persistent.is_absolute()):
        raise ValueError('Timeout recovery belongs to a different frozen request')
    if (receipt.get('previous_archive_sha256') != pointer['archive_sha256']
            or snapshot.resolve() != (persistent / 'attempt_history' / pointer['archive_sha256']).resolve()
            or receipt.get('original_guard_sha256') != sha256(persistent / 'attempt_started.json')
            or receipt.get('original_guard_sha256') != sha256(snapshot / 'attempt_started.json')
            or receipt.get('previous_result_sha256') != sha256(snapshot / 'result.json')):
        raise ValueError('Timeout recovery differs from preserved first-attempt evidence')
    if (previous.get('complete') is not False or previous.get('exit_code') != 124
            or previous.get('native', {}).get('exit_code') != 124
            or previous.get('operational_interruption') is not False
            or previous.get('exception_categories')
            or type(previous_guard.get('recovery_count', 0)) is not int
            or previous_guard.get('recovery_count', 0) != 0
            or type(receipt.get('maximum_retries')) is not int
            or receipt.get('maximum_retries') != 1):
        raise FileExistsError('Only one reviewed native timeout may be retried')
    original_timeout = receipt.get('original_timeout_seconds')
    effective_timeout = receipt.get('effective_timeout_seconds')
    native_job = receipt.get('native_job', {})
    required = ('cluster_id', 'proc_id', 'job_start_date', 'max_runtime_seconds')
    if (type(original_timeout) is not int or type(effective_timeout) is not int
            or original_timeout != request.get('native_timeout_seconds', 900)
            or not 0 < original_timeout < effective_timeout
            or any(type(native_job.get(name)) is not int for name in required)
            or native_job['cluster_id'] <= 0 or native_job['proc_id'] < 0
            or native_job['job_start_date'] <= 0
            or native_job['max_runtime_seconds'] <= request.get('job_max_runtime_seconds', 3600)):
        raise ValueError('Timeout recovery requires explicit increased CPU and native runtime bounds')
    review = receipt.get('review', {})
    if not isinstance(review, dict) or not isinstance(review.get('reason'), str) or not review['reason'].strip():
        raise ValueError('Timeout recovery lacks its explicit review reason')
    ad = Path(os.environ['_CONDOR_JOB_AD']).read_text()
    for attribute, name in (('ClusterId', 'cluster_id'), ('ProcId', 'proc_id'),
                            ('JobStartDate', 'job_start_date'), ('MaxRuntime', 'max_runtime_seconds')):
        matches = re.findall(r'^\s*' + attribute + r'\s*=\s*([0-9]+)\s*$', ad, re.MULTILINE | re.IGNORECASE)
        if len(matches) != 1:
            raise ValueError('Timeout recovery lacks exact native job attributes')
        actual = int(matches[0])
        if ((name == 'max_runtime_seconds' and actual < native_job[name])
                or (name != 'max_runtime_seconds' and actual != native_job[name])):
            raise ValueError('Timeout recovery does not match the executing original native job')
    elapsed = time.time() - native_job['job_start_date']
    if elapsed < 0 or elapsed + effective_timeout + 300 >= native_job['max_runtime_seconds']:
        raise FileExistsError('Original native submission clock cannot safely cover timeout recovery')
    from shift_data_storage_claims import reserve
    claim = reserve('retry', request, request_sha256)
    return dict(action='retry_reviewed_timeout', recovery_count=1,
                previous_archive_sha256=pointer['archive_sha256'],
                timeout_recovery=dict(path=str(receipt_path.resolve()), sha256=receipt_sha256,
                                      original_timeout_seconds=original_timeout,
                                      effective_timeout_seconds=effective_timeout,
                                      native_job=native_job, review=review,
                                      storage_claim=str(claim), new_independent_exposure=False))


def begin_attempt(persistent, work, request, request_sha256, expected, audit,
                  timeout_recovery=None, timeout_recovery_sha256=None):
    """Adopt verified completed work, or permit one known interrupted replay."""
    persistent, work = Path(persistent), Path(work)
    if (timeout_recovery is None) != (timeout_recovery_sha256 is None):
        raise ValueError('Timeout recovery requires both the receipt and its SHA-256')
    guard = persistent / 'attempt_started.json'
    if not guard.exists():
        if timeout_recovery is not None:
            raise FileExistsError('Timeout recovery requires a preserved original attempt')
        return dict(action='new', recovery_count=0)
    previous_guard = json.loads(guard.read_text())
    if previous_guard.get('request_sha256') != request_sha256:
        raise ValueError('Previous attempt belongs to a different frozen request')
    pointer_path = persistent / 'previous_attempt.json'
    if not pointer_path.is_file():
        raise FileExistsError('Previous attempt lacks preserved output evidence; hold for review')
    pointer = json.loads(pointer_path.read_text())
    snapshot = Path(pointer['directory'])
    if sha256(snapshot / 'return_payload.tar.gz') != pointer['archive_sha256']:
        raise ValueError('Preserved previous output archive changed')
    previous = json.loads((snapshot / 'result.json').read_text())
    if previous.get('request_sha256') != request_sha256:
        raise ValueError('Previous producer receipt differs from the current request')
    if previous.get('native', {}).get('exit_code') == 0:
        root = restore_root(snapshot, work, request)
        if root is not None:
            if previous.get('output') and (sha256(root) != previous['output']['sha256']
                                          or root.stat().st_size != previous['output']['bytes']):
                raise ValueError('Completed previous output changed; do not replay')
            try:
                identity = audit(root, expected)
            except Exception:
                if previous.get('complete'):
                    raise
            else:
                adopted = dict(previous)
                adopted.update(complete=True, exit_code=0, identity_audit=identity,
                               output=dict(name=root.name, bytes=root.stat().st_size,
                                           sha256=sha256(root), root_uuid=identity['root_uuid']))
                adopted['recovery'] = dict(action='adopted_verified_previous_output',
                                           previous_archive_sha256=pointer['archive_sha256'],
                                           new_independent_exposure=False)
                return dict(action='adopt', result=adopted)
    if timeout_recovery is not None:
        return reviewed_timeout_recovery(persistent, request, request_sha256, snapshot,
                                         pointer, previous_guard, previous,
                                         timeout_recovery, timeout_recovery_sha256)
    count = previous_guard.get('recovery_count', 0)
    if (previous.get('operational_interruption') is not True
            or count >= request.get('operational_retry_limit', 0)):
        raise FileExistsError('Unknown or exhausted previous failure; preserve and hold')
    elapsed = time.time() - previous_guard['started_utc']
    if elapsed + request.get('native_timeout_seconds', 900) + 300 >= request.get('job_max_runtime_seconds', 3600):
        raise FileExistsError('Original submission clock cannot safely cover a retry')
    return dict(action='retry_interrupted', recovery_count=count + 1,
                previous_archive_sha256=pointer['archive_sha256'])


if __name__ == '__main__':
    import sys
    snapshot_previous(Path(sys.argv[1]), Path(sys.argv[2]))
