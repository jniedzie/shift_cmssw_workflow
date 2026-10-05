"""Fail-closed publication and manager accounting tests without EOS or CMSSW."""
import copy
from contextlib import ExitStack
import hashlib
import json
import math
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import run_shift_production_health_audit as health
from audit_shift_weighted_gen import ALGORITHM, LEDGER_SCHEMA, SOURCE_MODEL_SHA256


def fixture(weights=(.2, .4)):
    """Four fixed parent trials, with two positive sparse accepted identities."""
    trials, run = 4, 30000001
    sumw, sumw2 = math.fsum(weights), math.fsum(w*w for w in weights)
    sigma = 65.*sumw/trials
    error = 65.*math.sqrt(max(0., sumw2-sumw*sumw/trials)/(trials*(trials-1)))
    ledger = dict(schema=LEDGER_SCHEMA, algorithm=ALGORITHM, complete=True,
        native_gen_lumi_is_trial_ledger=False, physics_valid=False, normalization_ready=False,
        sample='qcd', lower=5., upper=10., requested_trials=trials, framework_slots=trials,
        tried=trials, proposal_calls=5, accounted_proposal_calls=5, max_proposal_calls_per_slot=2,
        intrinsic_retry_slots=1, ordinary_proposal_calls=1, ordinary_proposal_fraction=.1,
        accepted=len(weights), accumulated_accepted=len(weights), sumw=sumw, sumw2=sumw2,
        sigma_nd_mb=65., sigma_mb=sigma, sigma_error_mb=error, run=run, lumi=1,
        first_parent_event=1, last_parent_event=trials, initializations=1,
        unexpected_failures=0, accounting_failures=0,
        status_counts=[len(weights)]+[0]*7+[trials-len(weights)]+[0]*7)
    row = dict(id='qcd_5to10_0000', stratum='qcd_5to10', sample='qcd', bounds=[5., 10.],
               trials=trials, run=run, seed=301000001, stage='production', chunk_index=0,
               publish=True, timeout_seconds=3600)
    inputs = {'audit_shift_weighted_gen.py': 'a'*64}
    manifest = dict(schema='shift-weighted-high-bin-manager-v2', algorithm=ALGORITHM,
                    source_model_settings_sha256=SOURCE_MODEL_SHA256,
                    inputs=inputs, eos_base='/synthetic/eos', chunk_trials=trials, jobs=[row])
    manager = dict(name='high', audit_contract=health.WEIGHTED_CONTRACT,
                   manifest_sha256='b'*64)
    artifacts = {name: dict(bytes=10+i, sha256=str(i+1)*64)
                 for i, name in enumerate(health.WEIGHTED_ARTIFACTS)}
    semantic = dict(schema='shift-weighted-gen-semantic-audit-v1', complete=True, healthy=True,
                    algorithm=ALGORITHM, ledger=copy.deepcopy(ledger),
                    source_model_settings_sha256=SOURCE_MODEL_SHA256,
                    auditor_sha256=inputs['audit_shift_weighted_gen.py'],
                    events=len(weights), requested=trials, tried=trials,
                    event_ids=[[run, 1, parent] for parent in (1, 4)[:len(weights)]],
                    weights=list(weights), scale=[6.]*len(weights), charged=[10]*len(weights),
                    sigma_weighted_mb=sigma, error_weighted_mb=error,
                    weighted_run_info=dict(internal_xsec_pb=sigma*1e9, error_pb=error*1e9,
                                           filter_efficiency=1.),
                    input_sha256=artifacts['gen.root']['sha256'],
                    ledger_sha256=artifacts['proposal_ledger.json']['sha256'],
                    config_sha256=artifacts['resolved_gen_cfg.py']['sha256'])
    directory = manifest['eos_base']+'/'+row['id']
    publications = [dict(name=name, path=directory+'/'+name, independent_sha256_readback=True, **item)
                    for name, item in artifacts.items()]
    record = dict(schema=health.WEIGHTED_CONTRACT, complete=True, generated_edm=True,
                  publication_complete=True, manifest_sha256=manager['manifest_sha256'],
                  node_id=row['id'], request=copy.deepcopy(row), source_inputs=copy.deepcopy(inputs),
                  algorithm=ALGORITHM, physics_valid=False, normalization_ready=False,
                  scientific_gate_sha256='c'*64, ledger=ledger, semantic_audit=semantic,
                  event_count=len(weights), publication_directory=directory,
                  publication=publications, artifacts=artifacts)
    return record, manager, manifest, row


def frozen_namespace(root, weights=(.2, .4)):
    record, manager, manifest, row = fixture(weights)
    root = Path(root)
    base = root/'eos'
    base.mkdir()
    results = root/'results'
    results.mkdir()
    manifest['eos_base'] = str(base)
    manifest_path = root/'manifest.json'
    manifest_path.write_text(json.dumps(manifest))
    manager.update(manifest=str(manifest_path), manifest_sha256=health.digest(manifest_path))
    gate = dict(schema='shift-weighted-high-bin-scientific-gate-v2',
                manifest_sha256=manager['manifest_sha256'], **{'pass': True},
                production_plan={row['stratum']: dict(chunks=1, trials=4)})
    gate_path = root/'scientific_gate.json'
    gate_path.write_text(json.dumps(gate))
    record['manifest_sha256'] = manager['manifest_sha256']
    record['scientific_gate_sha256'] = health.digest(gate_path)
    directory = base/row['id']
    directory.mkdir()
    record['publication_directory'] = str(directory)
    for item in record['publication']:
        item['path'] = str(directory/item['name'])
    write_receipts(root, record)
    return record, manager, manifest, row


def write_receipts(root, record):
    root = Path(root)
    serialized = json.dumps(record)
    (root/'results'/(record['node_id']+'.json')).write_text(serialized)
    (Path(record['publication_directory'])/'publication_receipt.json').write_text(serialized)


class WeightedReceiptTest(unittest.TestCase):
    def test_fixed_denominator_and_sparse_parent_ids(self):
        record, manager, manifest, row = fixture()
        checked = health.weighted_receipt(record, manager, manifest, row, 'c'*64)
        self.assertEqual((checked['trials'], checked['events']), (4, 2))
        self.assertEqual(checked['identities'], {(row['run'], 1, 1), (row['run'], 1, 4)})
        self.assertAlmostEqual(checked['sumw'], .6)

    def test_manifest_row_source_and_gate_mutations_rejected(self):
        mutations = [
            lambda r: r.update(schema='old-receipt'),
            lambda r: r.update(manifest_sha256='d'*64),
            lambda r: r.update(node_id='different-node'),
            lambda r: r['request'].update(trials=5),
            lambda r: r['request'].update(seed=301000002),
            lambda r: r['request'].update(bounds=[5., -1]),
            lambda r: r['source_inputs'].update({'audit_shift_weighted_gen.py': 'd'*64}),
            lambda r: r.update(scientific_gate_sha256='d'*64),
            lambda r: r.update(physics_valid=True),
            lambda r: r.update(normalization_ready=True),
            lambda r: r.update(publication_complete=False),
        ]
        for index, mutate in enumerate(mutations):
            record, manager, manifest, row = fixture()
            mutate(record)
            with self.subTest(index=index), self.assertRaises(ValueError):
                health.weighted_receipt(record, manager, manifest, row, 'c'*64)

    def test_trial_counters_and_accepted_denominator_cannot_replace_fixed_n(self):
        for field in ('requested_trials', 'framework_slots', 'tried', 'last_parent_event'):
            record, manager, manifest, row = fixture()
            record['ledger'][field] = 2
            record['semantic_audit']['ledger'] = copy.deepcopy(record['ledger'])
            with self.subTest(field=field), self.assertRaises(ValueError):
                health.weighted_receipt(record, manager, manifest, row, 'c'*64)
        for field in ('sigma_mb', 'sigma_error_mb'):
            record, manager, manifest, row = fixture()
            record['ledger'][field] *= 2
            record['semantic_audit']['ledger'] = copy.deepcopy(record['ledger'])
            with self.subTest(field=field), self.assertRaises(ValueError):
                health.weighted_receipt(record, manager, manifest, row, 'c'*64)

    def test_malformed_semantics_weights_and_event_arrays_rejected(self):
        mutations = [
            lambda s: s.update(healthy=False),
            lambda s: s.update(source_model_settings_sha256='d'*64),
            lambda s: s.update(auditor_sha256='d'*64),
            lambda s: s.update(requested=2),
            lambda s: s['event_ids'].__setitem__(1, s['event_ids'][0]),
            lambda s: s['event_ids'][0].__setitem__(2, 5),
            lambda s: s['event_ids'][0].__setitem__(0, 30000002),
            lambda s: s['weights'].__setitem__(0, math.nan),
            lambda s: s['weights'].__setitem__(0, 0.),
            lambda s: s['scale'].__setitem__(0, 10.),
            lambda s: s['charged'].__setitem__(0, True),
            lambda s: s['weights'].pop(),
            lambda s: s['weighted_run_info'].update(filter_efficiency=.5),
            lambda s: s.update(input_sha256='d'*64),
        ]
        for index, mutate in enumerate(mutations):
            record, manager, manifest, row = fixture()
            mutate(record['semantic_audit'])
            with self.subTest(index=index), self.assertRaises(ValueError):
                health.weighted_receipt(record, manager, manifest, row, 'c'*64)

    def test_publication_requires_all_four_exact_immutable_artifacts(self):
        mutations = [
            lambda r: r.update(publication=None),
            lambda r: r['publication'].pop(),
            lambda r: r['publication'].__setitem__(1, r['publication'][0]),
            lambda r: r['publication'][0].update(path='/other/gen.root'),
            lambda r: r['publication'][0].update(bytes=0),
            lambda r: r['publication'][0].update(bytes=True),
            lambda r: r['publication'][0].update(sha256='not-a-sha'),
            lambda r: r['publication'][0].update(sha256='d'*64),
            lambda r: r['publication'][0].update(independent_sha256_readback=False),
            lambda r: r.update(publication_directory='/other'),
        ]
        for index, mutate in enumerate(mutations):
            record, manager, manifest, row = fixture()
            mutate(record)
            with self.subTest(index=index), self.assertRaises(ValueError):
                health.weighted_receipt(record, manager, manifest, row, 'c'*64)

    def test_zero_yield_publication_still_counts_all_fixed_trials(self):
        with tempfile.TemporaryDirectory() as root:
            record, manager, manifest, row = frozen_namespace(root, ())
            checked = health.weighted_receipt(record, manager, manifest, row, record['scientific_gate_sha256'])
            self.assertEqual((checked['events'], checked['sumw'], checked['sumw2']), (0, 0., 0.))
            with patch.object(health, 'readback_weighted', return_value={'healthy': True}) as readback:
                summary, representatives, ids = health.audit_weighted_manager(manager, root, set())
            self.assertEqual((summary['completed_chunks'], summary['completed_events'], summary['completed_fixed_trials']), (1, 0, 4))
            self.assertEqual(ids, set())
            self.assertEqual(len(representatives), 1)
            readback.assert_called_once()

    def test_changed_or_malformed_frozen_manifest_rejected_before_counting(self):
        with tempfile.TemporaryDirectory() as root:
            _, manager, _, _ = frozen_namespace(root)
            path = Path(manager['manifest'])
            path.write_text(path.read_text()+'\n')
            with self.assertRaisesRegex(ValueError, 'manifest changed'):
                health.audit_weighted_manager(manager, root, set())
            path.write_text('{bad-json')
            manager['manifest_sha256'] = health.digest(path)
            with self.assertRaises(ValueError):
                health.audit_weighted_manager(manager, root, set())

    def test_duplicate_manifest_nodes_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            _, manager, manifest, row = frozen_namespace(root)
            manifest['jobs'].append(copy.deepcopy(row))
            path = Path(manager['manifest'])
            path.write_text(json.dumps(manifest))
            manager['manifest_sha256'] = health.digest(path)
            with self.assertRaisesRegex(ValueError, 'Duplicate weighted manifest'):
                health.audit_weighted_manager(manager, root, set())

    def test_missing_or_failed_gate_and_out_of_budget_rows_rejected(self):
        for mutation in ('missing', 'failed', 'wrong-budget'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as root:
                record, manager, _, row = frozen_namespace(root)
                path = Path(root)/'scientific_gate.json'
                if mutation == 'missing':
                    path.unlink()
                else:
                    gate = json.loads(path.read_text())
                    if mutation == 'failed':
                        gate['pass'] = False
                    else:
                        gate['production_plan'][row['stratum']]['chunks'] = 0
                    path.write_text(json.dumps(gate))
                    record['scientific_gate_sha256'] = health.digest(path)
                    write_receipts(root, record)
                with self.assertRaises((ValueError, OSError)):
                    health.audit_weighted_manager(manager, root, set())

    def test_pilots_never_enter_production_totals(self):
        with tempfile.TemporaryDirectory() as root:
            record, manager, manifest, row = frozen_namespace(root)
            row['stage'] = 'pilot'
            manifest['jobs'] = [row]
            path = Path(manager['manifest'])
            path.write_text(json.dumps(manifest))
            manager['manifest_sha256'] = health.digest(path)
            record.update(manifest_sha256=manager['manifest_sha256'], request=copy.deepcopy(row))
            write_receipts(root, record)
            (Path(root)/'scientific_gate.json').unlink()
            with patch.object(health, 'readback_weighted') as readback:
                summary, _, ids = health.audit_weighted_manager(manager, root, set())
            self.assertEqual((summary['completed_chunks'], summary['completed_events'], summary['completed_fixed_trials']), (0, 0, 0))
            self.assertEqual(ids, set())
            readback.assert_not_called()

    def test_mutated_eos_receipt_and_duplicate_cross_manager_identity_rejected(self):
        for mutation in ('eos-weight', 'cross-manager-id'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as root:
                record, manager, _, row = frozen_namespace(root)
                existing = set()
                if mutation == 'eos-weight':
                    record['semantic_audit']['weights'][0] *= 2
                    (Path(record['publication_directory'])/'publication_receipt.json').write_text(json.dumps(record))
                else:
                    existing.add((row['run'], 1, 1))
                with self.assertRaises(ValueError):
                    health.audit_weighted_manager(manager, root, existing)


class WeightedFailureIsolationTest(unittest.TestCase):
    def _audit_with_high_failure(self, root, failure):
        root = Path(root)
        receipts = root/'low_receipts'/'qcd_0to1'
        receipts.mkdir(parents=True)
        low_manifest = dict(receipt_base=str(root/'low_receipts'),
                            plan=dict(strata=[dict(id='qcd_0to1', jobs=1, events_per_job=1, target_events=1)]))
        # The production contract appends /receipts to its immutable namespace.
        namespace = root/'low_receipts'
        (namespace/'receipts').mkdir()
        receipts.rename(namespace/'receipts'/'qcd_0to1')
        receipts = namespace/'receipts'/'qcd_0to1'
        manifest_path = root/'low_manifest.json'
        manifest_path.write_text(json.dumps(low_manifest))
        low = dict(name='low', manifest=str(manifest_path), audit_contract='frozen_gen_receipts',
                   manifest_sha256=health.digest(manifest_path))
        payload = b'gen'
        receipt = dict(complete=True, manifest_sha256=low['manifest_sha256'], stratum='qcd_0to1',
                       pilot=False, chunk=0, events=1, requested_events=1, event_ids=[[1, 1, 1]],
                       artifacts=[dict(path='/synthetic/gen.root', bytes=len(payload),
                                       sha256=hashlib.sha256(payload).hexdigest())])
        (receipts/'part00000.json').write_text(json.dumps(receipt))
        high = dict(name='high', audit_contract=health.WEIGHTED_CONTRACT)
        request = dict(nonce='test', registry_sha256='b'*64, collector_pool='mock', managers=[high, low])
        auxiliary = SimpleNamespace(run=lambda: 1, luminosityBlock=lambda: 1, event=lambda: 1)
        event = SimpleNamespace(eventAuxiliary=lambda: auxiliary, getByLabel=lambda *args: None)
        tree = SimpleNamespace(GetEntries=lambda: 1)
        root_file = SimpleNamespace(IsZombie=lambda: False, TestBit=lambda bit: False,
                                    Get=lambda name: tree, Close=lambda: None)
        fake_root = SimpleNamespace(TFile=SimpleNamespace(Open=lambda path: root_file, kRecovered=1))

        class FakeHandle:
            def __init__(self, kind):
                self.kind = kind

            def isValid(self):
                return True

            def product(self):
                return SimpleNamespace(weight=lambda: 1.) if self.kind == 'GenEventInfoProduct' else [object()]

        fake_fwlite = SimpleNamespace(Events=lambda path: [event], Handle=FakeHandle)

        def fake_run(command):
            if command[0] == 'eos':
                return 'space=/eos/user/j/jniedzie/ maxlogicalbytes=1000 usedlogicalbytes=100 maxfiles=100 usedfiles=1'
            if command[0] == 'xrdcp':
                Path(command[-1]).write_bytes(payload)
                return ''
            raise AssertionError(command)

        with ExitStack() as stack:
            stack.enter_context(patch.dict(sys.modules, {'ROOT': fake_root, 'DataFormats': SimpleNamespace(),
                                                        'DataFormats.FWLite': fake_fwlite}))
            stack.enter_context(patch('shift_condor_native.query_account', return_value=[]))
            stack.enter_context(patch.object(health, 'run', side_effect=fake_run))
            stack.enter_context(patch.object(health, 'audit_weighted_manager', side_effect=failure))
            return health.audit(request)

    def test_confirmed_high_failure_preserves_completed_low_production(self):
        with tempfile.TemporaryDirectory() as root:
            result = self._audit_with_high_failure(root, ValueError('Mutated weighted receipt'))
        self.assertTrue(result['healthy'])
        self.assertEqual((result['completed_chunks'], result['completed_events']), (1, 1))
        self.assertEqual(result['managers']['low']['completed_events'], 1)
        self.assertFalse(result['managers']['high']['healthy'])
        self.assertTrue(result['manager_failures']['high']['verified'])
        self.assertEqual(result['representatives'][0]['manager'], 'low')

    def test_unknown_eos_or_service_error_is_not_a_confirmed_manager_failure(self):
        with tempfile.TemporaryDirectory() as root, self.assertRaisesRegex(OSError, 'EOS unavailable'):
            self._audit_with_high_failure(root, OSError('EOS unavailable'))


if __name__ == '__main__':
    unittest.main()
