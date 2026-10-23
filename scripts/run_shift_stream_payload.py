#!/usr/bin/env python3
# Code style: AI-code. AI-generated small frozen streaming-stage dispatcher.
"""Execute one pinned pipeline command and return compact completion evidence."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile


def read(path):
    return json.loads(Path(path).read_text())


def request(path):
    data = read(path)
    if data.get('schema') != 'shift-stream-payload-v1' or data.get('environment') not in ('cmssw', 'tea'):
        raise ValueError('Unknown streaming payload request')
    if not isinstance(data.get('argv'), list) or not data['argv'] or any(not isinstance(v, str) for v in data['argv']):
        raise ValueError('Missing explicit payload command')
    return data


def payload_environment(environment, tools, parent=None):
    """Keep the analysis storage helper ahead of the workflow facade for TEA."""
    env = dict(os.environ if parent is None else parent)
    scripts = str(Path(tools) / 'scripts')
    paths = [scripts]
    if environment == 'tea':
        # The modules intentionally have the same name but different APIs.
        # Embedded Python in the histogrammer follows PYTHONPATH directly.
        paths = [str(Path(tools) / 'tea/configs'), str(Path(tools) / 'tea/bin'), scripts]
    if env.get('PYTHONPATH'):
        paths.append(env['PYTHONPATH'])
    env['PYTHONPATH'] = os.pathsep.join(paths)
    return env


def finish(data, result, request_path=None):
    receipt = Path(data['result_path'])
    if receipt.is_file():
        evidence = read(receipt)
    else:
        evidence = dict(complete=False, error='Stage did not write its completion receipt')
    if request_path is not None:
        expected = hashlib.sha256(Path(request_path).read_bytes()).hexdigest()
        if evidence.get('request_sha256', expected) != expected:
            evidence.update(complete=False, error='Stage receipt request hash differs')
        evidence['request_sha256'] = expected
    complete = result == 0 and evidence.get('complete') is True
    if not complete and result == 0:
        result = 1
    status = {k: evidence[k] for k in (
        'events', 'nano_path', 'nano_sha256', 'nano_readback_sha256',
        'source_descriptor_sha256', 'selected_event_ids_sha256',
        'source_id', 'gen_sha256', 'descriptor_sha256', 'inventory_sha256',
        'wall_seconds', 'validated_tier_events', 'error', 'removed_scratch_inputs') if k in evidence}
    if isinstance(status.get('events'), list):
        status['events'] = len(status['events'])
    extra = data.get('status_fields', {})
    if set(extra) & (set(status) | {'complete', 'exit_code', 'stage', 'cohort_id', 'analysis_release'}):
        raise ValueError('Request metadata cannot override measured completion evidence')
    status.update(extra)
    status.update(complete=complete, exit_code=result, cohort_id=data['cohort_id'],
                  stage=data['stage'], analysis_release=False)
    evidence.update(exit_code=result)
    Path('result.json').write_text(json.dumps(evidence, indent=2) + '\n')
    with tarfile.open('return_payload.tar.gz', 'w:gz') as archive:
        names=data.get('return_files', [])+([] if complete else data.get('failure_return_files', []))
        for name in names:
            path = Path(name)
            if path.is_absolute() or '..' in path.parts or path.suffix == '.root':
                raise ValueError('Only compact local evidence can return through Condor')
            if path.is_file():
                archive.add(path, arcname=name)
            elif path.is_dir():
                for item in sorted(path.rglob('*')):
                    if item.is_file():
                        if item.is_symlink() or item.suffix not in ('.pdf', '.json'):
                            raise ValueError('Returned plot directories may contain only regular PDF/JSON files')
                        archive.add(item, arcname=str(item))
    status['return_payload_sha256'] = hashlib.sha256(Path('return_payload.tar.gz').read_bytes()).hexdigest()
    Path('status.json').write_text(json.dumps(status, indent=2) + '\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('settings', 'run', 'finish'))
    parser.add_argument('request', type=Path)
    parser.add_argument('exit_code', type=int, nargs='?')
    args = parser.parse_args()
    data = request(args.request)
    if args.mode == 'settings':
        runtime = read('tools/runtimes.json')[data['environment']]
        print('\n'.join(map(str, [data['environment'], runtime['url'], runtime['sha256'],
                                 data['application_timeout_seconds']])))
    elif args.mode == 'run':
        scratch, tools = str(Path.cwd()), str(Path('tools').resolve())
        argv = [value.replace('@TOOLS@', tools).replace('@SCRATCH@', scratch) for value in data['argv']]
        if argv[0] == 'python3':
            argv[0] = sys.executable
        env = payload_environment(data['environment'], tools)
        if data['environment'] == 'tea':
            # Fail before staging Nano inputs if the frozen Python closure is
            # incomplete. Use the same environment as the real application.
            subprocess.run([sys.executable, '-c',
                'from shift_storage_paths import _canonical_path, resolve_storage_path, tier_directory; '
                'from shift_sample_paths import streaming_histogram_inputs'], env=env, check=True)
        subprocess.run(argv, env=env, check=True)
    else:
        sys.exit(finish(data, args.exit_code, args.request))


if __name__ == '__main__':
    main()
