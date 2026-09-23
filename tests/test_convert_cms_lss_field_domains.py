import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from convert_cms_lss_field_domains import FieldDomainError, convert_domains


class FieldDomainConversionTest(unittest.TestCase):
    def fixture(self, directory, region="OUT 5 +OUT -HOLE"):
        root = Path(directory)
        deck = root / "active.inp"
        deck.write_text(
            "RCC OUT 0 0 -2 0 0 4 5\n"
            "ZCC HOLE 0 0 1\n"
            "RPP BOX -2 3 -4 5 -6 7\n"
            f"{region}\n"
            "BOXCELL 5 +BOX\n"
            "ROT-DEFI 1000 0 0 0 0 -10 ROT\n",
            encoding="ascii",
        )
        manifest = root / "fields.json"
        manifest.write_text(json.dumps({
            "maps": {"MAP": {"output": "MAP.dat"}},
            "inline_analytic_definitions": {"DIPOLE": {"metadata": {
                "type": "DIPOLE", "core_radius_cm": 4.0,
                "analytical_origin_cm": [0.0, 0.0, 0.0],
            }}},
            "assignments": [
                {"line": 10, "sdum": "MAP", "what": ["2", "ROT", "0", "BOXCELL", "0", "0"]},
                {"line": 11, "sdum": "DIPOLE", "what": ["-0.3", "ROT", "0", "OUT", "0", "0"]},
            ],
        }), encoding="utf-8")
        geometry = root / "geometry.json"
        geometry.write_text(json.dumps({"lattice_conversion": {"lattices": [
            {"cell": "OUT", "physical_cell_bounds_mm": [[-50, -50, 80], [50, 50, 120]]},
            {"cell": "BOXCELL", "physical_cell_bounds_mm": [[-20, -40, 40], [30, 50, 170]]},
        ]}}), encoding="utf-8")
        return deck, manifest, geometry

    def test_exact_annulus_box_transform_and_dipole(self):
        with tempfile.TemporaryDirectory() as directory:
            report = convert_domains(*self.fixture(directory))
            self.assertTrue(report["domain_conversion_validated"])
            self.assertEqual(report["element_count"], 2)
            box, annulus = report["elements"]
            self.assertEqual(annulus["origin_model_cm"], [-0.0, -0.0, 10.0])
            self.assertEqual((annulus["inner_radius_cm"], annulus["outer_radius_cm"]), (1.0, 4.0))
            self.assertEqual(annulus["assigned_target_domain"]["outer_radius_cm"], 5.0)
            self.assertTrue(annulus["analytic_core_clipped"])
            self.assertEqual(box["type"], "flukaMap2D")
            self.assertEqual(annulus["field_model_tesla"], [0.0, -0.3, 0.0])
            self.assertEqual(box["minimum_cm"], [-2.0, -4.0, -6.0])
            self.assertFalse(report["production_ready"])

    def test_rejects_union_or_rotated_transform(self):
        with tempfile.TemporaryDirectory() as directory:
            files = self.fixture(directory, "OUT 5 +OUT | +HOLE")
            with self.assertRaisesRegex(FieldDomainError, "unions"):
                convert_domains(*files)
        with tempfile.TemporaryDirectory() as directory:
            deck, manifest, geometry = self.fixture(directory)
            deck.write_text(deck.read_text().replace("1000 0 0", "1000 1 0"))
            with self.assertRaisesRegex(FieldDomainError, "rotated"):
                convert_domains(deck, manifest, geometry)


if __name__ == "__main__":
    unittest.main()
