# Code style: AI-code. AI-generated full-HepMC feature and control-support tests.
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import shift_gen_material_features as features


def particle(barcode, pdg, parents=(), status=2, birth=(0., 0., 100.), end=(0., 0., 90.)):
    return dict(barcode=barcode, pdg=pdg, status=status, parents=list(parents),
                momentum_GeV=[0., 0., -10.], energy_GeV=10.,
                birth_cm=list(birth) if birth is not None else None,
                end_cm=list(end) if end is not None else None)


class GenMaterialFeatureTests(unittest.TestCase):
    def test_isolated_scoring_is_identical_to_direct_scoring(self):
        rows=[particle(1,211),particle(2,13,[1],status=1,end=None)]
        scored=dict(complete=True,full_saved_chord_coverage=True,
                    interaction_depth_proxy=.4,column_density_g_cm2=20.)
        frame=dict(minimum_abs_z_cm=10.)
        with patch.object(features,'score_paths',side_effect=lambda *a:dict(paths=[dict(scored)])):
            direct=features.event_features(rows,[1,1,1],2.,[2.],object(),frame)
            isolated=features.bounded_event_features(rows,[1,1,1],2.,[2.],object(),frame,2.)
        self.assertEqual(direct,isolated)

    def test_one_timeout_preserves_graph_and_does_not_poison_next_event(self):
        rows=[particle(1,211),particle(2,13,[1],status=1,end=None)]
        frame=dict(minimum_abs_z_cm=10.)
        def slow(*unused):time.sleep(1.)
        with patch.object(features,'_score_event_to_file',side_effect=slow):
            result=features.bounded_event_features(rows,[1,1,1],2.,[2.],object(),frame,.02)
        baseline=features.event_features(rows,[1,1,1],2.,[2.],None,frame)
        for key in ('event_id','graph_sha256','native_weight','hepmc_weights'):
            self.assertEqual(result[key],baseline[key])
        self.assertTrue(result['material_navigation_timeout'])
        self.assertIsNone(result['parent_flights'][0]['material']['interaction_depth_proxy'])
        self.assertIn('timeout',result['parent_flights'][0]['material']['unknown_reason'])
        # A later event still exercises its own geometry, not a cohort fallback.
        scored=dict(complete=True,full_saved_chord_coverage=True,
                    interaction_depth_proxy=.4,column_density_g_cm2=20.)
        with patch.object(features,'score_paths',side_effect=lambda *a:dict(paths=[dict(scored)])):
            following=features.bounded_event_features(rows,[1,1,2],2.,[2.],object(),frame,2.)
        self.assertNotIn('material_navigation_timeout',following)
        self.assertEqual(following['parent_flights'][0]['material']['interaction_depth_proxy'],.4)

    def test_missing_child_result_is_a_failure_not_an_unknown_path(self):
        with patch.object(features,'_score_event_to_file',return_value=None):
            with self.assertRaisesRegex(RuntimeError,'child failed'):
                features.bounded_event_features([], [1,1,1],1.,[1.],object(),{},2.)
        for invalid in (0.,-1.,float('nan'),float('inf')):
            with self.assertRaises(ValueError):
                features.bounded_event_features([], [1,1,1],1.,[1.],None,{},invalid)

    def test_zero_muon_event_and_unfinished_hadrons_are_retained(self):
        row=features.event_features([particle(1,211,status=1,end=None)], [1,2,3], -2., [-2.])
        self.assertTrue(row['zero_GEN_muon_control'])
        self.assertEqual(row['native_weight'], -2.)
        self.assertEqual(row['incoming_stable_hadrons_without_saved_decay'],1)

    def test_immediate_pion_parent_is_not_replaced_by_heavy_ancestor(self):
        rows=[particle(1,511),particle(2,211,[1]),particle(3,13,[2],status=1,end=None)]
        row=features.event_features(rows,[1,1,1],1.,[1.])
        self.assertEqual(row['muons'][0]['first_non_muon_parent_barcodes'],[2])
        self.assertEqual(row['muons'][0]['ancestor_barcodes'],[1,2])
        self.assertEqual(row['muons'][0]['transport_parent_barcodes'],[1,2])
        self.assertIsNone(row['muons'][0]['parent_depth_proxy_sum'])

    def test_muon_copy_and_multiple_real_mothers_preserve_full_edges(self):
        rows=[particle(1,211),particle(2,321),particle(3,13,[1,2]),particle(4,13,[3],status=1,end=None)]
        muon=features.event_features(rows,[1,1,1],1.,[1.])['muons'][0]
        self.assertEqual(muon['immediate_parent_barcodes'],[3])
        self.assertEqual(muon['first_non_muon_parent_barcodes'],[1,2])
        self.assertEqual(muon['ancestor_barcodes'],[1,2,3])

    def test_cycles_missing_and_duplicate_barcodes_fail(self):
        for rows in ([particle(1,211,[2]),particle(2,13,[1],status=1)],
                     [particle(1,13,[99],status=1)],
                     [particle(1,13,status=1),particle(1,211)]):
            with self.assertRaises(ValueError):
                features.event_features(rows,[1,1,1],1.,[1.])

    def test_diamond_ancestry_is_not_double_counted(self):
        rows=[particle(1,511),particle(2,211,[1]),particle(3,321,[1]),particle(4,13,[2,3],status=1)]
        row=features.event_features(rows,[1,1,1],1.,[1.])
        self.assertEqual([p['barcode'] for p in row['parent_flights']],[1,2,3])

    def test_straight_ray_handles_both_sides_and_unknown_central_region(self):
        self.assertEqual(features.ray_to_cms_entry([1.,2.,100.],[1.,0.,-2.],10.)[0],[46.,2.,10.])
        self.assertEqual(features.ray_to_cms_entry([-1.,2.,-100.],[-1.,0.,2.],10.)[0],[-46.,2.,-10.])
        self.assertIsNone(features.ray_to_cms_entry([0.,0.,5.],[0.,0.,-1.],10.)[0])
        self.assertIsNone(features.ray_to_cms_entry([0.,0.,100.],[0.,0.,1.],10.)[0])

    def test_native_weight_and_graph_identity_are_sensitive_to_changed_decay(self):
        rows=[particle(1,211),particle(2,13,[1],status=1)]
        first=features.event_features(rows,[1,1,1],3.,[3.,4.])
        rows[0]['end_cm'][2]=80.
        second=features.event_features(rows,[1,1,1],3.,[3.,4.])
        self.assertNotEqual(first['graph_sha256'],second['graph_sha256'])
        self.assertEqual(second['hepmc_weights'],[3.,4.])
        self.assertEqual(second['native_weight'],3.)

    def test_fractional_or_bool_event_identity_is_rejected(self):
        for event_id in ([1.,1,1],[True,1,1],[1,1,1.5]):
            with self.assertRaises(ValueError):
                features.event_features([],event_id,1.,[1.])

    def test_diquark_is_not_a_transport_hadron(self):
        self.assertFalse(features.hadron(2101))
        for value in (130,310,211,321,511,5122):
            self.assertTrue(features.hadron(value))

    def test_remnant_diquarks_do_not_make_every_muon_event_unknown(self):
        for pdg in (1103, 2101, -2103, 2203, 3101):
            for status in (63, 71):
                rows=[particle(1,pdg,status=status),particle(2,211,[1]),
                      particle(3,13,[2],status=1,end=None)]
                row=features.event_features(rows,[1,1,1],3.,[3.])
                self.assertEqual(row['unknown_transport_ancestor_barcodes'],[])
                self.assertEqual(row['muons'][0]['ancestor_barcodes'],[1,2])
                self.assertEqual(row['muons'][0]['transport_parent_barcodes'],[2])
                self.assertEqual(row['native_weight'],3.)

    def test_unclassified_BSM_and_nonbookkeeping_status_remain_unknown(self):
        for pdg,status in ((1000021,2),(9900015,63),(2101,1),(2101,2)):
            rows=[particle(1,pdg,status=status),particle(2,13,[1],status=1,end=None)]
            row=features.event_features(rows,[1,1,1],1.,[1.])
            self.assertEqual(row['unknown_transport_ancestor_barcodes'],[1])


if __name__ == '__main__':
    unittest.main()
