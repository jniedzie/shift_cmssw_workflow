import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys
import os
import io
import subprocess
from contextlib import redirect_stdout

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import run_shift_gen_to_nano as worker

spec = importlib.util.spec_from_file_location('handoff', Path(__file__).resolve().parents[1] / 'scripts/prepare_shift_gen_to_nano.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class HandoffTests(unittest.TestCase):
    def test_local_pilot_input_requires_exact_frozen_checksum(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'source.root';target=root/'gen.root'
            source.write_bytes(b'validated frozen GEN fixture')
            descriptor=dict(gen_transport='local',gen=str(source),gen_sha256=worker.sha(source))
            worker.copy_gen_input(descriptor,target)
            self.assertEqual(target.read_bytes(),source.read_bytes())
            source.write_bytes(b'changed input')
            with self.assertRaisesRegex(ValueError,'Frozen GEN payload changed'):
                worker.copy_gen_input(descriptor,target)

    def inventory_fixture(self, directory, identities):
        root = Path(directory)
        ordinary = root / 'ordinary.json'
        weighted = root / 'weighted.json'
        ordinary.write_text(json.dumps(dict(receipt_base=str(root),
            plan=dict(strata=[dict(id='qcd_0to1', jobs=len(identities))]))))
        weighted.write_text(json.dumps(dict(eos_base=str(root), jobs=[])))
        (root / 'production_complete.json').write_text(json.dumps(dict(
            complete=True, manifest_sha256=module.digest(weighted), strata={})))
        receipts = root / 'receipts' / 'qcd_0to1'
        receipts.mkdir(parents=True)
        for chunk, identity in enumerate(identities):
            gen = root / f'gen{chunk}.root'
            gen.write_bytes(b'synthetic GEN')
            (receipts / f'part{chunk:05d}.json').write_text(json.dumps(dict(
                schema='shift-dag-receipt-v1', complete=True,
                manifest_sha256=module.digest(ordinary), event_ids=[identity],
                events=1, stratum='qcd_0to1',
                artifacts=[dict(path=str(gen), bytes=gen.stat().st_size,
                                sha256=module.digest(gen))])))
        templates = root / 'archived-configs'
        for stage in range(1, 5):
            stage_directory = templates / f'step{stage}'
            stage_directory.mkdir(parents=True)
            (stage_directory / 'synthetic_part0000_cfg.py').write_text(f'# synthetic stage {stage}\n')
        output = root / 'inventory'
        arguments = ['prepare', '--ordinary', str(ordinary), '--weighted', str(weighted),
                     '--output', str(output), '--eos-output', '/eos/synthetic/inventory',
                     '--template-directory', str(templates), '--events-per-job', '1']
        return output, templates, arguments

    def test_explicit_archived_templates_and_neutral_preparation_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            output, templates, arguments = self.inventory_fixture(directory, [[1, 1, 1], [1, 1, 2]])
            with patch.object(sys, 'argv', arguments), redirect_stdout(io.StringIO()):
                module.main()
            manifest = json.loads((output / 'manifest.json').read_text())
            self.assertEqual(manifest['events'], 2)
            self.assertEqual(manifest['jobs'], 2)
            self.assertNotIn('approval_basis', manifest)
            self.assertIn('preparation_basis', manifest)
            self.assertFalse(manifest['physics_valid'])
            self.assertFalse(manifest['normalization_ready'])
            for stage in range(1, 5):
                original = templates / f'step{stage}' / 'synthetic_part0000_cfg.py'
                self.assertEqual(manifest['templates'][str(stage)]['source'], str(original))
                self.assertEqual((output / 'templates' / f'step{stage}.py').read_bytes(), original.read_bytes())

    def test_migrated_gen_receipts_and_archived_templates_keep_logical_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output, templates, arguments = self.inventory_fixture(directory, [[1, 1, 1], [1, 1, 2]])
            old_receipts = root / 'receipts'
            moved_receipts = root / 'support/receipts'
            moved_receipts.parent.mkdir()
            old_receipts.rename(moved_receipts)
            old_templates = templates
            moved_templates = root / 'moved-configs'
            templates.rename(moved_templates)
            paths = {}
            for index in range(2):
                old = root / f'gen{index}.root'
                new = root / f'moved-gen{index}.root'
                old.rename(new)
                paths[str(old)] = str(new)
            migration = root / 'migration.json'
            migration.write_text(json.dumps(dict(schema='shift-storage-path-map-v1', complete=True,
                paths=paths,
                directories={str(old_templates / f'step{i}'): str(moved_templates / f'step{i}') for i in range(1, 5)},
                support_prefixes={str(old_receipts): str(moved_receipts)})))
            frozen = {str(path): path.read_bytes() for path in moved_receipts.rglob('*.json')}
            with patch.dict(os.environ, {'SHIFT_STORAGE_MIGRATION_MANIFEST': str(migration)}), \
                    patch.object(sys, 'argv', arguments), redirect_stdout(io.StringIO()):
                module.main()
                source = json.loads((output / 'sources/source00000.json').read_text())
                self.assertEqual(source['gen'], str(root / 'gen0.root'))
                self.assertEqual(source['receipt'], str(old_receipts / 'qcd_0to1/part00000.json'))
                local_descriptor = dict(source, gen_transport='local')
                worker.copy_gen_input(local_descriptor, root / 'readback.root')
                self.assertEqual(worker.sha(root / 'readback.root'), source['gen_sha256'])
            manifest = json.loads((output / 'manifest.json').read_text())
            self.assertEqual(manifest['templates']['1']['source'], str(old_templates / 'step1/synthetic_part0000_cfg.py'))
            self.assertEqual({str(path): path.read_bytes() for path in moved_receipts.rglob('*.json')}, frozen)

    def test_reject_duplicate_identities_across_distinct_gen_files(self):
        with tempfile.TemporaryDirectory() as directory:
            output, _, arguments = self.inventory_fixture(directory, [[1, 1, 1], [1, 1, 1]])
            with patch.object(sys, 'argv', arguments), redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(ValueError, 'Duplicate event identity across source GEN files'):
                    module.main()
            self.assertFalse((output / 'manifest.json').exists())

    def test_published_receipt_and_evidence_remain_byte_identical_at_exit(self):
        """Finalization must not invalidate already published provenance."""
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'source.json'
            payload=b'frozen GEN';gen=root/'gen.root';gen.write_bytes(payload)
            source.write_text(json.dumps(dict(gen='/eos/gen.root',gen_sha256=worker.sha(gen),
                receipt='/eos/parent.json',receipt_sha256='parent',stratum='dy_2to5',
                event_ids=[[1,1,1]],output_base='/eos/production')))
            row=dict(id=[1,1,1],weight=1.0,hepmc_sha256='unchanged',timing_persisted_in_input=True)
            nano=dict(events=1,event_ids=[[1,1,1]],sumw=1.0,shift_muons=0,branches=2,compressed_event_bytes=10)
            remote={}
            def run(args,**kwargs):
                if args[0]=='xrdcp':
                    origin,target=args[-2:]
                    if origin.startswith('root://'):
                        Path(target).write_bytes(remote.get(origin,payload))
                    else:
                        remote[target]=Path(origin).read_bytes()
                elif len(args)>2 and args[2]=='make-config':
                    Path(args[-1]).write_text('resolved configuration\n')
                elif args[0]=='cmsRun':
                    stage=int(Path(args[1]).name[4]);target=root/(f'step{stage}.root' if stage<4 else 'nano.root')
                    target.write_bytes(b'audited ROOT output')
            previous=Path.cwd()
            try:
                os.chdir(root)
                with patch.object(sys,'argv',['worker',str(source),'--skip','0','--count','1','--job','7','--publish']), \
                     patch.object(worker,'command',side_effect=run),patch.object(worker,'signatures',return_value=[row]), \
                     patch.object(worker,'audit_edm',return_value={'events':1}),patch.object(worker,'audit_nano',return_value=nano), \
                     redirect_stdout(io.StringIO()):
                    worker.main()
            finally:
                os.chdir(previous)
            receipt=root/'report.json';data=json.loads(receipt.read_text())
            self.assertTrue(data['complete'])
            self.assertEqual(remote['root://eosuser.cern.ch//eos/production/job0000007/complete.json'],receipt.read_bytes())
            archived=remote['root://eosuser.cern.ch//eos/production/job0000007/evidence.tar.gz']
            self.assertEqual(archived,(root/'evidence.tar.gz').read_bytes())
            self.assertEqual(data['evidence_sha256'],worker.sha(root/'evidence.tar.gz'))

    def test_progress_requires_a_new_framework_record(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / 'cmsRun.log'
            log.write_text('Begin processing the 1st record. Run 1, Event 2\n')
            with patch.object(worker.time, 'monotonic', return_value=0), patch.object(worker, 'progress') as update:
                poll = worker.event_progress(log, {}, 'SIM', {}, 1800)
                poll()
                self.assertEqual(update.call_count, 1)
                poll()
                self.assertEqual(update.call_count, 1)
                with patch.object(worker.time, 'monotonic', return_value=1801):
                    with self.assertRaisesRegex(TimeoutError, 'no event progress'):
                        poll()
                with log.open('a') as stream:
                    stream.write('Begin processing the 2nd record. Run 1, Event 3\n')
                with patch.object(worker.time, 'monotonic', return_value=1900):
                    poll()
                self.assertEqual(update.call_count, 2)

    def test_progress_preserves_a_partially_written_record(self):
        with tempfile.TemporaryDirectory() as directory:
            log=Path(directory)/'cmsRun.log';log.write_text('Begin processing the ')
            with patch.object(worker.time,'monotonic',return_value=0),patch.object(worker,'progress') as update:
                poll=worker.event_progress(log,{},'SIM',{},1800)
                poll()
                update.assert_not_called()
                with log.open('a') as stream: stream.write('1st record. Run 1, Event 2\n')
                poll()
                update.assert_called_once()

    def test_completed_records_use_absolute_stage_limit_for_finalisation(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / 'cmsRun.log'
            log.write_text('Begin processing the 1st record. Run 1, Event 2\n')
            with patch.object(worker.time, 'monotonic', side_effect=[0, 0, 1801]), \
                 patch.object(worker, 'progress'):
                poll = worker.event_progress(log, {}, 'NANO', {}, 1800, expected_records=1)
                poll()
                poll()  # closing the requested output is governed by the absolute limit

    def test_final_record_telemetry_keeps_scheduler_and_worker_deadlines_consistent(self):
        import run_shift_ntuple_controller as controller
        with tempfile.TemporaryDirectory() as directory:
            log=Path(directory)/'cmsRun.log'
            log.write_text('Begin processing the 1st record. Run 1, Event 2\n')
            report=dict(stage_started_epoch=1000,stage_deadline_epoch=6400,stage_timeout_seconds=1800)
            with patch.object(worker.time,'monotonic',return_value=0), \
                 patch.object(worker.time,'time',return_value=1001),patch.object(worker,'progress') as update:
                poll=worker.event_progress(log,report,'NANO',{},1800,expected_records=1)
                poll()
            self.assertEqual(report['stage_timeout_seconds'],5400)
            update.assert_called_once()
            ad=dict(JobStatus=2,JobCurrentStartDate=900,ShiftNtupleProgressEpoch=1001,
                    ShiftNtupleStageTimeout=report['stage_timeout_seconds'],ShiftNtupleStageDeadlineEpoch=6400)
            policy=dict(queue_timeout_seconds=7200,worker_timeout_seconds=21600,setup_timeout_seconds=1800)
            self.assertIsNone(controller.problem(ad,3100,policy))
            self.assertIn('stage wall-time',controller.problem(ad,6431,policy))

    def test_telemetry_failure_is_recorded_without_aborting_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            previous = Path.cwd()
            try:
                os.chdir(directory)
                report = {'source_stratum': 'qcd'}
                with patch.dict(worker.os.environ, {'_CONDOR_SCRATCH_DIR': directory}), \
                     patch.object(worker.shutil, 'which', return_value='/usr/bin/condor_chirp'), \
                     patch.object(worker.subprocess, 'run', side_effect=subprocess.TimeoutExpired('chirp', 10)):
                    worker.progress(report, 'SIM', {})
                self.assertIn('telemetry_error', report)
                self.assertTrue(Path('report.json').exists())
            finally:
                os.chdir(previous)

    def test_stage_transition_chirps_progress_before_shorter_limits(self):
        for tier, limit in (('NANO_AUDIT',900), ('RECO_CONFIG',600)):
            with self.subTest(tier=tier), tempfile.TemporaryDirectory() as directory:
                previous = Path.cwd()
                try:
                    os.chdir(directory)
                    report = dict(source_stratum='qcd', progress_epoch=100,
                                  stage_timeout_seconds=limit, stage_deadline_epoch=2900)
                    with patch.dict(worker.os.environ, {'_CONDOR_SCRATCH_DIR':directory}), \
                         patch.object(worker.shutil, 'which', return_value='/usr/bin/condor_chirp'), \
                         patch.object(worker.time, 'time', return_value=2000), \
                         patch.object(worker.subprocess, 'run') as chirp:
                        worker.progress(report, tier, {'NANO':10})
                    updates = [(call.args[0][2], json.loads(call.args[0][3])) for call in chirp.call_args_list]
                    self.assertEqual(updates[0], ('ShiftNtupleProgressEpoch',2000))
                    self.assertEqual(dict(updates)['ShiftNtupleStageTimeout'], limit)
                    self.assertEqual(dict(updates)['ShiftNtupleStageDeadlineEpoch'],2900)
                    self.assertEqual(dict(updates)['ShiftNtupleTier'],tier)
                finally:
                    os.chdir(previous)

    def test_partition_has_no_missing_or_repeated_events(self):
        for events in (1, 20, 51, 1000, 11342):
            pieces = module.slices(events, 50)
            self.assertEqual([i for skip, count in pieces for i in range(skip, skip + count)], list(range(events)))
            self.assertTrue(all(0 < count <= 50 for _, count in pieces))

    def test_reject_duplicate_source_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / 'receipt.json'
            p.write_text(json.dumps({'schema': 'shift-dag-receipt-v1', 'complete': True,
                                    'manifest_sha256': 'frozen', 'event_ids': [[1, 1, 1], [1, 1, 1]],
                                    'events': 2, 'artifacts': [], 'stratum': 'qcd_0to1'}))
            with self.assertRaisesRegex(ValueError, 'duplicate'):
                module.descriptor(p, 'frozen', '/eos/test')

    def test_weighted_parent_and_accepted_weights_survive_handoff(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'gen.root'
            root.write_bytes(b'GEN')
            p = Path(directory) / 'receipt.json'
            ledger = {'name': 'proposal_ledger.json', 'path': '/eos/parent/ledger.json', 'sha256': 'parent'}
            p.write_text(json.dumps({'schema': 'shift-weighted-gen-receipt-v3', 'complete': True,
                'manifest_sha256': 'frozen', 'publication_complete': True,
                'request': {'stage': 'production', 'stratum': 'qcd_20toinf'}, 'event_count': 2,
                'semantic_audit': {'event_ids': [[31, 1, 2], [31, 1, 9]], 'weights': [1e-8, 3e-9]},
                'publication': [{'path': str(root), 'bytes': 3, 'sha256': 'payload'}, ledger]}))
            result = module.descriptor(p, 'frozen', '/eos/output')
            self.assertEqual(result['event_ids'], [[31, 1, 2], [31, 1, 9]])
            self.assertEqual(result['weights'], [1e-8, 3e-9])
            self.assertEqual(result['normalization_record'], ledger)


if __name__ == '__main__':
    unittest.main()
