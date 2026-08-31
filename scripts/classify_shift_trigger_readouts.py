#!/usr/bin/env python3
"""Combine paired SHIFT trigger-funnel reports without changing trigger rules."""

import argparse
import json


def _load(spec):
    label, separator, path = spec.partition("=")
    if not separator or not label or not path:
        raise ValueError(f"expected LABEL=JSON, got {spec!r}")
    with open(path, encoding="utf-8") as input_file:
        report = json.load(input_file)
    if report.get("schema_version") != 1:
        raise RuntimeError(f"unsupported trigger-funnel schema in {path}")
    muons = {}
    events = {}
    for event in report["events"]:
        event_key = event["run"], event["lumi"], event["event"]
        events[event_key] = event["regional_candidate_bx_counts"]
        for muon in event["signal_muons"]:
            key = (*event_key, muon["event_id"]["raw"], muon["track_id"])
            muons[key] = muon
    return label, path, muons, events


def _spatial_lct(record):
    return tuple(
        record[field]
        for field in (
            "chamber_id", "track_number", "quality", "key_wire", "strip", "bend"
        )
    )


def _classification(expected, stored_by_readout):
    if not expected:
        return "no_chamber_compatible_simulated_LCT"
    if any(expected <= stored for stored in stored_by_readout.values()):
        return "complete_in_one_tested_readout"
    if expected <= set().union(*stored_by_readout.values()):
        return "complete_only_in_union_of_tested_readouts"
    if any(stored_by_readout.values()):
        return "partial_across_tested_readouts"
    return "no_LCT_content_in_tested_readouts"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("readouts", nargs="+", help="paired reports as LABEL=JSON")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    reports = [_load(spec) for spec in args.readouts]
    muon_keys = [set(report[2]) for report in reports]
    event_keys = [set(report[3]) for report in reports]
    if any(keys != muon_keys[0] for keys in muon_keys[1:]):
        raise RuntimeError("readouts do not contain identical signal-muon identities")
    if any(keys != event_keys[0] for keys in event_keys[1:]):
        raise RuntimeError("readouts do not contain identical event identities")

    muon_results = []
    summary = {}
    for key in sorted(muon_keys[0]):
        per_readout = {label: muons[key] for label, _, muons, _ in reports}
        crossed_csc = any(muon["CSC"]["crossed_chambers"] for muon in per_readout.values())
        expected = {
            _spatial_lct(item)
            for muon in per_readout.values()
            for item in muon["CSC"]["compatible_prepack_LCTs"]
        }
        stored_by_readout = {
            label: {_spatial_lct(item) for item in muon["CSC"]["matched_unpacked_LCTs"]}
            for label, muon in per_readout.items()
        }
        classification = (
            _classification(expected, stored_by_readout) if crossed_csc else "no_CSC_crossing"
        )
        summary[classification] = summary.get(classification, 0) + 1
        stored_union = set().union(*stored_by_readout.values())
        muon_results.append(
            {
                "run": key[0], "lumi": key[1], "event": key[2],
                "event_id_raw": key[3], "track_id": key[4],
                "classification": classification,
                "expected_spatial_LCTs": len(expected),
                "stored_spatial_LCTs_by_readout": {
                    label: len(items) for label, items in stored_by_readout.items()
                },
                "stored_spatial_LCTs_in_union": len(stored_union),
                "missing_spatial_LCTs_from_union": len(expected - stored_union),
                "payload_changes_by_readout": {
                    label: len(muon["CSC"]["payload_changes_after_unpack"])
                    for label, muon in per_readout.items()
                },
                "DT_status_by_readout": {
                    label: muon["DT"]["status"] for label, muon in per_readout.items()
                },
            }
        )

    output = {
        "schema_version": 1,
        "valid": True,
        "readouts": {label: path for label, path, _, _ in reports},
        "definition": (
            "CSC LCT spatial identity is chamber, track slot, quality, key wire, strip, and bend; "
            "readout-relative BX and format-dependent pattern fields are omitted only for the multi-readout union."
        ),
        "limitations": [
            "Signal association is chamber-compatible, not an exact SimTrack association.",
            "The union describes the tested triggered readouts, not an implemented multi-event DAQ decision.",
            "Regional and uGMT candidates remain event-global and are not assigned to a signal muon.",
        ],
        "muons": muon_results,
        "events": [
            {
                "run": key[0], "lumi": key[1], "event": key[2],
                "regional_candidate_bx_counts_by_readout": {
                    label: events[key] for label, _, _, events in reports
                },
            }
            for key in sorted(event_keys[0])
        ],
        "summary": dict(sorted(summary.items())),
    }
    with open(args.output, "w", encoding="utf-8") as output_file:
        json.dump(output, output_file, indent=2, sort_keys=True)
        output_file.write("\n")
    print(json.dumps(output["summary"], sort_keys=True))


if __name__ == "__main__":
    main()
