# Code style: AI-code. AI-generated exact saved-GEN recovery regression tests.
import copy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile


SOURCE = Path(__file__).resolve().parents[1]/'scripts/recover_mu_enriched_qcd_publication.py'
SPEC = importlib.util.spec_from_file_location('recover_saved_GEN', SOURCE)
RECOVERY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RECOVERY)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.previous = Path.cwd()
        os.chdir(self.directory.name)
        Path('helper.py').write_text('# synthetic pinned input\n')
        request = dict(id='saved_source', stage='production', trials=2)
        manifest = dict(generator_profile=RECOVERY.PROFILE, jobs=[request],
                        inputs={'helper.py':RECOVERY.sha('helper.py')})
        RECOVERY.write_json('manifest.json', manifest)
        audit = dict(complete=True, healthy=True, generator_profile=RECOVERY.PROFILE,
            events=1, source_accepted=2, full_source_trial_denominator=1000,
            sigma_nd_mb=65., sigma_mb=.065, event_ids=[[1, 1, 2]], weights=[1.],
            no_second_filter_efficiency=True)
        payloads = {'gen.root':b'synthetic saved GEN bytes',
                    'resolved_gen_cfg.py':b'# synthetic config\n',
                    'cmsRun.log':b'synthetic full ND trial log\n',
                    'semantic_audit.json':(json.dumps(audit, indent=2)+'\n').encode()}
        for name, data in payloads.items():
            Path(name).write_bytes(data)
        artifacts = {name:dict(bytes=len(data), sha256=RECOVERY.sha(name)) for name,data in payloads.items()}
        self.failed = dict(schema='shift-mu-enriched-ordinary-gen-receipt-v1',
            complete=False, publication_complete=False, exit_status=1, generated_edm=True,
            generator_profile=RECOVERY.PROFILE, manifest_sha256=RECOVERY.sha('manifest.json'),
            request=request, source_inputs=manifest['inputs'], semantic_audit=audit,
            event_count=1, artifacts=artifacts, error='original publication failure')
        RECOVERY.write_json('source.failed.json', self.failed)
        with zipfile.ZipFile('source.zip', 'w') as archive:
            for name in payloads:
                archive.write(name)
            archive.write('source.failed.json', 'result.json')
            archive.write('manifest.json')
            archive.writestr('request.json', json.dumps(request))
        self.row = dict(node_id='saved_source', original_failed_receipt=str(Path('source.failed.json').resolve()),
            original_failed_receipt_sha256=RECOVERY.sha('source.failed.json'),
            frozen_manifest=str(Path('manifest.json').resolve()), frozen_manifest_sha256=RECOVERY.sha('manifest.json'),
            request=request, saved_artifacts=artifacts, saved_events=1, framework_source_calls=2,
            full_ND_trial_denominator=1000, sigma_nd_mb=65., selected_sigma_mb=.065,
            event_identity_sha256=RECOVERY.array_sha(audit['event_ids']),
            native_weights_sha256=RECOVERY.array_sha(audit['weights']),
            saved_archive=str(Path('source.zip').resolve()), saved_archive_sha256=RECOVERY.sha('source.zip'),
            canonical_EOS_directory='/eos/synthetic/saved_source', job_id='123.0')
        self.plan = dict(schema='shift-exact-saved-GEN-publication-recovery-plan-v1',
            source_model_or_sampling_changes=False, no_new_GEN_calls=True,
            worker_sha256=RECOVERY.sha(SOURCE), jobs=[self.row])

    def tearDown(self):
        os.chdir(self.previous)
        self.directory.cleanup()

    def test_exact_source_preserves_ids_weights_and_full_denominator(self):
        _, record, _ = RECOVERY.unpack_saved_source(self.plan, 'saved_source')
        self.assertEqual(record, self.failed)
        self.assertEqual(record['semantic_audit']['full_source_trial_denominator'], 1000)
        self.assertEqual(RECOVERY.sha('gen.root'), self.row['saved_artifacts']['gen.root']['sha256'])

    def change_failed(self, field, value):
        self.failed['semantic_audit'][field] = value
        RECOVERY.write_json('source.failed.json', self.failed)
        self.row['original_failed_receipt_sha256'] = RECOVERY.sha('source.failed.json')

    def test_changed_native_weight_is_rejected(self):
        self.change_failed('weights', [.5])
        with self.assertRaisesRegex(ValueError, 'identity, exposure'):
            RECOVERY.unpack_saved_source(self.plan, 'saved_source')

    def test_framework_calls_cannot_replace_full_trial_denominator(self):
        self.change_failed('full_source_trial_denominator', 2)
        with self.assertRaisesRegex(ValueError, 'identity, exposure'):
            RECOVERY.unpack_saved_source(self.plan, 'saved_source')

    def test_changed_saved_root_is_rejected(self):
        with zipfile.ZipFile('source.zip') as source:
            members = {name:source.read(name) for name in source.namelist()}
        members['gen.root'] = b'a different collision'
        with zipfile.ZipFile('source.zip', 'w') as target:
            for name,data in members.items():
                target.writestr(name, data)
        self.row['saved_archive_sha256'] = RECOVERY.sha('source.zip')
        with self.assertRaisesRegex(ValueError, 'artifact size|artifact checksum'):
            RECOVERY.unpack_saved_source(self.plan, 'saved_source')

    def test_changed_frozen_helper_is_rejected(self):
        Path('helper.py').write_text('# changed code\n')
        with self.assertRaisesRegex(ValueError, 'Pinned recovery input changed'):
            RECOVERY.unpack_saved_source(self.plan, 'saved_source')

    def test_native_reread_must_match_entire_original_audit(self):
        fresh = copy.deepcopy(self.failed['semantic_audit'])
        fresh['full_source_trial_denominator'] = 2
        def fake_auditor(*args, **kwargs):
            RECOVERY.write_json('recovery_semantic_audit.json', fresh)
        with patch.object(RECOVERY.subprocess, 'run', side_effect=fake_auditor):
            with self.assertRaisesRegex(ValueError, 'Native reread differs'):
                RECOVERY.native_audit(self.failed, {'audit_mu_enriched_qcd_gen.py':'auditor.py'}, {})

    def test_conflicting_existing_eos_root_is_not_overwritten(self):
        def fake_command(arguments, **kwargs):
            if arguments[0] == 'xrdcp':
                Path(arguments[-1]).write_bytes(b'conflicting EOS bytes')
            return SimpleNamespace(returncode=0, stdout='', stderr='')
        helper = SimpleNamespace(publish=unittest.mock.Mock())
        with patch.object(RECOVERY.subprocess, 'run', side_effect=fake_command):
            with self.assertRaisesRegex(ValueError, 'Conflicting existing EOS artifact'):
                RECOVERY.finish_publication(self.row, self.failed, helper)
        helper.publish.assert_not_called()

    def test_unknown_eos_state_is_not_treated_as_absent(self):
        with patch.object(RECOVERY.subprocess, 'run', return_value=SimpleNamespace(
                returncode=1, stdout='', stderr='permission denied')):
            with self.assertRaisesRegex(ValueError, 'Unknown completion-receipt state'):
                RECOVERY.existing_completion_receipt(self.row, self.failed)

    def test_existing_receipt_must_bind_the_exact_root_metadata(self):
        existing = copy.deepcopy(self.failed)
        existing.pop('error')
        existing.update(complete=True, publication_complete=True, exit_status=0)
        existing['publication'] = [dict(name=name, path=self.row['canonical_EOS_directory']+'/'+name,
            **expected, independent_sha256_readback=True) for name,expected in self.row['saved_artifacts'].items()]
        existing['publication'][0]['sha256'] = 'wrong ROOT hash'
        def fake_command(arguments, **kwargs):
            if arguments[0] == 'xrdcp':
                RECOVERY.write_json(arguments[-1], existing)
            return SimpleNamespace(returncode=0, stdout='', stderr='')
        with patch.object(RECOVERY.subprocess, 'run', side_effect=fake_command):
            with self.assertRaisesRegex(ValueError, 'bind every original artifact'):
                RECOVERY.existing_completion_receipt(self.row, self.failed)

    def test_validation_mode_cannot_mark_source_complete_or_write_eos(self):
        RECOVERY.write_json('plan.json', self.plan)
        arguments = ['recover', '--recovery-manifest', 'plan.json', '--expected-sha256',
                     RECOVERY.sha('plan.json'), '--node', 'saved_source', '--runtime-env', 'unused.json',
                     '--validate-only']
        def fake_audit(*args):
            RECOVERY.write_json('recovery_semantic_audit.json', self.failed['semantic_audit'])
        with patch('sys.argv', arguments), patch.object(RECOVERY, 'runtime_environment', return_value={}), \
                patch.object(RECOVERY, 'native_audit', side_effect=fake_audit), \
                patch.object(RECOVERY.subprocess, 'run') as command:
            self.assertEqual(RECOVERY.main(), 0)
        result = json.loads(Path('result.json').read_text())
        self.assertFalse(result['complete'])
        self.assertFalse(result['publication_complete'])
        self.assertTrue(result['recovery_validation']['complete'])
        self.assertEqual(result['exit_status'], 1)
        command.assert_not_called()

    def test_publication_returns_success_only_after_completion_receipt(self):
        RECOVERY.write_json('plan.json', self.plan)
        arguments = ['recover', '--recovery-manifest', 'plan.json', '--expected-sha256',
                     RECOVERY.sha('plan.json'), '--node', 'saved_source', '--publish']
        published = []
        def publish(files, destination):
            rows = []
            for local, name in files:
                published.append(name)
                rows.append(dict(name=name, path=destination+'/'+name, sha256=RECOVERY.sha(local),
                                 bytes=Path(local).stat().st_size, independent_sha256_readback=True))
            return rows
        helper = SimpleNamespace(publish=publish)
        spec = SimpleNamespace(loader=SimpleNamespace(exec_module=lambda module:None))
        unpack = RECOVERY.unpack_saved_source
        def unpack_with_publisher(*args, **kwargs):
            row, failed, inputs = unpack(*args, **kwargs)
            inputs['run_shift_weighted_gen.py'] = 'unused'
            return row, failed, inputs
        def fake_audit(*args):
            RECOVERY.write_json('recovery_semantic_audit.json', self.failed['semantic_audit'])
        with patch('sys.argv', arguments), patch.object(RECOVERY, 'unpack_saved_source', side_effect=unpack_with_publisher), \
                patch.object(RECOVERY, 'runtime_environment', return_value={}), \
                patch.object(RECOVERY, 'native_audit', side_effect=fake_audit), \
                patch.object(RECOVERY.importlib.util, 'spec_from_file_location', return_value=spec), \
                patch.object(RECOVERY.importlib.util, 'module_from_spec', return_value=helper), \
                patch.object(RECOVERY.subprocess, 'run', return_value=SimpleNamespace(
                    returncode=1, stdout='', stderr='[3011] No such file')):
            self.assertEqual(RECOVERY.main(), 0)
        result = json.loads(Path('result.json').read_text())
        self.assertTrue(result['complete'])
        self.assertTrue(result['publication_complete'])
        self.assertEqual(result['exit_status'], 0)
        self.assertEqual(result['semantic_audit'], self.failed['semantic_audit'])
        self.assertEqual(published[-1], 'publication_receipt.json')
        self.assertEqual(len(published), 5)
        self.assertEqual(result['recovery']['original_exit_status'], 1)
        self.assertEqual(json.loads(Path('source.failed.json').read_text()), self.failed)
        self.assertNotIn('error', result)

    def test_missing_explicit_mode_cannot_return_success(self):
        arguments = ['recover', '--recovery-manifest', 'plan.json', '--expected-sha256',
                     'unused', '--node', 'saved_source']
        with patch('sys.argv', arguments), patch('sys.stderr'), self.assertRaises(SystemExit) as error:
            RECOVERY.main()
        self.assertEqual(error.exception.code, 2)


if __name__ == '__main__':
    unittest.main()
