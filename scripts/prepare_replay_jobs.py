#!/usr/bin/env python3
"""Prepare, but do not submit, bounded replay jobs from explicit probability ledgers."""
import argparse
import json
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('root', type=Path)
    p.add_argument('--tag', default='p10floor0p1')
    p.add_argument('--seed-base', type=int, default=9240000)
    p.add_argument('--stage-timeout', type=int, default=7200)
    args = p.parse_args()
    if not 60 <= args.stage_timeout <= 21600 or not 0 < args.seed_base < 900000000:
        p.error('Invalid stage allowance or seed base')
    root = args.root.resolve()
    lines, manifest = [], []
    for index, (lower, upper) in enumerate(((1,2),(2,5),(5,10))):
        path = root/f'qcd_{lower}to{upper}_ledger.json'
        ledger = json.loads(path.read_text())
        source = json.loads(Path(ledger['source_report']).read_text())
        if (source['contract']['lower'], source['contract']['upper']) != (lower, upper):
            raise ValueError('Ledger bin mismatch')
        campaign = f'WeightedReplay_qcdmu_pThat_{lower}to{upper}_{args.tag}_20260921_v1'
        for chunk, rows in enumerate(ledger['chunks']):
            if chunk >= 1000:
                raise ValueError('Chunk count exceeds reserved seed block')
            seed = args.seed_base+index*1000+chunk
            lines.append(f'{path} {chunk} {seed} {lower} {upper} {args.tag} {source["contract"]["seed"]}')
        manifest.append(dict(ledger=str(path), campaign=campaign, chunks=len(ledger['chunks']),
                             selected_events=ledger['selected_events']))
    (root/'replay.jobs').write_text('\n'.join(lines)+'\n')
    (root/'replay_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    submit = f'''universe = vanilla
executable = /bin/bash
arguments = "{root}/workflow/scripts/run_sampling_replay_worker.sh {root} $(ledger) $(chunk) $(seed) $(lower) $(upper) $(tag) $(generator_seed)"
getenv = True
environment = "SAMPLING_STAGE_TIMEOUT_SECONDS={args.stage_timeout}"
should_transfer_files = NO
request_cpus = 1
request_memory = 4000 MB
+JobFlavour = "workday"
output = {root}/logs/$(ClusterId).$(Process).out
error = {root}/logs/$(ClusterId).$(Process).err
log = {root}/logs/$(ClusterId).log
queue ledger,chunk,seed,lower,upper,tag,generator_seed from {root}/replay.jobs
'''
    (root/'replay.sub').write_text(submit)
    print(f'Prepared {len(lines)} jobs for {sum(m["selected_events"] for m in manifest)} events')


if __name__ == '__main__':
    main()
