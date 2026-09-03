#!/usr/bin/env python3

import importlib.util
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET


SCRIPT = Path(__file__).parents[1] / "scripts" / "visualize_lss_geometry.py"
SPEC = importlib.util.spec_from_file_location("visualize_lss_geometry", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
NS = {"c": "http://www.collada.org/2005/11/COLLADASchema"}


class PreviewColladaTest(unittest.TestCase):
    def test_writes_normals_and_omits_empty_categories(self):
        mesh = MODULE.Mesh(
            name="test triangle",
            category="cms_muon",
            vertices=[(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
            triangles=[(0, 1, 2)],
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "test.dae"
            MODULE.write_preview_collada(output, [mesh])
            root = ET.parse(output).getroot()

        geometries = root.findall(".//c:geometry", NS)
        self.assertEqual([geometry.get("id") for geometry in geometries], ["cms_muon-geometry"])
        triangles = geometries[0].find(".//c:triangles", NS)
        inputs = [(entry.get("semantic"), entry.get("offset"))
                  for entry in triangles.findall("c:input", NS)]
        self.assertEqual(inputs, [("VERTEX", "0"), ("NORMAL", "1")])
        self.assertEqual(triangles.get("count"), "1")
        self.assertEqual(triangles.find("c:p", NS).text.split(), ["0", "0", "1", "1", "2", "2"])

        arrays = geometries[0].findall(".//c:float_array", NS)
        self.assertEqual(len(arrays), 2)
        for array in arrays:
            self.assertEqual(int(array.get("count")), len(array.text.split()))


if __name__ == "__main__":
    unittest.main()
