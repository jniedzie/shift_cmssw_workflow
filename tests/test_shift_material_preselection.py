# Code style: AI-code. AI-generated sampling accounting and material-priority tests.
import copy
import importlib.util
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


class MaterialAdmissionTests(unittest.TestCase):
    def test_depth_orders_but_shared_parent_is_counted_once(self):
        zero=admission.pair_priority(event(depth=0),20.)['priority']
        one=admission.pair_priority(event(depth=1),20.)['priority']
        self.assertAlmostEqual(one/zero,math.exp(-1))
        self.assertEqual(admission.pair_priority(event(),20.)['preferred_pair_barcodes'],[10,11])

    def test_same_charge_pairs_are_kept(self):
        f,a=inputs()
        row=admission.prepare_selection(f,a,policy())['events'][0]
        self.assertGreater(row['conditional_sampling_probability'],.1)

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
