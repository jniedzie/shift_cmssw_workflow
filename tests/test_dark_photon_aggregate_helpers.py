# Code style: AI-code. AI-generated aggregate physics-dependency closure tests.
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from run_dark_photon_statistics import aggregate_helper_names


class AggregateHelperClosure(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name);self.tools=self.root/'tools';self.tools.mkdir()
        self.base=['aggregate_dark_photon_samples.py','dark_photon_production_support.py',
                   'export_dark_photon_cross_sections.py','pythia_generation_ledger.py',
                   'dark_photon_meson.py','dark_photon_meson_source.py']
        for name in self.base:(self.tools/name).write_text('# synthetic standalone helper\n')
        self.card=self.root/'model.json'
        self.config=dict(tools=str(self.tools),model_card=str(self.card),production_mechanism='meson-radiative')

    def model(self,*decays):
        self.card.write_text(json.dumps(dict(parents=[dict(decay_type=x) for x in decays])))

    def isolated_imports(self,names):
        target=self.root/'isolated';target.mkdir()
        for name in names:shutil.copy2(self.tools/name,target/name)
        code='import importlib,sys;sys.path.insert(0,sys.argv[1]);[importlib.import_module(x[:-3]) for x in sys.argv[2:]]'
        subprocess.run([sys.executable,'-I','-B','-c',code,str(target),*names],cwd=target,check=True,capture_output=True)

    def test_legacy_eta_closure_does_not_require_unrelated_vector_files(self):
        self.model('pseudoscalar-photon')
        (self.tools/'dark_photon_meson_source.py').write_text('from dark_photon_meson import __doc__\n')
        names=aggregate_helper_names(self.config)
        self.assertEqual(set(names),set(self.base));self.isolated_imports(names)

    def test_modern_eta_card_keeps_its_direct_vector_import(self):
        self.model('pseudoscalar-photon')
        (self.tools/'dark_photon_meson_source.py').write_text('import dark_photon_vector_meson\n')
        with self.assertRaisesRegex(FileNotFoundError,'dark_photon_vector_meson'):
            aggregate_helper_names(self.config)
        (self.tools/'dark_photon_vector_meson.py').write_text('from dark_photon_meson import __doc__\n')
        names=aggregate_helper_names(self.config)
        self.assertIn('dark_photon_vector_meson.py',names);self.isolated_imports(names)

    def test_model_lazily_imported_dipion_and_rho_kernels_remain_required(self):
        extra=['dark_photon_vector_meson.py','dark_photon_dipion_meson.py',
               'dark_photon_rho_radiative.py','dark_photon_charged_rho_radiative.py']
        for name in extra:(self.tools/name).write_text('# synthetic decay kernel\n')
        self.model('vector-pseudoscalar','pseudoscalar-dipion','vector-dipion','charged-vector-dipion')
        names=aggregate_helper_names(self.config)
        self.assertEqual(set(names),set(self.base+extra));self.isolated_imports(names)
        (self.tools/'dark_photon_rho_radiative.py').unlink()
        with self.assertRaisesRegex(FileNotFoundError,'dark_photon_rho_radiative'):
            aggregate_helper_names(self.config)

    def test_transitive_static_import_is_required_from_the_same_tools(self):
        self.model('pseudoscalar-photon')
        (self.tools/'dark_photon_meson_source.py').write_text('import dark_photon_vector_meson\n')
        (self.tools/'dark_photon_vector_meson.py').write_text('import dark_photon_dipion_meson\n')
        with self.assertRaisesRegex(FileNotFoundError,'dark_photon_dipion_meson'):
            aggregate_helper_names(self.config)
        (self.tools/'dark_photon_dipion_meson.py').write_text('import dark_photon_meson\n')
        names=aggregate_helper_names(self.config)
        self.assertEqual(len(names),len(set(names)));self.isolated_imports(names)

    def test_unified_model_card_is_required(self):
        self.config.pop('model_card')
        with self.assertRaisesRegex(ValueError,'actual model card'):
            aggregate_helper_names(self.config)


if __name__=='__main__':unittest.main()
