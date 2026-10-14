#!/usr/bin/env python3
# Code style: AI-code. AI-generated read-only quota/account audit for the scheduler night controller.
"""Run a bounded observation on an authenticated worker; never schedule reconstruction."""
import json
from pathlib import Path
import re
import subprocess
import sys
import time

from shift_condor_native import query_account


def audit(request):
    result = {'nonce': request['nonce'], 'complete': False, 'account_coverage_verified': False,
              'account_owner': request['account_owner'], 'account_pool': request['account_pool']}
    try:
        text = subprocess.run(['/usr/bin/eos', '-b', 'root://eoshome-j.cern.ch', 'quota', 'ls', '-m',
                               '/eos/user/j/jniedzie'], check=True, timeout=60,
                              capture_output=True, text=True).stdout
        entries = [dict(re.findall(r'(\w+)=([^\s]+)', line)) for line in text.splitlines()]
        entry = next(row for row in entries if row.get('space') == '/eos/user/j/jniedzie/')
        result.update(complete=True, quota_checked_at_epoch=time.time(),
                      free_bytes=int(entry['maxlogicalbytes']) - int(entry['usedlogicalbytes']),
                      free_files=int(entry['maxfiles']) - int(entry['usedfiles']))
        workers = query_account(request['account_owner'],
                                'JobUniverse == 5 && (JobStatus == 1 || JobStatus == 2 || JobStatus == 6 || JobStatus == 7)',
                                ['ClusterId', 'ProcId', 'JobUniverse', 'JobStatus', 'ShiftSuiteTag',
                                 'ShiftNightCampaign', 'ShiftNightWaveNonce', 'ShiftNightJob', 'ShiftNtupleQuotaAudit'],
                                pool=request['account_pool'], local_schedd=False, timeout=60)
        result.update(account_coverage_verified=True, account_checked_at_epoch=time.time(), account_workers=workers)
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
