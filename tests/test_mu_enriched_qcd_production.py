# Code style: AI-code. AI-generated source-exposure and six-bin launch regressions.
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from control_mu_enriched_qcd_gen import aggregate, cohort
from prepare_mu_enriched_qcd_production import prepare
from qcd_mu_enrichment import PROFILE


def receipt(name, source, tried, saved):
    # A fixed non-diffractive source has sigma=100mb. Its ordinary bin
    # source acceptance and external muon selection are different stages.
    return dict(request={'id':name}, semantic_audit=dict(bounds=[0,1],
        generator_profile=PROFILE, sigma_nd_mb=100., source_accepted=source,
        full_source_trial_denominator=tried, events=saved,
        native_generator_run_info={'internal_xsec_pb':100.e9*source/tried}))


class ProductionTest(unittest.TestCase):
    def test_cold_control_handoff_reuses_exact_completed_originals_without_rerun(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name)
            assets=root/'assets'
            assets.mkdir()
            for asset in ('runtime.tar.gz','pluginShiftMpiWeightedProposalHook.so','libShiftMpiTailProposal.so'):
                (assets/asset).write_bytes(b'fixture')
            reference=root/'reference.json'
            reference.write_text(json.dumps(dict(generator_profile=PROFILE,samples=[{'stratum':'qcd_5to10'}])))
            old=root/'old'
            prepare(old,assets,reference,'/eos/user/j/jniedzie/shift_cmssw',200)
            old_weighted=old/'weighted/manifest.json'
            weighted_manifest=json.loads(old_weighted.read_text())
            weighted_manifest['budget_safety_factor']=2.
            old_weighted.write_text(json.dumps(weighted_manifest,indent=2)+'\n')
            manifest=json.loads((old/'ordinary/manifest.json').read_text())
            request=manifest['jobs'][0]
            import hashlib
            digest=hashlib.sha256((old/'ordinary/manifest.json').read_bytes()).hexdigest()
            record=dict(schema='shift-mu-enriched-ordinary-gen-receipt-v1',
                complete=True,publication_complete=True,exit_status=0,request=request,
                manifest_sha256=digest,source_inputs=manifest['inputs'],generator_profile=PROFILE,
                event_count=1,publication_directory=request['output_base']+'/'+request['id'],
                semantic_audit=dict(generator_profile=PROFILE,
                    source_model_settings_sha256=manifest['source_model_settings_sha256'],
                    complete=True,healthy=True,source_accepted=request['trials'],events=1,
                    no_second_filter_efficiency=True,event_ids=[[request['run'],1,9]],weights=[1.]))
            path=old/'ordinary/results'/(request['id']+'.json')
            path.write_text(json.dumps(record))
            new=root/'new'
            summary=prepare(new,assets,reference,'/eos/user/j/jniedzie/shift_cmssw',200,resume_from=old)
            self.assertEqual(summary['reused_completed_nodes'],1)
            self.assertEqual(old_weighted.read_bytes(),(new/'weighted/manifest.json').read_bytes())
            self.assertEqual(path.read_bytes(),(new/'ordinary/results'/path.name).read_bytes())
            dag=(new/'production.dag').read_text()
            self.assertNotIn('L0000',dag)
            self.assertNotIn('DONE ',dag)
            self.assertIn('PARENT L0101 L0202 L0303 L0404 CHILD LOW_GATE',dag)
            manifest['jobs'][0]['seed']+=1
            (old/'ordinary/manifest.json').write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError,'cannot change scientific'):
                prepare(root/'changed',assets,reference,'/eos/user/j/jniedzie/shift_cmssw',200,resume_from=old)

    def test_failed_or_unknown_worker_exit_cannot_pass_the_completion_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory)
            (base/'results').mkdir()
            request=dict(id='canary_qcd',stage='canary')
            for exit_status in (1,None):
                record=dict(schema='shift-mu-enriched-ordinary-gen-receipt-v1',
                            complete=True,publication_complete=True,
                            exit_status=exit_status,semantic_audit={})
                (base/'results/canary_qcd.json').write_text(json.dumps(record))
                with self.subTest(exit_status=exit_status),self.assertRaisesRegex(ValueError,'Invalid completed'):
                    cohort(dict(jobs=[request]),'a'*64,base,'canary')

    def test_pool_all_native_trials_and_apply_external_filter_once(self):
        result = aggregate([receipt('a',100,200,80), receipt('b',100,300,60)])
        self.assertEqual(result['full_source_trial_denominator'],500)
        self.assertEqual(result['framework_source_events'],200)
        self.assertEqual(result['saved_filtered_events'],140)
        self.assertAlmostEqual(result['selected_cross_section_pb'],28.e9)
        self.assertAlmostEqual(result['event_weight_pb_by_chunk']['a'],.2e9)
        self.assertAlmostEqual(result['event_weight_pb_by_chunk']['b'],.2e9)
        self.assertAlmostEqual(result['raw_filter_efficiency'],.7)

    def test_zero_filtered_chunk_keeps_its_entire_trial_denominator(self):
        result = aggregate([receipt('a',100,200,0), receipt('b',100,300,60)])
        self.assertEqual(result['full_source_trial_denominator'],500)
        self.assertAlmostEqual(result['selected_cross_section_pb'],12.e9)

    def test_mixed_bin_profile_or_source_is_rejected(self):
        for field, value in [('bounds',[1,2]), ('generator_profile','ordinary'), ('sigma_nd_mb',101.)]:
            rows = [receipt('a',100,200,80), receipt('b',100,300,60)]
            rows[1]['semantic_audit'][field] = value
            with self.assertRaises(ValueError):
                aggregate(rows)

    def test_all_six_bins_have_disjoint_ids_and_physical_paths(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            assets = root/'assets'
            assets.mkdir()
            for asset in ('runtime.tar.gz','pluginShiftMpiWeightedProposalHook.so','libShiftMpiTailProposal.so'):
                (assets/asset).write_bytes(b'fixture')
            ref = root/'reference.json'
            ref.write_text(json.dumps(dict(generator_profile=PROFILE, samples=[{'stratum':'qcd_5to10'}])))
            output = root/'production'
            summary = prepare(output,assets,ref,'/eos/user/j/jniedzie/shift_cmssw',200)
            ordinary = json.loads((output/'ordinary/manifest.json').read_text())
            weighted = json.loads((output/'weighted/manifest.json').read_text())
            rows = ordinary['jobs']+weighted['jobs']
            self.assertEqual(len({row['run'] for row in rows}),len(rows))
            self.assertEqual(len({row['seed'] for row in rows}),len(rows))
            self.assertEqual({tuple(row['bounds']) for row in rows},
                             {(0,1),(1,2),(2,5),(5,10),(10,20),(20,-1)})
            self.assertTrue(all('/qcd_pThat' in row['output_base'] and
                                '/20261015_mu_enriched_v1/GEN' in row['output_base'] for row in rows))
            self.assertEqual(sum(row['trials'] for row in ordinary['jobs'] if row['stage']=='production'),500000)
            dag = (output/'production.dag').read_text()
            self.assertIn('MAXJOBS workers 200',dag)
            self.assertIn('PARENT LOW_GATE CHILD L0001',dag)
            self.assertIn('PARENT HIGH_GATE CHILD H0005',dag)
            self.assertFalse(summary['submitted'])


if __name__ == '__main__':
    unittest.main()
