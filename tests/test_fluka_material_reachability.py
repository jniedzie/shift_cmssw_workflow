import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from fluka_material_reachability import material_reachability


class MaterialReachabilityTest(unittest.TestCase):
    def inventory(self):
        return {"regionDict": {"names": ["R1", "R2"]},
                "material_assignments": {"R1": ["MIX", None, None], "R2": "BASE"},
                "parsed_materials": {
                    "MIX": {"components": [{"material": "BASE"}, {"material": "ISO"}]},
                    "BASE": {"components": []},
                    "ISO": {"components": [], "massNumber": 6},
                    "UNUSED_ISO": {"components": [], "massNumber": 7}}}

    def test_transitive_reachability_and_dependency_order_without_mutation(self):
        inventory = self.inventory()
        original = copy.deepcopy(inventory)
        result = material_reachability(inventory)
        self.assertEqual(result["reachable_materials"], ["BASE", "ISO", "MIX"])
        self.assertEqual(result["reachable_isotopes"], ["ISO"])
        self.assertEqual(result["unused_isotopes"], ["UNUSED_ISO"])
        self.assertLess(result["dependency_order"].index("ISO"), result["dependency_order"].index("MIX"))
        self.assertEqual(inventory, original)
        self.assertFalse(result["production_ready"])

    def test_missing_assignment_and_undefined_dependency_fail(self):
        inventory = self.inventory()
        del inventory["material_assignments"]["R1"]
        with self.assertRaisesRegex(ValueError, "missing material assignment"):
            material_reachability(inventory)
        inventory = self.inventory()
        del inventory["parsed_materials"]["ISO"]
        with self.assertRaisesRegex(ValueError, "MIX -> ISO"):
            material_reachability(inventory)

    def test_cycle_and_duplicate_region_fail(self):
        inventory = self.inventory()
        inventory["parsed_materials"]["BASE"]["components"] = [{"material": "MIX"}]
        with self.assertRaisesRegex(ValueError, "cyclic"):
            material_reachability(inventory)
        inventory = self.inventory()
        inventory["regionDict"]["names"].append("R1")
        with self.assertRaisesRegex(ValueError, "duplicate"):
            material_reachability(inventory)


if __name__ == "__main__":
    unittest.main()
