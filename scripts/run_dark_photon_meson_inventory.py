#!/usr/bin/env python3
# Code style: AI-code. AI-generated bounded diagnostic census worker.
"""Run an immutable meson census using the qualified worker-local GEN runtime.

Only compact diagnostic JSON, status and bounded logs return to the persistent
working directory. This worker neither submits jobs nor publishes physics data.
"""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import resource
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time

sys.dont_write_bytecode = True


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value,indent=2,sort_keys=True)+'\n')
    temporary.replace(path)


def pin(authority):
    path = Path(authority['path'])
    if not path.is_absolute() or sha(path) != authority['sha256']:
        raise ValueError('Changed frozen census input: '+str(path))
    return path


def bounded_log(source, target, maximum_bytes=131072):
    size = source.stat().st_size
    with source.open('rb') as stream:
        first = stream.read(maximum_bytes//2 if size>maximum_bytes else maximum_bytes)
        if size>maximum_bytes:
            stream.seek(-maximum_bytes//2,os.SEEK_END)
            first += b'\n[intermediate log bytes omitted; diagnostic output bound]\n'+stream.read()
    target.write_bytes(first)


def run(request_path, expected):
    if sha(request_path) != expected:
        raise ValueError('The immutable census request changed')
    request = json.loads(request_path.read_text())
    directory = Path(request['directory'])
    if (request['schema'] != 'shift-dp-parent-census-request-v1' or not directory.is_absolute()
            or directory != request_path.parent or directory != Path.cwd()
            or str(directory).startswith(('/tmp/','/pool/')) or sha(__file__) != request['worker_sha256']
            or type(request['source_collisions']) is not int or not 2<=request['source_collisions']<=100000
            or type(request['seed']) is not int or not 1<=request['seed']<=899999800
            or not 0<request['source_timeout_seconds']<request['worker_timeout_seconds']<=5400
            or not 0<request['maximum_result_bytes']<=4*1024*1024):
        raise ValueError('Invalid frozen census worker identity, exposure or persistent working directory')
    model, tools_archive = pin(request['model']), pin(request['tools_archive'])
    runtime_provenance = pin(request['gen_runtime']['provenance'])
    status_path = directory/'status.json'
    with (directory/'worker.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if status_path.exists():
            raise RuntimeError('This census worker already started; preserve the original for reviewed recovery')
        status = dict(schema='shift-dp-parent-census-status-v1',complete=False,request_sha256=expected,
            started_utc=datetime.now(timezone.utc).isoformat(),seed=request['seed'],
            requested_source_collisions=request['source_collisions'],no_scheduler_mutations=True,
            no_canonical_data_writes=True)
        save(status_path,status)
        started = time.monotonic()
        try:
            parent = os.environ.get('_CONDOR_SCRATCH_DIR','/tmp')
            with tempfile.TemporaryDirectory(prefix='dp-parent-census-',dir=parent) as temporary:
                scratch = Path(temporary)
                tools = scratch/'tools'
                tools.mkdir()
                with tarfile.open(tools_archive) as archive:
                    members = archive.getmembers()
                    if any(not item.isfile() or Path(item.name).name != item.name or not item.name.endswith('.py')
                           for item in members):
                        raise ValueError('Unsafe or unexpected census tools archive')
                    archive.extractall(tools)
                sys.path.insert(0,str(tools))
                import run_dark_photon_meson_production as production
                configuration = dict(production_mechanism='meson-radiative',
                    gen_runtime=dict(request['gen_runtime'],provenance=dict(
                        path=str(runtime_provenance),sha256=sha(runtime_provenance))))
                release, runtime_archive = production.unpack_gen_runtime(configuration,scratch)
                if runtime_archive is None:
                    raise ValueError('Census requires its actual qualified worker-local runtime')
                inventory, coverage = scratch/'source_inventory.json', scratch/'coverage.json'
                argv = ['python3',str(tools/'audit_dark_photon_meson_coverage.py'),
                    '--particle-data','PYTHIA_PARTICLE_DATA', '--model',str(model),
                    '--masses',*[str(mass) for mass in request['masses_gev']],
                    '--source-collisions',str(request['source_collisions']),'--seed',str(request['seed']),
                    '--timeout',str(request['source_timeout_seconds']),'--examples-per-species','1',
                    '--heartbeat',str(directory/'heartbeat.json'),
                    '--sampled-inventory-output',str(inventory),'--output',str(coverage)]
                shell = ('export SCRAM_ARCH=el9_amd64_gcc13; source /cvmfs/cms.cern.ch/cmsset_default.sh && cd '+
                    shlex.quote(str(release/'src'))+' && scram b ProjectRename && eval "$(scram runtime -sh)" && cd '+
                    shlex.quote(str(scratch))+' && export PYTHONDONTWRITEBYTECODE=1 && exec '+
                    shlex.join(argv).replace('PYTHIA_PARTICLE_DATA','"$PYTHIA8DATA/ParticleData.xml"'))
                log = scratch/'native.log'
                try:
                    with log.open('xb') as output:
                        subprocess.run(['/bin/bash','-c',shell],cwd=directory,stdout=output,
                            stderr=subprocess.STDOUT,env=production.durable.native_env(),
                            timeout=request['worker_timeout_seconds'],check=True)
                finally:
                    if log.is_file():
                        bounded_log(log,directory/'native.log')
                result = json.loads(inventory.read_text())
                if (result['collision_exposure'] != request['source_collisions'] or result['seed'] != request['seed']
                        or result['native']['source_collisions'] != request['source_collisions']
                        or result['model_sha256'] != request['model']['sha256']):
                    raise ValueError('Returned census differs from its fixed source authority')
                files = [inventory,coverage]
                if sum(path.stat().st_size for path in files)>request['maximum_result_bytes']:
                    raise ValueError('Compact census result exceeds its declared persistent-storage budget')
                for path in files:
                    destination = directory/path.name
                    shutil.copy2(path,destination)
                    if sha(destination)!=sha(path):
                        raise ValueError('Census result readback differs')
                status.update(complete=True,source_collisions=result['collision_exposure'],
                    source_record_stream_sha256=result['source_record_stream_sha256'],
                    result_sha256={str(directory/path.name):sha(path) for path in files},
                    result_bytes=sum(path.stat().st_size for path in files))
        except Exception as error:
            status['error'] = repr(error)
            raise
        finally:
            status.update(checked_utc=datetime.now(timezone.utc).isoformat(),
                wall_seconds=time.monotonic()-started,
                maximum_child_rss_kib=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)
            save(status_path,status)
    print(json.dumps(status),flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request',type=Path,required=True)
    parser.add_argument('--sha256',required=True)
    args = parser.parse_args()
    run(args.request,args.sha256)
