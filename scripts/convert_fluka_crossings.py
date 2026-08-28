#!/usr/bin/env python3

"""Convert a FLUKA CMS-interface crossing file to strict SHIFT JSONL."""

import argparse
from pathlib import Path
import sys

from lss5_crossing_records import CrossingFormatError, convert_to_jsonl


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="FLUKA text crossing file, optionally gzip-compressed")
    parser.add_argument(
        "--output", required=True, help="Output JSONL path; use a .gz suffix for compression"
    )
    parser.add_argument("--force", action="store_true", help="Replace an existing output file")
    parser.add_argument(
        "--model-label",
        default="unverified-FLUKA-software-fixture",
        help="Human-readable source-model label; output remains marked as a software fixture",
    )
    parser.add_argument(
        "--interface-label",
        default="unconfirmed-scoring-interface",
        help="Human-readable scoring interface label; no coordinate transform is inferred",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    output_path = Path(args.output)
    if output_path.exists() and not args.force:
        print(f"error: output already exists: {output_path}; pass --force to replace it", file=sys.stderr)
        return 1
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        scan = convert_to_jsonl(
            args.input, output_path, args.model_label, args.interface_label
        )
    except (CrossingFormatError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    print(
        f"wrote {scan.event_count} events and {scan.particle_count} particles "
        f"to {output_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
