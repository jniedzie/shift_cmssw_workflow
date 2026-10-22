#!/usr/bin/env python3
# Code style: AI-code. AI-generated immutable worker-local detector runtime deployment.
"""Enter a pinned detector capsule without reading mutable shared CMSSW libraries."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile


SCHEMA = 'shift-predefined-signal-detector-runtime-v1'
CORE_SOURCES = ('run_shift_gen_to_nano.py', 'run_dark_photon_detector.py')


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            digest.update(block)
    return digest.hexdigest()


def validate_profile(profile):
    if (not isinstance(profile, dict) or profile.get('schema') != SCHEMA
            or profile.get('transport_predefined_signal_decays') is not True
            or profile.get('cmssw_relative') != 'CMSSW_17_0_0_pre4'
            or profile.get('scram_arch') != 'el9_amd64_gcc13'
            or not isinstance(profile.get('runtime_url'), str)
            or not profile['runtime_url'].startswith('root://eosuser.cern.ch//eos/user/j/jniedzie/shift_cmssw_support/')
            or type(profile.get('runtime_bytes')) is not int or not 0 < profile['runtime_bytes'] <= 100*1024**2):
        raise ValueError('Require the explicit pinned predefined-signal detector runtime profile')
    digest = profile.get('runtime_sha256', '')
    if len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
        raise ValueError('Detector capsule SHA-256 is invalid')
    authority = profile.get('manifest', {})
    path = Path(authority.get('path', ''))
    if not path.is_absolute() or not path.is_file() or sha256(path) != authority.get('sha256'):
        raise ValueError('Pinned detector capsule manifest changed or is missing')
    manifest = json.loads(path.read_text())
    if (manifest.get('schema') != 'shift-kaon-complete-private-detector-capsule-v2'
            or manifest.get('default_configs_unchanged') is not True
            or manifest.get('explicit_optin_point_only') is not True
            or manifest.get('private_geometry_payloads_included') is not False):
        raise ValueError('Detector capsule lacks the qualified unchanged-default contract')
    for name in CORE_SOURCES:
        row = manifest.get('files', {}).get('workflow_helpers/' + name)
        if not isinstance(row, dict) or not isinstance(row.get('sha256'), str):
            raise ValueError('Detector capsule does not pin its qualified core source: ' + name)
    return manifest


def clean_environment():
    environment = dict(os.environ)
    for name in ('LD_LIBRARY_PATH', 'LD_PRELOAD', 'PYTHONPATH', 'PYTHONHOME', 'ROOTSYS',
                 'CONDA_PREFIX', 'CMSSW_BASE', 'CMSSW_RELEASE_BASE', 'CMSSW_SEARCH_PATH'):
        environment.pop(name, None)
    environment.update(PATH='/usr/bin:/bin', PYTHONDONTWRITEBYTECODE='1', SCRAM_ARCH='el9_amd64_gcc13')
    return environment


def validate_cached_files(cache, profile, manifest):
    if sha256(cache / 'manifest.json') != profile['manifest']['sha256']:
        raise ValueError('Worker-local detector manifest changed')
    # These six SCRAM files are the measured ProjectRename outputs. All source,
    # Python, templates and DSOs stay byte-identical and are checked on every use.
    relocated = {profile['cmssw_relative'] + '/.SCRAM/' + profile['scram_arch'] + '/' + name
                 for name in ('RuntimeCache.json', 'DirCache.json', 'tools/self',
                              'MakeData/variables.mk', 'MakeData/Tools.mk', 'MakeData/Tools/self.mk')}
    for name, row in manifest['files'].items():
        if name in relocated:
            continue
        path = cache / name
        if path.stat().st_size != row['bytes'] or sha256(path) != row['sha256']:
            raise ValueError('Worker-local detector runtime member changed: ' + name)


def deploy(profile, manifest):
    cache = Path('/tmp') / ('shift_detector_' + str(os.getuid()) + '_' + profile['runtime_sha256'][:16])
    with Path(str(cache) + '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if (cache / 'READY').is_file():
            if (cache / 'READY').read_text().strip() != profile['runtime_sha256']:
                raise ValueError('Worker-local detector cache identity changed')
            validate_cached_files(cache, profile, manifest)
            return cache
        if cache.exists():
            raise FileExistsError('Preserve incomplete worker-local detector cache: ' + str(cache))
        stage = Path(tempfile.mkdtemp(prefix=cache.name + '.partial.', dir='/tmp'))
        try:
            archive = stage / 'runtime.tar.gz'
            subprocess.run(['/usr/bin/xrdcp', '--silent', '--cksum', 'adler32', profile['runtime_url'], str(archive)],
                           env=clean_environment(), check=True, timeout=900)
            if archive.stat().st_size != profile['runtime_bytes'] or sha256(archive) != profile['runtime_sha256']:
                raise ValueError('Downloaded detector capsule changed')
            with tarfile.open(archive) as source:
                for item in source:
                    if (item.name.startswith('/') or '..' in Path(item.name).parts
                            or item.isdev() or (item.issym() and item.linkname.startswith('/afs/'))):
                        raise ValueError('Unsafe detector capsule member: ' + item.name)
                source.extractall(stage, filter='fully_trusted')
            archive.unlink()
            if sha256(stage / 'manifest.json') != profile['manifest']['sha256']:
                raise ValueError('Extracted detector manifest differs')
            for name, row in manifest['files'].items():
                path = stage / name
                if path.stat().st_size != row['bytes'] or sha256(path) != row['sha256']:
                    raise ValueError('Extracted detector runtime member changed: ' + name)
            # SCRAM records the final cache name, so relocate only after the atomic rename.
            stage.rename(cache)
            release = cache / profile['cmssw_relative']
            command = 'source /cvmfs/cms.cern.ch/cmsset_default.sh && cd ' + shlex.quote(str(release / 'src')) + ' && scram b ProjectRename'
            result = subprocess.run(['/bin/bash', '-c', command], env=clean_environment(),
                                    capture_output=True, text=True, timeout=180)
            (cache / 'relocation.log').write_text(result.stdout + result.stderr)
            if result.returncode:
                raise RuntimeError('Detector capsule relocation failed; preserve ' + str(cache / 'relocation.log'))
            validate_cached_files(cache, profile, manifest)
            (cache / 'READY').write_text(profile['runtime_sha256'] + '\n')
        finally:
            if stage.exists():
                shutil.rmtree(stage)
    return cache


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--entry', type=Path, required=True)
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    profile = json.loads(args.profile.read_text())
    manifest = validate_profile(profile)
    cache = deploy(profile, manifest)
    release = cache / profile['cmssw_relative']
    arguments = args.arguments[1:] if args.arguments[:1] == ['--'] else args.arguments
    # Native CMS Python may leave PYTHONPATH unset; its path hook supplies CMSSW modules.
    code = "import os,sys; os.environ['LD_LIBRARY_PATH']=':'.join(p for p in os.environ.get('LD_LIBRARY_PATH','').split(':') if '/biglib/' not in p); assert all('/afs/' not in os.environ.get(k,'') for k in ['LD_LIBRARY_PATH','PYTHONPATH','CMSSW_SEARCH_PATH']); os.execv(sys.executable,[sys.executable]+sys.argv[1:])"
    invocation = ['python3', '-c', code, str(args.entry.resolve()), *arguments]
    command = ('source /cvmfs/cms.cern.ch/cmsset_default.sh && cd ' + shlex.quote(str(release / 'src'))
               + ' && eval "$(scram runtime -sh)" && cd ' + shlex.quote(str(Path.cwd()))
               + ' && exec ' + shlex.join(invocation))
    os.execve('/bin/bash', ['/bin/bash', '-c', command], clean_environment())


if __name__ == '__main__':
    main()
