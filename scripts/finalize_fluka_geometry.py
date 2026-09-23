#!/usr/bin/env python3

"""Turn a validated full FLUKA conversion into a bounded physical artifact.

FLUKA lattice decks commonly keep reusable component definitions in a remote
source-only reservoir.  The converter must retain those ordinary regions long
enough to construct each lattice copy, but their original parked placements do
not belong to the physical machine.  This finalizer removes only an explicitly
named and bounds-audited reservoir, preserves every converted lattice copy, and
records the coordinate translation applied while tightening the GDML world.
"""

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
import xml.etree.ElementTree as ET


class FinalizationError(RuntimeError):
    pass


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path, description):
    path = Path(path)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FinalizationError(f"cannot read {description} {path}: {error}") from error


def write_json_atomic(path, payload):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def bounds_contain(outer, inner, tolerance_mm=0.01):
    return all(
        outer[0][axis] - tolerance_mm <= inner[0][axis]
        and inner[1][axis] <= outer[1][axis] + tolerance_mm
        for axis in range(3)
    )


def load_validated_inputs(conversion_report_path, bounds_report_path):
    conversion = read_json(conversion_report_path, "conversion report")
    if not conversion.get("conversion_passed") or not conversion.get("full_model_exported"):
        raise FinalizationError("finalization requires a passing full-model conversion")
    lattice = conversion.get("lattice_conversion")
    if not isinstance(lattice, dict) or not lattice.get("passed"):
        raise FinalizationError("finalization requires a passing lattice conversion")
    if lattice.get("placement_count") != sum(
        len(item.get("placements", [])) for item in lattice.get("lattices", [])
    ):
        raise FinalizationError("lattice placement count is internally inconsistent")

    bounds_report = read_json(bounds_report_path, "source-bounds report")
    if not bounds_report.get("passed"):
        raise FinalizationError("finalization requires a passing source-bounds report")
    bounds = bounds_report.get("bounds_mm")
    if not isinstance(bounds, dict) or not bounds:
        raise FinalizationError("source-bounds report has no region bounds")
    return conversion, bounds_report, bounds


def physical_partition(conversion, source_bounds, reservoir_region, tolerance_mm=0.01):
    if tolerance_mm < 0:
        raise FinalizationError("containment tolerance must be non-negative")
    if reservoir_region not in source_bounds:
        raise FinalizationError(
            f"prototype reservoir {reservoir_region} has no certified source bounds"
        )

    lattice = conversion["lattice_conversion"]
    lattice_cells = {item["cell"] for item in lattice["lattices"]}
    copied_prototypes = {
        placement["prototype"]
        for item in lattice["lattices"]
        for placement in item["placements"]
    }
    if not copied_prototypes:
        raise FinalizationError("lattice report contains no copied prototypes")
    missing_bounds = sorted(copied_prototypes - set(source_bounds))
    if missing_bounds:
        raise FinalizationError(
            "copied prototypes lack certified source bounds: " + ", ".join(missing_bounds)
        )

    reservoir_bounds = source_bounds[reservoir_region]
    outside = sorted(
        name
        for name in copied_prototypes
        if not bounds_contain(reservoir_bounds, source_bounds[name], tolerance_mm)
    )
    if outside:
        raise FinalizationError(
            "explicit reservoir does not contain every copied prototype: "
            + ", ".join(outside)
        )

    source_only = {
        name
        for name, bounds in source_bounds.items()
        if name == reservoir_region
        or bounds_contain(reservoir_bounds, bounds, tolerance_mm)
    }
    physical_cells_in_reservoir = sorted(lattice_cells & source_only)
    if physical_cells_in_reservoir:
        raise FinalizationError(
            "prototype reservoir contains physical lattice cells: "
            + ", ".join(physical_cells_in_reservoir)
        )

    coverage = conversion.get("geometry", {}).get("coverage", {})
    converted_regions = set(coverage.get("converted_regions", []))
    if len(converted_regions) != coverage.get("converted_region_count"):
        raise FinalizationError("converted-region coverage is internally inconsistent")
    removed_regions = source_only & converted_regions
    if not copied_prototypes <= removed_regions:
        missing = sorted(copied_prototypes - removed_regions)
        raise FinalizationError(
            "copied prototypes are not all removable ordinary regions: " + ", ".join(missing)
        )

    retained_bounds = [
        bounds
        for name, bounds in source_bounds.items()
        if name in converted_regions and name not in removed_regions
    ]
    retained_bounds.extend(
        item["physical_cell_bounds_mm"] for item in lattice["lattices"]
    )
    if not retained_bounds:
        raise FinalizationError("physical partition has no retained bounds")
    lower = [min(bounds[0][axis] for bounds in retained_bounds) for axis in range(3)]
    upper = [max(bounds[1][axis] for bounds in retained_bounds) for axis in range(3)]
    return {
        "reservoir_region": reservoir_region,
        "reservoir_bounds_mm": reservoir_bounds,
        "containment_tolerance_mm": tolerance_mm,
        "copied_prototype_count": len(copied_prototypes),
        "source_only_region_count": len(source_only),
        "removed_regions": sorted(removed_regions),
        "retained_model_bounds_mm": [lower, upper],
    }


def _world_and_volumes(root):
    solids = root.find("solids")
    structure = root.find("structure")
    setup = root.find("setup")
    if solids is None or structure is None or setup is None:
        raise FinalizationError("GDML requires solids, structure, and setup sections")
    world_ref = setup.find("world")
    if world_ref is None or set(world_ref.attrib) != {"ref"}:
        raise FinalizationError("GDML setup has no unambiguous world reference")
    volumes = {
        volume.attrib["name"]: volume
        for volume in structure.findall("volume")
        if "name" in volume.attrib
    }
    try:
        world = volumes[world_ref.attrib["ref"]]
        world_solid_ref = world.find("solidref").attrib["ref"]
    except (KeyError, AttributeError) as error:
        raise FinalizationError("GDML world volume is malformed") from error
    world_solid = next(
        (solid for solid in solids if solid.attrib.get("name") == world_solid_ref), None
    )
    if world_solid is None or world_solid.tag != "box":
        raise FinalizationError("bounded finalization requires a box world solid")
    return solids, world, world_solid, volumes


def _placement_position_mm(physical):
    if physical.find("positionref") is not None:
        raise FinalizationError("same-material partition does not support positionref")
    position = physical.find("position")
    if position is None:
        return [0.0, 0.0, 0.0]
    if position.attrib.get("unit", "mm") != "mm":
        raise FinalizationError("same-material partition supports only mm placements")
    return [float(position.attrib.get(axis, "0")) for axis in ("x", "y", "z")]



def _placement_rotation(physical):
    if physical.find("rotationref") is not None:
        raise FinalizationError("model interface partition does not support rotationref")
    rotation = physical.find("rotation")
    if rotation is None:
        return [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    unit = rotation.attrib.get("unit", "rad")
    if unit not in ("rad", "deg"):
        raise FinalizationError(f"unsupported placement rotation unit {unit}")
    angles = [float(rotation.attrib.get(axis, "0")) for axis in ("x", "y", "z")]
    if unit == "deg":
        angles = [math.radians(value) for value in angles]
    x, y, z = angles
    sx, cx = math.sin(x), math.cos(x)
    sy, cy = math.sin(y), math.cos(y)
    sz, cz = math.sin(z), math.cos(z)
    # GDML stores the passive form of the x -> y -> z Tait-Bryan
    # rotation. Return the active local-to-parent matrix used for points.
    encoded = [
        [cz * cy, cz * sy * sx - sz * cx, cz * sy * cx + sz * sx],
        [sz * cy, sz * sy * sx + cz * cx, sz * sy * cx - cz * sx],
        [-sy, cy * sx, cy * cx],
    ]
    return _transpose(encoded)


def _transpose(matrix):
    return [[matrix[column][row] for column in range(3)] for row in range(3)]


def _matrix_vector(matrix, vector):
    return [sum(matrix[row][column] * vector[column] for column in range(3))
            for row in range(3)]


def _matrix_to_tbxyz(matrix):
    value = max(-1.0, min(1.0, matrix[2][0]))
    if abs(abs(value) - 1.0) > 1.0e-12:
        return [
            math.atan2(matrix[2][1], matrix[2][2]),
            math.asin(-value),
            math.atan2(matrix[1][0], matrix[0][0]),
        ]
    if value < 0.0:
        return [math.atan2(matrix[0][1], matrix[0][2]), math.pi / 2.0, 0.0]
    return [math.atan2(-matrix[0][1], -matrix[0][2]), -math.pi / 2.0, 0.0]


_LENGTH_TO_MM = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "um": 0.001}
_EMPTY_BOUNDS = object()


def _length_mm(value, unit):
    try:
        return float(value) * _LENGTH_TO_MM[unit]
    except (KeyError, TypeError, ValueError) as error:
        raise FinalizationError(f"unsupported or invalid GDML length {value!r} {unit!r}") from error


def _primitive_bounds(solid):
    """Return a conservative local AABB in mm, or None for an unknown primitive."""
    unit = solid.attrib.get("lunit", "mm")
    if solid.tag == "box":
        half = [_length_mm(solid.attrib[axis], unit) / 2.0 for axis in "xyz"]
    elif solid.tag in ("tube", "cone"):
        radius_names = ("rmax",) if solid.tag == "tube" else ("rmax1", "rmax2")
        radius = max(_length_mm(solid.attrib[name], unit) for name in radius_names)
        half = [radius, radius, _length_mm(solid.attrib["z"], unit) / 2.0]
    elif solid.tag == "orb":
        radius = _length_mm(solid.attrib["r"], unit)
        half = [radius, radius, radius]
    elif solid.tag == "eltube":
        half = [_length_mm(solid.attrib[axis], unit) for axis in ("dx", "dy", "dz")]
    else:
        return None
    if any(not math.isfinite(value) or value < 0.0 for value in half):
        raise FinalizationError(f"solid {solid.attrib.get('name')} has invalid dimensions")
    return [[-value for value in half], list(half)]


def _inline_transform(boolean, prefix):
    position_tag = prefix + "position" if prefix else "position"
    rotation_tag = prefix + "rotation" if prefix else "rotation"
    if boolean.find(position_tag + "ref") is not None:
        raise FinalizationError(f"Boolean {boolean.attrib.get('name')} uses {position_tag}ref")
    if boolean.find(rotation_tag + "ref") is not None:
        raise FinalizationError(f"Boolean {boolean.attrib.get('name')} uses {rotation_tag}ref")
    position = boolean.find(position_tag)
    if position is None:
        translation = [0.0, 0.0, 0.0]
    else:
        unit = position.attrib.get("unit", "mm")
        translation = [
            _length_mm(position.attrib.get(axis, "0"), unit) for axis in "xyz"
        ]
    rotation = boolean.find(rotation_tag)
    if rotation is None:
        matrix = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    else:
        unit = rotation.attrib.get("unit", "rad")
        if unit not in ("rad", "deg"):
            raise FinalizationError(
                f"Boolean {boolean.attrib.get('name')} uses unsupported angle unit {unit}"
            )
        angles = [float(rotation.attrib.get(axis, "0")) for axis in "xyz"]
        if unit == "deg":
            angles = [math.radians(value) for value in angles]
        x, y, z = angles
        sx, cx = math.sin(x), math.cos(x)
        sy, cy = math.sin(y), math.cos(y)
        sz, cz = math.sin(z), math.cos(z)
        encoded = [
            [cz * cy, cz * sy * sx - sz * cx, cz * sy * cx + sz * sx],
            [sz * cy, sz * sy * sx + cz * cx, sz * sy * cx - cz * sx],
            [-sy, cy * sx, cy * cx],
        ]
        matrix = _transpose(encoded)
    return matrix, translation


def _transform_bounds(bounds, transform):
    if bounds is None or bounds is _EMPTY_BOUNDS:
        return bounds
    matrix, translation = transform
    points = []
    for x in bounds[0][0], bounds[1][0]:
        for y in bounds[0][1], bounds[1][1]:
            for z in bounds[0][2], bounds[1][2]:
                rotated = _matrix_vector(matrix, [x, y, z])
                points.append([rotated[axis] + translation[axis] for axis in range(3)])
    return [
        [min(point[axis] for point in points) for axis in range(3)],
        [max(point[axis] for point in points) for axis in range(3)],
    ]


def _bounds_union(first, second):
    if first is None or second is None:
        return None
    return [
        [min(first[0][axis], second[0][axis]) for axis in range(3)],
        [max(first[1][axis], second[1][axis]) for axis in range(3)],
    ]


def _bounds_intersection(first, second):
    if first is _EMPTY_BOUNDS or second is _EMPTY_BOUNDS:
        return _EMPTY_BOUNDS
    if first is None or second is None:
        return None
    lower = [max(first[0][axis], second[0][axis]) for axis in range(3)]
    upper = [min(first[1][axis], second[1][axis]) for axis in range(3)]
    if any(lower[axis] > upper[axis] for axis in range(3)):
        return _EMPTY_BOUNDS
    return [lower, upper]


def _has_operand_transform(boolean, prefix):
    tags = (
        prefix + "position" if prefix else "position",
        prefix + "rotation" if prefix else "rotation",
        prefix + "positionref" if prefix else "positionref",
        prefix + "rotationref" if prefix else "rotationref",
    )
    return any(boolean.find(tag) is not None for tag in tags)


def simplify_empty_booleans(root):
    """Remove Boolean branches whose emptiness is proven by conservative AABBs."""
    solids = root.find("solids")
    structure = root.find("structure")
    if solids is None or structure is None:
        raise FinalizationError("GDML requires solids and structure sections")
    by_name = {}
    for solid in solids:
        name = solid.attrib.get("name")
        if not name or name in by_name:
            raise FinalizationError("GDML solid names are missing or duplicated")
        by_name[name] = solid

    results = {}
    active = set()
    proofs = []

    def evaluate(name):
        if name in results:
            return results[name]
        if name in active:
            raise FinalizationError(f"cyclic GDML solid reference at {name}")
        try:
            solid = by_name[name]
        except KeyError as error:
            raise FinalizationError(f"GDML references undefined solid {name}") from error
        active.add(name)
        if solid.tag not in ("union", "intersection", "subtraction"):
            result = (name, _primitive_bounds(solid))
        else:
            first = solid.find("first")
            second = solid.find("second")
            if first is None or second is None:
                raise FinalizationError(f"Boolean solid {name} has incomplete operands")
            first_name, first_bounds = evaluate(first.attrib["ref"])
            second_name, second_bounds = evaluate(second.attrib["ref"])
            if first_name is not None:
                first.attrib["ref"] = first_name
            if second_name is not None:
                second.attrib["ref"] = second_name
            first_bounds = _transform_bounds(
                first_bounds, _inline_transform(solid, "first")
            )
            second_bounds = _transform_bounds(
                second_bounds, _inline_transform(solid, "")
            )
            if solid.tag == "intersection":
                bounds = _bounds_intersection(first_bounds, second_bounds)
                result = (None, _EMPTY_BOUNDS) if bounds is _EMPTY_BOUNDS else (name, bounds)
                if bounds is _EMPTY_BOUNDS:
                    proofs.append({
                        "solid": name,
                        "operation": "intersection",
                        "result": "empty",
                        "reason": (
                            "empty_operand" if first_bounds is _EMPTY_BOUNDS
                            or second_bounds is _EMPTY_BOUNDS else "disjoint_conservative_aabbs"
                        ),
                    })
            elif solid.tag == "union":
                if first_bounds is _EMPTY_BOUNDS and second_bounds is _EMPTY_BOUNDS:
                    result = (None, _EMPTY_BOUNDS)
                    proofs.append({"solid": name, "operation": "union", "result": "empty",
                                   "reason": "both_operands_empty"})
                elif second_bounds is _EMPTY_BOUNDS:
                    if _has_operand_transform(solid, "first"):
                        raise FinalizationError(
                            f"cannot simplify {name}: surviving first operand is transformed"
                        )
                    result = (first_name, first_bounds)
                    proofs.append({"solid": name, "operation": "union",
                                   "result": "alias_first", "replacement": first_name,
                                   "reason": "second_operand_empty"})
                elif first_bounds is _EMPTY_BOUNDS:
                    if _has_operand_transform(solid, ""):
                        raise FinalizationError(
                            f"cannot simplify {name}: surviving second operand is transformed"
                        )
                    result = (second_name, second_bounds)
                    proofs.append({"solid": name, "operation": "union",
                                   "result": "alias_second", "replacement": second_name,
                                   "reason": "first_operand_empty"})
                else:
                    result = (name, _bounds_union(first_bounds, second_bounds))
            else:
                if first_bounds is _EMPTY_BOUNDS:
                    result = (None, _EMPTY_BOUNDS)
                    proofs.append({"solid": name, "operation": "subtraction",
                                   "result": "empty", "reason": "first_operand_empty"})
                elif second_bounds is _EMPTY_BOUNDS:
                    if _has_operand_transform(solid, "first"):
                        raise FinalizationError(
                            f"cannot simplify {name}: surviving first operand is transformed"
                        )
                    result = (first_name, first_bounds)
                    proofs.append({"solid": name, "operation": "subtraction",
                                   "result": "alias_first", "replacement": first_name,
                                   "reason": "second_operand_empty"})
                else:
                    result = (name, first_bounds)
        active.remove(name)
        results[name] = result
        return result

    for name in by_name:
        evaluate(name)

    replaced_references = 0
    for volume in structure.findall("volume"):
        reference = volume.find("solidref")
        if reference is None:
            continue
        resolved, _ = evaluate(reference.attrib["ref"])
        if resolved is None:
            raise FinalizationError(
                f"logical volume {volume.attrib.get('name')} directly references proven-empty solid "
                f"{reference.attrib['ref']}"
            )
        if resolved != reference.attrib["ref"]:
            reference.attrib["ref"] = resolved
            replaced_references += 1

    removable = {name for name, (resolved, _) in results.items() if resolved != name}
    referenced = {
        child.attrib["ref"]
        for solid in solids
        if solid.attrib.get("name") not in removable
        for child in list(solid)
        if child.tag in ("first", "second") and "ref" in child.attrib
    }
    referenced.update(
        reference.attrib["ref"]
        for volume in structure.findall("volume")
        for reference in volume.findall("solidref")
    )
    still_referenced = sorted(removable & referenced)
    if still_referenced:
        raise FinalizationError(
            "simplified Boolean solids remain referenced: " + ", ".join(still_referenced)
        )
    removed = []
    for solid in list(solids):
        if solid.attrib.get("name") in removable:
            removed.append(solid.attrib["name"])
            solids.remove(solid)
    return {
        "proof_method": "conservative_axis_aligned_bounds",
        "proof_count": len(proofs),
        "proofs": proofs,
        "removed_boolean_count": len(removed),
        "removed_boolean_solids": sorted(removed),
        "replaced_structure_reference_count": replaced_references,
    }


def partition_model_z_minimum(
    solids, world, volumes, conversion, source_bounds, removed_regions,
    retained_bounds, boundary_mm, tolerance_mm=0.01,
):
    """Restrict the external model to an explicit +z ownership half-space."""
    if not math.isfinite(boundary_mm):
        raise FinalizationError("model z minimum must be finite")
    lower, upper = retained_bounds
    if not lower[2] < boundary_mm < upper[2]:
        raise FinalizationError("model z minimum must lie strictly inside retained bounds")
    lattice_bounds = {}
    for item in conversion["lattice_conversion"]["lattices"]:
        for placement in item["placements"]:
            lattice_bounds[placement["placement_name"]] = item["physical_cell_bounds_mm"]
    removed_regions = set(removed_regions)
    references = {}
    for volume in volumes.values():
        for reference in volume.findall("physvol/volumeref"):
            name = reference.attrib.get("ref")
            references[name] = references.get(name, 0) + 1

    clip_name = "shift_model_z_min_clip_box"
    if any(item.attrib.get("name") == clip_name for item in solids):
        raise FinalizationError(f"GDML already defines solid {clip_name}")
    dimensions = [
        upper[0] - lower[0] + 2.0 * tolerance_mm,
        upper[1] - lower[1] + 2.0 * tolerance_mm,
        upper[2] - boundary_mm,
    ]
    if any(value <= 0.0 for value in dimensions):
        raise FinalizationError("model interface clip box has invalid dimensions")
    ET.SubElement(solids, "box", {
        "name": clip_name,
        "x": repr(dimensions[0]),
        "y": repr(dimensions[1]),
        "z": repr(dimensions[2]),
        "lunit": "mm",
    })
    clip_centre = [
        (lower[0] + upper[0]) / 2.0,
        (lower[1] + upper[1]) / 2.0,
        (boundary_mm + upper[2]) / 2.0,
    ]

    removed, clipped = [], []
    for physical in list(world.findall("physvol")):
        name = physical.attrib["name"]
        if name in lattice_bounds:
            bounds = lattice_bounds[name]
            if bounds[0][2] < boundary_mm + tolerance_mm:
                raise FinalizationError(
                    f"model interface boundary affects physical lattice cell placement {name}"
                )
            continue
        if not name.endswith("_pv"):
            raise FinalizationError(f"cannot map world placement {name} to certified source bounds")
        region = name[:-3]
        if region in removed_regions:
            continue
        if region not in source_bounds:
            raise FinalizationError(f"world region {region} has no certified source bounds")
        bounds = source_bounds[region]
        if bounds[1][2] <= boundary_mm + tolerance_mm:
            world.remove(physical)
            removed.append(name)
            continue
        if bounds[0][2] >= boundary_mm - tolerance_mm:
            continue
        try:
            volume_ref = physical.find("volumeref").attrib["ref"]
            volume = volumes[volume_ref]
            solid_ref = volume.find("solidref")
            first_solid = solid_ref.attrib["ref"]
        except (KeyError, AttributeError) as error:
            raise FinalizationError(f"interface-crossing placement {name} is malformed") from error
        if references.get(volume_ref) != 1:
            raise FinalizationError(
                f"interface-crossing logical volume {volume_ref} is shared by multiple placements"
            )
        position = _placement_position_mm(physical)
        rotation = _placement_rotation(physical)
        inverse = _transpose(rotation)
        relative_position = _matrix_vector(
            inverse, [clip_centre[axis] - position[axis] for axis in range(3)]
        )
        # GDML encodes the transpose (passive form) of the active matrix.
        relative_rotation = _matrix_to_tbxyz(_transpose(inverse))
        output_solid = f"shift_model_z_min_clip_{len(clipped)}"
        intersection = ET.SubElement(solids, "intersection", {"name": output_solid})
        ET.SubElement(intersection, "first", {"ref": first_solid})
        ET.SubElement(intersection, "second", {"ref": clip_name})
        ET.SubElement(intersection, "position", {
            "name": output_solid + "_position",
            "x": repr(relative_position[0]),
            "y": repr(relative_position[1]),
            "z": repr(relative_position[2]),
            "unit": "mm",
        })
        ET.SubElement(intersection, "rotation", {
            "name": output_solid + "_rotation",
            "x": repr(relative_rotation[0]),
            "y": repr(relative_rotation[1]),
            "z": repr(relative_rotation[2]),
            "unit": "rad",
        })
        solid_ref.attrib["ref"] = output_solid
        clipped.append({
            "placement": name,
            "region": region,
            "source_bounds_mm": bounds,
            "output_solid": output_solid,
        })
    if not clipped:
        raise FinalizationError("model interface boundary clips no region")
    return {
        "axis": "model_z",
        "retained_half_space": f"z >= {boundary_mm} mm",
        "boundary_mm": boundary_mm,
        "classification_tolerance_mm": tolerance_mm,
        "removed_placement_count": len(removed),
        "removed_placements": sorted(removed),
        "clipped_placement_count": len(clipped),
        "clipped_placements": clipped,
        "retained_model_bounds_mm": [
            [lower[0], lower[1], boundary_mm],
            list(upper),
        ],
        "lattice_placements_affected": 0,
        "certified_bounds_source": "source bounds report and lattice conversion report",
    }


def partition_same_material_siblings(solids, world, volumes, precedence_pairs):
    """Make same-material sibling regions disjoint without changing their union.

    Each pair is ``(preserved, trimmed)``. The preserved solid is subtracted
    from the trimmed solid. This implements deterministic volume ownership,
    while ``(trimmed - preserved) union preserved`` is exactly the original
    material union. Rotated top-level placements deliberately fail closed.
    """

    placements = {item.attrib.get("name"): item for item in world.findall("physvol")}
    existing_solids = {item.attrib.get("name") for item in solids}
    report = []
    seen_trimmed = set()
    for index, (preserved_region, trimmed_region) in enumerate(precedence_pairs):
        if preserved_region == trimmed_region:
            raise FinalizationError("same-material precedence regions must differ")
        if trimmed_region in seen_trimmed:
            raise FinalizationError(
                f"same-material region {trimmed_region} is trimmed more than once"
            )
        seen_trimmed.add(trimmed_region)
        try:
            preserved_pv = placements[preserved_region + "_pv"]
            trimmed_pv = placements[trimmed_region + "_pv"]
            preserved_lv = volumes[preserved_pv.find("volumeref").attrib["ref"]]
            trimmed_lv = volumes[trimmed_pv.find("volumeref").attrib["ref"]]
            preserved_material = preserved_lv.find("materialref").attrib["ref"]
            trimmed_material = trimmed_lv.find("materialref").attrib["ref"]
            preserved_solid = preserved_lv.find("solidref").attrib["ref"]
            trimmed_solid_ref = trimmed_lv.find("solidref")
            trimmed_solid = trimmed_solid_ref.attrib["ref"]
        except (KeyError, AttributeError) as error:
            raise FinalizationError(
                f"same-material precedence pair {preserved_region}:{trimmed_region} "
                "does not resolve to two complete world sibling volumes"
            ) from error
        if preserved_material != trimmed_material:
            raise FinalizationError(
                f"same-material precedence pair {preserved_region}:{trimmed_region} "
                f"has materials {preserved_material} and {trimmed_material}"
            )
        for physical, region in (
            (preserved_pv, preserved_region),
            (trimmed_pv, trimmed_region),
        ):
            if physical.find("rotation") is not None or physical.find("rotationref") is not None:
                raise FinalizationError(
                    f"same-material region {region} has a rotated top-level placement"
                )
        preserved_position = _placement_position_mm(preserved_pv)
        trimmed_position = _placement_position_mm(trimmed_pv)
        relative_position = [
            preserved - trimmed
            for preserved, trimmed in zip(preserved_position, trimmed_position)
        ]
        new_solid_name = f"shift_same_material_partition_{index}"
        if new_solid_name in existing_solids:
            raise FinalizationError(f"GDML already defines solid {new_solid_name}")
        existing_solids.add(new_solid_name)
        subtraction = ET.SubElement(solids, "subtraction", {"name": new_solid_name})
        ET.SubElement(subtraction, "first", {"ref": trimmed_solid})
        ET.SubElement(subtraction, "second", {"ref": preserved_solid})
        ET.SubElement(
            subtraction,
            "position",
            {
                "name": new_solid_name + "_position",
                "x": repr(relative_position[0]),
                "y": repr(relative_position[1]),
                "z": repr(relative_position[2]),
                "unit": "mm",
            },
        )
        trimmed_solid_ref.attrib["ref"] = new_solid_name
        report.append(
            {
                "preserved_region": preserved_region,
                "trimmed_region": trimmed_region,
                "material": preserved_material,
                "operation": "trimmed_minus_preserved",
                "preserved_to_trimmed_local_translation_mm": relative_position,
                "output_solid": new_solid_name,
                "material_union_invariant": "(trimmed - preserved) union preserved",
            }
        )
    return report


def finalize_gdml(
    input_path, output_path, conversion, partition, padding_mm, precedence_pairs=(),
    source_bounds=None, model_z_min_mm=None, interface_tolerance_mm=0.01,
):
    if padding_mm <= 0:
        raise FinalizationError("world padding must be positive")
    tree = ET.parse(input_path)
    root = tree.getroot()
    solids, world, world_solid, volumes = _world_and_volumes(root)
    empty_boolean_simplification = simplify_empty_booleans(root)
    direct_placements = list(world.findall("physvol"))
    direct_names = {item.attrib.get("name") for item in direct_placements}
    if None in direct_names or len(direct_names) != len(direct_placements):
        raise FinalizationError("world placement names are missing or duplicated")

    lattice_names = {
        placement["placement_name"]
        for item in conversion["lattice_conversion"]["lattices"]
        for placement in item["placements"]
    }
    missing_lattices = sorted(lattice_names - direct_names)
    if missing_lattices:
        raise FinalizationError(
            "GDML is missing reported lattice placements: " + ", ".join(missing_lattices)
        )

    removed_regions = set(partition["removed_regions"])
    expected_placements = {name + "_pv" for name in removed_regions}
    missing_originals = sorted(expected_placements - direct_names)
    if missing_originals:
        raise FinalizationError(
            "GDML is missing source-only ordinary placements: " + ", ".join(missing_originals)
        )
    if expected_placements & lattice_names:
        raise FinalizationError("source-only removal would delete a lattice copy")
    for physical in direct_placements:
        if physical.attrib["name"] in expected_placements:
            world.remove(physical)

    retained_direct_names = {item.attrib["name"] for item in world.findall("physvol")}
    if not lattice_names <= retained_direct_names:
        raise FinalizationError("a lattice copy was lost during source-only removal")

    same_material_partitions = partition_same_material_siblings(
        solids, world, volumes, precedence_pairs
    )

    interface_partition = None
    model_bounds = partition["retained_model_bounds_mm"]
    if model_z_min_mm is not None:
        if source_bounds is None:
            raise FinalizationError("model z minimum requires certified source bounds")
        interface_partition = partition_model_z_minimum(
            solids, world, volumes, conversion, source_bounds, removed_regions,
            model_bounds, model_z_min_mm, interface_tolerance_mm,
        )
        model_bounds = interface_partition["retained_model_bounds_mm"]

    retained_direct_names = {item.attrib["name"] for item in world.findall("physvol")}
    if not lattice_names <= retained_direct_names:
        raise FinalizationError("a lattice copy was lost during interface partition")

    lower, upper = model_bounds
    centre = [(low + high) / 2.0 for low, high in zip(lower, upper)]
    dimensions = [high - low + 2.0 * padding_mm for low, high in zip(lower, upper)]
    world_padding_policy = "symmetric"
    if interface_partition is not None:
        # The lower z face is a physical ownership boundary. Keep the GDML
        # bookkeeping world exactly on that plane and put all z padding outside.
        centre[2] += padding_mm / 2.0
        dimensions[2] -= padding_mm
        world_padding_policy = "symmetric_xy_upper_z_only"
    for physical in world.findall("physvol"):
        if physical.find("positionref") is not None:
            raise FinalizationError("bounded finalization does not support positionref placements")
        position = physical.find("position")
        if position is None:
            position = ET.SubElement(
                physical,
                "position",
                {
                    "name": physical.attrib["name"] + "_bounded_pos",
                    "x": "0",
                    "y": "0",
                    "z": "0",
                    "unit": "mm",
                },
            )
        if position.attrib.get("unit", "mm") != "mm":
            raise FinalizationError("bounded finalization supports only mm placements")
        for axis, offset in zip(("x", "y", "z"), centre):
            position.attrib[axis] = repr(float(position.attrib.get(axis, "0")) - offset)
    for axis, dimension in zip(("x", "y", "z"), dimensions):
        world_solid.attrib[axis] = repr(dimension)
    world_solid.attrib["lunit"] = "mm"

    material_counts = {}
    for physical in world.findall("physvol"):
        volume_ref = physical.find("volumeref")
        if volume_ref is None or volume_ref.attrib.get("ref") not in volumes:
            raise FinalizationError("world placement has an invalid logical-volume reference")
        material_ref = volumes[volume_ref.attrib["ref"]].find("materialref")
        if material_ref is None:
            raise FinalizationError("placed logical volume has no material")
        material = material_ref.attrib["ref"]
        material_counts[material] = material_counts.get(material, 0) + 1

    output_path = Path(output_path)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    tree.write(temporary, encoding="utf-8", xml_declaration=True)
    temporary.replace(output_path)
    return {
        "input_direct_placement_count": len(direct_placements),
        "removed_source_placement_count": len(expected_placements),
        "retained_direct_placement_count": len(retained_direct_names),
        "preserved_lattice_placement_count": len(lattice_names),
        "world_dimensions_mm": dimensions,
        "artifact_origin_in_model_mm": centre,
        "model_to_artifact_translation_mm": [-value for value in centre],
        "world_padding_mm": padding_mm,
        "world_padding_policy": world_padding_policy,
        "placed_material_volume_counts": dict(sorted(material_counts.items())),
        "same_material_partitions": same_material_partitions,
        "model_interface_partition": interface_partition,
        "empty_boolean_simplification": empty_boolean_simplification,
    }


def parse_precedence_pair(text):
    parts = text.split(":")
    if len(parts) != 2 or not all(parts):
        raise argparse.ArgumentTypeError(
            "same-material precedence must be PRESERVED_REGION:TRIMMED_REGION"
        )
    return tuple(parts)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-gdml", type=Path, required=True)
    parser.add_argument("--conversion-report", type=Path, required=True)
    parser.add_argument("--source-bounds-report", type=Path, required=True)
    parser.add_argument("--prototype-reservoir-region", required=True)
    parser.add_argument("--output-gdml", type=Path, required=True)
    parser.add_argument("--output-report", type=Path, required=True)
    parser.add_argument("--world-padding-mm", type=float, default=10.0)
    parser.add_argument("--containment-tolerance-mm", type=float, default=0.01)
    parser.add_argument(
        "--model-z-min-mm",
        type=float,
        help=(
            "Explicit physical ownership boundary: remove or exactly clip source regions "
            "below this model-frame z coordinate; lattice cells must lie wholly above it"
        ),
    )
    parser.add_argument("--interface-tolerance-mm", type=float, default=0.01)
    parser.add_argument(
        "--same-material-precedence",
        action="append",
        type=parse_precedence_pair,
        default=[],
        metavar="PRESERVED_REGION:TRIMMED_REGION",
        help=(
            "Resolve an audited same-material sibling overlap by subtracting the "
            "preserved region from the trimmed region; may be repeated"
        ),
    )
    return parser.parse_args()


def main():
    args = parse_args()
    try:
        conversion, bounds_report, bounds = load_validated_inputs(
            args.conversion_report, args.source_bounds_report
        )
        partition = physical_partition(
            conversion,
            bounds,
            args.prototype_reservoir_region,
            args.containment_tolerance_mm,
        )
        finalization = finalize_gdml(
            args.input_gdml,
            args.output_gdml,
            conversion,
            partition,
            args.world_padding_mm,
            args.same_material_precedence,
            bounds,
            args.model_z_min_mm,
            args.interface_tolerance_mm,
        )
        report = {
            "schema": "shift-fluka-physical-artifact-v1",
            "passed": True,
            "production_ready": False,
            "production_ready_reason": (
                "geometry partition passed; independent overlap/navigation, material, "
                "field, and CMS attachment validation remain external gates"
            ),
            "input_gdml_sha256": sha256(args.input_gdml),
            "conversion_report_sha256": sha256(args.conversion_report),
            "source_bounds_report_sha256": sha256(args.source_bounds_report),
            "source_bounds_report_timeout_seconds": bounds_report.get("timeout_seconds"),
            "physical_partition": partition,
            "finalization": finalization,
            "output_gdml": str(args.output_gdml),
            "output_gdml_sha256": sha256(args.output_gdml),
        }
        write_json_atomic(args.output_report, report)
    except (FinalizationError, ET.ParseError, OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(args.output_report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
