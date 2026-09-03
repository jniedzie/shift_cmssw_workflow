#!/usr/bin/env python3
"""Scan physical SHIFT delays using one staged Step-1 file and compact NanoAOD outputs."""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from run_shift_readout_response_grid import (
    CMS_DEFAULT,
    runtime_command,
    sanitized_runtime_environment,
)


BUNCH_SPACING_NS = Decimal("25")


def _decimal_text(value):
    text = format(Decimal(value).normalize(), "f")
    return "0" if text == "-0" else text


def parse_delays(spec):
    """Parse comma-separated Decimal values and inclusive start:stop:step ranges."""
    values = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        fields = part.split(":")
        try:
            numbers = [Decimal(field) for field in fields]
        except InvalidOperation as error:
            raise ValueError(f"invalid delay {part!r}") from error
        if any(not value.is_finite() for value in numbers):
            raise ValueError(f"delay must be finite, got {part!r}")
        if len(numbers) == 1:
            values.add(numbers[0])
            continue
        if len(numbers) not in (2, 3):
            raise ValueError(f"invalid delay range {part!r}")
        start, stop = numbers[:2]
        step = numbers[2] if len(numbers) == 3 else Decimal(1)
        if step == 0 or (stop - start) * step < 0:
            raise ValueError(f"range does not progress toward its stop: {part!r}")
        value = start
        if step > 0:
            while value <= stop:
                values.add(value)
                value += step
        else:
            while value >= stop:
                values.add(value)
                value += step
    if not values:
        raise ValueError("delay list is empty")
    return sorted(values)


def normalize_delay(delay_ns):
    """Map any signed delay to the exact CMSSW BX plus [0,25) ns phase form."""
    delay = Decimal(delay_ns)
    bx = int((delay / BUNCH_SPACING_NS).to_integral_value(rounding=ROUND_FLOOR))
    phase = delay - Decimal(bx) * BUNCH_SPACING_NS
    return bx, phase


def delay_name(delay_ns):
    delay = Decimal(delay_ns)
    sign = "m" if delay < 0 else "p"
    label = _decimal_text(abs(delay)).replace(".", "p")
    return f"delay_{sign}{label}ns"


def find_step1_files(path):
    path = path.resolve()
    if path.is_file():
        return [path]
    candidates = path / "samples" / "step1"
    search_dir = candidates if candidates.is_dir() else path
    files = sorted(search_dir.glob("events_step1_part*.root"))
    if not files:
        raise ValueError(f"no events_step1_part*.root files found under {path}")
    return files


def part_label(path, fallback):
    stem = path.stem
    marker = "events_step1_part"
    return stem[len(marker):] if stem.startswith(marker) else f"{fallback:04d}"


def compact_footer(output_path, delay, bx, phase, source_paths):
    provenance = json.dumps(
        {
            "format": "shift-reco-delay-scan-v1",
            "delay_ns": _decimal_text(delay),
            "bx_offset": bx,
            "phase_ns": _decimal_text(phase),
            "source_step1": [str(path) for path in source_paths],
            "pileup_mode": "none",
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return f'''\n
# Test-only compact output for the same-SimHit reconstruction delay scan.
# Electronics and reconstruction settings above remain unchanged.
process.schedule.remove(process.AODSIMoutput_step)
from PhysicsTools.NanoAOD.genparticles_cff import finalGenParticles, genParticleTable
from PhysicsTools.NanoAOD.common_cff import Var
process.finalGenParticles = finalGenParticles.clone(src="genParticles")
process.genParticleTable = genParticleTable.clone(src="finalGenParticles")
del process.genParticleTable.externalVariables.iso
process.genParticleTable.variables.pz = Var("pz", float, precision=23)
for _coordinate in ("vx", "vy", "vz"):
    setattr(process.genParticleTable.variables, _coordinate,
            Var(_coordinate, float, precision=23))
process.nanoMetadata = cms.EDProducer(
    "UniqueStringProducer", strings=cms.PSet(tag=cms.string({provenance!r})))
process.nanoAOD_step = cms.Path(
    process.finalGenParticles + process.genParticleTable + process.nanoMetadata)
from PhysicsTools.ShiftMuonSegments.shiftMuonSegments_cff import addShiftMuonSegments
process = addShiftMuonSegments(
    process, augmentDTHits=True, augmentTrackerHits=False, useExtendedTiming=False)
process.NANOAODSIMoutput = cms.OutputModule(
    "NanoAODOutputModule",
    fileName=cms.untracked.string({('file:' + str(output_path))!r}),
    outputCommands=cms.untracked.vstring(
        "drop *",
        "keep nanoaodFlatTable_genParticleTable_*_*",
        "keep nanoaodFlatTable_shiftMuonTable_*_*",
        "keep nanoaodFlatTable_shiftMuonSegmentsTable_*_*",
        "keep nanoaodUniqueString_nanoMetadata_*_*",
    ),
)
process.NANOAODSIMoutput_step = cms.EndPath(process.NANOAODSIMoutput)
process.schedule.append(process.nanoAOD_step)
process.schedule.append(process.NANOAODSIMoutput_step)
'''


def report_matches(report_path, output_path, delay, source_paths):
    try:
        with report_path.open(encoding="utf-8") as report_file:
            report = json.load(report_file)
        return (
            report["status"] == "complete"
            and report["delay_ns"] == _decimal_text(delay)
            and report["source_step1"] == [str(path) for path in source_paths]
            and output_path.is_file()
            and output_path.stat().st_size > 1024
        )
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        return False


def run_logged(command, log_path, *, env):
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log_file:
        completed = subprocess.run(
            command,
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
    if completed.returncode:
        raise RuntimeError(
            f"command failed with status {completed.returncode}; see {log_path}"
        )


def run_point(delay, staged_inputs, source_inputs, part, args, cmssw_dir, work_root):
    bx, phase = normalize_delay(delay)
    point_dir = args.output_dir / delay_name(delay)
    output_path = point_dir / f"events_shiftDelayScan_part{part}.root"
    report_path = point_dir / f"events_shiftDelayScan_part{part}.json"
    if not args.force and report_matches(report_path, output_path, delay, source_inputs):
        return "reused"
    if not args.force and (output_path.exists() or report_path.exists()):
        raise RuntimeError(
            f"existing output or report does not match this scan point; inspect it or rerun "
            f"with --force: {output_path}"
        )

    point_dir.mkdir(parents=True, exist_ok=True)
    config_dir = args.output_dir / "configs" / delay_name(delay)
    log_dir = args.output_dir / "logs" / delay_name(delay)
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / f"events_shiftDelayScan_part{part}_cfg.py"
    driver_log = log_dir / f"cmsDriver_part{part}.log"
    run_log = log_dir / f"cmsRun_part{part}.log"
    dummy_aod = work_root / f"unused_aod_{delay_name(delay)}_{part}.root"
    local_output = work_root / f"events_shiftDelayScan_{delay_name(delay)}_part{part}.root"

    timing = (
        "from IOMC.ShiftEventTiming.shiftSimHitTiming_customise import "
        "customiseShiftSimHitReferenceTiming; "
        f"process = customiseShiftSimHitReferenceTiming(process, bxOffset={bx}, "
        f"phaseNs={_decimal_text(phase)}, bunchSpacingNs=25.0); "
    )
    reco = (
        "from PhysicsTools.ShiftMuonSegments.shiftMuonSegments_customise import "
        "customiseKeepShiftTruth, customiseRecoForShiftMuons, "
        "customiseTraversingShiftMuonReco; "
        "process = customiseKeepShiftTruth(process, keepMergedTrackTruth=False, "
        "keepSimMuonRPCDigis=True); "
        "process = customiseRecoForShiftMuons(process, numberOfSigma=5.0, "
        "maxHitChi2=100.0, seedPosition='in', doBackwardFilter=True, "
        "keepAllSeedSegments=True, navigationType='Standard', "
        "pcaPropagator='SteppingHelixPropagatorAny', enableDTMeasurement=True, "
        "enableGEMMeasurement=False); "
        "process = customiseTraversingShiftMuonReco(process, trackerMode='none', "
        "enableDTMeasurement=True)"
    )
    driver = runtime_command(
        cmssw_dir,
        [
            "cmsDriver.py", "shiftDelayScan",
            "--step", "DIGI:pdigi_valid,L1,DIGI2RAW,RAW2DIGI,L1Reco,RECO",
            "--conditions", args.global_tag,
            "--datatier", "AODSIM", "--eventcontent", "AODSIM",
            "--geometry", "DB:Extended", "--era", "Run3_2023",
            "--filein", ",".join(f"file:{path}" for path in staged_inputs),
            "--fileout", f"file:{dummy_aod}",
            "--python_filename", config_path,
            "--no_exec", "-n", str(args.events),
            "--customise_commands", timing + reco,
        ],
    )
    environment = sanitized_runtime_environment(os.environ)
    run_logged(driver, driver_log, env=environment)
    with config_path.open("a", encoding="utf-8") as config_file:
        config_file.write(compact_footer(local_output, delay, bx, phase, source_inputs))
    run_logged(
        runtime_command(cmssw_dir, ["cmsRun", config_path]),
        run_log,
        env=environment,
    )
    if not local_output.is_file() or local_output.stat().st_size <= 1024:
        raise RuntimeError(f"cmsRun did not produce a healthy compact output: {local_output}")
    partial_output = output_path.parent / f".{output_path.name}.{os.getpid()}.partial"
    try:
        shutil.copy2(local_output, partial_output)
        if partial_output.stat().st_size != local_output.stat().st_size:
            raise RuntimeError(f"incomplete staged output: {partial_output}")
        os.replace(partial_output, output_path)
    finally:
        if partial_output.exists():
            partial_output.unlink()
    report = {
        "status": "complete",
        "format": "shift-reco-delay-scan-v1",
        "delay_ns": _decimal_text(delay),
        "bx_offset": bx,
        "phase_ns": _decimal_text(phase),
        "source_step1": [str(path) for path in source_inputs],
        "output": str(output_path),
        "output_bytes": output_path.stat().st_size,
        "pileup_mode": "none",
    }
    with report_path.open("w", encoding="utf-8") as report_file:
        json.dump(report, report_file, indent=2, sort_keys=True)
        report_file.write("\n")
    return "generated"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline_step1", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument(
        "--delays", default="-100:100:10",
        help="delays in ns; comma-separated values or inclusive START:STOP:STEP ranges",
    )
    parser.add_argument("--events", type=int, default=-1, help="maximum events per file group")
    parser.add_argument("--first-file", type=int, default=0)
    parser.add_argument("--files", type=int, default=1, help="number of Step-1 files")
    parser.add_argument(
        "--files-per-job", type=int, default=1,
        help="Step-1 files processed together in each CMSSW job",
    )
    parser.add_argument("--workers", type=int, default=1, help="parallel delays per staged file")
    parser.add_argument("--global-tag", default="auto:phase1_2023_realistic")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    try:
        delays = parse_delays(args.delays)
        inputs = find_step1_files(args.baseline_step1)
    except ValueError as error:
        parser.error(str(error))
    if args.events == 0 or args.events < -1:
        parser.error("--events must be -1 or positive")
    if args.first_file < 0 or args.files < 1 or args.files_per_job < 1 or args.workers < 1:
        parser.error(
            "--first-file must be non-negative; --files, --files-per-job, and --workers "
            "must be positive"
        )
    selected = inputs[args.first_file:args.first_file + args.files]
    if not selected:
        parser.error("the selected Step-1 file range is empty")

    workflow_root = Path(__file__).resolve().parents[1]
    cmssw_dir = workflow_root.parent / "CMSSW_17_0_0_pre4"
    if not (cmssw_dir / "src").is_dir():
        parser.error(f"CMSSW release is missing: {cmssw_dir}")
    args.output_dir = args.output_dir.resolve()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    failures = []
    groups = [
        selected[index:index + args.files_per_job]
        for index in range(0, len(selected), args.files_per_job)
    ]
    for group_index, source_inputs in enumerate(groups):
        first_index = args.first_file + group_index * args.files_per_job
        first_part = part_label(source_inputs[0], first_index)
        last_part = part_label(source_inputs[-1], first_index + len(source_inputs) - 1)
        part = first_part if len(source_inputs) == 1 else f"{first_part}to{last_part}"
        if not args.force and all(
            report_matches(
                args.output_dir / delay_name(delay) / f"events_shiftDelayScan_part{part}.json",
                args.output_dir / delay_name(delay) / f"events_shiftDelayScan_part{part}.root",
                delay,
                source_inputs,
            )
            for delay in delays
        ):
            for delay in delays:
                print(
                    f"files {part} delay {_decimal_text(delay)} ns: reused",
                    flush=True,
                )
            continue
        with tempfile.TemporaryDirectory(prefix=f"shift_delay_scan_{part}_") as temp_dir:
            staged_inputs = []
            for source_input in source_inputs:
                staged_input = Path(temp_dir) / source_input.name
                print(f"staging once: {source_input} -> {staged_input}", flush=True)
                shutil.copy2(source_input, staged_input)
                staged_inputs.append(staged_input)
            with ThreadPoolExecutor(max_workers=args.workers) as executor:
                futures = {
                    executor.submit(
                        run_point, delay, staged_inputs, source_inputs, part, args,
                        cmssw_dir, Path(temp_dir),
                    ): delay
                    for delay in delays
                }
                for future in as_completed(futures):
                    delay = futures[future]
                    try:
                        status = future.result()
                        print(f"files {part} delay {_decimal_text(delay)} ns: {status}", flush=True)
                    except Exception as error:
                        failures.append(
                            ([str(path) for path in source_inputs], _decimal_text(delay), str(error))
                        )
                        print(
                            f"files {part} delay {_decimal_text(delay)} ns: FAILED: {error}",
                            file=sys.stderr,
                            flush=True,
                        )
    summary = {
        "format": "shift-reco-delay-scan-v1",
        "delays_ns": [_decimal_text(delay) for delay in delays],
        "files": [str(path) for path in selected],
        "failures": failures,
        "pileup_mode": "none",
    }
    with (args.output_dir / "scan.json").open("w", encoding="utf-8") as summary_file:
        json.dump(summary, summary_file, indent=2, sort_keys=True)
        summary_file.write("\n")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
