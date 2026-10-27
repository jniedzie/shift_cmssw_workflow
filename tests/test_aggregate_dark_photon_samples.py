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
                                         completed_histogram_input, event_rows, execute, group_inputs,
                                         qualified_finalizer, sha, unique_populations,
                                         validate_event_rows, verify_aggregate_seal)


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
    def fixture(self, root, change=None):
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
        write(gen/'contract.json',dict(production_mechanism='pure-on-shell-DY'))
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
