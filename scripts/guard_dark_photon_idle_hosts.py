# Code style: AI-code. AI-generated narrow native scheduler operations helper.
"""Exclude reviewed failed machines from idle DP detector jobs only.

The guard never releases, removes or submits a science job. Running jobs and
immutable production files are untouched. Every payload edit has a durable exact
job manifest and an atomic JobStatus == 1 constraint. Optional manager-only
queue and submitted-node ceilings reduce new starts without evicting payloads.
"""
import argparse
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import time

import shift_condor_native as native


ATTRIBUTES = ['ClusterId', 'ProcId', 'Owner', 'JobStatus', 'Iwd', 'Cmd',
              'DAGManJobId', 'Requirements', 'JobUniverse', 'DAGMan_MaxIdle',
              'DAGMan_MaxJobs', 'DAG_AdUpdateTime', 'NumJobStarts',
              'EnteredCurrentStatus', 'JobCurrentStartDate']


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('w') as stream:
        stream.write(json.dumps(value, indent=2, sort_keys=True) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def validate_config(config):
    if config.get('schema') != 'shift-dp-idle-host-guard-v1':
        raise ValueError('Unsupported guard configuration')
    if not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9.-]*', config['owner']):
        raise ValueError('Guard owner must be explicit')
    directory = Path(config['directory'])
    if not directory.is_absolute() or str(directory).startswith(('/tmp/', '/pool/')):
        raise ValueError('Guard working directory must be persistent and absolute')
    if not config['namespaces'] or len(config['namespaces']) > 32:
        raise ValueError('An explicit bounded production namespace list is required')
    for namespace in config['namespaces']:
        if (not Path(namespace).is_absolute() or '/validation/bsm' not in namespace
                or namespace.endswith('/') or '..' in Path(namespace).parts):
            raise ValueError('Invalid exact DP production namespace')
    hosts = config['excluded_hosts']
    if not hosts or len(hosts) > 8 or len(hosts) != len(set(hosts)):
        raise ValueError('A small explicit failed-host list is required')
    if any(not re.fullmatch(r'[A-Za-z0-9.-]+[.]cern[.]ch', host) for host in hosts):
        raise ValueError('Invalid failed execute machine')
    if not 30 <= config['interval_seconds'] <= 300:
        raise ValueError('Guard poll interval is outside its reviewed bounds')
    if not 1 <= config['runtime_seconds'] <= 604800:
        raise ValueError('Guard runtime exceeds seven days')
    if not 1024 <= config['maximum_receipt_bytes'] <= 64 * 1024 * 1024:
        raise ValueError('Guard evidence budget must be bounded')
    idle = config.get('manager_idle_ceiling')
    if idle is not None and (type(idle) is not int or not 1 <= idle <= 20):
        raise ValueError('Manager ready-queue ceiling must be between1 and20')
    ceilings = config.get('manager_job_ceilings', {})
    if not isinstance(ceilings, dict):
        raise ValueError('Manager node ceilings require explicit namespace keys')
    for namespace, ceiling in ceilings.items():
        if (namespace not in config['namespaces'] or type(ceiling) is not int
                or not 1 <= ceiling <= 10000):
            raise ValueError('Invalid bounded manager submitted-node ceiling')
        if any(namespace != other and namespace.startswith(other + '/') for other in ceilings):
            raise ValueError('Manager ceiling namespaces must not overlap')
    if not 1 <= config.get('maximum_consecutive_native_failures', 3) <= 10:
        raise ValueError('Native scheduler uncertainty grace must be bounded')
    if not 1 <= config.get('maximum_unprotected_polls', 3) <= 10:
        raise ValueError('Unprotected idle-job grace must be bounded')


def eligible(config, ad):
    if (ad.get('Owner') != config['owner'] or ad.get('JobStatus') != 1
            or type(ad.get('DAGManJobId')) is not int or ad['DAGManJobId'] <= 0):
        return False
    directory = ad.get('Iwd', '')
    if not any(directory == root or directory.startswith(root + '/')
               for root in config['namespaces']):
        return False
    return ad.get('Cmd') == directory + '/detector_worker.sh'


def requirements(ad, hosts):
    value = ad.get('Requirements', '')
    if not value.startswith('/Expr(') or not value.endswith(')/'):
        raise ValueError('Native Requirements is not a verified ClassAd expression')
    expression = value[6:-2]
    missing = [host for host in hosts if not re.search(
        r'TARGET[.]Machine\s*(?:=!=|isnt)\s*"' + re.escape(host) + '"', expression,
        flags=re.IGNORECASE)]
    if not missing:
        return None
    return '(' + expression + ')' + ''.join(
        ' && (TARGET.Machine =!= ' + json.dumps(host) + ')' for host in missing)


def exact_constraint(owner, ads):
    identities = []
    for ad in ads:
        expression = ad['Requirements']
        if not expression.startswith('/Expr(') or not expression.endswith(')/'):
            raise ValueError('Cannot compare an unparsed native Requirements')
        # Compare the unevaluated expression, not its truth value. This prevents
        # overwriting a concurrent, stronger requirement with our earlier copy.
        identity = f"ClusterId == {ad['ClusterId']} && ProcId == {ad['ProcId']}"
        for name in ('Iwd', 'Cmd', 'DAGManJobId'):
            identity += ' && ' + name + ' == ' + json.dumps(ad[name])
        if 'JobUniverse' in ad:
            identity += ' && JobUniverse == ' + json.dumps(ad['JobUniverse'])
        identity += ' && unparse(Requirements) == ' + json.dumps(expression[6:-2])
        identities.append('(' + identity + ')')
    identities = ' || '.join(identities)
    return 'Owner == ' + json.dumps(owner) + ' && JobStatus == 1 && (' + identities + ')'


def check_idle_readback(batch, after, hosts):
    """Verify exact identities; leave started/finished jobs untouched."""
    expected = {(ad['ClusterId'], ad['ProcId']): ad for ad in batch}
    seen = set()
    readback = []
    pending = []
    for ad in after:
        key = (ad.get('ClusterId'), ad.get('ProcId'))
        if key not in expected or key in seen:
            raise ValueError('Idle-job readback has an unexpected or duplicate identity')
        seen.add(key)
        before = expected[key]
        if any(ad.get(name) != before.get(name)
               for name in ('Owner', 'Iwd', 'Cmd', 'DAGManJobId', 'JobUniverse')):
            raise ValueError('Idle-job identity changed before native readback')
        status = ad.get('JobStatus')
        if status not in (1, 2, 3, 4, 5, 6, 7):
            raise ValueError('Idle-job readback has an unknown job state')
        protected = requirements(ad, hosts) is None if status == 1 else False
        row = dict(cluster=key[0], proc=key[1], status=status,
                   requirements_sha256=hashlib.sha256(
                       ad.get('Requirements', '').encode()).hexdigest(),
                   host_exclusions_present=protected)
        if status == 1 and not protected:
            row['new_idle_period_observed'] = any(
                isinstance(ad.get(name), int) and isinstance(before.get(name), int)
                and ad[name] > before[name]
                for name in ('NumJobStarts', 'EnteredCurrentStatus', 'JobCurrentStartDate'))
            pending.append(row)
        readback.append(row)
    return readback, pending


def manager_authority(config, ad):
    """Bind a running detector DAG manager to its unchanged persistent receipt."""
    directory = ad.get('Iwd', '')
    if (ad.get('Owner') != config['owner'] or ad.get('JobStatus') != 2
            or ad.get('ProcId') != 0
            or ad.get('JobUniverse') != 7
            or ad.get('Cmd') != '/usr/bin/condor_dagman'
            or not any(directory == root or directory.startswith(root + '/')
                       for root in config['namespaces'])):
        return None
    receipt_path = Path(directory) / 'submission.json'
    log_path = Path(directory) / 'pipeline.dag.dagman.out'
    if not receipt_path.is_file() or not log_path.is_file():
        return None  # The actual submitter/manager may still be writing these.
    receipt = json.loads(receipt_path.read_text())
    if receipt.get('cluster') != ad['ClusterId'] or receipt.get('task') != directory:
        raise ValueError('Manager does not match its persistent detector submission receipt')
    with log_path.open() as stream:
        startup = stream.read(65536)
    if '$CondorVersion: 25.0.' not in startup:
        raise ValueError('Manager version is outside the reviewed25.0 live-throttle contract')
    if 'DAGMAN_REMOVE_JOBS_AFTER_LIMIT_CHANGE setting: False' not in startup:
        raise ValueError('Cannot establish that manager throttle changes preserve payloads')
    if type(ad.get('DAGMan_MaxIdle')) is not int or type(ad.get('DAGMan_MaxJobs')) is not int:
        return None  # Wait for the initialized manager ClassAd.
    return dict(submission_path=str(receipt_path), submission_sha256=digest(receipt_path),
                startup_log=str(log_path), startup_prefix_sha256=hashlib.sha256(startup.encode()).hexdigest())


def manager_limits(config, ad):
    """Only tighten the declared ceilings; preserve any stricter live setting."""
    ceilings = {}
    if config.get('manager_idle_ceiling') is not None:
        ceilings['DAGMan_MaxIdle'] = config['manager_idle_ceiling']
    directory = ad['Iwd']
    for namespace, ceiling in config.get('manager_job_ceilings', {}).items():
        if directory == namespace or directory.startswith(namespace + '/'):
            ceilings['DAGMan_MaxJobs'] = ceiling
    return {name: ceiling for name, ceiling in ceilings.items()
            if ad[name] <= 0 or ad[name] > ceiling}


def throttle_managers(config, ads, sequence):
    if config.get('manager_idle_ceiling') is None and not config.get('manager_job_ceilings'):
        return []
    changes = []
    for ad in ads:
        authority = manager_authority(config, ad)
        if authority is None:
            continue
        limits = manager_limits(config, ad)
        if limits:
            changes.append(dict(ad=ad, authority=authority, limits=limits))
    if not changes:
        return []
    path = Path(config['directory']) / 'receipts' / f'manager_poll_{sequence:06d}.json'
    path.parent.mkdir(exist_ok=True)
    if path.exists():
        raise FileExistsError('Preserve earlier manager edit receipts')
    receipt = dict(schema='shift-dp-manager-throttle-receipt-v2', created_utc=utc(),
                   changes=changes, actions=[], complete=False)
    used = sum(item.stat().st_size for item in path.parent.glob('*.json'))
    if used + len(json.dumps(receipt).encode()) * 4 > config['maximum_receipt_bytes']:
        raise ValueError('Guard receipt budget exhausted before manager edits')
    save(path, receipt)
    for item in changes:
        ad = item['ad']
        schedd = ad.get('_queried_schedd', '')
        if not re.fullmatch(r'[A-Za-z0-9_.@-]+', schedd):
            raise ValueError('Manager has no verified scheduler identity')
        constraint = (f"ClusterId == {ad['ClusterId']} && ProcId == {ad['ProcId']}"
                      ' && Owner == ' + json.dumps(config['owner']) +
                      ' && JobStatus == 2 && JobUniverse == 7 && Iwd == ' + json.dumps(ad['Iwd']) +
                      ' && Cmd == ' + json.dumps(ad['Cmd']))
        expected = {name: ad[name] for name in ('DAGMan_MaxJobs', 'DAGMan_MaxIdle')}
        for name in ('DAGMan_MaxJobs', 'DAGMan_MaxIdle'):
            if name not in item['limits']:
                continue
            ceiling = item['limits'][name]
            # The saved intent and compare-and-set preserve a concurrent stricter
            # setting. An unexpected edit is a review condition, never a retry.
            edit_constraint = constraint + f' && {name} == {expected[name]}'
            command = ['/usr/bin/condor_qedit', '-pool', config['condor_pool'], '-name', schedd,
                       '-constraint', edit_constraint, name, str(ceiling)]
            action = dict(cluster=ad['ClusterId'], proc=ad['ProcId'], schedd=schedd,
                          attribute=name, before=expected[name], ceiling=ceiling,
                          constraint=edit_constraint, intent_utc=utc())
            receipt['actions'].append(action)
            save(path, receipt)
            output = native.run_condor(command, timeout=50)
            after = native.query(f"ClusterId == {ad['ClusterId']} && ProcId == {ad['ProcId']}",
                                 ATTRIBUTES, pool=config['condor_pool'], schedd=schedd, timeout=50)
            expected[name] = ceiling
            action.update(edited_epoch=time.time(), output=output, readback=after)
            save(path, receipt)
            for current in after:
                if any(current.get(key) != value for key, value in expected.items()):
                    raise ValueError('Manager throttle or unchanged companion limit readback differs')
    receipt.update(complete=True, completed_utc=utc())
    save(path, receipt)
    return [str(path)]


def step(config, sequence, previous=None):
    previous = previous or {}
    constraint = ('(JobStatus == 1 && DAGManJobId > 0 '
                  '&& regexp("/detector_worker[.]sh$", Cmd)) '
                  '|| (JobStatus == 2 && JobUniverse == 7 && regexp("/condor_dagman$", Cmd))')
    ads = native.query_account(config['owner'], constraint, ATTRIBUTES,
                               pool=config['condor_pool'], timeout=50)
    manager_receipts = throttle_managers(config, ads, sequence)
    manager_observations = [dict(cluster=ad['ClusterId'], max_idle=ad.get('DAGMan_MaxIdle'),
                                 max_jobs=ad.get('DAGMan_MaxJobs'),
                                 manager_ad_update_epoch=ad.get('DAG_AdUpdateTime'))
                            for ad in ads if (config.get('manager_idle_ceiling') is not None
                                             or config.get('manager_job_ceilings'))
                            and manager_authority(config, ad) is not None]
    selected = [ad for ad in ads if eligible(config, ad)]
    changes = []
    for ad in selected:
        updated = requirements(ad, config['excluded_hosts'])
        if updated is not None:
            if not re.fullmatch(r'[A-Za-z0-9_.@-]+', ad.get('_queried_schedd', '')):
                raise ValueError('Selected job has no verified scheduler identity')
            changes.append(dict(ad=ad, updated=updated))
    summary = dict(checked_utc=utc(), eligible_idle_jobs=len(selected),
                   edits_planned=len(changes), protected_idle_jobs=len(selected)-len(changes),
                   manager_receipts=manager_receipts, manager_observations=manager_observations,
                   pending_protection={})
    if not changes:
        return summary
    receipts = Path(config['directory']) / 'receipts'
    receipts.mkdir(exist_ok=True)
    used = sum(path.stat().st_size for path in receipts.glob('*.json'))
    receipt = dict(schema='shift-dp-idle-host-edit-receipt-v1', created_utc=utc(),
                   owner=config['owner'], excluded_hosts=config['excluded_hosts'],
                   changes=changes, actions=[], complete=False)
    if used + len(json.dumps(receipt).encode()) * 4 > config['maximum_receipt_bytes']:
        raise ValueError('Guard receipt budget exhausted; no further edits permitted')
    path = receipts / f'poll_{sequence:06d}.json'
    if path.exists():
        raise FileExistsError('Preserve previous guard edit receipts')
    save(path, receipt)
    groups = {}
    for change in changes:
        ad = change['ad']
        groups.setdefault((ad['_queried_schedd'], change['updated']), []).append(ad)
    for (schedd, updated), rows in groups.items():
        for index in range(0, len(rows), 100):
            batch = rows[index:index + 100]
            command = ['/usr/bin/condor_qedit', '-pool', config['condor_pool'],
                       '-name', schedd, '-constraint', exact_constraint(config['owner'], batch),
                       'Requirements', updated]
            action = dict(schedd=schedd, constraint=command[-3], intent_utc=utc(),
                          selected_jobs=len(batch), output=None, readbacks=[])
            receipt['actions'].append(action)
            save(path, receipt)
            output = native.run_condor(command, timeout=50)
            action['output'] = output
            count_match = re.search(r'(\d+)\s+matching jobs?[.]?', output)
            matched = int(count_match.group(1)) if count_match else None
            if matched is not None and not 0 <= matched <= len(batch):
                raise ValueError('Native edit count exceeds its exact idle-job scope')
            action['matched_jobs'] = matched
            save(path, receipt)
            identity_constraint = ' || '.join(
                f"(ClusterId == {ad['ClusterId']} && ProcId == {ad['ProcId']})" for ad in batch)
            for attempt in range(3):
                after = native.query(identity_constraint, ATTRIBUTES, pool=config['condor_pool'],
                                     schedd=schedd, timeout=50)
                readback, pending = check_idle_readback(batch, after, config['excluded_hosts'])
                action['readback'] = readback
                action['readbacks'].append(dict(checked_utc=utc(), rows=readback))
                save(path, receipt)
                if not pending:
                    break
                if attempt < 2:
                    time.sleep(0.5)
            for row in pending:
                key = str(row['cluster']) + '.' + str(row['proc'])
                prior = previous.get('pending_protection', {}).get(key, {})
                polls = prior.get('unprotected_polls', 0) + 1
                # A short compare-and-set proves that some job left the exact
                # editable state. Preserve unresolved IDs for a fresh next poll;
                # never describe them as protected or repeat the old expression.
                known_change = (matched is not None and matched < len(batch))
                if (not known_change and not row['new_idle_period_observed']
                        or polls >= config.get('maximum_unprotected_polls', 3)):
                    raise ValueError('Native readback still has an unprotected idle job')
                summary['pending_protection'][key] = dict(
                    row, unprotected_polls=polls,
                    reason='Exact edit skipped a changed job; fresh identity/requirements needed')
            action['pending_protection'] = summary['pending_protection']
            save(path, receipt)
    receipt.update(complete=True, completed_utc=utc())
    save(path, receipt)
    summary['receipt'] = str(path)
    return summary


def poll(config, sequence, previous):
    """Native uncertainty waits for a new readback; semantic failures stop."""
    try:
        result = step(config, sequence, previous)
    except native.NativeCondorError as error:
        failures = previous.get('consecutive_native_failures', 0) + 1
        if failures >= config.get('maximum_consecutive_native_failures', 3):
            raise
        result = dict(previous, checked_utc=utc(), status='waiting_native_scheduler',
                      native_error=str(error), consecutive_native_failures=failures,
                      uncertain_poll=sequence)
    else:
        result.update(status=('waiting_idle_protection' if result.get('pending_protection')
                              else 'guarding'),
                      native_error=None, consecutive_native_failures=0)
    result.update(schema='shift-dp-idle-host-guard-state-v1', poll=sequence,
                  error=None, complete=False)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--config-sha256', required=True)
    args = parser.parse_args()
    config_path = Path(args.config)
    if digest(config_path) != args.config_sha256:
        raise ValueError('Guard configuration checksum differs')
    config = json.loads(config_path.read_text())
    validate_config(config)
    for path, expected in config['dependencies'].items():
        if digest(path) != expected:
            raise ValueError('Frozen guard dependency changed: ' + path)
    root = Path(config['directory'])
    os.chdir(root)
    with (root / 'guard.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        started = time.monotonic()
        sequence = max((int(path.stem.split('_')[-1])
                        for path in (root / 'receipts').glob('poll_*.json')), default=0)
        if (root / 'state.json').is_file():
            sequence = max(sequence, json.loads((root / 'state.json').read_text()).get('poll', 0))
        result = dict(checked_utc=utc(), complete=False, error=None,
                      stage='starting native idle query')
        try:
            save(root / 'state.json', result)
            while time.monotonic() - started < config['runtime_seconds']:
                sequence += 1
                result = poll(config, sequence, result)
                save(root / 'state.json', result)
                time.sleep(config['interval_seconds'])
            save(root / 'state.json', dict(checked_utc=utc(), complete=True, error=None,
                                          reason='Reviewed seven-day runtime finished'))
        except Exception as error:
            save(root / 'state.json', dict(result, checked_utc=utc(), complete=False,
                                          error=type(error).__name__ + ': ' + str(error), poll=sequence))
            raise


if __name__ == '__main__':
    main()
