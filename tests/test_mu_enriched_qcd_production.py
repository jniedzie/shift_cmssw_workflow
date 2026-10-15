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
