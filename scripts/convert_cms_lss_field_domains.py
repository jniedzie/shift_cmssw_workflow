#!/usr/bin/env python3
"""Translate audited FLUKA MGNFIELD domains into CMSSW element parameters.

Only exact axis-aligned RCC/ZCC annuli and RPP boxes are accepted.  The
converter fails closed on rotations, Boolean unions, or other primitives.
The resulting coordinates remain in the source model frame; selecting the
absolute model-to-CMS transform is deliberately outside this step.
"""

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys


class FieldDomainError(ValueError):
    pass


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _number(value, context):
    try:
        result = float(value.replace("D", "E"))
    except (AttributeError, ValueError) as error:
        raise FieldDomainError(f"{context}: invalid number {value!r}") from error
    if not math.isfinite(result):
        raise FieldDomainError(f"{context}: non-finite number")
    return result


def _active_records(path):
    records = []
    for number, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = raw.split("!", 1)[0].strip()
        if line and not line.startswith(("*", "#")):
            records.append((number, line.split()))
    return records


def _parse_deck(path, targets, transform_names):
    bodies, regions, transforms = {}, {}, {}
    body_types = {"RCC", "RPP", "ZCC"}
    for number, tokens in _active_records(path):
        if tokens[0] in body_types and len(tokens) >= 3:
            name = tokens[1]
            if name in bodies:
                raise FieldDomainError(f"{path}:{number}: duplicate body {name}")
            bodies[name] = {"type": tokens[0], "values": tokens[2:], "line": number}
        elif tokens[0] == "ROT-DEFI" and tokens[-1] in transform_names:
            if len(tokens) != 8:
                raise FieldDomainError(f"{path}:{number}: unsupported ROT-DEFI layout")
            transforms.setdefault(tokens[-1], []).append(
                {"values": [_number(value, f"{path}:{number}") for value in tokens[1:7]],
                 "line": number}
            )
        elif tokens[0] in targets:
            if len(tokens) < 3:
                raise FieldDomainError(f"{path}:{number}: malformed field target region")
            if tokens[0] in regions:
                raise FieldDomainError(f"{path}:{number}: duplicate region {tokens[0]}")
            regions[tokens[0]] = {"expression": tokens[2:], "line": number}
    missing_regions = sorted(targets - set(regions))
    missing_transforms = sorted(transform_names - set(transforms))
    if missing_regions or missing_transforms:
        raise FieldDomainError(
            "missing field inputs: regions=" + ",".join(missing_regions) +
            " transforms=" + ",".join(missing_transforms)
        )
    return bodies, regions, transforms


def _translation_origin(transform, context):
    if len(transform) != 1:
        raise FieldDomainError(f"{context}: recursive field transforms are unsupported")
    values = transform[0]["values"]
    if values[1] != 0.0 or values[2] != 0.0:
        raise FieldDomainError(f"{context}: rotated field frame is unsupported")
    # With zero rotation ROT-DEFI maps model coordinates to field coordinates
    # as new=old+offset.  The field-frame origin is therefore -offset.
    return [-values[3], -values[4], -values[5]]


def _domain(region, bodies, context):
    terms = region["expression"]
    if any(not term.startswith(("+", "-")) for term in terms):
        raise FieldDomainError(f"{context}: Boolean unions are unsupported")
    resolved = []
    for term in terms:
        name = term[1:]
        if name not in bodies:
            raise FieldDomainError(f"{context}: missing body {name}")
        resolved.append((term[0], name, bodies[name]))

    if len(resolved) == 1 and resolved[0][0] == "+" and resolved[0][2]["type"] == "RPP":
        values = [_number(value, context) for value in resolved[0][2]["values"]]
        if len(values) != 6 or not all(values[index] < values[index + 1] for index in (0, 2, 4)):
            raise FieldDomainError(f"{context}: invalid RPP")
        return {"bounds_shape": "box", "minimum_cm": values[::2],
                "maximum_cm": values[1::2]}

    outer, inner, z_min, z_max = math.inf, 0.0, -math.inf, math.inf
    for sign, name, body in resolved:
        values = [_number(value, context) for value in body["values"]]
        if body["type"] == "ZCC":
            if len(values) != 3 or values[0] != 0.0 or values[1] != 0.0 or values[2] <= 0.0:
                raise FieldDomainError(f"{context}: only origin-centred ZCC is supported")
            if sign == "+":
                outer = min(outer, values[2])
            else:
                inner = max(inner, values[2])
        elif body["type"] == "RCC":
            if (len(values) != 7 or values[0] != 0.0 or values[1] != 0.0 or
                    values[3] != 0.0 or values[4] != 0.0 or values[6] <= 0.0 or
                    values[5] == 0.0 or sign != "+"):
                raise FieldDomainError(f"{context}: only positive axis-aligned RCC is supported")
            outer = min(outer, values[6])
            z_min = max(z_min, min(values[2], values[2] + values[5]))
            z_max = min(z_max, max(values[2], values[2] + values[5]))
        else:
            raise FieldDomainError(f"{context}: mixed RPP/cylinder domain is unsupported")
    if not (0.0 <= inner < outer < math.inf and z_min < z_max):
        raise FieldDomainError(f"{context}: empty or unbounded cylindrical domain")
    return {
        "bounds_shape": "cylinderZ",
        "bounds_center_cm": [0.0, 0.0, 0.0],
        "inner_radius_cm": inner,
        "outer_radius_cm": outer,
        "minimum_cm": [-outer, -outer, z_min],
        "maximum_cm": [outer, outer, z_max],
    }


def _validate_geometry_bounds(element, geometry_cell, tolerance_cm=1.0e-6):
    expected = geometry_cell["physical_cell_bounds_mm"]
    origin = element["origin_model_cm"]
    actual = [[origin[axis] + element["minimum_cm"][axis] for axis in range(3)],
              [origin[axis] + element["maximum_cm"][axis] for axis in range(3)]]
    expected_cm = [[value / 10.0 for value in edge] for edge in expected]
    residual = max(abs(actual[edge][axis] - expected_cm[edge][axis])
                   for edge in range(2) for axis in range(3))
    if residual > tolerance_cm:
        raise FieldDomainError(
            f"{element['target_region']}: field domain disagrees with converted lattice bounds "
            f"by {residual} cm"
        )
    return residual


def convert_domains(active_deck, field_manifest_path, geometry_report_path):
    manifest = json.loads(Path(field_manifest_path).read_text(encoding="utf-8"))
    geometry = json.loads(Path(geometry_report_path).read_text(encoding="utf-8"))
    assignments = manifest["assignments"]
    targets = {assignment["what"][3] for assignment in assignments}
    transform_names = {assignment["what"][1] for assignment in assignments}
    bodies, regions, transforms = _parse_deck(active_deck, targets, transform_names)
    lattice_cells = {cell["cell"]: cell for cell in geometry["lattice_conversion"]["lattices"]}

    elements = []
    for assignment in assignments:
        what, name = assignment["what"], assignment["sdum"]
        target, transform_name = what[3], what[1]
        if what[2] not in ("", "0", "0.0") or any(value not in ("", "0", "0.0") for value in what[4:]):
            raise FieldDomainError(f"line {assignment['line']}: unsupported MGNFIELD range or mode")
        if target not in lattice_cells:
            raise FieldDomainError(f"line {assignment['line']}: target {target} is not a converted lattice cell")
        element = {
            "name": f"{name}.{target}",
            "field": name,
            "field_scale": _number(what[0], f"line {assignment['line']}"),
            "field_transform": transform_name,
            "origin_model_cm": _translation_origin(transforms[transform_name], transform_name),
            "source_line": assignment["line"],
            "target_region": target,
        }
        assigned_domain = _domain(regions[target], bodies, target)
        element.update(assigned_domain)
        element["maximum_geometry_bound_residual_cm"] = _validate_geometry_bounds(
            element, lattice_cells[target]
        )
        element["assigned_target_domain"] = assigned_domain
        if name in manifest["maps"]:
            element.update(type="flukaMap2D", map_file=manifest["maps"][name]["output"])
        elif name in manifest["inline_analytic_definitions"]:
            definition = manifest["inline_analytic_definitions"][name]
            if definition["metadata"]["type"] != "DIPOLE":
                raise FieldDomainError(f"{name}: unsupported inline analytic type")
            core_radius = definition["metadata"]["core_radius_cm"]
            core_origin = definition["metadata"]["analytical_origin_cm"]
            if core_radius:
                if (element["bounds_shape"] != "cylinderZ" or core_origin != [0.0, 0.0, 0.0] or
                        element["bounds_center_cm"] != [0.0, 0.0, 0.0]):
                    raise FieldDomainError(f"{name}: analytic core cannot be clipped exactly")
                effective_outer = min(element["outer_radius_cm"], core_radius)
                if element["inner_radius_cm"] >= effective_outer:
                    raise FieldDomainError(f"{name}: analytic core has no field support in {target}")
                element["outer_radius_cm"] = effective_outer
                element["minimum_cm"] = [-effective_outer, -effective_outer, element["minimum_cm"][2]]
                element["maximum_cm"] = [effective_outer, effective_outer, element["maximum_cm"][2]]
                element["analytic_core_clipped"] = effective_outer < assigned_domain["outer_radius_cm"]
            element.update(type="uniform", field_model_tesla=[0.0, element["field_scale"], 0.0])
        else:
            raise FieldDomainError(f"{name}: missing translated field definition")
        elements.append(element)

    return {
        "schema": "shift-cms-lss-field-domains-v1",
        "production_ready": False,
        "domain_conversion_validated": True,
        "element_count": len(elements),
        "active_deck_sha256": _sha256(active_deck),
        "field_manifest_sha256": _sha256(field_manifest_path),
        "geometry_report_sha256": _sha256(geometry_report_path),
        "elements": elements,
        "references": {
            "MGNCREAT": "https://flukafiles.web.cern.ch/manual/chapters/description_input/description_options/mgncreat.html",
            "MGNFIELD": "https://flukafiles.web.cern.ch/manual/chapters/description_input/description_options/mgnfield.html",
            "ROT-DEFI": "https://flukafiles.web.cern.ch/manual/chapters/description_input/description_options/rot-defi.html",
        },
        "limitations": [
            "Coordinates and vectors remain in the FLUKA source model frame.",
            "An independently reviewed model-to-CMS transform is still required.",
            "Native FLUKA reference-vector and transport comparisons are still required.",
            "The source geometry overlap gate is not passed.",
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--active-deck", type=Path, required=True)
    parser.add_argument("--field-manifest", type=Path, required=True)
    parser.add_argument("--geometry-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = convert_domains(args.active_deck, args.field_manifest, args.geometry_report)
        if args.output.exists():
            raise FieldDomainError("output already exists")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except (FieldDomainError, KeyError, OSError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"wrote {report['element_count']} exact model-frame field domains; production_ready=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
