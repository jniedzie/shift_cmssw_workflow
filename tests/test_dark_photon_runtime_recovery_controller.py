# Code style: AI-code. AI-generated checks for bounded native runtime recovery.
import importlib.util
import io
import errno
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
        self.chunk=dict(chunk=0,skip=0,count=2,job=6508705,nano='/eos/user/a/c/nanoAOD/chunk_000.root',
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

    def test_shared_storage_reads_survive_prolonged_outage_but_permission_errors_are_fatal(self):
        timeout=TimeoutError(errno.ETIMEDOUT,'AFS timed out')
        with patch.object(recovery,'original_read',side_effect=[timeout]*5+[{'complete':True}]) as read,patch.object(recovery.time,'sleep'):
            self.assertEqual(recovery.read(self.status),{'complete':True})
        self.assertEqual(read.call_count,6)
        with patch.object(recovery,'original_read',side_effect=PermissionError(errno.EACCES,'denied')),patch.object(recovery.time,'sleep') as wait:
            with self.assertRaises(PermissionError):recovery.read(self.status)
        wait.assert_not_called()

    def test_final_save_outage_cannot_repeat_a_release_or_restore_an_older_snapshot(self):
        attempt=self.attempt();state=dict(attempts={'123':attempt});row=dict(self.row);calls=[];failures=[5]
        actual_save=statistics.save
        def save(path,value):
            if value['attempts']['123']['phase']=='released' and failures[0]:
                failures[0]-=1
                raise TimeoutError(errno.ETIMEDOUT,'AFS timed out after native release')
            actual_save(path,value)
        def native(config,cluster):return dict(ClusterId=100,JobStatus=2) if cluster==100 else dict(row)
        def command(config,exe,args):
            calls.append(exe)
            if exe=='condor_qedit' and args[0]!='-dry-run':
                for key,value in zip(args[1::2],args[2::2]):row[key]=json.loads(value)
            return 'single native action'
        with patch.object(recovery,'exact_job',side_effect=native),patch.object(recovery,'verify_output_absence'), \
             patch.object(recovery,'command',side_effect=command),patch.object(statistics,'save',side_effect=save), \
             patch.object(statistics.time,'sleep'):
            recovery.recover(self.config,state,123)
        self.assertEqual(calls.count('condor_release'),1);self.assertEqual(failures[0],0)
        self.assertEqual(json.loads((self.directory/'state.json').read_text())['attempts']['123']['phase'],'released')

    def test_loop_reads_state_once_and_waits_for_saved_snapshot_before_the_next_poll(self):
        state={'attempts':{},'heartbeat':0};(self.directory/'state.json').write_text(json.dumps(state))
        actual_read=recovery.original_read;reads=[];polls=[];failures=[5];actual_save=statistics.save
        class StopLoop(BaseException):pass
        def read(path):reads.append(str(path));return actual_read(path)
        def poll(config,value):
            polls.append(value['heartbeat'])
            if len(polls)==2:
                self.assertEqual(value['heartbeat'],1)
                self.assertEqual(json.loads((self.directory/'state.json').read_text())['heartbeat'],1)
                raise StopLoop()
            value['heartbeat']=1;return value
        def save(path,value):
            if failures[0]:failures[0]-=1;raise OSError(errno.ESTALE,'stale AFS handle')
            actual_save(path,value)
        with patch.object(recovery,'original_read',side_effect=read),patch.object(recovery,'poll',side_effect=poll), \
             patch.object(statistics,'save',side_effect=save),patch.object(recovery.time,'sleep'):
            with self.assertRaises(StopLoop):recovery.controller_loop(self.config)
        self.assertEqual(reads,[str(self.directory/'state.json')]);self.assertEqual(polls,[0,1])

    def test_persisted_held_release_intent_never_issues_a_second_release(self):
        attempt=self.attempt();attempt['phase']='release_intent'
        with patch.object(recovery,'exact_job',return_value=self.row),patch.object(recovery,'command') as command:
            with self.assertRaisesRegex(RuntimeError,'ambiguous'):recovery.recover(self.config,{'attempts':{'123':attempt}},123)
        command.assert_not_called()


    def timeout_authority(self):
        import hashlib
        error="TimeoutError('SIM made no event progress for 7200 seconds; records started=1')"
        report=dict(current_tier='SIM',stage_records_started=1,validated_tier_events={'SIM':0},error=error)
        text='Begin processing the first source event; ended by the saved progress guard'
        archive=self.production/'failures/old.tar.gz'
        with tarfile.open(archive,'w:gz') as source:
            for name,value in [('report.json',json.dumps(report)),('step1.log',text)]:
                payload=value.encode();item=tarfile.TarInfo(self.point+'_000/detector/'+name);item.size=len(payload);source.addfile(item,io.BytesIO(payload))
        cases=[];jobs=[]
        for variant,cluster in [('original',321),('v3',322)]:
            directory=self.root/variant;directory.mkdir();path=directory/'receipt.json';path.write_text('{"complete":true}')
            cases.append(dict(path=str(path),sha256=recovery.sha(path)))
            jobs.append(dict(ClusterId=cluster,ProcId=0,Iwd=str(directory),JobStatus=4,ExitCode=0,ExitBySignal=False))
        native=self.root/'native.json';native.write_text(json.dumps(dict(complete=True,jobs=jobs)))
        comparison=self.root/'comparison.json';comparison.write_text(json.dumps(dict(
            schema='shift-same-seed-timeout-control-comparison-v1',complete=True,original_cluster=123,
            job=self.chunk['job'],seed=230000000+100*self.chunk['job'],original_event_id=[1,1,1],
            original_full_GEN_sha256='b'*64,mismatches=[],exact_original_GEN_selection_and_seed=True,
            all_tiers_exact_identity=True,detector_electronics_unchanged=True,original_SIM_seconds=823.,v3_SIM_seconds=887.,receipts=cases)))
        proof=dict(schema='shift-reviewed-same-seed-SIM-timeout-recovery-v1',complete=True,single_reviewed_retry=True,
            original_cluster=123,production=str(self.production),point=self.point,chunk=0,
            plan_sha256=recovery.sha(self.production/'plan.json'),seal_sha256=self.seal,gen_sha256='b'*64,
            expected_event_ids=[[1,1,1],[1,1,3]],original_native=self.row,
            failure_archive=dict(path=str(archive),sha256=recovery.sha(archive)),
            failure_report_sha256=hashlib.sha256(json.dumps(report,sort_keys=True).encode()).hexdigest(),
            failure_step1_log_sha256=hashlib.sha256(text.encode()).hexdigest(),retry_stage_timeouts_seconds=[43200,7200,14400,14400],
            original_event_progress_guard_seconds=7200,timeout_cause_identified=False,
            same_seed_comparison=dict(path=str(comparison),sha256=recovery.sha(comparison)),
            native_test_completion=dict(path=str(native),sha256=recovery.sha(native)))
        path=self.root/'timeout_authority.json';path.write_text(json.dumps(proof))
        self.config['reviewed_timeout_recoveries']={'123':dict(path=str(path),sha256=recovery.sha(path))}
        return path

    def test_only_the_exact_pinned_same_seed_timeout_can_be_classified(self):
        path=self.timeout_authority();review=recovery.inspect_failure(self.config,self.row)
        self.assertEqual(review['failure_kind'],'reviewed_SIM_progress_timeout')
        self.assertEqual(review['expected_event_ids'],[[1,1,1],[1,1,3]])
        self.assertEqual(review['timeout_authority'],self.config['reviewed_timeout_recoveries']['123'])
        del self.config['reviewed_timeout_recoveries']
        with self.assertRaisesRegex(ValueError,'specifically qualified'):recovery.inspect_failure(self.config,self.row)

    def test_timeout_review_rejects_changed_job_GEN_population_budget_or_failed_control(self):
        for field,value in [('original_cluster',124),('gen_sha256','changed'),('expected_event_ids',[[1,1,1]]),
                            ('retry_stage_timeouts_seconds',[21600,7200,14400,14400]),('single_reviewed_retry',False)]:
            with self.subTest(field=field):
                path=self.timeout_authority();proof=json.loads(path.read_text());proof[field]=value;path.write_text(json.dumps(proof))
                self.config['reviewed_timeout_recoveries']['123']['sha256']=recovery.sha(path)
                with self.assertRaises(ValueError):recovery.inspect_failure(self.config,self.row)
                for variant in ('original','v3'):
                    for child in (self.root/variant).iterdir():child.unlink()
                    (self.root/variant).rmdir()

    def test_pinned_complete_preflight_migrates_without_requerying_its_history(self):
        import hashlib
        profile=self.root/'replacement_profile.json';profile.write_text('{"runtime_sha256":"new"}')
        auth=json.loads(self.authority.read_text());auth['new_profile']=dict(path=str(profile),sha256=recovery.sha(profile));self.authority.write_text(json.dumps(auth))
        self.config['runtime_authorities'][self.old]['sha256']=recovery.sha(self.authority)
        review=recovery.inspect_failure(self.config,self.row)
        identity=hashlib.sha256((review['production']+auth['new_profile']['sha256']+':'+self.point).encode()).hexdigest()
        old=self.root/'old_guard';directory=old/'preflights'/identity;directory.mkdir(parents=True)
        result=directory/'receipt.json';result.write_text('{"complete":true}')
        scan=dict(phase='complete',production=review['production'],profile=auth['new_profile'],point_scope=[self.point],directory=str(directory),
            submission={'cluster':321},receipt=dict(path=str(result),sha256=recovery.sha(result)),native_terminal={'JobStatus':4})
        original_state=self.root/'original_state.json';original_state.write_text(json.dumps(dict(preflights={identity:scan})))
        old_config=self.root/'old_config.json';old_config.write_text(json.dumps(dict(directory=str(old))))
        proof=self.root/'migration.json';proof.write_text(json.dumps(dict(schema='shift-reviewed-runtime-supervisor-migration-v1',
            replacement_directory=str(self.directory),original_configuration=dict(path=str(old_config),sha256=recovery.sha(old_config)),
            original_state_snapshot=dict(path=str(original_state),sha256=recovery.sha(original_state)))))
        self.config['state_migration']=dict(path=str(proof),sha256=recovery.sha(proof))
        state={'preflights':{identity:scan}}
        with patch.object(recovery,'native',return_value=[]),patch.object(recovery,'command') as command:
            self.assertEqual(recovery.production_preflight(self.config,state,review),scan['receipt'])
        command.assert_not_called()
        state['preflights'][identity]=dict(scan,submission={'cluster':999})
        with self.assertRaisesRegex(ValueError,'migration'):recovery.production_preflight(self.config,state,review)



if __name__=='__main__':unittest.main()
