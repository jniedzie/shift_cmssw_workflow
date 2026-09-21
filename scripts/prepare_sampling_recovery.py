#!/usr/bin/env python3
"""Prepare exact same-seed timeout recovery into new campaign directories."""
import argparse
import json
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('original_manifest', type=Path)
    p.add_argument('summary', type=Path)
    p.add_argument('prepared_root', type=Path)
    p.add_argument('--tag', default='timeoutretry')
    p.add_argument('--timeout', type=int, default=10800)
    args = p.parse_args()
    summary = json.loads(args.summary.read_text())
    failed = set()
    for key, group in summary['groups'].items():
        if group['missing']:
            raise ValueError('Missing reports: determine scheduler state before recovery')
        for f in group['failed']:
            if not f['error'].startswith('TimeoutExpired('):
                raise ValueError('Only diagnosed wall-time failures supported')
            failed.add((key, f['chunk']))
    complete, recover = [], []
    for line in args.original_manifest.read_text().splitlines():
        fields = line.split()
        sample, mode, lo, hi, events, seed, chunk, tag = fields
        if (f'{sample}/{mode}/{lo}:{hi}', int(chunk)) in failed:
            fields[-1] = args.tag
            recover.append(' '.join(fields))
        complete.append(' '.join(fields))
    if len(recover) != len(failed) or not failed:
        raise ValueError('Incomplete or empty recovery mapping')
    root = args.prepared_root.resolve()
    (root/'recovery.jobs').write_text('\n'.join(recover)+'\n')
    (root/'full_complete.jobs').write_text('\n'.join(complete)+'\n')
    submit = (root/'full.sub').read_text().replace('/full.jobs', '/recovery.jobs')
    submit = submit.replace('getenv = True', f'getenv = True\nenvironment = "SAMPLING_STAGE_TIMEOUT_SECONDS={args.timeout}"')
    (root/'recovery.sub').write_text(submit)
    print(f'Prepared {len(recover)} same-seed recoveries with external timeout {args.timeout}s')


if __name__ == '__main__':
    main()
