#!/usr/bin/env python3
"""Validate a measured ordered L1A/HLT export for SHIFT convolution.

The input CSV is deliberately an export adapter, not a trigger simulation. It
must contain signal_event_id,parent_slot,relative_bx,l1a_recorded,hlt_persisted,
run,lumi and be ordered by signal event then relative BX.
"""

import argparse
import csv
import json
import os
from pathlib import Path
import sys


class TimelineError(ValueError):
    pass


def boolean(value, field):
    values = {"0": False, "1": True, "false": False, "true": True, "False": False, "True": True}
    if value not in values:
        raise TimelineError(f"{field} must be one of 0,1,false,true")
    return values[value]


def load_rows(path):
    required = ("signal_event_id", "parent_slot", "relative_bx", "l1a_recorded", "hlt_persisted", "run", "lumi")
    try:
        with Path(path).open(newline="", encoding="utf-8") as source:
            reader = csv.DictReader(source)
            if reader.fieldnames is None or set(required) - set(reader.fieldnames):
                raise TimelineError("CSV lacks required opportunity columns")
            rows, last_event, last_bx, parent_slots = [], None, None, {}
            for line, row in enumerate(reader, 2):
                identifier = row["signal_event_id"]
                try:
                    slot, bx, run, lumi = (int(row[name]) for name in ("parent_slot", "relative_bx", "run", "lumi"))
                except (TypeError, ValueError) as error:
                    raise TimelineError(f"{path}:{line}: invalid integer field") from error
                if not identifier or not 1 <= slot <= 3564 or run <= 0 or lumi <= 0:
                    raise TimelineError(f"{path}:{line}: invalid event, slot, run, or lumi")
                if identifier != last_event:
                    if identifier in parent_slots:
                        raise TimelineError(f"{path}:{line}: signal event is not contiguous")
                    last_event, last_bx = identifier, None
                    parent_slots[identifier] = slot
                elif slot != parent_slots[identifier] or bx <= last_bx:
                    raise TimelineError(f"{path}:{line}: non-increasing BX or changed parent slot")
                recorded = boolean(row["l1a_recorded"], f"{path}:{line}: l1a_recorded")
                persisted = boolean(row["hlt_persisted"], f"{path}:{line}: hlt_persisted")
                if persisted and not recorded:
                    raise TimelineError(f"{path}:{line}: HLT persistence without recorded L1A")
                rows.append({"signal_event_id": identifier, "parent_slot": slot, "relative_bx": bx,
                             "l1a_recorded": recorded, "hlt_persisted": persisted, "run": run, "lumi": lumi})
                last_bx = bx
    except OSError as error:
        raise TimelineError(f"cannot read {path}: {error}") from error
    if not rows:
        raise TimelineError("CSV has no opportunity records")
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-csv", required=True)
    parser.add_argument("--source", required=True, help="scaler/TCDS/OMS export reference")
    parser.add_argument("--run-period", required=True)
    parser.add_argument("--physics-valid", action="store_true", help="assert complete menu, prescale, deadtime, and live-state coverage")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        rows = load_rows(args.input_csv)
        metadata = {"schema": "shift-l1a-opportunity-timeline", "schema_version": 1,
                    "physics_valid": args.physics_valid,
                    "provenance": {"source": args.source, "run_period": args.run_period,
                                   "input_csv": str(Path(args.input_csv).resolve()),
                                   "ordered": "signal_event_id then increasing relative_bx"}}
        destination = Path(args.output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{os.getpid()}.partial")
        with temporary.open("w", encoding="utf-8") as output:
            output.write(json.dumps(metadata, sort_keys=True) + "\n")
            for row in rows:
                output.write(json.dumps(row, sort_keys=True) + "\n")
        os.replace(temporary, destination)
    except TimelineError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"wrote {len(rows)} measured L1A/HLT opportunities")
    return 0


if __name__ == "__main__":
    sys.exit(main())
