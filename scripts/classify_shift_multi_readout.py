#!/usr/bin/env python3
"""Combine paired one-readout SHIFT capture audits without changing readout rules."""

import argparse
import json


def _load(spec):
    label, separator, path = spec.partition("=")
    if not separator or not label or not path:
        raise ValueError(f"expected LABEL=JSON, got {spec!r}")
    with open(path, encoding="utf-8") as input_file:
        report = json.load(input_file)
    muons = {}
    for event in report["events"]:
        for muon in event["signal_muons"]:
            key = (event["run"], event["lumi"], event["event"], muon["event_id"]["raw"], muon["track_id"])
            muons[key] = muon
    return label, path, muons


def _channels(subsystem, field):
    return {tuple(channel) for channel in subsystem[field]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("readouts", nargs="+", help="paired one-readout JSON reports as LABEL=JSON")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    reports = [_load(spec) for spec in args.readouts]
    key_sets = [set(report[2]) for report in reports]
    if any(keys != key_sets[0] for keys in key_sets[1:]):
        raise RuntimeError("readouts do not contain identical signal-muon identities")

    simhit_mismatches = []
    reference_label, _, reference_muons = reports[0]
    for key in sorted(key_sets[0]):
        reference = reference_muons[key]
        for label, _, muons in reports[1:]:
            for subsystem in reference["subdetectors"]:
                expected = reference["subdetectors"][subsystem]["simhits"]
                observed = muons[key]["subdetectors"][subsystem]["simhits"]
                if expected != observed:
                    simhit_mismatches.append({
                        "event": key[2], "track_id": key[4], "subsystem": subsystem,
                        reference_label: expected, label: observed,
                    })
    if simhit_mismatches:
        output = {
            "schema_version": 1,
            "valid": False,
            "readouts": {label: path for label, path, _ in reports},
            "error": "SimHit populations differ; these are not the same detector-level muon realization.",
            "simhit_mismatches": simhit_mismatches,
        }
        with open(args.output, "w", encoding="utf-8") as output_file:
            json.dump(output, output_file, indent=2, sort_keys=True)
            output_file.write("\n")
        print(json.dumps({"valid": False, "simhit_mismatches": len(simhit_mismatches)}, sort_keys=True))
        raise SystemExit(2)

    results = []
    summary = {}
    for key in sorted(key_sets[0]):
        per_readout = {label: muons[key] for label, _, muons in reports}
        crossed = {
            subsystem
            for muon in per_readout.values()
            for subsystem, values in muon["subdetectors"].items()
            if values["simhits"]
        }
        if not crossed:
            classification = "no_muon_detector_crossing"
        elif any(muon["classification"] == "complete_at_digi_RAW_boundary" for muon in per_readout.values()):
            classification = "complete_in_one_tested_readout"
        else:
            union_complete = True
            for subsystem in crossed:
                expected = set()
                stored = set()
                for muon in per_readout.values():
                    values = muon["subdetectors"][subsystem]
                    expected |= _channels(values, "linked_channels")
                    stored |= _channels(values, "matched_channels")
                union_complete &= expected <= stored and bool(expected)
            if union_complete:
                classification = "complete_only_in_union_of_tested_readouts"
            elif any(
                values["matched_unpacked_digis"]
                for muon in per_readout.values()
                for values in muon["subdetectors"].values()
            ):
                classification = "partial_across_tested_readouts"
            else:
                classification = "no_muon_content_in_tested_readouts"

        summary[classification] = summary.get(classification, 0) + 1
        results.append(
            {
                "run": key[0],
                "lumi": key[1],
                "event": key[2],
                "event_id_raw": key[3],
                "track_id": key[4],
                "classification": classification,
                "per_readout": {label: muon["classification"] for label, muon in per_readout.items()},
            }
        )

    output = {
        "schema_version": 1,
        "valid": True,
        "readouts": {label: path for label, path, _ in reports},
        "definition": "Channel identity is subsystem digi kind, detector id, and channel; BX/TDC sample is omitted when taking the union.",
        "limitation": "The expected set is the union of truth-linked simulated digis produced at the tested timing points, not every SimHit.",
        "muons": results,
        "summary": dict(sorted(summary.items())),
    }
    with open(args.output, "w", encoding="utf-8") as output_file:
        json.dump(output, output_file, indent=2, sort_keys=True)
        output_file.write("\n")
    print(json.dumps(output["summary"], sort_keys=True))


if __name__ == "__main__":
    main()
