#!/usr/bin/env python3
"""Merge one delay point while preserving its Step-1 provenance."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


def load_inputs(delay_dir):
    reports = sorted(delay_dir.glob("events_shiftDelayScan_part*.json"))
    if not reports:
        raise RuntimeError(f"no delay-scan reports found in {delay_dir}")

    roots = []
    sources = []
    reference = None
    for report_path in reports:
        with report_path.open(encoding="utf-8") as report_file:
            report = json.load(report_file)
        if report.get("status") != "complete":
            raise RuntimeError(f"incomplete report: {report_path}")
        fields = {
            key: report.get(key)
            for key in ("format", "delay_ns", "bx_offset", "phase_ns", "pileup_mode")
        }
        if fields["format"] != "shift-reco-delay-scan-v1" or fields["pileup_mode"] != "none":
            raise RuntimeError(f"unsupported report provenance: {report_path}")
        if reference is None:
            reference = fields
        elif fields != reference:
            raise RuntimeError(f"inconsistent report provenance: {report_path}")

        root_path = Path(report["output"])
        if not root_path.is_file() or root_path.stat().st_size != report.get("output_bytes"):
            raise RuntimeError(f"missing or size-mismatched ROOT output: {root_path}")
        report_sources = report.get("source_step1")
        if not isinstance(report_sources, list) or not report_sources:
            raise RuntimeError(f"missing Step-1 provenance: {report_path}")
        roots.append(root_path)
        sources.extend(report_sources)

    if len(sources) != len(set(sources)):
        raise RuntimeError(f"duplicate Step-1 source in {delay_dir}")
    return roots, sorted(sources), reference


def rewrite_tag(path, provenance):
    import ROOT

    ROOT.gROOT.SetBatch(True)
    output = ROOT.TFile.Open(str(path), "UPDATE")
    if not output or output.IsZombie():
        raise RuntimeError(f"cannot reopen merged ROOT file: {path}")
    output.Delete("tag;*")
    tag = ROOT.TObjString(json.dumps(provenance, sort_keys=True, separators=(",", ":")))
    if tag.Write("tag") <= 0:
        raise RuntimeError(f"could not write merged provenance to {path}")
    tree = output.Get("Events")
    entries = int(tree.GetEntries()) if tree else -1
    output.Close()
    if entries < 1:
        raise RuntimeError(f"merged ROOT file has no Events entries: {path}")
    return entries


def event_count(path):
    """Open one input with ROOT so unreadable files fail before publication."""
    import ROOT

    ROOT.gROOT.SetBatch(True)
    source = ROOT.TFile.Open(str(path), "READ")
    if not source or source.IsZombie():
        raise RuntimeError(f"cannot open input ROOT file: {path}")
    tree = source.Get("Events")
    entries = int(tree.GetEntries()) if tree else -1
    source.Close()
    if entries < 0:
        raise RuntimeError(f"input ROOT file has no Events tree: {path}")
    return entries


def write_json_atomic(path, payload):
    partial = path.parent / f".{path.name}.{os.getpid()}.partial"
    try:
        with partial.open("w", encoding="utf-8") as report_file:
            json.dump(payload, report_file, indent=2, sort_keys=True)
            report_file.write("\n")
        os.replace(partial, path)
    finally:
        if partial.exists():
            partial.unlink()


def merge_delay(delay_dir, output_root, force=False):
    roots, sources, fields = load_inputs(delay_dir)
    output_dir = output_root / delay_dir.name
    output_path = output_dir / "events_shiftDelayScan_merged.root"
    report_path = output_dir / "events_shiftDelayScan_merged.json"
    if output_path.exists() or report_path.exists():
        if not force:
            raise RuntimeError(f"merged output already exists; use --force: {output_path}")

    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f"shift_delay_merge_{delay_dir.name}_") as temporary:
        temporary = Path(temporary)
        staged = []
        expected_entries = 0
        for index, source in enumerate(roots):
            destination = temporary / f"input_{index:04d}.root"
            shutil.copy2(source, destination)
            expected_entries += event_count(destination)
            staged.append(destination)
        local_output = temporary / output_path.name
        completed = subprocess.run(
            ["hadd", "-f", str(local_output), *map(str, staged)],
            text=True,
            check=False,
        )
        if completed.returncode:
            raise RuntimeError(f"hadd failed with status {completed.returncode}")
        provenance = {**fields, "source_step1": sources}
        entries = rewrite_tag(local_output, provenance)
        if entries != expected_entries:
            raise RuntimeError(
                f"merged event-count mismatch: got {entries}, expected {expected_entries}"
            )
        partial = output_dir / f".{output_path.name}.{os.getpid()}.partial"
        try:
            shutil.copy2(local_output, partial)
            if partial.stat().st_size != local_output.stat().st_size:
                raise RuntimeError(f"incomplete merged-file staging: {partial}")
            os.replace(partial, output_path)
        finally:
            if partial.exists():
                partial.unlink()

    report = {
        "status": "complete",
        "format": "shift-reco-delay-scan-merge-v1",
        "delay_ns": fields["delay_ns"],
        "bx_offset": fields["bx_offset"],
        "phase_ns": fields["phase_ns"],
        "pileup_mode": fields["pileup_mode"],
        "source_step1": sources,
        "input_files": [str(path) for path in roots],
        "input_file_count": len(roots),
        "output": str(output_path),
        "output_bytes": output_path.stat().st_size,
        "events": entries,
        "input_events": expected_entries,
    }
    write_json_atomic(report_path, report)
    print(f"merged {len(roots)} files with {entries} events into {output_path}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scan_dir", type=Path)
    parser.add_argument("delay_name", help="delay directory name, for example delay_m25ns")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    scan_dir = args.scan_dir.resolve()
    delay_dir = scan_dir / args.delay_name
    if not delay_dir.is_dir() or not args.delay_name.startswith("delay_"):
        parser.error(f"invalid delay directory: {delay_dir}")
    output_root = (args.output_dir or (scan_dir / "merged")).resolve()
    merge_delay(delay_dir, output_root, args.force)


if __name__ == "__main__":
    main()
