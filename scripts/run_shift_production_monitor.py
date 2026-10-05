#!/usr/bin/env python3
"""Persistent Condor scheduler monitor; EOS readback runs on a vanilla worker.

Only the registered DAGMan jobs are edited or held. Scientific workers,
unrelated campaigns and immutable campaign manifests are never changed.
"""
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import sys
import time
import uuid

# Scheduler launch deliberately uses Python -I -S.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from shift_adaptive_capacity import allocations, decide, safe_limits, storage_reservation
from shift_condor_native import edit, query, run_condor

STANDBY_REASONS = ('SHIFT adaptive capacity standby',
                   'SHIFT adaptive capacity standby (by user jniedzie)')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    temporary = Path(str(path)+'.tmp')
    temporary.write_text(json.dumps(value, indent=2)+'\n')
    temporary.replace(path)


def audit_contract(managers, auditor_sha256, helper_sha256=None):
    keys = ('name', 'tag', 'manifest', 'manifest_sha256', 'bundle_url', 'bundle_sha256', 'audit_contract')
    value = dict(managers=[{k: m.get(k) for k in keys} for m in managers], auditor_sha256=auditor_sha256,
                 helper_sha256=helper_sha256 or {})
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def audit_interval(failures, policy):
    return min(policy.get('audit_retry_max_interval', 3600),
               policy.get('audit_interval', 600)*2**min(failures, 6))


def limit_arguments(arguments, cap):
    """Persist only the two numeric initial limits across DAGMan recovery."""
    for option, value in (('-MaxJobs', cap), ('-MaxIdle', min(cap, 50))):
        arguments, count = re.subn(r'(?<!\S)'+option+r'\s+[0-9]+(?=\s|$)', option+' '+str(value), arguments)
        if count != 1:
            raise ValueError('Expected exactly one numeric manager argument: '+option)
    return arguments


def standby_constraint(controller, monitor):
    return (f'ClusterId == {int(controller)} && ProcId == 0 && JobStatus == 5 && '
            f'ShiftCapacityWait == true && ShiftCapacityMonitor == {int(monitor)} && '
            '('+' || '.join('HoldReason == '+json.dumps(reason) for reason in STANDBY_REASONS)+')')


def submit_audit(state, registry, registry_hash):
    nonce = uuid.uuid4().hex
    folder = state/'audits'/nonce
    folder.mkdir(parents=True)
    frozen = Path(__file__).resolve().parent
    base = next(m for m in registry['managers'] if m.get('audit_contract') == 'frozen_gen_receipts')
    request = dict(nonce=nonce, registry_sha256=registry_hash,
                   collector_pool=registry['collector_pool'],
                   managers=registry['managers'], bundle_url=base['bundle_url'],
                   bundle_sha256=base['bundle_sha256'],
                   auditor_sha256=digest(frozen/'health_audit.py'),
                   condor_helper_sha256=digest(frozen/'shift_condor_native.py'),
                   audit_helper_sha256={name: digest(frozen/name) for name in
                       ('audit_shift_weighted_gen.py', 'generation_publication.py', 'soft_mpi_model.py')})
    save(folder/'health_request.json', request)
    description = '\n'.join([
        'universe = vanilla', f'initialdir = {folder}',
        f'executable = {frozen}/health_bootstrap.sh',
        'should_transfer_files = YES', 'when_to_transfer_output = ON_EXIT',
        'transfer_input_files = '+', '.join(str(p) for p in [folder/'health_request.json', frozen/'health_audit.py',
             frozen/'shift_condor_native.py']+[frozen/name for name in request['audit_helper_sha256']]),
        'transfer_output_files = health_result.json', 'getenv = False',
        'request_cpus = 1', 'request_memory = 4000', 'request_disk = 10000000', 'priority = 100',
        '+MaxRuntime = 3600', '+ShiftHealthAudit = True',
        f'+ShiftAuditNonce = "{nonce}"', '+JobBatchName = "shift_gen_health_monitor"',
        'on_exit_hold = (ExitBySignal == True) || (ExitCode != 0)',
        'periodic_release = False', 'output = audit.out', 'error = audit.err',
        'log = audit.log', 'queue 1', ''])
    (folder/'audit.sub').write_text(description)
    output = run_condor(['/usr/bin/condor_submit', str(folder/'audit.sub')], timeout=30)
    match = re.search(r'submitted to cluster (\d+)', output)
    if not match:
        raise ValueError('Audit submission identity unknown: '+output)
    record = dict(cluster=int(match.group(1)), folder=str(folder), nonce=nonce,
                  registry_sha256=registry_hash, submitted_at=time.time())
    save(folder/'submission.json', record)
    return record


def collect_audit(pending, registry_hash, registry):
    path = Path(pending['folder'])/'health_result.json'
    if not path.exists():
        ads = query(f"ClusterId == {pending['cluster']}", attributes=['JobStatus', 'HoldReason'])
        if ads and ads[0]['JobStatus'] == 5:
            raise ValueError('Health worker held: '+ads[0].get('HoldReason', 'unknown'))
        if not ads and time.time()-pending['submitted_at'] > 180:
            raise ValueError('Health worker ended without transferred result')
        return None
    result = json.loads(path.read_text())
    if (result.get('schema') != 'shift-capacity-health-v1' or result.get('nonce') != pending['nonce']
            or result.get('registry_sha256') != pending['registry_sha256']):
        raise ValueError('Audit result identity mismatch')
    request = json.loads((Path(pending['folder'])/'health_request.json').read_text())
    contract = audit_contract(request['managers'], request['auditor_sha256'], request.get('audit_helper_sha256'))
    current_contract = registry_audit_contract(registry)
    if contract != current_contract:
        return dict(result, healthy=False, refresh_required=True, error='registry changed; audit refresh required')
    return dict(result, validated_contract_sha256=contract)


def registry_audit_contract(registry):
    hashes = registry['deployment_sha256']
    helpers = {name: hashes[name] for name in
        ('audit_shift_weighted_gen.py', 'generation_publication.py', 'soft_mpi_model.py') if name in hashes}
    return audit_contract(registry['managers'], hashes['health_audit.py'], helpers)


def scheduler_state(registry, health, now):
    attributes = ['ClusterId', 'ProcId', 'JobStatus', 'JobUniverse', 'Cmd', 'Arguments', 'Iwd',
                  'ShiftSuiteTag', 'ShiftProductionController', 'ShiftProductionSuite',
                  'DAG_NodesFailed', 'DAG_NodesDone', 'DAG_NodesTotal',
                  'DAGMan_MaxJobs', 'DAGMan_MaxIdle', 'DAGManJobId',
                  'ShiftCapacityWait', 'ShiftCapacityMonitor', 'HoldReason']
    ads = query('Owner == "jniedzie" && (ShiftProductionSuite == true || ShiftProductionController == true)',
                attributes=attributes, timeout=25)
    coverage = (health or {}).get('account_queue', {})
    if (not coverage.get('verified') or now-coverage.get('checked_at_epoch', 0) > registry['policy'].get('audit_max_age', 1500)):
        raise ValueError('Fresh account-wide scheduler coverage from a credentialed worker required')
    if any(a.get('_queried_schedd') != registry['schedd'] and a.get('JobStatus') in (1, 2, 6, 7) for a in coverage['ads']):
        raise ValueError('Active SHIFT jobs outside the registered manager scheduler; shared capacity requires review')
    by_cluster = {a['ClusterId']: a for a in ads if a.get('ShiftProductionController')}
    known_ids = {m['controller'] for m in registry['managers']}
    known_tags = {m['tag'] for m in registry['managers']}
    parent_by_tag = {m['tag']: m['controller'] for m in registry['managers']}
    for ad in ads:
        if ad.get('JobStatus') not in (1, 2, 6, 7):
            continue
        if ad.get('ShiftProductionSuite') and ad.get('ShiftSuiteTag') not in known_tags:
            raise ValueError('Unregistered active SHIFT worker; shared capacity unknown')
        if ad.get('ShiftProductionSuite') and ad.get('DAGManJobId') != parent_by_tag.get(ad.get('ShiftSuiteTag')):
            raise ValueError('Registered worker tag has an unexpected parent manager')
        if ad.get('ShiftProductionController') and ad['ClusterId'] not in known_ids:
            raise ValueError('Unregistered active SHIFT controller; shared capacity unknown')
    managers = []
    for entry in registry['managers']:
        manager = dict(entry)
        ad = by_cluster.get(entry['controller'])
        manager['active'] = bool(ad and ad.get('JobStatus') in (1, 2, 5, 6, 7))
        if ad:
            if Path(ad.get('Cmd', '')).name != 'condor_dagman' or ad.get('JobUniverse') != 7:
                raise ValueError('Registered controller is not scheduler DAGMan')
            arguments = shlex.split(ad.get('Arguments', ''))
            if '-Dag' not in arguments or arguments.index('-Dag')+1 == len(arguments):
                raise ValueError('Registered controller has no exact DAG argument')
            dag_path = Path(arguments[arguments.index('-Dag')+1])
            if not dag_path.is_absolute():
                dag_path = Path(ad['Iwd'])/dag_path
            if str(dag_path.resolve()) != entry['dag']:
                raise ValueError('Registered controller DAG path differs')
            manager['ad'] = ad
        managers.append(manager)
    workers = [a for a in ads if a.get('ShiftProductionSuite') and a.get('ShiftSuiteTag') in known_tags]
    isolated_failures = []
    isolated_tags = set()
    for manager in managers:
        ad = manager.get('ad', {})
        manager['capacity_standby'] = bool(ad.get('JobStatus') == 5 and
            ad.get('HoldReason') in STANDBY_REASONS and ad.get('ShiftCapacityWait') and
            ad.get('ShiftCapacityMonitor') == registry.get('monitor_controller', 12794348))
        manager['active_workers'] = sum(w.get('ShiftSuiteTag') == manager['tag'] and
            w.get('JobStatus') in (1, 2, 6, 7) for w in workers)
        if manager.get('isolate_failures') and (manager['name'] in (health or {}).get('manager_failures', {}) or
                manager.get('ad', {}).get('DAG_NodesFailed', 0) or
                (manager.get('ad', {}).get('JobStatus') == 5 and not manager['capacity_standby']) or
                any(w.get('JobStatus') == 5 and w.get('ShiftSuiteTag') == manager['tag'] for w in workers)):
            isolated_failures.append(manager['name'])
            isolated_tags.add(manager['tag'])
            manager['ready'] = False
    return dict(verified=True, running=sum(a.get('JobStatus') == 2 for a in workers),
                idle=sum(a.get('JobStatus') == 1 for a in workers),
                held=sum(a.get('JobStatus') == 5 and a.get('ShiftSuiteTag') not in isolated_tags for a in workers)+
                     sum(m.get('ad', {}).get('JobStatus') == 5 and not m['capacity_standby'] and m['name'] not in isolated_failures for m in managers),
                failed_nodes=sum(m.get('ad', {}).get('DAG_NodesFailed', 0) for m in managers if m['name'] not in isolated_failures),
                isolated_failures=isolated_failures,
                isolated_active_workers=sum(a.get('JobStatus') in (1, 2, 6, 7) and a.get('ShiftSuiteTag') in isolated_tags for a in workers),
                managers=managers)


def main(state):
    state = state.resolve()
    singleton = (state/'monitor.lock').open('a')
    fcntl.flock(singleton, fcntl.LOCK_EX | fcntl.LOCK_NB)
    status_path = state/'status.json'
    status = json.loads(status_path.read_text()) if status_path.exists() else dict(
        target=50, changed_at=time.time()-1200, baseline_completed=0)
    pending = status.get('pending_audit')
    health = status.get('health')
    last_submit = status.get('last_audit_submit', 0)
    audit_failures = status.get('audit_failures', 0)
    while True:
        now = time.time()
        status['checked_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        status['monitor_host'] = os.uname().nodename
        status['monitor_pid'] = os.getpid()
        try:
            registry_path = state/'registry.json'
            registry_hash = digest(registry_path)
            registry = json.loads(registry_path.read_text())
            for name, expected in registry['deployment_sha256'].items():
                if digest(Path(__file__).resolve().parent/name) != expected:
                    raise ValueError('Frozen monitor deployment changed: '+name)
            for manager in registry['managers']:
                if manager.get('manifest') and digest(manager['manifest']) != manager['manifest_sha256']:
                    raise ValueError('Frozen scientific manifest changed: '+manager['name'])
                if manager.get('freeze'):
                    freeze_path = Path(manager['freeze'])
                    if digest(freeze_path) != manager['freeze_sha256']:
                        raise ValueError('Frozen campaign checksum manifest changed')
                    for name, expected in json.loads(freeze_path.read_text()).items():
                        if digest(freeze_path.parent/name) != expected:
                            raise ValueError('Frozen campaign file changed: '+name)
            current_contract = registry_audit_contract(registry)
            if health and health.get('validated_contract_sha256') != current_contract:
                health = dict(health, healthy=False, error='registry changed; fresh audit required')
            if pending:
                try:
                    result = collect_audit(pending, registry_hash, registry)
                except Exception as error:
                    save(Path(pending['folder'])/'monitor_failure.json', dict(error=str(error), checked_at=now))
                    pending = None
                    audit_failures += 1
                    health = dict(healthy=False, error=str(error))
                    result = None
                if result is not None:
                    health = result
                    pending = None
                    if health.get('refresh_required'):
                        last_submit = 0
                    else:
                        audit_failures = 0 if health.get('healthy') else audit_failures+1
                    save(state/'last_health.json', health)
            if not pending and now-last_submit >= audit_interval(audit_failures, registry['policy']):
                last_submit = now
                try:
                    pending = submit_audit(state, registry, registry_hash)
                except Exception:
                    audit_failures += 1
                    raise
            try:
                queue = scheduler_state(registry, health, now)
            except Exception as error:
                queue = dict(verified=False, error=str(error))
            policy = dict(registry['policy'], reservation_bytes=storage_reservation(registry['policy'], registry['managers']))
            decision = decide(status, health, queue, now, policy)
            status['storage_reservation_bytes'] = policy['reservation_bytes']
            status.update(decision)
            assigned = {}
            if queue['verified']:
                for manager in queue['managers']:
                    if manager['name'] in queue['isolated_failures'] and manager.get('ad', {}).get('JobStatus') == 2:
                        run_condor(['/usr/bin/condor_hold', str(manager['controller'])], timeout=25)
                if decision.get('stop_admissions'):
                    for manager in queue['managers']:
                        if manager['active'] and manager.get('ad', {}).get('JobStatus') == 2:
                            run_condor(['/usr/bin/condor_hold', str(manager['controller'])], timeout=25)
                else:
                    budget = max(1, decision['target']-queue['isolated_active_workers']-
                                 registry.get('startup_reserved_workers', 0))
                    desired = allocations(budget, queue['managers'])
                    assigned = safe_limits(budget, desired, queue['managers'])
                    growth_allowed = bool(health and health.get('healthy') and
                        now-health.get('checked_at_epoch', 0) <= registry['policy'].get('audit_max_age', 1500))
                    for manager in queue['managers']:
                        if manager['name'] not in assigned:
                            continue
                        cap = assigned[manager['name']]
                        ad = manager['ad']
                        if cap is None:
                            if not manager['capacity_standby']:
                                edit(str(manager['controller']), {'ShiftCapacityWait': True,
                                     'ShiftCapacityMonitor': registry.get('monitor_controller', 12794348)}, timeout=25)
                                run_condor(['/usr/bin/condor_hold', '-reason', 'SHIFT adaptive capacity standby',
                                            '-constraint', f'ClusterId == {manager["controller"]} && ProcId == 0 && (JobStatus == 1 || JobStatus == 2)'], timeout=25)
                            continue
                        if not growth_allowed:
                            if not isinstance(ad.get('DAGMan_MaxJobs'), int):
                                continue
                            if ad['DAGMan_MaxJobs'] > 0:
                                cap = min(cap, ad['DAGMan_MaxJobs'])
                            assigned[manager['name']] = cap
                        arguments = limit_arguments(ad['Arguments'], cap)
                        if ad.get('DAGMan_MaxJobs') != cap or ad.get('DAGMan_MaxIdle') != min(cap, 50) or ad['Arguments'] != arguments:
                            edit(str(manager['controller']), {'DAGMan_MaxJobs': cap, 'DAGMan_MaxIdle': min(cap, 50),
                                                              'Arguments': arguments}, timeout=25)
                            with (state/'changes.jsonl').open('a') as output:
                                output.write(json.dumps(dict(time=now, controller=manager['controller'], allocation=cap, reason=decision['reason']))+'\n')
                        if manager['capacity_standby'] and growth_allowed:
                            run_condor(['/usr/bin/condor_release', '-constraint',
                                standby_constraint(manager['controller'], registry.get('monitor_controller', 12794348))], timeout=25)
                            edit(str(manager['controller']), {'ShiftCapacityWait': False}, timeout=25)
            status.update(queue=queue, allocations=assigned, health=health,
                          pending_audit=pending, last_audit_submit=last_submit,
                          audit_failures=audit_failures,
                          registry_sha256=registry_hash)
            status.pop('error', None)
        except Exception as error:
            status['error'] = str(error)
            status['reason'] = 'monitor continues; admission changes frozen pending verified state'
            status.update(pending_audit=pending, last_audit_submit=last_submit, health=health, audit_failures=audit_failures)
        save(status_path, status)
        print(json.dumps({k: status.get(k) for k in ('checked_at', 'target', 'reason', 'error', 'allocations')}), flush=True)
        time.sleep(60)


if __name__ == '__main__':
    main(Path(sys.argv[1]))
