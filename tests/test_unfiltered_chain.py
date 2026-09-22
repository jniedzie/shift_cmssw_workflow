import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from unfiltered_qcd_contract import check, PROCESS, JPSI_PROCESS
from run_retiring_chain import canonical_paths, retire, sha
from collect_generation_metadata import combine
import run_retiring_chain as chain


class UnfilteredContractTest(unittest.TestCase):
    def env(self):
        return dict(PROCESS=PROCESS, GEN_PTHAT_MIN='1', GEN_PTHAT_MAX='2',
                    CLEANUP_PREVIOUS_STEP='1', WORKFLOW_LOCAL_GENERATOR='1',
                    PILEUP_MODE='none', TRIGGER_SCENARIO='none', TRIGGER_TIMELINE_MODE='none',
                    STEP4_INPUTS_PER_JOB='1', ENABLE_EXONANOAOD='0')

    def test_supported_bins_and_no_cuts(self):
        for lo, hi in [('1', '2'), ('2', '5'), ('5', '10'), ('10', '20'), ('20', '-1')]:
            self.assertTrue(check(dict(self.env(), GEN_PTHAT_MIN=lo, GEN_PTHAT_MAX=hi)))
        text = (ROOT / 'fragments' / (PROCESS + '_pythia8_cff.py')).read_text()
        self.assertEqual(text.count('cms.EDFilter('), 1)
        self.assertIn('ProductionFilterSequence = cms.Sequence(generator)', text)
        self.assertNotIn('mugenfilter', text)
        self.assertNotIn('MinEta', text)
        self.assertIn("'ParticleDecays:limitTau0 = off'", text)
        self.assertIn("'Charmonium:all = off'", text)

    def test_reject_unvalidated_bins_or_filtered_inputs(self):
        for key, value in [('GEN_PTHAT_MIN', '0'), ('GEN_PTHAT_MAX', 'nan'),
                           ('PROCESS', 'QCD_MuEnriched_FixedTarget_pThat_1to5GeV_13p6TeV'),
                           ('STEP4_INPUTS_PER_JOB', '2'), ('PILEUP_MODE', 'standard'),
                           ('TRIGGER_SCENARIO', 'piggyback_central'), ('CLEANUP_PREVIOUS_STEP', 'yes')]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                check(dict(self.env(), **{key: value}))

    def test_legacy_default_unaffected(self):
        self.assertTrue(check({}))

    def test_jpsi_unfiltered_preserves_pilot_lifetime_policy(self):
        self.assertTrue(check(dict(self.env(), PROCESS=JPSI_PROCESS)))
        text = (ROOT / 'fragments' / (JPSI_PROCESS + '_pythia8_cff.py')).read_text()
        self.assertEqual(text.count('cms.EDFilter('), 1)
        self.assertIn('ProductionFilterSequence = cms.Sequence(generator)', text)
        for obsolete in ('limitTau0 = off', 'minWidth', '13:mayDecay = off', 'mugenfilter'):
            self.assertNotIn(obsolete, text)
        self.assertIn('443:onIfMatch = 13 -13', text)

    def test_cross_sections_not_mixed_between_bins(self):
        records = [dict(schema='shift-production-gen-v1', chunk=i, events=10,
                        process=PROCESS, fragment_sha256='same', configured_pthat_bounds=[1., 2.],
                        generated_filter_efficiency=1., forced_decay='none', sum_weights=10.,
                        sum_weights_squared=10., runs=[dict(internal_xsec_pb=100., error_pb=2.)])
                   for i in range(2)]
        self.assertEqual(combine(records, 2)['filter_efficiency'], 1.)
        records[1]['configured_pthat_bounds'] = [2., 5.]
        with self.assertRaisesRegex(ValueError, 'Mixed generator pThat'):
            combine(records, 2)


class RetirementTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.campaign = Path(self.tmp.name) / 'campaign'
        self.env = {f'STEP{s}_DIR': str(self.campaign / 'samples' / f'step{s}') for s in range(1, 5)}
        for path in self.env.values():
            Path(path).mkdir(parents=True)
        self.paths = canonical_paths(self.campaign, 7, self.env)
        self.previous, self.successor = self.paths[1], self.paths[2]
        self.previous.write_text('source payload')
        self.successor.write_text('successor payload')
        self.record = dict(previous_stage=1, previous_sha256=sha(self.previous),
                           successor_sha256=sha(self.successor))

    def test_exact_predecessor_only_and_repeat_safe(self):
        unrelated = self.previous.parent / 'events_step1_part0008.root'
        unrelated.write_text('other chunk')
        retire(self.previous, self.successor, self.record, self.campaign)
        self.assertFalse(self.previous.exists())
        self.assertTrue(self.successor.exists())
        self.assertTrue(unrelated.exists())
        self.assertEqual(json.loads((self.campaign / 'retired_step1.json').read_text())['status'], 'deleted')
        retire(self.previous, self.successor, self.record, self.campaign)

    def test_changed_successor_never_deletes_input(self):
        self.successor.write_text('corrupt')
        with self.assertRaises(ValueError):
            retire(self.previous, self.successor, self.record, self.campaign)
        self.assertTrue(self.previous.exists())

    def test_missing_successor_never_deletes_input(self):
        self.successor.unlink()
        with self.assertRaises(ValueError):
            retire(self.previous, self.successor, self.record, self.campaign)
        self.assertTrue(self.previous.exists())

    def test_no_symlink_or_external_stage(self):
        with self.assertRaises(ValueError):
            canonical_paths(self.campaign, 7, dict(self.env, STEP1_DIR='/tmp'))
        self.previous.unlink()
        self.previous.symlink_to(self.successor)
        with self.assertRaises(ValueError):
            canonical_paths(self.campaign, 7, self.env)


class ChainRestartTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.campaign = Path(temporary.name) / 'campaign'
        self.env = UnfilteredContractTest().env()
        self.env.update(WORKFLOW_ROOT=str(ROOT), SAMPLE_DIR=str(self.campaign))
        for stage in range(1, 5):
            path = self.campaign / 'samples' / f'step{stage}'
            path.mkdir(parents=True)
            self.env[f'STEP{stage}_DIR'] = str(path)
        self.ids = [[1, 1, i] for i in range(1, 4)]
        self.executed = []
        self.fail_stage = None

    def fake_run(self, command, **kwargs):
        stage = next(s for s, name in chain.SCRIPTS.items() if Path(command[-3]).name == name)
        self.executed.append(stage)
        if stage == self.fail_stage:
            raise RuntimeError('Simulated cmsRun failure')
        output = Path(self.env[f'STEP{stage}_DIR']) / chain.NAMES[stage].format(part='0000')
        output.write_text(f'valid stage {stage}')
        config_dir = self.campaign / 'configs' / f'step{stage}'
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / 'events_part0000_cfg.py').write_text('retained config')
        logs = self.campaign / 'logs'
        logs.mkdir(exist_ok=True)
        (logs / f'step{stage}_events_part0000.log').write_text('retained log')
        if stage == 1:
            metadata = self.campaign / 'generation_metadata'
            metadata.mkdir(exist_ok=True)
            (metadata / 'part0000.json').write_text(json.dumps(dict(event_ids=self.ids,
                events=3, attempted_events=3, generated_filter_efficiency=1.)))

    def fake_inspect(self, path, stage):
        if path.read_text() != f'valid stage {stage}':
            raise ValueError('corrupt payload')
        return dict(path=str(path), sha256=sha(path), bytes=path.stat().st_size,
                    event_ids=self.ids, events=3, reconstructed_mass_read=False)

    def run_chain(self):
        with patch.dict(os.environ, self.env, clear=True), \
                patch.object(sys, 'argv', ['chain', '--chunk', '0', '--events', '3']), \
                patch.object(chain, 'inspect', side_effect=self.fake_inspect), \
                patch.object(chain, 'run_command', side_effect=self.fake_run):
            chain.main()

    def test_success_and_restart_do_not_regenerate_retired_inputs(self):
        self.run_chain()
        self.assertEqual(self.executed, [1, 2, 3, 4])
        self.executed.clear()
        self.run_chain()
        self.assertEqual(self.executed, [])
        self.assertEqual(len(list((self.campaign / 'samples').glob('*/*.root'))), 1)
        self.assertEqual(len(list((self.campaign / 'logs').glob('*.log'))), 4)

    def test_failed_stage_keeps_latest_checkpoint_and_resumes(self):
        self.fail_stage = 3
        with self.assertRaisesRegex(RuntimeError, 'cmsRun failure'):
            self.run_chain()
        self.assertTrue((Path(self.env['STEP2_DIR']) / 'events_step2_part0000.root').exists())
        self.assertFalse((Path(self.env['STEP1_DIR']) / 'events_step1_part0000.root').exists())
        self.fail_stage = None
        self.executed.clear()
        self.run_chain()
        self.assertEqual(self.executed, [3, 4])

    def test_corrupt_latest_checkpoint_stops_without_regeneration(self):
        self.run_chain()
        output = Path(self.env['STEP4_DIR']) / 'events_NanoAOD_part_0000.root'
        output.write_text('corrupt')
        self.executed.clear()
        with self.assertRaisesRegex(ValueError, 'corrupt'):
            self.run_chain()
        self.assertEqual(self.executed, [])

    def test_missing_provenance_stops_resume(self):
        self.run_chain()
        (self.campaign / 'configs/step1/events_part0000_cfg.py').unlink()
        self.executed.clear()
        with self.assertRaisesRegex(ValueError, 'provenance changed or is missing'):
            self.run_chain()
        self.assertEqual(self.executed, [])


if __name__ == '__main__':
    unittest.main()
