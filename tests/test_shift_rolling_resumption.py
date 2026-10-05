import json
import os
from pathlib import Path
import re
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from prepare_shift_rolling_resumption import rolling_dag, prepare
from prepare_shift_production_dag import submit_description
from shift_production_plan import make_plan
from generation_publication import sha256
import run_shift_production_rolling_node as entrypoint


class RollingResumptionTest(unittest.TestCase):
    def test_slow_qcd_cannot_block_other_production_nodes(self):
        plan = make_plan('rolling', '/eos/user/j/jniedzie/shift_cmssw',
                         include_strata=['qcd_10to20', 'jpsi_2to5', 'dy_2to5'])
        dag = rolling_dag(plan)
        parents = {}
        for line in dag.splitlines():
            if line.startswith('PARENT '):
                left, right = line[7:].split(' CHILD ')
                for child in right.split():
                    parents.setdefault(child, set()).update(left.split())
        jobs = [f'J{i:02d}_{chunk:05d}' for i, stratum in enumerate(plan['strata'])
                for chunk in range(stratum['jobs'])]
        self.assertEqual(len(jobs), 700)
        self.assertTrue(all(parents[job] == {'SIZING'} for job in jobs))
        self.assertEqual(parents['COMPLETE'], set(jobs))
        self.assertEqual(parents['SIZING'], {'P00', 'P01', 'P02'})
        self.assertIn('MAXJOBS workers 50', dag)
        self.assertNotIn('JOB Q', dag)
        self.assertNotIn('RETRY ', dag)

    def test_quota_failure_prevents_worker_execution(self):
        frozen = types.SimpleNamespace(validate_plan=mock.Mock(),
            quota=mock.Mock(side_effect=ValueError('No headroom')), main=mock.Mock())
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp)/'manifest.json'
            manifest.write_text(json.dumps({'plan': {}}))
            before = list(sys.path)
            try:
                with mock.patch.dict(sys.modules, {'run_shift_production_node': frozen}), \
                     mock.patch.dict(os.environ, SHIFT_DAG_LOCAL_ROOT=tmp), \
                     mock.patch.object(sys, 'argv', ['rolling', str(manifest), 'production', '0:0']):
                    with self.assertRaisesRegex(ValueError, 'No headroom'):
                        entrypoint.main()
            finally:
                sys.path[:] = before
        frozen.main.assert_not_called()

    def test_successful_quota_delegates_to_unchanged_worker(self):
        order = []
        frozen = types.SimpleNamespace(validate_plan=lambda plan: order.append('plan'),
            quota=lambda manifest: order.append('quota'), main=lambda: order.append('worker'))
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp)/'manifest.json'
            manifest.write_text(json.dumps({'plan': {}}))
            before = list(sys.path)
            try:
                with mock.patch.dict(sys.modules, {'run_shift_production_node': frozen}), \
                     mock.patch.dict(os.environ, SHIFT_DAG_LOCAL_ROOT=tmp), \
                     mock.patch.object(sys, 'argv', ['rolling', str(manifest), 'production', '0:0']):
                    entrypoint.main()
            finally:
                sys.path[:] = before
        self.assertEqual(order, ['plan', 'quota', 'worker'])

    def test_prepare_preserves_scientific_manifest_and_predecessor(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, destination = Path(tmp)/'source', Path(tmp)/'rolling'
            source.mkdir()
            plan = make_plan('rolling', '/eos/user/j/jniedzie/shift_cmssw')
            manifest = {'plan': plan, 'bundle_sha256': 'a'*64}
            (source/'manifest.json').write_text(json.dumps(manifest))
            (source/'bootstrap.sh').write_text(
                'exec python3 payload/workflow/scripts/run_shift_production_node.py manifest.json "$mode" "$item"\n')
            for name in ('worker.sub', 'control.sub'):
                (source/name).write_text(submit_description(source, sha256(source/'manifest.json')))
            (source/'freeze.json').write_text(json.dumps({p.name: sha256(p) for p in source.iterdir()}))
            original = {p.name: p.read_bytes() for p in source.iterdir()}
            with mock.patch('prepare_shift_rolling_resumption.subprocess.run'):
                prepare(source, destination)
            self.assertEqual(original, {p.name: p.read_bytes() for p in source.iterdir()})
            self.assertEqual((source/'manifest.json').read_bytes(), (destination/'manifest.json').read_bytes())
            self.assertIn('rolling_node.py', (destination/'worker.sub').read_text())
            self.assertIn(sha256(destination/'rolling_node.py'), (destination/'bootstrap.sh').read_text())
            frozen = json.loads((destination/'freeze.json').read_text())
            for name, expected in frozen.items():
                self.assertEqual(sha256(destination/name), expected)

    def test_adaptive_quota_covers_ceiling_without_mutating_scientific_manifest(self):
        scientific = {'plan': {'max_workers': 50}, 'minimum_free_bytes': 50000}
        calls = []
        frozen = types.SimpleNamespace(validate_plan=mock.Mock(),
            quota=lambda value: calls.append(value), main=mock.Mock())
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp)/'manifest.json'
            policy = Path(tmp)/'policy.json'
            manifest.write_text(json.dumps(scientific))
            policy.write_text(json.dumps(dict(schema='shift-gen-scheduling-policy-v1',
                scientific_manifest_sha256=sha256(manifest), scheduling_ceiling=300,
                quota_reservation_workers=300)))
            before = list(sys.path)
            try:
                with mock.patch.dict(sys.modules, {'run_shift_production_node': frozen}), \
                     mock.patch.dict(os.environ, SHIFT_DAG_LOCAL_ROOT=tmp,
                                     SHIFT_DAG_SCHEDULING_POLICY=str(policy)), \
                     mock.patch.object(sys, 'argv', ['rolling', str(manifest), 'production', '0:0']):
                    entrypoint.main()
            finally:
                sys.path[:] = before
            self.assertEqual(calls[0]['plan']['max_workers'], 300)
            self.assertEqual(json.loads(manifest.read_text()), scientific)
        frozen.main.assert_called_once()

    def test_prepare_adaptive_successor_from_rolling_predecessor(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, rolling, adaptive = [Path(tmp)/name for name in ('source', 'rolling', 'adaptive')]
            source.mkdir()
            plan = make_plan('rolling', '/eos/user/j/jniedzie/shift_cmssw')
            (source/'manifest.json').write_text(json.dumps({'plan': plan, 'bundle_sha256': 'a'*64}))
            (source/'bootstrap.sh').write_text(
                'exec python3 payload/workflow/scripts/run_shift_production_node.py manifest.json "$mode" "$item"\n')
            for name in ('worker.sub', 'control.sub'):
                (source/name).write_text(submit_description(source, sha256(source/'manifest.json')))
            (source/'freeze.json').write_text(json.dumps({p.name: sha256(p) for p in source.iterdir()}))
            with mock.patch('prepare_shift_rolling_resumption.subprocess.run'):
                prepare(source, rolling)
                prepare(rolling, adaptive, 300, 50)
            self.assertEqual((source/'manifest.json').read_bytes(), (adaptive/'manifest.json').read_bytes())
            self.assertIn('MAXJOBS workers 300', (adaptive/'production.dag').read_text())
            bootstrap = (adaptive/'bootstrap.sh').read_text()
            self.assertEqual(bootstrap.count("printf '%s  rolling_node.py\\n'"), 1)
            self.assertEqual(bootstrap.count('export SHIFT_DAG_SCHEDULING_POLICY='), 1)
            transfer = next(line for line in (adaptive/'worker.sub').read_text().splitlines()
                            if line.startswith('transfer_input_files ='))
            self.assertEqual(transfer.count('rolling_node.py'), 1)
            self.assertEqual(transfer.count('scheduling_policy.json'), 1)


if __name__ == '__main__':
    unittest.main()
