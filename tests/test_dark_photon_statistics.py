# Code style: AI-code. AI-generated independent-batch allocation checks.
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location('dp_statistics', SCRIPTS / 'prepare_dark_photon_statistics.py')
TOOL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TOOL)


def allocation(budgets):
    return dict(points=[dict(point=name, vertex_z_plan=dict(planned_next_stage_additional_events=events))
                        for name, events in budgets.items()])


class StatisticsPreparationTest(unittest.TestCase):
    def test_selected_wave_keeps_qualified_inputs_and_publication_tools_from_older_templates(self):
        import prepare_dark_photon_grid as grid
        import dark_photon_dy_prescription as prescription
        from test_dark_photon_grid import references, fake_native
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);ref=references();refpath=root/'references.json'
            refpath.write_text(json.dumps(ref));templates=root/'old_templates';templates.mkdir()
            for index in range(1,5):(templates/f'step{index}.py').write_text('# unchanged older detector template\n')
            physical=grid.preflight_points(grid.plan_points(ref,masses=[15.],epsilons=[.001],
                events=50,accepted_events=True),ref,fake_native,dy_prescription='msht20-nnlo')
            physical=grid.plan_detector_stage(physical,'20261101_reference','/eos/user/t/test',detector_job_base=1000)
            runtime=dict(cmssw_base='/qualified/cmssw',cmssw_version='qualified_version',
                         scram_arch='qualified_arch',python_executable=sys.executable,
                         gitless_deployment=True,runtime_provenance=dict(
                             cmssw_git_head='qualified_head',archive_sha256='a'*64))
            prepared=grid.write_plan(root/'physical',refpath,templates,physical,runtime)
            name=prepared['points'][0].get('original_point',prepared['points'][0]['point'])
            approved=allocation({name:40});approved['totals']=dict(completed_events=0,planned_next_stage_additional_events=40)
            approved_path=root/'allocation.json';approved_path.write_text(json.dumps(approved))
            args=SimpleNamespace(output=root/'worklist',allocation=approved_path,canary_points=[name],
                batch_events=20,points_per_wave=1,detector_job_base=2000,events_per_job=5,
                seed_base=700001,run_base=1700000001,prior_inventories=[],prior_plans=[],
                reserved_seeds=[],reserved_runs=[],campaign='20261109_msht_statistics_test',
                physical_preparations=[root/'physical/plan.json'],detector_runner=None,eos_base='/eos/user/t/test')
            TOOL.prepare_worklist(args)
            worklist=json.loads((args.output/'worklist.json').read_text())
            self.assertIn('aggregate_dark_photon_samples.py',worklist['dy_prescription_helpers'])
            self.assertIn(prescription.TABLE_NAME,worklist['physics_script_names'])
            original_sources=grid.SOURCES
            with patch.object(prescription,'prescription_files',side_effect=AssertionError('Must use the frozen qualified map')):
                result=TOOL.prepare_wave(args.output/'worklist.json',0,root/'wave')
            self.assertEqual(grid.SOURCES,original_sources)
            plan=json.loads(Path(result['preparation']).read_text());row=plan['points'][0]
            self.assertEqual(plan['runtime']['runtime_provenance'],runtime['runtime_provenance'])
            self.assertEqual(row['gen_argv'].count('--dy-prescription'),1)
            for filename in [prescription.HELPER_NAME,prescription.TABLE_NAME]:
                frozen=plan['frozen_dependencies']['sources'][filename]
                self.assertEqual(grid.digest(frozen['frozen_copy']),grid.digest(args.output/'physics_scripts'/filename))
            self.assertEqual(row['events'],20)
            changed=copy.deepcopy(plan);changed['runtime']['runtime_provenance']['archive_sha256']='b'*64
            changed_path=root/'changed.json';changed_path.write_text(json.dumps(changed))
            with self.assertRaisesRegex(ValueError,'identical frozen runtime provenance'):
                TOOL.combine_plans([result['preparation'],changed_path],root/'bad_runtime')

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

    def test_jaw_extension_redraws_only_new_source_ids_not_physics(self):
        from dark_photon_source_placement import place_contract
        from test_shift_jaw_source import profile
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'jaw.json';path.write_text(json.dumps(profile()))
            original=dict(schema='shift-dark-photon-gen-v1',event_budget_mode='outputAccepted',
                requested_events=2,maximum_framework_events=20,seed=10,run_number=10,
                epsilon=1.e-6,proper_ctau_mm=42.,source_model='native DY axis baseline')
            original=place_contract(original,path)
            newer=TOOL.extend_contract(original,20,862350001,1762350001,path)
            self.assertEqual(TOOL.physics_digest(newer),TOOL.physics_digest(original))
            self.assertEqual(len(newer['source_placement']['event_positions']),200)
            self.assertEqual(newer['source_placement']['event_positions'][0]['event_id'],[1762350001,1,1])
            self.assertEqual(newer['epsilon'],original['epsilon'])
            self.assertEqual(newer['proper_ctau_mm'],original['proper_ctau_mm'])
            with self.assertRaisesRegex(ValueError,'frozen jaw'):
                TOOL.extend_contract(original,20,862350001,1762350001)

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
