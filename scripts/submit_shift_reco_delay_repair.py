#!/usr/bin/env python3
"""Submit one forced HTCondor repair job per invalid delay-scan point."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from run_shift_reco_delay_scan import (
    _decimal_text,
    delay_name,
    find_step1_files,
    normalize_delay,
    parse_delays,
    part_label,
)
from submit_shift_reco_delay_scan import prepare_output_directories, quote_argument


def point_is_complete(output_dir, delay, source, index):
    """Check the complete on-disk contract without changing the scan."""
    part = part_label(source, index)
    stem = f"events_shiftDelayScan_part{part}"
    point_dir = output_dir / delay_name(delay)
    root_path = point_dir / f"{stem}.root"
    report_path = point_dir / f"{stem}.json"
    bx, phase = normalize_delay(delay)
    try:
        with report_path.open(encoding="utf-8") as report_file:
            report = json.load(report_file)
        size = root_path.stat().st_size
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return False
    return (
        report.get("status") == "complete"
        and report.get("format") == "shift-reco-delay-scan-v1"
        and report.get("delay_ns") == _decimal_text(delay)
        and report.get("bx_offset") == bx
        and report.get("phase_ns") == _decimal_text(phase)
        and report.get("source_step1") == [str(source)]
        and report.get("output") == str(root_path)
        and report.get("output_bytes") == size
        and report.get("pileup_mode") == "none"
        and size > 1024
    )


def find_repair_points(baseline, output_dir, delays, total_files, workers=1):
    sources = find_step1_files(baseline)
    if total_files < 1 or total_files > len(sources):
        raise ValueError(f"--files must be between 1 and {len(sources)}")

    def check_source(item):
        index, source = item
        return [
            (delay, index)
            for delay in delays
            if not point_is_complete(output_dir, delay, source, index)
        ]

    indexed_sources = list(enumerate(sources[:total_files]))
    if workers == 1:
        groups = map(check_source, indexed_sources)
    else:
        executor = ThreadPoolExecutor(max_workers=workers)
        groups = executor.map(check_source, indexed_sources)
    try:
        return [point for group in groups for point in group]
    finally:
        if workers != 1:
            executor.shutdown()


def render_submit(runner, baseline, output_dir, points, memory_mb, log_dir):
    arguments = " ".join(
        [
            quote_argument(baseline),
            quote_argument(output_dir),
            quote_argument("--delays=$(delay)"),
            "--first-file $(first_file)",
            "--files 1",
            "--files-per-job 1",
            "--workers 1",
            "--force",
        ]
    )
    rows = "\n".join(f"{_decimal_text(delay)} {index}" for delay, index in points)
    return f"""universe = vanilla
executable = {runner}
arguments = "{arguments}"
should_transfer_files = NO
transfer_executable = False
getenv = True
request_cpus = 1
request_memory = {memory_mb} MB
+MaxRuntime = 7200
notification = Never
output = {log_dir}/condor_$(ClusterId).$(Process).out
error = {log_dir}/condor_$(ClusterId).$(Process).err
log = {log_dir}/condor_$(ClusterId).log
queue delay,first_file from (
{rows}
)
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline_step1", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--delays", default="-100:100:10")
    parser.add_argument("--files", type=int, help="number of Step-1 files; default: all")
    parser.add_argument("--memory-mb", type=int, default=5000)
    parser.add_argument(
        "--workers", type=int, default=8,
        help="parallel metadata checks against EOS (default: 8)",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    try:
        delays = parse_delays(args.delays)
        available = find_step1_files(args.baseline_step1)
        total_files = args.files if args.files is not None else len(available)
        if args.workers < 1:
            raise ValueError("--workers must be positive")
        points = find_repair_points(
            args.baseline_step1.resolve(), args.output_dir.resolve(), delays,
            total_files, args.workers,
        )
    except ValueError as error:
        parser.error(str(error))
    if args.memory_mb < 1:
        parser.error("--memory-mb must be positive")

    print(f"invalid points: {len(points)}")
    if not points:
        return 0
    workflow_root = Path(__file__).resolve().parents[1]
    output_dir = args.output_dir.resolve()
    prepare_output_directories(output_dir, delays)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_dir = workflow_root / "condor" / "delay_repair_logs" / f"{output_dir.name}_{timestamp}"
    log_dir.mkdir(parents=True, exist_ok=False)
    submit_path = log_dir / "shift_reco_delay_repair.sub"
    submit_path.write_text(
        render_submit(
            workflow_root / "scripts" / "run_shift_reco_delay_scan.py",
            args.baseline_step1.resolve(),
            output_dir,
            points,
            args.memory_mb,
            log_dir,
        ),
        encoding="utf-8",
    )
    print(f"submit file: {submit_path}")
    print(f"jobs: {len(points)}")
    if args.dry_run:
        return 0
    completed = subprocess.run(["condor_submit", str(submit_path)], check=False)
    return completed.returncode


if __name__ == "__main__":
    sys.exit(main())
