#!/usr/bin/env python3
"""Gate and supervise one frozen GEN-to-Nano campaign, with one failure email."""
import argparse
from email.message import EmailMessage
import fcntl
import json
import math
import os
from pathlib import Path
import pwd
import re
import shutil
import signal
import socket
import subprocess
import time
import uuid

from shift_condor_native import NativeCondorError, parse_ads, query, run_condor

MAX_WORKERS = 1000
CAPACITY_POLICY_KEYS = {'worker_ceiling', 'initial_workers', 'capacity_step',
    'capacity_interval_seconds', 'capacity_completions', 'max_idle_workers',
    'adaptive_capacity', 'capacity_idle_fraction', 'asynchronous_audits'}


class GlobalProductionError(RuntimeError):
    """Verified campaign-wide unsafe condition, rather than a monitoring fault."""


def read(path):
    return json.loads(path.read_text())


def save(path, value):
    temporary = path.with_name(path.name + '.' + str(os.getpid()) + '.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def effective_policy(root, capacity_path=None):
    """Use a separate scheduling-only policy without changing worker files."""
    policy = read(root / 'policy.json')
    if capacity_path is not None:
        updates = read(Path(capacity_path))
        if not isinstance(updates, dict) or set(updates) - CAPACITY_POLICY_KEYS:
            raise ValueError('Capacity policy may only change scheduling limits')
        policy.update(updates)
    ceiling, initial = policy.get('worker_ceiling', 100), policy.get('initial_workers', 100)
    if (type(ceiling) is not int or type(initial) is not int or
            not 1 <= initial <= ceiling <= MAX_WORKERS):
        raise ValueError('Worker capacity must be between 1 and 1000')
    return policy


def bounded_query(root, constraint, attributes=None, *, role='controller', _reader=None, **kwargs):
    """Retry a read-only scheduler query before treating an outage as fatal.

    Never retry submit, hold, release or edit here: their first result may be
    ambiguous and a repeated mutation could change the campaign twice.
    """
    state_path = root / ('watchdog_state.json' if role == 'watchdog' else 'controller_state.json')
    previous = read(state_path) if state_path.exists() else {}
    uncertain = previous.get('scheduler_state') == 'unknown'
    reader = query if _reader is None else _reader
    for attempt in range(1, 4):
        try:
            ads = reader(constraint, attributes, **kwargs)
        except NativeCondorError as error:
            uncertain = True
            state = read(state_path) if state_path.exists() else {}
            state.update(checked_at_epoch=time.time(), scheduler_state='unknown', health='unknown',
                         health_before_unknown=state.get('health_before_unknown', state.get('health', 'healthy')),
                         scheduler_query_attempt=attempt, scheduler_query_error=repr(error))
            save(state_path, state)
            if attempt == 3:
                raise
            time.sleep(10)
            continue
        if uncertain:
            state = read(state_path) if state_path.exists() else {}
            prior_health = state.pop('health_before_unknown', 'healthy')
            state.update(checked_at_epoch=time.time(), scheduler_state='known',
                         health='blocked' if state.get('phase') == 'blocked' else prior_health,
                         scheduler_query_attempt=attempt)
            state.pop('scheduler_query_error', None)
            save(state_path, state)
        return ads


def problem(ad, now, policy):
    status = ad.get('JobStatus')
    if status in (3, 5):
        return 'Job removed or held: ' + str(ad)
    if status == 4 and (ad.get('ExitBySignal') or ad.get('ExitCode', 0) != 0):
        return 'Job exited unsuccessfully: ' + str(ad)
    queued_at = max(ad.get('EnteredCurrentStatus') or 0, ad.get('JobMaterializeDate') or 0,
                    ad['QDate'] if ad.get('QDate') is not None else now)
    if status == 1 and now - queued_at > policy['queue_timeout_seconds']:
        return 'Job has waited in the queue too long: ' + str(ad)
    if status == 2:
        start = ad.get('JobCurrentStartDate') or now
        if now - start > policy['worker_timeout_seconds']:
            return 'Worker exceeded total time limit: ' + str(ad)
        deadline = ad.get('ShiftNtupleStageDeadlineEpoch')
        progress = ad.get('ShiftNtupleProgressEpoch') or start
        reported = ad.get('ShiftNtupleProgressEpoch')
        if (deadline is not None and deadline > 0 and deadline >= start and
                reported is not None and reported >= start):
            if now > deadline + 30:
                return 'Worker exceeded its stage wall-time limit: ' + str(ad)
            # Stage attributes arrive in separate chirps. A new short audit
            # timeout can temporarily coexist with the previous event epoch.
            # The worker independently enforces its per-event progress guard.
            return None
        limit = ad.get('ShiftNtupleStageTimeout') or policy['setup_timeout_seconds']
        # ClassAds can retain telemetry from an earlier attempt.  Apply it
        # only after this attempt has reported, otherwise use its setup guard.
        if reported is None or reported < start:
            progress = start
            limit = policy['setup_timeout_seconds']
        if now - progress > limit + 180:
            return 'Worker stopped making progress within its stage limit: ' + str(ad)
    return None


def recoverable_query(root, constraint, attributes=None, *, role='controller', **kwargs):
    """Keep read outages distinct from worker or storage failures.

    A failed read supplies no evidence that running workers must be stopped.
    Keep a live heartbeat and retry the same read. No submit, release, edit,
    capacity increase or worker mutation is authorized by an unknown result.
    Execute-side periodic guards continue to bound individual workers.
    """
    while True:
        try:
            return bounded_query(root, constraint, attributes, role=role, **kwargs)
        except NativeCondorError as error:
            path = root / ('watchdog_state.json' if role == 'watchdog' else 'controller_state.json')
            state = read(path) if path.exists() else {}
            state.update(checked_at_epoch=time.time(), health='unknown',
                         scheduler_state='unknown', attention_required=True,
                         activity='retrying_scheduler_read_without_worker_mutations')
            save(path, state)
            policy = read(root / 'policy.json') if (root / 'policy.json').exists() else {}
            if policy.get('attention_email'):
                notify_once(root, policy['attention_email'], str(error))
            time.sleep(30)


def reservation(results, manifest, free_bytes, free_files):
    by_stratum = {}
    for result in results:
        by_stratum.setdefault(result['source_stratum'],[]).append(result)
    if set(by_stratum) != set(manifest['strata']):
        raise ValueError('Every production stratum needs a passed canary')
    jobs = {s: 0 for s in manifest['strata']}
    for source in manifest['sources']:
        jobs[source['stratum']] += (source['events'] + manifest['events_per_job'] - 1) // manifest['events_per_job']
    projected = 0
    for stratum, events in manifest['strata'].items():
        rows = by_stratum[stratum]
        event_size = max(r['compressed_event_bytes']/r['events'] for r in rows)
        fixed_size = max(max(0,r['nano_bytes']-r['compressed_event_bytes']) for r in rows)
        evidence_size=max(500000,max(r['evidence_bytes'] for r in rows))
        projected += event_size * events + (fixed_size+evidence_size) * jobs[stratum]
    projected = int(2 * projected)
    required_files = 3 * manifest['jobs'] + manifest['minimum_free_files']
    if free_bytes < projected + manifest['minimum_free_bytes'] or free_files < required_files:
        raise ValueError(f'EOS reservation failed: projected={projected}, free={free_bytes}, required_files={required_files}, free_files={free_files}')
    return projected


def quota(root, *, local_schedd=True, heartbeat=True):
    """Obtain fresh quota evidence, replacing only confirmed completed audits."""
    while True:
        result = _quota_attempt(root, local_schedd=local_schedd, heartbeat=heartbeat)
        if result is not None:
            return result


def launch_quota_audit(root, *, local_schedd=True, persistent=False):
    # Scheduler hosts have no EOS client. Execute this small audit on a worker,
    # where the same EOS environment used by production has been verified.
    nonce = uuid.uuid4().hex
    folder = root / 'quota_audits' / nonce
    folder.mkdir(parents=True)
    auditor = Path(__file__).resolve().with_name('run_shift_quota_audit.py')
    shutil.copy2(auditor,folder/'audit.py')
    native = Path(__file__).resolve().with_name('shift_condor_native.py')
    shutil.copy2(native,folder/'shift_condor_native.py')
    policy=read(root/'policy.json') if (root/'policy.json').exists() else {}
    save(folder/'quota_request.json',{'nonce':nonce,
         'account_owner':policy.get('account_owner',pwd.getpwuid(os.getuid()).pw_name),
         'account_pool':policy.get('account_pool','tweetybird04.cern.ch')})
    (folder/'quota.sub').write_text(f'''universe = vanilla
initialdir = {folder}
executable = /usr/bin/python3
arguments = audit.py quota_request.json
should_transfer_files = YES
when_to_transfer_output = ON_EXIT
transfer_input_files = {folder}/audit.py,{folder}/shift_condor_native.py,{folder}/quota_request.json
transfer_output_files = quota_result.json
getenv = False
notification = Never
request_cpus = 1
request_memory = 512
request_disk = 1000
priority = 100
+MaxRuntime = 300
+ShiftNtupleQuotaAudit = True
+ShiftNtupleAuditNonce = "{nonce}"
+JobBatchName = "shift_ntuple_quota_audit"
output = audit.out
error = audit.err
log = audit.log
on_exit_hold = (ExitBySignal == True) || (ExitCode != 0)
periodic_release = False
queue 1
''')
    pending = dict(folder=str(folder), nonce=nonce, cluster=None,
                   submitted_at_epoch=time.time(), status='submitting')
    if persistent:
        save(root/'pending_quota.json', pending)
    response = run_condor(['/usr/bin/condor_submit','-terse',str(folder/'quota.sub')],
                          local_schedd=local_schedd,timeout=30,cwd=folder)
    match = re.fullmatch(r'(\d+)\.\d+\s*-\s*\d+\.\d+\s*',response)
    if not match:
        raise RuntimeError('Ambiguous quota audit submission: '+response)
    cluster = int(match[1])
    save(folder/'submission.json',{'cluster':cluster,'nonce':nonce})
    pending.update(cluster=cluster, status='pending')
    if persistent:
        save(root/'pending_quota.json', pending)
    return pending


def _quota_attempt(root, *, local_schedd=True, heartbeat=True):
    pending = launch_quota_audit(root, local_schedd=local_schedd)
    folder, cluster, nonce = Path(pending['folder']), pending['cluster'], pending['nonce']
    deadline = time.time()+900
    while True:
        if heartbeat:
            state_path = root/'controller_state.json'
            state = read(state_path) if state_path.exists() else {'phase':'quota_check'}
            state.update(checked_at_epoch=time.time(),quota_audit_cluster=cluster)
            save(state_path,state)
        path = folder/'quota_result.json'
        stale_result = None
        if path.exists():
            result=read(path)
            if result.get('nonce')!=nonce or not result.get('complete'):
                raise RuntimeError('EOS quota audit failed: '+json.dumps(result))
            if time.time()-result['checked_at_epoch']<=120:
                save(root/'last_quota.json',result)
                return result['free_bytes'],result['free_files']
            stale_result = result
        ads=recoverable_query(root,f'ClusterId == {cluster}',['JobStatus','HoldReason','ExitCode','ExitBySignal'],
                  local_schedd=local_schedd,timeout=30)
        if any(ad['JobStatus'] in (3,5) for ad in ads):
            raise RuntimeError('Quota audit removed or held: '+str(ads))
        if any(ad['JobStatus']==4 and (ad.get('ExitBySignal') or ad.get('ExitCode',0)!=0) for ad in ads):
            raise RuntimeError('Quota audit exited unsuccessfully: '+str(ads))
        if stale_result is not None and (not ads or all(ad['JobStatus']==4 for ad in ads)):
            # A scheduler read outage can outlast freshness and polling limits.
            # Preserve this completed audit, then obtain a new one. Never
            # duplicate an audit whose terminal state remains unverified.
            save(folder/'refresh.json',{'reason':'completed_result_expired_during_scheduler_read',
                 'cluster':cluster,'nonce':nonce,'refreshed_at_epoch':time.time(),
                 'result_checked_at_epoch':stale_result['checked_at_epoch']})
            return None
        if stale_result is None and path.exists():
            # The result may have arrived while the recoverable read waited.
            # Inspect its nonce and status before acting on the old deadline.
            continue
        if time.time()>=deadline:
            run_condor(['/usr/bin/condor_hold',str(cluster)],local_schedd=local_schedd,timeout=30)
            raise TimeoutError('EOS quota audit did not finish within fifteen minutes')
        time.sleep(15)


def refresh_quota(root):
    """Poll one durable audit without delaying supervision or stopping workers.

    A queued, failed or unavailable audit means unknown storage/account state.
    It cannot establish an exhausted quota.  Preserve its evidence, freeze
    growth, and retry only after the previous audit is confirmed terminal.
    """
    pending_path = root/'pending_quota.json'
    pending = read(pending_path) if pending_path.exists() else {}
    snapshot_path = root/'last_quota.json'
    snapshot = read(snapshot_path) if snapshot_path.exists() else {}
    now = time.time()
    if pending.get('status') not in ('pending', 'submitting'):
        checked = snapshot.get('quota_checked_at_epoch', snapshot.get('checked_at_epoch', 0)) or 0
        if snapshot.get('complete') and now-checked < 300:
            return snapshot
        if now < pending.get('retry_after_epoch', 0):
            return snapshot
        try:
            pending = launch_quota_audit(root, persistent=True)
        except Exception as error:
            # Submission may have succeeded despite a lost response.  Keep
            # its nonce and reconcile the queue; never submit it blindly twice.
            pending = read(pending_path) if pending_path.exists() else pending
            pending.update(error=repr(error), checked_at_epoch=now)
            save(pending_path, pending)
            return snapshot
    folder = Path(pending['folder'])
    result_path = folder/'quota_result.json'
    try:
        if result_path.exists():
            result = read(result_path)
            if result.get('nonce') != pending['nonce']:
                raise ValueError('Quota result nonce mismatch')
            if not result.get('complete'):
                raise ValueError('Quota observation failed: '+str(result.get('error')))
            if any(type(result.get(key)) not in (int,float) or not math.isfinite(result[key])
                   for key in ('free_bytes','free_files','checked_at_epoch')):
                raise ValueError('Quota result has invalid numeric fields')
            if not 0 <= now-result['checked_at_epoch'] <= 600:
                raise ValueError('Quota result expired before collection')
            save(snapshot_path, result)
            pending.update(status='complete', checked_at_epoch=now)
            save(pending_path, pending)
            return result
        if pending.get('cluster') is None:
            ads = bounded_query(root, 'ShiftNtupleQuotaAudit == true && ShiftNtupleAuditNonce == '+json.dumps(pending['nonce']),
                                ['ClusterId','JobStatus'], local_schedd=True,timeout=30)
            clusters = {ad['ClusterId'] for ad in ads}
            if len(clusters) != 1:
                pending.update(error='Audit submission identity remains unknown', checked_at_epoch=now)
                save(pending_path,pending)
                return snapshot
            pending['cluster'] = clusters.pop()
            pending['status'] = 'pending'
        ads = bounded_query(root, f'ClusterId == {pending["cluster"]}',
                            ['JobStatus','HoldReason','JobCurrentStartDate'],local_schedd=True,timeout=30)
        pending.update(checked_at_epoch=now, queue=ads)
        if any(ad['JobStatus'] in (3,4,5) for ad in ads) or (
                not ads and now-pending['submitted_at_epoch'] > 180):
            pending.update(status='failed', error='Audit ended without a valid result', retry_after_epoch=now+60)
        # An idle audit stays pending regardless of queue delay. Its own
        # execute-side MaxRuntime bounds a running audit independently.
        save(pending_path, pending)
    except Exception as error:
        pending.update(error=repr(error), checked_at_epoch=now)
        # Invalid transferred output alone does not prove terminal state.
        # Query it next time; preserve identity so no duplicate is submitted.
        if result_path.exists():
            try:
                ads = bounded_query(root,f'ClusterId == {pending["cluster"]}', ['JobStatus'],
                                    local_schedd=True,timeout=30) if pending.get('cluster') is not None else [{}]
                if not ads or all(ad.get('JobStatus') in (3,4,5) for ad in ads):
                    pending.update(status='failed', retry_after_epoch=now+60)
            except NativeCondorError:
                pass
        save(pending_path,pending)
    return snapshot


def notify_once(root, recipient, reason):
    """Claim the single alert durably before sending, including across restarts."""
    marker = root / 'attention_email.json'
    try:
        with marker.open('x') as stream:
            json.dump({'recipient': recipient, 'attempted_at': time.time(), 'reason': reason}, stream)
    except FileExistsError:
        return
    message = EmailMessage()
    message['To'] = recipient
    message['From'] = 'jniedzie@cern.ch'
    message['Subject'] = 'SHIFT production needs attention'
    message.set_content('The SHIFT GEN-to-Nano production needs attention because:\n\n' + reason +
                        '\n\nCampaign state and preserved evidence:\n' + str(root) +
                        '\n\nFailed workers are not automatically resubmitted. This is the only alert for this campaign.\n')
    mailer = shutil.which('sendmail') or '/usr/sbin/sendmail'
    try:
        subprocess.run([mailer, '-t', '-oi'], input=message.as_bytes(), check=True, timeout=30)
        save(marker, {'recipient': recipient, 'submitted_at': time.time(), 'reason': reason,
                      'status': 'accepted_by_local_mailer'})
    except Exception as error:
        save(marker, {'recipient': recipient, 'reason': reason, 'status': 'delivery_failed', 'error': repr(error)})


def submit(root, name, *, materialize_limit=None):
    intent = root / (name + '_submission_intent.json')
    if intent.exists():
        raise RuntimeError('Submission already attempted; reconcile queue/history before restarting: ' + str(intent))
    save(intent, {'attempted_at': time.time()})
    command = ['/usr/bin/condor_submit', '-terse']
    if materialize_limit is not None:
        if not isinstance(materialize_limit,int) or materialize_limit < 1:
            raise ValueError('A positive factory limit is required; zero means unlimited')
        command.extend(['-append','max_materialize = '+str(materialize_limit)])
    command.append(str(root / (name + '.sub')))
    response = run_condor(command,
                          local_schedd=True, timeout=60, cwd=root)
    match = re.fullmatch(r'(\d+)\.\d+\s*-\s*\d+\.\d+\s*', response)
    if not match:
        raise RuntimeError('Ambiguous submission result: ' + response)
    return int(match.group(1))


def update_submission(root, **updates):
    # The launcher may register the watchdog after this process has started.
    # Merge only fields we own into the latest record, never our old snapshot.
    submitted = read(root / 'submission.json')
    submitted.update(updates)
    save(root / 'submission.json', submitted)
    return submitted


LIVE_WORKER_STATES = (1,2,6,7)


def account_workers(root, policy):
    """Use nonce-verified account coverage from a credentialed execute audit."""
    owner = policy.get('account_owner', pwd.getpwuid(os.getuid()).pw_name)
    pool=policy.get('account_pool','tweetybird04.cern.ch')
    path=root/'last_quota.json'
    snapshot=read(path) if path.exists() else {}
    checked=snapshot.get('account_checked_at_epoch',snapshot.get('checked_at_epoch',0))
    if time.time()-checked>600 and not policy.get('asynchronous_audits'):
        quota(root)
        snapshot=read(path) if path.exists() else {}
        checked=snapshot.get('account_checked_at_epoch',snapshot.get('checked_at_epoch',0))
    if (not snapshot.get('nonce') or not snapshot.get('complete') or
        not snapshot.get('account_coverage_verified') or time.time()-checked>600 or
        snapshot.get('account_owner')!=owner or snapshot.get('account_pool')!=pool or
        not isinstance(snapshot.get('account_workers'),list)):
        raise NativeCondorError('Credentialed account coverage is unavailable: '+str(snapshot.get('account_error','missing or stale account audit')))
    return snapshot['account_workers']


def external_workers(ads, cluster, policy):
    schedd = policy.get('schedd_name', socket.getfqdn())
    return sum(ad.get('JobUniverse',5)==5 and ad.get('JobStatus') in LIVE_WORKER_STATES and
               not (ad.get('ClusterId') == cluster and ad.get('_queried_schedd',schedd) == schedd)
               for ad in ads)


def recovery_reservation(ads, submitted, completed=(), *, policy=None):
    """Reserve recovery factories' allocation even before they materialize.

    A cached account snapshot can otherwise miss their next worker starts.
    Only add the part not already counted as live external workers.
    """
    budgets = submitted.get('recovery_worker_budgets', {})
    schedd = (policy or {}).get('schedd_name', socket.getfqdn())
    extra = 0
    for cluster, budget in budgets.items():
        if type(budget) is not int or not 0 <= budget <= MAX_WORKERS:
            raise ValueError('Invalid recovery worker reservation')
        identities = submitted.get('recovery_job_ids', {}).get(str(cluster))
        if identities and set(identities).issubset(completed):
            continue
        # A finished recovery factory must not keep reserving its original
        # peak allocation.  Reserve only the exact unfinished identities.
        reserved = min(budget, len(set(identities) - set(completed))) if identities else budget
        live = sum(ad.get('ClusterId') == int(cluster) and
                   ad.get('_queried_schedd', schedd) == schedd and
                   ad.get('JobUniverse', 5) == 5 and ad.get('JobStatus') in LIVE_WORKER_STATES
                   for ad in ads)
        extra += max(0, reserved - live)
    return extra


def capacity_plan(policy, previous, *, now, completed_jobs, ads, healthy,
                  quota_verified, account_verified, external):
    """Grow only after sustained useful progress; reserve other live workers."""
    ceiling = int(policy.get('worker_ceiling',100))
    initial = min(ceiling,int(policy.get('initial_workers',100)))
    if not 1 <= initial <= ceiling <= MAX_WORKERS:
        raise ValueError('Worker capacity must be between 1 and 1000')
    state = dict(previous or {})
    capacity = min(ceiling,max(initial,int(state.get('current_capacity',initial))))
    changed_at = state.get('last_capacity_change_epoch',now)
    completed_at_change = state.get('completed_at_change',completed_jobs)
    active = [ad for ad in ads if ad.get('JobStatus') in LIVE_WORKER_STATES]
    idle_fraction = sum(ad.get('JobStatus') == 1 for ad in active) / len(active) if active else 1.0
    reason = 'waiting_for_progress_gate'
    if not healthy:
        if capacity != initial:
            capacity=initial;changed_at=now;completed_at_change=completed_jobs
        reason='growth_frozen_by_worker_incident'
    elif not account_verified:
        reason='growth_frozen_by_unknown_account_coverage'
    elif not quota_verified:
        reason='growth_frozen_by_unverified_quota'
    elif idle_fraction > policy.get('capacity_idle_fraction',0.25):
        reason='growth_frozen_by_idle_workers'
    elif (policy.get('adaptive_capacity') and capacity < ceiling and
          now-changed_at >= policy.get('capacity_interval_seconds',1200) and
          completed_jobs-completed_at_change >= policy.get('capacity_completions',10)):
        capacity=min(ceiling,capacity+policy.get('capacity_step',50))
        changed_at=now;completed_at_change=completed_jobs;reason='capacity_increased'
    elif capacity == ceiling:
        reason='at_worker_ceiling'
    available=max(0,capacity-external) if account_verified else 0
    state.update(current_capacity=capacity,worker_ceiling=ceiling,initial_workers=initial,
                 last_capacity_change_epoch=changed_at,completed_at_change=completed_at_change,
                 checked_at_epoch=now,completed_jobs=completed_jobs,idle_fraction=idle_fraction,
                 external_live_workers=external if account_verified else None,account_coverage_verified=account_verified,
                 quota_verified=quota_verified,reason=reason,factory_budget=available,
                 materialization_paused=available==0,growth_frozen=reason.startswith('growth_frozen'))
    return state


def query_factory(root, cluster, attributes=None):
    """Read the factory itself, including before any workers materialize.

    A verified empty result means the factory has retired. Query failures stay
    unknown and retry without permitting worker or allocation changes.
    """
    attributes = attributes or ['ClusterId', 'JobMaterializeLimit', 'JobMaterializeMaxIdle']
    def factory_reader(constraint, fields, **kwargs):
        command = ['/usr/bin/condor_q', '-factory', '-constraint', constraint, '-json',
                   '-attributes', ','.join(fields)]
        return parse_ads(run_condor(command, **kwargs))
    return recoverable_query(root, f'ClusterId == {cluster}', attributes,
                             _reader=factory_reader, local_schedd=True, timeout=30)


def set_factory_budget(root, cluster, limit, max_idle):
    # max_materialize <= 0 means unlimited. max_idle = 0 safely suspends
    # future materialization while preserving all already running jobs.
    if not isinstance(limit,int) or not 0 <= limit <= MAX_WORKERS or not isinstance(max_idle,int) or max_idle < 1:
        raise ValueError('Factory budget must be 0 to 1000 with a positive idle allowance')
    # Held jobs count toward MaxIdle.  Allow the policy to expose the full
    # bounded factory capacity, otherwise a small set of quarantined attempts
    # can silently starve all remaining work.
    attributes={'JobMaterializeLimit':max(1,limit), 'JobMaterializeMaxIdle':min(max_idle,limit) if limit else 0}
    command=['/usr/bin/condor_qedit',str(cluster)]
    for name,value in attributes.items():command.extend([name,str(value)])
    run_condor(command,local_schedd=True,timeout=30)
    rows=recoverable_query(root,f'ClusterId == {cluster}',list(attributes),local_schedd=True,timeout=30)
    if not rows:
        rows=query_factory(root,cluster,list(attributes))
    if not any(all(row.get(name)==value for name,value in attributes.items()) for row in rows):
        raise RuntimeError('Factory materialization budget was not confirmed')
    return attributes


def record_capacity_wait(root, policy, other_workers):
    waiting={'current_capacity':policy['initial_workers'],'worker_ceiling':policy['worker_ceiling'],
             'factory_budget':0,'external_live_workers':other_workers,'checked_at_epoch':time.time(),
             'account_coverage_verified':True,'reason':'waiting_for_other_workers'}
    save(root/'capacity_state.json',waiting)
    current=read(root/'controller_state.json') if (root/'controller_state.json').exists() else {}
    save(root/'controller_state.json',{**current,'checked_at_epoch':time.time(),
         'activity':'waiting_for_other_SHIFT_workers','capacity':waiting})


def activate_deferred_recovery(root, policy, submitted, ads, covered):
    """Admit registered recovery work only after the prior allocation drains."""
    if not submitted.get('deferred_recovery_clusters'):
        return submitted
    schedd = policy.get('schedd_name', socket.getfqdn())
    # The two snapshots can contain disjoint workers or equal numeric IDs on
    # different schedds. Their union is conservative while cached jobs drain;
    # taking only the larger count can admit work above the account ceiling.
    allocated = {(ad.get('_queried_schedd',schedd), ad['ClusterId'], ad['ProcId'])
                 for ad in [*ads,*covered] if ad.get('JobStatus') in LIVE_WORKER_STATES}
    reserved_count = len(allocated)
    for deferred in list(submitted.get('deferred_recovery_clusters',[])):
        budget = submitted['recovery_worker_budgets'][str(deferred)]
        if reserved_count+budget <= policy['worker_ceiling']:
            set_factory_budget(root,int(deferred),budget,budget)
            remaining = [value for value in submitted['deferred_recovery_clusters'] if value != deferred]
            submitted = update_submission(root,deferred_recovery_clusters=remaining)
            reserved_count += budget
    return submitted


def record_account_unknown(root, policy, error):
    path=root/'capacity_state.json'
    previous=read(path) if path.exists() else {}
    state={**previous,'checked_at_epoch':time.time(),'account_coverage_verified':False,
           'growth_frozen':True,'reason':'growth_frozen_by_unknown_account_coverage','account_error':str(error)}
    # Keep the last confirmed allocation. Unknown coverage cannot authorize an
    # increase, and does not require killing or draining healthy production.
    save(path,state)
    current=read(root/'controller_state.json') if (root/'controller_state.json').exists() else {}
    save(root/'controller_state.json',{**current,'checked_at_epoch':time.time(),'health':'unknown',
         'attention_required':True,'activity':'account_coverage_unavailable_growth_frozen','capacity':state})
    notify_once(root,policy['attention_email'],str(error))
    return state


def validated_receipt(result, job):
    """A successful process exit is insufficient without every tier receipt."""
    if result.get('job') != job or result.get('exit_code') != 0 or not result.get('complete'):
        return 'Worker failed: ' + json.dumps(result)
    if not result.get('nano_path') or not result.get('report_sha256'):
        return 'Worker did not publish a validated receipt: ' + str(job)
    events = result.get('events')
    if not isinstance(events, int) or events <= 0 or result.get('validated_tier_events') != {
            tier: events for tier in ('GEN', 'SIM', 'DIGIHLT', 'RECO', 'NANO')}:
        return 'Missing tier validation: ' + str(job)
    return None


def record_incident(root, policy, identity, reason, *, receipt=None, ad=None):
    """Quarantine a worker failure without stopping unrelated production."""
    path = root / 'worker_incidents.json'
    ledger = read(path) if path.exists() else {'jobs': {}}
    identity = str(identity)
    now = time.time()
    previous = ledger['jobs'].get(identity)
    record = dict(previous or {}, identity=identity, reason=reason, active=True,
                  first_seen_epoch=(previous or {}).get('first_seen_epoch', now),
                  last_seen_epoch=now)
    if receipt is not None:
        record.update(job=receipt.get('job'), source_stratum=receipt.get('source_stratum'), receipt=receipt)
    if ad is not None:
        record.update(cluster=ad.get('ClusterId'), proc=ad.get('ProcId'),
                      job=ad.get('ShiftNtupleJob', record.get('job')), queue_ad=ad)
    ledger['jobs'][identity] = record
    ledger['updated_at_epoch'] = now
    save(path, ledger)
    if previous is None or not previous.get('active', True):
        notify_once(root, policy['attention_email'], reason)


def resolve_incidents(root, jobs):
    """Resolve a receipt batch with one ledger read, including queue identities."""
    jobs = set(jobs)
    path = root / 'worker_incidents.json'
    if not jobs or not path.exists():
        return
    ledger = read(path)
    changed = False
    resolved_at = time.time()
    for record in ledger['jobs'].values():
        if record.get('job') in jobs and record.get('active', True):
            record.update(active=False, resolved_at_epoch=resolved_at)
            changed = True
    if changed:
        save(path, ledger)


def receipt_heartbeat(root, phase, previous):
    """Keep both receipt reading and validation visible to the watchdog."""
    now = time.monotonic()
    if now - previous > 30:
        path = root / 'controller_state.json'
        current = read(path) if path.exists() else {}
        save(path, {**current, 'phase':phase, 'checked_at_epoch':time.time(),
                    'activity':'reconciling preserved worker receipts'})
        return now
    return previous


def active_incidents(root):
    path = root / 'worker_incidents.json'
    return [row for row in read(path)['jobs'].values() if row.get('active', True)] if path.exists() else []


def isolate_worker(root, policy, ad, reason):
    """Stop only a timed-out worker; held/terminal workers already stopped."""
    job = ad.get('ShiftNtupleJob')
    identity = job if job is not None else str(ad['ClusterId']) + '.' + str(ad['ProcId'])
    if ad.get('JobStatus') in (1, 2):
        worker_id = str(ad['ClusterId']) + '.' + str(ad['ProcId'])
        run_condor(['/usr/bin/condor_hold', worker_id], local_schedd=True, timeout=30)
    record_incident(root, policy, identity, reason, ad=ad)


def canary_proofs(root, policy):
    """Load explicitly supplied passed checks from an identical frozen recipe."""
    configured = policy.get('validated_canary_results')
    path = Path(configured) if configured else root / 'canary_results'
    if not path.is_absolute():
        path = root / path
    if not path.exists():
        if configured:
            raise ValueError('Validated canary evidence is missing: ' + str(path))
        return []
    if path.is_dir():
        return [read(item) for item in sorted(path.glob('status*.json'))]
    rows = read(path)
    if isinstance(rows, dict):
        rows = rows.get('results', rows.get('validated_canary_results'))
    if not isinstance(rows, list):
        raise ValueError('Validated canary evidence must contain receipt objects')
    return rows


def supervise(root, policy=None):
    manifest = read(root / 'manifest.json')
    policy = effective_policy(root) if policy is None else policy
    submitted = read(root / 'submission.json')
    # Submitted clusters, rather than a transient in-memory phase, are the
    # durable transition record. Re-entry must never resubmit a released gate.
    phase = 'bulk' if submitted.get('bulk_cluster') is not None else (
        'pilot' if submitted.get('pilot_cluster') is not None else 'canaries')
    previous = read(root / 'controller_state.json') if (root / 'controller_state.json').exists() else {}
    started = previous.get('phase_started_at_epoch', time.time()) if previous.get('phase') == phase else time.time()
    completed = {}
    stages = {s: {tier: 0 for tier in ('GEN', 'SIM', 'DIGIHLT', 'RECO', 'NANO')}
              for s in manifest['strata']}
    cluster = submitted.get(phase.replace('canaries', 'canary') + '_cluster')
    last_completion = started
    last_quota_check = started - 601
    quota_path=root/'last_quota.json'
    if quota_path.exists():
        cached=read(quota_path)
        checked=cached.get('quota_checked_at_epoch',cached.get('checked_at_epoch',0))
        if cached.get('nonce') and cached.get('complete') and 0 <= time.time()-checked <= 600:
            free,files=cached['free_bytes'],cached['free_files']
            if free<manifest['minimum_free_bytes'] or files<manifest['minimum_free_files']:
                raise GlobalProductionError('Preserved EOS audit reports exhausted safety headroom')
            last_quota_check=checked
    expected = set(range(manifest['jobs'])) if phase == 'bulk' else set(
        manifest['pilot_jobs'] if phase == 'pilot' else submitted.get('canary_jobs', []))
    proofs = canary_proofs(root, policy) if phase == 'canaries' else []
    if proofs and cluster is None:
        expected = {row['job'] for row in proofs}
    if not expected:
        raise ValueError('No expected validation or production jobs are recorded')
    factory_limit=None
    factory_configuration=None
    managed_capacity='worker_ceiling' in policy
    capacity_state=None
    while True:
        now = time.time()
        # Repairs can register disjoint recovery factories while this
        # observer remains alive. Always query and reserve the current list.
        submitted = read(root/'submission.json')
        if (root / 'stop_requested.json').exists():
            raise GlobalProductionError('Campaign stop was explicitly requested')
        if policy.get('asynchronous_audits'):
            cached = refresh_quota(root)
            checked = cached.get('quota_checked_at_epoch',cached.get('checked_at_epoch',0)) or 0
            if cached.get('complete') and 0 <= now-checked <= 600:
                free, files = cached['free_bytes'], cached['free_files']
                if free < manifest['minimum_free_bytes'] or files < manifest['minimum_free_files']:
                    raise GlobalProductionError(f'EOS safety headroom exhausted: free_bytes={free}, free_files={files}')
                last_quota_check = checked
        elif now - last_quota_check > 600:
            free, files = quota(root)
            now = time.time()
            if free < manifest['minimum_free_bytes'] or files < manifest['minimum_free_files']:
                raise GlobalProductionError(f'EOS safety headroom exhausted: free_bytes={free}, free_files={files}')
            last_quota_check = now
        # Reading tens of thousands of completed AFS receipts every minute
        # would itself delay the controller heartbeat. Completed IDs are final.
        receipts = []
        scan_heartbeat = time.monotonic()
        for path in sorted((root / 'results').glob('status*.json')):
            job = int(path.stem[6:])
            if job in expected and job not in completed:
                receipts.append((job, read(path)))
            scan_heartbeat = receipt_heartbeat(root, phase, scan_heartbeat)
        if phase == 'canaries':
            receipts += [(row['job'], row) for row in proofs]
        accepted_jobs = []
        for job, result in receipts:
            scan_heartbeat = receipt_heartbeat(root, phase, scan_heartbeat)
            if job in completed:
                continue
            if job not in expected:
                continue  # Validation samples and different phases do not count.
            error = validated_receipt(result, job)
            if not error and result.get('source_stratum') not in manifest['strata']:
                error = 'Worker receipt has an unknown production stratum: ' + str(job)
            if error:
                record_incident(root, policy, job, error, receipt=result)
                continue
            completed[job] = result
            accepted_jobs.append(job)
            for tier, count in result['validated_tier_events'].items():
                stages[result['source_stratum']][tier] += count
            last_completion = now
        resolve_incidents(root, accepted_jobs)
        attrs = ['ClusterId','ProcId','JobStatus','QDate','JobMaterializeDate','EnteredCurrentStatus','JobCurrentStartDate','HoldReason',
                 'ShiftNtupleJob','ShiftNtupleTier','ShiftNtupleProgressEpoch','ShiftNtupleStageTimeout',
                 'ShiftNtupleStageDeadlineEpoch','ShiftNtupleStratum','ShiftNtupleStageRecordsStarted',
                 'ShiftNtupleGENEvents','ShiftNtupleSIMEvents','ShiftNtupleDIGIHLTEvents',
                 'ShiftNtupleRECOEvents','ShiftNtupleNANOEvents','ExitCode','ExitBySignal']
        constraint=f'ClusterId == {cluster}'
        if phase == 'bulk':
            for recovery_cluster in submitted.get('recovery_clusters', []):
                constraint += f' || ClusterId == {int(recovery_cluster)}'
        if phase=='bulk' and submitted.get('pilot_cluster'):
            constraint+=f' || ClusterId == {submitted["pilot_cluster"]}'
        ads = recoverable_query(root,constraint, attrs, local_schedd=True, timeout=30) if cluster is not None else []
        for ad in ads:
            if ad.get('ShiftNtupleJob') in completed:
                continue
            error = problem(ad, now, policy)
            if error:
                isolate_worker(root, policy, ad, error)
        incidents = active_incidents(root)
        if phase=='bulk' and managed_capacity and set(completed) != expected:
            capacity_path=root/'capacity_state.json'
            try:
                covered = account_workers(root,policy)
            except NativeCondorError as error:
                capacity_state=record_account_unknown(root,policy,error)
            else:
                other_workers = external_workers(covered,cluster,policy) + recovery_reservation(covered,submitted,completed,policy=policy)
                capacity_state=capacity_plan(policy,read(capacity_path) if capacity_path.exists() else {},
                    now=time.time(),completed_jobs=len(completed),ads=[ad for ad in ads if ad['ClusterId']==cluster],
                    healthy=not incidents,quota_verified=time.time()-last_quota_check<=600,
                    account_verified=True,external=other_workers)
                configuration=(capacity_state['factory_budget'],policy.get('max_idle_workers',100))
                main_factory_present = bool(query_factory(root, cluster))
                capacity_state['main_factory_present'] = main_factory_present
                if main_factory_present and configuration != factory_configuration:
                    capacity_state['factory_attributes']=set_factory_budget(root,cluster,*configuration)
                    factory_configuration=configuration
                elif not main_factory_present:
                    # Condor reaps an exhausted factory after its last proc
                    # exits, while disjoint recovery factories may still run.
                    # Its disappearance is not evidence of campaign completion.
                    capacity_state.update(factory_budget=0, materialization_paused=True,
                                          reason='main_factory_retired_recovery_observed')
                    capacity_state.pop('factory_attributes', None)
                save(capacity_path,capacity_state)
                # Fresh recovery factories can be queued without materializing
                # workers until the previous allocation has actually drained.
                submitted = activate_deferred_recovery(root,policy,submitted,ads,covered)
        elif phase=='bulk' and manifest.get('pilot_jobs') and set(completed) != expected:
            outstanding_pilot=sum(ad['JobStatus'] in (1,2) for ad in ads if ad['ClusterId']==submitted['pilot_cluster'])
            benchmark_ads=[]
            if policy.get('benchmark_cluster'):
                benchmark_ads=recoverable_query(root,f'ClusterId == {policy["benchmark_cluster"]}',['JobStatus'],local_schedd=True,timeout=30)
            outstanding_bench=sum(ad['JobStatus'] in (1,2) for ad in benchmark_ads)
            limit=max(0,100-outstanding_pilot-outstanding_bench)
            if limit!=factory_limit and query_factory(root, cluster):
                set_factory_budget(root,cluster,limit,100)
                factory_limit=limit
        now=time.time()
        coverage_unknown=capacity_state is not None and capacity_state.get('account_coverage_verified') is False
        save(root / 'controller_state.json', {'phase': phase, 'checked_at_epoch': now,
             'phase_started_at_epoch':started,'health':'degraded' if incidents else ('unknown' if coverage_unknown else 'healthy'),
             'attention_required':bool(incidents) or coverage_unknown,'failed_jobs':len(incidents),
             'failed_job_ids':sorted({row['job'] for row in incidents if row.get('job') is not None}),
             'cluster': cluster, 'completed_jobs': len(completed), 'expected_jobs': len(expected),
             'completed_tier_events': stages, 'queue': ads, 'last_completion_epoch': last_completion,
             'capacity':capacity_state})
        pilot_ready=(phase=='pilot' and {r['source_stratum'] for r in completed.values()}==set(manifest['strata']))
        if set(completed) == expected or pilot_ready:
            if phase == 'bulk' or (phase == 'pilot' and set(completed) == set(range(manifest['jobs']))):
                if any(stages[stratum][tier] != count for stratum, count in manifest['strata'].items()
                       for tier in ('GEN','SIM','DIGIHLT','RECO','NANO')):
                    raise GlobalProductionError('Complete job receipts do not match the frozen event inventory')
                save(root / 'production_complete.json', {'complete': True, 'events': manifest['events'],
                     'jobs': manifest['jobs'], 'completed_at_epoch': now, 'tier_events': stages})
                save(root / 'controller_state.json', {'phase': 'complete', 'completed_at_epoch': now,
                     'completed_jobs': len(completed), 'completed_tier_events': stages})
                return
            free, files = quota(root)
            last_quota_check=time.time()
            if phase=='canaries' and manifest.get('pilot_jobs'):
                pilot_manifest={**manifest,'sources':manifest['pilot_sources'],'strata':manifest['pilot_strata'],
                                'jobs':len(manifest['pilot_jobs']),'events':manifest['pilot_events']}
                projected=reservation(list(completed.values()),pilot_manifest,free,files)
                if (root/'stop_requested.json').exists():
                    raise RuntimeError('Watchdog stopped the campaign before pilot release')
                if managed_capacity:
                    try:
                        covered=account_workers(root,policy)
                    except NativeCondorError as error:
                        record_account_unknown(root,policy,error)
                        time.sleep(60)
                        continue
                    other_workers=external_workers(covered,None,policy)
                    slots=min(len(manifest['pilot_jobs']),policy['initial_workers']-other_workers)
                    if slots<1:
                        record_capacity_wait(root,policy,other_workers)
                        time.sleep(60)
                        continue
                    cluster=submit(root,'pilot',materialize_limit=slots)
                else:
                    cluster=submit(root,'pilot')
                submitted=update_submission(root,pilot_cluster=cluster)
                save(root/'release_state.json',{'phase':'pilot_released','pilot_cluster':cluster,
                    'pilot_jobs':len(manifest['pilot_jobs']),'pilot_events':manifest['pilot_events'],
                    'projected_pilot_bytes':projected,'short_canaries_passed':len(completed),
                    'released_at_epoch':time.time()})
                phase='pilot'
                started=time.time()
                completed={}
                stages={s:{t:0 for t in ('GEN','SIM','DIGIHLT','RECO','NANO')} for s in manifest['strata']}
                expected=set(manifest['pilot_jobs'])
                last_completion=time.time()
                continue
            projected = reservation(list(completed.values()), manifest, free, files)
            benchmark = {r['source_stratum']: r for r in completed.values()}
            cpu_seconds = sum(benchmark[s['stratum']]['wall_seconds'] *
                max(1,manifest['events_per_job']/benchmark[s['stratum']]['events']) *
                ((s['events']+manifest['events_per_job']-1)//manifest['events_per_job']) for s in manifest['sources'])
            if (root / 'stop_requested.json').exists():
                raise RuntimeError('Watchdog stopped the campaign before bulk release')
            if managed_capacity:
                try:
                    covered=account_workers(root,policy)
                except NativeCondorError as error:
                    record_account_unknown(root,policy,error)
                    time.sleep(60)
                    continue
                other_workers=external_workers(covered,None,policy)
                slots=policy['initial_workers']-other_workers
                if slots<1:
                    record_capacity_wait(root,policy,other_workers)
                    time.sleep(60)
                    continue
                cluster=submit(root,'bulk',materialize_limit=slots)
                capacity_state=capacity_plan(policy,{},now=time.time(),completed_jobs=len(completed) if phase=='pilot' else 0,
                    ads=[],healthy=not incidents,quota_verified=True,account_verified=True,external=other_workers)
                capacity_state['reason']='initial_capacity_released'
                save(root/'capacity_state.json',capacity_state)
            else:
                cluster = submit(root, 'bulk')
            submitted=update_submission(root,bulk_cluster=cluster)
            save(root / 'release_state.json', {'phase': 'released', 'bulk_cluster': cluster,
                 'projected_output_bytes': projected, 'free_bytes': free, 'free_files': files,
                 'estimated_hours_at_initial_workers':cpu_seconds / policy.get('initial_workers',100) / 3600,
                 'initial_workers':policy.get('initial_workers',100),'worker_ceiling':policy.get('worker_ceiling',100),
                 'estimate_assumptions': 'canary wall time with continuous initial allocation; includes startup; excludes queue delay',
                 'canaries_passed': len(completed), 'released_at_epoch': now})
            if phase=='canaries':
                completed = {}
                stages = {s: {t: 0 for t in ('GEN','SIM','DIGIHLT','RECO','NANO')} for s in manifest['strata']}
            phase = 'bulk'
            started=time.time()
            expected = set(range(manifest['jobs']))
            last_completion = time.time()
            continue
        if phase == 'canaries' and now - started > policy['canary_timeout_seconds']:
            raise TimeoutError('Validation campaign exceeded its time limit')
        healthy = [ad for ad in ads if ad.get('JobStatus') in (1,2,6,7)]
        if phase in ('pilot','bulk') and healthy and now - last_completion > policy['completion_timeout_seconds']:
            raise TimeoutError('No bulk job has completed within the allowed interval')
        if not ads and not incidents and now - last_completion > 180:
            raise RuntimeError('Workers disappeared before all completion receipts arrived')
        time.sleep(60)


def stop(root, policy, error):
    stop_errors = []
    save(root / 'stop_requested.json', {'reason': str(error), 'requested_at_epoch': time.time()})
    constraint = 'ShiftNtupleProduction == true && ShiftSuiteTag == ' + json.dumps(policy['tag'])
    clusters = set()
    fallback_needed = False
    try:
        submitted = read(root / 'submission.json')
        for name in ('canary_cluster', 'pilot_cluster', 'bulk_cluster'):
            if submitted.get(name) is not None:
                clusters.add(int(submitted[name]))
        # Collect deferred factories before any read that may fail. They can
        # have no materialized procs and still admit workers after a quota stop.
        clusters.update(int(cluster) for cluster in submitted.get('recovery_clusters', []))
    except Exception as stop_error:
        stop_errors.append(repr(stop_error))
        fallback_needed = True
    try:
        ads = bounded_query(root,constraint, ['ClusterId'], local_schedd=True, timeout=30)
        clusters.update(ad['ClusterId'] for ad in ads)
    except Exception as stop_error:
        stop_errors.append(repr(stop_error))
        fallback_needed = True
    # Holding cluster IDs pauses each deferred factory as well as its workers.
    # A failure for one factory must not prevent attempting the other holds.
    for cluster in sorted(clusters):
        try:
            run_condor(['/usr/bin/condor_hold',str(cluster)], local_schedd=True, timeout=30)
        except Exception as stop_error:
            stop_errors.append(repr(stop_error))
            fallback_needed = True
    if fallback_needed:
        try:
            run_condor(['/usr/bin/condor_hold','-constraint',constraint], local_schedd=True,timeout=30)
        except Exception as fallback_error:
            stop_errors.append(repr(fallback_error))
    previous = read(root / 'controller_state.json') if (root / 'controller_state.json').exists() else {}
    record = {**previous, 'phase': 'blocked', 'health':'blocked', 'error': str(error),
              'resume_phase':previous.get('resume_phase',previous.get('phase')),
              'checked_at_epoch':time.time(), 'stopped_at_epoch': time.time(),
              'stop_errors': stop_errors, 'automatic_retry': False}
    save(root / 'controller_state.json', record)
    save(root / 'release_state.json', record)
    notify_once(root, policy['attention_email'], str(error))


def monitor_blocked(root, policy, *, role='controller'):
    """Remain observable after a global stop, without releasing or resubmitting."""
    path = root / ('watchdog_state.json' if role == 'watchdog' else 'controller_state.json')
    while True:
        record = read(path) if path.exists() else {}
        record.update(phase='blocked', checked_at_epoch=time.time(), monitoring=True,
                      automatic_retry=False, pid=os.getpid(), host=socket.gethostname())
        try:
            constraint = 'ShiftNtupleProduction == true && ShiftSuiteTag == ' + json.dumps(policy['tag'])
            record['queue'] = bounded_query(root,constraint, ['ClusterId','ProcId','JobStatus','HoldReason','ShiftNtupleJob',
                                    'ShiftNtupleStratum','ShiftNtupleTier','ShiftNtupleStageRecordsStarted',
                                    'ShiftNtupleGENEvents','ShiftNtupleSIMEvents','ShiftNtupleDIGIHLTEvents',
                                    'ShiftNtupleRECOEvents','ShiftNtupleNANOEvents'],role=role,
                                    local_schedd=True, timeout=30)
            record.pop('monitor_error', None)
        except Exception as error:
            record['monitor_error'] = repr(error)
        current = read(path) if path.exists() else {}
        for key in ('scheduler_state','scheduler_query_attempt','scheduler_query_error','health_before_unknown','health'):
            if key in current:
                record[key] = current[key]
            else:
                record.pop(key, None)
        record['checked_at_epoch'] = time.time()
        if role == 'watchdog':
            main_state = read(root / 'controller_state.json') if (root / 'controller_state.json').exists() else {}
            record['controller_heartbeat_epoch'] = main_state.get('checked_at_epoch')
            record['controller_phase'] = main_state.get('phase')
        save(path, record)
        time.sleep(60)


def watchdog(root, policy):
    """Independent scheduler process catches a dead or hung main controller."""
    started = time.time()
    while True:
        state_path = root / 'controller_state.json'
        state = read(state_path) if state_path.exists() else {}
        save(root / 'watchdog_state.json', {'phase':'watching', 'checked_at_epoch':time.time(),
             'controller_phase':state.get('phase'), 'controller_heartbeat_epoch':state.get('checked_at_epoch'),
             'pid':os.getpid(), 'host':socket.gethostname()})
        if state.get('phase') == 'blocked':
            notify_once(root, policy['attention_email'], state.get('error','Production is blocked'))
        if state.get('phase') == 'complete':
            save(root / 'watchdog_state.json', {'phase':'complete', 'checked_at_epoch':time.time()})
            return
        submitted = read(root / 'submission.json')
        cluster = submitted.get('controller_cluster')
        ads = []
        if cluster is not None:
            ads = recoverable_query(root,f'ClusterId == {cluster}', ['JobStatus','HoldReason'],role='watchdog', local_schedd=True, timeout=30)
        # A read outage can outlast the heartbeat allowance. The main
        # controller may have kept updating while this watchdog retried, or
        # completed its work in the meantime, so use its current state.
        state = read(state_path) if state_path.exists() else {}
        if state.get('phase') == 'complete':
            save(root / 'watchdog_state.json', {'phase':'complete', 'checked_at_epoch':time.time()})
            return
        if cluster is not None and (any(ad['JobStatus'] not in (1,2,6,7) for ad in ads) or
                (not ads and time.time()-started>180)):
            raise RuntimeError('Main production controller stopped unexpectedly: ' + str(ads))
        heartbeat = state.get('checked_at_epoch', started)
        if time.time() - heartbeat > 300:
            raise TimeoutError('Production controller has not updated its status for five minutes')
        time.sleep(60)


class ControllerHandoff(BaseException):
    """SIGTERM is an explicit supervisor handoff, not a worker failure."""


def lock_supervisor(root, role):
    stream = (root / (role + '.lock')).open('a+')
    try:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        stream.close()
        raise RuntimeError('Another ' + role + ' is already supervising this campaign')
    stream.seek(0)
    stream.truncate()
    json.dump({'pid':os.getpid(),'host':socket.gethostname(),'started_at_epoch':time.time()},stream)
    stream.flush()
    return stream


def observe_with_recovery(root, policy, role):
    """Restart a failed observer without interpreting its fault as worker failure.

    Existing worker-side absolute/stall guards remain active. Only explicit
    stops, verified storage exhaustion or inventory corruption can globally
    stop the campaign. Submission intents still prevent duplicate launches.
    """
    while True:
        try:
            if role == 'watchdog':
                watchdog(root, policy)
            else:
                supervise(root, policy)
            return
        except GlobalProductionError:
            raise
        except Exception as error:
            path = root/(role+'_fault.json')
            previous = read(path) if path.exists() else {}
            fault = dict(error=repr(error), checked_at_epoch=time.time(),
                         occurrence=previous.get('occurrence',0)+1,
                         worker_action='none', retry_after_seconds=60)
            save(path,fault)
            state_path = root/(role+'_state.json')
            state = read(state_path) if state_path.exists() else {}
            state.update(checked_at_epoch=time.time(),health='unknown',attention_required=True,
                         observer_error=repr(error), activity='restarting_observer_preserving_workers')
            save(state_path,state)
            try:
                notify_once(root,policy['attention_email'],repr(error))
            except Exception as mail_error:
                fault['notification_error'] = repr(mail_error)
                save(path,fault)
            time.sleep(60)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('campaign', type=Path)
    parser.add_argument('--watchdog', action='store_true')
    parser.add_argument('--capacity-policy', type=Path,
                        help='Separate scheduling-only override for the frozen deployment')
    args = parser.parse_args()
    root = args.campaign.resolve()
    policy = effective_policy(root, args.capacity_policy)
    role = 'watchdog' if args.watchdog else 'controller'
    lock = lock_supervisor(root, role)
    def terminate(signum, frame):
        raise ControllerHandoff(signum)
    signal.signal(signal.SIGTERM, terminate)
    try:
        if args.watchdog:
            observe_with_recovery(root, policy, role)
        else:
            state = read(root / 'controller_state.json') if (root / 'controller_state.json').exists() else {}
            if state.get('phase') == 'blocked' or (root / 'stop_requested.json').exists():
                monitor_blocked(root, policy)
                return
            mailer = shutil.which('sendmail') or '/usr/sbin/sendmail'
            save(root / 'notification_preflight.json', {'checked_at_epoch': time.time(), 'mailer': mailer,
                 'available': Path(mailer).is_file(), 'recipient': policy['attention_email']})
            observe_with_recovery(root, policy, role)
    except ControllerHandoff as error:
        save(root / (role + '_handoff.json'), {'signal':error.args[0], 'handed_off_at_epoch':time.time(),
             'pid':os.getpid(), 'host':socket.gethostname()})
    except GlobalProductionError as error:
        stop(root, policy, repr(error))
        try:
            monitor_blocked(root, policy, role=role)
        except ControllerHandoff as handoff:
            save(root / (role + '_handoff.json'), {'signal':handoff.args[0], 'handed_off_at_epoch':time.time(),
                 'pid':os.getpid(), 'host':socket.gethostname()})
    finally:
        lock.close()


if __name__ == '__main__':
    main()
