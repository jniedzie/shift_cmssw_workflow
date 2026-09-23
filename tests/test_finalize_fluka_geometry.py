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
