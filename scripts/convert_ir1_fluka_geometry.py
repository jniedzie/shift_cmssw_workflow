#!/usr/bin/env python3

"""Convert the frozen IR1 proxy geometry to GDML and extract field metadata."""

import argparse
from pathlib import Path
import sys

from ir1_fluka_geometry import ProxyModelError, convert_geometry


REPOSITORY = Path(__file__).resolve().parents[1]


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model-dir",
        type=Path,
        default=REPOSITORY / "models" / "lss5_ir1_atlas_proxy",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--regions",
        help="Comma-separated FLUKA region names for a bounded conversion probe",
    )
    parser.add_argument(
        "--acknowledge-geometry-only",
        action="store_true",
        help="Acknowledge that GDML excludes FLUKA magnetic-field behavior",
    )
    parser.add_argument(
        "--use-lattice-aabb-workaround",
        action="store_true",
        help=(
            "Use FLUKA cell meshes for pyg4ometry's lattice overlap AABBs; "
            "the choice is recorded in the conversion report"
        ),
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if not args.acknowledge_geometry_only:
        print(
            "error: pass --acknowledge-geometry-only; GDML conversion does not implement bmagfld.f",
            file=sys.stderr,
        )
        return 2
    try:
        regions = [item.strip() for item in args.regions.split(",")] if args.regions else None
        report = convert_geometry(
            args.model_dir,
            args.output_dir,
            regions=regions,
            lattice_aabb_workaround=args.use_lattice_aabb_workaround,
        )
    except (OSError, ProxyModelError, RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(
        f"wrote {report['geometry']['gdml']} with "
        f"{report['geometry']['logical_volume_count']} logical volumes; "
        "magnetic fields remain a separate unimplemented CMSSW layer"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
