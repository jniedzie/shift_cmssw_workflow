# Code style: AI-code. AI-generated eta/eta-prime native precision regression checks.
import copy
import json
import math
from pathlib import Path
import random
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import dark_photon_meson_source as source


class PseudoscalarNativePrecisionTest(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[1]/'config/campaigns/dark_photon_meson_extended_model.json'
        card = json.loads(path.read_text())
        card['parents'] = [row for row in card['parents'] if row['pdg_id'] == 221]
        card['channels'] = [row['channel'] for row in card['parents']]
        native = dict(parents=[dict(pdg_id=221, mass_min_gev=.547862,
                                   mass_max_gev=.547862, mass_width_gev=0.)])
        self.models = source.resolved_parent_models(card, native, .25)
        # Exact parent from the failed source seed, after the native replay
        # matched all 220,769 preceding collisions and 1,148 recoil injections.
        self.parent = dict(index=44, pdg_id=221, native_mass_gev=.547862,
            p4=[130.0061925446488, .22804794595604153, .0007799850516959916,
                -130.00481855483832])
        self.mass = math.sqrt(math.fsum((self.parent['p4'][0]**2,
                            *(-value*value for value in self.parent['p4'][1:]))))

    def resolve(self, parent=None):
        return source.resolve_parent_kinematics([parent or self.parent], 1.e-3, .25, self.models, .2)[0]

    def report(self):
        return source.pseudoscalar_dipion_precision_report(
            self.parent, self.mass, .25, self.models[221], .2)

    def test_exact_eta_keeps_native_momentum_mass_and_proposal_probability(self):
        original = copy.deepcopy(self.parent)
        result = self.resolve()
        self.assertEqual(self.parent, original)
        self.assertEqual(result['p4'], original['p4'])
        self.assertEqual(result['mass_gev'], original['native_mass_gev'])
        report = result['native_parent_precision_report']
        self.assertAlmostEqual(report['parent_l1_relative_rate_shift'], .013231884907518674)
        self.assertAlmostEqual(report['parent_original_weighted_model_budget'], .024943858341208727)
        self.assertLess(report['exceptional_dipion_rate_plus_shape_bound'], 1.7e-5)
        self.assertGreater(report['parent_l1_relative_rate_shift'], 1.e-3)
        bound = source.coefficient_bound(2., .2, self.models, .25)
        nominal = dict(original, mass_gev=original['native_mass_gev'])
        for seed in range(100):
            expected = source.choose_collision([nominal], .25, .2, self.models, bound, random.Random(seed))
            actual = source.choose_collision([result], .25, .2, self.models, bound, random.Random(seed))
            self.assertEqual(actual, expected)

    def test_normal_native_parent_has_no_exception_report(self):
        parent = dict(self.parent, p4=[.547862, 0., 0., 0.])
        self.assertNotIn('native_parent_precision_report', self.resolve(parent))

    def test_eta_prime_uses_only_its_own_two_channels(self):
        path = Path(__file__).resolve().parents[1]/'config/campaigns/dark_photon_meson_extended_model.json'
        card = json.loads(path.read_text())
        card['parents'] = [row for row in card['parents'] if row['pdg_id'] == 331]
        card['channels'] = [row['channel'] for row in card['parents']]
        native = dict(parents=[dict(pdg_id=331, mass_min_gev=.95778,
                                   mass_max_gev=.95778, mass_width_gev=0.)])
        models = source.resolved_parent_models(card, native, .65)
        parent = dict(index=1, pdg_id=331, native_mass_gev=.95778,
                      p4=[100., 0., 0., -math.sqrt(10000-.95878**2)])
        resolved, = source.resolve_parent_kinematics([parent], 1.e-3, .65, models, .2)
        report = resolved['native_parent_precision_report']
        self.assertEqual(report['parent_pdg_id'], 331)
        self.assertLess(report['exceptional_dipion_rate_plus_shape_bound'], 1.4e-5)
        self.assertTrue(all(row['channel'].startswith('eta_prime->') for row in report['channels']))
        self.assertEqual(resolved['p4'], parent['p4'])

    def test_bound_does_not_depend_on_the_dimuon_branching_fraction(self):
        first = self.report()
        second = source.pseudoscalar_dipion_precision_report(
            self.parent, self.mass, .25, self.models[221], 1.)
        for name in ('exceptional_dipion_rate_plus_shape_bound', 'parent_l1_relative_rate_shift',
                     'parent_original_weighted_model_budget'):
            self.assertAlmostEqual(first[name], second[name], delta=1.e-15)

    def test_large_exceptional_component_still_fails(self):
        photon = self.models[221]['exclusive_channels'][0]
        photon['br_gamma_gamma'] /= 100
        with self.assertRaisesRegex(ValueError, 'exceeds 0.1% of its own parent'):
            self.resolve()

    def test_sibling_cannot_borrow_the_exceptional_channel_budget(self):
        photon = self.models[221]['exclusive_channels'][0]
        photon['relative_model_uncertainty'] = 1.e-3
        with self.assertRaisesRegex(ValueError, 'unchanged isolated BR/TFF'):
            self.report()
        with self.assertRaisesRegex(ValueError, 'BR/TFF precision budget'):
            self.resolve()

    def test_combined_rate_must_fit_the_original_weighted_budgets(self):
        photon = self.models[221]['exclusive_channels'][0]
        photon['relative_model_uncertainty'] = .013222283025212223+1.e-8
        with self.assertRaisesRegex(ValueError, 'Total parent rate shift'):
            self.report()

    def test_exception_requires_both_owned_pole_channels(self):
        for change in ('species', 'single_channel', 'mass_support'):
            with self.subTest(change=change):
                parent, model = copy.deepcopy(self.parent), copy.deepcopy(self.models[221])
                if change == 'species':
                    parent['pdg_id'] = 113
                elif change == 'single_channel':
                    model['exclusive_channels'] = model['exclusive_channels'][1:]
                else:
                    model['mass_max_gev'] += .01
                with self.assertRaisesRegex(ValueError, 'two owned eta/eta-prime pole channels'):
                    source.pseudoscalar_dipion_precision_report(parent, self.mass, .25, model, .2)

    def test_native_mass_tolerance_and_open_channel_checks_remain_strict(self):
        with self.assertRaisesRegex(ValueError, 'unchanged Pythia mass tolerance'):
            source.resolve_parent_kinematics([self.parent], 1.e-6, .25, self.models, .2)
        model = copy.deepcopy(self.models[221])
        model['exclusive_channels'].reverse()
        with self.assertRaisesRegex(ValueError, 'whether an owned rare decay is open'):
            source.pseudoscalar_dipion_precision_report(
                self.parent, .52, .25, model, .2)


if __name__ == '__main__':
    unittest.main()
