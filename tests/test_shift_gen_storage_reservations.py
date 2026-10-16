# Code style: AI-code. AI-generated fixed GEN storage-budget regressions.
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import run_shift_stream_controller as module


class GenBudgetTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='gen-storage-budget-', dir=SCRIPTS.parent / 'tmp')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.profile = 'shift-qcd-mu-enriched-cylinder-v1'
        self.policy = dict(suite_tag='gen', reserved_bytes_per_worker=20, reserved_files_per_worker=8)
        self.observation = dict(free_bytes=153, free_files=1000,
            external_storage_commitments={'required_free_bytes': 60},
            quota_observation_started_at_epoch=time.time() + 60)
        self.manifests = {}
        for kind in ('ordinary', 'weighted'):
            worker = 'run_mu_enriched_qcd_gen.py' if kind == 'ordinary' else 'run_shift_weighted_gen.py'
            directory = self.root / kind
            directory.mkdir()
            (directory / 'results').mkdir()
            (directory / worker).write_text('# synthetic pinned worker\n')
            stages = ('production', 'canary') if kind == 'ordinary' else ('production', 'pilot', 'closure')
            rows = [dict(id=kind + '_' + stage, stratum=kind, stage=stage, chunk_index=0,
                         trials=10, output_base='/eos/test/GEN') for stage in stages]
            if kind == 'weighted':
                rows += [dict(rows[0], id='unused_high_node', chunk_index=1)]
            manifest = dict(schema=('shift-mu-enriched-ordinary-manager-v1' if kind == 'ordinary'
                                     else 'shift-weighted-high-bin-manager-v2'),
                tag='gen', generator_profile=self.profile, inputs={worker: module.sha(directory / worker)},
                source_model_settings_sha256='a'*64, peak_bytes_per_worker=20, jobs=rows)
            if kind == 'ordinary':
                publisher = directory / 'run_shift_weighted_gen.py'
                publisher.write_text('# synthetic pinned no-overwrite publisher\n')
                manifest['inputs'][publisher.name] = module.sha(publisher)
            self.manifests[kind] = manifest
            module.save(directory / 'manifest.json', manifest)
        module.save(self.root / 'weighted/scientific_gate.json', {'pass': True, 'failures': [],
            'manifest_sha256': module.sha(self.root / 'weighted/manifest.json'),
            'generator_profile': self.profile, 'production_plan': {'weighted': {'chunks': 1, 'trials': 10}}})
        (self.root / 'production.dag').write_text('JOB source worker.sub\n')
        self.authority = dict(schema='shift-stream-gen-completion-authority-v1',
            manager_directory=str(self.root), manager_cluster=123,
            files={name: module.sha(self.root / name) for name in (
                'ordinary/manifest.json', 'weighted/manifest.json',
                'weighted/scientific_gate.json', 'production.dag')})
        for kind, manifest in self.manifests.items():
            for row in manifest['jobs']:
                if row['id'] == 'unused_high_node':
                    continue
                names = (('gen.root', 'resolved_gen_cfg.py', 'semantic_audit.json', 'cmsRun.log')
                         if kind == 'ordinary' else ('gen.root', 'resolved_gen_cfg.py',
                                                    'semantic_audit.json', 'proposal_ledger.json'))
                artifact = {name: dict(sha256='b'*64, bytes=1) for name in names}
                destination = row['output_base'] + '/' + row['id']
                record = dict(schema=('shift-mu-enriched-ordinary-gen-receipt-v1' if kind == 'ordinary'
                                      else 'shift-weighted-gen-receipt-v3'),
                    complete=True, publication_complete=True, generated_edm=True, exit_status=0,
                    request=row, manifest_sha256=self.authority['files'][kind + '/manifest.json'],
                    generator_profile=self.profile, source_inputs=manifest['inputs'], artifacts=artifact,
                    publication_directory=destination,
                    publication=[dict(name=name, path=destination + '/' + name,
                                      independent_sha256_readback=True, **values)
                                 for name, values in artifact.items()],
                    semantic_audit=dict(complete=True, healthy=True, generator_profile=self.profile,
                        source_model_settings_sha256='a'*64, input_sha256='b'*64))
                module.save(self.root / kind / 'results' / (row['id'] + '.json'), record)

    def check(self, ads=None, cache=None):
        return module.remaining_gen_budget(self.authority, self.policy, ads or [], self.observation, cache)

    def test_complete_canonical_sources_release_space_including_all_validation(self):
        result = self.check()
        self.assertTrue(result['verified'], result)
        self.assertEqual(result['planned_sources'], 5)
        self.assertEqual(result['production_sources'], 2)
        self.assertEqual(result['validation_sources'], 3)
        self.assertEqual(result['unfinished_sources'], 0)
        protected = module.reserve_other_gen_slots({'minimum_free_bytes': 50}, self.observation,
                                                  self.policy, remaining=result)
        self.assertEqual(protected['protected_gen_worker_slots'], 0)
        self.assertEqual(protected['free_bytes'], 153)

    def test_missing_validation_and_held_sources_retain_full_claims(self):
        (self.root / 'ordinary/results/ordinary_canary.json').unlink()
        held = dict(JobUniverse=5, JobStatus=5, ShiftMuEnrichedQCD=True,
                    ShiftSuiteTag='gen', ShiftMuEnrichedNodeId='weighted_production',
                    Iwd=str(self.root / 'weighted'), DAGManJobId=123)
        result = self.check([held])
        self.assertTrue(result['verified'], result)
        self.assertEqual(result['unfinished_sources'], 2)
        self.assertEqual(result['pending_by_reason'], {'missing_publication': 1, 'queued_or_held': 1})
        protected = module.reserve_other_gen_slots({'minimum_free_bytes': 50}, self.observation,
                                                  self.policy, remaining=result)
        self.assertEqual(protected['protected_gen_worker_slots'], 2)
        self.assertEqual(protected['free_bytes'], 113)

    def test_completed_after_quota_and_failed_readback_are_not_credited(self):
        path = self.root / 'weighted/results/weighted_pilot.json'
        after = self.observation['quota_observation_started_at_epoch'] + 1
        os.utime(path, (after, after))
        broken = self.root / 'ordinary/results/ordinary_production.json'
        record = module.read(broken)
        record['publication'][0]['independent_sha256_readback'] = False
        module.save(broken, record)
        result = self.check()
        self.assertTrue(result['verified'], result)
        self.assertEqual(result['unfinished_sources'], 2)
        self.assertEqual(result['pending_by_reason'], {'unverified_publication': 1, 'closed_after_quota': 1})

    def test_unknown_writers_results_and_changed_workers_keep_full_wave(self):
        for ad in (
            dict(JobUniverse=5, ShiftMuEnrichedQCD=True, ShiftSuiteTag='gen', ShiftMuEnrichedNodeId='unused_high_node'),
            dict(JobUniverse=5, ShiftMuEnrichedQCD=True, ShiftSuiteTag='other', ShiftMuEnrichedNodeId='weighted_pilot'),
            dict(JobUniverse=5, DAGManJobId=123),
            dict(JobUniverse=5, ShiftMuEnrichedQCD=True, ShiftSuiteTag='gen', ShiftMuEnrichedNodeId='weighted_pilot',
                 Iwd='/different/manager', DAGManJobId=456)):
            with self.subTest(ad=ad):
                result = self.check([ad])
                self.assertFalse(result['verified'])
                protected = module.reserve_other_gen_slots({'minimum_free_bytes': 50}, self.observation,
                                                          self.policy, remaining=result)
                self.assertEqual(protected['protected_gen_worker_slots'], 4)
        module.save(self.root / 'weighted/results/unexpected.json', {})
        self.assertFalse(self.check()['verified'])
        (self.root / 'weighted/results/unexpected.json').unlink()
        (self.root / 'weighted/run_shift_weighted_gen.py').write_text('# changed worker\n')
        self.assertFalse(self.check()['verified'])

    def test_changed_ordinary_publisher_helper_keeps_full_reserve(self):
        (self.root / 'ordinary/run_shift_weighted_gen.py').write_text('# altered publisher\n')
        result = self.check()
        self.assertFalse(result['verified'])
        self.assertIn('worker or publication bound changed', result['reason'])

    def test_receipt_cache_rechecks_changed_inode_time_and_content(self):
        path = self.root / 'ordinary/results/ordinary_production.json'
        row = self.manifests['ordinary']['jobs'][0]
        cache = {}
        with mock.patch.object(module.json, 'loads', wraps=json.loads) as parser:
            first = module.published_gen_before_quota(path, row, self.manifests['ordinary'],
                self.authority['files']['ordinary/manifest.json'], cache)
            again = module.published_gen_before_quota(path, row, self.manifests['ordinary'],
                self.authority['files']['ordinary/manifest.json'], cache)
            self.assertTrue(first['verified'])
            self.assertEqual(first, again)
            self.assertEqual(parser.call_count, 1)
        record = module.read(path)
        record['publication_complete'] = False
        module.save(path, record)
        changed = module.published_gen_before_quota(path, row, self.manifests['ordinary'],
            self.authority['files']['ordinary/manifest.json'], cache)
        self.assertFalse(changed['verified'])


if __name__ == '__main__':
    unittest.main()
