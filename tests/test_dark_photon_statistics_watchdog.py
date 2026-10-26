# Code style: AI-code. AI-generated checks for the read-only production watchdog.
import importlib.util
import errno
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location('dp_watchdog', SCRIPTS / 'watch_dark_photon_statistics.py')
WATCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(WATCH)


class StatisticsWatchdogTest(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.root = Path(self.scratch.name)
        self.addCleanup(self.scratch.cleanup)
        worklist = self.root / 'worklist.json'
        worklist.write_text(json.dumps(dict(total_additional_events=417100)))
        self.config = dict(production_directory=str(self.root), worklist=str(worklist),
                           owner='jniedzie', condor_pool='pool.example', condor_schedd='schedd.example',
                           initial_gen_manager_cluster=100, initial_supervisor_cluster=200,
                           queue_warning_seconds=7200, queue_critical_seconds=21600,
                           heartbeat_warning_seconds=1200, gen_progress_warning_seconds=14400,
                           detector_progress_warning_seconds=50400)
        self.now = 200000.
        self.directory = self.root / 'gen_stage'
        self.directory.mkdir()
        self.receipts = {100: dict(directory=str(self.directory), receipt='fake', kind='gen_stage', submitted=self.now - 120)}
        self.counts = dict(planned_events=60, completed_events=0, started_receipts=0,
                           failed_receipts=0, complete_receipts=0, completed_points=0,
                           failed_files=[], latest_receipt_epoch=0)

    def assess(self, children, events=None, previous=None, manager_age=120):
        manager = dict(ClusterId=100, ProcId=0, JobStatus=2, Iwd=str(self.directory), JobStartDate=self.now - manager_age)
        with patch.object(WATCH, 'stage_counts', return_value=self.counts), patch.object(WATCH, 'execute_events', return_value=events or []):
            return WATCH.assess(self.config, previous or {}, [manager, *children], [], self.receipts, self.now)

    def child(self, **kwargs):
        return dict(ClusterId=101, ProcId=0, DAGManJobId=100, JobStatus=kwargs.get('status', 1),
                    Iwd=kwargs.get('iwd', str(self.directory)), NumJobStarts=kwargs.get('starts', 0),
                    QDate=self.now - kwargs.get('age', 120))

    def point_preflight_fixture(self):
        production = self.root / 'production'
        directory = production / 'point_preflights/m2_prompt'
        directory.mkdir(parents=True)
        def write(path, value):
            path.write_text(json.dumps(value))
            return dict(path=str(path), sha256=WATCH.sha(path))
        plan = write(production / 'plan.json', {'points': [{'point': 'm2_prompt'}]})
        seal = write(production / 'seal.json', {'files': {}})
        inventory = write(production / 'gen_inventory.json', {'points': {'m2_prompt': {}}})
        profile = write(production / 'profile.json', {'runtime_sha256': 'a' * 64})
        scan = write(directory / 'preflight.json', dict(point_scope=['m2_prompt'],
            production=str(production), output=str(directory), original_plan=plan,
            original_seal=seal, inventory=inventory, replacement_profile=profile,
            replacement_runtime_sha256='a' * 64))
        (directory / 'worker.sh').write_text('#!/bin/bash\nexit 0\n')
        (directory / 'worker.sub').write_text('executable = ' + str(directory / 'worker.sh') + '\n')
        (production / 'pipeline.dag').write_text('JOB P_m2_prompt ' + str(directory / 'worker.sub') + '\n')
        task = dict(complete=True, config=scan,
            worker=dict(path=str(directory / 'worker.sh'), sha256=WATCH.sha(directory / 'worker.sh')),
            submit=dict(path=str(directory / 'worker.sub'), sha256=WATCH.sha(directory / 'worker.sub')),
            receipt=str(directory / 'receipt.json'))
        manifest = write(production / 'point_preflights/manifest.json', dict(
            schema='shift-independent-point-preflights-v1', complete=True,
            original_plan_sha256=plan['sha256'], original_seal_sha256=seal['sha256'],
            original_inventory_sha256=inventory['sha256'],
            pipeline_dag_sha256=WATCH.sha(production / 'pipeline.dag'),
            replacement_runtime_sha256='a' * 64, points={'m2_prompt': task}))
        manager = dict(ClusterId=100, Cmd='/usr/bin/condor_dagman',
                       ShiftDPPointPreflightManifestSHA256=manifest['sha256'])
        child = self.child(iwd=str(directory), status=2)
        child['Cmd'] = str(directory / 'worker.sh')
        return production, directory, manager, child

    def test_each_runtime_repair_uses_its_own_pinned_validator_revision(self):
        dependencies={};authorities={};validators={}
        for index,name in enumerate(['old','new'],100):
            tools=self.root/name;tools.mkdir();helper=tools/'validator.py'
            imported=tools/'same_named_adapter.py';imported.write_text('REVISION='+repr(name)+'\n')
            dependencies[str(imported)]=WATCH.sha(imported)
            helper.write_text('import same_named_adapter\n'
                'def validate_unsubmitted_runtime_recovery(original,batch,authority):\n'
                ' import json\n from pathlib import Path\n'
                ' proof=json.loads(Path(authority["path"]).read_text())\n'
                ' assert proof["expected_validator"]==same_named_adapter.REVISION\n return proof\n')
            directory=self.root/(name+'_DAG');directory.mkdir()
            descriptor=directory/'pipeline.dag.condor.sub';descriptor.write_text('sealed manager')
            (directory/'submission.json').write_text(json.dumps(dict(cluster=index,task=str(directory),
                submit_sha256=WATCH.sha(descriptor),utc='2026-10-26T09:00:00+00:00')))
            proof=self.root/(name+'.json');proof.write_text(json.dumps(dict(point='p',
                expected_validator=name,recovery_directory=str(directory))))
            authorities[name]=dict(path=str(proof),sha256=WATCH.sha(proof))
            validators[name]=dict(path=str(helper),sha256=WATCH.sha(helper))
            dependencies.update({str(proof):WATCH.sha(proof),str(helper):WATCH.sha(helper)})
        config=dict(production_directory='/science',dependencies=dependencies,
            reviewed_unsubmitted_runtime_recoveries=authorities,
            unsubmitted_runtime_validator=validators['new'],unsubmitted_runtime_validators=validators)
        self.assertEqual(set(WATCH.unsubmitted_runtime_managers(config)),{100,101})
        config['unsubmitted_runtime_validators']['old']=validators['new']
        with self.assertRaises(AssertionError):WATCH.unsubmitted_runtime_managers(config)

    def test_runtime_rescue_uses_only_the_frozen_read_only_validator(self):
        tools=self.root/'rescue_tools';tools.mkdir()
        helper=tools/'validate.py'
        marker=self.root/'validated.json'
        helper.write_text('import json\nfrom pathlib import Path\n'
            'def adopt_reviewed_runtime_rescues(config,state,*,validate_only=False):\n'
            ' assert validate_only is True\n'
            ' Path('+repr(str(marker))+').write_text(json.dumps(state))\n')
        proof_path=self.root/'rescue.json'
        native=dict(ClusterId=123,ProcId=0,JobStatus=4,ExitCode=1)
        proof_path.write_text(json.dumps(dict(previous_terminal_native_ad=native,
            previous_directory='/old/recovery',original_directory='/science/waves/w003/production',
            replacement_directory='/new/recovery')))
        pin=dict(path=str(proof_path),sha256=WATCH.sha(proof_path))
        config=dict(production_directory='/science',reviewed_detector_runtime_rescues={'3':pin},
            runtime_rescue_validator=dict(path=str(helper),sha256=WATCH.sha(helper)),
            dependencies={str(helper):WATCH.sha(helper),str(proof_path):WATCH.sha(proof_path)})
        result=WATCH.reviewed_runtime_rescues(config)
        self.assertEqual(result[123]['native'],native)
        self.assertEqual(result[123]['recovery_submission'],'/new/recovery/submission.json')
        self.assertEqual(json.loads(marker.read_text())['waves']['3']['cluster'],123)
        helper.write_text('raise AssertionError("changed helper must never run")')
        with self.assertRaisesRegex(ValueError,'frozen authority'):
            WATCH.reviewed_runtime_rescues(config)

    def test_native_bound_point_preflight_has_its_declared_child_directory(self):
        production, directory, manager, child = self.point_preflight_fixture()
        self.assertTrue(WATCH.declared_point_preflight_child(production, child, manager))
        child['DAGManJobId'] = 999
        self.assertFalse(WATCH.declared_point_preflight_child(production, child, manager))

    def test_point_preflight_requires_native_manifest_binding_and_exact_command(self):
        production, directory, manager, child = self.point_preflight_fixture()
        manager['ShiftDPPointPreflightManifestSHA256'] = 'b' * 64
        self.assertFalse(WATCH.declared_point_preflight_child(production, child, manager))
        manager['ShiftDPPointPreflightManifestSHA256'] = WATCH.sha(production / 'point_preflights/manifest.json')
        child['Cmd'] = '/bin/bash'
        self.assertFalse(WATCH.declared_point_preflight_child(production, child, manager))

    def test_changed_point_scope_cannot_gain_monitor_approval(self):
        production, directory, manager, child = self.point_preflight_fixture()
        path = directory / 'preflight.json'
        scan = json.loads(path.read_text())
        scan['point_scope'] = ['m2_prompt', 'm40_prompt']
        path.write_text(json.dumps(scan))
        manifest_path = production / 'point_preflights/manifest.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['points']['m2_prompt']['config']['sha256'] = WATCH.sha(path)
        manifest_path.write_text(json.dumps(manifest))
        manager['ShiftDPPointPreflightManifestSHA256'] = WATCH.sha(manifest_path)
        self.assertFalse(WATCH.declared_point_preflight_child(production, child, manager))

    def test_undeclared_point_directory_is_not_a_general_child_iwd_exception(self):
        production, directory, manager, child = self.point_preflight_fixture()
        child['Iwd'] = str(production / 'point_preflights/m40_prompt')
        self.assertFalse(WATCH.declared_point_preflight_child(production, child, manager))

    def test_long_shared_storage_read_outage_keeps_the_verified_snapshot(self):
        target = self.root / 'verified.json'
        target.write_text('{"terminal_history": [123]}')
        original = Path.read_text
        failures = []

        def transient(path, *args, **kwargs):
            if path == target and len(failures) < 5:
                failures.append(True)
                raise OSError(errno.ETIMEDOUT, 'long AFS outage')
            return original(path, *args, **kwargs)

        with patch.object(Path, 'read_text', transient), patch.object(WATCH.time, 'sleep'), \
                patch.object(WATCH.sys, 'stderr'):
            self.assertEqual(WATCH.read(target), {'terminal_history': [123]})
        self.assertEqual(len(failures), 5)

    def test_retrying_record_does_not_repeat_scheduler_poll_or_discard_history(self):
        report = {'terminal_history': [123]}
        calls = []

        def record():
            calls.append(report)
            if len(calls) < 6:
                raise OSError(errno.ESTALE, 'AFS outage')
            return report

        with patch.object(WATCH.time, 'sleep'), patch.object(WATCH.sys, 'stderr'):
            self.assertIs(WATCH.retry_shared_storage(record), report)
        self.assertEqual(len(calls), 6)
        self.assertTrue(all(value is report for value in calls))

    def test_shared_storage_retry_does_not_hide_corrupt_or_unauthorized_state(self):
        for error in (ValueError('changed identity'), PermissionError(errno.EACCES, 'denied'),
                      OSError(errno.ENOSPC, 'quota')):
            with self.subTest(error=error), patch.object(WATCH.time, 'sleep') as sleep:
                with self.assertRaises(type(error)):
                    WATCH.retry_shared_storage(lambda: (_ for _ in ()).throw(error))
                sleep.assert_not_called()

    def test_first_minutes_in_queue_are_not_a_failure_or_execution_claim(self):
        report = self.assess([self.child()])
        self.assertEqual(report['health'], 'ok')
        self.assertEqual(report['observed_state'], 'awaiting_payload_execution')

    def test_coverage_recovery_keeps_original_failure_and_rejects_changed_success(self):
        def pin(name, value):
            path = self.root/name
            path.write_text(json.dumps(value))
            return dict(path=str(path), sha256=WATCH.sha(path))
        old = self.root/'coverage'
        new = self.root/'repaired_coverage'
        proof = dict(schema='shift-reviewed-meson-coverage-recovery-v1', scope='coverage-only',
            worklist_sha256=WATCH.sha(self.config['worklist']),
            original_submission=pin('original.json', dict(cluster=123, task=str(old))),
            original_status=pin('failed.json', dict(complete=False, error='preserved failure')),
            original_native_failure=pin('native_failure.json', dict(ClusterId=123, ProcId=0,
                Iwd=str(old), ExitCode=1, ExitBySignal=False, JobStatus=5)),
            replacement_submission=pin('replacement.json', dict(cluster=124, task=str(new))),
            replacement_native_success=pin('native_success.json', dict(ClusterId=124, ProcId=0,
                Iwd=str(new), ExitCode=0, ExitBySignal=False, JobStatus=4)),
            replacement_status=pin('success.json', dict(complete=True,
                report=pin('report.json', dict(physics_complete=False)))))
        authority = pin('review.json', proof)
        self.config.update(reviewed_meson_coverage_recoveries=[authority],
                           dependencies={authority['path']: authority['sha256']})
        reviewed = WATCH.reviewed_terminal_failures(self.config)
        self.assertEqual(reviewed[123]['native']['ExitCode'], 1)
        self.assertEqual(reviewed[123]['recovery_submission'], proof['replacement_submission']['path'])
        (self.root/'native_success.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'Changed retained'):
            WATCH.reviewed_terminal_failures(self.config)

    def test_partial_gen_recovery_discovers_replacement_and_keeps_original_failure(self):
        old=self.root/'waves/w000/gen_stage';new=old.with_name('gen_stage_recovery_v1')
        for path,cluster in ((old,100),(new,300)):
            path.mkdir(parents=True);(path/'statuses').mkdir()
            (path/'submission.json').write_text(json.dumps(dict(cluster=cluster,task=str(path),utc='1970-01-01T00:00:00+00:00')))
            (path/'plan.json').write_text('{}')
        (old/'statuses/m2_prompt.json').write_text(json.dumps(dict(complete=False,error='original contract mismatch')))
        successful=old/'statuses/m15_prompt.json';successful.write_text(json.dumps(dict(complete=True)))
        proof=self.root/'review.json';proof.write_text(json.dumps(dict(schema='shift-reviewed-partial-GEN-recovery-v1',
            original_stage=str(old),replacement_stage=str(new),physics_seeds_exposure_unchanged=True,
            original_plan_sha256=WATCH.sha(old/'plan.json'),recovered_plan_sha256=WATCH.sha(new/'plan.json'),
            original_gen_manager=100,failed_point='m2_prompt',reused_points={'m15':dict(status=str(successful),sha256=WATCH.sha(successful))})))
        self.config.update(reviewed_gen_stage_overrides={'0':dict(directory=str(new),review=dict(path=str(proof),sha256=WATCH.sha(proof)))},dependencies={str(proof):WATCH.sha(proof)})
        receipts=WATCH.manager_receipts(self.root,config=self.config)
        self.assertEqual(receipts[100]['kind'],'reviewed_orchestration')
        self.assertEqual(receipts[300]['kind'],'gen_stage')
        native=dict(ClusterId=100,ProcId=0,JobStatus=3,Iwd=str(old),ExitCode=0,ExitBySignal=False)
        with patch.object(WATCH,'stage_counts',return_value=self.counts),patch.object(WATCH,'execute_events',return_value=[]):
            report=WATCH.assess(self.config,{},[],[native],{100:receipts[100]},self.now)
            self.assertTrue(any(x['code']=='reviewed_orchestration_failure_100' for x in report['issues']))
            self.assertFalse(any(x['severity']=='critical' for x in report['issues']))
            revived=dict(native,ClusterId=999,JobStatus=2)
            report=WATCH.assess(self.config,{},[revived],[native],{100:receipts[100]},self.now)
            self.assertTrue(any(x['code']=='reviewed_failure_active_999' for x in report['issues']))
        successful.write_text('{"complete":false}')
        with self.assertRaisesRegex(ValueError,'successful population'):WATCH.operational_recoveries(self.config)

    def test_reviewed_failed_operational_handoff_keeps_its_sealed_old_identity(self):
        batch='b000';science=self.root/'batches'/batch;science.mkdir(parents=True)
        (science/'source_status.json').write_text('{"complete":true}')
        owner=self.root/'old_controller';old=owner/'handoffs'/batch;old.mkdir(parents=True)
        (old/'run.sh').write_text('original failed launcher')
        (old/'worker.sub.in').write_text('seal=OPERATIONS_SEAL_DIGEST')
        seal=owner/'seal.json';seal.write_text(json.dumps(dict(files={str(p):WATCH.sha(p)
            for p in [old/'run.sh',old/'worker.sub.in']})))
        (old/'worker.sub').write_text((old/'worker.sub.in').read_text().replace('OPERATIONS_SEAL_DIGEST',WATCH.sha(seal)))
        receipt=dict(cluster=100,task=str(old),submit_sha256=WATCH.sha(old/'worker.sub'))
        (old/'submission.json').write_text(json.dumps(receipt));(old/'failed.err').write_text('preserved failure')
        review=self.root/'operational_review.json';review.write_text(json.dumps(dict(
            schema='shift-meson-handoff-recovery-v1',original_handoff_directory=str(old),
            original_controller_seal=dict(path=str(seal),sha256=WATCH.sha(seal)),
            original_submission=receipt,gen_or_detector_rerun_allowed=False,
            source_status_sha256=WATCH.sha(science/'source_status.json'),failed_log=dict(path=str(old/'failed.err')))))
        self.config.update(operational_handoffs=dict(directory=str(self.root/'new_controller/handoffs'),
            reviewed_failures={batch:dict(path=str(review),sha256=WATCH.sha(review))}),
            dependencies={str(review):WATCH.sha(review),str(seal):WATCH.sha(seal)})
        result=WATCH.operational_recoveries(self.config)[100]
        self.assertEqual(result['original_directory'],str(old))
        self.assertEqual(result['recovery_submission'],str(self.root/'new_controller/handoffs'/batch/'submission.json'))
        (old/'run.sh').write_text('unreviewed replacement')
        with self.assertRaisesRegex(ValueError,'launcher changed'):WATCH.operational_recoveries(self.config)

    def test_declared_meson_operational_handoff_is_discovered_with_exact_review(self):
        old=self.root/'batches/b000/handoff';old.mkdir(parents=True)
        source=old.parent/'source_status.json';source.write_text('{"complete":true}')
        failure=old/'worker.err';failure.write_text('original Python bootstrap failure')
        original=dict(cluster=100,task=str(old),utc='1970-01-01T00:00:00+00:00')
        (old/'submission.json').write_text(json.dumps(original))
        destination=self.root/'operations/handoffs';new=destination/'b000';new.mkdir(parents=True)
        (new/'submission.json').write_text(json.dumps(dict(cluster=300,task=str(new),utc='1970-01-01T00:00:00+00:00')))
        proof=self.root/'review.json';proof.write_text(json.dumps(dict(schema='shift-meson-handoff-recovery-v1',
            original_submission=original,source_status_sha256=WATCH.sha(source),gen_or_detector_rerun_allowed=False,
            failed_log=dict(path=str(failure)))))
        self.config.update(operational_handoffs=dict(directory=str(destination),reviewed_failures={'b000':dict(path=str(proof),sha256=WATCH.sha(proof))}),dependencies={str(proof):WATCH.sha(proof)})
        receipts=WATCH.manager_receipts(self.root,config=self.config)
        self.assertEqual(receipts[100]['kind'],'reviewed_orchestration');self.assertEqual(receipts[300]['kind'],'handoff')
        # A later controller keeps the successful old replacement while new
        # batches use its own handoff directory.
        (new/'worker.sub').write_text('queue\n')
        receipt=WATCH.read(new/'submission.json')
        receipt['submit_sha256']=WATCH.sha(new/'worker.sub')
        (new/'submission.json').write_text(json.dumps(receipt))
        self.config['operational_handoffs'].update(directory=str(self.root/'next/handoffs'),
                                                  preserved_directories={'b000':str(new)})
        for path in (new/'submission.json',new/'worker.sub'):
            self.config['dependencies'][str(path)]=WATCH.sha(path)
        receipts=WATCH.manager_receipts(self.root,config=self.config)
        self.assertEqual(receipts[300]['directory'],str(new))
        self.assertEqual(WATCH.operational_recoveries(self.config)[100]['recovery_submission'],str(new/'submission.json'))
        source.write_text('{"complete":false}')
        with self.assertRaisesRegex(ValueError,'source authority'):WATCH.operational_recoveries(self.config)

    def test_preserved_handoff_requires_pinned_receipt_and_matching_submit_identity(self):
        directory=self.root/'earlier/handoffs/b001';directory.mkdir(parents=True)
        submit=directory/'worker.sub';submit.write_text('queue\n')
        receipt=directory/'submission.json'
        receipt.write_text(json.dumps(dict(cluster=301,task=str(directory),
            submit_sha256=WATCH.sha(submit),utc='1970-01-01T00:00:00+00:00')))
        self.config.update(operational_handoffs=dict(directory=str(self.root/'next/handoffs'),
            preserved_directories={'b001':str(directory)}),
            dependencies={str(path):WATCH.sha(path) for path in (submit,receipt)})
        record=WATCH.manager_receipts(self.root,config=self.config)[301]
        self.assertEqual(record['kind'],'handoff')
        self.assertEqual(record['directory'],str(directory))
        submit.write_text('changed\n')
        with self.assertRaisesRegex(ValueError,'frozen watchdog authority'):
            WATCH.manager_receipts(self.root,config=self.config)
        self.config['dependencies'][str(submit)]=WATCH.sha(submit)
        with self.assertRaisesRegex(ValueError,'submitted identity'):
            WATCH.manager_receipts(self.root,config=self.config)
        self.config['operational_handoffs']['preserved_directories']['b001']='relative/b001'
        with self.assertRaisesRegex(ValueError,'persistent batch directory'):
            WATCH.manager_receipts(self.root,config=self.config)

    def test_seven_real_gen_qualification_rows_need_no_expected_signal_count(self):
        masses=('m0p22','m0p25','m0p3','m0p4','m0p5','m0p7','m0p9')
        rows=[dict(batch='gen_qualification_%02d'%index,point=mass+'_prompt',source_collisions=10000,
                   gen_qualification_only=True,operational_canary=False) for index,mass in enumerate(masses)]
        rows.extend(dict(batch='operational_%04d'%index,point='m0p3_'+life,source_collisions=10000,
                         operational_canary=True) for index,life in enumerate(('prompt','lab_30m')))
        rows.append(dict(batch='bulk_0002',point='m0p22_prompt',source_collisions=42083,operational_canary=False))
        worklist=Path(self.config['worklist'])
        worklist.write_text(json.dumps(dict(schema='shift-dp-meson-production-v1',batches=rows)))
        report=self.assess([])
        self.assertEqual(report['actual_planned_progress']['authorized_source_collisions'],132083)
        self.assertIsNone(report['actual_planned_progress']['expected_signal_events'])
        rows[-1]['expected_signal_events']=123
        worklist.write_text(json.dumps(dict(schema='shift-dp-meson-production-v1',batches=rows)))
        report=self.assess([])
        self.assertEqual(report['actual_planned_progress']['expected_signal_events'],123)

    def test_source_qualifications_do_not_crash_or_replace_aggregated_publication_counts(self):
        worklist=dict(schema='shift-dp-meson-production-v1',batches=[dict(batch=name,point='m0p3_prompt',
            source_collisions=10000,expected_signal_events=25) for name in ('qualification','first','second')])
        Path(self.config['worklist']).write_text(json.dumps(worklist))
        state=dict(complete=False,published={'m0p3_prompt':dict(events=8)},batches={
            'qualification':dict(published=dict(source_qualification_only=True)),
            'first':dict(published=dict(events=2)),'second':dict(published=dict(events=3))})
        (self.root/'state.json').write_text(json.dumps(state))
        report=self.assess([])
        self.assertEqual(report['actual_planned_progress']['published_total_events_by_point'],{'m0p3_prompt':8})
        state.pop('published');(self.root/'state.json').write_text(json.dumps(state))
        report=self.assess([])
        self.assertEqual(report['actual_planned_progress']['published_total_events_by_point'],{'m0p3_prompt':5})

    def test_transient_afs_write_is_retried_without_losing_the_record(self):
        original = Path.write_text
        attempts = []
        def flaky(path, body):
            attempts.append(path)
            if len(attempts) == 1:
                raise TimeoutError(errno.ETIMEDOUT, 'AFS timeout')
            return original(path, body)
        target = self.root/'health.json'
        with patch.object(Path, 'write_text', new=flaky), patch.object(WATCH.time, 'sleep') as sleep:
            WATCH.save(target, dict(health='ok'))
        self.assertEqual(WATCH.read(target), dict(health='ok'))
        self.assertEqual(len(attempts), 2)
        sleep.assert_called_once_with(1)

    def test_permanent_or_repeated_storage_errors_remain_failures(self):
        for failure, expected_calls in ((PermissionError(errno.EACCES, 'Denied'), 1),
                                        (TimeoutError(errno.ETIMEDOUT, 'AFS timeout'), 4)):
            with self.subTest(failure=failure), patch.object(Path, 'write_text', side_effect=failure) as write, \
                    patch.object(WATCH.time, 'sleep'):
                with self.assertRaises(OSError):
                    WATCH.save(self.root/'health.json', dict(health='ok'))
                self.assertEqual(write.call_count, expected_calls)

    def meson_recovery(self):
        original=dict(cluster=100,task=str(self.directory),submit_sha256='original')
        (self.directory/'submission.json').write_text(json.dumps(original))
        for name in ('plan.json','seal.json'):
            (self.directory/name).write_text('{}')
        manager=dict(ClusterId=100,ProcId=0,Iwd=str(self.directory),JobStatus=4,
                     ExitCode=1,ExitBySignal=False)
        finalizer=dict(manager,ClusterId=101,ExitCode=124)
        terminal=self.root/'terminal_native_review.json'
        terminal.write_text(json.dumps(dict(queue=[],history={'100':[manager],'101':[finalizer]})))
        recovery=self.root/'finalizer_recovery'
        recovery.mkdir()
        (recovery/'pipeline.dag').write_text('JOB FINALIZE finalizer.sub\nJOB COLLECT collect.sub\n')
        (recovery/'submission.json').write_text(json.dumps(dict(cluster=400,task=str(recovery),utc='1970-01-03T07:31:20+00:00')))
        proof=self.root/'meson_recovery.json'
        proof.write_text(json.dumps(dict(schema='shift-meson-finalizer-recovery-v1',
            original_submission=original,original_plan_sha256=WATCH.sha(self.directory/'plan.json'),
            original_seal_sha256=WATCH.sha(self.directory/'seal.json'),
            allowed_stages=['finalizer','collect'],gen_or_detector_jobs_allowed=False,
            original_manager_exit_code=1,original_finalizer_cluster=101,original_finalizer_exit_code=124,
            terminal_native_review=dict(path=str(terminal),sha256=WATCH.sha(terminal)),
            recovery_directory=str(recovery),recovery_files={str(recovery/'pipeline.dag'):WATCH.sha(recovery/'pipeline.dag')})))
        self.config['reviewed_meson_finalizer_recoveries']=[dict(path=str(proof),sha256=WATCH.sha(proof))]
        self.config['dependencies']={str(proof):WATCH.sha(proof),str(terminal):WATCH.sha(terminal)}
        self.receipts[400]=dict(directory=str(recovery),receipt=str(recovery/'submission.json'),
            submitted=self.now-120,kind='production',external_prerequisite=True)
        return manager,recovery,terminal

    def test_meson_finalizer_recovery_preserves_failure_without_double_counting_events(self):
        manager,recovery,terminal=self.meson_recovery()
        current=dict(ClusterId=400,ProcId=0,Iwd=str(recovery),JobStatus=2,JobStartDate=self.now-120)
        child=dict(ClusterId=401,ProcId=0,DAGManJobId=400,Iwd=str(recovery),JobStatus=2)
        recovered_counts=dict(self.counts,completed_events=1947,complete_receipts=98)
        def counts(path):
            return recovered_counts if Path(path)==recovery else self.counts
        with patch.object(WATCH,'stage_counts',side_effect=counts), \
             patch.object(WATCH,'execute_events',return_value=['401.0']):
            report=WATCH.assess(self.config,{},[current,child],[manager],self.receipts,self.now)
        self.assertEqual(report['health'],'warning')
        self.assertEqual(report['actual_planned_progress']['detector_completed_events'],0)
        self.assertTrue(any(x['code']=='reviewed_meson_failure_100' for x in report['issues']))
        self.assertEqual(report['reviewed_terminal_failures'][0]['failed_finalizer_native']['ExitCode'],124)
        self.assertTrue(report['stages']['400']['external_prerequisite'])

    def test_original_meson_directory_cannot_reappear_under_a_different_job_id(self):
        manager,recovery,terminal=self.meson_recovery()
        revived=dict(ClusterId=999,ProcId=0,Iwd=str(self.directory),JobStatus=2,DAGManJobId=100)
        with patch.object(WATCH,'stage_counts',return_value=self.counts), \
             patch.object(WATCH,'execute_events',return_value=[]):
            report=WATCH.assess(self.config,{},[revived],[manager],self.receipts,self.now)
        self.assertEqual(report['health'],'critical')
        self.assertTrue(any(x['code']=='reviewed_failure_active_999' for x in report['issues']))

    def test_meson_native_cache_and_original_plan_remain_pinned(self):
        manager,recovery,terminal=self.meson_recovery()
        terminal.write_text('{}')
        with self.assertRaisesRegex(ValueError,'population or native record'):
            WATCH.reviewed_terminal_failures(self.config)

    def operation(self, name='admission', cluster=300, *, optional=False):
        directory=self.root/name
        directory.mkdir()
        iwd='/afs/project/operations/'+name
        receipt=directory/'submission.json'
        if not optional:
            receipt.write_text(json.dumps(dict(cluster=cluster,submitted_epoch=self.now-120,Iwd=iwd)))
        specification=dict(name=name,receipt=str(receipt),iwd=iwd,
            expected_cluster=cluster,receipt_optional=optional,state_path=str(directory/'state.json'),
            heartbeat_path=str(directory/'heartbeat.json'),require_state_complete=True)
        self.config.setdefault('operational_jobs',[]).append(specification)
        return directory,specification

    def assess_operations(self, rows=(), history=()):
        receipts=WATCH.operational_receipts(self.config['operational_jobs'])
        return WATCH.assess(self.config,{},list(rows),list(history),receipts,self.now)

    def test_operational_numeric_receipt_uses_explicit_iwd(self):
        directory,specification=self.operation()
        elsewhere=self.root/'receipt_elsewhere.json'
        elsewhere.write_bytes((directory/'submission.json').read_bytes())
        specification['receipt']=str(elsewhere)
        receipt=WATCH.operational_receipts([specification])[300]
        self.assertEqual(receipt['directory'],specification['iwd'])
        self.assertEqual(receipt['submitted'],self.now-120)
        self.assertEqual(receipt['kind'],'operation')

    def test_operational_receipt_rejects_changed_native_identity(self):
        directory,specification=self.operation()
        specification['expected_cluster']=301
        with self.assertRaisesRegex(ValueError,'identity'):
            WATCH.operational_receipts([specification])
        specification['expected_cluster']=300
        specification['receipt_sha256']='f'*64
        with self.assertRaisesRegex(ValueError,'identity'):
            WATCH.operational_receipts([specification])

    def test_numeric_state_and_separate_live_heartbeat_keep_wait_healthy(self):
        directory,specification=self.operation()
        (directory/'state.json').write_text(json.dumps(dict(checked_epoch=self.now-10000,
            phase='waiting_for_actual_standalone_qualification')))
        (directory/'heartbeat.json').write_text(json.dumps(dict(checked_epoch=self.now-10)))
        row=dict(ClusterId=300,ProcId=0,JobStatus=2,Iwd=specification['iwd'],JobStartDate=self.now-10000)
        report=self.assess_operations([row])
        self.assertEqual(report['health'],'ok')
        self.assertEqual(report['operations']['admission']['heartbeat_epoch'],self.now-10)
        self.assertFalse(report['controller_finished'])

    def test_operational_stale_heartbeat_and_recorded_failure_are_critical(self):
        directory,specification=self.operation()
        (directory/'state.json').write_text(json.dumps(dict(checked_epoch=self.now-10000,error='failed')))
        row=dict(ClusterId=300,ProcId=0,JobStatus=2,Iwd=specification['iwd'],JobStartDate=self.now-10000)
        report=self.assess_operations([row])
        self.assertEqual(report['health'],'critical')
        self.assertTrue(any(x['code']=='operation_heartbeat_admission' for x in report['issues']))
        self.assertTrue(any(x['code']=='operation_error_admission' for x in report['issues']))

    def test_admission_done_does_not_finish_before_future_retirement(self):
        admission,admission_specification=self.operation()
        retirement,specification=self.operation('retirement',400,optional=True)
        specification['after_job']='admission'
        self.config['finish_requires_operational_jobs']=['retirement']
        (self.root/'state.json').write_text(json.dumps(dict(complete=True,checked_epoch=self.now)))
        (admission/'state.json').write_text(json.dumps(dict(complete=True,checked_epoch=self.now-1200)))
        ended=dict(ClusterId=300,ProcId=0,JobStatus=4,Iwd=admission_specification['iwd'],ExitCode=0,
                   ExitBySignal=False,CompletionDate=self.now-1200)
        report=self.assess_operations(history=[ended])
        self.assertTrue(report['supervisor_finished'])
        self.assertFalse(report['controller_finished'])
        self.assertTrue(report['operations']['retirement']['receipt_pending'])
        self.assertTrue(any(x['code']=='operation_handoff_retirement' for x in report['issues']))
        (retirement/'submission.json').write_text(json.dumps(dict(cluster=400,submitted_epoch=self.now,Iwd=specification['iwd'])))
        held=dict(ClusterId=400,ProcId=0,JobStatus=5,Iwd=specification['iwd'],HoldReason='failure')
        report=self.assess_operations([held],[ended])
        self.assertFalse(report['controller_finished'])
        self.assertEqual(report['health'],'critical')
        (retirement/'state.json').write_text(json.dumps(dict(complete=True,checked_utc='1970-01-03T07:33:20+00:00')))
        closed=dict(held,JobStatus=4,ExitCode=0,ExitBySignal=False,CompletionDate=self.now)
        report=self.assess_operations(history=[ended,closed])
        self.assertTrue(report['controller_finished'])

    def test_numeric_supervisor_time_and_heartbeat_fallback(self):
        (self.root/'state.json').write_text(json.dumps(dict(checked_epoch=self.now-10000)))
        heartbeat=self.root/'heartbeat.json'
        heartbeat.write_text(json.dumps(dict(checked_epoch=self.now-10)))
        self.config['supervisor_heartbeat_path']=str(heartbeat)
        report=self.assess([])
        self.assertFalse(any(x['code']=='supervisor_heartbeat' for x in report['issues']))

    def test_invalid_numeric_timestamp_is_unknown_not_fresh(self):
        for value in [True,float('nan'),-1]:
            with self.assertRaises(ValueError):
                WATCH.record_epoch(dict(checked_epoch=value),'checked_epoch')

    def reviewed_failure(self):
        for name in ('plan.json', 'seal.json', 'aggregate.sub'):
            (self.directory / name).write_text('original failed attempt')
        (self.directory / 'submission.json').write_text(json.dumps(dict(cluster=100)))
        log = self.directory / 'failure.err'
        log.write_text('Runtime download authentication failed before aggregation')
        native = dict(ClusterId=100, ProcId=0, Iwd=str(self.directory), JobStatus=4,
                      ExitBySignal=False, ExitCode=54)
        record = dict(directory=str(self.directory), plan_sha256=WATCH.sha(self.directory / 'plan.json'),
                      seal_sha256=WATCH.sha(self.directory / 'seal.json'),
                      submit_sha256=WATCH.sha(self.directory / 'aggregate.sub'))
        path = self.root / 'reviewed_failure.json'
        path.write_text(json.dumps(dict(schema='shift-reviewed-aggregate-retirement-v1',
            directory=str(self.directory), aggregate_record=record, terminal_native_ad=native,
            payload_started=False, queue_absent=True, failure_evidence_sha256={str(log): WATCH.sha(log)})))
        self.config['retired_aggregate_attempts'] = [dict(path=str(path), sha256=WATCH.sha(path))]
        self.config['dependencies'] = {str(path): WATCH.sha(path)}
        return native

    def test_reviewed_failure_stays_visible_and_live_reappearance_is_critical(self):
        native = self.reviewed_failure()
        with patch.object(WATCH, 'stage_counts', return_value=self.counts):
            report = WATCH.assess(self.config, {}, [], [native], self.receipts, self.now)
            self.assertEqual(report['health'], 'ok')
            self.assertEqual(report['reviewed_terminal_failures'][0]['native']['ExitCode'], 54)
            active = dict(native, JobStatus=2)
            report = WATCH.assess(self.config, {}, [active], [], self.receipts, self.now)
            self.assertEqual(report['health'], 'critical')

    def test_changed_reviewed_error_evidence_or_native_exit_is_rejected(self):
        native = self.reviewed_failure()
        with patch.object(WATCH, 'stage_counts', return_value=self.counts):
            with self.assertRaisesRegex(ValueError, 'Native history'):
                WATCH.assess(self.config, {}, [], [dict(native, ExitCode=0)], self.receipts, self.now)
        (self.directory / 'failure.err').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'evidence changed'):
            WATCH.reviewed_terminal_failures(self.config)

    def test_removed_held_aggregate_requires_exact_reviewed_original_failure(self):
        self.reviewed_failure()
        path = self.root / 'reviewed_failure.json'
        proof = json.loads(path.read_text())
        native = dict(proof['terminal_native_ad'], JobStatus=3, ExitCode=1)
        proof.update(terminal_native_ad=native, failure_kind='GEN_ledger_log_path_relocation',
                     original_held_native_ad=dict(native, JobStatus=5))
        def pin():
            path.write_text(json.dumps(proof))
            authority = dict(path=str(path), sha256=WATCH.sha(path))
            self.config.update(retired_aggregate_attempts=[authority],
                               dependencies={str(path): authority['sha256']})
        pin()
        self.assertEqual(WATCH.reviewed_terminal_failures(self.config)[100]['native'], native)
        for key, value in [('JobStatus', 2), ('ExitCode', 0), ('Iwd', '/another/task'),
                           ('ClusterId', 999), ('ExitBySignal', True)]:
            with self.subTest(key=key):
                proof['original_held_native_ad'] = dict(native, JobStatus=5)
                proof['original_held_native_ad'][key] = value
                pin()
                with self.assertRaisesRegex(ValueError, 'exact terminal attempt'):
                    WATCH.reviewed_terminal_failures(self.config)

    def test_recovered_handoff_requires_complete_unchanged_gen_and_tracks_pending_detector(self):
        stage = self.root / 'waves/w001/gen_stage'
        stage.mkdir(parents=True)
        (stage / 'submission.json').write_text(json.dumps(dict(cluster=100)))
        for name in ('gen_inventory.json', 'gen_publication.json'):
            (stage / name).write_text(json.dumps(dict(complete=True)))
        native = dict(ClusterId=100, ProcId=0, Iwd=str(stage), JobStatus=4,
                      ExitBySignal=False, ExitCode=1)
        proof = self.root / 'reviewed_gen_handoff.json'
        proof.write_text(json.dumps(dict(schema='shift-reviewed-gen-handoff-v1', wave_index=1,
            failure_kind='downstream_prepare_ImportError_only', science_rerun=False,
            gen_cluster=100, terminal_native_ad=native, files={name: WATCH.sha(stage / name)
                for name in ('gen_inventory.json', 'gen_publication.json')})))
        self.config['reviewed_gen_handoffs'] = [dict(path=str(proof), sha256=WATCH.sha(proof))]
        self.config['dependencies'] = {str(proof): WATCH.sha(proof)}
        receipts = {100: dict(directory=str(stage), receipt='fake', kind='gen_stage', submitted=self.now - 120)}
        with patch.object(WATCH, 'stage_counts', return_value=self.counts), \
                patch.object(WATCH, 'execute_events', return_value=[]):
            report = WATCH.assess(self.config, {}, [], [native], receipts, self.now)
            self.assertEqual(report['health'], 'warning')
            self.assertEqual(report['issues'][0]['code'], 'reviewed_handoff_pending_100')
            production = stage.parent / 'production'
            production.mkdir()
            (production / 'submission.json').write_text(json.dumps(dict(cluster=300)))
            report = WATCH.assess(self.config, {}, [], [native], receipts, self.now)
            self.assertEqual(report['health'], 'ok')
        (stage / 'gen_inventory.json').write_text(json.dumps(dict(complete=False)))
        with self.assertRaisesRegex(ValueError, 'changed or unfinished'):
            WATCH.reviewed_terminal_failures(self.config)

    def test_thirteen_hours_queued_is_critical(self):
        report = self.assess([self.child(age=13 * 3600)])
        self.assertEqual(report['health'], 'critical')
        self.assertTrue(any(item['code'] == 'queue_101' for item in report['issues']))

    def test_stage_budget_recovery_preserves_gen_counts_and_follows_new_handoff(self):
        stage=self.root/'waves/w001/gen_stage';stage.mkdir(parents=True)
        (stage/'submission.json').write_text(json.dumps(dict(cluster=100,utc='1970-01-01T00:00:00+00:00')))
        for name in ('gen_inventory.json','gen_publication.json'):
            (stage/name).write_text(json.dumps(dict(complete=True)))
        descriptor=stage.parent/'material_selected_source.json'
        descriptor.write_text('{"selected_indices":[1,3]}')
        replacement=self.root/'operations/handoffs/w001';replacement.mkdir(parents=True)
        (replacement/'submission.json').write_text(json.dumps(dict(cluster=301,task=str(replacement),
            utc='1970-01-01T00:00:00+00:00')))
        native=dict(ClusterId=100,ProcId=0,Iwd=str(stage),JobStatus=4,ExitBySignal=False,ExitCode=1)
        value=dict(schema='shift-reviewed-gen-handoff-v1',wave_index=1,gen_cluster=100,
            failure_kind='detector_stage_budget_upgrade_before_SIM',science_rerun=False,
            terminal_native_ad=native,files={name:WATCH.sha(stage/name) for name in ('gen_inventory.json','gen_publication.json')},
            absolute_stage_timeouts=[43200,7200,14400,14400],
            preserved_material_descriptors={str(descriptor):WATCH.sha(descriptor)},
            replacement_handoff_directory=str(replacement))
        proof=self.root/'budget_recovery.json'
        def pin():
            proof.write_text(json.dumps(value))
            self.config.update(reviewed_gen_handoffs=[dict(path=str(proof),sha256=WATCH.sha(proof))],
                               dependencies={str(proof):WATCH.sha(proof)})
        pin()
        receipts=WATCH.manager_receipts(self.root,config=self.config)
        self.assertEqual(receipts[100]['kind'],'gen_stage')
        self.assertEqual(receipts[301]['kind'],'handoff')
        self.assertEqual(WATCH.reviewed_terminal_failures(self.config)[100]['recovery_submission'],str(replacement/'submission.json'))
        value['absolute_stage_timeouts'][0]=1800;pin()
        with self.assertRaisesRegex(ValueError,'deadline or handoff authority'):
            WATCH.manager_receipts(self.root,config=self.config)
        value['absolute_stage_timeouts'][0]=43200;pin()
        descriptor.write_text('{"selected_indices":[2]}')
        with self.assertRaisesRegex(ValueError,'preserved material descriptor'):
            WATCH.manager_receipts(self.root,config=self.config)

    def test_detector_recovery_binds_original_payload_directory_and_counts_it_once(self):
        original=self.root/'waves/w001/production';original.mkdir(parents=True)
        for name in ('plan.json','seal.json'):(original/name).write_text('{}')
        (original/'submission.json').write_text(json.dumps(dict(cluster=100,utc='1970-01-01T00:00:00+00:00')))
        replacement=self.root/'operations/detector_w001';replacement.mkdir(parents=True)
        for name in ('pipeline.dag','detector.sub','worker.sh'):(replacement/name).write_text(name+'\n')
        files={str(path):WATCH.sha(path) for path in replacement.iterdir()}
        (replacement/'submission.json').write_text(json.dumps(dict(cluster=301,utc='1970-01-01T00:00:00+00:00')))
        native=dict(ClusterId=100,ProcId=0,Iwd=str(original),JobStatus=4,ExitBySignal=False,ExitCode=1)
        proof=self.root/'detector_recovery.json'
        proof.write_text(json.dumps(dict(schema='shift-reviewed-detector-stage-replacement-v1',wave_index=1,
            failure_kind='malformed_budget_preflight_shell_before_SIM',science_rerun=False,
            detector_payload_started=False,original_directory=str(original),directory=str(replacement),
            detector_cluster=100,terminal_native_ad=native,original_plan_sha256=WATCH.sha(original/'plan.json'),
            original_seal_sha256=WATCH.sha(original/'seal.json'),files=files)))
        self.config.update(reviewed_detector_stage_overrides={'1':dict(directory=str(replacement),
            review=dict(path=str(proof),sha256=WATCH.sha(proof)))},dependencies={str(proof):WATCH.sha(proof)})
        receipts=WATCH.manager_receipts(self.root,config=self.config)
        self.assertEqual(receipts[100]['kind'],'reviewed_orchestration')
        self.assertEqual(receipts[301]['kind'],'production')
        self.assertEqual(receipts[301]['payload_directory'],str(original))
        manager=dict(ClusterId=301,ProcId=0,JobStatus=2,Iwd=str(replacement),JobStartDate=self.now-120)
        child=dict(ClusterId=302,ProcId=0,DAGManJobId=301,JobStatus=2,Iwd=str(original),QDate=self.now-120)
        self.counts.update(completed_events=5,complete_receipts=1,latest_receipt_epoch=self.now)
        with patch.object(WATCH,'stage_counts',return_value=self.counts) as counts, \
                patch.object(WATCH,'execute_events',return_value=['302.0']):
            report=WATCH.assess(self.config,{},[manager,child],[native],receipts,self.now)
            self.assertEqual(report['actual_planned_progress']['detector_completed_events'],5)
            self.assertFalse(any(i['severity']=='critical' for i in report['issues']))
            self.assertFalse(any(i['code'].startswith('wrong_iwd') for i in report['issues']))
            self.assertTrue(all(call.args==(original,) for call in counts.call_args_list))
            for parent in (100,999):
                child['DAGManJobId']=parent
                report=WATCH.assess(self.config,{},[manager,child],[native],receipts,self.now)
                self.assertTrue(any(i['code']=='reviewed_failure_active_302' for i in report['issues']))
            child['DAGManJobId']=301
            child['Iwd']=str(self.root/'unexpected')
            report=WATCH.assess(self.config,{},[manager,child],[native],receipts,self.now)
            self.assertTrue(any(i['code']=='wrong_iwd_302' for i in report['issues']))
        (replacement/'worker.sh').write_text('changed\n')
        with self.assertRaisesRegex(ValueError,'replacement inputs'):
            WATCH.manager_receipts(self.root,config=self.config)

    def test_running_classad_alone_does_not_prove_execution(self):
        report = self.assess([self.child(status=2, starts=1)])
        self.assertFalse(report['stages']['100']['execution_verified'])

    def test_dag_execute_event_proves_child_execution(self):
        report = self.assess([self.child(status=2, starts=1)], ['101.0'])
        self.assertTrue(report['stages']['100']['execution_verified'])

    def test_wrong_worker_scratch_iwd_is_immediately_critical(self):
        report = self.assess([self.child(iwd='/pool/condor/dir_123')])
        self.assertTrue(any(item['code'] == 'wrong_iwd_101' and item['severity'] == 'critical' for item in report['issues']))

    def test_held_child_is_detected_even_with_a_running_manager(self):
        report = self.assess([self.child(status=5)])
        self.assertTrue(any(item['code'] == 'held_101' for item in report['issues']))

    def test_brief_no_child_transition_does_not_inherit_full_manager_age(self):
        self.counts.update(complete_receipts=3, completed_events=60, latest_receipt_epoch=self.now)
        report = self.assess([], manager_age=13 * 3600)
        self.assertFalse(any(item['code'].startswith('no_children') for item in report['issues']))

    def test_no_child_condition_persists_across_polls(self):
        old = self.assess([])
        old['stages']['100']['no_children_since_epoch'] = self.now - 3700
        report = self.assess([], previous=old)
        self.assertTrue(any(item['code'] == 'no_children_100' and item['severity'] == 'critical' for item in report['issues']))

    def test_stale_supervisor_heartbeat_is_reported(self):
        (self.root / 'state.json').write_text(json.dumps(dict(checked_utc='1970-01-01T00:00:00+00:00')))
        report = self.assess([self.child()])
        self.assertTrue(any(item['code'] == 'supervisor_heartbeat' for item in report['issues']))

    def test_completed_supervisor_can_wait_for_recovery_without_an_expected_heartbeat(self):
        (self.root/'state.json').write_text(json.dumps(dict(complete=True,checked_epoch=self.now-10000)))
        receipts=dict(self.receipts)
        receipts[200]=dict(directory=str(self.root),receipt='supervisor',kind='supervisor',submitted=self.now-20000)
        manager=dict(ClusterId=100,ProcId=0,JobStatus=2,Iwd=str(self.directory),JobStartDate=self.now-120)
        ended=dict(ClusterId=200,ProcId=0,JobStatus=4,Iwd=str(self.root),ExitCode=0,ExitBySignal=False)
        with patch.object(WATCH,'stage_counts',return_value=self.counts),patch.object(WATCH,'execute_events',return_value=[]):
            successful=WATCH.assess(self.config,{},[manager,self.child()],[ended],receipts,self.now)
            failed=WATCH.assess(self.config,{},[manager,self.child()],[dict(ended,ExitCode=1)],receipts,self.now)
            live=WATCH.assess(self.config,{},[manager,self.child(),dict(ended,JobStatus=2)],[],receipts,self.now)
        self.assertFalse(any(x['code']=='supervisor_heartbeat' for x in successful['issues']))
        for report in (failed,live):
            self.assertTrue(any(x['code']=='supervisor_heartbeat' for x in report['issues']))

    def test_large_campaign_query_has_constant_depth(self):
        receipts = {cluster: dict(self.receipts[100]) for cluster in range(100, 700)}
        queued = [dict(ClusterId=cluster, ProcId=0) for cluster in receipts]
        with patch.object(WATCH, 'scheduler_options', return_value=({}, [])), \
                patch.object(WATCH, 'query', return_value=queued) as query, \
                patch.object(WATCH, 'run_condor') as history:
            _, _, condition = WATCH.scheduler_snapshot(self.config, receipts, {})
            history.assert_not_called()
        self.assertIn('member(ClusterId,{100,101,', condition)
        self.assertIn('member(DAGManJobId,{100,101,', condition)
        self.assertLess(condition.count(' || '), 5)
        self.assertNotIn('ClusterId ==', condition)

    def test_history_uses_one_exact_match_and_caches_terminal_managers(self):
        receipts = dict(self.receipts)
        receipts[300] = dict(self.receipts[100])
        queued = [dict(ClusterId=100, ProcId=0)]
        ended = [dict(ClusterId=300, ProcId=0, JobStatus=4, ExitCode=0)]
        with patch.object(WATCH, 'scheduler_options', return_value=({}, [])), \
                patch.object(WATCH, 'query', return_value=queued), \
                patch.object(WATCH, 'run_condor', return_value=json.dumps(ended)) as command:
            rows, history, _ = WATCH.scheduler_snapshot(self.config, receipts, {})
            argv = command.call_args.args[0]
            self.assertIn('300.0', argv)
            self.assertEqual(argv[argv.index('-limit') + 1], '1')
            self.assertEqual(history, ended)
            command.reset_mock()
            WATCH.scheduler_snapshot(self.config, receipts, dict(terminal_history=history))
            command.assert_not_called()

    def test_migrated_manager_ids_are_not_child_execution_evidence(self):
        previous = dict(executed_jobs=[100, 300], stages={
            '100': dict(observed_execute_events=['105.0']),
        })
        report = self.assess([self.child(status=2)], events=['101.0'], previous=previous)
        self.assertEqual(report['executed_jobs'], ['101.0', '105.0'])
        self.assertTrue(report['stages']['100']['execution_verified'])
        self.assertEqual(WATCH.cached_execution_jobs(dict(executed_jobs=[100])), set())
        for invalid in (100, '100', '100.0.extra', True):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, 'cluster.proc'):
                WATCH.cached_execution_jobs(dict(stages={
                    '100': dict(observed_execute_events=[invalid]),
                }))

    def test_unknown_poll_preserves_success_and_failure_cache_without_health_credit(self):
        ended = [dict(ClusterId=cluster, ProcId=0, JobStatus=4, ExitCode=exit_code)
                 for cluster, exit_code in ((300, 0), (400, 1))]
        previous = dict(scheduler_state='verified', terminal_history=ended,
                        stages={'100': dict(observed_execute_events=['105.0'])})
        unknown = WATCH.unknown_report(previous, RuntimeError('native history timeout'))
        self.assertEqual(unknown['health'], 'unknown')
        self.assertEqual(unknown['scheduler_state'], 'unknown')
        self.assertNotIn('controller_finished', unknown)
        self.assertEqual(unknown['terminal_history'], ended)
        self.assertEqual(WATCH.cached_execution_jobs(unknown), {'105.0'})
        receipts = dict(self.receipts)
        receipts.update({cluster: dict(self.receipts[100]) for cluster in (300, 400)})
        with patch.object(WATCH, 'scheduler_options', return_value=({}, [])), \
                patch.object(WATCH, 'query', return_value=[dict(ClusterId=100, ProcId=0)]), \
                patch.object(WATCH, 'run_condor') as command:
            _, history, _ = WATCH.scheduler_snapshot(self.config, receipts, unknown)
            self.assertEqual(history, ended)
            command.assert_not_called()
        # A fresh live record wins over immutable history, even after a timeout.
        with patch.object(WATCH, 'scheduler_options', return_value=({}, [])), \
                patch.object(WATCH, 'query', return_value=[dict(ClusterId=cluster, ProcId=0)
                                                         for cluster in (100, 300)]), \
                patch.object(WATCH, 'run_condor') as command:
            _, history, _ = WATCH.scheduler_snapshot(self.config, receipts, unknown)
            self.assertEqual(history, [ended[1]])
            command.assert_not_called()

    def test_old_unknown_health_restores_only_last_verified_cache(self):
        previous = dict(health='unknown', scheduler_state='unknown', issues=[dict(code='timeout')])
        verified = dict(health='ok', scheduler_state='verified', controller_finished=True,
                        terminal_history=[dict(ClusterId=300, ProcId=0, JobStatus=4, ExitCode=0)],
                        stages={'100': dict(observed_execute_events=['105.0'])})
        (self.root/'health.json').write_text(json.dumps(previous))
        (self.root/'last_verified_health.json').write_text(json.dumps(verified))
        restored = WATCH.polling_state(self.root)
        self.assertEqual(restored['health'], 'unknown')
        self.assertEqual(restored['issues'], previous['issues'])
        self.assertNotIn('controller_finished', restored)
        self.assertEqual(restored['terminal_history'], verified['terminal_history'])
        self.assertEqual(WATCH.cached_execution_jobs(restored), {'105.0'})
        # Never substitute an unverified report for a verified cache.
        verified['scheduler_state'] = 'unknown'
        (self.root/'last_verified_health.json').write_text(json.dumps(verified))
        self.assertEqual(WATCH.polling_state(self.root), previous)

    def test_recorded_unknown_cache_survives_repeated_scheduler_errors(self):
        self.config['state_directory'] = str(self.root)
        verified = dict(health='ok', scheduler_state='verified', checked_utc='2026-10-21T12:00:00Z',
                        issues=[], terminal_history=[dict(ClusterId=300, ProcId=0, JobStatus=4, ExitCode=0)],
                        stages={'100': dict(observed_execute_events=['105.0'])})
        WATCH.record(self.config, {}, verified, dict(queue=[], history=verified['terminal_history']))
        for attempt in range(2):
            previous = WATCH.polling_state(self.root)
            unknown = WATCH.unknown_report(previous, RuntimeError('native history timeout'))
            WATCH.record(self.config, previous, unknown, dict(error='native history timeout'))
            self.assertEqual(WATCH.polling_state(self.root)['terminal_history'], verified['terminal_history'])
        self.assertEqual(WATCH.read(self.root/'last_verified_health.json'), verified)
        self.assertEqual(WATCH.read(self.root/'failure_capture.json')['health']['health'], 'unknown')

    def test_rollover_reads_the_new_supervisor_receipt_without_rewriting_the_old_one(self):
        old = self.root / 'submission.json'
        old.write_text(json.dumps(dict(cluster=200, utc='1970-01-01T00:00:00+00:00')))
        new = self.root / 'controller_v2/submission.json'
        new.parent.mkdir()
        new.write_text(json.dumps(dict(cluster=300, utc='1970-01-01T00:00:00+00:00')))
        receipts = WATCH.manager_receipts(self.root, new)
        self.assertEqual(set(receipts), {300})
        self.assertEqual(receipts[300]['kind'], 'supervisor')
        self.assertEqual(receipts[300]['directory'], str(new.parent))
        self.assertEqual(WATCH.read(old)['cluster'], 200)

    def test_external_canary_failure_is_visible_without_crediting_bulk_progress(self):
        external = self.root / 'external_detector'
        external.mkdir()
        (external/'submission.json').write_text(json.dumps(dict(cluster=100, utc='1970-01-01T00:00:00+00:00')))
        self.receipts = WATCH.manager_receipts(self.root, external_productions=[str(external)])
        self.directory = external
        self.counts.update(completed_events=20, complete_receipts=1)
        report = self.assess([self.child(status=5)])
        self.assertEqual(report['health'], 'critical')
        self.assertTrue(report['stages']['100']['external_prerequisite'])
        self.assertEqual(report['actual_planned_progress']['detector_completed_events'], 0)
        self.assertEqual(report['observed_state'], 'awaiting_payload_execution')
        with self.assertRaisesRegex(ValueError, 'persistent production receipt'):
            WATCH.manager_receipts(self.root, external_productions=['relative/path'])

    def test_only_the_exact_held_or_terminal_retired_supervisor_is_ignored(self):
        self.config['retired_supervisors'] = {'200': str(self.root)}
        for status, iwd, expected in ((5, str(self.root), 'ok'), (3, str(self.root), 'ok'),
                                     (4, str(self.root), 'ok'), (2, str(self.root), 'critical'),
                                     (1, str(self.root), 'critical'), (5, '/pool/condor/wrong', 'critical')):
            with self.subTest(status=status, iwd=iwd):
                row = dict(ClusterId=200, ProcId=0, JobStatus=status, Iwd=iwd)
                report = self.assess([self.child(), row])
                self.assertEqual(report['health'], expected)
                if expected == 'critical':
                    self.assertTrue(any(item['code'] == 'retired_supervisor_200' for item in report['issues']))

    def test_meson_batch_receipts_distinguish_source_worker_and_detector_manager(self):
        source = self.root / 'batches/b000'
        detector = source / 'production'
        detector.mkdir(parents=True)
        handoff=source/'handoff'
        handoff.mkdir()
        for path, cluster in ((source, 101), (detector, 102), (handoff,103)):
            (path/'submission.json').write_text(json.dumps(dict(cluster=cluster, utc='1970-01-01T00:00:00+00:00')))
        receipts = WATCH.manager_receipts(self.root)
        self.assertEqual(receipts[101]['kind'], 'meson_source')
        self.assertEqual(receipts[102]['kind'], 'production')
        self.assertEqual(receipts[103]['kind'],'handoff')
        (source/'source_status.json').write_text(json.dumps(dict(source_collisions=10000, complete=True, events=23)))
        counts = WATCH.stage_counts(source)
        self.assertEqual(counts['completed_events'], 23)
        self.assertEqual(counts['planned_source_collisions'], 10000)
        self.assertEqual(counts['complete_receipts'], 1)

    def test_meson_watch_keeps_source_M_separate_from_actual_signal_N(self):
        worklist = Path(self.config['worklist'])
        worklist.write_text(json.dumps(dict(schema='shift-dp-meson-production-v1', batches=[dict(
            batch='b000', point='m0p3_prompt', source_collisions=10000, expected_signal_events=25)])))
        (self.root/'state.json').write_text(json.dumps(dict(checked_utc='1970-01-03T07:33:20+00:00',
            complete=True, batches={'b000':{'published':{'events':23}}})))
        self.receipts[100]['kind'] = 'meson_source'
        self.counts.update(completed_events=23, complete_receipts=1,
                           planned_source_collisions=10000, source_heartbeat_epoch=self.now)
        report = self.assess([])
        self.assertTrue(report['controller_finished'])
        self.assertEqual(report['actual_planned_progress']['gen_completed_events'], 23)
        self.assertEqual(report['actual_planned_progress']['authorized_source_collisions'], 10000)
        self.assertEqual(report['actual_planned_progress']['expected_signal_events'], 25)
        self.assertIsNone(report['actual_planned_progress']['authorized_new_events'])
        self.assertFalse(any(item['code'].startswith('no_children') for item in report['issues']))

    def test_fresh_source_heartbeat_does_not_hide_stalled_physics(self):
        self.receipts[100]['kind'] = 'meson_source'
        self.counts.update(started_receipts=1, source_ledger_bytes=1000,
                           source_heartbeat_epoch=self.now - 30, source_phase='GEN')
        previous = self.assess([])
        previous['stages']['100']['last_progress_epoch'] = self.now - 15000
        self.counts['source_heartbeat_epoch'] = self.now
        report = self.assess([], previous=previous)
        self.assertTrue(any(item['code'] == 'stale_source_progress_100' for item in report['issues']))
        self.assertFalse(any(item['code'] == 'source_heartbeat_100' for item in report['issues']))

    def test_missing_or_mismatched_explicit_supervisor_receipt_fails_closed(self):
        receipt = self.root / 'controller_v2/submission.json'
        self.config['supervisor_receipt'] = str(receipt)
        with self.assertRaisesRegex(ValueError, 'receipt is missing'):
            WATCH.poll(self.config, {})
        receipt.parent.mkdir()
        receipt.write_text(json.dumps(dict(cluster=300, utc='1970-01-01T00:00:00+00:00')))
        with patch.object(WATCH, 'scheduler_snapshot') as query:
            with self.assertRaisesRegex(ValueError, 'configured native cluster'):
                WATCH.poll(self.config, {})
            query.assert_not_called()


class PreSubmissionMonitor(unittest.TestCase):
    def test_operational_receipt_keeps_the_pinned_science_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            science = root / 'science'
            science.mkdir()
            deployment = root / 'operations'
            deployment.mkdir()
            submit = deployment / 'controller.sub'
            submit.write_text('initialdir = ' + str(deployment) + '\nqueue\n')
            authority = deployment / 'config.json'
            authority.write_text(json.dumps(dict(directory=str(science))))
            receipt = deployment / 'submission.json'
            receipt.write_text(json.dumps(dict(cluster=123, task=str(deployment),
                submit_sha256=WATCH.sha(submit), utc='2026-10-19T00:00:00+00:00')))
            config = dict(resolve_supervisor_from_receipt=True,
                production_directory=str(science), supervisor_deployment_directory=str(deployment),
                supervisor_receipt=str(receipt), dependencies={str(authority):WATCH.sha(authority)})
            with patch.object(WATCH, 'scheduler_snapshot', return_value=([], [], 'exact')) as native, \
                 patch.object(WATCH, 'assess', return_value={}):
                WATCH.poll(config, {})
                self.assertEqual(native.call_args.args[0]['production_directory'], str(science))
                config['dependencies'] = {}
                with self.assertRaisesRegex(ValueError, 'production authority'):
                    WATCH.poll(config, {})
                config['dependencies'] = {str(authority):WATCH.sha(authority)}
                authority.write_text(json.dumps(dict(directory=str(root / 'other'))))
                config['dependencies'][str(authority)] = WATCH.sha(authority)
                with self.assertRaisesRegex(ValueError, 'production authority'):
                    WATCH.poll(config, {})
                self.assertEqual(native.call_count, 1)

    def test_receipt_resolves_only_the_frozen_controller_launch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            submit = root / 'controller.sub'
            submit.write_text('initialdir = ' + str(root) + '\nqueue\n')
            receipt = root / 'submission.json'
            receipt.write_text(json.dumps(dict(cluster=123, task=str(root), submit_sha256=WATCH.sha(submit), utc='2026-10-17T00:00:00+00:00')))
            config = dict(resolve_supervisor_from_receipt=True, production_directory=str(root), supervisor_receipt=str(receipt))
            with patch.object(WATCH, 'scheduler_snapshot', return_value=([], [], 'exact')) as native, \
                 patch.object(WATCH, 'assess', return_value={}):
                WATCH.poll(config, {})
                self.assertEqual(native.call_args.args[0]['initial_supervisor_cluster'], 123)
                self.assertNotIn('initial_supervisor_cluster', config)
                submit.write_text('changed launch')
                with self.assertRaisesRegex(ValueError, 'exact launch identity'):
                    WATCH.poll(config, {})
                self.assertEqual(native.call_count, 1)

    def unsubmitted_runtime_fixture(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        helpers = self.root / 'validator'
        helpers.mkdir()
        helper = helpers / 'run_dark_photon_meson_production.py'
        helper.write_text('import json\nfrom pathlib import Path\n'
            'def validate_unsubmitted_runtime_recovery(production, batch, pin):\n'
            '    proof = json.loads(Path(pin["path"]).read_text())\n'
            '    assert str(production) == proof["original_directory"]\n'
            '    assert batch == {"batch": "bulk_0135", "point": "m0p9_lab_30m"}\n'
            '    return proof\n')
        original = self.root / 'batches/bulk_0135/production'
        original.mkdir(parents=True)
        replacement = self.root / 'recovery/bulk_0135'
        replacement.mkdir(parents=True)
        submit = replacement / 'pipeline.dag.condor.sub'
        submit.write_text('initialdir = ' + str(replacement) + '\nqueue\n')
        receipt = replacement / 'submission.json'
        receipt.write_text(json.dumps(dict(cluster=300, task=str(replacement),
            submit_sha256=WATCH.sha(submit), utc='2026-10-24T20:00:00+00:00')))
        proof = self.root / 'review.json'
        proof.write_text(json.dumps(dict(point='m0p9_lab_30m',
            original_directory=str(original), recovery_directory=str(replacement))))
        config = dict(production_directory=str(self.root), reviewed_unsubmitted_runtime_recoveries={
            'bulk_0135':dict(path=str(proof),sha256=WATCH.sha(proof))},
            unsubmitted_runtime_validator=dict(path=str(helper),sha256=WATCH.sha(helper)),
            dependencies={str(helper):WATCH.sha(helper),str(proof):WATCH.sha(proof)})
        return config, original, replacement, helper, receipt

    def test_unsubmitted_runtime_manager_keeps_original_payload_directory(self):
        config, original, replacement, helper, receipt = self.unsubmitted_runtime_fixture()
        records = WATCH.unsubmitted_runtime_managers(config)
        self.assertEqual(set(records), {300})
        self.assertEqual(records[300]['directory'], str(replacement))
        self.assertEqual(records[300]['payload_directory'], str(original))
        self.assertEqual(records[300]['kind'], 'production')
        receipt.unlink()
        self.assertEqual(WATCH.unsubmitted_runtime_managers(config), {})

    def test_unsubmitted_runtime_manager_rejects_changed_helper_inventory(self):
        config, original, replacement, helper, receipt = self.unsubmitted_runtime_fixture()
        extra = helper.with_name('unreviewed.py')
        extra.write_text('pass\n')
        with self.assertRaisesRegex(ValueError, 'helper is not pinned'):
            WATCH.unsubmitted_runtime_managers(config)
        extra.unlink()
        helper.write_text(helper.read_text() + '# changed\n')
        with self.assertRaisesRegex(ValueError, 'validator is outside'):
            WATCH.unsubmitted_runtime_managers(config)

    def test_unsubmitted_runtime_manager_rejects_wrong_native_receipt_scope(self):
        config, original, replacement, helper, receipt = self.unsubmitted_runtime_fixture()
        saved = json.loads(receipt.read_text())
        saved['task'] = str(original)
        receipt.write_text(json.dumps(saved))
        with self.assertRaisesRegex(ValueError, 'replacement receipt changed'):
            WATCH.unsubmitted_runtime_managers(config)


if __name__ == '__main__':
    unittest.main()
