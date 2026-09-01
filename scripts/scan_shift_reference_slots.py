#!/usr/bin/env python3
"""Group filled SHIFT reference slots by their nearby IP5 collision pattern."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

from sample_zero_bias_trigger_timeline import (
    TriggerLibraryError,
    load_ip5_bunch_mask,
)


SCHEMA = "shift-reference-slot-scan"
SCHEMA_VERSION = 1


def scan_reference_slots(mask_path, beam, start_bx, end_bx):
    if end_bx < start_bx:
        raise TriggerLibraryError("end BX precedes start BX")
    path = Path(mask_path)
    payload_bytes = path.read_bytes()
    try:
        mask = json.loads(payload_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TriggerLibraryError(f"{mask_path} is not valid JSON") from error
    slots_field = f"beam{beam}_filled_bx_slots"
    reference_slots = mask.get(slots_field)
    if not isinstance(reference_slots, list) or not reference_slots:
        raise TriggerLibraryError(f"{mask_path} has no {slots_field}")

    timeline_bxs = list(range(start_bx, end_bx + 1))
    slot_records = []
    pattern_slots = {}
    provenance = None
    for slot in reference_slots:
        relative, slot_provenance = load_ip5_bunch_mask(
            str(path), timeline_bxs, slot, beam
        )
        if provenance is None:
            provenance = slot_provenance
        pattern = tuple(sorted(relative))
        pattern_slots.setdefault(pattern, []).append(slot)
        slot_records.append(
            {
                "reference_bx_slot": slot,
                "colliding_relative_bxs": list(pattern),
                "collision_opportunities": len(pattern),
            }
        )

    groups = []
    for pattern, slots in sorted(
        pattern_slots.items(), key=lambda item: (len(item[0]), item[0])
    ):
        groups.append(
            {
                "colliding_relative_bxs": list(pattern),
                "collision_opportunities": len(pattern),
                "reference_slot_count": len(slots),
                "reference_slots": slots,
                "uniform_filled_slot_fraction": len(slots) / len(reference_slots),
            }
        )

    return {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "fill_number": provenance["fill_number"],
        "scheme_name": provenance["scheme_name"],
        "shift_beam": beam,
        "relative_bx_range": {"start": start_bx, "end": end_bx},
        "reference_slot_count": len(reference_slots),
        "pattern_group_count": len(groups),
        "collision_opportunity_distribution": {
            str(count): sum(
                1 for record in slot_records
                if record["collision_opportunities"] == count
            )
            for count in sorted(
                {record["collision_opportunities"] for record in slot_records}
            )
        },
        "weighting": {
            "status": "uniform_filled_slots_only",
            "physics_valid": False,
            "reason": "authoritative per-bunch intensities are not present in the LPC mask",
        },
        "source": {
            "mask_file": str(path),
            "mask_file_sha256": hashlib.sha256(payload_bytes).hexdigest(),
            "mask_csv_sha256": mask["source"]["csv_sha256"],
        },
        "pattern_groups": groups,
        "reference_slots": slot_records,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mask")
    parser.add_argument("--beam", type=int, choices=(1, 2), required=True)
    parser.add_argument("--start-bx", type=int, default=-24)
    parser.add_argument("--end-bx", type=int, default=5)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        result = scan_reference_slots(
            args.mask, args.beam, args.start_bx, args.end_bx
        )
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(f"{output.name}.partial.{os.getpid()}")
        with temporary.open("w", encoding="utf-8") as destination:
            json.dump(result, destination, indent=2, sort_keys=True)
            destination.write("\n")
        os.replace(temporary, output)
    except (OSError, KeyError, TriggerLibraryError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        f"scanned {result['reference_slot_count']} filled Beam-{args.beam} slots "
        f"into {result['pattern_group_count']} nearby-collision patterns; "
        "physical intensity weighting remains unavailable"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
