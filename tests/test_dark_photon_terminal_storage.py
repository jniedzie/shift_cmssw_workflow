# Code style: AI-code. AI-generated terminal production storage regressions.
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import run_dark_photon_statistics as statistics
import run_dark_photon_meson_production as meson


class TerminalStorage(unittest.TestCase):
    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
        return dict(path=str(path), sha256=statistics.sha(path))

    def fixture(self, root):
        self.root = root
        self.observed = time.time()+2
        self.checked = datetime.fromtimestamp(self.observed-3600, timezone.utc).isoformat()
        self.batch = dict(point='m2_prompt', events=20, seed=123, run_number=456)
        self.allocation = dict(total_additional_events=20,
            waves=[dict(wave_index=0, batches=[self.batch])])
        self.worklist = self.write(root/'worklist.json', self.allocation)
        production = root/'waves/w000/production'
        plan = self.write(production/'plan.json', dict(points=[self.batch]))
        seal = self.write(production/'seal.json', dict(files={plan['path']:plan['sha256']}))
        self.primitive_path = production/'completed_points/m2_prompt.json'
        self.write(self.primitive_path, dict(complete=True, point='m2_prompt', events=20,
            plan_sha256=plan['sha256'], seal_sha256=seal['sha256']))
        histogram = dict(path='/eos/synthetic/histograms_merged/ntuple_0.root', sha256='a'*64)
        self.report_path = root/'aggregates/m2_prompt/v0001/completed_points/m2_prompt.json'
        report = self.write(self.report_path, dict(complete=True, point='m2_prompt', events=25,
            score_histograms=dict(marker=dict(complete=True, published=dict(histograms=histogram)))))
        self.state = dict(finished=True, complete=False, checked_utc=self.checked, failures={},
            aggregates={}, unpublished_points=[], waves={'0':dict(terminal=True, successful=True, exit_code=0)},
            published=dict(m2_prompt=dict(events=25, source_plans=[plan['path']], published_utc=self.checked,
                point_receipt_path=report['path'], point_receipt_sha256=report['sha256'],
                histogram_path=histogram['path'], histogram_sha256=histogram['sha256'])))
        self.task = root/'supervisor'
        self.controller = dict(directory=str(root), worklist=self.worklist['path'],
            retired_aggregate_attempts=[], condor_schedd='synthetic', condor_pool='synthetic')
        self.controller_pin = self.write(self.task/'config.json', self.controller)
        (self.task/'controller.sub').write_text('queue 1\n')
        self.receipt = self.write(self.task/'submission.json', dict(task=str(self.task), cluster=123,
            submit_sha256=statistics.sha(self.task/'controller.sub')))
        self.proof = dict(schema='shift-statistics-terminal-storage-authority-v1', directory=str(root),
            worklist=self.worklist, controller_config=self.controller_pin, controller_receipt=self.receipt,
            state=self.write(root/'state.json', self.state),
            terminal_native_ad=dict(ClusterId=123, ProcId=0, Iwd=str(self.task), JobStatus=4,
                ExitBySignal=False, ExitCode=0, JobStartDate=1, CompletionDate=int(self.observed-3500)))
        self.authority = self.write(root/'terminal_review.json', self.proof)
        self.campaign = dict(name='main', kind='statistics', directory=str(root),
            worklist=self.worklist, bytes_per_event=326000, terminal_completion=self.authority)

    def refresh_proof(self):
        self.proof['state'] = self.write(self.root/'state.json', self.state)
        self.proof['controller_config'] = self.write(self.task/'config.json', self.controller)
        self.authority = self.write(self.root/'terminal_review.json', self.proof)
        self.campaign['terminal_completion'] = self.authority

    def test_terminal_publication_keeps_credit_after_heartbeat_stales(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(statistics, 'jobs', return_value=[]) as queue:
            self.fixture(Path(folder))
            self.assertIsNone(statistics.healthy_peer_state(self.root, 1200, self.observed))
            claim = statistics.supplement_campaign_claim(self.campaign, self.observed, 1200)
            self.assertEqual(claim['credited_events'], 20)
            self.assertEqual(claim['remaining_bytes'], 0)
            self.assertTrue(claim['terminal_completion_verified'])
            self.assertIn('(waves|aggregates)', queue.call_args.args[1])

    def test_live_or_unknown_scheduler_preserves_or_stops_admission(self):
        with tempfile.TemporaryDirectory() as folder:
            self.fixture(Path(folder))
            with patch.object(statistics, 'jobs', return_value=[dict(ClusterId=124)]):
                claim = statistics.supplement_campaign_claim(self.campaign, self.observed, 1200)
            self.assertEqual(claim['remaining_bytes'], 20*326000)
            self.assertFalse(claim['terminal_completion_verified'])
            with patch.object(statistics, 'jobs', side_effect=RuntimeError('unknown schedd')):
                with self.assertRaisesRegex(RuntimeError, 'unknown schedd'):
                    statistics.supplement_campaign_claim(self.campaign, self.observed, 1200)

    def test_exact_retired_failure_stays_recorded_and_does_not_block_completed_population(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(statistics, 'jobs', return_value=[]):
            self.fixture(Path(folder))
            failed = dict(cluster=99, directory=str(self.root/'aggregates/m2_prompt/old'))
            self.state['failures'] = dict(aggregate_m2_prompt=failed)
            retirement = self.write(self.root/'retirement.json', dict(
                schema='shift-reviewed-aggregate-retirement-v1', point='m2_prompt',
                original_frozen_attempt_unchanged=True, payload_started=False, prior_state_entry=failed))
            self.controller['retired_aggregate_attempts'] = [retirement]
            self.refresh_proof()
            claim = statistics.supplement_campaign_claim(self.campaign, self.observed, 1200)
            self.assertEqual(claim['remaining_bytes'], 0)
            self.assertEqual(statistics.read(self.root/'state.json')['failures'], self.state['failures'])

    def test_unreviewed_failure_missing_primitive_and_changed_receipt_are_rejected(self):
        for change in ('unreviewed', 'missing_primitive', 'changed_receipt', 'changed_state', 'failed_native'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as folder:
                self.fixture(Path(folder))
                if change == 'unreviewed':
                    self.state['failures']['unreviewed'] = dict(cluster=99)
                    self.refresh_proof()
                elif change == 'missing_primitive':
                    self.primitive_path.unlink()
                elif change == 'changed_receipt':
                    self.report_path.write_text('{}')
                elif change == 'changed_state':
                    (self.root/'state.json').write_text('{}')
                else:
                    self.proof['terminal_native_ad']['ExitCode'] = 1
                    self.refresh_proof()
                with patch.object(statistics, 'jobs', return_value=[]), self.assertRaises(ValueError):
                    statistics.supplement_campaign_claim(self.campaign, self.observed, 1200)

    def test_meson_preserves_fixed_floor_and_full_claim_for_live_peer(self):
        with tempfile.TemporaryDirectory() as folder:
            self.fixture(Path(folder))
            config = dict(adaptive_directory=str(self.root), adaptive_terminal_completion=self.authority)
            with patch.object(statistics, 'jobs', return_value=[]):
                self.assertEqual(meson.adaptive_reservation(config), 2000000000)
            with patch.object(statistics, 'jobs', return_value=[dict(ClusterId=124)]):
                self.assertEqual(meson.adaptive_reservation(config), 2000000000+20*326000)


class TerminalMesonPeerClosure(unittest.TestCase):
    def fixture(self, root):
        def write(path, record):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(record))
            return dict(path=str(path), sha256=statistics.sha(path))
        self.write = write
        self.current = root/'current'
        science = write(self.current/'config.json', {})
        self.config = dict(directory=str(self.current), bulk_storage_gate=str(root/'gate.json'))
        write(root/'gate.json', dict(per_batch_forecast_bytes=dict(new_bank=100)))
        peer = root/'previous'
        plan = write(peer/'batches/old/production/plan.json',
                     dict(points=[dict(point='signal', events=4, full_gen_events=20)]))
        seal = write(peer/'batches/old/production/seal.json', dict(files={plan['path']:plan['sha256']}))
        self.report = peer/'batches/old/production/completed_points/signal.json'
        write(self.report, dict(point='signal', complete=True, events=4, full_gen_events=20,
            plan_sha256=plan['sha256'], seal_sha256=seal['sha256']))
        producer = write(peer/'config.json', dict(schema='shift-dp-meson-production-v1',
            directory=str(peer), batches=[dict(batch='old', point='signal')], maximum_meson_forecast_bytes=1000))
        write(peer/'seal.json', dict(files={producer['path']:producer['sha256']}))
        self.authority = dict(science_configuration=science, original_storage_registration=dict(path='registered'),
            closed_peer_claims=[dict(name='completed_test',configurations=[producer],completed_points=[
                dict(path=str(self.report),sha256=statistics.sha(self.report))])])
        self.fresh = dict(authority=dict(storage_registration=dict(path='registered')),
            storage_registration=dict(peer_reservations=[dict(name='completed_test',reserved_bytes=1000),
                dict(name='active_SM',reserved_bytes=2000)]), reserved_peer_bytes=3000)
        self.observation = dict(observed_utc=datetime.now(timezone.utc).isoformat())

    def test_only_future_claim_is_closed_and_active_peer_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            self.fixture(Path(folder))
            with patch.object(meson,'fresh_production_authority',return_value=self.fresh), patch.object(statistics,'jobs',return_value=[]):
                reserved, future, evidence = meson.reviewed_terminal_peer_storage(self.config,self.observation,{},self.authority)
                self.assertEqual((reserved,future),(2000,100))
                self.assertEqual(evidence['physical_disk_credit_bytes'],0)

    def test_live_incomplete_changed_or_unregistered_peers_cannot_release_claims(self):
        for failure in ('live','incomplete','changed_pin','duplicate','unknown','wrong_budget'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as folder:
                self.fixture(Path(folder))
                live=[]
                if failure=='live':live=[dict(ClusterId=123)]
                elif failure=='incomplete':self.report.write_text('{}')
                elif failure=='changed_pin':self.authority['closed_peer_claims'][0]['configurations'][0]['sha256']='0'*64
                elif failure=='duplicate':self.authority['closed_peer_claims']*=2
                elif failure=='unknown':self.authority['closed_peer_claims'][0]['name']='unregistered'
                elif failure=='wrong_budget':self.fresh['storage_registration']['peer_reservations'][0]['reserved_bytes']=999
                with patch.object(meson,'fresh_production_authority',return_value=self.fresh), patch.object(statistics,'jobs',return_value=live):
                    with self.assertRaises((ValueError,OSError)):
                        meson.reviewed_terminal_peer_storage(self.config,self.observation,{},self.authority)


if __name__ == '__main__':
    unittest.main()
