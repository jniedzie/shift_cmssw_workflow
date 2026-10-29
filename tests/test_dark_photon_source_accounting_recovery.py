# Code style: AI-code. Exact-bank scientific accounting recovery checks.
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import run_dark_photon_meson_production as production


class SourceAccountingRecovery(unittest.TestCase):
    def fixture(self,root):
        original=root/'science/tools';tools=root/'review/tools'
        original.mkdir(parents=True);tools.mkdir(parents=True)
        write=lambda p,v:p.write_text(json.dumps(v))
        pin=lambda p:dict(path=str(p),sha256=production.durable.sha(p))
        changes={}
        for name in ['dark_photon_senscalc.py','run_dark_photon_senscalc_gen.py','export_dark_photon_cross_sections.py']:
            (original/name).write_text('original '+name)
            (tools/name).write_text('repaired '+name if name!='export_dark_photon_cross_sections.py' else 'original '+name)
            if name!='export_dark_photon_cross_sections.py':
                changes[name]=dict(original_sha256=production.durable.sha(original/name),
                                   replacement_sha256=production.durable.sha(tools/name))
        write(root/'science/seal.json',dict(files={str(p):production.durable.sha(p) for p in original.iterdir()}))
        bank=root/'science/batches/a';bank.mkdir(parents=True);write(bank/'source_status.json',dict(complete=True))
        manifest=root/'review/manifest.json';write(manifest,dict(mass_gev=.25,seed=10,source_trials=50))
        row=dict(batch='a',mass_gev=.25,seed=10,source_trials=50,
            all_original_trials_and_candidates_identical=True,
            all_bookkept_generated_and_pole_masses_equal_requested=True)
        audit=root/'review/audit.json';write(audit,dict(batches=[row],physics_or_source_momenta_changed=False,
            source_collisions_redrawn_for_production=0,diagnostic_replay_exposure_credit=0))
        overlay=root/'review/overlay.json';write(overlay,dict(schema='shift-reviewed-exact-source-pole-mass-helper-overlay-v1',
            changed_helpers=changes,source_p4_or_weights_changed=False,additional_source_collisions=0,
            saved_banks=dict(a=dict(manifest=pin(manifest),additional_source_collisions=0)),authority=pin(audit)))
        authority=root/'review/authority.json';write(authority,dict(schema='shift-reviewed-exact-source-mass-accounting-v1',
            helper_overlay=pin(overlay),batches=dict(a={}),only_changed_helpers=list(changes),accounting_tools=str(tools)))
        config=dict(directory=str(root/'science'),tools=str(original),reviewed_source_accounting=pin(authority),
            batches=[dict(batch='a',mass_gev=.25,source_seed=10,source_collisions=50)])
        return config,bank,tools,original

    def test_only_named_complete_bank_uses_qualified_accounting(self):
        with tempfile.TemporaryDirectory() as temporary:
            config,bank,tools,original=self.fixture(Path(temporary))
            self.assertEqual(production.source_accounting_export(config,bank),tools/'export_dark_photon_cross_sections.py')
            self.assertEqual(production.source_accounting_export(config,bank.with_name('other')),original/'export_dark_photon_cross_sections.py')
            (bank/'source_status.json').write_text('{"complete":false}')
            with self.assertRaisesRegex(ValueError,'completed GEN'):production.source_accounting_export(config,bank)

    def test_changed_source_identity_or_unrelated_helper_is_rejected(self):
        for changed in ['mass','export']:
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as temporary:
                config,bank,tools,original=self.fixture(Path(temporary))
                if changed=='mass':config['batches'][0]['mass_gev']=.7
                else:(tools/'export_dark_photon_cross_sections.py').write_text('unreviewed export')
                with self.assertRaises(ValueError):production.source_accounting_export(config,bank)

    def test_width_scope_and_frozen_sources_use_only_the_reviewed_two_helpers(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);original=root/'original';reviewed=root/'reviewed'
            original.mkdir();reviewed.mkdir()
            (reviewed/'dark_photon_senscalc.py').write_text('VALUE="reviewed"\n')
            (reviewed/'run_dark_photon_senscalc_gen.py').write_text('from dark_photon_senscalc import VALUE\n')
            before={name:sys.modules.get(name) for name in ('dark_photon_senscalc','run_dark_photon_senscalc_gen')}
            config=dict(tools=str(original),directory=str(root))
            with patch.object(production,'source_accounting_export',return_value=reviewed/'export.py'):
                with self.assertRaisesRegex(RuntimeError,'control'):
                    with production.source_accounting_scope(config,root/'bank'):
                        self.assertEqual(sys.modules['run_dark_photon_senscalc_gen'].VALUE,'reviewed')
                        raise RuntimeError('control')
                with patch.object(production,'adopt_with_accounting',side_effect=lambda config,batch:
                        sys.modules['run_dark_photon_senscalc_gen'].VALUE):
                    self.assertEqual(production.adopt(config,dict(batch='bank')),'reviewed')
                frozen=production.adoption_sources(config,
                    {'dark_photon_senscalc.py','run_dark_photon_senscalc_gen.py'},set(),source_directory=root/'bank')
            self.assertTrue(all(Path(row['frozen_copy']).parent==reviewed for row in frozen.values()))
            self.assertEqual({name:sys.modules.get(name) for name in before},before)

    def test_preparer_binding_covers_validation_and_rejects_a_different_gen(self):
        import prepare_dark_photon_production as preparer
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);bank=root/'bank';bank.mkdir();gen=bank/'gen';gen.mkdir()
            original=root/'original';reviewed=root/'reviewed';original.mkdir();reviewed.mkdir()
            (reviewed/'dark_photon_senscalc.py').write_text('VALUE="reviewed"\n')
            (reviewed/'run_dark_photon_senscalc_gen.py').write_text('from dark_photon_senscalc import VALUE\n')
            def pin(path,value):
                path.write_text(json.dumps(value));return dict(path=str(path),sha256=production.durable.sha(path))
            status=pin(bank/'source_status.json',dict(complete=True));contract=pin(gen/'contract.json',{})
            binding=root/'binding.json';pin(binding,dict(schema='shift-reviewed-source-accounting-preparation-v1',
                configuration=dict(tools=str(original)),source_directory=str(bank),source_status=status,contract=contract))
            plan=root/'plan.json';pin(plan,dict(points=[dict(gen_directory=str(gen))]))
            args=SimpleNamespace(preparation=plan,reviewed_source_accounting=binding,
                reviewed_source_accounting_sha256=production.durable.sha(binding))
            with patch.object(production,'source_accounting_export',return_value=reviewed/'export.py'), \
                    patch.object(preparer,'prepare_with_accounting',side_effect=lambda args:sys.modules['run_dark_photon_senscalc_gen'].VALUE):
                self.assertEqual(preparer.prepare(args),'reviewed')
                pin(plan,dict(points=[dict(gen_directory='/different/GEN')]))
                with self.assertRaisesRegex(ValueError,'another GEN cohort'):preparer.prepare(args)

    def test_aggregates_without_recovered_sources_keep_the_original_operational_helpers(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);config,bank,tools,original=self.fixture(root)
            with patch.object(production,'__file__',str(root/'operations/run.py')):
                self.assertEqual(production.main_aggregate_tools(config,[dict(plan_path='/other/production/plan.json')]),root/'operations')

    def test_missing_aggregate_import_does_not_leave_an_incomplete_stage(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);sources=[]
            row=dict(point='p',mass_gev=.25,epsilon=.1,proper_ctau_mm=1,sampling_epsilon=.1)
            for i in range(2):
                plan=root/('source'+str(i)+'.json')
                plan.write_text(json.dumps(dict(main_histogrammer=True,points=[row])))
                sources.append(dict(plan_path=str(plan),plan_sha256=production.durable.sha(plan)))
            target=root/'aggregate'
            with self.assertRaises(FileNotFoundError):
                production.durable.main_histogram_aggregate_plan(dict(tools=str(root/'missing')),'p',sources,target)
            self.assertFalse(target.exists())

    def source_retry(self,root):
        config,bank,tools,original=self.fixture(root)
        write=lambda p,v:p.write_text(json.dumps(v))
        pin=lambda p:dict(path=str(p),sha256=production.durable.sha(p))
        old=dict(cluster=10,task=str(bank));write(bank/'submission.json',old)
        target=root/'replacement';target.mkdir();description=target/'controller.sub';description.write_text('frozen worker')
        replacement=dict(cluster=20,task=str(target),submit_sha256=production.durable.sha(description),utc='2026-10-29T08:00:00+00:00')
        write(target/'submission.json',replacement)
        write(target/'removed.json',dict(ClusterId=10,JobStatus=3,NumJobStarts=1,RemoveReason='SYSTEM_PERIODIC_REMOVE'))
        proof=dict(schema='shift-reviewed-meson-source-retry-v1',complete=True,batch='a',
            original_directory=str(bank),original_batch=config['batches'][0],original_submission=pin(bank/'submission.json'),
            original_native_removed=pin(target/'removed.json'),source_accounting_authority=config['reviewed_source_accounting'],
            additional_source_collisions=0,removed_recovery_payload_executed=False,
            replacement_submission=pin(target/'submission.json'),replacement_submit_description=pin(description))
        authority=target/'authority.json';write(authority,proof)
        config['reviewed_source_recovery_receipts']=dict(a=pin(authority))
        return config,bank,old,replacement,proof,authority

    def test_fresh_source_clock_keeps_original_exposure_and_requires_old_owner_absence(self):
        with tempfile.TemporaryDirectory() as temporary:
            config,bank,old,new,proof,authority=self.source_retry(Path(temporary))
            with patch.object(production.durable,'jobs',return_value=[]):
                self.assertEqual(production.reviewed_source_receipt(config,config['batches'][0],old),new)
            with patch.object(production.durable,'jobs',return_value=[dict(ClusterId=10)]):
                with self.assertRaisesRegex(RuntimeError,'remains live'):
                    production.reviewed_source_receipt(config,config['batches'][0],old)
            proof['additional_source_collisions']=50;authority.write_text(json.dumps(proof))
            config['reviewed_source_recovery_receipts']['a']['sha256']=production.durable.sha(authority)
            with self.assertRaisesRegex(ValueError,'original exposure'):
                production.reviewed_source_receipt(config,config['batches'][0],old)

    def test_source_storage_credit_uses_new_native_log_and_waits_for_terminal_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            config,bank,old,new,proof,authority=self.source_retry(Path(temporary))
            (bank/'events.log').write_text('005 (20.000.000)\nNormal termination (return value 0)\n...\n')
            (Path(new['task'])/'events.log').write_text('000 (20.000.000)\n...\n')
            with patch.object(production.durable,'jobs',return_value=[]):
                self.assertEqual(production.committed_meson_source_bytes(config,config['batches'][0],dict(source_cluster=20),set()),0)

    def test_successful_fresh_source_archives_only_its_reviewed_old_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            config,bank,old,new,proof,authority=self.source_retry(Path(temporary))
            known=dict(stage='GEN',cluster=10,error='Original reviewed failure')
            unknown=dict(stage='GEN',cluster=99,error='Unrelated failure remains visible')
            state=dict(failed_batches=dict(a=[known,unknown]));entry=dict(failure=known)
            with patch.object(production,'native_status',return_value='complete'), \
                 patch.object(production,'reconcile_runtime_failures'):
                self.assertEqual(production.batch_job_status(config,state,config['batches'][0],entry,new,'GEN'),'complete')
            self.assertEqual(state['failed_batches']['a'],[unknown])
            self.assertEqual(state['resolved_failed_batches']['a'][0]['failures'],[known])
            self.assertNotIn('failure',entry)

    def test_paid_source_proof_avoids_repeated_history_but_never_overrides_live_jobs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);config,bank,tools,original=self.fixture(root)
            receipt=dict(cluster=10,task=str(bank));(bank/'submission.json').write_text(json.dumps(receipt))
            (bank/'gen').mkdir();(bank/'gen/manifest.json').write_text('{}');(bank/'gen/root_publication.json').write_text('{}')
            event='005 (10.000.000)\nNormal termination (return value 0)'
            (bank/'events.log').write_text(event+'\n...\n')
            fields=dict(source_status_sha256=bank/'source_status.json',submission_sha256=bank/'submission.json',
                        manifest_sha256=bank/'gen/manifest.json',publication_sha256=bank/'gen/root_publication.json')
            paid=root/'science/committed_source_storage';paid.mkdir()
            proof=dict(cluster=10,native_zero_exit=event,**{key:production.durable.sha(path) for key,path in fields.items()})
            (paid/'10.json').write_text(json.dumps(proof))
            seed=root/'seed.json';seed.write_text(json.dumps(dict(schema='shift-reviewed-meson-successful-native-seed-v1',jobs={})))
            config['reviewed_terminal_native_jobs']=dict(path=str(seed),sha256=production.durable.sha(seed))
            with patch.object(production.durable,'jobs',return_value=[]),patch.object(production.durable,'run_condor') as history:
                self.assertEqual(production.native_status(config,receipt),'complete');history.assert_not_called()
            with patch.object(production.durable,'jobs',return_value=[dict(ClusterId=10,ProcId=0,Iwd=str(bank),JobStatus=2)]):
                self.assertEqual(production.native_status(config,receipt),'active')
            (bank/'gen/manifest.json').write_text('{"changed":true}')
            self.assertIsNone(production.cached_terminal_record(config,receipt))

    def test_worker_claim_uses_sealed_chunks_then_verified_selection_then_full_GEN(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);config,bank,tools,original=self.fixture(root);batch=config['batches'][0]
            batch.update(point='p',events_per_job=10);config['detector_workers_per_point']=100
            (bank/'source_status.json').write_text(json.dumps(dict(complete=True,events=100,parent=dict(gen_sha256='GEN'))))
            self.assertEqual(production.detector_slot_claim(config,batch),12)
            gen=bank/'gen';gen.mkdir();selected=gen/'material_selected_source.json'
            selected.write_text(json.dumps(dict(gen_sha256='GEN',selected_indices=list(range(15)))))
            (gen/'material_selection_receipt.json').write_text(json.dumps(dict(complete=True,descriptor_sha256=production.durable.sha(selected))))
            self.assertEqual(production.detector_slot_claim(config,batch),4)
            stage=bank/'production';stage.mkdir();plan=stage/'plan.json'
            plan.write_text(json.dumps(dict(points=[dict(point='p',chunks=[{}, {}, {}])])) )
            (stage/'seal.json').write_text(json.dumps(dict(files={str(plan):production.durable.sha(plan)})))
            self.assertEqual(production.detector_slot_claim(config,batch),5)
            plan.write_text('{"changed":true}')
            with self.assertRaisesRegex(ValueError,'sealed production plan'):production.detector_slot_claim(config,batch)


if __name__=='__main__':unittest.main()
