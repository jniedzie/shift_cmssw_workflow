# Code style: AI-code.
"""Exercise weighted source handoff, complete admission and single submission."""
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import run_shift_sm_candidate_handoff as handoff
import test_shift_sm_candidate_reco as candidate_fixture


class CandidateHandoffTest(unittest.TestCase):
    def setUp(self):
        self.fixture = candidate_fixture.CandidateRecoTest()
        self.fixture.setUp()
        self.root = self.fixture.directory
        self.record = self.fixture.record()
        request = self.root/'request.json'
        handoff.save(request, dict(source='frozen-original'))
        result_path = self.root/'result.json'
        result = handoff.read(result_path)
        result['request_sha256'] = handoff.sha(request)
        handoff.save(result_path, result)
        handoff.save(self.root/'status.json', dict(complete=True, exit_code=0))
        self.helpers = self.root/'helpers'
        self.helpers.mkdir()
        names = ('monitor_shift_sm_response_benchmark.py','shift_condor_native.py')
        for name in names:
            (self.helpers/name).write_text('# frozen helper\n')
        capsule = self.root/'tools.tar.gz'
        capsule.write_bytes(b'qualified-tools')
        bootstrap = self.root/'worker.sh'
        bootstrap.write_text('#!/bin/bash\nexit 0\n')
        self.budget = self.root/'budget.json'
        handoff.save(self.budget, dict(schema=handoff.BUDGET_SCHEMA,
            maximum_detector_jobs=10, reservations={}))
        self.config = dict(schema=handoff.SCHEMA,
            preparation_directory=str(self.root), preparation_request_sha256=handoff.sha(request),
            output_directory=str(self.root/'reconstruction'), campaign='SM_candidate_test',
            job_batch_name='SM_QCD20_candidate_test', detector_job_base=4000000,
            output_eos_directory='/eos/user/j/jniedzie/shift_cmssw_support/test_candidate',
            admission_budget_path=str(self.budget), selection_seed='fixed-test', maximum_events=1000,
            controls_per_cell=1, feature_path=self.record['feature_path'],
            helpers_directory=str(self.helpers),
            helper_sha256s={name:handoff.sha(self.helpers/name) for name in names},
            frozen=dict(capsule=str(capsule), capsule_sha256=handoff.sha(capsule),
                bootstrap=str(bootstrap), bootstrap_sha256=handoff.sha(bootstrap),
                runtime=dict(sha256='c'*64), preflight_addon=dict(path='plugin/frozen.so',sha256='d'*64)))
        self.persistent = patch.object(handoff, 'persistent_directory', side_effect=Path)
        self.persistent.start()

    def tearDown(self):
        self.persistent.stop()
        self.fixture.tearDown()

    def test_real_draw_builds_full_gen_gate_and_preserves_p_q_and_exposure(self):
        result = handoff.prepare(self.config, 'e'*64)
        self.assertEqual(len(result['detector_jobs']), 3)
        output = Path(result['working_directory'])
        dag = (output/'response.dag').read_text()
        self.assertEqual(dag.count('PARENT PREFLIGHT CHILD DET_'), 3)
        self.assertIn('CHILD COLLECT', dag)
        self.assertEqual(result['full_original_saved_events'], 7)
        self.assertEqual(handoff.read(output/'full_population_exposure.json')['sources'][0]
            ['original_full_exposure']['full_source_trials'], 1000)
        for job in result['detector_jobs']:
            request = handoff.read(Path(job['job_directory'])/'request.json')
            self.assertEqual(request['detector_source']['sampling_probabilities'], [.25]*7)
            self.assertEqual(request['benchmark_inclusion_probability'],
                job['selected']['benchmark_inclusion_probability'])
            self.assertEqual(request['detector_source']['maximum_publication_bytes'], 4*handoff.MIB)
            text = (Path(job['job_directory'])/'worker.sub').read_text()
            self.assertIn('+SendCredential = True', text)
            self.assertIn('periodic_release = False', text)
            self.assertIn('initialdir = '+job['job_directory'], text)
            self.assertIn(self.config['job_batch_name'], text)
        reservation = handoff.read(self.budget)['reservations']['supply_qcd_20toinf_test']
        self.assertEqual(reservation['detector_jobs'], 3)
        self.assertEqual(handoff.prepare(self.config, 'e'*64), result)
        with self.assertRaisesRegex(ValueError, 'another configuration'):
            handoff.prepare(self.config, 'f'*64)

    def test_budget_refuses_partial_draw_and_does_not_overbook(self):
        state = handoff.read(self.budget)
        state['maximum_detector_jobs'] = 2
        handoff.save(self.budget, state)
        with self.assertRaisesRegex(ValueError, 'budget exhausted'):
            handoff.prepare(self.config, 'e'*64)
        self.assertEqual(handoff.read(self.budget)['reservations'], {})
        self.assertFalse((Path(self.config['output_directory'])/'prepared.json').exists())

    def test_reservation_is_idempotent_and_binding_cannot_change(self):
        handoff.reserve_budget(self.budget, 'source', 'a'*64, 7)
        handoff.reserve_budget(self.budget, 'source', 'a'*64, 7)
        self.assertEqual(len(handoff.read(self.budget)['reservations']), 1)
        with self.assertRaisesRegex(ValueError, 'reservation differs'):
            handoff.reserve_budget(self.budget, 'source', 'b'*64, 7)
        with self.assertRaisesRegex(ValueError, 'budget exhausted'):
            handoff.reserve_budget(self.budget, 'second', 'b'*64, 4)

    def test_failed_or_changed_preparation_is_not_reconstructed(self):
        result = handoff.read(self.root/'result.json')
        result['complete'] = False
        handoff.save(self.root/'result.json', result)
        with self.assertRaisesRegex(ValueError, 'did not complete'):
            handoff.prepare(self.config, 'e'*64)
        self.assertEqual(handoff.read(self.budget)['reservations'], {})

    def test_returned_archive_feature_is_bound_to_published_bytes(self):
        archive = self.root/'return_payload.tar.gz'
        with tarfile.open(archive, 'w:gz') as stream:
            stream.add(self.record['feature_path'], arcname='features.json.gz')
        status = handoff.read(self.root/'status.json')
        status['return_payload_sha256'] = handoff.sha(archive)
        handoff.save(self.root/'status.json', status)
        self.config.pop('feature_path')
        with patch.object(handoff.subprocess, 'run', side_effect=AssertionError('No network needed')):
            result = handoff.prepare(self.config, 'e'*64)
        self.assertEqual(len(result['detector_jobs']), 3)
        self.assertTrue(archive.exists())

    def test_duplicate_or_unbound_feature_archive_fails(self):
        archive = self.root/'return_payload.tar.gz'
        with tarfile.open(archive, 'w:gz') as stream:
            stream.add(self.record['feature_path'], arcname='features.json.gz')
            stream.add(self.record['feature_path'], arcname='features.json.gz')
        handoff.save(self.root/'status.json', dict(complete=True, exit_code=0,
            return_payload_sha256=handoff.sha(archive)))
        self.config.pop('feature_path')
        with self.assertRaisesRegex(ValueError, 'Duplicate returned'):
            handoff.prepare(self.config, 'e'*64)

    def test_fresh_census_materializes_new_authority_without_changing_old_mask(self):
        original = (self.root/'result.json').read_bytes()
        self.config.update(fresh_census=True, first_sim_source=True, preparation_mask_unused=True)
        result = handoff.prepare(self.config, 'e'*64)
        for job in result['detector_jobs']:
            request = handoff.read(Path(job['job_directory'])/'request.json')
            self.assertEqual(request['detector_source']['sampling_probabilities'], [1.]*7)
        self.assertEqual((self.root/'result.json').read_bytes(), original)
        pin = handoff.read(Path(result['working_directory'])/'source_pins.json')['sources'][0]
        self.assertIn('/response_authority/', pin['original_result_path'])
        self.assertTrue(Path(pin['original_result_path']).is_file())

    def test_fresh_census_requires_explicit_unused_source(self):
        self.config['fresh_census'] = True
        with self.assertRaisesRegex(ValueError, 'explicitly unused'):
            handoff.prepare(self.config, 'e'*64)

    def test_retirement_keeps_eos_proof_draw_and_status_and_removes_only_feature_copy(self):
        archive = self.root/'return_payload.tar.gz'
        with tarfile.open(archive, 'w:gz') as stream:
            stream.add(self.record['feature_path'], arcname='features.json.gz')
        status = handoff.read(self.root/'status.json')
        status['return_payload_sha256'] = handoff.sha(archive)
        handoff.save(self.root/'status.json', status)
        result = handoff.read(self.root/'result.json')
        result['prepared_publications'][0]['independent_sha256_readback'] = True
        handoff.save(self.root/'result.json', result)
        self.config.pop('feature_path')
        self.config['retire_preparation_feature_archive'] = True
        (self.root/'events.log').write_text('005 (123.000.000) 10/29 10:00:00 Job terminated.\n'
            '    (1) Normal termination (return value 0)\n')
        prepared = handoff.prepare(self.config, 'e'*64)
        status_before = (self.root/'status.json').read_bytes()
        handoff.retire_returned_features(self.config, prepared)
        self.assertFalse(archive.exists())
        self.assertEqual((self.root/'status.json').read_bytes(), status_before)
        self.assertTrue(Path(prepared['benchmark_plan']['path']).exists())
        receipt = handoff.read(Path(prepared['working_directory'])/'feature_return_retirement.json')
        self.assertTrue(receipt['removed'])
        self.assertEqual(receipt['retained_feature']['sha256'], self.record['feature_sha256'])
        handoff.retire_returned_features(self.config, prepared)

    def test_retirement_refuses_other_archive_evidence_or_unverified_eos(self):
        self.config['retire_preparation_feature_archive'] = True
        prepared = handoff.prepare(self.config, 'e'*64)
        with self.assertRaisesRegex(ValueError, 'retained EOS publication'):
            handoff.retire_returned_features(self.config, prepared)

    def test_submit_once_checks_real_iwd_and_does_not_repeat_submission(self):
        sub = self.root/'manager.sub'
        sub.write_text('queue 1\n')
        ad = dict(ClusterId=123, ProcId=0, Iwd=str(self.root), JobBatchName='SM_test',
            GlobalJobId='schedd#123.0#1', JobStatus=1)
        with patch.object(handoff,'query',side_effect=[[],[ad]]), \
                patch.object(handoff,'run_condor',return_value='123.0 - 123.0\n') as run:
            result = handoff.submit_once(sub,'SM_test',self.root,'manager','a'*64)
            again = handoff.submit_once(sub,'SM_test',self.root,'manager','a'*64)
        self.assertEqual(result, again)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.kwargs['cwd'], self.root)

    def test_wrong_submitted_iwd_preserves_intent_and_blocks_retry(self):
        sub = self.root/'manager.sub'
        sub.write_text('queue 1\n')
        wrong = dict(ClusterId=123, ProcId=0, Iwd='/pool/condor/dir_1', JobBatchName='SM_test')
        with patch.object(handoff,'query',side_effect=[[],[wrong]]), \
                patch.object(handoff,'run_condor',return_value='123.0 - 123.0\n') as run:
            with self.assertRaisesRegex(ValueError, 'Iwd or batch name differs'):
                handoff.submit_once(sub,'SM_test',self.root,'manager','a'*64)
            with self.assertRaisesRegex(ValueError, 'Uncertain previous submission'):
                handoff.submit_once(sub,'SM_test',self.root,'manager','a'*64)
        self.assertEqual(run.call_count, 1)


if __name__ == '__main__':
    unittest.main()
