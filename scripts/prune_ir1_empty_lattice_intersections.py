#!/usr/bin/env python3

"""Prune ROOT-proven empty lattice intersections from a bounded IR1 GDML."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET


EMPTY_PATTERN = re.compile(
    r"Warning in <TGeoIntersection::ComputeBBox>: shapes (.+) and (.+) do not intersect$"
)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-gdml", type=Path, required=True)
    parser.add_argument("--input-report", type=Path, required=True)
    parser.add_argument("--output-gdml", type=Path, required=True)
    parser.add_argument("--output-report", type=Path, required=True)
    parser.add_argument("--root-command", default="root")
    return parser.parse_args()


def root_import(root_command, gdml_path):
    quoted = json.dumps(str(Path(gdml_path).resolve()))
    expression = (
        f"TGeoManager::Import({quoted}); "
        "if (!gGeoManager) gSystem->Exit(2); "
        'std::cout << "SHIFT_PRUNE_IMPORT\\t" '
        "<< gGeoManager->GetTopVolume()->GetNdaughters() << std::endl; "
        "gSystem->Exit(0);"
    )
    result = subprocess.run(
        [root_command, "-l", "-b", "-q", "-e", expression],
        text=True,
        capture_output=True,
    )
    output = result.stdout + result.stderr
    if result.returncode:
        raise RuntimeError(
            f"ROOT import failed with status {result.returncode}:\n{output[-4000:]}"
        )
    if "SHIFT_PRUNE_IMPORT\t" not in output:
        raise RuntimeError("ROOT import emitted no success marker")
    empty_pairs = []
    for line in output.splitlines():
        match = EMPTY_PATTERN.search(line)
        if match:
            empty_pairs.append(frozenset(match.groups()))
    return set(empty_pairs), output


def volume_inventory(structure, world):
    volumes = {
        volume.attrib["name"]: volume
        for volume in structure.findall("volume")
        if "name" in volume.attrib
    }
    logical_volumes = set()
    material_counts = {}
    for placement in world.findall("physvol"):
        reference = placement.find("volumeref")
        if reference is None:
            raise ValueError("world placement has no volumeref")
        name = reference.attrib["ref"]
        logical_volumes.add(name)
        material = volumes[name].find("materialref")
        if material is None:
            raise ValueError(f"logical volume {name} has no materialref")
        material_name = material.attrib["ref"]
        material_counts[material_name] = material_counts.get(material_name, 0) + 1
    return {
        "placed_volume_count": len(world.findall("physvol")),
        "placed_logical_volume_count": len(logical_volumes),
        "placed_material_count": len(material_counts),
        "placed_material_volume_counts": dict(sorted(material_counts.items())),
    }


def prune_tree(tree, conversion, empty_pairs):
    root = tree.getroot()
    solids = root.find("solids")
    structure = root.find("structure")
    setup = root.find("setup")
    if solids is None or structure is None or setup is None:
        raise ValueError("GDML requires solids, structure, and setup")
    world_name = setup.find("world").attrib["ref"]
    world = next(
        volume
        for volume in structure.findall("volume")
        if volume.attrib.get("name") == world_name
    )
    intersections = {
        solid.attrib["name"]: solid
        for solid in solids.findall("intersection")
        if "name" in solid.attrib
    }

    empty_source_solids = []
    empty_lattice_solids = []
    for name, solid in intersections.items():
        first = solid.find("first")
        second = solid.find("second")
        if first is None or second is None:
            continue
        pair = frozenset((first.attrib["ref"], second.attrib["ref"]))
        if pair not in empty_pairs:
            continue
        if name.endswith("_source_bounded_solid"):
            empty_source_solids.append(name)
        elif name.endswith("_lattice_clip_solid"):
            empty_lattice_solids.append(name)
    if empty_source_solids:
        raise ValueError(
            "audited source-envelope intersections are empty: "
            + ", ".join(sorted(empty_source_solids))
        )

    volumes = {
        volume.attrib["name"]: volume
        for volume in structure.findall("volume")
        if "name" in volume.attrib
    }
    empty_lattice_volumes = {
        name.removesuffix("_solid") + "_lv"
        for name in empty_lattice_solids
    }
    removed_placements = []
    for placement in list(world.findall("physvol")):
        reference = placement.find("volumeref")
        if reference is not None and reference.attrib["ref"] in empty_lattice_volumes:
            removed_placements.append(placement.attrib["name"])
            world.remove(placement)
    if len(removed_placements) != len(empty_lattice_solids):
        raise ValueError(
            "empty lattice solid/placement count mismatch: "
            f"solids={len(empty_lattice_solids)} placements={len(removed_placements)}"
        )

    for name in empty_lattice_volumes:
        structure.remove(volumes[name])
    for name in empty_lattice_solids:
        solids.remove(intersections[name])

    removed_set = set(removed_placements)
    lattice = conversion["geometry"]["lattice_placement"]
    used = set()
    for item in lattice["lattices"]:
        retained = []
        for prototype in item["prototypes"]:
            placement_name = (
                f"{item['lattice']}__{prototype['prototype']}_lattice_pv"
            )
            if placement_name not in removed_set:
                retained.append(prototype)
                used.add(prototype["prototype"])
        item["prototypes"] = retained
        item["prototype_count"] = len(retained)
    lattice["lattice_placement_count"] = sum(
        item["prototype_count"] for item in lattice["lattices"]
    )
    lattice["clipped_lattice_placement_count"] = lattice[
        "lattice_placement_count"
    ]
    lattice["used_parking_prototype_count"] = len(used)
    lattice["unused_parking_prototypes"] = sorted(
        set(lattice["parking_prototypes"]) - used
    )

    inventory = volume_inventory(structure, world)
    conversion["geometry"]["bounded_artifact"].update(inventory)
    pruning = {
        "root_import_validated": True,
        "empty_lattice_candidate_count": len(removed_placements),
        "empty_lattice_placements": sorted(removed_placements),
        "retained_lattice_placement_count": lattice["lattice_placement_count"],
        "final_structure_volume_count": len(structure.findall("volume")),
        "final_solid_count": len(list(solids)),
    }
    conversion["geometry"]["empty_lattice_pruning"] = pruning
    conversion["root_tgdml_import"]["validated"] = True
    return pruning


def write_tree_atomic(path, tree):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    tree.write(temporary, encoding="utf-8", xml_declaration=True)
    temporary.replace(path)


def write_json_atomic(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def main():
    args = parse_args()
    try:
        conversion = json.loads(args.input_report.read_text(encoding="utf-8"))
        if not conversion["conversion_controls"]["bounded_installable_requested"]:
            raise ValueError("conversion report is not for a bounded artifact")
        empty_pairs, _ = root_import(args.root_command, args.input_gdml)
        tree = ET.parse(args.input_gdml)
        pruning = prune_tree(tree, conversion, empty_pairs)
        write_tree_atomic(args.output_gdml, tree)
        remaining_pairs, _ = root_import(args.root_command, args.output_gdml)

        remaining_targets = []
        root = tree.getroot()
        for solid in root.find("solids").findall("intersection"):
            name = solid.attrib.get("name", "")
            if not name.endswith(("_source_bounded_solid", "_lattice_clip_solid")):
                continue
            pair = frozenset(
                (solid.find("first").attrib["ref"], solid.find("second").attrib["ref"])
            )
            if pair in remaining_pairs:
                remaining_targets.append(name)
        if remaining_targets:
            raise ValueError(
                "targeted empty intersections remain after pruning: "
                + ", ".join(sorted(remaining_targets))
            )

        digest = sha256(args.output_gdml)
        conversion["geometry"]["gdml"] = args.output_gdml.name
        conversion["geometry"]["gdml_sha256"] = digest
        conversion["geometry"]["bounded_artifact"]["gdml_sha256"] = digest
        pruning["post_prune_root_import_validated"] = True
        pruning["gdml_sha256"] = digest
        write_json_atomic(args.output_report, conversion)
    except Exception as error:
        print(f"error: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    print(
        f"wrote {args.output_gdml} after pruning "
        f"{pruning['empty_lattice_candidate_count']} empty lattice candidates"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
