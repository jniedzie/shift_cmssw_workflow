"""Synthetic coverage for immutable Nano scoring plans and publication gates."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import prepare_dimuon_score_production as production


def fixture(base):
    sources = base / 'sources'; sources.mkdir()
    for name in production.SOURCES:
        (sources / name).write_text('# Synthetic scoring dependency\n')
    inventory = base / 'inputs.txt'
    inputs = [production.SOURCE_ROOT + f'/{stratum}/job{index:07d}/nano.root'
              for index, stratum in enumerate(sorted(production.STRATA))]
    inventory.write_text(''.join(repr((source, '', '/unused/hist.root')) + '\n' for source in inputs))
    model_path = base / 'model.json'
    names = ['reco_quality']
    model = dict(schema='shift-dimuon-uniform-bdt-json-v1', feature_names=names,
                 feature_contract_sha256=hashlib.sha256(json.dumps(names, separators=(',', ':')).encode()).hexdigest(),
                 requires_gen=False, selection_applied=False, physics_ready=False, threshold_metadata=0.4,
                 provenance=dict(model_name='synthetic_model', source_sha256={
                     name: production.digest(sources / name) for name in ('features.py', 'portable_inference.py')}))
    model_path.write_text(json.dumps(model))
    receipt = dict(schema='shift-dimuon-bdt-export-receipt-v1', model_sha256=production.digest(model_path),
                   provenance=model['provenance'], production_scoring_parity_validated=True,
                   selection_applied=False, physics_ready=False,
                   validation=dict(rows=3494, bitwise_score_equality=True, threshold_decision_equality=True,
                                   maximum_absolute_score_difference=0., score_dtype='float64', selection_applied=False))
    receipt_path = model_path.with_suffix('.json.receipt.json'); receipt_path.write_text(json.dumps(receipt))
    runtime = base / 'LCG_108/x86_64-el9-gcc13-opt/setup.sh'
    runtime.parent.mkdir(parents=True); runtime.write_text('# synthetic pinned setup\n')
    return dict(inventory=inventory, model_path=model_path, scorer_directory=sources,
                export_receipt=receipt_path, runtime_setup=runtime, inputs=inputs)


def prepared(base, **kwargs):
    data = fixture(base)
    args = {k: v for k, v in data.items() if k != 'inputs'}
    output = base / 'plan'
    plan = production.prepare_plan(output=output, **args, **kwargs)
    return data, output, plan


class InventoryContract(unittest.TestCase):
    def test_mount_aliases_are_canonical_and_all_zero_pair_files_are_retained(self):
        with tempfile.TemporaryDirectory() as name:
            base = Path(name); data = fixture(base)
            text = data['inventory'].read_text().replace('/eos/user/j/', '/eos/home-j/')
            data['inventory'].write_text(text)
            rows = production.read_inventory(data['inventory'])
            self.assertEqual(len(rows), 19)
            self.assertEqual({r['stratum'] for r in rows}, production.STRATA)
            self.assertTrue(all(r['source'].startswith(production.SOURCE_ROOT + '/') for r in rows))
            self.assertTrue(all('root://eosuser.cern.ch//eos/user/' in r['source_url'] for r in rows))

    def test_duplicate_alias_and_cross_bin_job_identity_fail(self):
        with tempfile.TemporaryDirectory() as name:
            data = fixture(Path(name)); original = data['inventory'].read_text()
            first = data['inputs'][0]
            for duplicate in (first.replace('/eos/user/j/', '/eos/home-j/'),
                              production.SOURCE_ROOT + '/qcd_0to1/job0000000/nano.root'):
                data['inventory'].write_text(original + repr((duplicate, '', 'unused')) + '\n')
                with self.assertRaisesRegex(ValueError, 'Duplicate'):
                    production.read_inventory(data['inventory'])

    def test_wrong_campaign_traversal_unknown_bin_and_nonliteral_fail(self):
        with tempfile.TemporaryDirectory() as name:
            data = fixture(Path(name)); source = data['inputs'][0]
            bad = [repr((source.replace('_v10/', '_v8/'), '', 'unused')),
                   repr((source.replace('/nano.root', '/../nano.root'), '', 'unused')),
                   repr((source.replace('/dy_0.211317to0.5/', '/dy_unknown/'), '', 'unused')),
                   repr((source, 'mass > 3', 'unused')), '__import__("os").system("touch bad")']
            for line in bad:
                data['inventory'].write_text(line + '\n')
                with self.subTest(line=line), self.assertRaises(ValueError):
                    production.read_inventory(data['inventory'])

    def test_missing_bins_and_empty_inventory_fail(self):
        with tempfile.TemporaryDirectory() as name:
            data = fixture(Path(name))
            for text in ('', repr((data['inputs'][0], '', 'unused')) + '\n'):
                data['inventory'].write_text(text)
                with self.assertRaises(ValueError): production.read_inventory(data['inventory'])

    def test_source_tree_and_ancestor_cannot_be_output(self):
        for output in (production.SOURCE_ROOT, production.SOURCE_ROOT + '/scores',
                       str(Path(production.SOURCE_ROOT).parent)):
            with self.assertRaises(ValueError): production.validate_output_root(output)
        self.assertEqual(production.validate_output_root(production.OUTPUT_ROOT), production.OUTPUT_ROOT)


class FrozenPlanContract(unittest.TestCase):
    def test_immutable_model_sources_runtime_batches_and_no_external_actions(self):
        with tempfile.TemporaryDirectory() as name, mock.patch.object(production.subprocess, 'run') as external:
            data, output, plan = prepared(Path(name), files_per_batch=7, expected_files=168)
            external.assert_not_called()
            self.assertEqual([len(b['file_indices']) for b in plan['batches']], [7, 7, 5])
            self.assertEqual(plan['missing_from_expected'], 149)
            self.assertFalse(plan['launched']); self.assertFalse(plan['selection_applied'])
            self.assertTrue(plan['retain_zero_pair_files']); self.assertFalse(plan['source_population_filtered'])
            self.assertFalse(plan['physics_ready']); self.assertFalse(plan['normalization_transfer_validated'])
            pilot_rows = [plan['files'][i] for i in plan['pilot_batches'][0]['file_indices']]
            self.assertEqual([r['stratum'].split('_')[0] for r in pilot_rows], ['qcd', 'jpsi', 'dy'])
            self.assertEqual(json.loads((output / 'plan.json').read_text()), plan)
            self.assertEqual(production.verify_plan(output / 'plan.json', production.digest(output / 'plan.json')), plan)
            for row in plan['files']:
                self.assertNotEqual(row['source'], row['output'])
                self.assertIn('--sample-kind', row['score_argv']); self.assertNotIn('--threshold', row['score_argv'])
            kwargs = {k:v for k,v in data.items() if k != 'inputs'}
            with self.assertRaises(FileExistsError): production.prepare_plan(output=output, **kwargs)

    def test_condor_is_prepared_without_submission_and_worker_shell_is_valid(self):
        with tempfile.TemporaryDirectory() as name:
            _, output, plan = prepared(Path(name), prepare_condor=True)
            subprocess.run(['bash', '-n', str(output / 'worker.sh')], check=True)
            receipt = json.loads((output / 'condor_receipt.json').read_text())
            self.assertFalse(receipt['submitted']); self.assertEqual(receipt['max_materialize'], 20)
            submit = (output / 'score.sub').read_text()
            for required in ('request_cpus = 1', 'request_memory = 2000', 'request_disk = 2000000',
                             'max_materialize = 20', '+JobFlavour = "microcentury"', 'batch_failure.tar.gz'):
                self.assertIn(required, submit)
            self.assertNotIn('condor_submit', (output / 'worker.sh').read_text())
            self.assertIn('arguments = $(batch) pilot', (output / 'pilot.sub').read_text())
            self.assertIn('max_materialize = 1', (output / 'pilot.sub').read_text())
            self.assertTrue(plan['condor_prepared'])

    def test_changed_source_or_model_or_runtime_is_detected(self):
        for target in ('features.py', 'model.json', 'runtime'):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as name:
                _, output, plan = prepared(Path(name))
                path = Path(plan['runtime']['setup'] if target == 'runtime'
                            else plan['frozen_dependencies'][target]['path'])
                path.write_text(path.read_text() + '\nchanged')
                with self.assertRaises(ValueError): production.verify_plan(output / 'plan.json', production.digest(output / 'plan.json'))

    def test_parity_receipt_mismatch_and_true_physics_flags_fail_before_output(self):
        mutations = [lambda d:d.update(model_sha256='0' * 64),
                     lambda d:d.update(physics_ready=True),
                     lambda d:d.update(production_scoring_parity_validated=False),
                     lambda d:d['validation'].update(bitwise_score_equality=False),
                     lambda d:d['validation'].update(rows=0),
                     lambda d:d['validation'].update(maximum_absolute_score_difference=1e-16)]
        for mutate in mutations:
            with tempfile.TemporaryDirectory() as name:
                base = Path(name); data = fixture(base)
                receipt = json.loads(data['export_receipt'].read_text()); mutate(receipt)
                data['export_receipt'].write_text(json.dumps(receipt))
                kwargs = {k:v for k,v in data.items() if k != 'inputs'}
                with self.assertRaises(ValueError): production.prepare_plan(output=base / 'plan', **kwargs)
                self.assertFalse((base / 'plan').exists())

    def test_unvalidated_scoring_source_and_invalid_counts_fail_before_output(self):
        with tempfile.TemporaryDirectory() as name:
            base = Path(name); data = fixture(base); kwargs = {k:v for k,v in data.items() if k != 'inputs'}
            for option in ({'files_per_batch':0}, {'files_per_batch':101}, {'chunk_events':0}, {'expected_files':18}):
                with self.assertRaises(ValueError): production.prepare_plan(output=base / 'plan', **kwargs, **option)
            (data['scorer_directory'] / 'portable_inference.py').write_text('changed source')
            with self.assertRaisesRegex(ValueError, 'source differs'):
                production.prepare_plan(output=base / 'plan', **kwargs)
            self.assertFalse((base / 'plan').exists())


class WorkerPublicationContract(unittest.TestCase):
    def fake_io(self, plan, row):
        source = b'Original simulated Nano fixture; zero retained pairs'
        store = {row['source']: source, row['source_receipt']: json.dumps(dict(
            complete=True, nano_path=row['source'], nano_sha256=hashlib.sha256(source).hexdigest(), events=2)).encode()}
        publication_order = []
        def get(remote, local): Path(local).write_bytes(store[remote])
        def put(local, remote):
            if remote in store: raise FileExistsError('Remote artifact already exists')
            store[remote] = Path(local).read_bytes(); publication_order.append(remote)
        def score(argv, **kwargs):
            def argument(name): return Path(argv[argv.index(name) + 1])
            source_path, output, receipt = [argument(k) for k in ('--input', '--output', '--receipt')]
            output.write_bytes(source_path.read_bytes() + b'; appended Float64 score branch')
            value = dict(complete=True, source=str(source_path), output=str(output),
                         source_sha256=production.digest(source_path), model_sha256=plan['model_sha256'],
                         output_sha256=production.digest(output), selection_applied=False, physics_ready=False,
                         normalization_transfer_validated=False,
                         events=2, retained_pairs=0,
                         verification=dict(all_original_content_equal=True, all_scores_recomputed_equal=True))
            receipt.write_text(json.dumps(value))
            kwargs['stdout'].write('Synthetic scoring complete; original content preserved\n')
            return subprocess.CompletedProcess(argv, 0)
        return store, publication_order, get, put, score

    def test_publish_readbacks_and_last_marker_keep_zero_pair_input(self):
        with tempfile.TemporaryDirectory() as name:
            _, output, plan = prepared(Path(name)); row = plan['files'][0]
            store, order, get, put, score = self.fake_io(plan, row)
            original = store[row['source']]
            with mock.patch.object(production, 'get_remote', side_effect=get), \
                 mock.patch.object(production, 'put_remote', side_effect=put), \
                 mock.patch.object(production, 'remote_exists', side_effect=lambda path:path in store), \
                 mock.patch.object(production, 'transfer'), \
                 mock.patch.object(production.subprocess, 'run', side_effect=score):
                result = production.run_file(plan, production.digest(output / 'plan.json'), row, Path(name) / 'work')
                reused = production.run_file(plan, production.digest(output / 'plan.json'), row, Path(name) / 'reuse')
            self.assertTrue(result['complete']); self.assertFalse(result['reused']); self.assertTrue(reused['reused'])
            self.assertEqual(order, [row['output'], row['receipt'], row['log'], row['complete_marker']])
            self.assertEqual(store[row['source']], original)
            receipt = json.loads(store[row['receipt']])
            self.assertEqual(receipt['source'], row['source']); self.assertEqual(receipt['output'], row['output'])
            self.assertEqual(receipt['retained_pairs'], 0); self.assertFalse(receipt['selection_applied'])
            self.assertEqual(receipt['score_log'], row['log'])
            self.assertEqual(receipt['score_log_sha256'], hashlib.sha256(store[row['log']]).hexdigest())
            self.assertIn(b'original content preserved', store[row['log']])

    def test_corrupt_source_or_orphan_publication_stops_without_overwrite(self):
        for failure in ('source', 'orphan'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as name:
                _, output, plan = prepared(Path(name)); row = plan['files'][0]
                store, order, get, put, score = self.fake_io(plan, row)
                if failure == 'source': store[row['source']] = b'changed source'
                else: store[row['output']] = b'orphan payload'
                with mock.patch.object(production, 'get_remote', side_effect=get), \
                     mock.patch.object(production, 'put_remote', side_effect=put), \
                     mock.patch.object(production, 'remote_exists', side_effect=lambda path:path in store), \
                     mock.patch.object(production.subprocess, 'run') as launch:
                    with self.assertRaises((ValueError, FileExistsError)):
                        production.run_file(plan, production.digest(output / 'plan.json'), row, Path(name) / 'work')
                launch.assert_not_called(); self.assertFalse(order)

    def test_corrupt_published_readback_never_creates_complete_marker(self):
        with tempfile.TemporaryDirectory() as name:
            _, output, plan = prepared(Path(name)); row = plan['files'][0]
            store, order, get, put, score = self.fake_io(plan, row)
            def bad_get(remote, local):
                get(remote, local)
                if remote == row['output']: Path(local).write_bytes(b'corrupt readback')
            with mock.patch.object(production, 'get_remote', side_effect=bad_get), \
                 mock.patch.object(production, 'put_remote', side_effect=put), \
                 mock.patch.object(production, 'remote_exists', side_effect=lambda path:path in store), \
                 mock.patch.object(production, 'transfer'), \
                 mock.patch.object(production.subprocess, 'run', side_effect=score):
                with self.assertRaisesRegex(ValueError, 'readback'):
                    production.run_file(plan, production.digest(output / 'plan.json'), row, Path(name) / 'work')
            self.assertNotIn(row['complete_marker'], store); self.assertIn(row['output'], store)

    def test_unknown_remote_state_is_never_treated_as_missing(self):
        for message, missing in (('[ERROR] [3011] No such file or directory', True),
                                  ('Permission denied', False), ('Connection failed', False)):
            with mock.patch.object(production.subprocess, 'run', return_value=subprocess.CompletedProcess(
                    [], 1, '', message)):
                if missing: self.assertFalse(production.remote_exists('/explicit/target'))
                else:
                    with self.assertRaises(RuntimeError): production.remote_exists('/explicit/target')

    def test_failed_batch_keeps_exact_pending_population_and_transferred_evidence(self):
        with tempfile.TemporaryDirectory() as name:
            base = Path(name); _, output, plan = prepared(base, files_per_batch=2)
            scratch = base / 'scratch'; scratch.mkdir()
            def failing(plan, plan_sha, row, work):
                work.mkdir(); (work / 'nano.root.incomplete').write_bytes(b'failed partial')
                (work / 'bdt_score.json').write_text('{"complete":false}')
                raise RuntimeError('synthetic scorer failure')
            with mock.patch.object(production, 'run_file', side_effect=failing):
                with self.assertRaisesRegex(RuntimeError, 'synthetic'):
                    production.run_batch(output / 'plan.json', production.digest(output / 'plan.json'), 0, scratch)
            status = json.loads((scratch / 'batch_status.json').read_text())
            self.assertFalse(status['complete']); self.assertEqual(status['pending_file_indices'], [0, 1])
            with tarfile.open(scratch / 'batch_failure.tar.gz') as archive:
                self.assertTrue(any(n.endswith('nano.root.incomplete') for n in archive.getnames()))
                self.assertTrue(any(n.endswith('bdt_score.json') for n in archive.getnames()))


if __name__ == '__main__':
    unittest.main()
