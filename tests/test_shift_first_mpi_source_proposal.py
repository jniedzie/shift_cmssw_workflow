# Code style: AI-code. AI-generated native source-density and publication tests.
import copy
import json
import math
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).parents[1]/'scripts'))
import shift_first_mpi_source_proposal as source
import run_shift_weighted_gen as worker


def proposal(mode='mixture'):
    return dict(schema=source.SCHEMA, algorithm=source.ALGORITHM,
                base_sampler_algorithm=source.BASE_ALGORITHM, mode=mode, target='heavy',
                component_seed=173050002, overlay_sha256='a'*64, natural_decays=True,
                maximum_history_bytes=128*1024*1024)


def fixture():
    """Three source slots: one accepted retry, one zero, one final-bin reject."""
    rows, candidate = [], 0
    def ordinary(call, slot, pt):
        weight = 10. if pt < 20. else 0.
        rows.append(dict(type='first_complete', first_call=call, slot=slot,
            component='original_below_L', normalizer_geometric_count=1,
            upstream_enabled=False, upstream_biased_component=False, raw_first_pT_GeV=pt,
            first_call_weight=weight, full_intrinsic_retry_path_weight=weight,
            selected_code=111))
    def tail(call, slot, retry_weight=1., failures=False):
        nonlocal candidate
        log = 0.
        statuses = ('zero', 'rejected', 'kinematic_failure', 'success') if failures else ('success',)
        for status in statuses:
            candidate += 1
            common = dict(first_call=call, slot=slot, candidate=candidate)
            rows.append(dict(common, type='candidate_begin', log_path_ratio=log))
            if status == 'zero':
                rows.append(dict(common, type='candidate_zero', reason='incoming_x_bound', log_path_ratio=log))
                rows.append(dict(common, type='outcome', WT=0., WT_kept=False,
                    kinematics_attempted=False, kinematics_succeeded=False, rao_blackwell_thinning=False,
                    channel_factor_in_path=1., upstream_channel_ratio_in_path=1., upstream_log_path_ratio=log))
                continue
            incoming = dict(common, type='incoming', PDF_entries=[[-1,1.,1.,.25,.25],
                [0,2.,2.,.5,.5],[1,1.,1.,.25,.25]], PDF_sums=[4.,4.],
                id1=21,id2=21, eligible_probability=.375, native_probability=.25,
                biased_probability=.625)
            log += math.log(2.5)
            rows.append(dict(incoming,log_path_ratio=log))
            log += math.log(.92/.2)
            rows.append(dict(common,type='cache_group',picked_other=True,
                             native_probability=.2,biased_probability=.92,log_path_ratio=log))
            # Signed entries exercise first-crossing support rather than raw/sum.
            cache = [[0,0,111,0,0,1.,.2],[0,1,121,4,4,2.,.38],[0,2,999,0,0,-.1,0.],
                     [1,0,111,0,0,1.,.2],[1,1,121,4,4,1.,.2],[1,2,999,0,0,.1,.02]]
            q = .1*.38+.9*.38/.58
            ratio = q/.38
            rows.append(dict(common,type='select',cache=cache,cache_total=5.,heavy_cache_total=3.,
                native_side_sums=[2.9,2.1],
                native_heavy_probability=.58,selected_side=0,selected_index=1,selected_code=121,
                selected_mass_ids=[4,4],bias_enabled=True,proposal_applied=True,picked_other=True,
                has_negative_raw_cache_terms=True,native_probability=.38,proposal_probability=q,
                f_over_q=1.,upstream_biased_density_ratio=ratio))
            attempted = status != 'rejected'
            if attempted: log += math.log(ratio)
            rows.append(dict(common,type='outcome',WT=.2 if not attempted else 1.,WT_kept=attempted,
                kinematics_attempted=attempted,kinematics_succeeded=status=='success',
                rao_blackwell_thinning=False,channel_factor_in_path=1.,
                upstream_channel_ratio_in_path=ratio if attempted else 1.,upstream_log_path_ratio=log))
        correction=1./(.1+.9*math.exp(log))
        weight=2.*.01*correction/.9
        rows.append(dict(type='first_complete',first_call=call,slot=slot,component='tail',
            normalizer_geometric_count=2,upstream_enabled=True,upstream_biased_component=True,
            upstream_component_coin_seed=173050002,upstream_target_quark=0,
            upstream_log_path_ratio=log,upstream_correction=correction,
            failed_kinematics_channel_product=1.,final_channel_factor=1.,
            accepted_tail_candidate=True,tail_occurrence_probability=.01,thinning_factor=1.,
            selected_code=121,first_call_weight=weight,full_intrinsic_retry_path_weight=retry_weight*weight))
        return retry_weight*weight
    ordinary(1,1,10.)
    accepted_weight=tail(2,1,10.,True)
    ordinary(3,2,30.)
    tail(4,3)  # Positive source path, but ownership/final-bin selection rejects it.
    ledger=dict(proposal_calls=4,tried=3,normalizer_trials=6,handled_kinematic_failures=1,
                lower=20.,run=510230001,accepted=1,sumw=accepted_weight,sumw2=accepted_weight**2,initializations=1)
    semantic=dict(event_ids=[[ledger['run'],1,1]],weights=[accepted_weight])
    prefilter=dict(semantic,events=1)
    return rows,ledger,semantic,prefilter


def two_tails(rare_channel=False):
    original,_,_,_=fixture()
    template=[copy.deepcopy(row) for row in original if row['first_call']==4]
    if rare_channel:
        selected=next(row for row in template if row['type']=='select')
        selected.update(cache=[[0,0,121,4,4,1.e-100,5.e-101],[0,1,111,0,0,1.,.5],
            [1,0,121,4,4,0.,0.],[1,1,111,0,0,1.,.5]],cache_total=2.,heavy_cache_total=1.e-100,
            native_side_sums=[1.,1.],
            native_heavy_probability=5.e-101,selected_index=0,has_negative_raw_cache_terms=False,
            native_probability=5.e-101,proposal_probability=.9,
            upstream_biased_density_ratio=.9/5.e-101)
        log=math.log(2.5)+math.log(.92/.2)+math.log(selected['upstream_biased_density_ratio'])
        outcome=next(row for row in template if row['type']=='outcome')
        outcome.update(upstream_channel_ratio_in_path=selected['upstream_biased_density_ratio'],
                       upstream_log_path_ratio=log)
        final=template[-1]
        correction=1./(.1+.9*math.exp(log))
        final.update(upstream_log_path_ratio=log,upstream_correction=correction,
                     first_call_weight=2.*.01*correction/.9,
                     full_intrinsic_retry_path_weight=2.*.01*correction/.9)
    rows=[]
    for call in (1,2):
        for original_row in template:
            row=copy.deepcopy(original_row);row.update(first_call=call,slot=call)
            if 'candidate' in row:row['candidate']=call
            rows.append(row)
    weight=template[-1]['first_call_weight']
    ledger=dict(proposal_calls=2,tried=2,normalizer_trials=4,handled_kinematic_failures=0,
                lower=20.,run=510230001,accepted=2,sumw=2.*weight,sumw2=2.*weight*weight,initializations=1)
    saved=dict(event_ids=[[ledger['run'],1,1],[ledger['run'],1,2]],weights=[weight,weight])
    return rows,ledger,saved,dict(copy.deepcopy(saved),events=2)


class SourceProposalTest(unittest.TestCase):
    def test_retry_rejection_signed_cache_and_final_acceptance_are_reconciled(self):
        rows,ledger,saved,prefilter=fixture()
        report=source.audit_history(rows,ledger,saved,proposal(),prefilter)
        self.assertTrue(report['passed'])
        self.assertEqual(report['full_source_trials'],3)
        self.assertEqual(report['prefilter_accepted_events'],1)
        self.assertEqual(report['first_scatter_calls'],4)
        self.assertEqual(report['saved_events_by_last_first_hard_code'],{'121':1})

    def test_rejected_channels_are_marginalized_but_incoming_and_group_draws_remain(self):
        rows,ledger,saved,prefilter=fixture()
        index=next(i for i,r in enumerate(rows) if r['type']=='outcome' and r['WT']==.2)
        rows[index]['upstream_channel_ratio_in_path']=2.
        with self.assertRaisesRegex(ValueError,'channel path factor'):
            source.audit_history(rows,ledger,saved,proposal(),prefilter)

    def test_missing_zero_or_rejected_candidate_cannot_pass(self):
        for kind in ('candidate_zero','outcome','incoming','cache_group'):
            rows,ledger,saved,prefilter=fixture()
            rows.pop(next(i for i,r in enumerate(rows) if r['type']==kind))
            with self.subTest(kind=kind),self.assertRaises(ValueError):
                source.audit_history(rows,ledger,saved,proposal(),prefilter)

    def test_wrong_coin_seed_or_component_is_rejected(self):
        rows,ledger,saved,prefilter=fixture()
        next(r for r in rows if r['type']=='first_complete' and r['component']=='tail')['upstream_biased_component']=False
        with self.assertRaisesRegex(ValueError,'coin sequence'):
            source.audit_history(rows,ledger,saved,proposal(),prefilter)

    def test_swapped_completion_slots_fail_even_when_all_weights_match(self):
        rows,ledger,saved,prefilter=two_tails()
        self.assertTrue(source.audit_history(rows,ledger,saved,proposal(),prefilter)['passed'])
        finals=[row for row in rows if row['type']=='first_complete']
        finals[0]['slot'],finals[1]['slot']=2,1
        with self.assertRaisesRegex(ValueError,'crosses framework slots'):
            source.audit_history(rows,ledger,saved,proposal(),prefilter)

    def test_tiny_incoming_probability_and_eligible_support_are_checked_relatively(self):
        row=dict(PDF_entries=[[-1,1.e-100,1.e-100,1.e-100,1.e-100],
            [0,0.,0.,0.,0.],[1,1.,1.,1.,1.]],PDF_sums=[1.,1.],id1=-1,id2=-1,
            eligible_probability=2.e-100,native_probability=1.e-200,biased_probability=1.e-201)
        self.assertAlmostEqual(source.incoming_ratio(row),.1)
        for field in ('native_probability','biased_probability','eligible_probability'):
            for multiplier in (0.,100.):
                changed=copy.deepcopy(row);changed[field]*=multiplier
                with self.subTest(field=field,multiplier=multiplier),self.assertRaises(ValueError):
                    source.incoming_ratio(changed)

    def test_tiny_native_channel_probability_and_whole_path_correction_cannot_be_zeroed(self):
        rows,ledger,saved,prefilter=two_tails(True)
        self.assertTrue(source.audit_history(rows,ledger,saved,proposal(),prefilter)['passed'])
        for kind,field in (('select','native_probability'),('select','native_heavy_probability'),
                           ('first_complete','upstream_correction')):
            for multiplier in (0.,100.):
                changed=copy.deepcopy(rows)
                next(row for row in changed if row['type']==kind)[field]*=multiplier
                with self.subTest(kind=kind,field=field,multiplier=multiplier),self.assertRaises(ValueError):
                    source.audit_history(changed,ledger,saved,proposal(),prefilter)

    def test_native_side_denominators_preserve_small_CDF_intervals(self):
        # A recorded native double sum differs by one ulp from compensated sum.
        # Near the CDF end, subtraction magnifies that into a relative error.
        raw_u=[0.,1.7375544566378028e-11,8.67796223156199e-13,3.3706556325994176e-15,
            3.7389540066252245e-15,2.2153811721144023e-13,1.1403905304055866e-15,
            9.121694132761018e-16,1.19350589378342e-14,1.7107837002386378e-13,
            6.717991908158755e-14,1.580213724504088e-13,2.2652065830365936e-14,
            1.5228204287491516e-13,2.1334010975647238e-15,3.948060187101745e-13,
            2.1704230182386106e-15,2.902278731372461e-16,9.864612988511411e-14,
            6.104852782763055e-16,7.32148667259762e-17,1.4819623825384855e-13,
            9.807591823119452e-16,1.1063913360239986e-16,8.097836136100739e-15,
            9.239513089297456e-14,3.697071518815792e-14,9.140965748810481e-14,
            1.479254050699301e-14,9.084335885539958e-14]
        native=[1.9334714012454923e-11,2.003971668189427e-11]
        self.assertNotEqual(math.fsum(raw_u),native[1])
        probability_u=native[1]/(native[0]+native[1])
        cache=[[0,i,111,0,0,native[0] if i==0 else 0.,1.-probability_u if i==0 else 0.]
               for i in range(len(raw_u))]
        cumulative,highest=0.,0.
        for index,value in enumerate(raw_u):
            cumulative+=value;boundary=min(1.,max(0.,cumulative/native[1]))
            probability=probability_u*max(0.,boundary-highest);highest=max(highest,boundary)
            cache.append([1,index,121 if index==20 else 111,4 if index==20 else 0,
                          4 if index==20 else 0,value,probability])
        # This exact native interval caused the first real disabled canary to fail.
        self.assertEqual(cache[len(raw_u)+20][6],1.859452071686505e-6)
        row=dict(cache=cache,native_side_sums=native,cache_total=native[0]+native[1],
            heavy_cache_total=raw_u[20],native_heavy_probability=cache[len(raw_u)+20][6],
            selected_side=1,selected_index=20,selected_code=121,selected_mass_ids=[4,4],
            bias_enabled=False,proposal_applied=False,has_negative_raw_cache_terms=False,
            native_probability=cache[len(raw_u)+20][6],proposal_probability=cache[len(raw_u)+20][6],
            f_over_q=1.,upstream_biased_density_ratio=1.)
        self.assertEqual(source.channel_ratio(row,'heavy',False),1.)
        changed=copy.deepcopy(row);changed['native_side_sums'][1]=math.fsum(raw_u)
        with self.assertRaisesRegex(ValueError,'Native channel interval'):
            source.channel_ratio(changed,'heavy',False)
        del row['native_side_sums']
        with self.assertRaisesRegex(ValueError,'explicitly recorded'):
            source.channel_ratio(row,'heavy',False)

    def test_full_source_acceptance_is_not_all_positive_prefixes(self):
        rows,ledger,saved,prefilter=fixture()
        rejected=rows[-1]['full_intrinsic_retry_path_weight']
        self.assertGreater(rejected,0.)
        bad=copy.deepcopy(prefilter)
        bad['events']=2
        bad['event_ids'].append([ledger['run'],1,3]);bad['weights'].append(rejected)
        with self.assertRaisesRegex(ValueError,'full native acceptance'):
            source.audit_history(rows,ledger,saved,proposal(),bad)

    def test_filter_subsets_are_allowed_but_extra_or_reweighted_events_are_not(self):
        rows,ledger,saved,prefilter=fixture()
        self.assertEqual(source.audit_history(rows,ledger,dict(event_ids=[],weights=[]),proposal(),prefilter)['saved_events'],0)
        for change in ('identity','weight','prefilter_weight'):
            rows,ledger,saved,prefilter=fixture()
            if change=='identity':saved['event_ids']=[[ledger['run'],1,3]]
            elif change=='weight':saved['weights']=[saved['weights'][0]*2]
            else:prefilter['weights']=[prefilter['weights'][0]*2]
            with self.subTest(change=change),self.assertRaises(ValueError):
                source.audit_history(rows,ledger,saved,proposal(),prefilter)

    def test_native_coin_replay_known_libstdcxx_fixture(self):
        # Independent retained C++ std::mt19937_64 utility, seed173050002.
        self.assertEqual(list(source.coin_flags(173050002,25)),
            [True]*4+[False]+[True]*16+[False,False,True,True])

    def test_disabled_proposal_replays_native_channel_and_base_weights(self):
        rows,_,_,_=fixture()
        selected=copy.deepcopy(next(row for row in rows if row['type']=='select'))
        selected.update(first_call=1,slot=1,candidate=1,bias_enabled=False,proposal_applied=False,
                        proposal_probability=selected['native_probability'],upstream_biased_density_ratio=1.)
        outcome=dict(type='outcome',first_call=1,slot=1,candidate=1,WT=1.,WT_kept=True,
            kinematics_attempted=True,kinematics_succeeded=True,rao_blackwell_thinning=False,
            channel_factor_in_path=1.,upstream_channel_ratio_in_path=1.,upstream_log_path_ratio=0.)
        final=copy.deepcopy(next(row for row in rows if row['type']=='first_complete' and row['component']=='tail'))
        weight=2.*.01/.9
        final.update(first_call=1,slot=1,upstream_enabled=False,upstream_biased_component=False,
                     upstream_correction=1.,first_call_weight=weight,full_intrinsic_retry_path_weight=weight)
        zero=copy.deepcopy(next(row for row in rows if row['type']=='first_complete' and row['slot']==2))
        zero['first_call']=2
        ledger=dict(proposal_calls=2,tried=2,normalizer_trials=3,handled_kinematic_failures=0,
                    lower=20.,run=510230001,accepted=1,sumw=weight,sumw2=weight**2,initializations=1)
        saved=dict(event_ids=[[ledger['run'],1,1]],weights=[weight])
        report=source.audit_history([selected,outcome,final,zero],ledger,saved,proposal('disabled'),dict(saved,events=1))
        self.assertEqual(report['upstream_component_draws'],0)
        self.assertEqual(report['saved_events'],1)

    def test_generation_environment_drops_ambient_proposals_and_decay_wrappers(self):
        env=dict(PATH='/bin',LD_PRELOAD='/frozen/source.so',SHIFT_HEAVY_WHOLE_MODE='mixture',
                 SHIFT_MPI_BIAS_FIRST_JPSI='1',SHIFT_MPI_UPSTREAM_TARGET='bottom')
        result=source.generation_environment(env,proposal())
        self.assertNotIn('SHIFT_HEAVY_WHOLE_MODE',result)
        self.assertNotIn('SHIFT_MPI_BIAS_FIRST_JPSI',result)
        self.assertEqual(result['SHIFT_MPI_UPSTREAM_TARGET'],'heavy')
        self.assertEqual(result['LD_PRELOAD'],'/frozen/source.so')
        disabled=source.generation_environment(env,proposal('disabled'))
        self.assertEqual([k for k in disabled if k.startswith('SHIFT_')],['SHIFT_MPI_HARD_CHANNEL_AUDIT'])

    def test_old_manifests_are_unchanged_and_new_policy_is_pilot_only(self):
        self.assertIsNone(source.validate({},{}))
        self.assertEqual(worker.source_artifacts({}),[])
        row=dict(stage='pilot',sample='qcd',trials=500)
        manifest=dict(algorithm=source.ALGORITHM,generator_profile=source.PROFILE,
                      maximum_publication_bytes_per_worker=160000000,
                      source_proposal=proposal(),inputs={'libShiftMpiTailProposal.so':'a'*64,
                      'shift_first_mpi_source_proposal.py':'b'*64})
        self.assertEqual(source.validate(manifest,row),proposal())
        for mutate in (lambda m,r:r.update(stage='production'), lambda m,r:r.update(trials=2001),
                       lambda m,r:m['source_proposal'].update(natural_decays=False),
                       lambda m,r:m['source_proposal'].update(environment={'LD_PRELOAD':'bad'}),
                       lambda m,r:m.update(algorithm=source.BASE_ALGORITHM)):
            m,r=copy.deepcopy(manifest),dict(row);mutate(m,r)
            with self.assertRaises(ValueError):source.validate(m,r)


class WorkerPublicationGateTest(unittest.TestCase):
    def test_source_publication_size_includes_both_ROOTs_history_and_receipts(self):
        with tempfile.TemporaryDirectory() as temp:
            files=[]
            for name in ('gen.root','prefilter.root','hard_channel_history.jsonl'):
                path=Path(temp)/name;path.write_bytes(b'x'*100);files.append(path)
            manifest=dict(source_proposal=proposal(),maximum_publication_bytes_per_worker=1000000)
            with self.assertRaisesRegex(ValueError,'byte reservation'):
                worker.check_source_publication_size(manifest,files,{})
            manifest['maximum_publication_bytes_per_worker']=2000000
            record={};worker.check_source_publication_size(manifest,files,record)
            self.assertEqual(record['publication_size_guard']['payload_bytes'],300)
            self.assertGreater(record['publication_size_guard']['projected_bytes'],1024*1024+300)

    def test_history_storage_guard_stops_attempt_and_preserves_written_history(self):
        with tempfile.TemporaryDirectory() as temp:
            previous=Path.cwd();os.chdir(temp)
            try:
                program="from pathlib import Path; import time; Path('hard_channel_history.jsonl').write_bytes(b'x'*2048); time.sleep(3)"
                with self.assertRaisesRegex(ValueError,'storage bound'):
                    worker.command([sys.executable,'-c',program],'generation.log',timeout=10,source_history_bound=1024)
                self.assertEqual(Path('hard_channel_history.jsonl').read_bytes(),b'x'*2048)
            finally:os.chdir(previous)

    def test_failed_source_audit_publishes_nothing_and_preserves_full_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            previous=Path.cwd();os.chdir(temp)
            try:
                root=Path(temp)
                (root/'libShiftMpiTailProposal.so').write_bytes(b'overlay')
                (root/'pluginShiftMpiWeightedProposalHook.so').write_bytes(b'hook')
                (root/'qcd_mu_enrichment.py').write_bytes(b'profile')
                (root/'shift_first_mpi_source_proposal.py').write_bytes(b'auditor')
                with tarfile.open('runtime.tar.gz','w:gz') as archive:
                    entry=tarfile.TarInfo('CMSSW_17_0_0_pre4/lib/el9_amd64_gcc13');entry.type=tarfile.DIRTYPE;entry.mode=0o755;archive.addfile(entry)
                inputs={p.name:worker.sha(p) for p in root.iterdir() if p.is_file()}
                p=proposal();p['overlay_sha256']=inputs['libShiftMpiTailProposal.so']
                row=dict(id='pilot',stage='pilot',sample='qcd',trials=3,run=510230001,seed=3,
                         bounds=[20.,25.],publish=True)
                manifest=dict(algorithm=source.ALGORITHM,generator_profile=source.PROFILE,
                              maximum_publication_bytes_per_worker=160000000,
                              source_proposal=p,inputs=inputs,jobs=[row],eos_base='/synthetic/eos')
                Path('manifest.json').write_text(json.dumps(manifest))
                def command(args,log,env=None,timeout=3600,source_history_bound=None):
                    Path(log).write_text('test')
                    if args[0]=='bash':Path('runtime_env.json').write_text('{}')
                    elif args[0]=='cmsDriver.py':Path('gen_cfg.py').write_text('# synthetic config\n')
                    elif args[0]=='edmConfigDump':Path(log).write_text('# synthetic resolved config\n')
                    elif args[0]=='cmsRun':
                        Path('gen.root').write_bytes(b'gen');Path('prefilter.root').write_bytes(b'prefilter')
                        Path('hard_channel_history.jsonl').write_text('{"full_history":"preserved"}\n')
                        Path('proposal_ledger.json').write_text(json.dumps(dict(algorithm=source.BASE_ALGORITHM,
                            requested_trials=3,run=row['run'],lower=20.,upper=25.)))
                    elif args[1]=='audit_shift_weighted_gen.py':Path('semantic_audit.json').write_text('{"events":0}')
                    elif args[1]=='shift_first_mpi_source_proposal.py':raise ValueError('source density mismatch')
                    else:raise AssertionError(args)
                with patch.object(worker,'command',side_effect=command),patch.object(worker,'existing_publication',return_value=None),\
                     patch.object(worker,'publish') as publish,patch.object(sys,'argv',
                     ['worker','--manifest','manifest.json','--expected-sha256',worker.sha('manifest.json'),'--node','pilot']):
                    self.assertEqual(worker.main(),1)
                    publish.assert_not_called()
                report=json.loads(Path('result.json').read_text())
                self.assertFalse(report['complete']);self.assertFalse(report['publication_complete'])
                self.assertIn('source density mismatch',report['error'])
                with zipfile.ZipFile('artifacts.zip') as archive:
                    for name in ('gen.root','prefilter.root','hard_channel_history.jsonl'):
                        self.assertEqual(archive.read(name),Path(name).read_bytes())
            finally:os.chdir(previous)


if __name__=='__main__':unittest.main()
