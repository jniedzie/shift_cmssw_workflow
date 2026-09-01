#!/usr/bin/env python3

"""Audit converted GDML region coverage and placed bounds against FLUKA CSG."""

import argparse
from contextlib import redirect_stdout
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from convert_ir1_fluka_geometry_full import (
    install_transformed_infinite_cylinder_centre_workaround,
    restore_transformed_infinite_cylinder_centres,
)
from fluka_region_preflight import isolated_region_bounds
from ir1_fluka_geometry import ProxyModelError, normalized_deck, verify_source_bundle


REPOSITORY = Path(__file__).resolve().parents[1]
BOUND_PREFIX = "SHIFT_REGION_BOUND\t"


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model-dir",
        type=Path,
        default=REPOSITORY / "models" / "lss5_ir1_atlas_proxy",
    )
    parser.add_argument("--gdml", type=Path, required=True)
    parser.add_argument("--conversion-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--root-command", default="root")
    parser.add_argument("--containment-tolerance-mm", type=float, default=0.01)
    parser.add_argument("--progress-every", type=int, default=100)
    parser.add_argument("--region-timeout-seconds", type=float, default=300.0)
    return parser.parse_args()


def root_bounds_expression(gdml_path):
    quoted_path = json.dumps(str(Path(gdml_path).resolve()))
    return f'''TGeoManager::Import({quoted_path});
if (!gGeoManager) gSystem->Exit(2);
auto *top = gGeoManager->GetTopVolume();
for (int index = 0; index < top->GetNdaughters(); ++index) {{
  auto *node = top->GetNode(index);
  auto *shape = node->GetVolume()->GetShape();
  shape->ComputeBBox();
  auto *box = dynamic_cast<TGeoBBox *>(shape);
  if (!box) gSystem->Exit(3);
  const double *origin = box->GetOrigin();
  double low[3] = {{origin[0] - box->GetDX(),
                    origin[1] - box->GetDY(),
                    origin[2] - box->GetDZ()}};
  double high[3] = {{origin[0] + box->GetDX(),
                     origin[1] + box->GetDY(),
                     origin[2] + box->GetDZ()}};
  double placedLow[3] = {{1.e99, 1.e99, 1.e99}};
  double placedHigh[3] = {{-1.e99, -1.e99, -1.e99}};
  for (int corner = 0; corner < 8; ++corner) {{
    double local[3] = {{corner & 1 ? high[0] : low[0],
                        corner & 2 ? high[1] : low[1],
                        corner & 4 ? high[2] : low[2]}};
    double placed[3];
    node->LocalToMaster(local, placed);
    for (int axis = 0; axis < 3; ++axis) {{
      placedLow[axis] = std::min(placedLow[axis], placed[axis]);
      placedHigh[axis] = std::max(placedHigh[axis], placed[axis]);
    }}
  }}
  std::cout << "{BOUND_PREFIX}" << node->GetVolume()->GetName();
  for (int axis = 0; axis < 3; ++axis)
    std::cout << "\\t" << std::setprecision(17) << 10. * placedLow[axis]
              << "\\t" << 10. * placedHigh[axis];
  std::cout << std::endl;
}}
gSystem->Exit(0);'''


def parse_root_bounds(output):
    bounds = {}
    for line in output.splitlines():
        if not line.startswith(BOUND_PREFIX):
            continue
        fields = line.split("\t")
        if len(fields) != 8:
            raise ProxyModelError(f"malformed ROOT bounds record: {line}")
        volume_name = fields[1]
        if not volume_name.endswith("_lv"):
            raise ProxyModelError(f"unexpected external volume name: {volume_name}")
        region_name = volume_name[:-3]
        if region_name in bounds:
            raise ProxyModelError(f"duplicate ROOT region volume: {region_name}")
        values = [float(value) for value in fields[2:]]
        bounds[region_name] = [[values[0], values[2], values[4]],
                               [values[1], values[3], values[5]]]
    if not bounds:
        raise ProxyModelError("ROOT emitted no external region bounds")
    return bounds


def read_root_bounds(root_command, gdml_path):
    command = [root_command, "-l", "-b", "-q", "-e", root_bounds_expression(gdml_path)]
    result = subprocess.run(command, text=True, capture_output=True)
    combined = result.stdout + result.stderr
    if result.returncode:
        raise ProxyModelError(
            f"ROOT bounds extraction failed with status {result.returncode}:\n{combined[-4000:]}"
        )
    return parse_root_bounds(combined), combined


def source_region_bounds(model_dir, region_names, timeout_seconds=300.0, progress_every=100):
    try:
        from pyg4ometry.fluka import Reader
    except ImportError as error:
        raise ProxyModelError("pyg4ometry is required for the source-bounds audit") from error

    source_deck = Path(model_dir) / "source" / "lhc_ir1_exp_b2.inp"
    result = {}
    source_null_regions = []
    evaluation_errors = []
    originals = install_transformed_infinite_cylinder_centre_workaround()
    try:
        with tempfile.TemporaryDirectory() as directory:
            normalized_path = Path(directory) / "lhc_ir1_exp_b2.normalized.inp"
            normalized_deck(source_deck, normalized_path)
            with open(Path(directory) / "reader.log", "w", encoding="utf-8") as log:
                with redirect_stdout(log):
                    registry = Reader(str(normalized_path)).flukaregistry
            for index, name in enumerate(region_names, 1):
                if name not in registry.regionDict:
                    raise ProxyModelError(f"conversion report contains unknown source region {name}")
                if progress_every > 0 and index % progress_every == 0:
                    print(
                        f"audited source bounds for {index}/{len(region_names)} regions",
                        flush=True,
                    )
                evaluation = isolated_region_bounds(
                    registry.regionDict[name], timeout_seconds=timeout_seconds
                )
                if evaluation["status"] == "error":
                    evaluation_errors.append({"name": name, **evaluation})
                    continue
                if evaluation["status"] == "null":
                    source_null_regions.append(name)
                    continue
                result[name] = evaluation["bounds"]
    finally:
        restore_transformed_infinite_cylinder_centres(originals)
    return result, source_null_regions, evaluation_errors


def containment_result(source_bound, gdml_bound, tolerance_mm):
    deficits = []
    excesses = []
    for axis in range(3):
        deficits.extend([
            max(0.0, gdml_bound[0][axis] - source_bound[0][axis]),
            max(0.0, source_bound[1][axis] - gdml_bound[1][axis]),
        ])
        excesses.extend([
            max(0.0, source_bound[0][axis] - gdml_bound[0][axis]),
            max(0.0, gdml_bound[1][axis] - source_bound[1][axis]),
        ])
    return {
        "source_bounds_mm": source_bound,
        "gdml_bounds_mm": gdml_bound,
        "maximum_containment_deficit_mm": max(deficits),
        "maximum_conservative_excess_mm": max(excesses),
        "contained": max(deficits) <= tolerance_mm,
    }


def apply_secondary_source_bounds(
    source_bounds,
    source_null_regions,
    source_bounds_errors,
    raw_preflight,
    expected_regions,
):
    """Replace failed/null CGAL source bounds with proven pycsg bounds."""

    secondary = (raw_preflight or {}).get("secondary_classification", {})
    secondary_bounds = secondary.get("bounds_mm", {})
    expected = set(expected_regions)
    replacements = sorted(expected & set(secondary_bounds))
    for name in replacements:
        source_bounds[name] = secondary_bounds[name]
    replaced = set(replacements)
    source_null_regions[:] = [
        name for name in source_null_regions if name not in replaced
    ]
    source_bounds_errors[:] = [
        item for item in source_bounds_errors if item["name"] not in replaced
    ]
    return replacements


def audit(
    model_dir,
    gdml_path,
    conversion_report_path,
    root_command,
    tolerance_mm,
    region_timeout_seconds,
    progress_every,
):
    verify_source_bundle(model_dir)
    conversion = json.loads(Path(conversion_report_path).read_text(encoding="utf-8"))
    converted_regions = conversion["geometry"]["converted_regions"]
    if len(converted_regions) != conversion["geometry"]["converted_region_count"]:
        raise ProxyModelError("conversion report converted-region count is inconsistent")

    root_bounds, root_output = read_root_bounds(root_command, gdml_path)
    expected = set(converted_regions)
    observed = set(root_bounds)
    missing = sorted(expected - observed)
    unexpected = sorted(observed - expected)
    source_bounds, converted_source_null_regions, source_bounds_errors = source_region_bounds(
        model_dir, converted_regions, region_timeout_seconds, progress_every
    )
    secondary_source_bound_regions = apply_secondary_source_bounds(
        source_bounds,
        converted_source_null_regions,
        source_bounds_errors,
        conversion["geometry"].get("raw_region_preflight"),
        expected,
    )
    comparable = expected & observed & set(source_bounds)
    regions = {
        name: containment_result(source_bounds[name], root_bounds[name], tolerance_mm)
        for name in sorted(comparable)
    }
    failures = [name for name, result in regions.items() if not result["contained"]]
    return {
        "schema": "shift-ir1-proxy-bounds-audit",
        "schema_version": 1,
        "model_status": "provisional-ir1-atlas-proxy",
        "source_sha256": verify_source_bundle(model_dir),
        "gdml": str(Path(gdml_path).resolve()),
        "gdml_sha256": sha256(gdml_path),
        "conversion_report_sha256": sha256(conversion_report_path),
        "containment_tolerance_mm": tolerance_mm,
        "expected_region_count": len(expected),
        "root_region_count": len(observed),
        "missing_root_regions": missing,
        "unexpected_root_regions": unexpected,
        "converted_source_null_region_count": len(converted_source_null_regions),
        "converted_source_null_regions": converted_source_null_regions,
        "source_bounds_evaluation_error_count": len(source_bounds_errors),
        "source_bounds_evaluation_errors": source_bounds_errors,
        "secondary_source_bound_region_count": len(secondary_source_bound_regions),
        "secondary_source_bound_regions": secondary_source_bound_regions,
        "containment_failure_count": len(failures),
        "containment_failures": failures,
        "maximum_containment_deficit_mm": max(
            (result["maximum_containment_deficit_mm"] for result in regions.values()),
            default=0.0,
        ),
        "root_diagnostic_line_count": len(root_output.splitlines()),
        "passed": (
            not missing
            and not unexpected
            and not converted_source_null_regions
            and not source_bounds_errors
            and not failures
        ),
        "regions": regions,
    }


def main():
    args = parse_args()
    if args.region_timeout_seconds <= 0.0:
        print("error: --region-timeout-seconds must be positive", file=sys.stderr)
        return 2
    if args.containment_tolerance_mm < 0.0:
        print("error: --containment-tolerance-mm must be non-negative", file=sys.stderr)
        return 2
    try:
        result = audit(
            args.model_dir,
            args.gdml,
            args.conversion_report,
            args.root_command,
            args.containment_tolerance_mm,
            args.region_timeout_seconds,
            args.progress_every,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(
        f"audited {result['expected_region_count']} regions; "
        f"containment failures={result['containment_failure_count']}"
    )
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
