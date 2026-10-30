# Code style: AI-code. AI-generated automatic-admission safety checks.
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/dark_photon_automatic_admission.py'
SPEC = importlib.util.spec_from_file_location('automatic_admission', SCRIPT)
admission = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(admission)


class AutomaticAdmissionTest(unittest.TestCase):
    def test_reference_source_gate_uses_reference_runtime_and_rejects_a_changed_capsule(self):
        import sys
        sys.path.insert(0, str(SCRIPT.parent))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            batch = dict(batch='source', mass_gev=.7)
            gen = root / 'batches/source/gen'
            gen.mkdir(parents=True)
            (gen.parent/'source_status.json').write_text(json.dumps(dict(complete=True, metadata_sha256={}, publication={})))
            (gen/'contract.json').write_text(json.dumps(dict(source_placement=dict(profile_sha256='jaw'))))
            (gen/'validation.json').write_text('{}')
            manifest = dict(detector_runtime_profile=dict(runtime_sha256='reference-runtime'))
            (gen/'manifest.json').write_text(json.dumps(manifest))
            config = dict(directory=str(root), production_mechanism='senscalc-meson-fragmentation-bremsstrahlung',
                          detector_runtime=dict(runtime_sha256='reference-runtime'), jaw_source_profile=dict(sha256='jaw'))
            with patch('run_dark_photon_meson_production.qualify_gen_source', return_value=dict(complete=True)) as qualify:
                self.assertTrue(admission.source_evidence(config, batch)['complete'])
                qualify.assert_called_once()
                manifest['detector_runtime_profile']['runtime_sha256'] = 'wrong-runtime'
                (gen/'manifest.json').write_text(json.dumps(manifest))
                with self.assertRaisesRegex(ValueError, 'runtime or source placement'):
                    admission.source_evidence(config, batch)
                qualify.assert_called_once()

    def test_reference_control_compares_its_mass_profile_not_unrelated_grid_points(self):
        with tempfile.TemporaryDirectory() as temporary:
            model = Path(temporary) / 'model.json'
            model.write_text('{}')
            previous = dict(model_card=str(model), production_mechanism='senscalc-meson-fragmentation-bremsstrahlung',
                jaw_source_profile=dict(path='/old/jaw', sha256='jaw'),
                detector_runtime=dict(runtime_sha256='runtime'),
                reference_source=dict(runtime_profile=dict(path='/old/source', sha256='source'),
                    bremsstrahlung_profiles={'0.7':dict(path='/old/profile', sha256='profile')}))
            current = json.loads(json.dumps(previous))
            current['reference_source']['bremsstrahlung_profiles']['0.22'] = dict(path='/new/other', sha256='other')
            admission.matching_reference_physics(previous, current, dict(mass_gev=.7))
            current['reference_source']['bremsstrahlung_profiles']['0.7']['sha256'] = 'changed'
            with self.assertRaisesRegex(ValueError, 'source model or runtime'):
                admission.matching_reference_physics(previous, current, dict(mass_gev=.7))

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        (self.directory / 'config.json').write_text('{"frozen": true}')
        (self.directory / 'model.json').write_text('{"model": "fixed"}')
        masses = [.22, .25, .3, .4, .5, .7, .9]
        batches = [dict(batch='gen'+str(i), mass_gev=mass,
                        gen_qualification_only=True, operational_canary=True)
                   for i, mass in enumerate(masses)]
        batches.extend([dict(batch='prompt', operational_canary=True, prompt=True),
                        dict(batch='displaced', operational_canary=True, prompt=False),
                        dict(batch='bulk', operational_canary=False)])
        self.config = dict(directory=str(self.directory),
            model_card=str(self.directory / 'model.json'),
            bulk_storage_gate=str(self.directory / 'gate.json'),
            maximum_meson_forecast_bytes=1000, batches=batches,
            coverage_audit=dict(masses_gev=masses),
            automatic_bulk_admission=dict(schema='shift-dp-automatic-bulk-admission-v1',
                per_batch_forecast_bytes={b['batch']: 10 for b in batches}))
        self.state = dict(batches={b['batch']: dict(gen_qualification=dict(complete=True))
                                  for b in batches})

    def test_no_tests_no_automatic_release(self):
        self.assertIsNone(admission.maybe_create_bulk_gate(self.config, {}))
        self.assertFalse(Path(self.config['bulk_storage_gate']).exists())

    def test_missing_mass_cannot_be_substituted_by_more_other_tests(self):
        self.config['coverage_audit']['masses_gev'].append(.8)
        with self.assertRaisesRegex(ValueError, 'every requested mass'):
            admission.maybe_create_bulk_gate(self.config, self.state)

    def test_incomplete_downstream_keeps_bulk_waiting(self):
        with patch.object(admission, 'source_evidence', return_value={'GEN': 'valid'}), \
             patch.object(admission, 'fullchain_evidence', return_value=None):
            self.assertIsNone(admission.maybe_create_bulk_gate(self.config, self.state))
        self.assertFalse(Path(self.config['bulk_storage_gate']).exists())

    def test_failed_evidence_does_not_create_gate(self):
        with patch.object(admission, 'source_evidence', side_effect=ValueError('changed source')):
            with self.assertRaisesRegex(ValueError, 'changed source'):
                admission.maybe_create_bulk_gate(self.config, self.state)
        self.assertFalse(Path(self.config['bulk_storage_gate']).exists())

    def test_frozen_reservation_cannot_expand(self):
        self.config['automatic_bulk_admission']['per_batch_forecast_bytes']['bulk'] = 1001
        with self.assertRaisesRegex(ValueError, 'storage allocation'):
            admission.maybe_create_bulk_gate(self.config, self.state)

    def test_all_prerequisites_release_exact_frozen_request_once(self):
        with patch.object(admission, 'source_evidence', return_value={'GEN': 'verified'}), \
             patch.object(admission, 'fullchain_evidence', return_value={'ROOT': 'verified'}):
            gate = admission.maybe_create_bulk_gate(self.config, self.state)
        self.assertTrue(gate['complete'])
        self.assertEqual(len(gate['source_qualification']), 7)
        self.assertEqual(len(gate['fullchain_qualification']), 2)
        self.assertEqual(gate['worklist_sha256'], admission.sha(self.directory / 'config.json'))
        before = Path(self.config['bulk_storage_gate']).read_bytes()
        self.assertEqual(admission.maybe_create_bulk_gate(self.config, {}), gate)
        self.assertEqual(Path(self.config['bulk_storage_gate']).read_bytes(), before)

    def test_external_fullchain_controls_require_prompt_displaced_and_real_passes(self):
        controls = []
        for index, prompt in enumerate([True, False]):
            path = self.directory / ('control'+str(index)+'.json')
            batch = dict(batch='control', point='prompt' if prompt else 'displaced', prompt=prompt)
            path.write_text(json.dumps(dict(batches=[batch])))
            controls.append(dict(batch='control', configuration=dict(path=str(path), sha256=admission.sha(path))))
        self.config['automatic_bulk_admission']['qualified_controls'] = controls
        with patch.object(admission, 'source_evidence', return_value={'GEN':'verified'}), \
             patch.object(admission, 'reference_response', return_value=dict(effective_passing_events=0)):
            with self.assertRaisesRegex(ValueError, 'measured passing events'):
                admission.maybe_create_bulk_gate(self.config, self.state)
        self.assertFalse(Path(self.config['bulk_storage_gate']).exists())
        with patch.object(admission, 'source_evidence', return_value={'GEN':'verified'}), \
             patch.object(admission, 'reference_response', return_value=dict(effective_passing_events=2)):
            gate = admission.maybe_create_bulk_gate(self.config, self.state)
        self.assertEqual(set(gate['allowed_batches']), {b['batch'] for b in self.config['batches']})

    def test_existing_gate_from_different_request_is_rejected(self):
        Path(self.config['bulk_storage_gate']).write_text(json.dumps(dict(
            automatic_canary_admission=True, worklist_sha256='wrong')))
        with self.assertRaisesRegex(ValueError, 'another frozen request'):
            admission.maybe_create_bulk_gate(self.config, self.state)


class ReferenceAdmissionTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        (self.directory / 'config.json').write_text('{"frozen": true}')
        (self.directory / 'model.json').write_text('{"model": "reference"}')
        self.points = ['m0p25_lab_0p01m', 'm0p7_lab_0p01m']
        batches = [dict(batch=f'p{point}_b{bank}', point=name,
                        source_collisions=50000, operational_canary=bank == 0)
                   for bank in range(6) for point, name in enumerate(self.points)]
        self.config = dict(directory=str(self.directory), model_card=str(self.directory / 'model.json'),
            bulk_storage_gate=str(self.directory / 'gate.json'), batches=batches,
            maximum_meson_forecast_bytes=200000000,
            automatic_bulk_admission=dict(schema='shift-dp-reference-response-admission-v1',
                target_effective_events=500, cost_margin=1.25,
                qualified_controls=[dict(point=name) for name in self.points],
                point_budget_bytes={name:100000000 for name in self.points},
                per_batch_forecast_bytes={batch['batch']:12000000 for batch in batches}))
        self.responses = {name:dict(point=name, source_collisions=50000,
            effective_passing_events=100., passing_sumw=100., passing_sumw2=100.,
            output_bytes=4000000, point_receipt_path='/qualified/'+name,
            point_receipt_sha256='a'*64, histogram_sha256='b'*64) for name in self.points}

    def gate(self):
        with patch.object(admission, 'reference_response', side_effect=lambda config, authority:
                          self.responses[authority['point']]):
            return admission.maybe_create_bulk_gate(self.config, {})

    def test_five_hundred_goal_selects_only_a_finite_frozen_subset(self):
        gate = self.gate()
        self.assertEqual(gate['target_effective_events'], 500)
        self.assertEqual(len(gate['allowed_batches']), 10)
        self.assertTrue(set(gate['allowed_batches']) <= {b['batch'] for b in self.config['batches']})
        self.assertTrue(gate['unsubmitted_banks_have_zero_exposure'])
        self.assertTrue(all(row['required_banks'] == 5 for row in gate['response_allocations']))

    def test_no_observed_passes_do_not_authorize_unbounded_production(self):
        self.responses[self.points[1]]['effective_passing_events'] = 0.
        self.responses[self.points[1]]['passing_sumw'] = 0.
        self.responses[self.points[1]]['passing_sumw2'] = 0.
        gate = self.gate()
        allowed = [b for b in self.config['batches'] if b['batch'] in gate['allowed_batches']
                   and b['point'] == self.points[1]]
        self.assertEqual(len(allowed), 1)
        self.assertTrue(allowed[0]['operational_canary'])
        row = next(r for r in gate['response_allocations'] if r['point'] == self.points[1])
        self.assertIsNone(row['required_banks'])
        self.assertFalse(row['admitted'])

    def test_point_cost_cap_keeps_only_bounded_controls(self):
        self.responses[self.points[0]]['output_bytes'] = 50000000
        gate = self.gate()
        allowed = [b for b in self.config['batches'] if b['batch'] in gate['allowed_batches']
                   and b['point'] == self.points[0]]
        self.assertEqual(len(allowed), 1)
        self.assertTrue(allowed[0]['operational_canary'])
        self.assertFalse(gate['response_allocations'][0]['admitted'])

    def test_repeated_gate_uses_the_identical_frozen_decision(self):
        first = self.gate()
        before = Path(self.config['bulk_storage_gate']).read_bytes()
        with patch.object(admission, 'reference_response', side_effect=RuntimeError('Do not redraw')) as evaluate:
            again = admission.maybe_create_bulk_gate(self.config, {})
        evaluate.assert_not_called()
        self.assertEqual(again, first)
        self.assertEqual(Path(self.config['bulk_storage_gate']).read_bytes(), before)

    def test_unfinished_control_leaves_gate_unwritten(self):
        self.responses[self.points[1]] = None
        self.assertIsNone(self.gate())
        self.assertFalse(Path(self.config['bulk_storage_gate']).exists())

    def test_canary_is_counted_inside_target_even_when_it_is_last(self):
        self.config['batches'] = self.config['batches'][2:] + self.config['batches'][:2]
        gate = self.gate()
        for point in self.points:
            selected = [b for b in self.config['batches'] if b['point'] == point
                        and b['batch'] in gate['allowed_batches']]
            self.assertEqual(len(selected), 5)
            self.assertEqual(sum(b['operational_canary'] for b in selected), 1)

    def test_retained_metadata_can_reject_a_bank_even_when_nano_fits(self):
        point = self.points[0]
        self.responses[point].update(selected_events=80,
            maximum_retained_metadata_bytes_per_job=1000000)
        for batch in self.config['batches']:
            batch['events_per_job'] = 8
        # Nano alone costs6.05MB, within12MB. Ten retained metadata bundles
        # raise the actual bank forecast above12MB, so bulk must stay closed.
        gate = self.gate()
        selected = [batch for batch in self.config['batches']
                    if batch['point'] == point and not batch['operational_canary']
                    and batch['batch'] in gate['allowed_batches']]
        self.assertEqual(selected, [])
        response = next(row for row in gate['response_allocations'] if row['point'] == point)
        self.assertGreater(response['reserved_bytes_per_bank'], 12000000)

    def test_exact_batch_iwd_keeps_its_future_write_claim_until_producer_exits(self):
        import sys
        from datetime import datetime, timezone
        sys.path.insert(0, str(SCRIPT.parent))
        import run_dark_photon_meson_production as production
        point = self.points[0]
        batch = dict(batch='bank0', point=point, operational_canary=False)
        config = dict(self.config, batches=[batch], minimum_eos_free_bytes=50,
                      minimum_eos_free_files=1)
        gate = dict(complete=True, all_requested_mass_GEN_audits_passed=True,
            model_card_sha256=admission.sha(config['model_card']),
            worklist_sha256=admission.sha(self.directory/'config.json'),
            per_batch_forecast_bytes={'bank0':12000000}, total_meson_forecast_bytes=12000000,
            allowed_batches=['bank0'])
        Path(config['bulk_storage_gate']).write_text(json.dumps(gate))
        state = dict(batches={'bank0':dict(primitive_histograms={'verified':'receipt'})})
        observed = dict(observed_utc=datetime.now(timezone.utc).isoformat(),
                        free_bytes=1000000000, free_files=100)
        live = [{'ClusterId':17, 'Iwd':str(self.directory/'batches/bank0'), 'JobStatus':2}]
        with patch.object(production, 'adaptive_reservation', return_value=0), \
             patch.object(production, 'additional_reservations', return_value=(0, [])), \
             patch.object(production, 'aggregation_reservation', return_value=0), \
             patch.object(production, 'source_mechanism', return_value=production.REFERENCE_MECHANISMS[0]), \
             patch.object(production.durable, 'jobs', return_value=live) as jobs, \
             patch.object(production.durable, 'meson_published_batch', return_value=True) as closed:
            _, evidence = production.storage_gate(config, observed, bulk=True, state=state)
            self.assertEqual(evidence['meson_forecast_bytes'], 12000000)
            closed.assert_not_called()
            jobs.return_value = []
            _, evidence = production.storage_gate(config, observed, bulk=True, state=state)
            self.assertEqual(evidence['meson_forecast_bytes'], 0)
            closed.assert_called_once()

    def test_underforecast_banks_are_not_admitted_from_a_looser_point_budget(self):
        # The total point cap fits, but the actual per-bank storage gate would
        # otherwise reserve less than this measured, margin-included cost.
        self.responses[self.points[0]]['output_bytes'] = 10000000
        gate = self.gate()
        selected = [b for b in self.config['batches'] if b['point'] == self.points[0]
                    and b['batch'] in gate['allowed_batches'] and not b['operational_canary']]
        self.assertEqual(selected, [])


if __name__ == '__main__':
    unittest.main()
