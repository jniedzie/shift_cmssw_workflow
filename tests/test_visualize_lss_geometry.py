#!/usr/bin/env python3

import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile


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

    def test_discards_degenerate_and_duplicate_triangles(self):
        first = MODULE.Mesh(
            name="first",
            category="cms_muon",
            vertices=[(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
            triangles=[(0, 1, 2), (0, 1, 1)],
        )
        duplicate = MODULE.Mesh(
            name="duplicate",
            category="cms_calo",
            vertices=[tuple(coordinate + 0.00004 if axis == 0 else coordinate
                            for axis, coordinate in enumerate(vertex))
                      for vertex in first.vertices],
            triangles=[(2, 1, 0)],
        )
        seen = set()
        positions, normals = MODULE.display_triangle_data(first, seen)
        duplicate_positions, duplicate_normals = MODULE.display_triangle_data(duplicate, seen)
        self.assertEqual(len(positions), 3)
        self.assertEqual(len(normals), 3)
        self.assertEqual(duplicate_positions, [])
        self.assertEqual(duplicate_normals, [])

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "test.usdz"
            MODULE.write_usdz(output, [first, duplicate])
            with zipfile.ZipFile(output) as archive:
                usda = archive.read("cms_lss_geometry.usda").decode()
        self.assertEqual(usda.count('def Mesh "'), 1)
        self.assertIn('def Mesh "cms_muon_geometry"', usda)
        self.assertIn("int[] faceVertexCounts = [3]", usda)
        self.assertIn("int[] faceVertexIndices = [0, 1, 2]", usda)
        self.assertNotIn('def Camera "PresentationCamera"', usda)
        self.assertIn("float inputs:opacity = 0.22", usda)
        self.assertIn("float inputs:opacityThreshold = 0.01", usda)
        self.assertIn("uniform bool doubleSided = false", usda)
        self.assertIn("startTimeCode = 0", usda)
        self.assertIn(f"endTimeCode = {MODULE.ANIMATION_SECONDS * MODULE.ANIMATION_FPS}", usda)
        self.assertIn("matrix4d xformOp:transform.timeSamples", usda)

    def test_model_animation_is_closed_and_focuses_collimators(self):
        samples = MODULE.presentation_model_samples()
        self.assertEqual(len(samples), MODULE.ANIMATION_SECONDS * MODULE.ANIMATION_FPS + 1)
        self.assertEqual(samples[0][1], samples[-1][1])
        self.assertEqual(samples[0][1], MODULE.model_matrix(MODULE.PRESENTATION_CENTRE, 1.0, 0.0))

        _, collimator_matrix = samples[int(3.5 * MODULE.ANIMATION_FPS)]
        point = (158.0, 0.0, 0.0, 1.0)
        transformed = tuple(
            sum(point[row] * collimator_matrix[row][column] for row in range(4))
            for column in range(4)
        )
        for actual, expected in zip(transformed, (*MODULE.PRESENTATION_CENTRE, 1.0)):
            self.assertAlmostEqual(actual, expected, places=10)
        self.assertLess(MODULE.MODEL_KEYFRAMES[2][3], 0.0)
        cms_yaws = [keyframe[3] for keyframe in MODULE.MODEL_KEYFRAMES
                    if 9.5 <= keyframe[0] <= 12.5]
        self.assertEqual(cms_yaws, sorted(cms_yaws, reverse=True))
        cms_point = (*MODULE.CMS_PRESENTATION_FOCUS, 1.0)
        cms_start = int(9.5 * MODULE.ANIMATION_FPS)
        cms_stop = int(12.5 * MODULE.ANIMATION_FPS)
        for _, cms_matrix in samples[cms_start:cms_stop + 1]:
            cms_transformed = tuple(
                sum(cms_point[row] * cms_matrix[row][column] for row in range(4))
                for column in range(4)
            )
            for actual, expected in zip(cms_transformed, (*MODULE.PRESENTATION_CENTRE, 1.0)):
                self.assertAlmostEqual(actual, expected, places=10)
        self.assertEqual(MODULE.USDZ_COLORS["lss_magnet"][3], 1.0)
        self.assertEqual(MODULE.USDZ_COLORS["cms_muon"][3], 0.35)
        self.assertEqual(MODULE.USDZ_COLORS["lss_beamline"][3], 1.0)

    def test_geant_muons_are_embedded_and_animated(self):
        mesh = MODULE.Mesh(
            name="test triangle", category="cms_muon",
            vertices=[(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
            triangles=[(0, 1, 2)],
        )
        tracks = [{
            "pdg_id": 13,
            "points_m": [[0.0, 0.0, 148.0], [0.1, 0.2, 80.0], [0.2, 0.3, 0.0]],
        }]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "event.usdz"
            MODULE.write_usdz(output, [mesh], tracks, 14)
            with zipfile.ZipFile(output) as archive:
                usda = archive.read("cms_lss_geometry.usda").decode()
        self.assertIn('def Xform "Event_14"', usda)
        self.assertIn('def Mesh "muon_0_minus_track"', usda)
        self.assertIn('def Sphere "muon_0_minus_head"', usda)
        self.assertIn("double3 xformOp:translate.timeSamples", usda)
        self.assertIn("color3f inputs:emissiveColor", usda)
        self.assertEqual(usda.count("color3f inputs:diffuseColor = (0, 0.78, 0.28)"), 2)

    def test_glb_has_distinct_high_contrast_materials(self):
        mesh = MODULE.Mesh(
            name="test triangle",
            category="cms_muon",
            vertices=[(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
            triangles=[(0, 1, 2)],
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "test.glb"
            MODULE.write_glb(output, [mesh])
            payload = output.read_bytes()
        magic, version, total_length = struct.unpack_from("<4sII", payload)
        json_length, json_kind = struct.unpack_from("<I4s", payload, 12)
        document = json.loads(payload[20:20 + json_length])
        self.assertEqual((magic, version, total_length), (b"glTF", 2, len(payload)))
        self.assertEqual(json_kind, b"JSON")
        colors = [material["pbrMetallicRoughness"]["baseColorFactor"]
                  for material in document["materials"]]
        self.assertEqual(len(colors), len(set(tuple(color) for color in colors)))
        self.assertTrue(all("emissiveFactor" in material for material in document["materials"]))


if __name__ == "__main__":
    unittest.main()
