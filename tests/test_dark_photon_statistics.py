# Code style: AI-code. AI-generated independent-batch allocation checks.
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location('dp_statistics', SCRIPTS / 'prepare_dark_photon_statistics.py')
TOOL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TOOL)


def allocation(budgets):
    return dict(points=[dict(point=name, vertex_z_plan=dict(planned_next_stage_additional_events=events))
                        for name, events in budgets.items()])


class StatisticsPreparationTest(unittest.TestCase):
    def test_contract_extension_preserves_all_physical_fields(self):
        original = dict(schema='shift-dark-photon-gen-v1', event_budget_mode='outputAccepted',
                        requested_events=50, maximum_framework_events=500, seed=10, run_number=10,
                        epsilon=3.e-8, width_authority=dict(ctau_mm=12.3, physical_br_mumu=.2),
                        source_z_mm=-160000, timing=dict(mode='unchanged'))
        before = copy.deepcopy(original)
        result = TOOL.extend_contract(original, 2000, 761116001, 1761116001)
        self.assertEqual(original, before)
        self.assertEqual(TOOL.physics_digest(result), TOOL.physics_digest(original))
        self.assertEqual(result['maximum_framework_events'], 20000)
        self.assertEqual(result['requested_events'], 2000)

    def test_canary_is_part_of_exact_allocation(self):
        requested = {'m2_prompt': 9520, 'm15_prompt': 10140, 'm70_lab_1000m': 10120}
        waves = TOOL.build_waves(allocation(requested), list(requested))
        self.assertEqual(waves[0]['batches'], [dict(point=name, events=20) for name in requested])
        self.assertEqual(sum(batch['events'] for wave in waves for batch in wave['batches']), sum(requested.values()))
        for name, events in requested.items():
            self.assertEqual(sum(batch['events'] for wave in waves for batch in wave['batches'] if batch['point'] == name), events)

    def test_wave_limits_keep_distinct_points_and_bound_groups(self):
        budgets = {'p' + str(index): 4500 + index * 20 for index in range(30)}
        waves = TOOL.build_waves(allocation(budgets), ['p0', 'p15', 'p29'])
        for wave in waves:
            self.assertLessEqual(len(wave['batches']), 10)
            self.assertEqual(len({batch['point'] for batch in wave['batches']}), len(wave['batches']))
            self.assertTrue(all(0 < batch['events'] <= 2000 for batch in wave['batches']))
        first_round = [batch['point'] for wave in waves[1:4] for batch in wave['batches']]
        self.assertEqual(set(first_round), set(budgets))

    def test_canary_cannot_add_unapproved_events(self):
        with self.assertRaisesRegex(ValueError, 'twenty allocated'):
            TOOL.build_waves(allocation({'p': 19}), ['p'])

    def test_overlapping_historical_namespace_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'old.json'
            path.write_text(json.dumps(dict(points=dict(old=dict(seed=100, run_number=1000)))))
            waves = [dict(batches=[dict(seed=100, run_number=2000, detector_job_base=10000,
                                        detector_chunks=1, detector_audit_job=10001)])]
            with self.assertRaisesRegex(ValueError, 'overlaps.*seeds'):
                TOOL.namespace_audit(waves, [path], [], [], [])

    def test_detector_audit_jobs_are_distinct_from_payload_jobs(self):
        waves = [dict(batches=[dict(seed=200, run_number=2000, detector_job_base=10000,
                                    detector_chunks=2, detector_audit_job=10001)])]
        with self.assertRaisesRegex(ValueError, 'detector_jobs'):
            TOOL.namespace_audit(waves, [], [], [], [])


if __name__ == '__main__':
    unittest.main()
