#!/usr/bin/env python3
# Code style: AI-code. AI-generated read-only watchdog for one bounded SM DAG.
"""Report failures without resubmitting, releasing, or removing any job."""
import argparse
import errno
import fcntl
import json
import math
import os
from pathlib import Path
import pwd
import re
import time

from shift_condor_native import NativeCondorError, parse_ads, query, run_condor


ATTRIBUTES = ['ClusterId', 'ProcId', 'Owner', 'GlobalJobId', 'Iwd', 'JobStatus',
              'DAGManJobId', 'HoldReason', 'ExitBySignal', 'ExitCode',
              'DAG_Status', 'DAG_NodesFailed']
TRANSIENT_STORAGE_ERRORS = (errno.EIO, errno.ESTALE, errno.ETIMEDOUT)


def read_json(path):
    with Path(path).open('rb') as stream:
        return json.loads(b''.join(iter(lambda: stream.read(1048576), b'')))


def persistent_path(value):
    path = Path(value)
    if not path.is_absolute() or str(path).startswith(('/tmp/', '/pool/')):
        raise ValueError('Watchdog paths must be absolute and persistent')
    return path


def validate_config(config):
    if type(config['manager_id']) is not int or config['manager_id'] <= 0:
        raise ValueError('Require one exact native DAG manager ID')
    if not re.fullmatch('[0-9a-f]{64}', config['expected_plan_sha256']):
        raise ValueError('Require the frozen benchmark plan SHA-256')
    for name in ('working_directory', 'manager_iwd', 'final_result_path',
                 'heartbeat_path', 'failure_path', 'state_path'):
        persistent_path(config[name])
    owner = config.get('owner', pwd.getpwuid(os.getuid()).pw_name)
    if not re.fullmatch('[A-Za-z_][A-Za-z_0-9.-]*', owner):
        raise ValueError('Invalid native job owner')
    if not 1 <= config.get('poll_seconds', 30) <= 60:
        raise ValueError('Watchdog poll interval must be between one and sixty seconds')
    maximum = config.get('maximum_wall_seconds', 172800)
    if isinstance(maximum, bool) or not math.isfinite(maximum) or maximum <= 0:
        raise ValueError('Watchdog wall-time bound must be finite and positive')
    return dict(manager_id=config['manager_id'], manager_iwd=config['manager_iwd'],
                owner=owner, expected_plan_sha256=config['expected_plan_sha256'])


def write_snapshot(path, value, sleep=time.sleep, write_bytes=None):
    """Retry these same serialized bytes before observing the scheduler again."""
    path = Path(path)
    data = (json.dumps(value, indent=2, allow_nan=False)+'\n').encode()

    def atomic_write(payload):
        temporary = path.with_suffix(path.suffix+'.tmp')
        with temporary.open('wb') as stream:
            for start in range(0, len(payload), 1048576):
                stream.write(payload[start:start+1048576])
        temporary.replace(path)

    writer = write_bytes or atomic_write
    while True:
        try:
            writer(data)
            return
        except OSError as error:
            if error.errno not in TRANSIENT_STORAGE_ERRORS:
                raise
            sleep(10)


def native_manager_history(config):
    answer = run_condor(['/usr/bin/condor_history', str(config['manager_id']),
        '-limit', '1', '-json', '-attributes', ','.join(ATTRIBUTES)], timeout=45)
    return parse_ads(answer)


def verify_manager(ad, binding, config):
    if (ad.get('ClusterId') != binding['manager_id'] or ad.get('ProcId') != 0
            or ad.get('Owner') != binding['owner'] or ad.get('Iwd') != binding['manager_iwd']):
        raise ValueError('Native DAG manager identity or persistent Iwd differs')
    expected_global = config.get('manager_global_job_id')
    if expected_global and ad.get('GlobalJobId') != expected_global:
        raise ValueError('Native DAG manager GlobalJobId differs')


def collector_result(config):
    try:
        result = read_json(config['final_result_path'])
    except FileNotFoundError:
        return None
    if result.get('benchmark_plan_sha256') != config['expected_plan_sha256']:
        raise ValueError('Collector result belongs to a different frozen plan')
    if (result.get('complete') is not True or result.get('exit_code') != 0
            or result.get('complete_frozen_draw_collected') is not True):
        return None
    return result


def observe(config, state, now=None, query_jobs=query, history=native_manager_history):
    """One read-only native observation, with terminal history cached once."""
    now = time.time() if now is None else now
    binding = validate_config(config)
    if state.get('binding', binding) != binding:
        raise ValueError('Saved watchdog state belongs to a different benchmark')
    state['binding'] = binding
    constraint = ('Owner == '+json.dumps(binding['owner'])+' && ((ClusterId == '
        +str(binding['manager_id'])+' && ProcId == 0) || DAGManJobId == '
        +str(binding['manager_id'])+')')
    ads = query_jobs(constraint, ATTRIBUTES, timeout=45)
    managers = [ad for ad in ads if ad.get('ClusterId') == binding['manager_id']
                and ad.get('ProcId') == 0]
    children = [ad for ad in ads if ad.get('DAGManJobId') == binding['manager_id']]
    if len(managers) > 1 or len(managers)+len(children) != len(ads):
        raise ValueError('Native queue returned an unexpected DAG member')
    if any(ad.get('Owner') != binding['owner'] for ad in children):
        raise ValueError('Native child owner differs from the benchmark')
    problems = []
    held = [ad for ad in children if ad.get('JobStatus') == 5]
    if held:
        problems.append('Held DAG children require review; no automatic release')
    manager = managers[0] if managers else None
    terminal = None
    if manager is not None:
        verify_manager(manager, binding, config)
        if manager.get('JobStatus') == 5:
            problems.append('DAG manager is held')
        elif manager.get('JobStatus') == 3:
            problems.append('DAG manager was removed')
        if manager.get('DAG_Status', 0) != 0 or manager.get('DAG_NodesFailed', 0) > 0:
            problems.append('Live DAG manager reports failed nodes or unsuccessful DAG status')
        if children:
            state.pop('no_children_since', None)
        else:
            since = state.setdefault('no_children_since', now)
            if now-since > 300:
                problems.append('DAG manager has no child workers for more than 300 seconds')
    else:
        state.pop('no_children_since', None)
        terminal = state.get('verified_manager_terminal')
        if terminal is None:
            records = history(config)
            if len(records) > 1:
                raise ValueError('Ambiguous native DAG history')
            if records:
                terminal = records[0]
                verify_manager(terminal, binding, config)
                state['verified_manager_terminal'] = terminal
        else:
            verify_manager(terminal, binding, config)
        if terminal is None:
            problems.append('DAG manager absent without verified native terminal evidence')
        elif (terminal.get('JobStatus') != 4 or terminal.get('ExitBySignal') is not False
                or terminal.get('ExitCode') != 0 or terminal.get('DAG_Status', 0) != 0
                or terminal.get('DAG_NodesFailed', 0) != 0):
            problems.append('Native DAG manager terminated unsuccessfully')
    result = collector_result(config)
    manager_success = (terminal is not None and not problems)
    if manager_success and result is None:
        problems.append('Successful DAG lacks a complete matching final collector result')
    complete = manager_success and result is not None and not children
    record = dict(checked_epoch=now, phase='complete' if complete else
                  ('attention_required' if problems else 'running'), terminal=complete,
                  problems=problems, manager_ads=managers, manager_terminal=terminal,
                  children=len(children), held_children=held,
                  expected_plan_sha256=binding['expected_plan_sha256'],
                  final_result_complete=result is not None,
                  no_children_since=state.get('no_children_since'))
    return record


def run_once(config, state, now=None, observer=observe, writer=write_snapshot):
    """Persist one observation and its first failure before another native poll."""
    now = time.time() if now is None else now
    started = state.setdefault('monitor_started_epoch', now)
    if now-started >= config.get('maximum_wall_seconds', 172800):
        record = dict(checked_epoch=now, phase='watchdog_runtime_limit', terminal=False,
                      problems=['Watchdog wall-time bound reached before verified completion'])
    else:
        try:
            record = observer(config, state, now=now)
        except NativeCondorError as error:
            record = dict(checked_epoch=now, phase='scheduler_unknown', terminal=False,
                          problems=[str(error)])
        except (OSError, ValueError) as error:
            record = dict(checked_epoch=now, phase='monitor_error', terminal=False,
                          problems=[repr(error)])
    if record['problems'] and 'first_failure' not in state:
        state['first_failure'] = record
    state['last_observation'] = record
    writer(config['state_path'], state)
    writer(config['heartbeat_path'], record)
    if record['problems']:
        writer(config['failure_path'], dict(first_failure=state['first_failure'], latest=record))
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config', type=Path)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    persistent_path(str(args.config))
    config = read_json(args.config)
    validate_config(config)
    os.chdir(config['working_directory'])
    lock_path = Path(config['state_path']).with_suffix('.lock')
    with lock_path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            state = read_json(config['state_path'])
        except FileNotFoundError:
            state = {}
        while True:
            record = run_once(config, state)
            if record['phase'] == 'watchdog_runtime_limit':
                raise SystemExit('Watchdog runtime limit recorded; production completion is unverified')
            if args.once or record['terminal']:
                return
            time.sleep(config.get('poll_seconds', 30))


if __name__ == '__main__':
    main()
