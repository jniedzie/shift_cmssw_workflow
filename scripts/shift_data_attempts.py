# Code style: AI-code.
"""AI-generated preservation and bounded recovery for one owned data shard."""
import hashlib
import json
from pathlib import Path
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


def restore_root(snapshot, work):
    destination = Path(work) / 'restricted_nano.root'
    with tarfile.open(Path(snapshot) / 'return_payload.tar.gz') as archive:
        try:
            member = archive.getmember('restricted_nano.root')
        except KeyError:
            return None
        if not member.isfile() or member.size > 100 * 1024**2:
            raise ValueError('Unsafe previous ROOT archive member')
        with archive.extractfile(member) as source, destination.open('wb') as target:
            shutil.copyfileobj(source, target)
    return destination


def begin_attempt(persistent, work, request, request_sha256, expected, audit):
    """Adopt verified completed work, or permit one known interrupted replay."""
    persistent, work = Path(persistent), Path(work)
    guard = persistent / 'attempt_started.json'
    if not guard.exists():
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
        root = restore_root(snapshot, work)
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
