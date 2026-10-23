# Code style: AI-code. AI-generated independent source-coverage checks.
from pathlib import Path
import json
import math
import random
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from audit_dark_photon_meson_coverage import (audit_coverage, particle_data_inventory,
    transition_candidates, model_owned_channels, catalogue_ownership, CATALOGUE,
    physical_mesons, final_parent_yield_bound, FeatureMoments, collision_mean_upper_bound,
    bind_completion_scope)
from dark_photon_meson_source import resolved_parent_models


def source():
    return dict(particle_data=[
        dict(pdg_id=111, mass_gev=.1349768, mass_min_gev=.1349768, mass_max_gev=.1349768,
             channels=[dict(br=.988, products=[22, 22])]),
        dict(pdg_id=211, mass_gev=.13957039, channels=[]),
        dict(pdg_id=221, mass_gev=.547862, channels=[dict(br=.3936, products=[22, 22])]),
        dict(pdg_id=223, mass_gev=.78266, mass_min_gev=.75, mass_max_gev=.85,
             channels=[dict(br=.0833, products=[111, 22])]),
        dict(pdg_id=331, mass_gev=.95778, channels=[dict(br=.2948, products=[113, 22])]),
        dict(pdg_id=113, mass_gev=.77526, mass_min_gev=.3, mass_max_gev=1.5,
             channels=[dict(br=1., products=[211, -211])]),
    ])


class CoverageTest(unittest.TestCase):
    def test_physical_inventory_owns_neutral_kaon_once_after_flavor_conversion(self):
        records = [dict(index=1,pdg_id=311,daughter_indices=[2]),
                   dict(index=2,pdg_id=130,daughter_indices=[])]
        physical, excluded = physical_mesons(dict(mesons=records))
        self.assertEqual([row['pdg_id'] for row in physical], [130])
        self.assertEqual(excluded, {'neutral_kaon_flavor_to_mass_eigenstate':1})

    def test_physical_inventory_keeps_feeddown_species_and_final_long_lived_parents(self):
        records = [dict(index=1,pdg_id=331,daughter_indices=[2]),
                   dict(index=2,pdg_id=221,daughter_indices=[]),
                   dict(index=3,pdg_id=310,daughter_indices=[],final=True)]
        physical, excluded = physical_mesons(dict(mesons=records))
        self.assertEqual([row['pdg_id'] for row in physical], [331,221,310])
        self.assertEqual(excluded, {})

    def test_physical_inventory_skips_copies_but_keeps_charge_conjugates(self):
        records = [dict(index=1,pdg_id=213,daughter_indices=[2]),
                   dict(index=2,pdg_id=213,daughter_indices=[]),
                   dict(index=3,pdg_id=-213,daughter_indices=[])]
        physical, excluded = physical_mesons(dict(mesons=records))
        self.assertEqual([row['index'] for row in physical], [2,3])
        self.assertEqual(excluded, {'same_species_generator_copy':1})

    def test_physical_inventory_rejects_duplicate_native_event_indices(self):
        with self.assertRaisesRegex(ValueError,'repeats'):
            physical_mesons(dict(mesons=[dict(index=1),dict(index=1)]))

    def test_final_parent_bound_cannot_be_used_for_decaying_ancestor_multiplicities(self):
        parent = dict(may_decay=True,mean_proper_lifetime_mm=0.,mass_min_gev=.5)
        result = final_parent_yield_bound(parent,dict(limitTau0=True,tau0Max=10),
            dict(check_event=True,energy_tolerance=.01,mass_tolerance=.001),dict(eA_gev=1.,eB_gev=10.),
            2000,.8,1.e9)
        self.assertIsNone(result)

    def test_final_parent_bound_uses_whole_collision_ceiling_and_never_sets_zero_from_mc(self):
        parent = dict(may_decay=False,mean_proper_lifetime_mm=100.,mass_min_gev=.5)
        result = final_parent_yield_bound(parent,dict(limitTau0=True,tau0Max=10),
            dict(check_event=True,energy_tolerance=.01,mass_tolerance=.001),dict(eA_gev=1.,eB_gev=10.),
            2000,0.,1.e9)
        self.assertEqual(result['maximum_parents_per_collision'],22)
        self.assertGreater(result['mean_parents_per_collision_upper'],0.)
        yields = result['maximum_accepted_events_if_branching_and_efficiency_are_one']
        self.assertAlmostEqual(yields['0.3']/yields['0.03'],10.)
        self.assertIn('No epsilon scaling',result['interpretation'])

    def test_mean_lifetime_cut_is_distinct_from_a_cut_on_the_selected_flight(self):
        parent = dict(may_decay=True,mean_proper_lifetime_mm=26.8,mass_min_gev=.5)
        shared = (dict(check_event=True,energy_tolerance=.01,mass_tolerance=.001),dict(eA_gev=1.,eB_gev=10.),2000,.8,1.e9)
        self.assertIsNotNone(final_parent_yield_bound(parent,dict(limitTau0=True,tau0Max=10),*shared))
        self.assertIsNone(final_parent_yield_bound(parent,dict(limitTau0=False,tau0Max=10),*shared))

    def test_streaming_features_keep_full_moments_without_unbounded_arrays(self):
        features = FeatureMoments(maximum_sample=32)
        rng = random.Random(123)
        for value in range(10000):
            features.add(float(value),rng)
        report = features.summary()
        self.assertEqual(report['count'],10000)
        self.assertEqual(report['minimum'],0.)
        self.assertEqual(report['maximum'],9999.)
        self.assertEqual(report['mean'],4999.5)
        self.assertEqual(report['quantile_sample_size'],32)
        self.assertGreater(report['sample_variance'],0.)

    def test_final_parent_bound_includes_the_actual_native_mass_check_tolerance(self):
        parent = dict(may_decay=False,mean_proper_lifetime_mm=100.,mass_min_gev=.5)
        result = final_parent_yield_bound(parent,dict(limitTau0=True,tau0Max=10),
            dict(check_event=True,energy_tolerance=.01,mass_tolerance=.001),
            dict(eA_gev=1.,eB_gev=10.),2000,0.,1.e9)
        self.assertAlmostEqual(result['minimum_final_particle_energy_gev'],.499)
        parent['mass_min_gev'] = .0001
        self.assertIsNone(final_parent_yield_bound(parent,dict(limitTau0=True,tau0Max=10),
            dict(check_event=True,energy_tolerance=.01,mass_tolerance=.001),
            dict(eA_gev=1.,eB_gev=10.),2000,0.,1.e9))

    def test_empirical_bernstein_keeps_range_remainder_when_no_parent_was_seen(self):
        bound = collision_mean_upper_bound(0.,0,10000,14000,.975,'empirical_bernstein')
        self.assertGreater(bound['mean_upper'],0.)
        self.assertAlmostEqual(bound['mean_upper'],7.*14000*math.log(80)/(3.*9999))
        self.assertEqual(bound['sample_variance'],0.)
        self.assertIn('Theorem 4',bound['reference'])

    def test_empirical_bernstein_uses_collision_variance_for_clustered_parents(self):
        # Fifty collisions with four parents and fifty with none: variance is
        # four per collision, not the variance of 200 independent parents.
        bound = collision_mean_upper_bound(2.,800,100,4,.975,'empirical_bernstein')
        self.assertAlmostEqual(bound['sample_variance'],400./99.)
        self.assertIn('correlations among parents',bound['scope'])

    def test_empirical_bernstein_rejects_missing_or_inconsistent_raw_moments(self):
        for second in (None,1,10000000):
            with self.subTest(second=second),self.assertRaisesRegex(ValueError,'second moment'):
                collision_mean_upper_bound(2.,second,100,4,.975,'empirical_bernstein')

    def test_broad_parent_native_tail_is_not_closed_at_pole_endpoint(self):
        omega, = [row for row in transition_candidates(source()) if row['parent_pdg_id'] == 223]
        self.assertAlmostEqual(omega['native_support_endpoint_gev'], .85 - .1349768)
        self.assertGreater(omega['native_support_endpoint_gev'], .7)
        self.assertTrue(omega['dimuon_open_in_table'])

    def test_identical_photon_candidate_count_is_kept_without_duplicate_rows(self):
        eta, = [row for row in transition_candidates(source()) if row['parent_pdg_id'] == 221]
        self.assertEqual(eta['replacement_multiplicity'], 2)
        self.assertFalse(eta['independent_rate_qualified'])

    def test_resonant_and_radiative_paths_remain_unowned_overlaps(self):
        candidates = [row for row in transition_candidates(source()) if row['parent_pdg_id'] == 331]
        self.assertEqual({row['replace_pdg_id'] for row in candidates}, {22, 113})
        self.assertTrue(all(not row['independent_rate_qualified'] for row in candidates))
        self.assertTrue(all('ownership' in row['reason'] for row in candidates))

    def test_implemented_channels_do_not_establish_physics_completeness(self):
        model = dict(channels=['eta -> gamma Aprime'], parents=[dict(pdg_id=221,
            transition_form_factor=dict(physical_dimuon_mass_range_gev=[.211316751, .547862]))])
        report = audit_coverage(model, [.22, .5, .7], source())
        self.assertEqual(report['included_channels'], model['channels'])
        self.assertFalse(report['physics_complete'])
        self.assertFalse(report['source_sampling_verified'])
        eta, = [row for row in report['catalogue'] if row['channel'] == 'eta -> gamma Aprime']
        self.assertTrue(eta['implemented_in_model'])
        self.assertEqual(eta['pole_mass_open_points_gev'], [.22, .5])
        statuses = {row['channel']: row['status'] for row in report['remaining_channels']}
        self.assertNotIn('eta -> gamma Aprime', statuses)
        self.assertEqual(report['implemented_channels'], ['eta -> gamma Aprime'])
        self.assertEqual(report['unvalidated_channels'][0]['status'], 'needs_validation')
        self.assertEqual(statuses['omega -> pi0 Aprime'], 'missing_model')
        self.assertEqual(statuses['diffractive parent-meson source'], 'missing_source')

    def test_photon_and_dipion_channels_share_one_parent_without_losing_ownership(self):
        channels = [dict(pdg_id=331, channel='eta_prime->gamma_Aprime'),
            dict(pdg_id=331, channel='eta_prime->pipi_Aprime',
                decay_type='pseudoscalar-dipion',
                dipion_model=dict(valid_mass_range_gev=[.211316751, .67863922]))]
        model = dict(channels=[row['channel'] for row in channels], parents=channels,
                     physics_complete=True, production_rate_validated=True)
        report = audit_coverage(model, [.22, .5, .7], source())
        self.assertEqual(set(model_owned_channels(model)), {(331, (22,)), (331, (-211, 211))})
        dipion, = [row for row in report['catalogue'] if row['channel'] == 'etaprime -> pi+ pi- Aprime']
        self.assertTrue(dipion['implemented_in_model'])
        self.assertEqual(dipion['owned_model_channel'], 'eta_prime->pipi_Aprime')
        self.assertEqual(dipion['unsupported_form_factor_mass_points_gev'], [])
        self.assertEqual(report['coverage_counts']['implemented'], 2)
        self.assertEqual(report['coverage_counts']['unvalidated'], 2)
        self.assertFalse(report['physics_complete'])
        self.assertTrue(all(row['status'] == 'needs_validation' for row in report['unvalidated_channels']))
        self.assertNotIn(dipion['channel'], {row['channel'] for row in report['remaining_channels']})

    def test_grouped_and_flat_channels_have_identical_audit_ownership(self):
        channels = [dict(channel='phi->eta_Aprime', recoil_pdg_id=221),
                    dict(channel='phi->pi0_Aprime', recoil_pdg_id=111)]
        grouped = dict(channels=[row['channel'] for row in channels],
                       parents=[dict(pdg_id=333, exclusive_channels=channels)])
        flat = dict(channels=grouped['channels'], parents=[dict(pdg_id=333, **row) for row in channels])
        self.assertEqual(audit_coverage(grouped, [.22, .4], source()),
                         audit_coverage(flat, [.22, .4], source()))
        duplicate = dict(parents=[dict(pdg_id=333, exclusive_channels=[channels[0], channels[0]])])
        with self.assertRaisesRegex(ValueError, 'ownership'):
            model_owned_channels(duplicate)

    def test_leptonic_kaon_flavors_and_charges_have_distinct_ownership(self):
        channels = [dict(pdg_id=sign*321, channel=f'K{sign}_{lepton}',
            decay_type='weak-kaon-leptonic', charged_lepton_pdg_id=-sign*lepton,
            neutrino_pdg_id=sign*(lepton+1)) for sign in (1, -1) for lepton in (11, 13)]
        owners = model_owned_channels(dict(parents=channels))
        self.assertEqual(set(owners), {(321, (-11, 12)), (321, (-13, 14)),
                                      (-321, (-12, 11)), (-321, (-14, 13))})
        with self.assertRaisesRegex(ValueError, 'charge-matched'):
            model_owned_channels(dict(parents=[dict(channels[0], neutrino_pdg_id=-12)]))

    def test_actual_leading_card_has_thirty_distinct_exclusive_channels(self):
        card = Path(__file__).resolve().parents[1]/'config/physics/dark_photon_meson_leading_model.json'
        model = json.loads(card.read_text())
        self.assertEqual(len(model_owned_channels(model)), 30)

    def test_detailed_scope_keeps_every_generic_inventory_gap(self):
        inventory = [dict(channel='generic missing family', status='missing_model', reason='unbounded')]
        report = dict(included_channels=['one'], remaining_channels=inventory, physics_complete=False)
        scope = dict(included_channels=['one'], physics_complete=False,
                     remaining_channels=[dict(channel='detailed current', status='needs_validation', reason='conditional bound')])
        result = bind_completion_scope(report, scope, 'pinned')
        self.assertEqual(result['inventory_remaining_channels'], inventory)
        self.assertEqual(result['remaining_channels'], scope['remaining_channels'])
        self.assertEqual(report['remaining_channels'], inventory)
        self.assertFalse(result['physics_complete'])
        with self.assertRaisesRegex(ValueError, 'differs'):
            bind_completion_scope(report, dict(scope, included_channels=['different']), 'pinned')

    def test_charged_catalogue_channels_require_both_conjugates(self):
        for parent, recoil in ((213, 211), (313, 311), (323, 321)):
            with self.subTest(parent=parent):
                positive = dict(pdg_id=parent, recoil_pdg_id=recoil, channel='positive')
                model = dict(channels=['positive'], parents=[positive])
                report = audit_coverage(model, [.22], source())
                partial, = [row for row in report['catalogue'] if row['parent_pdg_id'] == parent and row['recoil_pdg_id'] == recoil]
                self.assertFalse(partial['implemented_in_model'])
                self.assertTrue(partial['partially_implemented_in_model'])
                self.assertEqual(partial['missing_parent_pdg_ids'], [-parent])
                self.assertIn('charge-conjugate', next(row['reason'] for row in report['remaining_channels']
                    if row['channel'] == partial['channel']))
                model['parents'].append(dict(pdg_id=-parent, recoil_pdg_id=-recoil, channel='negative'))
                model['channels'].append('negative')
                report = audit_coverage(model, [.22], source())
                complete, = [row for row in report['catalogue'] if row['parent_pdg_id'] == parent and row['recoil_pdg_id'] == recoil]
                self.assertTrue(complete['implemented_in_model'])
                self.assertEqual(complete['owned_model_channels'], ['positive', 'negative'])

    def test_charged_rho_dipion_owns_charge_correct_both_daughters(self):
        item, = [row for row in CATALOGUE if row['channel'] == 'rho+- -> pi+- pi0 Aprime']
        self.assertEqual(catalogue_ownership(item), [(213, (111, 211)), (-213, (-211, 111))])
        positive = dict(pdg_id=213, channel='rho_plus->pi_charged_pi0_Aprime',
            decay_type='charged-vector-dipion', recoil_pdg_ids=[211, 111],
            charged_rho_radiative_model=dict(valid_mass_range_gev=[.211316751, .50056281]))
        negative = dict(positive, pdg_id=-213, channel='rho_minus->pi_charged_pi0_Aprime',
                        recoil_pdg_ids=[-211, 111])
        model = dict(parents=[positive], channels=[positive['channel']])
        report = audit_coverage(model, [.22, .5, .5006, .7], source())
        row, = [row for row in report['catalogue'] if row['channel'] == item['channel']]
        self.assertFalse(row['implemented_in_model'])
        self.assertTrue(row['partially_implemented_in_model'])
        self.assertEqual(row['missing_parent_pdg_ids'], [-213])
        model['parents'].append(negative)
        model['channels'].append(negative['channel'])
        report = audit_coverage(model, [.22, .5, .5006, .7], source())
        row, = [row for row in report['catalogue'] if row['channel'] == item['channel']]
        self.assertTrue(row['implemented_in_model'])
        self.assertEqual(row['owned_model_channels'], model['channels'])
        self.assertEqual(row['pole_mass_open_points_gev'], [.22, .5])
        self.assertEqual(row['declared_form_factor_domains_gev'], [[.211316751, .50056281]]*2)
        self.assertFalse(report['physics_complete'])
        self.assertEqual(row['status'], 'needs_validation')
        self.assertIn('g_EM=2', row['reason'])
        self.assertIn('12 percent', row['reason'])
        for recoil in (None, [211, -211], [-211, 111], [211, -111], [211]):
            bad = dict(positive, recoil_pdg_ids=recoil)
            with self.subTest(recoil=recoil), self.assertRaises(ValueError):
                model_owned_channels(dict(parents=[bad]))

    def test_charged_rho_and_two_body_channel_share_parent_without_overlap(self):
        channels = [dict(pdg_id=213, channel='rho_plus->pi_charged_Aprime', recoil_pdg_id=211),
            dict(pdg_id=213, channel='rho_plus->pi_charged_pi0_Aprime',
                 decay_type='charged-vector-dipion', recoil_pdg_ids=[211, 111])]
        flat = dict(parents=channels, channels=[row['channel'] for row in channels])
        grouped = dict(parents=[dict(pdg_id=213, exclusive_channels=channels)], channels=flat['channels'])
        expected = {(213, (211,)), (213, (111, 211))}
        self.assertEqual(set(model_owned_channels(flat)), expected)
        self.assertEqual(set(model_owned_channels(grouped)), expected)
        self.assertEqual(audit_coverage(flat, [.22], source()), audit_coverage(grouped, [.22], source()))
        duplicate = dict(parents=channels+[dict(channels[1], recoil_pdg_ids=[111, 211])])
        with self.assertRaisesRegex(ValueError, 'ownership'):
            model_owned_channels(duplicate)

    def test_charged_model_missing_nonfinite_or_short_domain_stays_unsupported(self):
        for domain in (None, [.211316751, float('inf')], [.211316751, .3]):
            parents = [dict(pdg_id=sign*213, channel=str(sign), decay_type='charged-vector-dipion',
                recoil_pdg_ids=[sign*211, 111],
                charged_rho_radiative_model=dict(valid_mass_range_gev=domain)) for sign in (1, -1)]
            report = audit_coverage(dict(parents=parents, channels=['1', '-1']), [.4], source())
            row, = [row for row in report['catalogue'] if row['channel'] == 'rho+- -> pi+- pi0 Aprime']
            self.assertEqual(row['unsupported_form_factor_mass_points_gev'], [.4])
            self.assertFalse(report['physics_complete'])

    def test_unknown_declared_transition_is_reported_without_claiming_closure(self):
        report = audit_coverage(dict(channels=['a0->gamma_Aprime'],
            parents=[dict(pdg_id=9000111, channel='a0->gamma_Aprime')]), [.3], source())
        self.assertEqual(report['unclassified_model_channels'][0]['parent_pdg_id'], 9000111)
        self.assertEqual(report['coverage_counts']['unclassified_model_channels'], 1)
        self.assertFalse(report['physics_complete'])

    def test_all_explicit_exclusive_channels_leave_validation_and_source_gaps_open(self):
        parents = []
        for item in CATALOGUE:
            for pdg, recoil in catalogue_ownership(item):
                row = dict(pdg_id=pdg, channel=str(pdg) + ':' + str(recoil))
                if len(recoil) == 2:
                    row.update(decay_type='charged-vector-dipion' if abs(pdg) == 213 else 'pseudoscalar-dipion',
                               recoil_pdg_ids=list(recoil))
                else:
                    row['recoil_pdg_id'] = recoil[0]
                parents.append(row)
        inventory = dict(source(), source_sampling_verified=True, resolved_source_configuration=True)
        model = dict(parents=parents, channels=[row['channel'] for row in parents], physics_complete=True)
        report = audit_coverage(model, [.22, .5, .9], inventory)
        self.assertEqual(report['coverage_counts'], dict(implemented=15, unvalidated=15,
            missing_models=4, missing_sources=2, unclassified_model_channels=0))
        self.assertEqual(len(report['remaining_channels']), 6)
        self.assertTrue(all(row['status'] == 'needs_validation' for row in report['unvalidated_channels']))
        self.assertFalse(report['physics_complete'])

    def test_malformed_exclusive_groups_and_dipion_recoils_are_rejected(self):
        bad = [dict(pdg_id=221, exclusive_channels=[]),
               dict(pdg_id=221, exclusive_channels=[dict(pdg_id=331)]),
               dict(pdg_id=221, decay_type='pseudoscalar-dipion', recoil_pdg_ids=[111, 111]),
               dict(pdg_id=221, recoil_pdg_ids=None), dict(pdg_id=221, channel=[])]
        for parent in bad:
            with self.subTest(parent=parent), self.assertRaises(ValueError):
                model_owned_channels(dict(parents=[parent]))
        with self.assertRaisesRegex(ValueError, 'ownership'):
            model_owned_channels(dict(parents=[dict(pdg_id=221, channel='same'),
                                              dict(pdg_id=331, channel='same')]))

    def test_omega_form_factor_domain_is_separate_from_native_parent_support(self):
        model = dict(channels=['omega -> pi0 Aprime'], parents=[dict(pdg_id=223,
            recoil_pdg_id=111, transition_form_factor=dict(valid_mass_range_gev=[.211316751, .64]))])
        report = audit_coverage(model, [.6, .645, .7], source())
        omega, = [row for row in report['catalogue'] if row['channel'] == 'omega -> pi0 Aprime']
        self.assertEqual(omega['unsupported_form_factor_mass_points_gev'], [.645])
        self.assertFalse(omega['pole_endpoint_is_native_cutoff'])

    def test_disabled_and_zero_rate_table_modes_do_not_become_candidates(self):
        inventory = source()
        inventory['particle_data'][2]['channels'] = [dict(br=0., products=[22, 22]),
                                                    dict(br=.1, on_mode=0, products=[22, 22])]
        self.assertFalse(any(row['parent_pdg_id'] == 221 for row in transition_candidates(inventory)))

    def test_xml_documentation_is_parsed_without_trusting_default_as_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'ParticleData.xml'
            path.write_text('<chapter><p>Non-XML &unknown; documentation</p>'
                '<particle id="223" name="omega" m0="0.78266" mWidth="0.00849" mMin="0.75" mMax="0.85">'
                '<channel onMode="1" bRatio="0.0833" products="111 22"/></particle>'
                '<particle id="2212" name="proton" m0=".938"><channel onMode="1" bRatio="1" products="22 22"/></particle></chapter>')
            inventory = particle_data_inventory(path)
        self.assertEqual(len(inventory['particle_data']), 1)
        self.assertEqual(inventory['particle_data'][0]['mass_max_gev'], .85)
        self.assertFalse(inventory['resolved_source_configuration'])

    def test_unphysical_masses_and_ambiguous_ownership_are_rejected(self):
        for masses in ([], [.2], [float('nan')]):
            with self.assertRaises(ValueError):
                audit_coverage({}, masses, source())
        with self.assertRaisesRegex(ValueError, 'unique'):
            audit_coverage(dict(channels=['eta', 'eta']), [.3], source())
        with self.assertRaisesRegex(ValueError, 'ownership'):
            audit_coverage(dict(parents=[dict(pdg_id=221), dict(pdg_id=221)]), [.3], source())

    def test_expanded19_grid_retains_one_inventory_per_charged_parent(self):
        # Exercise the promoted card directly; historical coverage is separate.
        path = Path(__file__).resolve().parents[1] / 'config/campaigns/dark_photon_meson_extended_model.json'
        card = json.loads(path.read_text())
        species = {p['pdg_id']: p for p in card['parents']}
        configuration = dict(parents=[dict(pdg_id=pdg, mass_min_gev=p['mass_gev'],
            mass_max_gev=p['mass_gev'], mass_width_gev=0.) for pdg, p in species.items()])
        report = audit_coverage(card, [.22, .25, .3, .4, .5, .7, .9], source())
        self.assertEqual(len(card['channels']), 19)
        self.assertEqual(report['coverage_counts'], dict(implemented=15, unvalidated=15,
            missing_models=4, missing_sources=2, unclassified_model_channels=0))
        self.assertFalse(report['physics_complete'])
        self.assertEqual(len(model_owned_channels(card)), 19)
        for mass in (.22, .25, .3, .4, .5, .7, .9):
            models = resolved_parent_models(card, configuration, mass)
            self.assertEqual(len(models), 11)
            self.assertEqual(sum(len(group.get('exclusive_channels', [group]))
                                 for group in models.values()), 19)
            for sign in (1, -1):
                channels = models[sign*213]['exclusive_channels']
                self.assertEqual(len(channels), 2)
                dipion, = [row for row in channels if row['decay_type'] == 'charged-vector-dipion']
                self.assertEqual(dipion['recoil_pdg_ids'], [sign*211, 111])
                self.assertEqual(dipion['kinematically_open'], mass < .50056281)

    def test_historical17_card_grid_and_unsupported_omega_interval(self):
        # Preserve the exact previous card, including all its original inputs.
        path = Path(__file__).resolve().parent / 'fixtures/dark_photon_meson_extended_model17.json'
        card = json.loads(path.read_text())
        species = {p['pdg_id']: p for p in card['parents']}
        configuration = dict(parents=[dict(pdg_id=pdg, mass_min_gev=p['mass_gev'],
            mass_max_gev=p['mass_gev'], mass_width_gev=0.) for pdg, p in species.items()])
        report = audit_coverage(card, [.22, .25, .3, .4, .5, .7, .9], source())
        self.assertEqual(len(card['channels']), 17)
        self.assertEqual(report['coverage_counts'], dict(implemented=14, unvalidated=14,
            missing_models=5, missing_sources=2, unclassified_model_channels=0))
        self.assertTrue(all(not row['missing_parent_pdg_ids'] for row in report['catalogue']
                            if row['implemented_in_model']))
        for mass in (.22, .25, .3, .4, .5, .7, .9):
            models = resolved_parent_models(card, configuration, mass)
            self.assertEqual(set(models), {221, 331, 223, 333, 113, 213, -213, 313, -313, 323, -323})
            self.assertEqual(sum(len(group.get('exclusive_channels', [group]))
                                 for group in models.values()), 17)
            rho = models[113]['exclusive_channels']
            radiation, = [row for row in rho if row['decay_type'] == 'vector-dipion']
            self.assertEqual(radiation['kinematically_open'], mass < .49611922)
            if mass >= .7:
                self.assertTrue(all(not row['kinematically_open']
                                    for row in models[223]['exclusive_channels']))
            if mass == .7:
                self.assertTrue(any(row['kinematically_open']
                                    for row in models[333]['exclusive_channels']))
        with self.assertRaisesRegex(ValueError, 'domain'):
            resolved_parent_models(card, configuration, .645)
        self.assertFalse(card['physics_complete'])


if __name__ == '__main__':
    unittest.main()
