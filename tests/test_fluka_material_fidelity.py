import importlib
import inspect
from pathlib import Path
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from fluka_material_fidelity import (  # noqa: E402
    MaterialFidelityError, audit_material_cards, material_fidelity_guard,
)


class MaterialFidelityTest(unittest.TestCase):
    def setUp(self):
        self.flu = importlib.import_module("pyg4ometry.fluka")
        self.g4 = importlib.import_module("pyg4ometry.geant4")
        self.converter = importlib.import_module("pyg4ometry.convert.fluka2g4materials")
        self.freg = self.flu.FlukaRegistry()
        self.greg = self.g4.Registry()
        self.ledger = {}

    def convert(self):
        with material_fidelity_guard(self.ledger):
            return self.converter.makeFlukaToG4MaterialsMap(self.freg, self.greg)

    def test_builtin_carbon_is_carbon_and_masses_not_truncated(self):
        result = self.convert()
        for name, z, mass in [("CARBON", 6, 12.0107), ("OXYGEN", 8, 15.9994),
                               ("IRON", 26, 55.845), ("ALUMINUM", 13, 26.981538)]:
            element = self.greg.materialDict[name + "_element"]
            self.assertEqual(element.Z, z)
            self.assertEqual(element.A, mass)
            self.assertEqual(result[name].atomic_number, z)
            self.assertEqual(result[name].atomic_weight, mass)
        self.assertEqual(self.greg.materialDict["CARBON_element"].symbol, "C")
        self.assertEqual(result["CARBON"].density, 2.0)
        self.assertFalse(self.ledger["production_ready"])

    def test_material_card_retains_explicit_mass_and_isotope(self):
        card = self.flu.Card("MATERIAL", what1=6, what2=13.003355,
                             what3=2.1, what6=13, sdum="CUSTOM_C")
        with material_fidelity_guard(self.ledger):
            parsed = self.flu.Material.fromCard(card, self.freg)
            self.assertEqual(parsed.atomicMass, 13.003355)
            self.assertEqual(parsed.massNumber, 13)
            result = self.converter.makeFlukaToG4MaterialsMap(self.freg, self.greg)
        isotope = self.greg.materialDict["CUSTOM_C_element_isotope_13"]
        self.assertEqual((isotope.Z, isotope.N, isotope.a), (6, 13, 13.003355))
        self.assertEqual(result["CUSTOM_C"].density, 2.1)

    def test_missing_isotopic_molar_mass_fails_closed(self):
        self.flu.Material("C13", 6, 2.0, massNumber=13, flukaregistry=self.freg)
        with self.assertRaisesRegex(MaterialFidelityError, "authoritative molar mass"):
            self.convert()

    def test_explicit_dependency_selection_does_not_export_unused_isotope(self):
        carbon = self.freg.materials["CARBON"]
        self.flu.Material("UNUSED_ISO", 6, 2.0, massNumber=13, flukaregistry=self.freg)
        self.flu.Compound("MIX", 2.0, [(carbon, 1.0)], "mass", flukaregistry=self.freg)
        before = dict(self.freg.materials)
        with material_fidelity_guard(self.ledger, required_materials=["MIX"]):
            result = self.converter.makeFlukaToG4MaterialsMap(self.freg, self.greg)
        self.assertEqual(set(result), {"MIX", "CARBON"})
        self.assertEqual(dict(self.freg.materials), before)
        self.assertIn("UNUSED_ISO", self.ledger["dependency_selection"]["unused_definitions_not_exported"])
        with material_fidelity_guard({}, required_materials=["UNUSED_ISO"]):
            with self.assertRaisesRegex(MaterialFidelityError, "authoritative molar mass"):
                self.converter.makeFlukaToG4MaterialsMap(self.freg, self.g4.Registry())

    def test_dependency_selection_rejects_stale_constituent_definition(self):
        carbon = self.freg.materials["CARBON"]
        self.flu.Compound("MIX", 2.0, [(carbon, 1.0)], "mass", flukaregistry=self.freg)
        self.flu.Material("CARBON", 8, 3.0, atomicMass=17.0, flukaregistry=self.freg)
        with material_fidelity_guard({}, required_materials=["MIX"]):
            with self.assertRaisesRegex(MaterialFidelityError, "differs from the current registry"):
                self.converter.makeFlukaToG4MaterialsMap(self.freg, self.greg)

    def test_dependency_selection_rejects_deep_stale_binding(self):
        carbon = self.freg.materials["CARBON"]
        old_inner = self.flu.Compound("INNER", 2.0, [(carbon, 1.0)], "mass", flukaregistry=self.freg)
        self.flu.Compound("OUTER", 2.0, [(old_inner, 1.0)], "mass", flukaregistry=self.freg)
        replacement = self.flu.Material("CARBON", 8, 3.0, atomicMass=17.0, flukaregistry=self.freg)
        # Same immediate INNER signature, but OUTER still points to the old
        # nested carbon definition while the registry now exports oxygen.
        self.freg.materials["INNER"] = self.flu.Compound("INNER", 2.0, [(replacement, 1.0)], "mass")
        with material_fidelity_guard({}, required_materials=["OUTER"]):
            with self.assertRaisesRegex(MaterialFidelityError, "differs from the current registry"):
                self.converter.makeFlukaToG4MaterialsMap(self.freg, self.greg)

    def test_dependency_selection_rejects_cyclic_stale_object_graph(self):
        carbon = self.freg.materials["CARBON"]
        old_inner = self.flu.Compound("INNER", 2.0, [(carbon, 1.0)], "mass", flukaregistry=self.freg)
        self.flu.Compound("OUTER", 2.0, [(old_inner, 1.0)], "mass", flukaregistry=self.freg)
        old_inner.fractions = [(old_inner, 1.0)]
        self.freg.materials["INNER"] = self.flu.Compound("INNER", 2.0, [(carbon, 1.0)], "mass")
        with material_fidelity_guard({}, required_materials=["OUTER"]):
            with self.assertRaisesRegex(MaterialFidelityError, "cyclic constituent object"):
                self.converter.makeFlukaToG4MaterialsMap(self.freg, self.greg)

    def test_explicit_material_roots_reject_mixed_and_empty_names(self):
        for roots in ([], ["CARBON", 1], [""], 3):
            with self.subTest(roots=roots):
                with material_fidelity_guard({}, required_materials=roots):
                    with self.assertRaises(MaterialFidelityError):
                        self.converter.makeFlukaToG4MaterialsMap(self.freg, self.greg)

    def test_predefined_name_override_uses_actual_z_and_mass(self):
        self.flu.Material("CARBON", 8, 3.0, atomicMass=17.25, flukaregistry=self.freg)
        result = self.convert()
        element = self.greg.materialDict["CARBON_element"]
        self.assertEqual((element.Z, element.A, element.symbol), (8, 17.25, "O"))
        self.assertEqual(result["CARBON"].atomic_weight, 17.25)

    def test_atomic_mass_volume_fractions(self):
        light = self.flu.Material("LIGHT", 1, 2, atomicMass=2, flukaregistry=self.freg)
        heavy = self.flu.Material("HEAVY", 8, 8, atomicMass=18, flukaregistry=self.freg)
        for kind in ("atomic", "mass", "volume"):
            self.flu.Compound("MIX_" + kind, 5, [(light, 2), (heavy, 1)], kind,
                              flukaregistry=self.freg)
        result = self.convert()
        for kind, expected in [("atomic", 4/22), ("mass", 2/3), ("volume", 4/12)]:
            components = result["MIX_" + kind].components
            self.assertAlmostEqual(components[0][1], expected)
            self.assertAlmostEqual(sum(x[1] for x in components), 1.0)

    def test_invalid_values_rejected(self):
        for property_name, value in [("atomicNumber", 6.5), ("atomicMass", float("nan")),
                                      ("density", -1), ("massNumber", 12.5)]:
            with self.subTest(property=property_name):
                self.freg = self.flu.FlukaRegistry()
                self.greg = self.g4.Registry()
                material = self.flu.Material("BAD", 6, 2, atomicMass=12, flukaregistry=self.freg)
                setattr(material, property_name, value)
                with self.assertRaises(MaterialFidelityError):
                    self.convert()

    def test_fraction_and_multiplication_overflow_rejected(self):
        for kind, weights in [("atomic", [1e308, 1]), ("mass", [1e308, 1e308]),
                              ("volume", [1e308, 1]), ("mass", [0, 1])]:
            with self.subTest(kind=kind, weights=weights):
                self.freg = self.flu.FlukaRegistry()
                self.greg = self.g4.Registry()
                one = self.flu.Material("ONE", 6, 2, atomicMass=12, flukaregistry=self.freg)
                two = self.flu.Material("TWO", 8, 3, atomicMass=16, flukaregistry=self.freg)
                self.flu.Compound("BAD", 2, list(zip([one, two], weights)), kind,
                                  flukaregistry=self.freg)
                with self.assertRaises(MaterialFidelityError):
                    self.convert()

    def test_duplicate_cards_reported_without_selecting_winner(self):
        cards = [self.flu.Card("MATERIAL", what1=6, what3=density, sdum="DUP")
                 for density in (1.0, 2.0)]
        audit = audit_material_cards(cards)
        self.assertEqual(len(audit["duplicate_material_definitions"]["DUP"]), 2)
        self.assertFalse(audit["native_material_semantics_validated"])
        self.assertEqual([card.what3 for card in cards], [1.0, 2.0])

    def test_patches_restored_after_exception_and_nested_context(self):
        conversion = importlib.import_module("pyg4ometry.convert.fluka2Geant4")
        original = (inspect.getattr_static(self.flu.Material, "fromCard"),
                    self.converter.makeFlukaToG4MaterialsMap, conversion._makeFlukaToG4MaterialsMap)
        with self.assertRaisesRegex(RuntimeError, "test"):
            with material_fidelity_guard({}):
                outer = self.converter.makeFlukaToG4MaterialsMap
                with material_fidelity_guard({}):
                    self.assertIsNot(outer, self.converter.makeFlukaToG4MaterialsMap)
                self.assertIs(outer, self.converter.makeFlukaToG4MaterialsMap)
                raise RuntimeError("test")
        restored = (inspect.getattr_static(self.flu.Material, "fromCard"),
                    self.converter.makeFlukaToG4MaterialsMap, conversion._makeFlukaToG4MaterialsMap)
        for before, after in zip(original, restored):
            self.assertIs(before, after)

    def test_written_gdml_preserves_correct_element_values(self):
        from pyg4ometry.gdml import Writer
        result = self.convert()
        solid = self.g4.solid.Box("box", 10, 10, 10, self.greg)
        world = self.g4.LogicalVolume(solid, result["CARBON"], "world", self.greg)
        self.greg.setWorld(world)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "probe.gdml"
            writer = Writer()
            writer.addDetector(self.greg)
            writer.write(str(path))
            tree = ET.parse(path)
        element = tree.find("./materials/element[@name='CARBON_element']")
        self.assertEqual(element.get("formula"), "C")
        self.assertEqual(float(element.get("Z")), 6)
        self.assertEqual(float(element.find("atom").get("value")), 12.0107)


if __name__ == "__main__":
    unittest.main()
