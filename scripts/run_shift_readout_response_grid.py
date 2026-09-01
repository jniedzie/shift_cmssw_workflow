#!/usr/bin/env python3
"""Run a resumable same-SimHit BX/phase detector-response grid locally."""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import subprocess
import sys


CMS_DEFAULT = "/cvmfs/cms.cern.ch/cmsset_default.sh"
RUNTIME_ENVIRONMENT_ALLOWLIST = (
    "CMSSW_PREPARED",
    "KRB5CCNAME",
    "LOGNAME",
    "PATH",
    "SHELL",
    "TMPDIR",
    "USER",
    "WORKFLOW_HOST",
    "X509_USER_PROXY",
)


def parse_offsets(spec):
    values = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            fields = part.split(":")
            if len(fields) not in (2, 3):
                raise ValueError(f"invalid offset range {part!r}")
            start, stop = int(fields[0]), int(fields[1])
            step = int(fields[2]) if len(fields) == 3 else 1
            if step == 0 or (stop - start) * step < 0:
                raise ValueError(f"range does not progress toward its stop: {part!r}")
            values.update(range(start, stop + (1 if step > 0 else -1), step))
        else:
            values.add(int(part))
    if not values:
        raise ValueError("offset list is empty")
    return sorted(values)


def parse_phases(spec):
    values = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            phase = Decimal(part)
        except InvalidOperation as error:
            raise ValueError(f"invalid phase {part!r}") from error
        if not phase.is_finite() or not Decimal(0) <= phase < Decimal(25):
            raise ValueError(f"phase must be in [0, 25) ns, got {part!r}")
        values.add(phase)
    if not values:
        raise ValueError("phase list is empty")
    return sorted(values)


def _decimal_text(value):
    text = format(value.normalize(), "f")
    return "0" if text == "-0" else text


def point_name(offset, phase=Decimal(0)):
    phase_label = _decimal_text(Decimal(phase)).replace(".", "p")
    return f"bx_{'m' if offset < 0 else 'p'}{abs(offset)}_phase_{phase_label}"


def report_matches(path, offset, phase=Decimal(0)):
    try:
        with path.open(encoding="utf-8") as input_file:
            report = json.load(input_file)
        timing = report["simhit_reference_timing"]
        return (
            int(timing["bx_offset"]) == offset
            and abs(float(timing["phase_ns"]) - float(phase)) < 1.0e-9
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False


def runtime_command(cmssw_dir, command):
    script = (
        f"source {CMS_DEFAULT} && cd \"$1\" && eval `scram runtime -sh` "
        "&& shift && exec \"$@\""
    )
    # Do not start a login shell: user profile hooks can reintroduce unrelated
    # compiler/Python library paths after the environment was sanitized.
    return ["/bin/bash", "-c", script, "shift-grid", str(cmssw_dir), *map(str, command)]


def sanitized_runtime_environment(environment):
    result = {
        name: environment[name]
        for name in RUNTIME_ENVIRONMENT_ALLOWLIST
        if environment.get(name)
    }
    result["PATH"] = "/usr/bin:/bin"
    return result


def run_logged(command, log_path, *, cwd=None, env=None):
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
    if completed.returncode:
        raise RuntimeError(
            f"command failed with status {completed.returncode}; see {log_path}"
        )


def run_point(point, args, workflow_root, cmssw_dir):
    offset, phase = point
    point_dir = args.output_dir / point_name(offset, phase)
    capture_report = point_dir / "capture.json"
    trigger_report = point_dir / "trigger_funnel.json"
    if (
        not args.force
        and report_matches(capture_report, offset, phase)
        and report_matches(trigger_report, offset, phase)
    ):
        return point, "reused"

    point_dir.mkdir(parents=True, exist_ok=True)
    environment = sanitized_runtime_environment(os.environ)
    environment.update(
        {
            "CMSSW_PREPARED": "1",
            "SAMPLE_DIR": str(point_dir),
            "PILEUP_MODE": "none",
            "TRIGGER_TIMELINE_MODE": "none",
            "SHIFT_READOUT_DIAGNOSTICS": "1",
            "SHIFT_SIMHIT_REFERENCE_BX_OFFSET": str(offset),
            "SHIFT_SIMHIT_REFERENCE_PHASE_NS": _decimal_text(phase),
            "SHIFT_SIMHIT_REFERENCE_INPUT": str(args.baseline_step1),
        }
    )
    step2_command = runtime_command(
        cmssw_dir,
        [
            workflow_root / "run_step2_digi_raw.sh",
            *(("--force",) if args.force else ()),
            "0",
            str(args.events),
        ],
    )
    run_logged(
        step2_command,
        point_dir / "grid_step2.log",
        cwd=workflow_root,
        env=environment,
    )

    step2_output = point_dir / "samples" / "step2" / "events_step2_part0000.root"
    unpacked = point_dir / "events_unpacked.root"
    unpack_command = runtime_command(
        cmssw_dir,
        [
            "cmsRun",
            workflow_root / "scripts" / "shift_readout_unpack_cfg.py",
            f"inputFiles=file:{step2_output}",
            f"outputFile={unpacked}",
            f"maxEvents={args.events}",
            f"collisionYear={args.collision_year}",
            f"globalTag={args.global_tag}",
        ],
    )
    run_logged(unpack_command, point_dir / "grid_unpack.log")

    for script_name, output_path, log_name in (
        ("analyze_shift_readout_capture.py", capture_report, "grid_capture.log"),
        ("analyze_shift_trigger_funnel.py", trigger_report, "grid_trigger.log"),
    ):
        command = runtime_command(
            cmssw_dir,
            [
                "python3",
                workflow_root / "scripts" / script_name,
                unpacked,
                "--output",
                output_path,
            ],
        )
        run_logged(command, point_dir / log_name)

    if not report_matches(capture_report, offset, phase) or not report_matches(
        trigger_report, offset, phase
    ):
        raise RuntimeError(
            f"response reports failed provenance validation at offset {offset}, "
            f"phase {_decimal_text(phase)} ns"
        )
    return point, "generated"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline_step1", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument(
        "--offsets", default="-5:24",
        help="inclusive comma-separated integers/ranges START:STOP[:STEP]",
    )
    parser.add_argument(
        "--phases", default="0",
        help="comma-separated intra-BX phases in [0,25) ns (default: 0)",
    )
    parser.add_argument("--events", type=int, default=10)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--collision-year", default="2023")
    parser.add_argument("--global-tag", default="auto:phase1_2023_realistic")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    if not args.baseline_step1.is_absolute() or not args.baseline_step1.is_file():
        parser.error("baseline_step1 must be an absolute existing file")
    if args.events < 1 or args.workers < 1:
        parser.error("--events and --workers must be positive")
    try:
        offsets = parse_offsets(args.offsets)
        phases = parse_phases(args.phases)
    except ValueError as error:
        parser.error(str(error))

    workflow_root = Path(__file__).resolve().parents[1]
    cmssw_dir = workflow_root.parent / "CMSSW_17_0_0_pre4"
    if not (cmssw_dir / "src").is_dir():
        parser.error(f"CMSSW release is missing: {cmssw_dir}")
    args.output_dir = args.output_dir.resolve()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    points = [(offset, phase) for offset in offsets for phase in phases]
    failures = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(run_point, point, args, workflow_root, cmssw_dir): point
            for point in points
        }
        for future in as_completed(futures):
            offset, phase = futures[future]
            label = f"offset {offset:+d}, phase {_decimal_text(phase)} ns"
            try:
                _, status = future.result()
                print(f"{label}: {status}", flush=True)
            except Exception as error:  # report all independent point failures
                failures.append((offset, _decimal_text(phase), str(error)))
                print(f"{label}: FAILED: {error}", file=sys.stderr, flush=True)
    if failures:
        print(json.dumps({"failures": failures}, sort_keys=True), file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "offsets": offsets,
                "phases_ns": [_decimal_text(phase) for phase in phases],
                "points": len(points),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
