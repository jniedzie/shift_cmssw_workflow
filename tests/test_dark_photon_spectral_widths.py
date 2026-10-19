# Code style: AI-code. AI-generated full-mixing spectral adapter regression tests.
import ast
import hashlib
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from dark_photon_spectral_widths import full_hypercharge_model


class ModelProvider:
    """Minimal model loader; the physical coupling calculation is not mocked."""
    def Model(self, name, path=None):
        if name == 'dark_photon':
            return SimpleNamespace(xfs={'e': (lambda mass: 0., lambda mass: -.30282212088)})
        module = Path(path) / (name + '.py')
        coupling_values = ast.literal_eval(ast.parse(module.read_text()).body[0].value)
        return SimpleNamespace(coupling_values=coupling_values)


class HyperchargeSpectralModelTest(unittest.TestCase):
    def test_neutrinos_retain_only_left_handed_coupling(self):
        with tempfile.TemporaryDirectory() as directory:
            model, photon, card = full_hypercharge_model(ModelProvider(), 10., .1, directory)
            self.assertEqual(len(model.coupling_values), 12)
            for name in ['nue', 'numu', 'nutau']:
                axial, vector = model.coupling_values[name]
                self.assertNotEqual(vector, 0.)
                self.assertEqual(axial, vector)
            self.assertAlmostEqual(card['mixing']['em_coefficient'] / .1, 1.0050003112713495)
            module = Path(directory) / card['module']
            self.assertEqual(hashlib.sha256(module.read_bytes()).hexdigest(), card['module_sha256'])
            self.assertEqual(card['global_coupling'], .1)

    def test_model_input_does_not_shift_the_fixed_physical_z_mass(self):
        with tempfile.TemporaryDirectory() as directory:
            _, _, card = full_hypercharge_model(ModelProvider(), 10., .1, directory)
            self.assertEqual(card['electroweak_inputs']['m_z_gev'], 91.1876)
            self.assertNotEqual(card['mixing']['z0_mass_squared_gev2'], 91.1876**2)
            with self.assertRaises(ValueError):
                full_hypercharge_model(ModelProvider(), 90., .1, directory)

    def test_changed_frozen_coupling_card_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            _, _, card = full_hypercharge_model(ModelProvider(), 2., .02, directory)
            module = Path(directory) / card['module']
            module.write_text('# changed frozen card\n')
            with self.assertRaisesRegex(ValueError, 'frozen coupling module'):
                full_hypercharge_model(ModelProvider(), 2., .02, directory)


if __name__ == '__main__':
    unittest.main()
