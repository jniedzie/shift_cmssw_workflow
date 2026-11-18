#!/usr/bin/env python3
# Code style: AI-code.
"""AI-generated bounded data worker; only event headers are inspected here."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import signal
import subprocess
import time
import xml.etree.ElementTree as ET

from shift_data_attempts import begin_attempt, snapshot_previous


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    temporary = path.with_suffix(path.suffix + '.new')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def validate_request(request):
    schema = request.get('schema')
    if schema not in ('shift-data-reconstruction-job-v1', 'shift-data-reconstruction-job-v2'):
        raise ValueError('Unknown data worker request schema')
    directory = Path(request['directory'])
    if not directory.is_absolute() or len(directory.parts) < 2 or directory.parts[1] not in ('afs', 'eos'):
        raise ValueError('Require an absolute persistent job directory')
    identities = request['events']
    if not 1 <= len(identities) <= 1000:
        raise ValueError('Data job must have between one and 1000 owned originals')
    rows = []
    for identity in identities:
        row = tuple(identity[name] for name in ('run', 'luminosityBlock', 'event'))
        if any(type(value) is not int or not 0 < value < 2**bits
               for value, bits in zip(row, (32, 32, 64))):
            raise ValueError('Invalid original data identity')
        rows.append(row)
    if len(set(rows)) != len(rows):
        raise ValueError('A worker cannot own duplicate originals')
    if type(request['source_offset']) is not int or request['source_offset'] < 0:
        raise ValueError('Invalid original source offset')
    if schema == 'shift-data-reconstruction-job-v1':
        if not str(request['batch_name']).startswith('DATA_Run2023D_ZeroBias_SHIFTReco_20261118_'):
            raise ValueError('Legacy data jobs must identify their qualified ZeroBias period')
    else:
        dataset = re.fullmatch(r'/([A-Za-z0-9_]+)/(Run2023D)-(v[0-9]+)/RAW',
                               request.get('dataset', ''))
        if not dataset or request.get('era') != dataset.group(2):
            raise ValueError('Data jobs require an explicit supported RAW dataset and era')
        primary, era, version = dataset.groups()
        source = request.get('source_url', '')
        expected = f'/store/data/{era}/{primary}/RAW/{version}/'
        if not (source.startswith('root://') and expected in source and source.endswith('.root')):
            raise ValueError('The RAW source differs from the declared dataset')
        if not re.fullmatch(rf'DATA_{era}_{primary}_SHIFTReco_[0-9]{{8}}_[A-Za-z0-9_]+',
                            request['batch_name']):
            raise ValueError('Data jobs must identify DATA, era and primary dataset')
    return rows


def audit_identities(path, expected):
    import ROOT
    source = ROOT.TFile.Open(str(path))
    if not source or source.IsZombie():
        raise ValueError('Data output ROOT is unreadable')
    tree = source.Get('Events')
    if not tree:
        raise ValueError('Data output has no Events tree')
    names = {branch.GetName() for branch in tree.GetListOfBranches()}
    if not {'run', 'luminosityBlock', 'event', 'ShiftDimuonVertex_mass'} <= names:
        raise ValueError('Data output lacks the required identity or SHIFT schema')
    tree.SetBranchStatus('*', 0)
    for name in ('run', 'luminosityBlock', 'event'):
        tree.SetBranchStatus(name, 1)
    observed = []
    for entry in range(tree.GetEntries()):
        tree.GetEntry(entry)
        observed.append((int(tree.run), int(tree.luminosityBlock), int(tree.event)))
    uuid = source.GetUUID().AsString()
    source.Close()
    if observed != expected or len(set(observed)) != len(expected):
        raise ValueError('Output event identities differ from the frozen owned originals')
    digest = hashlib.sha256(json.dumps(observed, separators=(',', ':')).encode()).hexdigest()
    return dict(events=len(observed), unique_events=len(set(observed)),
                ordered_original_ids_match=True, identity_sha256=digest,
                root_uuid=uuid, branches_read=['run', 'luminosityBlock', 'event'],
                candidate_branches_read=False)


def timing_report(path):
    allowed = {'MinEventTime', 'MaxEventTime', 'AvgEventTime', 'EventThroughput',
               'TotalJobTime', 'TotalJobCPU', 'TotalLoopTime', 'TotalLoopCPU',
               'TotalInitTime', 'TotalInitCPU', 'TotalEventSetupTime',
               'ROOT-tfile-read-totalMegabytes', 'ROOT-tfile-write-totalMegabytes'}
    if not path.is_file():
        return {}
    result = {}
    for metric in ET.parse(path).iter('Metric'):
        name = metric.attrib.get('Name')
        if name in allowed or (name.startswith('Timing-root-') and
                               name.endswith(('totalMegabytes', 'totalMsecs', 'numOperations'))):
            result[name] = float(metric.attrib['Value'])
    return result


def write_config(payload, work, request):
    config = work / 'execute_cfg.py'
    # The frozen template uses relative sibling helpers, profile and Golden JSON.
    # The only per-job changes are owned input identities, bounded count/output,
    # and native timing instrumentation. Reconstruction parameters stay pinned.
    code = 'import hashlib, json, runpy\nfrom pathlib import Path\n'
    code += 'import FWCore.ParameterSet.Config as cms\n'
    code += 'process = runpy.run_path(' + repr(str(payload / 'reconstruct_cfg.py')) + ')["process"]\n'
    code += 'owned = json.loads(Path(' + repr(str(work / 'request.json')) + ').read_text())\n'
    code += 'process.source.fileNames = cms.untracked.vstring(owned["source_url"])\n'
    code += 'process.maxEvents.input = cms.untracked.int32(len(owned["events"]))\n'
    code += 'process.source.skipEvents = cms.untracked.uint32(0)\n'
    code += 'process.source.eventsToProcess = cms.untracked.VEventRange(*["{run}:{luminosityBlock}:{event}".format(**row) for row in owned["events"]])\n'
    code += 'process.NANOAODoutput.fileName = cms.untracked.string(' + repr('file:' + str(work / 'restricted_nano.root')) + ')\n'
    code += 'process.Timing = cms.Service("Timing", summaryOnly=cms.untracked.bool(True), useJobReport=cms.untracked.bool(True))\n'
    code += 'table = process.shiftMuonTable.clone()\n'
    code += 'assert hashlib.sha256(table.dumpPython().encode()).hexdigest() == owned["table_sha256"]\n'
    config.write_text(code)
    return config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('request', type=Path)
    parser.add_argument('request_sha256')
    parser.add_argument('--payload', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    work = Path.cwd().resolve()
    if sha256(args.request) != args.request_sha256:
        raise ValueError('Transferred data request changed')
    request = json.loads(args.request.read_text())
    expected = validate_request(request)
    persistent = Path(request['directory'])
    if sha256(persistent / 'request.json') != args.request_sha256:
        raise ValueError('Persistent request differs from transferred request')
    for name, digest in request['payload_files'].items():
        if sha256(args.payload / name) != digest:
            raise ValueError('Frozen worker helper or configuration changed: ' + name)
    os.chdir(persistent)
    guard = persistent / 'attempt_started.json'
    recovery = begin_attempt(persistent, work, request, args.request_sha256, expected, audit_identities)
    if recovery['action'] == 'adopt':
        result = recovery['result']
        save(work / 'result.json', result)
        save(work / 'status.json', dict(complete=True, exit_code=0, request_sha256=args.request_sha256))
        save(persistent / 'worker_status.json', result)
        return
    guard_record = dict(request_sha256=args.request_sha256, phase='starting',
                        pid=os.getpid(), hostname=os.uname().nodename, work_directory=str(work),
                        persistent_directory=str(persistent), started_utc=time.time(),
                        recovery_count=recovery['recovery_count'], recovery=recovery)
    if recovery['action'] == 'new':
        with guard.open('x') as stream:
            json.dump(guard_record, stream)
    else:
        save(guard, guard_record)
    os.chdir(work)
    result = dict(schema='shift-data-worker-result-v1', complete=False,
                  request_sha256=args.request_sha256, job_name=request['job_name'],
                  batch_name=request['batch_name'], original_events=len(expected),
                  input_origin='collision-data', candidate_branches_read=False)
    if request['schema'] == 'shift-data-reconstruction-job-v2':
        result.update(dataset=request['dataset'], era=request['era'], source_url=request['source_url'],
                      original_runs=sorted({identity[0] for identity in expected}))
    termination = dict(requested=False, child=None)
    def terminate(signum, frame):
        termination['requested'] = True
        child = termination['child']
        if child is not None and child.poll() is None:
            child.terminate()
    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    started = time.monotonic()
    native_code = None
    try:
        config = write_config(args.payload.resolve(), work, request)
        child_before = resource.getrusage(resource.RUSAGE_CHILDREN)
        native_started = time.monotonic()
        with (work / 'cmsRun.restricted.log').open('xb') as log:
            try:
                native = subprocess.Popen(['cmsRun', '-j', str(work / 'FrameworkJobReport.xml'), str(config)],
                                          cwd=work, stdout=log, stderr=subprocess.STDOUT)
                termination['child'] = native
                native_code = native.wait(timeout=request.get('native_timeout_seconds', 900))
            except subprocess.TimeoutExpired:
                native.kill()
                native.wait()
                native_code = 124
        child_after = resource.getrusage(resource.RUSAGE_CHILDREN)
        result['native'] = dict(exit_code=native_code, elapsed_seconds=time.monotonic()-native_started,
                                cpu_seconds=child_after.ru_utime+child_after.ru_stime-child_before.ru_utime-child_before.ru_stime,
                                max_rss_kb=child_after.ru_maxrss,
                                os_block_inputs=child_after.ru_inblock-child_before.ru_inblock,
                                os_block_outputs=child_after.ru_oublock-child_before.ru_oublock,
                                timing=timing_report(work / 'FrameworkJobReport.xml'))
        log = (work / 'cmsRun.restricted.log').read_text(errors='replace')
        result['exception_categories'] = sorted(set(re.findall(r"exception of category '([A-Za-z][A-Za-z0-9_]*)'", log)))
        if native_code != 0:
            raise RuntimeError('Native data reconstruction failed; restricted evidence preserved')
        root = work / 'restricted_nano.root'
        result['identity_audit'] = audit_identities(root, expected)
        result['output'] = dict(name=root.name, bytes=root.stat().st_size, sha256=sha256(root),
                                root_uuid=result['identity_audit']['root_uuid'])
        result['complete'] = True
        result['exit_code'] = 0
    except Exception as error:
        result['exit_code'] = native_code if native_code not in (None, 0) else 1
        result['error_type'] = type(error).__name__
        # Exceptions may contain protected values; never export their text.
    finally:
        result['worker_elapsed_seconds'] = time.monotonic()-started
        result['operational_interruption'] = termination['requested']
        save(work / 'result.json', result)
        save(work / 'status.json', dict(complete=result['complete'], exit_code=result['exit_code'],
                                       request_sha256=args.request_sha256))
        save(persistent / 'worker_status.json', result)
    print(json.dumps({name:result[name] for name in ('complete', 'exit_code', 'job_name', 'original_events')}))
    raise SystemExit(result['exit_code'])


if __name__ == '__main__':
    main()
