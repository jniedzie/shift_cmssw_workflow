#!/usr/bin/env python3
"""Build validated SHIFT parent-bunch weights from measured external inputs.

The CSV must contain one row for every selected filled SHIFT-beam slot with
``slot,bunch_population,source_weight``.  The result does not alter the source
model: it records and normalizes the supplied measured/externally-calculated
weights for the collection convolution.
"""

import argparse
import csv
import hashlib
import json
import os
from decimal import Decimal, InvalidOperation
from pathlib import Path
import sys


class DistributionError(ValueError):
    pass


def number(value, field):
    try:
        result = Decimal(value)
    except (InvalidOperation, TypeError) as error:
        raise DistributionError(f"{field} is not numeric") from error
    if not result.is_finite() or result <= 0:
        raise DistributionError(f"{field} must be finite and positive")
    return result


def load_mask(path, beam):
    raw = Path(path).read_bytes()
    try:
        mask = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DistributionError(f"{path} is not JSON") from error
    if (mask.get("schema"), mask.get("schema_version"), mask.get("orbit_slots")) != (
        "cms-lpc-ip5-bunch-mask", 1, 3564
    ):
        raise DistributionError(f"{path} is not a supported LPC bunch mask")
    slots = mask.get(f"beam{beam}_filled_bx_slots")
    if not isinstance(slots, list) or not slots or any(type(slot) is not int for slot in slots):
        raise DistributionError(f"{path} has invalid Beam-{beam} filled slots")
    return mask, hashlib.sha256(raw).hexdigest(), set(slots)


def load_rows(path, allowed_slots):
    try:
        with Path(path).open(newline="", encoding="utf-8") as source:
            reader = csv.DictReader(source)
            if reader.fieldnames is None or set(("slot", "bunch_population", "source_weight")) - set(reader.fieldnames):
                raise DistributionError("weight CSV needs slot,bunch_population,source_weight columns")
            rows = {}
            for line, row in enumerate(reader, 2):
                try:
                    slot = int(row["slot"])
                except (TypeError, ValueError) as error:
                    raise DistributionError(f"{path}:{line}: invalid slot") from error
                if slot not in allowed_slots or slot in rows:
                    raise DistributionError(f"{path}:{line}: slot is unfilled or duplicated")
                population = number(row["bunch_population"], f"{path}:{line}: bunch_population")
                source_weight = number(row["source_weight"], f"{path}:{line}: source_weight")
                rows[slot] = (population, source_weight)
    except OSError as error:
        raise DistributionError(f"cannot read {path}: {error}") from error
    if set(rows) != allowed_slots:
        missing = sorted(allowed_slots - set(rows))
        raise DistributionError(f"weight CSV omits filled SHIFT-beam slots: {missing[:10]}")
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bunch-mask", required=True)
    parser.add_argument("--weights-csv", required=True)
    parser.add_argument("--shift-beam", choices=(1, 2), required=True, type=int)
    parser.add_argument("--bunch-population-source", required=True)
    parser.add_argument("--source-weight-source", required=True)
    parser.add_argument("--physics-valid", action="store_true", help="assert that both supplied sources are authoritative")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        mask, mask_hash, filled_slots = load_mask(args.bunch_mask, args.shift_beam)
        rows = load_rows(args.weights_csv, filled_slots)
        raw_weights = {slot: population * source_weight for slot, (population, source_weight) in rows.items()}
        total = sum(raw_weights.values())
        output = {
            "schema": "shift-parent-bunch-distribution", "schema_version": 1,
            "physics_valid": args.physics_valid,
            "fill_number": mask["fill_number"], "shift_beam": args.shift_beam,
            "provenance": {
                "source": "measured bunch populations times external SHIFT source weights",
                "bunch_mask": {"path": args.bunch_mask, "sha256": mask_hash},
                "bunch_population_source": args.bunch_population_source,
                "source_weight_source": args.source_weight_source,
                "weight_definition": "bunch_population * source_weight, normalized over filled SHIFT-beam slots",
            },
            "slots": [
                {"slot": slot, "weight": float(raw_weights[slot] / total),
                 "bunch_population": float(rows[slot][0]), "source_weight": float(rows[slot][1])}
                for slot in sorted(rows)
            ],
        }
        destination = Path(args.output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{os.getpid()}.partial")
        temporary.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, destination)
    except (OSError, KeyError, DistributionError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"wrote {len(output['slots'])} normalized parent-slot weights for fill {output['fill_number']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
