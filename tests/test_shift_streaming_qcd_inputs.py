# Code style: AI-code. AI-generated streaming exposure and GEN selection tests.
import copy
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from shift_streaming_qcd_inputs import (DEFAULT_POLICY, closed_normalization, digest,
    discover_sources, feature_stratum, planned_cohort, select_events, sha,
    source_descriptor, validate_policy, write_new)


def plan_for(sources, policy=None):
    policy = copy.deepcopy(DEFAULT_POLICY if policy is None else policy)
    value = dict(schema='shift-streaming-source-plan-v1', cohort_id='fixed-before-RECO',
                 source_slots=[dict(source_id=s['source_id'], request=s['request']) for s in sources],
                 selection_policy=policy, policy_sha256=digest(policy))
    value['plan_sha256'] = digest(value)
    return value


def source(count=12, source_id='production_qcd_0to1_0000', run=42000000, trials=100):
    request = dict(id=source_id, stage='production', trials=count, run=run,
                   stratum='qcd_0to1', bounds=[0, 1], chunk_index=0)
    return dict(source_id=source_id, stratum='qcd_0to1', request=request,
        gen_sha256=digest(source_id), complete_muon_decays=False,
        event_ids=[[run, 1, event+1] for event in range(count)], weights=[1.]*count,
        normalization=dict(schema='shift-stream-source-exposure-v1', source_id=source_id,
            gen_sha256=digest(source_id), stratum='qcd_0to1', sigma_nd_pb=650.,
            full_source_trials=trials, source_saved_events=count, native_saved_sumw=float(count)))


def features(s, labels):
    return dict(schema='shift-stream-GEN-features-v1', complete=True, gen_sha256=s['gen_sha256'],
        source_id=s['source_id'], uses_only_original_stable_GEN_muons=True,
        events=[dict(event_id=identity, native_weight=w, feature_stratum=label)
                for identity, w, label in zip(s['event_ids'], s['weights'], labels)])


def ordinary_fixture(directory, saved=3):
    base = Path(directory)
    request = dict(id='production_qcd_0to1_0000', stage='production', trials=20,
                   run=42000000, stratum='qcd_0to1', bounds=[0, 1], chunk_index=0,
                   output_base='/eos/user/test/qcd/GEN')
    from qcd_mu_enrichment import PROFILE
    manifest = dict(generator_profile=PROFILE, inputs={'source.py':'a'*64}, jobs=[request])
    manifest_path = base/'manifest.json'
    manifest_path.write_text(json.dumps(manifest))
    directory = request['output_base']+'/'+request['id']
    publications = [dict(name=name, path=directory+'/'+name, bytes=100,
                         sha256=('b' if name=='gen.root' else 'c')*64,
                         independent_sha256_readback=True) for name in ('gen.root', 'semantic_audit.json')]
    audit = dict(complete=True, healthy=True, generator_profile=PROFILE, events=saved,
        source_accepted=20, full_source_trial_denominator=100, sigma_nd_mb=65.,
        native_generator_run_info=dict(internal_xsec_pb=65.e9*20/100), sigma_mb=65.*saved/100,
        event_ids=[[42000000, 1, event+1] for event in range(saved)], weights=[1.]*saved,
        no_second_filter_efficiency=True, input_sha256='b'*64,
        filter_records=[dict(total_positive=20, pass_positive=saved, total_negative=0, pass_negative=0)])
    record = dict(schema='shift-mu-enriched-ordinary-gen-receipt-v1', generator_profile=PROFILE,
        complete=True, publication_complete=True, exit_status=0, manifest_sha256=sha(manifest_path),
        request=request, source_inputs=manifest['inputs'], semantic_audit=audit,
        event_count=saved, publication=publications)
    receipt = base/'receipt.json'
    receipt.write_text(json.dumps(record))
    return receipt, manifest_path, record


class StreamingQCDTest(unittest.TestCase):
    def test_planned_prefix_ignores_availability_and_full_campaign_keeps_high_budget(self):
        from qcd_mu_enrichment import PROFILE
        strata=('qcd_0to1','qcd_1to2','qcd_2to5','qcd_5to10','qcd_10to20','qcd_20toinf')
        with tempfile.TemporaryDirectory()as directory:
            base=Path(directory)
            for kind in ('ordinary','weighted'):
                (base/kind/'results').mkdir(parents=True)
                names=strata[:5]if kind=='ordinary'else strata[5:]
                jobs=[dict(id=name+'_'+str(index),stage='production',stratum=name,
                           chunk_index=index)for name in names for index in range(3)]
                (base/kind/'manifest.json').write_text(json.dumps(dict(generator_profile=PROFILE,inputs={},jobs=jobs)))
            gate=dict(generator_profile=PROFILE,manifest_sha256=sha(base/'weighted/manifest.json'),
                      production_plan=dict(qcd_20toinf=dict(chunks=2)),**{'pass':True})
            (base/'weighted/scientific_gate.json').write_text(json.dumps(gate))
            # Later completed source files cannot replace missing planned index0.
            (base/'ordinary/results/qcd_0to1_1.json').write_text('{"complete":true}')
            plan=planned_cohort(base,prefix=1)
            self.assertEqual(len(plan['source_slots']),6)
            self.assertTrue(all(slot['request']['chunk_index']==0 for slot in plan['source_slots']))
            self.assertEqual(len(discover_sources(plan)['pending_source_ids']),6)
            whole=planned_cohort(base,prefix=None)
            self.assertEqual(len(whole['source_slots']),17)
            self.assertEqual(sum(s['stratum']=='qcd_20toinf'for s in whole['source_slots']),2)

    def test_weighted_filtered_source_keeps_full_fixed_trials_and_sparse_ids(self):
        from test_qcd_mu_enriched_receipts import filtered_fixture
        record,_,manifest,row=filtered_fixture()
        row['output_base']='/eos/user/test/weighted/GEN'
        record['request']=copy.deepcopy(row)
        for item in record['publication']:item['path']=row['output_base']+'/'+row['id']+'/'+item['name']
        record['exit_status']=0
        with tempfile.TemporaryDirectory()as directory:
            base=Path(directory);m=base/'manifest.json';m.write_text(json.dumps(manifest))
            record['manifest_sha256']=sha(m);p=base/'receipt.json';p.write_text(json.dumps(record))
            result=source_descriptor(p,m)
            self.assertEqual(result['normalization']['full_source_trials'],4)
            self.assertEqual(result['normalization']['source_saved_events'],1)
            self.assertEqual(result['weights'],[.2])
            self.assertEqual(result['event_ids'][0][-1],1)

    def test_ordinary_internal_trials_are_not_framework_or_saved_denominators(self):
        with tempfile.TemporaryDirectory() as directory:
            receipt, manifest, record = ordinary_fixture(directory)
            descriptor = source_descriptor(receipt, manifest)
            n = descriptor['normalization']
            self.assertEqual((n['full_source_trials'], n['source_saved_events']), (100, 3))
            self.assertEqual(n['sigma_nd_pb'], 65.e9)
            self.assertAlmostEqual(n['sigma_nd_pb']*3/100, record['semantic_audit']['sigma_mb']*1.e9)
            self.assertFalse(descriptor['complete_muon_decays'])

    def test_zero_saved_source_still_carries_all_generator_exposure(self):
        with tempfile.TemporaryDirectory() as directory:
            receipt, manifest, _ = ordinary_fixture(directory, saved=0)
            descriptor = source_descriptor(receipt, manifest)
            self.assertEqual(descriptor['event_ids'], [])
            self.assertEqual(descriptor['normalization']['full_source_trials'], 100)

    def test_validation_missing_native_weight_and_second_filter_efficiency_fail(self):
        for mutation in ('validation', 'duplicate', 'weight', 'double_filter', 'wrong_manifest'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                receipt, manifest, record = ordinary_fixture(directory)
                if mutation=='validation': record['request']['stage']='canary'
                elif mutation=='duplicate': record['semantic_audit']['event_ids'][1]=record['semantic_audit']['event_ids'][0]
                elif mutation=='weight': record['semantic_audit']['weights'][0]=2.
                elif mutation=='double_filter': record['semantic_audit']['sigma_mb']*=3/20
                else: record['manifest_sha256']='d'*64
                receipt.write_text(json.dumps(record))
                with self.assertRaises(ValueError): source_descriptor(receipt, manifest)

    def test_fixed_stratum_quotas_keep_positive_exact_probabilities(self):
        s=source(30)
        labels=['tail']*10+['forward_OS']*10+['remainder']*10
        plan=plan_for([s])
        selected=select_events(s,plan,features(s,labels))
        self.assertEqual(len(selected['selected_events']),10)
        expected=dict(tail=.6, forward_OS=.3, remainder=.1)
        for row in selected['selected_events']:
            self.assertEqual(row['sampling_probability'],expected[row['feature_stratum']])
            self.assertEqual(row['sampling_gen_weight'],1/expected[row['feature_stratum']])
        self.assertAlmostEqual(sum(row['sampling_gen_weight']for row in selected['selected_events']),30.)

    def test_pthat_bin_quotas_do_not_change_native_weights_or_default_bin(self):
        low = source(30)
        high = source(30, source_id='production_qcd_20toinf_0000', run=43000000)
        high['stratum'] = high['request']['stratum'] = high['normalization']['stratum'] = 'qcd_20toinf'
        policy = copy.deepcopy(DEFAULT_POLICY)
        policy['quotas_by_stratum'] = {'qcd_20toinf': dict(tail=30, forward_OS=5, remainder=2)}
        plan = plan_for([low, high], policy)
        labels = ['tail']*10 + ['forward_OS']*10 + ['remainder']*10
        low_rows = select_events(low, plan, features(low, labels))['selected_events']
        high_rows = select_events(high, plan, features(high, labels))['selected_events']
        self.assertEqual(len(low_rows), 10)
        self.assertEqual(len(high_rows), 17)
        for rows, probabilities in ((low_rows, dict(tail=.6, forward_OS=.3, remainder=.1)),
                                    (high_rows, dict(tail=1., forward_OS=.5, remainder=.2))):
            for row in rows:
                self.assertEqual(row['native_weight'], 1.)
                self.assertEqual(row['sampling_probability'], probabilities[row['feature_stratum']])
            self.assertAlmostEqual(sum(row['sampling_gen_weight'] for row in rows), 30.)

    def test_fractional_sampling_keeps_all_tails_and_rounds_controls_with_exact_probability(self):
        s = source(37)
        policy = copy.deepcopy(DEFAULT_POLICY)
        policy.update(mode='GEN-stratified-fixed-fraction',
                      fractions=dict(tail=1., forward_OS=.5, remainder=.1))
        labels = ['tail']*11 + ['forward_OS']*13 + ['remainder']*13
        selected = select_events(s, plan_for([s], policy), features(s, labels))
        populations = selected['sampling']['populations']
        self.assertEqual([populations[label]['selected'] for label in ('tail', 'forward_OS', 'remainder')],
                         [11, 7, 2])
        self.assertEqual(populations['forward_OS']['inclusion_probability'], 7/13)
        self.assertEqual(populations['remainder']['inclusion_probability'], 2/13)
        self.assertEqual(len(selected['selected_events']), 20)
        self.assertAlmostEqual(sum(row['sampling_gen_weight'] for row in selected['selected_events']), 37.)

    def test_fractional_and_bin_allocation_reject_zero_unknown_and_nonfinite_support(self):
        for bad in (0, -1, 1.01, float('nan'), float('inf'), True):
            policy = copy.deepcopy(DEFAULT_POLICY)
            policy.update(mode='GEN-stratified-fixed-fraction',
                          fractions=dict(tail=1., forward_OS=.5, remainder=bad))
            with self.subTest(fraction=bad), self.assertRaisesRegex(ValueError, 'positive sampling support'):
                validate_policy(policy)
        policy = copy.deepcopy(DEFAULT_POLICY)
        policy['quotas_by_stratum'] = {'qcd_typo': dict(tail=5, forward_OS=2, remainder=1)}
        with self.assertRaisesRegex(ValueError, 'known QCD'):
            validate_policy(policy)
        policy['quotas_by_stratum'] = {'qcd_20toinf': dict(tail=5, forward_OS=2, remainder=0)}
        with self.assertRaisesRegex(ValueError, 'positive sampling support'):
            validate_policy(policy)

    def test_missing_features_cannot_silently_switch_to_uniform(self):
        s=source()
        with self.assertRaisesRegex(ValueError,'matching upstream-only'):
            select_events(s,plan_for([s]))

    def test_preview_growth_does_not_resample_or_change_training_folds(self):
        a,b=source(),source(source_id='production_qcd_0to1_0001',run=42000001)
        f=features(a,['remainder']*12)
        first=select_events(a,plan_for([a]),f)
        later=select_events(a,plan_for([a,b]),f)
        self.assertEqual(first['selected_events'],later['selected_events'])
        self.assertEqual(first['sampling']['plan_sha256'],later['sampling']['plan_sha256'])
        self.assertEqual(first['cohort_id'],later['cohort_id'])

    def test_training_split_is_stable_when_selection_seed_changes(self):
        s=source(50)
        f=features(s,['tail']*50)
        plan=plan_for([s]);first=select_events(s,plan,f)
        policy=copy.deepcopy(DEFAULT_POLICY);policy['seed']='independent-selection-seed'
        later=select_events(s,plan_for([s],policy),f)
        original={tuple(r['event_id']):r['training_fold']for r in first['selected_events']}
        for row in later['selected_events']:
            if tuple(row['event_id'])in original:self.assertEqual(original[tuple(row['event_id'])],row['training_fold'])

    def test_partial_fast_reco_is_diagnostic_until_fixed_membership_closes(self):
        s=source()
        plan=plan_for([s]);selected=select_events(s,plan,features(s,['remainder']*12))
        with self.assertRaisesRegex(ValueError,'diagnostic only'):
            closed_normalization(plan,[selected],[])
        result=closed_normalization(plan,[selected],selected['selected_events'])
        self.assertTrue(result['complete'])
        self.assertEqual(result['strata']['qcd_0to1']['scale_pb_per_histogram_weight'],6.5)

    def test_closed_sources_pool_exposure_instead_of_summing_source_rates(self):
        a,b=source(trials=100),source(source_id='production_qcd_0to1_0001',run=42000001,trials=300)
        plan=plan_for([a,b])
        selected=[select_events(s,plan,features(s,['remainder']*12))for s in (a,b)]
        observed=[row for s in selected for row in s['selected_events']]
        result=closed_normalization(plan,selected,observed)
        normalization=result['strata']['qcd_0to1']
        self.assertEqual(normalization['full_source_trials'],400)
        self.assertEqual(normalization['scale_pb_per_histogram_weight'],650/400)
        with self.assertRaisesRegex(ValueError,'incomplete'):
            closed_normalization(plan,selected[:1],observed[:1])
        with self.assertRaisesRegex(ValueError,'Duplicate GEN'):
            closed_normalization(plan,selected+[selected[0]],observed)

    def test_lost_zero_muon_events_and_changed_weights_cannot_close(self):
        s=source();plan=plan_for([s]);selected=select_events(s,plan,features(s,['remainder']*12))
        observed=copy.deepcopy(selected['selected_events']);observed[0]['native_weight']*=2
        with self.assertRaisesRegex(ValueError,'native weight'):
            closed_normalization(plan,[selected],observed)

    def test_upstream_priority_uses_region_and_real_OS_pairs_only(self):
        a=dict(pdg_id=13,px=1.,py=0.,pz=-100.,energy=101.,x_mm=0.,y_mm=0.,z_mm=148000.)
        b=dict(a,pdg_id=-13,energy=100.02)
        # Avoid the mass cue when testing the forward-pair branch.
        a['energy']=math.sqrt(10001.011)
        b['energy']=a['energy']
        self.assertEqual(feature_stratum([a,b]),'forward_OS')
        self.assertEqual(feature_stratum([a,dict(b,pdg_id=13)]),'remainder')
        self.assertEqual(feature_stratum([dict(a,px=6.)]),'tail')
        self.assertEqual(feature_stratum([dict(a,px=6.,z_mm=200000.)]),'remainder')

    def test_no_zero_support_policy_and_no_live_descriptor_overwrite(self):
        policy=copy.deepcopy(DEFAULT_POLICY);policy['quotas']['remainder']=0
        with self.assertRaisesRegex(ValueError,'positive'):
            validate_policy(policy)
        with tempfile.TemporaryDirectory()as directory:
            path=Path(directory)/'immutable.json';write_new(path,dict(first=True))
            with self.assertRaises(FileExistsError):write_new(path,dict(first=False))
            self.assertEqual(json.loads(path.read_text()),dict(first=True))


if __name__=='__main__':
    unittest.main()
