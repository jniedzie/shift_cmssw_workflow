"""Synthetic retirement safety guards; ROOT payload inspection is injected."""
import getpass
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import retire_dark_photon_intermediates as retire


class RetirementTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.guard = patch.object(retire, 'VALIDATION', self.base)
        self.guard.start()
        self.gen = self.base/'gen'; self.gen.mkdir()
        self.det = self.base/'detector'; self.det.mkdir()
        self.diag = self.base/'diagnostic.json'
        self.ids = [[10, 1, 1], [10, 1, 2]]
        self.hist_inventory = {'cutFlow': dict(entries=2., sum_all_cells=2., sum_error_squared_all_cells=2.)}
        for path in [self.gen/'gen.root', *[self.det/name for name in ('gen.root','step1.root','step2.root','step3.root','nano.root','histograms.root')]]:
            path.write_bytes(b'synthetic root placeholder')
        self.contract = dict(requested_events=2, epsilon=1e-3, mass_gev=15, production_rate_correction=0.04)
        self.audit = dict(runtime_validated=True, events=2, event_ids=self.ids,
                          event_rows=[dict(id=i, weight=1., full_graph_sha256='graph',
                                           signal_decays=[dict(proper_length_mm=0.1, position_mm=[0,0,1,1])]) for i in self.ids])
        self.write(self.gen/'contract.json', self.contract)
        self.write(self.gen/'validation.json', self.audit)
        self.manifest = dict(status='GEN runtime audit passed; physics provisional', validation=self.audit,
                             output_sha256=retire.digest(self.gen/'gen.root'))
        self.write(self.gen/'manifest.json', self.manifest)
        self.source = dict(gen_transport='local', gen=str(self.gen/'gen.root'), gen_sha256=self.manifest['output_sha256'],
                           receipt=str(self.gen/'manifest.json'), receipt_sha256=retire.digest(self.gen/'manifest.json'),
                           event_ids=self.ids, weights=[1.,1.], signal_contract_sha256=retire.digest(self.gen/'contract.json'))
        self.write(self.det/'source.json', self.source)
        self.common = dict(complete=True, events=2, source_gen=str(self.gen/'gen.root'),
                           source_gen_sha256=self.manifest['output_sha256'], source_receipt_sha256=retire.digest(self.gen/'manifest.json'),
                           source_descriptor_sha256=retire.digest(self.det/'source.json'), nano_sha256=retire.digest(self.det/'nano.root'),
                           stages={str(i):dict(bytes=(self.det/f'step{i}.root').stat().st_size,
                                               audit=dict(event_ids=self.ids)) for i in (1,2,3)})
        self.write(self.det/'report.json', self.common)
        self.signal_audit = dict(edm_tiers={str(i):dict(events=2, genparticle_graph_checked=2,
                                                       full_hepmc_graph_checked=2) for i in (1,2,3)})
        self.write(self.det/'signal_audit.json', self.signal_audit)
        self.signal = dict(complete=True, requested_event_ids=self.ids, signal_contract=self.contract,
                           source_manifest_sha256=retire.digest(self.gen/'manifest.json'),
                           common_report_sha256=retire.digest(self.det/'report.json'), signal_audit=self.signal_audit)
        self.write(self.det/'signal_report.json', self.signal)
        self.hist = dict(complete=True, input_sha256=self.common['nano_sha256'],
                         histogram_sha256=retire.digest(self.det/'histograms.root'), histogram_bytes=(self.det/'histograms.root').stat().st_size,
                         input_event_ids=self.ids, native_event_weight_sum=2.,
                         source_signal_report_sha256=retire.digest(self.det/'signal_report.json'),
                         source_chain_report_sha256=retire.digest(self.det/'report.json'),
                         config_overrides=dict(nEvents=2,weightsBranchName='genWeight'),
                         frozen_component_sha256={'binary':'frozen'}, freeze_manifest_sha256='frozen',
                         histograms=self.hist_inventory, histogram_count=1)
        self.write(self.det/'histogram_report.json', self.hist)
        self.diagnostic = dict(schema='shift-dark-photon-mother-aware-nano-diagnostics-v1', complete=True,
                               input=str(self.det/'nano.root'), input_sha256=self.common['nano_sha256'], input_events=2,
                               summary=dict(events=2), events=[dict(event_id=i,native_weight=1.) for i in self.ids],
                               adjacent_receipt_sha256={name:retire.digest(self.det/name) for name in ('signal_report.json','report.json')})
        self.write(self.diag, self.diagnostic)
        self.proof = self.base/'scheduler.json'
        self.scheduler = dict(schema=retire.PROOF_SCHEMA,account=getpass.getuser(),account_wide=True,complete=True,
                              captured_epoch=time.time(),query=dict(returncode=0,stderr='',
                              constraint=f'Owner == "{getpass.getuser()}" || AccountingGroupUser == "{getpass.getuser()}"',
                              command=['condor_q','-global']),ads=[])
        self.write(self.proof,self.scheduler)

    def tearDown(self):
        self.guard.stop(); self.temporary.cleanup()

    def write(self,path,value):
        path.write_text(json.dumps(value))

    def validator(self,kind,path):
        if kind == 'nano':
            return dict(events=2,event_ids=self.ids,weights=[1.,1.])
        if kind == 'histograms':
            return dict(histograms=self.hist_inventory,histogram_count=1)
        return dict(events=2)

    def plan(self):
        return retire.build_plan(self.gen,self.det,self.diag,self.base,self.validator)

    def frozen(self):
        plan=self.plan();path=self.det/'retirement_plan.json';self.write(path,plan);return path,plan

    def execute(self,path):
        return retire.execute_plan(path,self.proof,self.validator,lambda _:None)

    def test_exact_retirement_retains_physics_and_outputs(self):
        path,plan=self.frozen()
        self.assertEqual(len(plan['candidates']),5)
        result=self.execute(path)
        self.assertTrue(result['complete'])
        self.assertTrue((self.det/'nano.root').exists())
        self.assertTrue((self.det/'histograms.root').exists())
        self.assertTrue((self.gen/'validation.json').exists())
        self.assertEqual(plan['full_gen_audit']['event_rows'][0]['signal_decays'][0]['proper_length_mm'],.1)
        self.assertFalse((self.gen/'gen.root').exists())
        self.assertTrue(self.execute(path)['complete'])  # safe resumable verification

    def test_changed_source_blocks_all_deletions(self):
        path,plan=self.frozen();(self.gen/'gen.root').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'Candidate changed'):
            self.execute(path)
        self.assertTrue((self.det/'step1.root').exists())

    def test_foreign_candidate_in_plan_rejected(self):
        path,plan=self.frozen();plan['candidates'][0]['path']=str(self.det/'nano.root');self.write(path,plan)
        with self.assertRaisesRegex(ValueError,'Foreign deletion'):
            self.execute(path)

    def test_symlink_candidate_rejected(self):
        path=self.det/'step1.root';path.unlink();path.symlink_to(self.gen/'gen.root')
        with self.assertRaisesRegex(ValueError,'Symlinks'):
            self.plan()

    def test_hardlinked_candidate_rejected(self):
        os.link(self.det/'step1.root',self.det/'shared.root')
        with self.assertRaisesRegex(ValueError,'multiply linked'):
            self.plan()

    def test_shared_original_source_blocks(self):
        path,plan=self.frozen();other=self.base/'another';other.mkdir();self.write(other/'source.json',self.source)
        with self.assertRaisesRegex(ValueError,'Another detector'):
            self.execute(path)
        self.assertTrue((self.det/'step1.root').exists())

    def test_incomplete_and_outstanding_receipts_block(self):
        self.signal['complete']=False;self.write(self.det/'signal_report.json',self.signal)
        with self.assertRaisesRegex(ValueError,'Incomplete'):
            self.plan()
        self.signal['complete']=True;self.signal['requested_event_ids']=[[10,1,3]];self.write(self.det/'signal_report.json',self.signal)
        with self.assertRaises(ValueError):
            self.plan()

    def test_corrupt_protected_payload_blocks(self):
        with self.assertRaisesRegex(ValueError,'Nano content'):
            retire.build_plan(self.gen,self.det,self.diag,self.base,
                               lambda kind,path: dict(events=0) if kind=='nano' else self.validator(kind,path))

    def test_changed_nano_after_plan_blocks(self):
        path,plan=self.frozen();(self.det/'nano.root').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'Protected file changed'):
            self.execute(path)

    def test_unknown_stale_and_live_scheduler_block(self):
        path,plan=self.frozen()
        for change in (dict(complete=False),dict(captured_epoch=time.time()-301),
                       dict(ads=[dict(Owner=getpass.getuser(),Iwd=str(self.det),Cmd='/bin/worker')])):
            self.write(self.proof,{**self.scheduler,**change})
            with self.assertRaises(ValueError):
                self.execute(path)
            self.assertTrue((self.det/'step1.root').exists())

    def test_local_process_guard_blocks(self):
        path,plan=self.frozen()
        def blocked(_):raise ValueError('live open file')
        with self.assertRaisesRegex(ValueError,'live open file'):
            retire.execute_plan(path,self.proof,self.validator,blocked)
        self.assertTrue((self.det/'step1.root').exists())

    def test_original_gen_requires_full_replay(self):
        path,plan=self.frozen();plan['full_gen_events']=3;self.write(path,plan)
        with self.assertRaisesRegex(ValueError,'unprocessed'):
            self.execute(path)

    def test_partial_replay_retains_original_gen(self):
        full_ids=[*self.ids,[10,1,3]]
        self.audit.update(events=3,event_ids=full_ids)
        self.audit['event_rows'].append(dict(id=full_ids[-1],weight=1.,full_graph_sha256='graph3',
                                           signal_decays=[dict(proper_length_mm=.2,position_mm=[0,0,2,2])]))
        self.contract['requested_events']=3
        self.write(self.gen/'contract.json',self.contract);self.write(self.gen/'validation.json',self.audit)
        self.manifest['validation']=self.audit;self.write(self.gen/'manifest.json',self.manifest)
        self.source.update(event_ids=full_ids,weights=[1.,1.,1.],receipt_sha256=retire.digest(self.gen/'manifest.json'),
                           signal_contract_sha256=retire.digest(self.gen/'contract.json'))
        self.write(self.det/'source.json',self.source)
        self.common.update(source_receipt_sha256=retire.digest(self.gen/'manifest.json'),
                           source_descriptor_sha256=retire.digest(self.det/'source.json'))
        self.write(self.det/'report.json',self.common)
        self.signal.update(signal_contract=self.contract,source_manifest_sha256=retire.digest(self.gen/'manifest.json'),
                           common_report_sha256=retire.digest(self.det/'report.json'))
        self.write(self.det/'signal_report.json',self.signal)
        self.hist.update(source_signal_report_sha256=retire.digest(self.det/'signal_report.json'),
                         source_chain_report_sha256=retire.digest(self.det/'report.json'))
        self.write(self.det/'histogram_report.json',self.hist)
        self.diagnostic['adjacent_receipt_sha256']={name:retire.digest(self.det/name) for name in ('signal_report.json','report.json')}
        self.write(self.diag,self.diagnostic)
        old_validator=self.validator
        def validator(kind,path):
            if kind=='edm' and path.name=='gen.root':return dict(events=3)
            return old_validator(kind,path)
        self.validator=validator
        path,plan=self.frozen()
        self.assertTrue(plan['original_gen_retained']);self.assertEqual(len(plan['candidates']),4)
        self.execute(path)
        self.assertTrue((self.gen/'gen.root').exists())

    def test_missing_intermediate_blocks_planning(self):
        (self.det/'step2.root').unlink()
        with self.assertRaisesRegex(ValueError,'missing intermediate'):
            self.plan()

    def test_unique_current_diagnostic_required(self):
        self.write(self.base/'duplicate.json',self.diagnostic)
        with self.assertRaisesRegex(ValueError,'exactly one current diagnostic'):
            self.plan()

    def test_foreign_path_rejected(self):
        with self.assertRaisesRegex(ValueError,'Foreign path'):
            retire.local_path('/etc/passwd')

    def test_unrelated_current_account_jobs_do_not_block(self):
        path,plan=self.frozen()
        self.scheduler['ads']=[dict(Owner=getpass.getuser(),Iwd='/tmp/unrelated',Cmd='/bin/worker',Args='other_point')]
        self.write(self.proof,self.scheduler)
        self.assertTrue(self.execute(path)['complete'])

    def test_relative_scheduler_reference_blocks(self):
        path,plan=self.frozen()
        self.scheduler['ads']=[dict(Owner=getpass.getuser(),Iwd=str(self.base),Cmd='/bin/worker',Args='detector/step1.py')]
        self.write(self.proof,self.scheduler)
        with self.assertRaisesRegex(ValueError,'Live scheduler reference'):
            self.execute(path)

    def test_failed_query_blocks(self):
        path,plan=self.frozen();self.scheduler['query']['returncode']=1;self.write(self.proof,self.scheduler)
        with self.assertRaisesRegex(ValueError,'Failed/stale/unknown'):
            self.execute(path)

    def test_unrelated_incomplete_descriptor_does_not_block(self):
        path,plan=self.frozen();other=self.base/'obsolete';other.mkdir();(other/'source.json').write_text('')
        self.assertTrue(self.execute(path)['complete'])

    def test_candidate_specific_queries_reject_reader(self):
        plan=self.plan()
        with patch.object(retire.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='12345',stderr='')):
            with self.assertRaisesRegex(ValueError,'Open reader'):
                retire.candidate_open_file_checks(plan)

    def test_candidate_queries_preserve_known_foreign_fuse_warning(self):
        plan=self.plan()
        warning="lsof: WARNING: can't stat() fuse.portal file system /run/user/999999/doc\n      Output information may be incomplete.\n"
        results=[SimpleNamespace(returncode=1,stdout='',stderr=''),SimpleNamespace(returncode=1,stdout='',stderr=warning)]
        with patch.object(retire.subprocess,'run',side_effect=results):
            observed=retire.candidate_open_file_checks(plan)
        self.assertEqual(observed[1]['stderr'],warning)
        self.assertIn('inaccessible',observed[1]['limitation'])

    def test_unknown_open_file_query_warning_blocks(self):
        plan=self.plan()
        with patch.object(retire.subprocess,'run',return_value=SimpleNamespace(returncode=1,stdout='',stderr='permission denied')):
            with self.assertRaisesRegex(ValueError,'Unknown candidate-specific'):
                retire.candidate_open_file_checks(plan)

    def test_new_opaque_pid_cannot_be_added_to_reviewed_proof(self):
        proof=self.base/'process_coverage.json'
        self.write(proof,dict(schema=retire.PROCESS_PROOF_SCHEMA,account=getpass.getuser(),uid=os.getuid(),
                   hostname=retire.socket.gethostname(),reviewed_pids=[1,2],exceptions=[],
                   review_decision='explicit-root-review-20261009-protected-login-session-only'))
        with self.assertRaisesRegex(ValueError,'Unreviewed process exception'):
            retire.process_exceptions(dict(process_exceptions_proof=retire.fingerprint(proof)))


if __name__=='__main__':unittest.main()
