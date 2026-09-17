"""Exercise campaign reuse/preflight without a scheduler, ROOT or EOS."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


WORKFLOW = Path(__file__).resolve().parents[1]
FIT_FLAGS = (
    'SHIFT_TARGET_DETAILED_MATERIAL', 'SHIFT_TARGET_CONSISTENT_BACKWARD_COVARIANCE',
    'SHIFT_TARGET_MEAN_ENERGY_LOSS_JACOBIAN', 'SHIFT_TARGET_FIELD_GRADIENT_JACOBIAN',
    'SHIFT_TARGET_UNQUENCHED_IONIZATION_VARIANCE', 'SHIFT_TARGET_MOMENT_FIT',
)


class FourWayTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.base = self.root / 'samples'
        scripts = self.root / 'workflow/scripts'
        scripts.mkdir(parents=True)
        self.wrapper = scripts / 'run_lss_reco_fourway.sh'
        shutil.copy2(WORKFLOW / 'scripts/run_lss_reco_fourway.sh', self.wrapper)
        boundary = scripts / 'run_lss_paired_production.sh'
        boundary.write_text('''#!/usr/bin/env python3
import json, os, pathlib, sys
row = {'argv': sys.argv[1:], 'env': dict(os.environ)}
manifest = os.environ.get('CONDOR_CHUNKS_FILE')
row['chunks'] = pathlib.Path(manifest).read_text().splitlines() if manifest else None
with open(os.environ['LSS_TEST_CALLS'], 'a') as stream:
    stream.write(json.dumps(row) + '\\n')
''')
        boundary.chmod(0o755)
        self.calls_path = self.root / 'calls.jsonl'
        self.env = dict(os.environ, SAMPLE_BASE=str(self.base), N_JOBS='2',
                        N_EVENTS='10', STEP4_INPUTS_PER_JOB='1', CHUNK_START='0',
                        LSS_TEST_CALLS=str(self.calls_path))
        self.env.pop('CONDOR_CHUNKS_FILE', None)
        self.env.update({key: '0' for key in FIT_FLAGS})

    def campaign(self, name):
        return self.base / 'jpsi' / name

    def input(self, stem, version, chunk):
        campaign = self.campaign(f'lssPaired_{stem}_10k_2023_v{version}')
        path = campaign / 'samples/step3' / f'events_AOD_part{chunk:04d}.root'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'input validation is performed by the worker')
        return campaign

    def configs(self, campaign, chunk):
        for step in (1, 2, 3):
            path = campaign / f'configs/step{step}' / f'events_part{chunk:04d}_cfg.py'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f'filename = "part{chunk:04d}"\nseed = 13579\n')

    def run_wrapper(self, mode, **settings):
        return subprocess.run([str(self.wrapper), mode, '--check'],
                              env=dict(self.env, **settings), capture_output=True, text=True)

    def calls(self):
        return [json.loads(line) for line in self.calls_path.read_text().splitlines()]

    def archived_combined_output(self, version, forward):
        campaign = self.campaign(f'lssPaired_materialField_10k_2023_v{version}')
        output = campaign / 'samples/step4/events_NanoAOD_part_0000.root'
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b'worker performs ROOT validation')
        config = campaign / 'configs/step4/events_NanoAOD_part_0000_cfg.py'
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text('''
process = customise(process, targetUseDetailedMaterialPropagation=True,
    useDetailedMaterialPropagation=True, directionalRefitUseDetailedMaterialEffects=False,
    directionalRefitUseGeometryMaterialEffects=False, directionalRefitUseGeometryMaterialEffectsInFitter=False,
    directionalRefitUseGeometryMaterialEffectsInSmoother=False, directionalRefitUseGeometryTargetMaterialEffects=False,
    enableHcalDiagnostics=False, enableZDCDiagnostics=False, augmentDTHits=True,
    augmentTrackerHits=False, useExtendedTiming=False, useVertexConstrainedRefit=True)
process.shiftMuonTable.targetUseNumericalTransportCovariance = cms.bool(False)
process.shiftMuonTable.targetUseConsistentBackwardCovariance = cms.bool(True)
process.shiftMuonTable.targetUseMeanEnergyLossJacobian = cms.bool(True)
process.shiftMuonTable.targetUseUnquenchedIonizationVariance = cms.bool(True)
process.shiftMuonTable.targetUseFieldGradientJacobian = cms.bool(True)
process.shiftMuonTable.targetUseForwardRefit = cms.bool(True)
process.shiftMuonTable.targetUseMomentFit = cms.bool(True)
process.shiftMuonTable.targetForwardMaxIterations = cms.uint32(32)
process.shiftMuonTable.directionalRefitSeedMomentumScale = cms.double(1.0)
process.shiftMuonTable.directionalRefitSecondSeedErrorRescale = cms.double(100.0)
process.shiftMuonTable.directionalRefitUseSecondIteration = cms.bool(False)
process.shiftMuonTable.directionalRefitEnergyLossScale = cms.double(1.0)
process.shiftMuonTable.directionalRefitLogGeometryMaterialComparison = cms.bool(False)
process.shiftMuonTable.useMaterialAwareVertexTransport = cms.bool(False)
process.shiftMuonTable.useMaterialAwarePcaTransport = cms.bool(False)
process.shiftLssWorkflowContract = cms.PSet(materialMode=cms.string('external'), fieldMode=cms.string('ir1_atlas_proxy'))
''' + f'process.shiftMuonTable.useForwardCommonVertexFit = cms.bool({forward})\n')
        return config

    def test_control_reuses_exact_chunks_and_final_flags(self):
        primary = self.input('control', 2, 0)
        fallback = self.input('control', 1, 1)
        self.configs(primary, 0)
        self.configs(primary, 1)
        self.configs(fallback, 1)
        # Earlier failure left no v2 downstream config. The template comparison
        # is allowed only for Steps 2/3, retaining exact Step-1 seed provenance.
        for step in (2, 3):
            (primary / f'configs/step{step}/events_part0001_cfg.py').unlink()
        result = self.run_wrapper('control')
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        self.assertEqual([row['chunks'] for row in calls], [['0'], ['1']])
        for row in calls:
            self.assertIn('--check', row['argv'])
            self.assertEqual(row['argv'][-3:-1], ['--steps', '4'])
            self.assertEqual(row['env']['SHIFT_TARGET_NUMERICAL_COVARIANCE'], '0')
            for flag in FIT_FLAGS:
                self.assertEqual(row['env'][flag], '1')

    def test_control_rejects_changed_generator_before_submission(self):
        primary = self.input('control', 2, 0)
        fallback = self.input('control', 1, 1)
        self.configs(primary, 1)
        self.configs(fallback, 1)
        (fallback / 'configs/step1/events_part0001_cfg.py').write_text('seed = 99\n')
        result = self.run_wrapper('control')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Step-1 config mismatch', result.stderr)
        self.assertFalse(self.calls_path.exists())

    def test_missing_combined_input_fails_before_submission(self):
        self.input('materialField', 2, 0)
        result = self.run_wrapper('combined')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Missing required Step-3 input', result.stderr)
        self.assertFalse(self.calls_path.exists())

    def test_field_reco_reuses_v3_without_simulation_or_overwriting_v3(self):
        source = self.input('field', 3, 0)
        self.input('field', 3, 1)
        result = self.run_wrapper('field-reco')
        self.assertEqual(result.returncode, 0, result.stderr)
        row, = self.calls()
        self.assertEqual(row['argv'], ['field', 'lssPaired_field_10k_2023_v4',
                                      '--steps', '4', '--check'])
        self.assertEqual(row['chunks'], ['0', '1'])
        self.assertEqual(row['env']['STEP3_DIR'], str(source / 'samples/step3'))
        self.assertEqual(row['env']['STEP1_CONFIG_DIR'], str(source / 'configs/step1'))
        for flag in FIT_FLAGS:
            self.assertEqual(row['env'][flag], '1')

    def test_field_reco_missing_aod_fails_before_submission(self):
        self.input('field', 3, 0)
        result = self.run_wrapper('field-reco')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Missing required Step-3 input', result.stderr)
        self.assertFalse(self.calls_path.exists())

    def test_combined_reco_reuses_aod_and_enables_only_vertex_transport(self):
        source = self.input('materialField', 2, 0)
        self.input('materialField', 2, 1)
        result = self.run_wrapper('combined-reco')
        self.assertEqual(result.returncode, 0, result.stderr)
        row, = self.calls()
        self.assertEqual(row['argv'], ['combined', 'lssPaired_materialField_10k_2023_v4',
                                      '--steps', '4', '--check'])
        self.assertEqual(row['chunks'], ['0', '1'])
        self.assertEqual(row['env']['STEP3_DIR'], str(source / 'samples/step3'))
        self.assertEqual(row['env']['SHIFT_USE_MATERIAL_AWARE_VERTEX_TRANSPORT'], '1')
        self.assertEqual(row['env']['SHIFT_USE_VERTEX_CONSTRAINED_REFIT'], '1')
        self.assertEqual(row['env']['SHIFT_USE_FORWARD_COMMON_VERTEX_FIT'], '0')

    def test_forward_reco_uses_new_campaign_and_fixed_diagnostic_recipe(self):
        source = self.input('materialField', 2, 0)
        self.input('materialField', 2, 1)
        # Existing v4 artifacts must not be inspected, reused, or modified.
        previous = self.campaign('lssPaired_materialField_10k_2023_v4') / 'samples/step4/events_NanoAOD_part_0000.root'
        previous.parent.mkdir(parents=True)
        previous.write_bytes(b'preserve old v4 pilot')
        result = self.run_wrapper('combined-forward-reco',
                                  SHIFT_USE_FORWARD_COMMON_VERTEX_FIT='0',
                                  SHIFT_USE_MATERIAL_AWARE_VERTEX_TRANSPORT='1',
                                  SHIFT_USE_VERTEX_CONSTRAINED_REFIT='0')
        self.assertEqual(result.returncode, 0, result.stderr)
        row, = self.calls()
        self.assertEqual(row['argv'], ['combined', 'lssPaired_materialField_10k_2023_v5', '--steps', '4', '--check'])
        self.assertEqual(row['chunks'], ['0', '1'])
        self.assertEqual(row['env']['STEP3_DIR'], str(source / 'samples/step3'))
        self.assertEqual(row['env']['STEP1_CONFIG_DIR'], str(source / 'configs/step1'))
        self.assertEqual(row['env']['N_EVENTS'], '10')
        self.assertEqual(row['env']['STEP4_INPUTS_PER_JOB'], '1')
        self.assertEqual(row['env']['SHIFT_USE_FORWARD_COMMON_VERTEX_FIT'], '1')
        self.assertEqual(row['env']['SHIFT_USE_MATERIAL_AWARE_VERTEX_TRANSPORT'], '0')
        self.assertEqual(row['env']['SHIFT_USE_VERTEX_CONSTRAINED_REFIT'], '1')
        self.assertEqual(previous.read_bytes(), b'preserve old v4 pilot')

    def test_forward_reco_requires_every_v2_input(self):
        self.input('materialField', 2, 0)
        result = self.run_wrapper('combined-forward-reco')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Missing required Step-3 input', result.stderr)
        self.assertFalse(self.calls_path.exists())

    def test_forward_reco_accepts_only_its_archived_recipe(self):
        self.input('materialField', 2, 0)
        self.input('materialField', 2, 1)
        config = self.archived_combined_output(5, True)
        good = config.read_text()
        result = self.run_wrapper('combined-forward-reco')
        self.assertEqual(result.returncode, 0, result.stderr)
        for before, after in (
                ('useForwardCommonVertexFit = cms.bool(True)', 'useForwardCommonVertexFit = cms.bool(False)'),
                ('useMaterialAwareVertexTransport = cms.bool(False)', 'useMaterialAwareVertexTransport = cms.bool(True)'),
                ('useMaterialAwarePcaTransport = cms.bool(False)', 'useMaterialAwarePcaTransport = cms.bool(True)')):
            with self.subTest(changed=before):
                config.write_text(good.replace(before, after))
                result = self.run_wrapper('combined-forward-reco')
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('incompatible', result.stderr)
        self.assertEqual(len(self.calls()), 1)

    def test_ordinary_campaign_rejects_forward_fit_output(self):
        self.input('materialField', 2, 0)
        self.input('materialField', 2, 1)
        self.archived_combined_output(3, True)
        result = self.run_wrapper('combined')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('incompatible', result.stderr)
        self.assertFalse(self.calls_path.exists())

    def test_submission_preflight_rejects_invalid_forward_settings(self):
        environment = {key: value for key, value in os.environ.items() if not key.startswith('SHIFT_')}
        environment.update(SAMPLE_BASE=str(self.base), TRIGGER_SCENARIO='none', PILEUP_MODE='none',
                           SHIFT_USE_VERTEX_CONSTRAINED_REFIT='1', SHIFT_TARGET_DETAILED_MATERIAL='1')
        for flags, error in (
                ({'SHIFT_USE_FORWARD_COMMON_VERTEX_FIT': 'invalid'}, 'expected 0 or 1'),
                ({'SHIFT_USE_FORWARD_COMMON_VERTEX_FIT': '1', 'SHIFT_USE_VERTEX_CONSTRAINED_REFIT': '0'},
                 'requires vertex refit'),
                ({'SHIFT_USE_FORWARD_COMMON_VERTEX_FIT': '1', 'SHIFT_TARGET_DETAILED_MATERIAL': '0'},
                 'requires vertex refit')):
            with self.subTest(flags=flags):
                result = subprocess.run([str(WORKFLOW / 'run_condor.sh'), '--steps', '4', '--check'],
                                        env=dict(environment, **flags), capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(error, result.stderr)

    def test_ordinary_modes_keep_material_aware_vertex_transport_off(self):
        self.input('materialField', 2, 0)
        self.input('materialField', 2, 1)
        result = self.run_wrapper('combined')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls()[0]['env']['SHIFT_USE_MATERIAL_AWARE_VERTEX_TRANSPORT'], '0')

    def test_fullchain_modes_clear_inherited_paths(self):
        paths = ('SAMPLE_DIR', 'SAMPLES_DIR', 'CONFIG_BASE_DIR', 'WORKDIR', 'LOG_DIR',
                 'STEP1_DIR', 'STEP2_DIR', 'STEP3_DIR', 'STEP4_DIR',
                 'STEP1_CONFIG_DIR', 'STEP2_CONFIG_DIR', 'STEP3_CONFIG_DIR', 'STEP4_CONFIG_DIR')
        for mode in ('material', 'field'):
            result = self.run_wrapper(mode, **{key: '/incorrect/campaign' for key in paths})
            self.assertEqual(result.returncode, 0, result.stderr)
            row = self.calls()[-1]
            self.assertEqual(row['argv'][-3:], ['--steps', '1,2,3,4', '--check'])
            self.assertTrue(all(key not in row['env'] for key in paths))

    def test_old_study_settings_cannot_change_the_fixed_recipe(self):
        old_study = {
            'SHIFT_REFIT_SEED_MOMENTUM_SCALE': '2.5',
            'SHIFT_REFIT_ENERGY_LOSS_SCALE': '0.25',
            'SHIFT_REFIT_SECOND_SEED_ERROR_RESCALE': '1.0',
            'SHIFT_REFIT_USE_SECOND_ITERATION': '1',
            'SHIFT_REFIT_DETAILED_MATERIAL_EFFECTS': '1',
            'SHIFT_REFIT_GEOMETRY_MATERIAL_EFFECTS': '1',
            'SHIFT_REFIT_GEOMETRY_MATERIAL_FITTER': '1',
            'SHIFT_REFIT_GEOMETRY_MATERIAL_SMOOTHER': '1',
            'SHIFT_REFIT_GEOMETRY_TARGET_MATERIAL': '1',
            'SHIFT_REFIT_LOG_GEOMETRY_COMPARISON': '1',
            'SHIFT_SIMHIT_REFERENCE_BX_OFFSET': '50',
            'SHIFT_SIMHIT_REFERENCE_INPUT': '/wrong/transport.root',
            'SHIFT_TIMING_BEAM_DIRECTION_Z': '1',
            'SHIFT_LSS_GDML_FILE': 'wrong/geometry.gdml',
            'SHIFT_FUTURE_EXPERIMENT': 'unrecognized study',
            'GEOMETRY': 'wrong geometry', 'ERA': 'wrong era',
            'CONDITIONS': 'wrong conditions', 'BEAMSPOT': 'wrong beamspot',
            'HLT_MENU': 'wrong menu',
        }
        forced = {
            'CMSSW_USE_BIGLIB': '0', 'ENABLE_EXONANOAOD': '0',
            'DEBUG_MUON_PRIMARIES': '0', 'DEBUG_MUON_HITS': '0',
            'DEBUG_MUON_TRACKING': '0', 'TRACE_PRIMARY_MUON_PATHS': '0',
            'N_THREADS': '1', 'N_STREAMS': '0',
        }
        overrides = dict(old_study, **{key: '17' for key in forced})
        result = self.run_wrapper('field', **overrides)
        self.assertEqual(result.returncode, 0, result.stderr)
        environment = self.calls()[-1]['env']
        self.assertTrue(all(key not in environment for key in old_study))
        for key, expected in forced.items():
            self.assertEqual(environment[key], expected)
        for flag in FIT_FLAGS:
            self.assertEqual(environment[flag], '1')

    def test_existing_incompatible_output_and_missing_prefix_fail(self):
        destination = self.campaign('lssPaired_control_10k_2023_v3')
        path = destination / 'samples/step4/events_NanoAOD_part_0000.root'
        path.parent.mkdir(parents=True)
        path.write_bytes(b'old result')
        config = destination / 'configs/step4/events_NanoAOD_part_0000_cfg.py'
        config.parent.mkdir(parents=True)
        config.write_text('process.shiftMuonTable.targetUseMomentFit = cms.bool(False)\n')
        result = self.run_wrapper('control')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('incompatible', result.stderr)
        path.unlink()
        result = self.run_wrapper('control', CHUNK_START='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Cannot skip missing prefix', result.stderr)


if __name__ == '__main__':
    unittest.main()
