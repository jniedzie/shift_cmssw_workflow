"""Remove inactive scheduler logs across campaigns; retain EOS provenance logs."""
import argparse
import getpass
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time

def active_jobs():
    result = subprocess.run([os.environ.get('CONDOR_Q_BIN', 'condor_q'), '-global',
        '-constraint', 'Owner == ' + json.dumps(getpass.getuser()), '-json',
        '-attributes', 'ClusterId,Out,Err,UserLog'],
        capture_output=True, text=True, check=True, timeout=45)
    if result.stderr.strip():
        raise ValueError('Scheduler warning: ' + result.stderr.strip())
    jobs = json.loads(result.stdout)
    if not isinstance(jobs, list) or any(not isinstance(j.get('ClusterId'), int) for j in jobs):
        raise ValueError('Incomplete scheduler response')
    return jobs

def candidates(root, jobs, cutoff):
    clusters = {j['ClusterId'] for j in jobs}
    protected = {j[k] for j in jobs for k in ('Out','Err','UserLog') if k in j}
    rows = []
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if not (Path(directory) / d).is_symlink()]
        for name in files:
            path = Path(directory) / name
            match = re.fullmatch(r'condor_(\d+)(?:\.\d+)?\.(?:out|err|log)', name)
            if not match or int(match[1]) in clusters or str(path) in protected:
                continue
            s = path.lstat()
            if stat.S_ISREG(s.st_mode) and s.st_mtime_ns < cutoff:
                rows.append((path, s.st_size, s.st_mtime_ns, s.st_ino))
    return rows

def remove_unchanged(rows):
    count = total = 0
    for path, size, mtime, inode in rows:
        try:
            s = path.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(s.st_mode) or (s.st_size,s.st_mtime_ns,s.st_ino) != (size,mtime,inode):
            continue
        path.unlink()
        count += 1
        total += size
    return count, total

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('condor_log_dir', type=Path)
    p.add_argument('payload_log_dir', type=Path)
    p.add_argument('workflow_executable')  # Legacy interface; frozen wrappers differ.
    args = p.parse_args()
    root = Path(__file__).resolve().parents[1] / 'condor/logs'
    requested = args.condor_log_dir
    if not requested.is_absolute() or requested.resolve() != requested or root.resolve() != root:
        raise ValueError('Require an absolute nonsymlinked workflow log path')
    if requested != root and root not in requested.parents:
        raise ValueError('Refusing a path outside this workflow condor/logs tree')
    cutoff = time.time_ns()
    try:
        jobs = active_jobs()
        rows = candidates(root, jobs, cutoff)
        jobs += active_jobs()
        allowed = {r[0] for r in candidates(root, jobs, cutoff)}
    except (OSError, ValueError, TypeError, subprocess.SubprocessError) as error:
        print(f'WARNING: cleanup skipped; queue/inventory unavailable: {error}')
        return
    count, total = remove_unchanged([r for r in rows if r[0] in allowed])
    print(f'Removed {count} inactive AFS Condor logs ({total} bytes) across campaigns')
    print(f'Preserved payload logs in {args.payload_log_dir}: checkpoint/provenance evidence')

if __name__ == '__main__':
    main()
