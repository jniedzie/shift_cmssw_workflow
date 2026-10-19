# Code style: AI-code. AI-generated clean-runtime native-library resolution tests.
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from dark_photon_meson_native import cmssw_native_libraries


class NativeLibrariesTest(unittest.TestCase):
    def test_scram_versioned_sonames_and_aliases_resolve_once(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            hepmc = root/'hepmc'; hepmc.mkdir()
            pythia = root/'pythia'; pythia.mkdir()
            for name in ('HepMC', 'HepMCfio'):
                (hepmc/('lib'+name+'.so.4.0.0')).write_bytes(b'native')
                (hepmc/('lib'+name+'.so.4')).symlink_to('lib'+name+'.so.4.0.0')
            (pythia/'libpythia8.so').write_bytes(b'native')
            def information(command, cwd, text):
                self.assertEqual(command[:3], ['scram', 'tool', 'info'])
                self.assertEqual(cwd, root/'src')
                return 'LIBDIR='+str(hepmc if command[-1]=='hepmc' else pythia)+'\n'
            with patch.dict(os.environ, {'CMSSW_BASE':str(root)}), \
                 patch('dark_photon_meson_native.subprocess.check_output', side_effect=information):
                self.assertEqual(cmssw_native_libraries(), [str(hepmc/'libHepMC.so.4.0.0'),
                    str(hepmc/'libHepMCfio.so.4.0.0'), str(pythia/'libpythia8.so')])
                # An unrelated inherited library is never a fallback.
                (hepmc/'libHepMC.so.4.0.0').unlink()
                with self.assertRaisesRegex(RuntimeError, 'Missing or ambiguous'):
                    cmssw_native_libraries()

    def test_ambiguous_scram_directory_is_rejected(self):
        with patch.dict(os.environ, {'CMSSW_BASE':'/current/release'}), \
             patch('dark_photon_meson_native.subprocess.check_output',
                   return_value='LIBDIR=/one\nLIBDIR=/two\n'):
            with self.assertRaisesRegex(RuntimeError, 'SCRAM library directory'):
                cmssw_native_libraries()


if __name__ == '__main__':
    unittest.main()
