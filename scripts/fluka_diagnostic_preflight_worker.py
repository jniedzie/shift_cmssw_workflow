#!/usr/bin/env python3
"""Independent pycsg raw-region audit with the same scoped numerical guards."""

from contextlib import redirect_stderr, redirect_stdout
import argparse
import hashlib
import json
from pathlib import Path
import sys
import traceback

from fluka_region_preflight_worker import bootstrap_pyg4ometry_pycsg
from convert_ir1_fluka_geometry_full import parse_world_dimensions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--normalized-deck", type=Path, required=True)
    parser.add_argument("--regions-json", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=float, required=True)
    parser.add_argument("--world-dimensions-mm", type=parse_world_dimensions, required=True)
    args = parser.parse_args()
    result = {"backend": None, "production_ready": False}
    try:
        package = bootstrap_pyg4ometry_pycsg()
        result["backend"] = package.config.backendName()
        from pyg4ometry.fluka import Reader
        from convert_fluka_geometry_diagnostic import normalized_orthogonality_guard
        from convert_ir1_fluka_geometry_full import full_conversion_guards
        from fluka_region_preflight import classify_raw_regions
        from fluka_pycsg_compatibility import pycsg_compatibility_guard
        from fluka_halfspace_bounds import halfspace_bounds_guard

        regions = json.loads(args.regions_json.read_text())
        if not isinstance(regions, list) or not all(isinstance(name, str) for name in regions):
            raise ValueError("regions must be a list of strings")
        ledger = result["orthogonality_roundoff_acceptances"] = []
        result["inputs"] = {
            name: {"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            for name, path in (("normalized_deck", args.normalized_deck),
                               ("regions", args.regions_json),
                               ("worker", Path(__file__)),
                               ("halfspace_bounds", Path(__file__).with_name("fluka_halfspace_bounds.py")),
                               ("analytic_bounds", Path(__file__).with_name("fluka_analytic_bounds.py")),
                               ("pycsg_compatibility", Path(__file__).with_name("fluka_pycsg_compatibility.py")))
        }
        result["argv"] = sys.argv
        result["python_version"] = sys.version
        with normalized_orthogonality_guard(ledger), full_conversion_guards(args.world_dimensions_mm), pycsg_compatibility_guard() as compatibility, halfspace_bounds_guard(result.setdefault("halfspace_bounds", {})):
            result["pycsg_compatibility"] = compatibility
            with args.output.with_suffix(".reader.log").open("w") as log, redirect_stdout(log), redirect_stderr(log):
                registry = Reader(str(args.normalized_deck)).flukaregistry
            result.update(classify_raw_regions(registry, regions,
                          timeout_seconds=args.timeout_seconds, include_bounds=True))
    except Exception as error:
        result["error"] = {"type": type(error).__name__, "message": str(error),
                           "traceback": traceback.format_exc()}
        print(result["error"]["traceback"], file=sys.stderr)
    finally:
        temporary = args.output.with_suffix(args.output.suffix + ".tmp")
        temporary.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        temporary.replace(args.output)
    return 1 if "error" in result else 0


if __name__ == "__main__":
    raise SystemExit(main())
