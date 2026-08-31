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
    install_transformed_infinite_cylinder_centre_workaround,
    lower_multi_unions_for_root,
    restore_transformed_infinite_cylinder_centres,
)


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
