#!/usr/bin/env python3
"""Submit the exact prepared DAG once, after checking frozen files and queue."""
import argparse
import fcntl
import json
from pathlib import Path
import re
import subprocess
import tempfile

from generation_publication import sha256
from shift_production_plan import validate_plan


def parse_queue(stdout, stderr):
    if stderr.strip():
        raise ValueError('Scheduler state is unknown: '+stderr.strip())
    # HTCondor 24.12 emits an empty string on a successful no-match query.
    # Global queries may emit several consecutive JSON arrays.
    jobs=[]
    decoder=json.JSONDecoder()
    remaining=stdout.strip()
    while remaining:
        value,end=decoder.raw_decode(remaining)
        if not isinstance(value,list) or any(not isinstance(row,dict) for row in value):
            raise ValueError('Unexpected scheduler response')
        jobs.extend(value)
        remaining=remaining[end:].strip()
    return jobs


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('state', type=Path)
    p.add_argument('--check', action='store_true', help='Read-only readiness check; never submit')
    p.add_argument('--resume', action='store_true', help='Use DAGMan auto-rescue after reviewed failures')
    args = p.parse_args()
    state = args.state.resolve()
    if (state/'SUPERSEDED_BY.json').exists():
        raise ValueError('This prepared DAG was superseded: '+(state/'SUPERSEDED_BY.json').read_text())
    manifest = json.loads((state/'manifest.json').read_text())
    validate_plan(manifest['plan'])
    for name, expected in json.loads((state/'freeze.json').read_text()).items():
        if sha256(state/name) != expected:
            raise ValueError('Prepared file changed: '+name)
    if not manifest['bundle_url'].startswith('root://eosuser.cern.ch//eos/user/'):
        raise ValueError('Runtime bundle must use the canonical EOS XRootD namespace')
    with tempfile.TemporaryDirectory(prefix='shift_bundle_submit_check_') as tmp:
        bundle = Path(tmp)/'runtime.tar.gz'
        subprocess.run(['xrdcp','--silent',manifest['bundle_url'],str(bundle)],check=True)
        if sha256(bundle) != manifest['bundle_sha256']:
            raise ValueError('Frozen runtime archive is missing or changed')
    with (state/'submit.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = subprocess.run(['condor_q','-global','-json','-constraint',
                                 'Owner == "jniedzie" && (ShiftProductionSuite == true || ShiftProductionController == true)'],
                                check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        active = parse_queue(result.stdout, result.stderr)
        if active:
            raise ValueError('A SHIFT production DAG or worker is already active; global concurrency must remain bounded')
        receipt = state/'submission.json'
        if receipt.exists() and not args.resume:
            raise ValueError('Already submitted; review results before an explicit --resume')
        if args.check:
            print(json.dumps(dict(ready=True, submitted=False, state=str(state))))
            return
        if args.resume and not list(state.glob('production.dag.rescue*')):
            raise ValueError('No rescue DAG: inspect the interrupted or held nodes before resuming')
        # The frozen DAGMan submit file already enables AutoRescue=1. Reuse it
        # unchanged so its original caps and source hashes remain reviewable.
        output = subprocess.run(['condor_submit', str(state/'production.dag.condor.sub')],
                                cwd=state, check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout
        match = re.search(r'submitted to cluster (\d+)', output)
        if not match:
            raise ValueError('Submission succeeded but cluster identity is unknown: '+output)
        record = dict(cluster=int(match.group(1)), manifest_sha256=sha256(state/'manifest.json'),
                      resume=args.resume, dag=str(state/'production.dag'), working_directory=str(state))
        if receipt.exists():
            history = state/f'submission_before_{record["cluster"]}.json'
            history.write_bytes(receipt.read_bytes())
        receipt.write_text(json.dumps(record, indent=2)+'\n')
        print(json.dumps(record))


if __name__ == '__main__':
    main()
