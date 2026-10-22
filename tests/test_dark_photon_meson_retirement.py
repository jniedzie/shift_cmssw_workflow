# Code style: AI-code. AI-generated partial-production retirement regressions.
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import run_dark_photon_meson_production as meson
import run_dark_photon_statistics as durable


class MesonRetirement(unittest.TestCase):
    def pin(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
        return dict(path=str(path), sha256=durable.sha(path))

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.old = self.root/'old'; self.new = self.root/'new'; self.owner = self.root/'old_owner'
        model = self.pin(self.old/'model.json', {})
        self.rows = [dict(batch=name, point='m0p3_prompt', source_collisions=5000)
                     for name in ('published', 'failed', 'gen_only', 'unsubmitted')]
        self.old_config = dict(schema='shift-dp-meson-production-v1', directory=str(self.old),
            tools=str(self.old/'tools'), model_card=model['path'], batches=self.rows)
        self.old_pin = self.pin(self.old/'config.json', self.old_config)
        seal = self.pin(self.old/'seal.json', dict(files={self.old_pin['path']: self.old_pin['sha256']}))
        replacement = self.pin(self.new/'config.json', dict(directory=str(self.new)))
        owner_config = self.pin(self.owner/'config.json', dict(self.old_config,physics_tools=self.old_config['tools']))
        wrapper = self.owner/'lcg_controller.sh'; wrapper.write_text('fixed launch')
        owner_seal = self.pin(self.owner/'seal.json',dict(files={
            owner_config['path']:owner_config['sha256'],str(wrapper):durable.sha(wrapper)}))
        submit = self.owner/'controller.sub'
        submit.write_text('initialdir = '+str(self.owner)+'\nexecutable = '+str(wrapper)+
                          '\narguments = '+owner_seal['sha256']+'\n')
        owner_receipt = self.pin(self.owner/'submission.json',dict(
            cluster=100,task=str(self.owner),submit_sha256=durable.sha(submit)))
        self.native = [dict(ClusterId=100,ProcId=0,Iwd=str(self.owner),JobStatus=3,
                            Cmd=str(wrapper),Args=owner_seal['sha256'])]
        records = []
        state = dict(error='A source failed; production is partial',complete=False,batches={})
        for index, row in enumerate(self.rows):
            name = row['batch']; directory = self.old/'batches'/name
            record = dict(batch=name,disposition=name,source_status=None,retained_source={})
            if name != 'unsubmitted':
                self.pin(directory/'submission.json',dict(cluster=11+index,task=str(directory)))
                source = dict(batch=name,point=row['point'],source_collisions=5000,
                              complete=name!='failed',events=20,
                              ledger_publication=dict(path='/eos/ledger/'+name,sha256='c'*64),
                              metadata_archive=dict(path='/eos/archive/'+name,sha256='d'*64))
                if name == 'failed': source['error'] = 'Preserved native failure'
                record['source_status'] = self.pin(directory/'source_status.json',source)
                record['retained_source'] = {key:source[key] for key in ('ledger_publication','metadata_archive')}
                self.native.append(dict(ClusterId=11+index,ProcId=0,Iwd=str(directory),JobStatus=4,
                                        ExitCode=1 if name=='failed' else 0,ExitBySignal=False))
            if name == 'gen_only': record['detector_output_used'] = False
            if name == 'published':
                production = directory/'production'
                plan = self.pin(production/'plan.json',dict(points=[dict(point=row['point'],events=20)]))
                production_seal = self.pin(production/'seal.json',dict(files={plan['path']:plan['sha256']}))
                output = '/eos/data/darkphoton_m0p3_prompt/old_campaign'
                histogram = dict(path=output+'/histograms_merged/ntuple_0.root',sha256='a'*64)
                point = self.pin(production/'completed_points/m0p3_prompt.json',dict(
                    complete=True,point=row['point'],events=20,plan_sha256=plan['sha256'],
                    seal_sha256=production_seal['sha256'],final_output_base=output,
                    score_histograms=dict(marker=dict(complete=True,published=dict(histograms=histogram)))))
                published = dict(batch=name,point_receipt_path=point['path'],point_receipt_sha256=point['sha256'],
                                 events=20,campaign='old_campaign',histogram_path=histogram['path'],
                                 histogram_sha256=histogram['sha256'],published_utc='2026-01-01T00:00:00+00:00')
                state['batches'][name] = dict(published=published)
                record['point_receipt'] = point
            records.append(record)
        self.pin(self.old/'state.json',state)
        final = self.pin(self.root/'final_state.json',state)
        self.native_pin = self.pin(self.root/'native.json',dict(
            schema='shift-reviewed-meson-retirement-native-v1',queue_absent=True,jobs=self.native))
        self.proof = dict(schema='shift-reviewed-meson-retirement-v1',allocation_canceled=True,
            replacement_configuration=replacement,native_snapshot=self.native_pin,productions=[dict(
                configuration=self.old_pin,seal=seal,final_state=final,batches=records,
                controllers=[dict(configuration=owner_config,seal=owner_seal,submission=owner_receipt)])])
        self.authority = self.pin(self.root/'retirement.json',self.proof)
        self.config = dict(directory=str(self.new))

    def evaluate(self):
        with patch.object(durable,'jobs',return_value=[]) as query:
            result = meson.reviewed_meson_retirement(self.config,self.authority,self.old_pin)
            self.assertIn(str(self.old),query.call_args.args[1])
            self.assertIn(str(self.owner),query.call_args.args[1])
            self.assertIn('DAGManJobId == 100',query.call_args.args[1])
            return result

    def update_proof(self):
        self.authority = self.pin(self.root/'retirement.json',self.proof)

    def test_partial_retirement_preserves_failures_unused_gen_and_published_campaigns(self):
        before = (self.old/'state.json').read_bytes()
        result = self.evaluate()
        self.assertEqual(result['campaigns'],{'m0p3_prompt':['old_campaign']})
        self.assertEqual(result['published_batches'][str(self.old)],['published'])
        self.assertEqual((self.old/'state.json').read_bytes(),before)
        self.assertFalse(json.loads(before)['complete'])
        self.assertTrue((self.old/'batches/failed/source_status.json').is_file())
        self.assertTrue((self.old/'batches/gen_only/source_status.json').is_file())

    def test_live_job_or_unknown_scheduler_blocks_retirement(self):
        for response in ([dict(ClusterId=100)], [dict(ClusterId=900,DAGManJobId=11)]):
            with self.subTest(response=response),patch.object(durable,'jobs',return_value=response):
                with self.assertRaisesRegex(RuntimeError,'still live'):
                    meson.reviewed_meson_retirement(self.config,self.authority,self.old_pin)
        with patch.object(durable,'jobs',side_effect=RuntimeError('unknown scheduler')):
            with self.assertRaisesRegex(RuntimeError,'unknown scheduler'):
                meson.reviewed_meson_retirement(self.config,self.authority,self.old_pin)

    def test_ambiguous_source_or_detector_intent_is_not_an_unsubmitted_batch(self):
        for relative in ('submission_intent.json','production/submission_intent.json'):
            with self.subTest(relative=relative):
                path=self.old/'batches/unsubmitted'/relative
                self.pin(path,dict(pending_native_submission=True))
                with self.assertRaisesRegex(ValueError,'attempted batch unsubmitted'):
                    self.evaluate()
                path.unlink()

    def test_missing_batch_changed_pin_or_faked_histogram_is_rejected(self):
        for change in ('missing_batch','changed_status','fake_receipt','lost_ledger','unused_gen','changed_replacement'):
            with self.subTest(change=change):
                original = copy.deepcopy(self.proof)
                entry = self.proof['productions'][0]
                status = self.old/'batches/failed/source_status.json'; saved_status = status.read_bytes()
                if change=='missing_batch':entry['batches'].pop()
                elif change=='changed_status':status.write_text('{}')
                elif change=='fake_receipt':entry['batches'][0]['point_receipt']['sha256']='f'*64
                elif change=='lost_ledger':entry['batches'][1]['retained_source']={}
                elif change=='unused_gen':entry['batches'][2]['detector_output_used']=True
                else:self.proof['replacement_configuration']=self.old_pin
                self.update_proof()
                with self.assertRaises(ValueError):self.evaluate()
                self.proof=original;status.write_bytes(saved_status);self.update_proof()

    def test_missing_active_or_mismatched_native_identity_is_rejected(self):
        for change in ('missing','active','wrong_iwd','wrong_execution','duplicate'):
            with self.subTest(change=change):
                rows=copy.deepcopy(self.native)
                if change=='missing':rows.pop()
                elif change=='active':rows[0]['JobStatus']=2
                elif change=='wrong_iwd':rows[0]['Iwd']=str(self.old)
                elif change=='wrong_execution':rows[0]['Args']='different-seal'
                else:rows.append(rows[0])
                self.proof['native_snapshot']=self.pin(self.root/'native.json',dict(
                    schema='shift-reviewed-meson-retirement-native-v1',queue_absent=True,jobs=rows))
                self.update_proof()
                with self.assertRaises(ValueError):self.evaluate()

    def reservation(self,name='old'):
        forecast=self.pin(self.old/'forecast.json',dict(complete=True,worklist_sha256=self.old_pin['sha256'],
            model_card_sha256=durable.sha(self.old_config['model_card']),
            per_batch_forecast_bytes={row['batch']:100 for row in self.rows},total_meson_forecast_bytes=400))
        return dict(name=name,peer_configuration=self.old_pin,forecast=forecast)

    def test_cancellation_releases_only_future_writes_without_completing_old_events(self):
        reservation=self.reservation()
        candidate=dict(self.config,additional_reserved_meson_forecasts=[dict(
            reservation,retirement_authority=self.authority)])
        previous=dict(additional_reserved_meson_forecasts=[reservation])
        self.assertEqual(meson.validate_meson_reservation_additions(previous,candidate),[])
        with patch.object(durable,'jobs',return_value=[]):
            amount,claims=meson.additional_reservations(candidate)
        self.assertEqual(amount,0)
        self.assertEqual(claims[0]['credited_bytes'],100)
        self.assertEqual(claims[0]['canceled_future_bytes'],300)
        self.assertFalse(claims[0]['terminal_completion_verified'])
        self.assertFalse(json.loads((self.old/'state.json').read_text())['complete'])
        candidate['additional_reserved_meson_forecasts'].append(dict(
            reservation,name='duplicate',retirement_authority=self.authority))
        with patch.object(durable,'jobs',return_value=[]),self.assertRaisesRegex(ValueError,'more than once'):
            meson.additional_reservations(candidate)

    def test_cancellation_cannot_change_remove_or_repeat_original_claim(self):
        reservation=self.reservation()
        previous=dict(additional_reserved_meson_forecasts=[reservation])
        for changed in ([],[dict(reservation,name='other')],[dict(reservation,forecast={})]):
            with self.subTest(changed=changed),self.assertRaises(ValueError):
                meson.validate_meson_reservation_additions(previous,dict(self.config,
                    additional_reserved_meson_forecasts=changed))


if __name__=='__main__':unittest.main()
