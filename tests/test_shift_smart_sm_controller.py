# Code style: AI-code. AI-generated native scheduler and receipt regression checks.
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from run_shift_smart_sm_controller import admission_order, native_exit, observe, publication_charge


class PublicationChargeTests(unittest.TestCase):
    def empty(self):
        return dict(schema='shift-smart-SM-source-v1',empty_source=True,selected_events=0,
            source=dict(saved_events=0),raw_publications=[dict(name=name,bytes=size,
            independent_sha256_readback=True) for name,size in
            [('gen.root',100000),('resolved_gen_cfg.py',1000000),('validation.json',4000)]])

    def test_empty_native_output_releases_only_the_unused_reservation(self):
        evidence=self.empty();charge=publication_charge(evidence,134217728)
        self.assertGreater(charge,1104000)
        self.assertLess(charge,1500000)
        self.assertEqual(evidence['source']['saved_events'],0)

    def test_nonempty_unknown_receipt_keeps_full_conservative_charge(self):
        self.assertEqual(publication_charge({},134217728),134217728+65536)
        self.assertEqual(publication_charge(dict(published_storage_bytes=12345),134217728),12345)

    def test_empty_credit_requires_census_and_readback_proof(self):
        for mutate in (lambda e:e.update(selected_events=1),
                lambda e:e['source'].update(saved_events=1),
                lambda e:e['raw_publications'][0].update(independent_sha256_readback=False)):
            evidence=self.empty();mutate(evidence)
            with self.assertRaises(ValueError):publication_charge(evidence,134217728)


class SchedulingTests(unittest.TestCase):
    def test_gen_prefetch_cannot_be_starved_by_detector_backlog(self):
        tasks = {f'qcd{i}':dict(kind='detector',phase='ready',stratum='qcd_20toinf')
                 for i in range(100)}
        tasks['new_jpsi'] = dict(kind='source',phase='ready',stratum='jpsi_2to5')
        self.assertEqual(admission_order(tasks)[0][0], 'new_jpsi')

    def test_detector_admission_balances_bins_without_stopping_live_jobs(self):
        tasks = dict(live=dict(kind='detector',phase='submitted',stratum='qcd_20toinf'),
            qcd=dict(kind='detector',phase='ready',stratum='qcd_20toinf'),
            jpsi=dict(kind='detector',phase='ready',stratum='jpsi_2to5'),
            dy=dict(kind='detector',phase='ready',stratum='dy_20toinf'))
        order=[name for name,_ in admission_order(tasks)]
        self.assertEqual(set(order[:2]), {'jpsi','dy'})
        self.assertEqual(order[-1], 'qcd')

    def test_source_backpressure_keeps_existing_workers(self):
        tasks={str(i):dict(kind='source',phase='submitted',stratum='qcd_5to10') for i in range(2)}
        tasks['next']=dict(kind='source',phase='ready',stratum='qcd_5to10')
        self.assertEqual(admission_order(tasks), [])
        self.assertTrue(all(tasks[str(i)]['phase']=='submitted' for i in range(2)))


class CompletionTests(unittest.TestCase):
    def test_exact_native_job_is_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'events.log'
            path.write_text('005 (124.000.000) event\nNormal termination (return value 0)\n...\n')
            self.assertEqual(native_exit(path, 124), 0)
            self.assertIsNone(native_exit(path, 123))

    def test_returned_success_without_native_closure_does_not_credit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path/'status.json').write_text(json.dumps(dict(complete=True, exit_code=0)))
            task = dict(directory=tmp, phase='submitted', cluster=123)
            observe(task, [])
            self.assertEqual(task['phase'], 'submitted')

    def test_hold_is_kept_for_review(self):
        task = dict(directory='/persistent/job', phase='submitted', cluster=123)
        observe(task, [dict(ClusterId=123, ProcId=0, Iwd='/persistent/job',
                           JobStatus=5, HoldReason='Actual worker failed')])
        self.assertEqual(task['phase'], 'failed')
        self.assertEqual(task['error'], 'Actual worker failed')

    def test_success_is_bound_to_request_and_native_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path/'events.log').write_text('005 (123.000.000) event\nNormal termination (return value 0)\n...\n')
            (path/'status.json').write_text(json.dumps(dict(complete=True, exit_code=0)))
            (path/'result.json').write_text(json.dumps(dict(complete=True, request_sha256='a'*64)))
            task = dict(directory=tmp, phase='submitted', cluster=123,
                        request_sha256='a'*64, bound_bytes=10000)
            observe(task, [])
            self.assertEqual(task['phase'], 'done')
            task.update(phase='submitted', request_sha256='b'*64)
            observe(task, [])
            self.assertEqual(task['phase'], 'failed')

    def test_uncertain_submission_is_never_repeated(self):
        with self.assertRaisesRegex(RuntimeError, 'Uncertain submission'):
            observe(dict(directory='/persistent/job', phase='submitting'), [])


if __name__ == '__main__':
    unittest.main()
