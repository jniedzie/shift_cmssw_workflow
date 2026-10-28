# Code style: AI-code. AI-generated tests of closed-source SM publication.
import sys
from pathlib import Path
import unittest
import tempfile
import json
import errno
import copy
import math
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from run_shift_sm_postprocessing import exposure, selected_rows, hadd_command, validate_dimuon_histograms
from run_shift_sm_postprocessing_controller import closed_sources, meaningful_update
from run_shift_smart_sm_controller import postprocessing_charge
from run_shift_smart_sm_source import accepted_class_authority
import run_shift_smart_sm_controller as main_controller


class PostprocessingTest(unittest.TestCase):
    def test_publication_requires_categories_beyond_inclusive_and_both(self):
        class Root:
            def Get(self, name):
                return name not in ('dimuon/ShiftDimuonVertexOther_logMass',
                                    'dimuon/ShiftDimuonVertexNear-Far_mass')
        with self.assertRaisesRegex(ValueError, 'Other_logMass'):
            validate_dimuon_histograms(Root())

    def test_empty_booked_categories_are_valid_for_a_production_chunk(self):
        class EmptyHistogram:
            def GetEntries(self):
                return 0
        class Root:
            def Get(self, name):
                return EmptyHistogram()
        self.assertEqual(len(validate_dimuon_histograms(Root())), 21)

    def test_larger_hadd_file_limit_preserves_input_order_and_compression(self):
        self.assertEqual(hadd_command('out.root', ['a.root', 'b.root'], 200),
            ['hadd', '-fk', '-n', '200', 'out.root', 'a.root', 'b.root'])
        with self.assertRaises(ValueError):
            hadd_command('out.root', ['a.root'], 0)

    def source(self, checksum, count=100):
        return dict(stratum='jpsi_5to10', source_id=checksum, gen_sha256=checksum,
                    denominator_kind='fixed_source_trials', full_source_trials=count,
                    sigma_nd_pb=123., physics_normalization_ready=False,
                    forced_decay_branching_convention_ready=False)

    def test_full_exposure_includes_zero_admission_sources(self):
        result = exposure([self.source('a'), self.source('b', 200)], 'jpsi_5to10')
        self.assertEqual(result['full_source_sumw'], 300)
        self.assertEqual(result['cross_section_pb'], 123.)
        self.assertFalse(result['physics_normalization_ready'])
        self.assertFalse(result['forced_decay_branching_convention_ready'])

    def test_original_gen_cannot_be_counted_twice(self):
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            exposure([self.source('a'), self.source('a')], 'jpsi_5to10')

    def test_accepted_event_cross_sections_keep_their_denominator(self):
        sources = []
        for key, count, rate in [('a', 2, 10.), ('b', 6, 30.)]:
            sources.append(dict(source_id=key, gen_sha256=key, stratum='dy_1to2',
                denominator_kind='accepted_gen_events', full_source_events=count,
                native_cross_section_pb=rate))
        result = exposure(sources, 'dy_1to2')
        self.assertEqual(result['full_source_sumw'], 8)
        self.assertEqual(result['cross_section_pb'], 25.)

    def accepted_class_source(self, key, count, trials):
        sigma_nd = 1200.
        rate, error = sigma_nd*count/trials, sigma_nd*math.sqrt(count)/trials
        source = dict(source_id=key, gen_sha256=key*64, stratum='jpsi_0to1',
            source_family='jpsi', bounds=[0., 1.], denominator_kind='accepted_gen_events',
            full_source_events=count, native_cross_section_pb=rate,
            native_cross_section_error_pb=error)
        audit = dict(events=count, accepted_events=count, framework_requested_events=count,
            generator_failed_framework_slots=0, sum_weights=count, sum_weights_squared=count,
            generated_filter_efficiency=1., hard_process_codes={'101': count},
            event_class='direct_jpsi', configured_pthat_bounds=[0., 1.],
            input_sha256=key*64, mpi_model_settings_sha256='c'*64,
            mpi_model_contract='pythia8-softqcd-nd-cp5-processlevel3-v1',
            partition_contract='direct-hard-jpsi-status23or33-v1',
            pythia_process_statistics=dict(tried=trials, selected=trials, accepted=count,
                sigma_pb=rate, error_pb=error),
            runs=[dict(internal_xsec_pb=rate, error_pb=error)])
        source['native_class_normalization'] = accepted_class_authority(source, audit, 'd'*64)
        return source, audit

    def test_accepted_class_pools_native_trials_keeps_accepted_denominator(self):
        first, _ = self.accepted_class_source('a', 2, 100)
        second, _ = self.accepted_class_source('b', 6, 600)
        result = exposure([first, second], 'jpsi_0to1')
        self.assertEqual(result['full_source_sumw'], 8)
        self.assertAlmostEqual(result['cross_section_pb'], 1200.*8/700)
        self.assertNotAlmostEqual(result['cross_section_pb'], (2*24.+6*12.)/8)
        self.assertEqual(result['native_pythia_trials'], 700)
        regrouped, _ = self.accepted_class_source('e', 8, 700)
        combined = exposure([regrouped], 'jpsi_0to1')
        self.assertAlmostEqual(result['cross_section_pb'], combined['cross_section_pb'])
        self.assertAlmostEqual(result['cross_section_error_pb'], combined['cross_section_error_pb'])

    def test_accepted_class_rejects_missing_or_incompatible_native_authority(self):
        first, _ = self.accepted_class_source('a', 2, 100)
        second, _ = self.accepted_class_source('b', 6, 600)
        for change in [dict(pythia_tried=6), dict(pythia_accepted=5),
                       dict(mpi_model_settings_sha256='e'*64), dict(sigma_nd_pb=1300.),
                       dict(event_class='qcd'), dict(bounds=[1., 2.])]:
            changed = copy.deepcopy(second)
            changed['native_class_normalization'].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                exposure([first, changed], 'jpsi_0to1')
        legacy = copy.deepcopy(second)
        del legacy['native_class_normalization']
        with self.assertRaisesRegex(ValueError, 'Exact native Pythia'):
            exposure([first, legacy], 'jpsi_0to1')
        # One legacy source needs no pooling: retain its exact native authority.
        self.assertEqual(exposure([legacy], 'jpsi_0to1')['cross_section_pb'],
                         legacy['native_cross_section_pb'])
        incompatible = copy.deepcopy(second)
        incompatible['native_cross_section_pb'] *= 2.
        incompatible['native_class_normalization']['sigma_nd_pb'] *= 2.
        with self.assertRaisesRegex(ValueError, 'incompatible native model or ND'):
            exposure([first, incompatible], 'jpsi_0to1')

    def test_native_class_authority_requires_the_exact_complete_unit_source_audit(self):
        source, audit = self.accepted_class_source('a', 5, 142017)
        for change in [dict(input_sha256='f'*64), dict(generator_failed_framework_slots=1),
                       dict(sum_weights=4.), dict(generated_filter_efficiency=.8),
                       dict(framework_requested_events=6)]:
            changed = copy.deepcopy(audit)
            changed.update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                accepted_class_authority(source, changed, 'd'*64)

    def test_signal_histograms_keep_conditional_gen_denominator_and_physical_rate(self):
        source=dict(source_id='signal',gen_sha256='a',stratum='darkphoton_m0p7_lab_100m',
            denominator_kind='conditional_signal_events',full_source_events=141,
            physical_dimuon_cross_section_pb=1.5e-5,
            signal_rate_record=dict(full_gen=dict(accepted_events=141,sum_weights=141.),
                physical_selected_cross_sections_pb=[dict(value_pb=1.5e-5)],
                source_exposure=dict(source_collisions=10000)))
        result=exposure([source],source['stratum'])
        self.assertEqual(result['full_source_sumw'],141)
        self.assertEqual(result['cross_section_pb'],1.5e-5)
        self.assertTrue(result['no_second_filter_efficiency'])
        with self.assertRaisesRegex(ValueError,'source-aware aggregator'):
            exposure([source,dict(source,gen_sha256='b')],source['stratum'])
        source['physical_dimuon_cross_section_pb']*=.4
        with self.assertRaisesRegex(ValueError,'full source-rate record'):
            exposure([source],source['stratum'])

    def test_partial_source_waits_but_zero_selected_source_closes(self):
        sources = [dict(descriptor_hashes=['a'], normalization=self.source('a'),
                        selected=dict(event_ids=[[1, 1, 1], [1, 1, 2]])),
                   dict(descriptor_hashes=['b'], normalization=self.source('b'),
                        selected=dict(event_ids=[]))]
        nanos = [dict(source_descriptor_sha256='a', path='one.root', events=1)]
        self.assertEqual([s['normalization']['source_id'] for s in
                          closed_sources(sources, nanos)['jpsi_5to10']], ['b'])
        nanos.append(dict(source_descriptor_sha256='a', path='two.root', events=1))
        self.assertEqual(len(closed_sources(sources, nanos)['jpsi_5to10']), 2)

    def test_selected_weights_use_selected_position_probability(self):
        rows = selected_rows(dict(event_ids=[[1, 1, 1], [1, 1, 2]], weights=[3., 7.],
                                  selected_indices=[1], sampling_probabilities=[0.1]))
        self.assertEqual(rows, [dict(event_id=[1, 1, 2], native_weight=7., sampling_probability=0.1)])

    def test_remerge_needs_meaningful_new_stats(self):
        self.assertFalse(meaningful_update(list(range(100)), list(range(101))))
        self.assertTrue(meaningful_update(list(range(100)), list(range(110))))
        self.assertTrue(meaningful_update([], ['first']))
        with self.assertRaises(ValueError):
            meaningful_update(['missing'], ['other'])

    def test_parent_storage_charge_includes_downstream_committed_and_pending(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'config.json').write_text(json.dumps(dict(worker_publication_bound_bytes=100)))
            (root / 'state.json').write_text(json.dumps(dict(tasks={
                'done': dict(phase='done', published_bytes=17),
                'running': dict(phase='submitted'), 'uncertain': dict(phase='submitting'),
                'not_yet_admitted': dict(phase='ready')})))
            self.assertEqual(postprocessing_charge(dict(postprocessing_directory=str(root))), (17, 200))
        self.assertEqual(postprocessing_charge({}), (0, 0))

    def test_AFS_outage_retries_the_same_serialized_checkpoint(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'state.json'
            payload = dict(value=1)
            original_open = Path.open
            failures = []

            def interrupted_open(current, mode='r', *args, **kwargs):
                if mode == 'wb' and len(failures) < 3:
                    failures.append(current)
                    raise OSError(errno.EIO, 'AFS unavailable')
                return original_open(current, mode, *args, **kwargs)

            with patch.object(Path, 'open', interrupted_open), patch.object(
                    main_controller.time, 'sleep', side_effect=lambda seconds: payload.update(value=99)):
                main_controller.save(path, payload)
            self.assertEqual(json.loads(path.read_text()), dict(value=1))
            self.assertEqual(len(failures), 3)

    def test_permission_error_is_not_hidden_as_AFS_outage(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(
                Path, 'open', side_effect=PermissionError(errno.EACCES, 'permission denied')):
            with self.assertRaises(PermissionError):
                main_controller.save(Path(temporary) / 'state.json', dict(value=1))


if __name__ == '__main__':
    unittest.main()
