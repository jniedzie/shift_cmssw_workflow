#!/usr/bin/env python3
"""Print CMSSW event ranges selected by a piggyback decision report."""

import argparse
import json
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("decisions")
    parser.add_argument("--level", choices=("raw", "persisted"), required=True)
    args = parser.parse_args()
    try:
        with open(args.decisions, encoding="utf-8") as source:
            payload = json.load(source)
        if (
            payload.get("schema") != "shift-piggyback-central-readout"
            or payload.get("schema_version") != 1
            or payload.get("scenario") != "piggyback_central"
            or payload.get("signal_contributed_to_trigger_decision") is not False
        ):
            raise ValueError("unsupported or unsafe piggyback decision report")
        field = (
            "accepted_after_trigger_rules"
            if args.level == "raw"
            else "persisted_by_ordinary_hlt_proxy"
        )
        ranges = []
        for decision in payload.get("decisions", []):
            if not decision.get(field):
                continue
            event_id = decision["event_id"]
            ranges.append(
                f"{int(event_id['run'])}:{int(event_id['lumi'])}:{int(event_id['event'])}"
            )
        print(json.dumps(ranges, separators=(",", ":")))
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
