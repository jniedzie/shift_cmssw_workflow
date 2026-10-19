#!/usr/bin/env python3
# Code style: AI-code. AI-generated read-only quota/account audit for the scheduler night controller.
"""Run a bounded observation on an authenticated worker; never schedule reconstruction."""
import json
from pathlib import Path
import re
import subprocess
import sys
import time

from shift_condor_native import _account_schedds, query

def audit(request):
    result = {'nonce': request['nonce'], 'complete': False, 'account_coverage_verified': False,
              'account_owner': request['account_owner'], 'account_pool': request['account_pool']}
    try:
        if request.get('external_storage_reservations'):
            # Count completions before measuring free space. A later count
            # could credit publications absent from an older quota snapshot.
            Path('external_commitment_request.json').write_text(json.dumps(request))
            subprocess.run(['/usr/bin/python3', 'read_shift_external_storage_commitments.py',
                '--request', 'external_commitment_request.json',
                '--output', 'external_commitment_result.json'], check=True, timeout=600,
                capture_output=True, text=True)
            external = json.loads(Path('external_commitment_result.json').read_text())
            if external.get('complete') is not True:
                raise ValueError('External storage commitment observation did not complete')
            result['external_storage_commitments'] = external
        result['quota_observation_started_at_epoch'] = time.time()
        text = subprocess.run(['/usr/bin/eos', '-b', 'root://eoshome-j.cern.ch', 'quota', 'ls', '-m',
                               '/eos/user/j/jniedzie'], check=True, timeout=60,
                              capture_output=True, text=True).stdout
        entries = [dict(re.findall(r'(\w+)=([^\s]+)', line)) for line in text.splitlines()]
        entry = next(row for row in entries if row.get('space') == '/eos/user/j/jniedzie/')
        result.update(complete=True, quota_checked_at_epoch=time.time(),
                      free_bytes=int(entry['maxlogicalbytes']) - int(entry['usedlogicalbytes']),
                      free_files=int(entry['maxfiles']) - int(entry['usedfiles']))
        local_name = request['local_schedd_name']
        if not isinstance(local_name, str) or not local_name:
            raise ValueError('Local scheduler identity must be explicit')
        before = _account_schedds(request['account_owner'], timeout=20,
                                  local_schedd=False, pool=request['account_pool'])
        workers, peer_writers = [], []
        for schedd in sorted(before - {local_name}):
            rows = query('Owner == ' + json.dumps(request['account_owner']) + ' && '
                'JobUniverse == 5 && (JobStatus == 1 || JobStatus == 2 || JobStatus == 5 || JobStatus == 6 || JobStatus == 7)',
                ['ClusterId', 'ProcId', 'GlobalJobId', 'Owner', 'JobUniverse', 'JobStatus',
                 'DAGManJobId', 'ShiftNightCampaign', 'Iwd', 'Cmd', 'Args',
                 'JobBatchName', 'ShiftSignalValidationOnly', 'ShiftBSMPoint', 'ShiftDPStatisticsTask',
                 'ShiftDPStatisticsReadOnly', 'ShiftDPStatisticsWatchdog',
                 'ShiftStreamCampaign', 'ShiftMuEnrichedQCD'],
                pool=request['account_pool'], schedd=schedd, local_schedd=False, timeout=20)
            peer_writers.extend(rows)
            workers.extend(row for row in rows if row.get('JobStatus') in (1, 2, 6, 7))
        after = _account_schedds(request['account_owner'], timeout=20,
                                 local_schedd=False, pool=request['account_pool'])
        if before != after or local_name not in before:
            raise ValueError('Account scheduler coverage changed or omitted the local scheduler')
        result.update(remote_account_coverage_verified=True, remote_account_checked_at_epoch=time.time(),
                      account_schedds=sorted(before), excluded_local_schedd=local_name,
                      remote_account_workers=workers, remote_account_peer_writer_rows=peer_writers)
    except Exception as error:
        result['error'] = repr(error)
    result['checked_at_epoch'] = time.time()
    return result


def main():
    request = json.loads(Path(sys.argv[1]).read_text())
    result = audit(request)
    Path('quota_result.json').write_text(json.dumps(result) + '\n')
    if not result['complete']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
