#!/usr/bin/env python3
"""Submit a paired, grouped SHIFT reconstruction delay scan to HTCondor."""

import argparse
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys

from run_shift_reco_delay_scan import find_step1_files, parse_delays


def file_groups(total_files, files_per_job):
    return [
        (first, min(files_per_job, total_files - first))
        for first in range(0, total_files, files_per_job)
    ]


def quote_argument(value):
    value = str(value)
    if '"' in value or "\n" in value:
        raise ValueError(f"unsupported character in Condor argument: {value!r}")
    return f'"{value}"'


def render_submit(runner, baseline, output_dir, delays, groups, workers, memory_mb, log_dir):
    arguments = " ".join(
        [
            quote_argument(baseline),
            quote_argument(output_dir),
            quote_argument(f"--delays={delays}"),
            "--first-file $(first_file)",
            "--files $(file_count)",
            "--files-per-job $(file_count)",
            f"--workers {workers}",
        ]
    )
    rows = "\n".join(f"{first} {count}" for first, count in groups)
    return f"""universe = vanilla
executable = {runner}
arguments = {arguments}
should_transfer_files = NO
getenv = True
request_cpus = {workers}
request_memory = {memory_mb} MB
+MaxRuntime = 43200
notification = Never
output = {log_dir}/condor_$(ClusterId).$(Process).out
error = {log_dir}/condor_$(ClusterId).$(Process).err
log = {log_dir}/condor_$(ClusterId).log
queue first_file,file_count from (
{rows}
)
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline_step1", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--delays", default="-100:100:10")
    parser.add_argument("--files", type=int, help="number of Step-1 files; default: all")
    parser.add_argument("--files-per-job", type=int, default=20)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--memory-mb", type=int, default=8000)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    try:
        parse_delays(args.delays)
        available = find_step1_files(args.baseline_step1)
    except ValueError as error:
        parser.error(str(error))
    total_files = args.files if args.files is not None else len(available)
    if total_files < 1 or total_files > len(available):
        parser.error(f"--files must be between 1 and {len(available)}")
    if args.files_per_job < 1 or args.workers < 1 or args.memory_mb < 1:
        parser.error("--files-per-job, --workers, and --memory-mb must be positive")

    workflow_root = Path(__file__).resolve().parents[1]
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_dir = workflow_root / "condor" / "delay_scan_logs" / f"{output_dir.name}_{timestamp}"
    log_dir.mkdir(parents=True, exist_ok=False)
    submit_path = log_dir / "shift_reco_delay_scan.sub"
    submit_path.write_text(
        render_submit(
            workflow_root / "scripts" / "run_shift_reco_delay_scan.py",
            args.baseline_step1.resolve(),
            output_dir,
            args.delays,
            file_groups(total_files, args.files_per_job),
            args.workers,
            args.memory_mb,
            log_dir,
        ),
        encoding="utf-8",
    )
    print(f"submit file: {submit_path}")
    print(f"jobs: {len(file_groups(total_files, args.files_per_job))}")
    if args.dry_run:
        return 0
    completed = subprocess.run(["condor_submit", str(submit_path)], check=False)
    return completed.returncode


if __name__ == "__main__":
    sys.exit(main())
