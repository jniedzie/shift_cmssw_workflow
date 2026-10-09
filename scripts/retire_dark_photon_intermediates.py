#!/usr/bin/env python3
"""Freeze, review, then retire exact closed dark-photon pilot intermediates.

Run once without --execute to create detector/retirement_plan.json. Then run
the same arguments with --execute and a fresh account-wide scheduler proof.
Requires ROOT in this Python runtime; inspection runs in a fresh subprocess.
No directory/glob deletion, scheduler mutation, builds or publication occur.
The original GEN is retained unless every audited GEN event was replayed and
no other source descriptor refers to it. Plans, JSONL progress and all physics
ledgers, configurations, logs, NanoAOD and histograms are retained permanently.

Scheduler proof schema: shift-dark-photon-retirement-scheduler-proof-v1;
account=current user, account_wide=true, complete=true, captured_epoch=UTC
epoch; query={returncode:0,constraint:<explicit account>,command:[...],stderr:""};
ads=[complete current account ads]. Failed/unknown/stale coverage is rejected.
"""
import argparse
import fcntl
import getpass
import hashlib
import json
import math
import os
from pathlib import Path
import shlex
import re
import socket
import stat
import subprocess
import sys
import time


def discover_workspace():
    for parent in Path(__file__).resolve().parents:
        if (parent/'SHIFT_BSM.md').is_file() and (parent/'shift_cmssw_workflow/AGENTS.md').is_file():
            return parent
    return Path(__file__).resolve().parents[2]


WORKSPACE = discover_workspace()
VALIDATION = WORKSPACE / 'validation'
PLAN_SCHEMA = 'shift-dark-photon-intermediate-retirement-plan-v1'
PROOF_SCHEMA = 'shift-dark-photon-retirement-scheduler-proof-v1'
PROCESS_PROOF_SCHEMA = 'shift-dark-photon-retirement-process-exceptions-v1'
# Explicit operational review for this existing native lxplus login session.
# A replacement/new opaque process is not automatically exempted.
REVIEWED_LOGIN_PIDS = {3434277, 3434294}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            h.update(block)
    return h.hexdigest()


def local_path(path, root=None):
    root = VALIDATION if root is None else root
    path = Path(os.path.abspath(path))
    require(path == path.resolve(), 'Symlinks/aliased paths are not allowed: ' + str(path))
    require(path.is_relative_to(root.resolve()), 'Foreign path outside validation: ' + str(path))
    return path


def fingerprint(path, deletable=False):
    path = local_path(path)
    value = path.lstat()
    require(stat.S_ISREG(value.st_mode), 'Not a local regular file: ' + str(path))
    require(value.st_size > 0, 'Empty file: ' + str(path))
    if deletable:
        require(value.st_uid == os.getuid() and value.st_nlink == 1,
                'Foreign-owned or multiply linked deletion candidate: ' + str(path))
    checksum = digest(path)
    unchanged = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_gid, s.st_nlink,
                           s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    require(unchanged(path.lstat()) == unchanged(value), 'File changed while hashing: ' + str(path))
    return dict(path=str(path), sha256=checksum, bytes=value.st_size,
                device=value.st_dev, inode=value.st_ino, mtime_ns=value.st_mtime_ns)


def load(path):
    fingerprint(path)
    return json.loads(Path(path).read_text())


def identities(values):
    require(isinstance(values, list) and len(values) > 0, 'Missing event identities')
    require(all(isinstance(v, list) and len(v) == 3 and
                all(type(x) is int and x >= 0 for x in v) for v in values), 'Invalid event identities')
    require(len({tuple(v) for v in values}) == len(values), 'Duplicate event identities')
    return values


def inspect_root(kind, path):
    """Fresh ROOT interpreter prevents loader-state changes in the caller."""
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()),
                             '--inspect-root', kind, str(path)], capture_output=True,
                            text=True, timeout=180)
    require(result.returncode == 0, 'Independent ROOT inspection failed: ' + result.stderr[-2000:])
    return json.loads(result.stdout)


def root_record(kind, path):
    import ROOT
    root = ROOT.TFile.Open(str(path))
    require(root and not root.IsZombie() and not root.TestBit(ROOT.TFile.kRecovered),
            'Unreadable/recovered ROOT file')
    try:
        if kind != 'histograms':
            tree = root.Get('Events')
            require(tree, 'Missing Events tree')
            record = dict(events=int(tree.GetEntries()))
            if kind == 'nano':
                for name in ('run', 'luminosityBlock', 'event', 'genWeight'):
                    require(tree.GetBranch(name), 'Missing Nano branch: ' + name)
                rows = [[int(e.run), int(e.luminosityBlock), int(e.event), float(e.genWeight)] for e in tree]
                require(all(math.isfinite(row[3]) for row in rows), 'Nonfinite Nano weights')
                record.update(event_ids=[row[:3] for row in rows], weights=[row[3] for row in rows])
            return record
        inventory = {}
        def visit(directory, prefix=''):
            for key in directory.GetListOfKeys():
                obj = key.ReadObj(); name = prefix + key.GetName()
                if obj.InheritsFrom('TDirectory'):
                    visit(obj, name + '/')
                elif obj.InheritsFrom('TH1'):
                    values = [float(obj.GetBinContent(i)) for i in range(obj.GetNcells())]
                    errors = [float(obj.GetBinError(i)) for i in range(obj.GetNcells())]
                    require(all(math.isfinite(v) for v in values + errors), 'Nonfinite histogram: ' + name)
                    inventory[name] = dict(entries=float(obj.GetEntries()), sum_all_cells=sum(values),
                                           sum_error_squared_all_cells=sum(v*v for v in errors))
        visit(root)
        require(inventory, 'No histograms')
        return dict(histograms=inventory, histogram_count=len(inventory))
    finally:
        root.Close()


def build_plan(gen_directory, detector_directory, diagnostic, reference_root=VALIDATION,
               root_validator=inspect_root):
    gen, detector, diagnostic = [local_path(p) for p in (gen_directory, detector_directory, diagnostic)]
    require(gen != detector and gen.is_dir() and detector.is_dir(), 'Require distinct GEN/detector directories')
    source_path = detector/'source.json'
    protected_paths = [gen/'manifest.json', gen/'contract.json', gen/'validation.json', source_path,
                       detector/'signal_report.json', detector/'report.json', detector/'signal_audit.json',
                       detector/'histogram_report.json', diagnostic, detector/'nano.root', detector/'histograms.root']
    manifest, contract, audit, source, signal, common, signal_audit, hist, diag = [load(p) for p in protected_paths[:9]]
    require(manifest.get('status', '').startswith('GEN runtime audit passed') and audit.get('runtime_validated') is True,
            'GEN manifest/full-GEN audit incomplete')
    require(manifest.get('validation') == audit, 'Manifest audit differs from full-GEN ledger')
    all_ids = identities(audit.get('event_ids'))
    rows = audit.get('event_rows', [])
    require(audit.get('events') == contract.get('requested_events') == len(rows) == len(all_ids),
            'Full-GEN denominator mismatch')
    require([row.get('id') for row in rows] == all_ids, 'GEN row identities mismatch')
    all_weights = [row.get('weight') for row in rows]
    require(all(type(w) in (int, float) and math.isfinite(w) and w == 1. for w in all_weights),
            'Pilot requires exact native unit GEN weights')
    require(all(row.get('signal_decays') and row.get('full_graph_sha256') for row in rows), 'Missing lifetime/full-graph ledger')
    if audit.get('proposal_ledger') is not None:
        from pythia_generation_ledger import read_pythia_generation_ledger
        require(read_pythia_generation_ledger(gen/'cmsRun.log', len(all_ids)) == audit['proposal_ledger'],
                'Trial ledger/log mismatch')
        require(audit.get('full_gen_denominator', {}).get('accepted_events') == len(all_ids), 'Trial denominator mismatch')
        protected_paths.append(gen/'cmsRun.log')
    require(signal.get('complete') is True and common.get('complete') is True and hist.get('complete') is True
            and diag.get('complete') is True, 'Incomplete detector/histogram/diagnostic receipt')
    requested = identities(signal.get('requested_event_ids'))
    count = len(requested)
    require(1 <= count <= 20 and common.get('events') == count, 'Unsupported/outstanding detector scope')
    require(source.get('gen_transport') == 'local' and source.get('gen') == str(gen/'gen.root')
            and common.get('source_gen') == str(gen/'gen.root'), 'Foreign/shared GEN source')
    require(source.get('event_ids') == all_ids and source.get('weights') == all_weights, 'Source denominator differs from full GEN')
    expected = {tuple(i): w for i, w in zip(all_ids, all_weights)}
    require(all(tuple(i) in expected for i in requested), 'Replay includes foreign identities')
    weights = [expected[tuple(i)] for i in requested]
    for value in (source.get('gen_sha256'), common.get('source_gen_sha256')):
        require(value == manifest.get('output_sha256'), 'Source GEN hash mismatch')
    require(source.get('receipt') == str(gen/'manifest.json') and source.get('receipt_sha256') == digest(gen/'manifest.json')
            and common.get('source_receipt_sha256') == digest(gen/'manifest.json')
            and common.get('source_descriptor_sha256') == digest(source_path)
            and signal.get('source_manifest_sha256') == digest(gen/'manifest.json'), 'Source receipt changed')
    require(signal.get('signal_contract') == contract and source.get('signal_contract_sha256') == digest(gen/'contract.json'),
            'Signal contract changed')
    require(signal.get('common_report_sha256') == digest(detector/'report.json')
            and signal.get('signal_audit') == signal_audit, 'Signal graph audit receipt changed')
    for stage in ('1', '2', '3'):
        tier = signal_audit.get('edm_tiers', {}).get(stage, {})
        require(tier.get('events') == count and tier.get('genparticle_graph_checked') == count,
                'Incomplete generated-ancestry audit at stage ' + stage)
        if stage == '1':
            require(tier.get('full_hepmc_graph_checked') == count, 'Incomplete SIM HepMC graph audit')
        require(common.get('stages', {}).get(stage, {}).get('audit', {}).get('event_ids') == requested,
                'Stage identity mismatch')
    nano, histograms = detector/'nano.root', detector/'histograms.root'
    nano_hash, hist_hash = digest(nano), digest(histograms)
    require(nano_hash == common.get('nano_sha256') == hist.get('input_sha256') == diag.get('input_sha256'),
            'Protected Nano/receipt hash mismatch')
    require(hist_hash == hist.get('histogram_sha256') and histograms.stat().st_size == hist.get('histogram_bytes'),
            'Protected histogram hash/size mismatch')
    require(hist.get('input_event_ids') == requested and hist.get('native_event_weight_sum') == sum(weights)
            and hist.get('source_signal_report_sha256') == digest(detector/'signal_report.json')
            and hist.get('source_chain_report_sha256') == digest(detector/'report.json'), 'Histogram scope/receipt mismatch')
    require(hist.get('config_overrides') == dict(nEvents=count, weightsBranchName='genWeight')
            and hist.get('frozen_component_sha256') and hist.get('freeze_manifest_sha256'), 'Missing frozen histogram provenance')
    require(diag.get('schema') == 'shift-dark-photon-mother-aware-nano-diagnostics-v1'
            and diag.get('input') == str(nano) and diag.get('input_events') == count
            and diag.get('summary', {}).get('events') == count
            and [row.get('event_id') for row in diag.get('events', [])] == requested
            and [row.get('native_weight') for row in diag.get('events', [])] == weights,
            'Diagnostic must cover every retained Nano event exactly')
    for name in ('signal_report.json', 'report.json'):
        require(diag.get('adjacent_receipt_sha256', {}).get(name) == digest(detector/name), 'Stale diagnostic receipt')
    current_diagnostics = []
    for path in diagnostic.parent.glob('*.json'):
        value = load(path)
        if (value.get('schema') == diag['schema'] and value.get('complete') is True
                and value.get('input') == str(nano) and value.get('input_sha256') == nano_hash):
            current_diagnostics.append(path)
    require(current_diagnostics == [diagnostic], 'Require exactly one current diagnostic receipt for this Nano')
    nano_record = root_validator('nano', nano)
    require(nano_record == dict(events=count, event_ids=requested, weights=weights), 'Nano content mismatch/corruption')
    require(root_validator('histograms', histograms) == dict(histograms=hist.get('histograms'),
            histogram_count=hist.get('histogram_count')), 'Histogram content mismatch/corruption')
    candidates = []
    for name in ('gen.root', 'step1.root', 'step2.root', 'step3.root'):
        path = detector/name
        require(path.exists(), 'Unexplained missing intermediate: ' + str(path))
        row = fingerprint(path, deletable=True)
        require(root_validator('edm', path)['events'] == (len(all_ids) if name == 'gen.root' else count), 'EDM cardinality mismatch')
        if name == 'gen.root':
            require(row['sha256'] == manifest['output_sha256'], 'Detector GEN copy changed')
        else:
            require(row['bytes'] == common['stages'][name[4]]['bytes'], 'Detector stage size changed')
        candidates.append(row)
    original = fingerprint(gen/'gen.root', deletable=True)
    require(original['sha256'] == manifest['output_sha256'] and root_validator('edm', gen/'gen.root')['events'] == len(all_ids),
            'Original GEN changed/corrupt')
    complete_replay = requested == all_ids
    if complete_replay:
        candidates.append(original)
    else:
        protected_paths.append(gen/'gen.root')
    require(candidates, 'No named intermediates to retire')
    return dict(schema=PLAN_SCHEMA, created_epoch=time.time(), gen_directory=str(gen), detector_directory=str(detector),
                runner_sha256=digest(__file__),
                diagnostic=str(diagnostic), reference_root=str(local_path(reference_root)), candidates=candidates,
                protected=[fingerprint(path) for path in protected_paths], full_gen_events=len(all_ids), replayed_events=count,
                original_gen_retained=not complete_replay, original_gen_record=original,
                event_ids=requested, weights=weights, full_gen_audit=audit, signal_contract=contract,
                policy='Named regular intermediates only; all ledgers/receipts/logs/configs/Nano/histograms retained',
                physics_valid=False, normalization_ready=False)


def point_referenced(values, cwd, plan):
    needles = [plan['gen_directory'], plan['detector_directory']]
    gen, detector = Path(needles[0]), Path(needles[1])
    if gen.parent == detector.parent and gen.name == 'gen' and detector.name == 'detector':
        needles.append(str(gen.parent))
    text = json.dumps(values)
    if any(needle in text for needle in needles):
        return True
    if not cwd or not Path(cwd).is_absolute():
        return False
    for value in values:
        if not isinstance(value, str):
            continue
        try:
            tokens = shlex.split(value.replace(',', ' '))
        except ValueError:
            tokens = value.split()
        for token in tokens:
            token = token.split('=', 1)[-1]
            if not token or token.startswith('-'):
                continue
            path = (Path(cwd)/token).resolve()
            if any(path == Path(needle) or path.is_relative_to(needle) for needle in needles):
                return True
    return False


def scheduler_guard(proof_path, plan, max_age=300):
    proof = load(proof_path)
    query = proof.get('query', {})
    require(proof.get('schema') == PROOF_SCHEMA and proof.get('account') == getpass.getuser()
            and proof.get('account_wide') is True and proof.get('complete') is True, 'Unknown scheduler account coverage')
    stamp = proof.get('captured_epoch')
    require(type(stamp) in (int, float) and math.isfinite(stamp), 'Unknown scheduler timestamp')
    age = time.time() - stamp
    expected_constraint = f'Owner == "{getpass.getuser()}" || AccountingGroupUser == "{getpass.getuser()}"'
    require(0 <= age <= max_age and query.get('returncode') == 0 and query.get('stderr') == ''
            and isinstance(query.get('command'), list) and '-global' in query['command']
            and query.get('constraint', '').strip() == expected_constraint
            and isinstance(proof.get('ads'), list), 'Failed/stale/unknown scheduler query')
    for ad in proof['ads']:
        require(isinstance(ad, dict), 'Invalid scheduler ad')
        require(ad.get('Owner') == getpass.getuser() or ad.get('AccountingGroupUser') == getpass.getuser(),
                'Scheduler ad outside claimed account coverage')
        require(isinstance(ad.get('Iwd'), str) and ('Cmd' in ad or 'Args' in ad or 'Arguments' in ad),
                'Scheduler ad lacks reference-audit attributes')
        require(not point_referenced(list(ad.values()), ad.get('Iwd'), plan), 'Live scheduler reference to retirement point')
    return fingerprint(proof_path)


def proc_identity(pid):
    value = Path('/proc', str(pid)).stat()
    return dict(pid=pid, uid=value.st_uid, inode=value.st_ino, device=value.st_dev,
                mode=value.st_mode, ctime_ns=value.st_ctime_ns, mtime_ns=value.st_mtime_ns)


def process_exceptions(plan):
    record = plan.get('process_exceptions_proof')
    if record is None:
        return {}
    require(fingerprint(record['path']) == record, 'Process exception proof changed')
    proof = load(record['path'])
    require(proof.get('schema') == PROCESS_PROOF_SCHEMA and proof.get('account') == getpass.getuser()
            and proof.get('uid') == os.getuid() and proof.get('hostname') == socket.gethostname()
            and proof.get('reviewed_pids') == sorted(REVIEWED_LOGIN_PIDS)
            and proof.get('review_decision') == 'explicit-root-review-20261009-protected-login-session-only',
            'Unreviewed process exception proof')
    entries = proof.get('exceptions', [])
    require({entry['identity']['pid'] for entry in entries} == REVIEWED_LOGIN_PIDS and len(entries) == 2,
            'Only the two explicitly reviewed login/session exceptions are allowed')
    result = {}
    for entry in entries:
        identity = entry['identity']; pid = identity['pid']
        if not Path('/proc', str(pid)).exists():
            continue
        require(proc_identity(pid) == identity and identity['uid'] == os.getuid(), 'Pinned opaque PID identity changed')
        relation = entry['relationship']; observed = relation['identity']; related_pid = observed['pid']
        require(proc_identity(related_pid) == observed, 'Pinned readable relationship process changed')
        command = Path('/proc', str(related_pid), 'cmdline').read_bytes().rstrip(b'\0').split(b'\0')
        require([part.decode() for part in command] == relation['command_argv'], 'Pinned relationship command changed')
        if relation['kind'] == 'readable-systemd-user-parent':
            require(pid == 3434277 and command == [b'/usr/lib/systemd/systemd', b'--user'], 'Invalid reviewed systemd relationship')
            children = Path('/proc',str(related_pid),'task',str(related_pid),'children').read_text().split()
            require(str(pid) in children, 'Pinned systemd child relationship changed')
        elif relation['kind'] == 'readable-preexisting-login-shell-child':
            require(pid == 3434294 and command == [b'-bash'], 'Invalid reviewed login-shell relationship')
            fields = Path('/proc',str(related_pid),'stat').read_text().rsplit(')',1)[1].split()
            require(int(fields[1]) == pid, 'Pinned login-shell parent relationship changed')
        else:
            raise ValueError('Unknown process exception relationship')
        result[pid] = entry
    return result


def candidate_open_file_checks(plan):
    paths = sorted({row['path'] for row in plan['candidates'] + plan['protected']
                    if row['path'].endswith('.root') and Path(row['path']).exists()})
    require(paths, 'No existing ROOT files for candidate-specific open-file checks')
    # Installed CERN psmisc fuser treats '--' as an option reset and discards
    # following names. Canonical absolute paths cannot be option arguments.
    require(all(Path(path).is_absolute() for path in paths), 'Open-file checks require absolute filenames')
    commands = [['fuser','-v',*paths], ['lsof','-n','-P','-a','-u',getpass.getuser(),'--',*paths]]
    records = []
    for command in commands:
        result = subprocess.run(command, capture_output=True, text=True, timeout=60)
        require(result.returncode == 1 and not result.stdout.strip(), 'Open reader or failed open-file query: ' + command[0])
        warnings = result.stderr.strip()
        if warnings:
            match = re.fullmatch(r"lsof: WARNING: can't stat\(\) fuse\.portal file system /run/user/([0-9]+)/doc\n\s*Output information may be incomplete\.", warnings)
            require(command[0] == 'lsof' and match is not None and int(match.group(1)) != os.getuid(),
                    'Unknown candidate-specific open-file coverage: ' + command[0])
        records.append(dict(command=command, returncode=result.returncode, stdout=result.stdout,
                            stderr=result.stderr, reported_matching_readers=0,
                            limitation='Protected login/session FDs remain inaccessible; foreign FUSE warning retained when present'))
    return records


def local_process_guard(plan):
    """Check current-account local command lines, cwd and open descriptors."""
    require(Path('/proc/self/fd').is_dir(), 'Local process coverage unavailable')
    # CERN restricts reading root-owned /proc/1/comm even on the native host.
    # The Codex PID sandbox has a current-user-owned PID 1; host PID 1 is root.
    # Actual deletion remains a native central-controller operation, never a
    # worker/container operation. All visible current-account processes must
    # still permit cmdline/cwd/fd inspection below, otherwise fail closed.
    require(Path('/proc/1').stat().st_uid == 0,
            'Host process coverage unavailable inside PID namespace; run on native host')
    ancestors = {os.getpid()}
    parent = os.getppid()
    while parent and parent not in ancestors:
        try:
            if Path(f'/proc/{parent}').stat().st_uid != os.getuid():
                break
            ancestors.add(parent)
            fields = Path(f'/proc/{parent}/stat').read_text().rsplit(')', 1)[1].split()
            parent = int(fields[1])
        except FileNotFoundError:
            break
    needles = [plan['gen_directory'], plan['detector_directory']]
    exceptions = process_exceptions(plan)
    checked = 0; accepted_opaque = []
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid != os.getuid() or int(entry.name) == os.getpid():
                continue
            command = (entry/'cmdline').read_bytes().replace(b'\0', b' ').decode(errors='replace')
            cwd = os.readlink(entry/'cwd')
            require(not any(needle in cwd for needle in needles), 'Local process cwd uses retirement point: PID ' + entry.name)
            if int(entry.name) not in ancestors:
                require(not point_referenced([command], cwd, plan), 'Live local command references point: PID ' + entry.name)
            for fd in (entry/'fd').iterdir():
                try:
                    target = os.readlink(fd)
                except FileNotFoundError:
                    continue
                require(not any(needle in target for needle in needles), 'Open file uses retirement point: PID ' + entry.name)
            checked += 1
        except (FileNotFoundError, ProcessLookupError):
            continue
        except PermissionError as error:
            pid = int(entry.name)
            if pid not in exceptions:
                raise ValueError('Unknown local process/open-file coverage: PID ' + entry.name) from error
            require(proc_identity(pid) == exceptions[pid]['identity'], 'Pinned opaque PID changed during check')
            accepted_opaque.append(pid)
    return dict(scope='native host; current account; explicit reviewed login/session exceptions',
                hostname=socket.gethostname(), fully_inspected_processes=checked,
                reviewed_uninspectable_pids=sorted(accepted_opaque),
                open_file_checks=candidate_open_file_checks(plan),
                coverage_caveat='No exhaustive FD claim: reviewed protected login/session FDs cannot be read; exact candidate queries and all accessible process/queue references checked')


def shared_source_guard(plan):
    if plan['original_gen_retained']:
        return
    own = Path(plan['detector_directory'])/'source.json'
    original = str(Path(plan['gen_directory'])/'gen.root')
    for path in Path(plan['reference_root']).rglob('source.json'):
        if path == own:
            continue
        payload = path.read_text()
        try:
            value = json.loads(payload)
        except (ValueError, UnicodeError):
            require(original not in payload, 'Unreadable descriptor references original GEN: ' + str(path))
            continue
        require(not isinstance(value, dict) or value.get('gen') != original,
                'Another detector source references original GEN: ' + str(path))


def revalidate_protected(plan, root_validator=inspect_root):
    for row in plan['protected']:
        require(fingerprint(row['path']) == row, 'Protected file changed: ' + row['path'])
    detector = Path(plan['detector_directory'])
    require(root_validator('nano', detector/'nano.root') == dict(events=plan['replayed_events'],
            event_ids=plan['event_ids'], weights=plan['weights']), 'Protected Nano content changed')
    hist = load(detector/'histogram_report.json')
    require(root_validator('histograms', detector/'histograms.root') == dict(histograms=hist['histograms'],
            histogram_count=hist['histogram_count']), 'Protected histogram content changed')


def execute_plan(plan_path, proof_path, root_validator=inspect_root,
                 process_guard=local_process_guard):
    plan_path = local_path(plan_path)
    plan = load(plan_path)
    require(plan.get('schema') == PLAN_SCHEMA and plan_path == Path(plan['detector_directory'])/'retirement_plan.json',
            'Foreign retirement plan')
    require(plan.get('runner_sha256') == digest(__file__), 'Retirement runner changed since frozen dry run')
    allowed = {str(Path(plan['detector_directory'])/name) for name in ('gen.root', 'step1.root', 'step2.root', 'step3.root')}
    if not plan['original_gen_retained']:
        require(plan['full_gen_events'] == plan['replayed_events'] and plan['full_gen_audit']['event_ids'] == plan['event_ids'],
                'Original GEN has unprocessed events')
        allowed.add(str(Path(plan['gen_directory'])/'gen.root'))
    require(len({row['path'] for row in plan['candidates']}) == len(plan['candidates'])
            and all(row['path'] in allowed for row in plan['candidates']), 'Foreign deletion candidate in plan')
    progress_path = plan_path.with_name('retirement_progress.jsonl')
    lock_path = plan_path.with_name('retirement.lock')
    for path in (progress_path, lock_path):
        local_path(path)
        if path.exists():
            value = path.lstat()
            require(stat.S_ISREG(value.st_mode) and value.st_uid == os.getuid() and value.st_nlink == 1,
                    'Unsafe retirement progress/lock file')
    with lock_path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        history = [json.loads(line) for line in progress_path.read_text().splitlines()] if progress_path.exists() else []
        plan_hash = digest(plan_path)
        require(all(row.get('plan_sha256') == plan_hash for row in history), 'Progress belongs to another plan')
        deleted = {row['path'] for row in history if row.get('action') == 'deleted'}
        revalidate_protected(plan, root_validator)
        shared_source_guard(plan)
        for row in plan['candidates']:
            path = Path(row['path'])
            if str(path) in deleted:
                require(not path.exists(), 'Previously retired candidate reappeared')
            else:
                require(fingerprint(path, deletable=True) == row, 'Candidate changed since frozen dry run: ' + str(path))
        def append(action, **values):
            with progress_path.open('a') as stream:
                stream.write(json.dumps(dict(plan_sha256=plan_hash, epoch=time.time(), action=action, **values), allow_nan=False)+'\n')
                stream.flush(); os.fsync(stream.fileno())
        try:
            proof = scheduler_guard(proof_path, plan)
            append('validated', scheduler_proof=proof, scheduler_snapshot=load(proof_path))
            for row in plan['candidates']:
                path = Path(row['path'])
                if str(path) in deleted:
                    continue
                proof = scheduler_guard(proof_path, plan)
                coverage = process_guard(plan)
                require(fingerprint(path, deletable=True) == row, 'Candidate changed before deletion')
                append('deleting', path=str(path), file=row, scheduler_proof=proof, process_coverage=coverage)
                path.unlink()
                require(not path.exists(), 'Candidate still exists after deletion')
                append('deleted', path=str(path), file=row)
            revalidate_protected(plan, root_validator)
            append('complete', protected_files_unchanged=True)
        except Exception as error:
            append('failed', error=repr(error))
            raise
    return dict(complete=True, retired_files=len(plan['candidates']), retired_bytes=sum(row['bytes'] for row in plan['candidates']),
                original_gen_retained=plan['original_gen_retained'], plan_sha256=plan_hash)


def main():
    global WORKSPACE, VALIDATION
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, default=WORKSPACE,
                        help='Canonical SHIFT workspace, also usable from a frozen script copy')
    parser.add_argument('--gen-directory', type=Path, required=True)
    parser.add_argument('--detector-directory', type=Path, required=True)
    parser.add_argument('--diagnostic', type=Path, required=True)
    parser.add_argument('--scheduler-proof', type=Path, required=True)
    parser.add_argument('--process-exceptions-proof', type=Path,
                        help='Optional exact root-reviewed protected login/session PID proof; new opaque processes still block')
    parser.add_argument('--reference-root', type=Path,
                        help='Must be the whole workspace validation directory; narrowing coverage is refused')
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    WORKSPACE = Path(os.path.abspath(args.workspace))
    require(WORKSPACE == WORKSPACE.resolve() and (WORKSPACE/'SHIFT_BSM.md').is_file()
            and (WORKSPACE/'shift_cmssw_workflow/AGENTS.md').is_file(), 'Require canonical SHIFT workspace')
    VALIDATION = WORKSPACE/'validation'
    reference_root = VALIDATION if args.reference_root is None else local_path(args.reference_root)
    require(reference_root == VALIDATION, 'Reference coverage must include the whole workspace validation directory')
    plan_path = local_path(args.detector_directory)/'retirement_plan.json'
    if args.execute:
        plan = load(plan_path)
        require(plan['gen_directory'] == str(local_path(args.gen_directory)) and plan['diagnostic'] == str(local_path(args.diagnostic))
                and plan['reference_root'] == str(reference_root), 'Arguments differ from frozen dry run')
        require((plan.get('process_exceptions_proof') or {}).get('path') ==
                (str(local_path(args.process_exceptions_proof)) if args.process_exceptions_proof else None),
                'Process exception argument differs from frozen dry run')
        result = execute_plan(plan_path, args.scheduler_proof)
    else:
        require(not plan_path.exists(), 'Refuse to overwrite frozen retirement plan')
        plan = build_plan(args.gen_directory, args.detector_directory, args.diagnostic, reference_root)
        if args.process_exceptions_proof:
            record = fingerprint(args.process_exceptions_proof)
            plan.update(process_exceptions_proof=record, process_exception_evidence=load(record['path']))
            plan['protected'].append(record)
        scheduler_guard(args.scheduler_proof, plan)
        plan['process_coverage'] = local_process_guard(plan)
        shared_source_guard(plan)
        with plan_path.open('x') as stream:
            json.dump(plan, stream, indent=2, allow_nan=False); stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        result = dict(dry_run=True, plan=str(plan_path), candidate_files=len(plan['candidates']),
                      candidate_bytes=sum(row['bytes'] for row in plan['candidates']), original_gen_retained=plan['original_gen_retained'])
    print(json.dumps(result, allow_nan=False))


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == '--inspect-root':
        print(json.dumps(root_record(sys.argv[2], sys.argv[3]), allow_nan=False))
    else:
        main()
