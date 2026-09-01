#!/usr/bin/env python3

"""Audit a bounded IR1 proxy GDML with ROOT geometry navigation."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile


PREFIX = "SHIFT_BOUNDED_AUDIT\t"


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gdml", type=Path, required=True)
    parser.add_argument("--conversion-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--root-command", default="root")
    parser.add_argument("--overlap-tolerance-mm", type=float, default=0.01)
    return parser.parse_args()


def scan_definitions(conversion):
    bounded = conversion["geometry"]["bounded_artifact"]
    translation = bounded["model_to_artifact_translation_mm"]
    model_bounds = bounded["model_bounds_mm"]
    scans = [
        {
            "name": "longitudinal_x0_y0",
            "axis": 2,
            "fixed_mm": [translation[0], translation[1]],
        }
    ]
    lattice_centres = sorted(
        {
            round(
                0.5
                * (
                    item["physical_cell_bounds_mm"][0][2]
                    + item["physical_cell_bounds_mm"][1][2]
                ),
                9,
            )
            for item in conversion["geometry"]["lattice_placement"]["lattices"]
        }
    )
    for index, model_z in enumerate(lattice_centres):
        artifact_z = model_z + translation[2]
        scans.extend(
            [
                {
                    "name": f"transverse_x_{index:02d}",
                    "axis": 0,
                    "fixed_mm": [translation[1], artifact_z],
                    "model_z_mm": model_z,
                },
                {
                    "name": f"transverse_y_{index:02d}",
                    "axis": 1,
                    "fixed_mm": [translation[0], artifact_z],
                    "model_z_mm": model_z,
                },
            ]
        )
    for scan in scans:
        scan["model_bounds_mm"] = model_bounds
    return scans


def root_expression(gdml_path, scans, overlap_tolerance_mm):
    path = json.dumps(str(Path(gdml_path).resolve()))
    scan_lines = []
    for scan in scans:
        fixed = scan["fixed_mm"]
        scan_lines.append(
            "scanRay("
            + json.dumps(scan["name"])
            + f", {scan['axis']}, {fixed[0] / 10.0:.17g}, {fixed[1] / 10.0:.17g});"
        )
    return f'''#include <iomanip>
#include <iostream>
#include <TGeoBBox.h>
#include <TGeoManager.h>
#include <TGeoNode.h>
#include <TGeoOverlap.h>
#include <TObjArray.h>

TGeoManager::Import({path});
if (!gGeoManager) gSystem->Exit(2);
auto *top = gGeoManager->GetTopVolume();
auto *topBox = dynamic_cast<TGeoBBox *>(top->GetShape());
if (!top || !topBox) gSystem->Exit(3);
const double *topOrigin = topBox->GetOrigin();
double worldLow[3] = {{topOrigin[0] - topBox->GetDX(),
                       topOrigin[1] - topBox->GetDY(),
                       topOrigin[2] - topBox->GetDZ()}};
double worldHigh[3] = {{topOrigin[0] + topBox->GetDX(),
                        topOrigin[1] + topBox->GetDY(),
                        topOrigin[2] + topBox->GetDZ()}};
std::cout << "{PREFIX}WORLD";
for (int axis = 0; axis < 3; ++axis)
  std::cout << "\\t" << std::setprecision(17) << 10. * worldLow[axis]
            << "\\t" << 10. * worldHigh[axis];
std::cout << std::endl;

for (int index = 0; index < top->GetNdaughters(); ++index) {{
  auto *node = top->GetNode(index);
  auto *box = dynamic_cast<TGeoBBox *>(node->GetVolume()->GetShape());
  if (!box) gSystem->Exit(4);
  box->ComputeBBox();
  const double *origin = box->GetOrigin();
  double low[3] = {{origin[0] - box->GetDX(), origin[1] - box->GetDY(),
                    origin[2] - box->GetDZ()}};
  double high[3] = {{origin[0] + box->GetDX(), origin[1] + box->GetDY(),
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
  auto *material = node->GetVolume()->GetMaterial();
  std::cout << "{PREFIX}PLACEMENT\\t" << node->GetName() << "\\t"
            << node->GetVolume()->GetName() << "\\t"
            << (material ? material->GetName() : "<none>");
  for (int axis = 0; axis < 3; ++axis)
    std::cout << "\\t" << std::setprecision(17) << 10. * placedLow[axis]
              << "\\t" << 10. * placedHigh[axis];
  std::cout << std::endl;
}}

gGeoManager->CheckOverlaps({overlap_tolerance_mm / 10.0:.17g});
auto *overlaps = gGeoManager->GetListOfOverlaps();
std::cout << "{PREFIX}OVERLAP_COUNT\\t"
          << (overlaps ? overlaps->GetEntries() : 0) << std::endl;
if (overlaps) {{
  for (int index = 0; index < overlaps->GetEntries(); ++index) {{
    auto *overlap = dynamic_cast<TGeoOverlap *>(overlaps->At(index));
    if (!overlap) continue;
    std::cout << "{PREFIX}OVERLAP\\t" << overlap->GetName() << "\\t"
              << overlap->GetTitle() << "\\t" << std::setprecision(17)
              << 10. * overlap->GetOverlap() << "\\t"
              << (overlap->IsExtrusion() ? 1 : 0) << std::endl;
  }}
}}

auto scanRay = [&](const char *name, int axis, double fixed0, double fixed1) {{
  double point[3] = {{0., 0., 0.}};
  double direction[3] = {{0., 0., 0.}};
  int fixedIndex = 0;
  for (int coordinate = 0; coordinate < 3; ++coordinate) {{
    if (coordinate == axis) {{
      point[coordinate] = worldLow[coordinate] + 1.e-7;
      direction[coordinate] = 1.;
    }} else {{
      point[coordinate] = fixedIndex++ == 0 ? fixed0 : fixed1;
    }}
  }}
  gGeoManager->InitTrack(point, direction);
  for (int segment = 0; segment < 100000; ++segment) {{
    const double start = gGeoManager->GetCurrentPoint()[axis];
    const double remaining = worldHigh[axis] - start - 1.e-7;
    if (remaining <= 0. || gGeoManager->IsOutside()) break;
    auto *node = gGeoManager->GetCurrentNode();
    auto *volume = node ? node->GetVolume() : nullptr;
    auto *material = volume ? volume->GetMaterial() : nullptr;
    gGeoManager->FindNextBoundaryAndStep(remaining, false);
    const double step = gGeoManager->GetStep();
    std::cout << "{PREFIX}SCAN\\t" << name << "\\t" << segment
              << "\\t" << (volume ? volume->GetName() : "<none>")
              << "\\t" << (material ? material->GetName() : "<none>")
              << "\\t" << std::setprecision(17) << 10. * start
              << "\\t" << 10. * (start + step) << std::endl;
    if (!(step > 1.e-10)) {{
      std::cout << "{PREFIX}SCAN_ERROR\\t" << name
                << "\\tnon-positive navigation step" << std::endl;
      break;
    }}
  }}
}};
{''.join(scan_lines)}
gSystem->Exit(0);'''


def parse_root_output(output):
    result = {"placements": [], "overlaps": [], "scans": {}, "scan_errors": []}
    for line in output.splitlines():
        if not line.startswith(PREFIX):
            continue
        fields = line.split("\t")
        record = fields[1]
        if record == "WORLD":
            values = [float(value) for value in fields[2:]]
            result["world_bounds_mm"] = [values[0::2], values[1::2]]
        elif record == "PLACEMENT":
            values = [float(value) for value in fields[5:]]
            result["placements"].append(
                {
                    "name": fields[2],
                    "logical_volume": fields[3],
                    "material": fields[4],
                    "bounds_mm": [values[0::2], values[1::2]],
                }
            )
        elif record == "OVERLAP_COUNT":
            result["root_overlap_count"] = int(fields[2])
        elif record == "OVERLAP":
            result["overlaps"].append(
                {
                    "name": fields[2],
                    "description": fields[3],
                    "overlap_mm": float(fields[4]),
                    "is_extrusion": bool(int(fields[5])),
                }
            )
        elif record == "SCAN":
            result["scans"].setdefault(fields[2], []).append(
                {
                    "index": int(fields[3]),
                    "logical_volume": fields[4],
                    "material": fields[5],
                    "start_mm": float(fields[6]),
                    "end_mm": float(fields[7]),
                }
            )
        elif record == "SCAN_ERROR":
            result["scan_errors"].append({"scan": fields[2], "error": fields[3]})
    required = {"world_bounds_mm", "root_overlap_count"}
    if not required <= set(result):
        raise ValueError("ROOT output omitted required bounded-audit records")
    return result


def contains(outer, inner, tolerance_mm):
    return all(
        outer[0][axis] - tolerance_mm <= inner[0][axis]
        and inner[1][axis] <= outer[1][axis] + tolerance_mm
        for axis in range(3)
    )


def audit(args):
    conversion = json.loads(args.conversion_report.read_text(encoding="utf-8"))
    if not conversion["conversion_controls"]["bounded_installable_requested"]:
        raise ValueError("conversion report is not for a bounded artifact")
    scans = scan_definitions(conversion)
    expression = root_expression(args.gdml, scans, args.overlap_tolerance_mm)
    # ROOT 6.36 can return status 99 without diagnostics when a long `-e`
    # expression exceeds its command-line parser's practical limit.  A macro
    # file exercises the same interpreter while avoiding that failure mode.
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".C", prefix="shift_bounded_audit_"
    ) as macro:
        macro.write("{\n" + expression + "\n}\n")
        macro.flush()
        command = [args.root_command, "-l", "-b", "-q", macro.name]
        process = subprocess.run(command, text=True, capture_output=True)
    root_output = process.stdout + process.stderr
    if process.returncode:
        raise RuntimeError(
            f"ROOT bounded audit failed with status {process.returncode}:\n"
            + root_output[-4000:]
        )
    root = parse_root_output(root_output)
    expected = conversion["geometry"]["bounded_artifact"]
    expected_count = expected["placed_volume_count"]
    placement_names = {item["name"] for item in root["placements"]}
    parked = set(conversion["geometry"]["lattice_placement"]["parking_prototypes"])
    parked.add("PARKr")
    parked_placements = sorted(
        name for name in placement_names if name.endswith("_pv") and name[:-3] in parked
    )
    containment_failures = sorted(
        item["name"]
        for item in root["placements"]
        if not contains(root["world_bounds_mm"], item["bounds_mm"], args.overlap_tolerance_mm)
    )
    scan_by_name = {item["name"]: item for item in scans}
    material_lengths = {}
    internal_world_gaps = []
    top_name = conversion["geometry"]["world_volume"]
    for name, segments in root["scans"].items():
        for segment in segments:
            length = segment["end_mm"] - segment["start_mm"]
            material_lengths[segment["material"]] = (
                material_lengths.get(segment["material"], 0.0) + length
            )
        occupied = [index for index, segment in enumerate(segments) if segment["logical_volume"] != top_name]
        if occupied:
            first, last = min(occupied), max(occupied)
            internal_world_gaps.extend(
                {
                    "scan": name,
                    "start_mm": segment["start_mm"],
                    "end_mm": segment["end_mm"],
                }
                for segment in segments[first:last + 1]
                if segment["logical_volume"] == top_name
                and segment["end_mm"] - segment["start_mm"] > args.overlap_tolerance_mm
            )
    failures = {
        "placement_count_mismatch": len(root["placements"]) != expected_count,
        "parked_placements": parked_placements,
        "world_containment_failures": containment_failures,
        "root_overlap_count": root["root_overlap_count"],
        "scan_errors": root["scan_errors"],
        "internal_world_gaps": internal_world_gaps,
    }
    passed = not any(
        [
            failures["placement_count_mismatch"],
            parked_placements,
            containment_failures,
            root["root_overlap_count"],
            root["scan_errors"],
            internal_world_gaps,
        ]
    )
    return {
        "schema": "shift-ir1-bounded-gdml-audit",
        "schema_version": 1,
        "model_status": "provisional-ir1-atlas-proxy",
        "gdml": str(args.gdml.resolve()),
        "gdml_sha256": sha256(args.gdml),
        "conversion_report": str(args.conversion_report.resolve()),
        "conversion_report_sha256": sha256(args.conversion_report),
        "overlap_tolerance_mm": args.overlap_tolerance_mm,
        "scan_definitions": scan_by_name,
        "world_bounds_mm": root["world_bounds_mm"],
        "expected_placement_count": expected_count,
        "root_placement_count": len(root["placements"]),
        "placements": root["placements"],
        "root_overlap_count": root["root_overlap_count"],
        "overlaps": root["overlaps"],
        "material_path_length_mm": dict(sorted(material_lengths.items())),
        "scans": root["scans"],
        "internal_world_gap_count": len(internal_world_gaps),
        "internal_world_gaps": internal_world_gaps,
        "failures": failures,
        "passed": passed,
    }


def main():
    args = parse_args()
    if args.overlap_tolerance_mm <= 0.0:
        print("error: --overlap-tolerance-mm must be positive", file=sys.stderr)
        return 2
    try:
        payload = audit(args)
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(args.output)
    print(
        f"wrote {args.output} with {payload['root_overlap_count']} ROOT overlaps, "
        f"{payload['internal_world_gap_count']} internal world gaps, "
        f"passed={payload['passed']}"
    )
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
