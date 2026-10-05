#!/usr/bin/env python3
"""Prepare a separate rolling GEN DAG; never change or submit the live DAG."""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess

from generation_publication import sha256
from prepare_shift_production_dag import build_dag
from shift_production_plan import validate_plan


def rolling_dag(plan, scheduling_ceiling=None):
    # Retain the supported pilot/sizing dependencies. Replace batch barriers
    # with per-job quota checks in the separately transferred entrypoint.
    prefix = build_dag(plan).split('JOB Q0000 control.sub\n', 1)[0]
    lines = [prefix.rstrip()]
    workers = []
    for chunk in range(max(s['jobs'] for s in plan['strata'])):
        for index, stratum in enumerate(plan['strata']):
            if chunk >= stratum['jobs']:
                continue
            node = f'J{index:02d}_{chunk:05d}'
            workers.append(node)
            lines.extend([f'JOB {node} worker.sub',
                          f'VARS {node} mode="production" node_item="{index}:{chunk}"',
                          f'CATEGORY {node} workers', f'PARENT SIZING CHILD {node}'])
    lines.extend(['JOB COMPLETE control.sub', 'VARS COMPLETE mode="complete" node_item="0"'])
    # Several parent lines avoid a single oversized DAG-parser line.
    for start in range(0, len(workers), 50):
        lines.append('PARENT '+' '.join(workers[start:start+50])+' CHILD COMPLETE')
    lines.extend(['ABORT-DAG-ON COMPLETE 1 RETURN 1',
                  f'MAXJOBS workers {scheduling_ceiling or plan["max_workers"]}', 'MAXJOBS pilots 10',
                  'NODE_STATUS_FILE nodes.status 60 ALWAYS-UPDATE'])
    return '\n'.join(lines)+'\n'


def prepare(source, destination, scheduling_ceiling=None, initial_workers=None):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if any(c.isspace() for c in str(destination)):
        raise ValueError('Require a state path without whitespace')
    if source == destination or source in destination.parents:
        raise ValueError('Use a separate successor directory outside the live DAG')
    freeze = json.loads((source/'freeze.json').read_text())
    for name, expected in freeze.items():
        if sha256(source/name) != expected:
            raise ValueError('Frozen predecessor changed: '+name)
    manifest = json.loads((source/'manifest.json').read_text())
    plan = validate_plan(manifest['plan'])
    ceiling = plan['max_workers'] if scheduling_ceiling is None else scheduling_ceiling
    initial = plan['max_workers'] if initial_workers is None else initial_workers
    if not isinstance(ceiling, int) or not plan['max_workers'] <= ceiling <= 300:
        raise ValueError('Scheduling ceiling must preserve the original reservation and be at most 300')
    if not isinstance(initial, int) or not 1 <= initial <= ceiling:
        raise ValueError('Initial parallelism must be between 1 and the scheduling ceiling')
    destination.mkdir(parents=True, exist_ok=False)
    (destination/'logs').mkdir()
    shutil.copy2(source/'manifest.json', destination/'manifest.json')
    entrypoint = Path(__file__).with_name('run_shift_production_rolling_node.py')
    shutil.copy2(entrypoint, destination/'rolling_node.py')
    original = (source/'bootstrap.sh').read_text()
    commands = ['exec python3 payload/workflow/scripts/run_shift_production_node.py manifest.json "$mode" "$item"',
                'exec python3 rolling_node.py manifest.json "$mode" "$item"']
    command = next((value for value in commands if original.count(value) == 1), None)
    if command is None:
        raise ValueError('Unsupported frozen bootstrap')
    # A rolling predecessor already checks its old scheduling entrypoint.
    # Replace only that external scheduling check; the scientific payload
    # archive and manifest checks stay byte-for-byte unchanged.
    if command == commands[1]:
        old_lines = original.splitlines(keepends=True)
        marker = "printf '%s  rolling_node.py\\n' "
        if sum(line.startswith(marker) for line in old_lines) != 1:
            raise ValueError('Unsupported rolling-entrypoint checksum check')
        original = ''.join(line for line in old_lines if not line.startswith(marker)
                           and not line.startswith("printf '%s  scheduling_policy.json\\n' ")
                           and not line.startswith('export SHIFT_DAG_SCHEDULING_POLICY='))
    check = "printf '%s  rolling_node.py\\n' '"+sha256(entrypoint)+"' | sha256sum -c -\n"
    policy = dict(schema='shift-gen-scheduling-policy-v1',
                  scientific_manifest_sha256=sha256(destination/'manifest.json'),
                  initial_workers=initial, scheduling_ceiling=ceiling,
                  quota_reservation_workers=ceiling,
                  scientific_runtime_changed=False)
    (destination/'scheduling_policy.json').write_text(json.dumps(policy, indent=2)+'\n')
    check += "printf '%s  scheduling_policy.json\\n' '"+sha256(destination/'scheduling_policy.json')+"' | sha256sum -c -\n"
    bootstrap = original.replace(command, check+'export SHIFT_DAG_SCHEDULING_POLICY=scheduling_policy.json\n'
                                 +'exec python3 rolling_node.py manifest.json "$mode" "$item"')
    (destination/'bootstrap.sh').write_text(bootstrap)
    (destination/'bootstrap.sh').chmod(0o755)
    for name in ('worker.sub', 'control.sub'):
        text = (source/name).read_text().replace(str(source), str(destination))
        # Use one actual core: the measured generator uses one core, and
        # another requested core would reserve idle capacity.
        text = re.sub(r'^request_cpus\s*=.*$', 'request_cpus = 1', text, flags=re.M)
        transfer = f'transfer_input_files = {destination}/manifest.json'
        if text.count(transfer) != 1:
            raise ValueError('Unsupported transferred-input description')
        # The predecessor may already transfer an earlier rolling entrypoint.
        text = text.replace(f', {destination}/rolling_node.py', '')
        text = text.replace(f', {destination}/scheduling_policy.json', '')
        text = text.replace(transfer, transfer+f', {destination}/rolling_node.py, {destination}/scheduling_policy.json')
        (destination/name).write_text(text)
    (destination/'production.dag').write_text(rolling_dag(plan, ceiling))
    report = dict(schema='shift-rolling-resumption-v1', predecessor=str(source),
                  manifest_sha256=sha256(destination/'manifest.json'),
                  runtime_bundle_sha256=manifest['bundle_sha256'],
                  max_workers=initial, scheduling_ceiling=ceiling,
                  quota_reservation_workers=ceiling, scientific_runtime_changed=False,
                  completed_jobs='Revalidate exact receipts through the frozen worker; never regenerate validated chunks',
                  readiness='PREPARED ONLY; predecessor and its workers must drain before submission')
    (destination/'schedule.json').write_text(json.dumps(report, indent=2)+'\n')
    subprocess.run(['condor_submit_dag', '-no_submit', '-maxjobs', str(initial),
                    '-maxidle', str(initial), '-batch-name', plan['tag'],
                    '-append', '+ShiftProductionController = true', str(destination/'production.dag')],
                   check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    frozen = {p.name: sha256(p) for p in destination.iterdir() if p.is_file()}
    (destination/'freeze.json').write_text(json.dumps(frozen, indent=2)+'\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('predecessor', type=Path)
    parser.add_argument('destination', type=Path)
    parser.add_argument('--scheduling-ceiling', type=int)
    parser.add_argument('--initial-workers', type=int)
    args = parser.parse_args()
    print(json.dumps(prepare(args.predecessor, args.destination,
                             args.scheduling_ceiling, args.initial_workers), indent=2))


if __name__ == '__main__':
    main()
