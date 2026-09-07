import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("losses", Path(__file__).parents[1] / "scripts/analyze_lss_muon_losses.py")
losses = importlib.util.module_from_spec(spec)
spec.loader.exec_module(losses)


class UpstreamLossTests(unittest.TestCase):
    def test_downstream_stop_is_not_upstream_loss(self):
        start = {"position_mm": "(0,0,20000)"}
        end = {"position_mm": "(0,0,-1200000)", "kinetic_energy_GeV": "0"}
        points = [{"position_mm": "(0,0,10000)", "material": "EARTHBOH", "step_length_mm": "10000"},
                  {"position_mm": "(0,0,-1200000)", "material": "EARTHBOH", "step_length_mm": "1210000"}]
        result = losses.before_cms(start, end, points, 11000)
        self.assertTrue(result["cms_plane_reached"])
        self.assertFalse(result["low_energy_end_before_cms"])
        self.assertAlmostEqual(result["upstream_rock_path_m"], 9)

    def test_missing_trace_is_unknown(self):
        result = losses.before_cms({"position_mm": "(0,0,20000)"}, {}, [], 11000)
        self.assertIsNone(result["cms_plane_reached"])

    def test_rock_then_stop_elsewhere_is_not_rock_stop(self):
        category, _ = losses.classify({}, {"volume": "copper", "kinetic_energy_GeV": "0"},
                                     [{"stage": "volume-transition", "pre_volume": "rock"}])
        self.assertEqual(category, "stopped_in_material")

    def test_negative_target_upstream_stop(self):
        result = losses.before_cms({"position_mm": "(0,0,-20000)"},
                                  {"position_mm": "(0,0,-15000)", "kinetic_energy_GeV": "0"},
                                  [{"position_mm": "(0,0,-15000)", "material": "EARTHBOH", "step_length_mm": "5000"}], 11000)
        self.assertFalse(result["cms_plane_reached"])
        self.assertTrue(result["low_energy_end_before_cms"])


if __name__ == "__main__":
    unittest.main()
