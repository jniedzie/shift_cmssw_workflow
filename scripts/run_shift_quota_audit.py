#!/usr/bin/env python3
"""Read EOS quota on an execute node and return a nonce-bound result."""
import json
from pathlib import Path
import pwd
import re
import subprocess
import sys
import time

from shift_condor_native import NativeCondorError, query_account


WORKER_CONSTRAINT = ('JobUniverse == 5 && (ShiftProductionSuite == true || ShiftNtupleProduction == true) '
                    '&& (JobStatus == 1 || JobStatus == 2 || JobStatus == 6 || JobStatus == 7)')


def audit(request):
    result = {'nonce':request['nonce'],'complete':False,'account_coverage_verified':False,
              'account_owner':request['account_owner'],'account_pool':request['account_pool']}
    try:
        text = subprocess.run(['/usr/bin/eos','-b','root://eoshome-j.cern.ch','quota','ls','-m',
                               '/eos/user/j/jniedzie'],check=True,timeout=60,capture_output=True,text=True).stdout
        rows = [dict(re.findall(r'(\w+)=([^\s]+)',line)) for line in text.splitlines()]
        row = next(r for r in rows if r.get('space') == '/eos/user/j/jniedzie/')
        result.update(complete=True,quota_checked_at_epoch=time.time(),
                      free_bytes=int(row['maxlogicalbytes'])-int(row['usedlogicalbytes']),
                      free_files=int(row['maxfiles'])-int(row['usedfiles']))
    except Exception as error:
        result['error']=repr(error)
    # Vanilla jobs receive delegated user credentials. Scheduler-universe
    # processes can query their local queue but cannot authenticate to collectors.
    if result['complete']:
        for attempt in range(1,4):
            try:
                ads=query_account(request['account_owner'],WORKER_CONSTRAINT,
                    ['ClusterId','ProcId','JobUniverse','JobStatus','ShiftSuiteTag'],
                    pool=request['account_pool'],local_schedd=False,timeout=30)
                result.update(account_coverage_verified=True,account_checked_at_epoch=time.time(),account_workers=ads)
                result.pop('account_error',None)
                break
            except NativeCondorError as error:
                result['account_error']=repr(error)
                if attempt<3:time.sleep(10)
            except Exception as error:
                result['account_error']=repr(error)
                break
    result['checked_at_epoch']=time.time()
    return result


def main():
    path=Path(sys.argv[1])
    request=json.loads(path.read_text()) if path.is_file() else dict(nonce=sys.argv[1],
        account_owner=pwd.getpwuid(os.getuid()).pw_name,account_pool='tweetybird04.cern.ch')
    result=audit(request)
    Path('quota_result.json').write_text(json.dumps(result)+'\n')
    if not result['complete']:raise SystemExit(1)


if __name__=='__main__':main()
