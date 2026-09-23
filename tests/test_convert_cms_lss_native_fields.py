import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from audit_cms_lss_source import native_cards, active_lines, native_map_summary
from convert_cms_lss_native_fields import (  # noqa: E402
    FieldTranslationError,
    convert_native_fields,
    legacy_map_text,
)


class NativeFieldConversionTest(unittest.TestCase):
    def test_lossless_two_component_map_and_analytic_quadrupole(self):
        mapped = (
            "MGNCREAT , 204, 2.4, 0, 0, 0, , MAP\n"
            "MGNCREAT , , , , 2, 2, , &\n"
            "MGNCREAT , -1, -2, , 1, 2, , &&\n"
            "MGNDATA , 1, 2, 3, 4, 5, 6, MAP\n"
            "MGNDATA , 7, 8, , , , , &\n"
        )
        cards = native_cards(active_lines(mapped, "map")[0], "map")
        summary, values = native_map_summary(cards)
        text = legacy_map_text(summary, values)
        self.assertIn("TYPE QUADINT\n", text)
        self.assertIn("XGRID -1 1 2\n", text)
        self.assertEqual(text.split("DATA\n", 1)[1].splitlines(),
                         ["1 2", "3 4", "5 6", "7 8"])

        analytic = "MGNCREAT , 4, 5, 9.7, 0, 2, , QUAD\n"
        cards = native_cards(active_lines(analytic, "analytic")[0], "analytic")
        summary, values = native_map_summary(cards)
        text = legacy_map_text(summary, values)
        self.assertIn("TYPE QUAD\n", text)
        self.assertNotIn("DATA", text)

    def test_rejects_unsupported_curved_map(self):
        summary = {"metadata_status": "supported-comparison-subset", "metadata": {
            "type": "QUAD", "symmetry": "NONE", "core_radius_cm": 2.4,
            "analytical_origin_cm": [0, 0, 0], "azimuth_degrees": 0,
            "bend_radius_cm": 100, "sagitta_cm": 0, "x_grid": None, "y_grid": None,
        }}
        with self.assertRaisesRegex(FieldTranslationError, "bend radius"):
            legacy_map_text(summary, [])

    def test_bundle_conversion_resolves_only_referenced_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            output = Path(directory) / "output"
            source.mkdir()
            (source / "deck.inp").write_text(
                "#include /external/MB.inp\n"
                "#include /external/MAP.inp\n"
                "FREE\n"
                "MGNFIELD 1.5 ROT 0 CELL 0 0 MAP\n"
                "ROT-DEFI 1000 0 0 0 0 -10 ROT\n"
                "LATTICE CELL 0 CELL CELL\n",
                encoding="ascii")
            native = (
                "MGNCREAT , 204, 2.4, 0, 0, 0, , MAP\n"
                "MGNCREAT , , , , 2, 2, , &\n"
                "MGNCREAT , -1, -2, , 1, 2, , &&\n"
                "MGNDATA , 1, 2, 3, 4, 5, 6, MAP\n"
                "MGNDATA , 7, 8, , , , , &\n"
            )
            with zipfile.ZipFile(source / "fields.zip", "w") as archive:
                archive.writestr("MAP.inp", native)
            manifest = convert_native_fields(source, "deck.inp", output)
            self.assertEqual(set(manifest["maps"]), {"MAP"})
            self.assertEqual(manifest["inline_analytic_fields"], [])
            self.assertTrue(manifest["maps"]["MAP"]["roundtrip_metadata_equal"])
            self.assertTrue(manifest["maps"]["MAP"]["roundtrip_numeric_arrays_equal"])
            self.assertEqual(manifest["unresolved_includes"], ["MB.inp"])
            self.assertEqual(json.loads((output / "field_manifest.json").read_text())["maps"],
                             manifest["maps"])
            self.assertTrue((output / "MAP.dat").is_file())
            self.assertFalse(manifest["production_ready"])


if __name__ == "__main__":
    unittest.main()
