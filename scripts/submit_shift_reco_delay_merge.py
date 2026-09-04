#!/usr/bin/env python3
"""Submit one provenance-preserving merge job per completed delay point."""

import argparse
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys


def quote_argument(value):
    value = str(value)
    if "'" in value or "\n" in value:
        raise ValueError(f"unsupported character in Condor argument: {value!r}")
    return f"'{value}'"


def find_delays(scan_dir):
    return sorted(
        path.name
        for path in scan_dir.glob("delay_*ns")
        if path.is_dir()
    )


def render_submit(runner, scan_dir, output_dir, delay_names, log_dir):
    arguments = " ".join(
        [quote_argument(scan_dir), "$(delay_name)", "--output-dir", quote_argument(output_dir)]
    )
    rows = "\n".join(delay_names)
    return f"""universe = vanilla
executable = {runner}
arguments = "{arguments}"
should_transfer_files = NO
transfer_executable = False
getenv = True
request_cpus = 1
request_memory = 3000 MB
request_disk = 4000 MB
+MaxRuntime = 7200
notification = Never
output = {log_dir}/condor_$(ClusterId).$(Process).out
error = {log_dir}/condor_$(ClusterId).$(Process).err
log = {log_dir}/condor_$(ClusterId).log
queue delay_name from (
{rows}
)
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scan_dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    scan_dir = args.scan_dir.resolve()
    delay_names = find_delays(scan_dir)
    if not delay_names:
        parser.error(f"no delay directories found under {scan_dir}")
    output_dir = (args.output_dir or (scan_dir / "merged")).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    workflow_root = Path(__file__).resolve().parents[1]
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_dir = workflow_root / "condor" / "delay_merge_logs" / f"{scan_dir.name}_{timestamp}"
    log_dir.mkdir(parents=True, exist_ok=False)
    submit_path = log_dir / "shift_reco_delay_merge.sub"
    submit_path.write_text(
        render_submit(
            workflow_root / "scripts" / "merge_shift_reco_delay.py",
            scan_dir,
            output_dir,
            delay_names,
            log_dir,
        ),
        encoding="utf-8",
    )
    print(f"submit file: {submit_path}")
    print(f"jobs: {len(delay_names)}")
    if args.dry_run:
        return 0
    return subprocess.run(["condor_submit", str(submit_path)], check=False).returncode


if __name__ == "__main__":
    sys.exit(main())
