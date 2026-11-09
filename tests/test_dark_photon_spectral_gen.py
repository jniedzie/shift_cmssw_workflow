# Code style: AI-code. AI-generated spectral GEN contract and provenance tests.
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from run_dark_photon_spectral_gen import (
    PINNED_DARKCAST_COMMIT, SOURCES, build_contract, cross_section_correction,
    freeze_spectral_inputs, physical_width_table, validate_point,
)
from prepare_dark_photon_spectral_grid import plan_points
import prepare_dark_photon_spectral_grid as spectral_grid


class SpectralGenerationTest(unittest.TestCase):
    def test_ordinary_spectral_deployments_keep_their_existing_source_dependencies(self):
        self.assertNotIn('dark_photon_spectral_widths.py', SOURCES)

    def test_selected_rate_corrects_native_branching_fraction_once(self):
        correction = cross_section_correction(1.e-7, .01, .2, .4)
        self.assertAlmostEqual(correction / 1.e-10, .5)
        for native in [0., -1., float("nan"), 1.1]:
            with self.assertRaises(ValueError):
                cross_section_correction(1.e-7, .01, .2, native)

    def test_low_masses_are_not_silently_promoted_to_partonic_production(self):
        for mass in [.22, 1., 1.5, 12., float("nan")]:
            with self.assertRaises(ValueError):
                validate_point(mass, 1.e-7, .01)
        for mass in [2., 5., 10.]:
            validate_point(mass, 1.e-7, .01)

    def test_experimental_low_mass_dy_does_not_open_subgev_region(self):
        for mass in [1.4, 1.5, 1.8]:
            validate_point(mass, 1.e-7, .01, experimental_low_mass_dy=True)
        for mass in [.22, 1., 1.39]:
            with self.assertRaises(ValueError):
                validate_point(mass, 1.e-7, .01, experimental_low_mass_dy=True)

    def test_large_epsilon_is_explicit_and_bounded(self):
        for epsilon in (.02, .05, .1):
            with self.assertRaises(ValueError):
                validate_point(2., epsilon, epsilon)
            validate_point(2., epsilon, epsilon, experimental_large_epsilon=True)
        with self.assertRaises(ValueError):
            validate_point(2., .10001, .10001, experimental_large_epsilon=True)

    def test_contract_retains_physical_width_and_accepted_identity_budget(self):
        args = SimpleNamespace(mass=2., epsilon=1.e-7, sampling_epsilon=.01,
                               events=10, seed=1234, run_number=5678)
        width = dict(total_width_gev=2.e-16, ctau_mm=986.634902, br_mumu=.25)
        native = dict(pythia_version=8.317, mass_gev=2., m_width_gev=2.e-6,
                      m_min_gev=1.98, m_max_gev=2.02,
                      channels=[dict(on_shell_width_gev=1.e-6, branching_fraction=.5, products=[13,-13]),
                                dict(on_shell_width_gev=1.e-6, branching_fraction=.5, products=[11,-11])])
        result = build_contract(args, width, native, ["common"], ["CP5"])
        self.assertEqual(result["width_authority"], width)
        self.assertEqual(result["run_number"], 5678)
        self.assertEqual(result["event_budget_mode"], "outputAccepted")
        self.assertEqual(result["maximum_framework_events"], 100)
        self.assertEqual(result["cross_section_correction_components"]["branching_ratio"], .5)
        self.assertAlmostEqual(result["production_rate_correction"] / 1.e-10, .5)
        self.assertIn("32:tauCalc = off", result["process_settings"])
        self.assertFalse(result["low_mass_mechanisms_included"])

    def test_external_source_pin_detects_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = root / "source/darkcast"
            package.mkdir(parents=True)
            source = package / "model.py"
            source.write_text("# pinned external model\n")
            receipt = root / "source_receipt.json"
            receipt.write_text(json.dumps(dict(commit=PINNED_DARKCAST_COMMIT,
                files={"darkcast/model.py": hashlib.sha256(source.read_bytes()).hexdigest()})))
            output = root / "good"
            output.mkdir()
            authority = freeze_spectral_inputs(package.parent, receipt, output)
            self.assertEqual(authority["commit"], PINNED_DARKCAST_COMMIT)
            source.write_text("# changed external model\n")
            bad = root / "bad"
            bad.mkdir()
            with self.assertRaisesRegex(ValueError, "differs"):
                freeze_spectral_inputs(package.parent, receipt, bad)

    def test_incomplete_channel_sum_does_not_become_total_width_authority(self):
        widths = {"e_e":1., "mu_mu":1., "tau_tau":0., "hadrons":2., "quarks":0., "neutrinos":0., "dark":0., "total":5.}
        model = SimpleNamespace(width=lambda state,mass,epsilon:widths[state])
        with self.assertRaisesRegex(ValueError, "channel sum"):
            physical_width_table(model, 2., 1.e-7, {})

    def test_spectral_grid_translates_lab_flights_with_unfiltered_boost(self):
        reference = dict(schema='shift-dark-photon-grid-reference-v1', sample_kind='simulation_only',
            references=[dict(mass_gev=2.,epsilon=1.e-7,mean_beta_gamma=200.,boost_source={'events':100},
                             width_authority=dict(schema='shift-dark-photon-spectral-width-v1',ctau_mm=1000.))])
        plan = plan_points(reference,[2.],[5.,30.,100.,1000.],50,251013701,2700001)
        self.assertEqual(len(plan['points']),5)
        self.assertEqual(len({row['run_number'] for row in plan['points']}),5)
        self.assertEqual(plan['event_budget_mode'],'outputAccepted')
        for row in plan['points'][1:]:
            proper = 1000 * (1.e-7/row['epsilon'])**2
            self.assertAlmostEqual(proper*200/1000,row['target_mean_lab_flight_m'])
        with self.assertRaises(ValueError):
            plan_points(reference,[.22],[5.],50,251013701,2700001)
        self.assertEqual(plan_points(reference,[2.],[5.],10000,251013701,2700001)['points'][0]['events'],10000)
        with self.assertRaises(ValueError):
            plan_points(reference,[2.],[5.],10001,251013701,2700001)

    def test_explicit_epsilon_grid_keeps_physical_couplings_and_unique_identities(self):
        reference = dict(schema='shift-dark-photon-grid-reference-v1', sample_kind='simulation_only',
            references=[dict(mass_gev=mass,epsilon=1.e-7,mean_beta_gamma=200.,boost_source={'events':100},
                             width_authority=dict(schema='shift-dark-photon-spectral-width-v1',ctau_mm=1000.))
                        for mass in [2., 5., 10.]])
        epsilons = [1.e-6, 1.e-4, 1.e-2]
        plan = plan_points(reference, [2., 5., 10.], None, 50, 251013801, 2800001,
                           epsilons=epsilons)
        self.assertEqual(plan['grid_mode'], 'cartesian-mass-epsilon')
        self.assertEqual({(row['mass_gev'], row['epsilon']) for row in plan['points']},
                         {(mass, epsilon) for mass in [2., 5., 10.] for epsilon in epsilons})
        self.assertEqual(plan['points'][0]['point'], 'm2_epsilon_1e-06')
        self.assertEqual(plan['points'][-1]['point'], 'm10_epsilon_0p01')
        self.assertEqual(len({row['run_number'] for row in plan['points']}), 9)
        self.assertTrue(all(row['target_mean_lab_flight_m'] is None for row in plan['points']))
        for invalid in [[], [1.e-6, 1.e-6], [0.], [float('nan')], [.02]]:
            with self.subTest(epsilons=invalid), self.assertRaises(ValueError):
                plan_points(reference, [2.], None, 50, 251013801, 2800001, epsilons=invalid)


class PBCSpectralPreparation(unittest.TestCase):
    @staticmethod
    def reference(masses, boost=200.):
        return dict(schema='shift-dark-photon-grid-reference-v1', sample_kind='simulation_only',
            references=[dict(mass_gev=mass, epsilon=1.e-7, mean_beta_gamma=boost,
                boost_source={'events':100},
                width_authority=dict(schema='shift-dark-photon-spectral-width-v1', ctau_mm=1000.))
                for mass in masses])

    def test_default_plan_bytes_are_unchanged(self):
        plan = plan_points(self.reference([2.]), [2.], [5.,30.,100.,1000.], 50, 251013701, 2700001)
        encoded = (json.dumps(plan, indent=2, allow_nan=False) + '\n').encode()
        # Captured from the default planner before adding the named profile.
        self.assertEqual(hashlib.sha256(encoded).hexdigest(),
                         'b1338d400060a4e7533141485f90d27a3ae600003ed43c904715b1ed3ee21da7')

    def test_overlap_requires_named_profile_and_never_opens_lower_masses(self):
        for mass in [1.5, 1.8]:
            with self.subTest(mass=mass), self.assertRaises(ValueError):
                plan_points(self.reference([mass]), [mass], [5.], 50, 251013701, 2700001)
        plan = plan_points(self.reference([1.5,1.8,2.]), [1.5,1.8,2.], [5.],
                           50, 251013701, 2700001, benchmark='pbc-bc1')
        self.assertEqual(plan['benchmark'], 'pbc-bc1')
        for row in plan['points']:
            self.assertEqual(row.get('experimental_low_mass_dy', False), row['mass_gev'] < 2.)
        for mass in [.22, 1., 1.4, 1.499999, 12., float('nan')]:
            with self.subTest(mass=mass), self.assertRaises(ValueError):
                plan_points(self.reference([mass]), [mass], [5.], 50, 251013701, 2700001,
                            benchmark='pbc-bc1')

    def test_profile_survives_native_preflight_freezing_and_physical_point_reuse(self):
        # Synthetic replies exercise the real planner/contracts, never physics qualification.
        model = SimpleNamespace(
            width=lambda state,mass,epsilon: .01973269804*epsilon**2 *
                {'total':1., 'e_e':.5, 'mu_mu':.25, 'hadrons':.25}.get(state, 0.),
            bfrac=lambda state,mass: .25,
            xfs={'e':(lambda mass:0., lambda mass:-.302822)})
        inspected_masses = []

        def native_inspect(settings):
            mass = float(next(value.split(' = ')[1] for value in settings if value.startswith('32:m0 = ')))
            inspected_masses.append(mass)
            return dict(pythia_version=8.317, mass_gev=mass, m_width_gev=2.e-6,
                m_min_gev=.99*mass, m_max_gev=1.01*mass,
                channels=[dict(on_shell_width_gev=1.e-6, branching_fraction=.5, products=[13,-13]),
                          dict(on_shell_width_gev=1.e-6, branching_fraction=.5, products=[11,-11])])

        modules = {
            'Configuration.Generator.Pythia8CommonSettings_cfi': SimpleNamespace(
                pythia8CommonSettingsBlock=SimpleNamespace(pythia8CommonSettings=[])),
            'Configuration.Generator.MCTunes2017.PythiaCP5Settings_cfi': SimpleNamespace(
                pythia8CP5SettingsBlock=SimpleNamespace(pythia8CP5Settings=[])),
            'darkcast': SimpleNamespace(Model=lambda name:model),
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root/'source/darkcast'; package.mkdir(parents=True)
            source = package/'model.py'; source.write_text('# synthetic provider fixture\n')
            receipt = root/'source_receipt.json'
            receipt.write_text(json.dumps(dict(commit=PINNED_DARKCAST_COMMIT,
                files={'darkcast/model.py':hashlib.sha256(source.read_bytes()).hexdigest()})))
            authority_output = root/'authority'; authority_output.mkdir()
            authority = freeze_spectral_inputs(package.parent, receipt, authority_output)
            templates = root/'templates'; templates.mkdir()
            for number in range(1,5):
                (templates/f'step{number}.py').write_text('# synthetic template fixture\n')
            (root/'src').mkdir()

            def prepare(output, boost, seed, previous=None):
                reference = self.reference([1.5,1.8,2.], boost)
                reference.update(common=[], cp5=[])
                for row in reference['references']:
                    row['width_authority']['spectral_source'] = authority
                reference_path = root/(output+'_references.json')
                reference_path.write_text(json.dumps(reference))
                argv = ['prepare_dark_photon_spectral_grid.py', '--references', str(reference_path),
                    '--templates', str(templates), '--output', str(root/output),
                    '--darkcast-path', str(package.parent), '--darkcast-source-receipt', str(receipt),
                    '--benchmark', 'pbc-bc1', '--masses', '1.5', '1.8', '2', '--mean-lab-flights-m', '5',
                    '--events', '2', '--seed-base', str(seed), '--detector-job-base', '2700001',
                    '--campaign', 'pbc_test_20261109', '--eos-base', '/eos/user/j/fixture']
                if previous is not None:
                    argv.extend(['--reuse-physical-plan', str(previous)])
                with mock.patch.object(sys, 'argv', argv), mock.patch.dict(sys.modules, modules), \
                     mock.patch.dict(os.environ, {'CMSSW_BASE':str(root)}), \
                     mock.patch('dark_photon_pythia._native_inspect', side_effect=native_inspect), \
                     mock.patch.object(spectral_grid.subprocess, 'check_output', return_value='synthetic-runtime'), \
                     mock.patch.object(spectral_grid.grid, 'SOURCES', tuple(spectral_grid.grid.SOURCES)), \
                     mock.patch('sys.stdout', new_callable=io.StringIO):
                    spectral_grid.main()
                return json.loads((root/output/'plan.json').read_text())

            first = prepare('first', 200., 251013701)
            reused = prepare('reused', 400., 251013801, root/'first/plan.json')
            self.assertEqual(set(inspected_masses), {1.5,1.8,2.})
            for old, new in zip(first['points'], reused['points']):
                self.assertEqual(new['epsilon'], old['epsilon'])
                self.assertEqual(new['proper_ctau_mm'], old['proper_ctau_mm'])
                self.assertEqual(new['mean_beta_gamma_reference'], old['mean_beta_gamma_reference'])
                self.assertNotEqual(new['run_number'], old['run_number'])
                enabled = new['mass_gev'] < 2.
                contract = json.loads(Path(new['signal_contract']).read_text())
                self.assertEqual(contract['experimental_low_mass_dy'], enabled)
                self.assertEqual(new['gen_argv'].count('--experimental-low-mass-dy'), int(enabled))
                self.assertEqual(Path(new['gen_argv'][1]).name, 'run_dark_photon_spectral_gen.py')
                self.assertTrue(Path(new['gen_argv'][1]).is_file())
                self.assertFalse(contract['physics_valid'])
                self.assertFalse(reused['physics_valid'])
                self.assertFalse(reused['production_rate_validated'])


class FrozenSpectralContract(unittest.TestCase):
    def test_rounding_and_old_descriptive_defaults_preserve_exact_original_draw(self):
        import copy, math
        from dark_photon_source_placement import place_contract
        from run_dark_photon_spectral_gen import frozen_contract
        from test_shift_jaw_source import profile
        before=dict(seed=42,run_number=101,requested_events=2,maximum_framework_events=20,
            epsilon=1.e-6,mass_gev=2.,source_model='original fixed source',
            process_settings=['Zprime:vmu = 0.01'],width_authority={'ctau_mm':5.})
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);jaw=root/'jaw.json';jaw.write_text(json.dumps(profile()))
            expected=place_contract(before,jaw);path=root/'contract.json';path.write_text(json.dumps(expected))
            fresh=copy.deepcopy(before);fresh['experimental_low_mass_dy']=False
            fresh['production_scope']='DY-only spectral pilot'
            fresh['process_settings']=['Zprime:vmu = '+repr(math.nextafter(.01,1.))]
            actual,receipt=frozen_contract(fresh,path,jaw)
            self.assertEqual(actual,expected);self.assertEqual(receipt['maximum_ulp_difference'],1)
            self.assertEqual(actual['source_placement']['event_positions'],expected['source_placement']['event_positions'])
            for key,value in [('epsilon',2.e-6),('seed',43),('width_authority',{'ctau_mm':5.0000001}),
                              ('process_settings',['Zprime:vmu = 0.010000001'])]:
                changed=copy.deepcopy(fresh);changed[key]=value
                with self.subTest(key=key),self.assertRaises(ValueError):frozen_contract(changed,path,jaw)
            changed=copy.deepcopy(expected);changed['source_placement']['event_positions'][0]['target_position_cm'][0]+=1.
            path.write_text(json.dumps(changed))
            with self.assertRaises(ValueError):frozen_contract(fresh,path,jaw)


if __name__ == "__main__":
    unittest.main()
