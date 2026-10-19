# Code style: AI-code. AI-generated material-SIM audit regression checks.
import importlib.util
from pathlib import Path
import unittest


SOURCE = Path(__file__).resolve().parents[1] / 'scripts/audit_shift_material_scout.py'
SPEC = importlib.util.spec_from_file_location('audit_shift_material_scout', SOURCE)
SCOUT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SCOUT)


def muon(number, pdg, plus=(1, 2), minus=(1, 2)):
    return dict(track_id=number, pdg=pdg, momentum_GeV=[0., 0., 0.], energy_GeV=1.,
                source_type='test', CSC_station_hits=dict(plus={x: 6 for x in plus},
                                                         minus={x: 6 for x in minus}))


class MaterialScoutTest(unittest.TestCase):
    def test_same_sign_pairs_are_retained_for_charge_flip_studies(self):
        selected, pairs = SCOUT.supported_pairs([muon(1, 13), muon(2, 13), muon(3, -13)], 2)
        self.assertEqual(len(selected), 3)
        self.assertEqual(len(pairs), 3)
        self.assertEqual(sum(pair['opposite_charge'] for pair in pairs), 2)
        self.assertEqual(pairs[0]['mass_at_birth_GeV'], 2.)

    def test_hits_in_only_one_endcap_do_not_establish_both_support(self):
        selected, pairs = SCOUT.supported_pairs([muon(1, 13, minus=()), muon(2, -13)], 2)
        self.assertEqual([row['track_id'] for row in selected], [2])
        self.assertEqual(pairs, [])

    def test_many_layers_in_one_station_are_not_two_stations(self):
        value = muon(1, 13, plus=(1,))
        value['CSC_station_hits']['plus'][1] = 1000
        selected, _ = SCOUT.supported_pairs([value], 2)
        self.assertEqual(selected, [])

    def test_repeated_muon_track_is_not_a_new_leg(self):
        with self.assertRaisesRegex(ValueError, 'distinct'):
            SCOUT.supported_pairs([muon(1, 13), muon(1, -13)], 2)

    def test_invalid_station_requirement_fails(self):
        for threshold in (0, 5, 1.5, True):
            with self.subTest(threshold=threshold), self.assertRaises(ValueError):
                SCOUT.supported_pairs([], threshold)

    def test_material_secondary_exists_without_a_vacuum_gen_muon(self):
        self.assertEqual(SCOUT.muon_source([211], False), 'material_or_other_G4_secondary')

    def test_open_heavy_flavor_and_prompt_quarkonium_are_separate(self):
        self.assertEqual(SCOUT.muon_source([411, 421], True), 'heavy_flavor')
        self.assertEqual(SCOUT.muon_source([443], True), 'prompt_quarkonium')
        self.assertEqual(SCOUT.muon_source([211, 130], True), 'pion_kaon_decay')

    def test_long_lived_pion_from_heavy_flavor_is_still_a_pion_decay(self):
        self.assertEqual(SCOUT.muon_source([211, 411, 511], True, [211]), 'pion_kaon_decay')

    def test_saved_birth_state_does_not_claim_survival_or_absorption(self):
        self.assertEqual(SCOUT.material_outcome([]), 'terminal_state_not_recorded')
        self.assertEqual(SCOUT.material_outcome([dict(process_subtype=111)]),
                         'hadronic_interaction_observed')
        self.assertEqual(SCOUT.material_outcome([dict(process_subtype=121)]),
                         'inelastic_interaction_observed')
        self.assertEqual(SCOUT.material_outcome([dict(process_subtype=201)]), 'decay_observed')

    def test_missing_saved_parent_is_explicit(self):
        track = dict(vertex=dict(parent_G4_track_id=12))
        ancestry, missing = SCOUT.saved_ancestry(track, {})
        self.assertEqual(ancestry, [])
        self.assertEqual(missing, 12)

    def test_saved_ancestry_cycle_fails(self):
        track = dict(vertex=dict(parent_G4_track_id=1))
        parents = {1: dict(pdg=211, GEN_barcode=40, vertex=dict(parent_G4_track_id=1))}
        with self.assertRaisesRegex(ValueError, 'Cycle'):
            SCOUT.saved_ancestry(track, parents)

    def test_one_selected_event_keeps_full_original_lumi_denominator(self):
        values = dict(runs=[dict(run=3), dict(run=4)],
                      lumis=[dict(run=3, lumi=1, tried=100000), dict(run=3, lumi=2, tried=90000),
                             dict(run=4, lumi=1, tried=80000)])
        filtered = SCOUT.represented_metadata(values, {(3, 1, 42)})
        self.assertEqual(filtered, dict(runs=[dict(run=3)], lumis=[dict(run=3, lumi=1, tried=100000)]))
        self.assertEqual(len(values['lumis']), 3)


if __name__ == '__main__':
    unittest.main()
