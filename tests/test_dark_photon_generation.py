# Code style: AI-code. AI-generated signal generation contract tests.
"""Guard physical-point and normalization contracts before runtime pilots."""
import math
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from dark_photon_generation import contract, production_settings, signal_settings


class SignalGenerationContractTest(unittest.TestCase):
    def setUp(self):
        self.width = dict(mass_gev=15., epsilon=1e-3, total_width_gev=2.64e-5,
                          ctau_mm=1.973269804e-13/2.64e-5, br_mumu=.145)

    def test_forced_decay_retains_total_lifetime_and_branch_convention(self):
        data = contract(15.,1e-3,self.width,'mumu',100,1234,1234,[],[])
        self.assertIn('32:onIfMatch = 13 -13', data['process_settings'])
        self.assertEqual(data['width_authority']['total_width_gev'],self.width['total_width_gev'])
        self.assertIn('no second BR factor',data['cross_section_convention'])
        self.assertFalse(data['physics_valid'])
        self.assertIn('32:tau0 = ', '\n'.join(data['process_settings']))
        self.assertFalse(any('doForceWidth = on' in x for x in data['process_settings']))

    def test_mismatched_or_partial_width_table_fails(self):
        for change in ({'epsilon':1e-6},{'mass_gev':20.},{'ctau_mm':1.},
                       {'br_mumu':0.},{'total_width_gev':float('nan')}):
            with self.assertRaises(ValueError):
                signal_settings(15.,1e-3,dict(self.width,**change))

    def test_accepted_output_budget_is_explicit_and_bounded(self):
        legacy=contract(15.,1e-3,self.width,'mumu',100,1234,1234,[],[])
        accepted=contract(15.,1e-3,self.width,'mumu',100,1234,1234,[],[],accepted_events=True)
        self.assertNotIn('event_budget_mode',legacy)
        self.assertEqual(accepted['event_budget_mode'],'outputAccepted')
        self.assertEqual(accepted['maximum_framework_events'],1000)
        self.assertEqual(accepted['requested_events'],100)
        self.assertEqual(accepted['process_settings'],legacy['process_settings'])

    def test_unsupported_hadronic_and_degenerate_points_fail(self):
        for mass,eps in ((.22,1e-7),(5.,1e-7),(91.1876,1e-7),(15.,0.),(15.,.1)):
            with self.assertRaises(ValueError):
                production_settings(mass,eps)

    def test_large_epsilon_requires_explicit_benchmark_opt_in(self):
        for epsilon in (.02, .05, .1):
            with self.assertRaises(ValueError):
                production_settings(15., epsilon)
            settings = production_settings(15., epsilon, experimental_large_epsilon=True)
            self.assertIn('Zprime:gmZmode = 3', settings)
        for mass, epsilon in ((15., .10001), (90., .1)):
            with self.assertRaises(ValueError):
                production_settings(mass, epsilon, experimental_large_epsilon=True)

    def test_inclusive_has_no_signal_forcing(self):
        commands = signal_settings(15.,1e-3,self.width,'inclusive')
        self.assertFalse(any('onIfMatch' in command for command in commands))

    def test_fixed_target_and_generator_scope(self):
        data = contract(15.,1e-3,self.width,'mumu',2,1234,1234,[],[])
        self.assertIn('Beams:eA = 0.',data['beam_settings'])
        self.assertIn('Beams:eB = 6800.0',data['beam_settings'])
        self.assertEqual(data['source_z_mm'],148000.)
        self.assertFalse(any(any(word in command for word in ('Detector','Readout','Trigger','G4'))
                             for command in data['process_settings']))

    def test_unresolved_native_pole_fails_closed(self):
        tiny=dict(self.width, epsilon=1e-7, total_width_gev=2.64e-15,
                  ctau_mm=1.973269804e-13/2.64e-15)
        with self.assertRaises(ValueError):
            contract(15.,1e-7,tiny,'mumu',2,1234,1234,[],[])
        data=contract(15.,1e-7,tiny,'mumu',2,1234,1234,[],[],self.width,1e5)
        self.assertEqual(data['production_rate_correction'],1e-10)
        self.assertEqual(data['width_authority']['ctau_mm'],tiny['ctau_mm'])


class NativeRunnerPrescription(unittest.TestCase):
    def test_selected_profile_reaches_preflights_frozen_files_and_worker_environment(self):
        import dark_photon_dy_prescription as prescription
        import run_dark_photon_gen as runner
        import test_dark_photon_dy_prescription as prescription_tests
        import test_dark_photon_grid as grid_tests
        modules = {
            'Configuration.Generator.Pythia8CommonSettings_cfi': SimpleNamespace(
                pythia8CommonSettingsBlock=SimpleNamespace(pythia8CommonSettings=[])),
            'Configuration.Generator.MCTunes2017.PythiaCP5Settings_cfi': SimpleNamespace(
                pythia8CP5SettingsBlock=SimpleNamespace(pythia8CP5Settings=[])),
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); (root/'src').mkdir()
            table = root/'authority.json'
            fixture = prescription_tests.DYPrescriptionTest().table()
            fixture['points'][0]['mass_gev'] = 15.
            table.write_text(json.dumps(fixture))
            original_files = prescription.prescription_files
            def fixture_files(name, table_path=None):
                return original_files(name, table if table_path is None else table_path)
            observation = dict(schema='shift-dy-pdf-runtime-v1', data_root=fixture['pdf_data_root'],
                               files=fixture['pdf_inputs'])
            def configure(name):
                os.environ['LHAPDF_DATA_PATH'] = observation['data_root']
                return observation
            results = []
            for selected in [False, True]:
                inspected, executed = [], []
                output = root/('selected' if selected else 'legacy')
                def measure(settings):
                    inspected.append(settings)
                    return grid_tests.fake_native(15., .001, scale=1. if len(inspected)==1 else 5.)
                def execute(command, **options):
                    # Fake only external execution; contract creation and source freezing are real.
                    executed.append(options['env'])
                    if command[0] == 'cmsRun':
                        (output/'gen.root').write_bytes(b'synthetic GEN fixture')
                    else:
                        (output/'validation.json').write_text(json.dumps(dict(events=2, runtime_validated=True)))
                argv = ['run_dark_photon_gen.py', '--mass', '15', '--epsilon', '.001',
                        '--sampling-epsilon', '.005', '--events', '2', '--output', str(output)]
                if selected:
                    argv.extend(['--dy-prescription', 'msht20-nnlo'])
                with mock.patch.object(sys, 'argv', argv), mock.patch.dict(sys.modules, modules), \
                     mock.patch.dict(os.environ, {'CMSSW_BASE':str(root)}), \
                     mock.patch.object(runner.shutil, 'which', return_value='/synthetic/cmsRun'), \
                     mock.patch.object(runner, 'record_runtime'), \
                     mock.patch('dark_photon_pythia.measure_widths', side_effect=measure), \
                     mock.patch.object(runner.subprocess, 'check_output', return_value='synthetic Pythia tool'), \
                     mock.patch.object(runner.subprocess, 'run', side_effect=execute), \
                     mock.patch.object(prescription, 'prescription_files', side_effect=fixture_files), \
                     mock.patch.object(prescription, 'configure_hard_pdf', side_effect=configure), \
                     mock.patch('sys.stdout', new_callable=io.StringIO):
                    runner.main()
                manifest = json.loads((output/'manifest.json').read_text())
                data = json.loads((output/'contract.json').read_text())
                self.assertEqual(len(inspected), 2)
                self.assertTrue(all(('PDF:useHard = on' in settings) == selected for settings in inspected))
                self.assertEqual('dy_prescription' in data, selected)
                for filename in [prescription.HELPER_NAME, prescription.TABLE_NAME]:
                    self.assertEqual(filename in manifest['source_sha256'], selected)
                    self.assertEqual((output/filename).is_file(), selected)
                if selected:
                    self.assertEqual(manifest['dy_pdf_runtime'], observation)
                    self.assertEqual(prescription.validate_prescription(data, output), data['dy_prescription'])
                    self.assertTrue(all(environment['LHAPDF_DATA_PATH'] == observation['data_root']
                                        for environment in executed))
                else:
                    self.assertNotIn('dy_pdf_runtime', manifest)
                results.append(data)
            for key in ('production_rate_correction', 'width_authority', 'sampling_width_authority',
                        'requested_events', 'seed', 'run_number', 'cp5'):
                self.assertEqual(results[0][key], results[1][key])
            self.assertEqual(results[1]['process_settings'], results[0]['process_settings'] +
                             prescription.hard_pdf_settings('msht20-nnlo'))


if __name__ == '__main__':
    unittest.main()
