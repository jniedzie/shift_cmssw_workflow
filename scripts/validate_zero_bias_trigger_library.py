#!/usr/bin/env python3

"""Validate and summarize one or more SHIFT ZeroBias trigger JSONL files."""

import argparse
import json
import sys

from zero_bias_trigger_library import (
    TriggerLibraryError,
    load_l1_menus,
    load_trigger_library,
    summarize_group,
    validate_trigger_library,
)


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("inputs", nargs="+", help="trigger-bit JSONL files")
    parser.add_argument("-o", "--output", help="optional machine-readable summary JSON")
    parser.add_argument("--min-events-per-group", type=int, default=1000)
    parser.add_argument("--top-pairs", type=int, default=25)
    parser.add_argument(
        "--l1-menu",
        action="append",
        default=[],
        help="bit-to-name JSON from extract_zero_bias_l1_menu.py; may be repeated",
    )
    parser.add_argument("--strict", action="store_true", help="make warnings fatal")
    return parser.parse_args()


def main():
    args = parse_args()
    try:
        library = load_trigger_library(args.inputs)
        errors, warnings, groups = validate_trigger_library(library)
        l1_menus = load_l1_menus(args.l1_menu)
    except (OSError, TriggerLibraryError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    for key, events in groups.items():
        if len(events) < args.min_events_per_group:
            warnings.append(
                f"group {key.group_id} has only {len(events)} events; "
                f"minimum requested is {args.min_events_per_group}"
            )
        if args.l1_menu and (key.l1_menu_uuid, key.l1_firmware_uuid) not in l1_menus:
            errors.append(f"group {key.group_id} has no matching supplied L1 menu mapping")

    summary = {
        "schema": "shift-zero-bias-trigger-library-summary",
        "schema_version": 1,
        "input_files": args.inputs,
        "event_count": len(library.events),
        "menu_count": len(library.menus),
        "group_count": len(groups),
        "errors": errors,
        "warnings": warnings,
        "groups": [
            summarize_group(
                key,
                groups[key],
                library.menus,
                args.top_pairs,
                l1_menus.get((key.l1_menu_uuid, key.l1_firmware_uuid)),
            )
            for key in sorted(groups)
        ],
    }

    if args.output:
        with open(args.output, "w", encoding="utf-8") as output:
            json.dump(summary, output, indent=2, sort_keys=True)
            output.write("\n")

    print(
        f"events={summary['event_count']} menus={summary['menu_count']} "
        f"groups={summary['group_count']} errors={len(errors)} warnings={len(warnings)}"
    )
    for group in summary["groups"]:
        print(
            f"  {group['group_id']}: events={group['event_count']} "
            f"runs={group['runs']} final_or={group['final_or_count']}"
        )
    for warning in warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    return int(bool(errors) or (args.strict and bool(warnings)))


if __name__ == "__main__":
    sys.exit(main())
