import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from stage_cms_lss_cmssw_payload import PayloadStagingError, stage_payload


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


class CmsLssPayloadStagingTest(unittest.TestCase):
    def inputs(self, root):
        gdml = root / "geometry.gdml"
        gdml.write_text("<gdml/>\n", encoding="ascii")
        final = root / "final.json"
        write_json(final, {"passed": True, "output_gdml_sha256": digest(gdml)})
        root_audit = root / "root.json"
        write_json(root_audit, {"passed": True, "gdml_sha256": digest(gdml), "overlap_count": 0})
        material = root / "material.json"
        write_json(material, {"passed": True, "undefined_materials": []})
        map_file = root / "MAP.dat"
        map_file.write_text("TYPE QUAD\nQORIGIN 0 0 0\nQRADIUS 2\n", encoding="ascii")
        field_manifest = root / "fields.json"
        write_json(field_manifest, {
            "maps": {"MAP": {"output": "MAP.dat", "output_sha256": digest(map_file)}},
            "payload_dependency_closure": {
                "status": "pass", "unresolved_includes_not_consumed": ["UNUSED.inp"],
            },
        })
        domains = root / "domains.json"
        write_json(domains, {
            "field_manifest_sha256": digest(field_manifest),
            "domain_conversion_validated": True,
            "element_count": 2,
            "elements": [
                {"name": "MAP.CELL", "type": "flukaMap2D", "map_file": "MAP.dat",
                 "field_scale": 1.25, "origin_model_cm": [0, 0, 10],
                 "minimum_cm": [-2, -2, -5], "maximum_cm": [2, 2, 5],
                 "bounds_shape": "cylinderZ", "bounds_center_cm": [0, 0, 0],
                 "inner_radius_cm": 0, "outer_radius_cm": 2},
                {"name": "DIPOLE.CELL", "type": "uniform", "field_model_tesla": [0, -2, 0],
                 "field_scale": -2, "origin_model_cm": [0, 0, 20],
                 "minimum_cm": [-3, -4, -5], "maximum_cm": [3, 4, 5],
                 "bounds_shape": "box"},
            ],
        })
        closure = root / "closure.json"
        write_json(closure, {
            "passed": True,
            "field_domains_sha256": digest(domains),
            "finalization_report_sha256": digest(final),
            "artifact_origin_in_model_cm": [1.0, 2.0, 3.0],
        })
        return dict(
            geometry_gdml=gdml, finalization_report=final, root_audit=root_audit,
            material_audit=material, field_manifest=field_manifest, field_domains=domains,
            coordinate_audit=closure, output_dir=root / "staged", payload_name="cms_ir5_2023",
            python_module="shiftLssCmsIr5_2023_cff",
            function_name="shiftLssCmsIr5_2023FieldElements",
        )

    def test_stages_checksummed_cmssw_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.inputs(Path(directory))
            report = stage_payload(**args)
            output = args["output_dir"]
            self.assertTrue(report["staging_complete"])
            self.assertFalse(report["production_ready"])
            self.assertEqual(report["element_count"], 2)
            self.assertEqual(report["unresolved_native_includes_not_consumed"], ["UNUSED.inp"])
            self.assertTrue((output / report["geometry_file_in_path"]).is_file())
            module = output / "PhysicsTools/ShiftMuonSegments/python/shiftLssCmsIr5_2023_cff.py"
            text = module.read_text()
            compile(text, str(module), "exec")
            self.assertIn("shiftLssCmsIr5_2023FieldElements", text)
            self.assertIn("PhysicsTools/ShiftMuonSegments/data/lss/cms_ir5_2023", text)
            self.assertIn("map_file='MAP.dat'", text)
            stored = json.loads((output / "payload_manifest.json").read_text())
            self.assertEqual(stored["files"], report["files"])

    def test_rejects_failed_overlap_gate_without_output(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.inputs(Path(directory))
            write_json(args["root_audit"], {
                "passed": True, "gdml_sha256": digest(args["geometry_gdml"]), "overlap_count": 1,
            })
            with self.assertRaisesRegex(PayloadStagingError, "overlap"):
                stage_payload(**args)
            self.assertFalse(args["output_dir"].exists())

    def test_rejects_field_lineage_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.inputs(Path(directory))
            domains = json.loads(args["field_domains"].read_text())
            domains["field_manifest_sha256"] = "0" * 64
            write_json(args["field_domains"], domains)
            closure = json.loads(args["coordinate_audit"].read_text())
            closure["field_domains_sha256"] = digest(args["field_domains"])
            write_json(args["coordinate_audit"], closure)
            with self.assertRaisesRegex(PayloadStagingError, "lineage"):
                stage_payload(**args)

    def test_rejects_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.inputs(Path(directory))
            args["output_dir"].mkdir()
            with self.assertRaisesRegex(PayloadStagingError, "must not exist"):
                stage_payload(**args)


if __name__ == "__main__":
    unittest.main()
