# Code style: AI-code. AI-generated singleton reconstruction probe worker.
"""Run one frozen JOB contract; submission and physics checks belong to its owner."""

from pathlib import Path
import hashlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import time


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, data):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, indent=2) + '\n')
    temporary.replace(path)


def unpack_verified_archive(path, target):
    # The caller pins a trusted runtime archive by SHA; this is not a general
    # untrusted-archive extractor. Preserve the qualified runtime's links.
    with tarfile.open(path) as archive:
        for member in archive.getmembers():
            if Path(member.name).is_absolute() or '..' in Path(member.name).parts:
                raise ValueError('unsafe runtime member: ' + member.name)
        archive.extractall(target)


def failure_receipt(iwd, sandbox, job, phase, error, exit_code=1):
    """Record phase failures without replacing an existing native-run receipt."""
    iwd.mkdir(parents=True, exist_ok=True)
    if (iwd / 'run.json').exists():
        return
    now = time.time()
    bootstrap = iwd / 'bootstrap.json'
    output = iwd / 'nano.root'
    log = iwd / 'native.log'
    write_json(iwd / 'run.json', {
        'id': job.get('id'), 'events': job.get('events', []),
        'config_sha256': job.get('config_sha256'),
        'start_epoch': now, 'end_epoch': now, 'exit_code': exit_code or 1,
        # An escape during run finalization cannot prove zero processing.
        'error': str(error), 'phase': phase,
        'native_started': None if phase == 'run' else False,
        'host': socket.gethostname(), 'persistent_iwd': str(iwd),
        'worker_scratch': str(sandbox / 'native_runtime'), 'max_rss_kb': 0,
        'log': str(log), 'log_bytes': log.stat().st_size if log.exists() else 0,
        'log_budget_pass': False, 'output_exists': output.exists(),
        'output_bytes': output.stat().st_size if output.exists() else 0,
        'output_sha256': None,
        'worker_log_exists': (sandbox / 'native_runtime/native.log').exists(),
        'worker_output_exists': (sandbox / 'native_runtime/nano.root').exists(),
        'private_mapping_pass': False, 'mapped_private_libraries': {},
        'all_observed_worker_library_maps': {},
        'bootstrap': json.loads(bootstrap.read_text()) if bootstrap.exists() else {},
    })


def prepare(iwd, sandbox, job):
    iwd.mkdir(parents=True, exist_ok=True)
    os.chdir(iwd)
    scratch = sandbox / 'native_runtime'
    scratch.mkdir(exist_ok=False)
    write_json(iwd / 'bootstrap.json', {
        'phase': 'started', 'epoch': time.time(), 'host': socket.gethostname(),
        'persistent_iwd': str(iwd), 'scratch': str(scratch),
    })
    for kind in ('input', 'template', 'config'):
        if sha256(sandbox / job['transfer_' + kind]) != job[kind + '_sha256']:
            raise ValueError(kind + ' SHA256 mismatch')
    base = scratch / 'runtime.tar.gz'
    subprocess.run(['xrdcp', '--nopbar', job['base_uri'], str(base)], check=True)
    if sha256(base) != job['base_sha256']:
        raise ValueError('base runtime SHA256 mismatch')
    unpack_verified_archive(base, scratch)
    base.unlink()
    overlay = sandbox / 'native_overlay.tar.gz'
    if sha256(overlay) != job['overlay_sha256']:
        raise ValueError('overlay SHA256 mismatch')
    unpack_verified_archive(overlay, scratch)
    text = (sandbox / job['transfer_config']).read_text()
    replacements = [
        (job['template'], str(sandbox / job['transfer_template'])),
        (job['input'], str(sandbox / job['transfer_input'])),
        (job['output'], str(scratch / 'nano.root')),
    ]
    for before, after in replacements:
        if before not in text:
            raise ValueError('configuration relocation token missing: ' + before)
        text = text.replace(before, after)
    (scratch / 'nano_cfg.py').write_text(text)
    write_json(iwd / 'bootstrap.json', {
        'phase': 'prepared', 'epoch': time.time(), 'host': socket.gethostname(),
        'persistent_iwd': str(iwd), 'scratch': str(scratch),
        'worker_cfg_sha256': sha256(scratch / 'nano_cfg.py'),
        'base_sha256': job['base_sha256'], 'overlay_sha256': job['overlay_sha256'],
    })


def run(iwd, sandbox, job):
    os.chdir(iwd)
    scratch = sandbox / 'native_runtime'
    start = time.time()
    log = scratch / 'native.log'
    maps = {}
    peak = 0
    exit_code = 99
    error = None
    native_started = False
    try:
        with log.open('wb') as output:
            process = subprocess.Popen(
                ['cmsRun', str(scratch / 'nano_cfg.py')], stdout=output,
                stderr=subprocess.STDOUT, start_new_session=True)
            native_started = True
            while process.poll() is None:
                try:
                    for line in Path('/proc/%d/maps' % process.pid).read_text().splitlines():
                        path = line.split()[-1]
                        if (path.startswith('/') and '.so' in path and
                                (str(scratch) in path or
                                 any(name in path for name in job['required_libraries'])) and
                                path not in maps and Path(path).is_file()):
                            maps[path] = {'path': path, 'sha256': sha256(path)}
                    for line in Path('/proc/%d/status' % process.pid).read_text().splitlines():
                        if line.startswith('VmRSS:'):
                            peak = max(peak, int(line.split()[1]))
                except (OSError, ValueError):
                    pass
                if time.time() - start > 1200:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        # Reap the actual process before publishing failure;
                        # it must not outlive a released worker allocation.
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait()
                    raise RuntimeError('1200s whole-job guard')
                time.sleep(.5)
            exit_code = process.returncode
    except Exception as exception:
        error = str(exception)
    if log.exists():
        shutil.copyfile(log, iwd / 'native.log')
    output = iwd / 'nano.root'
    candidate = scratch / 'nano.root'
    if candidate.exists():
        shutil.copyfile(candidate, output)
    bindings = {}
    for row in maps.values():
        name = Path(row['path']).name
        if name in job['required_libraries']:
            bindings[name] = row
    mapping = all(name in bindings and bindings[name]['sha256'] == digest
                  for name, digest in job['required_libraries'].items())
    receipt = {
        'id': job['id'], 'events': job['events'],
        'config_sha256': job['config_sha256'],
        'start_epoch': start, 'end_epoch': time.time(),
        'exit_code': exit_code, 'error': error,
        'phase': 'native', 'native_started': native_started,
        'host': socket.gethostname(), 'persistent_iwd': str(iwd),
        'worker_scratch': str(scratch), 'max_rss_kb': peak,
        'log': str(iwd / 'native.log'),
        'log_bytes': (iwd / 'native.log').stat().st_size if (iwd / 'native.log').exists() else 0,
        'log_budget_pass': log.exists() and log.stat().st_size < 16 * 1024 * 1024,
        'output_exists': output.exists(),
        'output_bytes': output.stat().st_size if output.exists() else 0,
        'output_sha256': sha256(output) if output.exists() else None,
        'private_mapping_pass': mapping, 'mapped_private_libraries': bindings,
        'all_observed_worker_library_maps': maps,
        'bootstrap': json.loads((iwd / 'bootstrap.json').read_text()),
    }
    write_json(iwd / 'run.json', receipt)
    return 0 if (exit_code == 0 and mapping and output.exists() and
                 receipt['log_budget_pass']) else 1


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) < 3:
        raise SystemExit('usage: reco_probe_worker.py prepare|run|setup-failed IWD SANDBOX [code command]')
    phase, iwd, sandbox = argv[0], Path(argv[1]).resolve(), Path(argv[2]).resolve()
    job = {}
    try:
        job = json.loads((sandbox / 'job.json').read_text())
        if phase == 'prepare':
            prepare(iwd, sandbox, job)
            return 0
        if phase == 'run':
            return run(iwd, sandbox, job)
        if phase == 'setup-failed':
            code = int(argv[3]) if len(argv) > 3 else 1
            command = argv[4] if len(argv) > 4 else 'shell setup'
            failure_receipt(iwd, sandbox, job, 'setup', command, code)
            return code or 1
        raise ValueError('invalid worker phase: ' + phase)
    except Exception as exception:
        failure_receipt(iwd, sandbox, job, phase, exception)
        print(str(exception), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
