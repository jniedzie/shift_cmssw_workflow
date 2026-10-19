# Code style: AI-code. AI-generated scheduler guard scope and race tests.
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import guard_dark_photon_idle_hosts as guard


class IdleHostGuard(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='shift-idle-host-guard-')
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.namespace = '/afs/project/validation/bsm_campaign/waves'
        self.hosts = ['bad1.cern.ch', 'bad2.cern.ch']
        self.config = dict(schema='shift-dp-idle-host-guard-v1', owner='jniedzie',
                           directory=str(self.directory), namespaces=[self.namespace],
                           excluded_hosts=self.hosts, condor_pool='pool.cern.ch',
                           interval_seconds=60, runtime_seconds=604800,
                           maximum_receipt_bytes=1024*1024)
        self.sleep = patch.object(guard.time, 'sleep').start()
        self.addCleanup(patch.stopall)
        self.ad = dict(ClusterId=100, ProcId=0, Owner='jniedzie', JobStatus=1,
                       Iwd=self.namespace+'/w001/production', DAGManJobId=50,
                       Requirements='/Expr(TARGET.Memory >= 4000)/',
                       _queried_schedd='schedd.cern.ch')
        self.ad['Cmd'] = self.ad['Iwd']+'/detector_worker.sh'

    def test_scope_requires_owner_idle_dag_and_exact_executable(self):
        self.assertTrue(guard.eligible(self.config, self.ad))
        for key, value in [('Owner', 'other'), ('JobStatus', 2),
                           ('DAGManJobId', None), ('Cmd', '/tmp/detector_worker.sh'),
                           ('Iwd', self.namespace+'-other/w001/production')]:
            ad = dict(self.ad, **{key:value})
            self.assertFalse(guard.eligible(self.config, ad), key)

    def test_preserve_existing_requirements(self):
        value = guard.requirements(self.ad, self.hosts)
        self.assertIn('(TARGET.Memory >= 4000)', value)
        for host in self.hosts:
            self.assertIn('TARGET.Machine =!= '+json.dumps(host), value)

    def test_native_isnt_and_symbolic_spelling_are_idempotent(self):
        for spelling in ['isnt', '=!=']:
            expression = ' && '.join('TARGET.Machine '+spelling+' '+json.dumps(host)
                                     for host in self.hosts)
            self.assertIsNone(guard.requirements(dict(self.ad, Requirements='/Expr('+expression+')/'), self.hosts))

    def test_hostname_elsewhere_is_not_protection(self):
        ad = dict(self.ad, Requirements='/Expr(TARGET.Label == "bad1.cern.ch")/')
        self.assertIn('TARGET.Machine =!= "bad1.cern.ch"', guard.requirements(ad, self.hosts))

    def test_refuse_unparsed_requirements(self):
        with self.assertRaises(ValueError):
            guard.requirements(dict(self.ad, Requirements='true'), self.hosts)

    def test_all_native_mutations_are_atomic_idle_requirements_only(self):
        updated = guard.requirements(self.ad, self.hosts)
        after = dict(self.ad, Requirements='/Expr('+updated+')/')
        with patch.object(guard.native, 'query_account', return_value=[self.ad]), \
             patch.object(guard.native, 'run_condor', return_value='Set attribute for 1 matching jobs.') as run, \
             patch.object(guard.native, 'query', return_value=[after]):
            result = guard.step(self.config, 1)
        command = run.call_args.args[0]
        self.assertEqual(command[0], '/usr/bin/condor_qedit')
        self.assertEqual(command[-2], 'Requirements')
        self.assertIn('Owner == "jniedzie" && JobStatus == 1', command[-3])
        self.assertIn('ClusterId == 100 && ProcId == 0', command[-3])
        self.assertIn('unparse(Requirements) == "TARGET.Memory >= 4000"', command[-3])
        self.assertIn('Iwd == '+json.dumps(self.ad['Iwd']), command[-3])
        self.assertIn('Cmd == '+json.dumps(self.ad['Cmd']), command[-3])
        receipt = json.loads(Path(result['receipt']).read_text())
        self.assertTrue(receipt['complete'])

    def test_race_to_running_is_not_an_error_or_retry(self):
        after = dict(self.ad, JobStatus=2)
        with patch.object(guard.native, 'query_account', return_value=[self.ad]), \
             patch.object(guard.native, 'run_condor', return_value='0 matching jobs.') as run, \
             patch.object(guard.native, 'query', return_value=[after]):
            result = guard.step(self.config, 1)
        self.assertEqual(run.call_count, 1)
        self.assertTrue(json.loads(Path(result['receipt']).read_text())['complete'])

    def test_failed_native_readback_retains_intent_and_stops(self):
        with patch.object(guard.native, 'query_account', return_value=[self.ad]), \
             patch.object(guard.native, 'run_condor', return_value='Set.'), \
             patch.object(guard.native, 'query', return_value=[self.ad]):
            with self.assertRaises(ValueError):
                guard.step(self.config, 1)
        self.assertFalse(json.loads((self.directory/'receipts/poll_000001.json').read_text())['complete'])

    def test_readback_lag_is_rechecked_without_repeating_mutation(self):
        after = dict(self.ad, Requirements='/Expr('+guard.requirements(self.ad, self.hosts)+')/')
        with patch.object(guard.native, 'query_account', return_value=[self.ad]), \
             patch.object(guard.native, 'run_condor', return_value='1 matching jobs.') as run, \
             patch.object(guard.native, 'query', side_effect=[[self.ad], [after]]) as query:
            result = guard.step(self.config, 1)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(query.call_count, 2)
        self.assertEqual(result['pending_protection'], {})

    def test_short_compare_and_set_keeps_unprotected_identity_pending(self):
        with patch.object(guard.native, 'query_account', return_value=[self.ad]), \
             patch.object(guard.native, 'run_condor', return_value='0 matching jobs.') as run, \
             patch.object(guard.native, 'query', return_value=[self.ad]):
            result = guard.step(self.config, 1)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(result['protected_idle_jobs'], 0)
        self.assertEqual(result['pending_protection']['100.0']['unprotected_polls'], 1)
        self.assertFalse(result['pending_protection']['100.0']['host_exclusions_present'])

    def test_persistent_static_unprotected_identity_stops_after_bounded_polls(self):
        previous = {'pending_protection': {'100.0': {'unprotected_polls': 2}}}
        with patch.object(guard.native, 'query_account', return_value=[self.ad]), \
             patch.object(guard.native, 'run_condor', return_value='0 matching jobs.'), \
             patch.object(guard.native, 'query', return_value=[self.ad]):
            with self.assertRaisesRegex(ValueError, 'unprotected idle job'):
                guard.step(self.config, 1, previous)

    def test_proven_new_idle_period_is_pending_without_claiming_protection(self):
        before = dict(self.ad, NumJobStarts=0, EnteredCurrentStatus=10)
        after = dict(before, NumJobStarts=1, EnteredCurrentStatus=20)
        with patch.object(guard.native, 'query_account', return_value=[before]), \
             patch.object(guard.native, 'run_condor', return_value='1 matching jobs.'), \
             patch.object(guard.native, 'query', return_value=[after]):
            result = guard.step(self.config, 1)
        self.assertTrue(result['pending_protection']['100.0']['new_idle_period_observed'])

    def test_readback_identity_change_is_fatal_even_after_short_edit(self):
        for name, value in [('Owner', 'other'), ('Iwd', '/other'),
                            ('Cmd', '/other/worker'), ('DAGManJobId', 70)]:
            with self.subTest(name=name), \
                 patch.object(guard.native, 'query_account', return_value=[self.ad]), \
                 patch.object(guard.native, 'run_condor', return_value='0 matching jobs.'), \
                 patch.object(guard.native, 'query', return_value=[dict(self.ad, **{name:value})]):
                with self.assertRaisesRegex(ValueError, 'identity changed'):
                    guard.step(self.config, 1)
            (self.directory/'receipts/poll_000001.json').unlink()

    def test_missing_terminal_job_requires_no_edit_retry(self):
        with patch.object(guard.native, 'query_account', return_value=[self.ad]), \
             patch.object(guard.native, 'run_condor', return_value='0 matching jobs.') as run, \
             patch.object(guard.native, 'query', return_value=[]):
            result = guard.step(self.config, 1)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(result['pending_protection'], {})

    def test_mutation_intent_exists_before_native_edit_and_query_failure(self):
        def fail(command, **kwargs):
            receipt = json.loads((self.directory/'receipts/poll_000001.json').read_text())
            self.assertEqual(len(receipt['actions']), 1)
            self.assertTrue(receipt['actions'][0]['intent_utc'])
            self.assertIsNone(receipt['actions'][0]['output'])
            raise guard.native.NativeCondorError('unknown edit outcome')
        with patch.object(guard.native, 'query_account', return_value=[self.ad]), \
             patch.object(guard.native, 'run_condor', side_effect=fail) as run:
            with self.assertRaises(guard.native.NativeCondorError):
                guard.step(self.config, 1)
        self.assertEqual(run.call_count, 1)

    def test_concurrent_stronger_requirements_are_used_on_fresh_next_poll(self):
        stronger = dict(self.ad, Requirements='/Expr(TARGET.Memory >= 8000)/')
        with patch.object(guard.native, 'query_account', return_value=[self.ad]), \
             patch.object(guard.native, 'run_condor', return_value='0 matching jobs.'), \
             patch.object(guard.native, 'query', return_value=[stronger]):
            previous = guard.step(self.config, 1)
        protected = dict(stronger, Requirements='/Expr('+guard.requirements(stronger, self.hosts)+')/')
        with patch.object(guard.native, 'query_account', return_value=[stronger]), \
             patch.object(guard.native, 'run_condor', return_value='1 matching jobs.') as run, \
             patch.object(guard.native, 'query', return_value=[protected]):
            result = guard.step(self.config, 2, previous)
        self.assertIn('unparse(Requirements) == "TARGET.Memory >= 8000"', run.call_args.args[0][-3])
        self.assertIn('TARGET.Memory >= 8000', run.call_args.args[0][-1])
        self.assertEqual(result['pending_protection'], {})

    def test_unknown_scheduler_state_performs_no_edits(self):
        with patch.object(guard.native, 'query_account', side_effect=RuntimeError('unknown')), \
             patch.object(guard.native, 'run_condor') as run:
            with self.assertRaises(RuntimeError):
                guard.step(self.config, 1)
        run.assert_not_called()

    def test_protected_and_unrelated_jobs_perform_no_edits(self):
        protected = dict(self.ad, Requirements='/Expr('+guard.requirements(self.ad, self.hosts)+')/')
        foreign = dict(self.ad, Owner='other')
        running = dict(self.ad, JobStatus=2)
        with patch.object(guard.native, 'query_account', return_value=[protected,foreign,running]), \
             patch.object(guard.native, 'run_condor') as run:
            result = guard.step(self.config, 1)
        self.assertEqual(result['edits_planned'], 0)
        run.assert_not_called()

    def test_evidence_budget_blocks_before_mutation(self):
        directory = self.directory/'receipts'
        directory.mkdir()
        (directory/'old.json').write_text('x'*self.config['maximum_receipt_bytes'])
        with patch.object(guard.native, 'query_account', return_value=[self.ad]), \
             patch.object(guard.native, 'run_condor') as run:
            with self.assertRaises(ValueError):
                guard.step(self.config, 1)
        run.assert_not_called()

    def test_configuration_rejects_broad_or_unbounded_scope(self):
        config = copy.deepcopy(self.config)
        config['directory'] = '/afs/project/persistent_guard'
        guard.validate_config(config)
        for key,value in [('runtime_seconds',604801), ('namespaces',['/afs/project']),
                          ('excluded_hosts',['bad host']), ('directory','/tmp/guard')]:
            with self.assertRaises(ValueError):
                guard.validate_config(dict(config, **{key:value}))

    def manager(self, remove='False'):
        directory=self.directory/'validation/bsm_campaign/waves/w001/production'
        directory.mkdir(parents=True)
        self.config['namespaces']=[str(directory.parent.parent)]
        self.config['manager_idle_ceiling']=20
        (directory/'submission.json').write_text(json.dumps(dict(cluster=200,task=str(directory))))
        (directory/'pipeline.dag.dagman.out').write_text(
            '$CondorVersion: 25.0.14 $\nDAGMAN_REMOVE_JOBS_AFTER_LIMIT_CHANGE setting: '+remove+'\n')
        return dict(ClusterId=200,ProcId=0,Owner='jniedzie',JobStatus=2,JobUniverse=7,
                    Iwd=str(directory),Cmd='/usr/bin/condor_dagman',DAGMan_MaxIdle=302,
                    DAGMan_MaxJobs=300,_queried_schedd='schedd.cern.ch')

    def test_manager_requires_exact_receipt_and_real_executable(self):
        ad=self.manager()
        self.assertIsNotNone(guard.manager_authority(self.config,ad))
        self.assertIsNone(guard.manager_authority(self.config,dict(ad,Cmd='/tmp/condor_dagman')))
        self.assertIsNone(guard.manager_authority(self.config,dict(ad,JobStatus=1)))
        with self.assertRaises(ValueError):
            guard.manager_authority(self.config,dict(ad,ClusterId=201))

    def test_manager_rejects_payload_removal_configuration(self):
        ad=self.manager(remove='True')
        with patch.object(guard.native,'run_condor') as run:
            with self.assertRaises(ValueError):
                guard.throttle_managers(self.config,[ad],1)
        run.assert_not_called()

    def test_manager_edit_only_changes_idle_ceiling_and_keeps_maxjobs(self):
        ad=self.manager()
        after=dict(ad,DAGMan_MaxIdle=20)
        with patch.object(guard.native,'run_condor',return_value='Set.') as run, \
             patch.object(guard.native,'query',return_value=[after]):
            paths=guard.throttle_managers(self.config,[ad],1)
        command=run.call_args.args[0]
        self.assertEqual(command[-2:],['DAGMan_MaxIdle','20'])
        self.assertIn('JobStatus == 2 && JobUniverse == 7',command[-3])
        self.assertIn('Iwd == '+json.dumps(ad['Iwd']),command[-3])
        self.assertTrue(json.loads(Path(paths[0]).read_text())['complete'])

    def test_manager_stricter_idle_ceiling_stays_unchanged(self):
        ad=dict(self.manager(),DAGMan_MaxIdle=5)
        with patch.object(guard.native,'run_condor') as run:
            self.assertEqual(guard.throttle_managers(self.config,[ad],1),[])
        run.assert_not_called()

    def test_manager_changed_maxjobs_readback_is_rejected(self):
        ad=self.manager()
        after=dict(ad,DAGMan_MaxIdle=20,DAGMan_MaxJobs=1)
        with patch.object(guard.native,'run_condor',return_value='Set.'), \
             patch.object(guard.native,'query',return_value=[after]):
            with self.assertRaises(ValueError):
                guard.throttle_managers(self.config,[ad],1)

    def test_namespace_limits_tighten_both_attributes_with_exact_intents(self):
        ad = self.manager()
        self.config['manager_idle_ceiling'] = 5
        self.config['manager_job_ceilings'] = {self.config['namespaces'][0]: 127}
        first = dict(ad, DAGMan_MaxJobs=127)
        second = dict(first, DAGMan_MaxIdle=5)
        with patch.object(guard.native, 'run_condor', return_value='Set.') as run, \
             patch.object(guard.native, 'query', side_effect=[[first], [second]]):
            paths = guard.throttle_managers(self.config, [ad], 1)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual([command[-2:] for command in commands],
                         [['DAGMan_MaxJobs', '127'], ['DAGMan_MaxIdle', '5']])
        self.assertIn('DAGMan_MaxJobs == 300', commands[0][-3])
        self.assertIn('DAGMan_MaxIdle == 302', commands[1][-3])
        receipt = json.loads(Path(paths[0]).read_text())
        self.assertTrue(receipt['complete'])
        self.assertEqual([action['ceiling'] for action in receipt['actions']], [127, 5])
        self.assertTrue(all(action.get('intent_utc') for action in receipt['actions']))

    def test_distinct_namespaces_get_their_declared_caps(self):
        root = '/afs/project/validation/bsm'
        config = dict(manager_idle_ceiling=5, manager_job_ceilings={
            root+'/main': 127, root+'/meson': 12, root+'/supplement': 27})
        for namespace, ceiling in config['manager_job_ceilings'].items():
            limits = guard.manager_limits(config, dict(
                Iwd=namespace+'/w001/production', DAGMan_MaxJobs=302, DAGMan_MaxIdle=20))
            self.assertEqual(limits, dict(DAGMan_MaxJobs=ceiling, DAGMan_MaxIdle=5))
        self.assertEqual(guard.manager_limits(config, dict(
            Iwd=root+'/main-other/w001', DAGMan_MaxJobs=302, DAGMan_MaxIdle=20)),
            dict(DAGMan_MaxIdle=5))

    def test_stricter_live_node_and_ready_limits_are_preserved(self):
        ad = dict(self.manager(), DAGMan_MaxJobs=10, DAGMan_MaxIdle=2)
        self.config['manager_idle_ceiling'] = 5
        self.config['manager_job_ceilings'] = {self.config['namespaces'][0]: 127}
        with patch.object(guard.native, 'run_condor') as run:
            self.assertEqual(guard.throttle_managers(self.config, [ad], 1), [])
        run.assert_not_called()

    def test_unlimited_zero_is_replaced_but_stricter_other_limit_is_kept(self):
        ad = dict(self.manager(), DAGMan_MaxJobs=0, DAGMan_MaxIdle=2)
        self.config['manager_idle_ceiling'] = 5
        self.config['manager_job_ceilings'] = {self.config['namespaces'][0]: 12}
        with patch.object(guard.native, 'run_condor', return_value='Set.') as run, \
             patch.object(guard.native, 'query', return_value=[dict(ad, DAGMan_MaxJobs=12)]):
            guard.throttle_managers(self.config, [ad], 1)
        self.assertEqual(run.call_args.args[0][-2:], ['DAGMan_MaxJobs', '12'])
        self.assertEqual(run.call_count, 1)

    def test_manager_node_change_stops_if_readback_does_not_match(self):
        ad = self.manager()
        self.config['manager_job_ceilings'] = {self.config['namespaces'][0]: 127}
        with patch.object(guard.native, 'run_condor', return_value='0 matching jobs.'), \
             patch.object(guard.native, 'query', return_value=[dict(ad, DAGMan_MaxJobs=100)]):
            with self.assertRaises(ValueError):
                guard.throttle_managers(self.config, [ad], 1)
        receipt = json.loads((self.directory/'receipts/manager_poll_000001.json').read_text())
        self.assertFalse(receipt['complete'])
        self.assertEqual(len(receipt['actions']), 1)

    def test_manager_throttle_refuses_unexpected_process_or_unsafe_startup(self):
        ad = self.manager(remove='True')
        self.assertIsNone(guard.manager_authority(self.config, dict(ad, ProcId=1)))
        self.config['manager_job_ceilings'] = {self.config['namespaces'][0]: 127}
        with patch.object(guard.native, 'run_condor') as run:
            with self.assertRaises(ValueError):
                guard.throttle_managers(self.config, [ad], 1)
        run.assert_not_called()

    def test_configuration_validates_cap_namespaces_and_numeric_bounds(self):
        config = dict(self.config, directory='/afs/project/persistent_guard',
                      manager_idle_ceiling=5, manager_job_ceilings={self.namespace:127})
        guard.validate_config(config)
        for caps in [{self.namespace+'-other':127}, {self.namespace:0},
                     {self.namespace:True}, {self.namespace:10001}, []]:
            with self.assertRaises(ValueError):
                guard.validate_config(dict(config, manager_job_ceilings=caps))
        for idle in [0, 21, True, '5']:
            with self.assertRaises(ValueError):
                guard.validate_config(dict(config, manager_idle_ceiling=idle))
        overlap = dict(config, namespaces=[self.namespace,self.namespace+'/w001'],
                       manager_job_ceilings={self.namespace:127,self.namespace+'/w001':12})
        with self.assertRaises(ValueError):
            guard.validate_config(overlap)

    def test_transient_native_uncertainty_waits_without_further_actions(self):
        previous=dict(manager_observations=[dict(cluster=200,max_idle=20)])
        with patch.object(guard,'step',side_effect=guard.native.NativeCondorError('unknown')) as step:
            result=guard.poll(self.config,1,previous)
        self.assertEqual(step.call_count,1)
        self.assertEqual(result['status'],'waiting_native_scheduler')
        self.assertIsNone(result['error'])
        self.assertEqual(result['consecutive_native_failures'],1)
        self.assertEqual(result['manager_observations'],previous['manager_observations'])

    def test_new_native_readback_resets_uncertainty_count(self):
        with patch.object(guard,'step',return_value=dict(checked_utc=guard.utc(),edits_planned=0)):
            result=guard.poll(self.config,2,dict(consecutive_native_failures=1))
        self.assertEqual(result['consecutive_native_failures'],0)
        self.assertEqual(result['status'],'guarding')

    def test_exhausted_native_grace_and_semantic_errors_stop(self):
        with patch.object(guard,'step',side_effect=guard.native.NativeCondorError('unknown')):
            with self.assertRaises(guard.native.NativeCondorError):
                guard.poll(self.config,3,dict(consecutive_native_failures=2))
        with patch.object(guard,'step',side_effect=ValueError('wrong scope')):
            with self.assertRaises(ValueError):
                guard.poll(self.config,4,{})


if __name__ == '__main__':
    unittest.main()
