#!/usr/bin/env python3
# Code style: AI-code. AI-generated exact saved-GEN publication recovery.
"""Re-audit saved ordinary GEN bytes and finish their publication, without GEN.

Run this as a later execution of the original held DAG job. The failed native
exit and its original ZIP remain evidence; only this real execution may exit0.
Validation performs no EOS writes. Choose --validate-only or --publish explicitly.
"""
import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tarfile
import time
import zipfile


PROFILE = 'shift-qcd-mu-enriched-cylinder-v1'


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


def array_sha(value):
    return hashlib.sha256(json.dumps(value, separators=(',', ':')).encode()).hexdigest()


def write_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value, indent=2)+'\n')
    temporary.replace(path)


def pinned_input(path, expected, staged):
    """Workers consume staged inputs; local validation can read pinned sources."""
    original = Path(path)
    selected = Path(original.name) if Path(original.name).is_file() else original
    if staged and selected.is_absolute():
        raise ValueError('Required recovery input was not staged: '+original.name)
    if sha(selected) != expected:
        raise ValueError('Pinned recovery input changed: '+str(selected))
    return selected


def unpack_saved_source(plan, node, staged=True):
    rows = [row for row in plan['jobs'] if row['node_id'] == node]
    if len(rows) != 1 or plan['source_model_or_sampling_changes'] or not plan['no_new_GEN_calls']:
        raise ValueError('Recovery must name one unchanged saved GEN source')
    row = rows[0]
    failed_path = pinned_input(row['original_failed_receipt'], row['original_failed_receipt_sha256'], staged)
    failed_bytes = failed_path.read_bytes()
    failed = json.loads(failed_bytes)
    manifest_path = pinned_input(row['frozen_manifest'], row['frozen_manifest_sha256'], staged)
    manifest = json.loads(manifest_path.read_text())
    request, audit = failed['request'], failed['semantic_audit']
    if (failed.get('schema') != 'shift-mu-enriched-ordinary-gen-receipt-v1'
            or failed.get('complete') is not False or failed.get('publication_complete') is not False
            or failed.get('exit_status') != 1 or not failed.get('generated_edm')
            or failed['generator_profile'] != PROFILE or manifest['generator_profile'] != PROFILE
            or request != row['request'] or request not in manifest['jobs']
            or request['stage'] != 'production' or request['id'] != node
            or failed['manifest_sha256'] != row['frozen_manifest_sha256']
            or failed['source_inputs'] != manifest['inputs'] or failed['artifacts'] != row['saved_artifacts']
            or not audit.get('complete') or not audit.get('healthy')
            or audit['generator_profile'] != PROFILE or audit['events'] != row['saved_events']
            or audit['events'] != failed['event_count']
            or audit['source_accepted'] != row['framework_source_calls']
            or audit['source_accepted'] != request['trials']
            or audit['full_source_trial_denominator'] != row['full_ND_trial_denominator']
            or audit['sigma_nd_mb'] != row['sigma_nd_mb'] or audit['sigma_mb'] != row['selected_sigma_mb']
            or audit.get('no_second_filter_efficiency') is not True
            or len(audit['event_ids']) != audit['events'] or len(audit['weights']) != audit['events']
            or array_sha(audit['event_ids']) != row['event_identity_sha256']
            or array_sha(audit['weights']) != row['native_weights_sha256']):
        raise ValueError('Saved source identity, exposure or frozen contract changed')
    inputs = {name:pinned_input(manifest_path.parent/name, digest, staged)
              for name, digest in manifest['inputs'].items()}
    archive_path = pinned_input(row['saved_archive'], row['saved_archive_sha256'], staged)
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError('Duplicate ZIP members')
        if (archive.read('result.json') != failed_bytes
                or json.loads(archive.read('request.json')) != request
                or hashlib.sha256(archive.read('manifest.json')).hexdigest() != row['frozen_manifest_sha256']):
            raise ValueError('ZIP receipt, request or manifest differs')
        for name, expected in row['saved_artifacts'].items():
            if Path(name).name != name:
                raise ValueError('Unsafe artifact name')
            info = archive.getinfo(name)
            if info.file_size != expected['bytes'] or info.file_size > 16*1024*1024:
                raise ValueError('Unexpected saved artifact size: '+name)
            payload = archive.read(name)
            if hashlib.sha256(payload).hexdigest() != expected['sha256']:
                raise ValueError('Saved artifact checksum differs: '+name)
            target = Path(name)
            if target.exists() and sha(target) != expected['sha256']:
                raise ValueError('Conflicting local recovery artifact: '+name)
            target.write_bytes(payload)
        if json.loads(Path('semantic_audit.json').read_text()) != audit:
            raise ValueError('Archived semantic audit differs')
        write_json('request.json', request)
    return row, failed, inputs


def runtime_environment(inputs, runtime_env=None):
    if runtime_env:
        env = json.loads(Path(runtime_env).read_text())
    else:
        runtime = Path('runtime')
        runtime.mkdir()
        with tarfile.open(inputs['runtime.tar.gz']) as archive:
            # The checksum-pinned existing CMSSW runtime has CVMFS symlinks.
            archive.extractall(runtime, filter='fully_trusted')
        bootstrap = '''set -euo pipefail
source /cvmfs/cms.cern.ch/cmsset_default.sh
cd runtime/CMSSW_17_0_0_pre4
scram b ProjectRename > ../../recovery_relocate.log 2>&1
eval "$(scram runtime -sh)"
cd ../..
python3 -c 'import json,os;json.dump(dict(os.environ),open("recovery_runtime_env.json","w"))'
'''
        with Path('recovery_bootstrap.log').open('w') as stream:
            subprocess.run(['bash', '-c', bootstrap], stdout=stream, stderr=subprocess.STDOUT,
                           timeout=600, check=True)
        env = json.loads(Path('recovery_runtime_env.json').read_text())
    if env.get('CMSSW_VERSION') != 'CMSSW_17_0_0_pre4':
        raise ValueError('Wrong frozen CMSSW runtime')
    env.pop('LD_PRELOAD', None)
    return env


def native_audit(failed, inputs, env):
    arguments = ['python3', str(inputs['audit_mu_enriched_qcd_gen.py']), '--input', 'gen.root',
                 '--config', 'resolved_gen_cfg.py', '--log', 'cmsRun.log', '--request', 'request.json',
                 '--output', 'recovery_semantic_audit.json']
    with Path('recovery_audit.log').open('w') as stream:
        subprocess.run(arguments, env=env, stdout=stream, stderr=subprocess.STDOUT,
                       timeout=900, check=True)
    fresh = json.loads(Path('recovery_semantic_audit.json').read_text())
    if fresh != failed['semantic_audit']:
        raise ValueError('Native reread differs from the original complete semantic audit')


def finish_publication(row, failed, helper):
    """Read back existing matching files; publish only absent files, receipt last."""
    destination = row['canonical_EOS_directory']
    publication = []
    for name, expected in row['saved_artifacts'].items():
        target = destination+'/'+name
        stat = subprocess.run(['xrdfs', 'root://eosuser.cern.ch', 'stat', target],
                              capture_output=True, text=True, timeout=120)
        if stat.returncode == 0:
            readback = Path('recovery_readback_'+name)
            subprocess.run(['xrdcp', '--cksum', 'adler32', 'root://eosuser.cern.ch/'+target,
                            str(readback)], check=True, timeout=900)
            if readback.stat().st_size != expected['bytes'] or sha(readback) != expected['sha256']:
                raise ValueError('Conflicting existing EOS artifact: '+target)
            readback.unlink()
            publication.append(dict(name=name, path=target, **expected,
                                    remote_stat=stat.stdout, independent_sha256_readback=True))
        elif '[3011]' in stat.stderr+stat.stdout or 'No such file' in stat.stderr+stat.stdout:
            publication.extend(helper.publish([(Path(name), name)], destination))
        else:
            raise ValueError('Unknown EOS state: '+stat.stderr)
    result = copy.deepcopy(failed)
    result.pop('error', None)
    result.pop('error_type', None)
    result.update(complete=True, publication_complete=True, exit_status=0,
                  publication=publication, publication_directory=destination)
    return result


def existing_completion_receipt(row, failed):
    target = row['canonical_EOS_directory']+'/publication_receipt.json'
    stat = subprocess.run(['xrdfs', 'root://eosuser.cern.ch', 'stat', target],
                          capture_output=True, text=True, timeout=120)
    if stat.returncode != 0:
        if '[3011]' in stat.stderr+stat.stdout or 'No such file' in stat.stderr+stat.stdout:
            return None
        raise ValueError('Unknown completion-receipt state: '+stat.stderr)
    subprocess.run(['xrdcp', '--cksum', 'adler32', 'root://eosuser.cern.ch/'+target,
                    'existing_publication_receipt.json'], check=True, timeout=900)
    existing = json.loads(Path('existing_publication_receipt.json').read_text())
    if (any(existing.get(key) != failed.get(key) for key in
            ('schema', 'request', 'manifest_sha256', 'source_inputs', 'semantic_audit', 'artifacts',
             'generator_profile', 'generated_edm', 'event_count'))
            or not existing.get('complete') or not existing.get('publication_complete')
            or existing.get('exit_status') != 0 or existing.get('error')
            or existing.get('physics_valid') is True or existing.get('normalization_ready') is True):
        raise ValueError('Existing completion receipt differs from the exact saved source')
    published = {item['name']:item for item in existing['publication']}
    if set(published) != set(row['saved_artifacts']) or len(published) != len(existing['publication']):
        raise ValueError('Existing publication has missing or duplicate artifact metadata')
    for name, expected in row['saved_artifacts'].items():
        item = published[name]
        if (item['path'] != row['canonical_EOS_directory']+'/'+name
                or any(item.get(key) != value for key, value in expected.items())
                or item.get('independent_sha256_readback') is not True):
            raise ValueError('Existing publication does not bind every original artifact')
    return existing


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--recovery-manifest', required=True)
    parser.add_argument('--expected-sha256', required=True)
    parser.add_argument('--node', required=True)
    parser.add_argument('--runtime-env', help='Existing pinned runtime, local validation only')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--publish', action='store_true')
    mode.add_argument('--validate-only', action='store_true')
    args = parser.parse_args()
    started, status = time.monotonic(), 1
    result = dict(schema='shift-mu-enriched-ordinary-gen-receipt-v1', complete=False,
                  publication_complete=False, exit_status=1, recovery_validation_only=not args.publish)
    try:
        if args.publish and args.runtime_env:
            raise ValueError('Published recovery must bootstrap its own staged frozen runtime')
        if sha(args.recovery_manifest) != args.expected_sha256:
            raise ValueError('Recovery manifest changed')
        plan = json.loads(Path(args.recovery_manifest).read_text())
        if plan.get('schema') != 'shift-exact-saved-GEN-publication-recovery-plan-v1':
            raise ValueError('Unsupported recovery manifest')
        if sha(__file__) != plan['worker_sha256']:
            raise ValueError('Recovery worker differs from the frozen code')
        row, failed, inputs = unpack_saved_source(plan, args.node, staged=not bool(args.runtime_env))
        result = copy.deepcopy(failed)
        proof = dict(original_job_id=row['job_id'], original_exit_status=failed['exit_status'],
            original_failed_receipt=row['original_failed_receipt'],
            original_failed_receipt_sha256=row['original_failed_receipt_sha256'],
            saved_archive_sha256=row['saved_archive_sha256'], worker_sha256=plan['worker_sha256'],
            recovery_manifest_sha256=args.expected_sha256, no_GEN_calls=True)
        native_audit(failed, inputs, runtime_environment(inputs, args.runtime_env))
        proof['fresh_semantic_audit_sha256'] = sha('recovery_semantic_audit.json')
        proof['fresh_semantic_audit_matches_original'] = True
        if args.publish:
            existing = existing_completion_receipt(row, failed)
            module = importlib.util.spec_from_file_location('frozen_GEN_publication', inputs['run_shift_weighted_gen.py'])
            helper = importlib.util.module_from_spec(module)
            module.loader.exec_module(helper)
            result = finish_publication(row, failed, helper)
            result['recovery'] = dict(proof, publication_only=True, wall_seconds=time.monotonic()-started)
            write_json('publication_receipt.json', result)
            if existing:
                result = existing
                result['publication_recovery_reexecution'] = proof
            else:
                helper.publish([(Path('publication_receipt.json'), 'publication_receipt.json')], row['canonical_EOS_directory'])
        else:
            result['recovery_validation'] = dict(proof, complete=True, EOS_writes=False)
            # Validation succeeds as a command; the source remains unpublished.
            result['recovery_validation_only'] = True
        status = 0
    except Exception as error:
        result.update(complete=False, publication_complete=False, exit_status=1,
                      recovery_error=repr(error), recovery_error_type=type(error).__name__)
    finally:
        result['recovery_command_exit_status'] = status
        write_json('result.json', result)
        with zipfile.ZipFile('artifacts.zip', 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for pattern in ('recovery_*.log', 'recovery_semantic_audit.json', 'result.json', 'publication_receipt.json'):
                for path in sorted(Path('.').glob(pattern)):
                    if path.is_file():
                        archive.write(path, path.name)
    return status


if __name__ == '__main__':
    raise SystemExit(main())
