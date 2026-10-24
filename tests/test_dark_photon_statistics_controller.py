# Code style: AI-code. AI-generated tests for durable adaptive production transitions.
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import run_dark_photon_statistics as controller


class AdditionalStatistics(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)
        self.baseline=self.root/'baseline.json'
        controller.new(self.baseline,dict(points={'m2_prompt':dict(point='m2_prompt',plan_path='/baseline/plan.json')}))
        self.extra=self.root/'recovery';self.extra.mkdir();(self.extra/'completed_points').mkdir()
        controller.new(self.extra/'plan.json',dict(points=[dict(point='m2_prompt')]))
        controller.new(self.extra/'seal.json',dict(files={str(self.extra/'plan.json'):controller.sha(self.extra/'plan.json')}))
        self.record=dict(directory=str(self.extra),plan_sha256=controller.sha(self.extra/'plan.json'),seal_sha256=controller.sha(self.extra/'seal.json'))
        self.config=dict(baseline_sources=str(self.baseline),conditions=dict(sha256='a'*64),
            previous_statistics=str(self.root/'original'),directory=str(self.root),dependencies={
                str(self.extra/name):controller.sha(self.extra/name) for name in ('plan.json','seal.json')})

    def test_default_and_unfinished_recovery_do_not_change_sources(self):
        self.assertEqual(len(controller.available_sources(self.config)['m2_prompt']),1)
        self.config['additional_previous_statistics']=[self.record]
        self.assertEqual(len(controller.available_sources(self.config)['m2_prompt']),1)
        controller.new(self.extra/'completed_points/m2_prompt.json',dict(complete=True))
        values=controller.available_sources(self.config)['m2_prompt']
        self.assertEqual(len(values),2)
        self.assertEqual(values[1]['plan_sha256'],self.record['plan_sha256'])

    def test_unpinned_changed_relative_and_duplicate_extra_sources_fail(self):
        for changes in [dict(directory='relative'),dict(plan_sha256='b'*64)]:
            self.config['additional_previous_statistics']=[dict(self.record,**changes)]
            with self.subTest(changes=changes),self.assertRaises(ValueError):controller.available_sources(self.config)
        self.config['additional_previous_statistics']=[self.record,self.record]
        with self.assertRaises(ValueError):controller.available_sources(self.config)
        self.config['additional_previous_statistics']=[self.record]
        del self.config['dependencies'][str(self.extra/'seal.json')]
        with self.assertRaises(ValueError):controller.available_sources(self.config)

    def test_pinned_recovery_triggers_publication_without_waiting_for_a_wave(self):
        worklist=self.root/'worklist.json';controller.new(worklist,dict(waves=[dict(batches=[])]))
        config=dict(self.config,worklist=str(worklist),aggregate_workers=0,
                    additional_previous_statistics=[self.record])
        sources={'m01_old':[dict(plan_path='/baseline'),dict(plan_path='/old_statistics')],
                 'm2_prompt':[dict(plan_path='/baseline'),dict(plan_path=str(self.extra/'plan.json'))]}
        with patch('watch_dark_photon_production.EosRpc'),patch.object(controller,'available_sources',return_value=sources), \
             patch.object(controller,'jobs',return_value=[]),patch.object(controller,'reconcile_aggregate',return_value=None) as reconcile:
            controller.publish(config,{})
        self.assertEqual(reconcile.call_count,1)
        self.assertEqual(reconcile.call_args.args[2],'m2_prompt')

    def test_step_publishes_ready_recovery_before_wave_zero_plan_exists(self):
        worklist=self.root/'worklist.json'
        controller.new(worklist,dict(waves=[dict(batches=[dict(point='m2_prompt')])]))
        config=dict(self.config,worklist=str(worklist),aggregate_workers=0,
                    additional_previous_statistics=[self.record],simultaneous_waves=1,
                    minimum_free_bytes=100,minimum_free_files=10)
        controller.new(self.extra/'completed_points/m2_prompt.json',dict(complete=True))
        self.assertFalse((controller.wave_directory(config,0)/'production/plan.json').exists())
        with patch('watch_dark_photon_production.EosRpc'), \
             patch.object(controller,'quota',return_value=dict(free_bytes=0,free_files=0)), \
             patch.object(controller,'jobs',return_value=[]), \
             patch.object(controller,'reconcile_aggregate',return_value=None) as reconcile, \
             patch.object(controller,'prepare_gen') as submit:
            state=controller.step(config,self.root/'config.json',{})
        reconcile.assert_called_once()
        self.assertEqual(reconcile.call_args.args[2],'m2_prompt')
        self.assertEqual(state['unpublished_points'],['m2_prompt'])
        self.assertFalse(state['canary_published'])
        self.assertEqual(state['admission'],'waiting_for_storage')
        submit.assert_not_called()

    def test_only_pinned_exact_terminal_failed_attempt_can_be_skipped(self):
        directory=self.root/'v0001';directory.mkdir()
        controller.new(directory/'submission.json',dict(cluster=123))
        record=dict(directory=str(directory),plan_sha256='a'*64)
        proof=dict(schema='shift-reviewed-aggregate-retirement-v1',directory=str(directory),point='m2_prompt',
            aggregate_record=record,payload_started=False,queue_absent=True,terminal_native_ad=dict(
                ClusterId=123,ProcId=0,JobStatus=4,ExitCode=54,ExitBySignal=False,Iwd=str(directory)))
        path=self.root/'retired.json';controller.new(path,proof)
        config=dict(dependencies={str(path):controller.sha(path)},retired_aggregate_attempts=[dict(path=str(path),sha256=controller.sha(path))])
        with patch.object(controller,'jobs',return_value=[]):
            self.assertTrue(controller.retired_aggregate_attempt(config,directory,'m2_prompt',record))
            with self.assertRaises(ValueError):controller.retired_aggregate_attempt(config,directory,'m5_prompt',record)
        with patch.object(controller,'jobs',return_value=[dict(JobStatus=5)]):
            with self.assertRaises(RuntimeError):controller.retired_aggregate_attempt(config,directory,'m2_prompt',record)
        path.write_text('{}')
        with self.assertRaises(ValueError):controller.retired_aggregate_attempt(config,directory,'m2_prompt',record)

    def test_recovery_rate_gate_waits_for_complete_readbacks_and_exact_plan(self):
        path=self.root/'rates_ready.json'
        source=dict(self.record,readiness_receipt=str(path),expected_ready_points=['m2_prompt'])
        self.assertFalse(controller.additional_statistics_ready(source))
        controller.new(path,dict(complete=False,published=False))
        self.assertFalse(controller.additional_statistics_ready(source))
        artifact=dict(path='/eos/rate.json',bytes=123,sha256='b'*64)
        proof=dict(schema='shift-dark-photon-rate-retention-v1',complete=True,published=True,
            plan_sha256=self.record['plan_sha256'],points=[dict(point='m2_prompt',published=True,
            artifacts=[artifact],publication_readbacks=[dict(artifact,readback_sha256='b'*64)],
            receipt_publication=dict(sha256='c'*64,readback_sha256='c'*64))])
        path.write_text(json.dumps(proof));self.assertTrue(controller.additional_statistics_ready(source))
        proof['plan_sha256']='d'*64;path.write_text(json.dumps(proof))
        with self.assertRaises(ValueError):controller.additional_statistics_ready(source)
        proof['plan_sha256']=self.record['plan_sha256'];proof['points'][0]['publication_readbacks'][0]['readback_sha256']='d'*64
        path.write_text(json.dumps(proof))
        with self.assertRaises(ValueError):controller.additional_statistics_ready(source)


class SupplementAdmission(unittest.TestCase):
    def setUp(self):
        import time
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name);self.observed=time.time()+2
        self.worklist=self.root/'worklist.json'
        controller.new(self.worklist,dict(total_additional_events=10,waves=[dict(wave_index=0,
            batches=[dict(point='m2_prompt',events=10,seed=123,run_number=456)])]))
        self.authority=dict(name='main',kind='statistics',directory=str(self.root),
            worklist=dict(path=str(self.worklist),sha256=controller.sha(self.worklist)),bytes_per_event=326000)
        controller.new(self.root/'state.json',dict(checked_utc=controller.datetime.now(controller.timezone.utc).isoformat(),
            published={'m2_prompt':dict(new_events=999999,source_plans=['/unrelated/plan.json'],
                published_utc=controller.datetime.now(controller.timezone.utc).isoformat())}))

    def test_old_aggregate_counts_and_unhealthy_peers_do_not_release_new_events(self):
        claim=controller.supplement_campaign_claim(self.authority,self.observed,1200)
        self.assertEqual(claim['remaining_bytes'],3260000)
        state=controller.read(self.root/'state.json');state['error']='stopped';controller.save(self.root/'state.json',state)
        self.assertFalse(controller.supplement_campaign_claim(self.authority,self.observed,1200)['peer_healthy'])
        (self.root/'state.json').unlink()
        self.assertEqual(controller.supplement_campaign_claim(self.authority,self.observed,1200)['remaining_bytes'],3260000)

    def test_only_sealed_published_originals_before_quota_receive_credit(self):
        production=self.root/'waves/w000/production';production.mkdir(parents=True);(production/'completed_points').mkdir()
        controller.new(production/'plan.json',dict(points=[dict(point='m2_prompt',events=10,seed=123,run_number=456)]))
        controller.new(production/'seal.json',dict(files={str(production/'plan.json'):controller.sha(production/'plan.json')}))
        controller.new(production/'completed_points/m2_prompt.json',dict(complete=True,point='m2_prompt',events=10,
            plan_sha256=controller.sha(production/'plan.json'),seal_sha256=controller.sha(production/'seal.json')))
        state=controller.read(self.root/'state.json');state['published']['m2_prompt']['source_plans']=[str(production/'plan.json')];controller.save(self.root/'state.json',state)
        claim=controller.supplement_campaign_claim(self.authority,self.observed,1200)
        self.assertEqual((claim['remaining_bytes'],claim['credited_events']),(0,10))
        self.assertFalse(controller.closed_primitive(production,'m2_prompt',10,0))
        report=production/'completed_points/m2_prompt.json';data=controller.read(report);data['events']=9;controller.save(report,data)
        with self.assertRaises(ValueError):controller.supplement_campaign_claim(self.authority,self.observed,1200)

    def test_shared_full_forecasts_and_floor_gate_without_double_retirement_credit(self):
        policy=self.root/'policy.json';controller.new(policy,dict(schema='shift-dp-supplement-admission-v1',
            minimum_free_bytes=90000000000,minimum_free_files=100,campaigns=[self.authority]))
        authority=dict(path=str(policy),sha256=controller.sha(policy));config=dict(directory=str(self.root),
            dependencies={str(policy):authority['sha256']},supplement_admission=authority)
        observation=dict(observed_utc=controller.datetime.now(controller.timezone.utc).isoformat(),
                         free_bytes=90003260000,free_files=100)
        with patch.object(controller,'supplement_canaries',return_value=True):
            allowed,evidence=controller.supplement_admission(config,observation)
            self.assertTrue(allowed);self.assertEqual(evidence['retirement_credit_bytes'],0)
            allowed,_=controller.supplement_admission(config,dict(observation,free_bytes=90000000000))
            self.assertFalse(allowed)
        with patch.object(controller,'supplement_canaries',return_value=False):
            self.assertEqual(controller.supplement_admission(config,observation)[1]['reason'],
                             'waiting_for_complete_detector_canary_histogram_readbacks')


class FreshMesonQualificationStorage(unittest.TestCase):
    def test_seven_source_checks_release_no_histogram_reservation_credit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);meson=root/'meson';meson.mkdir()
            batches=[dict(batch='gen_qualification_%02d'%index,point=mass+'_prompt',
                gen_qualification_only=True) for index,mass in enumerate(
                ('m0p22','m0p25','m0p3','m0p4','m0p5','m0p7','m0p9'))]
            controller.new(meson/'config.json',dict(directory=str(meson),batches=batches))
            controller.new(meson/'state.json',dict(batches={row['batch']:dict(published=dict(
                batch=row['batch'],point=row['point'],complete=True,source_qualification_only=True)) for row in batches}))
            controller.new(root/'worklist.json',dict(total_additional_events=60,waves=[]))
            config=dict(directory=str(root),worklist=str(root/'worklist.json'),fresh_own_fixed_bytes=1000,
                minimum_free_bytes=50000,fresh_meson_peer=dict(configuration=dict(path=str(meson/'config.json'),
                sha256=controller.sha(meson/'config.json')),reservation_bytes=65000000,
                forecast_bytes={row['batch']:10000 for row in batches}))
            observation=dict(observed_utc='2026-10-23T19:42:00+00:00',free_bytes=100000000)
            with patch.object(controller,'jobs',return_value=[]):
                allowed,evidence=controller.fresh_storage_admission(config,observation)
            self.assertTrue(allowed)
            self.assertEqual(evidence['meson_closed_bytes'],0)
            self.assertEqual(evidence['meson_future_bytes'],65000000)
            self.assertEqual(evidence['required_free_bytes'],50000+1000+60*326000+65000000)
            reservation=root/'regrouping.json'
            controller.new(reservation,dict(schema='shift-dp-shared-aggregation-reservation-v1',complete=True,
                science_directory=str(meson),science_config_sha256=controller.sha(meson/'config.json'),
                additional_reserved_bytes=22000000,source_envelope_bytes=65000000,direct_DY_envelope_bytes=30000000,
                combined_DP_reservation_bytes=117000000,registered_controller_directories=[str(root)]))
            pin=dict(path=str(reservation),sha256=controller.sha(reservation))
            config['fresh_shared_aggregation_reservation']=pin;config['dependencies']={pin['path']:pin['sha256']}
            with patch.object(controller,'jobs',return_value=[]):
                allowed,protected=controller.fresh_storage_admission(config,observation)
            self.assertFalse(allowed)
            self.assertEqual(protected['shared_aggregation_reserved_bytes'],22000000)
            self.assertEqual(protected['required_free_bytes'],evidence['required_free_bytes']+22000000)
            reservation.write_text('{}')
            with patch.object(controller,'jobs',return_value=[]),self.assertRaises(ValueError):
                controller.fresh_storage_admission(config,observation)
            ordinary=dict(batch='regular',point='m0p3_prompt')
            self.assertFalse(controller.meson_published_batch(meson,ordinary,dict(
                batch='regular',source_qualification_only=True),0))
            with self.assertRaises(KeyError):
                controller.meson_published_batch(meson,ordinary,dict(batch='regular'),0)


class SupplementReadbacks(unittest.TestCase):
    def test_required_cohort_qualifies_itself_after_last_point_finishes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);cohort=root/'canary';cohort.mkdir();(cohort/'completed_points').mkdir();(cohort/'statuses').mkdir()
            controller.new(cohort/'plan.json',dict(campaign='canary',points=[dict(point='m2_prompt',events=20,final_output_base='/eos/canary')]))
            controller.new(cohort/'seal.json',dict(files={}))
            policy=dict(required_cohorts=[dict(directory=str(cohort),plan_sha256=controller.sha(cohort/'plan.json'),seal_sha256=controller.sha(cohort/'seal.json'))])
            config=dict(directory=str(root));self.assertFalse(controller.supplement_canaries(config,policy,'a'*64))
            failed=cohort/'statuses/failed.json';controller.new(failed,dict(complete=False,error='worker failed'))
            with self.assertRaises(RuntimeError):controller.supplement_canaries(config,policy,'a'*64)
            failed.unlink();controller.new(cohort/'completed_points/m2_prompt.json',dict(complete=True))
            with patch('watch_dark_photon_production.EosRpc'),patch('watch_dark_photon_production.completed_histogram',return_value=dict(events=20)) as verify:
                self.assertTrue(controller.supplement_canaries(config,policy,'a'*64));verify.assert_called_once()
                self.assertTrue(controller.supplement_canaries(config,policy,'a'*64));self.assertEqual(verify.call_count,1)
            self.assertTrue((root/'supplement_canaries_verified.json').is_file())

    def test_sample_metadata_requires_exact_published_point_readbacks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);path=root/'policy.json';metadata=root/'sample_cards.json'
            policy=dict(schema='shift-dp-supplement-admission-v1',required_sample_metadata=dict(path=str(metadata),expected_points=['m2_prompt']))
            controller.new(path,policy);authority=dict(path=str(path),sha256=controller.sha(path));config=dict(dependencies={str(path):authority['sha256']},supplement_admission=authority)
            self.assertEqual(controller.supplement_admission(config,{})[1]['reason'],'waiting_for_canonical_sample_parameters')
            controller.new(metadata,dict(schema='shift-dark-photon-sample-metadata-publication-v1',complete=True,points=[dict(point='m2_prompt',bytes=100,sha256='a'*64,readback_sha256='b'*64)]))
            with self.assertRaises(ValueError):controller.supplement_admission(config,{})


class ReadyBaselinePublication(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        source = self.root / 'pilot'
        source.mkdir()
        self.plan = source / 'plan.json'
        self.point = 'm2_lab_1em09m'
        controller.new(self.plan, dict(campaign='pilot-v1', points=[dict(point=self.point,
            events=20, final_output_base='/eos/darkphoton_' + self.point + '/pilot-v1')]))
        controller.new(source / 'seal.json', dict(files={str(self.plan): controller.sha(self.plan)}))
        self.authority = dict(point=self.point, plan_path=str(self.plan), plan_sha256=controller.sha(self.plan),
            seal_sha256=controller.sha(source / 'seal.json'), conditions_sha256='c' * 64)
        baseline = self.root / 'baselines.json'
        controller.new(baseline, dict(points={self.point: self.authority}))
        metadata = self.root / 'cards.json'
        controller.new(metadata, dict(schema='shift-dark-photon-sample-metadata-publication-v1',
            complete=True, points=[dict(point=self.point, bytes=100, sha256='a' * 64, readback_sha256='a' * 64)]))
        owner = self.root / 'owner.json'
        controller.new(owner, dict(owner=str(self.root), exclusive_supplement_namespace=True, points=[self.point]))
        self.config = dict(directory=str(self.root), publish_ready_baselines=True,
            baseline_sources=str(baseline), baseline_sample_metadata=dict(path=str(metadata), sha256=controller.sha(metadata)),
            publication_owner_receipt=str(owner), conditions=dict(sha256='c' * 64), eos_base='/eos',
            dependencies={str(path): controller.sha(path) for path in (self.plan, source / 'seal.json', metadata, owner)})
        report = source / 'completed_points' / (self.point + '.json')
        report.parent.mkdir()
        controller.new(report, dict(complete=True, score_histograms=dict(marker=dict(published=dict(histograms=dict(sha256='b' * 64))))))
        self.ready = dict(campaign='pilot-v1', events=20, point_receipt_path=str(report),
            point_receipt_sha256=controller.sha(report), histogram_sha256='b' * 64)
        self.transport = Mock()
        self.transport.alias_target.return_value = None

    def run_publication(self, state, ready=None):
        with patch('watch_dark_photon_production.EosRpc', return_value=self.transport), \
                patch('watch_dark_photon_production.completed_histogram', return_value=ready) as verify:
            controller.publish_ready_baselines(self.config, state)
        return verify

    def test_completed_pilot_publishes_before_any_wave_without_becoming_an_aggregate(self):
        state = dict(waves={})
        self.run_publication(state, self.ready.copy())
        self.assertEqual(state['baseline_publications'][self.point]['source'], self.authority)
        self.assertNotIn('published', state)
        self.transport.publish_alias.assert_called_once_with('/eos/darkphoton_' + self.point + '/latest',
            'pilot-v1', [None, 'pilot-v1'])
        self.transport.alias_target.return_value = 'pilot-v1'
        verify = self.run_publication(state, self.ready.copy())
        verify.assert_not_called()
        self.assertEqual(len(state['baseline_publications']), 1)
        self.transport.publish_alias.assert_called_once()

    def test_unfinished_pilot_waits_and_existing_aggregate_is_never_downgraded(self):
        state = {}
        self.run_publication(state)
        self.transport.publish_alias.assert_not_called()
        state['published'] = {self.point: dict(campaign='aggregate-v1')}
        verify = self.run_publication(state, self.ready.copy())
        verify.assert_not_called()
        self.transport.publish_alias.assert_not_called()

    def test_different_remote_campaign_and_changed_source_fail_before_alias_update(self):
        self.transport.alias_target.return_value = 'unrecorded-aggregate'
        with self.assertRaisesRegex(ValueError, 'different campaign'):
            self.run_publication({}, self.ready.copy())
        self.transport.publish_alias.assert_not_called()
        self.plan.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'authority changed'):
            self.run_publication({}, self.ready.copy())

    def test_metadata_and_exclusive_owner_are_required_before_readback(self):
        self.config['baseline_sample_metadata']['sha256'] = 'd' * 64
        with self.assertRaisesRegex(ValueError, 'outside frozen'):
            self.run_publication({}, self.ready.copy())
        self.transport.publish_alias.assert_not_called()

    def test_option_is_disabled_by_default(self):
        del self.config['publish_ready_baselines']
        state = {}
        verify = self.run_publication(state, self.ready.copy())
        verify.assert_not_called()
        self.assertEqual(state, {})


class GlobalCapacity(unittest.TestCase):
    def test_weekend_ceiling_is_bounded_by_the_actual_publication_allowance(self):
        config=dict(simultaneous_waves=8,detector_workers_per_wave=300,
            fixed_publication_breakdown=dict(global_2400_inflight_detector_bound_bytes=24000000000,
                group_and_GEN=20000000000,floor=20000000000,qcd=6144000000,failure=2000000000),
            fixed_publication_bytes=72144000000)
        controller.validate_capacity(config)
        self.assertLess(8*300*9900801,24000000000)
        for changes in (dict(simultaneous_waves=9),dict(detector_workers_per_wave=301)):
            with self.assertRaises(ValueError):controller.validate_capacity(dict(config,**changes))
        config['fixed_publication_breakdown']['global_2400_inflight_detector_bound_bytes']=23000000000
        with self.assertRaises(ValueError):controller.validate_capacity(config)


class ReviewedGenHandoff(unittest.TestCase):
    def test_exact_published_population_is_adopted_once_without_gen(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);stage=root/'waves/w001/gen_stage';stage.mkdir(parents=True)
            for name in ('gen_inventory.json','gen_publication.json'):
                controller.new(stage/name,dict(complete=True))
            proof=root/'authority.json'
            controller.new(proof,dict(schema='shift-reviewed-gen-handoff-v1',wave_index=1,
                gen_cluster=123,science_rerun=False,files={name:controller.sha(stage/name)
                for name in ('gen_inventory.json','gen_publication.json')}))
            authority=dict(path=str(proof),sha256=controller.sha(proof))
            config=dict(directory=str(root),dependencies={str(proof):authority['sha256']},
                        reviewed_gen_handoffs=[authority],handoff_operations=str(root/'operations'),lcg_wrapper='/qualified/lcg.sh')
            state=dict(waves={'1':dict(gen_cluster=123,terminal=True,successful=False)},failures={'wave_1':{}})
            native=dict(ExitCode=1,ExitBySignal=False,Iwd=str(stage))
            with patch.object(controller,'jobs',return_value=[]),patch.object(controller,'scheduler_arguments',return_value=[]), \
                 patch.object(controller,'run_condor',return_value=json.dumps([native])),patch.object(controller,'run_logged') as handoff:
                controller.adopt_reviewed_gen_handoffs(config,root/'config.json',state)
                controller.adopt_reviewed_gen_handoffs(config,root/'config.json',state)
                self.assertEqual(handoff.call_count,1)
                self.assertEqual(handoff.call_args.args[0],['/bin/bash','/qualified/lcg.sh','--config',str(root/'config.json'),'--handoff-wave','1'])
            self.assertNotIn('terminal',state['waves']['1'])
            self.assertNotIn('wave_1',state['failures'])
            self.assertEqual(len(state['reviewed_handoff_recoveries']),1)
            state['waves']['1'].pop('reviewed_handoff_sha256')
            with patch.object(controller,'jobs',return_value=[dict(JobStatus=2)]),self.assertRaises(RuntimeError):
                controller.adopt_reviewed_gen_handoffs(config,root/'config.json',state)
            (stage/'gen_inventory.json').write_text('{}')
            with self.assertRaises(ValueError):controller.adopt_reviewed_gen_handoffs(config,root/'config.json',state)


class DurableSubmission(unittest.TestCase):
    def ad(self, directory, **changes):
        row = dict(ClusterId=123, ProcId=0, JobStatus=2, Iwd=str(directory),
                   ShiftDPStatisticsTask=str(directory))
        row.update(changes)
        return row

    def test_history_lookup_uses_one_exact_match_for_a_known_job(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for cluster, limit in ((123, '1'), (None, '2')):
                with self.subTest(cluster=cluster), \
                     patch.object(controller, 'scheduler_arguments', return_value=[]), \
                     patch.object(controller, 'run_condor',
                                  return_value=json.dumps([self.ad(directory)])) as command:
                    self.assertEqual(controller.verify_submitted_job({}, directory, [], cluster), 123)
                    arguments = command.call_args.args[0]
                    self.assertEqual(arguments[arguments.index('-limit') + 1], limit)
                    if cluster is not None:
                        self.assertIn('123.0', arguments)
                        self.assertNotIn('-constraint', arguments)
                    else:
                        self.assertIn('-constraint', arguments)

    def test_ambiguous_intent_never_resubmits(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            description = directory / 'worker.sub'
            description.write_text('queue\n')
            controller.new(directory / 'submission_intent.json', dict(
                task=str(directory), submit_sha256=controller.sha(description)))
            with patch.object(controller, 'jobs', return_value=[]), \
                 patch.object(controller, 'scheduler_arguments', return_value=[]), \
                 patch.object(controller, 'run_condor', return_value='[]') as command:
                with self.assertRaisesRegex(RuntimeError, 'Ambiguous'):
                    controller.submission({}, directory, description)
                self.assertEqual(command.call_count, 1)
                self.assertIn('condor_history', command.call_args.args[0][0])

    def test_queued_working_directory_must_be_persistent(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            description = directory / 'worker.sub'
            description.write_text('queue\n')
            with patch.object(controller, 'scheduler_arguments', return_value=[]), \
                 patch.object(controller, 'jobs', side_effect=[[], [self.ad(directory, Iwd='/pool/condor/dir_1')]]), \
                 patch.object(controller, 'run_condor', side_effect=['', '1 job(s) submitted to cluster 123.']):
                with self.assertRaisesRegex(RuntimeError, 'persistent working directory'):
                    controller.submission({}, directory, description)
            self.assertFalse((directory / 'submission.json').exists())
            self.assertEqual(controller.read(directory / 'submission_intent.json')['cluster'], 123)
            with patch.object(controller, 'jobs', return_value=[self.ad(directory, Iwd='/pool/condor/dir_1')]), \
                 patch.object(controller, 'run_condor') as command:
                with self.assertRaisesRegex(RuntimeError, 'persistent working directory'):
                    controller.submission({}, directory, description)
                command.assert_not_called()

    def test_recovered_queue_and_history_reject_a_scratch_working_directory(self):
        for history in (False, True):
            with self.subTest(history=history), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                description = directory / 'worker.sub'
                description.write_text('queue\n')
                controller.new(directory / 'submission_intent.json', dict(
                    task=str(directory), submit_sha256=controller.sha(description)))
                rows = [self.ad(directory, Iwd='/pool/condor/dir_1')]
                with patch.object(controller, 'scheduler_arguments', return_value=[]), \
                     patch.object(controller, 'jobs', return_value=[] if history else rows), \
                     patch.object(controller, 'run_condor', return_value=json.dumps(rows)) as command:
                    with self.assertRaisesRegex(RuntimeError, 'persistent working directory'):
                        controller.submission({}, directory, description)
                self.assertFalse((directory / 'submission.json').exists())
                self.assertEqual(command.call_count, int(history))
                if history:
                    self.assertIn('condor_history', command.call_args.args[0][0])

    def test_existing_receipt_is_revalidated_on_every_restart(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            description = directory / 'worker.sub'
            description.write_text('queue\n')
            receipt = dict(task=str(directory), submit_sha256=controller.sha(description), cluster=123)
            controller.new(directory / 'submission.json', receipt)
            for changes, error in ((dict(Iwd='/pool/condor/dir_1'), 'persistent working directory'),
                                   (dict(ShiftDPStatisticsTask='another task'), 'task identity'),
                                   (dict(ClusterId=124), 'exact native cluster'),
                                   (dict(ProcId=1), 'exact native cluster')):
                with self.subTest(changes=changes), \
                     patch.object(controller, 'jobs', return_value=[self.ad(directory, **changes)]), \
                     patch.object(controller, 'run_condor') as command:
                    for _ in range(2):
                        with self.assertRaisesRegex(RuntimeError, error):
                            controller.submission({}, directory, description)
                    command.assert_not_called()
            with patch.object(controller, 'jobs', return_value=[self.ad(directory)]), \
                 patch.object(controller, 'run_condor') as command:
                self.assertEqual(controller.submission({}, directory, description), receipt)
                command.assert_not_called()

    def test_recovered_intent_keeps_its_known_cluster_and_task(self):
        for changes, error in ((dict(ClusterId=124), 'exact native cluster'),
                               (dict(ShiftDPStatisticsTask='another task'), 'task identity')):
            with self.subTest(changes=changes), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                description = directory / 'worker.sub'
                description.write_text('queue\n')
                controller.new(directory / 'submission_intent.json', dict(
                    task=str(directory), submit_sha256=controller.sha(description), cluster=123))
                with patch.object(controller, 'jobs', return_value=[self.ad(directory, **changes)]), \
                     patch.object(controller, 'run_condor') as command:
                    with self.assertRaisesRegex(RuntimeError, error):
                        controller.submission({}, directory, description)
                    command.assert_not_called()
                self.assertFalse((directory / 'submission.json').exists())


class AggregateSubmission(unittest.TestCase):
    def prepare(self, point, sources, target):
        target.mkdir(parents=True)
        controller.new(target / 'plan.json', dict(schema='shift-dark-photon-additive-plan-v1',
            histogram_only=True, campaign='synthetic', points=[dict(point=point, sources=sources)]))
        template = target / 'aggregate.sub.in'
        template.write_text('arguments = SEAL_DIGEST\nqueue\n')
        controller.new(target / 'seal.json', dict(files={str(path): controller.sha(path)
            for path in (target / 'plan.json', template)}))
        (target / 'aggregate.sub').write_text(template.read_text().replace(
            'SEAL_DIGEST', controller.sha(target / 'seal.json')))

    def test_crash_after_native_submission_recovers_the_same_aggregate(self):
        class ControllerCrash(BaseException):
            pass

        class Transport:
            def __init__(self, host, cache_directory):
                self.cache_directory = Path(cache_directory)
                self.cache_directory.mkdir(exist_ok=True)

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            worklist = directory / 'worklist.json'
            controller.new(worklist, dict(waves=[dict(batches=[dict(point='m15_prompt')])]))
            config = dict(directory=str(directory), aggregate_workers=1, worklist=str(worklist))
            populations = [dict(point='m15_prompt', plan_path=str(path)) for path in
                           (directory / 'baseline.json', directory / 'waves/w000/production/plan.json')]
            state, prepared, native_submits = {}, [], []
            queued = []
            state_path = directory / 'state.json'
            original_save = controller.save

            def prepare(config, point, sources, target):
                prepared.append(target)
                self.prepare(point, sources, target)

            def queue(config, constraint):
                return queued

            def command(arguments, **options):
                if '-dry-run' in arguments:
                    return ''
                self.assertIn('condor_submit', arguments[0])
                persisted = controller.read(state_path)['aggregates']['m15_prompt']
                self.assertEqual(persisted['directory'], str(prepared[0]))
                self.assertEqual(persisted['source_plans'], [item['plan_path'] for item in populations])
                self.assertNotIn('cluster', persisted)
                native_submits.append(arguments)
                queued.append(dict(ClusterId=123, ProcId=0, JobStatus=2, Iwd=str(prepared[0]),
                                   ShiftDPStatisticsTask=str(prepared[0])))
                return '1 job(s) submitted to cluster 123.'

            def crash_before_cluster_save(path, value):
                if Path(path) == state_path and value['aggregates']['m15_prompt'].get('cluster'):
                    raise ControllerCrash()
                original_save(path, value)

            with patch('watch_dark_photon_production.EosRpc', Transport), \
                 patch.object(controller, 'available_sources', return_value={'m15_prompt': populations}), \
                 patch.object(controller, 'aggregate_plan', side_effect=prepare), \
                 patch.object(controller, 'scheduler_arguments', return_value=[]), \
                 patch.object(controller, 'jobs', side_effect=queue), \
                 patch.object(controller, 'run_condor', side_effect=command):
                with patch.object(controller, 'save', side_effect=crash_before_cluster_save):
                    with self.assertRaises(ControllerCrash):
                        controller.publish(config, state)
                restarted = controller.read(state_path)
                self.assertNotIn('cluster', restarted['aggregates']['m15_prompt'])
                controller.publish(config, restarted)
            self.assertEqual(len(native_submits), 1)
            self.assertEqual(prepared, [directory / 'aggregates/m15_prompt/v0001'])
            self.assertEqual(list((directory / 'aggregates/m15_prompt').iterdir()), prepared)
            self.assertEqual(controller.read(state_path)['aggregates']['m15_prompt']['cluster'], 123)

    def test_committed_warning_and_lost_controller_state_recover_v1_without_resubmitting(self):
        class Transport:
            def __init__(self, host, cache_directory):
                self.cache_directory = Path(cache_directory)
                self.cache_directory.mkdir(exist_ok=True)

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            controller.new(directory / 'worklist.json', dict(waves=[dict(batches=[dict(point='m15_prompt')])]))
            config = dict(directory=str(directory), aggregate_workers=1,
                          worklist=str(directory / 'worklist.json'))
            populations = [dict(point='m15_prompt', plan_path=str(path)) for path in
                           (directory / 'baseline.json', directory / 'waves/w000/production/plan.json')]
            queued, submissions = [], []

            def command(arguments, **options):
                if '-dry-run' in arguments:
                    return ''
                submissions.append(arguments)
                target = directory / 'aggregates/m15_prompt/v0001'
                queued.append(dict(ClusterId=123, ProcId=0, JobStatus=2, Iwd=str(target),
                                   ShiftDPStatisticsTask=str(target)))
                raise RuntimeError('Scheduler state unknown: warning after committed submission')

            with patch('watch_dark_photon_production.EosRpc', Transport), \
                 patch.object(controller, 'available_sources', return_value={'m15_prompt': populations}), \
                 patch.object(controller, 'aggregate_plan', side_effect=lambda config, point, sources, target:
                              self.prepare(point, sources, target)) as prepare, \
                 patch.object(controller, 'scheduler_arguments', return_value=[]), \
                 patch.object(controller, 'jobs', side_effect=lambda *args: queued), \
                 patch.object(controller, 'run_condor', side_effect=command):
                with self.assertRaisesRegex(RuntimeError, 'committed submission'):
                    controller.publish(config, {})
                # Reproduce the old controller's lost pending state after the error.
                restarted = {}
                controller.publish(config, restarted)
            self.assertEqual(len(submissions), 1)
            self.assertEqual(prepare.call_count, 1)
            self.assertEqual(restarted['aggregates']['m15_prompt']['cluster'], 123)
            self.assertEqual([path.name for path in (directory / 'aggregates/m15_prompt').iterdir()], ['v0001'])

    def test_orphan_intent_with_unknown_scheduler_state_blocks_a_new_version(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            populations = [dict(plan_path='/baseline.json'), dict(plan_path='/waves/w000/production/plan.json')]
            target = directory / 'aggregates/m15_prompt/v0001'
            self.prepare('m15_prompt', populations, target)
            controller.new(target / 'submission_intent.json', dict(task=str(target),
                submit_sha256=controller.sha(target / 'aggregate.sub')))
            state = {}
            recovered = controller.reconcile_aggregate(dict(directory=str(directory)), state, 'm15_prompt', populations)
            with patch.object(controller, 'jobs', return_value=[]), \
                 patch.object(controller, 'scheduler_arguments', return_value=[]), \
                 patch.object(controller, 'run_condor', return_value='[]') as command:
                with self.assertRaisesRegex(RuntimeError, 'Ambiguous submitted job'):
                    controller.submission({}, recovered['directory'], target / 'aggregate.sub')
            self.assertEqual(command.call_count, 1)
            self.assertIn('condor_history', command.call_args.args[0][0])
            self.assertEqual(list(target.parent.iterdir()), [target])

    def test_multiple_or_changed_orphans_fail_closed_before_submission(self):
        for mode in ('multiple', 'changed_source', 'changed_template', 'changed_pending'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                populations = [dict(plan_path='/baseline.json', plan_sha256='a'),
                               dict(plan_path='/waves/w000/production/plan.json', plan_sha256='b')]
                target = directory / 'aggregates/m15_prompt/v0001'
                self.prepare('m15_prompt', populations, target)
                state = {}
                if mode == 'multiple':
                    self.prepare('m15_prompt', populations, target.parent / 'v0002')
                elif mode == 'changed_source':
                    populations = [dict(item, plan_sha256='changed') for item in populations]
                elif mode == 'changed_template':
                    (target / 'aggregate.sub.in').write_text('changed\n')
                else:
                    state = dict(aggregates=dict(m15_prompt=dict(directory='/another/path')))
                with patch.object(controller, 'submission') as submit:
                    with self.assertRaises((ValueError, RuntimeError)):
                        controller.reconcile_aggregate(dict(directory=str(directory)), state, 'm15_prompt', populations)
                    submit.assert_not_called()

    def test_new_sources_do_not_replace_the_single_pending_population(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            populations = [dict(plan_path='/baseline.json'), dict(plan_path='/waves/w000/production/plan.json')]
            target = directory / 'aggregates/m15_prompt/v0001'
            self.prepare('m15_prompt', populations, target)
            state = {}
            record = controller.reconcile_aggregate(dict(directory=str(directory)), state, 'm15_prompt',
                populations + [dict(plan_path='/waves/w001/production/plan.json')])
            self.assertEqual(record['source_plans'], [item['plan_path'] for item in populations])
            self.assertEqual(controller.read(target / 'aggregate_intent.json'), record)

    def test_verified_previous_publication_allows_the_next_version(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            populations = [dict(plan_path='/baseline.json'), dict(plan_path='/waves/w000/production/plan.json')]
            target = directory / 'aggregates/m15_prompt/v0001'
            self.prepare('m15_prompt', populations, target)
            receipt = target / 'completed_points/m15_prompt.json'
            receipt.parent.mkdir()
            controller.new(receipt, dict(complete=True))
            state = dict(published=dict(m15_prompt=dict(point_receipt_path=str(receipt),
                point_receipt_sha256=controller.sha(receipt), source_plans=[item['plan_path'] for item in populations])))
            self.assertIsNone(controller.reconcile_aggregate(dict(directory=str(directory)), state, 'm15_prompt', populations))


class AggregateDescription(unittest.TestCase):
    def test_standalone_aggregate_policy_is_sealed_and_preserves_other_requirements(self):
        for requirement in ('(TARGET.OpSysMajorVer == 9)', '(TARGET.Memory >= 4000)'):
            with self.subTest(requirement=requirement), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                tools = directory / 'tools'
                tools.mkdir()
                for name in ('aggregate_dark_photon_samples.py', 'dark_photon_production_support.py',
                             'export_dark_photon_cross_sections.py', 'pythia_generation_ledger.py',
                             'dark_photon_meson.py', 'dark_photon_meson_source.py',
                             'dark_photon_vector_meson.py', 'dark_photon_dipion_meson.py'):
                    (tools / name).write_text('# synthetic helper\n')
                meson = 'Memory' in requirement
                guard = directory / ('batches/meson_canary/production' if meson else 'waves/w000/production')
                guard.mkdir(parents=True)
                controller.new(guard / 'seal.json', {})
                controller.new(guard / 'plan.json', dict(points=[dict(point='m15_prompt')]))
                (guard / 'finalizer_worker.sh').write_text('#!/bin/bash\npython3 ' + str(guard / 'finalize_point.py') + '\n')
                (guard / 'finalizer.sub').write_text('initialdir = ' + str(guard) + '\nrequirements = ' + requirement +
                    '\nrequest_memory = 4000 MB\narguments = old\n' +
                    ('+SendCredential = False\n+WantIOProxy = False\non_exit_hold = False\non_exit_remove = True\n'
                     if meson else '') + 'queue\n')
                sources = []
                for index in range(2):
                    path = directory / ('source' + str(index) + '.json')
                    controller.new(path, dict(points=[dict(point='m15_prompt', mass_gev=15, epsilon=1e-5,
                        proper_ctau_mm=0, sampling_epsilon=.01, events=20)]))
                    sources.append(dict(point='m15_prompt', plan_path=str(path)))
                config = dict(directory=str(directory), tools=str(tools), campaign_prefix='synthetic',
                              eos_base='/eos/synthetic', conditions={})
                if meson:
                    config.update(production_mechanism='eta-etaprime-radiative', aggregation_guard=str(guard),
                                  merge_source_histograms=False,aggregate_group_events=4000,
                                  single_source_regrouping_canary=True)
                    sources=sources[:1]
                target = directory / 'aggregates/m15_prompt/v0001'
                controller.aggregate_plan(config, 'm15_prompt', sources, target)
                plan=controller.read(target/'plan.json')
                self.assertIs(plan['histogram_only'],not meson)
                self.assertEqual(plan['group_events'],4000 if meson else 2000)
                description = (target / 'aggregate.sub').read_text()
                self.assertNotIn('OpSysMajorVer', description)
                self.assertIn('request_memory = 4000 MB', description)
                for policy in ('+SendCredential = True', '+WantIOProxy = True',
                               'on_exit_hold = (ExitBySignal == True) || (ExitCode != 0)',
                               'on_exit_remove = (ExitBySignal == False) && (ExitCode == 0)'):
                    self.assertEqual(description.count(policy), 1)
                self.assertNotIn('+SendCredential = False', description)
                self.assertNotIn('+WantIOProxy = False', description)
                self.assertNotIn('on_exit_hold = False', description)
                self.assertNotIn('on_exit_remove = True', description)
                template = target / 'aggregate.sub.in'
                self.assertEqual(controller.read(target / 'seal.json')['files'][str(template)], controller.sha(template))
                if 'Memory' in requirement:
                    self.assertIn('requirements = ' + requirement, description)
                    self.assertTrue((target / 'dark_photon_meson.py').is_file())
                    plan = controller.read(target / 'plan.json')
                    self.assertEqual(plan['scoring_deployment']['path'], str(guard))
                    self.assertEqual(plan['dependencies'][str(target / 'dark_photon_meson_source.py')],
                                     controller.sha(tools / 'dark_photon_meson_source.py'))
                controller.aggregate_record(target, 'm15_prompt', sources)


class CanaryAndAdmission(unittest.TestCase):
    def config(self, directory):
        worklist = directory / 'worklist.json'
        controller.new(worklist, dict(waves=[dict(batches=[dict(point='m2_prompt')]),
                                                dict(batches=[dict(point='m5_prompt')])]))
        return dict(directory=str(directory), worklist=str(worklist), simultaneous_waves=3,
                    minimum_free_bytes=100, minimum_free_files=10)

    def test_old_statistics_do_not_pass_canary_gate(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = self.config(Path(temporary))
            state = dict(published=dict(m2_prompt=dict(new_events=100000,
                                                      source_plans=['/old/production/plan.json'])))
            self.assertFalse(controller.published_canary(config, state))
            state['published']['m2_prompt']['source_plans'].append(
                str(controller.wave_directory(config, 0) / 'production' / 'plan.json'))
            self.assertTrue(controller.published_canary(config, state))

    def test_bulk_waits_for_real_canary_publication(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = self.config(Path(temporary))
            with patch.object(controller, 'quota', return_value=dict(free_bytes=1000, free_files=100)), \
                 patch.object(controller, 'prepare_gen', return_value=dict(cluster=123)) as submit:
                state = controller.step(config, Path(temporary) / 'config.json', {})
            self.assertEqual(list(state['waves']), ['0'])
            self.assertEqual(submit.call_count, 1)
            self.assertFalse(state['canary_published'])

    def test_storage_shortfall_submits_nothing(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = self.config(Path(temporary))
            with patch.object(controller, 'quota', return_value=dict(free_bytes=99, free_files=100)), \
                 patch.object(controller, 'prepare_gen') as submit:
                state = controller.step(config, Path(temporary) / 'config.json', {})
            submit.assert_not_called()
            self.assertEqual(state['admission'], 'waiting_for_storage')

    def test_missing_manager_history_stops_new_admission(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            config = self.config(directory)
            stage = controller.wave_directory(config, 0) / 'gen_stage'
            stage.mkdir(parents=True)
            controller.new(stage / 'submission.json', dict(cluster=123))
            state = dict(waves={'0': dict(gen_cluster=123)})
            with patch.object(controller, 'quota', return_value=dict(free_bytes=1000, free_files=100)), \
                 patch.object(controller, 'jobs', return_value=[]), \
                 patch.object(controller, 'scheduler_arguments', return_value=[]), \
                 patch.object(controller, 'run_condor', return_value='[]'), \
                 patch.object(controller, 'prepare_gen') as submit:
                with self.assertRaisesRegex(RuntimeError, 'queue and history'):
                    controller.step(config, directory / 'config.json', state)
            submit.assert_not_called()


class DagQueueLimits(unittest.TestCase):
    def test_separate_ready_queue_limit_reaches_dagman(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with patch.object(controller, 'run_condor') as native, \
                 patch.object(controller, 'submission', return_value={'cluster': 1}):
                controller.submit_dag({'dag_max_idle': 5}, directory, 12)
            arguments = native.call_args.args[0]
            self.assertEqual(arguments[arguments.index('-maxjobs') + 1], '12')
            self.assertEqual(arguments[arguments.index('-maxidle') + 1], '5')
            self.assertEqual(native.call_args.kwargs['cwd'], directory)

    def test_invalid_ready_queue_limit_cannot_submit(self):
        with patch.object(controller, 'run_condor') as native:
            for limit in (0, 13, True):
                with self.subTest(limit=limit), self.assertRaises(ValueError):
                    controller.submit_dag({'dag_max_idle': limit}, Path('/persistent'), 12)
            native.assert_not_called()


class PreparationEnvironment(unittest.TestCase):
    def test_system_python_ignores_inherited_lcg_home_and_preserves_failed_logs(self):
        import os
        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / 'prepare.log'
            log.write_text('original failure evidence\n')
            command = ['/usr/bin/python3', '-c',
                       'import json,os; print(json.dumps(dict(home=os.getenv("PYTHONHOME"), '
                       'modules=os.getenv("PYTHONPATH"), libs=os.getenv("LD_LIBRARY_PATH"))))']
            with patch.dict(os.environ, {'PYTHONHOME':'/missing/lcg312',
                    'PYTHONPATH':'/missing/lcg312/site-packages', 'LD_LIBRARY_PATH':'/missing/lcg312/lib'}):
                controller.run_logged(command, log, timeout=30)
            self.assertEqual(log.read_text(), 'original failure evidence\n')
            self.assertEqual(json.loads((Path(str(log)+'.attempt1')).read_text()),
                             dict(home=None, modules=None, libs=None))

    def test_lcg_python_retains_its_matching_package_environment(self):
        import os
        with tempfile.TemporaryDirectory() as temporary:
            with patch.dict(os.environ, {'PYTHONHOME':'/qualified/lcg312',
                    'PYTHONPATH':'/qualified/lcg312/site-packages'}), \
                 patch.object(controller.subprocess, 'run') as launch:
                controller.run_logged(['/qualified/lcg312/bin/python3', 'qualify.py'],
                                      Path(temporary)/'qualify.log')
            self.assertEqual(launch.call_args.kwargs['env']['PYTHONHOME'], '/qualified/lcg312')
            self.assertEqual(launch.call_args.kwargs['env']['PYTHONPATH'], '/qualified/lcg312/site-packages')

    def test_gen_preparation_reuses_immutable_handoff_after_preparation_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root/'waves/w000'; directory.mkdir(parents=True)
            controller.new(directory/'preparation_receipt.json', dict(preparation='/frozen/plan.json'))
            controller.new(directory/'handoff.json', dict(original_failure='preserved'))
            config = dict(directory=str(root), lcg_wrapper='/new/qualified/lcg.sh',
                minimum_free_bytes=1000, minimum_free_files=100, tools='/new/frozen/tools',
                runtime_fingerprint='/frozen/runtime.json', dependencies={}, prior_inventories=[],
                failure_base='/eos/user/j/jniedzie/shift_cmssw_support/test', workspace='/persistent/workspace')
            with patch.object(controller, 'run_logged', side_effect=[RuntimeError('first failure'),None]) as prepare, \
                 patch.object(controller, 'submit_dag', return_value=dict(cluster=123)) as submit:
                with self.assertRaises(RuntimeError):
                    controller.prepare_gen(config, root/'config.json', 0)
                receipt = controller.prepare_gen(config, root/'config.json', 0)
            self.assertEqual(receipt, dict(cluster=123))
            self.assertEqual(prepare.call_count, 2)
            submit.assert_called_once()
            versions = list(directory.glob('handoff.*.json'))
            self.assertEqual(len(versions), 1)
            self.assertEqual(controller.read(versions[0])['handoff_argvs'][0][1], config['lcg_wrapper'])
            self.assertEqual(controller.read(directory/'handoff.json'), dict(original_failure='preserved'))


if __name__ == '__main__':
    unittest.main()

class ReviewedBudgetHandoff(unittest.TestCase):
    def fixture(self, root):
        stage=root/'waves/w001/gen_stage';stage.mkdir(parents=True)
        for name in ('gen_inventory.json','gen_publication.json'):
            controller.new(stage/name,dict(complete=True))
        descriptor=stage/'material_selected_source.json';controller.new(descriptor,dict(selected_indices=[1,2]))
        replacement=root/'operations/w001';replacement.mkdir(parents=True)
        description=replacement/'worker.sub';description.write_text('queue\n')
        native=dict(ClusterId=123,ProcId=0,Iwd=str(stage),JobStatus=4,ExitCode=1,ExitBySignal=False)
        proof=root/'authority.json'
        controller.new(proof,dict(schema='shift-reviewed-gen-handoff-v1',wave_index=1,gen_cluster=123,
            science_rerun=False,failure_kind='detector_stage_budget_upgrade_before_SIM',operational_cancel=True,
            detector_submission_absent=True,terminal_native_ad=native,absolute_stage_timeouts=[43200,7200,14400,14400],
            files={n:controller.sha(stage/n) for n in ('gen_inventory.json','gen_publication.json')},
            preserved_material_descriptors={str(descriptor):controller.sha(descriptor)},
            replacement_handoff_directory=str(replacement)))
        authority=dict(path=str(proof),sha256=controller.sha(proof))
        config=dict(directory=str(root),dependencies={str(proof):authority['sha256'],str(description):controller.sha(description)},
            reviewed_gen_handoffs=[authority],handoff_operations=str(root/'operations'),absolute_stage_timeouts=[43200,7200,14400,14400])
        state=dict(waves={'1':dict(gen_cluster=123,terminal=True,successful=False)},failures={'wave_1':{}})
        return config,state,native,replacement,descriptor

    def test_async_recovery_reuses_population_and_submits_once(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);config,state,native,replacement,descriptor=self.fixture(root)
            with patch.object(controller,'jobs',return_value=[]),patch.object(controller,'scheduler_arguments',return_value=[]), \
                 patch.object(controller,'run_condor',return_value=json.dumps([native])), \
                 patch.object(controller,'submission',return_value=dict(cluster=456)) as submit, \
                 patch.object(controller,'run_logged') as synchronous:
                controller.adopt_reviewed_gen_handoffs(config,root/'config.json',state)
                controller.adopt_reviewed_gen_handoffs(config,root/'config.json',state)
            submit.assert_called_once_with(config,replacement,replacement/'worker.sub')
            synchronous.assert_not_called()
            self.assertEqual(state['waves']['1']['replacement_handoff_cluster'],456)
            self.assertNotIn('terminal',state['waves']['1'])
            self.assertNotIn('wave_1',state['failures'])

    def test_native_or_descriptor_change_cannot_authorize_recovery(self):
        for changed in ('native','descriptor','submit','budgets'):
            with self.subTest(changed=changed),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);config,state,native,replacement,descriptor=self.fixture(root)
                if changed=='native':native=dict(native,ExitCode=0)
                elif changed=='descriptor':descriptor.write_text('{}')
                elif changed=='submit':(replacement/'worker.sub').write_text('tampered')
                else:config['absolute_stage_timeouts']=[1800,7200,14400,14400]
                with patch.object(controller,'jobs',return_value=[]),patch.object(controller,'scheduler_arguments',return_value=[]), \
                     patch.object(controller,'run_condor',return_value=json.dumps([native])),patch.object(controller,'submission') as submit:
                    with self.assertRaises((RuntimeError,ValueError)):
                        controller.adopt_reviewed_gen_handoffs(config,root/'config.json',state)
                    submit.assert_not_called()

class ReviewedDetectorStage(unittest.TestCase):
    def fixture(self,root):
        original=root/'waves/w001/production';original.mkdir(parents=True)
        replacement=root/'operations/w001';replacement.mkdir(parents=True)
        controller.new(original/'plan.json',dict(events=20))
        controller.new(original/'stage_budget_policy.json',dict(stage_timeouts_seconds=[43200,7200,14400,14400]))
        controller.new(original/'seal.json',dict(files={str(original/'plan.json'):controller.sha(original/'plan.json')}))
        controller.new(original/'submission.json',dict(cluster=123))
        (original/'detector.sub').write_text('executable = '+str(original/'detector_worker.sh')+'\narguments = identical seed slice seal\n')
        (original/'pipeline.dag').write_text('JOB D '+str(original/'detector.sub')+'\n')
        (replacement/'detector_worker.sh').write_text('#!/bin/bash\ntrue\n')
        (replacement/'detector.sub').write_text((original/'detector.sub').read_text().replace(str(original/'detector_worker.sh'),str(replacement/'detector_worker.sh')))
        (replacement/'pipeline.dag').write_text((original/'pipeline.dag').read_text().replace(str(original/'detector.sub'),str(replacement/'detector.sub')))
        native=dict(ClusterId=123,ProcId=0,Iwd=str(original),JobStatus=4,ExitCode=1,ExitBySignal=False)
        proof=dict(schema='shift-reviewed-detector-stage-replacement-v1',failure_kind='malformed_budget_preflight_shell_before_SIM',
            wave_index=1,detector_cluster=123,terminal_native_ad=native,science_rerun=False,detector_payload_started=False,
            original_directory=str(original),original_plan_sha256=controller.sha(original/'plan.json'),
            original_seal_sha256=controller.sha(original/'seal.json'),original_budget_policy_sha256=controller.sha(original/'stage_budget_policy.json'),directory=str(replacement),
            files={str(p):controller.sha(p) for p in [replacement/'pipeline.dag',replacement/'detector.sub',replacement/'detector_worker.sh']})
        path=root/'review.json';controller.new(path,proof)
        config=dict(directory=str(root),tools=str(root/'tools'),dependencies=dict(proof['files'],**{str(path):controller.sha(path)}),
            reviewed_detector_stage_overrides={'1':dict(directory=str(replacement),review=dict(path=str(path),sha256=controller.sha(path)))})
        state=dict(waves={'1':dict(gen_cluster=100,cluster=123,terminal=True,successful=False)},failures={'wave_1':{}})
        return config,state,native,replacement,original

    def test_only_new_operational_manager_is_adopted_once(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);config,state,native,replacement,original=self.fixture(root)
            with patch.object(controller,'jobs',return_value=[]),patch.object(controller,'scheduler_arguments',return_value=[]), \
                 patch.object(controller,'run_condor',return_value=json.dumps([native])), \
                 patch.object(controller,'submit_dag',return_value=dict(cluster=456)) as submit:
                controller.adopt_reviewed_detector_stages(config,state)
                controller.adopt_reviewed_detector_stages(config,state)
            submit.assert_called_once_with(config,replacement,102)
            self.assertEqual(state['waves']['1']['gen_cluster'],100)
            self.assertEqual(state['waves']['1']['replacement_detector_cluster'],456)
            self.assertNotIn('terminal',state['waves']['1'])

    def test_changed_seed_scientific_seal_or_native_record_blocks_replacement(self):
        for changed in ['arguments','seal','native','live']:
            with self.subTest(changed=changed),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);config,state,native,replacement,original=self.fixture(root)
                if changed=='arguments':(replacement/'detector.sub').write_text('changed seed')
                elif changed=='seal':(original/'plan.json').write_text('{}')
                elif changed=='native':native=dict(native,ExitCode=0)
                with patch.object(controller,'jobs',return_value=[dict(JobStatus=2)] if changed=='live' else []), \
                     patch.object(controller,'scheduler_arguments',return_value=[]),patch.object(controller,'run_condor',return_value=json.dumps([native])), \
                     patch.object(controller,'submit_dag') as submit:
                    with self.assertRaises((RuntimeError,ValueError)):
                        controller.adopt_reviewed_detector_stages(config,state)
                    submit.assert_not_called()

class RemovedFailedAggregate(unittest.TestCase):
    def test_removed_relocated_log_failure_keeps_exact_original_failed_hold(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);directory=root/'v0001';directory.mkdir()
            controller.new(directory/'submission.json',dict(cluster=123))
            failure=directory/'failure.err';failure.write_text('Source full GEN audit differs: retained log_path\n')
            record=dict(directory=str(directory),plan_sha256='a'*64)
            held=dict(ClusterId=123,ProcId=0,JobStatus=5,ExitCode=1,ExitBySignal=False,Iwd=str(directory))
            proof=dict(schema='shift-reviewed-aggregate-retirement-v1',failure_kind='GEN_ledger_log_path_relocation',
                directory=str(directory),point='m2_prompt',aggregate_record=record,payload_started=False,queue_absent=True,
                terminal_native_ad=dict(held,JobStatus=3),original_held_native_ad=held,
                failure_evidence_sha256={str(failure):controller.sha(failure)})
            def config_for(value):
                path=root/'proof.json';controller.save(path,value);digest=controller.sha(path)
                return dict(dependencies={str(path):digest},retired_aggregate_attempts=[dict(path=str(path),sha256=digest)])
            with patch.object(controller,'jobs',return_value=[]):
                self.assertTrue(controller.retired_aggregate_attempt(config_for(proof),directory,'m2_prompt',record))
                for change in [dict(failure_kind='arbitrary failure'),dict(original_held_native_ad=dict(held,ExitCode=0)),
                    dict(original_held_native_ad=dict(held,Iwd='/unrelated')),dict(failure_evidence_sha256={}),
                    dict(terminal_native_ad=dict(held,JobStatus=3,ExitBySignal=True))]:
                    with self.subTest(change=change),self.assertRaises(ValueError):
                        controller.retired_aggregate_attempt(config_for(dict(proof,**change)),directory,'m2_prompt',record)
            with patch.object(controller,'jobs',return_value=[held]),self.assertRaises(RuntimeError):
                controller.retired_aggregate_attempt(config_for(proof),directory,'m2_prompt',record)
            failure.write_text('changed evidence')
            with self.assertRaises(ValueError):
                controller.retired_aggregate_attempt(config_for(proof),directory,'m2_prompt',record)
