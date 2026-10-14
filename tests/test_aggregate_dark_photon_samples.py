# Code style: AI-code. AI-generated additive signal population regression tests.
"""Exercise GEN deduplication, rate accounting and ordered semantic Nano checks."""
from array import array
import copy
import hashlib
import json
import math
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from aggregate_dark_photon_samples import (combined_rate, event_rows, execute, group_inputs,
                                         sha, unique_populations, validate_event_rows)


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


class PopulationAccounting(unittest.TestCase):
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
            qualified = SimpleNamespace(validate_histogram_axes=lambda *arguments: stages.append(('axes', None)))
            plan = dict(campaign='additive', group_events=5, conditions=dict(sha256='c' * 64),
                scoring_deployment=dict(path=str(deployment)), publication_guard=dict(deployment_path=str(deployment),
                    seal_sha256='d' * 64, point='point'), points=[dict(point='point', events=10,
                    final_output_base='/eos/user/t/test/darkphoton_point/additive', sources=[dict(index=0), dict(index=1)])])
            with patch('aggregate_dark_photon_samples.load_plan', return_value=plan), patch(
                    'aggregate_dark_photon_samples.checked', side_effect=lambda path, digest: Path(path)), patch(
                    'aggregate_dark_photon_samples.load_source', side_effect=lambda source, *args: populations[source['index']]), patch.dict(
                    sys.modules, run_merged_dimuon_bdt=scorer, chunk_worker=guard, finalize_point=qualified,
                    dark_photon_production_support=SimpleNamespace(validate_histogram_merge=check_merge)):
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
