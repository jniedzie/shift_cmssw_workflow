# Code style: AI-code. AI-generated automatic empty-draw exposure tests.
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import run_dark_photon_meson_production as production
from shift_production_selection import default_policy
from shift_material_preselection import checksum
from dark_photon_material_selection import validate_rate_only_report


class EmptyAdmission(unittest.TestCase):
    def fixture(self,root):
        batch=dict(batch='empty',point='m0p9_lab_1000m',campaign='20261023-empty',operational_canary=False,
            final_output_base='/eos/user/t/test/darkphoton_m0p9_lab_1000m/20261023-empty')
        directory=root/'batches/empty';gen=directory/'gen';gen.mkdir(parents=True)
        (gen/'contract.json').write_text('{}')
        parent=dict(events=2,gen_sha256='a'*64,event_ids=[[100,1,1],[100,1,2]],native_sumw=2.,native_sumw2=2.)
        policy=default_policy('empty-test','frozen-before-reco')
        descriptor=dict(gen_sha256='a'*64,event_ids=parent['event_ids'],weights=[1.,1.],
            selected_indices=[],sampling_probabilities=[],production_selection=dict(
                schema='shift-production-material-selection-v1',policy=policy,policy_sha256=checksum(policy),
                ledger_sha256='b'*64,track_all_hadron_decays=True,maximum_untracked_hadron_flight_mm=1.e-5))
        (gen/'material_selected_source.json').write_text(json.dumps(descriptor))
        (gen/'material_selection_receipt.json').write_text(json.dumps(dict(complete=True,
            original_exposure_preserved=True,descriptor_sha256=production.durable.sha(gen/'material_selected_source.json'))))
        status=dict(complete=True,parent=parent,publication={},metadata_sha256={str(gen/'contract.json'):production.durable.sha(gen/'contract.json')})
        (directory/'source_status.json').write_text(json.dumps(status))
        config=dict(directory=str(root),tools='/frozen/science/tools',batches=[batch])
        row=dict(point=batch['point'],events=2)
        return config,batch,status,row

    def test_zero_draw_closes_once_with_full_parent_and_exported_rates(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);config,batch,status,row=self.fixture(root);parent=copy.deepcopy(status['parent'])
            with patch.object(production,'adopted_row',return_value=row), \
                 patch.object(production.durable,'run_logged') as exporter:
                first=production.complete_empty_admission(config,batch,status)
                second=production.complete_empty_admission(config,batch,status)
            self.assertEqual(first,second);exporter.assert_called_once()
            self.assertIn('--publish',exporter.call_args.args[0])
            p=root/'batches/empty/production';plan=production.read(p/'plan.json');report=production.read(p/'completed_points'/(batch['point']+'.json'))
            validate_rate_only_report(report,plan['points'][0],parent,production.durable.sha(p/'plan.json'),production.durable.sha(p/'seal.json'))
            self.assertEqual(report['full_gen_events'],2)
            self.assertEqual(report['union']['parent_gen'],parent)
            self.assertEqual(status['parent'],parent)
            self.assertEqual(plan['detector_chunks'],0)
            self.assertFalse((p/'submission.json').exists())
            self.assertNotIn('score_histograms',report)

    def test_last_empty_cohort_waits_for_existing_histogram_denominator_update(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);config,batch,status,row=self.fixture(root)
            other=dict(batch='positive',point=batch['point'],operational_canary=False)
            config['batches'].append(other)
            report=root/'batches/positive/production/completed_points'/(batch['point']+'.json')
            report.parent.mkdir(parents=True);report.write_text('{"complete":true,"events":5}')
            entry={};state={}
            with patch.object(production,'adopted_row',return_value=row), \
                 patch.object(production.durable,'run_logged'), \
                 patch.object(production,'publish_batch',side_effect=[None,dict(events=5,campaign='new-full-denominator')]) as publish:
                self.assertTrue(production.close_empty_admission(config,state,batch,entry,None))
                self.assertNotIn('published',entry)
                self.assertTrue(entry['detector_native_complete'])
                self.assertEqual(entry['stage'],'waiting_for_rate_only_histogram_update')
                self.assertTrue(production.close_empty_admission(config,state,batch,entry,None))
            self.assertEqual(publish.call_count,2)
            self.assertTrue(entry['published']['rate_only'])
            self.assertEqual(entry['published']['cumulative_histogram']['campaign'],'new-full-denominator')

    def test_zero_canary_cannot_open_the_bulk_gate(self):
        with tempfile.TemporaryDirectory() as temporary:
            config,batch,status,row=self.fixture(Path(temporary));batch['operational_canary']=True
            with self.assertRaisesRegex(ValueError,'cannot qualify'):
                production.close_empty_admission(config,{},batch,{},None)

    def test_never_replace_an_existing_detector_production(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);config,batch,status,row=self.fixture(root)
            p=root/'batches/empty/production';p.mkdir();(p/'submission.json').write_text('{"cluster":123}')
            with patch.object(production,'adopted_row',return_value=row):
                with self.assertRaisesRegex(ValueError,'existing detector deployment'):
                    production.complete_empty_admission(config,batch,status)
            self.assertEqual((p/'submission.json').read_text(),'{"cluster":123}')


if __name__=='__main__':
    unittest.main()
