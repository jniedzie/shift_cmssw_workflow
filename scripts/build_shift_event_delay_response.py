#!/usr/bin/env python3
"""Build the event-level delay-response input from paired reconstruction results.

The CSV records one signal event at every scanned additional delay. It is an
adapter only: detector and reconstruction decisions must already come from the
unchanged full CMSSW chain.
"""

import argparse
import csv
import json
import os
from decimal import Decimal, InvalidOperation
from pathlib import Path
import sys


class ResponseError(ValueError):
    pass


def decimal(value, field):
    try:
        result = Decimal(value)
    except (InvalidOperation, TypeError) as error:
        raise ResponseError(f"{field} is not numeric") from error
    if not result.is_finite():
        raise ResponseError(f"{field} must be finite")
    return result


def boolean(value, field):
    table = {"0": False, "1": True, "false": False, "true": True, "False": False, "True": True}
    if value not in table:
        raise ResponseError(f"{field} must be one of 0,1,false,true")
    return table[value]


def load_rows(path):
    required = ("signal_event_id", "physical_delay_ns", "additional_delay_ns", "readout", "reconstructed_muon", "reconstructed_dimuon", "classification")
    events = {}
    try:
        with Path(path).open(newline="", encoding="utf-8") as source:
            reader = csv.DictReader(source)
            if reader.fieldnames is None or set(required) - set(reader.fieldnames):
                raise ResponseError("CSV lacks required response columns")
            for line, row in enumerate(reader, 2):
                identifier = row["signal_event_id"]
                physical = decimal(row["physical_delay_ns"], f"{path}:{line}: physical_delay_ns")
                additional = decimal(row["additional_delay_ns"], f"{path}:{line}: additional_delay_ns")
                if not identifier or not row["classification"]:
                    raise ResponseError(f"{path}:{line}: empty event ID or classification")
                event = events.setdefault(identifier, {"physical_delay_ns": physical, "responses": {}})
                if event["physical_delay_ns"] != physical or additional in event["responses"]:
                    raise ResponseError(f"{path}:{line}: inconsistent physical delay or duplicate point")
                readout = boolean(row["readout"], f"{path}:{line}: readout")
                reco_muon = boolean(row["reconstructed_muon"], f"{path}:{line}: reconstructed_muon")
                reco_dimuon = boolean(row["reconstructed_dimuon"], f"{path}:{line}: reconstructed_dimuon")
                if (reco_muon or reco_dimuon) and not readout:
                    raise ResponseError(f"{path}:{line}: reconstruction without readout")
                event["responses"][additional] = {"additional_delay_ns": float(additional), "readout": readout,
                                                    "reconstructed_muon": reco_muon, "reconstructed_dimuon": reco_dimuon,
                                                    "classification": row["classification"]}
    except OSError as error:
        raise ResponseError(f"cannot read {path}: {error}") from error
    if not events:
        raise ResponseError("CSV has no response records")
    return events


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-csv", required=True)
    parser.add_argument("--source", required=True, help="full-chain campaign/artifact reference")
    parser.add_argument("--model-version", required=True)
    parser.add_argument("--physics-valid", action="store_true", help="assert paired full-chain validation")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        events = load_rows(args.input_csv)
        output = {"schema": "shift-event-delay-response", "schema_version": 1,
                  "physics_valid": args.physics_valid, "bunch_spacing_ns": 25,
                  "provenance": {"source": args.source, "input_csv": str(Path(args.input_csv).resolve()),
                                 "model_version": args.model_version,
                                 "definition": "unchanged full-chain event decision at each additional delay"},
                  "events": [{"signal_event_id": identifier, "physical_delay_ns": float(event["physical_delay_ns"]),
                              "responses": [event["responses"][delay] for delay in sorted(event["responses"])]}
                             for identifier, event in sorted(events.items())]}
        destination = Path(args.output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{os.getpid()}.partial")
        temporary.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, destination)
    except ResponseError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"wrote {len(output['events'])} event-level delay responses")
    return 0


if __name__ == "__main__":
    sys.exit(main())
