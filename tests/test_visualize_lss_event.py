#!/usr/bin/env python3

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).parents[1] / "scripts" / "visualize_lss_event.py"
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("visualize_lss_event", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class VisualizeLssEventTest(unittest.TestCase):
    def test_summary_uses_event_level_statistical_uncertainties(self):
        events = [
            {"gen_muons": [{}, {}], "reco_muons": [], "vertices": []},
            {"gen_muons": [{}, {}],
             "reco_muons": [{"gen_index": 0}, {"gen_index": -1}], "vertices": [{}]},
        ]
        geant_events = {
            1: [
                {"pdg_id": 13, "points_m": [[0.0, 0.0, 0.0]],
                 "materials": [{"name": "AIR", "path_m": 3.0}]},
                {"pdg_id": -13, "points_m": [[20.0, 0.0, 0.0]],
                 "materials": [{"name": "CONCRETE", "path_m": 1.0}]},
            ],
            2: [
                {"pdg_id": 13, "points_m": [[20.0, 0.0, 0.0]],
                 "materials": [
                     {"name": "AIR", "path_m": 1.0},
                     {"name": "CONCRETE", "path_m": 3.0},
                 ]},
            ],
        }

        stats = MODULE.summary_stats(events, geant_events)

        self.assertEqual(stats["generated_muons"], 4)
        self.assertAlmostEqual(stats["count_stat_uncertainty"]["generated_muons"], 0.0)
        self.assertAlmostEqual(stats["count_stat_uncertainty"]["geant_muons"], 1.0)
        self.assertAlmostEqual(stats["count_stat_uncertainty"]["reconstructed_muons"], 2.0)
        material_errors = stats["material_path_fraction_stat_uncertainty_percent"]
        self.assertAlmostEqual(material_errors["air"], 25.0)
        self.assertAlmostEqual(material_errors["concrete"], 25.0)

        with tempfile.TemporaryDirectory() as directory:
            names = MODULE.draw_summary(Path(directory), stats)
            self.assertEqual(len(names), 3)
            self.assertTrue(all((Path(directory) / name).is_file() for name in names))


if __name__ == "__main__":
    unittest.main()
