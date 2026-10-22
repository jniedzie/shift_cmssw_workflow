# Code style: AI-code. AI-generated meson-model publication ownership tests.
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import run_dark_photon_meson_production as production


class MesonModelReplacement(unittest.TestCase):
    def pin(self, root, name, value):
        path = root / name
        path.write_text(json.dumps(value))
        return dict(path=str(path), sha256=production.durable.sha(path))

    def fixture(self, root):
        previous = root / 'previous'
        previous.mkdir()
        conditions = self.pin(root, 'conditions.json', dict(unchanged_detector=True))
        reference = self.pin(root, 'reference.json', dict(
            schema='shift-meson-physical-point-reference-v1', points={
                'm0p3_prompt': dict(mass_gev=.3, epsilon=.001,
                    target_mean_lab_flight_m=None, proper_ctau_mm=.01)}))
        card = self.pin(root, 'old_model.json', dict(channels=['eta->gamma_Aprime']))
        scope = self.pin(root, 'old_scope.json', dict(
            schema='shift-dark-photon-meson-coverage-v1', model_sha256=card['sha256'],
            included_channels=['eta->gamma_Aprime'], physics_complete=False,
            remaining_channels=[dict(channel='rho0->piplus_piminus_Aprime',
                status='missing_model', reason='Separate qualification is required')]))
        provenance = self.pin(root, 'runtime.json', dict(
            schema='shift-bsm-gen-runtime-provenance-v1', archive_sha256='a'*64,
            archive_bytes=100, scram_arch='el9_amd64_gcc13',
            cmssw_git_head='synthetic-commit', cmssw_git_status=[]))
        rows = [dict(batch=name, point='m0p3_prompt', mass_gev=.3, epsilon=.001,
                     target_mean_lab_flight_m=None, seed=100+index,
                     run_number=200+index, operational_canary=name == 'canary')
                for index, name in enumerate(('canary', 'bulk'))]
        old = dict(schema='shift-dp-meson-production-v1', directory=str(previous),
            production_mechanism='meson-radiative', batches=rows, conditions=conditions,
            lifetime_reference_authority=reference, model_card=card['path'],
            meson_completion_scope=scope, calibration_authority=dict(sha256='old-calibration'),
            gen_runtime=dict(url='root://eosuser.cern.ch//eos/synthetic/runtime.tar.gz',
                cmssw_relative='CMSSW_17_0_0_pre4', scram_arch='el9_amd64_gcc13',
                bytes=100, sha256='a'*64, provenance=provenance))
        authority = self.pin(root, 'previous.json', old)
        controller = root / 'controller'
        controller.mkdir()
        (controller/'controller.sub').write_text('synthetic submit description')
        authority['controller_receipt'] = self.pin(root, 'controller_receipt.json', dict(
            cluster=123, task=str(controller),
            submit_sha256=production.durable.sha(controller/'controller.sub')))
        current = dict(production_mechanism='meson-radiative',
            prior_meson_publication=authority, conditions=conditions,
            lifetime_reference_authority=reference,
            calibration_authority=dict(sha256='new-calibration'),
            batches=[dict(row, seed=row['seed']+1000, run_number=row['run_number']+1000)
                     for row in rows])
        entries = {}
        for row in rows:
            directory = previous/'batches'/row['batch']/'production/completed_points'
            directory.mkdir(parents=True)
            campaign = 'old-model-'+row['batch']
            prefix = '/eos/synthetic/darkphoton_m0p3_prompt/'+campaign
            histogram = prefix+'/histograms_merged/ntuple_0.root'
            report = dict(point=row['point'], complete=True, events=26, final_output_base=prefix,
                score_histograms=dict(marker=dict(complete=True, published=dict(histograms=dict(
                    path=histogram, sha256='b'*64)))))
            receipt = self.pin(directory, row['point']+'.json', report)
            entries[row['batch']] = dict(published=dict(batch=row['batch'], campaign=campaign,
                events=26, point_receipt_path=receipt['path'], point_receipt_sha256=receipt['sha256'],
                histogram_path=histogram, histogram_sha256='b'*64))
        state = dict(complete=False, checked_utc=datetime.now(timezone.utc).isoformat(), batches=entries)
        (previous/'state.json').write_text(json.dumps(state))
        return current, old, state, previous

    def test_new_model_waits_for_every_prior_batch_at_the_same_point(self):
        with tempfile.TemporaryDirectory() as temporary:
            config, _, state, previous = self.fixture(Path(temporary))
            batch = config['batches'][1]
            ready, evidence = production.prior_publication_ready(config, batch)
            self.assertTrue(ready)
            self.assertEqual(evidence['allowed_previous_campaigns'],
                             ['old-model-canary', 'old-model-bulk'])
            state['batches']['canary'] = {}
            (previous/'state.json').write_text(json.dumps(state))
            ready, evidence = production.prior_publication_ready(config, batch)
            self.assertFalse(ready)
            self.assertEqual(evidence['pending_batch'], 'canary')

    def test_replacement_cannot_change_conditions_point_or_source_namespace(self):
        for change in ('conditions', 'mass', 'epsilon', 'ctau', 'lab', 'seed', 'run', 'model'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                config, old, _, _ = self.fixture(root)
                if change == 'conditions':
                    config['conditions'] = self.pin(root, 'other_conditions.json', dict(unchanged_detector=False))
                elif change in ('mass', 'epsilon', 'lab'):
                    key = dict(mass='mass_gev', epsilon='epsilon', lab='target_mean_lab_flight_m')[change]
                    config['batches'][0][key] = 42.
                elif change == 'ctau':
                    reference = json.loads(Path(config['lifetime_reference_authority']['path']).read_text())
                    reference['points']['m0p3_prompt']['proper_ctau_mm'] = .02
                    config['lifetime_reference_authority'] = self.pin(root, 'other_reference.json', reference)
                elif change in ('seed', 'run'):
                    key = 'seed' if change == 'seed' else 'run_number'
                    config['batches'][0][key] = old['batches'][0][key]
                else:
                    Path(old['model_card']).write_text(json.dumps(dict(channels=['omega->pi0_Aprime'])))
                with self.assertRaises(ValueError):
                    production.prior_meson_configuration(config)

    def test_pinned_predecessor_is_checked_even_when_publications_exist(self):
        with tempfile.TemporaryDirectory() as temporary:
            config, _, _, _ = self.fixture(Path(temporary))
            path = Path(config['prior_meson_publication']['path'])
            path.write_text(path.read_text()+' ')
            with self.assertRaisesRegex(ValueError, 'authority changed'):
                production.prior_publication_ready(config, config['batches'][1])

    def test_terminal_predecessor_still_requires_native_absence_and_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            config, _, state, previous = self.fixture(Path(temporary))
            state['complete'] = True
            (previous/'state.json').write_text(json.dumps(state))
            with patch.object(production.durable, 'jobs', return_value=[dict(ClusterId=123)]), \
                 patch.object(production, 'native_status') as native:
                self.assertFalse(production.prior_publication_ready(config, config['batches'][1])[0])
                native.assert_not_called()
            with patch.object(production.durable, 'jobs', return_value=[]), \
                 patch.object(production, 'native_status', return_value='complete'):
                self.assertTrue(production.prior_publication_ready(config, config['batches'][1])[0])

    def test_future_owner_breaks_the_checksum_cycle_without_loosening_job_identity(self):
        for change in ('none', 'eta_predecessor', 'physics', 'receipt_sha', 'receipt_task', 'native_iwd',
                       'native_cmd', 'native_args', 'external_config', 'parent_path'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                config, old, state, previous = self.fixture(root)
                old['tools'] = str(previous/'tools')
                if change == 'eta_predecessor':
                    old['production_mechanism'] = 'eta-etaprime-radiative'
                self.pin(Path(config['prior_meson_publication']['path']).parent,
                         'previous.json', old)
                config['prior_meson_publication']['sha256'] = production.durable.sha(
                    config['prior_meson_publication']['path'])
                state['complete'] = True
                (previous/'state.json').write_text(json.dumps(state))
                operations = root/'future_operations'
                operations.mkdir()
                candidate = json.loads(json.dumps(old))
                candidate['physics_tools'] = old['tools']
                if change == 'physics':
                    candidate['batches'][0]['epsilon'] = .002
                config_pin = self.pin(operations, 'config.json', candidate)
                wrapper = operations/'lcg_controller.sh'
                wrapper.write_text('#!/bin/bash\n# Synthetic fixed controller.\n')
                template = operations/'controller.sub.in'
                template.write_text('initialdir = '+str(operations)+'\nexecutable = '+str(wrapper)+
                                    '\narguments = OPERATIONS_SEAL_DIGEST\n')
                seal_pin = self.pin(operations, 'seal.json', dict(files={
                    config_pin['path']:config_pin['sha256'], str(wrapper):production.durable.sha(wrapper),
                    str(template):production.durable.sha(template)}))
                submit = operations/'controller.sub'
                submit.write_text(template.read_text().replace('OPERATIONS_SEAL_DIGEST', seal_pin['sha256']))
                receipt = dict(cluster=456, task=str(operations), submit_sha256=production.durable.sha(submit))
                if change == 'receipt_sha':
                    receipt['submit_sha256'] = 'b'*64
                elif change == 'receipt_task':
                    receipt['task'] = str(root/'unrelated_recovery')
                self.pin(operations, 'submission.json', receipt)
                native = dict(ClusterId=456, ProcId=0, JobStatus=4, ExitCode=0, ExitBySignal=False,
                    Iwd=str(operations), Cmd=str(wrapper), Args=seal_pin['sha256'])
                if change.startswith('native_'):
                    key = dict(native_iwd='Iwd', native_cmd='Cmd', native_args='Args')[change]
                    native[key] = '/unrelated/recovery'
                config['reviewed_terminal_native_jobs'] = self.pin(root, 'terminal_seed.json', dict(
                    schema='shift-reviewed-meson-successful-native-seed-v1',
                    jobs={'456':production.terminal_record(receipt, native)}))
                config['prior_meson_controller_directory'] = str(operations)
                if change == 'external_config':
                    external = root/'outside_config.json'
                    (operations/'config.json').rename(external)
                    (operations/'config.json').symlink_to(external)
                elif change == 'parent_path':
                    config['prior_meson_controller_directory'] = str(operations)+'/../future_operations'
                with patch.object(production.durable, 'jobs', return_value=[]), \
                     patch.object(production.durable, 'run_condor') as history:
                    ready, evidence = production.prior_publication_ready(config, config['batches'][1])
                self.assertEqual(ready, change in ('none', 'eta_predecessor'))
                history.assert_not_called()
                if ready:
                    self.assertTrue(evidence['terminal_closure_verified'])
                    self.assertEqual(evidence['prior_controller_cluster'], 456)

    def test_original_eta_anchor_survives_a_pinned_model_replacement_chain(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, old, _, _ = self.fixture(root)
            eta = dict(schema='shift-dp-meson-production-v1', directory=str(root/'eta'),
                production_mechanism='eta-etaprime-radiative', batches=old['batches'],
                calibration_authority=dict(sha256='eta-calibration'))
            old['prior_meson_publication'] = self.pin(root, 'eta.json', eta)
            authority = self.pin(root, 'previous.json', old)
            config['prior_meson_publication'].update(authority)
            config['eos_base'] = '/eos/synthetic'
            sample = dict(schema='shift-dark-photon-sample-v1', point='m0p3_prompt',
                original_point='m0p3_prompt', naming='mass-and-mean-lab-flight',
                selector_kind='mean_lab_flight', mass_gev=.3, epsilon=.001, sampling_epsilon=.001,
                target_mean_lab_flight_m=None, mean_lab_flight_m=None,
                calibration_mean_beta_gamma=500., mean_lab_flight_is_estimate=False,
                proper_ctau_mm=.01, reference_mean_lab_flight_m=.005,
                production_mechanism='meson-radiative', calibration_sha256='new-calibration')
            anchor = dict(sample, production_mechanism='eta-etaprime-radiative',
                          calibration_sha256='eta-calibration')
            cache = root/'cache'
            cache.mkdir()
            class Transport:
                host = 'root://eosuser.cern.ch'
                cache_directory = cache
                def metadata(self, path):
                    return dict(size=len(json.dumps(anchor).encode()), status='file')
                def command(self, argv):
                    Path(argv[-1]).write_text(json.dumps(anchor))
            with patch('export_dark_photon_cross_sections.publish_file_rpc') as publish:
                proof = production.common_sample_anchor(config, config['batches'][1], sample,
                                                       root/'sample.json', Transport())
                self.assertTrue(proof['retained_existing'])
                publish.assert_not_called()
                anchor['calibration_sha256'] = 'unowned-calibration'
                with self.assertRaisesRegex(ValueError, 'creator/calibration'):
                    production.common_sample_anchor(config, config['batches'][1], sample,
                                                    root/'sample.json', Transport())


if __name__ == '__main__':
    unittest.main()
