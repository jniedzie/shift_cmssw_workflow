# Code style: AI-code. AI-generated shared meson storage-credit regression tests.
import copy
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import run_dark_photon_statistics as durable
import run_dark_photon_meson_production as meson


class MesonPeerStorage(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.peer = self.root/'peer'
        self.peer.mkdir()
        self.observed = datetime.now(timezone.utc).timestamp()
        self.rows = [dict(batch=name, point='m0p3_prompt', source_collisions=5000) for name in ('a','b')]
        model = self.peer/'model.json'
        model.write_text('{}')
        self.config = dict(schema='shift-dp-meson-production-v1', directory=str(self.peer),
            production_mechanism='meson-radiative', model_card=str(model), batches=self.rows,
            reviewed_terminal_native_jobs=dict(path='/test/seed', sha256='c'*64))
        self.worklist = self.pin(self.peer/'config.json', self.config)
        self.forecast = self.pin(self.peer/'forecast.json', dict(complete=True,
            worklist_sha256=self.worklist['sha256'], model_card_sha256=durable.sha(model),
            per_batch_forecast_bytes=dict(a=100,b=200), total_meson_forecast_bytes=300))
        self.authority = dict(kind='meson', name='peer', directory=str(self.peer),
            worklist=self.worklist, forecast=self.forecast)
        self.state = dict(checked_utc=datetime.now(timezone.utc).isoformat(), batches={})
        self.published('a')
        self.save()
        self.closed = patch.object(durable, 'closed_primitive', return_value=True)
        self.closed.start()
        self.addCleanup(self.closed.stop)

    def pin(self,path,value):
        path.write_text(json.dumps(value))
        return dict(path=str(path), sha256=durable.sha(path))

    def save(self):
        (self.peer/'state.json').write_text(json.dumps(self.state))

    def published(self,name):
        production=self.peer/'batches'/name/'production'
        (production/'completed_points').mkdir(parents=True)
        (production.parent/'source_status.json').write_text(json.dumps(dict(
            complete=True,source_collisions=5000,events=20)))
        histogram='/eos/test/darkphoton_m0p3_prompt/campaign/histograms_merged/ntuple_0.root'
        report=dict(complete=True,point='m0p3_prompt',events=20,
            final_output_base='/eos/test/darkphoton_m0p3_prompt/campaign',
            score_histograms=dict(marker=dict(complete=True,published=dict(
                histograms=dict(path=histogram,sha256='a'*64)))))
        receipt=self.pin(production/'completed_points/m0p3_prompt.json',report)
        self.state['batches'][name]=dict(published=dict(batch=name, campaign='campaign',events=20,
            published_utc=datetime.fromtimestamp(self.observed-1,timezone.utc).isoformat(),
            point_receipt_path=receipt['path'],point_receipt_sha256=receipt['sha256'],
            histogram_path=histogram,histogram_sha256='a'*64))

    def claim(self):
        return durable.supplement_campaign_claim(self.authority,self.observed,1200)

    def test_only_verified_completed_batches_receive_partial_credit(self):
        self.assertEqual(self.claim()['remaining_bytes'],200)
        reservation=dict(name='peer',peer_configuration=self.worklist,forecast=self.forecast)
        total,claims=meson.additional_reservations(dict(additional_reserved_meson_forecasts=[reservation]))
        self.assertEqual(total,200)
        self.assertEqual(claims[0]['credited_bytes'],100)
        self.assertEqual(self.worklist['sha256'],durable.sha(self.worklist['path']))

    def test_stale_failed_or_missing_peer_keeps_the_full_forecast(self):
        for change in ('stale','error','failures','missing'):
            with self.subTest(change=change):
                state=copy.deepcopy(self.state)
                if change=='stale':state['checked_utc']=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat()
                elif change=='missing':
                    (self.peer/'state.json').unlink()
                else:state[change]=['failed source']
                if change!='missing':(self.peer/'state.json').write_text(json.dumps(state))
                self.assertEqual(self.claim()['remaining_bytes'],300)
                self.save()

    def test_wrong_batch_or_changed_receipt_cannot_release_storage(self):
        for change in ('batch','path','hash','histogram','source'):
            with self.subTest(change=change):
                original=copy.deepcopy(self.state)
                published=self.state['batches']['a']['published']
                if change=='batch':published['batch']='b'
                elif change=='path':published['point_receipt_path']=str(self.root/'m0p3_prompt.json')
                elif change=='hash':published['point_receipt_sha256']='b'*64
                elif change=='histogram':published['histogram_sha256']='b'*64
                else:
                    (self.peer/'batches/a/source_status.json').write_text(json.dumps(dict(
                        complete=True,source_collisions=5001,events=20)))
                self.save()
                if change=='batch':self.assertEqual(self.claim()['remaining_bytes'],300)
                else:
                    with self.assertRaises((ValueError,FileNotFoundError)):self.claim()
                self.state=original
                (self.peer/'batches/a/source_status.json').write_text(json.dumps(dict(
                    complete=True,source_collisions=5000,events=20)))
                self.save()

    def test_terminal_credit_uses_its_own_owner_and_fresh_queue_absence(self):
        self.published('b')
        self.state.update(complete=True,checked_utc=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat())
        self.save()
        owner=self.root/'owner';owner.mkdir()
        submit=owner/'controller.sub';submit.write_text('exact descriptor')
        receipt=dict(task=str(owner),cluster=123,submit_sha256=durable.sha(submit))
        (owner/'submission.json').write_text(json.dumps(receipt))
        execution=dict(Cmd=str(owner/'lcg_controller.sh'),Args='owner-seal')
        authority=dict(self.authority,controller_directory=str(owner))
        observer=dict(directory=str(self.root/'observer'),prior_meson_controller_directory='/wrong/main/owner')
        with patch.object(meson,'meson_controller_configuration',return_value=dict(
                submit=str(submit),native_execution=execution)) as validate, \
             patch.object(durable,'jobs',return_value=[]) as queue, \
             patch.object(meson,'native_status',return_value='complete'), \
             patch.object(meson,'cached_terminal_record',return_value=dict(terminal_native_ad=execution)) as cached:
            claim=durable.supplement_campaign_claim(authority,self.observed,1200,observer)
            self.assertEqual(claim['remaining_bytes'],0)
            self.assertTrue(claim['terminal_completion_verified'])
            validate.assert_called_with(self.config,owner,allow_science=True)
            self.assertIn(str(owner),queue.call_args.args[1])
            cached.return_value=dict(terminal_native_ad=dict(execution,Args='wrong'))
            self.assertEqual(durable.supplement_campaign_claim(authority,self.observed,1200,observer)['remaining_bytes'],300)
            cached.return_value=dict(terminal_native_ad=execution)
            queue.return_value=[dict(ClusterId=123)]
            self.assertEqual(durable.supplement_campaign_claim(authority,self.observed,1200,observer)['remaining_bytes'],300)
            queue.return_value=[]
            (owner/'submission.json').write_text(json.dumps(dict(receipt,task='/wrong/owner')))
            self.assertEqual(durable.supplement_campaign_claim(authority,self.observed,1200,observer)['remaining_bytes'],300)

    def test_owner_locators_only_name_protected_peers(self):
        for value in ({'unknown':str(self.root/'owner')},{'peer':'relative'},{'peer':str(self.root/'a/../owner')}):
            with self.subTest(value=value),self.assertRaises(ValueError):
                meson.validate_meson_peer_locators(dict(meson_peer_controller_directories=value),['peer'])
        self.assertEqual(meson.validate_meson_peer_locators(dict(
            meson_peer_controller_directories={'peer':str(self.root/'future_owner')}),['peer']),
            {'peer':str(self.root/'future_owner')})


if __name__=='__main__':unittest.main()
