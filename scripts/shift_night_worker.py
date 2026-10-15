#!/usr/bin/env python3
# Code style: AI-code. AI-generated frozen night-worker admission, provenance and publication checks.
"""Keep scheduling guards separate from the unchanged detector reconstruction worker."""
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
import tarfile
import time


def sha(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024**2), b''):
            result.update(block)
    return result.hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def optional_read(path):
    try:
        return read(path) if Path(path).exists() else {}
    except (OSError, ValueError):
        return {}


def save(path, data):
    Path(path).write_text(json.dumps(data, indent=2, allow_nan=False) + '\n')


def native(args, timeout=900):
    environment = dict(os.environ)
    for name in ('LD_LIBRARY_PATH', 'LD_PRELOAD', 'PYTHONPATH', 'PYTHONHOME', 'ROOTSYS'):
        environment.pop(name, None)
    environment['PATH'] = '/usr/bin:/bin'
    if Path(args[0]).name == 'condor_chirp':
        # The local Chirp endpoint comes from _CONDOR_CHIRP_CONFIG. CERN's
        # login-node myschedd lookup is unrelated and can hang on workers.
        environment['SKIP_LOCAL_CONFIG_FILE'] = 'TRUE'
    process = subprocess.Popen(args, env=environment, start_new_session=True,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate(timeout=2)
        raise
    if process.returncode:
        raise subprocess.CalledProcessError(process.returncode, args, stdout, stderr)
    return stdout


def chirp_command(arguments):
    """Retry a bounded transport read or idempotent attribute write, never science."""
    for attempt in range(3):
        try:
            return native(['/usr/libexec/condor/condor_chirp'] + arguments, timeout=10)
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
            if attempt == 2:
                raise
            time.sleep(1)


def chirp(name, value):
    chirp_command(['set_job_attr', name, json.dumps(value)])


def ad():
    result = {}
    for line in Path(os.environ['_CONDOR_JOB_AD']).read_text().splitlines():
        match = re.fullmatch(r'([A-Za-z][A-Za-z0-9_]*)\s*=\s*(-?\d+)\s*', line)
        if match:
            result[match[1]] = int(match[2])
        boolean = re.fullmatch(r'([A-Za-z][A-Za-z0-9_]*)\s*=\s*(true|false)\s*', line, re.I)
        if boolean:
            result[boolean[1]] = boolean[2].lower() == 'true'
    return result


def live_ad():
    current = {}
    for name in ('ShiftNightAdmissionStartEpoch', 'ShiftNightAdmissionStopEpoch',
                 'ShiftNightAdmissionLeaseEpoch', 'ShiftNightScienceStarted', 'NumJobStarts'):
        value = json.loads(chirp_command(['get_job_attr', name]).strip())
        if type(value) not in (int, bool):
            raise ValueError('Scheduler admission field has invalid type: ' + name)
        current[name] = value
    return current


def admit(live=False):
    current = live_ad() if live else ad()
    if current.get('ShiftNightScienceStarted') is True:
        save('science_restart_rejected.json', {'reason': 'Previous execution already started reconstruction',
                                               'requires_explicit_manual_recovery': True})
        raise SystemExit(76)
    now = time.time()
    allowed = (current['ShiftNightAdmissionStartEpoch'] <= now <
               min(current['ShiftNightAdmissionStopEpoch'], current['ShiftNightAdmissionLeaseEpoch']))
    if not allowed:
        chirp('ShiftNightBootstrapInProgress', False)
        chirp('ShiftNightAdmissionDeferred', True)
        chirp('ShiftNightProcessedEvents', 0)
        chirp('ShiftNightDeferredNumJobStarts', current.get('NumJobStarts', 1))
        save('admission.json', {'night_admission_deferred': True,
                               'processed_events': 0, 'checked_at_epoch': now})
        raise SystemExit(75)


def bootstrap_started():
    # The claim snapshot can still contain the previous start count. Read the
    # scheduler before writing a deferral proof tied to this execution.
    admit(live=True)
    chirp('ShiftNightBootstrapInProgress', True)
    chirp('ShiftNightProcessedEvents', 0)


def event_contract(selection):
    content = {'source_gen_sha256': selection['source_gen_sha256'],
               'raw_indices': selection['raw_indices'], 'event_ids': selection['event_ids'],
               'native_weights_hex': [float(w).hex() for w in selection['native_weights']],
               'sampling_probabilities_hex': [float(p).hex() for p in selection['sampling_probabilities']],
               'seed': selection['seed'], 'response_contract_sha256': selection['response_contract_sha256']}
    return hashlib.sha256(json.dumps(content, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def prepare(selection_path, selection_sha, contract_sha):
    selection = read(selection_path)
    bootstrap = read('bootstrap.json')
    if sha(selection_path) != selection_sha or event_contract(selection) != contract_sha:
        raise ValueError('Selection or exact native event contract changed')
    if selection['event_contract_sha256'] != contract_sha or not 1 <= selection['count'] <= 20:
        raise ValueError('Invalid sparse selection contract')
    if selection['seed'] != 230000000 + selection['job'] * 100:
        raise ValueError('Frozen original reconstruction seed changed')
    if selection['response_contract_sha256'] != bootstrap['response_contract_sha256']:
        raise ValueError('Frozen detector response authority changed')
    base = Path('base') / selection['source_base_path']
    if sha(base) != selection['source_base_sha256']:
        raise ValueError('Full original source descriptor changed')
    descriptor = read(base)
    if descriptor['gen_sha256'] != selection['source_gen_sha256']:
        raise ValueError('Original GEN file identity changed')
    for raw, identity, native_weight, probability in zip(
            selection['raw_indices'], selection['event_ids'], selection['native_weights'],
            selection['sampling_probabilities']):
        if descriptor['event_ids'][raw] != identity:
            raise ValueError('Original GEN identity changed')
        if float(descriptor.get('weights', [1.] * len(descriptor['event_ids']))[raw]).hex() != float(native_weight).hex():
            raise ValueError('Native double GEN weight changed')
        if not math.isfinite(probability) or not 0 < probability <= 1:
            raise ValueError('Invalid union sampling probability')
    descriptor['selected_indices'] = selection['raw_indices']
    descriptor['sampling_probabilities'] = selection['sampling_probabilities']
    descriptor['sampling'] = {'plan_sha256': bootstrap['night_plan_sha256'],
                              'event_contract_sha256': contract_sha,
                              'convention': 'Native W/pi; pi is union of original and complete fixed draws'}
    save('source.json', descriptor)
    shutil.copy2(selection_path, 'selection.json')
    for stage, expected in bootstrap['templates'].items():
        if sha(Path('base/templates') / ('step' + stage + '.py')) != expected:
            raise ValueError('Frozen detector template changed')
    if sha('base/response_contract.json') != bootstrap['response_contract_sha256']:
        raise ValueError('Frozen response contract changed')


def mark_started():
    # The claim-time ad is a snapshot. Renewed leases must be read from the scheduler.
    admit(live=True)
    chirp('ShiftNightScienceStarted', True)
    chirp('ShiftNightBootstrapInProgress', False)
    chirp('ShiftNightAdmissionDeferred', False)
    chirp('ShiftNightDeferredNumJobStarts', -1)
    chirp('ShiftNightProcessedEvents', -1)
    save('science_started.json', {'started_at_epoch': time.time()})


def upload(path, destination):
    native(['/usr/bin/xrdcp', '--silent', '--posc', '--cksum', 'adler32',
            str(path), 'root://eosuser.cern.ch/' + destination])
    readback = Path('readback_' + Path(path).name)
    native(['/usr/bin/xrdcp', '--silent', '--cksum', 'adler32',
            'root://eosuser.cern.ch/' + destination, str(readback)])
    expected = sha(path)
    if sha(readback) != expected:
        raise ValueError('Whole-file EOS readback SHA mismatch: ' + destination)
    readback.unlink()
    return expected


def classify_report_failure(report):
    tier = report.get('current_tier', '')
    if tier == 'SETUP':
        return 'input'
    if tier.endswith('_CONFIG'):
        return 'configuration'
    if tier.endswith('_AUDIT') or tier.endswith('_VALIDATED'):
        return 'integrity'
    if tier in ('SIM', 'DIGIHLT', 'RECO', 'NANO'):
        return 'computation'
    return 'integrity'


def finalise(exit_code, job, epoch, selection_sha, contract_sha):
    bootstrap = optional_read('bootstrap.json')
    selection = optional_read('selection.json')
    report = optional_read('report.json')
    admission = optional_read('admission.json')
    status = {'schema': 'shift-night-worker-completion-v1', 'job': job, 'epoch': epoch,
              'selection_sha256': selection_sha, 'event_contract_sha256': contract_sha,
              'source_gen_sha256': selection.get('source_gen_sha256'),
              'response_contract_sha256': bootstrap.get('response_contract_sha256'),
              'events': selection.get('count'), 'seed': selection.get('seed'),
              'complete': False, 'exit_code': exit_code, 'analysis_release': False,
              'normalization_ready': False, 'physics_valid': False,
              'validated_tier_events': report.get('validated_tier_events', {}),
              'exact_double_event_audit_passed': False,
              'night_admission_deferred': bool(admission.get('night_admission_deferred')),
              'processed_events': 0 if admission else report.get('validated_tier_events', {}).get('NANO', 0),
              'no_automatic_science_retries': True, 'resolved_response_config_passed': False,
              'failure_kind': 'admission' if exit_code == 75 else 'bootstrap',
              'error': report.get('error'), 'completed_at_epoch': time.time()}
    durable = False
    publishing_started = False
    try:
        if Path('science_started.json').exists():
            status['failure_kind'] = classify_report_failure(report)
            stages = {'SIM': 1, 'DIGIHLT': 2, 'RECO': 3, 'NANO': 4}
            active_stage = stages.get(report.get('current_tier'))
            status['resolved_response_config_passed'] = bool(active_stage and
                Path('step%d_cfg.py' % active_stage).is_file() and
                report.get('validated_tier_events', {}).get('GEN') == selection.get('count'))
        if exit_code == 0 and not report.get('complete'):
            raise ValueError('Successful exit lacks a complete detector worker receipt')
        if exit_code == 0 and report.get('complete'):
            import importlib.util
            spec = importlib.util.spec_from_file_location('frozen_v10', 'payload/workflow/scripts/run_shift_gen_to_nano.py')
            worker = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(worker)
            original = report.get('original_source_events', report['source_events'])
            if [row['id'] for row in original] != selection['event_ids']:
                raise ValueError('Completed originals do not match selected exact event identities')
            if [float(row['weight']).hex() for row in original] != [float(w).hex() for w in selection['native_weights']]:
                raise ValueError('Completed original native double weights changed')
            if report['seed'] != selection['seed'] or report['source_indices'] != selection['raw_indices']:
                raise ValueError('Completed source indices or original seed changed')
            if report['validated_tier_events'] != {name: selection['count'] for name in ('GEN', 'SIM', 'DIGIHLT', 'RECO', 'NANO')}:
                raise ValueError('Not all original events reached all requested tiers')
            result = worker.audit_nano(Path('nano.root'), report['source_events'], selection['sampling_probabilities'])
            if result != report['nano']:
                raise ValueError('Independent exact-double Nano audit differs from worker receipt')
            status['exact_double_event_audit_passed'] = True
            status['resolved_response_config_passed'] = True
            status['failure_kind'] = 'publication'
        if not status['night_admission_deferred'] and selection.get('output_base'):
            destination = selection['output_base'] + ('/canary' if selection.get('canary') else '/epochs/epoch%02d' % epoch) + '/job%d' % job
            publishing_started = True
            native(['/usr/bin/xrdfs', 'root://eosuser.cern.ch', 'mkdir', '-p', destination], timeout=120)
            names = ['evidence.tar.gz']
            if exit_code == 0 and report.get('complete'):
                names.append('nano.root')
            keep_reco = exit_code != 0 or selection.get('retain_RECO', False) or selection.get('canary', False)
            if exit_code == 0 and report.get('complete'):
                import ROOT
                handle = ROOT.TFile.Open('nano.root')
                for event in handle.Get('Events'):
                    if any(float(v) > 20 for v in event.ShiftMuon_pt) or any(float(v) > 10 for v in event.ShiftDimuonVertex_mass):
                        keep_reco = True
                handle.Close()
            if keep_reco:
                names.extend(['step3.root'] if exit_code == 0 and not selection.get('canary') else ['step1.root', 'step2.root', 'step3.root'])
            output_bytes = 0
            output_files = 0
            for name in names:
                path = Path(name)
                if not path.is_file():
                    continue
                kind = {'nano.root': 'nano', 'evidence.tar.gz': 'evidence', 'step3.root': 'reco'}.get(name, path.stem)
                remote = destination + '/' + name
                digest = upload(path, remote)
                status.update({kind + '_path': remote, kind + '_sha256': digest,
                               kind + '_readback_sha256': digest})
                output_bytes += path.stat().st_size
                output_files += 1
            status['complete'] = exit_code == 0 and report.get('complete') is True and status['exact_double_event_audit_passed']
            status['failure_kind'] = None if status['complete'] else status['failure_kind']
            receipt = {'schema': 'shift-night-durable-EOS-receipt-v1', 'status': dict(status),
                       'worker_report': report, 'selection': selection, 'bootstrap_sha256': sha('bootstrap.json')}
            save('completion_receipt.json', receipt)
            receipt_path = destination + ('/complete.json' if status['complete'] else '/failed.json')
            digest = upload(Path('completion_receipt.json'), receipt_path)
            status.update(receipt_path=receipt_path, receipt_sha256=digest, receipt_readback_sha256=digest,
                          published_output_files=output_files + 1,
                          published_output_bytes=output_bytes + Path('completion_receipt.json').stat().st_size)
            durable = True
    except Exception as error:
        status['complete'] = False
        status['exit_code'] = status['exit_code'] or 74
        status['failure_kind'] = 'publication' if publishing_started else 'integrity'
        status['error'] = repr(error)
    if not selection and not Path('science_started.json').exists():
        # The selection may not exist when the first scheduler read fails.
        # Retain that bootstrap failure instead of replacing it with a missing-key error.
        status.update(failure_kind='bootstrap', processed_events=0,
                      science_started=False,
                      error=status.get('error') or 'Bootstrap failed before selection was loaded')
    rescue_names = [] if durable or status['night_admission_deferred'] else [
        'bootstrap.json', 'source.json', 'selection.json', 'report.json', 'evidence.tar.gz',
        'nano.root', 'step1.root', 'step2.root', 'step3.root', 'worker_runtime.log',
        'science_restart_rejected.json']
    with tarfile.open('rescue_payload.tar.gz', 'w:gz') as archive:
        for name in rescue_names:
            if Path(name).is_file():
                archive.add(name, arcname=name)
    status.update(rescue_payload_sha256=sha('rescue_payload.tar.gz'),
                  rescue_payload_bytes=Path('rescue_payload.tar.gz').stat().st_size,
                  rescue_contains_unique_tiers=bool(rescue_names), durable_EOS_publication_passed=durable)
    save('status.json', status)
    # Console output is deliberately restricted to setup checks and one concise final line.
    # Reserve1MiB in addition to the exact two Condor transfer files for stdout/stderr.
    returned_bytes = Path('status.json').stat().st_size + Path('rescue_payload.tar.gz').stat().st_size + 1024**2
    try:
        chirp('ShiftNightReturnedBytes', returned_bytes)
        chirp('ShiftNightFailureKind', status['failure_kind'] or 'none')
        chirp('ShiftNightPublishedOutputFiles', status.get('published_output_files', 0))
        chirp('ShiftNightPublishedOutputBytes', status.get('published_output_bytes', 0))
        chirp('ShiftNightProcessedEvents', status['processed_events'])
    except Exception as error:
        status['chirp_reporting_error'] = repr(error)
        save('status.json', status)
        # Missing size proof leaves outputs in the scheduler spool until manual recovery.
        # Completed, hash-checked science remains successful if the optional
        # final progress message fails. Native transfer statistics bound retrieval.
        return status['exit_code']
    return status['exit_code']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('admit', 'bootstrap', 'prepare', 'started', 'finalise'))
    parser.add_argument('arguments', nargs='*')
    args = parser.parse_args()
    if args.mode == 'admit':
        admit()
    elif args.mode == 'bootstrap':
        bootstrap_started()
    elif args.mode == 'prepare':
        prepare(*args.arguments)
    elif args.mode == 'started':
        mark_started()
    else:
        code, job, epoch, selection_sha, contract_sha = args.arguments
        raise SystemExit(finalise(int(code), int(job), int(epoch), selection_sha, contract_sha))


if __name__ == '__main__':
    main()
