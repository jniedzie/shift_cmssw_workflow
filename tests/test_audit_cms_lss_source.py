#!/usr/bin/env python3

from pathlib import Path
import stat
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from audit_cms_lss_source import (
    IntakeError, active_lines, audit_bundle, legacy_map_comparison,
    material_definition_audit, native_cards, native_map_summary, safe_archive_members,
)


class CmsLssIntakeTest(unittest.TestCase):
    def test_conditionals_do_not_enable_inactive_defines(self):
        source = "#define LHC\n#if LHC\n#define IR5\n#elif SPS\n#define WRONG\n#endif\n#if WRONG\nbad\n#else\ngood\n#endif\n"
        lines, definitions, _ = active_lines(source, "deck")
        self.assertEqual(definitions, ["IR5", "LHC"])
        self.assertEqual(lines, [(10, "good")])

    def test_unsupported_preprocessing_fails_closed(self):
        for source in ("#if A || B\n#endif\n", "#else\n", "#if A\n", "#define A 3\n"):
            with self.subTest(source=source), self.assertRaises(IntakeError):
                active_lines(source, "deck")

    def test_unsafe_zip_members_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "fields.zip"
            for name in ("../map.inp", "/map.inp", "dir\\map.inp", "C:/map.inp"):
                with self.subTest(name=name):
                    with zipfile.ZipFile(archive, "w") as output:
                        output.writestr(name, "x")
                    with self.assertRaises(IntakeError):
                        safe_archive_members(archive)
            with zipfile.ZipFile(archive, "w") as output:
                entry = zipfile.ZipInfo("symlink")
                entry.external_attr = (stat.S_IFLNK | 0o777) << 16
                output.writestr(entry, "/outside")
            with self.assertRaises(IntakeError):
                safe_archive_members(archive)

    def test_missing_unused_include_remains_unresolved(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            (source / "2023.inp").write_text(
                "#define IR5\n#include /external/MB.inp\n#include /external/MQ.inp\n"
                "FREE\nMGNFIELD 1.2 rotate 0 region 0 0 QUAD\n"
                "MGNFIELD 30.0 0.0001 0.01 0.0 0.0 0.0\n")
            with zipfile.ZipFile(source / "fields.zip", "w") as output:
                output.writestr("MQ.inp", "FREE\nMGNCREAT , 4.0, 5.0, 9.7, 0.0, 2.0, , QUAD\nFIXED\n")
            before = {p.name: p.read_bytes() for p in source.iterdir()}
            report = audit_bundle(source)
            deck = report["decks"]["2023.inp"]
            self.assertFalse(report["production_ready"])
            self.assertFalse(deck["source_complete"])
            self.assertFalse(deck["preprocessing_complete"])
            self.assertEqual(deck["assignment_count"], 1)
            self.assertEqual(deck["unresolved_named_fields"], [])
            missing = deck["include_resolution"][0]
            self.assertEqual(missing["resolution"], "missing")
            self.assertFalse(missing["direct_assignment_to_filename_stem"])
            self.assertEqual(before, {p.name: p.read_bytes() for p in source.iterdir()})

    def test_named_field_must_have_definition(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            (source / "deck.inp").write_text("MGNFIELD 1 rotate 0 region 0 0 MISSING\n")
            report = audit_bundle(source)
            self.assertEqual(report["decks"]["deck.inp"]["unresolved_named_fields"], ["MISSING"])

    def test_loose_include_resolves_by_basename(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            (source / "generated_payload").mkdir()
            (source / "generated_payload/ignored.txt").write_text("output\n")
            (source / "2023.inp").write_text(
                "#include /provider/model/MB.inp\n"
                "MGNFIELD 1 0 0 region 0 0 MB\n")
            (source / "MB.inp").write_text(
                "FREE\nMGNCREAT , 4.0, 5.0, 9.7, 0.0, 2.0, , MB\nFIXED\n")
            report = audit_bundle(source)
            deck = report["decks"]["2023.inp"]
            self.assertTrue(deck["preprocessing_complete"])
            self.assertTrue(deck["source_complete"])
            self.assertEqual(deck["unresolved_named_fields"], [])
            include = deck["include_resolution"][0]
            self.assertEqual(include["resolution"], "available-by-basename")
            self.assertEqual(include["archive_candidates"], [])
            self.assertEqual(include["loose_file_candidates"], ["MB.inp"])
            self.assertEqual(include["field_asset_reference"], "MB.inp")

    def test_legacy_comparison_detects_numeric_and_metadata_mismatches(self):
        text = ("MGNCREAT , 204, 2.4, 0, 0, 0, , MAP\n"
                "MGNCREAT , , , , 2, 1, , &\n"
                "MGNCREAT , -1, 0, , 1, 0, , &&\n"
                "MGNDATA , 1, 2, 3, 4, , , MAP\n")
        cards = native_cards(list(enumerate(text.splitlines(), 1)), "map")
        summary, values = native_map_summary(cards)
        self.assertEqual(summary["numeric_value_count"], 4)
        self.assertTrue(all(card["card"] != "MGNDATA" for card in summary["cards"]))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "MAP.dat"
            legacy = "TYPE QUADINT\nQRADIUS 2.4\nXGRID -1 1 2\nYGRID 0 0 1\nDATA\n1 2\n3 4\n"
            path.write_text(legacy)
            equal = legacy_map_comparison(path, summary, values)
            self.assertTrue(equal["comparison_equal"])
            self.assertFalse(equal["placement_or_transport_equivalence"])
            path.write_text(legacy.replace("3 4", "3 5"))
            changed = legacy_map_comparison(path, summary, values)
            self.assertFalse(changed["numeric_arrays_equal"])
            self.assertTrue(changed["metadata_equal"])
            path.write_text(legacy.replace("QRADIUS 2.4", "QRADIUS 2.5"))
            changed = legacy_map_comparison(path, summary, values)
            self.assertTrue(changed["numeric_arrays_equal"])
            self.assertFalse(changed["metadata_equal"])
            self.assertIn("core_radius_cm", changed["metadata_differences"])

    def test_duplicate_material_density_and_composition_checked_independently(self):
        def card(keyword, values, name):
            return f"{keyword:<10}" + "".join(f"{value:>10}" for value in values) + name

        lines = [
            card("MATERIAL", ["", "", "2.4", "", "", ""], "ROCK"),
            card("COMPOUND", ["-0.4", "OXYGEN", "-0.6", "SILICON", "", ""], "ROCK"),
            card("MATERIAL", ["", "", "1.9", "", "", ""], "ROCK"),
            card("COMPOUND", ["-0.6", "SILICON", "-0.4", "OXYGEN", "", ""], "ROCK"),
            card("ASSIGNMA", ["ROCK", "GROUND", "", "", "", ""], ""),
        ]
        audit = material_definition_audit(list(enumerate(lines, 1)), "deck")
        rock = audit["duplicate_materials"]["ROCK"]
        self.assertEqual(audit["conflicting_density_names"], ["ROCK"])
        self.assertEqual(rock["density_values_g_cm3"], [2.4, 1.9])
        self.assertEqual([card["line"] for card in rock["definitions"]], [1, 3])
        self.assertTrue(rock["composition_blocks_equal"])
        self.assertFalse(rock["native_override_or_accumulation_semantics_validated"])
        self.assertEqual(len(rock["active_assignments"]), 1)
        lines[3] = lines[3].replace("-0.6", "-60.").replace("-0.4", "-40.")
        audit = material_definition_audit(list(enumerate(lines, 1)), "deck")
        self.assertTrue(audit["duplicate_materials"]["ROCK"]["composition_blocks_proportional"])
        self.assertEqual(audit["conflicting_composition_names"], [])
        lines[3] = lines[3].replace("-60.", "-0.6").replace("-40.", "-0.4")
        lines[3] = lines[3].replace("-0.4", "-0.5")
        audit = material_definition_audit(list(enumerate(lines, 1)), "deck")
        self.assertEqual(audit["conflicting_composition_names"], ["ROCK"])

    def test_inactive_material_definition_is_excluded(self):
        text = ("FREE\nMATERIAL , , , 2.4, , , , ROCK\n"
                "#if UNUSED\nMATERIAL , , , 1.9, , , , ROCK\n#endif\n")
        lines, _, _ = active_lines(text, "deck")
        audit = material_definition_audit(lines, "deck")
        self.assertEqual(audit["duplicate_material_count"], 0)


if __name__ == "__main__":
    unittest.main()
