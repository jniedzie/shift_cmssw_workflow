#!/usr/bin/env python3
# Code style: AI-code. AI-generated worker-runtime response verification.
"""Verify closed canaries or the final benchmark in the qualified CMS runtime."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import time

from summarize_shift_material_response import summarize


def native_success(path, cluster):
    blocks = path.read_text().split('...') if path.exists() else []
    for block in reversed(blocks):
        if re.search(r'005 \(0*'+str(cluster)+r'\.0+\.0+\)', block):
            return 'Normal termination (return value 0)' in block
    return False


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--canary', action='store_true')
    args = parser.parse_args()
    directory = args.directory.resolve()
    try:
        if args.canary:
            authority = json.loads((directory/'verifier_rollover.json').read_text())
            deadline = time.time()+86400
            while True:
                ready = True
                for name, cluster in authority['preserved_canaries'].items():
                    job = directory/'jobs'/name
                    status = job/'status.json'
                    if status.exists() and not json.loads(status.read_text()).get('complete'):
                        raise ValueError('Preserved canary failed: '+name)
                    ready = ready and status.exists() and native_success(job/'events.log', cluster)
                if ready:
                    break
                if time.time() > deadline:
                    raise TimeoutError('Preserved canaries did not close within one day')
                print(json.dumps(dict(phase='waiting_for_preserved_canaries', epoch=time.time())), flush=True)
                time.sleep(30)
        result = summarize(directory, args.canary)
        name = 'canary_summary.json' if args.canary else 'summary.json'
        Path('report.json').write_text(json.dumps(dict(complete=True,
            summary_sha256=hashlib.sha256((directory/name).read_bytes()).hexdigest(),
            summary_path=str(directory/name)))+'\n')
    except BaseException as error:
        Path('report.json').write_text(json.dumps(dict(complete=False, error=repr(error)))+'\n')
        raise
