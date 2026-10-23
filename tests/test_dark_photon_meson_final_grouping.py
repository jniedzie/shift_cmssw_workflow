# Code style: AI-code. AI-generated final point grouping and publication guards.
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import run_dark_photon_meson_production as production


class FinalPointGrouping(unittest.TestCase):
    def fixture(self,root):
        batches=[dict(batch='canary',point='m0p3_prompt',operational_canary=True),
            dict(batch='bulk_a',point='m0p3_prompt',operational_canary=False),
            dict(batch='bulk_b',point='m0p3_prompt',operational_canary=False),
            dict(batch='audit',point='m0p3_prompt',operational_canary=False,gen_qualification_only=True)]
        return dict(directory=str(root),batches=batches,regroup_complete_points=True,
            aggregate_group_events=4000,conditions=dict(sha256='a'*64))

    def report(self,root,batch,events):
        path=root/'batches'/batch/'production/completed_points/m0p3_prompt.json'
        path.parent.mkdir(parents=True);path.write_text(json.dumps(dict(complete=True,events=events)))

    def test_readiness_counts_all_sources_including_zero_and_excludes_gen_audits(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);config=self.fixture(root)
            self.report(root,'canary',56);self.report(root,'bulk_a',0)
            row=production.point_grouping_readiness(config)['m0p3_prompt']
            self.assertEqual(row,dict(total_sources=3,ready_sources=2,events=56,complete=False,target_group_events=4000))
            self.report(root,'bulk_b',700)
            row=production.point_grouping_readiness(config)['m0p3_prompt']
            self.assertTrue(row['complete']);self.assertEqual(row['events'],756)

    def test_bulk_waits_for_final_whole_point_but_canary_still_qualifies(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);config=self.fixture(root);self.report(root,'canary',56)
            primitive=dict(events=56)
            with patch.object(production.durable,'authority',return_value={'source':'actual-parent'}), \
                 patch.object(production.durable,'reconcile_aggregate') as aggregate:
                self.assertIsNone(production.cumulative_result(config,{},config['batches'][1],None,primitive))
                self.assertEqual(production.cumulative_result(config,{},config['batches'][0],None,primitive),primitive)
            aggregate.assert_not_called()

    def test_final_new_campaign_promotes_below_legacy_event_floor_only_with_all_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);config=self.fixture(root);config['minimum_histogram_events_before_latest']={'m0p3_prompt':1300}
            result=dict(events=756,source_plans=[str(root/'batches'/batch/'production/plan.json') for batch in ['canary','bulk_a','bulk_b']])
            floor=production.histogram_promotion_floor(config,config['batches'][1],result)
            self.assertTrue(floor['eligible']);self.assertTrue(floor['final_planned_point_complete'])
            self.assertFalse(production.histogram_promotion_floor(config,config['batches'][0],result)['eligible'])
            result['source_plans'].pop()
            self.assertFalse(production.histogram_promotion_floor(config,config['batches'][1],result)['eligible'])

    def test_shared_aggregation_allocation_is_protected_before_new_source_admission(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'config.json').write_text('{}');sm=root/'sm.json';sm.write_text('{}')
            proof=dict(schema='shift-dp-shared-aggregation-reservation-v1',complete=True,science_directory=str(root),
                science_config_sha256=production.durable.sha(root/'config.json'),additional_reserved_bytes=22000000000,
                source_envelope_bytes=65000000000,combined_DP_reservation_bytes=117000000000,
                protected_sm_external_envelope_bytes=150000000000,
                shared_SM_registration=dict(path=str(sm),sha256=production.durable.sha(sm)))
            path=root/'reservation.json';path.write_text(json.dumps(proof))
            config=dict(directory=str(root),regroup_complete_points=True,merge_source_histograms=False,aggregate_group_events=4000,
                aggregate_storage_reservation_bytes=22000000000,maximum_meson_forecast_bytes=65000000000,
                aggregation_storage_registration=dict(path=str(path),sha256=production.durable.sha(path)),
                minimum_eos_free_bytes=50000000000,minimum_eos_free_files=20000)
            with patch.object(production,'adaptive_reservation',return_value=0), \
                 patch.object(production,'additional_reservations',return_value=(0,[])):
                allowed,evidence=production.storage_gate(config,dict(free_bytes=70000000000,free_files=30000))
                self.assertFalse(allowed);self.assertEqual(evidence['required_free_bytes'],72500000000)
                self.assertTrue(production.storage_gate(config,dict(free_bytes=75000000000,free_files=30000))[0])
            sm.write_text('{"changed":true}')
            with self.assertRaisesRegex(ValueError,'authority changed'):
                production.aggregation_reservation(config)

    def test_real_regrouping_canary_must_finish_before_bulk_gate_can_open(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);config=self.fixture(root);config.update(simultaneous_sources=1,automatic_bulk_admission={})
            state=dict(batches={'canary':dict(source_cluster=1,published={'events':56}),'bulk_a':{},'bulk_b':{},'audit':{}})
            with patch.object(production,'qualify_regrouping_canary',return_value=False) as qualify, \
                 patch.object(production.durable,'quota',return_value={}), \
                 patch.object(production,'storage_gate',return_value=(True,{})), \
                 patch.object(production.durable,'submission') as submit, \
                 patch('dark_photon_automatic_admission.maybe_create_bulk_gate') as gate:
                # Audit scout is already complete; no pending GEN source may evade the new gate.
                config['batches']=config['batches'][:3]
                production.step(config,state)
            qualify.assert_called_once();gate.assert_not_called();submit.assert_not_called()
            self.assertFalse(state['canary_published'])


class RealRegroupingGate(unittest.TestCase):
    def fixture(self,root):
        directory=root/'regrouping_canary/v0001';directory.mkdir(parents=True)
        plan=dict(campaign='20261023-regroup-canary',histogram_only=False,group_events=4000,
            single_source_regrouping_canary=True,points=[dict(point='m0p3_prompt',events=56,
                final_output_base='/eos/user/test/data/darkphoton_m0p3_prompt/20261023-regroup-canary')])
        (directory/'plan.json').write_text(json.dumps(plan));(directory/'seal.json').write_text('{}')
        (directory/'aggregate.sub').write_text('frozen test worker')
        config=dict(directory=str(root),batches=[dict(batch='canary',point='m0p3_prompt',operational_canary=True)],
            conditions=dict(sha256='a'*64))
        return config,directory

    def test_native_running_worker_cannot_qualify_regrouping(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);config,directory=self.fixture(root);state={}
            with patch.object(production.durable,'authority',return_value={}), \
                 patch.object(production.durable,'aggregate_record',return_value=dict(directory=str(directory))), \
                 patch.object(production.durable,'submission',return_value=dict(cluster=123)), \
                 patch.object(production,'native_status',return_value='active'), \
                 patch('watch_dark_photon_production.completed_histogram') as readback:
                self.assertFalse(production.qualify_regrouping_canary(config,state))
            self.assertFalse(state['regrouping_canary']['complete']);readback.assert_not_called()

    def test_terminal_success_needs_verified_histogram_and_reuses_unchanged_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);config,directory=self.fixture(root);state={}
            report=directory/'completed_points/m0p3_prompt.json';report.parent.mkdir();report.write_text('{}')
            result=dict(campaign='20261023-regroup-canary',events=56,point_receipt_path=str(report),
                point_receipt_sha256=production.durable.sha(report),histogram_sha256='b'*64)
            with patch.object(production.durable,'authority',return_value={}), \
                 patch.object(production.durable,'aggregate_record',return_value=dict(directory=str(directory))), \
                 patch.object(production.durable,'submission',return_value=dict(cluster=123)), \
                 patch.object(production,'native_status',return_value='complete'), \
                 patch('watch_dark_photon_production.EosRpc'), \
                 patch('watch_dark_photon_production.completed_histogram',return_value=result) as readback:
                self.assertTrue(production.qualify_regrouping_canary(config,state))
                self.assertTrue(production.qualify_regrouping_canary(config,state))
            readback.assert_called_once()
            self.assertTrue(state['regrouping_canary']['complete'])
            self.assertFalse(state['regrouping_canary']['latest_alias_changed'])
            report.write_text('{"changed":true}')
            with self.assertRaisesRegex(ValueError,'receipt changed'):
                production.qualify_regrouping_canary(config,state)


class HistoricalAnchor(unittest.TestCase):
    def fixture(self,root):
        calibration=dict(schema='shift-meson-calibration-v1',production_mechanism='eta-etaprime-radiative',
            points=[dict(mass_gev=.3,reference_epsilon=.001,mean_beta_gamma_reference=500.,physical_width_reference=dict(ctau_mm=.01))])
        path=root/'calibration.json';path.write_text(json.dumps(calibration))
        sample=dict(schema='shift-dark-photon-sample-v1',point='m0p3_prompt',original_point='m0p3_prompt',
            naming='mass-and-mean-lab-flight',selector_kind='mean_lab_flight',mass_gev=.3,epsilon=.001,
            sampling_epsilon=.001,target_mean_lab_flight_m=None,mean_lab_flight_m=None,
            calibration_mean_beta_gamma=450.,mean_lab_flight_is_estimate=False,proper_ctau_mm=.01,
            reference_mean_lab_flight_m=.0045,production_mechanism='meson-radiative',calibration_sha256='new')
        old=dict(sample,calibration_mean_beta_gamma=500.,reference_mean_lab_flight_m=.005,
            production_mechanism='eta-etaprime-radiative',calibration_sha256=production.durable.sha(path))
        config=dict(eos_base='/eos/user/test/data',production_mechanism='meson-radiative',calibration_authority=dict(sha256='new'),
            historical_anchor_calibration=dict(path=str(path),sha256=production.durable.sha(path)))
        cache=root/'cache';cache.mkdir()
        class Transport:
            host='root://eosuser.cern.ch'
            cache_directory=cache
            def metadata(self,path):return dict(size=len(json.dumps(old).encode()),status='file')
            def command(self,argv):Path(argv[-1]).write_text(json.dumps(old))
        return config,sample,old,Transport()

    @patch.object(production,'prior_meson_configuration',return_value=None)
    def test_only_pinned_historical_calibration_is_retained_without_global_overwrite(self,prior):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);config,sample,old,transport=self.fixture(root)
            with patch('export_dark_photon_cross_sections.publish_file_rpc') as publish:
                proof=production.common_sample_anchor(config,dict(point=sample['point']),sample,root/'new.json',transport)
            self.assertTrue(proof['retained_existing']);publish.assert_not_called()
            for key,value in [('production_mechanism','unknown'),('calibration_sha256','wrong')]:
                original=old[key];old[key]=value
                with self.subTest(key=key),self.assertRaisesRegex(ValueError,'pinned creator/calibration'):
                    production.common_sample_anchor(config,dict(point=sample['point']),sample,root/'new.json',transport)
                old[key]=original
            old['epsilon']=.002
            with self.assertRaisesRegex(ValueError,'different physical'):
                production.common_sample_anchor(config,dict(point=sample['point']),sample,root/'new.json',transport)


if __name__=='__main__':unittest.main()
