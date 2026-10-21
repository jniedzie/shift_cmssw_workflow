#!/usr/bin/env python3
# Code style: AI-code. AI-generated bounded fine-bin GEN and tail-scan worker.
"""Run the unchanged weighted mu-enriched source, then save its tail identities.

The native generator audit and independent EOS readback must pass before the
tail scan. No detector jobs, analysis aliases or production gates are changed.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from run_shift_weighted_gen import publish, sha


def run(manifest_path, expected, node):
    started = time.time()
    manifest = json.loads(manifest_path.read_text())
    if sha(manifest_path) != expected:
        raise ValueError('Frozen GEN worklist changed')
    row = next(item for item in manifest['jobs'] if item['id'] == node)
    receipt = dict(complete=False, node_id=node, started_epoch=started,
                   manifest_sha256=expected, physics_normalization_ready=False)
    try:
        with Path('generation_worker.log').open('w') as output:
            subprocess.run([sys.executable, 'run_shift_weighted_gen.py', '--manifest', str(manifest_path),
                            '--expected-sha256', expected, '--node', node], stdout=output,
                           stderr=subprocess.STDOUT, timeout=7200, check=True)
        generation = json.loads(Path('result.json').read_text())
        if not generation['complete'] or not generation['publication_complete']:
            raise ValueError('Native GEN publication is incomplete')
        env = json.loads(Path('runtime_env.json').read_text())
        env.pop('LD_PRELOAD', None)
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        with Path('tail_scan.log').open('w') as output:
            subprocess.run(['python3', 'trace_shift_material_response.py', '--gen-input', 'gen.root',
                            '--source-receipt', 'result.json', '--output', 'tail_scan'], env=env,
                           stdout=output, stderr=subprocess.STDOUT, timeout=3600, check=True)
        summary = json.loads(Path('tail_scan/summary.json').read_text())
        if not summary['complete'] or summary['source_GEN_sha256'] != generation['artifacts']['gen.root']['sha256']:
            raise ValueError('Tail scan did not preserve original source identity')
        # GEN and its source ledger are already published by the native worker.
        # Additional diagnostics are independently read back before completion.
        publication = publish([(Path('tail_scan/GEN_tail_selection.json.gz'), 'GEN_tail_selection.json.gz'),
                               (Path('tail_scan/summary.json'), 'tail_summary.json')],
                              generation['publication_directory'])
        receipt.update(complete=True, generation_receipt_sha256=sha('result.json'),
                       generation_directory=generation['publication_directory'],
                       full_source_trials=summary['full_source_trials'], counts=summary['counts'],
                       targets=summary['targets'], selected_events=summary['selected_events'],
                       tail_publication=publication, saved_decays_unchanged=True)
        receipt['finished_epoch'] = time.time()
        Path('tail_worker_result.json').write_text(json.dumps(receipt, indent=2)+'\n')
        publish([(Path('tail_worker_result.json'), 'tail_trace_complete.json')],
                generation['publication_directory'])
    except BaseException as error:
        receipt.update(complete=False, error=repr(error), finished_epoch=time.time())
        Path('tail_worker_result.json').write_text(json.dumps(receipt, indent=2)+'\n')
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--expected-sha256', required=True)
    parser.add_argument('--node', required=True)
    args = parser.parse_args()
    run(args.manifest, args.expected_sha256, args.node)
