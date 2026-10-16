# Code style: AI-code. AI-generated regression tests for honest GEN closure.
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
spec = importlib.util.spec_from_file_location('closure', SCRIPTS / 'prepare_mu_qcd_recovery_closure.py')
closure = importlib.util.module_from_spec(spec)
spec.loader.exec_module(closure)


class RecoveryClosureTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def event(self, code, detail=''):
        return f'{code} (200.000.000) 10/16 12:00:00 event\n{detail}\n...\n'

    def test_native_requires_current_attempt_terminal(self):
        path = self.root / 'events'
        path.write_text(self.event('001') + self.event('005', 'Normal termination (return value 0)') + self.event('001'))
        self.assertEqual(closure.native_exit(path, '200.0'), {'executed': True, 'terminal': None})
        path.write_text(path.read_text() + self.event('005', 'Normal termination (return value 1)'))
        self.assertEqual(closure.native_exit(path, '200.0')['terminal'], 1)

    def test_native_rejects_truncated_exit_and_foreign_job(self):
        path = self.root / 'events'
        path.write_text(self.event('001') + self.event('005', 'Normal termination (return value 0)').removesuffix('...\n'))
        self.assertIsNone(closure.native_exit(path, '200.0')['terminal'])
        self.assertEqual(closure.native_exit(path, '201.0'), {'executed': False, 'terminal': None})

    def test_watchdog_flags_held_child_even_with_healthy_manager(self):
        manager = dict(JobStatus=2, Iwd=str(self.root), MaxRuntime=604800,
                       ShiftMuQCDRecoveryClosureManager=True)
        self.assertEqual(closure.supervision_problems(self.root, [manager])[1], [])
        child = dict(JobStatus=5, ClusterId=201, ProcId=0, HoldReason='exit1', DAGNodeName='ORDINARY')
        problems = closure.supervision_problems(self.root, [manager, child])[1]
        self.assertTrue(any('201.0 is held: exit1' in problem for problem in problems))

    def ordinary(self, count=1):
        target = self.root / 'accounting/ordinary'
        (target / 'results').mkdir(parents=True)
        rows = [dict(id=f'source_{index}', stratum=f'bin_{index % 5}', stage='production', trials=10)
                for index in range(count)]
        manifest = dict(jobs=rows, inputs={}, generator_profile='shift-qcd-mu-enriched-cylinder-v1',
                        source_model_settings_sha256='model')
        closure.save(target / 'manifest.json', manifest)
        for index, row in enumerate(rows):
            audit = dict(complete=True, healthy=True, events=1, source_accepted=10,
                         full_source_trial_denominator=20, event_ids=[[1, 1, index]], weights=[1.0],
                         input_sha256='gen', generator_profile=manifest['generator_profile'],
                         source_model_settings_sha256='model', no_second_filter_efficiency=True,
                         bounds=[index % 5, index % 5 + 1], sigma_nd_mb=1.0,
                         native_generator_run_info={'internal_xsec_pb': 1.0})
            record = dict(schema='shift-mu-enriched-ordinary-gen-receipt-v1', complete=True,
                          publication_complete=True, exit_status=0, request=row,
                          manifest_sha256=closure.sha(target / 'manifest.json'),
                          source_inputs={}, generator_profile=manifest['generator_profile'],
                          event_count=1, semantic_audit=audit)
            closure.save(target / 'results' / (row['id'] + '.json'), record)
        return target, rows

    def test_inventory_keeps_full_trials_and_all_pending_ids(self):
        target, rows = self.ordinary(3)
        (target / 'results/source_1.json').unlink()
        path = target / 'results/source_2.json'
        record = closure.read(path)
        record['publication_complete'] = False
        closure.save(path, record)
        inventory, pending = closure.source_inventory(self.root, 'ordinary', allow_pending=True)
        self.assertEqual(pending, ['source_1', 'source_2'])
        self.assertEqual(inventory[0]['full_source_trials'], 20)
        self.assertEqual(inventory[0]['framework_source_events'], 10)
        self.assertEqual(inventory[0]['saved_events'], 1)
        with self.assertRaises(FileNotFoundError):
            closure.source_inventory(self.root, 'ordinary')

    def test_inventory_rejects_changed_or_invalid_exposure(self):
        target, rows = self.ordinary()
        path = target / 'results/source_0.json'
        record = closure.read(path)
        record['semantic_audit']['full_source_trial_denominator'] = 0
        closure.save(path, record)
        with self.assertRaisesRegex(ValueError, 'full original source denominator'):
            closure.source_inventory(self.root, 'ordinary')

    def test_unchanged_finalizer_accounts_all_900_full_denominators(self):
        target, rows = self.ordinary(900)
        completed = subprocess.run([sys.executable, str(SCRIPTS / 'control_mu_enriched_qcd_gen.py'),
                                    str(target), 'production'], capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        record = closure.accounting(self.root, 'ordinary')
        self.assertEqual(sum(row['chunks'] for row in record['strata'].values()), 900)
        self.assertEqual(sum(row['full_source_trial_denominator'] for row in record['strata'].values()), 18000)
        self.assertEqual(sum(row['framework_source_events'] for row in record['strata'].values()), 9000)
        record['strata']['bin_0']['event_weight_pb_by_chunk'].pop('source_0')
        closure.save(target / 'production_complete.json', record)
        with self.assertRaisesRegex(ValueError, 'full source cohort'):
            closure.accounting(self.root, 'ordinary')

    def test_original_error_is_required_and_never_rewritten(self):
        path = self.root / 'nodes.status'
        path.write_text('[Node="L0614"; NodeStatus=6;][Node="L0454"; NodeStatus=6;]')
        original = path.read_bytes()
        self.assertEqual(len(closure.old_error(self.root)['error_node_blocks']), 2)
        self.assertEqual(path.read_bytes(), original)
        path.write_text('[Node="L0614"; NodeStatus=5;]')
        with self.assertRaisesRegex(ValueError, 'ERROR must remain'):
            closure.old_error(self.root)


if __name__ == '__main__':
    unittest.main()
