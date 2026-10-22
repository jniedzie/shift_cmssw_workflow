# Code style: AI-code. AI-generated fixed-M production and restart regression tests.
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import prepare_dark_photon_meson_production as preparation
import run_dark_photon_meson_production as production
import prepare_dark_photon_meson_controller as replacement
import audit_dark_photon_meson_coverage as coverage_audit


class FixedExposure(unittest.TestCase):
    def rows(self):
        return [dict(mass_gev=.3,epsilon=.001,source_collisions=4000,prompt=True,
                     operational_canary=True),
                dict(mass_gev=.9,epsilon=3e-7,source_collisions=25000,
                     target_mean_lab_flight_m=100,operational_canary=True)]

    def test_independent_fixed_M_worklist_does_not_allocate_a_fake_N(self):
        rows=preparation.validate_batches(self.rows(),[])
        self.assertEqual([row['source_collisions'] for row in rows],[4000,25000])
        self.assertNotIn('events',rows[0])
        self.assertEqual(rows[1]['point'],'m0p9_lab_100m')
        self.assertEqual(rows[1]['detector_job_base']-rows[0]['detector_job_base'],1000)

    def test_a_fresh_version_uses_disjoint_explicit_namespaces(self):
        old=preparation.validate_batches(self.rows(),[])
        new=preparation.validate_batches(self.rows(),[],761217001,1761217001,5540000)
        for key in ('seed','run_number','detector_job_base'):
            self.assertTrue({row[key] for row in old}.isdisjoint({row[key] for row in new}))

    def test_prior_seed_collision_or_wrong_canary_family_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'prior.json'
            path.write_text(json.dumps(dict(points={'old':dict(seed=761216001,event_ids=[])})))
            with self.assertRaisesRegex(ValueError,'overlap'):
                preparation.validate_batches(self.rows(),[path])
        rows=self.rows()
        rows[1].update(mass_gev=.4)
        with self.assertRaisesRegex(ValueError,'eta-prime-only'):
            preparation.validate_batches(rows,[])

    def test_adoption_uses_all_actual_N_and_keeps_M_separate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            batch=preparation.validate_batches(self.rows(),[])[0]
            batch['final_output_base']='/eos/synthetic'
            contract=dict(production_mechanism='eta-etaprime-radiative',requested_events=23,
                source_exposure=dict(fixed_source_collisions=4000,accepted_signal_events=23),
                physical_width_table=dict(ctau_mm=.01),mean_beta_gamma_reference=500,
                mean_lab_flight_reference_estimate_m=.005,
                **{key:batch[key] for key in ('mass_gev','epsilon','seed','run_number')})
            (root/'contract.json').write_text(json.dumps(contract))
            (root/'validation.json').write_text(json.dumps(dict(runtime_validated=True,events=23,
                event_ids=[[batch['run_number'],1,index] for index in range(1,24)])))
            row=production.adopted_row(batch,root)
            self.assertEqual(row['events'],23)
            self.assertNotEqual(row['events'],batch['source_collisions'])
            bad=dict(batch,source_collisions=23)
            with self.assertRaisesRegex(ValueError,'fixed-M/random-N'):
                production.adopted_row(bad,root)

    def test_large_source_ledger_and_payloads_never_enter_compact_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            for name in ('contract.json','manifest.json','meson_ledger_publication.json',
                         'meson_source_ledger.jsonl','gen.root','signal.hepmc'):
                (root/name).write_text('{}')
            kept={path.name for path in production.compact_files(root)}
            self.assertEqual(kept,{'contract.json','manifest.json','meson_ledger_publication.json'})


class UnifiedSource(unittest.TestCase):
    def authority(self, root, name, data):
        path = root / name
        path.write_text(json.dumps(data))
        return dict(path=str(path), sha256=production.durable.sha(path))

    def configuration(self, root):
        card = dict(channels=['eta->gamma_Aprime', 'omega->pi0_Aprime'])
        model = root / 'model.json'
        model.write_text(json.dumps(card))
        batch = dict(point='m0p3_prompt', mass_gev=.3, epsilon=.001,
                     target_mean_lab_flight_m=None, seed=123, run_number=456,
                     source_collisions=4000, final_output_base='/eos/synthetic')
        config = dict(production_mechanism='meson-radiative', model_card=str(model))
        config['meson_completion_scope'] = self.authority(root, 'scope.json', dict(
            schema='shift-dark-photon-meson-coverage-v1', included_channels=card['channels'],
            physics_complete=False, remaining_channels=[dict(channel='eta_prime->pipi_Aprime',
                status='missing_model', reason='Differential transition model needs validation')]))
        config['lifetime_reference_authority'] = self.authority(root, 'reference.json', dict(
            schema='shift-meson-physical-point-reference-v1', points={batch['point']:dict(
                mass_gev=.3, epsilon=.001, proper_ctau_mm=.01, target_mean_lab_flight_m=None)}))
        return config, batch, card

    def test_declared_canaries_need_not_repeat_the_legacy_eta_split(self):
        rows = FixedExposure().rows()
        rows[1]['mass_gev'] = .5
        unified = preparation.validate_batches(rows, [], mechanism='meson-radiative')
        self.assertEqual(len(unified), 2)
        with self.assertRaisesRegex(ValueError, 'eta-prime-only'):
            preparation.validate_batches(rows, [])
        rows[1].update(mass_gev=.3, epsilon=.001, prompt=True)
        rows[1].pop('target_mean_lab_flight_m')
        with self.assertRaisesRegex(ValueError, 'two distinct'):
            preparation.validate_batches(rows, [], mechanism='meson-radiative')

    def test_unified_adoption_keeps_reference_physics_and_actual_signal_N(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, batch, card = self.configuration(root)
            contract = dict(production_mechanism='meson-radiative', requested_events=23,
                source_exposure=dict(fixed_source_collisions=4000, accepted_signal_events=23),
                physical_width_table=dict(ctau_mm=.01), mean_beta_gamma_reference=123.,
                mean_lab_flight_reference_estimate_m=.00123, meson_model_card=card,
                meson_model_card_sha256=production.durable.sha(config['model_card']),
                **{key:batch[key] for key in ('mass_gev','epsilon','seed','run_number')})
            (root/'contract.json').write_text(json.dumps(contract))
            (root/'validation.json').write_text(json.dumps(dict(runtime_validated=True,events=23,
                event_ids=[[456,1,index] for index in range(1,24)])))
            adopted = production.adopted_row(batch, root, config)
            self.assertEqual(adopted['events'], 23)
            self.assertEqual(adopted['production_mechanism'], 'meson-radiative')
            self.assertEqual(adopted['mean_beta_gamma_reference'], 123.)
            self.assertEqual(adopted['proper_ctau_mm'], .01)
            contract['physical_width_table']['ctau_mm'] = .02
            (root/'contract.json').write_text(json.dumps(contract))
            with self.assertRaisesRegex(ValueError, 'reference lifetime'):
                production.adopted_row(batch, root, config)

    def test_new_boost_cannot_retune_the_existing_epsilon_anchor(self):
        with tempfile.TemporaryDirectory() as temporary:
            config, batch, _ = self.configuration(Path(temporary))
            production.reference_point(config, batch)
            with self.assertRaisesRegex(ValueError, 'reference epsilon/lifetime'):
                production.reference_point(config, dict(batch, epsilon=.002))

    def test_finite_job_completion_does_not_close_missing_physics(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, _, _ = self.configuration(root)
            scope = production.completion_scope(config)
            self.assertFalse(scope['physics_complete'])
            self.assertEqual(len(scope['remaining_channels']), 1)
            authority = Path(config['meson_completion_scope']['path'])
            data = json.loads(authority.read_text())
            data['physics_complete'] = True
            authority.write_text(json.dumps(data))
            config['meson_completion_scope']['sha256'] = production.durable.sha(authority)
            with self.assertRaisesRegex(ValueError, 'incomplete channel scope'):
                production.completion_scope(config)

    def test_other_meson_grid_keeps_the_entire_pinned_reservation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            forecast = self.authority(root, 'legacy_gate.json', dict(complete=True,
                total_meson_forecast_bytes=300, per_batch_forecast_bytes=dict(a=100,b=200)))
            config = dict(additional_reserved_meson_forecasts=[dict(name='legacy_eta', forecast=forecast)])
            total, claims = production.additional_reservations(config)
            self.assertEqual(total, 300)
            self.assertTrue(claims[0]['completed_peer_batches_release_no_credit'])
            (root/'state.json').write_text(json.dumps(dict(complete=True)))
            self.assertEqual(production.additional_reservations(config)[0], 300)
            Path(forecast['path']).write_text('{}')
            with self.assertRaisesRegex(ValueError, 'authority changed'):
                production.additional_reservations(config)

    def test_real_coverage_worker_retains_missing_channels_without_claiming_MC(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, batch, card = self.configuration(root)
            particle = root / 'ParticleData.xml'
            particle.write_text('<particle id="221" name="eta" m0=".547862">'
                '<channel bRatio=".39" products="22 22" onMode="1" /></particle>')
            inventory = coverage_audit.particle_data_inventory(particle)
            scope = coverage_audit.audit_coverage(card, [.3], inventory)
            config.update(directory=str(root), batches=[batch], tools=str(
                Path(production.__file__).parent), coverage_audit=dict(masses_gev=[.3],
                particle_data=dict(path=str(particle), sha256=production.durable.sha(particle))))
            config['meson_completion_scope'] = self.authority(root, 'scope.json', scope)
            (root/'coverage').mkdir()
            result = production.coverage(config)
            self.assertTrue(result['complete'])
            self.assertFalse(result['physics_complete'])
            self.assertFalse(result['source_sampling_verified'])
            self.assertGreaterEqual(len(result['remaining_channels']), 10)
            self.assertGreater(result['candidate_transitions'], 0)
            with self.assertRaisesRegex(RuntimeError, 'already started'):
                production.coverage(config)

    def test_coverage_native_execution_is_required_before_storage_or_MC_admission(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, batch, _ = self.configuration(root)
            config.update(directory=str(root), batches=[dict(batch, batch='canary')])
            receipt = dict(cluster=123, task=str(root/'coverage'))
            with patch.object(production.durable, 'submission', return_value=receipt) as submit, \
                 patch.object(production, 'native_status', return_value='active'), \
                 patch.object(production.durable, 'quota') as quota:
                state = production.step(config, {})
            self.assertEqual(submit.call_count, 1)
            self.assertEqual(state['coverage_audit']['cluster'], 123)
            self.assertFalse(state['physics_complete'])
            self.assertFalse(state['complete'])
            quota.assert_not_called()
            self.assertFalse(state['batches']['canary'])

    def test_finished_GEN_still_waits_for_full_storage_before_detector_handoff(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, batch, _ = self.configuration(root)
            batch.update(batch='canary', operational_canary=True)
            config.update(directory=str(root), batches=[batch], simultaneous_sources=1,
                          simultaneous_detector_points=1)
            directory = root/'batches/canary'
            directory.mkdir(parents=True)
            (directory/'submission.json').write_text('{}')
            (directory/'source_status.json').write_text(json.dumps(dict(complete=True)))
            (root/'coverage').mkdir()
            (root/'coverage/submission.json').write_text('{}')
            with patch.object(production, 'existing_receipt', return_value=dict(cluster=123)), \
                 patch.object(production, 'native_status', return_value='complete'), \
                 patch.object(production, 'coverage_result', return_value=dict(complete=True)), \
                 patch.object(production.durable, 'quota', return_value=dict(free_bytes=1, free_files=1)), \
                 patch.object(production, 'storage_gate', return_value=(False, dict(reason='full_peer_claims'))), \
                 patch.object(production, 'handoff') as handoff, \
                 patch.object(production.durable, 'submission') as submit:
                state = production.step(config, {})
            handoff.assert_not_called()
            submit.assert_not_called()
            self.assertEqual(state['batches']['canary']['stage'], 'waiting_for_detector_storage')

    def test_local_runtime_download_is_pinned_and_cannot_include_private_geometry(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, _, _ = self.configuration(root)
            private = root/'forbidden.gdml'
            private.write_text('synthetic test payload')
            source = root/'runtime.tar.gz'
            with tarfile.open(source, 'w:gz') as archive:
                archive.add(private, arcname='CMSSW_17_0_0_pre4/forbidden.gdml')
            digest = production.durable.sha(source)
            provenance = dict(schema='shift-bsm-gen-runtime-provenance-v1',
                archive_sha256=digest, archive_bytes=source.stat().st_size,
                cmssw_git_head='a'*40, cmssw_git_status=[], scram_arch='el9_amd64_gcc13')
            config['gen_runtime'] = dict(url='root://eosuser.cern.ch//eos/user/test/runtime.tar.gz',
                sha256=digest, bytes=source.stat().st_size, scram_arch='el9_amd64_gcc13',
                cmssw_relative='CMSSW_17_0_0_pre4', provenance=self.authority(root, 'runtime.json', provenance))
            scratch = root/'scratch'
            scratch.mkdir()
            import run_dark_photon_gen_stage as stage
            def copy_archive(argv, **kwargs):
                Path(argv[-1]).write_bytes(source.read_bytes())
            with patch.object(stage, 'command', side_effect=copy_archive):
                with self.assertRaisesRegex(ValueError, 'unsafe or private'):
                    production.unpack_gen_runtime(config, scratch)
            self.assertFalse((scratch/'runtime/CMSSW_17_0_0_pre4/forbidden.gdml').exists())


class Admission(unittest.TestCase):
    def test_newer_worklists_keep_their_full_storage_claim(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            path=root/'worklist.json'
            path.write_text(json.dumps(dict(total_additional_events=20,
                waves=[dict(batches=[dict(events=20)])])))
            authority=dict(name='newer_grid',bytes_per_event=326000,
                worklist=dict(path=str(path),sha256=production.durable.sha(path)))
            config=dict(additional_reserved_worklists=[authority])
            amount,claims=production.additional_reservations(config)
            self.assertEqual(amount,6520000)
            self.assertEqual(claims,[dict(name='newer_grid',events=20,reserved_bytes=6520000)])
            # A peer state, even claiming completion, cannot release this
            # deliberately conservative reservation.
            (root/'state.json').write_text(json.dumps(dict(complete=True)))
            self.assertEqual(production.additional_reservations(config)[0],amount)
            for value in [dict(authority,bytes_per_event=325999),
                          dict(authority,bytes_per_event=True)]:
                with self.assertRaises(ValueError):
                    production.additional_reservations(dict(additional_reserved_worklists=[value]))
            path.write_text('{}')
            with self.assertRaisesRegex(ValueError,'authority changed'):
                production.additional_reservations(config)

    def test_storage_gate_protects_new_campaigns_in_addition_to_both_original_claims(self):
        config=dict(minimum_eos_free_bytes=1000,minimum_eos_free_files=20)
        with patch.object(production,'adaptive_reservation',return_value=300), \
             patch.object(production,'additional_reservations',return_value=(400,[dict(name='new')])):
            allowed,evidence=production.storage_gate(config,dict(free_bytes=500001500,free_files=100))
            self.assertFalse(allowed)
            self.assertEqual(evidence['required_free_bytes'],500001700)
            self.assertEqual(evidence['additional_reserved_bytes'],400)

    def test_old_scale10_new_events_do_not_release_adaptive_reservation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            now=datetime.now(timezone.utc).isoformat()
            wave=dict(wave_index=0,batches=[dict(point='m2_prompt',events=20,seed=123,run_number=456)])
            pending=dict(wave_index=1,batches=[dict(point='remaining',events=417080,seed=789,run_number=987)])
            (root/'worklist.json').write_text(json.dumps(dict(total_additional_events=417100,waves=[wave,pending])))
            state=dict(checked_utc=now,published=dict(m2_prompt=dict(new_events=59030,
                source_plans=['/old/scale10/plan.json'])))
            (root/'state.json').write_text(json.dumps(state))
            config=dict(adaptive_directory=str(root))
            full=2000000000+417100*326000
            self.assertEqual(production.adaptive_reservation(config),full)
            plan=root/'waves/w000/production/plan.json'
            plan.parent.mkdir(parents=True)
            plan.write_text(json.dumps(dict(points=wave['batches'])))
            state['published']['m2_prompt']['source_plans'].append(str(plan))
            (root/'state.json').write_text(json.dumps(state))
            self.assertEqual(production.adaptive_reservation(config),full-20*326000)

    def test_unhealthy_or_missing_peer_state_keeps_the_complete_sealed_allocation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            worklist=dict(total_additional_events=20,waves=[dict(wave_index=0,
                batches=[dict(point='m2_prompt',events=20,seed=123,run_number=456)])])
            worklist_path=root/'worklist.json'
            worklist_path.write_text(json.dumps(worklist))
            authority=root/'meson'
            authority.mkdir()
            (authority/'seal.json').write_text(json.dumps(dict(files={
                str(worklist_path):production.durable.sha(worklist_path)})))
            config=dict(adaptive_directory=str(root),directory=str(authority))
            full=2000000000+20*326000
            self.assertEqual(production.adaptive_reservation(config),full)
            for state in [{},dict(checked_utc='invalid'),
                          dict(checked_utc='2000-01-01T00:00:00+00:00',complete=True),
                          dict(checked_utc=datetime.now(timezone.utc).isoformat(),error='held for repair')]:
                with self.subTest(state=state):
                    (root/'state.json').write_text(json.dumps(state))
                    self.assertEqual(production.adaptive_reservation(config),full)
            worklist['total_additional_events']=0
            worklist_path.write_text(json.dumps(worklist))
            with self.assertRaisesRegex(ValueError,'frozen meson authority'):
                production.adaptive_reservation(config)

    def test_bulk_forecast_releases_only_fully_published_batches(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            (root/'config.json').write_text('{}')
            (root/'card.json').write_text('{}')
            gate=dict(complete=True,all_seven_mass_GEN_audits_passed=True,
                model_card_sha256=production.durable.sha(root/'card.json'),
                worklist_sha256=production.durable.sha(root/'config.json'),
                total_meson_forecast_bytes=300,per_batch_forecast_bytes={'a':100,'b':200})
            (root/'gate.json').write_text(json.dumps(gate))
            config=dict(directory=str(root),model_card=str(root/'card.json'),bulk_storage_gate=str(root/'gate.json'),
                minimum_eos_free_bytes=1000,minimum_eos_free_files=20,batches=[dict(batch='a'),dict(batch='b')])
            observation=dict(free_bytes=1500,free_files=100)
            with patch.object(production,'adaptive_reservation',return_value=300):
                allowed,evidence=production.storage_gate(config,observation,bulk=True)
                self.assertFalse(allowed)
                state=dict(batches=dict(a=dict(published={'events':20}),b=dict(stage='GEN')))
                allowed,evidence=production.storage_gate(config,observation,bulk=True,state=state)
                self.assertTrue(allowed)
                self.assertEqual(evidence['meson_forecast_bytes'],200)

    def test_unreviewed_bulk_storage_gate_admits_nothing(self):
        with patch.object(production,'adaptive_reservation',return_value=0):
            allowed,evidence=production.storage_gate({},dict(free_bytes=10**15,free_files=10**6),bulk=True)
        self.assertFalse(allowed)

    def test_large_forecast_needs_an_explicit_frozen_ceiling(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            (root/'config.json').write_text('{}');(root/'card.json').write_text('{}')
            gate=dict(complete=True,all_seven_mass_GEN_audits_passed=True,
                model_card_sha256=production.durable.sha(root/'card.json'),
                worklist_sha256=production.durable.sha(root/'config.json'),
                total_meson_forecast_bytes=80000000000,
                per_batch_forecast_bytes=dict(a=80000000000))
            (root/'gate.json').write_text(json.dumps(gate))
            config=dict(directory=str(root),model_card=str(root/'card.json'),
                bulk_storage_gate=str(root/'gate.json'),minimum_eos_free_bytes=90000000000,
                minimum_eos_free_files=20000,batches=[dict(batch='a')])
            with patch.object(production,'adaptive_reservation',return_value=0):
                with self.assertRaisesRegex(ValueError,'Bulk storage/physics gate'):
                    production.storage_gate(config,dict(free_bytes=200000000000,free_files=100000),bulk=True)
                config['maximum_meson_forecast_bytes']=100000000000
                allowed,evidence=production.storage_gate(config,
                    dict(free_bytes=200000000000,free_files=100000),bulk=True)
            self.assertTrue(allowed)
            self.assertEqual(evidence['meson_forecast_bytes'],80000000000)

    def test_bulk_waits_for_both_published_canaries(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            batches=[dict(batch='a',operational_canary=True),dict(batch='b',operational_canary=True),
                     dict(batch='c',operational_canary=False)]
            for row in batches:
                (root/'batches'/row['batch']).mkdir(parents=True)
            config=dict(directory=str(root),batches=batches,simultaneous_sources=2)
            state=dict(batches=dict(a=dict(published={'events':20}),b=dict(published={'events':21})))
            # Once both canaries are published, a missing explicit bulk gate
            # still prevents any native submission.
            with patch.object(production.durable,'quota',return_value=dict(free_bytes=10**15,free_files=10**6)), \
                 patch.object(production,'storage_gate',return_value=(False,{})), \
                 patch.object(production.durable,'submission') as submit:
                result=production.step(config,state)
            submit.assert_not_called()
            self.assertTrue(result['canary_published'])

    def test_one_verified_source_failure_does_not_block_an_independent_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            batches=[dict(batch='failed',operational_canary=False),
                     dict(batch='next',operational_canary=False)]
            failed=root/'batches/failed'
            failed.mkdir(parents=True)
            (failed/'submission.json').write_text('{}')
            config=dict(directory=str(root),batches=batches,simultaneous_sources=2)
            receipt=dict(cluster=123,task=str(failed))
            native=[dict(ClusterId=123,ProcId=0,Iwd=str(failed),JobStatus=5,ExitCode=1)]
            with patch.object(production,'existing_receipt',return_value=receipt), \
                 patch.object(production.durable,'jobs',return_value=native), \
                 patch.object(production.durable,'quota',return_value={}), \
                 patch.object(production,'storage_gate',return_value=(True,{})), \
                 patch.object(production.durable,'submission',return_value=dict(cluster=456)) as submit, \
                 patch.object(production,'handoff') as handoff:
                state=production.step(config,{})
            self.assertEqual(state['batches']['failed']['stage'],'GEN_failed')
            self.assertEqual(state['failed_batches']['failed'][0]['native_jobs'],native)
            self.assertEqual(state['batches']['next']['source_cluster'],456)
            self.assertFalse(state['complete'])
            submit.assert_called_once()
            handoff.assert_not_called()

    def test_scheduler_failure_still_stops_the_poll_instead_of_quarantining(self):
        with patch.object(production,'native_status',side_effect=RuntimeError('unknown schedd')):
            with self.assertRaisesRegex(RuntimeError,'unknown schedd'):
                production.batch_job_status({}, {},dict(batch='a'),{},dict(cluster=123),'GEN')

    def test_preparation_runs_as_an_independent_worker_instead_of_blocking_the_poll(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            batch=dict(batch='a',operational_canary=True)
            directory=root/'batches/a';directory.mkdir(parents=True)
            (directory/'submission.json').write_text('{}')
            (directory/'source_status.json').write_text(json.dumps(dict(complete=True)))
            config=dict(directory=str(root),batches=[batch],simultaneous_sources=1,
                        simultaneous_detector_points=1,parallel_handoffs=True)
            with patch.object(production,'existing_receipt',return_value=dict(cluster=123)), \
                 patch.object(production,'native_status',return_value='complete'), \
                 patch.object(production.durable,'quota',return_value={}), \
                 patch.object(production,'storage_gate',return_value=(True,{})), \
                 patch.object(production,'parallel_handoff',return_value=('active',None)) as submit, \
                 patch.object(production,'handoff') as synchronous:
                state=production.step(config,{})
            synchronous.assert_not_called()
            submit.assert_called_once()
            self.assertFalse(state['complete'])

    def test_worker_budget_counts_later_admissions_before_an_earlier_ready_batch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            batches=[dict(batch=name, operational_canary=True, events_per_job=5)
                     for name in ('waiting', 'already_admitted')]
            for batch in batches:
                directory=root/'batches'/batch['batch']
                directory.mkdir(parents=True)
                (directory/'submission.json').write_text('{}')
                (directory/'source_status.json').write_text(json.dumps(dict(complete=True, events=10)))
            (root/'batches/already_admitted/production').mkdir()
            (root/'batches/already_admitted/production/submission.json').write_text('{}')
            config=dict(directory=str(root),batches=batches,simultaneous_sources=2,
                simultaneous_detector_points=256,maximum_active_detector_workers=4,
                detector_workers_per_point=100)
            state={}
            with patch.object(production,'existing_receipt',return_value=dict(cluster=123)), \
                 patch.object(production,'native_status',return_value='complete'), \
                 patch.object(production.durable,'quota',return_value={}), \
                 patch.object(production,'storage_gate',return_value=(True,{})), \
                 patch.object(production,'handoff',return_value=dict(cluster=456)) as handoff, \
                 patch.object(production,'publish_batch',return_value=None):
                result=production.step(config,state)
            self.assertEqual([call.args[1]['batch'] for call in handoff.call_args_list], ['already_admitted'])
            self.assertEqual(result['batches']['waiting']['stage'], 'waiting_for_detector_capacity')
            self.assertTrue(result['batches']['already_admitted']['detector_native_complete'])
            self.assertEqual(production.detector_reservations(config,result['batches']), {})

    def test_worker_budget_keeps_failed_admissions_and_uses_actual_gen_count(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); directory=root/'batches/a'
            (directory/'handoff').mkdir(parents=True)
            (directory/'handoff/submission_intent.json').write_text('{}')
            (directory/'source_status.json').write_text(json.dumps(dict(complete=True,events=23)))
            batch=dict(batch='a',events_per_job=10,source_collisions=50000)
            config=dict(directory=str(root),batches=[batch],maximum_active_detector_workers=2400,
                        detector_workers_per_point=100)
            entries=dict(a=dict(stage='handoff_failed'))
            self.assertEqual(production.detector_reservations(config,entries), {'a':5})
            entries['a']['detector_native_complete']=True
            self.assertEqual(production.detector_reservations(config,entries), {})
            self.assertIsNone(production.detector_reservations({}, {}))

    def test_handoff_submit_freezes_credentials_persistent_directory_and_failure_hold(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            batch=dict(batch='a');(root/'batches/a').mkdir(parents=True)
            config=dict(directory=str(root),tools='/frozen/tools')
            files=production.prepare_handoff_worker(config,batch)
            self.assertEqual(len(files),2)
            text=(root/'batches/a/handoff/worker.sub.in').read_text()
            for required in ('initialdir = '+str(root/'batches/a/handoff'),
                             '+SendCredential = True','arguments = SEAL_DIGEST',
                             'periodic_release = False','on_exit_hold ='):
                self.assertIn(required,text)
            wrapper=(root/'batches/a/handoff/run.sh').read_text()
            self.assertIn('--action handoff --batch a',wrapper)
            self.assertIn('unset PYTHONPATH',wrapper)

    def test_preparation_success_without_rates_cannot_start_publication(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);directory=root/'batches/a/handoff';directory.mkdir(parents=True)
            (directory/'submission.json').write_text('{}')
            with patch.object(production,'existing_receipt',return_value=dict(cluster=123)), \
                 patch.object(production,'batch_job_status',return_value='complete'):
                with self.assertRaisesRegex(RuntimeError,'complete rate export'):
                    production.parallel_handoff(dict(directory=str(root)),{},dict(batch='a'),{},None)


class PublicationSafety(unittest.TestCase):
    def publication_floor_fixture(self, root):
        batch = dict(batch='bulk', point='m0p3_prompt', operational_canary=False, mass_gev=.3,
            epsilon=.001, calibration_mean_beta_gamma=500., source_collisions=10000,
            campaign='new-bulk', final_output_base='/eos/synthetic/new-bulk')
        directory = root/'batches/bulk'
        (directory/'production').mkdir(parents=True)
        (directory/'gen').mkdir()
        (directory/'production/plan.json').write_text(json.dumps(dict(campaign=batch['campaign'],
            points=[dict(final_output_base=batch['final_output_base'])])))
        (directory/'production/seal.json').write_text('{}')
        (directory/'rates.json').write_text('{}')
        (directory/'gen/contract.json').write_text(json.dumps(dict(physical_width_table=dict(ctau_mm=.01),
            mean_lab_flight_reference_estimate_m=.005, mean_beta_gamma_reference=500.,
            mean_beta_gamma_reference_standard_error=1., requested_events=999999)))
        config = dict(directory=str(root), batches=[batch], production_mechanism='meson-radiative',
            publish_canary_aliases=False, calibration_authority=dict(sha256='a'*64),
            eos_base='/eos/user/j/jniedzie/shift_cmssw',
            minimum_histogram_events_before_latest=dict(m0p3_prompt=20),
            allowed_previous_aliases=dict(m0p3_prompt=['old-ready']))
        return config, batch, directory

    def test_small_cumulative_histogram_is_ready_without_replacing_larger_latest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, batch, directory = self.publication_floor_fixture(root)
            primitive = dict(campaign='new-bulk', events=9)
            cumulative = dict(campaign='new-cumulative', events=19, sources_sha256='source-signature',
                              source_plans=['one', 'two'])
            state = dict(aggregates=dict(m0p3_prompt=dict(pending=True)))
            with patch('watch_dark_photon_production.EosRpc') as transport, \
                 patch('watch_dark_photon_production.completed_histogram', return_value=primitive), \
                 patch.object(production, 'cumulative_result', return_value=cumulative), \
                 patch('export_dark_photon_cross_sections.publish_file_rpc') as publish, \
                 patch.object(production, 'prior_publication_ready') as prior, \
                 patch.object(production, 'common_sample_anchor') as anchor:
                result = production.publish_batch(config, state, batch)
            self.assertTrue(result['histogram_ready'])
            self.assertFalse(result['alias_published'])
            self.assertEqual(result['latest_promotion'], dict(minimum_events=20,
                validated_histogram_events=19, eligible=False))
            self.assertEqual(state['published']['m0p3_prompt'], result)
            self.assertEqual(state['aggregates'], {})
            self.assertEqual(json.loads((directory/'publication.json').read_text()), result)
            prior.assert_not_called()
            anchor.assert_not_called()
            transport.return_value.alias_target.assert_not_called()
            transport.return_value.publish_alias.assert_not_called()
            self.assertEqual(publish.call_count, 1)
            self.assertEqual(publish.call_args.args[1], batch['final_output_base']+'/sample_parameters.json')

    def test_cumulative_histogram_at_floor_runs_normal_ownership_checks_and_promotes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, batch, _ = self.publication_floor_fixture(root)
            cumulative = dict(campaign='new-cumulative', events=20, sources_sha256='source-signature',
                              source_plans=['one', 'two'])
            state = {}
            with patch('watch_dark_photon_production.EosRpc') as transport, \
                 patch('watch_dark_photon_production.completed_histogram', return_value=dict(events=9)), \
                 patch.object(production, 'cumulative_result', return_value=cumulative), \
                 patch('export_dark_photon_cross_sections.publish_file_rpc'), \
                 patch.object(production, 'prior_publication_ready', return_value=(True,
                     dict(allowed_previous_campaigns=['old-ready']))) as prior, \
                 patch.object(production, 'common_sample_anchor', return_value=dict(retained_existing=True)) as anchor:
                transport.return_value.alias_target.return_value = 'old-ready'
                result = production.publish_batch(config, state, batch)
            self.assertTrue(result['alias_published'])
            self.assertTrue(result['latest_promotion']['eligible'])
            prior.assert_called_once()
            anchor.assert_called_once()
            self.assertEqual(transport.return_value.publish_alias.call_args.args[1], 'new-cumulative')

    def test_histogram_floor_requires_validated_counts_and_canaries_remain_private(self):
        batch = dict(point='p')
        self.assertIsNone(production.histogram_promotion_floor({}, batch, {}))
        for floor in (True, -1, 1.2, '20'):
            with self.subTest(floor=floor), self.assertRaises(ValueError):
                production.histogram_promotion_floor(dict(batches=[batch],
                    minimum_histogram_events_before_latest=dict(p=floor)), batch, dict(events=20))
        for count in (None, True, -1, 1.2):
            with self.subTest(count=count), self.assertRaises(ValueError):
                production.histogram_promotion_floor(dict(batches=[batch],
                    minimum_histogram_events_before_latest=dict(p=20)), batch, dict(events=count))
        with self.assertRaises(ValueError):
            production.histogram_promotion_floor(dict(batches=[batch],
                minimum_histogram_events_before_latest=dict(unknown=20)), batch, dict(events=20))
        with tempfile.TemporaryDirectory() as temporary:
            config, batch, _ = self.publication_floor_fixture(Path(temporary))
            batch['operational_canary'] = True
            with patch('watch_dark_photon_production.EosRpc') as transport, \
                 patch('watch_dark_photon_production.completed_histogram', return_value=dict(campaign='private', events=1)), \
                 patch.object(production, 'cumulative_result', side_effect=lambda c,s,b,t,r:r), \
                 patch('export_dark_photon_cross_sections.publish_file_rpc'), \
                 patch.object(production, 'histogram_promotion_floor') as floor:
                result = production.publish_batch(config, {}, batch)
            self.assertFalse(result['alias_published'])
            floor.assert_not_called()
            transport.return_value.publish_alias.assert_not_called()

    def test_prior_cumulative_alias_requires_unchanged_receipt_and_subset_exposure(self):
        from watch_dark_photon_production import completed_histogram
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            point = 'm0p3_prompt'
            directory = root/'aggregates'/point/'v0001'
            (directory/'completed_points').mkdir(parents=True)
            output = root/'eos'
            histogram = output/'histograms_merged/ntuple_0.root'
            row = dict(point=point, events=20, final_output_base=str(output))
            (directory/'plan.json').write_text(json.dumps(dict(campaign='cumulative-v0001', points=[row])))
            (directory/'seal.json').write_text('{}')
            source = dict(directory=directory, campaign='cumulative-v0001',
                plan_sha256=production.durable.sha(directory/'plan.json'),
                seal_sha256=production.durable.sha(directory/'seal.json'))
            report = dict(complete=True, point=point, events=20, final_output_base=str(output),
                plan_sha256=source['plan_sha256'], seal_sha256=source['seal_sha256'],
                score_histograms=dict(marker=dict(complete=True,
                    published=dict(histograms=dict(path=str(histogram), bytes=123, sha256='a'*64)))))
            path = directory/'completed_points'/(point+'.json')
            path.write_text(json.dumps(report))
            class Transport:
                def root_files(self, where): return [histogram]
                def verify_file(self, where, size, digest):
                    self.checked = (where, size, digest)
            transport = Transport()
            previous = completed_histogram(dict(source=source, row=row, output=output), transport)
            previous.update(source_plans=['one', 'two'])
            state = dict(published={point: previous})
            result = dict(campaign='cumulative-v0002', events=30, source_plans=['one', 'two', 'three'])
            config, batch = dict(directory=str(root)), dict(point=point)
            self.assertEqual(production.prior_cumulative_campaign(config, state, batch, result, transport),
                             'cumulative-v0001')
            self.assertEqual(transport.checked, (histogram, 123, 'a'*64))
            for change in (dict(source_plans=['one']), dict(events=19)):
                with self.subTest(change=change), self.assertRaises(ValueError):
                    production.prior_cumulative_campaign(config, state, batch, dict(result, **change), transport)
            report['events'] = 21
            path.write_text(json.dumps(report))
            with self.assertRaisesRegex(ValueError, 'Previous cumulative'):
                production.prior_cumulative_campaign(config, state, batch, result, transport)

    def previous(self, root, complete=False, age=0):
        legacy = root/'legacy'
        legacy.mkdir()
        batches = [dict(batch='canary',point='m0p3_prompt'),dict(batch='bulk',point='m0p3_prompt'),
                   dict(batch='other',point='m0p9_prompt')]
        config = dict(schema='shift-dp-meson-production-v1',directory=str(legacy),
            production_mechanism='eta-etaprime-radiative',batches=batches,
            calibration_authority=dict(sha256='old-calibration'))
        authority = UnifiedSource().authority(root,'previous.json',config)
        entries = {}
        for row in batches:
            directory = legacy/'batches'/row['batch']/'production/completed_points'
            directory.mkdir(parents=True)
            campaign = 'legacy-'+row['batch']
            prefix = '/eos/synthetic/darkphoton_'+row['point']+'/'+campaign
            histogram = prefix+'/histograms_merged/ntuple_0.root'
            report = dict(point=row['point'],complete=True,events=20,
                final_output_base=prefix,
                score_histograms=dict(marker=dict(complete=True,published=dict(histograms=dict(
                    path=histogram,sha256='a'*64)))))
            path = directory/(row['point']+'.json')
            path.write_text(json.dumps(report))
            entries[row['batch']] = dict(published=dict(batch=row['batch'],campaign=campaign,events=20,
                point_receipt_path=str(path),point_receipt_sha256=production.durable.sha(path),
                histogram_path=histogram,histogram_sha256='a'*64))
        state = dict(complete=complete,checked_utc=(datetime.now(timezone.utc)-timedelta(seconds=age)).isoformat(),
                     batches=entries)
        (legacy/'state.json').write_text(json.dumps(state))
        controller = root/'old_controller'
        controller.mkdir()
        (controller/'controller.sub').write_text('reviewed descriptor')
        receipt = dict(cluster=123,task=str(controller),submit_sha256=production.durable.sha(controller/'controller.sub'))
        authority['controller_receipt'] = UnifiedSource().authority(root,'old_controller_receipt.json',receipt)
        return dict(production_mechanism='meson-radiative',prior_meson_publication=authority), state, legacy

    def test_all_prior_batches_at_this_point_include_the_canary(self):
        with tempfile.TemporaryDirectory() as temporary:
            config,state,legacy = self.previous(Path(temporary))
            state['batches']['other'] = {}
            (legacy/'state.json').write_text(json.dumps(state))
            batch = dict(point='m0p3_prompt')
            ready,evidence = production.prior_publication_ready(config,batch)
            self.assertTrue(ready)
            self.assertEqual(len(evidence['verified_batches']),2)
            self.assertEqual(evidence['allowed_previous_campaigns'],['legacy-canary','legacy-bulk'])
            state['batches']['canary'] = {}
            (legacy/'state.json').write_text(json.dumps(state))
            ready,evidence = production.prior_publication_ready(config,batch)
            self.assertFalse(ready)
            self.assertEqual(evidence['pending_batch'],'canary')

    def test_stale_partial_or_changed_receipt_never_releases_prior_ownership(self):
        for change in ('stale','changed','wrong_point','error'):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as temporary:
                config,state,legacy = self.previous(Path(temporary),age=1300 if change=='stale' else 0)
                published = state['batches']['canary']['published']
                path = Path(published['point_receipt_path'])
                if change=='changed':
                    path.write_text(path.read_text()+' ')
                if change=='wrong_point':
                    report = json.loads(path.read_text()); report['point']='m0p9_prompt'
                    path.write_text(json.dumps(report)); published['point_receipt_sha256']=production.durable.sha(path)
                if change=='error':
                    state['error']='old controller failed'
                (legacy/'state.json').write_text(json.dumps(state))
                self.assertFalse(production.prior_publication_ready(config,dict(point='m0p3_prompt'))[0])

    def test_stale_globally_complete_state_requires_exact_closed_controller(self):
        with tempfile.TemporaryDirectory() as temporary:
            config,state,legacy = self.previous(Path(temporary),complete=True,age=4000)
            with patch.object(production.durable,'jobs',return_value=[]), \
                 patch.object(production,'native_status',return_value='complete') as native:
                ready,evidence=production.prior_publication_ready(config,dict(point='m0p3_prompt'))
            self.assertTrue(ready)
            self.assertTrue(evidence['terminal_closure_verified'])
            self.assertEqual(len(evidence['verified_batches']),3)
            native.assert_called_once()
            with patch.object(production.durable,'jobs',return_value=[dict(ClusterId=123)]), \
                 patch.object(production,'native_status') as native:
                self.assertFalse(production.prior_publication_ready(config,dict(point='m0p3_prompt'))[0])
                native.assert_not_called()
            state['batches']['other']={}
            (legacy/'state.json').write_text(json.dumps(state))
            with patch.object(production.durable,'jobs') as query:
                self.assertFalse(production.prior_publication_ready(config,dict(point='m0p3_prompt'))[0])
                query.assert_not_called()

    def test_prior_publication_follows_only_a_sealed_operational_controller_replacement(self):
        for change in ('none', 'wrong_submission', 'changed_physics', 'missing_submission'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                config, state, legacy = self.previous(root, complete=True, age=4000)
                old_path = Path(config['prior_meson_publication']['path'])
                old = json.loads(old_path.read_text())
                old['tools'] = str(legacy/'tools')
                old_path.write_text(json.dumps(old))
                config['prior_meson_publication']['sha256'] = production.durable.sha(old_path)
                controller = root/'replacement_controller'
                controller.mkdir()
                replacement_config = dict(old, tools=str(controller/'tools'), physics_tools=old['tools'])
                if change == 'changed_physics':
                    replacement_config['batches'] = old['batches'][:1]
                config_pin = UnifiedSource().authority(controller, 'config.json', replacement_config)
                seal_pin = UnifiedSource().authority(controller, 'seal.json',
                    dict(files={config_pin['path']:config_pin['sha256']}))
                submit = controller/'controller.sub'
                submit.write_text('reviewed replacement descriptor')
                submit_pin = dict(path=str(submit), sha256=production.durable.sha(submit))
                config['prior_meson_controller_replacement'] = dict(
                    config=config_pin, seal=seal_pin, submit=submit_pin)
                if change != 'missing_submission':
                    receipt = dict(cluster=124, task=str(controller),
                        submit_sha256='b'*64 if change == 'wrong_submission' else submit_pin['sha256'])
                    (controller/'submission.json').write_text(json.dumps(receipt))
                with patch.object(production.durable, 'jobs', return_value=[]), \
                     patch.object(production, 'native_status', return_value='complete') as native:
                    ready, evidence = production.prior_publication_ready(config, dict(point='m0p3_prompt'))
                self.assertEqual(ready, change == 'none')
                if ready:
                    self.assertEqual(evidence['prior_controller_cluster'], 124)
                    self.assertEqual(native.call_args.args[1]['cluster'], 124)
                else:
                    native.assert_not_called()

    def test_existing_global_anchor_can_keep_legacy_mechanism_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            config,_,_=self.previous(root)
            config.update(eos_base='/eos/user/j/jniedzie/shift_cmssw',calibration_authority=dict(sha256='new-calibration'))
            sample=dict(schema='shift-dark-photon-sample-v1',point='m0p3_prompt',original_point='m0p3_prompt',
                naming='mass-and-mean-lab-flight',selector_kind='mean_lab_flight',mass_gev=.3,epsilon=.001,
                sampling_epsilon=.001,target_mean_lab_flight_m=None,mean_lab_flight_m=None,
                calibration_mean_beta_gamma=500.,mean_lab_flight_is_estimate=False,
                proper_ctau_mm=.01,reference_mean_lab_flight_m=.005,
                production_mechanism='meson-radiative',calibration_sha256='new-calibration')
            old=dict(sample,production_mechanism='eta-etaprime-radiative',calibration_sha256='old-calibration')
            cache=root/'cache';cache.mkdir()
            class Transport:
                host='root://eosuser.cern.ch'
                cache_directory=cache
                def metadata(self,path):return dict(size=len(json.dumps(old).encode()),status='file')
                def command(self,argv):Path(argv[-1]).write_text(json.dumps(old))
            batch=dict(point='m0p3_prompt')
            with patch('export_dark_photon_cross_sections.publish_file_rpc') as publish:
                proof=production.common_sample_anchor(config,batch,sample,root/'sample.json',Transport())
                self.assertTrue(proof['retained_existing'])
                self.assertIn('does not describe',proof['scope'])
                publish.assert_not_called()
                for field,value in [('epsilon',.002),('calibration_mean_beta_gamma',400.),
                                    ('proper_ctau_mm',.02),('target_mean_lab_flight_m',5.)]:
                    original=old[field];old[field]=value
                    with self.subTest(field=field),self.assertRaisesRegex(ValueError,'different physical'):
                        production.common_sample_anchor(config,batch,sample,root/'sample.json',Transport())
                    old[field]=original

    def test_private_canary_publishes_campaign_parameters_without_global_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            batch=dict(batch='canary',point='m0p3_prompt',operational_canary=True,mass_gev=.3,epsilon=.001,
                calibration_mean_beta_gamma=500.,source_collisions=4000,final_output_base='/eos/synthetic/canary')
            directory=root/'batches/canary';(directory/'production').mkdir(parents=True);(directory/'gen').mkdir()
            (directory/'production/plan.json').write_text(json.dumps(dict(campaign='new-canary',
                points=[dict(final_output_base=batch['final_output_base'])])))
            (directory/'production/seal.json').write_text('{}');(directory/'rates.json').write_text('{}')
            (directory/'gen/contract.json').write_text(json.dumps(dict(physical_width_table=dict(ctau_mm=.01),
                mean_lab_flight_reference_estimate_m=.005,mean_beta_gamma_reference=500.,
                mean_beta_gamma_reference_standard_error=1.,requested_events=20)))
            config=dict(directory=str(root),production_mechanism='meson-radiative',publish_canary_aliases=False,
                calibration_authority=dict(sha256='a'*64),eos_base='/eos/user/j/jniedzie/shift_cmssw')
            primitive=dict(campaign='new-canary',events=20)
            with patch('watch_dark_photon_production.EosRpc') as transport, \
                 patch('watch_dark_photon_production.completed_histogram',return_value=primitive), \
                 patch.object(production,'cumulative_result',side_effect=lambda c,s,b,t,r:r), \
                 patch('export_dark_photon_cross_sections.publish_file_rpc') as publish, \
                 patch.object(production,'prior_publication_ready') as prior:
                result=production.publish_batch(config,{},batch)
            self.assertFalse(result['alias_published'])
            self.assertTrue(result['campaign_parameters_published'])
            prior.assert_not_called()
            transport.return_value.publish_alias.assert_not_called()
            self.assertEqual(publish.call_count,1)
            self.assertEqual(publish.call_args.args[1],batch['final_output_base']+'/sample_parameters.json')
            batch['operational_canary']=False
            state={}
            with patch('watch_dark_photon_production.EosRpc'), \
                 patch('watch_dark_photon_production.completed_histogram',return_value=primitive), \
                 patch.object(production,'cumulative_result',side_effect=lambda c,s,b,t,r:r), \
                 patch('export_dark_photon_cross_sections.publish_file_rpc') as publish, \
                 patch.object(production,'prior_publication_ready',return_value=(False,dict(reason='old pending'))):
                self.assertIsNone(production.publish_batch(config,state,batch))
            publish.assert_not_called()
            self.assertEqual(state['batches']['canary']['stage'],'waiting_for_prior_publication')
            self.assertNotIn('published',state['batches']['canary'])

    def test_actual_signal_count_cannot_silently_exceed_the_bulk_reservation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            batch=dict(batch='bulk',point='m0p3_prompt',operational_canary=False)
            directory=root/'batches/bulk';(directory/'gen').mkdir(parents=True)
            model=root/'model.json';model.write_text('{}');(root/'config.json').write_text('{}')
            ledger=dict(encoding='gzip',bytes=1000,sha256='a'*64,readback_sha256='a'*64)
            (directory/'gen/meson_ledger_publication.json').write_text(json.dumps(ledger))
            gate=root/'bulk_gate.json'
            amount=20*326000+1000+5000000
            gate.write_text(json.dumps(dict(complete=True,all_requested_mass_GEN_audits_passed=True,
                worklist_sha256=production.durable.sha(root/'config.json'),model_card_sha256=production.durable.sha(model),
                total_meson_forecast_bytes=amount,per_batch_forecast_bytes=dict(bulk=amount))))
            config=dict(directory=str(root),production_mechanism='meson-radiative',batches=[batch],
                bulk_storage_gate=str(gate),model_card=str(model))
            result=production.actual_source_storage(config,batch,dict(events=20),dict(ledger_publication=ledger))
            self.assertTrue(result['passed'])
            (directory/'actual_source_storage.json').unlink()
            with self.assertRaisesRegex(ValueError,'GEN is preserved'):
                production.actual_source_storage(config,batch,dict(events=21),dict(ledger_publication=ledger))
            self.assertFalse(json.loads((directory/'actual_source_storage.json').read_text())['passed'])
            self.assertTrue((directory/'gen/meson_ledger_publication.json').is_file())

    def test_legacy_forecast_does_not_inherit_the_main_publication_owner(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            config,state,legacy=self.previous(root)
            forecast=UnifiedSource().authority(root,'forecast.json',dict(complete=True,
                worklist_sha256=config['prior_meson_publication']['sha256'],
                total_meson_forecast_bytes=300,per_batch_forecast_bytes=dict(canary=100,bulk=100,other=100)))
            config['additional_reserved_meson_forecasts']=[dict(name='old_eta',forecast=forecast,
                completion_authority=config['prior_meson_publication'])]
            state.update(complete=True,checked_utc=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat())
            (legacy/'state.json').write_text(json.dumps(state))
            with patch.object(production.durable,'jobs',return_value=[]), \
                 patch.object(production,'native_status',return_value='complete'):
                # A stale terminal peer needs its own explicit current owner.
                self.assertEqual(production.additional_reservations(config)[0],300)
                state['batches']['other']={}
                (legacy/'state.json').write_text(json.dumps(state))
                self.assertEqual(production.additional_reservations(config)[0],300)

    def test_supplement_histogram_without_source_closure_keeps_full_reservation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            peer=root/'supplement';peer.mkdir()
            batch=dict(point='m2_prompt',events=20,seed=123,run_number=456)
            worklist=UnifiedSource().authority(root,'worklist.json',dict(total_additional_events=20,
                waves=[dict(wave_index=0,batches=[batch])]))
            plan=peer/'waves/w000/production/plan.json';plan.parent.mkdir(parents=True)
            plan.write_text(json.dumps(dict(points=[batch])))
            histogram='/eos/synthetic/histograms_merged/ntuple_0.root'
            report=peer/'aggregates/m2_prompt/completed_points/m2_prompt.json';report.parent.mkdir(parents=True)
            report.write_text(json.dumps(dict(point='m2_prompt',events=20,complete=True,
                score_histograms=dict(marker=dict(complete=True,published=dict(histograms=dict(
                    path=histogram,sha256='a'*64)))))))
            state=dict(complete=True,published=dict(m2_prompt=dict(events=20,source_plans=[str(plan)],
                point_receipt_path=str(report),point_receipt_sha256=production.durable.sha(report),
                histogram_path=histogram,histogram_sha256='a'*64)))
            (peer/'state.json').write_text(json.dumps(state))
            controller=UnifiedSource().authority(peer,'config.json',dict(directory=str(peer),worklist=worklist['path']))
            (peer/'controller.sub').write_text('reviewed submit')
            receipt=UnifiedSource().authority(root,'receipt.json',dict(cluster=123,task=str(peer),
                submit_sha256=production.durable.sha(peer/'controller.sub')))
            config=dict(additional_reserved_worklists=[dict(name='supplement',worklist=worklist,bytes_per_event=326000,
                completion_authority=dict(controller_config=controller,current_controller_receipt=receipt))])
            with patch.object(production.durable,'jobs',return_value=[]), \
                 patch.object(production,'native_status',return_value='complete'):
                self.assertEqual(production.additional_reservations(config)[0],20*326000)
                state['published']['m2_prompt']['source_plans']=[]
                (peer/'state.json').write_text(json.dumps(state))
                self.assertEqual(production.additional_reservations(config)[0],20*326000)
                state['published']['m2_prompt']['source_plans']=[str(plan)]
                (peer/'state.json').write_text(json.dumps(state))
                plan.write_text(json.dumps(dict(points=[dict(batch,seed=124)])))
                self.assertEqual(production.additional_reservations(config)[0],20*326000)

    def terminal_supplement(self, root):
        def write(path, value):
            path.parent.mkdir(parents=True, exist_ok=True)
            return UnifiedSource().authority(path.parent, path.name, value)

        peer = root/'supplement'
        batch = dict(point='m2_prompt', events=20, seed=123, run_number=456)
        worklist = write(root/'worklist.json', dict(total_additional_events=20,
            waves=[dict(wave_index=0, batches=[batch])]))
        production_root = peer/'waves/w000/production'
        plan = write(production_root/'plan.json', dict(points=[batch]))
        seal = write(production_root/'seal.json', dict(files={plan['path']:plan['sha256']}))
        write(production_root/'completed_points/m2_prompt.json', dict(complete=True, point='m2_prompt',
            events=20, plan_sha256=plan['sha256'], seal_sha256=seal['sha256']))
        histogram = dict(path='/eos/synthetic/histograms_merged/ntuple_0.root', sha256='a'*64)
        report = write(peer/'aggregates/m2_prompt/completed_points/m2_prompt.json', dict(
            point='m2_prompt', events=20, complete=True,
            score_histograms=dict(marker=dict(complete=True, published=dict(histograms=histogram)))))
        checked = (datetime.now(timezone.utc)-timedelta(days=1)).isoformat()
        write(peer/'state.json', dict(finished=True, complete=True, checked_utc=checked,
            aggregates={}, unpublished_points=[], failures={},
            waves={'0':dict(terminal=True, successful=True, exit_code=0)},
            published=dict(m2_prompt=dict(events=20, source_plans=[plan['path']], published_utc=checked,
                point_receipt_path=report['path'], point_receipt_sha256=report['sha256'],
                histogram_path=histogram['path'], histogram_sha256=histogram['sha256']))))
        operations = root/'new_controller'
        controller = write(operations/'config.json', dict(directory=str(peer), worklist=worklist['path']))
        submit = operations/'controller.sub'
        submit.write_text('reviewed new submit')
        write(operations/'submission.json', dict(cluster=124, task=str(operations),
            submit_sha256=production.durable.sha(submit)))
        authority = dict(controller_config=controller, current_controller_submit=dict(
            path=str(submit), sha256=production.durable.sha(submit)))
        return peer, operations, authority, worklist

    def test_supplement_replacement_needs_its_exact_new_native_submission(self):
        for change in ('none', 'receipt_pin', 'wrong_task', 'wrong_submit', 'missing_submission',
                       'changed_worklist', 'live', 'live_worker', 'live_aggregate', 'unknown_queue', 'failed_native'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                peer, operations, authority, worklist = self.terminal_supplement(root)
                receipt_path = operations/'submission.json'
                receipt = production.read(receipt_path)
                if change == 'receipt_pin':
                    authority['current_controller_receipt'] = dict(path=str(receipt_path),
                        sha256=production.durable.sha(receipt_path))
                    del authority['current_controller_submit']
                elif change == 'missing_submission':
                    receipt_path.unlink()
                elif change in ('wrong_task', 'wrong_submit'):
                    if change == 'wrong_task':
                        receipt['task'] = str(peer)
                    else:
                        receipt['submit_sha256'] = 'b'*64
                    receipt_path.write_text(json.dumps(receipt))
                elif change == 'changed_worklist':
                    controller = production.read(operations/'config.json')
                    controller['worklist'] = '/wrong/worklist.json'
                    authority['controller_config'] = UnifiedSource().authority(operations, 'config.json', controller)
                queue = [dict(ClusterId=124)] if change == 'live' else []
                if change == 'live_worker':
                    queue = [dict(ClusterId=125, JobStatus=5, Iwd=str(peer/'waves/w000/production'))]
                elif change == 'live_aggregate':
                    queue = [dict(ClusterId=126, JobStatus=2, Iwd=str(peer/'aggregates/m2_prompt'))]
                error = RuntimeError('Unknown scheduler state') if change == 'unknown_queue' else None
                config = dict(additional_reserved_worklists=[dict(name='supplement', worklist=worklist,
                    bytes_per_event=326000, completion_authority=authority)])
                native_error = RuntimeError('Native closure failed') if change == 'failed_native' else None
                with patch.object(production.durable, 'jobs', return_value=queue, side_effect=error) as queue_query, \
                     patch.object(production, 'native_status', return_value='complete', side_effect=native_error) as native:
                    completed = production.terminal_peer_completion({}, authority, worklist)
                    self.assertEqual(production.additional_reservations(config)[0],
                                     0 if completed else 20*326000)
                self.assertEqual(completed, change in ('none', 'receipt_pin'))
                if completed or change in ('live', 'live_worker', 'live_aggregate', 'unknown_queue', 'failed_native'):
                    self.assertIn('^'+str(peer)+'/(waves|aggregates)/', queue_query.call_args.args[1])
                if completed or change == 'failed_native':
                    self.assertEqual(native.call_args.args[1]['cluster'], 124)
                else:
                    native.assert_not_called()

    def test_supplement_requires_full_source_and_publication_closure(self):
        for change in ('missing_seal', 'missing_primitive', 'changed_primitive', 'changed_seal',
                       'unfinished_wave', 'unsuccessful_wave', 'nonzero_exit', 'missing_wave', 'extra_wave',
                       'unfinished_state', 'pending_aggregate', 'unpublished', 'missing_unpublished',
                       'changed_histogram', 'changed_state'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                peer, operations, authority, worklist = self.terminal_supplement(root)
                state = production.read(peer/'state.json')
                source = peer/'waves/w000/production'
                if change == 'missing_seal':
                    (source/'seal.json').unlink()
                elif change == 'missing_primitive':
                    (source/'completed_points/m2_prompt.json').unlink()
                elif change == 'changed_primitive':
                    report = production.read(source/'completed_points/m2_prompt.json')
                    report['events'] = 19
                    (source/'completed_points/m2_prompt.json').write_text(json.dumps(report))
                elif change == 'changed_seal':
                    seal = production.read(source/'seal.json')
                    seal['files'][str(source/'plan.json')] = 'b'*64
                    (source/'seal.json').write_text(json.dumps(seal))
                elif change == 'unfinished_wave':
                    state['waves']['0']['terminal'] = False
                elif change == 'unsuccessful_wave':
                    state['waves']['0']['successful'] = False
                elif change == 'nonzero_exit':
                    state['waves']['0']['exit_code'] = 1
                elif change == 'missing_wave':
                    state['waves'] = {}
                elif change == 'extra_wave':
                    state['waves']['1'] = dict(terminal=True, successful=True, exit_code=0)
                elif change == 'unfinished_state':
                    state['finished'] = False
                elif change == 'pending_aggregate':
                    state['aggregates']['m2_prompt'] = dict(cluster=125)
                elif change == 'unpublished':
                    state['unpublished_points'] = ['m2_prompt']
                elif change == 'missing_unpublished':
                    del state['unpublished_points']
                elif change == 'changed_histogram':
                    Path(state['published']['m2_prompt']['point_receipt_path']).write_text('{}')
                if change == 'changed_state':
                    (peer/'state.json').write_text('{}')
                else:
                    (peer/'state.json').write_text(json.dumps(state))
                config = dict(additional_reserved_worklists=[dict(name='supplement', worklist=worklist,
                    bytes_per_event=326000, completion_authority=authority)])
                with patch.object(production.durable, 'jobs', return_value=[]), \
                     patch.object(production, 'native_status', return_value='complete'):
                    self.assertEqual(production.additional_reservations(config)[0], 20*326000)


class NativeHealth(unittest.TestCase):
    def test_completed_source_cache_never_bypasses_fresh_queue_or_source_integrity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            receipt=dict(cluster=123,task=str(root),submit_sha256='original')
            status=root/'source_status.json'
            status.write_text(json.dumps(dict(complete=True,source_collisions=4000,events=23)))
            native=dict(ClusterId=123,ProcId=0,Iwd=str(root),JobStatus=4,ExitBySignal=False,ExitCode=0)
            proof=root/'native_review.json'
            proof.write_text(json.dumps(dict(schema='shift-reviewed-meson-successful-native-seed-v1',
                jobs={'123':production.terminal_record(receipt,native)})))
            config=dict(directory=str(root),reviewed_terminal_native_jobs=dict(path=str(proof),sha256=production.durable.sha(proof)))
            with patch.object(production.durable,'jobs',return_value=[]), \
                 patch.object(production.durable,'run_condor') as scan:
                self.assertEqual(production.native_status(config,receipt),'complete')
            scan.assert_not_called()
            with patch.object(production.durable,'jobs',side_effect=RuntimeError('Unknown scheduler state')):
                with self.assertRaisesRegex(RuntimeError,'Unknown scheduler'):
                    production.native_status(config,receipt)
            with patch.object(production.durable,'jobs',return_value=[dict(native,JobStatus=5)]):
                with self.assertRaisesRegex(RuntimeError,'removed or held'):
                    production.native_status(config,receipt)
            status.write_text(json.dumps(dict(complete=True,source_collisions=23,events=23)))
            with patch.object(production.durable,'jobs',return_value=[]):
                with self.assertRaisesRegex(ValueError,'native population'):
                    production.native_status(config,receipt)

    def test_future_success_is_cached_once_after_native_history_then_still_checks_queue(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            proof=root/'seed.json'
            proof.write_text(json.dumps(dict(schema='shift-reviewed-meson-successful-native-seed-v1',jobs={})))
            config=dict(directory=str(root),reviewed_terminal_native_jobs=dict(path=str(proof),sha256=production.durable.sha(proof)))
            receipt=dict(cluster=123,task=str(root/'future_production'),submit_sha256='original')
            native=dict(ClusterId=123,ProcId=0,Iwd=receipt['task'],JobStatus=4,ExitCode=0,ExitBySignal=False)
            with patch.object(production.durable,'jobs',return_value=[]), \
                 patch.object(production.durable,'scheduler_arguments',return_value=[]), \
                 patch.object(production.durable,'run_condor',return_value=json.dumps([native])) as history:
                self.assertEqual(production.native_status(config,receipt),'complete')
            history.assert_called_once()
            self.assertTrue((root/'native_terminal_cache/123.json').is_file())
            with patch.object(production.durable,'jobs',return_value=[]) as queue, \
                 patch.object(production.durable,'run_condor') as history:
                self.assertEqual(production.native_status(config,receipt),'complete')
            queue.assert_called_once()
            history.assert_not_called()

    def recovery_fixture(self, root):
        production_root=root/'production'
        production_root.mkdir()
        for name in ('plan.json','seal.json'):
            (production_root/name).write_text('{}')
        replacement_root=root/'recovery'
        replacement_root.mkdir()
        descriptor=replacement_root/'pipeline.dag.condor.sub'
        descriptor.write_text('initialdir = '+str(replacement_root)+'\n')
        original=dict(cluster=123,task=str(production_root),submit_sha256='old')
        recovered=dict(cluster=456,task=str(replacement_root),submit_sha256=production.durable.sha(descriptor))
        (replacement_root/'submission.json').write_text(json.dumps(recovered))
        proof=dict(schema='shift-meson-finalizer-recovery-v1',batch='bulk',point='point',
            original_submission=original,original_plan_sha256=production.durable.sha(production_root/'plan.json'),
            original_seal_sha256=production.durable.sha(production_root/'seal.json'),
            allowed_stages=['finalizer','collect'],gen_or_detector_jobs_allowed=False,
            original_manager_exit_code=1,original_finalizer_cluster=124,original_finalizer_exit_code=124,
            recovery_directory=str(replacement_root),
            recovery_files={str(descriptor):production.durable.sha(descriptor)})
        native=dict(ClusterId=123,ProcId=0,Iwd=str(production_root),ExitCode=1,ExitBySignal=False)
        terminal=root/'terminal.json'
        terminal.write_text(json.dumps(dict(queue=[],history={'123':[native],
            '124':[dict(native,ClusterId=124,ExitCode=124)]})))
        proof['terminal_native_review']=dict(path=str(terminal),sha256=production.durable.sha(terminal))
        path=root/'proof.json'
        path.write_text(json.dumps(proof))
        authority=dict(path=str(path),sha256=production.durable.sha(path))
        config=dict(directory=str(root))
        batch=dict(batch='bulk',point='point')
        return config,batch,original,authority,proof,native,recovered

    def test_reviewed_recovery_uses_only_its_exact_submitted_owner(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            config,batch,original,authority,proof,native,recovered=self.recovery_fixture(root)
            with patch.object(production,'batch_directory',return_value=root), \
                 patch.object(production.durable,'jobs',return_value=[]) as jobs, \
                 patch.object(production.durable,'run_condor') as history_scan:
                result=production.reviewed_detector_recovery(config,batch,original,authority)
            self.assertEqual(result,recovered)
            self.assertIn('DAGManJobId == 123',jobs.call_args.args[1])
            self.assertIn('Iwd == ',jobs.call_args.args[1])
            history_scan.assert_not_called()

    def test_recovery_refuses_live_original_children_and_wrong_native_closure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            config,batch,original,authority,proof,native,recovered=self.recovery_fixture(root)
            with patch.object(production,'batch_directory',return_value=root), \
                 patch.object(production.durable,'jobs',return_value=[dict(ClusterId=124)]):
                with self.assertRaisesRegex(RuntimeError,'still live'):
                    production.reviewed_detector_recovery(config,batch,original,authority)
            for row in [dict(native,ExitCode=0),dict(native,Iwd='/wrong'),dict(native,ExitBySignal=True)]:
                terminal=Path(proof['terminal_native_review']['path'])
                record=json.loads(terminal.read_text())
                record['history']['123']=[row]
                terminal.write_text(json.dumps(record))
                proof['terminal_native_review']['sha256']=production.durable.sha(terminal)
                Path(authority['path']).write_text(json.dumps(proof))
                authority['sha256']=production.durable.sha(authority['path'])
                with self.subTest(row=row), \
                     patch.object(production,'batch_directory',return_value=root), \
                     patch.object(production.durable,'jobs',return_value=[]):
                    with self.assertRaisesRegex(RuntimeError,'native history'):
                        production.reviewed_detector_recovery(config,batch,original,authority)

    def test_scheduling_rollover_only_changes_bounded_capacity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            original=dict(directory='/original/production',tools='/original/physics',
                batches=[dict(source_collisions=4000,seed=123)],
                simultaneous_sources=2,simultaneous_detector_points=2,
                minimum_eos_free_bytes=90000000000,bulk_storage_gate='/original/bulk.json')
            candidate=replacement.scheduling_configuration(original,original,7,35)
            path=root/'config.json'
            seal=root/'seal.json'
            def adopt(value):
                path.write_text(json.dumps(value))
                seal.write_text(json.dumps(dict(files={str(path):production.durable.sha(path)})))
                return production.operational_configuration(original,path,production.durable.sha(seal))
            accepted=adopt(candidate)
            self.assertEqual(accepted['batches'],original['batches'])
            self.assertEqual(accepted['simultaneous_detector_points']*100,3500)
            for change in [dict(simultaneous_sources=8),dict(simultaneous_sources=True),
                           dict(simultaneous_detector_points=36),dict(simultaneous_detector_points=0),
                           dict(minimum_eos_free_bytes=1),dict(bulk_storage_gate='/other.json'),
                           dict(unreviewed_option=True)]:
                with self.subTest(change=change):
                    with self.assertRaises(ValueError):
                        adopt(dict(candidate,**change))

    def test_frozen_scheduling_helper_preserves_previous_tools_and_policy(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            original_root=root/'production'
            template=root/'previous'
            output=root/'replacement'
            original_root.mkdir()
            (template/'tools').mkdir(parents=True)
            original=dict(directory=str(original_root),tools=str(original_root/'tools'),batches=[],
                simultaneous_sources=2,simultaneous_detector_points=2)
            (original_root/'config.json').write_text(json.dumps(original))
            (original_root/'seal.json').write_text('{}')
            (template/'config.json').write_text(json.dumps(dict(original,physics_tools=original['tools'])))
            (template/'tools'/'retained.py').write_text('# Existing qualified tool.\n')
            (template/'lcg_controller.sh').write_text('#!/bin/bash\nexec '+str(template)+'/tools/run_dark_photon_meson_production.py\n')
            descriptor='\n'.join(['initialdir = '+str(template),'+SendCredential = True',
                '+MaxRuntime = 604800','on_exit_hold = (ExitCode != 0)',
                'periodic_release = False','arguments = OPERATIONS_SEAL_DIGEST'])
            (template/'controller.sub.in').write_text(descriptor)
            files={str(path):production.durable.sha(path) for path in [template/'config.json',
                template/'tools'/'retained.py',template/'lcg_controller.sh',template/'controller.sub.in']}
            (template/'seal.json').write_text(json.dumps(dict(files=files)))
            old_seal=production.durable.sha(original_root/'seal.json')
            template_seal=production.durable.sha(template/'seal.json')
            (template/'controller.sub').write_text(descriptor.replace('OPERATIONS_SEAL_DIGEST',template_seal))
            with patch.object(production,'verify',return_value=original):
                receipt=replacement.prepare(original_root,old_seal,template,template_seal,output)
            config=production.operational_configuration(original,output/'config.json',receipt['seal_sha256'])
            self.assertEqual(config['tools'],original['tools'])
            self.assertEqual(config['physics_tools'],original['tools'])
            self.assertEqual((output/'tools'/'retained.py').read_text(),(template/'tools'/'retained.py').read_text())
            self.assertNotIn(str(template),(output/'controller.sub').read_text())
            self.assertIn('+SendCredential = True',(output/'controller.sub').read_text())
            self.assertEqual(receipt['simultaneous_sources'],7)
            with patch.object(production,'verify',return_value=original):
                with self.assertRaisesRegex(ValueError,'already exists'):
                    replacement.prepare(original_root,old_seal,template,template_seal,output)

    def test_versioned_controller_preserves_original_physics_and_frozen_settings(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            original=dict(directory='/old/production',tools='/old/physics',batches=[dict(source_collisions=4000)])
            config=dict(original,tools='/new/operations',physics_tools='/old/physics')
            path=root/'config.json'
            path.write_text(json.dumps(config))
            seal=root/'seal.json'
            seal.write_text(json.dumps(dict(files={str(path):production.durable.sha(path)})))
            self.assertEqual(production.operational_configuration(original,path,production.durable.sha(seal)),config)
            config['batches'][0]['source_collisions']=20
            path.write_text(json.dumps(config))
            seal.write_text(json.dumps(dict(files={str(path):production.durable.sha(path)})))
            original['batches']=[dict(source_collisions=4000)]
            with self.assertRaisesRegex(ValueError,'frozen production settings'):
                production.operational_configuration(original,path,production.durable.sha(seal))

    def test_peer_controller_evidence_can_change_without_reducing_any_protected_claim(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = dict(directory='/old/production', tools='/old/physics', batches=[],
                additional_reserved_worklists=[dict(name='mass_gap', bytes_per_event=326000,
                    worklist=dict(path='/original/worklist.json', sha256='a'*64),
                    completion_authority=dict(controller_config='old', current_controller_receipt='old'))])
            candidate = dict(original, physics_tools=original['tools'])
            candidate['additional_reserved_worklists'] = json.loads(json.dumps(original['additional_reserved_worklists']))
            candidate['additional_reserved_worklists'][0]['completion_authority'] = dict(
                controller_config='new', current_controller_receipt='old', current_controller_submit='new')
            def adopt(value):
                path = root/'config.json'
                path.write_text(json.dumps(value))
                seal = root/'seal.json'
                seal.write_text(json.dumps(dict(files={str(path):production.durable.sha(path)})))
                return production.operational_configuration(original, path, production.durable.sha(seal))
            self.assertEqual(adopt(candidate), candidate)
            for change in ('name', 'bytes_per_event', 'worklist', 'remove'):
                changed = json.loads(json.dumps(candidate))
                if change == 'remove':
                    changed['additional_reserved_worklists'] = []
                else:
                    changed['additional_reserved_worklists'][0][change] = dict(
                        name='different', bytes_per_event=1, worklist=dict(path='/other', sha256='b'*64))[change]
                with self.subTest(change=change), self.assertRaisesRegex(ValueError, 'protected peer storage claims'):
                    adopt(changed)

    def test_held_child_prevents_a_false_running_claim(self):
        receipt=dict(cluster=123,task='/persistent/task')
        rows=[dict(ClusterId=123,ProcId=0,Iwd=receipt['task'],JobStatus=2),
              dict(ClusterId=124,ProcId=0,Iwd=receipt['task'],JobStatus=5)]
        with patch.object(production.durable,'jobs',return_value=rows):
            with self.assertRaisesRegex(RuntimeError,'child is removed or held'):
                production.native_status({},receipt)

    def test_exact_native_history_failure_never_permits_resubmission(self):
        receipt=dict(cluster=123,task='/persistent/task')
        row=dict(ClusterId=123,ProcId=0,Iwd=receipt['task'],ExitCode=1)
        with patch.object(production.durable,'jobs',return_value=[]), \
             patch.object(production.durable,'scheduler_arguments',return_value=[]), \
             patch.object(production.durable,'run_condor',return_value=json.dumps([row])) as command:
            with self.assertRaisesRegex(RuntimeError,'no automatic retry'):
                production.native_status({},receipt)
            self.assertIn('123.0',command.call_args.args[0])
            self.assertNotIn('condor_submit',command.call_args.args[0][0])

    def test_one_queue_snapshot_keeps_native_DAG_children_and_isolates_other_batches(self):
        config=dict(directory='/production',batch_native_queries=True)
        receipt=dict(cluster=123,task='/production/batches/a',utc='1970-01-01T00:00:00+00:00')
        rows=[dict(ClusterId=123,ProcId=0,Iwd=receipt['task'],JobStatus=2),
              dict(ClusterId=124,ProcId=0,DAGManJobId=123,Iwd=receipt['task'],JobStatus=5),
              dict(ClusterId=999,ProcId=0,Iwd='/production/batches/b',JobStatus=5)]
        with patch.object(production.durable,'scheduler_arguments',return_value=[]), \
             patch.object(production.durable,'run_condor',return_value=json.dumps(rows)) as command, \
             patch.object(production.durable,'jobs') as query:
            snapshot=production.production_queue_snapshot(config)
            with self.assertRaises(production.MesonJobFailure) as caught:
                production.native_status(config,receipt,snapshot)
            self.assertEqual([row['ClusterId'] for row in caught.exception.jobs],[123,124])
            query.assert_not_called()
            command.assert_called_once()
            self.assertIn('DAGManJobId',command.call_args.args[0][-1])

    def test_new_submission_or_external_recovery_cannot_use_an_earlier_snapshot(self):
        config=dict(directory='/production')
        snapshot=dict(before_epoch=1,rows=[])
        for task,utc in [('/production/batches/a','1970-01-01T00:00:02+00:00'),
                         ('/recovery/batches/a','1970-01-01T00:00:00+00:00')]:
            receipt=dict(cluster=123,task=task,utc=utc)
            row=dict(ClusterId=123,ProcId=0,Iwd=task,JobStatus=2)
            with self.subTest(task=task), \
                 patch.object(production.durable,'jobs',return_value=[row]) as query:
                self.assertEqual(production.native_status(config,receipt,snapshot),'active')
                query.assert_called_once()

    def test_unknown_snapshot_is_not_empty_success(self):
        with patch.object(production.durable,'scheduler_arguments',return_value=[]), \
             patch.object(production.durable,'run_condor',side_effect=RuntimeError('schedd unavailable')):
            with self.assertRaisesRegex(RuntimeError,'schedd unavailable'):
                production.production_queue_snapshot(dict(directory='/production',batch_native_queries=True))


if __name__=='__main__':
    unittest.main()
