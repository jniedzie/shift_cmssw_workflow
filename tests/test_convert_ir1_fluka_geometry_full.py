#!/usr/bin/env python3

from pathlib import Path
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET


REPOSITORY = Path(__file__).resolve().parents[1]
SCRIPTS = REPOSITORY / "scripts"
sys.path.insert(0, str(SCRIPTS))

from convert_ir1_fluka_geometry_full import (  # noqa: E402
    ProxyModelError,
    bounded_model_envelope,
    finalize_bounded_gdml,
    install_transformed_infinite_cylinder_centre_workaround,
    lower_multi_unions_for_root,
    restore_transformed_infinite_cylinder_centres,
)
from ir1_fluka_geometry import (  # noqa: E402
    _install_lattice_aabb_workaround,
    _source_bound_clip_region_names,
)
from audit_ir1_bounded_gdml import (  # noqa: E402
    classify_internal_world_gaps,
    parse_root_output,
    scan_definitions,
)
from prune_ir1_empty_lattice_intersections import prune_tree  # noqa: E402


GDML = """<?xml version="1.0"?>
<gdml>
  <solids>
    <box name="boxA" x="10" y="10" z="10" lunit="mm"/>
    <box name="boxB" x="10" y="10" z="10" lunit="mm"/>
    <box name="boxC" x="10" y="10" z="10" lunit="mm"/>
    <multiUnion name="threeBoxes">
      <multiUnionNode name="nodeA">
        <solid ref="boxA"/>
        <position name="positionA" x="-20" y="0" z="0" unit="mm"/>
        <rotation name="rotationA" x="0" y="0" z="0.1" unit="rad"/>
      </multiUnionNode>
      <multiUnionNode name="nodeB"><solid ref="boxB"/></multiUnionNode>
      <multiUnionNode name="nodeC">
        <solid ref="boxC"/>
        <positionref ref="positionC"/>
        <rotationref ref="rotationC"/>
      </multiUnionNode>
    </multiUnion>
  </solids>
</gdml>
"""


class InfiniteCylinderTransformWorkaroundTest(unittest.TestCase):
    def test_applies_translation_when_no_aabb_and_restores_original_method(self):
        from pyg4ometry.fluka import Three
        from pyg4ometry.fluka.body import XCC
        from pyg4ometry.fluka.directive import Transform

        body = XCC(
            "translated",
            0.0,
            0.0,
            1.0,
            transform=Transform(translation=[Three([10.0, 20.0, 30.0])]),
        )
        original_method = XCC.centre
        self.assertEqual(list(body.centre()), [0.0, 0.0, 0.0])
        originals = install_transformed_infinite_cylinder_centre_workaround()
        try:
            self.assertEqual(list(body.centre()), [10.0, 20.0, 30.0])
        finally:
            restore_transformed_infinite_cylinder_centres(originals)
        self.assertIs(XCC.centre, original_method)


class LatticeAabbWorkaroundTest(unittest.TestCase):
    def test_applies_affine_transform_once_to_world_coordinate_mesh(self):
        from pyg4ometry.fluka import Three
        from pyg4ometry.fluka.directive import Transform
        from pyg4ometry.pycgal.core import CSG

        class Cell:
            name = "cell"

            @staticmethod
            def mesh():
                return CSG.cube(center=[0, 0, 10], radius=[1, 2, 3])

        class Lattice:
            cellRegion = Cell()

            @staticmethod
            def getTransform():
                return Transform(translation=[Three([4, 5, -6])])

        class Converter:
            @staticmethod
            def _getTransformedCellRegionAABB(_):
                return None

        original = _install_lattice_aabb_workaround(Converter)
        try:
            bounds = Converter._getTransformedCellRegionAABB(Lattice())
        finally:
            Converter._getTransformedCellRegionAABB = original
        self.assertEqual(list(bounds.lower), [3.0, 3.0, 1.0])
        self.assertEqual(list(bounds.upper), [5.0, 7.0, 7.0])

    def test_lattice_source_bound_clipping_is_limited_to_parked_prototypes(self):
        self.assertEqual(
            _source_bound_clip_region_names(
                ["PARKr", "Prototype", "Physical"],
                {"PARKr", "Prototype"},
            ),
            {"PARKr", "Prototype"},
        )

BOUNDED_GDML = """<?xml version="1.0"?>
<gdml>
  <materials>
    <material name="Vacuum" Z="1"><D value="1e-25" unit="g/cm3"/><atom value="1" unit="g/mole"/></material>
    <material name="Steel" Z="26"><D value="7" unit="g/cm3"/><atom value="56" unit="g/mole"/></material>
  </materials>
  <solids><box name="world_solid" x="1000" y="1000" z="1000" lunit="mm"/><box name="part" x="1" y="1" z="1" lunit="mm"/></solids>
  <structure>
    <volume name="PARKr_lv"><materialref ref="Steel"/><solidref ref="part"/></volume>
    <volume name="Proto_lv"><materialref ref="Steel"/><solidref ref="part"/></volume>
    <volume name="Physical_lv"><materialref ref="Steel"/><solidref ref="part"/></volume>
    <volume name="LatticeClip_lv"><materialref ref="Vacuum"/><solidref ref="part"/></volume>
    <volume name="wl">
      <materialref ref="Vacuum"/><solidref ref="world_solid"/>
      <physvol name="PARKr_pv"><volumeref ref="PARKr_lv"/></physvol>
      <physvol name="Proto_pv"><volumeref ref="Proto_lv"/></physvol>
      <physvol name="Physical_pv"><volumeref ref="Physical_lv"/><position name="p" x="20" y="40" z="60" unit="mm"/></physvol>
      <physvol name="Cell__Proto_lattice_pv"><volumeref ref="LatticeClip_lv"/></physvol>
    </volume>
  </structure>
  <setup name="Default" version="1"><world ref="wl"/></setup>
</gdml>
"""


class BoundedArtifactTest(unittest.TestCase):
    def test_envelope_excludes_parking_and_includes_physical_lattice_cells(self):
        source = {
            "PARKr": [[-30, -40, 0], [30, -20, 100]],
            "Proto": [[-2, -35, 10], [2, -25, 20]],
            "Physical": [[0, 0, 20], [10, 20, 40]],
        }
        lattice = {"lattices": [{"physical_cell_bounds_mm": [[-5, 1, 10], [5, 3, 50]]}]}
        result = bounded_model_envelope(source, lattice, 2.0)
        self.assertEqual(result["model_bounds_mm"], [[-5, 0, 10], [10, 20, 50]])
        self.assertEqual(result["artifact_origin_in_model_mm"], [2.5, 10.0, 30.0])
        self.assertEqual(result["world_dimensions_mm"], [19.0, 24.0, 44.0])
        self.assertEqual(result["parking_regions"], ["PARKr", "Proto"])

    def test_removes_parked_placements_recentres_and_tightens_world(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.gdml"
            output = Path(directory) / "bounded.gdml"
            source.write_text(BOUNDED_GDML, encoding="utf-8")
            envelope = {
                "model_bounds_mm": [[0, 0, 0], [20, 40, 60]],
                "artifact_origin_in_model_mm": [10, 20, 30],
                "world_dimensions_mm": [22, 42, 62],
                "world_padding_mm": 1,
                "parking_region_count": 2,
                "parking_regions": ["PARKr", "Proto"],
            }
            report = finalize_bounded_gdml(source, output, envelope)
            root = ET.parse(output).getroot()
            world = next(
                volume
                for volume in root.find("structure").findall("volume")
                if volume.attrib["name"] == "wl"
            )
            placements = {item.attrib["name"]: item for item in world.findall("physvol")}
            self.assertEqual(set(placements), {"Physical_pv", "Cell__Proto_lattice_pv"})
            physical = placements["Physical_pv"].find("position")
            self.assertEqual([physical.attrib[axis] for axis in "xyz"], ["10.0", "20.0", "30.0"])
            lattice = placements["Cell__Proto_lattice_pv"].find("position")
            self.assertEqual([lattice.attrib[axis] for axis in "xyz"], ["-10.0", "-20.0", "-30.0"])
            world_box = next(
                solid
                for solid in root.find("solids")
                if solid.attrib.get("name") == "world_solid"
            )
            self.assertEqual([world_box.attrib[axis] for axis in "xyz"], ["22.0", "42.0", "62.0"])
            self.assertEqual(report["removed_parked_placement_count"], 2)
            self.assertEqual(report["placed_material_volume_counts"], {"Steel": 1, "Vacuum": 1})


class BoundedAuditTest(unittest.TestCase):
    def test_builds_longitudinal_and_unique_lattice_centre_scans(self):
        conversion = {
            "geometry": {
                "bounded_artifact": {
                    "model_to_artifact_translation_mm": [-10, -20, -30],
                    "model_bounds_mm": [[0, 0, 0], [100, 100, 100]],
                },
                "lattice_placement": {
                    "lattices": [
                        {"physical_cell_bounds_mm": [[0, 0, 10], [1, 1, 20]]},
                        {"physical_cell_bounds_mm": [[0, 0, 10], [1, 1, 20]]},
                        {"physical_cell_bounds_mm": [[0, 0, 30], [1, 1, 50]]},
                    ]
                },
            }
        }
        scans = scan_definitions(conversion)
        self.assertEqual(len(scans), 25)
        self.assertEqual(scans[0]["fixed_mm"], [-10, -20])
        self.assertEqual(scans[1]["fixed_mm"], [-10.1, -20])
        self.assertEqual(scans[5]["fixed_mm"], [-20, -15.0])
        self.assertEqual(scans[10]["fixed_mm"], [-10, -15.0])
        self.assertEqual(scans[15]["fixed_mm"], [-20, 10.0])

    def test_separates_persistent_and_boundary_sensitive_world_gaps(self):
        definitions = {
            name: {
                "base_name": "ray",
                "is_central": name == "ray",
            }
            for name in ("ray", "ray__minus", "ray__plus")
        }
        volume = lambda name, start, end: {
            "logical_volume": name,
            "start_mm": start,
            "end_mm": end,
        }
        scans = {
            "ray": [volume("part", 0, 1), volume("world", 1, 2), volume("part", 2, 3)],
            "ray__minus": [volume("part", 0, 3)],
            "ray__plus": [volume("part", 0, 3)],
        }
        _, persistent, boundary = classify_internal_world_gaps(
            scans, definitions, "world", 0.01
        )
        self.assertEqual(persistent, [])
        self.assertEqual(len(boundary), 1)

        scans["ray__minus"] = [
            volume("part", 0, 1.1),
            volume("world", 1.1, 1.9),
            volume("part", 1.9, 3),
        ]
        scans["ray__plus"] = [
            volume("part", 0, 1.2),
            volume("world", 1.2, 1.8),
            volume("part", 1.8, 3),
        ]
        _, persistent, boundary = classify_internal_world_gaps(
            scans, definitions, "world", 0.01
        )
        self.assertEqual(len(persistent), 1)
        self.assertEqual(persistent[0]["start_mm"], 1.2)
        self.assertEqual(persistent[0]["end_mm"], 1.8)
        self.assertEqual(boundary, [])

    def test_parses_root_records(self):
        output = "\n".join(
            [
                "SHIFT_BOUNDED_AUDIT\tWORLD\t-1\t1\t-2\t2\t-3\t3",
                "SHIFT_BOUNDED_AUDIT\tPLACEMENT\tpv\tlv\tSteel\t-1\t1\t-1\t1\t-1\t1",
                "SHIFT_BOUNDED_AUDIT\tOVERLAP_COUNT\t1",
                "SHIFT_BOUNDED_AUDIT\tOVERLAP\to\tdetail\t0.1\t0",
                "SHIFT_BOUNDED_AUDIT\tSCAN\tz\t0\tlv\tSteel\t-3\t3",
            ]
        )
        parsed = parse_root_output(output)
        self.assertEqual(parsed["world_bounds_mm"], [[-1, -2, -3], [1, 2, 3]])
        self.assertEqual(parsed["placements"][0]["bounds_mm"], [[-1, -1, -1], [1, 1, 1]])
        self.assertEqual(parsed["root_overlap_count"], 1)
        self.assertEqual(parsed["scans"]["z"][0]["material"], "Steel")

    def test_prunes_only_root_proven_empty_lattice_placement(self):
        root = ET.fromstring(BOUNDED_GDML)
        solids = root.find("solids")
        intersection = ET.SubElement(
            solids, "intersection", {"name": "Cell__Proto_lattice_clip_solid"}
        )
        ET.SubElement(intersection, "first", {"ref": "part"})
        ET.SubElement(intersection, "second", {"ref": "cell"})
        lattice_volume = next(
            volume
            for volume in root.find("structure").findall("volume")
            if volume.attrib["name"] == "LatticeClip_lv"
        )
        lattice_volume.attrib["name"] = "Cell__Proto_lattice_clip_lv"
        lattice_volume.find("solidref").attrib["ref"] = (
            "Cell__Proto_lattice_clip_solid"
        )
        world = next(
            volume
            for volume in root.find("structure").findall("volume")
            if volume.attrib["name"] == "wl"
        )
        next(
            placement
            for placement in world.findall("physvol")
            if placement.attrib["name"] == "Cell__Proto_lattice_pv"
        ).find("volumeref").attrib["ref"] = "Cell__Proto_lattice_clip_lv"
        conversion = {
            "geometry": {
                "bounded_artifact": {},
                "lattice_placement": {
                    "parking_prototypes": ["Proto"],
                    "parking_prototype_count": 1,
                    "lattices": [
                        {
                            "lattice": "Cell",
                            "prototype_count": 1,
                            "prototypes": [{"prototype": "Proto"}],
                        }
                    ],
                }
            },
            "root_tgdml_import": {"validated": False},
        }
        pruning = prune_tree(
            ET.ElementTree(root), conversion, {frozenset(("part", "cell"))}
        )
        self.assertEqual(pruning["empty_lattice_candidate_count"], 1)
        self.assertEqual(
            conversion["geometry"]["lattice_placement"]["lattice_placement_count"],
            0,
        )
        self.assertEqual(
            conversion["geometry"]["bounded_artifact"]["placed_volume_count"], 3
        )


class RootBinaryUnionLoweringTest(unittest.TestCase):
    def lower(self, text=GDML):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "geometry.gdml"
        path.write_text(text, encoding="utf-8")
        report = lower_multi_unions_for_root(path)
        return report, ET.parse(path).getroot()

    def test_preserves_every_operand_and_transform_in_binary_chain(self):
        report, root = self.lower()
        self.assertEqual(report["input_multi_union_count"], 1)
        self.assertEqual(report["input_node_count"], 3)
        self.assertEqual(report["output_binary_union_count"], 2)
        self.assertEqual(report["root_first_operand_transform_count"], 2)
        self.assertEqual(report["remaining_multi_union_count"], 0)

        solids = root.find("solids")
        unions = solids.findall("union")
        self.assertEqual(len(unions), 2)
        first, final = unions
        self.assertEqual(first.attrib["name"], "threeBoxes__root_binary_0001")
        self.assertEqual(first.find("first").attrib["ref"], "boxA")
        self.assertEqual(first.find("second").attrib["ref"], "boxB")
        self.assertEqual(first.find("firstposition").attrib["x"], "-20")
        self.assertEqual(first.find("firstrotation").attrib["z"], "0.1")
        self.assertEqual(final.attrib["name"], "threeBoxes")
        self.assertEqual(final.find("first").attrib["ref"], first.attrib["name"])
        self.assertEqual(final.find("second").attrib["ref"], "boxC")
        self.assertEqual(final.find("positionref").attrib["ref"], "positionC")
        self.assertEqual(final.find("rotationref").attrib["ref"], "rotationC")

    def test_second_pass_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "geometry.gdml"
            path.write_text(GDML, encoding="utf-8")
            lower_multi_unions_for_root(path)
            first = path.read_bytes()
            report = lower_multi_unions_for_root(path)
            self.assertEqual(report["input_multi_union_count"], 0)
            self.assertEqual(path.read_bytes(), first)

    def test_rejects_unknown_node_content(self):
        malformed = GDML.replace(
            '<position name="positionA" x="-20" y="0" z="0" unit="mm"/>',
            '<scale name="scaleA" x="1" y="1" z="1"/>',
        )
        with self.assertRaisesRegex(ProxyModelError, "unsupported multiUnion"):
            self.lower(malformed)


if __name__ == "__main__":
    unittest.main()
