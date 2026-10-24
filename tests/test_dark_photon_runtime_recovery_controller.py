# Code style: AI-code. AI-generated checks for bounded native runtime recovery.
import importlib.util
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS=Path(__file__).resolve().parents[1]/'scripts'
sys.path.insert(0,str(SCRIPTS))
import run_dark_photon_runtime_recovery as recovery
import prepare_dark_photon_kinematics_preflight as preflight
import run_dark_photon_statistics as statistics


class RuntimeRecoveryController(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.production=self.root/'science/waves/w001/production'
        self.production.mkdir(parents=True)
        for name in ('statuses','failures','frozen'):(self.production/name).mkdir()
        self.point='m2_prompt'
        self.status=self.production/'statuses/m2_prompt_000.json'
        self.chunk=dict(chunk=0,skip=0,count=2,nano='/eos/user/a/c/nanoAOD/chunk_000.root',
                        metadata='/eos/user/a/c/metadata/chunk_000')
        self.old='a'*64
        profile=self.production/'frozen/detector_runtime.json'
        profile.write_text(json.dumps(dict(runtime_sha256=self.old)))
        self.authority=self.root/'authority.json'
        self.authority.write_text(json.dumps(dict(old_profile=dict(path=str(profile),sha256=recovery.sha(profile)))))
        inventory=dict(points={self.point:dict(gen_sha256='b'*64,event_ids=[[1,1,1],[1,1,2],[1,1,3]])})
        plan=dict(detector_runtime=dict(runtime_sha256=self.old),points=[dict(point=self.point,
            material_selection=dict(selected_indices=[0,2]),chunks=[self.chunk])])
        for name,obj in [('plan.json',plan),('gen_inventory.json',inventory)]:
            (self.production/name).write_text(json.dumps(obj))
        (self.production/'chunk_worker.py').write_text('# original science worker\n')
        (self.production/'seal.json').write_text(json.dumps(dict(files={str(self.production/name):recovery.sha(self.production/name)
            for name in ('plan.json','gen_inventory.json','chunk_worker.py')})))
        self.seal=recovery.sha(self.production/'seal.json')
        self.status.write_text(json.dumps(dict(complete=False,published={},point=self.point,chunk=0,
            seal_sha256=self.seal,plan_sha256=recovery.sha(self.production/'plan.json'),
            parent_gen_sha256='b'*64,expected_event_ids=[[1,1,1],[1,1,3]])))
        archive=self.production/'failures/old.tar.gz'
        with tarfile.open(archive,'w:gz') as t:
            values={self.point+'_000/detector/report.json':json.dumps(dict(current_tier='SIM',
                error="CalledProcessError(66, ['cmsRun', '/scratch/step1_cfg.py'])")),
                self.point+'_000/detector/step1.log':recovery.SIGNATURE}
            for name,value in values.items():
                b=value.encode();item=tarfile.TarInfo(name);item.size=len(b);t.addfile(item,io.BytesIO(b))
        for name in ('old.sh','old.out','old.err'):(self.production/name).write_text(name)
        self.row=dict(ClusterId=123,ProcId=0,DAGManJobId=100,JobStatus=5,Iwd=str(self.production),
            ExitCode=1,ExitBySignal=False,PeriodicRelease=False,NumJobStarts=1,
            Args='m2_prompt 0 '+self.seal,Cmd=str(self.production/'old.sh'),
            Out=str(self.production/'old.out'),Err=str(self.production/'old.err'),
            TransferOutput='failure.tar.gz',TransferOutputRemaps='failure.tar.gz='+str(archive))
        self.directory=self.root/'controller';self.directory.mkdir()
        self.config=dict(directory=str(self.directory),production_directories=[str(self.root/'science')],
            runtime_authorities={self.old:dict(path=str(self.authority),sha256=recovery.sha(self.authority))},
            owner='jniedzie',condor_schedd='verified',condor_pool='verified')

    def test_real_archive_classification_preserves_exact_selected_ids(self):
        review=recovery.inspect_failure(self.config,self.row)
        self.assertEqual(review['expected_event_ids'],[[1,1,1],[1,1,3]])
        self.assertEqual(review['gen_sha256'],'b'*64)
        self.assertEqual(len(review['destinations']),3)
        self.assertTrue(self.status.is_file())

    def test_changed_population_partial_output_and_automatic_release_are_refused(self):
        for key,value in [('published',{'nanoAOD':{'path':'partial'}}),
                          ('expected_event_ids',[[1,1,1],[1,1,2]]),('complete',True)]:
            original=self.status.read_bytes();data=json.loads(original);data[key]=value
            self.status.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError,'receipt'):
                recovery.inspect_failure(self.config,self.row)
            self.status.write_bytes(original)
        with self.assertRaisesRegex(ValueError,'held-detector'):
            recovery.inspect_failure(self.config,dict(self.row,PeriodicRelease=True))

    def test_unknown_canonical_output_state_is_not_permission_to_retry(self):
        for code,message in [(0,''),(1,'Permission denied'),(1,'Timeout')]:
            result=type('Result',(),dict(returncode=code,stderr=message))()
            with patch.object(recovery.subprocess,'run',return_value=result),self.assertRaises(RuntimeError):
                recovery.verify_output_absence(['/eos/user/a/file.root'])
        result=type('Result',(),dict(returncode=54,stderr='[3011] No such file'))()
        with patch.object(recovery.subprocess,'run',return_value=result):
            recovery.verify_output_absence(['/eos/user/a/file.root'])

    def attempt(self):
        review=recovery.inspect_failure(self.config,self.row)
        directory=self.directory/'attempts/123';directory.mkdir(parents=True)
        edits=dict(Cmd=str(directory/'worker.sh'),TransferExecutable=False,
                   Out=str(directory/'new.out'),Err=str(directory/'new.err'),
                   TransferOutputRemaps='failure.tar.gz='+str(directory/'new.tar.gz'))
        return dict(review=review,edits=edits,phase='prepared')

    def test_single_native_edit_archives_before_release_and_preserves_old_logs(self):
        attempt=self.attempt();state=dict(attempts={'123':attempt});row=dict(self.row);calls=[]
        def native(config,cluster):
            return dict(ClusterId=100,JobStatus=2) if cluster==100 else dict(row)
        def command(config,exe,args):
            calls.append((exe,args))
            if exe=='condor_qedit':
                if args[0]=='-dry-run':
                    self.assertTrue(self.status.exists())
                    return 'allowed'
                for key,value in zip(args[1::2],args[2::2]):row[key]=json.loads(value)
                self.assertFalse(self.status.exists())
                self.assertTrue((self.directory/'attempts/123/original_status.json').exists())
            return 'reviewed native action'
        with patch.object(recovery,'exact_job',side_effect=native), \
                patch.object(recovery,'verify_output_absence'),patch.object(recovery,'command',side_effect=command):
            recovery.recover(self.config,state,123)
        self.assertEqual([c[0] for c in calls],['condor_qedit','condor_qedit','condor_release'])
        self.assertEqual(attempt['phase'],'released')
        self.assertEqual((self.production/'old.out').read_text(),'old.out')
        self.assertNotEqual(row['TransferOutputRemaps'],self.row['TransferOutputRemaps'])

    def test_changed_attempt_or_inactive_manager_cannot_archive_or_release(self):
        for changed in [dict(self.row,NumJobStarts=2),dict(self.row,Args='different')]:
            attempt=self.attempt();state=dict(attempts={'123':attempt})
            with patch.object(recovery,'exact_job',return_value=changed), \
                    patch.object(recovery,'command') as command,self.assertRaises(RuntimeError):
                recovery.recover(self.config,state,123)
            command.assert_not_called();self.assertTrue(self.status.exists())
            (self.directory/'attempts/123').rmdir()

    def test_reappearing_held_retry_is_never_released_a_second_time(self):
        attempt=self.attempt();attempt['phase']='released';state=dict(attempts={'123':attempt})
        with patch.object(recovery,'native',return_value=[self.row]), \
                patch.object(recovery,'recover') as execute:
            recovery.poll(self.config,state)
        execute.assert_not_called()
        self.assertIn('no second release',state['failures']['123'])

    def test_native_dry_run_failure_leaves_original_receipt_and_logs_untouched(self):
        attempt=self.attempt();state=dict(attempts={'123':attempt})
        def native(config,cluster):
            return dict(ClusterId=100,JobStatus=2) if cluster==100 else self.row
        with patch.object(recovery,'exact_job',side_effect=native), \
                patch.object(recovery,'verify_output_absence'), \
                patch.object(recovery,'command',side_effect=RuntimeError('native edit denied')):
            with self.assertRaisesRegex(RuntimeError,'denied'):
                recovery.recover(self.config,state,123)
        self.assertTrue(self.status.exists())
        self.assertFalse((self.directory/'attempts/123/original_status.json').exists())
        self.assertEqual(attempt['phase'],'prepared')

    def test_completed_retry_must_match_actual_runtime_native_attempt_and_population(self):
        attempt=self.attempt()
        profile=self.root/'new_profile.json';profile.write_text(json.dumps(dict(runtime_sha256='c'*64)))
        authority=json.loads(self.authority.read_text())
        authority['new_profile']=dict(path=str(profile),sha256=recovery.sha(profile))
        self.authority.write_text(json.dumps(authority))
        attempt['authority']=dict(path=str(self.authority),sha256=recovery.sha(self.authority))
        status=json.loads(self.status.read_text())
        status.update(complete=True,private_detector_runtime_sha256='c'*64,
            original_private_detector_runtime_sha256=self.old,runtime_override=dict(authority=attempt['authority']),events=2)
        self.status.write_text(json.dumps(status))
        native=dict(self.row,**attempt['edits']);native.update(JobStatus=4,ExitCode=0,NumJobStarts=2)
        recovery.completed_attempt(attempt,native)
        attempt['prepared_utc'] = '2026-10-24T12:00:00+00:00'
        recovery.completed_attempt(attempt,dict(native,NumJobStarts=1,JobCurrentStartDate=1792843201))
        with self.assertRaises(ValueError):
            recovery.completed_attempt(attempt,dict(native,NumJobStarts=1,JobCurrentStartDate=1792843199))
        for key,value in [('NumJobStarts',3),('Cmd',self.row['Cmd']),('ExitCode',66),('Args','changed')]:
            with self.assertRaises((ValueError,RuntimeError)):
                recovery.completed_attempt(attempt,dict(native,**{key:value}))
        status['private_detector_runtime_sha256']=self.old
        self.status.write_text(json.dumps(status))
        with self.assertRaisesRegex(ValueError,'actual runtime'):
            recovery.completed_attempt(attempt,native)

    def test_held_release_can_keep_counter_but_requires_fresh_execution_evidence(self):
        attempt = self.attempt()
        attempt['release_requested_utc'] = '2026-10-24T12:00:00+00:00'
        epoch = 1792843200
        self.assertFalse(recovery.retry_execution_seen(attempt, self.row))
        for field in ('JobCurrentStartDate', 'ShiftNtupleProgressEpoch'):
            self.assertFalse(recovery.retry_execution_seen(attempt, dict(self.row, **{field: epoch-1})))
            self.assertTrue(recovery.retry_execution_seen(attempt, dict(self.row, **{field: epoch+1})))
        for count in (0, 3, True):
            self.assertFalse(recovery.retry_execution_seen(attempt,
                dict(self.row, NumJobStarts=count, ShiftNtupleProgressEpoch=epoch+1)))

    def test_interrupted_preflight_is_preserved_and_never_implicitly_resubmitted(self):
        profile=self.root/'candidate.json';profile.write_text('{}')
        authority=json.loads(self.authority.read_text())
        authority['new_profile']=dict(path=str(profile),sha256=recovery.sha(profile))
        self.authority.write_text(json.dumps(authority))
        self.config['runtime_authorities'][self.old]['sha256']=recovery.sha(self.authority)
        review=recovery.inspect_failure(self.config,self.row);state=dict(attempts={})
        with patch.object(preflight,'prepare',side_effect=RuntimeError('interrupted preparation')) as prepare, \
                patch.object(statistics,'submission') as submit:
            with self.assertRaisesRegex(RuntimeError,'interrupted preparation'):
                recovery.production_preflight(self.config,state,review)
            with self.assertRaisesRegex(RuntimeError,'needs review'):
                recovery.production_preflight(self.config,state,review)
        prepare.assert_called_once();submit.assert_not_called()
        self.assertTrue(self.status.exists())

    def test_failed_retry_preparation_is_terminal_without_a_job_edit(self):
        state=dict(attempts={})
        with patch.object(recovery,'native',return_value=[self.row]), \
                patch.object(recovery,'production_preflight',return_value=dict(path='checked',sha256='a'*64)), \
                patch.object(recovery,'prepare_attempt',side_effect=ValueError('bad authority')) as prepare, \
                patch.object(recovery,'recover') as execute:
            recovery.poll(self.config,state)
            recovery.poll(self.config,state)
        prepare.assert_called_once();execute.assert_not_called()
        self.assertEqual(state['attempts']['123']['phase'],'preparation_failed')
        self.assertTrue(self.status.exists())
        self.assertIn('review',state['error'])


if __name__=='__main__':unittest.main()
