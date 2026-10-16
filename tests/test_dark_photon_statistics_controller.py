# Code style: AI-code. AI-generated tests for durable adaptive production transitions.
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import run_dark_photon_statistics as controller


class DurableSubmission(unittest.TestCase):
    def ad(self, directory, **changes):
        row = dict(ClusterId=123, ProcId=0, JobStatus=2, Iwd=str(directory),
                   ShiftDPStatisticsTask=str(directory))
        row.update(changes)
        return row

    def test_history_lookup_uses_one_exact_match_for_a_known_job(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for cluster, limit in ((123, '1'), (None, '2')):
                with self.subTest(cluster=cluster), \
                     patch.object(controller, 'scheduler_arguments', return_value=[]), \
                     patch.object(controller, 'run_condor',
                                  return_value=json.dumps([self.ad(directory)])) as command:
                    self.assertEqual(controller.verify_submitted_job({}, directory, [], cluster), 123)
                    arguments = command.call_args.args[0]
                    self.assertEqual(arguments[arguments.index('-limit') + 1], limit)
                    if cluster is not None:
                        self.assertIn('123.0', arguments)
                        self.assertNotIn('-constraint', arguments)
                    else:
                        self.assertIn('-constraint', arguments)

    def test_ambiguous_intent_never_resubmits(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            description = directory / 'worker.sub'
            description.write_text('queue\n')
            controller.new(directory / 'submission_intent.json', dict(
                task=str(directory), submit_sha256=controller.sha(description)))
            with patch.object(controller, 'jobs', return_value=[]), \
                 patch.object(controller, 'scheduler_arguments', return_value=[]), \
                 patch.object(controller, 'run_condor', return_value='[]') as command:
                with self.assertRaisesRegex(RuntimeError, 'Ambiguous'):
                    controller.submission({}, directory, description)
                self.assertEqual(command.call_count, 1)
                self.assertIn('condor_history', command.call_args.args[0][0])

    def test_queued_working_directory_must_be_persistent(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            description = directory / 'worker.sub'
            description.write_text('queue\n')
            with patch.object(controller, 'scheduler_arguments', return_value=[]), \
                 patch.object(controller, 'jobs', side_effect=[[], [self.ad(directory, Iwd='/pool/condor/dir_1')]]), \
                 patch.object(controller, 'run_condor', side_effect=['', '1 job(s) submitted to cluster 123.']):
                with self.assertRaisesRegex(RuntimeError, 'persistent working directory'):
                    controller.submission({}, directory, description)
            self.assertFalse((directory / 'submission.json').exists())
            self.assertEqual(controller.read(directory / 'submission_intent.json')['cluster'], 123)
            with patch.object(controller, 'jobs', return_value=[self.ad(directory, Iwd='/pool/condor/dir_1')]), \
                 patch.object(controller, 'run_condor') as command:
                with self.assertRaisesRegex(RuntimeError, 'persistent working directory'):
                    controller.submission({}, directory, description)
                command.assert_not_called()

    def test_recovered_queue_and_history_reject_a_scratch_working_directory(self):
        for history in (False, True):
            with self.subTest(history=history), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                description = directory / 'worker.sub'
                description.write_text('queue\n')
                controller.new(directory / 'submission_intent.json', dict(
                    task=str(directory), submit_sha256=controller.sha(description)))
                rows = [self.ad(directory, Iwd='/pool/condor/dir_1')]
                with patch.object(controller, 'scheduler_arguments', return_value=[]), \
                     patch.object(controller, 'jobs', return_value=[] if history else rows), \
                     patch.object(controller, 'run_condor', return_value=json.dumps(rows)) as command:
                    with self.assertRaisesRegex(RuntimeError, 'persistent working directory'):
                        controller.submission({}, directory, description)
                self.assertFalse((directory / 'submission.json').exists())
                self.assertEqual(command.call_count, int(history))
                if history:
                    self.assertIn('condor_history', command.call_args.args[0][0])

    def test_existing_receipt_is_revalidated_on_every_restart(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            description = directory / 'worker.sub'
            description.write_text('queue\n')
            receipt = dict(task=str(directory), submit_sha256=controller.sha(description), cluster=123)
            controller.new(directory / 'submission.json', receipt)
            for changes, error in ((dict(Iwd='/pool/condor/dir_1'), 'persistent working directory'),
                                   (dict(ShiftDPStatisticsTask='another task'), 'task identity'),
                                   (dict(ClusterId=124), 'exact native cluster'),
                                   (dict(ProcId=1), 'exact native cluster')):
                with self.subTest(changes=changes), \
                     patch.object(controller, 'jobs', return_value=[self.ad(directory, **changes)]), \
                     patch.object(controller, 'run_condor') as command:
                    for _ in range(2):
                        with self.assertRaisesRegex(RuntimeError, error):
                            controller.submission({}, directory, description)
                    command.assert_not_called()
            with patch.object(controller, 'jobs', return_value=[self.ad(directory)]), \
                 patch.object(controller, 'run_condor') as command:
                self.assertEqual(controller.submission({}, directory, description), receipt)
                command.assert_not_called()

    def test_recovered_intent_keeps_its_known_cluster_and_task(self):
        for changes, error in ((dict(ClusterId=124), 'exact native cluster'),
                               (dict(ShiftDPStatisticsTask='another task'), 'task identity')):
            with self.subTest(changes=changes), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                description = directory / 'worker.sub'
                description.write_text('queue\n')
                controller.new(directory / 'submission_intent.json', dict(
                    task=str(directory), submit_sha256=controller.sha(description), cluster=123))
                with patch.object(controller, 'jobs', return_value=[self.ad(directory, **changes)]), \
                     patch.object(controller, 'run_condor') as command:
                    with self.assertRaisesRegex(RuntimeError, error):
                        controller.submission({}, directory, description)
                    command.assert_not_called()
                self.assertFalse((directory / 'submission.json').exists())


class AggregateSubmission(unittest.TestCase):
    def prepare(self, point, sources, target):
        target.mkdir(parents=True)
        controller.new(target / 'plan.json', dict(schema='shift-dark-photon-additive-plan-v1',
            histogram_only=True, campaign='synthetic', points=[dict(point=point, sources=sources)]))
        template = target / 'aggregate.sub.in'
        template.write_text('arguments = SEAL_DIGEST\nqueue\n')
        controller.new(target / 'seal.json', dict(files={str(path): controller.sha(path)
            for path in (target / 'plan.json', template)}))
        (target / 'aggregate.sub').write_text(template.read_text().replace(
            'SEAL_DIGEST', controller.sha(target / 'seal.json')))

    def test_crash_after_native_submission_recovers_the_same_aggregate(self):
        class ControllerCrash(BaseException):
            pass

        class Transport:
            def __init__(self, host, cache_directory):
                self.cache_directory = Path(cache_directory)
                self.cache_directory.mkdir(exist_ok=True)

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            worklist = directory / 'worklist.json'
            controller.new(worklist, dict(waves=[dict(batches=[dict(point='m15_prompt')])]))
            config = dict(directory=str(directory), aggregate_workers=1, worklist=str(worklist))
            populations = [dict(point='m15_prompt', plan_path=str(path)) for path in
                           (directory / 'baseline.json', directory / 'waves/w000/production/plan.json')]
            state, prepared, native_submits = {}, [], []
            queued = []
            state_path = directory / 'state.json'
            original_save = controller.save

            def prepare(config, point, sources, target):
                prepared.append(target)
                self.prepare(point, sources, target)

            def queue(config, constraint):
                return queued

            def command(arguments, **options):
                if '-dry-run' in arguments:
                    return ''
                self.assertIn('condor_submit', arguments[0])
                persisted = controller.read(state_path)['aggregates']['m15_prompt']
                self.assertEqual(persisted['directory'], str(prepared[0]))
                self.assertEqual(persisted['source_plans'], [item['plan_path'] for item in populations])
                self.assertNotIn('cluster', persisted)
                native_submits.append(arguments)
                queued.append(dict(ClusterId=123, ProcId=0, JobStatus=2, Iwd=str(prepared[0]),
                                   ShiftDPStatisticsTask=str(prepared[0])))
                return '1 job(s) submitted to cluster 123.'

            def crash_before_cluster_save(path, value):
                if Path(path) == state_path and value['aggregates']['m15_prompt'].get('cluster'):
                    raise ControllerCrash()
                original_save(path, value)

            with patch('watch_dark_photon_production.EosRpc', Transport), \
                 patch.object(controller, 'available_sources', return_value={'m15_prompt': populations}), \
                 patch.object(controller, 'aggregate_plan', side_effect=prepare), \
                 patch.object(controller, 'scheduler_arguments', return_value=[]), \
                 patch.object(controller, 'jobs', side_effect=queue), \
                 patch.object(controller, 'run_condor', side_effect=command):
                with patch.object(controller, 'save', side_effect=crash_before_cluster_save):
                    with self.assertRaises(ControllerCrash):
                        controller.publish(config, state)
                restarted = controller.read(state_path)
                self.assertNotIn('cluster', restarted['aggregates']['m15_prompt'])
                controller.publish(config, restarted)
            self.assertEqual(len(native_submits), 1)
            self.assertEqual(prepared, [directory / 'aggregates/m15_prompt/v0001'])
            self.assertEqual(list((directory / 'aggregates/m15_prompt').iterdir()), prepared)
            self.assertEqual(controller.read(state_path)['aggregates']['m15_prompt']['cluster'], 123)

    def test_committed_warning_and_lost_controller_state_recover_v1_without_resubmitting(self):
        class Transport:
            def __init__(self, host, cache_directory):
                self.cache_directory = Path(cache_directory)
                self.cache_directory.mkdir(exist_ok=True)

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            controller.new(directory / 'worklist.json', dict(waves=[dict(batches=[dict(point='m15_prompt')])]))
            config = dict(directory=str(directory), aggregate_workers=1,
                          worklist=str(directory / 'worklist.json'))
            populations = [dict(point='m15_prompt', plan_path=str(path)) for path in
                           (directory / 'baseline.json', directory / 'waves/w000/production/plan.json')]
            queued, submissions = [], []

            def command(arguments, **options):
                if '-dry-run' in arguments:
                    return ''
                submissions.append(arguments)
                target = directory / 'aggregates/m15_prompt/v0001'
                queued.append(dict(ClusterId=123, ProcId=0, JobStatus=2, Iwd=str(target),
                                   ShiftDPStatisticsTask=str(target)))
                raise RuntimeError('Scheduler state unknown: warning after committed submission')

            with patch('watch_dark_photon_production.EosRpc', Transport), \
                 patch.object(controller, 'available_sources', return_value={'m15_prompt': populations}), \
                 patch.object(controller, 'aggregate_plan', side_effect=lambda config, point, sources, target:
                              self.prepare(point, sources, target)) as prepare, \
                 patch.object(controller, 'scheduler_arguments', return_value=[]), \
                 patch.object(controller, 'jobs', side_effect=lambda *args: queued), \
                 patch.object(controller, 'run_condor', side_effect=command):
                with self.assertRaisesRegex(RuntimeError, 'committed submission'):
                    controller.publish(config, {})
                # Reproduce the old controller's lost pending state after the error.
                restarted = {}
                controller.publish(config, restarted)
            self.assertEqual(len(submissions), 1)
            self.assertEqual(prepare.call_count, 1)
            self.assertEqual(restarted['aggregates']['m15_prompt']['cluster'], 123)
            self.assertEqual([path.name for path in (directory / 'aggregates/m15_prompt').iterdir()], ['v0001'])

    def test_orphan_intent_with_unknown_scheduler_state_blocks_a_new_version(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            populations = [dict(plan_path='/baseline.json'), dict(plan_path='/waves/w000/production/plan.json')]
            target = directory / 'aggregates/m15_prompt/v0001'
            self.prepare('m15_prompt', populations, target)
            controller.new(target / 'submission_intent.json', dict(task=str(target),
                submit_sha256=controller.sha(target / 'aggregate.sub')))
            state = {}
            recovered = controller.reconcile_aggregate(dict(directory=str(directory)), state, 'm15_prompt', populations)
            with patch.object(controller, 'jobs', return_value=[]), \
                 patch.object(controller, 'scheduler_arguments', return_value=[]), \
                 patch.object(controller, 'run_condor', return_value='[]') as command:
                with self.assertRaisesRegex(RuntimeError, 'Ambiguous submitted job'):
                    controller.submission({}, recovered['directory'], target / 'aggregate.sub')
            self.assertEqual(command.call_count, 1)
            self.assertIn('condor_history', command.call_args.args[0][0])
            self.assertEqual(list(target.parent.iterdir()), [target])

    def test_multiple_or_changed_orphans_fail_closed_before_submission(self):
        for mode in ('multiple', 'changed_source', 'changed_template', 'changed_pending'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                populations = [dict(plan_path='/baseline.json', plan_sha256='a'),
                               dict(plan_path='/waves/w000/production/plan.json', plan_sha256='b')]
                target = directory / 'aggregates/m15_prompt/v0001'
                self.prepare('m15_prompt', populations, target)
                state = {}
                if mode == 'multiple':
                    self.prepare('m15_prompt', populations, target.parent / 'v0002')
                elif mode == 'changed_source':
                    populations = [dict(item, plan_sha256='changed') for item in populations]
                elif mode == 'changed_template':
                    (target / 'aggregate.sub.in').write_text('changed\n')
                else:
                    state = dict(aggregates=dict(m15_prompt=dict(directory='/another/path')))
                with patch.object(controller, 'submission') as submit:
                    with self.assertRaises((ValueError, RuntimeError)):
                        controller.reconcile_aggregate(dict(directory=str(directory)), state, 'm15_prompt', populations)
                    submit.assert_not_called()

    def test_new_sources_do_not_replace_the_single_pending_population(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            populations = [dict(plan_path='/baseline.json'), dict(plan_path='/waves/w000/production/plan.json')]
            target = directory / 'aggregates/m15_prompt/v0001'
            self.prepare('m15_prompt', populations, target)
            state = {}
            record = controller.reconcile_aggregate(dict(directory=str(directory)), state, 'm15_prompt',
                populations + [dict(plan_path='/waves/w001/production/plan.json')])
            self.assertEqual(record['source_plans'], [item['plan_path'] for item in populations])
            self.assertEqual(controller.read(target / 'aggregate_intent.json'), record)

    def test_verified_previous_publication_allows_the_next_version(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            populations = [dict(plan_path='/baseline.json'), dict(plan_path='/waves/w000/production/plan.json')]
            target = directory / 'aggregates/m15_prompt/v0001'
            self.prepare('m15_prompt', populations, target)
            receipt = target / 'completed_points/m15_prompt.json'
            receipt.parent.mkdir()
            controller.new(receipt, dict(complete=True))
            state = dict(published=dict(m15_prompt=dict(point_receipt_path=str(receipt),
                point_receipt_sha256=controller.sha(receipt), source_plans=[item['plan_path'] for item in populations])))
            self.assertIsNone(controller.reconcile_aggregate(dict(directory=str(directory)), state, 'm15_prompt', populations))


class AggregateDescription(unittest.TestCase):
    def test_only_redundant_inherited_el9_requirement_is_removed_before_sealing(self):
        for requirement in ('(TARGET.OpSysMajorVer == 9)', '(TARGET.Memory >= 4000)'):
            with self.subTest(requirement=requirement), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                tools = directory / 'tools'
                tools.mkdir()
                for name in ('aggregate_dark_photon_samples.py', 'dark_photon_production_support.py',
                             'export_dark_photon_cross_sections.py', 'pythia_generation_ledger.py',
                             'dark_photon_meson.py', 'dark_photon_meson_source.py'):
                    (tools / name).write_text('# synthetic helper\n')
                meson = 'Memory' in requirement
                guard = directory / ('batches/meson_canary/production' if meson else 'waves/w000/production')
                guard.mkdir(parents=True)
                controller.new(guard / 'seal.json', {})
                controller.new(guard / 'plan.json', dict(points=[dict(point='m15_prompt')]))
                (guard / 'finalizer_worker.sh').write_text('#!/bin/bash\npython3 ' + str(guard / 'finalize_point.py') + '\n')
                (guard / 'finalizer.sub').write_text('initialdir = ' + str(guard) + '\nrequirements = ' + requirement +
                    '\nrequest_memory = 4000 MB\narguments = old\nqueue\n')
                sources = []
                for index in range(2):
                    path = directory / ('source' + str(index) + '.json')
                    controller.new(path, dict(points=[dict(point='m15_prompt', mass_gev=15, epsilon=1e-5,
                        proper_ctau_mm=0, sampling_epsilon=.01, events=20)]))
                    sources.append(dict(point='m15_prompt', plan_path=str(path)))
                config = dict(directory=str(directory), tools=str(tools), campaign_prefix='synthetic',
                              eos_base='/eos/synthetic', conditions={})
                if meson:
                    config.update(production_mechanism='eta-etaprime-radiative', aggregation_guard=str(guard))
                target = directory / 'aggregates/m15_prompt/v0001'
                controller.aggregate_plan(config, 'm15_prompt', sources, target)
                description = (target / 'aggregate.sub').read_text()
                self.assertNotIn('OpSysMajorVer', description)
                self.assertIn('request_memory = 4000 MB', description)
                if 'Memory' in requirement:
                    self.assertIn('requirements = ' + requirement, description)
                    self.assertTrue((target / 'dark_photon_meson.py').is_file())
                    plan = controller.read(target / 'plan.json')
                    self.assertEqual(plan['scoring_deployment']['path'], str(guard))
                    self.assertEqual(plan['dependencies'][str(target / 'dark_photon_meson_source.py')],
                                     controller.sha(tools / 'dark_photon_meson_source.py'))
                controller.aggregate_record(target, 'm15_prompt', sources)


class CanaryAndAdmission(unittest.TestCase):
    def config(self, directory):
        worklist = directory / 'worklist.json'
        controller.new(worklist, dict(waves=[dict(batches=[dict(point='m2_prompt')]),
                                                dict(batches=[dict(point='m5_prompt')])]))
        return dict(directory=str(directory), worklist=str(worklist), simultaneous_waves=3,
                    minimum_free_bytes=100, minimum_free_files=10)

    def test_old_statistics_do_not_pass_canary_gate(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = self.config(Path(temporary))
            state = dict(published=dict(m2_prompt=dict(new_events=100000,
                                                      source_plans=['/old/production/plan.json'])))
            self.assertFalse(controller.published_canary(config, state))
            state['published']['m2_prompt']['source_plans'].append(
                str(controller.wave_directory(config, 0) / 'production' / 'plan.json'))
            self.assertTrue(controller.published_canary(config, state))

    def test_bulk_waits_for_real_canary_publication(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = self.config(Path(temporary))
            with patch.object(controller, 'quota', return_value=dict(free_bytes=1000, free_files=100)), \
                 patch.object(controller, 'prepare_gen', return_value=dict(cluster=123)) as submit:
                state = controller.step(config, Path(temporary) / 'config.json', {})
            self.assertEqual(list(state['waves']), ['0'])
            self.assertEqual(submit.call_count, 1)
            self.assertFalse(state['canary_published'])

    def test_storage_shortfall_submits_nothing(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = self.config(Path(temporary))
            with patch.object(controller, 'quota', return_value=dict(free_bytes=99, free_files=100)), \
                 patch.object(controller, 'prepare_gen') as submit:
                state = controller.step(config, Path(temporary) / 'config.json', {})
            submit.assert_not_called()
            self.assertEqual(state['admission'], 'waiting_for_storage')

    def test_missing_manager_history_stops_new_admission(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            config = self.config(directory)
            stage = controller.wave_directory(config, 0) / 'gen_stage'
            stage.mkdir(parents=True)
            controller.new(stage / 'submission.json', dict(cluster=123))
            state = dict(waves={'0': dict(gen_cluster=123)})
            with patch.object(controller, 'quota', return_value=dict(free_bytes=1000, free_files=100)), \
                 patch.object(controller, 'jobs', return_value=[]), \
                 patch.object(controller, 'scheduler_arguments', return_value=[]), \
                 patch.object(controller, 'run_condor', return_value='[]'), \
                 patch.object(controller, 'prepare_gen') as submit:
                with self.assertRaisesRegex(RuntimeError, 'queue and history'):
                    controller.step(config, directory / 'config.json', state)
            submit.assert_not_called()


if __name__ == '__main__':
    unittest.main()
