#!/usr/bin/env python3

"""Sample whole correlated ZeroBias records onto a reference-BX timeline."""

import argparse
import json
import os
from pathlib import Path
import sys

from zero_bias_trigger_library import (
    TriggerLibraryError,
    load_l1_menus,
    load_trigger_library,
    sample_loaded_events,
    validate_trigger_library,
)


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("inputs", nargs="+", help="validated trigger-bit JSONL files")
    parser.add_argument("-o", "--output", required=True)
    parser.add_argument("--group-id", help="exact group ID printed by the validator")
    parser.add_argument("--start-bx", type=int, required=True)
    parser.add_argument("--end-bx", type=int, required=True, help="inclusive final relative BX")
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument(
        "--signal-events",
        type=int,
        default=1,
        help="number of independent SHIFT-event timelines to sample (default: 1)",
    )
    parser.add_argument(
        "--colliding-bx-file",
        help="optional file containing one colliding relative BX per line; default is every BX",
    )
    parser.add_argument("--without-replacement", action="store_true")
    parser.add_argument("--l1-menu", help="matching bit-to-name JSON for timeline provenance")
    return parser.parse_args()


def load_colliding_bxs(path, timeline_bxs):
    if not path:
        return set(timeline_bxs)
    values = set()
    with open(path, encoding="utf-8") as source:
        for line_number, text in enumerate(source, start=1):
            value = text.partition("#")[0].strip()
            if not value:
                continue
            try:
                values.add(int(value))
            except ValueError as error:
                raise TriggerLibraryError(
                    f"{path}:{line_number}: colliding BX must be an integer"
                ) from error
    outside = values - set(timeline_bxs)
    if outside:
        raise TriggerLibraryError(f"colliding BX values outside requested timeline: {sorted(outside)}")
    return values


def main():
    args = parse_args()
    if args.end_bx < args.start_bx:
        print("ERROR: --end-bx must be greater than or equal to --start-bx", file=sys.stderr)
        return 2
    if args.signal_events < 1:
        print("ERROR: --signal-events must be positive", file=sys.stderr)
        return 2

    try:
        library = load_trigger_library(args.inputs)
        errors, warnings, groups = validate_trigger_library(library)
        l1_menus = load_l1_menus([args.l1_menu] if args.l1_menu else [])
        if errors:
            raise TriggerLibraryError("input validation failed:\n  " + "\n  ".join(errors))

        groups_by_id = {key.group_id: (key, events) for key, events in groups.items()}
        if args.group_id:
            if args.group_id not in groups_by_id:
                raise TriggerLibraryError(
                    f"unknown group ID {args.group_id!r}; choices: {sorted(groups_by_id)}"
                )
            key, group_events = groups_by_id[args.group_id]
        elif len(groups_by_id) == 1:
            key, group_events = next(iter(groups_by_id.values()))
        else:
            raise TriggerLibraryError(
                "input has multiple trigger groups; select one with --group-id: "
                + ", ".join(sorted(groups_by_id))
            )

        l1_menu = l1_menus.get((key.l1_menu_uuid, key.l1_firmware_uuid))
        if args.l1_menu and not l1_menu:
            raise TriggerLibraryError(
                f"supplied L1 menu does not match group UUIDs in {key.group_id}"
            )

        timeline_bxs = list(range(args.start_bx, args.end_bx + 1))
        colliding_bxs = load_colliding_bxs(args.colliding_bx_file, timeline_bxs)
        sampled = iter(
            sample_loaded_events(
                group_events,
                len(colliding_bxs) * args.signal_events,
                args.seed,
                args.without_replacement,
            )
        )
    except (OSError, TriggerLibraryError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_name(f"{output_path.name}.partial.{os.getpid()}")
    try:
        with temporary_path.open("w", encoding="utf-8") as output:
            output.write(
                json.dumps(
                    {
                    "record_type": "metadata",
                    "schema": "shift-zero-bias-trigger-timeline",
                    "schema_version": 1,
                    "input_files": args.inputs,
                    "trigger_group": key.as_dict(),
                    "seed": args.seed,
                    "signal_events": args.signal_events,
                    "start_bx": args.start_bx,
                    "end_bx": args.end_bx,
                    "colliding_bx_file": args.colliding_bx_file or "",
                    "sampling": (
                        "without_replacement" if args.without_replacement else "with_replacement"
                    ),
                    "deadtime_applied": False,
                    "l1_menu": (
                        {
                            "source_file": l1_menu["source_file"],
                            "global_tag": l1_menu.get("global_tag", ""),
                            "menu_uuid": l1_menu["menu_uuid"],
                            "firmware_uuid": l1_menu["firmware_uuid"],
                            "algorithms": {
                                str(bit): name for bit, name in sorted(l1_menu["algorithms"].items())
                            },
                        }
                        if l1_menu
                        else None
                    ),
                    "warnings": warnings,
                    },
                    sort_keys=True,
                )
                + "\n"
            )

            sample_index = 0
            for signal_event_index in range(args.signal_events):
                for timeline_bx in timeline_bxs:
                    record = {
                        "record_type": "timeline_bx",
                        "signal_event_index": signal_event_index,
                        "timeline_bx": timeline_bx,
                        "colliding": timeline_bx in colliding_bxs,
                        "sample_index": None,
                        "source": None,
                        "candidate_decisions": None,
                        "readout_after_trigger_rules": None,
                    }
                    if timeline_bx in colliding_bxs:
                        loaded = next(sampled)
                        event = loaded.record
                        record["sample_index"] = sample_index
                        record["source"] = {
                            "file": loaded.source_file,
                            "line": loaded.source_line,
                            "run": event["run"],
                            "lumi": event["lumi"],
                            "event": event["event"],
                            "orbit": event["orbit"],
                            "bx": event["bx"],
                        }
                        record["candidate_decisions"] = {
                            "source_event_was_read_out": True,
                            "l1_by_bx": event["l1_by_bx"],
                            "l1_external_by_bx": event["l1_external_by_bx"],
                            "hlt_menu_id": event["hlt_menu_id"],
                            "hlt_accepted": event["hlt_accepted"],
                            "hlt_errors": event["hlt_errors"],
                        }
                        sample_index += 1
                    output.write(json.dumps(record, sort_keys=True) + "\n")
        os.replace(temporary_path, output_path)
    except OSError as error:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print(
        f"wrote {args.signal_events} timelines with {len(timeline_bxs)} BX records each "
        f"({len(colliding_bxs)} colliding per timeline) "
        f"from group {key.group_id} to {args.output}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
