#!/usr/bin/env python3
"""Read-only server-side SHIFT storage inventory. Never authorizes deletion."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    base = Path('/eos/home-j/jniedzie/shift_cmssw')
    sizes, shallow_fallbacks = {}, []
    def collect(path, depth):
        server_path = str(path).replace('/eos/home-j/', '/eos/user/j/')+'/'
        command = ['eos', 'root://eosuser.cern.ch', 'find', '-d', '--maxdepth', str(depth), '--du', server_path]
        result = subprocess.run(command, text=True, capture_output=True)
        if result.returncode == 7 and depth > 1:
            # Never retain truncated output. Shallow aggregate metadata is
            # sufficient for high-file-count delay scans; label the scope.
            shallow_fallbacks.append(str(path))
            return collect(path, 1)
        if result.returncode == 7:
            info = subprocess.check_output(['eos', 'root://eosuser.cern.ch', 'fileinfo',
                                             server_path, '-m'], text=True)
            size = re.search(r'\btreesize=(\d+)\b', info)
            if not size:
                raise ValueError('Missing aggregate directory size')
            sizes[str(path)] = int(size.group(1))
            # Partition high-file-count scans by their immediate directories;
            # discard the truncated listing rather than treating it as complete.
            for child in sorted(path.iterdir()):
                if child.is_dir():
                    collect(child, 1)
            return
        result.check_returncode()
        if 'truncat' in result.stderr.lower():
            raise ValueError('Truncated inventory without failure status')
        for line in result.stdout.splitlines():
            fields = line.split(maxsplit=1)
            if len(fields) != 2 or not fields[0].isdigit():
                raise ValueError(f'Unexpected inventory output: {line}')
            sizes[fields[1].replace('/eos/user/j/', '/eos/home-j/').rstrip('/')] = int(fields[0])
    collect(base, 2)
    campaigns = []
    for family in ('jpsi', 'qcd'):
        for campaign in sorted((base/family).iterdir()):
            if not campaign.is_dir():
                continue
            collect(campaign, 3)
            merged = sorted((campaign/'samples/step4_merged').glob('*.root'))
            campaigns.append(dict(path=str(campaign),
                step4_chunks=len(list((campaign/'samples/step4').glob('*.root'))),
                merged=[dict(path=str(path), bytes=path.stat().st_size) for path in merged]))
    quota = subprocess.check_output(['eos', 'root://eosuser.cern.ch', 'quota',
                                     '/eos/user/j/jniedzie/'], text=True)
    jobs = json.loads(subprocess.check_output(['condor_q', '-json', '-attributes',
        'ClusterId,ProcId,JobStatus,Args,Arguments,Cmd,Iwd'], text=True))
    result = dict(audited_utc=datetime.now(timezone.utc).isoformat(), read_only=True,
        directory_sizes=[dict(path=path, bytes=size) for path, size in sorted(sizes.items())],
        shallow_fallbacks=shallow_fallbacks, campaigns=campaigns, quota_text=quota, active_jobs=jobs,
        limitations='Directory aggregate metadata and filenames only. Merges and dependencies require separate semantic validation.',
        deletion_authorized=False, reconstructed_mass_read=False)
    args.output.write_text(json.dumps(result, indent=2)+'\n')
    print('Inventoried', len(sizes), 'directories,', len(campaigns), 'campaigns;',
          len(jobs), 'active jobs. No deletion authorized.')


if __name__ == '__main__':
    main()
