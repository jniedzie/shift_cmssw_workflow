#!/usr/bin/env python3
"""Audit coordinate closure between a finalized GDML and model-frame fields."""

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys


class CoordinateClosureError(ValueError):
    pass


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def audit_closure(finalization_path, field_domains_path, geometry_report_path,
                  tolerance_cm=1.0e-6):
    if not math.isfinite(tolerance_cm) or tolerance_cm <= 0.0:
        raise CoordinateClosureError("tolerance must be finite and positive")
    finalization = _load(finalization_path)
    domains = _load(field_domains_path)
    geometry = _load(geometry_report_path)
    finalization_sha = _sha256(finalization_path)
    domains_sha = _sha256(field_domains_path)
    geometry_sha = _sha256(geometry_report_path)

    if not finalization.get("passed"):
        raise CoordinateClosureError("finalized geometry did not pass")
    if not geometry.get("lattice_conversion", {}).get("passed"):
        raise CoordinateClosureError("geometry lattice conversion did not pass")
    if not domains.get("domain_conversion_validated"):
        raise CoordinateClosureError("field-domain conversion is not validated")
    if finalization.get("conversion_report_sha256") != geometry_sha:
        raise CoordinateClosureError("finalization report does not reference this geometry report")
    if domains.get("geometry_report_sha256") != geometry_sha:
        raise CoordinateClosureError("field domains do not reference this geometry report")

    origin_mm = finalization["finalization"]["artifact_origin_in_model_mm"]
    translation_mm = finalization["finalization"]["model_to_artifact_translation_mm"]
    if len(origin_mm) != 3 or len(translation_mm) != 3:
        raise CoordinateClosureError("artifact origin and translation must have three values")
    if any(not math.isfinite(float(value)) for value in (*origin_mm, *translation_mm)):
        raise CoordinateClosureError("artifact origin and translation must be finite")
    inverse_residual_mm = max(abs(float(origin_mm[i]) + float(translation_mm[i])) for i in range(3))
    if inverse_residual_mm > tolerance_cm * 10.0:
        raise CoordinateClosureError("model-to-artifact translation is not the inverse artifact origin")
    origin_cm = [float(value) / 10.0 for value in origin_mm]
    interface = finalization["finalization"].get("model_interface_partition")
    interface_boundary_cm = None
    if interface is not None:
        if interface.get("axis") != "model_z" or interface.get("lattice_placements_affected") != 0:
            raise CoordinateClosureError("unsupported or lattice-affecting model interface partition")
        interface_boundary_cm = float(interface["boundary_mm"]) / 10.0

    lattices = geometry["lattice_conversion"]["lattices"]
    by_cell = {item["cell"]: item for item in lattices}
    if len(by_cell) != len(lattices):
        raise CoordinateClosureError("geometry report contains duplicate lattice cells")

    results = []
    seen_names = set()
    maximum_residual = 0.0
    for element in domains["elements"]:
        name = element["name"]
        target = element["target_region"]
        if name in seen_names:
            raise CoordinateClosureError(f"duplicate field element {name}")
        seen_names.add(name)
        if target not in by_cell:
            raise CoordinateClosureError(f"field target {target} is absent from lattice report")
        assigned = element.get("assigned_target_domain", element)
        local_minimum = assigned["minimum_cm"]
        local_maximum = assigned["maximum_cm"]
        field_origin = element["origin_model_cm"]
        if any(len(values) != 3 for values in (local_minimum, local_maximum, field_origin)):
            raise CoordinateClosureError(f"{name}: bounds and origin must have three values")
        expected = [[float(value) / 10.0 for value in edge]
                    for edge in by_cell[target]["physical_cell_bounds_mm"]]
        if (interface_boundary_cm is not None
                and expected[0][2] < interface_boundary_cm - tolerance_cm):
            raise CoordinateClosureError(
                f"{name}: field lattice cell crosses the model interface boundary"
            )
        model_bounds = [
            [float(field_origin[axis]) + float(local_minimum[axis]) for axis in range(3)],
            [float(field_origin[axis]) + float(local_maximum[axis]) for axis in range(3)],
        ]
        model_residual = max(abs(model_bounds[edge][axis] - expected[edge][axis])
                             for edge in range(2) for axis in range(3))
        artifact_bounds = [[model_bounds[edge][axis] - origin_cm[axis] for axis in range(3)]
                           for edge in range(2)]
        expected_artifact = [[expected[edge][axis] - origin_cm[axis] for axis in range(3)]
                             for edge in range(2)]
        artifact_residual = max(abs(artifact_bounds[edge][axis] - expected_artifact[edge][axis])
                                for edge in range(2) for axis in range(3))
        residual = max(model_residual, artifact_residual)
        if residual > tolerance_cm:
            raise CoordinateClosureError(
                f"{name}: field/material bounds disagree by {residual} cm"
            )
        maximum_residual = max(maximum_residual, residual)
        results.append({
            "name": name,
            "target_region": target,
            "maximum_artifact_bound_residual_cm": artifact_residual,
        })

    if len(results) != domains.get("element_count"):
        raise CoordinateClosureError("field-domain element count is inconsistent")
    return {
        "schema": "shift-lss-artifact-field-coordinate-closure-v1",
        "passed": True,
        "production_ready": False,
        "production_ready_reason": (
            "artifact/model frame closure passed; authoritative model-to-CMS transform is still required"
        ),
        "tolerance_cm": tolerance_cm,
        "artifact_origin_in_model_cm": origin_cm,
        "model_to_artifact_translation_cm": [float(value) / 10.0 for value in translation_mm],
        "model_interface_boundary_cm": interface_boundary_cm,
        "field_lattice_cells_affected_by_interface": 0,
        "closure_identity": "artifact_point = model_field_point - artifactOriginInModelCm",
        "geometry_placement_contract": (
            "cms_point = modelOriginCm + modelToCms * artifactOriginInModelCm + modelToCms * artifact_point"
        ),
        "field_placement_contract": "cms_point = modelOriginCm + modelToCms * model_field_point",
        "element_count": len(results),
        "maximum_artifact_bound_residual_cm": maximum_residual,
        "elements": results,
        "field_domains_sha256": domains_sha,
        "finalization_report_sha256": finalization_sha,
        "geometry_report_sha256": geometry_sha,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--finalization-report", type=Path, required=True)
    parser.add_argument("--field-domains", type=Path, required=True)
    parser.add_argument("--geometry-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tolerance-cm", type=float, default=1.0e-6)
    args = parser.parse_args(argv)
    try:
        if args.output.exists():
            raise CoordinateClosureError("output already exists")
        report = audit_closure(
            args.finalization_report, args.field_domains, args.geometry_report,
            args.tolerance_cm,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except (CoordinateClosureError, KeyError, OSError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(
        f"audited {report['element_count']} field/material domains; "
        f"maximum residual={report['maximum_artifact_bound_residual_cm']:.6g} cm"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
