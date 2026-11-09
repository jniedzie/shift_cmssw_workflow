# Code style: AI-code. AI-generated additive signal population regression tests.
"""Exercise GEN deduplication, rate accounting and ordered semantic Nano checks."""
from array import array
import copy
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from aggregate_dark_photon_samples import (combined_rate, disjoint_parent_component_rates,
                                         senscalc_dy_component_rates, prepare_senscalc_dy_histograms,
                                         completed_histogram_input, event_rows, execute, group_inputs,
                                         qualified_finalizer, sha, unique_populations,
                                         validate_event_rows, verify_aggregate_seal,
                                         validated_bremsstrahlung_identity)


def population(run=10, events=2, trials=100, rate=2., error=.1):
    identities = [[run, 1, index + 1] for index in range(events)]
    digest = hashlib.sha256(json.dumps(identities, separators=(',', ':')).encode()).hexdigest()
    record = dict(mass_gev=15., epsilon=1e-7, proper_ctau_mm=100., sampling_epsilon=.005,
        seed=run, run_number=run, physical_selected_cross_sections_pb=[dict(value_pb=rate, error_pb=error)],
        physical_br_mumu=.2, mechanism='pure-on-shell-DY', model='visible-dark-photon',
        cross_section_convention='selected dimuon BR included once', beam_energy_GeV=6800.,
        source_model='stationary proton target', corrections=dict(applications=1, combined=1e-8),
        generated_mass_support_gev=[14.85, 15.15],
        proposal_ledger=dict(n_tried=trials, n_selected=events, n_accepted=events),
        full_gen=dict(accepted_events=events, sum_weights=float(events), sum_weights_squared=float(events),
                      event_ids_sha256=digest))
    return dict(source=dict(point='point', campaign='run_' + str(run)),
        parent=dict(gen_sha256=hashlib.sha256(str(run).encode()).hexdigest(), event_ids=identities,
                    native_sumw=float(events), native_sumw2=float(events)), rate=record,
        inputs=[dict(path='nano_' + str(run), events=events, event_ids=identities)])


class DYNormalizationAggregation(unittest.TestCase):
    @staticmethod
    def apply(sample):
        import dark_photon_dy_prescription as helper
        record=sample['rate'];factor,error=2.,.2
        prescription=dict(schema='shift-dy-msht20-nnlo-v1',name='msht20-nnlo',generator_order='LO+PS',
            hard_pdf=dict(set='MSHT20nnlo_as118',member=0),
            native_pdf_settings=list(helper.PDF_SYMMETRY_SETTINGS),
            pdf_inputs={name:'c'*64 for name in helper.PDF_INPUT_NAMES},
            normalization=dict(order='NNLO',reference='native-clipped-LO',factor=factor,common_numerical_error=error),
            authority=dict(filename='dy_msht20_nnlo_v1.json',sha256='a'*64,
                           helper_filename='dark_photon_dy_prescription.py',helper_sha256=sha(helper.__file__)))
        lo=copy.deepcopy(record['physical_selected_cross_sections_pb'])
        record.update(mechanism='pure-on-shell-drell-yan',dy_prescription=prescription,
            dy_normalization_applications=1,physical_lo_selected_cross_sections_pb=lo,
            physical_lo_inclusive_cross_sections_pb=[dict(value_pb=x['value_pb']/record['physical_br_mumu'],
                error_pb=x['error_pb']/record['physical_br_mumu']) for x in lo],
            dy_normalization_uncertainty=dict(kind='common_numerical_normalization',factor=factor,
                absolute_k_error=error,relative_error=error/factor,
                correlation_key='dy-nnlo-numerical:'+prescription['authority']['sha256']+':'+format(record['mass_gev'],'.17g'),
                authority=copy.deepcopy(prescription['authority']),included_in_independent_error_pb=False))
        record['physical_selected_cross_sections_pb']=[dict(value_pb=x['value_pb']*factor,error_pb=x['error_pb']*factor) for x in lo]
        record['physical_inclusive_cross_sections_pb']=[dict(value_pb=x['value_pb']*factor,error_pb=x['error_pb']*factor)
            for x in record['physical_lo_inclusive_cross_sections_pb']]
        return sample

    def test_pool_independent_MC_errors_but_keep_one_common_factor_error(self):
        samples=[self.apply(population(10,2,100,2.,.1)),self.apply(population(20,2,300,6.,.2))]
        result=combined_rate(samples,point='m15_prompt',campaign='repeat')
        self.assertEqual(result['physical_selected_cross_sections_pb'][0]['value_pb'],10.)
        self.assertAlmostEqual(result['physical_selected_cross_sections_pb'][0]['error_pb'],2*math.hypot(.025,.15))
        self.assertEqual(result['physical_lo_selected_cross_sections_pb'][0]['value_pb'],5.)
        self.assertEqual(result['dy_normalization_uncertainty'],samples[0]['rate']['dy_normalization_uncertainty'])
        self.assertEqual(result['dy_normalization_uncertainty']['relative_error'],.1)
        self.assertEqual(result['dy_prescription'],samples[0]['rate']['dy_prescription'])
        self.assertEqual(result['full_gen']['accepted_events'],4)

    def test_legacy_and_different_prescriptions_cannot_hide_in_pooling_or_recovery_views(self):
        first=self.apply(population(10));old=population(20);old['rate']['mechanism']='pure-on-shell-drell-yan'
        with self.assertRaisesRegex(ValueError,'Different DY prescriptions'):
            combined_rate([first,old],point='point',campaign='mixed')
        for change in ('prescription','missing_common_error','inflated_independent_error',
                       'inclusive_missing_K','inclusive_error_includes_common'):
            second=self.apply(population(20))
            if change=='prescription':second['rate']['dy_prescription']['hard_pdf']['member']=1
            elif change=='missing_common_error':del second['rate']['dy_normalization_uncertainty']
            elif change=='inclusive_missing_K':second['rate']['physical_inclusive_cross_sections_pb'][0]['value_pb']/=2.
            elif change=='inclusive_error_includes_common':second['rate']['physical_inclusive_cross_sections_pb'][0]['error_pb']=10.
            else:second['rate']['physical_selected_cross_sections_pb'][0]['error_pb']=1.
            with self.subTest(change=change),self.assertRaises(ValueError):
                combined_rate([first,second],point='point',campaign='mixed')
        recovery=copy.deepcopy(first);del recovery['rate']['dy_prescription']
        with self.assertRaisesRegex(ValueError,'Recovery views disagree'):
            unique_populations([first,recovery])
        with self.assertRaisesRegex(ValueError,'Incomplete DY normalization'):
            combined_rate([recovery],point='point',campaign='incomplete')

    def test_FR_plus_DY_sum_keeps_the_DY_common_error_as_a_separate_component_nuisance(self):
        reference=SensCalcDYAccounting.component('inclusive',10,10,400,5.,reference=True)
        dy=SensCalcDYAccounting.component('dy',30,2,100,3.)
        self.apply(dy['populations'][0])
        result=senscalc_dy_component_rates([reference,dy],point='m1p8_lab30',campaign='overlap')
        self.assertEqual(result['physical_selected_cross_sections_pb'][0]['value_pb'],11.)
        self.assertAlmostEqual(result['physical_selected_cross_sections_pb'][0]['error_pb'],math.hypot(.1,.2))
        common,=result['dy_normalization_uncertainties']
        self.assertEqual(common['component'],'dy')
        self.assertAlmostEqual(common['absolute_selected_error_pb'],.6)
        self.assertEqual(common['uncertainty']['relative_error'],.1)
        self.assertEqual(result['components'][1]['full_gen_weight_sum'],2.)
        self.assertNotIn('dy_prescription',reference['populations'][0]['rate'])


class MainHistogramAggregation(unittest.TestCase):
    def test_legacy_refresh_requires_explicit_optin_and_no_classifier_selection(self):
        from aggregate_dark_photon_samples import legacy_main_histograms
        for flag, selection in [(False,False),(True,True),(True,None)]:
            with self.subTest(flag=flag,selection=selection), tempfile.TemporaryDirectory() as tmp:
                source=dict(report=dict(score_histograms=dict(marker=dict(complete=True,selection_applied=selection))))
                with self.assertRaisesRegex(ValueError,'explicit regeneration'):
                    legacy_main_histograms(dict(rehistogram_legacy_sources=flag),source,Path(tmp)/'work')

    def test_real_ROOT_merge_keeps_W_over_p_and_uses_source_collisions_for_the_rate(self):
        import ROOT
        from aggregate_dark_photon_samples import execute_main_histograms
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'results/completed_points').mkdir(parents=True)
            samples=[PopulationAccounting().reference(10,100,999,2.),
                     PopulationAccounting().reference(20,300,3,6.)]
            for i,pop in enumerate(samples):
                path=root/('source_'+str(i)+'.root');f=ROOT.TFile(str(path),'RECREATE')
                weights=(2.,3.) if i==0 else (1.,1.)
                raw=ROOT.TH1D('rawEventsCutFlow','',1,0,1)
                for _ in weights:raw.Fill(.5)
                raw.Write();event=f.mkdir('event');event.cd()
                h=ROOT.TH1D('Event_nShiftMuon','',5,0,5);h.Sumw2()
                for w in weights:h.Fill(2,w)
                h.Write();d=f.mkdir('dimuon');d.cd()
                for category in ('','Good','Near-Both','Near-Far','Both-Both','Both-Far','Other'):
                    for variable in ('mass','pt','logMass'):
                        h=ROOT.TH1D('ShiftDimuonVertex'+category+'_'+variable,'',10,0,100);h.Sumw2()
                        for w in weights:h.Fill(15,w)
                        h.Write()
                original=dict(source_id=str(i),gen_sha256=pop['parent']['gen_sha256'])
                metadata=dict(full_source_sumw=2,cross_section_pb=pop['rate']['physical_selected_cross_sections_pb'][0]['value_pb'],original_sources=[original])
                f.cd();ROOT.TObjString(json.dumps(metadata)).Write('ShiftFullGenNormalization');f.Close()
                item=dict(path=str(path),sha256=sha(path),bytes=path.stat().st_size)
                pop['report']=dict(main_histograms=dict(published=dict(merged_histogram=item),full_original_exposure=metadata))
            rate=combined_rate(samples,point='m15_prompt',campaign='test')
            destinations={}
            def download(source,target,checksum):
                self.assertEqual(sha(source),checksum);shutil.copy2(source,target)
            def publish(source,directory):
                destination=directory+'/'+Path(source).name;copy=root/('published_'+str(len(destinations))+Path(source).suffix)
                shutil.copy2(source,copy);self.assertEqual(sha(source),sha(copy));destinations[destination]=copy
                return dict(path=destination,sha256=sha(copy),bytes=copy.stat().st_size)
            row=dict(point='m15_prompt',events=4,full_gen_events=4,mass_gev=15.,epsilon=1.e-7,
                sampling_epsilon=.005,proper_ctau_mm=100.,target_mean_lab_flight_m=None,
                final_output_base='/eos/user/j/jniedzie/shift_cmssw/darkphoton_m15_prompt/test')
            with patch('run_shift_sm_postprocessing.download',download),patch('run_shift_sm_postprocessing.publish',publish):
                report=execute_main_histograms(dict(maximum_publication_bytes=10000000),row,samples,rate,'p','s',root,root/'results')
            path=destinations[report['main_histograms']['published']['merged_histogram']['path']]
            f=ROOT.TFile.Open(str(path));metadata=json.loads(f.Get('ShiftFullGenNormalization').GetString().Data())
            self.assertEqual(metadata['full_source_sumw'],4)
            self.assertEqual(metadata['full_source_collisions'],400)
            self.assertEqual(metadata['cross_section_pb'],5.)
            self.assertEqual(f.Get('rawEventsCutFlow').GetBinContent(1),4.)
            h=f.Get('dimuon/ShiftDimuonVertexBoth-Both_logMass')
            self.assertEqual(h.Integral(),7.)
            self.assertAlmostEqual(h.GetBinError(h.FindBin(15.))**2,15.)
            f.Close()


class SingleSourceRegroupingCanary(unittest.TestCase):
    def test_explicit_nano_only_canary_keeps_one_actual_parent_denominator(self):
        from aggregate_dark_photon_samples import validate_aggregate_population_count
        source=population()
        plan=dict(histogram_only=False,single_source_regrouping_canary=True)
        row=dict(sources=[source['source']])
        validate_aggregate_population_count(plan,row,[source])
        self.assertEqual(len(unique_populations([source])),1)
        for changed_plan,changed_sources,changed_populations in [
            ({'histogram_only':False},row['sources'],[source]),
            (dict(plan,histogram_only=True),row['sources'],[source]),
            (dict(plan,single_source_regrouping_canary=1),row['sources'],[source]),
            (plan,row['sources']*2,[source]),(plan,row['sources'],[source,population(20)]),
        ]:
            with self.assertRaises(ValueError):
                validate_aggregate_population_count(changed_plan,dict(sources=changed_sources),changed_populations)


class DisjointParentAccounting(unittest.TestCase):
    @staticmethod
    def component(name, pdg, populations):
        for item in populations:
            rate = item['rate']
            exposure = rate['proposal_ledger']['n_tried']
            rate.update(mechanism='meson-radiative', meson_model_card={'parents': [{'pdg_id': pdg}]},
                rate_scope='non-diffractive pp', source_exposure=dict(
                    fixed_source_collisions=exposure, source_collisions=exposure,
                    native_n_accepted=exposure, accepted_signal_events=rate['full_gen']['accepted_events'],
                    coefficient_sum=1., coefficient_sum_squared=1.,
                    physical_probability_sum=1., physical_probability_sum_squared=1.))
            rate['full_gen']['source_collisions'] = exposure
        return dict(name=name, conditions_sha256='a' * 64, populations=populations)

    def combine(self, components):
        return disjoint_parent_component_rates(components, point='m15_lab_30m', campaign='combined')

    def test_pool_repeats_then_add_distinct_rates_with_separate_denominators(self):
        components = [self.component('light', 221, [population(10, 2, 100, 2.),
                       population(20, 8, 300, 6.)]),
                      self.component('kaon', 130, [population(30, 2, 100, 3., .2)])]
        result = self.combine(components)
        self.assertEqual(result['physical_selected_cross_sections_pb'][0]['value_pb'], 8.)
        self.assertAlmostEqual(result['physical_selected_cross_sections_pb'][0]['error_pb'],
                               math.sqrt(.025**2 + .075**2 + .2**2))
        light, kaon = result['components']
        self.assertEqual(light['selected_cross_section_pb'], 5.)
        self.assertEqual(light['full_gen_weight_sum'], 10.)
        self.assertEqual(kaon['full_gen_weight_sum'], 2.)
        self.assertEqual(light['cross_section_per_gen_weight_pb'], .5)
        self.assertEqual(kaon['cross_section_per_gen_weight_pb'], 1.5)
        self.assertEqual(result['histogram_normalization']['expected_full_gen_stored_sumw'], 12.)
        self.assertEqual(result['histogram_normalization']['expected_full_gen_stored_sumw2'], 15.75)
        self.assertFalse(result['normalization_ready'])
        # In a selected bin with two light events and one kaon event, the
        # correct yield is 2500 at 1000 pb^-1. A single raw-population scale
        # would incorrectly give 2000. The variance includes squared weights.
        factors = [row['histogram_scale'] for row in result['components']]
        reference = result['histogram_normalization']['cross_section_per_stored_weight_pb']
        selected_bin = 2 * factors[0] + factors[1]
        variance = 2 * factors[0]**2 + factors[1]**2
        self.assertAlmostEqual(1000 * reference * selected_bin, 2500.)
        self.assertAlmostEqual((1000 * reference)**2 * variance, 2750000.)
        # The same factors must apply to underflow/overflow and not only
        # visible bins; no second dimuon branching fraction is needed.
        self.assertAlmostEqual(reference * factors[1], 1.5)
        self.assertEqual(light['rate']['source_exposure']['source_collisions'], 400)

    def test_shared_parent_and_full_union_cannot_be_added_to_old_component(self):
        first = self.component('old', 221, [population(10)])
        second = self.component('union', 130, [population(20)])
        second['populations'][0]['rate']['meson_model_card']['parents'].append({'pdg_id': 221})
        with self.assertRaisesRegex(ValueError, 'share a rare parent'):
            self.combine([first, second])

    def test_sibling_channels_within_one_component_keep_one_parent_owner(self):
        first = self.component('old', 221, [population(10)])
        second = self.component('kaon', 310, [population(20)])
        second['populations'][0]['rate']['meson_model_card']['parents'].append({'pdg_id': 310})
        self.assertEqual(self.combine([first, second])['components'][1]['parent_pdg_ids'], [310])

    def test_cross_component_reuse_and_different_conditions_or_physics_fail(self):
        for case in ('reused_seed', 'reused_population', 'conditions', 'mass', 'br', 'convention'):
            with self.subTest(case=case):
                first = self.component('old', 221, [population(10)])
                second = self.component('kaon', 130, [population(20)])
                rate = second['populations'][0]['rate']
                if case == 'reused_seed':
                    rate['seed'] = 10
                elif case == 'reused_population':
                    second['populations'][0]['parent']['gen_sha256'] = first['populations'][0]['parent']['gen_sha256']
                elif case == 'conditions':
                    second['conditions_sha256'] = 'b' * 64
                elif case == 'mass':
                    rate['mass_gev'] = 16.
                elif case == 'br':
                    rate['physical_br_mumu'] = .3
                elif case == 'convention':
                    rate['cross_section_convention'] = 'different convention'
                with self.assertRaises(ValueError):
                    self.combine([first, second])

    def test_component_inventory_and_names_are_mandatory(self):
        for case in ('missing_inventory', 'zero_pdg', 'duplicate_name', 'missing_conditions'):
            with self.subTest(case=case):
                first = self.component('old', 221, [population(10)])
                second = self.component('kaon', 130, [population(20)])
                if case == 'missing_inventory':
                    second['populations'][0]['rate']['meson_model_card']['parents'] = []
                elif case == 'zero_pdg':
                    second['populations'][0]['rate']['meson_model_card']['parents'][0]['pdg_id'] = 0
                elif case == 'duplicate_name':
                    second['name'] = 'old'
                elif case == 'missing_conditions':
                    del second['conditions_sha256']
                with self.assertRaises(ValueError):
                    self.combine([first, second])


class SensCalcDYAccounting(unittest.TestCase):
    @staticmethod
    def component(name, run, events=2, trials=100, rate=2., reference=False):
        sample = population(run, events, trials, rate)
        record = sample['rate']
        record.update(mass_gev=1.8, generated_mass_support_gev=[1.782, 1.818],
            mechanism='pure-on-shell-drell-yan', source_sha256={
                key: hashlib.sha256((str(run) + key).encode()).hexdigest()
                for key in ('contract.json', 'validation.json', 'manifest.json', 'cmsRun.log')})
        if reference:
            record.update(mechanism='senscalc-meson-fragmentation-bremsstrahlung',
                sampling_epsilon=record['epsilon'], generated_mass_support_gev=[1.7999982, 1.8000018],
                source_model='ISR-off reference source', cross_section_convention='Reference dimuon rate',
                rate_profile='pointlike', radiative_model_sha256='a' * 64,
                source_graph_scope='isolated signal', polarization_model='isotropic',
                rate_scope='reference ND mesons, fragmentation and FR',
                source_ledger_sha256=str(run).zfill(64), source_seed=run,
                source_sampling=dict(proposal_probability=.01, unweighting_envelope=4.,
                    bremsstrahlung_profile_sha256='b' * 64, bremsstrahlung_sampler_sha256='c' * 64),
                bremsstrahlung_profile_identity=dict(raw_sha256='b' * 64, physics_sha256='d' * 64),
                source_exposure=dict(source_collisions=trials, fixed_source_collisions=trials,
                    accepted_signal_events=events))
            record['full_gen']['source_collisions'] = trials
        return dict(name=name, conditions_sha256='a' * 64, populations=[sample])

    def combine(self, components):
        return senscalc_dy_component_rates(components, point='m1p8_lab30', campaign='overlap')

    def test_pool_each_mechanism_then_sum_rates_with_independent_denominators(self):
        reference = self.component('inclusive', 10, 2, 100, 2., reference=True)
        reference['populations'] += self.component('repeat', 20, 8, 300, 6., reference=True)['populations']
        dy = self.component('dy', 30, 2, 100, 3.)
        result = self.combine([reference, dy])
        self.assertEqual(result['physical_selected_cross_sections_pb'][0]['value_pb'], 8.)
        self.assertAlmostEqual(result['physical_selected_cross_sections_pb'][0]['error_pb'],
                               math.sqrt(.025**2 + .075**2 + .1**2))
        low, high = result['components']
        self.assertEqual(low['rate']['combination']['normalized_weights'], [.25, .75])
        self.assertEqual([low['full_gen_weight_sum'], high['full_gen_weight_sum']], [10., 2.])
        normalization = result['histogram_normalization']['cross_section_per_stored_weight_pb']
        factors = [row['histogram_scale'] for row in result['components']]
        # Two selected reference events and one selected DY event: preserve
        # the rest of each original GEN denominator, including failed selection.
        self.assertAlmostEqual(normalization * (2 * factors[0] + factors[1]), 2.5)
        self.assertAlmostEqual(normalization**2 * (2 * factors[0]**2 + factors[1]**2), 2.75)
        self.assertAlmostEqual(normalization * sum(
            row['histogram_scale'] * row['full_gen_weight_sum'] for row in result['components']), 8.)
        # Technical proposal epsilon, mass support and convention stay attached
        # to their own component, rather than being silently made identical.
        self.assertEqual(low['rate']['sampling_epsilon'], 1e-7)
        self.assertEqual(high['rate']['sampling_epsilon'], .005)
        self.assertNotEqual(low['rate']['cross_section_convention'], high['rate']['cross_section_convention'])
        self.assertFalse(result['normalization_ready'])

    def test_same_population_recovery_view_does_not_add_rate_or_exposure(self):
        reference = self.component('inclusive', 10, reference=True)
        reference['populations'].append(copy.deepcopy(reference['populations'][0]))
        result = self.combine([reference, self.component('dy', 30, rate=3.)])
        self.assertEqual(result['physical_selected_cross_sections_pb'][0]['value_pb'], 5.)
        self.assertEqual(result['components'][0]['full_gen_weight_sum'], 2.)

    def test_more_GEN_at_the_same_efficiency_preserves_yield_and_reduces_variance(self):
        measured = []
        for size in (1, 2):
            reference = self.component('inclusive', 10, 10 * size, 400 * size, 5., reference=True)
            result = self.combine([reference, self.component('dy', 30, 2 * size, 100 * size, 3.)])
            scale = result['histogram_normalization']['cross_section_per_stored_weight_pb']
            a, b = [row['histogram_scale'] for row in result['components']]
            measured.append((scale * size * (2 * a + b), scale**2 * size * (2 * a**2 + b**2)))
        self.assertAlmostEqual(measured[0][0], measured[1][0])
        self.assertAlmostEqual(measured[1][1], measured[0][1] / 2)

    def test_ROOT_component_scaling_preserves_sampling_weights_and_variance(self):
        try:
            import ROOT
        except ImportError:
            self.skipTest('ROOT is needed for the real histogram scaling control')
        result = self.combine([self.component('inclusive', 10, 10, 400, 5., reference=True),
                               self.component('dy', 30, 2, 100, 3.)])
        selected, full = [], []
        for index, (component, weights) in enumerate(zip(result['components'], [(1., 3.), (1.,)])):
            hist = ROOT.TH1D('overlap_selected_' + str(index), '', 1, 0., 1.)
            hist.SetDirectory(0)
            hist.Sumw2()
            for weight in weights:
                hist.Fill(.5, weight)
            hist.Scale(component['histogram_scale'])
            selected.append(hist)
            complete = ROOT.TH1D('overlap_full_' + str(index), '', 1, 0., 1.)
            complete.SetDirectory(0)
            complete.Sumw2()
            for unused in range(int(component['full_gen_weight_sum'])):
                complete.Fill(.5)
            complete.Scale(component['histogram_scale'])
            full.append(complete)
        normalization = result['histogram_normalization']['cross_section_per_stored_weight_pb']
        selected[0].Add(selected[1])
        selected[0].Scale(normalization)
        full[0].Add(full[1])
        full[0].Scale(normalization)
        self.assertAlmostEqual(selected[0].Integral(), 3.5)
        self.assertAlmostEqual(selected[0].GetBinError(1)**2, 4.75)
        self.assertAlmostEqual(full[0].Integral(), 8.)

    def test_other_or_duplicate_mechanisms_are_not_independent_components(self):
        for mechanism in ('senscalc-meson-fragmentation-bremsstrahlung',
                          'senscalc-meson-fragmentation', 'meson-radiative', 'pure-on-shell-DY'):
            with self.subTest(mechanism=mechanism):
                first = self.component('inclusive', 10, reference=True)
                second = self.component('dy', 30)
                second['populations'][0]['rate']['mechanism'] = mechanism
                with self.assertRaisesRegex(ValueError, 'nonoverlapping'):
                    self.combine([first, second])
        with self.assertRaises(ValueError):
            self.combine([self.component('inclusive', 10, reference=True)])

    def test_cross_component_reuse_rejects_ids_runs_seeds_gen_and_source_hashes(self):
        for field in ('event_ids', 'run', 'seed', 'gen_sha256', 'source_sha256',
                      'source_seed', 'source_ledger_sha256'):
            with self.subTest(field=field):
                first = self.component('inclusive', 10, reference=True)
                second = self.component('dy', 30)
                a, b = first['populations'][0], second['populations'][0]
                if field in ('event_ids', 'run'):
                    b['parent']['event_ids'][0][0] = 10
                    if field == 'run':
                        b['parent']['event_ids'][0][2] = 99
                    b['rate']['full_gen']['event_ids_sha256'] = hashlib.sha256(
                        json.dumps(b['parent']['event_ids'], separators=(',', ':')).encode()).hexdigest()
                elif field == 'gen_sha256':
                    b['parent'][field] = a['parent'][field]
                elif field == 'source_sha256':
                    b['rate'][field] = copy.deepcopy(a['rate'][field])
                elif field == 'source_ledger_sha256':
                    b['rate'][field] = a['rate'][field]
                else:
                    b['rate'][field] = 10
                with self.assertRaisesRegex(ValueError, 'reuse GEN'):
                    self.combine([first, second])

    def test_shared_configuration_or_log_hash_is_not_reused_collision_exposure(self):
        first = self.component('inclusive', 10, reference=True)
        second = self.component('dy', 30, rate=3.)
        expected = self.combine(copy.deepcopy([first, second]))
        for key in ('contract.json', 'cmsRun.log'):
            second['populations'][0]['rate']['source_sha256'][key] = (
                first['populations'][0]['rate']['source_sha256'][key])
        actual = self.combine([first, second])
        self.assertEqual(actual['physical_selected_cross_sections_pb'], expected['physical_selected_cross_sections_pb'])
        self.assertEqual(actual['histogram_normalization'], expected['histogram_normalization'])

    def test_source_ledger_hash_must_be_valid_when_present(self):
        for invalid in (None, '', 'not-a-hash', 'g' * 64):
            with self.subTest(invalid=invalid):
                first = self.component('inclusive', 10, reference=True)
                second = self.component('dy', 30)
                second['populations'][0]['rate']['source_ledger_sha256'] = invalid
                with self.assertRaisesRegex(ValueError, 'source ledger hash'):
                    self.combine([first, second])

    def test_incompatible_physical_points_conditions_and_missing_source_proofs_fail(self):
        changes = dict(mass_gev=1.7, epsilon=2e-7, proper_ctau_mm=101.,
                       physical_br_mumu=.3, beam_energy_GeV=7000.)
        for field, value in changes.items():
            with self.subTest(field=field):
                first = self.component('inclusive', 10, reference=True)
                second = self.component('dy', 30)
                second['populations'][0]['rate'][field] = value
                with self.assertRaisesRegex(ValueError, 'different physical'):
                    self.combine([first, second])
        for field in ('conditions_sha256', 'source_sha256', 'generated_mass_support_gev'):
            with self.subTest(field=field):
                first = self.component('inclusive', 10, reference=True)
                second = self.component('dy', 30)
                if field == 'conditions_sha256':
                    second[field] = 'b' * 64
                elif field == 'source_sha256':
                    second['populations'][0]['rate'].pop(field)
                else:
                    second['populations'][0]['rate'][field] = [1.9, float('inf')]
                with self.assertRaises(ValueError):
                    self.combine([first, second])

    def test_source_profile_and_mass_range_are_explicitly_qualified(self):
        for field, value in [('rate_profile', 'measured-form-factor'),
                             ('bremsstrahlung_profile_identity', None), ('mass_gev', 1.4), ('mass_gev', 2.)]:
            with self.subTest(field=field, value=value):
                first = self.component('inclusive', 10, reference=True)
                second = self.component('dy', 30)
                if value is None:
                    first['populations'][0]['rate'].pop(field)
                else:
                    first['populations'][0]['rate'][field] = value
                with self.assertRaises(ValueError):
                    self.combine([first, second])


class CompositeMainHistograms(unittest.TestCase):
    def sources(self, directory):
        import ROOT
        components = [SensCalcDYAccounting.component('inclusive', 10, 10, 400, 5., reference=True),
                      SensCalcDYAccounting.component('dy', 30, 2, 100, 3.)]
        for index, component in enumerate(components):
            population = component['populations'][0]
            population['source']['point'] = 'm1p8_lab30' if index == 0 else 'm1p8_DY_component'
            path = directory / ('component_' + str(index) + '.root')
            file = ROOT.TFile(str(path), 'RECREATE')
            raw = ROOT.TH1D('rawEventsCutFlow', '', 1, 0., 1.)
            for unused in range(2 if index == 0 else 1):
                raw.Fill(.5)
            raw.Write()
            folder = file.mkdir('dimuon'); folder.cd()
            for category in ('', 'Good', 'Near-Both', 'Near-Far', 'Both-Both', 'Both-Far', 'Other'):
                for variable in ('mass', 'pt', 'logMass'):
                    hist = ROOT.TH1D('ShiftDimuonVertex' + category + '_' + variable, '', 10, 0., 100.)
                    hist.Sumw2()
                    for weight in ((1., 3.) if index == 0 else (1.,)):
                        hist.Fill(15., weight)
                    hist.Fill(-1., 2. if index == 0 else 3.)
                    hist.Fill(101., 4. if index == 0 else 2.)
                    hist.Write()
            metadata = dict(schema='shift-preserved-full-GEN-histogram-v1',
                stratum='darkphoton_' + population['source']['point'],
                full_source_sumw=population['rate']['full_gen']['sum_weights'],
                cross_section_pb=population['rate']['physical_selected_cross_sections_pb'][0]['value_pb'],
                histogram_weight='native_gen_weight/reco_inclusion_probability', no_second_filter_efficiency=True,
                original_sources=[dict(source_id=str(index), gen_sha256=population['parent']['gen_sha256'])])
            file.cd(); ROOT.TObjString(json.dumps(metadata)).Write('ShiftFullGenNormalization'); file.Close()
            population['report'] = dict(main_histograms=dict(complete=True,
                published=dict(merged_histogram=dict(path=str(path), sha256=sha(path), bytes=path.stat().st_size)),
                full_original_exposure=metadata))
        return components

    def test_saved_composite_roundtrips_through_ordinary_reader_with_flows_and_variances(self):
        import ROOT
        analysis = Path(__file__).resolve().parents[2] / 'tea_shift_cmssw'
        paths = [analysis / 'bin', analysis / 'tea/pylibs/helpers', analysis / 'tea/pylibs/logger']
        with patch.object(sys, 'path', [str(path) for path in paths] + sys.path):
            from shift_sample_paths import histogram_gen_normalization
            from teaHelpers import eos_xrootd_url  # Load the reader's lazy ordinary plotting dependency.
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary); components = self.sources(directory)
            snapshots = {row['name']: sha(row['populations'][0]['report']['main_histograms']['published']['merged_histogram']['path'])
                         for row in components}
            result = prepare_senscalc_dy_histograms(components, directory / 'composite.root',
                point='m1p8_lab30', campaign='overlap')
            normalization = histogram_gen_normalization(result['histogram']['path'], 'darkphoton_m1p8_lab30')
            self.assertEqual(normalization, dict(cross_section_pb=8., initial_weight_sum=12.))
            file = ROOT.TFile.Open(result['histogram']['path'])
            hist = file.Get('dimuon/ShiftDimuonVertexBoth-Both_logMass')
            self.assertEqual(hist.GetName(), 'ShiftDimuonVertexBoth-Both_logMass')
            factor = normalization['cross_section_pb'] / normalization['initial_weight_sum']
            self.assertAlmostEqual(factor * hist.Integral(), 3.5)
            self.assertAlmostEqual(factor**2 * hist.GetBinError(hist.FindBin(15.))**2, 4.75)
            self.assertAlmostEqual(factor * hist.GetBinContent(0), 5.5)
            self.assertAlmostEqual(factor**2 * hist.GetBinError(0)**2, 21.25)
            self.assertAlmostEqual(factor * hist.GetBinContent(hist.GetNbinsX() + 1), 5.)
            self.assertAlmostEqual(factor**2 * hist.GetBinError(hist.GetNbinsX() + 1)**2, 13.)
            self.assertEqual(file.Get('rawEventsCutFlow').GetBinContent(1), 3.)
            metadata = json.loads(file.Get('ShiftFullGenNormalization').GetString().Data())
            self.assertEqual(metadata['component_rate_scale_application_count'], 1)
            self.assertEqual(len(metadata['original_sources']), 2)
            self.assertFalse(metadata['component_nanos_combined'])
            self.assertFalse(metadata['native_nano_weights_changed'])
            self.assertFalse(result['publication_ready'])
            file.Close()
            for row in components:
                self.assertEqual(sha(row['populations'][0]['report']['main_histograms']['published']['merged_histogram']['path']),
                                 snapshots[row['name']])

    def test_DY_common_normalization_proof_survives_saved_ROOT_without_entering_statistical_Sumw2(self):
        import ROOT
        analysis = Path(__file__).resolve().parents[2] / 'tea_shift_cmssw'
        paths = [analysis / 'bin', analysis / 'tea/pylibs/helpers', analysis / 'tea/pylibs/logger']
        with patch.object(sys, 'path', [str(path) for path in paths] + sys.path):
            from shift_sample_paths import histogram_gen_normalization
            from teaHelpers import eos_xrootd_url
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary);components=self.sources(directory)
            dy=components[1]['populations'][0];DYNormalizationAggregation.apply(dy)
            marker=dy['report']['main_histograms'];item=marker['published']['merged_histogram']
            metadata=marker['full_original_exposure'];metadata['cross_section_pb']=6.
            root=ROOT.TFile.Open(item['path'],'UPDATE')
            ROOT.TObjString(json.dumps(metadata)).Write('ShiftFullGenNormalization',ROOT.TObject.kOverwrite)
            root.Close();item.update(sha256=sha(item['path']),bytes=Path(item['path']).stat().st_size)
            result=prepare_senscalc_dy_histograms(components,directory/'composite.root',point='m1p8_lab30',campaign='overlap')
            normalization=histogram_gen_normalization(result['histogram']['path'],'darkphoton_m1p8_lab30')
            self.assertEqual(normalization,dict(cross_section_pb=11.,initial_weight_sum=12.))
            root=ROOT.TFile.Open(result['histogram']['path'])
            stored=json.loads(root.Get('ShiftFullGenNormalization').GetString().Data())
            nuisance,=stored['dy_normalization_uncertainties']
            self.assertEqual(nuisance['component'],'dy')
            self.assertAlmostEqual(nuisance['absolute_selected_error_pb'],.6)
            self.assertEqual(nuisance['dy_prescription'],dy['rate']['dy_prescription'])
            self.assertEqual(len(nuisance['dy_prescription']['pdf_inputs']),4)
            histogram=root.Get('dimuon/ShiftDimuonVertexBoth-Both_logMass')
            factor=normalization['cross_section_pb']/normalization['initial_weight_sum']
            self.assertAlmostEqual(factor*histogram.GetBinContent(histogram.FindBin(15.)),5.)
            self.assertAlmostEqual(factor**2*histogram.GetBinError(histogram.FindBin(15.))**2,11.5)
            root.Close()

    def test_changed_receipt_missing_response_and_axis_mismatch_fail_before_output(self):
        import ROOT
        for change in ('checksum', 'exposure', 'receipt', 'axes', 'missing_histogram'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary); components = self.sources(directory)
                population = components[1]['populations'][0]
                marker = population['report']['main_histograms']; item = marker['published']['merged_histogram']
                if change == 'checksum':
                    item['sha256'] = '0' * 64
                elif change == 'exposure':
                    marker['full_original_exposure']['full_source_sumw'] = 1.
                elif change == 'receipt':
                    marker['complete'] = False
                else:
                    file = ROOT.TFile.Open(item['path'], 'UPDATE'); file.cd('dimuon')
                    name = 'ShiftDimuonVertexBoth-Both_logMass'
                    file.GetDirectory('dimuon').Delete(name + ';*')
                    if change == 'axes':
                        hist = ROOT.TH1D(name, '', 11, 0., 100.); hist.Sumw2(); hist.Write(name, ROOT.TObject.kOverwrite)
                    file.Close(); item.update(sha256=sha(item['path']), bytes=Path(item['path']).stat().st_size)
                with self.assertRaises((ValueError, TypeError)):
                    prepare_senscalc_dy_histograms(components, directory / 'composite.root',
                        point='m1p8_lab30', campaign='overlap')
                self.assertFalse((directory / 'composite.root').exists())

    def test_existing_output_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'keep.root'; output.write_bytes(b'preserve')
            with self.assertRaises(FileExistsError):
                prepare_senscalc_dy_histograms([], output, point='m1p8_lab30', campaign='overlap')
            self.assertEqual(output.read_bytes(), b'preserve')

    def test_completed_zero_response_keeps_original_GEN_but_pending_work_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary); components = self.sources(directory)
            reference = components[0]['populations'][0]
            reference['rate_only'] = True
            reference['report'] = dict(complete=True, rate_only=True, events=0)
            result = prepare_senscalc_dy_histograms(components, directory / 'zero_response.root',
                point='m1p8_lab30', campaign='overlap')
            metadata = result['full_original_exposure']
            self.assertEqual(metadata['full_source_sumw'], 12.)
            self.assertEqual(metadata['cross_section_pb'], 8.)
            self.assertEqual(len(metadata['original_sources']), 2)
            reference['report']['complete'] = False
            with self.assertRaisesRegex(ValueError, 'complete zero-response'):
                prepare_senscalc_dy_histograms(components, directory / 'pending.root',
                    point='m1p8_lab30', campaign='overlap')
            self.assertFalse((directory / 'pending.root').exists())


class PopulationAccounting(unittest.TestCase):
    def reference(self,run,source_collisions,draws,rate):
        sample=population(run,2,draws,rate)
        record=sample['rate'];record.update(mechanism='senscalc-meson-fragmentation',rate_profile='pointlike',
            radiative_model_sha256='a'*64,source_graph_scope='isolated signal',polarization_model='isotropic',
            rate_scope='reference ND mesons and fragmentation',source_ledger_sha256=str(run).zfill(64),
            source_seed=run,source_sampling=dict(proposal_probability=.01,unweighting_envelope=4.),
            source_exposure=dict(source_collisions=source_collisions,fixed_source_collisions=source_collisions,accepted_signal_events=2))
        record['full_gen']['source_collisions']=source_collisions
        return sample

    def test_reference_uses_original_collisions_instead_of_bank_unweighting_draws(self):
        a=self.reference(10,100,999,2.);b=self.reference(20,300,3,6.)
        result=combined_rate([a,b],point='test',campaign='test')
        self.assertEqual(result['physical_selected_cross_sections_pb'][0]['value_pb'],5.)
        self.assertEqual(result['combination']['normalized_weights'],[.25,.75])
        self.assertEqual(result['full_gen']['source_collisions'],400)
        self.assertEqual(result['full_gen']['accepted_events'],4)
        self.assertEqual(result['rate_profile'],'pointlike')
        b['rate']['source_ledger_sha256']=a['rate']['source_ledger_sha256']
        with self.assertRaisesRegex(ValueError,'banks overlap'):
            combined_rate([a,b],point='test',campaign='test')

    def test_reference_rate_profiles_must_never_be_pooled_as_extra_exposure(self):
        a=self.reference(10,100,999,2.);b=self.reference(20,300,3,6.)
        b['rate']['rate_profile']='measured-form-factor'
        with self.assertRaisesRegex(ValueError,'identical physics'):
            combined_rate([a,b],point='test',campaign='test')

    def test_combined_sources_require_the_same_frozen_bremsstrahlung_prescription(self):
        a=self.reference(10,100,999,2.);b=self.reference(20,300,3,6.)
        for p in (a,b):
            p['rate']['mechanism']='senscalc-meson-fragmentation-bremsstrahlung'
            p['rate']['source_sampling'].update(bremsstrahlung_profile_sha256='b'*64,
                bremsstrahlung_sampler_sha256='c'*64)
        self.assertEqual(combined_rate([a,b],point='test',campaign='test')['full_gen']['source_collisions'],400)
        b['rate']['source_sampling']['bremsstrahlung_profile_sha256']='d'*64
        with self.assertRaisesRegex(ValueError,'identical physics'):
            combined_rate([a,b],point='test',campaign='test')

    def bremsstrahlung_population(self, directory, run, *, pretty=False, scale=1.):
        from dark_photon_bremsstrahlung import sampling_profile
        directory.mkdir()
        profile=sampling_profile(.3,lambda_p_gev=scale)
        path=directory/'bremsstrahlung.json'
        path.write_text(json.dumps(profile,sort_keys=pretty,indent=2 if pretty else None))
        sample=self.reference(run,100 if run==10 else 300,999,2. if run==10 else 6.)
        record=sample['rate'];record['mechanism']='senscalc-meson-fragmentation-bremsstrahlung'
        record['source_sampling'].update(bremsstrahlung_profile_sha256=sha(path),
                                         bremsstrahlung_sampler_sha256='c'*64)
        record['bremsstrahlung_profile_identity']=validated_bremsstrahlung_identity(
            directory,dict(bremsstrahlung_profile_sha256=sha(path)),record['source_sampling'])
        return sample

    def test_equivalent_profile_JSON_keeps_original_collision_weighting_and_raw_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            a=self.bremsstrahlung_population(Path(tmp)/'a',10)
            b=self.bremsstrahlung_population(Path(tmp)/'b',20,pretty=True)
            self.assertNotEqual(a['rate']['source_sampling']['bremsstrahlung_profile_sha256'],
                                b['rate']['source_sampling']['bremsstrahlung_profile_sha256'])
            result=combined_rate([a,b],point='test',campaign='test')
            self.assertEqual(result['combination']['normalized_weights'],[.25,.75])
            self.assertEqual(result['physical_selected_cross_sections_pb'][0]['value_pb'],5.)
            self.assertEqual(result['full_gen']['source_collisions'],400)
            self.assertEqual(result['full_gen']['sum_weights'],4.)
            self.assertEqual(result['source_sampling'],a['rate']['source_sampling'])
            self.assertEqual(result['source_sampling_physics_identity']['bremsstrahlung_profile_sha256'],
                             a['rate']['bremsstrahlung_profile_identity']['physics_sha256'])

    def test_canonical_profile_does_not_relax_physics_or_other_sampling_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            a=self.bremsstrahlung_population(Path(tmp)/'a',10)
            b=self.bremsstrahlung_population(Path(tmp)/'b',20,pretty=True)
            changed=self.bremsstrahlung_population(Path(tmp)/'changed',30,scale=.5)
            with self.assertRaisesRegex(ValueError,'identical physics'):
                combined_rate([a,changed],point='test',campaign='test')
            for key,value in [('unweighting_envelope',5.),('proposal_probability',.02),
                              ('settings_sha256','different'),('runtime_receipt_sha256','different'),
                              ('bremsstrahlung_sampler_sha256','different')]:
                with self.subTest(key=key):
                    candidate=copy.deepcopy(b);candidate['rate']['source_sampling'][key]=value
                    with self.assertRaisesRegex(ValueError,'identical physics'):
                        combined_rate([a,candidate],point='test',campaign='test')

    def test_profile_identity_requires_unchanged_pinned_profile_and_tables(self):
        from dark_photon_bremsstrahlung import TABLE_HASHES,sampling_profile
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);profile=sampling_profile(.3);tables=root/'tables';tables.mkdir()
            for name in TABLE_HASHES:shutil.copy2(Path(profile['table_directory'])/name,tables/name)
            profile['table_directory']='tables';path=root/'bremsstrahlung.json';path.write_text(json.dumps(profile))
            contract=dict(bremsstrahlung_profile_sha256=sha(path));sampling=dict(bremsstrahlung_profile_sha256=sha(path))
            validated_bremsstrahlung_identity(root,contract,sampling)
            (tables/next(iter(TABLE_HASHES))).write_text('corrupt')
            with self.assertRaises(ValueError):validated_bremsstrahlung_identity(root,contract,sampling)
            path.write_text('{}')
            with self.assertRaises(ValueError):validated_bremsstrahlung_identity(root,contract,sampling)

    def test_recovery_cannot_replace_an_empty_frozen_draw_with_detected_events(self):
        first=population();first.update(selected_event_ids=[],sampling_probabilities=[],rate_only=True)
        changed=copy.deepcopy(first)
        changed.update(selected_event_ids=changed['parent']['event_ids'],
                       sampling_probabilities=[1.,1.],rate_only=False)
        with self.assertRaisesRegex(ValueError,'Recovery views disagree'):
            unique_populations([first,changed])

    def test_meson_rates_use_fixed_source_exposure_and_keep_signal_counts_separate(self):
        first, second = population(10, 2, 900, 2., .1), population(20, 18, 100, 4., .2)
        for item, count, mean in ((first, 100, .01), (second, 300, .02)):
            rate = item['rate']
            rate.update(mechanism='eta-etaprime-radiative', meson_model_card={'pinned': 'same model'},
                rate_scope='non-diffractive pp only', corrections={'combined': mean},
                source_exposure=dict(fixed_source_collisions=count, source_collisions=count,
                    native_n_accepted=count, accepted_signal_events=rate['full_gen']['accepted_events'],
                    coefficient_sum=mean*count, coefficient_sum_squared=mean**2*count,
                    physical_probability_sum=mean*count, physical_probability_sum_squared=mean**2*count))
            rate['full_gen']['source_collisions'] = count
        result = combined_rate([first, second], point='meson', campaign='additive')
        self.assertEqual(result['combination']['normalized_weights'], [.25, .75])
        self.assertEqual(result['physical_selected_cross_sections_pb'][0]['value_pb'], 3.5)
        self.assertAlmostEqual(result['physical_selected_cross_sections_pb'][0]['error_pb'], math.hypot(.025, .15))
        self.assertEqual(result['full_gen']['accepted_events'], 20)
        self.assertEqual(result['full_gen']['source_collisions'], 400)
        self.assertEqual(result['full_gen']['n_tried'], 1000)
        self.assertEqual(result['source_exposure']['physical_probability_sum'], 7.)
        wrong = copy.deepcopy(second)
        wrong['rate']['source_exposure']['source_collisions'] = 18
        with self.assertRaisesRegex(ValueError, 'Meson source exposure'):
            combined_rate([first, wrong], point='meson', campaign='additive')
        wrong = copy.deepcopy(second)
        wrong['rate']['meson_model_card']['pinned'] = 'different model'
        with self.assertRaisesRegex(ValueError, 'identical physics'):
            combined_rate([first, wrong], point='meson', campaign='additive')
        with self.assertRaisesRegex(ValueError, 'identical physics'):
            combined_rate([first, population(30)], point='meson', campaign='additive')

    def test_unified_meson_repeats_use_exposure_and_reject_old_component(self):
        populations = [population(10, 2), population(20, 4)]
        for item, exposure, value in zip(populations, (100, 300), (2., 4.)):
            rate = item['rate']
            rate.update(mechanism='meson-radiative', meson_model_card={'channels': ['eta', 'omega']},
                rate_scope='one unified source', source_exposure=dict(fixed_source_collisions=exposure,
                    source_collisions=exposure, native_n_accepted=exposure,
                    accepted_signal_events=rate['full_gen']['accepted_events'],
                    coefficient_sum=exposure * value, coefficient_sum_squared=exposure * value**2,
                    physical_probability_sum=exposure * value,
                    physical_probability_sum_squared=exposure * value**2))
            rate['full_gen']['source_collisions'] = exposure
            rate['physical_selected_cross_sections_pb'][0]['value_pb'] = value
        result = combined_rate(populations, point='unified', campaign='repeat')
        self.assertEqual(result['physical_selected_cross_sections_pb'][0]['value_pb'], 3.5)
        self.assertEqual(result['full_gen']['accepted_events'], 6)
        old = copy.deepcopy(populations[1])
        old['rate']['mechanism'] = 'eta-etaprime-radiative'
        with self.assertRaisesRegex(ValueError, 'identical physics'):
            combined_rate([populations[0], old], point='unified', campaign='wrong')

    def test_recovery_views_count_the_same_gen_population_once(self):
        base, fresh = population(), population(20)
        recovery = copy.deepcopy(base)
        recovery['source']['campaign'] = 'recovery'
        rows = unique_populations([base, recovery, fresh])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['duplicate_views'][0]['campaign'], 'recovery')
        rate = combined_rate(rows, point='canonical', campaign='additive')
        self.assertEqual(rate['full_gen']['accepted_events'], 4)
        self.assertEqual(rate['parent_gen_ledger_count'], 2)

    def test_trial_weighted_cross_sections_are_not_summed(self):
        result = combined_rate([population(), population(20, 18, 300, 4., .2)],
                               point='canonical', campaign='additive')
        estimate = result['physical_selected_cross_sections_pb'][0]
        self.assertEqual(estimate['value_pb'], 3.5)
        self.assertAlmostEqual(estimate['error_pb'], math.hypot(.25 * .1, .75 * .2))
        self.assertEqual(result['combination']['normalized_weights'], [.25, .75])
        self.assertFalse(result['combination']['cross_sections_summed'])
        self.assertFalse(result['combination']['n_tried_is_luminosity'])
        self.assertEqual(result['full_gen']['accepted_events'], 20)
        self.assertEqual(result['full_gen']['n_tried'], 400)
        self.assertEqual(result['physical_inclusive_cross_sections_pb'][0]['value_pb'], 17.5)
        self.assertFalse(result['normalization_ready'])

    def test_independent_population_uid_run_and_seed_collisions_fail(self):
        for kind in ('identity', 'run', 'seed'):
            with self.subTest(kind=kind):
                base, fresh = population(), population(20)
                if kind == 'seed':
                    fresh['rate']['seed'] = base['rate']['seed']
                else:
                    fresh['parent']['event_ids'] = [[10, 1, 1 if kind == 'identity' else 3], [10, 1, 4]]
                    fresh['rate']['full_gen']['event_ids_sha256'] = hashlib.sha256(json.dumps(
                        fresh['parent']['event_ids'], separators=(',', ':')).encode()).hexdigest()
                with self.assertRaisesRegex(ValueError, 'reuse'):
                    unique_populations([base, fresh])

    def test_recovery_cannot_change_its_rate_or_full_gen_ledger(self):
        base = population()
        changed = copy.deepcopy(base)
        changed['rate']['physical_selected_cross_sections_pb'][0]['value_pb'] *= 2
        with self.assertRaisesRegex(ValueError, 'Recovery views disagree'):
            unique_populations([base, changed])

    def test_physical_points_and_proposals_remain_separate(self):
        for field in ('mass_gev', 'epsilon', 'proper_ctau_mm', 'sampling_epsilon'):
            with self.subTest(field=field):
                base, fresh = population(), population(20)
                fresh['rate'][field] *= 2
                with self.assertRaisesRegex(ValueError, 'Different physical points'):
                    combined_rate([base, fresh], point='point', campaign='run')
        fresh = population(20)
        fresh['rate']['corrections']['combined'] *= 2
        with self.assertRaisesRegex(ValueError, 'identical physics'):
            combined_rate([population(), fresh], point='point', campaign='run')

    def test_missing_per_run_trial_allocation_is_rejected(self):
        fresh = population(20)
        fresh['rate']['physical_selected_cross_sections_pb'].append(dict(value_pb=1., error_pb=.1))
        with self.assertRaisesRegex(ValueError, 'per-run trial ledger'):
            combined_rate([population(), fresh], point='point', campaign='run')

    def test_changed_full_gen_denominator_is_rejected(self):
        fresh = population(20)
        fresh['parent']['native_sumw'] = 1.
        with self.assertRaisesRegex(ValueError, 'denominator'):
            unique_populations([population(), fresh])


class Grouping(unittest.TestCase):
    def test_complete_ten_thousand_event_point_groups_before_scoring(self):
        inputs = [dict(path=str(index), events=size) for index, size in enumerate([1000, 2000, 2000, 2000, 2000, 1000])]
        groups = group_inputs(inputs)
        self.assertEqual(sum(group['events'] for group in groups), 10000)
        self.assertEqual([item for group in groups for item in group['inputs']], inputs)
        self.assertTrue(all(1000 <= group['events'] <= 4000 for group in groups))

    def test_small_points_keep_the_complete_available_population(self):
        self.assertEqual(group_inputs([dict(events=50), dict(events=450)])[0]['events'], 500)
        self.assertEqual(group_inputs([dict(events=100), dict(events=900)])[0]['events'], 1000)

    def test_huge_source_must_already_be_partitioned_and_zero_is_rejected(self):
        for size in (0, 9000):
            with self.assertRaisesRegex(ValueError, 'Input Nano'):
                group_inputs([dict(events=size)])

    def test_order_duplicates_and_nonunit_weights_are_rejected(self):
        expected = [[10, 1, 1], [20, 1, 3]]
        validate_event_rows(expected, [1., 1.], expected)
        for identities, weights in ((expected[::-1], [1., 1.]), (expected[:1] * 2, [1., 1.]),
                                    (expected, [1., 2.]), (expected, [1., float('nan')])):
            with self.assertRaises(ValueError):
                validate_event_rows(identities, weights, expected)


class DeploymentIntegrity(unittest.TestCase):
    def test_seal_verifies_code_as_well_as_the_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            plan, worker = directory / 'plan.json', directory / 'worker.py'
            plan.write_text('{}')
            worker.write_text('# synthetic sealed worker\n')
            seal = directory / 'seal.json'
            seal.write_text(json.dumps(dict(files={str(plan): sha(plan), str(worker): sha(worker)})))
            verify_aggregate_seal(plan, sha(plan), sha(seal))
            worker.write_text('# changed worker\n')
            with self.assertRaisesRegex(ValueError, 'Frozen aggregate input changed'):
                verify_aggregate_seal(plan, sha(plan), sha(seal))

    def test_exact_qualified_module_is_used_when_wrapper_has_no_validator(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            (directory / 'finalize_point.py').write_text('from dark_photon_production_support import finalize\n')
            (directory / 'qualified_finalizer.py').write_text('def validate_histogram_axes(*arguments):\n    return 1081\n')
            with patch.dict(sys.modules, finalize_point=SimpleNamespace(), qualified_finalizer=SimpleNamespace(
                    validate_histogram_axes=lambda *arguments: 0)):
                qualified = qualified_finalizer(directory)
            self.assertEqual(qualified.validate_histogram_axes(None, None), 1081)
            self.assertEqual(Path(qualified.__file__), directory / 'qualified_finalizer.py')


class HistogramOnly(unittest.TestCase):
    def test_clean_process_imports_the_qualified_finalizer_after_its_guard(self):
        with tempfile.TemporaryDirectory(prefix='20261016-aggregate-import-', dir='/tmp') as temporary:
            base = Path(temporary)
            deployment, guard = base / 'deployment', base / 'guard'
            scripts = deployment / 'frozen/scoring/workflow/scripts'
            scripts.mkdir(parents=True)
            guard.mkdir()
            marker = base / 'qualified_imported.txt'
            (scripts / 'run_merged_dimuon_bdt.py').write_text('io = None\n')
            (deployment / 'scoring_components.json').write_text(json.dumps(dict(model_sha256='b' * 64)))
            (guard / 'seal.json').write_text('{}\n')
            (guard / 'chunk_worker.py').write_text(
                "MARKER = 'reviewed publication guard'\n"
                'def verify_seal(*args, **kwargs): pass\n'
                'def prepare_publication_guard(*args): pass\n'
                'def check_headroom():\n'
                "    raise RuntimeError('reached the publication guard')\n")
            (deployment / 'qualified_finalizer.py').write_text(
                'from pathlib import Path\n'
                'from chunk_worker import MARKER\n'
                f'Path({str(marker)!r}).write_text(MARKER)\n'
                'def validate_histogram_axes(*args): return 1081\n')
            plan = dict(scoring_deployment=dict(path=str(deployment)), publication_guard=dict(
                deployment_path=str(guard), seal_sha256=sha(guard / 'seal.json'), point='point'))
            plan_path = base / 'plan.json'
            plan_path.write_text(json.dumps(plan))
            child = '''import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from aggregate_dark_photon_samples import execute_histogram_only
assert 'chunk_worker' not in sys.modules
plan = json.loads(Path(sys.argv[2]).read_text())
try:
    execute_histogram_only(plan, {}, [], {}, [], 'e'*64, 'f'*64,
                          Path(sys.argv[2]).parent, Path(sys.argv[2]).parent)
except RuntimeError as error:
    assert str(error) == 'reached the publication guard', str(error)
else:
    raise AssertionError('Expected the synthetic publication guard to stop execution')
'''
            result = subprocess.run([sys.executable, '-c', child,
                str(Path(__file__).resolve().parents[1] / 'scripts'), str(plan_path)],
                capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(marker.read_text(), 'reviewed publication guard')

    def source(self, value, path):
        value = copy.deepcopy(value)
        count = len(value['parent']['event_ids'])
        value['contract'] = dict(common=['frozen'], cp5=['frozen'], timing='fixed')
        value['inputs'][0].update(sha256='a' * 64, bytes=100)
        value['report'] = dict(final_output_base='/eos/user/t/test/darkphoton_point/source_' + str(count),
            score_histograms=dict(marker=dict(complete=True, events=count, selection_applied=False,
                model_sha256='b' * 64, pairs=count, published=dict(histograms=dict(
                    path='/eos/user/t/test/darkphoton_point/source_' + str(count) + '/histograms_merged/ntuple_0.root',
                    sha256=sha(path), bytes=path.stat().st_size)))))
        return value

    def test_incomplete_foreign_model_and_unpinned_source_histograms_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'source.root'
            path.write_bytes(b'synthetic publication')
            source = self.source(population(), path)
            self.assertEqual(completed_histogram_input(source, 'b' * 64)['events'], 2)
            for change in (dict(complete=False), dict(events=1), dict(selection_applied=True),
                           dict(model_sha256='c' * 64), dict(pairs=-1)):
                damaged = copy.deepcopy(source)
                damaged['report']['score_histograms']['marker'].update(change)
                with self.subTest(change=change), self.assertRaisesRegex(ValueError, 'Source histogram'):
                    completed_histogram_input(damaged, 'b' * 64)
            for change in (dict(path='/eos/user/t/test/foreign.root'), dict(bytes=0), dict(sha256='missing')):
                damaged = copy.deepcopy(source)
                damaged['report']['score_histograms']['marker']['published']['histograms'].update(change)
                with self.subTest(change=change), self.assertRaisesRegex(ValueError, 'Source histogram'):
                    completed_histogram_input(damaged, 'b' * 64)

    def run_merge(self, base, *, corrupt_source=False, zero_second=False, all_zero=False):
        # Load PyROOT before patch.dict restores the worker import inventory.
        import ROOT
        ROOT.TFile
        import numpy as np
        import uproot
        if shutil.which('hadd') is None:
            self.skipTest('The existing ROOT runtime with hadd is required')
        from uproot.writing.identify import to_TAxis, to_TH1x
        deployment, scratch = base / 'deployment', base / 'scratch'
        (deployment / 'frozen/scoring').mkdir(parents=True)
        scratch.mkdir()
        (deployment / 'frozen/scoring/sm_axis_authority.root').write_bytes(b'synthetic axis authority')
        (deployment / 'scoring_components.json').write_text(json.dumps(dict(model_sha256='b' * 64)))
        populations, remote, reads, writes, checks = [], {}, [], [], []
        for index, value in enumerate((population(), population(20, 8, 300, 4., .2))):
            path = base / ('source_' + str(index) + '.root')
            count = len(value['parent']['event_ids'])
            edges = np.asarray([0., 1., 4., 8.])
            with uproot.recreate(path) as root:
                for name in ('rawEventsCutFlow', 'cutFlow', 'diagnostic'):
                    values = np.asarray([2., float(count), 3., 4., 5.])
                    variances = np.asarray([4., float(count), 9., 16., 25.])
                    root[name] = to_TH1x(None, name, values, float(count), float(values.sum()),
                        float(variances.sum()), 0., 0., variances,
                        to_TAxis('xaxis', '', 3, 0., 8., edges))
                for category in ('','Good','Near-Both','Near-Far','Both-Both','Both-Far','Other'):
                    root['dimuon/ShiftDimuonVertex'+category+'_logMass']=(np.zeros(160),np.linspace(-1.,3.,161))
            source = self.source(value, path)
            item = source['report']['score_histograms']['marker']['published']['histograms']
            remote[item['path']] = path
            populations.append(source)
            if all_zero or (zero_second and index == 1):
                source.update(rate_only=True,selected_event_ids=[],inputs=[],material_selected=True)
                source['report'].update(events=0,histogram_sumw=0.,standard_cutflow_sumw=0.,rate_only=True)
        if corrupt_source:
            populations[0]['report']['score_histograms']['marker']['published']['histograms']['sha256'] = 'e' * 64

        def get_remote(source, destination):
            self.assertNotIn('/nanoAOD_', source)
            reads.append(source)
            shutil.copyfile(remote[source], destination)

        def put_remote(source, destination):
            self.assertNotIn('/nanoAOD_', destination)
            writes.append(destination)
            target = base / ('published_' + str(len(writes)))
            shutil.copyfile(source, target)
            remote[destination] = target

        def check_counts(path, count, weight):
            with uproot.open(path) as root:
                self.assertEqual(root['rawEventsCutFlow'].values()[0], count)
                self.assertEqual(root['cutFlow'].values()[0], weight)
            checks.append(count)

        io = SimpleNamespace(get_remote=get_remote, put_remote=put_remote,
            remote_exists=lambda path: path in remote, transfer=lambda arguments: None)
        scorer = SimpleNamespace(io=io, check_standard_histograms=check_counts)
        guard = SimpleNamespace(verify_seal=lambda *a, **k: None,
            prepare_publication_guard=lambda *a: None, check_headroom=lambda: None)
        qualified = SimpleNamespace(validate_histogram_axes=lambda *arguments: 1081)
        plan = dict(campaign='additive', histogram_only=True, conditions=dict(sha256='c' * 64),
            scoring_deployment=dict(path=str(deployment)), publication_guard=dict(deployment_path=str(deployment),
                seal_sha256='d' * 64, point='point'), points=[dict(point='point', events=10,
                final_output_base='/eos/user/t/test/darkphoton_point/additive',
                sources=[dict(index=0), dict(index=0), dict(index=1)])])
        if zero_second or all_zero:
            plan['points'][0].update(events=0 if all_zero else 2,full_gen_events=10)
        with patch('aggregate_dark_photon_samples.verify_aggregate_seal'), patch(
                'aggregate_dark_photon_samples.load_plan', return_value=plan), patch(
                'aggregate_dark_photon_samples.checked', side_effect=lambda path, digest: Path(path)), patch(
                'aggregate_dark_photon_samples.load_source', side_effect=lambda source, *args: populations[source['index']]), patch(
                'aggregate_dark_photon_samples.qualified_finalizer', return_value=qualified), patch.dict(
                sys.modules, run_merged_dimuon_bdt=scorer, chunk_worker=guard):
            report = execute(base / 'plan.json', 'e' * 64, 'f' * 64, 'point', base / 'cache', scratch, base / 'results')
        return report, remote, reads, writes, checks

    def test_empty_draw_adds_full_denominator_without_a_histogram_input(self):
        with tempfile.TemporaryDirectory() as temporary:
            report,remote,reads,writes,checks=self.run_merge(Path(temporary),zero_second=True)
            self.assertEqual(report['events'],2)
            self.assertEqual(report['full_gen_events'],10)
            self.assertEqual(report['union']['parent_gen_ledger_count'],2)
            self.assertEqual(checks,[2,2])
            rates=next(path for key,path in remote.items() if key.endswith('/cross_sections.json'))
            self.assertEqual(json.loads(rates.read_text())['full_gen']['accepted_events'],10)

    def test_all_empty_draws_finish_rate_only_without_histogram_publication(self):
        with tempfile.TemporaryDirectory() as temporary:
            report,remote,reads,writes,checks=self.run_merge(Path(temporary),all_zero=True)
            self.assertTrue(report['rate_only'])
            self.assertEqual(report['events'],0)
            self.assertEqual(report['full_gen_events'],10)
            self.assertEqual(report['full_gen_rate']['full_gen']['accepted_events'],10)
            self.assertEqual(writes,[])
            self.assertEqual(checks,[])

    def test_histograms_keep_flow_errors_and_full_ledgers_without_new_nano_files(self):
        import uproot
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            report, remote, reads, writes, checks = self.run_merge(base)
            self.assertEqual(checks, [2, 8, 10])
            self.assertEqual(report['events'], 10)
            self.assertTrue(report['histogram_only'])
            self.assertFalse(report['union']['new_nano_files_created'])
            self.assertEqual(report['union']['parent_gen_ledger_count'], 2)
            self.assertEqual(len(report['source_histograms']), 2)
            self.assertEqual(report['union']['groups'][0]['grouped']['path'], 'nano_10')
            self.assertTrue(report['histogram_merge']['flow_bins_checked'])
            self.assertTrue(report['histogram_merge']['sum_weights_squared_checked'])
            item = report['score_histograms']['marker']['published']['histograms']
            with uproot.open(remote[item['path']]) as root:
                self.assertEqual(list(root['diagnostic'].values(flow=True)), [4., 10., 6., 8., 10.])
                self.assertEqual(list(root['diagnostic'].variances(flow=True)), [8., 10., 18., 32., 50.])
                self.assertEqual(list(root['diagnostic'].axis().edges()), [0., 1., 4., 8.])
            rate = json.loads(remote[report['final_output_base'] + '/cross_sections.json'].read_text())
            self.assertEqual(rate['full_gen']['accepted_events'], 10)
            self.assertEqual(rate['physical_selected_cross_sections_pb'][0]['value_pb'], 3.5)
            self.assertFalse(rate['combination']['cross_sections_summed'])
            self.assertTrue((base / 'results/completed_points/point.json').is_file())
            self.assertEqual(len(writes), 5)
            self.assertEqual(writes[-1], report['final_output_base'] + '/metadata/point_receipt.json')

    def test_wrong_source_sha_prevents_any_publication(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            with self.assertRaisesRegex(ValueError, 'checksum/size'):
                self.run_merge(base, corrupt_source=True)
            self.assertFalse((base / 'results/completed_points/point.json').exists())
            self.assertFalse(any(base.glob('published_*')))


class SemanticRoot(unittest.TestCase):
    def test_real_root_identity_and_weight_content(self):
        try:
            import ROOT
        except ImportError:
            self.skipTest('ROOT is unavailable in this Python environment')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'nano.root'
            root = ROOT.TFile(str(path), 'RECREATE')
            tree = ROOT.TTree('Events', 'Events')
            run, lumi, event, weight = array('I', [10]), array('I', [1]), array('Q', [1]), array('f', [1.])
            for name, value, leaf in (('run', run, 'i'), ('luminosityBlock', lumi, 'i'),
                                      ('event', event, 'l'), ('genWeight', weight, 'F')):
                tree.Branch(name, value, name + '/' + leaf)
            tree.Fill()
            run[0], event[0] = 20, 3
            tree.Fill()
            tree.Write()
            root.Close()
            identities, weights = event_rows(path)
            self.assertEqual(identities, [[10, 1, 1], [20, 1, 3]])
            validate_event_rows(identities, weights, identities)

    def test_real_grouping_and_histogram_merge_precede_complete_publication(self):
        try:
            import ROOT
        except ImportError:
            self.skipTest('ROOT is unavailable in this Python environment')
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            scratch = base / 'scratch'
            scratch.mkdir()
            deployment = base / 'deployment'
            (deployment / 'frozen/scoring').mkdir(parents=True)
            authority = deployment / 'frozen/scoring/sm_axis_authority.root'
            authority.write_bytes(b'synthetic axis authority')
            (deployment / 'qualified_finalizer.py').write_text('def validate_histogram_axes(*arguments):\n    return 1081\n')
            (deployment / 'scoring_components.json').write_text(json.dumps(dict(model_sha256='b' * 64)))
            populations = [population(), population(20, 8, 300, 4., .2)]
            stages, remote = [], {}
            def nano(path, identities):
                root = ROOT.TFile(str(path), 'RECREATE')
                tree = ROOT.TTree('Events', 'Events')
                run, lumi, event, weight = array('I', [0]), array('I', [1]), array('Q', [0]), array('f', [1.])
                for name, value, leaf in (('run', run, 'i'), ('luminosityBlock', lumi, 'i'),
                                          ('event', event, 'l'), ('genWeight', weight, 'F')):
                    tree.Branch(name, value, name + '/' + leaf)
                for identity in identities:
                    run[0], lumi[0], event[0] = identity
                    tree.Fill()
                tree.Write()
                root.Close()
            for index, value in enumerate(populations):
                value['contract'] = dict(common=['frozen'], cp5=['frozen'], timing='fixed')
                chunks = [value['parent']['event_ids']] if index == 0 else [
                    value['parent']['event_ids'][:3], value['parent']['event_ids'][3:6], value['parent']['event_ids'][6:]]
                value['inputs'] = []
                for number, identities in enumerate(chunks):
                    path = base / f'input_{index}_{number}.root'
                    nano(path, identities)
                    item = dict(path='source_' + str(index) + '_' + str(number), sha256=sha(path),
                                bytes=path.stat().st_size, events=len(identities), event_ids=identities)
                    value['inputs'].append(item)
                    remote[item['path']] = path
            def get_remote(source, destination):
                shutil.copyfile(remote[source], destination)
            def put_remote(source, destination):
                target = base / ('published_' + str(len(remote)))
                shutil.copyfile(source, target)
                remote[destination] = target
                stages.append(('put', destination))
            io = SimpleNamespace(get_remote=get_remote, put_remote=put_remote,
                remote_exists=lambda path: path in remote, transfer=lambda arguments: None)
            def score_file(scoreplan, row, work, cache, analysis, seal):
                self.assertEqual(sum('/nanoAOD_merged_unscored/' in path for path in remote), 2)
                stages.append(('score', row['index']))
                work.mkdir()
                hist = work / 'histograms.root'
                root = ROOT.TFile(str(hist), 'RECREATE')
                for name in ('rawEventsCutFlow', 'cutFlow'):
                    histogram = ROOT.TH1D(name, name, 2, 0., 2.)
                    histogram.Sumw2()
                    histogram.SetBinContent(1, row['expected_events'])
                    histogram.SetBinError(1, math.sqrt(row['expected_events']))
                    histogram.Write()
                root.mkdir('dimuon').cd()
                for category in ('','Good','Near-Both','Near-Far','Both-Both','Both-Far','Other'):
                    histogram=ROOT.TH1D('ShiftDimuonVertex'+category+'_logMass','',160,-1.,3.)
                    histogram.Sumw2();histogram.Write()
                root.Close()
                io.put_remote(hist, row['output_histograms'])
                item = dict(path=row['output_histograms'], sha256=sha(hist), bytes=hist.stat().st_size)
                return dict(complete=True, index=row['index'], marker=dict(complete=True,
                    events=row['expected_events'], pairs=0, published=dict(histograms=item)))
            def check_histograms(path, count, weight):
                root = ROOT.TFile.Open(str(path))
                self.assertEqual(root.Get('rawEventsCutFlow').GetBinContent(1), count)
                self.assertEqual(root.Get('cutFlow').GetBinContent(1), weight)
                root.Close()
            def check_merge(inputs, merged):
                self.assertEqual(len(inputs), 2)
                check_histograms(merged, 10, 10.)
                root = ROOT.TFile.Open(str(merged))
                self.assertAlmostEqual(root.Get('cutFlow').GetBinError(1)**2, 10.)
                root.Close()
                return dict(complete=True, sum_weights_squared_checked=True)
            scorer = SimpleNamespace(io=io, prepare_analysis=lambda *arguments: base / 'analysis',
                run_file=score_file, check_standard_histograms=check_histograms)
            guard = SimpleNamespace(verify_seal=lambda *a, **k: None, prepare_publication_guard=lambda *a: None,
                                    check_headroom=lambda: stages.append(('quota', None)))
            plan = dict(campaign='additive', group_events=5, conditions=dict(sha256='c' * 64),
                scoring_deployment=dict(path=str(deployment)), publication_guard=dict(deployment_path=str(deployment),
                    seal_sha256='d' * 64, point='point'), points=[dict(point='point', events=10,
                    final_output_base='/eos/user/t/test/darkphoton_point/additive', sources=[dict(index=0), dict(index=1)])])
            (base / 'plan.json').write_text(json.dumps(plan))
            (base / 'seal.json').write_text(json.dumps(dict(files={str(base / 'plan.json'): 'e' * 64})))
            from dark_photon_production_support import validate_standard_histograms
            with patch('aggregate_dark_photon_samples.load_plan', return_value=plan), patch(
                    'aggregate_dark_photon_samples.checked', side_effect=lambda path, digest: Path(path)), patch(
                    'aggregate_dark_photon_samples.load_source', side_effect=lambda source, *args: populations[source['index']]), patch.dict(
                    sys.modules, run_merged_dimuon_bdt=scorer, chunk_worker=guard, finalize_point=SimpleNamespace(),
                    dark_photon_production_support=SimpleNamespace(validate_histogram_merge=check_merge,
                        validate_standard_histograms=validate_standard_histograms)):
                report = execute(base / 'plan.json', 'e' * 64, 'f' * 64, 'point', base / 'cache', scratch, base / 'results')
            self.assertTrue(report['complete'])
            self.assertEqual(report['events'], 10)
            self.assertEqual(report['union']['parent_gen_ledger_count'], 2)
            self.assertEqual([group['events'] for group in report['union']['groups']], [5, 5])
            self.assertEqual(report['union']['event_ids'], populations[0]['parent']['event_ids'] + populations[1]['parent']['event_ids'])
            self.assertTrue((base / 'results/completed_points/point.json').is_file())
            self.assertEqual(stages[-1], ('put', '/eos/user/t/test/darkphoton_point/additive/metadata/point_receipt.json'))


if __name__ == '__main__':
    unittest.main()

class RelocatedDirectGenSource(unittest.TestCase):
    """Retained DY logs may move; their content and full exposure may not."""
    def fixture(self, root, change=None, prescription=None):
        from aggregate_dark_photon_samples import write
        from pythia_generation_ledger import read_pythia_generation_ledger
        from test_pythia_generation_ledger import table
        directory=root/'production';gen=root/'retained_GEN';gen.mkdir();directory.mkdir()
        identities=[[1762350000,1,1],[1762350000,1,2]]
        model=dict(mass_gev=2.,epsilon=.001,proper_ctau_mm=9.7e-6,sampling_epsilon=.01)
        row=dict(model,point='m2_prompt',events=2,full_gen_events=2,gen_directory=str(gen),
            final_output_base='/eos/user/test/darkphoton_m2_prompt/example')
        (gen/'cmsRun.log').write_text(table(
            ['f fbar -> gamma*/Z0/Zprime0 3001 | 100 2 2 | 6.984e-11 3.247e-12'],
            '100 2 2 | 6.984e-11 3.247e-12'))
        ledger=read_pythia_generation_ledger(gen/'cmsRun.log',expected_accepted=2)
        ledger['log_path']='/pool/condor/dir_12345/dp-private-GEN-example/gen/cmsRun.log'
        audit=dict(event_ids=identities,sum_weights=2.,sum_weights_squared=2.,proposal_ledger=ledger)
        manifest=dict(output_sha256='a'*64)
        if change=='counter':audit['proposal_ledger']['n_tried']+=1
        elif change=='log_hash':audit['proposal_ledger']['log_sha256']='b'*64
        elif change=='sumw':audit['sum_weights']=3.
        elif change=='sumw2':audit['sum_weights_squared']=3.
        elif change=='IDs':audit['event_ids']=[[1762350000,1,2],[1762350000,1,1]]
        elif change=='GEN_hash':manifest['output_sha256']='b'*64
        contract=dict(production_mechanism='pure-on-shell-DY')
        if prescription is not None:contract['dy_prescription']=prescription
        write(gen/'contract.json',contract)
        write(gen/'validation.json',audit);write(gen/'manifest.json',manifest)
        parent=dict(events=2,event_ids=identities,native_sumw=2.,native_sumw2=2.,gen_sha256='a'*64,
            gen_path='/eos/user/test/GEN.root',**{key:sha(gen/name) for name,key in [
                ('contract.json','contract_sha256'),('validation.json','validation_sha256'),('manifest.json','manifest_sha256')]})
        write(directory/'gen_inventory.json',dict(complete=True,points={'m2_prompt':parent}))
        write(directory/'plan.json',dict(campaign='example',points=[row]))
        write(directory/'seal.json',dict(files={str(directory/name):sha(directory/name)
            for name in ('plan.json','gen_inventory.json')}))
        source=dict(plan_path=str(directory/'plan.json'),plan_sha256=sha(directory/'plan.json'),
            seal_sha256=sha(directory/'seal.json'),point='m2_prompt',conditions_sha256='c'*64)
        write(directory/'completed_points/m2_prompt.json',dict(complete=True,point='m2_prompt',events=2,
            plan_sha256=source['plan_sha256'],seal_sha256=source['seal_sha256'],final_output_base=row['final_output_base'],
            union=dict(complete=True,parent_gen_ledger_count=1,parent_gen=parent,event_ids=identities,
                native_sumw=2.,native_sumw2=2.,grouped=dict(path=row['final_output_base']+'/nanoAOD_merged_unscored/ntuple_0.root'))))
        return source,model,identities

    def test_retained_direct_log_relocation_preserves_full_source_checks(self):
        from aggregate_dark_photon_samples import load_source
        with tempfile.TemporaryDirectory() as temporary:
            source,model,identities=self.fixture(Path(temporary))
            with patch('aggregate_dark_photon_samples.rate_record',return_value={'validated_elsewhere':True}) as rate:
                population=load_source(source,model,'c'*64)
            rate.assert_called_once()
            self.assertEqual(population['parent']['events'],2)
            self.assertEqual(population['selected_event_ids'],identities)
            self.assertEqual(population['inputs'][0]['event_ids'],identities)
            self.assertTrue(population['contract']['production_mechanism']=='pure-on-shell-DY')

    def test_moved_direct_source_still_rejects_physics_and_hash_changes(self):
        from aggregate_dark_photon_samples import load_source
        for change in ('counter','log_hash','sumw','sumw2','IDs','GEN_hash'):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as temporary:
                source,model,_=self.fixture(Path(temporary),change)
                with patch('aggregate_dark_photon_samples.rate_record') as rate:
                    with self.assertRaisesRegex(ValueError,'Source full GEN audit'):
                        load_source(source,model,'c'*64)
                    rate.assert_not_called()

    def test_new_contract_cannot_use_an_old_exporter_that_drops_its_DY_prescription(self):
        from aggregate_dark_photon_samples import load_source
        with tempfile.TemporaryDirectory() as temporary:
            source,model,_=self.fixture(Path(temporary),prescription={'schema':'synthetic-new-prescription'})
            with patch('aggregate_dark_photon_samples.rate_record',return_value={'legacy_exporter':True}):
                with self.assertRaisesRegex(ValueError,'complete helper closure'):
                    load_source(source,model,'c'*64)
