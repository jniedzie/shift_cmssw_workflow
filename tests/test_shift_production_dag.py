import copy
import json
from pathlib import Path
import sys
import tempfile
import os
import signal
import subprocess
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from shift_production_plan import make_plan, validate_plan, BINS, DY_BINS
from prepare_shift_production_dag import build_dag, submit_description
from run_shift_production_node import validate_receipt, receipt_path
from generation_publication import sha256
from fixed_target_generation import process_settings
from submit_shift_production_dag import parse_queue
import submit_shift_production_dag as submission
import run_shift_production_node as production_worker


class ProductionDagTest(unittest.TestCase):
    def setUp(self):
        self.plan = make_plan('test_suite', '/eos/user/j/jniedzie/shift_cmssw')

    def test_complete_partition_and_targets(self):
        self.assertEqual(len(self.plan['strata']), 19)
        for sample, target, bounds in [('qcd',100000,BINS), ('jpsi',10000,BINS), ('dy',10000,DY_BINS)]:
            strata = [s for s in self.plan['strata'] if s['sample']==sample]
            self.assertEqual([tuple(s['bounds']) for s in strata], list(bounds))
            self.assertTrue(all(s['target_events']==target for s in strata))
        self.assertFalse(self.plan['detector_production_ready'])

    def test_reject_changed_physics_and_targets(self):
        for name, value in [('target_events',999), ('seed_base',13), ('adapter','different')]:
            changed=copy.deepcopy(self.plan); changed['strata'][0][name]=value
            with self.assertRaises(ValueError): validate_plan(changed)
        self.assertEqual(validate_plan(self.plan), self.plan)

    def test_partial_plan_keeps_canonical_bins_and_seed_offsets(self):
        chosen = ['qcd_0to1', 'qcd_10to20', 'jpsi_0to1', 'dy_0.211317to0.5']
        partial = make_plan('phase_a', '/eos/user/j/jniedzie/shift_cmssw',
                            include_strata=chosen)
        self.assertEqual(partial['included_strata'], chosen)
        self.assertFalse(partial['full_partition_complete'])
        for stratum in partial['strata']:
            baseline = next(row for row in self.plan['strata'] if row['id'] == stratum['id'])
            self.assertEqual(stratum['seed_base'], baseline['seed_base'])
            self.assertEqual(stratum['run_offset'], baseline['run_offset'])
            self.assertEqual(stratum['bounds'], baseline['bounds'])
            self.assertEqual(stratum['target_events'], baseline['target_events'])
        self.assertEqual(validate_plan(partial), partial)
        for invalid in ([], ['qcd_0to1', 'qcd_0to1'], ['qcd_100to200']):
            with self.assertRaises(ValueError):
                make_plan('phase_a', '/eos/user/j/jniedzie/shift_cmssw',
                          include_strata=invalid)
        changed = copy.deepcopy(partial)
        changed['strata'][0]['events_per_job'] = 1
        with self.assertRaises(ValueError):
            validate_plan(changed)

    def test_all_workers_behind_sizing_and_quota_gates(self):
        dag=build_dag(self.plan)
        self.assertIn('MAXJOBS workers 50', dag)
        self.assertIn('MAXJOBS pilots 10', dag)
        self.assertIn('PARENT SIZING CHILD Q0000', dag)
        self.assertNotIn('RETRY ', dag)
        self.assertEqual(dag.count('CATEGORY J'), sum(s['jobs'] for s in self.plan['strata']))
        self.assertIn('PARENT Q0000 CHILD J00_00000', dag)
        self.assertIn('CHILD COMPLETE', dag)

    def test_one_proc_per_node_and_failures_held(self):
        text=submit_description(Path('/tmp/state'), 'a'*64)
        self.assertIn('queue 1', text)
        self.assertIn('getenv = False', text)
        self.assertIn('periodic_release = False', text)
        self.assertIn('ExitCode != 0', text)

    def test_dag_items_do_not_use_condor_queue_variable(self):
        dag=build_dag(self.plan)
        self.assertIn('VARS PREFLIGHT mode="preflight" node_item="0"',dag)
        self.assertIn('node_item="6:0"',dag)
        self.assertNotIn(' item=',dag)
        description=submit_description(Path('/tmp/state'),'a'*64)
        self.assertIn('$(node_item)',description)
        self.assertNotIn('$(item)',description)

    def test_reject_invalid_output_and_limits(self):
        for root in ('/eos/user/j/../escape','/tmp/out'):
            with self.assertRaises(ValueError): make_plan('t',root)
        with self.assertRaises(ValueError): make_plan('t','/eos/user/j/jniedzie/out',max_workers=51)
        with self.assertRaises(ValueError): make_plan('bad;tag','/eos/user/j/jniedzie/out')

    def test_receipt_hash_and_identity_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'gen.root'; root.write_bytes(b'audited payload')
            receipt=Path(directory)/'receipt.json'
            record=dict(complete=True, manifest_sha256='frozen', events=1, requested_events=1,
                        event_ids=[[1,1,1]], artifacts=[dict(path=str(root),bytes=root.stat().st_size,sha256=sha256(root))])
            receipt.write_text(json.dumps(record))
            validate_receipt(receipt,'frozen',1)
            with self.assertRaises(ValueError): validate_receipt(receipt,'changed',1)
            root.write_bytes(b'changed payload')
            with self.assertRaises(ValueError): validate_receipt(receipt,'frozen',1)

    def test_relocated_completion_receipt_and_payload_prevent_regeneration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old_gen = root / 'gen.root'
            new_gen = root / 'relocated_gen.root'
            new_gen.write_bytes(b'audited payload')
            logical_base = root / 'production_dags'
            logical_receipt = logical_base / 'receipts/qcd_0to1/part00000.json'
            moved_base = root / 'support/production_dags'
            moved_receipt = moved_base / 'receipts/qcd_0to1/part00000.json'
            moved_receipt.parent.mkdir(parents=True)
            record = dict(complete=True, manifest_sha256='frozen', events=1, requested_events=1,
                stratum='qcd_0to1', chunk=0, pilot=False, event_ids=[[1, 1, 1]],
                artifacts=[dict(path=str(old_gen), bytes=new_gen.stat().st_size, sha256=sha256(new_gen))])
            moved_receipt.write_text(json.dumps(record))
            original = moved_receipt.read_bytes()
            migration = root / 'migration.json'
            migration.write_text(json.dumps(dict(schema='shift-storage-path-map-v1', complete=True,
                paths={str(old_gen): str(new_gen)}, support_prefixes={str(logical_base): str(moved_base)})))
            manifest = dict(receipt_base=str(logical_base), plan=dict(strata=[dict(id='qcd_0to1',
                jobs=1, events_per_job=1, target_events=1, pilot_events=1)]))
            with mock.patch.dict(os.environ, {'SHIFT_STORAGE_MIGRATION_MANIFEST': str(migration),
                                             'SHIFT_DAG_LOCAL_ROOT': str(root)}):
                self.assertEqual(validate_receipt(logical_receipt, 'frozen', 1), record)
                production_worker.worker(manifest, 'frozen', 0, 0, False)
                self.assertFalse(logical_base.exists())
                self.assertEqual(moved_receipt.read_bytes(), original)
                new_gen.write_bytes(b'changed payload')
                with self.assertRaisesRegex(ValueError, 'Artifact changed'):
                    validate_receipt(logical_receipt, 'frozen', 1)

    def test_successful_empty_scheduler_query(self):
        self.assertEqual(parse_queue('', ''), [])
        self.assertEqual(parse_queue('[]', ''), [])

    def test_scheduler_errors_remain_unknown(self):
        with self.assertRaises(ValueError): parse_queue('', 'Authentication failed')
        with self.assertRaises(ValueError): parse_queue('not JSON', '')

    def test_global_queue_arrays(self):
        self.assertEqual(parse_queue('[{"ClusterId":1}]\n[{"ClusterId":2}]',''),
                         [{'ClusterId':1},{'ClusterId':2}])

    def test_submit_from_outside_prepared_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            state=Path(directory)/'prepared'; state.mkdir()
            bundle=b'frozen archive'
            import hashlib
            manifest=dict(plan=self.plan,
                          bundle_url='root://eosuser.cern.ch//eos/user/j/jniedzie/archive',
                          bundle_sha256=hashlib.sha256(bundle).hexdigest())
            (state/'manifest.json').write_text(json.dumps(manifest))
            (state/'freeze.json').write_text('{}')
            (state/'production.dag.condor.sub').write_text('queue 1')
            (state/'control.sub').write_text('queue 1')
            (state/'production.dag').write_text('JOB PREFLIGHT control.sub\n')
            def execute(command, **kwargs):
                if command[0]=='xrdcp':
                    Path(command[-1]).write_bytes(bundle)
                    return subprocess.CompletedProcess(command,0,'','')
                if command[0]=='condor_q':
                    return subprocess.CompletedProcess(command,0,'','')
                if command[0]=='condor_submit':
                    cwd=Path(kwargs.get('cwd',Path.cwd()))
                    self.assertTrue((cwd/'control.sub').is_file(), 'DAG node paths must resolve from prepared directory')
                    return subprocess.CompletedProcess(command,0,'1 job(s) submitted to cluster 12345.','')
                self.fail('Unexpected command '+str(command))
            with mock.patch.object(submission.subprocess,'run',side_effect=execute), mock.patch.object(sys,'argv',['submit',str(state)]):
                submission.main()
            receipt=json.loads((state/'submission.json').read_text())
            self.assertEqual(receipt['cluster'],12345)
            self.assertEqual(receipt['working_directory'],str(state))

    def test_graceful_stop_reaps_the_owned_child(self):
        with tempfile.TemporaryDirectory() as directory:
            child_pid=Path(directory)/'child.pid'
            script=Path(directory)/'driver.py'
            script.write_text('import sys; sys.path.insert(0,'+repr(str(Path(__file__).resolve().parents[1]/'scripts'))+'); '
                'from run_shift_production_node import children; '
                'children([sys.executable,"-c",'+repr('import os,time; open('+repr(str(child_pid))+',"w").write(str(os.getpid())); time.sleep(30)')+'],'+repr(str(Path(directory)/'worker.log'))+')')
            driver=subprocess.Popen([sys.executable,str(script)])
            try:
                deadline=time.monotonic()+5
                while not child_pid.exists() and time.monotonic()<deadline: time.sleep(0.02)
                self.assertTrue(child_pid.exists())
                owned_pid=int(child_pid.read_text())
                driver.send_signal(signal.SIGTERM)
                self.assertEqual(driver.wait(timeout=5),143)
                self.assertFalse(Path(f'/proc/{owned_pid}').exists())
            finally:
                if driver.poll() is None:
                    driver.kill(); driver.wait(timeout=5)

    def test_dy_threshold_and_unbounded_upper_edge(self):
        self.assertIn('23:mMin = 0.211317', process_settings('dy',0.211317,0.5))
        self.assertIn('PhaseSpace:mHatMax = -1', process_settings('dy',20,-1))
        self.assertFalse(any('pTHat' in x for x in process_settings('dy',0.5,1)))
        with self.assertRaises(ValueError): process_settings('dy',0.2,0.5)


if __name__=='__main__': unittest.main()
