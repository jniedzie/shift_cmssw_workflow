# Code style: AI-code. AI-generated full-source sampling and normalization tests.
import math
from pathlib import Path
import random
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from dark_photon_meson_source import (coefficient_bound, choose_collision, source_cross_section_pb,
    resolve_parent_kinematics, parent_branching_coefficient, resolved_parent_models, production_mechanism)
from dark_photon_vector_meson import two_body_momentum


class MesonSourceTest(unittest.TestCase):
    def test_mechanism_cannot_mislabel_new_exclusive_channels(self):
        legacy = dict(parents=[dict(pdg_id=221)])
        self.assertEqual(production_mechanism(legacy), 'eta-etaprime-radiative')
        with self.assertRaisesRegex(ValueError, 'Unknown meson'):
            production_mechanism(dict(legacy, mechanism='misspelled'))
        for decay in ('vector-pseudoscalar', 'pseudoscalar-dipion'):
            parent = dict(pdg_id=331, decay_type=decay)
            for inventory in ([parent], [dict(exclusive_channels=[parent])]):
                with self.subTest(decay=decay, inventory=inventory):
                    card = dict(parents=inventory)
                    with self.assertRaisesRegex(ValueError, 'meson-radiative'):
                        production_mechanism(card)
                    card['mechanism'] = 'meson-radiative'
                    with self.assertRaisesRegex(ValueError, 'channel inventory'):
                        production_mechanism(card)
                    card['channels'] = ['synthetic-test-channel']
                    self.assertEqual(production_mechanism(card), 'meson-radiative')

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
        with self.assertRaisesRegex(ValueError,'BR/TFF precision budget'):
            resolve_parent_kinematics([original],1.e-3,.3,self.models,.2)
        crossing = dict(index=1,pdg_id=221,native_mass_gev=.300001,p4=[.299999,0.,0.,0.])
        with self.assertRaisesRegex(ValueError,'whether the rare decay is open'):
            resolve_parent_kinematics([crossing],1.e-3,.3,self.models,.2)

    def test_unified_vector_and_pseudoscalar_weights_share_one_collision_bound(self):
        omega = dict(mass_min_gev=.78266, mass_max_gev=.78266, br_p_gamma=.0833,
            form_factor_abs2=1.7, decay_type='vector-pseudoscalar', recoil_pdg_id=111,
            recoil_mass_gev=.1349768, parent_polarization='unpolarized',
            parent_mass_model='zero-width-pole', provenance='synthetic fixture')
        self.models[223] = omega
        parents = self.parents + [dict(index=36,pdg_id=223,mass_gev=.78266)]
        br = .2
        value = parent_branching_coefficient(.78266,.3,omega,br)
        expected = .0833*1.7*br*(two_body_momentum(.78266,.1349768,.3)/
                                               two_body_momentum(.78266,.1349768,0.))**3
        self.assertAlmostEqual(value,expected,places=14)
        # A common bound selects the actual mixture. Pooling equal numbers
        # from independent component proposals would fail this closure.
        bound = coefficient_bound(2.,br,self.models,.3)
        rng = random.Random(761017102)
        accepted = 0
        omega_selected = 0
        count = 60000
        total = choose_collision(parents,.3,br,self.models,bound,random.Random(1))['coefficient_sum']
        for _ in range(count):
            decision = choose_collision(parents,.3,br,self.models,bound,rng)
            if decision['accepted']:
                accepted += 1
                omega_selected += decision['parent_index'] == 36
        probability = total/bound
        self.assertLess(abs(accepted/count-probability),5*math.sqrt(probability*(1-probability)/count))
        fraction = value/total
        self.assertLess(abs(omega_selected/accepted-fraction),5*math.sqrt(fraction*(1-fraction)/accepted))
        self.assertEqual(parent_branching_coefficient(.78266,.7,omega,br),0.)
        with self.assertRaisesRegex(ValueError,'zero-width'):
            coefficient_bound(2.,br,{223:dict(omega,mass_max_gev=.85)},.3)

    def test_vector_parent_card_requires_qualified_native_pole_and_form_factor_domain(self):
        parent = dict(pdg_id=223,mass_gev=.78266,pdg_reference='synthetic fixture',
            br_p_gamma=.0833,br_p_gamma_uncertainty=.0025,decay_type='vector-pseudoscalar',
            recoil_pdg_id=111,recoil_mass_gev=.1349768,parent_polarization='unpolarized',
            parent_mass_model='zero-width-pole',transition_form_factor=dict(kind='single-pole-slope',
                slope_gev_minus2=2.223,slope_stat_gev_minus2=.026,slope_syst_gev_minus2=.037,
                valid_mass_range_gev=[.211316751,.64],reference='synthetic fixture'))
        configuration = dict(parents=[dict(pdg_id=223,mass_min_gev=.78266,mass_max_gev=.78266,mass_width_gev=0.)])
        model = resolved_parent_models(dict(parents=[parent]),configuration,.3)[223]
        self.assertTrue(model['kinematically_open'])
        self.assertGreater(model['relative_model_uncertainty'],0.)
        with self.assertRaisesRegex(ValueError,'domain'):
            resolved_parent_models(dict(parents=[parent]),configuration,.645)
        self.assertFalse(resolved_parent_models(dict(parents=[parent]),configuration,.7)[223]['kinematically_open'])
        configuration['parents'][0]['mass_width_gev'] = .00849
        with self.assertRaisesRegex(ValueError,'zero-width'):
            resolved_parent_models(dict(parents=[parent]),configuration,.3)

    def test_one_parent_selects_exclusive_channels_without_duplicate_source_entries(self):
        first = dict(mass_min_gev=1.02, mass_max_gev=1.02, br_p_gamma=.013,
            form_factor_abs2=1.2, decay_type='vector-pseudoscalar', recoil_pdg_id=221,
            recoil_mass_gev=.548, parent_polarization='unpolarized',
            parent_mass_model='zero-width-pole', provenance='synthetic fixture', channel='phi_eta')
        second = dict(first, br_p_gamma=.0013, recoil_pdg_id=111,
                      recoil_mass_gev=.135, channel='phi_pi0')
        models = {333: dict(mass_min_gev=1.02, mass_max_gev=1.02,
                            exclusive_channels=[first,second])}
        parent = dict(index=5,pdg_id=333,mass_gev=1.02)
        values = [parent_branching_coefficient(1.02,.3,row,.3) for row in (first,second)]
        bound = coefficient_bound(2.04,.3,models,.3)
        self.assertAlmostEqual(bound,2*sum(values))
        rng = random.Random(761017202)
        selected = {'phi_eta':0,'phi_pi0':0}
        for _ in range(30000):
            row = choose_collision([parent],.3,.3,models,bound,rng)
            self.assertEqual(row['parent_count'],1)
            self.assertAlmostEqual(row['coefficient_sum'],sum(values))
            if row['accepted']:
                self.assertEqual(row['parent_index'],5)
                selected[row['selected_channel']] += 1
        probability = values[1]/sum(values)
        count = sum(selected.values())
        self.assertLess(abs(selected['phi_pi0']/count-probability),
                        5*math.sqrt(probability*(1-probability)/count))
        with self.assertRaisesRegex(ValueError,'repeated'):
            choose_collision([parent,parent],.3,.3,models,bound,rng)

    def test_charged_transition_requires_the_matching_recoil_charge(self):
        model = dict(mass_min_gev=.77511,mass_max_gev=.77511,br_p_gamma=.0004,
            form_factor_abs2=1.,decay_type='vector-pseudoscalar',recoil_pdg_id=211,
            recoil_mass_gev=.13957,parent_polarization='unpolarized',
            parent_mass_model='zero-width-pole',provenance='synthetic fixture')
        self.assertGreater(coefficient_bound(2.,.3,{213:model},.3),0.)
        with self.assertRaisesRegex(ValueError,'charge-conjugate'):
            coefficient_bound(2.,.3,{-213:model},.3)
        self.assertGreater(coefficient_bound(2.,.3,{-213:dict(model,recoil_pdg_id=-211)},.3),0.)


if __name__ == '__main__':
    unittest.main()
