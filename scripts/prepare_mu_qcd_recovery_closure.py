#!/usr/bin/env python3
# Code style: AI-code. AI-generated separate accounting for saved GEN recovery.
"""Prepare or run an honest accounting DAG without repairing the original DAG."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + str(os.getpid()) + '.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def native_exit(path, identity):
    cluster, process = [int(value) for value in identity.split('.')]
    state = {'executed': False, 'terminal': None}
    for block in Path(path).read_text().split('\n...\n')[:-1]:
        match = re.match(r'\s*(\d{3}) \((\d+)\.(\d+)\.\d+\)', block)
        if not match or (int(match[2]), int(match[3])) != (cluster, process):
            continue
        if match[1] == '001':
            state['executed'] = True
            state['terminal'] = None
        elif match[1] == '005':
            code = re.search(r'Normal termination \(return value (\d+)\)', block)
            state['terminal'] = int(code[1]) if code else 'signal_or_unknown'
        elif match[1] == '009':
            state['terminal'] = 'removed'
    return state


def old_error(manager):
    blocks = (Path(manager) / 'nodes.status').read_text().split('[')
    errors = [block.split(']', 1)[0] for block in blocks
              if re.search(r'\bNodeStatus\s*=\s*6\s*;', block)]
    if not any(re.search(r'\bNode\s*=\s*"L0614"\s*;', block) for block in errors):
        raise ValueError('The original L0614 ERROR must remain visible')
    return {'original_DAG_error_preserved': True, 'error_node_blocks': errors,
            'nodes_status_sha256': sha(Path(manager) / 'nodes.status'),
            'old_DAG_state_modified': False}


def frozen(root):
    root = Path(root)
    deployment = read(root / 'deployment.json')
    for path, expected in deployment['files'].items():
        if sha(root / path) != expected:
            raise ValueError('Closure deployment changed: ' + path)
    config = read(root / 'config.json')
    for path, expected in config['external_pins'].items():
        if sha(path) != expected:
            raise ValueError('Pinned recovery authority changed: ' + path)
    for kind in ('ordinary', 'weighted'):
        link = root / 'accounting' / kind / 'results'
        expected = Path(config['original_manager']) / kind / 'results'
        if not link.is_symlink() or link.resolve() != expected.resolve():
            raise ValueError('Canonical result link changed: ' + kind)
    return config


def production_rows(root, kind):
    manifest = read(Path(root) / 'accounting' / kind / 'manifest.json')
    rows = [row for row in manifest['jobs'] if row['stage'] == 'production']
    if kind == 'weighted':
        gate = read(Path(root) / 'accounting/weighted/scientific_gate.json')
        if not gate.get('pass') or gate['manifest_sha256'] != sha(Path(root) / 'accounting/weighted/manifest.json'):
            raise ValueError('Weighted fixed gate does not match its manifest')
        rows = [row for row in rows if row['chunk_index'] < gate['production_plan'][row['stratum']]['chunks']]
    return manifest, rows


def source_inventory(root, kind, allow_pending=False):
    """Bind every original source and its full denominator; never omit failures."""
    root = Path(root)
    manifest, rows = production_rows(root, kind)
    expected = sha(root / 'accounting' / kind / 'manifest.json')
    inventory, pending = [], []
    for row in rows:
        path = root / 'accounting' / kind / 'results' / (row['id'] + '.json')
        if not path.exists():
            pending.append(row['id'])
            continue
        payload = path.read_bytes()
        record = json.loads(payload)
        if not record.get('complete') or not record.get('publication_complete') or record.get('exit_status') != 0:
            pending.append(row['id'])
            continue
        audit = record['semantic_audit']
        if (record.get('request') != row or record.get('manifest_sha256') != expected
                or record.get('source_inputs') != manifest['inputs']
                or record.get('generator_profile') != manifest['generator_profile']
                or audit.get('complete') is not True or audit.get('healthy') is not True
                or audit.get('events') != record['event_count']):
            raise ValueError('Canonical source differs from the frozen manifest: ' + row['id'])
        full = audit['full_source_trial_denominator'] if kind == 'ordinary' else record['ledger']['tried']
        if type(full) is not int or full <= 0:
            raise ValueError('Missing full original source denominator: ' + row['id'])
        inventory.append(dict(source_id=row['id'], stratum=row['stratum'],
            receipt_sha256=hashlib.sha256(payload).hexdigest(), full_source_trials=full,
            framework_source_events=audit.get('source_accepted', record.get('ledger', {}).get('accepted')),
            saved_events=record['event_count'], gen_sha256=audit['input_sha256']))
    if pending and not allow_pending:
        raise FileNotFoundError('Unpublished fixed sources remain: ' + ','.join(pending))
    return inventory, pending


def accounting(root, kind):
    root = Path(root)
    record = read(root / 'accounting' / kind / 'production_complete.json')
    manifest, rows = production_rows(root, kind)
    schema = ('shift-mu-enriched-ordinary-accounting-v1' if kind == 'ordinary'
              else 'shift-weighted-high-bin-final-accounting-v1')
    if (record.get('schema') != schema or record.get('complete') is not True
            or record.get('failures') != []
            or record.get('manifest_sha256') != sha(root / 'accounting' / kind / 'manifest.json')):
        raise ValueError('Final accounting is incomplete: ' + kind)
    groups = {row['stratum'] for row in rows}
    if set(record['strata']) != groups:
        raise ValueError('Accounting omits a fixed production stratum')
    for stratum in groups:
        planned = [row for row in rows if row['stratum'] == stratum]
        summary = record['strata'][stratum]
        if summary.get('chunks') != len(planned):
            raise ValueError('Accounting omits a fixed source')
        if kind == 'ordinary':
            if (record.get('stage') != 'production' or record.get('pilots_excluded_from_production') is not True
                    or summary.get('framework_source_events') != sum(row['trials'] for row in planned)
                    or set(summary.get('event_weight_pb_by_chunk', {})) != {row['id'] for row in planned}):
                raise ValueError('Ordinary accounting differs from its full source cohort')
        elif (summary.get('trials') != sum(row['trials'] for row in planned)
                or summary.get('pilots_excluded') is not True or summary.get('effective_target_met') is not True):
            raise ValueError('Weighted accounting differs from its fixed trial budget')
    return record


def recovery_proofs(root, config):
    plan = read(config['recovery_plan'])
    first = read(config['first_proof'])
    workers = read(config['workers_manifest'])
    bindings = read(config['bindings'])
    combined = read(config['combined_proof'])
    if (plan.get('schema') != 'shift-exact-saved-GEN-publication-recovery-plan-v1'
            or workers.get('schema') != 'shift-six-saved-GEN-publication-workers-v1'
            or combined.get('schema') != 'shift-seven-saved-GEN-publication-closure-v1'
            or combined.get('complete') is not True or combined.get('source_count') != 7
            or combined.get('new_GEN_calls') != 0):
        raise ValueError('Unknown or incomplete seven-source recovery authority')
    independent = {row['source_id']: row for row in combined['sources']}
    if len(independent) != 7 or set(independent) != {row['node_id'] for row in plan['jobs']}:
        raise ValueError('Independent recovery proof omits an original source')
    if bindings.get('schema') != 'shift-held-GEN-publication-recovery-bindings-v1':
        raise ValueError('Unknown replacement-job binding schema')
    declared = {row['node_id']: row for row in workers['workers']}
    if set(bindings['jobs']) != set(declared) or len(declared) != 6:
        raise ValueError('All six genuine replacement jobs must be bound')
    if (first.get('schema') != 'shift-independent-saved-GEN-recovery-success-v1'
            or first.get('complete') is not True or not first.get('checks')
            or any(value is not True for value in first['checks'].values())):
        raise ValueError('First replacement proof is incomplete')
    proofs, seen = [], set()
    for row in plan['jobs']:
        identity = row['node_id']
        if identity == first['node_id']:
            job = first['new_job_id']
            event_path = Path(config['first_proof']).parent / 'worker/worker.events'
        else:
            worker = declared[identity]
            if worker['source_plan_row'] != row or worker['original_job_id'] != row['job_id']:
                raise ValueError('Replacement worker lost original source membership')
            job = bindings['jobs'][identity]
            event_path = Path(worker['worker_event_log'])
        if (not isinstance(job, str) or not re.fullmatch(r'\d+\.\d+', job)
                or job == row['job_id'] or job in seen):
            raise ValueError('A replacement needs its own distinct native job identity')
        seen.add(job)
        authority = independent[identity]
        proof = read(authority['proof'])
        if (sha(authority['proof']) != authority['proof_sha256']
                or proof.get('schema') != 'shift-independent-saved-GEN-recovery-success-v1'
                or proof.get('complete') is not True or not proof.get('checks')
                or any(value is not True for value in proof['checks'].values())
                or proof.get('node_id') != identity or proof.get('new_job_id') != job
                or proof.get('original_job_id') != row['job_id']
                or authority['new_job_id'] != job or authority['original_job_id'] != row['job_id']
                or authority['canonical_receipt'] != row['canonical_return_receipt']
                or sha(authority['canonical_receipt']) != authority['canonical_receipt_sha256']
                or proof.get('canonical_receipt_sha256') != authority['canonical_receipt_sha256']):
            raise ValueError('Independent original-byte publication proof changed: ' + identity)
        native = native_exit(event_path, job)
        if native != {'executed': True, 'terminal': 0}:
            raise FileNotFoundError('Replacement has no genuine successful native exit yet: ' + identity)
        path = Path(row['canonical_return_receipt'])
        record = read(path)
        failed = read(row['original_failed_receipt'])
        audit = record['semantic_audit']
        recovery = record.get('recovery', {})
        if (sha(row['original_failed_receipt']) != row['original_failed_receipt_sha256']
                or sha(row['saved_archive']) != row['saved_archive_sha256']
                or not record.get('complete') or not record.get('publication_complete')
                or record.get('exit_status') != 0 or record.get('recovery_command_exit_status') != 0
                or record.get('request') != row['request'] or record.get('artifacts') != row['saved_artifacts']
                or audit != failed['semantic_audit'] or record.get('source_inputs') != failed['source_inputs']
                or recovery.get('original_job_id') != row['job_id']
                or recovery.get('saved_archive_sha256') != row['saved_archive_sha256']
                or recovery.get('original_failed_receipt_sha256') != row['original_failed_receipt_sha256']
                or recovery.get('worker_sha256') != plan['worker_sha256']
                or recovery.get('recovery_manifest_sha256') != sha(config['recovery_plan'])
                or recovery.get('no_GEN_calls') is not True or recovery.get('publication_only') is not True
                or recovery.get('fresh_semantic_audit_matches_original') is not True
                or audit['full_source_trial_denominator'] != row['full_ND_trial_denominator']
                or record['event_count'] != row['saved_events']
                or digest(audit['event_ids']) != row['event_identity_sha256']
                or digest(audit['weights']) != row['native_weights_sha256']):
            raise ValueError('Recovered canonical bytes or original exposure changed: ' + identity)
        files = {item['name']: item for item in record['publication']}
        for name, expected in row['saved_artifacts'].items():
            item = files[name]
            if (item['path'] != row['canonical_EOS_directory'] + '/' + name
                    or item.get('independent_sha256_readback') is not True
                    or item['sha256'] != expected['sha256'] or item['bytes'] != expected['bytes']):
                raise ValueError('Recovered publication lacks its original readback: ' + identity)
        proofs.append(dict(source_id=identity, original_job_id=row['job_id'], replacement_job_id=job,
            original_DAG_node=row['DAG_node'], native_exit=native,
            event_log=str(event_path), event_log_sha256=sha(event_path),
            canonical_receipt=str(path), canonical_receipt_sha256=sha(path),
            full_source_trials=row['full_ND_trial_denominator'], saved_events=row['saved_events'],
            original_archive_sha256=row['saved_archive_sha256'],
            original_failed_receipt_sha256=row['original_failed_receipt_sha256']))
    if len(proofs) != 7:
        raise ValueError('All seven original failed publications must be included')
    return proofs


def wait_for(root, kind):
    root = Path(root)
    config = frozen(root)
    try:
        if kind == 'recovery':
            recovery_proofs(root, config)
            pending = []
        else:
            inventory, pending = source_inventory(root, kind, allow_pending=True)
        save(root / (kind + '_heartbeat.json'), {'checked_at_epoch': time.time(),
            'stage': kind, 'phase': 'waiting' if pending else 'ready', 'pending_source_ids': pending})
        return 75 if pending else 0
    except FileNotFoundError as error:
        save(root / (kind + '_heartbeat.json'), {'checked_at_epoch': time.time(),
            'stage': kind, 'phase': 'waiting', 'reason': str(error)})
        return 75


def finish(root):
    root = Path(root)
    config = frozen(root)
    ordinary, high = accounting(root, 'ordinary'), accounting(root, 'weighted')
    inventory = {}
    for kind in ('ordinary', 'weighted'):
        inventory[kind], pending = source_inventory(root, kind)
    if len(inventory['ordinary']) != 900 or len(inventory['weighted']) != 145:
        raise ValueError('Closure requires all 900 ordinary and 145 allocated high sources')
    for source in inventory['weighted']:
        if source['receipt_sha256'] != config['high_source_receipts_sha256'][source['source_id']]:
            raise ValueError('A high-bin receipt changed after its completed final accounting')
    proofs = recovery_proofs(root, config)
    error = old_error(config['original_manager'])
    record = dict(schema='shift-saved-GEN-recovery-closure-v1', complete=True,
        checked_at_epoch=time.time(), ordinary_sources=900, weighted_sources=145,
        recovery_sources=proofs, source_exposure=inventory,
        ordinary_accounting=ordinary, weighted_accounting=high,
        accounting_sha256={kind: sha(root / 'accounting' / kind / 'production_complete.json')
                           for kind in ('ordinary', 'weighted')},
        original_DAG=error, original_DAG_success=False,
        old_parallel_gen_completion_authority_passed=False,
        recovered_saved_events=sum(row['saved_events'] for row in proofs),
        recovered_full_source_trials=sum(row['full_source_trials'] for row in proofs),
        no_new_GEN_calls=True, analysis_release=False, normalization_ready=False)
    save(root / 'closure_complete.json', record)
    return record


def supervision_problems(root, ads):
    managers = [ad for ad in ads if ad.get('ShiftMuQCDRecoveryClosureManager') is True]
    problems = []
    if (len(managers) != 1 or managers[0].get('JobStatus') != 2
            or managers[0].get('Iwd') != str(root) or managers[0].get('MaxRuntime', 0) < 604800):
        problems.append('Expected one executing seven-day closure manager in its persistent Iwd')
    for ad in ads:
        if ad.get('JobStatus') == 5:
            identity = str(ad.get('ClusterId', '?')) + '.' + str(ad.get('ProcId', '?'))
            problems.append('Closure job ' + identity + ' is held: ' + str(ad.get('HoldReason', 'unknown reason')))
    return managers, problems


def watch(root):
    """Read-only detached supervision; never submit, release or repair jobs."""
    from shift_condor_native import query
    root = Path(root)
    while True:
        try:
            frozen(root)
            if (root / 'closure_complete.json').exists():
                return 0 if read(root / 'closure_complete.json').get('complete') is True else 1
            ads = query('ShiftMuQCDRecoveryClosure == ' + json.dumps(str(root)),
                ['ClusterId', 'ProcId', 'JobStatus', 'Iwd', 'MaxRuntime',
                 'ShiftMuQCDRecoveryClosureManager', 'HoldReason', 'DAGNodeName'],
                timeout=30, local_schedd=True)
            managers, problems = supervision_problems(root, ads)
            status = root / 'nodes.status'
            if status.exists() and re.search(r'\bNodesFailed\s*=\s*[1-9]\d*\s*;', status.read_text()):
                problems.append('The separate accounting DAG has a failed node')
            if (root / 'failure.json').exists():
                problems.append('Closure stage recorded a failure')
            if status.exists() and time.time() - status.stat().st_mtime > 180:
                problems.append('Native accounting DAG heartbeat is stale')
            record = dict(checked_at_epoch=time.time(), phase='attention_required' if problems else 'observing',
                          problems=problems, manager=managers,
                          held_jobs=[ad for ad in ads if ad.get('JobStatus') == 5],
                          worker_action='none; read-only')
        except Exception as error:
            record = dict(checked_at_epoch=time.time(), phase='attention_required',
                          scheduler_state='unknown', error=repr(error), worker_action='none; preserve all state')
        save(root / 'watchdog_heartbeat.json', record)
        time.sleep(60)


def prepare(destination, manager, recovery_plan, workers_manifest, bindings, first_proof, combined_proof):
    root, manager = Path(destination).resolve(), Path(manager).resolve()
    if root.exists() or str(root).startswith(('/tmp/', '/pool/condor/dir_')):
        raise ValueError('Choose a new persistent closure directory')
    root.mkdir(parents=True)
    (root / 'frozen').mkdir()
    source = Path(__file__).resolve()
    shutil.copy2(source, root / 'closure.py')
    shutil.copy2(manager / 'shift_condor_native.py', root / 'shift_condor_native.py')
    for name in ('control_mu_enriched_qcd_gen.py', 'qcd_mu_enrichment.py', 'audit_shift_weighted_gen.py',
                 'generation_publication.py', 'soft_mpi_model.py'):
        shutil.copy2(manager / 'ordinary' / name, root / 'frozen' / name)
    for kind in ('ordinary', 'weighted'):
        target = root / 'accounting' / kind
        target.mkdir(parents=True)
        shutil.copy2(manager / kind / 'manifest.json', target / 'manifest.json')
        (target / 'results').symlink_to(manager / kind / 'results', target_is_directory=True)
    shutil.copy2(manager / 'weighted/scientific_gate.json', root / 'accounting/weighted/scientific_gate.json')
    # This record was produced by the original unchanged 145-source finalizer.
    shutil.copy2(manager / 'weighted/production_complete.json', root / 'accounting/weighted/production_complete.json')
    accounting(root, 'weighted')
    high_inventory, pending = source_inventory(root, 'weighted')
    external = [Path(path).resolve() for path in (recovery_plan, workers_manifest, bindings, first_proof, combined_proof)]
    independent = [Path(row['proof']) for row in read(combined_proof)['sources']]
    if len(read(recovery_plan)['jobs']) != 7 or len(read(workers_manifest)['workers']) != 6:
        raise ValueError('Closure must bind exactly seven saved sources and six new replacements')
    config = dict(schema='shift-GEN-recovery-closure-policy-v1', original_manager=str(manager),
        recovery_plan=str(external[0]), workers_manifest=str(external[1]),
        bindings=str(external[2]), first_proof=str(external[3]), combined_proof=str(external[4]),
        external_pins={str(path): sha(path) for path in external + independent + [manager / 'production.dag',
            manager / 'ordinary/manifest.json', manager / 'weighted/manifest.json',
            manager / 'weighted/scientific_gate.json', manager / 'weighted/production_complete.json']},
        high_source_receipts_sha256={row['source_id']: row['receipt_sha256'] for row in high_inventory})
    save(root / 'config.json', config)
    save(root / 'old_DAG_error_snapshot.json', old_error(manager))
    common = f'''universe = scheduler
initialdir = {root}
executable = /usr/bin/python3
getenv = False
notification = Never
+MaxRuntime = 604800
+ShiftMuQCDRecoveryClosure = "{root}"
on_exit_hold = (ExitBySignal == True) || (ExitCode != 0)
periodic_release = False
log = accounting.events
'''
    commands = {
        'ordinary': f'{root}/frozen/control_mu_enriched_qcd_gen.py {root}/accounting/ordinary production',
        'high': f'{root}/closure.py check-high {root}',
        'final': f'{root}/closure.py finish {root}'}
    for name, arguments in commands.items():
        (root / (name + '.sub')).write_text(common + f'arguments = {arguments}\noutput = {name}.out\nerror = {name}.err\nqueue 1\n')
    (root / 'watchdog.sub').write_text(common + f'arguments = {root}/closure.py watch {root}\n'
        '+ShiftMuQCDRecoveryClosureWatchdog = true\noutput = watchdog.out\nerror = watchdog.err\nqueue 1\n')
    (root / 'closure.dag').write_text(f'''JOB ORDINARY ordinary.sub
SCRIPT DEFER 75 60 PRE ORDINARY /usr/bin/python3 {root}/closure.py wait {root} ordinary
JOB HIGH high.sub
JOB FINAL final.sub
SCRIPT DEFER 75 60 PRE FINAL /usr/bin/python3 {root}/closure.py wait {root} recovery
PARENT ORDINARY HIGH CHILD FINAL
NODE_STATUS_FILE {root}/nodes.status 30 ALWAYS-UPDATE
''')
    files = {str(path.relative_to(root)): sha(path) for path in root.rglob('*')
             if path.is_file() and not path.is_symlink()}
    save(root / 'deployment.json', {'schema': 'shift-GEN-recovery-closure-deployment-v1',
        'files': files, 'prepared_only': True, 'submitted': False,
        'old_DAG_edited': False, 'no_new_GEN_calls': True})
    return root


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    preparation = commands.add_parser('prepare')
    preparation.add_argument('directory', type=Path)
    for name in ('manager', 'recovery-plan', 'workers-manifest', 'bindings', 'first-proof', 'combined-proof'):
        preparation.add_argument('--' + name, type=Path, required=True)
    waiting = commands.add_parser('wait')
    waiting.add_argument('directory', type=Path)
    waiting.add_argument('kind', choices=('ordinary', 'weighted', 'recovery'))
    for name in ('finish', 'check-high', 'watch'):
        command = commands.add_parser(name)
        command.add_argument('directory', type=Path)
    args = parser.parse_args()
    if args.command == 'prepare':
        print(prepare(args.directory, args.manager, args.recovery_plan, args.workers_manifest,
                      args.bindings, args.first_proof, args.combined_proof))
        return 0
    try:
        if args.command == 'watch':
            return watch(args.directory)
        if args.command == 'wait':
            return wait_for(args.directory, args.kind)
        if args.command == 'check-high':
            config = frozen(args.directory)
            accounting(args.directory, 'weighted')
            inventory, pending = source_inventory(args.directory, 'weighted')
            if {row['source_id']: row['receipt_sha256'] for row in inventory} != config['high_source_receipts_sha256']:
                raise ValueError('Completed high-bin receipt cohort changed')
        else:
            finish(args.directory)
        return 0
    except Exception as error:
        save(args.directory / 'failure.json', {'checked_at_epoch': time.time(),
            'stage': args.command, 'complete': False, 'error': repr(error), 'old_DAG_edited': False})
        raise


if __name__ == '__main__':
    raise SystemExit(main())
