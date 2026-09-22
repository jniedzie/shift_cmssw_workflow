import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from convert_fluka_geometry_diagnostic import (audit_raw_world_bounds, main, normalized_orthogonality_guard,
                                               orthogonality_metrics, prepare_deck, registry_inventory,
                                               run_secondary_region_preflight)
from ir1_fluka_geometry import ProxyModelError


class DiagnosticPreprocessorTest(unittest.TestCase):
    def test_world_gate_rejects_known_outside_source_and_missing_bounds(self):
        preflight = {"non_null_regions": ["box"],
                     "primary_classification": {"bounds_mm": {"box": [[0, 0, 0], [1, 1, 10]]}},
                     "secondary_classification": {}}
        self.assertFalse(audit_raw_world_bounds(preflight, (10, 10, 10))["passed"])
        self.assertTrue(audit_raw_world_bounds(preflight, (30, 30, 30))["passed"])
        preflight["primary_classification"] = {}
        self.assertEqual(audit_raw_world_bounds(preflight, (30, 30, 30))["missing_source_bounds"], ["box"])

    def test_partial_registry_inventory_serializes_body_objects(self):
        inventory = registry_inventory(SimpleNamespace(bodyDict=[SimpleNamespace(name="box")],
                                                        regionDict={}, assignmas={}))
        self.assertEqual(inventory["bodyDict"]["names"], ["box"])
        json.dumps(inventory)

    def prepare(self, text, *, omit=False):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        source = directory / "source.inp"
        source.write_text(text, encoding="utf-8")
        output, report = prepare_deck(source, directory, omit_field_map_includes=omit)
        return output.read_text(encoding="ascii"), report

    def test_inactive_defines_and_nested_conditions_do_not_leak(self):
        text, report = self.prepare("#define ON\n#if MISSING\n#define LEAK\n#if ON\nwrong1\n#endif\n#else\nright1\n#endif\n#if LEAK\nwrong2\n#elif ON\nright2\n#else\nwrong3\n#endif\n")
        self.assertIn("right1", text)
        self.assertIn("right2", text)
        self.assertNotIn("wrong", text)
        self.assertEqual(report["active_defines"], ["ON"])
        self.assertEqual(len(text.splitlines()), 16)

    def test_inactive_undef_does_not_disable_active_flag(self):
        text, _ = self.prepare("#define ON\n#if OFF\n#undef ON\n#endif\n#if ON\nkept\n#endif\n")
        self.assertIn("kept", text)

    def test_map_include_is_explicit_and_recorded_exactly(self):
        line = "#include /external/magnetic_field_maps/fluka_format/MB.inp\n"
        with self.assertRaises(ProxyModelError):
            self.prepare(line)
        text, report = self.prepare(line, omit=True)
        self.assertEqual(text, "\n")
        entry = report["omitted_field_map_includes"][0]
        self.assertEqual(entry["text"], line.rstrip("\n"))
        self.assertEqual(entry["line_sha256"], hashlib.sha256(line.encode()).hexdigest())
        self.assertFalse(report["production_ready"])

    def test_unrelated_include_cannot_be_omitted(self):
        for line in ("#include geometry.inp\n", "#include /tmp/magnetic_field_maps/fluka_format/../geometry.inp\n"):
            with self.subTest(line=line), self.assertRaises(ProxyModelError):
                self.prepare(line, omit=True)

    def test_rejects_unsupported_or_unbalanced_preprocessing(self):
        for text in ("#if A || B\n#endif\n", "#define A 2\n", "#if A\n", "#else\n", "#endif\n",
                     "#if A\n#else\n#else\n#endif\n", "#if A\n#else\n#elif B\n#endif\n", "#pragma test\n"):
            with self.subTest(text=text), self.assertRaises(ProxyModelError):
                self.prepare(text)

    def test_inactive_includes_are_conditional_omissions_only(self):
        text, report = self.prepare("#if OFF\n#include geometry.inp\n#endif\n")
        self.assertEqual(text, "\n\n\n")
        self.assertEqual(report["omitted_field_map_includes"], [])

    def test_variable_expansions_rejected_but_geometry_transforms_preserved(self):
        for variable in ("$LENGTH", "$[LENGTH]", "$(LENGTH)"):
            with self.subTest(variable=variable), self.assertRaises(ProxyModelError):
                self.prepare(f"RPP box 0 {variable} 0 1 0 1\n")
        text, _ = self.prepare("$start_translat 1 2 3\n$end_translat\n")
        self.assertEqual(text, "$start_translat 1 2 3\n$end_translat\n")

    def test_unicode_comments_are_audited_without_changing_active_syntax(self):
        text, report = self.prepare("* angle 45°\nRPP box 0 1 0 1 0 1 ! 45°\n")
        self.assertEqual(text, "* angle 45\\xb0\nRPP box 0 1 0 1 0 1 ! 45\\xb0\n")
        self.assertEqual(len(report["comment_unicode_escapes"]), 2)
        with self.assertRaises(ProxyModelError):
            self.prepare("RPP böx 0 1 0 1 0 1\n")


class OrthogonalityGuardTest(unittest.TestCase):
    def test_scale_independent_roundoff_check_and_unchanged_vectors(self):
        vectors = [[-3266.78482610047, 0, -4184.1878936712],
                   [-296.133258388322020, 0, 231.204635066677611], [0, 321.8, 0]]
        before = json.dumps(vectors)
        for scale in (1e-8, 1, 10, 1e8):
            dots = orthogonality_metrics([[scale*x for x in v] for v in vectors])
            self.assertLess(max(map(abs, dots)), 1e-14)
        self.assertEqual(before, json.dumps(vectors))

    def test_skew_zero_nan_and_infinite_vectors_rejected(self):
        for third in ([1e-5, 0, 1], [0, 0, 0], [0, float("nan"), 1], [0, 0, float("inf")]):
            with self.subTest(third=third), self.assertRaises(ValueError):
                orthogonality_metrics(([1, 0, 0], [0, 1, 0], third))

    @unittest.skipUnless(importlib.util.find_spec("pyg4ometry"), "pyg4ometry not installed")
    def test_guard_records_only_new_roundoff_acceptances_and_preserves_inputs(self):
        from pyg4ometry.fluka.vector import Three
        body = importlib.import_module("pyg4ometry.fluka.body")
        vectors = [Three([1e10, 1e10, 0]), Three([1e10, -1e10 + 1e-5, 0]), Three([0, 0, 1e10])]
        before = [list(vector) for vector in vectors]
        with self.assertRaises(ValueError):
            body._raiseIfNotAllMutuallyPerpendicular(*vectors, "roundoff")
        ledger = []
        with normalized_orthogonality_guard(ledger):
            body._raiseIfNotAllMutuallyPerpendicular(*vectors, "roundoff")
            body._raiseIfNotAllMutuallyPerpendicular(Three([1, 0, 0]), Three([0, 1, 0]), Three([0, 0, 1]), "exact")
        self.assertEqual(len(ledger), 1)
        self.assertEqual([list(vector) for vector in vectors], before)

    @unittest.skipUnless(importlib.util.find_spec("pyg4ometry"), "pyg4ometry not installed")
    def test_guard_restored_after_exception(self):
        body = importlib.import_module("pyg4ometry.fluka.body")
        original = body._raiseIfNotAllMutuallyPerpendicular
        with self.assertRaises(RuntimeError):
            with normalized_orthogonality_guard([]):
                self.assertIsNot(body._raiseIfNotAllMutuallyPerpendicular, original)
                raise RuntimeError("test")
        self.assertIs(body._raiseIfNotAllMutuallyPerpendicular, original)


@unittest.skipUnless(importlib.util.find_spec("pyg4ometry"), "pyg4ometry not installed")
class DiagnosticConversionSmokeTest(unittest.TestCase):
    def test_nested_unions_have_the_expected_independent_csg_volume(self):
        from pyg4ometry.fluka import Reader
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source = directory / "nested.inp"
            source.write_text("GEOBEGIN                                                              COMBNAME\n"
                              "0 0\nRPP AA 0 4 0 4 0 4\nRPP BB 4 8 0 4 0 4\n"
                              "RPP CC 1 2 -1 5 -1 5\nRPP DD 6 7 -1 5 -1 5\nEND\n"
                              "TESTREG 5 +( | +AA | +BB ) -( | +CC | +DD )\nEND\nGEOEND\n"
                              f"{'ASSIGNMA':<10}{'IRON':>10}{'TESTREG':>10}\nSTOP\n")
            normalized, report = prepare_deck(source, directory)
            registry = Reader(str(normalized)).flukaregistry
            # Two disjoint 4x4x4 cm boxes minus two 1x4x4 cm slices.
            self.assertAlmostEqual(abs(registry.regionDict["TESTREG"].mesh().volume()),
                                   96000.0, delta=1e-3)
            self.assertTrue(report["nested_boolean_normalization"])

    def test_complete_small_geometry_remains_explicitly_diagnostic(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source = directory / "box.inp"
            source.write_text("GEOBEGIN                                                              COMBNAME\n"
                              "0 0\nRPP box -1 1 -1 1 -1 1\nEND\nBOXREG 5 +box\nEND\nGEOEND\n"
                              f"{'ASSIGNMA':<10}{'IRON':>10}{'BOXREG':>10}\nSTOP\n")
            output = directory / "output"
            self.assertEqual(main(["--input", str(source), "--output-dir", str(output),
                                   "--world-dimensions-mm", "100,100,100", "--geometry-only"]), 0)
            report = json.loads((output / "conversion_report.json").read_text())
            self.assertTrue(report["parser_passed"])
            self.assertTrue(report["conversion_passed"])
            self.assertFalse(report["production_ready"])
            self.assertEqual(report["geometry"]["coverage"]["converted_regions"], ["BOXREG"])
            elements = {item.get("name"): item for item in
                        ET.parse(output / "geometry_diagnostic.gdml").findall("./materials/element")}
            self.assertNotIn("CARBON_element", elements)  # No unused library materials.
            self.assertEqual(report["material_fidelity"]["dependency_selection"]["dependency_order"], ["IRON"])
            self.assertAlmostEqual(float(elements["IRON_element"].find("atom").get("value")), 55.845)
            self.assertFalse(report["material_fidelity"]["native_material_semantics_validated"])
            secondary = run_secondary_region_preflight(output / "normalized_geometry_diagnostic.inp",
                                                       ["BOXREG"], output, 10.0, (100, 100, 100))
            self.assertEqual(secondary["backend"], "pycsg")
            self.assertEqual(secondary["non_null_regions"], ["BOXREG"])

    def test_unused_isotope_stays_in_inventory_but_not_export(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source = directory / "unused.inp"
            source.write_text("GEOBEGIN                                                              COMBNAME\n"
                              "0 0\nRPP box -1 1 -1 1 -1 1\nEND\nBOXREG 5 +box\nEND\nGEOEND\n"
                              f"{'MATERIAL':<10}{3:>10}{'':>10}{1:>10}{'':>10}{'':>10}{6:>10}LITHIUM6\n"
                              f"{'ASSIGNMA':<10}{'IRON':>10}{'BOXREG':>10}\nSTOP\n")
            output = directory / "output"
            self.assertEqual(main(["--input", str(source), "--output-dir", str(output),
                                   "--world-dimensions-mm", "100,100,100", "--geometry-only",
                                   "--ordinary-regions-only"]), 0)
            report = json.loads((output / "conversion_report.json").read_text())
            self.assertEqual(report["material_reachability"]["unused_isotopes"], ["LITHIUM6"])
            self.assertIn("LITHIUM6", report["registry"]["parsed_materials"])
            self.assertNotIn("LITHIUM6", (output / "geometry_diagnostic.gdml").read_text())
            self.assertEqual(report["omitted_lattice_cells"], [])
            self.assertFalse(report["production_ready"])

    def test_missing_isotope_mass_stops_before_gdml_export(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source = directory / "isotope.inp"
            source.write_text("GEOBEGIN                                                              COMBNAME\n"
                              "0 0\nRPP box -1 1 -1 1 -1 1\nEND\nBOXREG 5 +box\nEND\nGEOEND\n"
                              f"{'MATERIAL':<10}{3:>10}{'':>10}{1:>10}{'':>10}{'':>10}{6:>10}LITHIUM6\n"
                              f"{'ASSIGNMA':<10}{'LITHIUM6':>10}{'BOXREG':>10}\nSTOP\n")
            output = directory / "output"
            self.assertEqual(main(["--input", str(source), "--output-dir", str(output),
                                   "--world-dimensions-mm", "100,100,100", "--geometry-only"]), 1)
            report = json.loads((output / "conversion_report.json").read_text())
            self.assertTrue(report["parser_passed"])
            self.assertFalse(report["conversion_passed"])
            self.assertFalse(report["production_ready"])
            self.assertIn("authoritative molar mass", report["error"]["message"])
            self.assertFalse((output / "geometry_diagnostic.gdml").exists())


if __name__ == "__main__":
    unittest.main()
