# Code style: AI-code. AI-generated tests for descriptive response predictors.
import copy
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from shift_sm_response_features import feature_values


def muon(pdg, x=300., z=14800., pz=-50., parents=()):
    return dict(pdg=pdg, birth_cm=[x, 0., z], momentum_GeV=[0., 0., pz],
        p_GeV=abs(pz), energy_GeV=math.hypot(pz, .105658),
        transport_parent_barcodes=list(parents), straight_line_towards_CMS_entry=True,
        material_to_CMS_entry=dict(complete=True, column_density_g_cm2=1000.))


def event(muons=(), parents=()):
    return dict(GEN_muons=len(muons), muons=list(muons), parent_flights=list(parents),
                unknown_transport_ancestor_barcodes=[])


def parent(barcode, depth, full=True):
    return dict(barcode=barcode, material=dict(interaction_depth_proxy=depth,
        full_saved_chord_coverage=full, complete=True))


class ResponseFeatureTest(unittest.TestCase):
    def test_empty_and_same_sign_are_not_pairs(self):
        empty=feature_values(event())
        self.assertEqual(empty['n_os_pairs'], 0)
        self.assertIsNone(empty['min_parent_prefix_depth'])
        self.assertFalse(empty['all_incoming_pairs_parent_depth_ge_10'])
        same=feature_values(event([muon(13),muon(13)]))
        self.assertEqual(same['n_os_pairs'], 0)
        self.assertIsNone(same['best_pair_miss_cm'])

    def test_pointing_pair_and_far_miss(self):
        hit=feature_values(event([muon(13),muon(-13)]))
        self.assertEqual(hit['n_both_forward_os_pairs'],1)
        self.assertEqual(hit['best_pair_miss_cm'],0.)
        self.assertEqual(hit['best_pair_two_station_miss_cm'],0.)
        self.assertAlmostEqual(hit['best_pair_max_column_loss_over_p'],.001688*1000/50)
        miss=feature_values(event([muon(13,x=10000),muon(-13,x=10000)]))
        self.assertGreater(miss['best_pair_miss_cm'],9000.)

    def test_missing_birth_preserves_unknown_and_no_pointing(self):
        missing=muon(13)
        missing['birth_cm']=None
        missing['straight_line_towards_CMS_entry']=False
        missing['material_to_CMS_entry']=dict(complete=False,
            column_density_g_cm2=None,unknown_reason='missing muon production vertex')
        result=feature_values(event([missing,muon(-13)]))
        self.assertEqual(result['n_incoming_os_pairs'],1)
        self.assertEqual(result['n_both_forward_os_pairs'],0)
        self.assertIn('missing muon production vertex',result['unknown_material_reasons'])

    def test_central_birth_keeps_direction_unknown_without_claiming_column(self):
        central=muon(13,z=700.)
        central['straight_line_towards_CMS_entry']=False
        central['material_to_CMS_entry']=dict(complete=False,column_density_g_cm2=None,
            unknown_reason='muon born inside unmodelled central CMS region')
        result=feature_values(event([central,muon(-13)]))
        self.assertEqual(result['n_incoming_os_pairs'],1)
        self.assertEqual(result['n_both_forward_os_pairs'],1)
        self.assertIsNone(result['best_pair_two_station_miss_cm'])
        self.assertIsNone(result['best_pair_max_column_loss_over_p'])

    def test_shared_parent_is_counted_once(self):
        result=feature_values(event([muon(13,parents=[7]),muon(-13,parents=[7])],
            [parent(7,6.)]))
        self.assertEqual(result['min_parent_prefix_depth'],6.)
        self.assertFalse(result['all_incoming_pairs_parent_depth_ge_10'])

    def test_minimum_uses_every_incoming_os_pair(self):
        result=feature_values(event([muon(13,parents=[1]),muon(-13,parents=[2]),
            muon(-13,parents=[3])],[parent(1,2.),parent(2,30.),parent(3,1.)]))
        self.assertEqual(result['n_incoming_os_pairs'],2)
        self.assertEqual(result['min_parent_prefix_depth'],3.)
        self.assertEqual(result['best_pair_parent_prefix_depth'],32.)
        self.assertFalse(result['all_incoming_pairs_parent_depth_ge_10'])

    def test_unknown_parent_prefix_is_described_without_absorption_or_mutation(self):
        original=event([muon(13,parents=[7]),muon(-13,parents=[7])],
            [parent(7,12.,full=False)])
        original['unknown_transport_ancestor_barcodes']=[9000001]
        unchanged=copy.deepcopy(original)
        result=feature_values(original)
        self.assertEqual(result['n_unknown_ancestor_barcodes'],1)
        self.assertEqual(result['min_parent_prefix_depth'],12.)
        self.assertTrue(result['all_incoming_pairs_parent_depth_ge_10'])
        self.assertFalse(result['best_pair_full_parent_coverage'])
        self.assertEqual(result['n_incomplete_incoming_parent_paths'],1)
        self.assertIn('known external prefix only',result['unknown_material_reasons'])
        self.assertEqual(original,unchanged)
        self.assertNotIn('sampling_probability',result)

    def test_all_pair_opportunities_keep_hard_pair_after_soft_geometric_tie(self):
        soft=muon(13,pz=-.1,parents=[7])
        result=feature_values(event([soft,muon(13),muon(-13)], [parent(7,15.)]))
        self.assertEqual(result['n_pointing_os_pairs'],2)
        self.assertLess(result['best_pair_min_energy_GeV'],1.)
        self.assertGreater(result['max_pair_min_energy_GeV'],50.)
        self.assertAlmostEqual(result['min_pair_max_column_loss_over_p'],.001688*1000/50)
        self.assertEqual(result['min_parent_prefix_depth_among_pointing_pairs'],0.)
        self.assertEqual(result['best_pair_parent_prefix_depth'],15.)
        # Low energy is retained in the description, not silently rejected.
        low=feature_values(event([soft,muon(-13,pz=-.1)],[parent(7,15.)]))
        self.assertEqual(low['n_pointing_os_pairs'],1)
        self.assertLess(low['max_pair_min_energy_GeV'],1.)

    def test_opportunity_unknown_column_is_not_zero(self):
        unknown=muon(13)
        unknown['material_to_CMS_entry']=dict(complete=False,
            column_density_g_cm2=None,unknown_reason='outside supplied geometry')
        result=feature_values(event([unknown,muon(-13)]))
        self.assertEqual(result['n_pointing_os_pairs'],1)
        self.assertEqual(result['n_pointing_pairs_known_muon_column'],0)
        self.assertIsNone(result['min_pair_max_column_loss_over_p'])
        miss=feature_values(event([muon(13,x=10000),muon(-13,x=10000)]))
        self.assertEqual(miss['n_pointing_os_pairs'],0)
        self.assertIsNone(miss['max_pair_min_energy_GeV'])
        self.assertIsNone(miss['min_parent_prefix_depth_among_pointing_pairs'])

    def test_absent_parent_and_invalid_material_fail(self):
        with self.assertRaisesRegex(ValueError,'absent parent'):
            feature_values(event([muon(13,parents=[7]),muon(-13)]))
        with self.assertRaisesRegex(ValueError,'nonnegative'):
            feature_values(event([muon(13,parents=[7]),muon(-13)], [parent(7,-1.)]))


if __name__ == '__main__':
    unittest.main()
