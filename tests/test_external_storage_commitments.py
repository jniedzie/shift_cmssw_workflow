# Code style: AI-code. AI-generated sealed external reservation audit tests.
import hashlib
import importlib.util
import json
import copy
from pathlib import Path
import tempfile
import unittest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/read_shift_external_storage_commitments.py'
spec = importlib.util.spec_from_file_location('external_storage', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ExternalStorageTests(unittest.TestCase):
    def peer_fixture(self, folder):
        root = Path(folder)
        def write(name, value):
            path = root/name
            path.write_text(json.dumps(value))
            return dict(path=str(path), sha256=module.digest(path))
        capacity = write('capacity.json', dict(schema='shift-reviewed-shared-weekend-capacity-v1',
            shared_free_floor_bytes=90, total_extra_reservation_bytes=89, qcd_reserve_bytes=6))
        campaigns = []
        for name, events in (('main', 50), ('meson', 50), ('mass_gap', 5), ('epsilon', 5)):
            directory = root/name
            directory.mkdir()
            worklist = write(name+'_worklist.json', dict(total_additional_events=events,
                waves=[dict(wave_index=0, batches=[dict(events=events)])]))
            campaigns.append(dict(name=name, kind='statistics', directory=str(directory),
                                  bytes_per_event=2, worklist=worklist))
        policy = dict(schema='shift-dp-supplement-admission-v1', campaigns=campaigns,
                      shared_capacity_review=capacity, minimum_free_bytes=90,
                      minimum_free_files=20, maximum_peer_age_seconds=1200)
        policy_pin = write('peer_policy.json', policy)
        reader = root/'reader.py'
        reader.write_text('# Synthetic proxy to the real reviewed accounting function.\n'
                          'from run_dark_photon_statistics import supplement_campaign_claim\n')
        guards = {}
        for name in ('main', 'meson'):
            guards[name] = dict(write(name+'_guard.json', {'protected': 6}), field='protected')
        supplements = {name: write(name+'_gate.json', policy) for name in ('mass_gap', 'epsilon')}
        bindings = {}
        for name in ('main', 'meson', 'mass_gap', 'epsilon'):
            controller = root/(name+'_controller')
            controller.mkdir()
            config = dict(supplement_admission=supplements[name]) if name in supplements else dict(guard=guards[name]['path'])
            config_pin = write(str(controller/'config.json'), config)
            launcher = controller/'worker.py'
            launcher.write_text('# Synthetic pinned controller.\n')
            submit = controller/'controller.sub'
            submit.write_text('initialdir = '+str(controller)+'\nexecutable = /usr/bin/python3\n'
                'arguments = '+str(launcher)+' --config '+config_pin['path']+'\n'
                '+ShiftDPStatisticsTask = '+json.dumps(str(controller))+'\n')
            bindings[name] = dict(config=config_pin,
                submit=dict(path=str(submit), sha256=module.digest(submit)),
                launch_files=[dict(path=str(launcher), sha256=module.digest(launcher))],
                guard_reference_field='guard')
        return dict(schema='shift-statistics-peer-storage-authority-v1', policy=policy_pin,
            reader=dict(path=str(reader), sha256=module.digest(reader)),
            replace_qcd_reservation_with_stream_PREs=True,
            constrained_stream=dict(active_campaign_names=['main', 'meson'],
                protected_qcd_guards=guards, supplement_policies=supplements, controller_bindings=bindings,
                required_absent_stream_campaigns=['old_preview', 'closed_canary']))

    def rollover_binding(self, folder, original):
        controller = Path(folder) / 'meson_controller_v2'
        controller.mkdir()
        config = controller / 'config.json'
        config.write_text(Path(original['config']['path']).read_text())
        launcher = controller / 'worker.py'
        launcher.write_text('# Synthetic replacement controller.\n')
        submit = controller / 'controller.sub'
        submit.write_text('initialdir = ' + str(controller) + '\nexecutable = /usr/bin/python3\n'
            'arguments = ' + str(launcher) + ' --config ' + str(config) + '\n'
            '+ShiftDPStatisticsTask = ' + json.dumps(str(controller)) + '\n')
        return dict(config=dict(path=str(config), sha256=module.digest(config)),
            submit=dict(path=str(submit), sha256=module.digest(submit)),
            launch_files=[dict(path=str(launcher), sha256=module.digest(launcher))],
            guard_reference_field=original['guard_reference_field'])

    def test_peer_group_keeps_every_future_claim_and_counts_shared_transients_only_once(self):
        with tempfile.TemporaryDirectory() as folder:
            result = module.read_commitments([self.peer_fixture(folder)])
            self.assertEqual(result['required_free_bytes'], 304)  # 220 future +90 floor -6 QCD.
            self.assertEqual(result['required_free_files'], 20)
            row = result['authorities'][0]
            self.assertEqual(row['remaining_canonical_bytes'], 220)
            self.assertTrue(all(not claim['peer_healthy'] for claim in row['claims']))
            self.assertEqual(result['stream_capacity']['constrained_required_free_bytes'], 284)
            self.assertEqual(result['stream_capacity']['deferred_remaining_canonical_bytes'], 20)
            self.assertEqual(result['stream_capacity']['protected_stream_bytes'], 6)
            self.assertEqual(len(result['stream_capacity']['controller_bindings']), 4)
            self.assertTrue(result['stream_capacity']['full_future_remains_reported'])
            self.assertEqual(row['retirement_credit_bytes'], 0)

    def test_controller_rollover_keeps_one_campaign_claim_and_both_native_bindings(self):
        with tempfile.TemporaryDirectory() as folder:
            authority = self.peer_fixture(folder)
            bindings = authority['constrained_stream']['controller_bindings']
            original = bindings['meson']
            replacement = self.rollover_binding(folder, original)
            bindings['meson'] = [original, replacement]
            result = module.read_commitments([authority])
            self.assertEqual(result['required_free_bytes'], 304)
            self.assertEqual(result['stream_capacity']['constrained_required_free_bytes'], 284)
            self.assertEqual(len(result['authorities'][0]['claims']), 4)
            self.assertEqual(result['authorities'][0]['remaining_canonical_bytes'], 220)
            controllers = result['stream_capacity']['controller_bindings']
            meson = [row for row in controllers if row['name'] == 'meson']
            self.assertEqual(len(controllers), 5)
            self.assertEqual(len(meson), 2)
            expected = {str(Path(row['config']['path']).parent) for row in bindings['meson']}
            self.assertEqual({row['expected_native']['Iwd'] for row in meson}, expected)
            self.assertTrue(expected.issubset(result['stream_capacity']['allowed_bsm_directories']))

    def replacement_authority(self, folder, original, *, policy_change=None, config_change=None):
        root = Path(folder)
        replacement = copy.deepcopy(original)
        policy = module.read_json(Path(original['policy']['path']))
        proof = root/'terminal.json'
        proof.write_text(json.dumps({'schema': 'synthetic-terminal-proof'}))
        policy['campaigns'][0]['terminal_completion'] = {
            'path': str(proof), 'sha256': module.digest(proof)}
        if policy_change:
            policy_change(policy)
        path = root/'replacement_policy.json'
        path.write_text(json.dumps(policy))
        replacement['policy'] = dict(path=str(path), sha256=module.digest(path))
        for name in replacement['constrained_stream']['supplement_policies']:
            gate = root/(name+'_replacement_gate.json')
            gate.write_text(json.dumps(policy))
            replacement['constrained_stream']['supplement_policies'][name] = {
                'path': str(gate), 'sha256': module.digest(gate)}
        for name, binding in list(replacement['constrained_stream']['controller_bindings'].items()):
            directory = root/(name+'_replacement')
            directory.mkdir()
            config = module.read_json(Path(binding['config']['path']))
            if name in replacement['constrained_stream']['supplement_policies']:
                config['supplement_admission'] = replacement['constrained_stream']['supplement_policies'][name]
            if config_change:
                config_change(name, config)
            path = directory/'config.json'
            path.write_text(json.dumps(config))
            launcher = directory/'controller.py'
            launcher.write_text('# Synthetic frozen controller replacement.\n')
            submit = directory/'controller.sub'
            submit.write_text('initialdir = '+str(directory)+'\nexecutable = /usr/bin/python3\n'
                'arguments = '+str(launcher)+' --config '+str(path)+'\n'
                '+ShiftDPStatisticsTask = '+json.dumps(str(directory))+'\n')
            replacement['constrained_stream']['controller_bindings'][name] = dict(
                config=dict(path=str(path), sha256=module.digest(path)),
                submit=dict(path=str(submit), sha256=module.digest(submit)),
                launch_files=[dict(path=str(launcher), sha256=module.digest(launcher))],
                guard_reference_field=binding['guard_reference_field'])
        return replacement

    def test_supervisor_authority_replacement_preserves_budget_and_native_bindings(self):
        with tempfile.TemporaryDirectory() as folder:
            original = self.peer_fixture(folder)
            before = copy.deepcopy(original)
            replacement = self.replacement_authority(folder, original)
            result = module.reviewed_peer_authority_replacement(original, replacement)
            self.assertEqual(result, replacement)
            self.assertEqual(original, before)
            self.assertIsNot(result, replacement)

    def test_supervisor_authority_replacement_rejects_changed_floors_and_worklists(self):
        changes = [lambda p: p.update(minimum_free_bytes=89),
                   lambda p: p['campaigns'][0].update(bytes_per_event=1),
                   lambda p: p['campaigns'][0].update(directory='/different'),
                   lambda p: p['campaigns'].pop()]
        for change in changes:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as folder:
                original = self.peer_fixture(folder)
                replacement = self.replacement_authority(folder, original, policy_change=change)
                with self.assertRaises(ValueError):
                    module.reviewed_peer_authority_replacement(original, replacement)

    def test_supervisor_authority_replacement_rejects_unbound_or_scientifically_changed_controls(self):
        for case in ('missing_controller', 'changed_science', 'changed_launch', 'changed_proof'):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as folder:
                original = self.peer_fixture(folder)
                change = (lambda name, config: config.update(worklist='/different')) if case == 'changed_science' else None
                replacement = self.replacement_authority(folder, original, config_change=change)
                if case == 'missing_controller':
                    replacement['constrained_stream']['controller_bindings'].pop('main')
                elif case == 'changed_launch':
                    pin = replacement['constrained_stream']['controller_bindings']['main']['submit']
                    path = Path(pin['path'])
                    path.write_text(path.read_text().replace('initialdir = ', 'initialdir = /wrong'))
                    pin['sha256'] = module.digest(path)
                elif case == 'changed_proof':
                    Path(folder, 'terminal.json').write_text('{}')
                with self.assertRaises(ValueError):
                    module.reviewed_peer_authority_replacement(original, replacement)

    def test_controller_rollover_rejects_a_different_protected_guard(self):
        with tempfile.TemporaryDirectory() as folder:
            authority = self.peer_fixture(folder)
            bindings = authority['constrained_stream']['controller_bindings']
            original = bindings['meson']
            replacement = self.rollover_binding(folder, original)
            config = Path(replacement['config']['path'])
            config.write_text(json.dumps(dict(guard=authority['constrained_stream'][
                'protected_qcd_guards']['main']['path'])))
            replacement['config']['sha256'] = module.digest(config)
            bindings['meson'] = [original, replacement]
            with self.assertRaisesRegex(ValueError, 'pinned QCD guard'):
                module.read_commitments([authority])

    def test_controller_rollover_rejects_empty_duplicate_and_unbounded_bindings(self):
        for case in ('empty', 'duplicate', 'too_many', 'invalid_record'):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as folder:
                authority = self.peer_fixture(folder)
                bindings = authority['constrained_stream']['controller_bindings']
                original = bindings['meson']
                records = dict(empty=[], duplicate=[original, copy.deepcopy(original)],
                    too_many=[original] * 3, invalid_record=[original, []])
                bindings['meson'] = records[case]
                with self.assertRaises(ValueError):
                    module.read_commitments([authority])

    def additional_meson(self, folder, authority):
        root = Path(folder)
        policy = module.read_json(Path(authority['policy']['path']))
        directory = root/'new_unified_meson'
        directory.mkdir()
        (directory/'tools').mkdir()
        config = dict(schema='shift-dp-meson-production-v1', production_mechanism='meson-radiative',
            directory=str(directory), tools=str(directory/'tools'), minimum_eos_free_bytes=90,
            adaptive_directory=policy['campaigns'][0]['directory'],
            additional_reserved_worklists=[dict(name=row['name'], worklist=row['worklist'],
                bytes_per_event=row['bytes_per_event']) for row in policy['campaigns'][1:]],
            batches=[dict(batch='first', source_collisions=4000), dict(batch='second', source_collisions=5000)])
        path = directory/'config.json'
        path.write_text(json.dumps(config))
        worklist = dict(path=str(path), sha256=module.digest(path))
        forecast_path = root/'new_meson_forecast.json'
        forecast_path.write_text(json.dumps(dict(complete=True, worklist_sha256=worklist['sha256'],
            per_batch_forecast_bytes=dict(first=100,second=200), total_meson_forecast_bytes=300)))
        program = directory/'tools/run_dark_photon_meson_production.py'
        program.write_text('# Synthetic controller authority.\n')
        wrapper = directory/'lcg_controller.sh'
        wrapper.write_text('#!/bin/bash\nexec python3 '+str(program)+' --directory '+str(directory)
            +' --seal "$1" --action controller\n')
        seal = directory/'seal.json'
        seal.write_text(json.dumps(dict(files={str(p):module.digest(p) for p in (path,program,wrapper)})))
        submit = directory/'controller.sub'
        submit.write_text('universe = vanilla\ninitialdir = '+str(directory)+'\nexecutable = '+str(wrapper)+'\n'
            'arguments = '+module.digest(seal)+'\n'
            '+ShiftDPStatisticsTask = '+json.dumps(str(directory))+'\n')
        entry = dict(campaign=dict(name='new_meson',kind='meson',directory=str(directory),worklist=worklist,
            forecast=dict(path=str(forecast_path),sha256=module.digest(forecast_path))),
            controller_binding=dict(config=worklist,submit=dict(path=str(submit),sha256=module.digest(submit)),
                seal=dict(path=str(seal),sha256=module.digest(seal)),
                launch_files=[dict(path=str(p),sha256=module.digest(p)) for p in (program,wrapper)]))
        authority['constrained_stream']['additional_meson_campaigns'] = [entry]
        return entry

    def test_new_meson_future_is_additive_and_deferred_while_older_peers_stay_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            authority = self.peer_fixture(folder)
            old = module.read_commitments([authority])
            original = copy.deepcopy(authority)
            entry = self.additional_meson(folder, authority)
            new = module.read_commitments([authority])
            self.assertEqual(new['required_free_bytes'], old['required_free_bytes']+300)
            self.assertEqual(new['required_free_files'], old['required_free_files'])
            self.assertEqual(new['stream_capacity']['constrained_required_free_bytes'],
                             old['stream_capacity']['constrained_required_free_bytes'])
            self.assertEqual(new['stream_capacity']['deferred_remaining_canonical_bytes'], 320)
            self.assertIn(entry['campaign']['directory'], new['stream_capacity']['deferred_bsm_directories'])
            self.assertEqual(len(new['stream_capacity']['controller_bindings']),5)
            self.assertEqual(new['authorities'][0]['fixed_reserve_bytes'],84)
            # The additional claim does not edit any original campaign or guard.
            old_again = copy.deepcopy(authority)
            old_again['constrained_stream'].pop('additional_meson_campaigns')
            self.assertEqual(old_again, original)

    def operational_additional_meson(self, folder, entry):
        original = Path(entry['campaign']['worklist']['path'])
        original_seal = entry['controller_binding']['seal']
        root = Path(folder)/'meson_operations'
        (root/'tools').mkdir(parents=True)
        config = module.read_json(original)
        config['physics_tools'] = config['tools']
        path = root/'config.json'
        path.write_text(json.dumps(config))
        program = root/'tools/run_dark_photon_meson_production.py'
        program.write_text('# Synthetic separately frozen supervision.\n')
        wrapper = root/'controller.sh'
        wrapper.write_text('#!/bin/bash\nexec python3 '+str(program)+' --directory '+str(original.parent)
            +' --seal '+original_seal['sha256']+' --action controller --controller-config '+str(path)
            +' --controller-seal "$1"\n')
        seal = root/'seal.json'
        seal.write_text(json.dumps({'files': {str(p): module.digest(p) for p in
            (path, program, wrapper, original, Path(original_seal['path']))}}))
        submit = root/'controller.sub'
        submit.write_text('universe = vanilla\ninitialdir = '+str(root)+'\nexecutable = '+str(wrapper)
            +'\narguments = '+module.digest(seal)+'\n+ShiftDPStatisticsTask = '+json.dumps(str(root))+'\n')
        entry['controller_binding'] = dict(config=dict(path=str(path),sha256=module.digest(path)),
            production_config=entry['campaign']['worklist'], seal=original_seal,
            controller_seal=dict(path=str(seal),sha256=module.digest(seal)),
            submit=dict(path=str(submit),sha256=module.digest(submit)),
            launch_files=[dict(path=str(p),sha256=module.digest(p)) for p in (program,wrapper)])
        return root

    def test_operational_meson_replacement_keeps_canonical_forecast_and_registers_exact_launcher(self):
        with tempfile.TemporaryDirectory() as folder:
            authority = self.peer_fixture(folder)
            entry = self.additional_meson(folder, authority)
            original = copy.deepcopy(entry['campaign'])
            root = self.operational_additional_meson(folder, entry)
            result = module.read_commitments([authority])
            self.assertEqual(result['required_free_bytes'], 604)
            self.assertEqual(entry['campaign'], original)
            self.assertIn(str(root), result['stream_capacity']['allowed_bsm_directories'])
            self.assertIn(original['directory'], result['stream_capacity']['deferred_bsm_directories'])

    def test_operational_meson_replacement_rejects_changed_source_and_native_wrapper(self):
        for case in ('source', 'wrapper'):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as folder:
                authority = self.peer_fixture(folder)
                entry = self.additional_meson(folder, authority)
                root = self.operational_additional_meson(folder, entry)
                if case == 'source':
                    config = module.read_json(root/'config.json')
                    config['batches'][0]['source_collisions'] += 1
                    (root/'config.json').write_text(json.dumps(config))
                    entry['controller_binding']['config']['sha256'] = module.digest(root/'config.json')
                else:
                    wrapper = root/'controller.sh'
                    wrapper.write_text(wrapper.read_text().replace('--action controller', '--action source'))
                    entry['controller_binding']['launch_files'][1]['sha256'] = module.digest(wrapper)
                with self.assertRaises(ValueError):
                    module.read_commitments([authority])

    def test_new_meson_peer_requires_unchanged_old_claims_exact_forecast_and_launch(self):
        for change in ('omit_peer', 'wrong_worklist', 'oversized', 'changed_launcher', 'duplicate'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as folder:
                authority = self.peer_fixture(folder)
                entry = self.additional_meson(folder, authority)
                if change == 'omit_peer':
                    path = Path(entry['campaign']['worklist']['path'])
                    config = json.loads(path.read_text())
                    config['additional_reserved_worklists'].pop()
                    path.write_text(json.dumps(config))
                    entry['campaign']['worklist']['sha256'] = module.digest(path)
                    forecast = Path(entry['campaign']['forecast']['path'])
                    content = json.loads(forecast.read_text())
                    content['worklist_sha256'] = module.digest(path)
                    forecast.write_text(json.dumps(content))
                    entry['campaign']['forecast']['sha256'] = module.digest(forecast)
                elif change in ('wrong_worklist','oversized'):
                    path = Path(entry['campaign']['forecast']['path'])
                    content = json.loads(path.read_text())
                    content['worklist_sha256' if change == 'wrong_worklist' else 'total_meson_forecast_bytes'] = (
                        'f'*64 if change == 'wrong_worklist' else 30000000001)
                    path.write_text(json.dumps(content))
                    entry['campaign']['forecast']['sha256'] = module.digest(path)
                elif change == 'changed_launcher':
                    Path(entry['controller_binding']['launch_files'][0]['path']).write_text('# changed\n')
                else:
                    authority['constrained_stream']['additional_meson_campaigns'].append(copy.deepcopy(entry))
                with self.assertRaises(ValueError):
                    module.read_commitments([authority])

    def test_additional_meson_wrapper_must_really_derive_the_reserved_config(self):
        with tempfile.TemporaryDirectory() as folder:
            authority = self.peer_fixture(folder)
            entry = self.additional_meson(folder, authority)
            binding = entry['controller_binding']
            wrapper = Path(binding['launch_files'][1]['path'])
            original = wrapper.read_text()
            wrong = original.replace('--directory '+entry['campaign']['directory'],
                                     '--directory /different/unreviewed_source')
            wrapper.write_text(wrong)
            binding['launch_files'][1]['sha256'] = module.digest(wrapper)
            seal = Path(binding['seal']['path'])
            value = json.loads(seal.read_text())
            value['files'][str(wrapper)] = module.digest(wrapper)
            seal.write_text(json.dumps(value))
            binding['seal']['sha256'] = module.digest(seal)
            submit = Path(binding['submit']['path'])
            lines = submit.read_text().splitlines()
            submit.write_text('\n'.join('arguments = '+module.digest(seal) if line.startswith('arguments =')
                                      else line for line in lines)+'\n')
            binding['submit']['sha256'] = module.digest(submit)
            with self.assertRaisesRegex(ValueError, 'exact sealed directory configuration'):
                module.read_commitments([authority])

    def test_peer_group_rejects_changed_guard_gate_or_reader_and_duplicate_budgets(self):
        for target in ('guard', 'gate', 'reader', 'duplicate', 'config_gate', 'launch'):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as folder:
                authority = self.peer_fixture(folder)
                if target == 'guard':
                    pin = authority['constrained_stream']['protected_qcd_guards']['main']
                    Path(pin['path']).write_text('{"protected":0}')
                    pin['sha256'] = module.digest(Path(pin['path']))
                elif target == 'gate':
                    pin = authority['constrained_stream']['supplement_policies']['epsilon']
                    value = json.loads(Path(pin['path']).read_text())
                    value['minimum_free_bytes'] = 1
                    Path(pin['path']).write_text(json.dumps(value))
                    pin['sha256'] = module.digest(Path(pin['path']))
                elif target == 'reader':
                    Path(authority['reader']['path']).write_text('# changed\n')
                elif target == 'config_gate':
                    pin = authority['constrained_stream']['controller_bindings']['epsilon']['config']
                    Path(pin['path']).write_text('{}')
                    pin['sha256'] = module.digest(Path(pin['path']))
                elif target == 'launch':
                    pin = authority['constrained_stream']['controller_bindings']['main']['launch_files'][0]
                    Path(pin['path']).write_text('# Changed launcher.\n')
                with self.assertRaises(ValueError):
                    module.read_commitments([authority, authority] if target == 'duplicate' else [authority])

    def fixture(self, folder, complete=3):
        root = Path(folder)
        (root / 'plan.json').write_text(json.dumps({'complete': complete}))
        (root / 'gen_inventory.json').write_text('{}')
        (root / 'storage_budget.json').write_text(json.dumps(dict(detector_chunks=10,
            bytes_per_detector_chunk=10, fixed_reservation_bytes=100)))
        (root / 'chunk_worker.py').write_text('''# Synthetic test fixture.
def completed_chunk_count(plan, inventory, seal, plan_hash, statuses, maximum):
    return plan['complete']
def quota_requirements(budget, complete):
    return budget['fixed_reservation_bytes'] + (10-complete)*10, 20+(10-complete)*2
''')
        (root / 'external.py').write_text('''# Synthetic test fixture.
def external_chunk_reservations(budget):
    return 30, 4, [{'unfinished_chunks': 3}]
''')
        files = {str(path):module.digest(path) for path in root.iterdir() if path.is_file()}
        (root / 'seal.json').write_text(json.dumps({'files': files}))
        return dict(production_directory=str(root), completion_reader='chunk_worker.py',
            external_reader='external.py', seal_sha256=module.digest(root/'seal.json'),
            plan_sha256=module.digest(root/'plan.json'),
            gen_inventory_sha256=module.digest(root/'gen_inventory.json'),
            storage_budget_sha256=module.digest(root/'storage_budget.json'))

    def test_sealed_authoritative_remaining_and_nested_reserves(self):
        with tempfile.TemporaryDirectory() as folder:
            result = module.read_commitments([self.fixture(folder)])
            self.assertEqual(result['required_free_bytes'], 200)
            self.assertEqual(result['required_free_files'], 38)
            self.assertEqual(result['authorities'][0]['fixed_reserve_bytes'], 100)

    def test_full_detector_completion_retains_the_fixed_future_publication_floor(self):
        with tempfile.TemporaryDirectory() as folder:
            result = module.read_commitments([self.fixture(folder, complete=10)])
            self.assertEqual(result['required_free_bytes'], 130)
            self.assertEqual(result['required_free_files'], 24)

    def test_changed_budget_or_worker_is_rejected(self):
        for name in ['storage_budget.json', 'chunk_worker.py']:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as folder:
                authority = self.fixture(folder)
                with (Path(folder)/name).open('a') as stream:
                    stream.write('\n# changed')
                with self.assertRaises(ValueError):
                    module.read_commitments([authority])

    def test_missing_authority_is_unknown(self):
        with tempfile.TemporaryDirectory() as folder:
            authority = self.fixture(folder)
            (Path(folder)/'gen_inventory.json').unlink()
            with self.assertRaises(FileNotFoundError):
                module.read_commitments([authority])

    def test_duplicate_budget_is_not_counted_or_credited_twice(self):
        with tempfile.TemporaryDirectory() as folder:
            authority = self.fixture(folder)
            with self.assertRaises(ValueError):
                module.read_commitments([authority, authority])

    def test_impossible_completion_count_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            authority = self.fixture(folder, complete=11)
            with self.assertRaises(ValueError):
                module.read_commitments([authority])

    def test_no_external_campaigns_requires_no_external_space(self):
        result = module.read_commitments([])
        self.assertEqual(result['required_free_bytes'], 0)
        self.assertEqual(result['required_free_files'], 0)


if __name__ == '__main__':
    unittest.main()
