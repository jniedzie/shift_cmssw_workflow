# Code style: AI-code. AI-generated full-HepMC feature and control-support tests.
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import Mock, patch

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

    def test_slow_kill_reaping_keeps_timeout_unknown_and_rejects_live_child(self):
        rows=[particle(1,211),particle(2,13,[1],status=1,end=None)]
        child=Mock(exitcode=-9)
        child.is_alive.side_effect=[True,True,False]
        context=Mock()
        context.Process.return_value=child
        with patch.object(features.multiprocessing,'get_context',return_value=context):
            result=features.bounded_event_features(rows,[1,1,1],2.,[2.],object(),{},.02)
        child.kill.assert_called_once()
        self.assertEqual(child.join.call_args_list[-1].args,(30,))
        self.assertTrue(result['material_navigation_timeout'])
        self.assertEqual(result['native_weight'],2.)
        self.assertIsNone(result['parent_flights'][0]['material']['interaction_depth_proxy'])
        child.is_alive.side_effect=None
        child.is_alive.return_value=True
        with patch.object(features.multiprocessing,'get_context',return_value=context):
            with self.assertRaisesRegex(RuntimeError,'could not be stopped'):
                features.bounded_event_features(rows,[1,1,1],2.,[2.],object(),{},.02)

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
            for status in (63, 71, 72, 73):
                rows=[particle(1,pdg,status=status),particle(2,211,[1]),
                      particle(3,13,[2],status=1,end=None)]
                row=features.event_features(rows,[1,1,1],3.,[3.])
                self.assertEqual(row['unknown_transport_ancestor_barcodes'],[])
                self.assertEqual(row['muons'][0]['ancestor_barcodes'],[1,2])
                self.assertEqual(row['muons'][0]['transport_parent_barcodes'],[2])
                self.assertEqual(row['native_weight'],3.)

    def test_unclassified_BSM_and_nonbookkeeping_status_remain_unknown(self):
        for pdg,status in ((1000021,2),(9900015,63),(9900015,72),(9900015,73),(2101,1),(2101,2)):
            rows=[particle(1,pdg,status=status),particle(2,13,[1],status=1,end=None)]
            row=features.event_features(rows,[1,1,1],1.,[1.])
            self.assertEqual(row['unknown_transport_ancestor_barcodes'],[1])

    def test_catalogued_octet_onia_are_generator_states_only_in_allowed_statuses(self):
        self.assertEqual(len(features.PYTHIA8317_OCTET_ONIA_IDS), 24)
        for pdg in features.PYTHIA8317_OCTET_ONIA_IDS:
            for status in (23, 62):
                self.assertTrue(features.generator_bookkeeping_state(pdg, status))
            for status in (1, 2, 4, 22, 33, 51, 63, 71):
                self.assertFalse(features.generator_bookkeeping_state(pdg, status))
        for pdg in (-9940103, 9940000, 9949999, 9959999, 9900015, 1000021):
            for status in (23, 62):
                self.assertFalse(features.generator_bookkeeping_state(pdg, status))

    def test_octet_history_keeps_physical_parents_weights_and_graph(self):
        rows = [particle(1, 9940103, status=23), particle(2, 9940103, [1], status=62),
                particle(3, 100443, [2]), particle(4, 443, [3]),
                particle(5, 13, [4], status=1, end=None)]
        result = features.event_features(rows, [1, 1, 1], -.125, [-.125])
        self.assertEqual(result['unknown_transport_ancestor_barcodes'], [])
        self.assertEqual(result['muons'][0]['ancestor_barcodes'], [1, 2, 3, 4])
        self.assertEqual(result['muons'][0]['transport_parent_barcodes'], [3, 4])
        self.assertEqual([row['pdg'] for row in result['parent_flights']], [100443, 443])
        self.assertEqual(result['native_weight'], -.125)
        self.assertEqual(result['hepmc_weights'], [-.125])


class QCDGeometryFirstTests(unittest.TestCase):
    def geometry_event(self):
        from test_shift_qcd_gen_filter import event
        value=event(1)
        for muon in value['muons']:
            muon['status']=1
            muon['material_to_CMS_entry']=dict(complete=False,
                full_saved_chord_coverage=False,column_density_g_cm2=None)
        return value

    def test_geometry_gate_matches_filter_for_twelve_known_and_unknown_cases(self):
        from shift_qcd_gen_filter import default_policy,filter_event
        import shift_qcd_gen_filter as qcd_filter
        import shift_sm_response_features as response
        policy=default_policy()
        cases=[('plausible_unknown_material',{},None,True),
               ('zero_pz',dict(momentum_GeV=[10.,0.,0.]),None,False),
               ('away',dict(momentum_GeV=[0.,0.,10.]),None,False),
               ('central_one_sided',dict(birth_cm=[0.,0.,0.]),None,False),
               ('large_miss',dict(momentum_GeV=[10.,0.,-1.]),None,False),
               ('missing_birth',dict(birth_cm=None),0,True),
               ('missing_momentum',dict(momentum_GeV=None),0,True),
               ('both_missing',dict(birth_cm=None,momentum_GeV=None),None,True),
               ('one_known_bad',dict(momentum_GeV=[10.,0.,0.]),0,False),
               ('unknown_parent',dict(transport_parent_barcodes=[999],ancestor_barcodes=[999]),0,True)]
        for name,update,index,expected in cases:
            with self.subTest(case=name):
                value=self.geometry_event()
                for i,muon in enumerate(value['muons']):
                    if index is None or i==index:muon.update(update)
                relevant=features.qcd_relevant_muons(value['muons'],policy)
                decision=filter_event(value,policy)
                self.assertEqual(bool(relevant),expected)
                self.assertEqual(decision['passes_filter'],expected)
                self.assertEqual(decision['protected_unknown'],expected)
        for projection in (None,dict(two_station_miss=None)):
            with self.subTest(projection=projection),patch.object(response,'_station_misses',return_value=projection),patch.object(qcd_filter,'_station_misses',return_value=projection):
                value=self.geometry_event()
                self.assertFalse(features.qcd_relevant_muons(value['muons'],policy))
                self.assertFalse(filter_event(value,policy)['passes_filter'])

    def timeout_rows(self):
        return [particle(10,211,birth=(0.,0.,14805.),end=(0.,0.,14800.)),
                particle(1,13,[10],status=1,birth=(0.,0.,14800.),end=None),
                particle(2,-13,[10],status=1,birth=(0.,0.,14800.),end=None)]

    def test_six_timeout_cases_keep_the_graph_weights_and_unknown_protection(self):
        from shift_qcd_gen_filter import default_policy,filter_event
        policy=default_policy()
        for case in ('eligible','missing_vertex','unknown_model','prompt','irrelevant_parent','known_away'):
            with self.subTest(case=case):
                rows=self.timeout_rows()
                if case=='missing_vertex':rows[1]['birth_cm']=None
                if case=='unknown_model':rows[0]['pdg']=9000001
                if case=='prompt':
                    rows=rows[1:]
                    for row in rows:row['parents']=[]
                if case=='irrelevant_parent':
                    rows.extend([particle(20,321,birth=(0.,0.,14805.),end=(0.,0.,14800.)),
                        particle(3,13,[20],status=1,birth=(0.,0.,14800.),end=None)])
                    rows[-1]['momentum_GeV']=[0.,0.,10.]
                if case=='known_away':
                    for row in rows[1:]:row['momentum_GeV']=[0.,0.,10.]
                results=[]
                for selected_policy in (None,policy):
                    with patch.object(features,'_score_event_to_file',side_effect=lambda *args:time.sleep(1.)):
                        result=features.bounded_event_features(rows,[99,1,1],2.,[2.],
                            object(),dict(minimum_abs_z_cm=1100.),.02,selected_policy)
                    self.assertEqual(result['native_weight'],2.)
                    self.assertEqual(result['GEN_muons'],len(rows)-sum(abs(r['pdg'])!=13 for r in rows))
                    self.assertEqual(bool(result.get('material_navigation_timeout')),
                        selected_policy is None or case!='known_away')
                    for muon in result['muons']:
                        material=muon['material_to_CMS_entry']
                        self.assertFalse(material['complete'])
                        self.assertFalse(material['full_saved_chord_coverage'])
                        self.assertIsNone(material['column_density_g_cm2'])
                    results.append(result)
                self.assertEqual(results[0]['graph_sha256'],results[1]['graph_sha256'])
                self.assertEqual(filter_event(results[0],policy),filter_event(results[1],policy))
                self.assertEqual(filter_event(results[1],policy)['passes_filter'],case!='known_away')

    def test_both_modes_keep_child_failure_and_invalid_timeout_guards(self):
        from shift_qcd_gen_filter import default_policy
        for selected_policy in (None,default_policy()):
            with self.subTest(policy=selected_policy):
                child=Mock(exitcode=1)
                child.is_alive.return_value=False
                context=Mock();context.Process.return_value=child
                with patch.object(features.multiprocessing,'get_context',return_value=context):
                    with self.assertRaisesRegex(RuntimeError,'child failed'):
                        features.bounded_event_features(self.timeout_rows(),[99,1,1],2.,[2.],
                            object(),dict(minimum_abs_z_cm=1100.),1.,selected_policy)
                for invalid in (0.,-1.,float('nan'),float('inf')):
                    with self.assertRaises(ValueError):
                        features.bounded_event_features(self.timeout_rows(),[99,1,1],2.,[2.],
                            None,dict(minimum_abs_z_cm=1100.),invalid,selected_policy)


if __name__ == '__main__':
    unittest.main()
