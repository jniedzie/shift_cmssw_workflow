#!/usr/bin/env python3

"""Evaluate selected raw FLUKA regions with pyg4ometry's pycsg backend."""

import argparse
from contextlib import redirect_stdout
import importlib.util
import json
from pathlib import Path
import sys


def bootstrap_pyg4ometry_pycsg():
    """Load pyg4ometry with pycsg selected before backend modules import."""

    package_spec = importlib.util.find_spec("pyg4ometry")
    if package_spec is None or package_spec.submodule_search_locations is None:
        raise RuntimeError("pyg4ometry package is not available")
    package = importlib.util.module_from_spec(package_spec)
    sys.modules["pyg4ometry"] = package

    package_directory = Path(next(iter(package_spec.submodule_search_locations)))
    config_spec = importlib.util.spec_from_file_location(
        "pyg4ometry.config", package_directory / "config.py"
    )
    if config_spec is None or config_spec.loader is None:
        raise RuntimeError("could not load pyg4ometry.config")
    config = importlib.util.module_from_spec(config_spec)
    sys.modules["pyg4ometry.config"] = config
    config_spec.loader.exec_module(config)
    config.meshing = config.meshingType.pycsg
    package.config = config
    package_spec.loader.exec_module(package)
    if package.config.backendName() != "pycsg":
        raise RuntimeError("pyg4ometry pycsg bootstrap did not take effect")
    return package


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--normalized-deck", type=Path, required=True)
    parser.add_argument("--regions-json", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    return parser.parse_args()


def write_json_atomic(path, payload):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main():
    args = parse_args()
    if args.timeout_seconds <= 0.0:
        print("error: --timeout-seconds must be positive", file=sys.stderr)
        return 2
    try:
        package = bootstrap_pyg4ometry_pycsg()

        from pyg4ometry.fluka import Reader

        from convert_ir1_fluka_geometry_full import (
            install_transformed_infinite_cylinder_centre_workaround,
            restore_transformed_infinite_cylinder_centres,
        )
        from fluka_region_preflight import classify_raw_regions

        region_names = json.loads(args.regions_json.read_text(encoding="utf-8"))
        if not isinstance(region_names, list) or not all(
            isinstance(name, str) for name in region_names
        ):
            raise ValueError("--regions-json must contain a JSON string list")
        with args.output.with_suffix(".reader.log").open(
            "w", encoding="utf-8"
        ) as log, redirect_stdout(log):
            registry = Reader(str(args.normalized_deck)).flukaregistry
        originals = install_transformed_infinite_cylinder_centre_workaround()
        try:
            result = classify_raw_regions(
                registry,
                region_names,
                timeout_seconds=args.timeout_seconds,
                include_bounds=True,
            )
        finally:
            restore_transformed_infinite_cylinder_centres(originals)
        result["backend"] = package.config.backendName()
        write_json_atomic(args.output, result)
    except Exception as error:
        print(f"error: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
