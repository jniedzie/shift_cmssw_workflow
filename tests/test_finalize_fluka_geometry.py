import sys
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from finalize_fluka_geometry import (  # noqa: E402
    FinalizationError,
    finalize_gdml,
    partition_same_material_siblings,
    physical_partition,
    simplify_empty_booleans,
)


GDML = """<?xml version="1.0"?>
<gdml>
  <materials>
    <material name="Vacuum" Z="1"><D value="1e-25" unit="g/cm3"/><atom value="1" unit="g/mole"/></material>
    <material name="Steel" Z="26"><D value="7" unit="g/cm3"/><atom value="56" unit="g/mole"/></material>
  </materials>
  <solids>
    <box name="world_solid" x="1000" y="1000" z="1000" lunit="mm"/>
    <box name="part" x="1" y="1" z="1" lunit="mm"/>
  </solids>
  <structure>
    <volume name="Reservoir_lv"><materialref ref="Vacuum"/><solidref ref="part"/></volume>
    <volume name="Prototype_lv"><materialref ref="Steel"/><solidref ref="part"/></volume>
    <volume name="Physical_lv"><materialref ref="Steel"/><solidref ref="part"/></volume>
    <volume name="AirOwner_lv"><materialref ref="Vacuum"/><solidref ref="part"/></volume>
    <volume name="AirTrimmed_lv"><materialref ref="Vacuum"/><solidref ref="part"/></volume>
    <volume name="Cell__Prototype_lattice_lv"><materialref ref="Steel"/><solidref ref="part"/></volume>
    <volume name="wl">
      <materialref ref="Vacuum"/><solidref ref="world_solid"/>
      <physvol name="Reservoir_pv"><volumeref ref="Reservoir_lv"/></physvol>
      <physvol name="Prototype_pv"><volumeref ref="Prototype_lv"/></physvol>
      <physvol name="Physical_pv"><volumeref ref="Physical_lv"/><position name="p" x="20" y="40" z="60" unit="mm"/></physvol>
      <physvol name="AirOwner_pv"><volumeref ref="AirOwner_lv"/><position name="ao" x="12" y="5" z="7" unit="mm"/></physvol>
      <physvol name="AirTrimmed_pv"><volumeref ref="AirTrimmed_lv"/><position name="at" x="2" y="3" z="4" unit="mm"/></physvol>
      <physvol name="Cell__Prototype_lattice_pv"><volumeref ref="Cell__Prototype_lattice_lv"/></physvol>
    </volume>
  </structure>
  <setup name="Default" version="1"><world ref="wl"/></setup>
</gdml>
"""


def conversion():
    return {
        "geometry": {
            "coverage": {
                "converted_region_count": 3,
                "converted_regions": ["Reservoir", "Prototype", "Physical"],
            }
        },
        "lattice_conversion": {
            "passed": True,
            "placement_count": 1,
            "lattices": [
                {
                    "cell": "Cell",
                    "physical_cell_bounds_mm": [[-5, 1, 10], [5, 3, 50]],
                    "placements": [
                        {
                            "prototype": "Prototype",
                            "placement_name": "Cell__Prototype_lattice_pv",
                        }
                    ],
                }
            ],
        },
    }


class PhysicalPartitionTest(unittest.TestCase):
    def test_explicit_reservoir_removes_sources_but_keeps_physical_bounds(self):
        bounds = {
            "Reservoir": [[-30, -40, 0], [30, -20, 100]],
            "Prototype": [[-2, -35, 10], [2, -25, 20]],
            "Physical": [[0, 0, 20], [10, 20, 40]],
            "Cell": [[-5, 1, 10], [5, 3, 50]],
        }
        result = physical_partition(conversion(), bounds, "Reservoir")
        self.assertEqual(result["removed_regions"], ["Prototype", "Reservoir"])
        self.assertEqual(result["retained_model_bounds_mm"], [[-5, 0, 10], [10, 20, 50]])

    def test_rejects_reservoir_that_does_not_contain_copied_prototype(self):
        bounds = {
            "Reservoir": [[-1, -1, -1], [1, 1, 1]],
            "Prototype": [[10, 10, 10], [11, 11, 11]],
            "Physical": [[0, 0, 20], [10, 20, 40]],
            "Cell": [[-5, 1, 10], [5, 3, 50]],
        }
        with self.assertRaisesRegex(FinalizationError, "does not contain"):
            physical_partition(conversion(), bounds, "Reservoir")

    def test_rejects_reservoir_containing_physical_lattice_cell(self):
        bounds = {
            "Reservoir": [[-30, -40, 0], [30, 40, 100]],
            "Prototype": [[-2, -35, 10], [2, -25, 20]],
            "Physical": [[40, 0, 20], [50, 20, 40]],
            "Cell": [[-5, 1, 10], [5, 3, 50]],
        }
        with self.assertRaisesRegex(FinalizationError, "physical lattice cells"):
            physical_partition(conversion(), bounds, "Reservoir")


class FinalizeGdmlTest(unittest.TestCase):
    def test_simplifies_proven_empty_intersection_without_dropping_parent_union(self):
        root = ET.fromstring("""<gdml>
          <solids>
            <box name="left" x="2" y="2" z="2" lunit="mm"/>
            <box name="right" x="2" y="2" z="2" lunit="mm"/>
            <intersection name="empty"><first ref="left"/><second ref="right"/>
              <position x="3" y="0" z="0" unit="mm"/></intersection>
            <box name="kept" x="4" y="4" z="4" lunit="mm"/>
            <union name="combined"><first ref="kept"/><second ref="empty"/>
              <position x="99" y="0" z="0" unit="mm"/></union>
          </solids>
          <structure><volume name="placed"><solidref ref="combined"/></volume></structure>
        </gdml>""")
        report = simplify_empty_booleans(root)
        reference = root.find("structure/volume/solidref")
        self.assertEqual(reference.attrib["ref"], "kept")
        names = {solid.attrib["name"] for solid in root.find("solids")}
        self.assertNotIn("empty", names)
        self.assertNotIn("combined", names)
        self.assertEqual(report["removed_boolean_count"], 2)
        self.assertEqual(
            [proof["result"] for proof in report["proofs"]],
            ["empty", "alias_first"],
        )

    def test_rejects_physically_referenced_proven_empty_solid(self):
        root = ET.fromstring("""<gdml>
          <solids>
            <box name="left" x="2" y="2" z="2" lunit="mm"/>
            <box name="right" x="2" y="2" z="2" lunit="mm"/>
            <intersection name="empty"><first ref="left"/><second ref="right"/>
              <position x="3" y="0" z="0" unit="mm"/></intersection>
          </solids>
          <structure><volume name="placed"><solidref ref="empty"/></volume></structure>
        </gdml>""")
        with self.assertRaisesRegex(FinalizationError, "directly references proven-empty"):
            simplify_empty_booleans(root)

    def test_removes_only_source_placements_and_preserves_lattice_copy(self):
        partition = {
            "removed_regions": ["Prototype", "Reservoir"],
            "retained_model_bounds_mm": [[-5, 0, 10], [10, 20, 50]],
        }
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.gdml"
            output = Path(directory) / "output.gdml"
            source.write_text(GDML, encoding="utf-8")
            report = finalize_gdml(source, output, conversion(), partition, 2.0)
            root = ET.parse(output).getroot()
            volumes = {v.attrib["name"]: v for v in root.find("structure").findall("volume")}
            world = volumes["wl"]
            placements = {p.attrib["name"]: p for p in world.findall("physvol")}
            self.assertEqual(
                set(placements),
                {
                    "Physical_pv",
                    "AirOwner_pv",
                    "AirTrimmed_pv",
                    "Cell__Prototype_lattice_pv",
                },
            )
            self.assertEqual(report["preserved_lattice_placement_count"], 1)
            self.assertEqual(report["removed_source_placement_count"], 2)
            self.assertEqual(report["world_dimensions_mm"], [19.0, 24.0, 44.0])
            self.assertEqual(report["artifact_origin_in_model_mm"], [2.5, 10.0, 30.0])

    def test_model_z_minimum_removes_clips_and_preserves_lattice(self):
        partition = {
            "removed_regions": ["Prototype", "Reservoir"],
            "retained_model_bounds_mm": [[-10, -10, 0], [20, 30, 50]],
        }
        source_bounds = {
            "Reservoir": [[-30, -40, 0], [30, -20, 100]],
            "Prototype": [[-2, -35, 10], [2, -25, 20]],
            "Physical": [[0, 0, 20], [10, 20, 40]],
            "AirOwner": [[0, 0, 0], [5, 5, 10]],
            "AirTrimmed": [[0, 0, 30], [5, 5, 40]],
        }
        converted = conversion()
        converted["lattice_conversion"]["lattices"][0]["physical_cell_bounds_mm"] = [
            [-5, 1, 30], [5, 3, 50]
        ]
        rotated = GDML.replace(
            '<position name="p" x="20" y="40" z="60" unit="mm"/>',
            '<position name="p" x="20" y="40" z="30" unit="mm"/>'
            '<rotation name="r" x="1.5707963267948966" y="0" z="0" unit="rad"/>',
        )
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.gdml"
            output = Path(directory) / "output.gdml"
            source.write_text(rotated, encoding="utf-8")
            report = finalize_gdml(
                source, output, converted, partition, 2.0,
                source_bounds=source_bounds, model_z_min_mm=25.0,
            )
            interface = report["model_interface_partition"]
            self.assertEqual(interface["removed_placements"], ["AirOwner_pv"])
            self.assertEqual(interface["clipped_placement_count"], 1)
            self.assertEqual(interface["clipped_placements"][0]["region"], "Physical")
            self.assertEqual(interface["retained_model_bounds_mm"][0][2], 25.0)
            root = ET.parse(output).getroot()
            volumes = {v.attrib["name"]: v for v in root.find("structure").findall("volume")}
            placements = {p.attrib["name"] for p in volumes["wl"].findall("physvol")}
            self.assertEqual(placements, {
                "Physical_pv", "AirTrimmed_pv", "Cell__Prototype_lattice_pv",
            })
            physical_solid = volumes["Physical_lv"].find("solidref").attrib["ref"]
            intersection = next(
                item for item in root.find("solids")
                if item.attrib.get("name") == physical_solid
            )
            self.assertEqual(intersection.tag, "intersection")
            self.assertEqual(intersection.find("second").attrib["ref"],
                             "shift_model_z_min_clip_box")
            position = intersection.find("position")
            self.assertAlmostEqual(float(position.attrib["x"]), -15.0)
            self.assertAlmostEqual(float(position.attrib["y"]), -7.5)
            self.assertAlmostEqual(float(position.attrib["z"]), -30.0)
            rotation = intersection.find("rotation")
            self.assertAlmostEqual(float(rotation.attrib["x"]), -1.5707963267948966)
            self.assertEqual(report["artifact_origin_in_model_mm"][2], 38.5)
            self.assertEqual(report["world_dimensions_mm"][2], 27.0)
            self.assertEqual(report["world_padding_policy"], "symmetric_xy_upper_z_only")

    def test_model_z_minimum_rejects_affected_lattice_cell(self):
        partition = {
            "removed_regions": ["Prototype", "Reservoir"],
            "retained_model_bounds_mm": [[-5, 0, 10], [10, 20, 50]],
        }
        source_bounds = {
            "Reservoir": [[-30, -40, 0], [30, -20, 100]],
            "Prototype": [[-2, -35, 10], [2, -25, 20]],
            "Physical": [[0, 0, 20], [10, 20, 40]],
            "AirOwner": [[0, 0, 30], [5, 5, 40]],
            "AirTrimmed": [[0, 0, 30], [5, 5, 40]],
        }
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.gdml"
            output = Path(directory) / "output.gdml"
            source.write_text(GDML, encoding="utf-8")
            with self.assertRaisesRegex(FinalizationError, "affects physical lattice"):
                finalize_gdml(
                    source, output, conversion(), partition, 2.0,
                    source_bounds=source_bounds, model_z_min_mm=25.0,
                )

    def test_fails_closed_if_reported_lattice_copy_is_missing(self):
        partition = {
            "removed_regions": ["Prototype", "Reservoir"],
            "retained_model_bounds_mm": [[-5, 0, 10], [10, 20, 50]],
        }
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.gdml"
            output = Path(directory) / "output.gdml"
            source.write_text(GDML.replace("Cell__Prototype_lattice_pv", "Wrong_pv"), encoding="utf-8")
            with self.assertRaisesRegex(FinalizationError, "missing reported lattice"):
                finalize_gdml(source, output, conversion(), partition, 2.0)

    def test_same_material_precedence_preserves_union_and_records_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.gdml"
            source.write_text(GDML, encoding="utf-8")
            root = ET.parse(source).getroot()
            solids = root.find("solids")
            volumes = {
                volume.attrib["name"]: volume
                for volume in root.find("structure").findall("volume")
            }
            report = partition_same_material_siblings(
                solids, volumes["wl"], volumes, [("AirOwner", "AirTrimmed")]
            )
            subtraction = next(
                solid
                for solid in solids
                if solid.attrib.get("name") == report[0]["output_solid"]
            )
            self.assertEqual(subtraction.find("first").attrib["ref"], "part")
            self.assertEqual(subtraction.find("second").attrib["ref"], "part")
            position = subtraction.find("position")
            self.assertEqual(
                [position.attrib[axis] for axis in "xyz"], ["10.0", "2.0", "3.0"]
            )
            self.assertEqual(
                volumes["AirTrimmed_lv"].find("solidref").attrib["ref"],
                report[0]["output_solid"],
            )


if __name__ == "__main__":
    unittest.main()
