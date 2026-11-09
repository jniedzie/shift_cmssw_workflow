# Code style: AI-code.
"""Exercise the actual source-to-detector handoff of two-muon enrichment."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from test_shift_sm_candidate_reco import event
from run_shift_smart_sm_source import prepared_selection
from shift_production_selection import configure_transport, validate_contract


class QCDSourceFilterTest(unittest.TestCase):
    def setUp(self):
        self.events = [event(1, depth=1.69), event(2, depth=20.), event(3, x=5000.)]
        self.ids = [row['event_id'] for row in self.events]
        self.weights = [row['native_weight'] for row in self.events]
        self.source = dict(source_id='supply_qcd_20toinf_test', stratum='qcd_20toinf',
            source_family='qcd_ordinary', generator_profile='ordinary',
            gen_sha256='a'*64, saved_events=3, denominator_kind='fixed_source_trials',
            full_source_trials=1000, sigma_nd_pb=100.,
            original_receipt='/eos/user/j/jniedzie/shift_cmssw_support/source.json',
            original_receipt_sha256='d'*64)
        self.prepared = dict(url='root://eoscms.cern.ch//eos/cms/store/group/phys_exotica/'
            'xtracks/jeremi/shift_cmssw/support/test/gen.root', gen_sha256='b'*64)
        self.request = dict(source_request=dict(family='qcd', stratum='qcd_20toinf',
            id=self.source['source_id'], seed=123), cohort_id='qcd-filter-test',
            selection_seed='frozen-test', nano_base='/eos/user/j/jniedzie/shift_cmssw_support/test/nano',
            metadata_base='/eos/user/j/jniedzie/shift_cmssw_support/test/metadata')

    def select(self, request=None, events=None):
        return prepared_selection(request or self.request, self.source, self.prepared,
            dict(events=self.events if events is None else events), self.ids, self.weights, 'e'*64)

    def test_source_defaults_to_enrichment_without_second_material_draw(self):
        original = copy.deepcopy(self.source)
        with patch('run_shift_smart_sm_source.prepare_default_selection',
                   side_effect=AssertionError('QCD must not receive the old extra draw')):
            selected, ledger = self.select()
        self.assertIn(0, selected['selected_indices'])
        self.assertEqual(selected['sampling_probabilities'][selected['selected_indices'].index(0)], 1.)
        self.assertEqual(selected['normalization_record'], original)
        self.assertEqual(self.source, original)
        self.assertEqual(selected['event_ids'], self.ids)
        self.assertEqual(selected['weights'], self.weights)
        self.assertEqual(ledger['normalization']['filtered_cross_section_pb'], .1)
        self.assertEqual(ledger['normalization']['original_denominator'], 1000)
        self.assertFalse(selected['qcd_gen_filter']['filter_efficiency_applied_to_event_weights'])
        self.assertEqual(selected['production_selection']['policy']['rejected_controls_per_primary'], .25)
        validate_contract(selected['production_selection'])

    def test_same_contract_keeps_physical_hadron_transport_and_reco_untouched(self):
        selected, _ = self.select()
        generator = SimpleNamespace(AlwaysTrackDecaysTo=[32])
        physics = SimpleNamespace(type='unchanged-physics-list')
        reco = object()
        process = SimpleNamespace(g4SimHits=SimpleNamespace(Generator=generator, Physics=physics),
                                  reconstruction=reco)
        cms = SimpleNamespace(untracked=SimpleNamespace(bool=bool, double=float))
        parameter_set = SimpleNamespace(Config=cms)
        with patch.dict(sys.modules, {'FWCore': SimpleNamespace(ParameterSet=parameter_set),
                'FWCore.ParameterSet': parameter_set, 'FWCore.ParameterSet.Config': cms}):
            configure_transport(process, selected['production_selection'])
        self.assertTrue(generator.TrackAllHadronDecays)
        self.assertEqual(generator.MaxUntrackedHadronFlightMm, 1.e-5)
        self.assertEqual(generator.AlwaysTrackDecaysTo, [32])
        self.assertIs(process.g4SimHits.Physics, physics)
        self.assertIs(process.reconstruction, reco)

    def test_changed_or_incomplete_census_cannot_become_new_exposure(self):
        with self.assertRaisesRegex(ValueError, 'complete ordered original census'):
            self.select(events=self.events[:2])
        changed = copy.deepcopy(self.events)
        changed[0]['native_weight'] = 12.
        with self.assertRaisesRegex(ValueError, 'complete ordered original census'):
            self.select(events=changed)

    def test_raw_reuse_needs_first_sim_declaration(self):
        request = dict(self.request, resume_raw=dict(url='unused', sha256='d'*64))
        with self.assertRaisesRegex(ValueError, 'first-SIM'):
            self.select(request=request)
        request['first_sim_source'] = True
        selected, _ = self.select(request=request)
        self.assertEqual(selected['normalization_record'], self.source)

    def test_zero_admission_cohort_still_contributes_full_histogram_exposure(self):
        from run_shift_sm_postprocessing import exposure, selected_rows
        from run_shift_sm_postprocessing_controller import closed_sources, compact_source
        selected, _ = self.select()
        self.assertEqual(selected['selected_indices'], [0])
        other_events = [event(10, depth=20.), event(11, depth=20.), event(12, depth=20.)]
        other_source = dict(self.source, source_id='supply_qcd_20toinf_zero', gen_sha256='f'*64)
        for row in other_events:
            row.update(source_id=other_source['source_id'], original_source_sha256='f'*64,
                       source_file_sha256='c'*64)
        other_prepared = dict(self.prepared, gen_sha256='c'*64)
        other, _ = prepared_selection(self.request, other_source, other_prepared,
            dict(events=other_events), [e['event_id'] for e in other_events],
            [e['native_weight'] for e in other_events], 'e'*64)
        self.assertEqual(other['selected_indices'], [])
        cached = [dict(normalization=original['normalization_record'],
            selected=compact_source(original), descriptor_hashes=[tag])
            for original, tag in [(selected, 'positive'), (other, 'zero')]]
        closed = closed_sources(cached, [dict(source_descriptor_sha256='positive',
            path='saved_nano.root', events=1)])['qcd_20toinf']
        self.assertEqual(len(closed), 2)
        normal = exposure([row['normalization'] for row in closed], 'qcd_20toinf')
        self.assertEqual(normal['full_source_sumw'], 2000)
        self.assertEqual(normal['cross_section_pb'], 100.)
        contribution = sum(r['native_weight']/r['sampling_probability']
                           for group in closed for r in selected_rows(group['selected']))
        self.assertEqual(contribution*normal['cross_section_pb']/normal['full_source_sumw'], .05)

    def test_ordinary_publication_retains_filter_counters_without_rescaling(self):
        from run_shift_sm_postprocessing import exposure
        from run_shift_sm_postprocessing_controller import source_cache
        selected, _ = self.select()
        result = dict(source=self.source, selected_source=selected,
                      qcd_gen_filter=selected['qcd_gen_filter'])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'result.json'
            path.write_text(json.dumps(result))
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            state = dict(sources={})
            parent = dict(tasks=dict(source=dict(kind='source', phase='done',
                result_sha256=digest, directory=directory)))
            source_cache(parent, state)
        record = state['sources'][digest]['normalization']
        normal = exposure([record], self.source['stratum'])
        self.assertEqual(normal['cross_section_pb'], self.source['sigma_nd_pb'])
        self.assertEqual(normal['full_source_sumw'], self.source['full_source_trials'])
        self.assertEqual(normal['original_sources'][0]['qcd_gen_filter'], selected['qcd_gen_filter'])
        self.assertEqual(self.source['gen_sha256'], record['gen_sha256'])


if __name__ == '__main__':
    unittest.main()
