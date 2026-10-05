#!/usr/bin/env python3
"""Prepare a persistent, globally bounded DAG. Submission is a separate command."""
import argparse
import hashlib
import re
import shlex
import json
from pathlib import Path
import shutil
import subprocess

from shift_production_plan import make_plan

REPO = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build_dag(plan, batch_size=50):
    lines = ['JOB PREFLIGHT control.sub', 'VARS PREFLIGHT mode="preflight" node_item="0"',
             'ABORT-DAG-ON PREFLIGHT 1 RETURN 1']
    pilots = []
    for index, stratum in enumerate(plan['strata']):
        node = f'P{index:02d}'
        pilots.append(node)
        lines.extend([f'JOB {node} worker.sub',
                      f'VARS {node} mode="pilot" node_item="{index}"',
                      f'CATEGORY {node} pilots',
                      f'PARENT PREFLIGHT CHILD {node}'])
    lines += ['JOB SIZING control.sub', 'VARS SIZING mode="sizing" node_item="0"',
              'PARENT ' + ' '.join(pilots) + ' CHILD SIZING',
              'ABORT-DAG-ON SIZING 1 RETURN 1']
    # Round-robin strata prevent a large QCD bin from starving signal samples.
    rows = [(index, chunk) for chunk in range(max(s['jobs'] for s in plan['strata']))
            for index, s in enumerate(plan['strata']) if chunk < s['jobs']]
    previous = ['SIZING']
    for batch, start in enumerate(range(0, len(rows), batch_size)):
        gate = f'Q{batch:04d}'
        lines.extend([f'JOB {gate} control.sub',
                      f'VARS {gate} mode="batch" node_item="{batch}"',
                      'PARENT ' + ' '.join(previous) + f' CHILD {gate}',
                      f'ABORT-DAG-ON {gate} 1 RETURN 1'])
        previous = []
        for index, chunk in rows[start:start+batch_size]:
            node = f'J{index:02d}_{chunk:05d}'
            previous.append(node)
            lines.extend([f'JOB {node} worker.sub',
                          f'VARS {node} mode="production" node_item="{index}:{chunk}"',
                          f'CATEGORY {node} workers', f'PARENT {gate} CHILD {node}'])
    lines += ['JOB COMPLETE control.sub', 'VARS COMPLETE mode="complete" node_item="0"',
              'PARENT ' + ' '.join(previous) + ' CHILD COMPLETE',
              'ABORT-DAG-ON COMPLETE 1 RETURN 1',
              f'MAXJOBS workers {plan["max_workers"]}', 'MAXJOBS pilots 10',
              'NODE_STATUS_FILE nodes.status 60 ALWAYS-UPDATE']
    return '\n'.join(lines) + '\n'


def submit_description(directory, manifest_sha, control=False, suite_tag=None):
    return '\n'.join([
        'universe = vanilla', f'initialdir = {directory}',
        f'executable = {directory}/bootstrap.sh',
        f'arguments = {manifest_sha} $(mode) $(node_item)',
        'should_transfer_files = YES', 'when_to_transfer_output = ON_EXIT',
        f'transfer_input_files = {directory}/manifest.json',
        'transfer_output_files = ""', 'getenv = False',
        'request_cpus = 1', f'request_memory = {2000 if control else 4000}',
        'request_disk = 10000000', '+MaxRuntime = 50400',
        '+ShiftProductionSuite = true',
        f'+ShiftSuiteTag = "{suite_tag or directory.name}"',
        'on_exit_hold = (ExitBySignal == True) || (ExitCode != 0)',
        'periodic_release = False',
        'output = logs/$(Cluster).$(Process).out',
        'error = logs/$(Cluster).$(Process).err',
        'log = events.log', 'queue 1', ''])


def prepare(directory, plan, bundle_url, bundle_sha):
    directory = Path(directory).resolve()
    if any(c.isspace() for c in str(directory)):
        raise ValueError('Condor state path must not contain whitespace')
    directory.mkdir(parents=True, exist_ok=False)
    (directory/'logs').mkdir()
    manifest = dict(schema='shift-dag-manifest-v1', plan=plan,
                    bundle_url=bundle_url, bundle_sha256=bundle_sha,
                    receipt_base=f'{plan["strata"][0]["campaign"].rsplit("/", 2)[0]}/production_dags/{plan["tag"]}',
                    batch_size=min(50, plan['max_workers']),
                    estimated_peak_bytes_per_worker=250_000_000,
                    minimum_free_bytes=50_000_000_000,
                    minimum_free_files=20000)
    (directory/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    manifest_sha = digest(directory/'manifest.json')
    shutil.copy2(REPO/'scripts/run_shift_production_bootstrap.sh', directory/'bootstrap.sh')
    (directory/'worker.sub').write_text(submit_description(directory, manifest_sha, suite_tag=plan["tag"]))
    (directory/'control.sub').write_text(submit_description(directory, manifest_sha, True, plan["tag"]))
    (directory/'production.dag').write_text(build_dag(plan, manifest['batch_size']))
    # CERN centrally bounds DAGMan scheduler jobs; workers use the account's
    # normal allocation. Never change schedds to evade a site-imposed limit.
    command = ['condor_submit_dag', '-no_submit', '-maxjobs', str(plan['max_workers']),
               '-maxidle', str(plan['max_workers']), '-batch-name', plan['tag'],
               '-append', '+ShiftProductionController = true', str(directory/'production.dag')]
    subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for name in ('worker.sub', 'control.sub'):
        subprocess.run(['condor_submit', '-dry-run', str(directory/(name+'.ad')),
                        str(directory/name), 'mode=preflight', 'node_item=0'], check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        ad = (directory/(name+'.ad')).read_text()
        match = re.search(r'^(?:Args|Arguments)\s*=\s*"([^"]*)"', ad, re.M)
        if not match or shlex.split(match.group(1)) != [manifest_sha, 'preflight', '0']:
            raise ValueError('Condor argument expansion lost the mode or node item: '+name)
    freeze = {p.name: digest(p) for p in directory.iterdir() if p.is_file()
              and p.suffix not in ('.ad',) and p.name != 'freeze.json'}
    (directory/'freeze.json').write_text(json.dumps(freeze, indent=2)+'\n')
    return directory


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--state', required=True, type=Path)
    p.add_argument('--tag', required=True)
    p.add_argument('--output-base', default='/eos/user/j/jniedzie/shift_cmssw')
    p.add_argument('--dy-definition', choices=('mass',), default='mass')
    p.add_argument('--max-workers', type=int, default=50)
    p.add_argument('--include-stratum', action='append', dest='include_strata',
                   help='Run one canonical stratum in this phase; repeat for a bounded partial plan')
    p.add_argument('--bundle-url', required=True)
    p.add_argument('--bundle-sha256', required=True)
    args = p.parse_args()
    if len(args.bundle_sha256) != 64 or any(c not in '0123456789abcdef' for c in args.bundle_sha256):
        p.error('Require the actual SHA-256 of a prepared frozen bundle')
    plan = make_plan(args.tag, args.output_base, args.dy_definition, args.max_workers,
                     args.include_strata)
    result = prepare(args.state, plan, args.bundle_url, args.bundle_sha256)
    print(json.dumps(dict(state=str(result), production_jobs=sum(s['jobs'] for s in plan['strata']),
                         submitted=False, targets=plan['targets'])))


if __name__ == '__main__':
    main()
