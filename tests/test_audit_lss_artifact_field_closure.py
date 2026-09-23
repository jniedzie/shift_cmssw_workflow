import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from audit_lss_artifact_field_closure import CoordinateClosureError, audit_closure


def write_json(path, value):
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ArtifactFieldClosureTest(unittest.TestCase):
    def inputs(self, root):
        geometry_path = root / "geometry.json"
        geometry = {
            "lattice_conversion": {"passed": True, "lattices": [{
                "cell": "CELL",
                "physical_cell_bounds_mm": [[90.0, 180.0, 270.0], [110.0, 220.0, 330.0]],
            }]},
        }
        write_json(geometry_path, geometry)
        domains_path = root / "domains.json"
        domains = {
            "domain_conversion_validated": True,
            "geometry_report_sha256": digest(geometry_path),
            "element_count": 1,
            "elements": [{
                "name": "MAP.CELL", "target_region": "CELL",
                "origin_model_cm": [10.0, 20.0, 30.0],
                "minimum_cm": [-1.0, -2.0, -3.0],
                "maximum_cm": [1.0, 2.0, 3.0],
                "assigned_target_domain": {
                    "minimum_cm": [-1.0, -2.0, -3.0],
                    "maximum_cm": [1.0, 2.0, 3.0],
                },
            }],
        }
        write_json(domains_path, domains)
        final_path = root / "final.json"
        final = {
            "passed": True,
            "conversion_report_sha256": digest(geometry_path),
            "finalization": {
                "artifact_origin_in_model_mm": [50.0, 100.0, 150.0],
                "model_to_artifact_translation_mm": [-50.0, -100.0, -150.0],
            },
        }
        write_json(final_path, final)
        return final_path, domains_path, geometry_path

    def test_exact_closure(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = self.inputs(Path(directory))
            report = audit_closure(*paths)
            self.assertTrue(report["passed"])
            self.assertEqual(report["artifact_origin_in_model_cm"], [5.0, 10.0, 15.0])
            self.assertEqual(report["element_count"], 1)
            self.assertEqual(report["maximum_artifact_bound_residual_cm"], 0.0)

    def test_rejects_domain_geometry_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self.inputs(root)
            domains = json.loads(paths[1].read_text())
            domains["elements"][0]["origin_model_cm"][2] += 0.01
            write_json(paths[1], domains)
            with self.assertRaisesRegex(CoordinateClosureError, "disagree"):
                audit_closure(*paths)

    def test_rejects_field_cell_crossing_model_interface(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self.inputs(root)
            final = json.loads(paths[0].read_text())
            final["finalization"]["model_interface_partition"] = {
                "axis": "model_z", "boundary_mm": 280.0,
                "lattice_placements_affected": 0,
            }
            write_json(paths[0], final)
            with self.assertRaisesRegex(CoordinateClosureError, "crosses the model interface"):
                audit_closure(*paths)

    def test_rejects_wrong_finalization_translation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self.inputs(root)
            final = json.loads(paths[0].read_text())
            final["finalization"]["model_to_artifact_translation_mm"][0] = 0.0
            write_json(paths[0], final)
            with self.assertRaisesRegex(CoordinateClosureError, "inverse"):
                audit_closure(*paths)


if __name__ == "__main__":
    unittest.main()
