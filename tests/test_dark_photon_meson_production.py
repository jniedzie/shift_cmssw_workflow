# Code style: AI-code. AI-generated fixed-M production and restart regression tests.
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import prepare_dark_photon_meson_production as preparation
import run_dark_photon_meson_production as production


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


class Admission(unittest.TestCase):
    def test_old_scale10_new_events_do_not_release_adaptive_reservation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            now=datetime.now(timezone.utc).isoformat()
            wave=dict(wave_index=0,batches=[dict(point='m2_prompt',events=20,seed=123,run_number=456)])
            (root/'worklist.json').write_text(json.dumps(dict(total_additional_events=417100,waves=[wave])))
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


class NativeHealth(unittest.TestCase):
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


if __name__=='__main__':
    unittest.main()
