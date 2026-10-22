# Code style: AI-code. AI-generated controlled source-contract regression tests.
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from shift_source_placement import TEST_SCOPE, ASSUMED_SCOPE, placement_contract


class SourcePlacementContractTest(unittest.TestCase):
    def test_explicit_source_and_test_scope_without_normalization_claim(self):
        value = placement_contract([-6.8, 0., 14800.], "VtxSmeared", test_scope=TEST_SCOPE)
        self.assertEqual(value["target_position_cm"], [-6.8, 0., 14800.])
        self.assertFalse(value["source_distribution_qualified"])
        self.assertFalse(value["normalization_ready"])
        self.assertEqual(value["new_GEN_exposure"], 0)

    def test_rejects_unknown_or_production_scope(self):
        for scope in (None, "production", "", True):
            with self.subTest(scope=scope), self.assertRaises(ValueError):
                placement_contract([0., 0., 14800.], "generatorSmeared", test_scope=scope)

    def test_rejects_bad_points_and_implicit_input(self):
        for point in (None, [1., 2.], [1., 2., 3., 4.], [True, 0., 1.], [math.nan, 0., 1.], [0., math.inf, 1.], [1.e308, 0., 1.]):
            with self.subTest(point=point), self.assertRaises(ValueError):
                placement_contract(point, "VtxSmeared", test_scope=TEST_SCOPE)
        for label in (None, "", 13):
            with self.subTest(label=label), self.assertRaises(ValueError):
                placement_contract([0., 0., 14800.], label, test_scope=TEST_SCOPE)

    def test_event_map_requires_exact_unique_identities_and_explicit_scope(self):
        rows = [dict(event_id=[12, 1, 7], target_position_cm=[-6.8, 0., 14800.])]
        result = placement_contract(None, "VtxSmeared", test_scope=ASSUMED_SCOPE, event_positions=rows)
        self.assertEqual(result["event_positions"], rows)
        self.assertIsNone(result["target_position_cm"])
        for bad in ([], rows + rows, [dict(rows[0], event_id=[12, 1, True])],
                    [dict(rows[0], event_id=[12, 0, 7])], [dict(rows[0], event_id=[12, 1, 2**64])]):
            with self.subTest(rows=bad), self.assertRaises(ValueError):
                placement_contract(None, "VtxSmeared", test_scope=ASSUMED_SCOPE, event_positions=bad)
        with self.assertRaises(ValueError):
            placement_contract([0., 0., 14800.], "VtxSmeared", test_scope=ASSUMED_SCOPE, event_positions=rows)
        with self.assertRaises(ValueError):
            placement_contract([0., 0., 14800.], "VtxSmeared", test_scope=TEST_SCOPE, event_positions=rows)


if __name__ == "__main__":
    unittest.main()
