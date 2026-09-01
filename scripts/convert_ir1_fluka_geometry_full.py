#!/usr/bin/env python3

"""Run the complete IR1 proxy conversion with explicit, audited compatibility guards."""

import argparse
from contextlib import contextmanager
from copy import deepcopy
from functools import reduce
import importlib
from importlib import metadata
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

from ir1_fluka_geometry import ProxyModelError, convert_geometry


REPOSITORY = Path(__file__).resolve().parents[1]


def parse_world_dimensions(text):
    try:
        dimensions = tuple(float(item.strip()) for item in text.split(","))
    except ValueError as error:
        raise ProxyModelError("world dimensions must be numeric X,Y,Z values in mm") from error
    if len(dimensions) != 3 or any(value <= 0.0 for value in dimensions):
        raise ProxyModelError("world dimensions must contain three positive values in mm")
    return dimensions


def install_null_body_aabb_workaround(converter_module):
    """Skip bodies only when no retained FLUKA region references them."""

    original = converter_module._makeBodyMinimumAABBMap
    skipped = set()

    def body_minimum_aabb_map(fluka_registry, region_zone_aabbs, retained_regions):
        region_aabbs = converter_module._regionZoneAABBsToRegionAABBs(region_zone_aabbs)
        result = {}
        for body_name, region_names in fluka_registry.getBodyToRegionsMap().items():
            candidates = [
                region_aabbs[region_name]
                for region_name in region_names
                if region_name in retained_regions and region_name in region_aabbs
            ]
            if not candidates:
                skipped.add(body_name)
                continue
            result[body_name] = reduce(converter_module._getMaximalOfTwoAABBs, candidates)
        return result

    converter_module._makeBodyMinimumAABBMap = body_minimum_aabb_map
    return original, skipped


def install_transformed_infinite_cylinder_centre_workaround():
    """Apply FLUKA transforms to infinite-cylinder centres without an AABB."""

    from pyg4ometry.fluka.body import XCC, XEC, YCC, YEC, ZEC

    originals = {}
    for cylinder_class in (XCC, YCC, XEC, YEC, ZEC):
        original = cylinder_class.centre
        originals[cylinder_class] = original

        def transformed_centre(self, aabb=None, *, _original=original):
            centre = _original(self, aabb)
            if aabb is None:
                return self.transform.leftMultiplyVector(centre)
            return centre

        cylinder_class.centre = transformed_centre
    return originals


def restore_transformed_infinite_cylinder_centres(originals):
    for cylinder_class, original in originals.items():
        cylinder_class.centre = original


@contextmanager
def full_conversion_guards(world_dimensions_mm):
    converter_module = importlib.import_module("pyg4ometry.convert.fluka2Geant4")
    logical_volume_module = importlib.import_module("pyg4ometry.geant4.LogicalVolume")
    original_body_aabb, skipped = install_null_body_aabb_workaround(converter_module)
    original_cylinder_centres = (
        install_transformed_infinite_cylinder_centre_workaround()
    )
    original_world_dimensions = converter_module.WORLD_DIMENSIONS
    original_clip_solid = logical_volume_module.LogicalVolume.clipSolid
    converter_module.WORLD_DIMENSIONS = list(world_dimensions_mm)
    logical_volume_module.LogicalVolume.clipSolid = lambda self, lengthSafety=1e-6: None
    try:
        yield skipped
    finally:
        restore_transformed_infinite_cylinder_centres(original_cylinder_centres)
        logical_volume_module.LogicalVolume.clipSolid = original_clip_solid
        converter_module.WORLD_DIMENSIONS = original_world_dimensions
        converter_module._makeBodyMinimumAABBMap = original_body_aabb


def count_multi_unions(gdml_path):
    return sum(
        line.count("<multiUnion ")
        for line in Path(gdml_path).read_text(encoding="utf-8").splitlines()
    )


def _lowered_transform(element, first_operand):
    tag = element.tag
    if first_operand:
        tag = {
            "position": "firstposition",
            "positionref": "firstpositionref",
            "rotation": "firstrotation",
            "rotationref": "firstrotationref",
        }.get(tag)
        if tag is None:
            raise ProxyModelError(
                f"unsupported multiUnion first-node transform element {element.tag}"
            )
    elif tag not in {"position", "positionref", "rotation", "rotationref"}:
        raise ProxyModelError(f"unsupported multiUnion node element {element.tag}")
    result = deepcopy(element)
    result.tag = tag
    return result


def _multi_union_operand(node, first_operand):
    solid = node.find("solid")
    if solid is None or set(solid.attrib) != {"ref"}:
        raise ProxyModelError("multiUnionNode must contain exactly one solid ref")
    transforms = [
        _lowered_transform(element, first_operand)
        for element in node
        if element.tag != "solid"
    ]
    return solid.attrib["ref"], transforms


def lower_multi_unions_for_root(gdml_path):
    """Lower GDML multiUnion nodes to exact ROOT binary Boolean unions.

    ROOT 6.36 supports transforms on both binary operands through its
    firstposition/firstrotation GDML extension, but does not parse multiUnion.
    Set-union associativity therefore permits an exact CSG rewrite without
    tessellation, approximation, or dropped nodes.
    """

    gdml_path = Path(gdml_path)
    tree = ET.parse(gdml_path)
    root = tree.getroot()
    solids = root.find("solids")
    if solids is None:
        raise ProxyModelError("GDML has no solids section")

    existing_names = {
        element.attrib["name"]
        for element in solids
        if "name" in element.attrib
    }
    multi_unions = [element for element in solids if element.tag == "multiUnion"]
    report = {
        "input_multi_union_count": len(multi_unions),
        "input_node_count": 0,
        "output_binary_union_count": 0,
        "root_first_operand_transform_count": 0,
        "uses_root_first_operand_transform_extension": True,
        "remaining_multi_union_count": None,
    }

    for multi_union in multi_unions:
        name = multi_union.attrib.get("name")
        if not name:
            raise ProxyModelError("multiUnion is missing its name")
        nodes = list(multi_union.findall("multiUnionNode"))
        if len(nodes) < 2 or len(nodes) != len(list(multi_union)):
            raise ProxyModelError(f"{name}: expected at least two pure multiUnionNode children")
        report["input_node_count"] += len(nodes)

        first_ref, first_transforms = _multi_union_operand(nodes[0], True)
        report["root_first_operand_transform_count"] += len(first_transforms)
        previous_ref = first_ref
        replacements = []
        for index, node in enumerate(nodes[1:], 1):
            is_final = index == len(nodes) - 1
            union_name = name if is_final else f"{name}__root_binary_{index:04d}"
            if not is_final and union_name in existing_names:
                raise ProxyModelError(f"generated binary-union name collision: {union_name}")
            existing_names.add(union_name)
            second_ref, second_transforms = _multi_union_operand(node, False)
            union = ET.Element("union", {"name": union_name})
            ET.SubElement(union, "first", {"ref": previous_ref})
            ET.SubElement(union, "second", {"ref": second_ref})
            if index == 1:
                union.extend(deepcopy(first_transforms))
            union.extend(second_transforms)
            replacements.append(union)
            previous_ref = union_name

        position = list(solids).index(multi_union)
        solids.remove(multi_union)
        for offset, replacement in enumerate(replacements):
            solids.insert(position + offset, replacement)
        report["output_binary_union_count"] += len(replacements)

    temporary = gdml_path.with_suffix(gdml_path.suffix + ".root-lowering.tmp")
    tree.write(temporary, encoding="utf-8", xml_declaration=True)
    temporary.replace(gdml_path)
    report["remaining_multi_union_count"] = count_multi_unions(gdml_path)
    if report["remaining_multi_union_count"]:
        raise ProxyModelError("ROOT lowering left multiUnion elements in the GDML")
    return report


def write_json_atomic(path, payload):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model-dir",
        type=Path,
        default=REPOSITORY / "models" / "lss5_ir1_atlas_proxy",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--region-timeout-seconds", type=float, default=300.0)
    parser.add_argument(
        "--world-dimensions-mm",
        required=True,
        metavar="X,Y,Z",
        help="Explicit full diagnostic world dimensions in mm",
    )
    parser.add_argument("--acknowledge-geometry-only", action="store_true")
    parser.add_argument("--acknowledge-ir1-atlas-proxy", action="store_true")
    parser.add_argument(
        "--root-compatible-binary-unions",
        action="store_true",
        help=(
            "Lower every multiUnion to exact binary unions using ROOT's "
            "first-operand transform extension"
        ),
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if args.region_timeout_seconds <= 0.0:
        print("error: --region-timeout-seconds must be positive", file=sys.stderr)
        return 2
    if not args.acknowledge_geometry_only:
        print("error: pass --acknowledge-geometry-only; GDML contains no magnetic field", file=sys.stderr)
        return 2
    if not args.acknowledge_ir1_atlas_proxy:
        print(
            "error: pass --acknowledge-ir1-atlas-proxy; this model is not Run-3 IR5/CMS geometry",
            file=sys.stderr,
        )
        return 2
    try:
        dimensions = parse_world_dimensions(args.world_dimensions_mm)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        with full_conversion_guards(dimensions) as skipped:
            report = convert_geometry(
                args.model_dir,
                args.output_dir,
                lattice_aabb_workaround=True,
                raw_region_preflight=True,
                region_timeout_seconds=args.region_timeout_seconds,
            )
        gdml_path = args.output_dir / report["geometry"]["gdml"]
        raw_multi_union_count = count_multi_unions(gdml_path)
        root_lowering = None
        if args.root_compatible_binary_unions:
            root_lowering = lower_multi_unions_for_root(gdml_path)
        report.update(
            {
                "schema_version": 2,
                "pyg4ometry_version": metadata.version("pyg4ometry"),
                "conversion_controls": {
                    "lattice_aabb_workaround": True,
                    "raw_region_preflight": True,
                    "raw_region_timeout_seconds": args.region_timeout_seconds,
                    "transformed_infinite_cylinder_centre_workaround": True,
                    "null_body_aabb_workaround": True,
                    "null_only_body_count": len(skipped),
                    "world_dimensions_mm": list(dimensions),
                    "world_clipping_disabled": True,
                },
                "root_tgdml_import": {
                    "validated": False,
                    "raw_multi_union_count": raw_multi_union_count,
                    "remaining_multi_union_count": count_multi_unions(gdml_path),
                    "binary_union_lowering": root_lowering,
                    "known_limitation": (
                        None
                        if args.root_compatible_binary_unions
                        else "ROOT 6.36 TGDMLParse does not support the generated multiUnion solids"
                    ),
                },
                "cmssw_geometry_contract": {
                    "default_cms_geometry_must_be_preserved": True,
                    "external_extension_only": True,
                    "bounded_external_volume_produced": False,
                    "overlap_with_cms_geometry_validated": False,
                },
            }
        )
        omission_audit = report["geometry"]["omitted_region_audit"]
        unexpected = omission_audit["unexpected_omitted_regions"]
        deferred_converted = omission_audit[
            "deferred_region_conversion_failures"
        ]
        report["geometry"]["lossless_nonempty_region_coverage"] = (
            not unexpected and not deferred_converted
        )
        write_json_atomic(args.output_dir / "conversion_report.json", report)
        if unexpected:
            raise ProxyModelError(
                "conversion omitted raw non-empty FLUKA regions: "
                + ", ".join(unexpected)
            )
        if deferred_converted:
            raise ProxyModelError(
                "length safety created material from unresolved raw-null regions: "
                + ", ".join(deferred_converted)
            )
    except Exception as error:
        failure = {
            "schema": "shift-ir1-proxy-conversion-failure",
            "schema_version": 1,
            "model_status": "provisional-ir1-atlas-proxy",
            "error_type": type(error).__name__,
            "error": str(error),
            "default_cms_geometry_modified": False,
        }
        args.output_dir.mkdir(parents=True, exist_ok=True)
        write_json_atomic(args.output_dir / "conversion_failure.json", failure)
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(
        f"wrote diagnostic {report['geometry']['gdml']} with "
        f"{report['geometry']['logical_volume_count']} logical volumes; "
        "it is not a bounded CMSSW geometry extension"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
