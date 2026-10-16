# Code style: AI-code. AI-generated source-card and fixed-exposure guard tests.
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from run_dark_photon_meson_gen import resolved_parent_models, run
from dark_photon_meson_source import coefficient_bound


class MesonRunnerTest(unittest.TestCase):
    def test_closed_eta_does_not_evaluate_its_unphysical_pole(self):
        card = json.loads((Path(__file__).resolve().parents[1] /
            'config/campaigns/dark_photon_meson_model.json').read_text())
        configuration = dict(parents=[dict(pdg_id=221,mass_min_gev=.547862,mass_max_gev=.547862),
                                      dict(pdg_id=331,mass_min_gev=.95578,mass_max_gev=.95978)])
        models = resolved_parent_models(card, configuration, .749)
        self.assertFalse(models[221]['kinematically_open'])
        self.assertTrue(models[331]['kinematically_open'])
        self.assertGreater(coefficient_bound(112.97, .2, models, .749), 0.)
        # The closed eta contributes zero even though its form-factor pole
        # is singular outside the physical decay interval.
        self.assertEqual(coefficient_bound(112.97, .2, {221: models[221]}, .749), 0.)

    def test_single_collision_exposure_is_rejected_before_creating_output(self):
        arguments = SimpleNamespace(mass=.3, epsilon=1.e-6, source_collisions=1,
                                    seed=761016999, run_number=1761016999, timeout=60)
        with self.assertRaisesRegex(ValueError, 'fixed source exposure'):
            run(arguments)


if __name__ == '__main__':
    unittest.main()
