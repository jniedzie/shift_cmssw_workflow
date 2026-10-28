# Code style: AI-code. AI-generated detector workflow regression tests.
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
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tea_shift_cmssw/utils'))
import run_shift_gen_to_nano as worker
import run_dark_photon_detector as signal_runner

spec = importlib.util.spec_from_file_location('handoff', Path(__file__).resolve().parents[1] / 'scripts/prepare_shift_gen_to_nano.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class HandoffTests(unittest.TestCase):
    def test_required_prepared_sidecar_cannot_be_silently_published_as_identity(self):
        from test_shift_production_selection import native_proposal_fixture
        from shift_production_selection import bind_source
        sidecar = dict(path='/eos/prepared/weight_authority.json', sha256='a'*64,
                       required_for_downstream_weights=True, diagnostic_only=True)
        conditional = dict(prepared_weight_authority=sidecar)
        self.assertIsNone(worker.prepare_decay_annotation(conditional, [0], publish=False))
        with self.assertRaisesRegex(ValueError, 'full-census decay_proposal'):
            worker.prepare_decay_annotation(conditional, [0], publish=True)
        source, ledger = native_proposal_fixture()
        source['prepared_weight_authority'] = sidecar
        bound = bind_source(source, ledger)
        rows, _ = worker.prepare_decay_annotation(bound, [1], publish=True)
        self.assertEqual(rows[0]['proposal_gen_weight'], source['weights'][1]/.4/1.6)

    def test_optional_proposal_is_validated_before_detector_processing(self):
        from test_shift_production_selection import native_proposal_fixture
        from shift_production_selection import bind_source
        source, ledger = native_proposal_fixture()
        bound = bind_source(source, ledger)
        rows, exposures = worker.prepare_decay_annotation(bound, [1])
        self.assertEqual(rows[0]['sampling_probability'], .4)
        self.assertEqual(rows[0]['native_bank']['selected_probability'], .8)
        self.assertEqual(exposures[0]['full_source_trials'], 2000)
        bound['decay_proposal']['events'].pop()
        with self.assertRaisesRegex(ValueError, 'authority changed'):
            worker.prepare_decay_annotation(bound, [1])
        self.assertIsNone(worker.prepare_decay_annotation({}, [0]))

    def test_weight_finalization_orders_annotation_before_final_audit(self):
        from types import SimpleNamespace
        calls = []
        annotator = SimpleNamespace(annotate_proposal_weights=lambda *args, **kwargs:
            calls.append(('proposal', kwargs['expected_nano_sha256'])) or {'exactly_once': True})
        with patch.object(worker, 'add_sampling_fields', side_effect=lambda *args: calls.append(('detector',))), \
                patch.object(worker, 'sha', return_value='f'*64), \
                patch.object(worker, 'audit_nano', side_effect=lambda *args: calls.append(('audit',)) or {'events': 1}), \
                patch.dict(sys.modules, {'shift_event_weights': annotator}):
            audit, annotation = worker.finalize_nano_weights('nano.root', [], [.4], ([], []))
        self.assertEqual(calls, [('detector',), ('proposal', 'f'*64), ('audit',)])
        self.assertEqual(audit, {'events': 1})
        self.assertEqual(annotation, {'exactly_once': True})

    def test_predefined_transport_requires_explicit_physical_point_and_mass_support(self):
        point = dict(schema='shift-predefined-dark-photon-transport-v1', mass_gev=.3,
                     proper_ctau_mm=58.55432679904731, generated_mass_support_gev=[.2999997, .3000003])
        self.assertIsNone(worker.signal_transport_contract(None))
        self.assertEqual(worker.signal_transport_contract(point), point)
        for key, value in [('proper_ctau_mm', 0.), ('proper_ctau_mm', float('nan')),
                           ('mass_gev', True), ('generated_mass_support_gev', [.4, .5]),
                           ('schema', 'lab-flight-in-metres')]:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                worker.signal_transport_contract(dict(point, **{key: value}))

    def test_consumed_scratch_input_has_a_deletion_receipt_and_rejects_external_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root/'step1.root', root/'step2.root'
            source.write_bytes(b'validated SIM')
            output.write_bytes(b'validated DIGI')
            expected = worker.sha(source)
            report = {}
            with patch.object(worker.Path, 'cwd', return_value=root):
                worker.remove_consumed_input(source, output, report)
                self.assertFalse(source.exists())
                self.assertEqual(report['removed_scratch_inputs'], [dict(
                    path='step1.root', bytes=13, sha256=expected, consumed_by='step2.root')])
                with self.assertRaisesRegex(ValueError, 'outside this worker'):
                    worker.remove_consumed_input(root.parent/'gen.root', output, report)
                canonical = root/'canonical.root'
                canonical.write_bytes(b'preserve')
                link = root/'gen.root'
                link.symlink_to(canonical)
                with self.assertRaisesRegex(ValueError, 'outside this worker'):
                    worker.remove_consumed_input(link, output, report)
                self.assertEqual(canonical.read_bytes(), b'preserve')

    def test_signal_runner_packages_local_storage_support_and_forwards_watchdog_limits(self):
        for mechanism, mass, name, transport, smart in ((None, 15., 'dy', False, False),
                (None, 2., 'dy', True, False),
                (None, 15., 'dy', True, True),
                ('eta-etaprime-radiative', .3, 'eta_etaprime', False, False),
                ('meson-radiative', .3, 'meson_radiative', False, False),
                ('meson-radiative', .3, 'meson_radiative', True, False),
                ('meson-radiative', .3, 'meson_radiative', True, True)):
            with self.subTest(mechanism=mechanism), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); gen = root/'gen'; gen.mkdir()
                (gen/'gen.root').write_bytes(b'synthetic GEN payload')
                (gen/'manifest.json').write_text(json.dumps(dict(
                    status='GEN runtime audit passed fixture', output_sha256=worker.sha(gen/'gen.root'))))
                contract=dict(mass_gev=mass, production_mechanism=mechanism,
                    physical_width_table=dict(ctau_mm=58.55432679904731),
                    generated_mass_support_gev=[mass*.999999, mass*1.000001])
                if mechanism is None and transport:
                    contract['width_authority']=contract.pop('physical_width_table')
                (gen/'contract.json').write_text(json.dumps(contract))
                identities = [[123, 1, event] for event in range(1, 6)]
                (gen/'validation.json').write_text(json.dumps(dict(runtime_validated=True, events=5,
                    event_ids=identities, event_rows=[dict(weight=1., full_graph_sha256='0'*64) for _ in identities])))
                if smart:
                    from test_shift_material_preselection import event, inputs
                    from shift_production_selection import prepare_default_selection, bind_source
                    rows = [event(identity[-1]) for identity in identities]
                    for row, identity in zip(rows, identities):
                        row.update(event_id=identity, source_file_sha256=worker.sha(gen/'gen.root'),
                                   unknown_transport_ancestor_barcodes=[99])
                    census, authority = inputs(rows)
                    authority['sources'][0]['feature_source_sha256s'] = [worker.sha(gen/'gen.root')]
                    ledger = prepare_default_selection(dict(schema='shift-production-material-inputs-v1',
                        cohort_id='bsm-control', selection_seed='bsm-control-seed', features=census, authority=authority))
                    selected = bind_source(dict(gen_sha256=worker.sha(gen/'gen.root'),
                                               event_ids=identities, weights=[1.] * 5), ledger)
                    (gen/'material_selected_source.json').write_text(json.dumps(selected))
                templates = root/'templates'; templates.mkdir()
                for stage in range(1, 5):
                    (templates/f'step{stage}.py').write_text('# synthetic template\n')
                output = root/'detector'
                launched = []
                audit_limits = []
                def run(command, **kwargs):
                    launched.append(command)
                    if Path(command[1]).name == 'run_shift_gen_to_nano.py':
                        (output/'report.json').write_text(json.dumps(dict(complete=True)))
                    elif command[2] == 'audit-replay':
                        (output/'signal_audit.json').write_text('{}\n')
                        audit_limits.append(kwargs['timeout'])
                    else:
                        self.fail('Unexpected external command')
                    return subprocess.CompletedProcess(command, 0)
                arguments = ['detector', str(gen), '--templates', str(templates), '--output', str(output),
                             '--count', '5', '--event-stall-timeout', '1800', '--first-event-stall-timeout', '3600',
                             '--audit-timeout', '900']
                if not smart:
                    arguments.append('--legacy-replay')
                if transport:
                    arguments.append('--transport-predefined-signal-decays')
                with patch.object(sys, 'argv', arguments), patch.dict(os.environ, {'CMSSW_BASE': directory}), \
                        patch.object(signal_runner.shutil, 'which', return_value='cmsRun'), \
                        patch.object(signal_runner.subprocess, 'run', side_effect=run), redirect_stdout(io.StringIO()):
                    signal_runner.main()
                descriptor = json.loads((output/'source.json').read_text())
                self.assertTrue(descriptor['gen_path_resolved'])
                self.assertEqual(descriptor['event_ids'], identities)
                self.assertEqual(descriptor['stratum'], 'darkphoton_'+name+'_m'+str(mass))
                if smart:
                    self.assertEqual(descriptor['selected_indices'], list(range(5)))
                    self.assertEqual(descriptor['sampling_probabilities'], [1.] * 5)
                    self.assertTrue((output/'shift_production_selection.py').is_file())
                    self.assertTrue((output/'shift_material_preselection.py').is_file())
                if transport:
                    self.assertEqual(descriptor['predefined_signal_decays']['proper_ctau_mm'], 58.55432679904731)
                    self.assertEqual(descriptor['predefined_signal_decays']['mass_gev'], mass)
                else:
                    self.assertNotIn('predefined_signal_decays', descriptor)
                self.assertTrue((output/'shift_storage_paths.py').is_file())
                self.assertEqual(launched[0][launched[0].index('--event-stall-timeout') + 1], '1800')
                self.assertEqual(launched[0][launched[0].index('--first-event-stall-timeout') + 1], '3600')
                self.assertEqual(audit_limits, [900])

    def test_signal_graph_audit_reads_only_exact_selected_gen_records(self):
        from types import SimpleNamespace
        identities = [[123, 1, event] for event in (1, 4, 8, 11, 19)]

        class SelectedEvents:
            def __init__(self):
                self.position = None
                self.visited = []

            def __iter__(self):
                raise AssertionError('The complete GEN population must not be scanned per chunk')

            def to(self, position):
                self.position = position
                self.visited.append(position)

            def eventAuxiliary(self):
                run, lumi, event = identities[self.position]
                return SimpleNamespace(run=lambda: run, luminosityBlock=lambda: lumi, event=lambda: event)

        events = SelectedEvents()
        selected = [identities[3], identities[1]]
        hashes = signal_runner.selected_genparticle_hashes(
            events, identities, selected, lambda source: 'hash-'+str(source.position))
        self.assertEqual(events.visited, [3, 1])
        self.assertEqual(hashes, {(123, 1, 11): 'hash-3', (123, 1, 4): 'hash-1'})
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            signal_runner.selected_genparticle_hashes(events, identities+[identities[0]], selected, lambda _: '')
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            signal_runner.selected_genparticle_hashes(events, identities, selected+selected, lambda _: '')
        with self.assertRaisesRegex(ValueError, 'absent'):
            signal_runner.selected_genparticle_hashes(events, identities, [[123, 1, 5]], lambda _: '')
        with self.assertRaisesRegex(ValueError, 'frozen identity'):
            signal_runner.selected_genparticle_hashes(events, list(reversed(identities)), [identities[1]], lambda _: '')

    def test_publication_paths_keep_nano_and_metadata_in_current_layout(self):
        descriptor = dict(storage_contract='shift-process-bin-detector-v1',
            nano_base='/eos/user/j/test/qcd/nanoAOD',
            metadata_base='/eos/user/j/test/qcd/metadata/detector_jobs')
        self.assertEqual(worker.publication_paths(descriptor, 17), (
            '/eos/user/j/test/qcd/nanoAOD/nano_job0000017.root',
            '/eos/user/j/test/qcd/metadata/detector_jobs/job0000017'))
        self.assertEqual(worker.publication_paths(descriptor, 17, True), (
            '/eos/user/j/test/qcd/nanoAOD/canaries/nano_job0000017.root',
            '/eos/user/j/test/qcd/metadata/detector_jobs/canaries/job0000017'))
        self.assertEqual(worker.publication_paths({'output_base': '/eos/test/legacy'}, 17), (
            '/eos/test/legacy/job0000017/nano.root', '/eos/test/legacy/job0000017'))
        with self.assertRaises(ValueError):
            worker.publication_paths({**descriptor, 'nano_base': '/tmp/not-eos'}, 17)
        with self.assertRaisesRegex(ValueError, 'Unsupported detector storage contract'):
            worker.publication_paths(dict(output_base='/eos/user/j/test/metadata',
                                           storage_contract='unknown'), 17)

    def test_local_pilot_input_requires_exact_frozen_checksum(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'source.root';target=root/'gen.root'
            source.write_bytes(b'validated frozen GEN fixture')
            descriptor=dict(gen_transport='local',gen=str(source),gen_sha256=worker.sha(source))
            worker.copy_gen_input(descriptor,target)
            self.assertEqual(target.read_bytes(),source.read_bytes())
            with patch.object(worker, 'resolve_storage_path', side_effect=RuntimeError('No resolver')):
                worker.copy_gen_input({**descriptor, 'gen_path_resolved': True}, root / 'resolved.root')
            self.assertEqual((root / 'resolved.root').read_bytes(), source.read_bytes())
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
                with patch.object(sys,'argv',['worker',str(source),'--skip','0','--count','1','--job','7','--publish',
                                              '--remove-consumed-intermediates', '--absolute-stage-timeouts',
                                              '--stage-timeouts', '7200,3600,7200,7200']), \
                     patch.object(worker,'command',side_effect=run),patch.object(worker,'signatures',return_value=[row]), \
                     patch.object(worker,'audit_edm',return_value={'events':1}),patch.object(worker,'audit_nano',return_value=nano), \
                     redirect_stdout(io.StringIO()):
                    worker.main()
            finally:
                os.chdir(previous)
            receipt=root/'report.json';data=json.loads(receipt.read_text())
            self.assertTrue(data['complete'])
            self.assertEqual([item['path'] for item in data['removed_scratch_inputs']],
                             ['gen.root', 'step1.root', 'step2.root', 'step3.root'])
            self.assertTrue((root/'nano.root').is_file())
            self.assertFalse((root/'nano_readback.root').exists())
            self.assertEqual(data['nano_sha256'], data['nano_readback_sha256'])
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

    def test_first_event_material_initialization_has_its_own_bounded_allowance(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / 'cmsRun.log'; log.write_text('')
            report = {}
            with patch.object(worker.time, 'monotonic', return_value=0), patch.object(worker, 'progress'):
                poll = worker.event_progress(log, report, 'NANO', {}, 1800,
                    expected_records=5, first_event_stall_seconds=3600)
                with patch.object(worker.time, 'monotonic', return_value=1801):
                    poll()  # no first record yet; startup has the same bounded allowance
                log.write_text('Begin processing the 1st record. Run 1, Event 106\n')
                with patch.object(worker.time, 'monotonic', return_value=1900):
                    poll()
                self.assertEqual(report['stage_timeout_seconds'], 3600)
                with patch.object(worker.time, 'monotonic', return_value=3701):
                    poll()  # lazy first-event material tables can exceed the later-event limit
                with patch.object(worker.time, 'monotonic', return_value=5501):
                    with self.assertRaisesRegex(TimeoutError, '3600 seconds; records started=1'):
                        poll()

    def test_second_record_restores_normal_stall_limit_and_scheduler_telemetry(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / 'cmsRun.log'
            log.write_text('Begin processing the 1st record. Run 1, Event 106\n')
            report = {}
            with patch.object(worker.time, 'monotonic', return_value=0), patch.object(worker, 'progress') as update:
                poll = worker.event_progress(log, report, 'NANO', {}, 1800,
                    expected_records=5, first_event_stall_seconds=3600)
                poll()
                with log.open('a') as stream:
                    stream.write('Begin processing the 2nd record. Run 1, Event 107\n')
                with patch.object(worker.time, 'monotonic', return_value=2000):
                    poll()
                self.assertEqual(report['stage_timeout_seconds'], 1800)
                self.assertEqual(update.call_count, 2)
                with patch.object(worker.time, 'monotonic', return_value=3801):
                    with self.assertRaisesRegex(TimeoutError, '1800 seconds; records started=2'):
                        poll()

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
                         patch.dict(worker._last_chirp_values, {}, clear=True), \
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


class TelemetryDeduplication(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        previous = Path.cwd()
        os.chdir(temporary.name)
        self.addCleanup(os.chdir,previous)
        for context in (patch.dict(worker.os.environ,{'_CONDOR_SCRATCH_DIR':temporary.name}),
                        patch.dict(worker._last_chirp_values,{},clear=True),
                        patch.object(worker.shutil,'which',return_value='/fake/condor_chirp')):
            context.start()
            self.addCleanup(context.stop)
        self.report = dict(source_stratum='fixed',stage_timeout_seconds=7200,
                           stage_deadline_epoch=10000,stage_records_started=0)

    def updates(self, chirp):
        return [(call.args[0][2],json.loads(call.args[0][3])) for call in chirp.call_args_list]

    def test_identical_values_keep_only_the_progress_update(self):
        with patch.object(worker.time,'time',return_value=1000),patch.object(worker.subprocess,'run') as chirp:
            worker.progress(self.report,'SIM',{'GEN':5})
            worker.progress(self.report,'SIM',{'GEN':5})
        updates=self.updates(chirp)
        self.assertEqual(len(updates),8)
        self.assertEqual(updates[-1],('ShiftNtupleProgressEpoch',1000))
        self.assertEqual(json.loads(Path('report.json').read_text())['validated_tier_events'],{'GEN':5})

    def test_stage_changes_follow_progress_and_keep_actual_record_updates(self):
        with patch.object(worker.subprocess,'run'):
            worker.progress(self.report,'SIM',{'GEN':5})
        self.report.update(stage_timeout_seconds=900,stage_deadline_epoch=20000,stage_records_started=1)
        with patch.object(worker.time,'time',return_value=2000),patch.object(worker.subprocess,'run') as chirp:
            worker.progress(self.report,'NANO_AUDIT',{'GEN':5})
        self.assertEqual(self.updates(chirp),[
            ('ShiftNtupleProgressEpoch',2000),('ShiftNtupleStageTimeout',900),
            ('ShiftNtupleStageDeadlineEpoch',20000),('ShiftNtupleStageRecordsStarted',1),
            ('ShiftNtupleTier','NANO_AUDIT')])

    def test_failed_write_is_not_cached_and_retries_without_aborting(self):
        with patch.object(worker.subprocess,'run'):
            worker.progress(self.report,'SIM',{'GEN':5})
        self.report['stage_timeout_seconds']=900
        def fail_timeout(argv,**options):
            if argv[2]=='ShiftNtupleStageTimeout':
                raise subprocess.TimeoutExpired('chirp',10)
        with patch.object(worker.time,'time',return_value=2000), \
             patch.object(worker.subprocess,'run',side_effect=fail_timeout):
            worker.progress(self.report,'SIM',{'GEN':5})
        self.assertIn('telemetry_error',self.report)
        self.assertEqual(worker._last_chirp_values['ShiftNtupleStageTimeout'],7200)
        with patch.object(worker.time,'time',return_value=2030),patch.object(worker.subprocess,'run') as chirp:
            worker.progress(self.report,'SIM',{'GEN':5})
        self.assertEqual(self.updates(chirp),[
            ('ShiftNtupleProgressEpoch',2030),('ShiftNtupleStageTimeout',900)])


if __name__ == '__main__':
    unittest.main()
