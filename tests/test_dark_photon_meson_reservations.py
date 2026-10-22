# Code style: AI-code. AI-generated append-only storage registration tests.
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import prepare_dark_photon_meson_controller as replacement
import run_dark_photon_meson_production as production


class MesonReservationRegistration(unittest.TestCase):
    def pin(self, path, value):
        path.write_text(json.dumps(value))
        return dict(path=str(path), sha256=production.durable.sha(path))

    def peer(self, root, name):
        directory = root/name
        directory.mkdir()
        model = directory/'model.json'
        model.write_text('{"channels": ["eta->gamma_Aprime"]}')
        config = self.pin(directory/'config.json', dict(
            schema='shift-dp-meson-production-v1', directory=str(directory),
            production_mechanism='meson-radiative', model_card=str(model),
            batches=[dict(batch='canary'), dict(batch='bulk')]))
        forecast = self.pin(directory/'forecast.json', dict(complete=True,
            worklist_sha256=config['sha256'], model_card_sha256=production.durable.sha(model),
            per_batch_forecast_bytes=dict(canary=100, bulk=200), total_meson_forecast_bytes=300))
        return dict(name=name, peer_configuration=config, forecast=forecast)

    def template(self, root, old_peer):
        directory = root/'science'
        directory.mkdir()
        (directory/'controller.lock').write_text('untouched lock')
        original = dict(directory=str(directory), tools=str(directory/'tools'), batches=[],
            simultaneous_sources=2, simultaneous_detector_points=2,
            minimum_eos_free_bytes=90000000000,
            adaptive_terminal_completion=dict(path='/preserved/authority', sha256='a'*64),
            additional_reserved_meson_forecasts=[old_peer])
        (directory/'config.json').write_text(json.dumps(original))
        (directory/'seal.json').write_text('{}')
        template = root/'template'
        (template/'tools').mkdir(parents=True)
        (template/'tools/retained.py').write_text('# Synthetic frozen helper.\n')
        (template/'config.json').write_text(json.dumps(dict(original, physics_tools=original['tools'])))
        (template/'lcg_controller.sh').write_text('#!/bin/bash\nexec '+str(template)+'/tools/controller.py\n')
        descriptor = '\n'.join(['initialdir = '+str(template), '+SendCredential = True',
            '+MaxRuntime = 604800', 'on_exit_hold = (ExitCode != 0)',
            'periodic_release = False', 'arguments = OPERATIONS_SEAL_DIGEST'])
        (template/'controller.sub.in').write_text(descriptor)
        files = {str(path):production.durable.sha(path) for path in (
            template/'config.json', template/'lcg_controller.sh',
            template/'controller.sub.in', template/'tools/retained.py')}
        seal = self.pin(template/'seal.json', dict(files=files))
        (template/'controller.sub').write_text(descriptor.replace('OPERATIONS_SEAL_DIGEST', seal['sha256']))
        return original, directory, template, seal['sha256']

    def test_registration_protects_the_complete_positive_peer_allocation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            peer = self.peer(root, 'new_peer')
            old = dict(directory='/existing/science', additional_reserved_meson_forecasts=[])
            current = dict(old, additional_reserved_meson_forecasts=[peer])
            self.assertEqual(production.validate_meson_reservation_additions(old, current), [peer])
            self.assertEqual(production.additional_reservations(current)[0], 300)
            # No peer heartbeat or partial completion releases any new claim.
            Path(peer['peer_configuration']['path']).with_name('state.json').write_text(
                json.dumps(dict(complete=True, batches=dict(canary=dict(published=True)))))
            self.assertEqual(production.additional_reservations(current)[0], 300)

    def test_registration_rejects_duplicate_changed_or_unbound_peers(self):
        changes = ('remove', 'change_old', 'duplicate_name', 'duplicate_directory', 'worklist',
                   'missing_batch', 'zero_amount', 'negative_batch', 'model', 'self', 'completion')
        for change in changes:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                old_peer, new_peer = self.peer(root, 'old_peer'), self.peer(root, 'new_peer')
                before = dict(directory='/existing/science', additional_reserved_meson_forecasts=[old_peer])
                current = copy.deepcopy(dict(before, additional_reserved_meson_forecasts=[old_peer, new_peer]))
                added = current['additional_reserved_meson_forecasts'][1]
                if change == 'remove':
                    current['additional_reserved_meson_forecasts'].pop(0)
                elif change == 'change_old':
                    current['additional_reserved_meson_forecasts'][0]['name'] = 'changed'
                elif change == 'duplicate_name':
                    added['name'] = old_peer['name']
                elif change == 'duplicate_directory':
                    added.update(copy.deepcopy(old_peer), name='another_name')
                elif change in ('worklist', 'missing_batch', 'zero_amount', 'negative_batch'):
                    path = Path(added['forecast']['path'])
                    forecast = production.read(path)
                    if change == 'worklist':
                        forecast['worklist_sha256'] = 'f'*64
                    elif change == 'missing_batch':
                        del forecast['per_batch_forecast_bytes']['canary']
                    elif change == 'zero_amount':
                        forecast['total_meson_forecast_bytes'] = 0
                    else:
                        forecast['per_batch_forecast_bytes'] = dict(canary=-100, bulk=400)
                    added['forecast'] = self.pin(path, forecast)
                elif change == 'model':
                    Path(new_peer['peer_configuration']['path']).with_name('model.json').write_text('{}')
                elif change == 'self':
                    current['directory'] = str(Path(new_peer['peer_configuration']['path']).parent)
                else:
                    added['completion_authority'] = dict(path='/invented/terminal', sha256='c'*64)
                with self.assertRaises(ValueError):
                    production.validate_meson_reservation_additions(before, current)

    def test_cross_kind_peer_names_and_runtime_pin_changes_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            peer = self.peer(root, 'peer')
            current = dict(additional_reserved_worklists=[dict(name='peer')],
                           additional_reserved_meson_forecasts=[peer])
            with self.assertRaisesRegex(ValueError, 'unique'):
                production.validate_meson_reservation_additions({}, current)
            Path(peer['peer_configuration']['path']).write_text('{}')
            with self.assertRaisesRegex(ValueError, 'authority changed'):
                production.additional_reservations(dict(additional_reserved_meson_forecasts=[peer]))

    def test_two_rollovers_preserve_original_and_previously_added_claims(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            peers = [self.peer(root, name) for name in ('old_peer', 'full16', 'full17')]
            original, directory, template, template_seal = self.template(root, peers[0])
            original_sha = production.durable.sha(directory/'config.json')
            original_seal = production.durable.sha(directory/'seal.json')
            for index, peer in enumerate(peers[1:], 1):
                output = root/('replacement'+str(index))
                arguments = [(peer['name'], peer['peer_configuration']['path'], peer['forecast']['path'])]
                with patch.object(production, 'verify', return_value=original):
                    receipt = replacement.prepare(directory, original_seal, template, template_seal,
                        output, sources=2, detector_points=2, additional_meson_forecasts=arguments)
                accepted = production.operational_configuration(original, output/'config.json', receipt['seal_sha256'])
                self.assertEqual(accepted['additional_reserved_meson_forecasts'], peers[:index+1])
                self.assertEqual(accepted['tools'], original['tools'])
                self.assertEqual(accepted['adaptive_terminal_completion'], original['adaptive_terminal_completion'])
                self.assertEqual(accepted['minimum_eos_free_bytes'], 90000000000)
                preparer = output/'tools/prepare_dark_photon_production.py'
                self.assertEqual(production.durable.sha(preparer), production.durable.sha(
                    Path(replacement.__file__).with_name(preparer.name)))
                template, template_seal = output, receipt['seal_sha256']
            self.assertEqual(production.durable.sha(directory/'config.json'), original_sha)
            self.assertEqual((directory/'controller.lock').read_text(), 'untouched lock')
            # A sealed later candidate cannot drop a claim added by an earlier
            # operational rollover, even if it retains all original claims.
            accepted['additional_reserved_meson_forecasts'].pop(1)
            malicious = root/'dropped_claim'
            malicious.mkdir()
            config_pin = self.pin(malicious/'config.json', accepted)
            seal_pin = self.pin(malicious/'seal.json', dict(files={config_pin['path']:config_pin['sha256']}))
            with self.assertRaisesRegex(ValueError, 'removed or changed'):
                production.operational_configuration(original, malicious/'config.json', seal_pin['sha256'])

    def test_first_operational_rollover_uses_the_original_science_template(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            peer = self.peer(root, 'peer')
            original, directory, _, _ = self.template(root, peer)
            tools = directory/'tools'
            tools.mkdir()
            (tools/'retained.py').write_text('# Original frozen science helper.\n')
            seal = self.pin(directory/'seal.json', dict(files={
                str(path):production.durable.sha(path) for path in (directory/'config.json', tools/'retained.py')}))
            (directory/'controller.sub').write_text('\n'.join([
                'executable = '+str(directory/'lcg_controller.sh'),
                'initialdir = '+str(directory), 'arguments = '+seal['sha256'],
                '+SendCredential = True', '+MaxRuntime = 604800',
                'on_exit_hold = (ExitCode != 0)', 'periodic_release = False', '']))
            output = root/'first_operations'
            with patch.object(production, 'verify', return_value=original):
                receipt = replacement.prepare(directory, seal['sha256'], directory,
                    seal['sha256'], output, sources=2, detector_points=2)
            current = production.operational_configuration(original, output/'config.json', receipt['seal_sha256'])
            self.assertEqual(current['directory'], str(directory))
            self.assertEqual(current['physics_tools'], original['tools'])
            wrapper = (output/'lcg_controller.sh').read_text()
            self.assertIn('--directory '+str(directory), wrapper)
            self.assertIn('--controller-config '+str(output/'config.json'), wrapper)
            self.assertIn('--controller-seal "$1"', wrapper)
            self.assertIn('arguments = '+receipt['seal_sha256'], (output/'controller.sub').read_text())
            self.assertEqual((directory/'controller.lock').read_text(), 'untouched lock')

    def test_handoff_uses_its_sealed_operational_preparer_and_preserves_physics_tools(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            batch = dict(batch='canary',point='m0p3_prompt')
            directory = root/'batches/canary'
            directory.mkdir(parents=True)
            budget = root/'budget.json'
            budget.write_text('{}')
            (directory/'rates.json').write_text('{"complete": true}')
            original_tools = str(root/'original_science_tools')
            config = dict(directory=str(root),tools=original_tools,physics_tools=original_tools,
                storage_budget_template=str(budget),minimum_eos_free_bytes=90000000000,
                qualified_template='/pinned/template',prior_inventories=[],
                failure_base='/pinned/failures',publication_canary='/pinned/canary')
            storage = directory/'storage_budget.json'
            storage.write_text(json.dumps(dict(events=22,detector_chunks=2,points=1,
                external_reservations=[],fixed_reservation_bytes=90000000000,
                gen_group_file_allowance=4000,safety_free_floor_files=5000)))
            storage_hash = production.durable.sha(storage)
            def prepare(argv, log, **options):
                self.assertEqual(argv[1],Path(production.__file__).with_name('prepare_dark_photon_production.py'))
                self.assertNotEqual(argv[1].parent,Path(original_tools))
                (directory/'production').mkdir()
            receipt = dict(cluster=123,task=str(directory/'production'))
            with patch.object(production,'adopt',return_value=dict(events=22,detector_chunks=2)), \
                 patch.object(production.durable,'run_logged',side_effect=prepare) as prepare_call, \
                 patch.object(production.durable,'submit_dag',return_value=receipt):
                self.assertEqual(production.handoff(config,batch),receipt)
            prepare_call.assert_called_once()
            self.assertEqual(config['tools'],original_tools)
            self.assertEqual(config['physics_tools'],original_tools)
            self.assertEqual(production.durable.sha(storage),storage_hash)

    def test_interrupted_handoff_rejects_a_changed_existing_storage_budget(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root/'batches/canary'
            directory.mkdir(parents=True)
            template = root/'budget.json'
            template.write_text('{}')
            storage = directory/'storage_budget.json'
            storage.write_text('{"events": 999}')
            original_hash = production.durable.sha(storage)
            config = dict(directory=str(root),storage_budget_template=str(template),
                          minimum_eos_free_bytes=90000000000)
            with patch.object(production,'adopt',return_value=dict(events=22,detector_chunks=2)), \
                 patch.object(production.durable,'run_logged') as prepare:
                with self.assertRaisesRegex(ValueError,'storage budget changed'):
                    production.handoff(config,dict(batch='canary',point='m0p3_prompt'))
            prepare.assert_not_called()
            self.assertEqual(production.durable.sha(storage),original_hash)


if __name__ == '__main__':
    unittest.main()
