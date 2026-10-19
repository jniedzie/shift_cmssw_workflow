# Code style: AI-code. AI-generated full-HepMC feature and control-support tests.
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import shift_gen_material_features as features


def particle(barcode, pdg, parents=(), status=2, birth=(0., 0., 100.), end=(0., 0., 90.)):
    return dict(barcode=barcode, pdg=pdg, status=status, parents=list(parents),
                momentum_GeV=[0., 0., -10.], energy_GeV=10.,
                birth_cm=list(birth) if birth is not None else None,
                end_cm=list(end) if end is not None else None)


class GenMaterialFeatureTests(unittest.TestCase):
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


if __name__ == '__main__':
    unittest.main()
