#!/usr/bin/env python3

"""Validation and conversion helpers for the frozen IR1 FLUKA proxy."""

from dataclasses import asdict, dataclass
from contextlib import redirect_stdout
from copy import deepcopy
import hashlib
import importlib
import json
from pathlib import Path
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET


class ProxyModelError(ValueError):
    pass


@dataclass(frozen=True)
class FieldAssignment:
    region_from: int
    region_to: int
    region_step: int
    field_scale_tesla: float
    field_type: str
    offset_cm: tuple
    rotation_radians: tuple


def load_checksums(model_dir):
    checksums = {}
    manifest = Path(model_dir) / "SOURCE_SHA256SUMS"
    for line_number, line in enumerate(manifest.read_text(encoding="ascii").splitlines(), 1):
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) != 2 or len(parts[0]) != 64:
            raise ProxyModelError(f"{manifest}:{line_number}: malformed checksum entry")
        checksums[parts[1]] = parts[0]
    if not checksums:
        raise ProxyModelError(f"{manifest}: empty checksum manifest")
    return checksums


def verify_source_bundle(model_dir):
    model_dir = Path(model_dir)
    checksums = load_checksums(model_dir)
    observed = {}
    for relative_path, expected in checksums.items():
        path = model_dir / relative_path
        if not path.is_file():
            raise ProxyModelError(f"missing proxy-model asset: {path}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != expected:
            raise ProxyModelError(
                f"checksum mismatch for {path}: expected {expected}, observed {digest}"
            )
        observed[relative_path] = digest
    return observed


def _parse_usrgcall(line, location):
    tokens = line.split()
    if len(tokens) != 8 or tokens[0] != "USRGCALL":
        raise ProxyModelError(f"{location}: expected USRGCALL with six values and SDUM")
    try:
        values = tuple(float(token.replace("D", "E")) for token in tokens[1:7])
    except ValueError as error:
        raise ProxyModelError(f"{location}: invalid USRGCALL numeric value") from error
    return values, tokens[7]


def extract_field_assignments(deck_path):
    deck_path = Path(deck_path)
    assignments = []
    pending = None
    for line_number, line in enumerate(deck_path.read_text(encoding="ascii").splitlines(), 1):
        if not line.startswith("USRGCALL"):
            continue
        values, field_type = _parse_usrgcall(line, f"{deck_path}:{line_number}")
        if field_type == "&":
            if pending is None:
                raise ProxyModelError(f"{deck_path}:{line_number}: orphan USRGCALL continuation")
            primary, primary_type, primary_line = pending
            assignments.append(
                FieldAssignment(
                    region_from=int(primary[1]),
                    region_to=int(primary[2]) if int(primary[2]) else int(primary[1]),
                    region_step=int(primary[3]) if int(primary[3]) > 0 else 1,
                    field_scale_tesla=primary[4],
                    field_type=primary_type,
                    offset_cm=values[0:3],
                    rotation_radians=values[3:6],
                )
            )
            pending = None
        else:
            if pending is not None:
                raise ProxyModelError(
                    f"{deck_path}:{line_number}: field card before continuation of line {pending[2]}"
                )
            pending = (values, field_type, line_number)
    if pending is not None:
        raise ProxyModelError(f"{deck_path}:{pending[2]}: missing USRGCALL continuation")
    if not assignments:
        raise ProxyModelError(f"{deck_path}: no magnetic-field assignments found")
    return assignments


def validate_field_assets(model_dir, assignments):
    source_dir = Path(model_dir) / "source"
    builtins = {"CONST", "KICKSIN"}
    missing = sorted(
        {
            assignment.field_type
            for assignment in assignments
            if assignment.field_type not in builtins
            and not (source_dir / f"{assignment.field_type}.dat").is_file()
        }
    )
    if missing:
        raise ProxyModelError("missing field maps: " + ", ".join(missing))


def normalized_deck(source_path, output_path):
    """Apply minimal, reported syntax normalization for pyg4ometry."""

    source_path = Path(source_path)
    removed = []
    fortran_exponents = []
    output_lines = []
    for line_number, line in enumerate(source_path.read_text(encoding="ascii").splitlines(True), 1):
        if line.startswith("COMPOUND") and not line[10:60].strip() and line[70:80].strip():
            removed.append({"source_line": line_number, "text": line.rstrip("\n")})
            continue
        normalized_line, replacement_count = re.subn(
            r"(?<![A-Za-z0-9_.])([+-]?(?:\d+(?:\.\d*)?|\.\d+))D([+-]?\d+)",
            r"\1E\2",
            line,
        )
        if replacement_count:
            fortran_exponents.append(
                {
                    "source_line": line_number,
                    "original": line.rstrip("\n"),
                    "normalized": normalized_line.rstrip("\n"),
                }
            )
        line = normalized_line
        output_lines.append(line)
    Path(output_path).write_text("".join(output_lines), encoding="ascii")
    return {
        "removed_noop_cards": removed,
        "fortran_exponent_replacements": fortran_exponents,
    }


def write_field_manifest(assignments, output_path):
    payload = {
        "schema": "shift-ir1-proxy-fields",
        "schema_version": 1,
        "model_status": "provisional-ir1-atlas-proxy",
        "coordinate_transform_to_cms": None,
        "assignments": [asdict(assignment) for assignment in assignments],
        "limitations": [
            "Region numbers refer to the frozen FLUKA deck and require geometry-preserving translation.",
            "No IR1-to-IR5 or ATLAS-to-CMS coordinate transform is defined.",
            "This manifest does not install a CMSSW MagneticField implementation.",
        ],
    }
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def extract_and_write_field_manifest(model_dir, output_path):
    model_dir = Path(model_dir)
    verify_source_bundle(model_dir)
    source_deck = model_dir / "source" / "lhc_ir1_exp_b2.inp"
    assignments = extract_field_assignments(source_deck)
    validate_field_assets(model_dir, assignments)
    write_field_manifest(assignments, output_path)
    return assignments


def _install_lattice_aabb_workaround(converter_module):
    """Use the FLUKA cell mesh for the lattice overlap-preselection AABB."""

    from pyg4ometry import fluka
    from pyg4ometry.visualisation.Mesh import _getBoundingBox

    original = converter_module._getTransformedCellRegionAABB

    def transformed_cell_region_aabb(lattice):
        transform = lattice.getTransform()
        cell_region = deepcopy(lattice.cellRegion)
        rotation = transform.leftMultiplyRotation(cell_region.rotation())
        centre = list(transform.leftMultiplyVector(cell_region.centre()))
        lower, upper = _getBoundingBox(
            cell_region.mesh(), rotation, centre, cell_region.name
        )
        return fluka.AABB(lower, upper)

    converter_module._getTransformedCellRegionAABB = transformed_cell_region_aabb
    return original


def expand_predefined_materials(registry):
    """Replace NIST handles with explicit compositions for ROOT/DD4hep GDML."""

    from pyg4ometry.geant4._Material import nist_material_2geant4Material

    predefined_by_name = {
        material.name: material
        for material in registry.materialDict.values()
        if getattr(material, "type", None) == "nist"
    }
    explicit_by_name = {
        name: nist_material_2geant4Material(name)
        for name in predefined_by_name
    }

    for logical_volume in registry.logicalVolumeDict.values():
        if getattr(logical_volume, "type", None) != "logical":
            continue
        material = logical_volume.material
        if getattr(material, "type", None) == "nist":
            logical_volume.material = explicit_by_name[material.name]

    for material in list(registry.materialDict.values()):
        components = getattr(material, "components", None)
        if not components:
            continue
        material.components = [
            (
                explicit_by_name.get(component.name, component)
                if getattr(component, "type", None) == "nist"
                else component,
                fraction,
                fraction_type,
            )
            for component, fraction, fraction_type in components
        ]

    for original_name in predefined_by_name:
        registry.materialDict.pop(original_name, None)
    for explicit in explicit_by_name.values():
        registry.materialDict[explicit.name] = explicit
    return dict(
        sorted(
            (original_name, explicit.name)
            for original_name, explicit in explicit_by_name.items()
        )
    )


def audit_gdml_material_references(gdml_path):
    root = ET.parse(gdml_path).getroot()
    materials = root.find("materials")
    structure = root.find("structure")
    if materials is None or structure is None:
        raise ProxyModelError("GDML requires materials and structure sections")
    defined = {
        material.attrib["name"]
        for material in materials.findall("material")
        if "name" in material.attrib
    }
    referenced = {
        reference.attrib["ref"]
        for reference in structure.iter("materialref")
        if "ref" in reference.attrib
    }
    undefined = sorted(referenced - defined)
    return {
        "defined_material_count": len(defined),
        "referenced_material_count": len(referenced),
        "undefined_material_count": len(undefined),
        "undefined_materials": undefined,
    }


def audit_omitted_region_geometry(fluka_registry, omitted_region_names):
    details = []
    for name in omitted_region_names:
        region = fluka_registry.regionDict[name]
        try:
            mesh = region.mesh()
            detail = {
                "name": name,
                "zone_count": len(region.zones),
                "csg": region.dumps(),
                "is_null": bool(mesh.isNull()),
                "vertex_count": int(mesh.vertexCount()),
                "polygon_count": int(mesh.polygonCount()),
                "volume_mm3": float(mesh.volume()),
                "evaluation_error": None,
            }
        except Exception as error:
            detail = {
                "name": name,
                "zone_count": len(region.zones),
                "csg": region.dumps(),
                "is_null": None,
                "vertex_count": None,
                "polygon_count": None,
                "volume_mm3": None,
                "evaluation_error": f"{type(error).__name__}: {error}",
            }
        details.append(detail)

    source_null_regions = [item["name"] for item in details if item["is_null"] is True]
    unexpected_omissions = [item["name"] for item in details if item["is_null"] is not True]
    return {
        "audited_region_count": len(details),
        "source_null_region_count": len(source_null_regions),
        "source_null_regions": source_null_regions,
        "unexpected_omitted_region_count": len(unexpected_omissions),
        "unexpected_omitted_regions": unexpected_omissions,
        "details": details,
    }


def summarize_region_coverage(source_region_names, selected_regions, logical_volume_names):
    source_region_names = list(source_region_names)
    requested_region_names = (
        list(selected_regions) if selected_regions is not None else source_region_names
    )
    logical_volume_names = set(logical_volume_names)
    converted_region_names = [
        name for name in requested_region_names if f"{name}_lv" in logical_volume_names
    ]
    omitted_region_names = sorted(set(requested_region_names) - set(converted_region_names))
    return {
        "source_region_count": len(source_region_names),
        "requested_region_count": len(requested_region_names),
        "unselected_region_count": len(source_region_names) - len(requested_region_names),
        "converted_region_count": len(converted_region_names),
        "converted_regions": converted_region_names,
        "omitted_region_count": len(omitted_region_names),
        "omitted_regions": omitted_region_names,
        "selected_regions": list(selected_regions) if selected_regions is not None else None,
    }


def convert_geometry(model_dir, output_dir, regions=None, lattice_aabb_workaround=False):
    try:
        import pyg4ometry
        from pyg4ometry.convert import fluka2Geant4
        from pyg4ometry.fluka import Reader
        from pyg4ometry.gdml import Writer
    except ImportError as error:
        raise ProxyModelError("pyg4ometry is required for FLUKA-to-GDML conversion") from error

    model_dir = Path(model_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    source_deck = model_dir / "source" / "lhc_ir1_exp_b2.inp"
    assignments = extract_field_assignments(source_deck)
    validate_field_assets(model_dir, assignments)
    checksums = verify_source_bundle(model_dir)

    with tempfile.TemporaryDirectory(dir=output_dir) as temporary_dir:
        temporary_dir = Path(temporary_dir)
        normalized_path = temporary_dir / "lhc_ir1_exp_b2.normalized.inp"
        normalization = normalized_deck(source_deck, normalized_path)
        conversion_log = temporary_dir / "pyg4ometry_conversion.log"
        with conversion_log.open("w", encoding="utf-8") as log, redirect_stdout(log):
            reader = Reader(str(normalized_path))
            fluka_registry = reader.flukaregistry
            if regions:
                unknown_regions = sorted(set(regions) - set(fluka_registry.regionDict))
                if unknown_regions:
                    raise ProxyModelError("unknown FLUKA regions: " + ", ".join(unknown_regions))
            converter_module = importlib.import_module("pyg4ometry.convert.fluka2Geant4")
            original_lattice_aabb = None
            if lattice_aabb_workaround:
                original_lattice_aabb = _install_lattice_aabb_workaround(converter_module)
            try:
                geant4_registry = fluka2Geant4(fluka_registry, regions=regions)
            except AttributeError as error:
                if "getBoundingBox" not in str(error):
                    raise
                raise ProxyModelError(
                    "pyg4ometry could not mesh a FLUKA lattice cell while determining its "
                    "Geant4 bounding box; no GDML was produced. Re-run with the explicit "
                    "lattice-AABB compatibility mode or update pyg4ometry; do not drop "
                    "LATTICE cards."
                ) from error
            finally:
                if original_lattice_aabb is not None:
                    converter_module._getTransformedCellRegionAABB = original_lattice_aabb
        geant4_registry.getWorldVolume().clipSolid()
        explicit_predefined_materials = expand_predefined_materials(geant4_registry)
        temporary_gdml = temporary_dir / "lhc_ir1_atlas_proxy.gdml"
        writer = Writer()
        writer.addDetector(geant4_registry)
        writer.write(str(temporary_gdml))
        material_reference_audit = audit_gdml_material_references(temporary_gdml)
        if material_reference_audit["undefined_material_count"]:
            raise ProxyModelError(
                "GDML contains undefined material references: "
                + ", ".join(material_reference_audit["undefined_materials"])
            )

        final_gdml = output_dir / "lhc_ir1_atlas_proxy.gdml"
        final_fields = output_dir / "lhc_ir1_atlas_proxy_fields.json"
        final_report = output_dir / "conversion_report.json"
        final_log = output_dir / conversion_log.name
        write_field_manifest(assignments, temporary_dir / final_fields.name)
        region_coverage = summarize_region_coverage(
            fluka_registry.regionDict, regions, geant4_registry.logicalVolumeDict
        )
        omitted_region_audit = audit_omitted_region_geometry(
            fluka_registry, region_coverage["omitted_regions"]
        )
        report = {
            "schema": "shift-ir1-proxy-conversion",
            "schema_version": 1,
            "model_status": "provisional-ir1-atlas-proxy",
            "pyg4ometry_version": getattr(pyg4ometry, "__version__", "unknown"),
            "source_sha256": checksums,
            "normalization": normalization,
            "pyg4ometry_lattice_aabb_workaround": lattice_aabb_workaround,
            "geometry": {
                "gdml": final_gdml.name,
                "world_volume": geant4_registry.getWorldVolume().name,
                **region_coverage,
                "omitted_region_audit": omitted_region_audit,
                "logical_volume_count": len(geant4_registry.logicalVolumeDict),
                "solid_count": len(geant4_registry.solidDict),
                "material_count": len(geant4_registry.materialDict),
                "explicit_predefined_materials": explicit_predefined_materials,
                "material_reference_audit": material_reference_audit,
            },
            "magnetic_field": {
                "manifest": final_fields.name,
                "implemented_in_gdml": False,
                "assignment_count": len(assignments),
            },
            "coordinate_transform_to_cms": None,
            "cmssw_geometry_integration_validated": False,
            "cmssw_reconstruction_backpropagation_validated": False,
        }
        (temporary_dir / final_report.name).write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        shutil.move(temporary_gdml, final_gdml)
        shutil.move(temporary_dir / final_fields.name, final_fields)
        shutil.move(temporary_dir / final_report.name, final_report)
        shutil.move(conversion_log, final_log)
    return report
