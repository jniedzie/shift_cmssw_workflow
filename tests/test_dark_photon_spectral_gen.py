# Code style: AI-code. AI-generated spectral GEN contract and provenance tests.
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from run_dark_photon_spectral_gen import (
    PINNED_DARKCAST_COMMIT, build_contract, cross_section_correction,
    freeze_spectral_inputs, physical_width_table, validate_point,
)
from prepare_dark_photon_spectral_grid import plan_points


class SpectralGenerationTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
