import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from fixed_target_generation import beam_settings, process_settings, canonical_settings


class GenerationSettingsTest(unittest.TestCase):
    def test_invalid_bins(self):
        for lower, upper in ((1, 1), (5, 1), (-1, 5), (0, float("inf")), (float("nan"), 5)):
            with self.assertRaises(ValueError):
                process_settings("jpsi", lower, upper)

    def test_qcd_cutoff(self):
        with self.assertRaises(ValueError):
            process_settings("qcd", 0, 5)
        self.assertIn("HardQCD:all = on", process_settings("qcd", 1, 5))

    def test_dy_full_continuum(self):
        settings = process_settings("dy", 2, 5)
        self.assertIn("WeakZ0:gmZmode = 0", settings)
        self.assertIn("23:onIfMatch = 13 -13", settings)
        self.assertIn("23:mMin = 2", settings)
        self.assertFalse(any("pTHat" in item for item in settings))

    def test_preserves_cms_lifetime_policy(self):
        for sample in ("qcd", "dy", "jpsi"):
            settings = process_settings(sample, 2, 5) + beam_settings(6800)
            self.assertFalse(any(key in item for item in settings for key in
                ("limitTau", "tau0Max", "minWidth", "mayDecay", "Tune:", "PDF:", "vertex")))

    def test_beam_direction(self):
        self.assertIn("Beams:eA = 0.", beam_settings(6800))
        self.assertIn("Beams:eB = 6800", beam_settings(6800))
        for energy in (0, -1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                beam_settings(energy)

    def test_setting_comparison(self):
        self.assertEqual(canonical_settings(["Tune:pp = 14"]), canonical_settings(["Tune:pp 14"]))


if __name__ == "__main__":
    unittest.main()
