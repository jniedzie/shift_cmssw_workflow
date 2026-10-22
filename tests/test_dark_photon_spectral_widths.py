# Code style: AI-code. AI-generated full-mixing spectral adapter regression tests.
import ast
import hashlib
import importlib
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

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
    def test_new_card_is_importable_with_coarse_directory_timestamps(self):
        class ImportingModelProvider(ModelProvider):
            def Model(self, name, path=None):
                if name == 'dark_photon':
                    return super().Model(name, path)
                return importlib.import_module(name)

        with tempfile.TemporaryDirectory() as directory:
            sys.path.insert(0, directory)
            module_name = None
            try:
                # Prime FileFinder before the card exists, as importing
                # DarkCast does. AFS may keep the same directory timestamp.
                self.assertIsNone(importlib.util.find_spec('shift_unwritten_card_probe'))
                before = Path(directory).stat()
                write_text = Path.write_text

                def write_with_same_directory_time(path, text, *args, **kwargs):
                    result = write_text(path, text, *args, **kwargs)
                    os.utime(directory, ns=(before.st_atime_ns, before.st_mtime_ns))
                    return result

                with patch.object(Path, 'write_text', write_with_same_directory_time):
                    model, _, card = full_hypercharge_model(ImportingModelProvider(), 2., .1, directory)
                module_name = Path(card['module']).stem
                self.assertEqual(Path(model.__file__), Path(directory) / card['module'])
                self.assertEqual(model.xfs, card['xfs'])
            finally:
                sys.path.remove(directory)
                sys.path_importer_cache.pop(directory, None)
                if module_name is not None:
                    sys.modules.pop(module_name, None)

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
