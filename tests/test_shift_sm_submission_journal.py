# Code style: AI-code.
"""Submission recovery must adopt exact native jobs without replay or large writes."""
import errno
import json
from pathlib import Path
import pwd
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import run_shift_smart_sm_controller as controller


class SubmissionJournalTest(unittest.TestCase):
    def fixture(self, root):
        config = dict(campaign='journal-test', submission_journal=True,
            maximum_starts_per_poll=100,
            worker_template='initialdir = @DIRECTORY@\narguments = @REQUEST_SHA@\nqueue 1\n')
        state = dict(tasks={}, next_detector_job=1)
        for index in range(2):
            state['tasks'][str(index)] = controller.make_task(root, config, str(index),
                dict(seed=100+index, selected_ids=[[1,1,index]], native_weights=[1.],
                     probabilities=[.1], original_exposure=1000), 'quota', '')
        controller.save(root/'ledger.json', state)
        return config, state

    def submit(self, root, config, state, batch, name='0'):
        task = state['tasks'][name]
        controller.submit_journal_task(root, config, state, batch, name, task,
                                       Path(task['directory'])/'worker.sub')

    def ad(self, task, cluster=41):
        return dict(ClusterId=cluster, ProcId=0, Owner=pwd.getpwuid(os.getuid()).pw_name,
            Iwd=task['directory'], Args=task['request_sha256']+' request.json', JobStatus=2)

    def test_default_is_unchanged_and_nonboolean_policy_is_rejected(self):
        self.assertFalse(controller.submission_journal_enabled({}))
        with self.assertRaises(ValueError):
            controller.submission_journal_enabled(dict(submission_journal=1))

    def test_death_after_receipt_adopts_first_job_and_submits_next_once(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);config,state=self.fixture(root)
            batch=controller.begin_submission_batch(root,config,state)
            original_save=controller.save
            def die_after_receipt(path, value):
                original_save(path,value)
                if Path(path).name=='native_submission_receipt.json':
                    raise SystemExit('simulated process death after durable receipt')
            with patch.object(controller,'run_condor',return_value='41.0 - 41.0\n') as native:
                with patch.object(controller,'save',die_after_receipt):
                    with self.assertRaises(SystemExit):self.submit(root,config,state,batch)
                restored=controller.read(root/'ledger.json')
                self.assertEqual(restored['tasks']['0']['phase'],'ready')
                with patch.object(controller,'query',return_value=[self.ad(restored['tasks']['0'])]):
                    controller.reconcile_submission_batch(root,config,restored)
                self.assertEqual(restored['tasks']['0']['cluster'],41)
                self.assertEqual(restored['tasks']['0']['phase'],'submitted')
                self.assertEqual(native.call_count,1)
                native.return_value='42.0 - 42.0\n'
                second=controller.begin_submission_batch(root,config,restored)
                self.submit(root,config,restored,second,'1')
                controller.finish_submission_batch(root,restored,second)
                self.assertEqual(native.call_count,2)
            self.assertEqual(controller.read(root/'submission_batch.json')['phase'],'checkpointed')
            self.assertEqual(restored['submission_journal_generation'],2)
            for task in restored['tasks'].values():
                self.assertEqual(controller.sha(Path(task['directory'])/'request.json'),task['request_sha256'])

    def test_intent_without_receipt_requires_review_and_never_replays(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);config,state=self.fixture(root)
            batch=controller.begin_submission_batch(root,config,state)
            with patch.object(controller,'run_condor',side_effect=SystemExit('death before native receipt')):
                with self.assertRaises(SystemExit):self.submit(root,config,state,batch)
            intent=Path(state['tasks']['0']['directory'])/'native_submission_intent.json'
            original=intent.read_bytes()
            with patch.object(controller,'run_condor') as native, patch.object(controller,'query') as query:
                with self.assertRaisesRegex(RuntimeError,'no complete submission receipt'):
                    controller.reconcile_submission_batch(root,config,controller.read(root/'ledger.json'))
                native.assert_not_called();query.assert_not_called()
            self.assertEqual(intent.read_bytes(),original)
            self.assertTrue((root/'submission_attention_required.json').is_file())

    def test_wrong_native_owner_or_arguments_cannot_be_adopted(self):
        for change in [dict(Owner='another-user'),dict(Args='different-request'),dict(Iwd='/elsewhere')]:
            with self.subTest(change=change),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);config,state=self.fixture(root)
                batch=controller.begin_submission_batch(root,config,state)
                with patch.object(controller,'run_condor',return_value='41.0 - 41.0\n'):
                    self.submit(root,config,state,batch)
                restored=controller.read(root/'ledger.json');ad=self.ad(restored['tasks']['0']);ad.update(change)
                with patch.object(controller,'query',return_value=[ad]):
                    with self.assertRaisesRegex(RuntimeError,'frozen request and owner'):
                        controller.reconcile_submission_batch(root,config,restored)

    def test_completed_job_requires_history_and_original_native_event(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);config,state=self.fixture(root)
            batch=controller.begin_submission_batch(root,config,state)
            with patch.object(controller,'run_condor',return_value='41.0 - 41.0\n'):
                self.submit(root,config,state,batch)
            restored=controller.read(root/'ledger.json');task=restored['tasks']['0']
            (Path(task['directory'])/'events.log').write_text('005 (41.000.000) native termination\n...\n')
            ad=self.ad(task);ad['JobStatus']=4
            with patch.object(controller,'query',return_value=[]),patch.object(controller,'run_condor',return_value=json.dumps([ad])):
                controller.reconcile_submission_batch(root,config,restored)
            self.assertEqual(restored['tasks']['0']['cluster'],41)
            self.assertTrue(restored['tasks']['0']['recovered_from_submission_journal'])

    def test_receipt_storage_outage_retries_the_write_not_the_native_submit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);config,state=self.fixture(root)
            batch=controller.begin_submission_batch(root,config,state)
            original_open=Path.open
            errors=[]
            def outage(path,*args,**kwargs):
                if Path(path).name=='native_submission_receipt.tmp' and len(errors)<3:
                    errors.append(1)
                    raise OSError(errno.EIO,'temporary AFS write outage')
                return original_open(path,*args,**kwargs)
            with patch.object(Path,'open',outage),patch.object(controller.time,'sleep') as wait, \
                    patch.object(controller,'run_condor',return_value='41.0 - 41.0\n') as native:
                self.submit(root,config,state,batch)
                native.assert_called_once()
                self.assertEqual(wait.call_count,3)
            receipt=controller.read(Path(state['tasks']['0']['directory'])/'native_submission_receipt.json')
            self.assertEqual(receipt['cluster'],41)
            self.assertEqual(state['tasks']['0']['phase'],'submitted')

    def test_one_hundred_submits_only_checkpoint_the_full_ledger_twice(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);config,state=self.fixture(root)
            state['tasks']={}
            for index in range(100):
                task=controller.make_task(root,config,'task'+str(index),dict(index=index),'quota','')
                state['tasks']['task'+str(index)]=task
            original=controller.save;full=[]
            def count(path,value):
                if Path(path)==root/'ledger.json':full.append(1)
                original(path,value)
            with patch.object(controller,'save',count),patch.object(controller,'run_condor',return_value='41.0 - 41.0\n'):
                batch=controller.begin_submission_batch(root,config,state)
                for name in state['tasks']:self.submit(root,config,state,batch,name)
                controller.finish_submission_batch(root,state,batch)
            self.assertEqual(len(full),2)


if __name__=='__main__':unittest.main()
