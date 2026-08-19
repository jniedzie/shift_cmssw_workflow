#!/usr/bin/env python3

"""Convert standard L1uGTTree aliases into a bit-to-algorithm JSON mapping."""

import argparse
import json
import re
import sys

import ROOT


ALIAS_EXPRESSION = re.compile(r"m_algoDecisionInitial\[(\d+)\]")


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", help="ROOT file produced by zero_bias_l1_menu_cfg.py")
    parser.add_argument("-o", "--output", required=True)
    parser.add_argument("--global-tag", default="auto:run3_data_prompt")
    parser.add_argument("--source-raw", default="")
    return parser.parse_args()


def main():
    args = parse_args()
    source = ROOT.TFile.Open(args.input)
    if not source or source.IsZombie():
        print(f"ERROR: cannot open {args.input}", file=sys.stderr)
        return 1
    tree = source.Get("l1uGTTree/L1uGTTree")
    if not tree or tree.GetEntries() < 1:
        print("ERROR: l1uGTTree/L1uGTTree is missing or empty", file=sys.stderr)
        return 1

    algorithms = {}
    aliases = tree.GetListOfAliases()
    if aliases:
        for alias in aliases:
            expression = str(alias.GetTitle())
            match = ALIAS_EXPRESSION.search(expression)
            if not match:
                continue
            bit = int(match.group(1))
            name = str(alias.GetName())
            if str(bit) in algorithms and algorithms[str(bit)] != name:
                print(f"ERROR: bit {bit} maps to multiple algorithm names", file=sys.stderr)
                return 1
            algorithms[str(bit)] = name
    if not algorithms:
        print("ERROR: no L1 algorithm aliases were found", file=sys.stderr)
        return 1

    tree.GetEntry(0)
    block = tree.L1uGT
    result = {
        "schema": "shift-zero-bias-l1-menu",
        "schema_version": 1,
        "input": args.input,
        "source_raw": args.source_raw,
        "global_tag": args.global_tag,
        "menu_uuid": int(block.getL1MenuUUID()) & 0xFFFFFFFF,
        "firmware_uuid": int(block.getL1FirmwareUUID()) & 0xFFFFFFFF,
        "algorithm_count": len(algorithms),
        "algorithms": dict(sorted(algorithms.items(), key=lambda item: int(item[0]))),
    }
    with open(args.output, "w", encoding="utf-8") as output:
        json.dump(result, output, indent=2, sort_keys=True)
        output.write("\n")
    print(
        f"wrote {len(algorithms)} L1 algorithm names for menu UUID "
        f"{result['menu_uuid']:08x} to {args.output}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

