# Code style: AI-code. AI-generated BSM production isolation and resource tests.
"""Check event reuse rejection, immutable external authorities and DAG barriers."""
import copy
import ast
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from dark_photon_publication_quota import sha256, external_chunk_reservations
from prepare_dark_photon_production import check_gen, dag_text, detector_timeout_settings, extend_worker


def write(path, value):
    path.write_text(json.dumps(value))


class GenIsolation(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        contract = dict(requested_events=2, mass_gev=12., epsilon=1e-8, seed=10001, run_number=10001)
        write(self.base / 'contract.json', contract)
        write(self.base / 'frozen.json', contract)
        write(self.base / 'manifest.json', dict(output_sha256='a'*64))
        ids = [[10001,1,1], [10001,1,2]]
        write(self.base / 'validation.json', dict(runtime_validated=True, events=2, event_ids=ids,
              sum_weights=2., sum_weights_squared=2., event_rows=[dict(weight=1.), dict(weight=1.)]))
        remote = '/eos/user/t/test/shift/darkphoton_m12_lab_1000m/settings/GEN/gen.root'
        row = dict(contract, point='m12_lab_1000m', events=2, gen_directory=str(self.base),
                   signal_contract=str(self.base/'frozen.json'), gen_remote_path=remote,
                   final_output_base=remote[:-len('/GEN/gen.root')])
        self.plan = dict(points=[row])
        parent = dict(contract, events=2, event_ids=ids, native_sumw=2., native_sumw2=2.,
                      gen_path=remote, gen_sha256='a'*64)
        for name,key in [('contract.json','contract_sha256'),('manifest.json','manifest_sha256'),
                         ('validation.json','validation_sha256')]:
            parent[key] = sha256(self.base/name)
        self.inventory = dict(complete=True, points={row['point']:parent})
        self.publication = dict(complete=True, inputs=[dict(point=row['point'], path=remote,
                                sha256='a'*64, readback_sha256='a'*64, bytes=1000)])
        self.previous = self.base/'previous.json'
        write(self.previous, dict(points={'other':dict(event_ids=[[9001,1,1]],seed=9001)}))

    def test_complete_gen_is_accepted_without_local_root_payload(self):
        result = check_gen(self.plan, self.inventory, self.publication, [self.previous])
        self.assertTrue(result['complete'])
        self.assertEqual(result['events'],2)
        self.assertFalse((self.base/'gen.root').exists())

    def test_explicit_accepted_mode_preserves_gaps_in_complete_parent_population(self):
        ids=[[10001,1,2],[10001,1,5]]
        contract=json.loads((self.base/'contract.json').read_text())
        contract.update(event_budget_mode='outputAccepted',maximum_framework_events=20)
        write(self.base/'contract.json',contract);write(self.base/'frozen.json',contract)
        audit=json.loads((self.base/'validation.json').read_text())
        audit['event_ids']=ids;write(self.base/'validation.json',audit)
        parent=self.inventory['points']['m12_lab_1000m']
        parent.update(event_ids=ids,contract_sha256=sha256(self.base/'contract.json'),
                      validation_sha256=sha256(self.base/'validation.json'))
        result=check_gen(self.plan,self.inventory,self.publication,[self.previous])
        self.assertTrue(result['complete'])
        self.assertEqual(parent['event_ids'],ids)

    def test_reused_events_or_rng_seed_fail(self):
        for parent in [dict(event_ids=[[10001,1,1]],seed=9999),
                       dict(event_ids=[[9999,1,1]],seed=10001)]:
            write(self.previous,dict(points={'previous':parent}))
            with self.assertRaisesRegex(ValueError,'overlap'):
                check_gen(self.plan,self.inventory,self.publication,[self.previous])

    def test_retained_audit_revision_hashes_are_required(self):
        revision=self.base/'audit_revisions/revision0001'
        revision.mkdir(parents=True)
        (revision/'audit_dark_photon_gen.py').write_text('# synthetic revision audit\n')
        (revision/'pythia_generation_ledger.py').write_text('# synthetic ledger parser\n')
        write(revision/'previous_manifest.json',dict(status='failed'))
        manifest=json.loads((self.base/'manifest.json').read_text())
        manifest['audit_recovery']=dict(script=str(revision/'audit_dark_photon_gen.py'),
            sha256=sha256(revision/'audit_dark_photon_gen.py'),
            ledger_script_sha256=sha256(revision/'pythia_generation_ledger.py'),
            previous_manifest_sha256=sha256(revision/'previous_manifest.json'))
        write(self.base/'manifest.json',manifest)
        self.inventory['points']['m12_lab_1000m']['manifest_sha256']=sha256(self.base/'manifest.json')
        self.assertTrue(check_gen(self.plan,self.inventory,self.publication,[self.previous])['complete'])
        (revision/'audit_dark_photon_gen.py').write_text('# changed revision audit\n')
        with self.assertRaisesRegex(ValueError,'evidence changed'):
            check_gen(self.plan,self.inventory,self.publication,[self.previous])

    def test_missing_gen_or_changed_publication_does_not_launch(self):
        for mutation in [lambda value:value.update(complete=False),
                         lambda value:value['inputs'][0].update(readback_sha256='b'*64),
                         lambda value:value['inputs'].append(copy.deepcopy(value['inputs'][0]))]:
            publication=copy.deepcopy(self.publication)
            mutation(publication)
            with self.assertRaises(ValueError):
                check_gen(self.plan,self.inventory,publication,[self.previous])

    def test_changed_audit_metadata_fails(self):
        audit=json.loads((self.base/'validation.json').read_text())
        audit['event_rows'][1]['weight']=2.
        write(self.base/'validation.json',audit)
        with self.assertRaisesRegex(ValueError,'weights'):
            check_gen(self.plan,self.inventory,self.publication,[self.previous])


class ExternalReservations(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base=Path(self.temporary.name)
        (self.base/'statuses').mkdir()
        # Synthetic receipt validator: a terminal marker with another seal
        # cannot release a chunk's reservation.
        (self.base/'chunk_worker.py').write_text('''import json
def completed_chunk_count(plan, inventory, seal_hash, plan_hash, directory, maximum):
    count=0
    for path in directory.glob('*.json'):
        try: row=json.loads(path.read_text())
        except json.JSONDecodeError: continue
        if row.get('complete') is not True: continue
        if row.get('seal_sha256')!=seal_hash: raise ValueError('Foreign complete receipt')
        count+=1
    return count
''')
        write(self.base/'plan.json',dict(points=[]))
        write(self.base/'gen_inventory.json',dict(points={}))
        write(self.base/'storage_budget.json',dict(detector_chunks=3,bytes_per_detector_chunk=100,
                                                 files_per_detector_chunk=7))
        write(self.base/'seal.json',dict(files={str(self.base/name):sha256(self.base/name)
              for name in ['chunk_worker.py','plan.json','gen_inventory.json','storage_budget.json']}))
        self.authority=dict(production_directory=str(self.base),seal_sha256=sha256(self.base/'seal.json'),
                            plan_sha256=sha256(self.base/'plan.json'),
                            gen_inventory_sha256=sha256(self.base/'gen_inventory.json'))
        self.budget=dict(external_reservations=[self.authority])

    def test_running_and_malformed_receipts_keep_their_full_reservations(self):
        write(self.base/'statuses/complete.json',dict(complete=True,seal_sha256=self.authority['seal_sha256']))
        write(self.base/'statuses/running.json',dict(complete=False))
        (self.base/'statuses/in_progress.json').write_text('{')
        size,files,details=external_chunk_reservations(self.budget)
        self.assertEqual((size,files),(200,14))
        self.assertEqual(details[0]['complete_chunks'],1)

    def test_changed_authority_or_worker_fails_closed(self):
        for name in ('plan.json','chunk_worker.py'):
            original=(self.base/name).read_text()
            (self.base/name).write_text(original+'\n# modified')
            with self.assertRaisesRegex(ValueError,'changed'):
                external_chunk_reservations(self.budget)
            (self.base/name).write_text(original)

    def test_foreign_complete_receipt_and_duplicate_reservation_fail(self):
        write(self.base/'statuses/foreign.json',dict(complete=True,seal_sha256='b'*64))
        with self.assertRaisesRegex(ValueError,'Foreign'):
            external_chunk_reservations(self.budget)
        (self.base/'statuses/foreign.json').unlink()
        self.budget['external_reservations'].append(copy.deepcopy(self.authority))
        with self.assertRaisesRegex(ValueError,'distinct'):
            external_chunk_reservations(self.budget)


class ProductionBarriers(unittest.TestCase):
    def test_complete_detector_unions_precede_finalizers_and_collector(self):
        plan=dict(points=[dict(point=name,chunks=[dict(chunk=0),dict(chunk=1)])
                          for name in ['m12_prompt','m12_lab_1000m']])
        dag=dag_text(plan,Path('/unsubmitted/new'),['m12_prompt'],80,3)
        self.assertIn('PARENT D_m12_prompt_000 CHILD D_m12_lab_1000m_000',dag)
        self.assertIn('PARENT D_m12_lab_1000m_000 D_m12_lab_1000m_001 D_m12_prompt_000 CHILD F_m12_lab_1000m',dag)
        self.assertIn('PARENT F_m12_prompt F_m12_lab_1000m CHILD COLLECT',dag)
        self.assertIn('MAXJOBS DETECTOR 80\nMAXJOBS FINALIZER 3',dag)
        weekend=dag_text(plan,Path('/unsubmitted/weekend'),['m12_prompt'],300,2)
        self.assertIn('MAXJOBS DETECTOR 300\nMAXJOBS FINALIZER 2',weekend)


class DetectorDeadlines(unittest.TestCase):
    def test_defaults_and_reviewed_long_event_deadlines(self):
        self.assertEqual(detector_timeout_settings(SimpleNamespace()), dict(
            event_stall_timeout_seconds=1800, first_event_stall_timeout_seconds=3600,
            detector_timeout_seconds=21600))
        reviewed = SimpleNamespace(event_stall_timeout=7200, first_event_stall_timeout=7200,
                                   detector_timeout=43200)
        self.assertEqual(detector_timeout_settings(reviewed), dict(
            event_stall_timeout_seconds=7200, first_event_stall_timeout_seconds=7200,
            detector_timeout_seconds=43200))

    def test_invalid_deadlines_are_rejected_before_preparing_files(self):
        for changed in (dict(event_stall_timeout=0), dict(first_event_stall_timeout=-1),
                        dict(detector_timeout=72001), dict(detector_timeout=True),
                        dict(event_stall_timeout=7200, first_event_stall_timeout=3600),
                        dict(detector_timeout=3600)):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                detector_timeout_settings(SimpleNamespace(**changed))

    def test_generated_worker_passes_the_frozen_deadlines_to_both_guards(self):
        qualified = Path(__file__).resolve().parents[2] / 'validation/bsm_stage2_20261013/production/chunk_worker.py'
        if not qualified.is_file():
            self.skipTest('Qualified deployment is not available in this checkout')
        source = extend_worker(qualified.read_text(), 'nano-metadata-v1')
        tree = ast.parse(source)
        run = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'run')
        attempt = next(node for node in run.body if isinstance(node, ast.Try))
        command_index = next(index for index, node in enumerate(attempt.body)
                             if isinstance(node, ast.Assign) and any(
                                 isinstance(target, ast.Name) and target.id == 'command' for target in node.targets))
        invocation = ast.Module(body=attempt.body[command_index:command_index+2], type_ignores=[])
        plan = detector_timeout_settings(SimpleNamespace(event_stall_timeout=7200,
            first_event_stall_timeout=7200, detector_timeout=43200))
        with tempfile.TemporaryDirectory() as directory, patch('subprocess.run') as launch:
            root = Path(directory)
            namespace = dict(sys=sys, B=root, gen=root/'gen', output=root/'detector', work=root,
                             chunk=dict(count=20, skip=340, job=4000017), plan=plan,
                             subprocess=__import__('subprocess'))
            exec(compile(invocation, '<generated-worker-invocation>', 'exec'), namespace)
            command = launch.call_args.args[0]
            self.assertEqual(command[command.index('--job')+1], '4000017')
            self.assertEqual(command[command.index('--skip')+1], '340')
            self.assertEqual(command[command.index('--count')+1], '20')
            self.assertEqual(command[command.index('--event-stall-timeout')+1], '7200')
            self.assertEqual(command[command.index('--first-event-stall-timeout')+1], '7200')
            self.assertEqual(command[command.index('--timeout')+1], '43200')
            self.assertEqual(launch.call_args.kwargs['timeout'], 43800)
            self.assertTrue(launch.call_args.kwargs['check'])


if __name__=='__main__':
    unittest.main()
