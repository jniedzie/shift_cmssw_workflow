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
    def test_heavy_source_mass_scope_comes_from_the_declared_current(self):
        from test_dark_photon_meson_source import CorrelatedCurrentSourceTest
        parent=CorrelatedCurrentSourceTest().quark()
        card=dict(parents=[parent])
        rows=self.rows();rows[1].update(mass_gev=1.5)
        prepared=preparation.validate_batches(rows,[],mechanism='meson-radiative',model_card=card)
        self.assertEqual(prepared[1]['mass_gev'],1.5)
        with self.assertRaises(ValueError):
            preparation.validate_batches(rows,[],mechanism='meson-radiative')
        rows[1]['mass_gev']=parent['mass_gev']
        with self.assertRaises(ValueError):
            preparation.validate_batches(rows,[],mechanism='meson-radiative',model_card=card)

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

    def test_source_closure_never_cancels_attempted_batches_or_unknown_live_owners(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            peer = root/'peer'
            owner = root/'closed_controller'
            peer.mkdir(); owner.mkdir()
            config = dict(directory=str(peer), batches=[dict(batch='unused'), dict(batch='running')])
            expected = self.authority(peer, 'config.json', config)
            owner_pin = self.authority(owner, 'config.json', dict(directory=str(peer), source_admission_closed=True))
            (owner/'controller.sub').write_text('sealed closed controller')
            submission = self.authority(owner, 'submission.json', dict(cluster=12, task=str(owner),
                submit_sha256=production.durable.sha(owner/'controller.sub')))
            proof = dict(schema='shift-reviewed-meson-source-closure-v1', scope='future-unsubmitted-sources-only',
                peer_configuration=expected, controller_configuration=owner_pin,
                controller_submission=submission, canceled_batches=['unused'], source_namespaces_remain_reserved=True)
            authority = self.authority(root, 'closure.json', proof)
            with patch.object(production, 'meson_controller_configuration',
                    return_value=dict(submit=str(owner/'controller.sub'))), \
                 patch.object(production.durable, 'jobs', return_value=[dict(Iwd=str(peer/'batches/running/production'))]) as jobs:
                self.assertEqual(production.reviewed_source_closure({}, authority, expected), ['unused'])
                jobs.return_value = [dict(Iwd=str(peer/'batches/unused'))]
                with self.assertRaisesRegex(RuntimeError, 'live job'):
                    production.reviewed_source_closure({}, authority, expected)
                jobs.return_value = [dict(Iwd=str(root/'unreviewed_controller'))]
                with self.assertRaisesRegex(RuntimeError, 'Unexpected live owner'):
                    production.reviewed_source_closure({}, authority, expected)
                (peer/'batches/unused').mkdir(parents=True)
                (peer/'batches/unused/submission_intent.json').write_text('{}')
                jobs.return_value = []
                with self.assertRaisesRegex(ValueError, 'already attempted'):
                    production.reviewed_source_closure({}, authority, expected)

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

    def test_reviewed_audit_repair_requires_failed_original_and_fresh_queue_absence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, batch, _ = self.configuration(root)
            config.update(directory=str(root), batches=[batch])
            (root/'config.json').write_text(json.dumps(config))
            (root/'coverage').mkdir()
            original = self.authority(root/'coverage', 'submission.json',
                dict(cluster=123, task=str(root/'coverage')))
            failed = self.authority(root/'coverage', 'coverage_status.json',
                dict(complete=False, error='preserved original audit failure'))
            native = self.authority(root, 'native.json', dict(ClusterId=123, ProcId=0,
                Iwd=str(root/'coverage'), JobStatus=5, ExitBySignal=False, ExitCode=1))
            replacement = root/'audit_repair'
            replacement.mkdir()
            (replacement/'worker.sub').write_text('synthetic read-only audit')
            receipt = self.authority(replacement, 'submission.json', dict(cluster=124,
                task=str(replacement), submit_sha256=production.durable.sha(replacement/'worker.sub')))
            auditor = self.authority(replacement, 'auditor.py', {})
            status = self.authority(replacement, 'coverage_status.json', dict(complete=True))
            proof = dict(schema='shift-reviewed-meson-coverage-recovery-v1', scope='coverage-only',
                worklist_sha256=production.durable.sha(root/'config.json'), original_submission=original,
                original_status=failed, original_native_failure=native, replacement_submission=receipt,
                replacement_auditor=auditor, replacement_status=status)
            config['reviewed_coverage_recovery'] = self.authority(root, 'review.json', proof)
            with patch.object(production.durable, 'jobs', return_value=[]):
                self.assertEqual(production.reviewed_coverage_recovery(config), proof)
            with patch.object(production.durable, 'jobs', return_value=[dict(ClusterId=123)]):
                with self.assertRaisesRegex(RuntimeError, 'still live'):
                    production.reviewed_coverage_recovery(config)
            proof['worklist_sha256'] = 'changed'
            config['reviewed_coverage_recovery'] = self.authority(root, 'bad_review.json', proof)
            with self.assertRaisesRegex(ValueError, 'frozen science'):
                production.reviewed_coverage_recovery(config)

    def test_repaired_coverage_still_requires_native_execution_before_MC(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, batch, _ = self.configuration(root)
            config.update(directory=str(root), batches=[dict(batch, batch='canary')])
            pin = self.authority(root, 'repair_submission.json', dict(cluster=124, task=str(root/'repair')))
            with patch.object(production, 'reviewed_coverage_recovery',
                    return_value=dict(replacement_submission=pin)), \
                 patch.object(production, 'native_status', return_value='active'), \
                 patch.object(production.durable, 'submission') as submit, \
                 patch.object(production.durable, 'quota') as quota:
                state = production.step(config, {})
            self.assertEqual(state['coverage_audit']['cluster'], 124)
            submit.assert_not_called()
            quota.assert_not_called()

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


class ReviewedRemainingStorage(unittest.TestCase):
    def pin(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
        return dict(path=str(path), sha256=production.durable.sha(path))

    def test_storage_reader_retries_only_bounded_transient_failures_and_checks_the_same_bytes(self):
        import errno
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'evidence.json'
            pin = self.pin(path, dict(complete=True))
            original = Path.open
            attempts = []
            def flaky(p, *args, **kwargs):
                attempts.append(str(p))
                if len(attempts) < 3:
                    raise OSError(errno.EIO, 'test transient')
                return original(p, *args, **kwargs)
            with patch.object(Path, 'open', flaky), patch.object(production.time, 'sleep') as pause:
                self.assertEqual(production.storage_pinned_read(pin), dict(complete=True))
            self.assertEqual(len(attempts), 3)
            self.assertEqual(pause.call_count, 2)
            path.write_text('{}')
            with self.assertRaisesRegex(ValueError, 'evidence changed'):
                production.storage_pinned_read(pin)

    def test_persistent_storage_error_has_the_exact_path_and_does_not_admit_partial_data(self):
        import errno
        path = Path('/afs/test/exact_failed_seed.json')
        with patch.object(Path, 'open', side_effect=OSError(errno.EIO, 'test persistent')) as opened, \
             patch.object(production.time, 'sleep'):
            with self.assertRaises(OSError) as caught:
                production.storage_payload(path)
        self.assertEqual(opened.call_count, 3)
        self.assertEqual(caught.exception.filename, str(path))
        self.assertIn(str(path), str(caught.exception))
        with patch.object(Path, 'open', side_effect=OSError(errno.EACCES, 'test permission')) as opened, \
             patch.object(production.time, 'sleep') as pause:
            with self.assertRaises(OSError):
                production.storage_payload(path)
        self.assertEqual(opened.call_count, 1)
        pause.assert_not_called()

    def test_storage_closed_primitive_keeps_the_existing_exact_completion_contract(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan = self.pin(root/'plan.json', dict(points=[]))
            seal = self.pin(root/'seal.json', dict(files={str(root/'plan.json'): plan['sha256']}))
            self.pin(root/'completed_points/p.json', dict(complete=True, point='p', events=1,
                full_gen_events=2, plan_sha256=plan['sha256'], seal_sha256=seal['sha256']))
            self.assertTrue(production.storage_closed_primitive(root, 'p', 2, float('inf')))
            with self.assertRaisesRegex(ValueError, 'sealed complete population'):
                production.storage_closed_primitive(root, 'p', 3, float('inf'))
            self.assertFalse(production.storage_closed_primitive(root, 'missing', 2, float('inf')))

    def remaining_fixture(self, root):
        science = root/'science'
        science.mkdir()
        original = self.pin(science/'config.json', dict(frozen=True))
        registration = self.pin(root/'registration.json', {})
        batches = [dict(batch=name, point=name, source_collisions=10) for name in ('a', 'b')]
        census = {}
        for batch, events in zip(batches, (4, 3)):
            work = science/'batches'/batch['batch']
            census[batch['batch']] = dict(
                source_status=self.pin(work/'source_status.json', dict(batch=batch['batch'],
                    point=batch['point'], source_collisions=10, complete=True, events=events,
                    finished_utc='2020-01-01T00:00:00+00:00', parent=dict(gen_sha256='a'*64))),
                source_submission=self.pin(work/'submission.json', dict(cluster=events, task=str(work))))
        census['a']['material_source'] = self.pin(science/'batches/a/material.json',
            dict(gen_sha256='a'*64, selected_indices=[1, 3]))
        authority = dict(schema='shift-dp-reviewed-remaining-storage-v1', science_configuration=original,
            original_storage_registration=registration, minimum_eos_free_bytes=50000000000,
            aggregate_storage_reservation_bytes=22000000000, bytes_per_selected_event=326000,
            fixed_bytes_per_batch=5000000, SM_registrations=[
                self.pin(root/'SM75.json', dict(new_SM_reservation_bytes=75000000000)),
                self.pin(root/'SM62.json', dict(new_SM_reservation_bytes=62000000000,
                    total_SM_reservation_bytes=137000000000))],
            source_census=self.pin(root/'census.json', dict(batches=census)))
        config = dict(directory=str(science), batches=batches, minimum_eos_free_bytes=50000000000,
            reviewed_remaining_storage_authority=self.pin(root/'authority.json', authority))
        fresh = dict(authority=dict(storage_registration=registration), storage_registration=dict(
            peer_reservations=[dict(name=name, reserved_bytes=amount) for name, amount in [
                ('SM_weekend', 75000000000), ('direct_DY_weekend', 30000000000),
                ('prior_SM_parent_protection', 6144000000)]]))
        return config, authority, fresh

    def test_closed_census_keeps_unknown_material_selection_at_full_GEN_bound(self):
        with tempfile.TemporaryDirectory() as temporary:
            config, authority, fresh = self.remaining_fixture(Path(temporary))
            with patch.object(production, 'fresh_production_authority', return_value=fresh), \
                 patch.object(production, 'aggregation_reservation', return_value=22000000000), \
                 patch.object(production, 'reviewed_sm_storage_credit', return_value=(10000000000, set(), {})), \
                 patch.object(production, 'storage_closed_primitive', return_value=False):
                peer, own, evidence = production.reviewed_remaining_storage(config,
                    dict(observed_utc='2021-01-01T00:00:00+00:00'), {})
            self.assertEqual(peer, 163144000000)
            self.assertEqual(own, 2*5000000+5*326000)
            self.assertEqual(evidence['full_gen_events'], 7)
            self.assertEqual(evidence['selected_event_upper_bound'], 5)

    def test_only_a_sealed_completed_primitive_releases_its_Nano_future_claim(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, authority, fresh = self.remaining_fixture(root)
            self.pin(Path(config['directory'])/'batches/a/production/completed_points/a.json', dict(events=2))
            with patch.object(production, 'fresh_production_authority', return_value=fresh), \
                 patch.object(production, 'aggregation_reservation', return_value=22000000000), \
                 patch.object(production, 'reviewed_sm_storage_credit', return_value=(0, set(), {})), \
                 patch.object(production, 'storage_closed_primitive', side_effect=lambda p, point, n, t: point == 'a'):
                _, own, evidence = production.reviewed_remaining_storage(config,
                    dict(observed_utc='2021-01-01T00:00:00+00:00'), {})
            self.assertEqual(own, 2*5000000+3*326000)
            self.assertEqual(evidence['completed_selected_events'], 2)

    def test_live_GEN_or_changed_source_is_not_a_closed_census(self):
        with tempfile.TemporaryDirectory() as temporary:
            config, authority, fresh = self.remaining_fixture(Path(temporary))
            with patch.object(production, 'fresh_production_authority', return_value=fresh), \
                 patch.object(production, 'aggregation_reservation', return_value=22000000000), \
                 patch.object(production, 'reviewed_sm_storage_credit', return_value=(0, {4}, {})):
                with self.assertRaisesRegex(ValueError, 'source identity'):
                    production.reviewed_remaining_storage(config,
                        dict(observed_utc='2021-01-01T00:00:00+00:00'), {})
            source = Path(config['directory'])/'batches/a/source_status.json'
            source.write_text('{}')
            with patch.object(production, 'fresh_production_authority', return_value=fresh), \
                 patch.object(production, 'aggregation_reservation', return_value=22000000000), \
                 patch.object(production, 'reviewed_sm_storage_credit', return_value=(0, set(), {})):
                with self.assertRaises(ValueError):
                    production.reviewed_remaining_storage(config,
                        dict(observed_utc='2021-01-01T00:00:00+00:00'), {})

    def test_reviewed_policy_cannot_lower_the_floor_or_omit_SM_raw_supply(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, authority, fresh = self.remaining_fixture(root)
            for change in [dict(minimum_eos_free_bytes=1), dict(SM_registrations=authority['SM_registrations'][:1])]:
                altered = dict(authority, **change)
                config['reviewed_remaining_storage_authority'] = self.pin(root/'changed.json', altered)
                with patch.object(production, 'fresh_production_authority', return_value=fresh), \
                     patch.object(production, 'aggregation_reservation', return_value=22000000000):
                    with self.assertRaises(ValueError):
                        production.reviewed_remaining_storage(config,
                            dict(observed_utc='2021-01-01T00:00:00+00:00'), {})

    def SM_fixture(self, root):
        science, peer = root/'science', root/'SM'
        science.mkdir()
        peer.mkdir()
        work = peer/'jobs/one'
        task = dict(cluster=22, directory=str(work), phase='done', kind='detector',
            native_exit_code=0, finished_epoch=10, request_sha256='a'*64, result_sha256='b'*64)
        row = dict(cluster=22, proc=0, iwd=str(work), finished_epoch=10,
            request_sha256='a'*64, result_sha256='b'*64,
            native_exit_event='005 (22.000.000) Job terminated.\nNormal termination (return value 0)',
            objects=[dict(path='/eos/user/j/jniedzie/test/Nano.root', bytes=123, sha256='c'*64)])
        authority = dict(account_owner='jniedzie', SM_configuration=self.pin(root/'SM_config.json',
            dict(campaign_directory=str(peer), own_reservation_bytes=137000000000)),
            SM_directory=str(peer), terminal_cache_directory=str(science/'cache'),
            terminal_seed_parts=[self.pin(root/'seed.json', dict(producers=[row]))])
        config = dict(directory=str(science), owner='jniedzie', condor_pool='collector',
            reviewed_remaining_storage_authority=dict(sha256='fixture'))
        production._REVIEWED_STORAGE_CACHES.clear()
        return config, authority, task, row

    def test_terminal_cache_reuses_proofs_and_rechecks_fresh_queue_every_poll(self):
        with tempfile.TemporaryDirectory() as temporary:
            config, authority, task, row = self.SM_fixture(Path(temporary))
            with patch.object(production, 'storage_ledger', return_value=dict(tasks=dict(one=task))), \
                 patch.object(production, 'committed_sm_storage_entry') as capture, \
                 patch('shift_condor_native.query_account', return_value=[]) as queue:
                for _ in range(2):
                    self.assertEqual(production.reviewed_sm_storage_credit(config, authority, 100)[0], 123)
                capture.assert_not_called()
                self.assertEqual(queue.call_count, 2)

    def test_peer_reader_captures_once_in_memory_without_writing_the_shared_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            config, authority, task, row = self.SM_fixture(Path(temporary))
            authority['terminal_seed_parts'] = []
            with patch.object(production, 'storage_ledger', return_value=dict(tasks=dict(one=task))), \
                 patch.object(production, 'committed_sm_storage_entry', return_value=row) as capture, \
                 patch.object(production.durable, 'save', side_effect=AssertionError('peer must not write')) as save, \
                 patch('shift_condor_native.query_account', return_value=[]) as queue:
                for _ in range(2):
                    self.assertEqual(production.reviewed_sm_storage_credit(config, authority, 100,
                        persist_terminal_cache=False)[0], 123)
                self.assertEqual(capture.call_count, 1)
                self.assertEqual(queue.call_count, 2)
                save.assert_not_called()
                self.assertFalse(Path(authority['terminal_cache_directory']).exists())
            with patch.object(production, 'storage_ledger', return_value=dict(tasks=dict(one=task))), \
                 patch('shift_condor_native.query_account', return_value=[dict(ClusterId=22)]):
                with self.assertRaisesRegex(RuntimeError, 'producer is live'):
                    production.reviewed_sm_storage_credit(config, authority, 100, persist_terminal_cache=False)

    def test_live_cached_producer_and_unknown_queue_cannot_release_storage(self):
        import shift_condor_native as native
        with tempfile.TemporaryDirectory() as temporary:
            config, authority, task, row = self.SM_fixture(Path(temporary))
            with patch.object(production, 'storage_ledger', return_value=dict(tasks=dict(one=task))), \
                 patch('shift_condor_native.query_account', return_value=[dict(ClusterId=22)]):
                with self.assertRaisesRegex(RuntimeError, 'producer is live'):
                    production.reviewed_sm_storage_credit(config, authority, 100)
            with patch.object(production, 'storage_ledger', return_value=dict(tasks=dict(one=task))), \
                 patch('shift_condor_native.query_account', side_effect=native.NativeCondorError('unknown')):
                with self.assertRaises(native.NativeCondorError):
                    production.reviewed_sm_storage_credit(config, authority, 100)

    def test_newly_done_but_still_queued_chunk_keeps_its_claim_without_poisoning_existing_credit(self):
        with tempfile.TemporaryDirectory() as temporary:
            config, authority, task, row = self.SM_fixture(Path(temporary))
            pending = dict(task, cluster=23, directory=str(Path(task['directory']).with_name('two')))
            with patch.object(production, 'storage_ledger', return_value=dict(tasks=dict(one=task, two=pending))), \
                 patch('shift_condor_native.query_account', return_value=[dict(ClusterId=23, JobStatus=4)]), \
                 patch.object(production, 'committed_sm_storage_entry') as capture:
                self.assertEqual(production.reviewed_sm_storage_credit(config, authority, 100)[0], 123)
                capture.assert_not_called()

    def test_identical_EOS_objects_are_credited_once_and_conflicts_reject(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, authority, task, row = self.SM_fixture(root)
            second_task = dict(task, cluster=23, directory=str(Path(task['directory']).with_name('two')))
            second_row = dict(row, cluster=23, iwd=second_task['directory'],
                native_exit_event='005 (23.000.000) Job terminated.\nNormal termination (return value 0)')
            authority['terminal_seed_parts'] = [self.pin(root/'both.json', dict(producers=[row, second_row]))]
            with patch.object(production, 'storage_ledger', return_value=dict(tasks=dict(one=task, two=second_task))), \
                 patch('shift_condor_native.query_account', return_value=[]):
                self.assertEqual(production.reviewed_sm_storage_credit(config, authority, 100)[0], 123)
                production._REVIEWED_STORAGE_CACHES['fixture'][23]['objects'] = [dict(row['objects'][0], bytes=124)]
                with self.assertRaisesRegex(ValueError, 'duplicate EOS'):
                    production.reviewed_sm_storage_credit(config, authority, 100)

    def test_new_chunk_is_captured_once_but_reserved_byte_fallback_is_never_credit(self):
        with tempfile.TemporaryDirectory() as temporary:
            config, authority, task, row = self.SM_fixture(Path(temporary))
            authority['terminal_seed_parts'] = []
            with patch.object(production, 'storage_ledger', return_value=dict(tasks=dict(one=task))), \
                 patch.object(production, 'committed_sm_storage_entry', return_value=row) as capture, \
                 patch('shift_condor_native.query_account', return_value=[]):
                production.reviewed_sm_storage_credit(config, authority, 100)
                production.reviewed_sm_storage_credit(config, authority, 100)
                self.assertEqual(capture.call_count, 1)
            production._REVIEWED_STORAGE_CACHES.clear()
            authority['terminal_cache_directory'] = str(Path(config['directory'])/'other_cache')
            with patch.object(production, 'storage_ledger', return_value=dict(tasks=dict(one=task))), \
                 patch.object(production, 'committed_sm_storage_entry', side_effect=ValueError('not verified')), \
                 patch('shift_condor_native.query_account', return_value=[]):
                self.assertEqual(production.reviewed_sm_storage_credit(config, authority, 100)[0], 0)


class UnsubmittedRuntimeRecovery(unittest.TestCase):
    def fixture(self, root):
        def pin(path, value):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value))
            return dict(path=str(path), sha256=production.durable.sha(path))
        original = root/'science/batches/a/production'
        original.mkdir(parents=True)
        batch = dict(batch='a', point='p')
        point = dict(point='p', chunks=[dict(chunk=0, nano='/eos/user/test/Nano.root',
            metadata='/eos/user/test/metadata')], groups=[dict(nano='/eos/user/test/group.root',
            histograms='/eos/user/test/hist.root')], merged_histograms='/eos/user/test/merged.root')
        pin(original/'plan.json', dict(points=[point]))
        parent = dict(gen_sha256='a'*64, event_ids=[[1, 1, 1]])
        pin(original/'gen_inventory.json', dict(points=dict(p=parent)))
        pin(original.parent/'source_status.json', dict(complete=True, batch='a', point='p', parent=parent))
        (original/'pipeline.dag.template').write_text('JOB D '+str(original/'detector.sub')+'\nVARS D seal="SEAL_DIGEST"\n')
        (original/'detector.sub').write_text('initialdir = '+str(original)+'\nexecutable = '+str(original/'detector_worker.sh')+'\narguments = $(point) $(chunk) $(seal)\n')
        for name in ('finalizer.sub', 'collect.sub'):
            (original/name).write_text('unchanged '+name)
        pin(original/'seal.json', dict(files={str(path): production.durable.sha(path)
            for path in original.iterdir() if path.is_file()}))
        (original/'pipeline.dag').write_text((original/'pipeline.dag.template').read_text().replace(
            'SEAL_DIGEST', production.durable.sha(original/'seal.json')))
        authority = pin(root/'runtime_authority.json', dict(complete=True))
        view_root = root/'view'
        policy = dict(production_directory=str(original), allowed_slices='all_planned',
            runtime_override=dict(authority=authority))
        pin(view_root/'stage_budget_policy.json', policy)
        (view_root/'detector_worker.sh').write_text('qualified wrapper')
        pin(view_root/'seal.json', dict(original_production=str(original),
            original_seal_sha256=production.durable.sha(original/'seal.json'), authority=authority,
            files={str(path): production.durable.sha(path) for path in view_root.iterdir() if path.is_file()}))
        view = dict(directory=str(view_root), policy=str(view_root/'stage_budget_policy.json'),
            wrapper=str(view_root/'detector_worker.sh'), seal_sha256=production.durable.sha(view_root/'seal.json'))
        old = root/'old/handoffs/a'
        old.mkdir(parents=True)
        (old/'worker.sub').write_text('preserved failed handoff')
        handoff = pin(old/'submission.json', dict(cluster=10, task=str(old),
            submit_sha256=production.durable.sha(old/'worker.sub')))
        preflight = old/'runtime_preflight'
        preflight.mkdir()
        (preflight/'worker.sub').write_text('preserved failed preflight')
        pin(preflight/'submission.json', dict(cluster=11, task=str(preflight),
            submit_sha256=production.durable.sha(preflight/'worker.sub')))
        failed = pin(preflight/'receipt.json', dict(complete=False, error='preserved converter failure',
            original_plan_sha256=production.durable.sha(original/'plan.json'),
            original_seal_sha256=production.durable.sha(original/'seal.json')))
        native = pin(root/'native.json', dict(schema='shift-meson-unsubmitted-runtime-native-review-v1',
            original_directory=str(original), original_detector_queue=[],
            failed_handoff=dict(ClusterId=10, ProcId=0, Iwd=str(old), Cmd=str(old/'run.sh'),
                JobStatus=5, ExitCode=1, ExitBySignal=False),
            failed_preflight=dict(ClusterId=11, ProcId=0, Iwd=str(preflight), Cmd=str(preflight/'worker.sh'),
                JobStatus=5, ExitCode=66, ExitBySignal=False)))
        absent = pin(root/'absent.json', dict(schema='shift-meson-unsubmitted-output-absence-v1',
            original_directory=str(original), absent_paths=production.unsubmitted_runtime_output_paths(dict(points=[point]))))
        with patch('dark_photon_runtime_recovery.validate_override'):
            proof = production.prepare_unsubmitted_runtime_recovery(original, batch, authority, view,
                root/'recovery', failed_handoff_submission=handoff, failed_preflight_receipt=failed,
                native_review=native, output_absence=absent)
        return original, batch, proof

    def test_pure_review_preserves_graph_arguments_and_rejects_changed_dependencies(self):
        with tempfile.TemporaryDirectory() as temporary:
            original, batch, pin = self.fixture(Path(temporary))
            with patch('dark_photon_runtime_recovery.validate_override') as qualified, \
                 patch.object(production.durable, 'jobs', side_effect=AssertionError('pure validation')):
                proof = production.validate_unsubmitted_runtime_recovery(original, batch, pin)
                qualified.assert_called_once()
                self.assertEqual(proof['original_plan_sha256'], production.durable.sha(original/'plan.json'))
                self.assertFalse((original/'submission.json').exists())
                replacement = Path(proof['recovery_directory'])
                (replacement/'pipeline.dag').write_text((replacement/'pipeline.dag').read_text()+'JOB EXTRA unsafe.sub\n')
                with self.assertRaisesRegex(ValueError, 'exact DAG'):
                    production.validate_unsubmitted_runtime_recovery(original, batch, pin)

    def test_submitted_original_or_incomplete_output_absence_cannot_be_adopted(self):
        with tempfile.TemporaryDirectory() as temporary:
            original, batch, pin = self.fixture(Path(temporary))
            with patch('dark_photon_runtime_recovery.validate_override'):
                (original/'submission_intent.json').write_text('{}')
                with self.assertRaisesRegex(ValueError, 'unsubmitted original'):
                    production.validate_unsubmitted_runtime_recovery(original, batch, pin)
                (original/'submission_intent.json').unlink()
                proof = production.read_pinned(pin)
                absent = Path(proof['output_absence']['path'])
                value = production.read(absent); value['absent_paths'].pop()
                absent.write_text(json.dumps(value)); proof['output_absence']['sha256']=production.durable.sha(absent)
                with self.assertRaisesRegex(ValueError, 'output-absence'):
                    production.validate_unsubmitted_runtime_recovery(original, batch, proof)

    def test_removed_failed_holds_need_their_exact_native_hold_history(self):
        for changed in ['valid','not_held','wrong_reason','no_holds','successful']:
            with self.subTest(changed=changed),tempfile.TemporaryDirectory() as temporary:
                original,batch,pin=self.fixture(Path(temporary));proof=production.read_pinned(pin)
                review_path=Path(proof['native_review']['path']);review=production.read(review_path)
                for key in ['failed_handoff','failed_preflight']:
                    review[key].update(JobStatus=3,LastJobStatus=5,LastHoldReasonCode=3,NumHolds=1,
                        LastHoldReason='OnExitHold expression evaluated to TRUE')
                if changed=='not_held':review['failed_handoff']['LastJobStatus']=2
                if changed=='wrong_reason':review['failed_handoff']['LastHoldReasonCode']=1
                if changed=='no_holds':review['failed_preflight']['NumHolds']=0
                if changed=='successful':review['failed_preflight']['ExitCode']=0
                review_path.write_text(json.dumps(review));proof['native_review']['sha256']=production.durable.sha(review_path)
                with patch('dark_photon_runtime_recovery.validate_override'):
                    if changed=='valid':production.validate_unsubmitted_runtime_recovery(original,batch,proof)
                    else:
                        with self.assertRaisesRegex(ValueError,'native failed holds'):
                            production.validate_unsubmitted_runtime_recovery(original,batch,proof)

    def test_first_admission_checks_held_attempts_outputs_and_never_submits_twice(self):
        with tempfile.TemporaryDirectory() as temporary:
            original, batch, pin = self.fixture(Path(temporary))
            config = dict(directory=str(original.parents[2]), detector_workers_per_point=100)
            proof = production.read_pinned(pin); replacement=Path(proof['recovery_directory'])
            review = production.read_pinned(proof['native_review'])
            queue = [review['failed_handoff'], review['failed_preflight']]
            def submit(config, directory, cap):
                self.assertEqual(directory,replacement)
                (directory/'pipeline.dag.condor.sub').write_text('prepared DAG manager')
                receipt=dict(cluster=20,task=str(directory),submit_sha256=production.durable.sha(directory/'pipeline.dag.condor.sub'))
                production.durable.new(directory/'submission.json',receipt)
                return receipt
            with patch('dark_photon_runtime_recovery.validate_override'), \
                 patch.object(production.durable,'jobs',return_value=queue), \
                 patch('run_dark_photon_runtime_recovery.verify_output_absence') as absence, \
                 patch.object(production.durable,'submit_dag',side_effect=submit) as launched:
                first=production.submit_unsubmitted_runtime_recovery(config,batch,pin)
                self.assertEqual(production.submit_unsubmitted_runtime_recovery(config,batch,pin),first)
                launched.assert_called_once(); absence.assert_called_once()
                self.assertFalse((original/'submission.json').exists())

    def test_unknown_scheduler_active_old_attempt_or_foreign_payload_owner_cannot_submit(self):
        import shift_condor_native as native
        with tempfile.TemporaryDirectory() as temporary:
            original,batch,pin=self.fixture(Path(temporary)); config=dict(directory=str(original.parents[2]))
            proof=production.read_pinned(pin);review=production.read_pinned(proof['native_review'])
            cases=[native.NativeCondorError('unknown'), [dict(review['failed_handoff'],JobStatus=2)],
                   [dict(ClusterId=99,ProcId=0,Iwd=str(original),JobStatus=2)]]
            for case in cases:
                with patch('dark_photon_runtime_recovery.validate_override'), \
                     patch.object(production.durable,'jobs',**({'side_effect':case} if isinstance(case,Exception) else {'return_value':case})), \
                     patch.object(production.durable,'submit_dag') as submit, \
                     patch('run_dark_photon_runtime_recovery.verify_output_absence') as absence:
                    with self.assertRaises((native.NativeCondorError,RuntimeError)):
                        production.submit_unsubmitted_runtime_recovery(config,batch,pin)
                    submit.assert_not_called();absence.assert_not_called()

    def test_recovery_manager_accepts_only_original_payload_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            original,batch,pin=self.fixture(Path(temporary));proof=production.read_pinned(pin)
            replacement=Path(proof['recovery_directory'])
            config=dict(directory=str(original.parents[2]),reviewed_unsubmitted_runtime_recoveries=dict(a=pin))
            receipt=dict(cluster=20,task=str(replacement))
            queue=[dict(ClusterId=20,ProcId=0,Iwd=str(replacement),JobStatus=2),
                   dict(ClusterId=21,ProcId=0,DAGManJobId=20,Iwd=str(original),JobStatus=2)]
            with patch('dark_photon_runtime_recovery.validate_override'), \
                 patch.object(production.durable,'jobs',return_value=queue), \
                 patch('watch_dark_photon_statistics.execute_events',return_value=1):
                self.assertEqual(production.native_status(config,receipt),'active')
                queue[1]['Iwd']=str(replacement)
                with self.assertRaisesRegex(RuntimeError,'child has an unexpected'):
                    production.native_status(config,receipt)

    def test_handoff_bypasses_failed_old_runtime_and_exports_only_original_rates(self):
        with tempfile.TemporaryDirectory() as temporary:
            original,batch,pin=self.fixture(Path(temporary))
            config=dict(directory=str(original.parents[2]),reviewed_unsubmitted_runtime_recoveries=dict(a=pin),
                future_detector_runtime=dict(profile='old_failed_runtime'))
            with patch.object(production,'adopt',return_value={}), \
                 patch.object(production,'submit_unsubmitted_runtime_recovery',return_value=dict(cluster=20)) as submit, \
                 patch.object(production,'export_original_rates') as rates, \
                 patch('dark_photon_runtime_recovery.future_preflight',side_effect=AssertionError('do not rerun failed preflight')):
                self.assertEqual(production.handoff(config,batch),dict(cluster=20))
                submit.assert_called_once_with(config,batch,pin)
                rates.assert_called_once_with(config,original.parent,original)

    def test_parallel_handoff_adopts_recovery_receipt_without_original_submission(self):
        with tempfile.TemporaryDirectory() as temporary:
            original,batch,pin=self.fixture(Path(temporary));root=Path(temporary)
            proof=production.read_pinned(pin);replacement=Path(proof['recovery_directory'])
            (replacement/'pipeline.dag.condor.sub').write_text('qualified manager')
            detector=dict(cluster=20,task=str(replacement),submit_sha256=production.durable.sha(replacement/'pipeline.dag.condor.sub'))
            production.durable.new(replacement/'submission.json',detector)
            worker=root/'new_handoff';worker.mkdir();(worker/'worker.sub').write_text('new handoff')
            handoff=dict(cluster=19,task=str(worker),submit_sha256=production.durable.sha(worker/'worker.sub'))
            production.durable.new(worker/'submission.json',handoff)
            production.durable.new(original.parent/'rates.json',dict(complete=True,
                production_plan_sha256=production.durable.sha(original/'plan.json')))
            config=dict(directory=str(original.parents[2]),reviewed_unsubmitted_runtime_recoveries=dict(a=pin))
            with patch('dark_photon_runtime_recovery.validate_override'), \
                 patch.object(production,'handoff_directory',return_value=worker), \
                 patch.object(production,'batch_job_status',return_value='complete'), \
                 patch.object(production.durable,'submit_dag',side_effect=AssertionError('already owned')):
                self.assertEqual(production.parallel_handoff(config,{},batch,{},None),('complete',detector))
            self.assertFalse((original/'submission.json').exists())


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

    def test_closed_source_admission_preserves_ready_work_without_starting_future_gen(self):
        with tempfile.TemporaryDirectory() as temporary:
            config=dict(directory=temporary,batches=[dict(batch='future',operational_canary=False)],
                        simultaneous_sources=7,source_admission_closed=True)
            with patch.object(production.durable,'quota',return_value={}), \
                 patch.object(production,'storage_gate',return_value=(True,{})), \
                 patch.object(production.durable,'submission') as submit:
                state=production.step(config,{})
            submit.assert_not_called()
            self.assertTrue(state['source_admission_closed'])
            self.assertFalse(state['complete'])

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

    def test_reviewed_existing_DAG_keeps_its_original_worker_reservation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);directory=root/'batches/a';directory.mkdir(parents=True)
            (directory/'source_status.json').write_text(json.dumps(dict(complete=True,events=53)))
            recovery=root/'recovery';recovery.mkdir();(recovery/'submission.json').write_text('{}')
            batch=dict(batch='a',events_per_job=10,source_collisions=50000)
            config=dict(directory=str(root),batches=[batch],maximum_active_detector_workers=2400,
                detector_workers_per_point=100,reviewed_unsubmitted_runtime_recoveries=dict(a={'review':'pinned'}))
            with patch.object(production,'validate_unsubmitted_runtime_recovery',
                    return_value=dict(recovery_directory=str(recovery))) as validate:
                self.assertEqual(production.detector_reservations(config,{}),{'a':8})
                validate.assert_called_once_with(directory/'production',batch,{'review':'pinned'})
            with patch.object(production,'validate_unsubmitted_runtime_recovery',side_effect=ValueError('changed review')):
                with self.assertRaisesRegex(ValueError,'changed review'):
                    production.detector_reservations(config,{})

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
                old.update(calibration_mean_beta_gamma=400.,reference_mean_lab_flight_m=.004)
                proof=production.common_sample_anchor(config,batch,sample,root/'sample.json',Transport())
                self.assertTrue(proof['retained_existing'])
                old.update(calibration_mean_beta_gamma=500.,reference_mean_lab_flight_m=.005)
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

    def test_reviewed_failed_launcher_is_replaced_and_other_owners_are_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);science=root/'science';old=root/'old';new=root/'new'
            science.mkdir();(old/'tools').mkdir(parents=True)
            batches=[dict(batch=name) for name in ['failed','successful','unreviewed']]
            original=dict(directory=str(science),tools=str(science/'tools'),batches=batches,
                simultaneous_sources=2,simultaneous_detector_points=2)
            previous=dict(original,operational_handoffs=dict(directory=str(old/'handoffs')))
            for name in ['config.json','seal.json']:(science/name).write_text('{}')
            (old/'config.json').write_text(json.dumps(previous))
            descriptor='\n'.join(['+SendCredential = True','+MaxRuntime = 604800',
                'on_exit_hold = (ExitCode != 0)','periodic_release = False',
                'arguments = OPERATIONS_SEAL_DIGEST'])
            (old/'controller.sub.in').write_text(descriptor)
            (old/'lcg_controller.sh').write_text('#!/bin/bash\n')
            (old/'seal.json').write_text(json.dumps(dict(files={})))
            digest=production.durable.sha(old/'seal.json')
            (old/'controller.sub').write_text(descriptor.replace('OPERATIONS_SEAL_DIGEST',digest))
            for batch in batches:
                directory=old/'handoffs'/batch['batch'];directory.mkdir(parents=True)
                (directory/'submission.json').write_text('{}')
            review=root/'review.json';review.write_text('{}')
            with patch.object(production,'verify',return_value=original), \
                 patch.object(production,'operational_configuration',return_value=previous):
                replacement.prepare(science,production.durable.sha(science/'seal.json'),old,digest,new,
                    operational_handoffs=True,reviewed_handoff_failures=[('failed',review)])
            actual=json.loads((new/'config.json').read_text())['operational_handoffs']
            self.assertEqual(set(actual['preserved_directories']),{'successful','unreviewed'})
            self.assertTrue((new/'handoffs/failed/worker.sub').is_file())
            self.assertFalse((new/'handoffs/successful').exists())
            self.assertEqual(actual['reviewed_failures']['failed']['sha256'],production.durable.sha(review))

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

    def test_native_directory_match_does_not_double_escape_CERN_path_dots(self):
        path='/afs/cern.ch/work/user/production'
        condition=production.directory_condition(path)
        self.assertIn('substr(Iwd,0,',condition)
        self.assertIn(json.dumps(path+'/'),condition)
        self.assertNotIn('regexp',condition)
        self.assertNotIn('\\',condition)
        arguments=production.directory_condition(path,'Args')
        self.assertIn('cern[.]ch',arguments)
        self.assertNotIn('\\',arguments)

    def test_full_campaign_storage_check_is_shared_but_expires_before_more_admission(self):
        decisions={}
        with patch.object(production.time,'monotonic',return_value=100) as clock, \
             patch.object(production.durable,'quota',return_value=dict(free_bytes=1000)) as quota, \
             patch.object(production,'storage_gate',return_value=(True,dict(full_allocation=900))) as gate:
            self.assertTrue(production.poll_storage_gate({}, {}, decisions, True)[0])
            clock.return_value=150
            for _ in range(256):production.poll_storage_gate({}, {}, decisions, True)
            self.assertEqual(quota.call_count,1)
            self.assertEqual(gate.call_count,1)
            production.poll_storage_gate({}, {}, decisions, False)
            self.assertEqual(gate.call_count,2)
            clock.return_value=401
            production.poll_storage_gate({}, {}, decisions, True)
            self.assertEqual(gate.call_count,3)

    def test_slow_storage_review_never_admits_using_an_expired_observation(self):
        decisions={}
        with patch.object(production.time,'monotonic',side_effect=[0,301]), \
             patch.object(production.durable,'quota',return_value=dict(free_bytes=1000)), \
             patch.object(production,'storage_gate',return_value=(True,{})):
            with self.assertRaisesRegex(RuntimeError,'freshness'):
                production.poll_storage_gate({}, {}, decisions, True)
        self.assertEqual(decisions,{})

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



class OperationalAdoption(unittest.TestCase):
    def test_new_runtime_helpers_do_not_replace_scientific_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);science=root/'science';operations=root/'operations'
            science.mkdir();operations.mkdir()
            (science/'run_shift_gen_to_nano.py').write_text('original science')
            (operations/'run_shift_gen_to_nano.py').write_text('unqualified replacement')
            (operations/'dark_photon_runtime_recovery.py').write_text('new operational helper')
            names={'run_shift_gen_to_nano.py','dark_photon_runtime_recovery.py'}
            with patch.object(production,'__file__',str(operations/'run.py')):
                rows=production.adoption_sources(dict(tools=str(science)),names,
                    {'dark_photon_runtime_recovery.py'})
            self.assertEqual(rows['run_shift_gen_to_nano.py']['frozen_copy'],str(science/'run_shift_gen_to_nano.py'))
            self.assertEqual(rows['dark_photon_runtime_recovery.py']['frozen_copy'],str(operations/'dark_photon_runtime_recovery.py'))
            self.assertEqual(rows['run_shift_gen_to_nano.py']['sha256'],production.durable.sha(science/'run_shift_gen_to_nano.py'))

    def test_interrupted_adoption_keeps_its_pinned_operational_helper(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);old=root/'old.py';old.write_text('sealed helper')
            previous=dict(frozen_dependencies=dict(sources={'old.py':dict(
                frozen_copy=str(old),sha256=production.durable.sha(old))}))
            with patch.object(production,'__file__',str(root/'new_owner/run.py')):
                actual=production.adoption_sources(dict(tools='/unused'),{'old.py'},{'old.py'},previous)
            self.assertEqual(actual,previous['frozen_dependencies']['sources'])
            old.write_text('changed')
            with self.assertRaisesRegex(ValueError,'Previously adopted'):
                production.adoption_sources({}, {'old.py'}, {'old.py'}, previous)

    def test_missing_operational_helper_stops_before_preparation(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(production,'__file__',str(Path(temporary)/'run.py')):
                with self.assertRaises(FileNotFoundError):
                    production.adoption_sources(dict(tools='/unused'),{'missing.py'},{'missing.py'})

    def reviewed_owner(self,root):
        science=root/'science';(science/'batches/a').mkdir(parents=True)
        status=science/'batches/a/source_status.json';status.write_text('{}')
        owners=[]
        for name in ['old','new']:
            owner=root/name;handoff=owner/'handoffs/a';handoff.mkdir(parents=True)
            (handoff/'run.sh').write_text('sealed '+name)
            (handoff/'worker.sub.in').write_text('seal=OPERATIONS_SEAL_DIGEST')
            seal=owner/'seal.json';seal.write_text(json.dumps(dict(files={
                str(p):production.durable.sha(p) for p in [handoff/'run.sh',handoff/'worker.sub.in']})))
            (handoff/'worker.sub').write_text((handoff/'worker.sub.in').read_text().replace(
                'OPERATIONS_SEAL_DIGEST',production.durable.sha(seal)))
            owners.append(handoff)
        old,new=owners;receipt=dict(cluster=17,task=str(old),submit_sha256=production.durable.sha(old/'worker.sub'))
        (old/'submission.json').write_text(json.dumps(receipt))
        proof=dict(schema='shift-meson-handoff-recovery-v1',original_submission=receipt,
            original_handoff_directory=str(old),original_controller_seal=dict(path=str(root/'old/seal.json'),
                sha256=production.durable.sha(root/'old/seal.json')),
            source_status_sha256=production.durable.sha(status),gen_or_detector_rerun_allowed=False)
        authority=root/'review.json';authority.write_text(json.dumps(proof));pin=dict(
            path=str(authority),sha256=production.durable.sha(authority))
        config=dict(directory=str(science),operational_handoffs=dict(directory=str(new.parent),
            reviewed_failures=dict(a=pin)))
        return config,old,new,receipt

    def test_reviewed_operational_failure_uses_its_exact_old_owner(self):
        with tempfile.TemporaryDirectory() as temporary:
            config,old,new,receipt=self.reviewed_owner(Path(temporary))
            with patch.object(production,'native_status',side_effect=production.MesonJobFailure('held',[])) as status:
                self.assertEqual(production.handoff_directory(config,dict(batch='a')),new)
                self.assertEqual(status.call_args.args[1],receipt)
            with patch.object(production,'native_status',return_value='active'):
                with self.assertRaisesRegex(RuntimeError,'preserve its owner'):
                    production.handoff_directory(config,dict(batch='a'))
            (old/'run.sh').write_text('changed')
            with self.assertRaisesRegex(ValueError,'launcher changed'):
                production.handoff_directory(config,dict(batch='a'))

    def test_handoff_completion_archives_only_the_reviewed_preparation_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            config,old,new,receipt=self.reviewed_owner(Path(temporary))
            failure=dict(stage='detector_preparation',cluster=17,native_jobs=[dict(ClusterId=17,ProcId=0)])
            unrelated=dict(stage='detector',cluster=99,native_jobs=[dict(ClusterId=99,ProcId=0)])
            state=dict(failed_batches=dict(a=[failure,unrelated]));entry=dict(failure=failure)
            production.reconcile_handoff_failure(config,state,dict(batch='a'),entry,dict(cluster=18,task=str(new)))
            self.assertEqual(state['failed_batches']['a'],[unrelated])
            self.assertEqual(state['resolved_failed_batches']['a'][0]['failure'],failure)
            self.assertNotIn('failure',entry)


if __name__=='__main__':
    unittest.main()


class QualifiedBulkDetectorArguments(unittest.TestCase):
    def test_default_and_operational_canary_keep_local_gate(self):
        self.assertEqual(production.detector_gate_arguments({},dict(point='p')),['--canary-points','p'])
        self.assertEqual(production.detector_gate_arguments(
            dict(qualified_global_detector_canary={}),dict(point='p',operational_canary=True)),
            ['--canary-points','p'])

    def test_bulk_requires_pinned_authority_and_native_future_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'authority.json';p.write_text('{}')
            config=dict(qualified_global_detector_canary=dict(path=str(p),sha256=production.durable.sha(p)))
            with self.assertRaisesRegex(ValueError,'future runtime'):
                production.detector_gate_arguments(config,dict(point='p'))
            config['future_detector_runtime']={}
            self.assertEqual(production.detector_gate_arguments(config,dict(point='p')),
                             ['--qualified-global-canary',str(p)])
            p.write_text('changed')
            with self.assertRaises(ValueError):
                production.detector_gate_arguments(config,dict(point='p'))


class HandoffStartThrottle(unittest.TestCase):
    def test_throttle_only_new_launches_existing_jobs_still_reconcile(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp);config=dict(maximum_new_detector_handoffs_per_poll=10)
            state=dict(detector_handoff_starts_this_poll=10);entry={};batch=dict(batch='a')
            with patch.object(production,'handoff_directory',return_value=directory), \
                 patch.object(production.durable,'submission') as submit:
                self.assertEqual(production.parallel_handoff(config,state,batch,entry,{}),('waiting',None))
                submit.assert_not_called()
            (directory/'submission.json').write_text('{}')
            with patch.object(production,'handoff_directory',return_value=directory), \
                 patch.object(production,'existing_receipt',return_value=dict(cluster=12)), \
                 patch.object(production,'batch_job_status',return_value='active') as check:
                self.assertEqual(production.parallel_handoff(config,state,batch,entry,{}),('active',None))
                check.assert_called_once()
            self.assertEqual(state['detector_handoff_starts_this_poll'],10)

    def test_one_actual_new_submission_consumes_one_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(production,'handoff_directory',return_value=Path(tmp)), \
                 patch.object(production.durable,'submission',return_value=dict(cluster=12)), \
                 patch.object(production,'batch_job_status',return_value='active'):
                state={}
                production.parallel_handoff(dict(maximum_new_detector_handoffs_per_poll=10),
                    state,dict(batch='a'),{}, {})
                self.assertEqual(state['detector_handoff_starts_this_poll'],1)


class HandoffStartPollReset(unittest.TestCase):
    def test_two_real_poll_entries_each_allow_ten_new_starts(self):
        with tempfile.TemporaryDirectory() as tmp:
            config=dict(directory=tmp,batches=[],maximum_new_detector_handoffs_per_poll=10)
            state=dict(detector_handoff_starts_this_poll=10)
            for poll in range(2):
                with patch.object(production,'completion_scope',return_value={}), \
                     patch.object(production,'reviewed_coverage_recovery',return_value=None), \
                     patch.object(production.durable,'submission',return_value=dict(cluster=1)), \
                     patch.object(production,'native_status',return_value='active'):
                    production.step(config,state)
                self.assertEqual(state['detector_handoff_starts_this_poll'],0)
                with patch.object(production,'handoff_directory',return_value=Path(tmp)), \
                     patch.object(production.durable,'submission',return_value=dict(cluster=12)), \
                     patch.object(production,'batch_job_status',return_value='active'):
                    for i in range(10):
                        self.assertEqual(production.parallel_handoff(config,state,dict(batch=str(i)),{},{}),('active',None))
                    self.assertEqual(production.parallel_handoff(config,state,dict(batch='next'),{},{}),('waiting',None))
                self.assertEqual(state['detector_handoff_starts_this_poll'],10)
