# Code style: AI-code. AI-generated independent source-coverage checks.
from pathlib import Path
import json
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from audit_dark_photon_meson_coverage import (audit_coverage, particle_data_inventory,
    transition_candidates, model_owned_channels, catalogue_ownership, CATALOGUE)
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

    def test_charged_catalogue_channels_require_both_conjugates(self):
        for parent, recoil in ((213, 211), (313, 311), (323, 321)):
            with self.subTest(parent=parent):
                positive = dict(pdg_id=parent, recoil_pdg_id=recoil, channel='positive')
                model = dict(channels=['positive'], parents=[positive])
                report = audit_coverage(model, [.22], source())
                partial, = [row for row in report['catalogue'] if row['parent_pdg_id'] == parent]
                self.assertFalse(partial['implemented_in_model'])
                self.assertTrue(partial['partially_implemented_in_model'])
                self.assertEqual(partial['missing_parent_pdg_ids'], [-parent])
                self.assertIn('charge-conjugate', next(row['reason'] for row in report['remaining_channels']
                    if row['channel'] == partial['channel']))
                model['parents'].append(dict(pdg_id=-parent, recoil_pdg_id=-recoil, channel='negative'))
                model['channels'].append('negative')
                report = audit_coverage(model, [.22], source())
                complete, = [row for row in report['catalogue'] if row['parent_pdg_id'] == parent]
                self.assertTrue(complete['implemented_in_model'])
                self.assertEqual(complete['owned_model_channels'], ['positive', 'negative'])

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
                    row.update(decay_type='pseudoscalar-dipion', recoil_pdg_ids=list(recoil))
                else:
                    row['recoil_pdg_id'] = recoil[0]
                parents.append(row)
        inventory = dict(source(), source_sampling_verified=True, resolved_source_configuration=True)
        model = dict(parents=parents, channels=[row['channel'] for row in parents], physics_complete=True)
        report = audit_coverage(model, [.22, .5, .9], inventory)
        self.assertEqual(report['coverage_counts'], dict(implemented=13, unvalidated=13,
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

    def test_extended_card_current_grid_and_unsupported_omega_interval(self):
        path = Path(__file__).resolve().parents[1] / 'config/campaigns/dark_photon_meson_extended_model.json'
        card = json.loads(path.read_text())
        species = {p['pdg_id']: p for p in card['parents']}
        configuration = dict(parents=[dict(pdg_id=pdg, mass_min_gev=p['mass_gev'],
            mass_max_gev=p['mass_gev'], mass_width_gev=0.) for pdg, p in species.items()])
        report = audit_coverage(card, [.22, .25, .3, .4, .5, .7, .9], source())
        self.assertEqual(len(card['channels']), 16)
        self.assertEqual(report['coverage_counts'], dict(implemented=13, unvalidated=13,
            missing_models=4, missing_sources=2, unclassified_model_channels=0))
        self.assertTrue(all(not row['missing_parent_pdg_ids'] for row in report['catalogue']
                            if row['implemented_in_model']))
        for mass in (.22, .25, .3, .4, .5, .7, .9):
            models = resolved_parent_models(card, configuration, mass)
            self.assertEqual(set(models), {221, 331, 223, 333, 113, 213, -213, 313, -313, 323, -323})
            self.assertEqual(sum(len(group.get('exclusive_channels', [group]))
                                 for group in models.values()), 16)
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
