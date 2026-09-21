#!/usr/bin/env python3
"""Snapshot the explicitly reviewed old control AOD candidates; never delete."""
import argparse
import json
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('output', type=Path)
    args = p.parse_args()
    directory = Path('/eos/home-j/jniedzie/shift_cmssw/jpsi/lssPaired_control_10k_2023_v1/samples/step3')
    protected = {522, 641, 643, 645, 666, 776, 826, 928, 952}
    keep, retire = [], []
    for file in sorted(directory.glob('events_AOD_part*.root')):
        if file.is_symlink():
            raise ValueError('Unexpected symlink')
        chunk = int(file.stem.removeprefix('events_AOD_part'))
        stat = file.stat()
        row = dict(path=str(file), chunk=chunk, bytes=stat.st_size, mtime_ns=stat.st_mtime_ns)
        (keep if chunk in protected else retire).append(row)
    if {r['chunk'] for r in keep} != protected or len(retire) != 990:
        raise ValueError('Current inventory differs from the reviewed candidate set')
    result = dict(deletion_authorized=False, deleted_files=0, keep=keep, retire=retire,
                  retire_bytes=sum(r['bytes'] for r in retire),
                  warning='Recheck jobs and saved size/mtime before any authorized deletion. Preserve every keep entry.')
    with args.output.open('x') as out:
        json.dump(result, out, indent=2)
    with args.output.with_suffix('.txt').open('x') as out:
        out.write('\n'.join(r['path'] for r in retire)+'\n')
    print('Prepared',len(retire),'existing candidates;',len(keep),'protected;',result['retire_bytes'],'bytes. Nothing deleted.')


if __name__ == '__main__':
    main()
