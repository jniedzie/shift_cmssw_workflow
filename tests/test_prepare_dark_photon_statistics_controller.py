# Code style: AI-code. AI-generated frozen statistics-rollover regression tests.
import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import prepare_dark_photon_statistics_controller as replacement
import run_dark_photon_statistics as statistics


class StatisticsControllerRollover(unittest.TestCase):
    def pin(self, path, value):
        path.write_text(json.dumps(value))
        return dict(path=str(path), sha256=statistics.sha(path))

    def peer(self, root, name):
        directory = root/name
        directory.mkdir()
        card = directory/'model.json'
        card.write_text('{"channels": ["eta->gamma_Aprime"]}')
        config = self.pin(directory/'config.json', dict(schema='shift-dp-meson-production-v1',
            directory=str(directory), production_mechanism='meson-radiative', model_card=str(card),
            batches=[dict(batch='canary'), dict(batch='bulk')]))
        forecast = self.pin(directory/'forecast.json', dict(complete=True,
            worklist_sha256=config['sha256'], model_card_sha256=statistics.sha(card),
            per_batch_forecast_bytes=dict(canary=100, bulk=200), total_meson_forecast_bytes=300))
        return dict(kind='meson', name=name, directory=str(directory), worklist=config, forecast=forecast)

    def fixture(self, root):
        science = root/'science'
        science.mkdir()
        (science/'controller.lock').write_text('original lock')
        (science/'state.json').write_text('{"waves": {"0": {"gen_cluster": 123}}}')
        foreign = root/'foreign_authority.json'
        foreign.write_text('{"untouched": true}')
        template = root/'template'
        tools = template/'tools'
        tools.mkdir(parents=True)
        for name in ('run_dark_photon_statistics.py', 'shift_condor_native.py'):
            shutil.copy2(Path(statistics.__file__).with_name(name), tools/name)
        (tools/'retained.py').write_text('# Exact frozen source.\n')
        policy = dict(schema='shift-dp-supplement-admission-v1', minimum_free_bytes=90000000000,
            minimum_free_files=20000, maximum_peer_age_seconds=1200, maximum_quota_age_seconds=300,
            campaigns=[dict(kind='statistics', name='main', directory=str(root/'main'),
                            worklist=dict(path=str(foreign), sha256=statistics.sha(foreign)),
                            terminal_completion=dict(path='/preserved/terminal', sha256='a'*64))],
            required_cohorts=[dict(directory='/preserved/canary', expected_events=26)],
            required_sample_metadata=dict(path='/preserved/sample_metadata'),
            shared_capacity_review=dict(path=str(foreign), sha256=statistics.sha(foreign)))
        policy_pin = self.pin(template/'admission_policy.json', policy)
        wrapper = template/'lcg_controller.sh'
        wrapper.write_text('#!/bin/bash\nexec python3 '+str(tools/'run_dark_photon_statistics.py')+' "$@"\n')
        wrapper.chmod(0o750)
        dependencies = {str(path):statistics.sha(path) for path in (
            foreign, wrapper, Path(policy_pin['path']), *tools.iterdir())}
        config = dict(schema='shift-dp-statistics-controller-v1', directory=str(science),
            tools=str(tools), lcg_wrapper=str(wrapper), handoff_operations=str(template/'handoffs'),
            dependencies=dependencies, supplement_admission=policy_pin, simultaneous_waves=2,
            detector_workers_per_wave=100, worklist=str(foreign),
            conditions=dict(path=str(foreign), sha256=statistics.sha(foreign)),
            reviewed_gen_handoffs=[], retired_aggregate_attempts=[], minimum_free_bytes=90000000000,
            previous_statistics='/preserved/previous', prior_inventories=['/preserved/inventory'])
        self.pin(template/'config.json', config)
        (template/'controller.sub').write_text('\n'.join([
            '# Code style: AI-code. AI-generated synthetic controller.', 'universe = vanilla',
            'executable = /usr/bin/python3', 'arguments = '+str(tools/'run_dark_photon_statistics.py')+
                ' --config '+str(template/'config.json'), 'initialdir = '+str(template),
            'should_transfer_files = NO', '+MaxRuntime = 604800', '+SendCredential = True',
            'on_exit_hold = (ExitBySignal == True) || (ExitCode != 0)', 'periodic_release = False',
            '+ShiftDPStatisticsTask = "'+str(template)+'"', 'queue', '']))
        freeze = self.pin(template/'freeze.json', dict(files={
            str(path):statistics.sha(path) for path in template.rglob('*') if path.is_file()}))
        peers = [self.peer(root, name) for name in ('full16', 'full17')]
        candidate = copy.deepcopy(policy)
        candidate['campaigns'].extend(peers)
        new_policy = self.pin(root/'new_policy.json', candidate)
        return template, freeze['sha256'], new_policy['path'], config, policy, peers

    def test_frozen_rollover_keeps_science_and_imports_the_actual_cloned_tools(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            template, freeze, policy, previous, _, peers = self.fixture(root)
            state = Path(previous['directory'])/'state.json'
            state_sha = statistics.sha(state)
            original_sha = statistics.sha(template/'config.json')
            output = root/'replacement'
            receipt = replacement.prepare(template, freeze, policy, output)
            current = statistics.configuration(output/'config.json')
            allowed = {'tools', 'lcg_wrapper', 'handoff_operations', 'supplement_admission', 'dependencies'}
            self.assertEqual({k:v for k,v in current.items() if k not in allowed},
                             {k:v for k,v in previous.items() if k not in allowed})
            foreign = str(root/'foreign_authority.json')
            self.assertEqual(current['dependencies'][foreign], previous['dependencies'][foreign])
            self.assertEqual(statistics.sha(state), state_sha)
            self.assertEqual(statistics.sha(template/'config.json'), original_sha)
            self.assertEqual((Path(previous['directory'])/'controller.lock').read_text(), 'original lock')
            self.assertEqual((output/'tools/retained.py').read_bytes(), (template/'tools/retained.py').read_bytes())
            self.assertEqual(receipt['added_meson_peers'], [peer['name'] for peer in peers])
            self.assertEqual(receipt['configuration_validation']['directory'], previous['directory'])
            self.assertIn('initialdir = '+str(output), (output/'controller.sub').read_text())
            self.assertIn('"'+str(output)+'"', (output/'controller.sub').read_text())
            self.assertFalse((output/'monitor').exists())
            self.assertFalse((output/'submission.json').exists())
            (output/'tools/retained.py').write_text('# Changed helper.\n')
            with self.assertRaisesRegex(ValueError, 'dependency changed'):
                statistics.configuration(output/'config.json')

    def test_new_policy_cannot_reduce_or_rewrite_any_existing_admission_gate(self):
        for change in ('remove_peer', 'rewrite_peer', 'floor', 'cohort', 'metadata', 'new_option',
                       'duplicate_name', 'duplicate_directory', 'nonmeson', 'wrong_directory', 'unbound'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                _, _, path, _, previous, _ = self.fixture(root)
                candidate = statistics.read(path)
                if change == 'remove_peer':
                    candidate['campaigns'].pop(0)
                elif change == 'rewrite_peer':
                    candidate['campaigns'][0]['terminal_completion'] = {}
                elif change == 'floor':
                    candidate['minimum_free_bytes'] = 1
                elif change == 'cohort':
                    candidate['required_cohorts'] = []
                elif change == 'metadata':
                    candidate['required_sample_metadata'] = {}
                elif change == 'new_option':
                    candidate['bypass'] = True
                elif change == 'duplicate_name':
                    candidate['campaigns'][1]['name'] = 'main'
                elif change == 'duplicate_directory':
                    candidate['campaigns'][1]['directory'] = candidate['campaigns'][0]['directory']
                elif change == 'nonmeson':
                    candidate['campaigns'][1]['kind'] = 'statistics'
                elif change == 'wrong_directory':
                    candidate['campaigns'][1]['directory'] = '/unrelated/science'
                else:
                    candidate['campaigns'][1]['worklist']['sha256'] = 'b'*64
                with self.assertRaises(ValueError):
                    replacement.validate_policy(previous, candidate)

    def test_template_foreign_dependencies_and_output_are_protected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            template, freeze, policy, _, _, _ = self.fixture(root)
            with self.assertRaisesRegex(ValueError, 'freeze changed'):
                replacement.prepare(template, 'a'*64, policy, root/'bad_freeze')
            (root/'foreign_authority.json').write_text('{}')
            with self.assertRaisesRegex(ValueError, 'dependency changed'):
                replacement.prepare(template, freeze, policy, root/'changed_foreign')
            output = root/'existing'
            output.mkdir()
            with self.assertRaisesRegex(ValueError, 'already exists'):
                replacement.prepare(template, freeze, policy, output)

    def test_maintained_validation_cannot_hide_a_broken_frozen_import(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            template, _, policy, config, _, _ = self.fixture(root)
            source = template/'tools/shift_condor_native.py'
            source.write_text('import missing_frozen_helper\n'+source.read_text())
            config['dependencies'][str(source)] = statistics.sha(source)
            self.pin(template/'config.json', config)
            freeze = self.pin(template/'freeze.json', dict(files={
                str(path):statistics.sha(path) for path in template.rglob('*')
                if path.is_file() and path.name != 'freeze.json'}))
            with self.assertRaises(subprocess.CalledProcessError):
                replacement.prepare(template, freeze['sha256'], policy, root/'broken_import')


if __name__ == '__main__':
    unittest.main()
