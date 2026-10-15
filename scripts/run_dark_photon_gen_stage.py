#!/usr/bin/env python3
# Code style: AI-code. AI-generated sealed GEN, publication and downstream handoff worker.
"""Run one independent GEN population, then hand audited populations downstream.

The DAG owns orchestration after logout. Workers never change a physics
contract, build CMSSW, retry a failed population or overwrite EOS payloads.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile

HOST = 'root://eosuser.cern.ch'


def read(path):
    return json.loads(Path(path).read_text())


def sha256(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1048576), b''):
            value.update(block)
    return value.hexdigest()


def write_new(path, value):
    with Path(path).open('x') as output:
        json.dump(value, output, indent=2, sort_keys=True, allow_nan=False)
        output.write('\n')


def update(path, value):
    temporary = Path(str(path) + '.tmp.' + str(os.getpid()))
    write_new(temporary, value)
    os.replace(temporary, path)


def native_env():
    environment = {key: value for key, value in os.environ.items() if key not in
                   ('LD_LIBRARY_PATH', 'PYTHONPATH', 'PYTHONHOME', 'ROOTSYS', 'LD_PRELOAD')}
    environment['PATH'] = '/usr/bin:/bin'
    return environment


def command(argv, *, timeout=300, check=True):
    return subprocess.run(list(map(str, argv)), capture_output=True, text=True,
                          timeout=timeout, check=check, env=native_env())


def verify_seal(directory, expected):
    seal_path = directory / 'seal.json'
    if sha256(seal_path) != expected:
        raise ValueError('GEN stage seal changed')
    seal = read(seal_path)
    for path, digest in seal['files'].items():
        if sha256(path) != digest:
            raise ValueError('Sealed GEN stage dependency changed: ' + path)
    runtime = read(directory / 'runtime_fingerprint.json')
    for path, record in runtime['files'].items():
        if sha256(path) != record['sha256']:
            raise ValueError('Shared CMSSW runtime changed: ' + path)
    return read(directory / 'plan.json'), read(directory / 'config.json')


def root_publication(row, manifest):
    """Bind the retained audit evidence to the canonical EOS GEN payload."""
    gen = Path(row['gen_directory'])
    proof = read(gen / 'root_publication.json')
    expected = dict(schema='shift-dark-photon-local-GEN-retention-v1', point=row['point'],
        local_path=str(gen / 'gen.root'), path=row['final_output_base'] + '/GEN/gen.root',
        sha256=manifest['output_sha256'], readback_sha256=manifest['output_sha256'],
        metadata_sha256={name: sha256(gen / name) for name in
                         ('manifest.json', 'contract.json', 'validation.json')})
    if (type(proof.get('bytes')) is not int or proof['bytes'] <= 0 or
            proof != dict(expected, bytes=proof['bytes'])):
        raise ValueError('Remote-only GEN publication proof differs from the retained audit evidence')
    return proof


def verify_remote_root(remote, expected, size, config=None):
    configuration = config or {}
    with tempfile.TemporaryDirectory(prefix='dp-gen-readback-',
                                     dir=configuration.get('scratch_base', '/tmp')) as temporary:
        check = Path(temporary) / 'gen.root'
        command(['/usr/bin/xrdcp', '--silent', '--cksum', 'adler32',
                 HOST + '/' + remote, str(check)],
                timeout=configuration.get('publication_timeout_seconds', 600))
        if check.stat().st_size != size or sha256(check) != expected:
            raise ValueError('Published GEN readback differs from the complete audited source')


def validate_population(row, config=None):
    """Require the complete accepted population and its original event identities."""
    gen = Path(row['gen_directory'])
    audit, manifest, contract = [read(gen / name) for name in
                                ('validation.json', 'manifest.json', 'contract.json')]
    if not manifest['status'].startswith('GEN runtime audit passed') or not audit.get('runtime_validated'):
        raise ValueError('GEN has not passed the complete runtime audit: ' + row['point'])
    count = row['events']
    identities = audit['event_ids']
    if (audit['events'] != count or len(identities) != count or
            len(set(map(tuple, identities))) != count or
            audit['sum_weights'] != count or audit['sum_weights_squared'] != count or
            len(audit['event_rows']) != count or any(item['weight'] != 1. for item in audit['event_rows']) or
            audit['proposal_ledger']['n_accepted'] != count):
        raise ValueError('GEN full population or native weights changed: ' + row['point'])
    maximum = contract['maximum_framework_events']
    for identity in identities:
        if (len(identity) != 3 or any(type(value) is not int for value in identity) or
                identity[:2] != [row['run_number'], 1] or not 1 <= identity[2] <= maximum):
            raise ValueError('GEN event identity lies outside its frozen run/slot budget')
    if any(left[2] >= right[2] for left, right in zip(identities, identities[1:])):
        raise ValueError('GEN accepted identity order changed')
    if (contract != read(row['signal_contract']) or contract['requested_events'] != count or
            any(contract[key] != row[key] for key in ('mass_gev', 'epsilon', 'seed', 'run_number'))):
        raise ValueError('GEN differs from the exact frozen physical contract: ' + row['point'])
    if (gen / 'gen.root').exists():
        if sha256(gen / 'gen.root') != manifest['output_sha256']:
            raise ValueError('GEN differs from the exact frozen physical contract: ' + row['point'])
    else:
        proof = root_publication(row, manifest)
        # A receipt never substitutes for checking the actual canonical ROOT.
        verify_remote_root(proof['path'], proof['sha256'], proof['bytes'], config)
    frozen = Path(row['gen_argv'][1]).parent
    for name, expected in manifest['source_sha256'].items():
        if sha256(gen / name) != expected or sha256(frozen / name) != expected:
            raise ValueError('GEN source copy changed: ' + name)
    return audit, manifest, contract


def check_afs_margin(config):
    lines = command(['/usr/bin/fs', 'listquota', '-path', config['workspace']], timeout=30).stdout.splitlines()
    row = lines[1].split()
    free_mb = (int(row[1]) - int(row[2])) / 1024
    metadata_mb = config.get('afs_metadata_reservation_mb', 0)
    retain_root = config.get('retain_local_gen_root', True)
    if (type(retain_root) is not bool or type(metadata_mb) is not int or metadata_mb < 0 or
            (not retain_root and metadata_mb == 0)):
        raise ValueError('Remote-only GEN requires a reviewed positive AFS metadata reservation')
    if free_mb < config['minimum_afs_free_mb'] + metadata_mb:
        raise ValueError('AFS free-space margin is too small for the remaining GEN outputs')
    return free_mb


def check_eos_margin(config, required_payload_bytes):
    output = command(['/usr/bin/eos', HOST, 'quota', '-m'], timeout=90).stdout
    rows = [dict(token.split('=', 1) for token in line.split() if '=' in token)
            for line in output.splitlines()]
    rows = [row for row in rows if row.get('space') == config['eos_quota_space']
            and 'maxlogicalbytes' in row]
    if len(rows) != 1:
        raise ValueError('EOS quota authority is unknown or ambiguous')
    free_bytes = int(rows[0]['maxlogicalbytes']) - int(rows[0]['usedlogicalbytes'])
    free_files = int(rows[0]['maxfiles']) - int(rows[0]['usedfiles'])
    if (free_bytes < config['minimum_eos_free_bytes'] + required_payload_bytes or
            free_files < config['minimum_eos_free_files'] + 1):
        raise ValueError('EOS publication would consume the reserved production margin')
    return dict(free_bytes=free_bytes, free_files=free_files)


def publish_gen(row, config):
    if type(config.get('retain_local_gen_root', True)) is not bool:
        raise ValueError('GEN local ROOT retention must be an explicit boolean')
    audit, manifest, unused_contract = validate_population(row, config)
    source = Path(row['gen_directory']) / 'gen.root'
    expected = manifest['output_sha256']
    remote = row['final_output_base'] + '/GEN/gen.root'
    proof = root_publication(row, manifest) if not source.exists() else None
    size = source.stat().st_size if source.exists() else proof['bytes']
    status = command(['/usr/bin/xrdfs', HOST, 'stat', remote], check=False)
    if status.returncode:
        if '[3011]' not in status.stderr and 'No such file' not in status.stderr:
            raise RuntimeError('EOS state is unknown: ' + status.stderr)
        if not source.exists():
            raise ValueError('Canonical remote-only GEN ROOT is missing; refuse to regenerate or overwrite')
        check_eos_margin(config, size)
        command(['/usr/bin/xrdfs', HOST, 'mkdir', '-p', str(Path(remote).parent)])
        command(['/usr/bin/xrdcp', '--silent', '--posc', '--cksum', 'adler32',
                 str(source), HOST + '/' + remote], timeout=config['publication_timeout_seconds'])
    # This also makes an interrupted publication recoverable without overwriting.
    verify_remote_root(remote, expected, size, config)
    publication = dict(point=row['point'], path=remote, sha256=expected,
                       readback_sha256=expected, bytes=size)
    parent = dict(event_ids=audit['event_ids'], events=row['events'], native_sumw=audit['sum_weights'],
        native_sumw2=audit['sum_weights_squared'], gen_path=remote, gen_sha256=expected,
        manifest_sha256=sha256(source.parent / 'manifest.json'),
        contract_sha256=sha256(source.parent / 'contract.json'),
        validation_sha256=sha256(source.parent / 'validation.json'),
        proposal_ledger=audit['proposal_ledger'], full_gen_denominator=audit['full_gen_denominator'],
        proper_ctau_mm=row['proper_ctau_mm'], epsilon=row['epsilon'], mass_gev=row['mass_gev'],
        seed=row['seed'], run_number=row['run_number'], sample_role='validation_only', gen_transport='xrootd')
    if not config.get('retain_local_gen_root', True):
        retention = dict(schema='shift-dark-photon-local-GEN-retention-v1', point=row['point'],
            local_path=str(source), path=remote, sha256=expected, readback_sha256=expected,
            bytes=size, metadata_sha256={name: sha256(source.parent / name) for name in
                                       ('manifest.json', 'contract.json', 'validation.json')})
        receipt = source.parent / 'root_publication.json'
        if receipt.exists():
            if read(receipt) != retention:
                raise ValueError('Previous GEN local-retention receipt differs')
        else:
            write_new(receipt, retention)
        root_publication(row, manifest)
        if source.exists():
            # Only this new local duplicate is removed; EOS and all audit files stay.
            if source.stat().st_size != size or sha256(source) != expected:
                raise ValueError('Local GEN ROOT changed after the successful EOS readback')
            source.unlink()
    return publication, parent


def run_point(directory, expected, point):
    plan, config = verify_seal(directory, expected)
    rows = [row for row in plan['points'] if row['point'] == point]
    if len(rows) != 1:
        raise ValueError('GEN point does not have unique ownership')
    row = rows[0]
    path = directory / 'statuses' / (point + '.json')
    if path.exists() and read(path).get('complete'):
        previous = read(path)
        if previous['seal_sha256'] != expected:
            raise ValueError('Completed GEN receipt belongs to a different stage')
        validate_population(row, config)
        print(json.dumps(dict(complete=True, reused=True, point=point)))
        return
    report = dict(schema='shift-dark-photon-GEN-stage-point-v1', point=point, complete=False,
                  seal_sha256=expected, started_utc=datetime.now(timezone.utc).isoformat())
    update(path, report)
    try:
        report['afs_free_mb_before'] = check_afs_margin(config)
        gen = Path(row['gen_directory'])
        if not gen.exists():
            runtime = plan['runtime']
            shell = ('source /cvmfs/cms.cern.ch/cmsset_default.sh && cd ' +
                     shlex.quote(str(Path(runtime['cmssw_base']) / 'src')) +
                     ' && eval "$(scram runtime -sh)" && exec ' + shlex.join(row['gen_argv']))
            with (directory / 'logs' / (point + '_GEN.log')).open('x') as log:
                subprocess.run(['/bin/bash', '-c', shell], stdout=log, stderr=subprocess.STDOUT,
                    timeout=config['gen_worker_timeout_seconds'], check=True, env=native_env())
        publication, parent = publish_gen(row, config)
        verify_seal(directory, expected)
        report.update(complete=True, publication=publication, parent=parent,
                      finished_utc=datetime.now(timezone.utc).isoformat())
    except Exception as error:
        report['error'] = repr(error)
        raise
    finally:
        update(path, report)
    print(json.dumps(dict(complete=True, point=point, events=row['events'])))


def collect(directory, expected):
    plan, config = verify_seal(directory, expected)
    parents, publications, seen = {}, [], set()
    for row in plan['points']:
        path = directory / 'statuses' / (row['point'] + '.json')
        status = read(path)
        if not status.get('complete') or status['seal_sha256'] != expected:
            raise ValueError('Full-grid GEN collection requires every complete sealed point')
        validate_population(row, config)
        parent = status['parent']
        identities = set(map(tuple, parent['event_ids']))
        if seen.intersection(identities):
            raise ValueError('Independent GEN populations contain repeated event identities')
        seen.update(identities)
        parents[row['point']] = parent
        publications.append(status['publication'])
    for prior_path in config['prior_inventories']:
        old = read(prior_path)
        for parent in old['points'].values():
            if seen.intersection(map(tuple, parent['event_ids'])):
                raise ValueError('New GEN population repeats an old accepted event')
    inventory = dict(schema='shift-independent-signal-GEN-inventory-v1', complete=True,
        points=parents, sample_role='validation_only', each_parent_ledger_counted_once=True,
        technical_trials_are_not_collision_exposure=True)
    publication = dict(schema='shift-stage-GEN-publication-v1', complete=True,
                       inputs=publications, previous_campaigns_untouched=True)
    for name, value in [('gen_inventory.json', inventory), ('gen_publication.json', publication)]:
        path = directory / name
        if path.exists():
            if read(path) != value:
                raise ValueError('Previous GEN collection differs: ' + name)
        else:
            write_new(path, value)
    print(json.dumps(dict(complete=True, events=len(seen), points=len(parents))))


def handoff(directory, expected):
    unused_plan, config = verify_seal(directory, expected)
    if not read(directory / 'gen_inventory.json').get('complete'):
        raise ValueError('Downstream handoff requires the complete full GEN denominator')
    report_path = directory / 'handoff_status.json'
    report = read(report_path) if report_path.exists() else dict(
        schema='shift-dark-photon-GEN-handoff-v1', seal_sha256=expected, complete=False, phases=[])
    if report['seal_sha256'] != expected:
        raise ValueError('Downstream handoff belongs to another sealed stage')
    for index, argv in enumerate(config['handoff_argvs']):
        completed = [item for item in report['phases'] if item['index'] == index and item.get('complete')]
        if completed:
            if sha256(completed[0]['log']) != completed[0]['log_sha256']:
                raise ValueError('Completed downstream handoff log changed')
            continue
        log_path = directory / 'logs' / ('handoff_' + str(index) + '.log')
        if log_path.exists():
            raise ValueError('Interrupted downstream command needs receipt/scheduler review: ' + str(log_path))
        report['phase'] = index
        update(report_path, report)
        with log_path.open('x') as log:
            subprocess.run(argv, stdout=log, stderr=subprocess.STDOUT,
                           timeout=config['handoff_timeout_seconds'], check=True, env=native_env())
        report['phases'].append(dict(index=index, complete=True, log=str(log_path), log_sha256=sha256(log_path)))
        update(report_path, report)
    report.update(complete=True, finished_utc=datetime.now(timezone.utc).isoformat())
    update(report_path, report)
    print(json.dumps(dict(complete=True, handoff_commands=len(config['handoff_argvs']))))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--seal', required=True)
    parser.add_argument('--action', choices=('run', 'collect', 'handoff'), required=True)
    parser.add_argument('--point')
    args = parser.parse_args()
    directory = args.directory.resolve()
    if args.action == 'run':
        run_point(directory, args.seal, args.point)
    elif args.action == 'collect':
        collect(directory, args.seal)
    else:
        handoff(directory, args.seal)


if __name__ == '__main__':
    main()
