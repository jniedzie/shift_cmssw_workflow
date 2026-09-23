#!/usr/bin/env python3
"""Stage validated CMS LSS geometry and field artifacts as a CMSSW payload tree."""

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import sys
import tempfile


class PayloadStagingError(ValueError):
    pass


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _identifier(value, label):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise PayloadStagingError(f"{label} must be a Python identifier")
    return value


def _payload_name(value):
    if not re.fullmatch(r"[a-z0-9][a-z0-9_]*", value):
        raise PayloadStagingError("payload name must contain only lowercase letters, digits, and underscores")
    return value


def _vector(values, length, label):
    if len(values) != length:
        raise PayloadStagingError(f"{label} must contain {length} values")
    result = tuple(float(value) for value in values)
    if not all(math.isfinite(value) for value in result):
        raise PayloadStagingError(f"{label} must contain only finite values")
    return result


def _literal(value):
    if isinstance(value, str):
        return repr(value)
    if isinstance(value, tuple):
        suffix = "," if len(value) == 1 else ""
        return "(" + ", ".join(_literal(item) for item in value) + suffix + ")"
    if isinstance(value, float):
        return format(value, ".17g")
    raise TypeError(type(value))


def _normalise_element(element, available_maps):
    name = str(element["name"])
    kind = element["type"]
    if kind not in ("uniform", "flukaMap2D"):
        raise PayloadStagingError(f"{name}: unsupported field type {kind}")
    minimum = _vector(element["minimum_cm"], 3, f"{name}.minimum_cm")
    maximum = _vector(element["maximum_cm"], 3, f"{name}.maximum_cm")
    if any(minimum[i] >= maximum[i] for i in range(3)):
        raise PayloadStagingError(f"{name}: invalid bounds")
    shape = element["bounds_shape"]
    if shape not in ("box", "cylinderZ"):
        raise PayloadStagingError(f"{name}: unsupported bounds shape {shape}")
    excluded = element.get("excluded_cylinders_cm", ())
    result = {
        "name": name,
        "type": kind,
        "minimum": minimum,
        "maximum": maximum,
        "origin": _vector(element["origin_model_cm"], 3, f"{name}.origin_model_cm"),
        "bounds_shape": shape,
        "bounds_center": _vector(element.get("bounds_center_cm", (0.0, 0.0, 0.0)), 3,
                                  f"{name}.bounds_center_cm"),
        "inner_radius": float(element.get("inner_radius_cm", 0.0)),
        "outer_radius": float(element.get("outer_radius_cm", 0.0)),
        "excluded_cylinders": _vector(excluded, len(excluded), f"{name}.excluded_cylinders_cm"),
    }
    if len(result["excluded_cylinders"]) % 3:
        raise PayloadStagingError(f"{name}: excluded cylinders must be x, y, radius triples")
    if shape == "cylinderZ" and not (
        math.isfinite(result["inner_radius"]) and math.isfinite(result["outer_radius"])
        and 0.0 <= result["inner_radius"] < result["outer_radius"]
    ):
        raise PayloadStagingError(f"{name}: invalid cylindrical radii")
    if kind == "uniform":
        result["field"] = _vector(element["field_model_tesla"], 3, f"{name}.field_model_tesla")
    else:
        map_name = element["map_file"]
        if map_name not in available_maps:
            raise PayloadStagingError(f"{name}: map {map_name} is not in the field manifest")
        result["map_file"] = map_name
        result["field_scale"] = float(element["field_scale"])
        if not math.isfinite(result["field_scale"]):
            raise PayloadStagingError(f"{name}: field scale must be finite")
    return result


def render_field_cff(elements, data_directory, function_name, domains_sha256):
    rows = []
    for element in elements:
        fields = []
        for key in ("name", "type", "minimum", "maximum", "origin", "bounds_shape",
                    "bounds_center", "inner_radius", "outer_radius", "excluded_cylinders",
                    "field", "map_file", "field_scale"):
            if key in element:
                fields.append(f"{key}={_literal(element[key])}")
        rows.append("    dict(" + ", ".join(fields) + "),")
    element_text = "\n".join(rows)
    return f"""# Generated, checksummed field placement for the CMS IR5 LSS model.
# Do not edit by hand. The caller must supply the reviewed model-to-CMS transform.

import math

from PhysicsTools.ShiftMuonSegments.shiftLssMagneticField_cfi import (
    shiftLssFlukaMap2DFieldElement,
    shiftLssUniformFieldElement,
)


_DATA_DIRECTORY = {data_directory!r}
_FIELD_DOMAINS_SHA256 = {domains_sha256!r}
_ELEMENTS = (
{element_text}
)


def _validated_transform(modelOriginCm, modelToCms):
    if len(modelOriginCm) != 3 or len(modelToCms) != 9:
        raise ValueError("modelOriginCm and modelToCms must contain 3 and 9 values")
    values = tuple(float(value) for value in (*modelOriginCm, *modelToCms))
    if not all(math.isfinite(value) for value in values):
        raise ValueError("coordinate transform must contain only finite values")
    origin, rotation = values[:3], values[3:]
    for row in range(3):
        for other in range(3):
            dot = sum(rotation[3 * row + column] * rotation[3 * other + column]
                      for column in range(3))
            if abs(dot - (1.0 if row == other else 0.0)) > 1.0e-9:
                raise ValueError("modelToCms must be an orthonormal rotation")
    determinant = (
        rotation[0] * (rotation[4] * rotation[8] - rotation[5] * rotation[7])
        - rotation[1] * (rotation[3] * rotation[8] - rotation[5] * rotation[6])
        + rotation[2] * (rotation[3] * rotation[7] - rotation[4] * rotation[6])
    )
    if abs(determinant - 1.0) > 1.0e-9:
        raise ValueError("modelToCms must have determinant +1")
    return origin, rotation


def {function_name}(*, modelOriginCm, modelToCms, fieldScale=1.0):
    model_origin, rotation = _validated_transform(modelOriginCm, modelToCms)
    field_scale = float(fieldScale)
    if not math.isfinite(field_scale) or field_scale == 0.0:
        raise ValueError("fieldScale must be finite and nonzero")

    def cms_origin(origin):
        return tuple(
            model_origin[axis]
            + sum(rotation[3 * axis + local] * origin[local] for local in range(3))
            for axis in range(3)
        )

    result = []
    for element in _ELEMENTS:
        common = dict(
            originCm=cms_origin(element["origin"]),
            localToGlobal=rotation,
            boundsShape=element["bounds_shape"],
            boundsCenterCm=element["bounds_center"],
            innerRadiusCm=element["inner_radius"],
            outerRadiusCm=element["outer_radius"],
            excludedCylindersCm=element["excluded_cylinders"],
        )
        if element["type"] == "uniform":
            result.append(shiftLssUniformFieldElement(
                element["name"], element["minimum"], element["maximum"],
                tuple(field_scale * value for value in element["field"]), **common,
            ))
        else:
            result.append(shiftLssFlukaMap2DFieldElement(
                element["name"], element["minimum"], element["maximum"],
                f"{{_DATA_DIRECTORY}}/{{element['map_file']}}",
                field_scale * element["field_scale"], **common,
            ))
    return result
"""


def stage_payload(*, geometry_gdml, finalization_report, root_audit, material_audit,
                  field_manifest, field_domains, coordinate_audit, output_dir,
                  payload_name, python_module, function_name):
    payload_name = _payload_name(payload_name)
    python_module = _identifier(python_module, "Python module")
    function_name = _identifier(function_name, "function name")
    output_dir = Path(output_dir)
    if output_dir.exists():
        raise PayloadStagingError("output directory must not exist")

    geometry_gdml = Path(geometry_gdml)
    finalization_report = Path(finalization_report)
    root_audit = Path(root_audit)
    material_audit = Path(material_audit)
    field_manifest = Path(field_manifest)
    field_domains = Path(field_domains)
    coordinate_audit = Path(coordinate_audit)
    for path in (geometry_gdml, finalization_report, root_audit, material_audit,
                 field_manifest, field_domains, coordinate_audit):
        if path.is_symlink() or not path.is_file():
            raise PayloadStagingError(f"required regular input file is missing: {path}")

    final = _load(finalization_report)
    root = _load(root_audit)
    materials = _load(material_audit)
    fields = _load(field_manifest)
    domains = _load(field_domains)
    closure = _load(coordinate_audit)
    gdml_sha = _sha256(geometry_gdml)
    final_sha = _sha256(finalization_report)
    fields_sha = _sha256(field_manifest)
    domains_sha = _sha256(field_domains)
    if not final.get("passed") or final.get("output_gdml_sha256") != gdml_sha:
        raise PayloadStagingError("finalization gate or GDML checksum failed")
    if not root.get("passed") or root.get("gdml_sha256") != gdml_sha or root.get("overlap_count") != 0:
        raise PayloadStagingError("ROOT overlap/navigation gate failed")
    if not materials.get("passed") or materials.get("undefined_materials"):
        raise PayloadStagingError("material-reference gate failed")
    if fields.get("payload_dependency_closure", {}).get("status") != "pass":
        raise PayloadStagingError("field payload dependency closure is absent or failed")
    if domains.get("field_manifest_sha256") != fields_sha or not domains.get("domain_conversion_validated"):
        raise PayloadStagingError("field-domain lineage or validation gate failed")
    if (not closure.get("passed") or closure.get("field_domains_sha256") != domains_sha
            or closure.get("finalization_report_sha256") != final_sha):
        raise PayloadStagingError("material-field coordinate-closure gate failed")

    map_sources = {}
    for definition in fields["maps"].values():
        name = definition["output"]
        if Path(name).name != name or name in map_sources:
            raise PayloadStagingError(f"unsafe or duplicate field map name {name}")
        source = field_manifest.parent / name
        if source.is_symlink() or not source.is_file() or _sha256(source) != definition["output_sha256"]:
            raise PayloadStagingError(f"field map checksum failed: {name}")
        map_sources[name] = source
    elements = [_normalise_element(item, map_sources) for item in domains["elements"]]
    names = [item["name"] for item in elements]
    if len(names) != len(set(names)) or len(elements) != domains.get("element_count"):
        raise PayloadStagingError("field elements are duplicated or count is inconsistent")

    geometry_relative = Path("PhysicsTools/ShiftLssGeometry/data") / payload_name / geometry_gdml.name
    map_root_relative = Path("PhysicsTools/ShiftMuonSegments/data/lss") / payload_name
    python_relative = Path("PhysicsTools/ShiftMuonSegments/python") / f"{python_module}.py"
    cff = render_field_cff(elements, map_root_relative.as_posix(), function_name, domains_sha)
    compile(cff, str(python_relative), "exec")

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{output_dir.name}.", dir=output_dir.parent) as temporary:
        stage = Path(temporary)
        (stage / geometry_relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(geometry_gdml, stage / geometry_relative)
        (stage / map_root_relative).mkdir(parents=True, exist_ok=True)
        for name, source in map_sources.items():
            shutil.copyfile(source, stage / map_root_relative / name)
        (stage / python_relative).parent.mkdir(parents=True, exist_ok=True)
        (stage / python_relative).write_text(cff, encoding="utf-8")
        files = {}
        for path in sorted(stage.rglob("*")):
            if path.is_file():
                files[path.relative_to(stage).as_posix()] = {
                    "bytes": path.stat().st_size,
                    "sha256": _sha256(path),
                }
        manifest = {
            "schema": "shift-cms-lss-cmssw-staging-v1",
            "payload_name": payload_name,
            "staging_complete": True,
            "production_ready": False,
            "production_ready_reason": (
                "artifact staging passed; authoritative CMS alignment/interface, native references, "
                "CMSSW attachment, and an end-to-end pilot remain required"
            ),
            "geometry_file_in_path": geometry_relative.as_posix(),
            "field_python_module": f"PhysicsTools.ShiftMuonSegments.{python_module}",
            "field_factory": function_name,
            "artifact_origin_in_model_cm": closure["artifact_origin_in_model_cm"],
            "element_count": len(elements),
            "source_reports": {
                "finalization_report_sha256": final_sha,
                "root_audit_sha256": _sha256(root_audit),
                "material_audit_sha256": _sha256(material_audit),
                "field_manifest_sha256": fields_sha,
                "field_domains_sha256": domains_sha,
                "coordinate_audit_sha256": _sha256(coordinate_audit),
            },
            "unresolved_native_includes_not_consumed": fields[
                "payload_dependency_closure"
            ]["unresolved_includes_not_consumed"],
            "files": files,
            "remaining_gates": [
                "Authoritative model-to-CMS basis, side, origin, and signed-field review.",
                "Owner-reviewed LSS/CMS geometry interface and protected-volume boundary.",
                "Native FLUKA material and signed field/integrated-bending references.",
                "Checksummed CMSSW installation with overlap-enabled attachment.",
                "Bounded end-to-end material, field, and combined transport pilot.",
            ],
        }
        (stage / "payload_manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        stage.rename(output_dir)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--geometry-gdml", type=Path, required=True)
    parser.add_argument("--finalization-report", type=Path, required=True)
    parser.add_argument("--root-audit", type=Path, required=True)
    parser.add_argument("--material-audit", type=Path, required=True)
    parser.add_argument("--field-manifest", type=Path, required=True)
    parser.add_argument("--field-domains", type=Path, required=True)
    parser.add_argument("--coordinate-audit", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--payload-name", required=True)
    parser.add_argument("--python-module", required=True)
    parser.add_argument("--function-name", required=True)
    args = parser.parse_args(argv)
    try:
        report = stage_payload(
            geometry_gdml=args.geometry_gdml,
            finalization_report=args.finalization_report,
            root_audit=args.root_audit,
            material_audit=args.material_audit,
            field_manifest=args.field_manifest,
            field_domains=args.field_domains,
            coordinate_audit=args.coordinate_audit,
            output_dir=args.output_dir,
            payload_name=args.payload_name,
            python_module=args.python_module,
            function_name=args.function_name,
        )
    except (PayloadStagingError, KeyError, OSError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(
        f"staged {len(report['files'])} files and {report['element_count']} field elements; "
        "production_ready=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
