# Code style: AI-code. AI-generated optional EDM publication integration tests.
"""Exercise the real worker orchestration with synthetic detector and storage I/O."""
import hashlib
import copy
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'tea_shift_cmssw/utils'))
import run_shift_material_response as worker
from summarize_shift_material_response import summarize
from shift_material_response import source_record, prepared_source_record
from test_shift_material_response import jpsi_receipt_fixture, preparation_receipt_fixture


class MaterialResponsePublicationTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        previous = Path.cwd()
        self.addCleanup(os.chdir, previous)
        os.chdir(directory.name)
        self.tools = Path.cwd()/'frozen/tools'
        self.runtime = Path.cwd()/'runtime/CMSSW_17_0_0_pre4'
        self.remote = {}
        self.commands = []
        self.annotations = []
        self.corrupt_readback = None
        self.timing_signature_mismatch = False
        self.fail_nano = False
        self.source_bytes = b'original GEN'
        self.exposure = {'full_source_trials': 100}
        self.original = dict(id=[1, 2, 3], weight=2.)
        self.request = dict(source=dict(source_id='source', gen_sha256=hashlib.sha256(self.source_bytes).hexdigest(),
            stratum='qcd_20to25', url='root://synthetic/gen.root', mean_abs_native_weight=2.,
            sigma_nd_pb=10., full_source_trials=100),
            sample=dict(id='sample', event_ids=[[1, 2, 3]], native_weights=[2.], indices=[0],
                        probabilities=[.5], seed=123),
            private_input_hashes={}, geometry_path='synthetic.gdml', frame={},
            policy=dict(momentum_scale_GeV=20.), stage_timeout_seconds=600,
            publication_bound_bytes=1024*1024, publication_directory='/eos/synthetic/response')

    def command(self, argv, **kwargs):
        self.commands.append(argv)
        if argv[0] == 'xrdcp':
            source, target = argv[-2:]
            if target == 'gen.root':
                Path(target).write_bytes(self.source_bytes)
            elif source.startswith('root://'):
                data = self.remote[source]
                Path(target).write_bytes(b'corrupted' if source.endswith('/'+str(self.corrupt_readback)) else data)
            else:
                if target in self.remote:
                    raise FileExistsError('Synthetic EOS output already exists')
                self.remote[target] = Path(source).read_bytes()
        elif argv[0] == 'cmsRun':
            if argv[1] == 'step4_cfg.py' and self.fail_nano:
                raise RuntimeError('Synthetic Nano end-lumi failure')
            output = {'timing_cfg.py': 'timed_gen.root', 'sim_cfg.py': 'step1.root',
                      'step2_cfg.py': 'step2.root', 'step3_cfg.py': 'step3.root',
                      'step4_cfg.py': 'step4.root'}[argv[1]]
            Path(output).write_bytes(output.encode()*(4096 if output in ('step1.root', 'step3.root') else 1))

    def run_worker(self):
        Path('request.json').write_text(json.dumps(self.request))
        geometry = types.SimpleNamespace(LockDefaultUnits=lambda _: None, SetDefaultUnits=lambda _: None,
                                         kRootUnits=0, Import=lambda _: object())
        events = types.SimpleNamespace(size=lambda: 1, to=lambda _: None)
        root = types.SimpleNamespace(TGeoManager=geometry)
        fwlite = types.SimpleNamespace(Events=lambda _: events)
        with mock.patch.dict(sys.modules, {'ROOT': root, 'DataFormats': types.ModuleType('DataFormats'),
                                          'DataFormats.FWLite': fwlite}), \
                mock.patch.dict(os.environ, {'_CONDOR_SCRATCH_DIR': str(Path.cwd()),
                    'CMSSW_BASE': str(self.runtime), 'SCRAM_ARCH': 'test_arch'}), \
                mock.patch.multiple(worker, __file__=str(self.tools/'scripts/run_shift_material_response.py'),
                    command=mock.Mock(side_effect=self.command), progress=mock.Mock(), event_progress=mock.Mock()), \
                mock.patch.object(sys, 'argv', ['worker', 'request.json']), \
                mock.patch.object(worker, 'metadata', return_value=self.exposure), \
                mock.patch.object(worker, 'signatures', side_effect=lambda path, *args, **kwargs:
                    [dict(self.original, hepmc_sha256=('b' if self.timing_signature_mismatch
                          and Path(path).name == 'timed_gen.root' else 'a')*64)]), \
                mock.patch.object(worker, 'audit_edm', return_value={'passed': True}), \
                mock.patch.object(worker, 'audit_nano', return_value={'passed': True}), \
                mock.patch.object(worker, 'audit', return_value=dict(passed=True,
                    full_original_run_lumi_metadata=self.exposure)), \
                mock.patch('audit_shift_material_scout.identity', return_value=tuple(self.original['id'])), \
                mock.patch.object(worker, 'hepmc_unit_names'), \
                mock.patch.object(worker, 'read_hepmc', return_value=([], self.original['weight'],
                                                                      [self.original['weight']], [])), \
                mock.patch.object(worker, 'event_features', return_value={}), \
                mock.patch.object(worker, 'pair_priority', return_value={}), \
                mock.patch.object(worker, 'admission_probability', return_value=.2), \
                mock.patch.object(worker, 'annotate_proposal_weights',
                                  side_effect=lambda *args, **kwargs: self.annotations.append((args, kwargs)) or {'passed': True}), \
                mock.patch.object(worker, 'response', return_value=[dict(event_id=self.original['id'],
                    retained_pairs=[], tail_counts={'pairs': 0})]), \
                mock.patch('builtins.print'):
            worker.main()
        return json.loads(Path('report.json').read_text())

    def test_default_keeps_only_nano_and_evidence_and_downstream_remains_supported(self):
        report = self.run_worker()
        self.assertTrue(report['complete'])
        self.assertEqual(report['diagnostic_edm_outputs'], [])
        self.assertEqual([Path(item['url']).name for item in report['published']], ['nano.root', 'evidence.tar.gz'])
        self.assertFalse(Path('step1.root').exists())
        self.assertFalse(Path('step3.root').exists())
        self.assertNotIn('prepared_hepmc2_copy_plugin', report)
        self.assertFalse(any(args[0] == 'edmPluginRefresh' for args in self.commands))
        sim = next(args[2] for args in self.commands if len(args) > 3 and args[1] == '-c' and 'sim_cfg.py' in args[2])
        self.assertNotIn('ShiftHepMC2CopyProducer', sim)
        self.check_downstream(report)

    def test_native_DY_uses_untimed_input_accepted_denominator_and_exact_inverse_sampling(self):
        source = self.request['source']
        del source['sigma_nd_pb'], source['full_source_trials']
        source.update(source_family='dy', denominator_kind='accepted_gen_events', full_source_events=100,
                      native_cross_section_pb=10., mean_abs_native_weight=1., timing_input_label='VtxSmeared',
                      generator_algorithm='native-unfiltered-unit-weight-LO-DY', decay_dependent_filter=False)
        self.original['weight'] = 1.
        self.request['sample']['native_weights'] = [1.]
        self.exposure = dict(runs=[dict(run=1)], lumis=[dict(run=1, lumi=2,
            original_GenLumiInfoProduct_present=True, GenLumiInfoProduct={'native_RUN_LO_events': 100})])
        report = self.run_worker()
        self.assertTrue(report['nano_optional_filter_table_omitted'])
        configs = [args for args in self.commands if 'make-config' in args]
        self.assertEqual([args[-2] for args in configs], ['1', '1', '1'])
        timing = next(args for args in self.commands if len(args) > 3 and args[1] == '-c'
                      and 'timing_cfg.py' in args[2])
        self.assertEqual(timing[-2:], ['VtxSmeared', ''])
        rows, exposure = self.annotations[0][0][1:3]
        self.assertEqual(rows[0]['sampling_gen_weight'], 2.)
        self.assertEqual(exposure[0]['full_source_events'], 100)
        self.assertNotIn('full_source_trials', exposure[0])
        with tarfile.open(fileobj=io.BytesIO(self.remote['root://eosuser.cern.ch/'+
                                                       self.request['publication_directory']+'/evidence.tar.gz']), mode='r:gz') as archive:
            response = json.load(archive.extractfile('response.json'))
        self.assertEqual(response['events'][0]['rate_weight'], .1)
        self.check_downstream(report)

    def test_optional_filter_table_requires_unfiltered_DY_and_exact_native_accounting(self):
        source = dict(source_family='dy', denominator_kind='accepted_gen_events', full_source_events=100,
            native_cross_section_pb=10., generator_algorithm='native-unfiltered-unit-weight-LO-DY',
            decay_dependent_filter=False)
        exposure = dict(runs=[dict(run=1)], lumis=[dict(run=1, lumi=1,
            original_GenLumiInfoProduct_present=True, GenLumiInfoProduct={'native':100})])
        source['original_full_Run_Lumi_metadata'] = copy.deepcopy(exposure)
        self.assertTrue(worker.nano_missing_filter_lumi(source, exposure))
        for key, value in (('decay_dependent_filter', True), ('generator_algorithm', 'filtered-DY')):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'unqualified DY'):
                worker.nano_missing_filter_lumi(dict(source, **{key:value}), exposure)
        with self.assertRaisesRegex(ValueError, 'bound source'):
            worker.nano_missing_filter_lumi(source, dict(exposure, runs=[]))
        del source['original_full_Run_Lumi_metadata']
        with self.assertRaisesRegex(ValueError, 'native DY luminosity'):
            worker.nano_missing_filter_lumi(source, dict(runs=[], lumis=[]))
        with_filter = copy.deepcopy(exposure)
        with_filter['lumis'][0]['GenFilterInfo'] = {'native':100}
        self.assertFalse(worker.nano_missing_filter_lumi(source, with_filter))
        with_filter['lumis'].append(exposure['lumis'][0])
        with self.assertRaisesRegex(ValueError, 'mixed GenFilterInfo'):
            worker.nano_missing_filter_lumi(source, with_filter)
        # Weighted or filtered populations keep the table and its strict missing-input failure.
        self.assertFalse(worker.nano_missing_filter_lumi(self.request['source'], exposure))

    def prepare_completed_input(self):
        native_path, native = jpsi_receipt_fixture(Path.cwd(), family='qcd_ordinary')
        raw = source_record(native_path, family='qcd_ordinary')
        identity = native['semantic_audit']['event_ids'][0]
        weight = native['semantic_audit']['weights'][0]
        self.original = dict(id=identity, weight=weight)
        self.source_bytes = b'prepared GEN'
        receipt, _ = preparation_receipt_fixture(Path.cwd(), raw, [identity], [weight], self.source_bytes)
        prepared = prepared_source_record(raw, receipt, expected_sha256=hashlib.sha256(receipt.read_bytes()).hexdigest())
        self.request['source'] = prepared
        plugin = self.tools/'plugin/pluginShiftHepMC2CopyProducer.so'
        plugin.parent.mkdir(parents=True)
        plugin.write_bytes(b'synthetic pinned HepMC2 copy plugin')
        self.request['hepmc2_copy_plugin_sha256'] = hashlib.sha256(plugin.read_bytes()).hexdigest()
        self.request['sample'].update(event_ids=[identity], native_weights=[weight], probabilities=[.25])
        self.exposure = prepared['original_full_Run_Lumi_metadata']
        return raw, prepared

    def test_prepared_source_keeps_raw_SHA_weights_and_exact_unshifted_timing_product(self):
        raw, prepared = self.prepare_completed_input()
        report = self.run_worker()
        timing = next(args for args in self.commands if len(args) > 3 and args[1] == '-c'
                      and 'timing_cfg.py' in args[2])
        self.assertEqual(timing[-2:], ['shiftFullMuonDecays', 'SHIFTFULLGEN'])
        sim = next(args[2] for args in self.commands if len(args) > 3 and args[1] == '-c' and 'sim_cfg.py' in args[2])
        self.assertIn("p.generatorSmeared=cms.EDProducer('ShiftHepMC2CopyProducer',src=cms.InputTag('shiftEventTime','','SHIFTTIMING'))", sim)
        self.assertEqual(report['prepared_hepmc2_copy_plugin']['sha256'], self.request['hepmc2_copy_plugin_sha256'])
        installed = self.runtime/'lib/test_arch/pluginShiftHepMC2CopyProducer.so'
        self.assertEqual(hashlib.sha256(installed.read_bytes()).hexdigest(), self.request['hepmc2_copy_plugin_sha256'])
        self.assertEqual(self.commands[0], ['edmPluginRefresh', str(installed.parent)])
        self.assertEqual(report['original_source_sha256'], raw['gen_sha256'])
        self.assertEqual(report['prepared_input_GEN_sha256'], prepared['gen_sha256'])
        rows, exposures = self.annotations[0][0][1:3]
        self.assertEqual(rows[0]['original_source_sha256'], raw['gen_sha256'])
        self.assertEqual(rows[0]['sampling_probability'], .25)
        self.assertEqual(rows[0]['sampling_gen_weight'], .8)
        self.assertEqual(exposures[0]['full_source_trials'], 4)
        with tarfile.open(fileobj=io.BytesIO(self.remote['root://eosuser.cern.ch/'+
                                                       self.request['publication_directory']+'/evidence.tar.gz']), mode='r:gz') as archive:
            response = json.load(archive.extractfile('response.json'))
        self.assertEqual(response['source_GEN_sha256'], raw['gen_sha256'])
        self.assertEqual(response['prepared_input_GEN_sha256'], prepared['gen_sha256'])
        self.assertEqual(response['events'][0]['original_source_sha256'], raw['gen_sha256'])
        self.assertLess(len(json.dumps(report).encode()), 65536)
        self.check_downstream(report)

    def test_prepared_source_accounting_mismatch_stops_before_any_detector_stage(self):
        self.prepare_completed_input()
        self.exposure = {'changed_full_source_accounting': True}
        with self.assertRaisesRegex(ValueError, 'full original source Run/Lumi'):
            self.run_worker()
        self.assertFalse(any(args[0] == 'cmsRun' for args in self.commands))

    def test_prepared_source_requires_exact_graph_equality_after_physical_timing(self):
        self.prepare_completed_input()
        self.timing_signature_mismatch = True
        with self.assertRaisesRegex(ValueError, 'timing translation changed particle graph'):
            self.run_worker()
        self.assertEqual([args[1] for args in self.commands if args[0] == 'cmsRun'], ['timing_cfg.py'])

    def test_prepared_source_cannot_silently_time_generatorSmeared_again(self):
        self.prepare_completed_input()
        self.request['source']['timing_input_label'] = 'generatorSmeared'
        with self.assertRaisesRegex(ValueError, 'bound native preparation'):
            self.run_worker()
        self.assertFalse(self.commands)

    def test_prepared_source_rejects_missing_or_changed_plugin_before_GEN_download(self):
        self.prepare_completed_input()
        del self.request['hepmc2_copy_plugin_sha256']
        with self.assertRaisesRegex(ValueError, 'copy plugin must be a lowercase SHA'):
            self.run_worker()
        self.assertFalse(self.commands)
        self.request['hepmc2_copy_plugin_sha256'] = 'e'*64
        with self.assertRaisesRegex(ValueError, 'copy plugin SHA differs'):
            self.run_worker()
        self.assertFalse(self.commands)

    def test_plugin_install_rejects_shared_runtime_and_symlink_escape_without_writing(self):
        self.prepare_completed_input()
        with tempfile.TemporaryDirectory() as outside:
            shared = Path(outside)/'shared/CMSSW'
            with mock.patch.dict(os.environ, {'_CONDOR_SCRATCH_DIR': str(Path.cwd()),
                    'CMSSW_BASE': str(shared), 'SCRAM_ARCH': 'test_arch'}):
                with self.assertRaisesRegex(ValueError, 'outside worker-local'):
                    worker.install_prepared_hepmc2_copy(self.request, self.tools)
            self.assertFalse(shared.exists())
            library = self.runtime/'lib/test_arch'
            library.mkdir(parents=True)
            (library/'pluginShiftHepMC2CopyProducer.so').symlink_to(Path(outside)/'outside.so')
            with mock.patch.dict(os.environ, {'_CONDOR_SCRATCH_DIR': str(Path.cwd()),
                    'CMSSW_BASE': str(self.runtime), 'SCRAM_ARCH': 'test_arch'}):
                with self.assertRaisesRegex(ValueError, 'outside worker-local'):
                    worker.install_prepared_hepmc2_copy(self.request, self.tools)
            self.assertFalse((Path(outside)/'outside.so').exists())

    def test_plugin_install_requires_explicit_Condor_scratch(self):
        self.prepare_completed_input()
        environment = dict(os.environ)
        environment.pop('_CONDOR_SCRATCH_DIR', None)
        with mock.patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(ValueError, 'worker-local Condor runtime'):
                worker.install_prepared_hepmc2_copy(self.request, self.tools)

    def test_all_edm_tiers_are_hash_verified_before_cleanup_and_declared_in_evidence(self):
        self.request['diagnostic_edm_tiers'] = ['SIM', 'DIGIHLT', 'RECO']
        report = self.run_worker()
        self.assertTrue(report['complete'])
        self.assertEqual([Path(item['url']).name for item in report['published']],
                         ['nano.root', 'evidence.tar.gz', 'sim.root', 'digi.root', 'reco.root'])
        base = 'root://eosuser.cern.ch/'+self.request['publication_directory']
        with tarfile.open(fileobj=io.BytesIO(self.remote[base+'/evidence.tar.gz']), mode='r:gz') as archive:
            evidence = json.load(archive.extractfile('report.json'))
        self.assertEqual(evidence['diagnostic_edm_outputs'], report['diagnostic_edm_outputs'])
        for item in report['diagnostic_edm_outputs']:
            data = self.remote[base+'/'+item['published_filename']]
            self.assertEqual(item['sha256'], hashlib.sha256(data).hexdigest())
            self.assertEqual(item['bytes'], len(data))
            self.assertFalse(Path(item['local_filename']).exists())
        checkpoint = report['validated_reco_checkpoint']['published'][0]
        self.assertEqual(checkpoint, next(item for item in report['published'] if item['url'].endswith('/reco.root')))
        uploads = [args for args in self.commands if args[0]=='xrdcp' and args[-1]==checkpoint['url']]
        self.assertEqual(len(uploads), 1)
        self.check_downstream(report)

    def check_downstream(self, report):
        """Use the actual canary verifier rather than assuming extra files are accepted."""
        job = Path('jobs/sample')
        job.mkdir(parents=True)
        (job/'request.json').write_bytes(Path('request.json').read_bytes())
        (job/'result.json').write_text(json.dumps(dict(report, exit_code=0)))
        (job/'status.json').write_text(json.dumps(dict(complete=True)))
        base = 'root://eosuser.cern.ch/'+self.request['publication_directory']
        (job/'return_payload.tar.gz').write_bytes(self.remote[base+'/evidence.tar.gz'])
        sample = self.request['sample'] | {'source_id': 'source', 'canary': True}
        Path('plan.json').write_text(json.dumps(dict(samples=[sample], sources={'source': self.request['source']})))
        with mock.patch('builtins.print'):
            result = summarize(Path.cwd(), canary=True)
        self.assertEqual(result['originals'], 1)
        self.assertEqual(result['publications'], report['published'])

    def test_invalid_tiers_fail_before_any_io_or_detector_stage(self):
        for value in (None, 'SIM', {}, ['SIM', 'SIM'], ['GEN'], ['SIM', 'reco'], [True], [[]]):
            with self.subTest(value=value):
                Path('request.json').write_text(json.dumps(dict(diagnostic_edm_tiers=value)))
                with mock.patch.object(sys, 'argv', ['worker', 'request.json']), \
                        mock.patch.object(worker, 'command') as command:
                    with self.assertRaisesRegex(ValueError, 'diagnostic_edm_tiers'):
                        worker.main()
                    command.assert_not_called()
                self.assertFalse(Path('report.json').exists())

    def test_raw_ordinary_QCD_is_rejected_before_io_or_detector_response(self):
        self.request['source'].update(source_family='qcd_ordinary', requires_full_muon_completion=True,
                                      muon_enhancement_complete=False)
        Path('request.json').write_text(json.dumps(self.request))
        with mock.patch.object(sys, 'argv', ['worker', 'request.json']), \
                mock.patch.object(worker, 'command') as command:
            with self.assertRaisesRegex(ValueError, 'full natural muon completion'):
                worker.main()
            command.assert_not_called()
        self.assertFalse(Path('report.json').exists())

    def test_registered_bound_includes_edm_and_prevents_any_remote_publication(self):
        self.request.update(diagnostic_edm_tiers=['SIM', 'RECO'], publication_bound_bytes=70000)
        with self.assertRaisesRegex(ValueError, 'registered storage allocation'):
            self.run_worker()
        self.assertFalse(Path('step4.root').exists())
        self.assertFalse(self.remote)
        self.assertTrue(Path('step1.root').exists())
        self.assertTrue(Path('step3.root').exists())
        self.assertFalse(json.loads(Path('report.json').read_text())['complete'])

    def test_requested_RECO_is_hash_checked_and_retained_if_Nano_later_fails(self):
        self.request['diagnostic_edm_tiers'] = ['SIM', 'RECO']
        self.fail_nano = True
        with self.assertRaisesRegex(RuntimeError, 'Nano end-lumi failure'):
            self.run_worker()
        report = json.loads(Path('report.json').read_text())
        self.assertFalse(report['complete'])
        checkpoint = report['validated_reco_checkpoint']
        self.assertTrue(checkpoint['audit']['passed'])
        published = checkpoint['published'][0]
        self.assertEqual(published['sha256'], hashlib.sha256(Path('step3.root').read_bytes()).hexdigest())
        self.assertEqual(self.remote[published['url']], Path('step3.root').read_bytes())
        self.assertEqual(list(self.remote), [published['url']])
        self.assertFalse(any(url.endswith('/complete.json') for url in self.remote))
        self.assertEqual(report['full_source_exposure'], self.exposure)

    def test_final_combined_bound_keeps_only_existing_RECO_checkpoint_on_failure(self):
        self.request.update(diagnostic_edm_tiers=['SIM', 'RECO'], publication_bound_bytes=110000)
        with self.assertRaisesRegex(ValueError, 'registered storage allocation'):
            self.run_worker()
        report = json.loads(Path('report.json').read_text())
        self.assertFalse(report['complete'])
        self.assertEqual(list(self.remote), [report['validated_reco_checkpoint']['published'][0]['url']])
        self.assertLessEqual(len(next(iter(self.remote.values())))+65536,
                             self.request['publication_bound_bytes'])

    def test_bad_edm_readback_keeps_scratch_and_never_publishes_complete_marker(self):
        self.request['diagnostic_edm_tiers'] = ['SIM', 'RECO']
        self.corrupt_readback = 'sim.root'
        with self.assertRaisesRegex(ValueError, 'Publication readback differs'):
            self.run_worker()
        self.assertFalse(any(url.endswith('/complete.json') for url in self.remote))
        self.assertTrue(Path('step1.root').exists())
        self.assertTrue(Path('step3.root').exists())
        self.assertFalse(json.loads(Path('report.json').read_text())['complete'])


if __name__ == '__main__':
    unittest.main()
