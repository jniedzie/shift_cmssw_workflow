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
                target.mkdir(parents=True)
                prepared.append(target)
                controller.new(target / 'plan.json', dict(campaign='synthetic', points=[]))
                controller.new(target / 'seal.json', {})
                (target / 'aggregate.sub').write_text('queue\n')

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
