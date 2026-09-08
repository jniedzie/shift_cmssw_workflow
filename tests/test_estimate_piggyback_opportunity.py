import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from estimate_piggyback_opportunity import estimate


class EstimatePiggybackOpportunityTest(unittest.TestCase):
    def test_default_scale(self):
        result = estimate(100_000, 3_000, 386)
        self.assertAlmostEqual(result["colliding_bx_rate_hz"], 40_000_000 * 386 / 3564)
        self.assertAlmostEqual(
            result["usable_stored_readout_probability_per_colliding_bx"],
            3000 / (40_000_000 * 386 / 3564),
        )

    def test_rejects_invalid_slots(self):
        with self.assertRaises(ValueError):
            estimate(1, 1, 3565)


if __name__ == "__main__":
    unittest.main()
