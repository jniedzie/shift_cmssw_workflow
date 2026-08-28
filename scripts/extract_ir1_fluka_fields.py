#!/usr/bin/env python3

"""Extract the frozen IR1 proxy's FLUKA magnetic-field assignments to JSON."""

import argparse
from pathlib import Path
import sys

from ir1_fluka_geometry import ProxyModelError, extract_and_write_field_manifest


REPOSITORY = Path(__file__).resolve().parents[1]


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model-dir",
        type=Path,
        default=REPOSITORY / "models" / "lss5_ir1_atlas_proxy",
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main():
    args = parse_args()
    try:
        assignments = extract_and_write_field_manifest(args.model_dir, args.output)
    except (OSError, ProxyModelError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"wrote {len(assignments)} FLUKA field assignments to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
