# Code style: AI-code. AI-generated full-source sampling and normalization tests.
import math
from pathlib import Path
import random
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from dark_photon_meson_source import coefficient_bound, choose_collision, source_cross_section_pb, resolve_parent_kinematics


class MesonSourceTest(unittest.TestCase):
    def setUp(self):
        self.models = {
            221: dict(mass_min_gev=.547, br_gamma_gamma=.39, form_factor_abs2=1., provenance='synthetic fixture'),
            331: dict(mass_min_gev=.957, br_gamma_gamma=.023, form_factor_abs2=2., provenance='synthetic fixture')}
        self.parents = [dict(index=17, pdg_id=221, mass_gev=.548),
                        dict(index=28, pdg_id=331, mass_gev=.958)]

    def test_energy_bound_covers_every_physically_allowed_parent_count(self):
        bound = coefficient_bound(10., .2, self.models)
        maximum = sum(math.floor(10./row['mass_min_gev'])*2*row['br_gamma_gamma']*
                      row['form_factor_abs2']*.2 for row in self.models.values())
        self.assertEqual(bound, maximum)
        result = choose_collision(self.parents, .3, .2, self.models, bound, random.Random(1))
        self.assertLessEqual(result['coefficient_sum'], bound)

    def test_zero_parent_and_closed_channels_stay_in_the_source_exposure(self):
        bound = coefficient_bound(10., .2, self.models)
        for parents, mass in (([], .3), (self.parents, 1.)):
            row = choose_collision(parents, mass, .2, self.models, bound, random.Random(1))
            self.assertFalse(row['accepted'])
            self.assertEqual(row['coefficient_sum'], 0.)
            self.assertEqual(row['acceptance_probability'], 0.)
        # Including the zero-parent collision halves the rate, rather than
        # renormalizing to the subset of interactions containing mesons.
        self.assertEqual(source_cross_section_pb(1., .001, 4., 2), 2000.)
        self.assertEqual(source_cross_section_pb(1., .002, 4., 2), 8000.)

    def test_collision_unweighting_and_one_parent_selection_close(self):
        rng = random.Random(761016017)
        bound = coefficient_bound(2., .2, self.models)
        accepted, selected, sums = 0, {17: 0, 28: 0}, []
        for _ in range(80000):
            row = choose_collision(self.parents, .3, .2, self.models, bound, rng)
            sums.append(row['coefficient_sum'])
            if row['accepted']:
                self.assertEqual(row['gen_weight'], 1.)
                accepted += 1
                selected[row['parent_index']] += 1
        expected = sums[0]/bound
        self.assertLess(abs(accepted/len(sums)-expected), 5*math.sqrt(expected*(1-expected)/len(sums)))
        eta = choose_collision(self.parents[:1], .3, .2, self.models, bound, random.Random(2))['coefficient_sum']
        fraction = eta/sums[0]
        self.assertLess(abs(selected[17]/accepted-fraction), 5*math.sqrt(fraction*(1-fraction)/accepted))

    def test_weights_bad_parent_inventory_and_unproven_bounds_fail_closed(self):
        bound = coefficient_bound(10., .2, self.models)
        for kwargs in (dict(native_weight=.5), dict(native_weight=True), dict(bound=1.e-6),
                       dict(parents=self.parents+self.parents), dict(parents=[], mass_gev=float('nan')),
                       dict(parents=[dict(index=-1,pdg_id=221,mass_gev=.548)]),
                       dict(parents=[dict(index=1,pdg_id=111,mass_gev=.135)]),
                       dict(parents=[dict(index=1,pdg_id=221,mass_gev=.5)])):
            arguments = dict(parents=self.parents, mass_gev=.3, br_mumu=.2, models=self.models,
                             bound=bound, rng=random.Random(1))
            arguments.update(kwargs)
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                choose_collision(**arguments)
        self.models[221]['provenance'] = ''
        with self.assertRaises(ValueError):
            coefficient_bound(10., .2, self.models)

    def test_native_mass_precision_is_retained_bounded_and_never_repaired(self):
        self.models[221]['relative_model_uncertainty'] = .01
        original = dict(index=38,pdg_id=221,native_mass_gev=.547862,
            p4=[570.5010019649538,-.7004529990853869,.7446772589972893,-570.4998228797658])
        row, = resolve_parent_kinematics([original],1.e-3,.3,self.models,.2)
        self.assertEqual(row['p4'],original['p4'])
        self.assertEqual(row['mass_gev'],original['native_mass_gev'])
        self.assertGreater(row['kinematic_mass_gev'],row['native_mass_gev'])
        self.assertGreater(row['coefficient_relative_precision_shift'],0.)
        with self.assertRaisesRegex(ValueError,'Pythia mass tolerance'):
            resolve_parent_kinematics([original],1.e-10,.3,self.models,.2)
        self.models[221]['relative_model_uncertainty'] = 1.e-10
        with self.assertRaisesRegex(ValueError,'measured BR/TFF'):
            resolve_parent_kinematics([original],1.e-3,.3,self.models,.2)
        crossing = dict(index=1,pdg_id=221,native_mass_gev=.300001,p4=[.299999,0.,0.,0.])
        with self.assertRaisesRegex(ValueError,'whether the rare decay is open'):
            resolve_parent_kinematics([crossing],1.e-3,.3,self.models,.2)


if __name__ == '__main__':
    unittest.main()
