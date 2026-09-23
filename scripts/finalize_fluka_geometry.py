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
    input_path, output_path, conversion, partition, padding_mm, precedence_pairs=()
):
    if padding_mm <= 0:
        raise FinalizationError("world padding must be positive")
    tree = ET.parse(input_path)
    root = tree.getroot()
    solids, world, world_solid, volumes = _world_and_volumes(root)
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

    lower, upper = partition["retained_model_bounds_mm"]
    centre = [(low + high) / 2.0 for low, high in zip(lower, upper)]
    dimensions = [high - low + 2.0 * padding_mm for low, high in zip(lower, upper)]
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
        "placed_material_volume_counts": dict(sorted(material_counts.items())),
        "same_material_partitions": same_material_partitions,
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
