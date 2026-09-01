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
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

from fluka_region_preflight import (
    classify_raw_regions,
    resolve_raw_region_classifications,
)


class ProxyModelError(ValueError):
    pass


RAW_ZONE_AABB_PADDING_MM = 1.0e-3


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
        matrix = transform.to4DMatrix()
        lower, upper = _getBoundingBox(
            cell_region.mesh(), matrix[:3, :3], list(matrix[:3, 3]), cell_region.name
        )
        return fluka.AABB(lower, upper)

    converter_module._getTransformedCellRegionAABB = transformed_cell_region_aabb
    return original


def _aabb_contains(outer, inner, tolerance_mm=0.0):
    return all(
        outer.lower[axis] - tolerance_mm <= inner.lower[axis]
        and inner.upper[axis] <= outer.upper[axis] + tolerance_mm
        for axis in range(3)
    )


def _source_bound_clip_region_names(region_names, parking_regions):
    """Return only parked prototypes that need a finite pre-lattice clip."""

    return set(region_names) & set(parking_regions)


def install_lattice_placement_workaround(
    converter_module,
    source_bounds_mm,
    report,
    containment_tolerance_mm=0.01,
):
    """Instantiate FLUKA parking prototypes at their physical lattice cells.

    LineBuilder stores reusable element prototypes inside the explicit PARKr
    reservoir.  A LATTICE transform maps a physical cell into that reservoir.
    pyg4ometry 1.4.4 both double-transforms the cell AABB and uses a
    surface-only intersection predicate, so contained prototypes are missed.
    This compatibility implementation uses source-audited AABBs only as a
    conservative preselection, intersects every candidate with the exact cell
    solid, and places each result with the exact inverse lattice transform.
    Thus an AABB false positive becomes an empty Boolean rather than material.
    """

    import numpy as np
    from pyg4ometry import config, fluka, geant4, transformation

    if containment_tolerance_mm <= 0.0:
        raise ValueError("containment_tolerance_mm must be positive")
    try:
        source_bounds = {
            name: fluka.AABB(bounds[0], bounds[1])
            for name, bounds in source_bounds_mm.items()
        }
        parking_bounds = source_bounds["PARKr"]
    except (KeyError, TypeError, ValueError) as error:
        raise ProxyModelError("source bounds must contain a valid PARKr entry") from error

    parking_regions = {
        name
        for name, bounds in source_bounds.items()
        if name != "PARKr"
        and _aabb_contains(parking_bounds, bounds, containment_tolerance_mm)
    }
    if not parking_regions:
        raise ProxyModelError("source bounds identify no PARKr prototype regions")

    original = converter_module._convertLatticeCells

    def affine(rotation, translation):
        matrix = np.identity(4)
        matrix[:3, :3] = rotation
        matrix[:3, 3] = translation
        return matrix

    def placement_affine(placement):
        active_rotation = transformation.reverse(placement.rotation.eval())
        return affine(
            transformation.tbxyz2matrix(active_rotation),
            placement.position.eval(),
        )

    def lattice_cell_conversion(
        geant4_registry,
        fluka_registry,
        world_logical_volume,
        region_zone_aabbs,
        region_names_to_lvs,
    ):
        original_region_placements = {
            placement.name[:-3]: placement
            for placement in world_logical_volume.daughterVolumes
            if placement.name.endswith("_pv")
            and placement.name[:-3] in region_names_to_lvs
        }
        missing_placements = sorted(
            set(region_names_to_lvs) - set(original_region_placements)
        )
        if missing_placements:
            raise ProxyModelError(
                "converted regions have no original physical placements: "
                + ", ".join(missing_placements)
            )

        source_bound_clip_count = 0
        source_bound_clip_regions = _source_bound_clip_region_names(
            region_names_to_lvs, parking_regions
        )
        for region_name, region_lv in region_names_to_lvs.items():
            # Mesh-derived AABBs are used for lattice candidate selection, but
            # curved CSG surfaces can extend beyond their chordal mesh bounds.
            # Clip only parked prototypes, which are
            # subsequently intersected with an exact physical lattice cell.
            # Clipping ordinary source regions can remove valid material at a
            # curved boundary (for example the R7/R7UPS B11 annulus).
            if region_name not in source_bound_clip_regions:
                continue
            bounds = source_bounds[region_name]
            dimensions = [
                float(bounds.upper[axis] - bounds.lower[axis])
                + 2.0 * containment_tolerance_mm
                for axis in range(3)
            ]
            centre = [
                0.5 * float(bounds.lower[axis] + bounds.upper[axis])
                for axis in range(3)
            ]
            bounds_solid = geant4.solid.Box(
                f"{region_name}_source_bounds_solid",
                *dimensions,
                geant4_registry,
                "mm",
            )
            region_to_model = placement_affine(
                original_region_placements[region_name]
            )
            bounds_to_model = affine(np.identity(3), centre)
            bounds_to_region_local = np.linalg.inv(region_to_model) @ bounds_to_model
            region_lv.solid = geant4.solid.Intersection(
                f"{region_name}_source_bounded_solid",
                region_lv.solid,
                bounds_solid,
                [
                    transformation.matrix2tbxyz(
                        bounds_to_region_local[:3, :3]
                    ),
                    list(bounds_to_region_local[:3, 3]),
                ],
                geant4_registry,
            )
            source_bound_clip_count += 1

        placement_details = []
        used_prototypes = set()
        clipped_count = 0
        for lattice_name, lattice in fluka_registry.latticeDict.items():
            transformed_cell_bounds = converter_module._getTransformedCellRegionAABB(
                lattice
            )
            physical_cell_bounds = fluka.AABB.fromMesh(lattice.cellRegion.mesh())
            candidates = sorted(
                name
                for name in parking_regions
                if name in region_names_to_lvs
                and converter_module._areAABBsOverlapping(
                    transformed_cell_bounds, source_bounds[name]
                )
            )
            if not candidates:
                raise ProxyModelError(
                    f"lattice {lattice_name} has no source-audited parking prototypes"
                )

            # Lattice-cell regions are intentionally omitted from the normal
            # Geant4 placement pass.  Convert a uniquely named, unplaced copy
            # here so it can be used as the exact clipping operand without
            # adding the cell material to the world.
            unique_cell = lattice.cellRegion.makeUnique(
                f"__{lattice_name}_lattice_cell", fluka.FlukaRegistry()
            )
            unique_cell.name = f"{lattice_name}_lattice_cell"
            cell_aabb_map = {
                body.name: physical_cell_bounds for body in unique_cell.bodies()
            }
            cell_solid = unique_cell.geant4Solid(
                geant4_registry, aabb=cell_aabb_map
            )
            cell_rotation = transformation.tbxyz2matrix(unique_cell.tbxyz())
            cell_centre = unique_cell.centre(aabb=cell_aabb_map)
            cell_to_model = affine(cell_rotation, cell_centre)
            physical_to_prototype = lattice.getTransform().to4DMatrix()
            cell_to_prototype = physical_to_prototype @ cell_to_model

            lattice_records = []
            for prototype_name in candidates:
                prototype_lv = region_names_to_lvs[prototype_name]
                prototype_to_model = placement_affine(
                    original_region_placements[prototype_name]
                )
                cell_to_prototype_local = (
                    np.linalg.inv(prototype_to_model) @ cell_to_prototype
                )
                protrusion_mm = max(
                    max(
                        transformed_cell_bounds.lower[axis]
                        - source_bounds[prototype_name].lower[axis],
                        source_bounds[prototype_name].upper[axis]
                        - transformed_cell_bounds.upper[axis],
                        0.0,
                    )
                    for axis in range(3)
                )
                clipped_solid = geant4.solid.Intersection(
                    f"{lattice_name}__{prototype_name}_lattice_clip_solid",
                    prototype_lv.solid,
                    cell_solid,
                    [
                        transformation.matrix2tbxyz(
                            cell_to_prototype_local[:3, :3]
                        ),
                        list(cell_to_prototype_local[:3, 3]),
                    ],
                    geant4_registry,
                )
                original_do_meshing = config.doMeshing
                config.doMeshing = False
                try:
                    placed_lv = geant4.LogicalVolume(
                        clipped_solid,
                        prototype_lv.material,
                        f"{lattice_name}__{prototype_name}_lattice_clip_lv",
                        geant4_registry,
                    )
                finally:
                    config.doMeshing = original_do_meshing
                clipped_count += 1

                prototype_to_physical = (
                    np.linalg.inv(physical_to_prototype) @ prototype_to_model
                )
                active_rotation = transformation.matrix2tbxyz(
                    prototype_to_physical[:3, :3]
                )
                geant4.PhysicalVolume(
                    list(transformation.reverse(active_rotation)),
                    list(prototype_to_physical[:3, 3]),
                    placed_lv,
                    f"{lattice_name}__{prototype_name}_lattice_pv",
                    world_logical_volume,
                    geant4_registry,
                )
                used_prototypes.add(prototype_name)
                lattice_records.append(
                    {
                        "prototype": prototype_name,
                        "clipped_to_cell": True,
                        "source_aabb_protrusion_mm": float(protrusion_mm),
                    }
                )
            placement_details.append(
                {
                    "lattice": lattice_name,
                    "prototype_count": len(lattice_records),
                    "physical_cell_bounds_mm": [
                        [float(value) for value in physical_cell_bounds.lower],
                        [float(value) for value in physical_cell_bounds.upper],
                    ],
                    "prototype_cell_bounds_mm": [
                        [float(value) for value in transformed_cell_bounds.lower],
                        [float(value) for value in transformed_cell_bounds.upper],
                    ],
                    "prototypes": lattice_records,
                }
            )

        report.update(
            {
                "parking_region": "PARKr",
                "source_bound_clip_count": source_bound_clip_count,
                "source_bound_clip_padding_mm": containment_tolerance_mm,
                "parking_prototype_count": len(parking_regions),
                "parking_prototypes": sorted(parking_regions),
                "lattice_count": len(placement_details),
                "lattice_placement_count": sum(
                    item["prototype_count"] for item in placement_details
                ),
                "clipped_lattice_placement_count": clipped_count,
                "used_parking_prototype_count": len(used_prototypes),
                "unused_parking_prototypes": sorted(parking_regions - used_prototypes),
                "lattices": placement_details,
            }
        )

    converter_module._convertLatticeCells = lattice_cell_conversion
    return original


def _install_raw_zone_aabb_fallback(
    converter_module,
    raw_preflight,
    padding_mm=RAW_ZONE_AABB_PADDING_MM,
):
    """Reuse independently meshed raw-zone bounds when CGAL loses a zone.

    The fallback only supplies the finite minimisation box.  The converted
    Boolean still comes from the length-safety-adjusted FLUKA source.
    """

    from pyg4ometry.fluka import AABB

    if padding_mm <= 0.0:
        raise ValueError("padding_mm must be positive")
    secondary = raw_preflight["secondary_classification"]
    independently_non_null = set(secondary["non_null_regions"])
    raw_zone_bounds = secondary.get("zone_bounds_mm", {})
    missing = sorted(independently_non_null - set(raw_zone_bounds))
    if missing:
        raise ProxyModelError(
            "secondary preflight omitted per-zone bounds for non-null regions: "
            + ", ".join(missing)
        )

    original = converter_module._getRegionZoneAABBs
    fallback_details = []

    def region_zone_aabbs(flukareg, regions, quadric_region_aabbs):
        result = original(flukareg, regions, quadric_region_aabbs)
        selected = set(regions)
        for name in secondary["non_null_regions"]:
            if name not in selected or name not in result:
                continue
            computed = result[name]
            independent = raw_zone_bounds[name]
            if len(computed) != len(independent):
                raise ProxyModelError(
                    f"zone-count mismatch for {name}: converter={len(computed)}, "
                    f"secondary={len(independent)}"
                )
            replacements = 0
            resolved = []
            for computed_bound, independent_bound in zip(computed, independent):
                if computed_bound is not None or independent_bound is None:
                    resolved.append(computed_bound)
                    continue
                lower, upper = independent_bound
                resolved.append(
                    AABB(
                        [value - padding_mm for value in lower],
                        [value + padding_mm for value in upper],
                    )
                )
                replacements += 1
            if replacements:
                result[name] = resolved
                fallback_details.append(
                    {"name": name, "replaced_zone_count": replacements}
                )
        return result

    converter_module._getRegionZoneAABBs = region_zone_aabbs
    return original, fallback_details


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
        "defined_materials": sorted(defined),
        "referenced_material_count": len(referenced),
        "referenced_materials": sorted(referenced),
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


def write_json_atomic(path, payload):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def run_secondary_region_preflight(
    normalized_path,
    region_names,
    temporary_dir,
    timeout_seconds,
):
    regions_path = Path(temporary_dir) / "pycsg_regions.json"
    output_path = Path(temporary_dir) / "pycsg_preflight.json"
    regions_path.write_text(
        json.dumps(list(region_names)) + "\n",
        encoding="utf-8",
    )
    command = [
        sys.executable,
        str(Path(__file__).with_name("fluka_region_preflight_worker.py")),
        "--normalized-deck",
        str(normalized_path),
        "--regions-json",
        str(regions_path),
        "--output",
        str(output_path),
        "--timeout-seconds",
        str(timeout_seconds),
    ]
    process_timeout = timeout_seconds * max(1, len(region_names)) + 300.0
    try:
        result = subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=process_timeout,
        )
    except subprocess.TimeoutExpired as error:
        raise ProxyModelError(
            "pycsg raw-region fallback exceeded its aggregate timeout"
        ) from error
    if result.returncode:
        diagnostic = (result.stdout + result.stderr)[-4000:]
        raise ProxyModelError(
            "pycsg raw-region fallback failed with status "
            f"{result.returncode}: {diagnostic}"
        )
    secondary = json.loads(output_path.read_text(encoding="utf-8"))
    if secondary.get("backend") != "pycsg":
        raise ProxyModelError("secondary raw-region worker did not use pycsg")
    return secondary


def summarize_preflight_omissions(region_coverage, raw_preflight):
    blackhole = set(raw_preflight["blackhole_regions"])
    confirmed_null = set(raw_preflight["source_null_regions"])
    deferred = set(raw_preflight["deferred_null_validation_regions"])
    omitted = region_coverage["omitted_regions"]
    omitted_set = set(omitted)
    deferred_omitted = deferred & omitted_set
    deferred_converted = sorted(deferred - omitted_set)
    source_null = confirmed_null | deferred_omitted
    unexpected = sorted(omitted_set - blackhole - source_null)
    return {
        "audited_region_count": len(omitted),
        "intentionally_omitted_blackhole_region_count": len(blackhole),
        "intentionally_omitted_blackhole_regions": sorted(blackhole),
        "confirmed_source_null_region_count": len(confirmed_null),
        "confirmed_source_null_regions": sorted(confirmed_null),
        "deferred_source_null_region_count": len(deferred_omitted),
        "deferred_source_null_regions": sorted(deferred_omitted),
        "source_null_region_count": len(source_null),
        "source_null_regions": sorted(source_null),
        "deferred_region_conversion_failure_count": len(deferred_converted),
        "deferred_region_conversion_failures": deferred_converted,
        "unexpected_omitted_region_count": len(unexpected),
        "unexpected_omitted_regions": unexpected,
        "details": [
            {
                "name": name,
                "reason": (
                    "blackhole"
                    if name in blackhole
                    else "confirmed_source_null"
                    if name in confirmed_null
                    else "deferred_source_null"
                    if name in deferred_omitted
                    else "unexpected"
                ),
            }
            for name in omitted
        ],
    }


def convert_geometry(
    model_dir,
    output_dir,
    regions=None,
    lattice_aabb_workaround=False,
    raw_region_preflight=False,
    region_timeout_seconds=300.0,
):
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
    if raw_region_preflight and region_timeout_seconds <= 0.0:
        raise ProxyModelError("region_timeout_seconds must be positive")
    raw_preflight = None

    with tempfile.TemporaryDirectory(dir=output_dir) as temporary_dir:
        temporary_dir = Path(temporary_dir)
        normalized_path = temporary_dir / "lhc_ir1_exp_b2.normalized.inp"
        normalization = normalized_deck(source_deck, normalized_path)
        conversion_log = temporary_dir / "pyg4ometry_conversion.log"
        with conversion_log.open("w", encoding="utf-8") as log, redirect_stdout(log):
            reader = Reader(str(normalized_path))
            fluka_registry = reader.flukaregistry
            if regions is not None:
                unknown_regions = sorted(set(regions) - set(fluka_registry.regionDict))
                if unknown_regions:
                    raise ProxyModelError("unknown FLUKA regions: " + ", ".join(unknown_regions))
            requested_regions = (
                list(regions) if regions is not None else list(fluka_registry.regionDict)
            )
            conversion_regions = regions
            if raw_region_preflight:
                primary_preflight = classify_raw_regions(
                    fluka_registry,
                    requested_regions,
                    timeout_seconds=region_timeout_seconds,
                    progress_every=100,
                )
                ambiguous_regions = (
                    primary_preflight["source_null_regions"]
                    + [
                        item["name"]
                        for item in primary_preflight["evaluation_errors"]
                    ]
                )
                if ambiguous_regions:
                    secondary_preflight = run_secondary_region_preflight(
                        normalized_path,
                        ambiguous_regions,
                        temporary_dir,
                        region_timeout_seconds,
                    )
                else:
                    secondary_preflight = {
                        "non_null_regions": [],
                        "source_null_regions": [],
                        "evaluation_errors": [],
                        "backend": "pycsg",
                    }
                raw_preflight = resolve_raw_region_classifications(
                    primary_preflight,
                    secondary_preflight,
                    requested_regions,
                )
                write_json_atomic(
                    output_dir / "raw_region_preflight.json",
                    raw_preflight,
                )
                if raw_preflight["evaluation_errors"]:
                    failures = "; ".join(
                        f"{item['name']}: {item['error']}"
                        for item in raw_preflight["evaluation_errors"]
                    )
                    raise ProxyModelError(
                        "raw FLUKA region preflight failed before length safety: "
                        + failures
                    )
                conversion_regions = raw_preflight["conversion_candidate_regions"]
            converter_module = importlib.import_module("pyg4ometry.convert.fluka2Geant4")
            original_lattice_aabb = None
            original_region_zone_aabbs = None
            raw_zone_aabb_fallbacks = []
            if lattice_aabb_workaround:
                original_lattice_aabb = _install_lattice_aabb_workaround(converter_module)
            if raw_preflight is not None:
                (
                    original_region_zone_aabbs,
                    raw_zone_aabb_fallbacks,
                ) = _install_raw_zone_aabb_fallback(
                    converter_module,
                    raw_preflight,
                )
            try:
                geant4_registry = fluka2Geant4(fluka_registry, regions=conversion_regions)
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
                if original_region_zone_aabbs is not None:
                    converter_module._getRegionZoneAABBs = original_region_zone_aabbs
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
        if raw_preflight is not None:
            omitted_region_audit = summarize_preflight_omissions(
                region_coverage, raw_preflight
            )
        else:
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
                "raw_region_preflight": raw_preflight,
                "raw_zone_aabb_fallback": {
                    "padding_mm": RAW_ZONE_AABB_PADDING_MM,
                    "region_count": len(raw_zone_aabb_fallbacks),
                    "regions": raw_zone_aabb_fallbacks,
                },
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
