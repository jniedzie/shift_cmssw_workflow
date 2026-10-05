#!/usr/bin/env python3
"""One frozen DAG node: audited generation, sizing, quota, or completion.

GEN only. Detector production and deletion never become implicitly authorized
by this controller. Receipts bind each exact job and artifact to the manifest.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid

from shift_production_plan import validate_plan
from generation_publication import sha256
from collect_generation_metadata import combine


def run(command, **kwargs):
    return subprocess.run(command, check=True, text=True, **kwargs)


def url(path):
    return 'root://eosuser.cern.ch/' + str(path)


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name+'.partial.'+uuid.uuid4().hex)
    temporary.write_text(json.dumps(value, indent=2)+'\n')
    os.replace(temporary, path)


def remote_exists(path):
    result = subprocess.run(['xrdfs','eosuser.cern.ch','stat',str(path)], text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode == 0:
        return True
    if 'No such file or directory' in result.stderr or '[3011]' in result.stderr:
        return False
    raise ValueError('Unknown EOS publication state: '+result.stderr)


def remote_info(path):
    # EOS FUSE can retain negative stat-cache entries after XRootD creates a
    # file. Read back through the same authoritative protocol used to publish.
    with tempfile.TemporaryDirectory(prefix='shift_publication_readback_') as tmp:
        local = Path(tmp)/'artifact'
        run(['xrdcp','--silent',url(path),str(local)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return dict(bytes=local.stat().st_size, sha256=sha256(local))


def publish(source, target):
    """No overwrite. An interrupted transfer remains explicitly partial."""
    source, target = Path(source), Path(target)
    expected = dict(bytes=source.stat().st_size, sha256=sha256(source))
    if remote_exists(target):
        if expected != remote_info(target):
            raise ValueError('Refusing different existing artifact: '+str(target))
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = str(target)+'.partial.'+uuid.uuid4().hex
    run(['xrdcp', '--silent', '--cksum', 'adler32', str(source), url(temporary)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if expected != remote_info(temporary):
        raise ValueError('Published artifact differs from audited local bytes')
    if remote_exists(target):
        raise ValueError('Concurrent publication: '+str(target))
    run(['xrdfs', 'eosuser.cern.ch', 'mv', temporary, str(target)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def quota(manifest):
    text = run(['eos', '-b', 'root://eoshome-j.cern.ch', 'quota', 'ls', '-m',
                '/eos/user/j/jniedzie'], stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout
    rows = [dict(re.findall(r'(\w+)=([^\s]+)', line)) for line in text.splitlines()]
    row = next((r for r in rows if r.get('space') == '/eos/user/j/jniedzie/'), None)
    if row is None:
        raise ValueError('EOS quota is unknown')
    free_bytes = int(row['maxlogicalbytes'])-int(row['usedlogicalbytes'])
    free_files = int(row['maxfiles'])-int(row['usedfiles'])
    reservation = (manifest['plan']['max_workers']*manifest['estimated_peak_bytes_per_worker']
                   + manifest['minimum_free_bytes'])
    if free_bytes < reservation or free_files < manifest['minimum_free_files']:
        raise ValueError(f'Insufficient EOS headroom: {free_bytes} bytes, {free_files} files; require {reservation} bytes')
    return dict(free_bytes=free_bytes, free_files=free_files, reservation_bytes=reservation)


def receipt_path(manifest, index, chunk=None):
    label = manifest['plan']['strata'][index]['id']
    return Path(manifest['receipt_base'])/'receipts'/label/('pilot.json' if chunk is None else f'part{chunk:05d}.json')


def validate_receipt(path, manifest_sha, expected_events=None):
    record = json.loads(Path(path).read_text())
    if record.get('manifest_sha256') != manifest_sha or record.get('complete') is not True:
        raise ValueError('Missing or mismatched completion receipt: '+str(path))
    if expected_events is not None and record['requested_events'] != expected_events:
        raise ValueError('Wrong requested event count')
    if not 0 < record['events'] <= record['requested_events']:
        raise ValueError('Invalid actual generated event count')
    if len(record['event_ids']) != record['events'] or len({tuple(x) for x in record['event_ids']}) != record['events']:
        raise ValueError('Missing or duplicate generated event identities')
    if not record['artifacts'] or not any(x['path'].endswith('.root') for x in record['artifacts']):
        raise ValueError('No audited ROOT artifact')
    for artifact in record['artifacts']:
        path = Path(artifact['path'])
        info = remote_info(path) if str(path).startswith('/eos/') else dict(bytes=path.stat().st_size,sha256=sha256(path))
        if path.is_symlink() or info['bytes'] != artifact['bytes'] or info['sha256'] != artifact['sha256']:
            raise ValueError('Artifact changed after semantic audit: '+str(path))
    return record


def rows(plan):
    return [(i, chunk) for chunk in range(max(s['jobs'] for s in plan['strata']))
            for i, s in enumerate(plan['strata']) if chunk < s['jobs']]


def mpi_command(root, stratum, chunk, events, local_campaign):
    process = ('QCD' if stratum['sample'] == 'qcd' else 'Charmonium')+'_SoftMpiPartition_FixedTarget_13p6TeV'
    env = os.environ.copy()
    env.update(PROCESS=process, SAMPLE_NAME=stratum['sample'],
               SAMPLE_BASE=str(local_campaign.parent.parent), CAMPAIGN_NAME=local_campaign.name,
               SAMPLE_DIR=str(local_campaign), N_EVENTS=str(events), N_JOBS=str(stratum['jobs']),
               GEN_PTHAT_MIN=str(stratum['bounds'][0]), GEN_PTHAT_MAX=str(stratum['bounds'][1]),
               GEN_EVENT_CLASS='qcd' if stratum['sample'] == 'qcd' else 'direct_jpsi',
               GEN_EVENT_RUN_OFFSET=str(stratum['run_offset']),
               GENERATOR_SEED=str(stratum['seed_base']), SIMULATION_SEED=str(stratum['seed_base']+1000000),
               WORKFLOW_LOCAL_GENERATOR='1', CMSSW_PREPARED='1', STEP1_GENERATION_ONLY='1',
               CLEANUP_PREVIOUS_STEP='0', PILEUP_MODE='none', TRIGGER_SCENARIO='none',
               TRIGGER_TIMELINE_MODE='none', PIGGYBACK_FILTER_RECONSTRUCTION='0',
               SHIFT_LSS_MATERIAL_MODE='none', SHIFT_LSS_FIELD_MODE='none',
               COLLISION_YEAR='2023', WORKDIR=str(local_campaign/'work'))
    return ['bash', str(root/'workflow/run_step1_generation.sh'), str(chunk), str(events)], env


def children(command, log, env=None):
    """Signal the owned group, then reap it outside Python's signal handler.

    Popen.wait holds a non-reentrant waitpid lock. Calling wait again from a
    signal handler can deadlock, so handlers only record and send the signal.
    """
    with Path(log).open('w') as out:
        child = subprocess.Popen(command, env=env, stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
        stopping = [0, None]
        def stop(signum, frame):
            if not stopping[0]:
                stopping[:] = [signum, time.monotonic()+30]
                try:
                    os.killpg(child.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        old = {sig: signal.signal(sig, stop) for sig in (signal.SIGTERM, signal.SIGINT)}
        try:
            while child.poll() is None:
                if stopping[0] and time.monotonic() >= stopping[1]:
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                time.sleep(0.05)
            code = child.wait()
            if stopping[0]:
                # A shell may exit before its descendants; ensure the entire
                # owned process group has stopped before releasing the lock.
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                raise SystemExit(128+stopping[0])
            if code:
                raise subprocess.CalledProcessError(code, command)
        finally:
            for sig, handler in old.items():
                signal.signal(sig, handler)


def worker(manifest, manifest_sha, index, chunk, pilot):
    root = Path(os.environ['SHIFT_DAG_LOCAL_ROOT'])
    stratum = manifest['plan']['strata'][index]
    if not 0 <= chunk < stratum['jobs'] and not (pilot and chunk == 99999):
        raise ValueError('Invalid chunk index')
    events = (stratum['pilot_events'] if pilot else
              min(stratum['events_per_job'], stratum['target_events']-chunk*stratum['events_per_job']))
    receipt = receipt_path(manifest, index, None if pilot else chunk)
    if receipt.exists():
        record = validate_receipt(receipt, manifest_sha, events)
        if (record['stratum'], record['chunk'], record['pilot']) != (stratum['id'], chunk, pilot):
            raise ValueError('Wrong receipt identity')
        print('Already validated:', stratum['id'], chunk, flush=True)
        return
    lock = receipt.with_suffix('.lock')
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.mkdir()  # Never steal a stale lock automatically.
    local = root/'jobs'/stratum['sample']/stratum['id']
    start = time.monotonic()
    try:
        local.mkdir(parents=True, exist_ok=False)
        if stratum['adapter'] == 'soft_mpi_gen':
            command, env = mpi_command(root, stratum, chunk, events, local)
            children(command, local/'worker.log', env)
            metadata_file = local/'generation_metadata'/f'part{chunk:04d}.json'
            metadata = json.loads(metadata_file.read_text())
            ids, count = metadata['event_ids'], metadata['events']
        elif stratum['adapter'] == 'dy_mass_gen':
            scripts = root/'workflow/scripts'
            command = ['cmsRun', str(scripts/'fixed_target_gen_cfg.py'), 'sample=dy',
                       f'lower={stratum["bounds"][0]}', f'upper={stratum["bounds"][1]}',
                       f'maxEvents={events}', f'seed={stratum["seed_base"]+chunk}',
                       f'runNumber={stratum["run_offset"]+chunk+1}', f'outputDir={local}']
            children(command, local/'cmsRun.log')
            children([sys.executable, str(scripts/'audit_fixed_target_gen.py'), str(local)], local/'audit.log')
            metadata = json.loads((local/'validation.json').read_text())
            count = metadata['counts']['events']
            from DataFormats.FWLite import Events
            ids = [[int(e.eventAuxiliary().run()), int(e.eventAuxiliary().luminosityBlock()), int(e.eventAuxiliary().event())]
                   for e in Events(str(local/'gen.root'))]
        else:
            raise ValueError('Unsupported generation adapter')
        if len({tuple(x) for x in ids}) != count:
            raise ValueError('Invalid semantic audit identities')
        artifacts = []
        target = Path(stratum['campaign'])/('pilot' if pilot else f'chunks/part{chunk:05d}')
        for source in sorted(p for p in local.rglob('*') if p.is_file()):
            if 'work' in source.relative_to(local).parts:
                continue
            destination = target/source.relative_to(local)
            publish(source, destination)
            artifacts.append(dict(path=str(destination), bytes=source.stat().st_size, sha256=sha256(source)))
        result = dict(schema='shift-dag-receipt-v1', complete=True, manifest_sha256=manifest_sha,
                      stratum=stratum['id'], chunk=chunk, pilot=pilot, requested_events=events,
                      events=count, event_ids=ids, semantic_audit=metadata,
                      wall_seconds=time.monotonic()-start, artifacts=artifacts,
                      physics_valid=False, normalization_ready=False)
        save(receipt, result)
        validate_receipt(receipt, manifest_sha, events)
        print('Validated:', stratum['id'], chunk, count, 'events', flush=True)
    except BaseException as error:
        save(receipt.with_suffix('.failure.json'), dict(manifest_sha256=manifest_sha,
             error=str(error), stratum=stratum['id'], chunk=chunk, pilot=pilot))
        # Preserve the bounded transcript even when cmsRun or validation failed.
        for transcript in local.glob('*.log'):
            destination = lock.parent/'failures'/(lock.stem+'_'+transcript.name+'.'+uuid.uuid4().hex)
            publish(transcript, destination)
        raise
    finally:
        lock.rmdir()


def control(manifest, manifest_sha, mode, item):
    plan = manifest['plan']
    base = Path(manifest['receipt_base'])
    report = dict(manifest_sha256=manifest_sha, mode=mode, quota=quota(manifest))
    if mode == 'preflight':
        if plan['stage'] != 'GEN' or plan['detector_production_ready']:
            raise ValueError('This frozen controller supports audited GEN production only')
        if not Path('/eos/user/j/jniedzie').is_dir():
            raise ValueError('Required EOS namespace is unavailable')
    elif mode == 'sizing':
        estimates = []
        for index, stratum in enumerate(plan['strata']):
            pilot = validate_receipt(receipt_path(manifest, index), manifest_sha, stratum['pilot_events'])
            estimate = pilot['wall_seconds']/pilot['events']*stratum['events_per_job']
            if estimate > 0.6*50400:
                raise ValueError(f'{stratum["id"]} needs smaller chunks: projected {estimate:.0f} seconds/job')
            estimates.append(dict(stratum=stratum['id'], projected_seconds_per_job=estimate,
                                  projected_cpu_hours=pilot['wall_seconds']/pilot['events']*stratum['target_events']/3600))
        report['resource_estimates'] = estimates
    elif mode == 'batch':
        batch = int(item)
        previous = rows(plan)[max(0, (batch-1)*manifest['batch_size']):batch*manifest['batch_size']]
        for index, chunk in previous:
            validate_receipt(receipt_path(manifest, index, chunk), manifest_sha)
        report['validated_previous_batch_chunks'] = len(previous)
    elif mode == 'complete':
        seen = set()
        totals, normalizations = {}, {}
        for index, stratum in enumerate(plan['strata']):
            records = [validate_receipt(receipt_path(manifest, index, chunk), manifest_sha,
                       min(stratum['events_per_job'], stratum['target_events']-chunk*stratum['events_per_job']))
                       for chunk in range(stratum['jobs'])]
            for record in records:
                for identity in record['event_ids']:
                    identity = tuple(identity)
                    if identity in seen:
                        raise ValueError('Duplicate event identity across the production suite')
                    seen.add(identity)
            totals[stratum['id']] = sum(r['events'] for r in records)
            if stratum['adapter'] == 'soft_mpi_gen':
                normalizations[stratum['id']] = combine([r['semantic_audit'] for r in records], stratum['jobs'])
            else:
                n = totals[stratum['id']]
                estimates = [(r['events'], r['semantic_audit']['run_info'][0]) for r in records]
                normalizations[stratum['id']] = dict(events=n,
                    cross_section_pb=sum(k*x['internal_xsec_pb'] for k,x in estimates)/n,
                    error_pb=math.sqrt(sum((k*x['error_pb'])**2 for k,x in estimates))/n,
                    normalization_ready=False, physics_valid=False)
        report.update(complete=True, full_partition_complete='included_strata' not in plan,
                      totals=totals, normalization_inputs=normalizations,
                      normalization_ready=False, physics_valid=False,
                      remaining_gates=plan['normalization_gates']+plan['detector_gates'])
    else:
        raise ValueError('Unsupported control mode')
    save(base/'control'/f'{mode}_{item}.json', report)
    print('Validated controller node:', mode, item, flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('manifest', type=Path)
    p.add_argument('mode', choices=('preflight','pilot','sizing','batch','production','complete'))
    p.add_argument('item')
    args = p.parse_args()
    manifest = json.loads(args.manifest.read_text())
    if manifest['schema'] != 'shift-dag-manifest-v1':
        raise ValueError('Unsupported manifest')
    validate_plan(manifest['plan'])
    manifest_sha = sha256(args.manifest)
    for name in ('CMSSW_BASE', 'PYTHONPATH', 'CMSSW_SEARCH_PATH', 'LD_LIBRARY_PATH'):
        if any(path.startswith('/afs/') for path in os.environ.get(name, '').split(':')):
            raise ValueError('AFS runtime dependency: '+name)
    if args.mode == 'pilot':
        worker(manifest, manifest_sha, int(args.item), 99999, True)
    elif args.mode == 'production':
        index, chunk = (int(x) for x in args.item.split(':'))
        worker(manifest, manifest_sha, index, chunk, False)
    else:
        control(manifest, manifest_sha, args.mode, args.item)


if __name__ == '__main__':
    main()
