#!/usr/bin/env python3
# Code style: AI-code. AI-generated persistent dark-photon production controller.
"""Keep a production heartbeat and publish compatible ready points as latest.

Run this under Condor's vanilla universe with delegated credentials, a frozen
candidate map and a copy of shift_condor_native.py. DAGMan owns dependencies and
retries. This controller reports failures; it never cancels, removes, releases
or resubmits jobs, and never modifies dated production payloads.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import uuid

from shift_condor_native import native_env, query


SCHEMA = 'shift-dark-photon-production-controller-v1'
CAMPAIGN_PATTERN = re.compile(r'[A-Za-z0-9_-]+')
POINT_PATTERN = re.compile(r'm[0-9]+(?:p[0-9]+)?_(?:prompt|lab_[0-9]+(?:p[0-9]+)?m|epsilon_[0-9]+(?:p[0-9]+)?(?:e[+-]?[0-9]+)?)')
DIGEST_PATTERN = re.compile(r'[0-9a-f]{64}')


def read(path):
    return json.loads(Path(path).read_text())


def sha256(path):
    checksum = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            checksum.update(block)
    return checksum.hexdigest()


def save(path, value):
    """Replace one small controller record without exposing partial JSON."""
    path = Path(path)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')
    os.replace(temporary, path)


def checked_digest(path, expected):
    if not isinstance(expected, str) or not DIGEST_PATTERN.fullmatch(expected):
        raise ValueError('Missing pinned checksum for ' + str(path))
    if sha256(path) != expected:
        raise ValueError('Frozen production input changed: ' + str(path))


class EosRpc:
    """Small native EOS adapter for workers without a mounted /eos directory."""
    def __init__(self, host, cache_directory):
        self.host = host
        self.cache_directory = Path(cache_directory)
        self.cache_directory.mkdir(parents=True, exist_ok=True)

    def command(self, arguments, *, missing_ok=False):
        environment = native_env()
        for name in ('X509_USER_PROXY', 'KRB5CCNAME'):
            if os.environ.get(name):
                environment[name] = os.environ[name]
        result = subprocess.run(arguments, capture_output=True, text=True,
                                env=environment, timeout=60)
        if result.returncode:
            message = result.stderr + result.stdout
            if missing_ok and ('No such file' in message or '[3011]' in message):
                return None
            raise RuntimeError('EOS RPC failed: ' + message.strip()[-1500:])
        if result.stderr.strip():
            raise RuntimeError('EOS RPC state unknown: ' + result.stderr.strip()[-1500:])
        return result.stdout

    def metadata(self, path):
        output = self.command(['/usr/bin/eos', self.host, 'fileinfo', str(path), '-m'], missing_ok=True)
        if output is None:
            return None
        return dict(token.split('=', 1) for token in output.split() if '=' in token)

    def root_files(self, directory):
        output = self.command(['/usr/bin/xrdfs', self.host, 'ls', str(directory)], missing_ok=True)
        if output is None:
            return []
        return sorted(Path(line.strip()) for line in output.splitlines() if line.strip().endswith('.root'))

    def verify_file(self, path, expected_bytes, expected_digest):
        info = self.metadata(path)
        if info is None or int(info.get('size', -1)) != expected_bytes:
            raise ValueError('Remote file size differs from reviewed publication: ' + str(path))
        target = self.cache_directory / (expected_digest + '_' + Path(path).name)
        # Fetch on every pass: a retained cache must not hide a changed source.
        if target.exists():
            target.unlink()
        self.command(['/usr/bin/xrdcp', '--silent', '--posc', '--cksum', 'adler32',
                      self.host + '/' + str(path), str(target)])
        checked_digest(target, expected_digest)
        if target.stat().st_size != expected_bytes:
            raise ValueError('Remote file changed during download: ' + str(path))

    def verify_receipt(self, path, expected_digest):
        info = self.metadata(path)
        if info is None:
            raise ValueError('Reviewed preview receipt is unavailable')
        self.verify_file(path, int(info['size']), expected_digest)

    def alias_target(self, alias_path):
        info = self.metadata(alias_path)
        if info is None:
            return None
        if info.get('status') != 'symlink' or not info.get('target'):
            raise ValueError('Refuse to replace a real alias directory or file')
        return info['target']

    def publish_alias(self, alias_path, campaign, allowed_campaigns):
        if campaign not in allowed_campaigns or not CAMPAIGN_PATTERN.fullmatch(campaign):
            raise ValueError('Alias target is outside reviewed compatible runs')
        previous = self.alias_target(alias_path)
        if previous == campaign:
            return False
        if previous is not None and previous not in allowed_campaigns:
            raise ValueError('Existing alias points outside reviewed compatible runs')
        # EOS updates its symlink metadata in one namespace operation. Never
        # remove an old link first, which would create a missing-input interval.
        arguments = ['/usr/bin/eos', self.host, 'file', 'symlink']
        if previous is not None:
            arguments.append('-f')
        arguments.extend([str(alias_path), campaign])
        self.command(arguments)
        if self.alias_target(alias_path) != campaign:
            raise ValueError('EOS alias update did not confirm the expected target')
        return True


def load_configuration(path, expected_digest=None):
    """Verify compatibility once, independently of the ordinary plotter."""
    path = Path(path).resolve()
    if expected_digest is not None:
        checked_digest(path, expected_digest)
    config = read(path)
    if config.get('schema') != 'shift-bsm-campaign-alias-candidates-v1':
        raise ValueError('Require a reviewed BSM alias candidate map')
    alias = config.get('alias')
    if not isinstance(alias, str) or not CAMPAIGN_PATTERN.fullmatch(alias):
        raise ValueError('Invalid shared campaign alias')
    sources = []
    campaigns = set()
    common_settings = None
    points = {}
    for record in config.get('source_records', []):
        campaign = record.get('campaign')
        if (not isinstance(campaign, str) or not CAMPAIGN_PATTERN.fullmatch(campaign)
                or campaign == alias or campaign in campaigns):
            raise ValueError('Duplicate or invalid source campaign')
        campaigns.add(campaign)
        plan_path = Path(record['plan_path'])
        if not plan_path.is_absolute():
            raise ValueError('Source plans must have absolute frozen paths')
        directory = plan_path.parent
        checked_digest(plan_path, record['plan_sha256'])
        checked_digest(directory / 'seal.json', record['seal_sha256'])
        plan = read(plan_path)
        if plan.get('campaign') != campaign:
            raise ValueError('Source campaign differs from its plan')
        declared = config.get('settings', {}).get(campaign, {})
        if not declared:
            raise ValueError('Source campaign has no reviewed condition settings')
        signatures = {}
        for name, artifact in declared.items():
            checked_digest(artifact['path'], artifact['sha256'])
            signatures[name] = artifact['sha256']
        if common_settings is None:
            common_settings = signatures
        elif signatures != common_settings:
            raise ValueError('Incompatible settings cannot share alias ' + alias)
        source = dict(record, directory=directory, plan=plan)
        sources.append(source)
        for row in plan.get('points', []):
            name = row.get('point')
            if not isinstance(name, str) or not POINT_PATTERN.fullmatch(name):
                raise ValueError('Invalid physical point name')
            output = Path(row['final_output_base'])
            if (not output.is_absolute() or '..' in output.parts or output.name != campaign
                    or output.parent.name != 'darkphoton_' + name):
                raise ValueError('Point output differs from its campaign namespace')
            model = {key: row.get(key) for key in
                     ('mass_gev', 'epsilon', 'proper_ctau_mm', 'sampling_epsilon', 'target_mean_lab_flight_m')}
            entry = points.setdefault(name, dict(alias_path=output.parent / alias,
                                                 model=model, candidates=[], external_ready_inputs=[]))
            if entry['model'] != model or entry['alias_path'] != output.parent / alias:
                raise ValueError('A shared point has incompatible physical settings: ' + name)
            entry['candidates'].append(dict(source=source, row=row, output=output))
    if not sources or not points:
        raise ValueError('Controller has no declared production points')
    owned = config.get('owned_points')
    if owned is not None:
        if (not isinstance(owned, list) or not owned or
                any(not isinstance(name, str) or not POINT_PATTERN.fullmatch(name) for name in owned) or
                len(owned) != len(set(owned)) or not set(owned) <= set(points)):
            raise ValueError('Owned points must be distinct existing physical point names')
        # Keep all reviewed source campaigns as allowed alias targets while
        # limiting which physical directories this controller may update.
        points = {name: points[name] for name in owned}
    source_by_campaign = {source['campaign']: source for source in sources}
    for record in config.get('external_ready_inputs', []):
        name, campaign = record.get('point'), record.get('campaign')
        authority = source_by_campaign.get(record.get('condition_campaign'))
        if (name not in points or not isinstance(campaign, str)
                or not CAMPAIGN_PATTERN.fullmatch(campaign) or campaign in campaigns or campaign == alias
                or authority is None or record.get('source_plan_sha256') != authority['plan_sha256']):
            raise ValueError('Preview input has no reviewed compatible condition authority')
        point = points[name]
        originals = [candidate for candidate in point['candidates'] if candidate['source'] is authority]
        if len(originals) != 1 or type(record.get('events')) is not int or \
                not 0 < record['events'] <= originals[0]['row']['events']:
            raise ValueError('Preview events differ from the original source point')
        histogram = Path(record['histogram_path'])
        output = point['alias_path'].parent / campaign
        if histogram.parent != output / 'histograms_merged' or histogram.suffix != '.root':
            raise ValueError('Preview histogram is outside its dedicated campaign')
        receipt = Path(record['receipt_path'])
        if not receipt.is_absolute() or output / 'metadata' not in receipt.parents:
            raise ValueError('Preview receipt must accompany its dedicated campaign')
        if (type(record.get('histogram_bytes')) is not int or record['histogram_bytes'] <= 0
                or not DIGEST_PATTERN.fullmatch(record.get('histogram_sha256', ''))
                or not DIGEST_PATTERN.fullmatch(record.get('receipt_sha256', ''))):
            raise ValueError('Preview input lacks exact payload and receipt digests')
        if any(previous['campaign'] == campaign for previous in point['external_ready_inputs']):
            raise ValueError('Duplicate external preview point')
        point['external_ready_inputs'].append(record)
    return dict(config=config, config_sha256=sha256(path), alias=alias,
                sources=sources, points=points)


def completed_histogram(candidate, transport=None):
    """Publish only after the immutable finalizer verified its merged output."""
    source, row, output = candidate['source'], candidate['row'], candidate['output']
    path = source['directory'] / 'completed_points' / (row['point'] + '.json')
    if not path.is_file():
        return None
    report = read(path)
    if report.get('complete') is not True:
        return None
    if (report.get('point') != row['point'] or report.get('events') != row['events']
            or report.get('plan_sha256') != source['plan_sha256']
            or report.get('seal_sha256') != source['seal_sha256']
            or report.get('final_output_base') != str(output)):
        raise ValueError('Point completion belongs to another frozen production')
    marker = report.get('score_histograms', {}).get('marker', {})
    item = marker.get('published', {}).get('histograms')
    if marker.get('complete') is not True or not isinstance(item, dict):
        raise ValueError('Point has no completed merged histogram publication')
    histogram = Path(item.get('path', ''))
    if histogram.parent != output / 'histograms_merged' or histogram.suffix != '.root':
        raise ValueError('Merged histogram is outside the point output')
    files = (sorted((output / 'histograms_merged').glob('*.root')) if transport is None
             else transport.root_files(output / 'histograms_merged'))
    if files != [histogram]:
        raise ValueError('Expected exactly one merged histogram for the point')
    if transport is None:
        if not histogram.is_file() or histogram.stat().st_size != item.get('bytes'):
            raise ValueError('Merged histogram size differs from its publication')
        checked_digest(histogram, item.get('sha256'))
    else:
        transport.verify_file(histogram, item.get('bytes'), item.get('sha256'))
    return dict(campaign=source['campaign'], histogram_path=str(histogram),
                histogram_sha256=item['sha256'], events=row['events'],
                point_receipt_path=str(path), point_receipt_sha256=sha256(path))


def external_histogram(record, transport=None):
    """Retain a separately published, audited partial preview until replacement."""
    histogram = Path(record['histogram_path'])
    if transport is None:
        if not histogram.is_file() or histogram.stat().st_size != record['histogram_bytes']:
            raise ValueError('Preview histogram differs from its reviewed inventory')
        checked_digest(histogram, record['histogram_sha256'])
        checked_digest(record['receipt_path'], record['receipt_sha256'])
    else:
        transport.verify_file(histogram, record['histogram_bytes'], record['histogram_sha256'])
        transport.verify_receipt(record['receipt_path'], record['receipt_sha256'])
    return dict(campaign=record['campaign'], histogram_path=str(histogram),
                histogram_sha256=record['histogram_sha256'], events=record['events'],
                point_receipt_path=record['receipt_path'], point_receipt_sha256=record['receipt_sha256'],
                partial=True, condition_campaign=record['condition_campaign'])


def publish_alias(alias_path, campaign, allowed_campaigns):
    """Atomically advance a link, preserving real directories and older runs."""
    alias_path = Path(alias_path)
    if campaign not in allowed_campaigns or not CAMPAIGN_PATTERN.fullmatch(campaign):
        raise ValueError('Alias target is outside the reviewed compatible runs')
    if os.path.lexists(alias_path):
        if not alias_path.is_symlink():
            raise ValueError('Refuse to replace a real alias directory or file')
        target = os.readlink(alias_path)
        if target not in allowed_campaigns:
            raise ValueError('Existing alias points outside the reviewed compatible runs')
        if target == campaign:
            return False
    temporary = alias_path.with_name('.' + alias_path.name + '.next-' + uuid.uuid4().hex)
    try:
        os.symlink(campaign, temporary, target_is_directory=True)
        os.replace(temporary, alias_path)
    finally:
        if temporary.is_symlink():
            temporary.unlink()
    return True


def submission_cluster(directory):
    for filename in ('submission_receipt.json', 'submission.json'):
        path = directory / filename
        if path.is_file():
            record = read(path)
            for key in ('manager_cluster', 'dagman_cluster'):
                value = record.get(key)
                if type(value) is int and value > 0:
                    return value
    return None


def scheduler_snapshot(source, config=None):
    config = config or {}
    directory = source['directory']
    cluster = submission_cluster(directory)
    result = dict(campaign=source['campaign'], production_directory=str(directory),
                  manager_cluster=cluster, health='not_submitted')
    if cluster is None:
        return result
    ads = query(f'ClusterId == {cluster} || DAGManJobId == {cluster}',
                ['ClusterId', 'ProcId', 'JobStatus', 'DAGManJobId', 'HoldReason', 'ShiftBSMPoint'],
                timeout=30, pool=config.get('condor_pool'),
                schedd=source.get('schedd', config.get('condor_schedd')))
    result.update(health='running', queue_status_counts=dict(Counter(str(ad.get('JobStatus')) for ad in ads)),
                  held_jobs=[ad for ad in ads if ad.get('JobStatus') == 5], scheduler_state='known')
    managers = [ad for ad in ads if ad.get('ClusterId') == cluster]
    if any(ad.get('JobStatus') == 5 for ad in managers):
        result['health'] = 'manager_held'
    elif not managers:
        result['health'] = 'draining' if ads else 'manager_not_live'
        metrics_path = directory / 'pipeline.dag.metrics'
        if metrics_path.is_file():
            metrics = read(metrics_path)
            if str(metrics.get('dagman_id')) == str(cluster) and metrics.get('end_time', 0) > 0:
                result.update(terminal=True, dag_exit_code=metrics.get('exitcode'))
                result['health'] = 'complete' if metrics.get('exitcode') == 0 else 'failed'
    result['failed_status_files'] = []
    for path in sorted((directory / 'statuses').glob('*.json')):
        try:
            record = read(path)
        except (OSError, json.JSONDecodeError):
            continue
        if record.get('complete') is False and (record.get('error') or record.get('exit_code', 0)):
            result['failed_status_files'].append(str(path))
    result['failure_records'] = sorted(str(path) for path in (directory / 'failures').glob('*.json'))
    result['rescue_files'] = sorted(str(path) for path in directory.glob('pipeline.dag.rescue[0-9][0-9][0-9]'))
    return result


def quota_snapshot(config):
    """Record fresh EOS/AFS headroom; workers retain their own reservation gates."""
    command = ['/usr/bin/eos', config.get('eos_host', 'root://eosuser.cern.ch'), 'quota', '-m']
    environment = native_env()
    for name in ('X509_USER_PROXY', 'KRB5CCNAME'):
        if os.environ.get(name):
            environment[name] = os.environ[name]
    result = subprocess.run(command, capture_output=True, text=True, env=environment,
                            timeout=30, check=True)
    if result.stderr.strip():
        raise RuntimeError('EOS quota state unknown: ' + result.stderr.strip()[-1000:])
    space = config.get('eos_quota_space', '/eos/user/j/jniedzie/')
    rows = [dict(token.split('=', 1) for token in line.split() if '=' in token)
            for line in result.stdout.splitlines()]
    rows = [row for row in rows if row.get('space') == space and 'maxlogicalbytes' in row]
    if len(rows) != 1:
        raise ValueError('Unknown or ambiguous EOS quota response')
    row = rows[0]
    snapshot = dict(checked_at_epoch=time.time(), eos=dict(
        free_logical_bytes=int(row['maxlogicalbytes']) - int(row['usedlogicalbytes']),
        free_files=int(row['maxfiles']) - int(row['usedfiles']), raw=row))
    afs_path = config.get('afs_quota_path')
    if afs_path:
        result = subprocess.run(['/usr/bin/fs', 'listquota', '-path', str(afs_path)],
                                capture_output=True, text=True, env=environment, timeout=30, check=True)
        lines = result.stdout.splitlines()
        if len(lines) != 2:
            raise ValueError('Unknown AFS quota response')
        fields = lines[1].split()
        snapshot['afs'] = dict(quota_kib=int(fields[1]), used_kib=int(fields[2]),
                               free_bytes=(int(fields[1]) - int(fields[2])) * 1024)
    return snapshot


def step(configuration, state_path, *, publish=False, transport=None):
    """One resumable pass; an unavailable campaign cannot block other points."""
    state_path = Path(state_path)
    previous = read(state_path) if state_path.is_file() else {}
    if previous and previous.get('config_sha256') != configuration['config_sha256']:
        raise ValueError('Existing controller state belongs to another frozen config')
    state = dict(schema=SCHEMA, config_sha256=configuration['config_sha256'],
                 started_epoch=previous.get('started_epoch', time.time()),
                 checked_at_epoch=time.time(), checked_utc=datetime.now(timezone.utc).isoformat(),
                 heartbeat_count=previous.get('heartbeat_count', 0) + 1,
                 publish_aliases=publish, productions=[], points={}, errors=[])
    blocked_campaigns = set()
    for source in configuration['sources']:
        try:
            checked_digest(source['directory'] / 'plan.json', source['plan_sha256'])
            checked_digest(source['directory'] / 'seal.json', source['seal_sha256'])
        except Exception as error:
            blocked_campaigns.add(source['campaign'])
            state['productions'].append(dict(campaign=source['campaign'], health='inputs_changed', error=str(error)))
            continue
        try:
            state['productions'].append(scheduler_snapshot(source, configuration['config']))
        except Exception as error:
            # A scheduler outage does not invalidate a completed histogram.
            state['productions'].append(dict(campaign=source['campaign'], scheduler_state='unknown', error=str(error)))
    try:
        state['quota'] = quota_snapshot(configuration['config'])
    except Exception as error:
        state['quota'] = dict(state='unknown', error=str(error))
    quota_known = state['quota'].get('eos', {}).get('free_files', 0) > 0
    for name, point in configuration['points'].items():
        allowed = [record['campaign'] for record in point['external_ready_inputs']]
        allowed.extend(candidate['source']['campaign'] for candidate in point['candidates'])
        entry = dict(alias_path=str(point['alias_path']), ready=False, candidate_errors=[])
        selected = None
        for record in point['external_ready_inputs']:
            try:
                if record['condition_campaign'] in blocked_campaigns:
                    raise ValueError('Preview condition authority changed')
                selected = external_histogram(record, transport)
            except Exception as error:
                entry['candidate_errors'].append(dict(campaign=record['campaign'], error=str(error)))
        for candidate in point['candidates']:
            try:
                if candidate['source']['campaign'] in blocked_campaigns:
                    raise ValueError('Frozen production inputs changed')
                ready = completed_histogram(candidate, transport)
                if ready is not None:
                    selected = ready
            except Exception as error:
                entry['candidate_errors'].append(dict(campaign=candidate['source']['campaign'], error=str(error)))
        if selected is not None:
            entry.update(selected, ready=True)
            try:
                if publish and not quota_known:
                    raise RuntimeError('Alias publication waits for known EOS quota')
                if publish:
                    entry['alias_changed'] = (publish_alias(point['alias_path'], selected['campaign'], allowed)
                        if transport is None else transport.publish_alias(point['alias_path'], selected['campaign'], allowed))
                entry['alias_target'] = (os.readlink(point['alias_path']) if point['alias_path'].is_symlink() else None
                    ) if transport is None else transport.alias_target(point['alias_path'])
            except Exception as error:
                entry['publication_error'] = str(error)
        state['points'][name] = entry
    state['ready_points'] = sum(point['ready'] for point in state['points'].values())
    state['expected_points'] = len(configuration['points'])
    state['latest_targets_ready'] = sum(
        state['points'][name].get('campaign') == point['candidates'][-1]['source']['campaign']
        for name, point in configuration['points'].items())
    state['complete'] = state['latest_targets_ready'] == state['expected_points']
    state['attention_required'] = (state['quota'].get('state') == 'unknown'
        or any(production.get('scheduler_state') == 'unknown' or production.get('health') in
               ('manager_held', 'failed', 'manager_not_live', 'inputs_changed') or production.get('held_jobs')
               or production.get('failed_status_files') for production in state['productions'])
        or any(point.get('candidate_errors') or point.get('publication_error') for point in state['points'].values()))
    save(state_path, state)
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--config-sha256')
    parser.add_argument('--state-directory', type=Path, required=True)
    parser.add_argument('--poll-seconds', type=int, default=120)
    parser.add_argument('--publish-aliases', action='store_true')
    parser.add_argument('--eos-rpc', action='store_true', help='Use native EOS/XRootD commands instead of the /eos mount')
    parser.add_argument('--exit-on-complete', action='store_true', help='Exit successfully after every newest output is ready and published')
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    if args.poll_seconds < 30:
        parser.error('Poll interval must be at least 30 seconds')
    directory = args.state_directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'controller.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        configuration = load_configuration(args.config, args.config_sha256)
        if not Path('/usr/bin/eos').is_file():
            raise RuntimeError('Controller worker has no native EOS client; publication disabled')
        if not args.eos_rpc:
            data_roots = {point['alias_path'].parent.parent for point in configuration['points'].values()}
            if not all(root.is_dir() for root in data_roots):
                raise RuntimeError('Controller worker has no accessible EOS data mount; publication disabled')
        scratch = tempfile.TemporaryDirectory(prefix='shift-bsm-controller-') if args.eos_rpc else None
        transport = EosRpc(configuration['config'].get('eos_host', 'root://eosuser.cern.ch'), scratch.name) if scratch else None
        while True:
            state = {}
            try:
                state = step(configuration, directory / 'live_status.json', publish=args.publish_aliases, transport=transport)
                print(json.dumps(dict(checked_utc=state['checked_utc'], ready_points=state['ready_points'],
                    expected_points=state['expected_points'], attention_required=state['attention_required'])), flush=True)
            except Exception as error:
                save(directory / 'controller_error.json', dict(error=str(error), checked_at_epoch=time.time()))
                print('Controller action waits:', str(error), flush=True)
                if args.once:
                    raise
            if args.once:
                return
            if args.exit_on_complete and state.get('complete') and all(
                    not point.get('publication_error') for point in state['points'].values()):
                return
            time.sleep(args.poll_seconds)


if __name__ == '__main__':
    main()
