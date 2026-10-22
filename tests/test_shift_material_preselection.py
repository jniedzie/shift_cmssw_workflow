# Code style: AI-code. AI-generated sampling accounting and material-priority tests.
import copy
import importlib.util
import json
import math
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location('admission', Path(__file__).parents[1]/'scripts/shift_material_preselection.py')
admission = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(admission)


def policy():
    return dict(schema='shift-material-admission-policy-v1', cohort_id='new-study-v1',
                selection_domain='new-material-gate-v1', selection_seed='frozen-42',
                fold_seed='original-collisions-19', control_probability=.1,
                unknown_probability=1., momentum_scale_GeV=20., training_folds=5)


def event(number=1, depth=0., native=1.):
    return dict(source_file_sha256='b'*64, original_source_sha256='a'*64,
                source_id='hard-source-1',event_id=[1,1,number], graph_sha256='c'*64,
                native_weight=native,
                parent_flights=[dict(barcode=9, material=dict(full_saved_chord_coverage=True,
                                                             interaction_depth_proxy=depth))],
                muons=[dict(barcode=i, pdg=13, p_GeV=40., transport_parent_barcodes=[9],
                            straight_line_towards_CMS_entry=True,
                            material_to_CMS_entry=dict(complete=True)) for i in (10,11)])


def inputs(rows=None):
    rows = rows if rows is not None else [event()]
    features=dict(schema='shift-gen-material-features-v1',events=rows)
    source=dict(source_id='hard-source-1', original_source_sha256='a'*64,
                feature_source_sha256s=['b'*64], full_gen_exposure=dict(attempted=2000,accepted=1000))
    authorities=[]
    for row in rows:
        authorities.append(dict(source_id=source['source_id'],original_source_sha256='a'*64,
                                feature_source_sha256=row['source_file_sha256'],
                                event_id=row['event_id'],graph_sha256=row['graph_sha256'],
                                native_weight=row['native_weight'], proposal_draw_id='draw-0',
                                sampling_probability=1.,previous_selection_domains=[],
                                proposal_kind='ordinary',proposal_density_ratio=1.,proposal_correction=1.,
                                proposal_provenance_sha256='d'*64))
    return features,dict(schema='shift-material-admission-authority-v1',sources=[source],events=authorities,
                         population_scope='conditional_diagnostic')


def pointing():
    return dict(schema='shift-two-endcap-pointing-v1',z_planes_cm=[-100.,100.],
                radial_scale_cm=10.,geometry_reference='synthetic two-plane geometry',
                geometry_sha256='f'*64)


def column_policy():
    return dict(schema='shift-muon-column-priority-v1',
                mass_stopping_power_GeV_cm2_g=.002,reference='synthetic mass stopping-power scale')


def chamber_pointing():
    config = pointing()
    config.update(schema='shift-two-endcap-pointing-v2', station_edge_scale_cm=1.,
                  station_envelopes=[dict(station=1, z_abs_cm=80.,
                                          radial_min_cm=5., radial_max_cm=9.)])
    return config


class MaterialAdmissionTests(unittest.TestCase):
    def test_chamber_pointing_downgrades_beam_hole_and_far_side_miss(self):
        config = chamber_pointing()
        covered = dict(birth_cm=[7., 0., -200.], momentum_GeV=[0., 0., 40.])
        hole = dict(birth_cm=[0., 0., -200.], momentum_GeV=[0., 0., 40.])
        one_side = dict(birth_cm=[0., 0., -200.], momentum_GeV=[1.6, 0., 40.])
        good = admission.two_endcap_pointing([covered], config)
        self.assertEqual(good['station_pointing'][0]['priority'], 1.)
        self.assertLess(admission.two_endcap_pointing([hole], config)['priority'],
                        good['priority']/100.)
        miss = admission.two_endcap_pointing([one_side], config)
        stations = miss['station_pointing'][0]['projected_stations']
        self.assertAlmostEqual(stations[0][0]['radius_cm'], 4.8)
        self.assertAlmostEqual(stations[1][0]['radius_cm'], 11.2)
        self.assertLess(miss['priority'], good['priority']/10.)

    def test_chamber_edge_is_soft_and_control_weights_remain_exact(self):
        rows = [event(1), event(2)]
        for row, radius in zip(rows, (7., 0.)):
            row['muons'][1]['pdg'] = -13
            for muon in row['muons']:
                muon.update(birth_cm=[radius, 0., -200.], momentum_GeV=[0., 0., 40.])
        features, authority = inputs(rows)
        config = policy()
        config.update(two_endcap_pointing=chamber_pointing(), require_opposite_sign=True)
        good, hole = admission.prepare_selection(features, authority, config)['events']
        self.assertGreater(good['conditional_sampling_probability'], hole['conditional_sampling_probability'])
        self.assertGreaterEqual(hole['conditional_sampling_probability'], .1)
        for row in (good, hole):
            p = row['sampling_probability']
            self.assertEqual(p*row['proposal_gen_weight'], row['native_weight'])
        # The edge does not become an acceptance cut.
        inside = dict(birth_cm=[4.999, 0., -200.], momentum_GeV=[0., 0., 40.])
        edge = admission.two_endcap_pointing([inside], chamber_pointing())
        self.assertGreater(edge['station_pointing'][0]['priority'], .99)

    def test_missing_chamber_truth_is_unknown_and_direction_has_no_pt_cut(self):
        muon = dict(birth_cm=[7., 0., -200.], momentum_GeV=[0., 0., 40.], pt_GeV=0.)
        first = admission.two_endcap_pointing([muon], chamber_pointing())
        muon.update(momentum_GeV=[0., 0., 400.], pt_GeV=1.e9)
        self.assertEqual(admission.two_endcap_pointing([muon], chamber_pointing()), first)
        muon['birth_cm'] = None
        missing = admission.two_endcap_pointing([muon], chamber_pointing())
        self.assertTrue(missing['unknown'])
        self.assertIsNone(missing['priority'])
        self.assertEqual(missing['station_pointing'], [None])

    def test_chamber_geometry_must_be_ordered_inside_reference_planes(self):
        for field, value in (('z_abs_cm', 110.), ('radial_min_cm', 9.),
                             ('radial_max_cm', math.inf), ('station', True)):
            config = chamber_pointing()
            config['station_envelopes'][0][field] = value
            with self.assertRaises(ValueError):
                admission.validate_pointing(config)
        config = chamber_pointing()
        config['station_envelopes'] *= 2
        with self.assertRaisesRegex(ValueError, 'distinct'):
            admission.validate_pointing(config)

    def test_muon_column_distinguishes_nearby_soft_muon_from_long_rock_path(self):
        muon=dict(p_GeV=1.,pt_GeV=0.,material_to_CMS_entry=dict(complete=True,column_density_g_cm2=0.))
        self.assertEqual(admission.muon_column_priority([muon],column_policy())['priority'],1.)
        muon['material_to_CMS_entry']['column_density_g_cm2']=1000.
        soft=admission.muon_column_priority([muon],column_policy())
        self.assertAlmostEqual(soft['priority'],1./17.)
        muon['pt_GeV']=1.e9
        self.assertEqual(admission.muon_column_priority([muon],column_policy()),soft)
        muon['p_GeV']=100.
        self.assertGreater(admission.muon_column_priority([muon],column_policy())['priority'],.999)

    def test_unknown_muon_column_preserves_unknown_protection_and_weights(self):
        row=event();row['muons'][1]['pdg']=-13
        for m in row['muons']:
            m['material_to_CMS_entry'].update(complete=False,column_density_g_cm2=1.e6)
        f,a=inputs([row]);p=policy();p['muon_column_priority']=column_policy()
        result=admission.prepare_selection(f,a,p)['events'][0]
        self.assertTrue(result['material_priority']['unknown_potential_pair'])
        self.assertEqual(result['conditional_sampling_probability'],1.)
        self.assertEqual(result['proposal_gen_weight'],result['native_weight'])

    def test_known_muon_column_downgrades_admission_with_exact_inverse_probability(self):
        row=event();row['muons'][1]['pdg']=-13
        for m in row['muons']:
            m['material_to_CMS_entry'].update(column_density_g_cm2=1.e6)
        f,a=inputs([row]);p=policy();p['muon_column_priority']=column_policy()
        result=admission.prepare_selection(f,a,p)['events'][0]
        self.assertGreaterEqual(result['conditional_sampling_probability'],.1)
        self.assertLess(result['conditional_sampling_probability'],.101)
        self.assertEqual(result['proposal_gen_weight'],result['native_weight']/result['sampling_probability'])

    def test_invalid_column_scale_or_material_is_rejected(self):
        for field,value in (('mass_stopping_power_GeV_cm2_g',0.),('reference','')):
            p=policy();p['muon_column_priority']=column_policy();p['muon_column_priority'][field]=value
            with self.assertRaises(ValueError):admission.validate_policy(p)
        muon=dict(p_GeV=1.,material_to_CMS_entry=dict(complete=True,column_density_g_cm2=-1.))
        with self.assertRaisesRegex(ValueError,'nonnegative'):
            admission.muon_column_priority([muon],column_policy())

    def test_pointing_checks_far_endcap_and_keeps_missing_paths_unknown(self):
        muon=dict(birth_cm=[0.,0.,-200.],momentum_GeV=[1.,0.,20.])
        result=admission.two_endcap_pointing([muon],pointing())
        self.assertEqual(result['projected_radii_cm'],[[5.,15.]])
        self.assertAlmostEqual(result['priority'],1./(1.+1.5**4))
        self.assertTrue(result['both_planes_forward'])
        muon['birth_cm']=None
        result=admission.two_endcap_pointing([muon],pointing())
        self.assertIsNone(result['priority'])
        self.assertTrue(result['unknown'])
        self.assertIsNone(result['both_planes_forward'])

    def test_geometry_uses_direction_not_muon_pt_or_momentum_scale(self):
        muon=dict(birth_cm=[0.,0.,-200.],momentum_GeV=[1.,2.,20.],pt_GeV=0.)
        first=admission.two_endcap_pointing([muon],pointing())
        muon.update(momentum_GeV=[10.,20.,200.],pt_GeV=1.e6)
        self.assertEqual(admission.two_endcap_pointing([muon],pointing()),first)

    def test_pointing_downgrades_wide_pairs_without_removing_control_or_changing_weight(self):
        near=event(1);far=event(2)
        for row,radius in ((near,1.),(far,1000.)):
            row['muons'][1]['pdg']=-13
            for m in row['muons']:
                m.update(birth_cm=[radius,0.,-200.],momentum_GeV=[0.,0.,40.])
        f,a=inputs([near,far]);p=policy();p.update(two_endcap_pointing=pointing(),require_opposite_sign=True)
        close,wide=admission.prepare_selection(f,a,p)['events']
        self.assertGreater(close['conditional_sampling_probability'],wide['conditional_sampling_probability'])
        self.assertGreaterEqual(wide['conditional_sampling_probability'],.1)
        for row in (close,wide):
            self.assertEqual(row['proposal_gen_weight'],row['native_weight']/row['sampling_probability'])
        # Old cohorts have no added field or changed ranking.
        self.assertNotIn('two_endcap_pointing',admission.pair_priority(near,20.))

    def test_missing_pointing_is_protected_by_unknown_probability(self):
        row=event();row['muons'][1]['pdg']=-13
        for muon in row['muons']:
            muon.update(birth_cm=None,momentum_GeV=[0.,0.,40.])
        f,a=inputs([row]);p=policy();p.update(two_endcap_pointing=pointing(),require_opposite_sign=True)
        result=admission.prepare_selection(f,a,p)['events'][0]
        self.assertTrue(result['material_priority']['unknown_potential_pair'])
        self.assertEqual(result['conditional_sampling_probability'],1.)

    def test_backward_or_central_birth_ray_remains_a_positive_control(self):
        for birth,pz in ((-200.,-40.),(0.,40.),(-200.,0.)):
            row=event();row['muons'][1]['pdg']=-13
            for muon in row['muons']:
                muon.update(birth_cm=[1.,0.,birth],momentum_GeV=[0.,0.,pz])
            f,a=inputs([row]);p=policy();p['two_endcap_pointing']=pointing()
            result=admission.prepare_selection(f,a,p)['events'][0]
            self.assertEqual(result['conditional_sampling_probability'],admission.grid_probability(.1))

    def test_pointing_geometry_reference_and_coordinates_are_checked(self):
        for field,value in (('radial_scale_cm',0.),('z_planes_cm',[100.,200.]),
                            ('geometry_reference',''),('geometry_sha256','invalid')):
            p=policy();p['two_endcap_pointing']=pointing();p['two_endcap_pointing'][field]=value
            with self.assertRaises(ValueError):admission.validate_policy(p)
        muon=dict(birth_cm=[0.,0.,-200.],momentum_GeV=[math.nan,0.,40.])
        with self.assertRaisesRegex(ValueError,'finite CMS'):
            admission.two_endcap_pointing([muon],pointing())

    def test_known_both_both_control_keeps_its_pointing_priority(self):
        # Source42300000:1:556, exact saved GEN vectors from the SHA-bound trace.
        muons=[dict(birth_cm=[.04489147020913916,-.017580011158852406,14833.158994355563],
                    momentum_GeV=p) for p in
               ([-.14517937067652786,-.1869471164084101,-26.79979572094639],
                [-.5747406573584914,-1.3301671672165933,-119.69973217285677])]
        config=pointing();config.update(z_planes_cm=[-1100.,1100.],radial_scale_cm=702.)
        result=admission.two_endcap_pointing(muons,config)
        self.assertGreater(result['priority'],.99)
        self.assertTrue(result['both_planes_forward'])
        config = json.loads((Path(__file__).parents[1]/'config/campaigns/sm_material_admission_pilot_v1.json').read_text())['policy']['two_endcap_pointing']
        self.assertGreater(admission.two_endcap_pointing(muons, config)['priority'], .99)
        # Independent raw truth-matched traversing legs in510211002:1:273.
        # Both have CSC hits in both endcaps; retain this old beam-pipe control.
        energetic = [dict(birth_cm=[.010250628067725363, .04995958265231276, 14787.15972547555],
                          momentum_GeV=[-8.259437873976298, 17.494227317442924, -702.7894097505847]),
                     dict(birth_cm=[.34038375595791215, -.5520615883988315, 14734.048947833802],
                          momentum_GeV=[6.375422746063677, -14.79802442959545, -1438.4355791438506])]
        ranked = admission.two_endcap_pointing(energetic, config)
        self.assertGreater(ranked['priority'], .8)
        self.assertEqual([row['priority'] for row in ranked['station_pointing']], [1., 1.])

    def test_depth_orders_but_shared_parent_is_counted_once(self):
        zero=admission.pair_priority(event(depth=0),20.)['priority']
        one=admission.pair_priority(event(depth=1),20.)['priority']
        self.assertAlmostEqual(one/zero,math.exp(-1))
        self.assertEqual(admission.pair_priority(event(),20.)['preferred_pair_barcodes'],[10,11])

    def test_same_charge_pairs_are_kept(self):
        f,a=inputs()
        row=admission.prepare_selection(f,a,policy())['events'][0]
        self.assertGreater(row['conditional_sampling_probability'],.1)

    def test_new_opposite_sign_priority_keeps_other_events_as_controls(self):
        rows=[event(1),event(2)]
        rows[1]['muons'][1]['pdg']=-13
        f,a=inputs(rows);p=policy();p['require_opposite_sign']=True
        first,second=admission.prepare_selection(f,a,p)['events']
        self.assertEqual(first['material_priority']['priority'],0.)
        self.assertEqual(first['conditional_sampling_probability'],admission.grid_probability(.1))
        self.assertGreater(second['material_priority']['priority'],0.)
        self.assertGreater(second['conditional_sampling_probability'],first['conditional_sampling_probability'])

    def test_opposite_sign_priority_uses_total_momentum_without_pt_cut(self):
        row=event();row['muons'][1]['pdg']=-13
        first=admission.pair_priority(row,20.,True)['priority']
        for muon in row['muons']:muon['pt_GeV']=0.
        self.assertEqual(admission.pair_priority(row,20.,True)['priority'],first)
        for muon in row['muons']:muon['p_GeV']=80.
        self.assertGreater(admission.pair_priority(row,20.,True)['priority'],first)
        f,a=inputs();p=policy();p['require_opposite_sign']='true'
        with self.assertRaisesRegex(ValueError,'Boolean'):admission.prepare_selection(f,a,p)

    def test_partial_parent_path_keeps_known_prefix_and_explicit_uncertainty(self):
        row=event(depth=10.);row['muons'][1]['pdg']=-13
        row['parent_flights'][0]['material']['full_saved_chord_coverage']=False
        old=admission.pair_priority(row,20.,True)
        new=admission.pair_priority(row,20.,True,True)
        self.assertEqual(old['priority'],0.)
        self.assertTrue(new['unknown_potential_pair'])
        self.assertTrue(new['ranked_with_incomplete_paths'])
        self.assertAlmostEqual(new['priority'],math.exp(-10.)*.8**2)
        f,a=inputs([row]);p=policy();p.update(require_opposite_sign=True,
            rank_partial_parent_paths=True,unknown_probability=.1)
        result=admission.prepare_selection(f,a,p)['events'][0]
        self.assertGreater(result['conditional_sampling_probability'],.1)
        self.assertLess(result['conditional_sampling_probability'],.101)

    def test_unknown_prefix_is_not_assumed_to_absorb_or_remove_muons(self):
        row=event();row['muons'][1]['pdg']=-13
        row['parent_flights'][0]['material'].update(full_saved_chord_coverage=False,
                                                   interaction_depth_proxy=None)
        for muon in row['muons']:
            muon['material_to_CMS_entry']['column_density_g_cm2']=1.e9
            muon['pt_GeV']=0.
        result=admission.pair_priority(row,20.,True,True)
        self.assertGreater(result['priority'],0.)
        self.assertTrue(result['unknown_potential_pair'])

    def test_zero_muon_and_outgoing_events_keep_control_probability(self):
        rows=[event(1),event(2)]
        rows[0]['muons']=[]
        for muon in rows[1]['muons']:
            muon['straight_line_towards_CMS_entry']=False
        f,a=inputs(rows)
        output=admission.prepare_selection(f,a,policy())
        self.assertEqual([x['conditional_sampling_probability'] for x in output['events']],
                         [admission.grid_probability(.1)]*2)
        self.assertEqual(output['sources'][0]['full_gen_exposure'],dict(attempted=2000,accepted=1000))

    def test_unknown_potential_pair_protected_instead_of_treated_as_vacuum(self):
        row=event()
        row['parent_flights'][0]['material']['full_saved_chord_coverage']=False
        f,a=inputs([row])
        result=admission.prepare_selection(f,a,policy())['events'][0]
        self.assertEqual(result['conditional_sampling_probability'],1.)
        self.assertTrue(result['material_priority']['unknown_potential_pair'])

    def test_exact_weight_has_both_independent_selections_and_decay_correction_once(self):
        f,a=inputs([event(native=-3.)])
        old=a['events'][0]
        old.update(sampling_probability=.25,previous_selection_domains=['older-gate'],
                   proposal_kind='importance',proposal_correction=2.,proposal_density_ratio=.5)
        row=admission.prepare_selection(f,a,policy())['events'][0]
        p=row['conditional_sampling_probability']
        self.assertAlmostEqual(row['sampling_probability'],.25*p)
        self.assertAlmostEqual(row['proposal_gen_weight'],-3.*2./(.25*p))
        # Sum over the two possible admissions: E[I W/(p_old p_new) f/q].
        self.assertAlmostEqual(p*row['proposal_gen_weight']+(1.-p)*0.,-3.*2./.25)
        self.assertAlmostEqual(row['proposal_gen_weight']/row['sampling_gen_weight'],2.)

    def test_control_protects_full_pre_admission_weight(self):
        rows=[event(i) for i in range(1,11)]
        for row in rows:
            row['muons']=[]
        f,a=inputs(rows)
        a['events'][0].update(proposal_kind='importance',proposal_correction=10.,proposal_density_ratio=.1)
        result=admission.prepare_selection(f,a,policy())['events']
        self.assertAlmostEqual(result[0]['conditional_sampling_probability'],1./1.9)
        self.assertEqual(result[1]['conditional_sampling_probability'],admission.grid_probability(.1))

    def test_mu_enriched_source_keeps_saved_decays_and_filter_exposure(self):
        f,a=inputs()
        source=a['sources'][0]
        source.update(generator_profile='shift-qcd-mu-enriched-cylinder-v1',
                      full_source_trials=123456, no_second_filter_efficiency=True)
        a['events'][0]['saved_decays_unchanged']=True
        result=admission.prepare_selection(f,a,policy())
        row=result['events'][0]
        self.assertEqual(row['proposal_gen_weight'],row['native_weight']/row['sampling_probability'])
        self.assertEqual(result['sources'][0]['full_source_trials'],123456)
        self.assertTrue(result['sources'][0]['no_second_filter_efficiency'])

    def test_mu_enriched_source_cannot_silently_redraw_lifetimes(self):
        for declaration in ({'generator_profile':'shift-qcd-mu-enriched-cylinder-v1'},
                            {'decay_dependent_filter':True}):
            f,a=inputs();a['sources'][0].update(declaration)
            with self.assertRaisesRegex(ValueError,'unchanged saved decays'):
                admission.prepare_selection(f,a,policy())
            a['events'][0].update(saved_decays_unchanged=True, proposal_kind='importance',
                                   proposal_correction=2.,proposal_density_ratio=.5)
            with self.assertRaisesRegex(ValueError,'unchanged saved decays'):
                admission.prepare_selection(f,a,policy())

    def test_invalid_decay_filter_declaration_fails(self):
        f,a=inputs();a['sources'][0]['decay_dependent_filter']='false'
        with self.assertRaisesRegex(ValueError,'Boolean'):
            admission.prepare_selection(f,a,policy())

    def test_replay_order_and_fold_identity_independent_of_proposal_draw(self):
        f,a=inputs([event(1),event(2)])
        first=admission.prepare_selection(f,a,policy())
        f['events'].reverse();a['events'].reverse()
        for row in a['events']:
            row['proposal_draw_id']='other-single-draw'
        second=admission.prepare_selection(f,a,policy())
        by_id=lambda result:{tuple(x['event_id']):(x['admission_draw'],x['training_fold']) for x in result['events']}
        self.assertEqual(by_id(first),by_id(second))
        other=policy();other['selection_seed']='fresh-43'
        third=admission.prepare_selection(f,a,other)
        self.assertNotEqual(second['events'][0]['admission_draw'],third['events'][0]['admission_draw'])
        self.assertEqual(second['events'][0]['training_fold'],third['events'][0]['training_fold'])

    def test_sibling_draws_cannot_double_count_source_exposure(self):
        f,a=inputs()
        sibling=copy.deepcopy(f['events'][0]);sibling['source_file_sha256']='e'*64
        f['events'].append(sibling)
        a['sources'][0]['feature_source_sha256s'].append('e'*64)
        row=copy.deepcopy(a['events'][0]);row.update(feature_source_sha256='e'*64,proposal_draw_id='draw-1')
        a['events'].append(row)
        with self.assertRaisesRegex(ValueError,'one proposal draw'):
            admission.prepare_selection(f,a,policy())

    def test_wrong_graph_source_and_missing_authority_fail(self):
        for field,value in [('graph_sha256','e'*64),('original_source_sha256','e'*64),('source_id','different')]:
            f,a=inputs();a['events'][0][field]=value
            with self.assertRaises(ValueError):
                admission.prepare_selection(f,a,policy())
        f,a=inputs();a['events']=[]
        with self.assertRaises(ValueError):
            admission.prepare_selection(f,a,policy())

    def test_reused_random_domain_invalid_density_and_implicit_ordinary_fail(self):
        for change in (dict(sampling_probability=.5,previous_selection_domains=[]),
                       dict(previous_selection_domains=['new-material-gate-v1']),
                       dict(proposal_correction=2.,proposal_density_ratio=1.),
                       dict(proposal_kind='ordinary',proposal_correction=2.,proposal_density_ratio=.5),
                       dict(native_weight=float('nan'))):
            f,a=inputs();a['events'][0].update(change)
            with self.assertRaises(ValueError):
                admission.prepare_selection(f,a,policy())

    def test_invalid_policy_cannot_remove_controls(self):
        for change in (dict(control_probability=0.),dict(control_probability=1.1),
                       dict(momentum_scale_GeV=0.),dict(unknown_probability=.01)):
            f,a=inputs();p=policy();p.update(change)
            with self.assertRaises(ValueError):
                admission.prepare_selection(f,a,p)

    def test_tiny_control_uses_the_actual_hash_grid_probability(self):
        self.assertEqual(admission.grid_probability(1.e-100),2.**-53)
        self.assertEqual(admission.grid_probability(1.),1.)
        for value in (.1,.3,.8):
            actual=admission.grid_probability(value)
            self.assertGreaterEqual(actual,value)
            self.assertEqual(actual*(1 << 53),int(actual*(1 << 53)))

    def test_probability_and_fold_hash_namespaces_are_distinct(self):
        f,a=inputs();p=policy()
        p.update(selection_domain='training-fold',selection_seed=p['fold_seed'])
        row=admission.prepare_selection(f,a,p)['events'][0]
        same_key=['training-fold',p['fold_seed'],['a'*64,[1,1,1]]]
        self.assertNotEqual(row['admission_draw'],admission.uniform(same_key))

    def test_underflow_fails_and_old_weight_is_divided_before_decay_multiplication(self):
        f,a=inputs([event(native=1.e-300)])
        a['events'][0].update(sampling_probability=1.e-300,previous_selection_domains=['old'],
                             proposal_kind='importance',proposal_correction=1.e-300,
                             proposal_density_ratio=1.e300)
        self.assertGreater(admission.prepare_selection(f,a,policy())['events'][0]['proposal_gen_weight'],0.)
        a['events'][0].update(sampling_probability=1.,previous_selection_domains=[],
                             proposal_correction=1.e-100,proposal_density_ratio=1.e100)
        with self.assertRaises(ValueError):
            admission.prepare_selection(f,a,policy())

    def test_old_random_domains_require_explicit_nonempty_list(self):
        for domains in ('old-domain',[''],[None],['old','old']):
            f,a=inputs();a['events'][0]['previous_selection_domains']=domains
            with self.assertRaises(ValueError):
                admission.prepare_selection(f,a,policy())

    def test_conditional_pilots_do_not_claim_physics_normalization(self):
        f,a=inputs();result=admission.prepare_selection(f,a,policy())
        self.assertEqual(result['population_scope'],'conditional_diagnostic')
        self.assertFalse(result['normalization_ready'])
        del a['population_scope']
        with self.assertRaises(KeyError):
            admission.prepare_selection(f,a,policy())

    def test_zero_admitted_source_keeps_its_full_exposure(self):
        f,a=inputs()
        a['sources'].append(dict(source_id='zero-admitted-original-source',
                                 original_source_sha256='f'*64,feature_source_sha256s=[],
                                 full_gen_exposure=dict(attempted=3000,accepted=1000)))
        result=admission.prepare_selection(f,a,policy())
        self.assertEqual(len(result['sources']),2)
        self.assertEqual(result['sources'][1]['full_gen_exposure']['attempted'],3000)

    def test_source_ids_are_unique_even_when_shas_differ(self):
        f,a=inputs()
        duplicate=copy.deepcopy(a['sources'][0]);duplicate['original_source_sha256']='f'*64
        a['sources'].append(duplicate)
        with self.assertRaisesRegex(ValueError,'exactly once'):
            admission.prepare_selection(f,a,policy())

    def test_one_merged_input_can_contain_two_original_sources(self):
        f,a=inputs([event(1),event(2)])
        source=copy.deepcopy(a['sources'][0])
        source.update(source_id='hard-source-2',original_source_sha256='f'*64)
        a['sources'].append(source)
        f['events'][1].update(source_id='hard-source-2',original_source_sha256='f'*64)
        a['events'][1].update(source_id='hard-source-2',original_source_sha256='f'*64)
        result=admission.prepare_selection(f,a,policy())
        self.assertEqual(len(result['events']),2)
        self.assertEqual(len(result['sources']),2)


if __name__ == '__main__':
    unittest.main()
