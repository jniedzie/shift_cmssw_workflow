# Code style: AI-code. AI-generated source-card and fixed-exposure guard tests.
import json
import shutil
from pathlib import Path
from types import SimpleNamespace
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from run_dark_photon_meson_gen import audit_generated_source, remaining_timeout, resolved_parent_models, run, source_settings, SOURCES
from dark_photon_meson_source import coefficient_bound


class MesonRunnerTest(unittest.TestCase):
    def test_frozen_runner_imports_without_the_original_checkout(self):
        with tempfile.TemporaryDirectory() as temporary:
            output=Path(temporary)
            scripts=Path(__file__).resolve().parents[1]/'scripts'
            for name in SOURCES:
                shutil.copy2(scripts/name,output/name)
            program=('import sys; from pathlib import Path; '
                'sys.path.insert(0,sys.argv[1]); '
                'import run_dark_photon_meson_gen,run_dark_photon_spectral_gen,dark_photon_source_placement; '
                'assert all(Path(module.__file__).parent==Path(sys.argv[1]) for module in '
                '(run_dark_photon_meson_gen,run_dark_photon_spectral_gen,dark_photon_source_placement))')
            result=subprocess.run([sys.executable,'-I','-B','-c',program,str(output)],
                cwd=output,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=30)
            self.assertEqual(result.returncode,0,result.stdout)

    def test_heavy_current_reaches_runtime_checks_above_old_eta_prime_cutoff(self):
        arguments=SimpleNamespace(mass=1.5,epsilon=1.e-6,source_collisions=2,
            seed=761016999,run_number=1761016999,timeout=60)
        with patch('run_dark_photon_meson_gen.shutil.which',return_value=None):
            with self.assertRaisesRegex(RuntimeError,'prepared CMSSW runtime'):
                run(arguments)

    def test_correlated_source_helpers_are_frozen_together(self):
        for name in ['dark_photon_double_radiative.py','dark_photon_scalar_meson.py',
                     'dark_photon_quarkonium.py','dark_photon_weak_radiative.py']:
            self.assertIn(name,SOURCES)
            self.assertTrue((Path(__file__).resolve().parents[1]/'scripts'/name).is_file())

    def test_source_settings_keep_explicit_heavy_poles_and_recoil_masses(self):
        from test_dark_photon_meson_source import CorrelatedCurrentSourceTest
        parent=CorrelatedCurrentSourceTest().quark('chi_c1->Jpsi_Aprime')
        card=dict(parents=[parent],source=dict(beam_energy_gev=6800.))
        common=types.SimpleNamespace(pythia8CommonSettingsBlock=types.SimpleNamespace(pythia8CommonSettings=['common']))
        cp5=types.SimpleNamespace(pythia8CP5SettingsBlock=types.SimpleNamespace(pythia8CP5Settings=['tune']))
        with patch.dict(sys.modules,{'Configuration.Generator.Pythia8CommonSettings_cfi':common,
                'Configuration.Generator.MCTunes2017.PythiaCP5Settings_cfi':cp5}):
            settings=source_settings(card)
        self.assertEqual(settings['common'],['common']);self.assertEqual(settings['cp5'],['tune'])
        self.assertIn('SoftQCD:nonDiffractive=on',settings['source_settings'])
        self.assertIn('13:mayDecay=off',settings['source_settings'])
        self.assertIn('20443:mWidth=0',settings['source_settings'])
        self.assertIn(f"443:m0={parent['recoil_mass_gev']:.17g}",settings['source_settings'])
        changed=dict(parent,parent_mass_model='unreviewed-broad-source')
        with patch.dict(sys.modules,{'Configuration.Generator.Pythia8CommonSettings_cfi':common,
                'Configuration.Generator.MCTunes2017.PythiaCP5Settings_cfi':cp5}):
            with self.assertRaisesRegex(ValueError,'off-shell'):
                source_settings(dict(card,parents=[changed]))

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


class MesonAuditDeadlineTest(unittest.TestCase):
    def test_audit_uses_remaining_large_source_budget_and_respects_short_requests(self):
        with patch('run_dark_photon_meson_gen.time.monotonic', return_value=4100):
            self.assertEqual(remaining_timeout(started=100, timeout=43200), 39200)
            self.assertEqual(remaining_timeout(started=4000, timeout=120), 20)
            for timeout in (100, 50):
                with self.subTest(timeout=timeout), self.assertRaises(TimeoutError):
                    remaining_timeout(started=4000, timeout=timeout)

    def test_real_audit_receives_the_directory_and_checks_every_fixture_row(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            (output/'meson_source_ledger.jsonl').write_text('first\nlast\n')
            (output/'audit_dark_photon_gen.py').write_text(
                'from pathlib import Path\nimport sys\n'
                'out=Path(sys.argv[1])\n'
                'assert out == Path.cwd()\n'
                'assert (out/"meson_source_ledger.jsonl").read_text().splitlines() == ["first","last"]\n'
                '(out/"validation.json").write_text("audited complete fixture")\n'
                'print("complete audit")\n')
            audit_generated_source(output, remaining_timeout(time.monotonic(), 43200))
            self.assertEqual((output/'validation.json').read_text(), 'audited complete fixture')
            self.assertIn('complete audit', (output/'audit.log').read_text())

    def test_real_audit_failure_or_timeout_cannot_be_reported_as_success(self):
        for source, failure, timeout in (
                ('raise RuntimeError("invalid ledger")\n', subprocess.CalledProcessError, 10),
                ('import time\ntime.sleep(10)\n', subprocess.TimeoutExpired, .01)):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary:
                output = Path(temporary)
                (output/'audit_dark_photon_gen.py').write_text(source)
                with self.assertRaises(failure):
                    audit_generated_source(output, timeout)
                self.assertFalse((output/'validation.json').exists())


if __name__ == '__main__':
    unittest.main()
