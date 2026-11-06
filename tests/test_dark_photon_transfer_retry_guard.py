# Code style: AI-code. AI-generated transfer-only retry regression controls.
import copy
from datetime import datetime,timezone
import errno
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import guard_dark_photon_idle_hosts as guard


class TransferRetry(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.stage=self.root/'production'
        (self.stage/'statuses').mkdir(parents=True);(self.stage/'logs').mkdir()
        (self.stage/'frozen/sources').mkdir(parents=True)
        fixture=Path(__file__).with_name('fixtures')/'dp_transfer_restart_failure.json'
        self.failure=json.loads(fixture.read_text());cluster=self.failure['cluster']
        self.ad=dict(ClusterId=cluster,ProcId=0,Owner='jniedzie',JobStatus=5,ExitCode=1,
            ExitBySignal=False,NumJobStarts=2,Iwd=str(self.stage),Cmd=str(self.stage/'detector_worker.sh'),
            Args='--chunk 1',DAGManJobId=50,_queried_schedd='schedd.cern.ch',
            Err=str(self.stage/'logs'/f'detector.{cluster}.0.err'),JobStartDate=int(time.time())-100,
            MaxRuntime=86400)
        self.manager=dict(ClusterId=50,ProcId=0,JobStatus=2,JobUniverse=7,
            Cmd='/usr/bin/condor_dagman',Iwd=str(self.stage),_queried_schedd='schedd.cern.ch')
        self.ads=[self.ad,self.manager]
        self.config=dict(owner='jniedzie',namespaces=[str(self.stage)],condor_pool='pool.cern.ch',
            directory=str(self.root),maximum_receipt_bytes=64*1048576,
            infrastructure_recovery=dict(dry_run=False,maximum_recoveries=2))
        self.write(self.stage/'submission.json',dict(cluster=50,task=str(self.stage)))
        self.write(self.stage/'source.json',dict(output_base='/eos/user/j/jniedzie/test/campaign/detector'))
        self.write(self.stage/'plan.json',dict(points=[dict(point='signal',events=2,full_gen_events=20,
            final_output_base='/eos/user/j/jniedzie/test/campaign',chunks=[dict(chunk=1,job=6620001,skip=1,count=1,
                nano='/eos/user/j/jniedzie/test/campaign/nanoAOD/chunk_001.root',
                metadata='/eos/user/j/jniedzie/test/campaign/metadata/chunk_001')])]))
        helper=Path(guard.__file__).with_name('run_shift_gen_to_nano.py')
        shutil.copy2(helper,self.stage/'frozen/sources'/helper.name)
        files={str(p):guard.digest(p) for p in [self.stage/'source.json',self.stage/'plan.json',self.stage/'frozen/sources'/helper.name]}
        self.write(self.stage/'seal.json',dict(files=files))
        self.status=self.stage/'statuses/chunk_1.json'
        self.write(self.status,dict(complete=False,full_gen_events=20,plan_sha256=guard.digest(self.stage/'plan.json'),seal_sha256=guard.digest(self.stage/'seal.json')))
        self.write(self.stage/'statuses/chunk_0.json',dict(complete=True,payload=dict(complete=True)))
        (self.stage/'events.log').write_text(self.failure['events'])
        Path(self.ad['Err']).write_text(self.failure['error'])

    def write(self,path,value):path.write_text(json.dumps(value))

    def test_preserved_native_signature_and_exact_native_output_names(self):
        self.assertTrue(guard.transfer_restart_signature(self.failure['events'],self.failure['error'],self.ad['ClusterId']))
        c=guard.transfer_retry_candidate(self.config,self.ad,self.ads)
        self.assertIn('/eos/user/j/jniedzie/test/campaign/detector/job6620001',c['outputs'])
        self.assertIn('/eos/user/j/jniedzie/test/campaign/detector/job6620001/nano.root',c['outputs'])
        self.assertEqual(c['source_sha256'],guard.digest(self.stage/'source.json'))

    def test_exact_same_DAG_retry_archives_once_and_preserves_successes(self):
        after=dict(self.ad,TransferOutput='',PeriodicRelease=False)
        original_success=guard.digest(self.stage/'statuses/chunk_0.json')
        with patch.object(guard,'output_absent',side_effect=lambda p:dict(path=p)), \
             patch.object(guard.native,'query',side_effect=[[self.ad],[after],[after]]), \
             patch.object(guard.native,'run_condor',return_value='1matchingjob') as run:
            r=guard.recover_transfer_jobs(self.config,self.ads)
            self.assertEqual(len(r['recovered']),1)
            self.assertEqual(run.call_count,2)
            self.assertIn('JobStatus == 5',run.call_args.args[0][-1])
            self.assertFalse(self.status.exists())
            self.assertEqual(original_success,guard.digest(self.stage/'statuses/chunk_0.json'))
            guard.recover_transfer_jobs(self.config,self.ads)
            self.assertEqual(run.call_count,2)
        archived=list((self.root/'transfer_recovery').glob('*/original_status.json'))
        self.assertEqual(len(archived),1)

    def test_physics_partial_output_running_duplicate_and_unknown_failures_are_blocked(self):
        for case in ['physics','payload','native_output','duplicate','wrong_native','new_plan']:
            with self.subTest(case=case):
                old=self.status.read_text();ads=copy.deepcopy(self.ads);events=(self.stage/'events.log').read_text()
                status=json.loads(old)
                if case=='physics':status['error']='physics failure'
                elif case=='payload':status['payload']=dict(complete=False)
                elif case=='duplicate':ads.append(dict(self.ad,ClusterId=999,JobStatus=2))
                elif case=='wrong_native':(self.stage/'events.log').write_text(events.replace('Code 1007','Code 1006'))
                elif case=='new_plan':status['plan_sha256']='0'*64
                self.write(self.status,status)
                with patch.object(guard,'output_absent',side_effect=ValueError('native output exists') if case=='native_output' else lambda p:dict(path=p)), patch.object(guard.native,'run_condor') as run:
                    r=guard.recover_transfer_jobs(self.config,ads)
                    self.assertTrue(r['blocked'])
                    run.assert_not_called();self.assertTrue(self.status.exists())
                self.status.write_text(old);(self.stage/'events.log').write_text(events)

    def test_dry_run_and_successful_control_never_release(self):
        self.config['infrastructure_recovery']['dry_run']=True
        with patch.object(guard,'output_absent',side_effect=lambda p:dict(path=p)),patch.object(guard.native,'run_condor') as run:
            r=guard.recover_transfer_jobs(self.config,self.ads);self.assertTrue(r['recovered'][0]['would_recover']);self.assertTrue(self.status.exists())
            r=guard.recover_transfer_jobs(self.config,[dict(self.ad,JobStatus=2),self.manager]);self.assertEqual(r['recovered'],[]);run.assert_not_called()

    def test_native_race_and_changed_edit_readback_preserve_incomplete_attempt(self):
        expected=dict(self.ad,TransferOutput='',PeriodicRelease=False)
        for responses in [[],[dict(self.ad,TransferOutput='unexpected')],[expected]]:
            with self.subTest(responses=responses):
                queries=[[self.ad],responses]
                if responses==[expected]:queries.append([expected,dict(expected,ClusterId=999,JobStatus=2)])
                with patch.object(guard,'output_absent',side_effect=lambda p:dict(path=p)),patch.object(guard.native,'query',side_effect=queries),patch.object(guard.native,'run_condor') as run:
                    r=guard.recover_transfer_jobs(self.config,self.ads);self.assertTrue(r['blocked']);self.assertTrue(self.status.exists());self.assertEqual(run.call_count,1)
                shutil.rmtree(self.root/'transfer_recovery')

    def test_state_write_retry_keeps_the_same_snapshot_and_does_not_repeat_actions(self):
        actual=guard.os.replace;calls=[]
        def outage(source,target):
            calls.append(Path(source).read_text())
            if len(calls)<4:raise OSError(errno.EIO,'transient AFS outage')
            return actual(source,target)
        with patch.object(guard.os,'replace',side_effect=outage),patch.object(guard.time,'sleep'):
            guard.save(self.root/'snapshot.json',dict(action_already_done=True,poll=7))
        self.assertEqual(len(set(calls)),1)
        self.assertEqual(json.loads((self.root/'snapshot.json').read_text())['poll'],7)

    def test_missing_monitor_is_optional_only_before_its_controller_submission(self):
        report=self.root/'health.json';receipt=self.root/'submission.json'
        self.config['watchdog_reports']=[dict(path=str(report),controller_receipt=str(receipt))]
        self.assertEqual(guard.recover_transfer_jobs(self.config,[])['blocked'],[])
        self.write(receipt,dict(cluster=5))
        self.assertTrue(guard.recover_transfer_jobs(self.config,[])['blocked'])
        self.write(report,dict(checked_utc=datetime.now(timezone.utc).isoformat(),health='critical',issues=[dict(code='supervisor_error')]))
        result=guard.recover_transfer_jobs(self.config,[])
        self.assertEqual(result['blocked'][0]['issues'][0]['code'],'supervisor_error')

    def test_ambiguous_release_after_archive_stays_blocked_without_repeating_action(self):
        after=dict(self.ad,TransferOutput='',PeriodicRelease=False)
        with patch.object(guard,'output_absent',side_effect=lambda p:dict(path=p)), \
             patch.object(guard.native,'query',side_effect=[[self.ad],[after],[after]]), \
             patch.object(guard.native,'run_condor',side_effect=['edited',guard.native.NativeCondorError('release timeout')]) as run:
            result=guard.recover_transfer_jobs(self.config,self.ads)
            self.assertEqual(result['blocked'][0]['code'],'native_retry_outcome_unknown')
            self.assertFalse(self.status.exists())
            self.assertEqual(len(list((self.root/'transfer_recovery').glob('*/original_status.json'))),1)
            running=[dict(after,JobStatus=2),self.manager]
            with patch.object(guard.native,'query_account',return_value=running):
                state=guard.poll(self.config,2,{})
            self.assertEqual(state['status'],'blocked')
            self.assertEqual(state['infrastructure_recovery']['blocked'][0]['code'],'ambiguous_retry_transaction')
            self.assertEqual(run.call_count,2)

    def test_EOS_timeout_keeps_attempt_and_later_poll_can_recover(self):
        original=guard.digest(self.status)
        with patch.object(guard.subprocess,'run',side_effect=guard.subprocess.TimeoutExpired(['xrdfs'],45)),patch.object(guard.native,'run_condor') as run:
            result=guard.recover_transfer_jobs(self.config,self.ads)
            self.assertTrue(result['blocked']);self.assertIn('timed out',result['blocked'][0]['reason'])
            self.assertEqual(guard.digest(self.status),original);run.assert_not_called()
            self.assertEqual(list((self.root/'transfer_recovery').glob('*/intent.json')),[])
        absent=guard.subprocess.CompletedProcess(['xrdfs'],54,stdout='',stderr='[3011] Unable to stat: No such file or directory')
        after=dict(self.ad,TransferOutput='',PeriodicRelease=False)
        with patch.object(guard.subprocess,'run',return_value=absent),patch.object(guard.native,'query',side_effect=[[self.ad],[after],[after]]),patch.object(guard.native,'run_condor',return_value='1matchingjob') as run:
            result=guard.recover_transfer_jobs(self.config,self.ads)
            self.assertEqual(len(result['recovered']),1);self.assertEqual(run.call_count,2)

    def test_transient_released_response_read_is_blocked_then_observed_without_action(self):
        candidate=guard.transfer_retry_candidate(self.config,self.ad,self.ads)
        directory=self.root/'transfer_recovery/saved';directory.mkdir(parents=True)
        self.write(directory/'intent.json',dict(ad=self.ad))
        self.write(directory/'released.json',dict(job=str(self.ad['ClusterId'])+'.0',candidate=candidate))
        self.write(self.status,dict(complete=True,plan_sha256=candidate['plan_sha256'],seal_sha256=candidate['seal_sha256'],payload=dict(complete=True,source_descriptor_sha256=candidate['source_sha256'])))
        read=Path.read_text
        def outage(path,*args,**kwargs):
            if path==self.status:raise OSError(errno.EIO,'Transient AFS response read')
            return read(path,*args,**kwargs)
        ads=[dict(self.ad,JobStatus=2),self.manager]
        with patch.object(Path,'read_text',outage),patch.object(guard.native,'run_condor') as run:
            result=guard.recover_transfer_jobs(self.config,ads)
            self.assertEqual(result['blocked'][0]['code'],'retry_response_read_unknown');run.assert_not_called()
        with patch.object(guard.native,'run_condor') as run:
            result=guard.recover_transfer_jobs(self.config,ads)
            self.assertEqual(result['blocked'],[]);self.assertTrue(result['observations'][0]['response_complete']);run.assert_not_called()


if __name__=='__main__':unittest.main()
