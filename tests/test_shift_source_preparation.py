# Code style: AI-code. AI-generated bounded source-census contract tests.
"""Census preparation must preserve the population, weights and exact source map."""
import ast
import copy
import gzip
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
import run_shift_source_preparation as prep


def plan(count=3):
    identities=[[91,1,i+1] for i in range(count)]
    positions=[dict(event_id=identity,target_position_cm=[1.,2.,3.]) for identity in identities]
    source=dict(source_id='synthetic',source_family='qcd_ordinary',generator_profile='ordinary',
        decay_dependent_filter=False,gen_sha256='a'*64,saved_events=count,
        full_source_trials=count+2,sigma_nd_pb=10.,denominator_kind='fixed_source_trials',
        timing_input_label='generatorSmeared',requires_full_muon_completion=True,
        muon_enhancement_complete=False)
    unit=dict(family='qcd',source=source,event_ids=identities,native_weights=[.2]*count,
        positions=positions,input_label='generatorSmeared',seed=17,
        full_saved_source_census=True,source_inclusion_probability=1.)
    return dict(maximum_events_per_unit=2000,units=[unit],test_scope='assumed-jaw-depth-canary',
        event_positions=copy.deepcopy(positions),containment_rows=[dict(event_id=x) for x in identities],
        material_feature_census=True,geometry_path='synthetic.gdml',frame=dict(minimum_abs_z_cm=1.))


class SourceCensusTests(unittest.TestCase):
    def test_smooth_audit_sums_all_components_and_requires_absorbed_latent_parents(self):
        definition=dict(ordinary_fraction=.1,components=[
            dict(probability=.2,parent_barcodes=[11],mean_proper_lifetime_mm=[2.]),
            dict(probability=.2,parent_barcodes=[22],mean_proper_lifetime_mm=[3.]),
            dict(probability=.5,parent_barcodes=[11,22],mean_proper_lifetime_mm=[2.,3.])])
        a=5.*math.exp(1./10.-1./2.)
        b=(20./3.)*math.exp(2./20.-2./3.)
        ratio=.1+.2*a+.2*b+.5*a*b
        prep.audit_smooth_proposal(definition,[11,22,33],[1.,2.,50.],[10.,20.,30.],
                                   .1,ratio,1./ratio,3,[11,22])
        with self.assertRaisesRegex(ValueError,'full mixture'):
            prep.audit_smooth_proposal(definition,[11,22],[1.,2.],[10.,20.],
                                       .1,.5*a*b,1./(.5*a*b),3,[11,22])
        # Absorption occurs after the draw and cannot remove a latent density.
        with self.assertRaisesRegex(ValueError,'component'):
            prep.audit_smooth_proposal(definition,[11],[1.],[10.],.1,ratio,1./ratio,3,[11,22])
        with self.assertRaisesRegex(ValueError,'Selected parents'):
            prep.audit_smooth_proposal(definition,[11,22],[1.,2.],[10.,20.],
                                       .1,ratio,1./ratio,1,[22])

    def test_all_295_originals_and_native_exposure_are_preserved(self):
        value=plan(295);before=copy.deepcopy(value)
        self.assertEqual(prep.validate_plan(value),295)
        self.assertEqual(value,before)
        self.assertEqual(value['units'][0]['source']['full_source_trials'],297)

    def test_censored_completed_partial_or_duplicate_inputs_are_rejected(self):
        changes=[lambda p:p['units'][0]['source'].update(generator_profile='shift-qcd-mu-enriched-cylinder-v1'),
            lambda p:p['units'][0]['source'].update(decay_dependent_filter=True),
            lambda p:p['units'][0]['source'].update(muon_enhancement_complete=True),
            lambda p:p['units'][0]['source'].update(source_preparation_receipt_json='already prepared'),
            lambda p:p['units'][0]['source'].update(saved_events=4),
            lambda p:p['units'][0].update(source_inclusion_probability=.1),
            lambda p:p['units'][0].update(source_inclusion_probability=True),
            lambda p:p['units'][0]['native_weights'].__setitem__(0,float('nan')),
            lambda p:p['units'][0]['native_weights'].__setitem__(0,-1.),
            lambda p:p['units'][0]['native_weights'].pop(),
            lambda p:p['units'][0]['event_ids'].__setitem__(1,p['units'][0]['event_ids'][0]),
            lambda p:p['units'][0].update(seed=900000000),
            lambda p:p.update(maximum_events_per_unit=2001),
            lambda p:p.update(material_feature_census=1)]
        for mutate in changes:
            value=plan();mutate(value)
            with self.subTest(value=value),self.assertRaises(ValueError):prep.validate_plan(value)

    def test_source_position_and_native_containment_census_cannot_diverge(self):
        value=plan();value['event_positions'][0]['target_position_cm'][0]+=1
        with self.assertRaisesRegex(ValueError,'Global native'):prep.validate_plan(value)
        value=plan();value['containment_rows'].pop()
        with self.assertRaisesRegex(ValueError,'containment'):prep.validate_plan(value)

    def test_configuration_uses_full_count_and_the_qualified_common_chain(self):
        value=plan(295);source=prep.source_configuration(value['units'][0],value);ast.parse(source)
        self.assertIn('cms.untracked.int32(295)',source)
        self.assertIn("chain=configure_complete_source(p,'generatorSmeared'",source)
        self.assertIn('91:1:295-91:1:295',source)
        self.assertIn("drop *_genParticles_*_*",source)
        self.assertIn("keep *_genParticles_*_SHIFTFULLGEN",source)
        self.assertNotIn('MinPt',source)

    def test_feature_reader_retains_zero_muon_controls_and_native_weight_identity(self):
        value=plan();value['feature_event_timeout_seconds']=10.;unit=value['units'][0]
        for mutation in (None,'weight','prefix','rawsha','missing'):
            with self.subTest(mutation=mutation),tempfile.TemporaryDirectory() as directory:
                path=Path(directory);output=path/'complete.root';output.write_bytes(b'synthetic')
                def scanner(command,**kwargs):
                    self.assertNotIn('--maximum-events-per-source',command)
                    self.assertIn('--event-timeout-seconds',command)
                    self.assertEqual(command[command.index('--event-timeout-seconds')+1],'10.0')
                    data=dict(events=[dict(event_id=x,native_weight=w,original_source_sha256='a'*64,
                        zero_GEN_muon_control=True) for x,w in zip(unit['event_ids'],unit['native_weights'])],
                        sources=[dict(sha256=prep.sha(output))],maximum_events_per_source=None,
                        prefix_requested=False,summary=dict(source_read_counts=[dict(entire_input_file_read=True)]))
                    if mutation=='weight':data['events'][0]['native_weight']*=2
                    if mutation=='prefix':data['prefix_requested']=True
                    if mutation=='rawsha':data['events'][0]['original_source_sha256']='b'*64
                    if mutation=='missing':data['events'].pop()
                    with gzip.open(command[-1],'wt') as stream:json.dump(data,stream)
                with patch.object(prep.subprocess,'run',side_effect=scanner), patch(
                        'run_shift_gen_to_nano.command',side_effect=scanner):
                    if mutation is None:
                        features,audit=prep.feature_census(path,unit,value,output)
                        self.assertTrue(audit['zero_muon_controls_retained'])
                        self.assertEqual(audit['events'],3)
                        self.assertFalse(audit['usable_as_physics_weight'])
                    else:
                        with self.assertRaises(ValueError):prep.feature_census(path,unit,value,output)

    def test_navigator_timeout_retries_full_graph_without_material_and_keeps_weights(self):
        value=plan();unit=value['units'][0]
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory);output=path/'complete.root';output.write_bytes(b'synthetic');calls=[]
            def scanner(command,**kwargs):
                calls.append(command)
                if len(calls)==1:
                    Path(command[-1]).write_bytes(b'partial')
                    raise prep.subprocess.TimeoutExpired(command,600)
                self.assertIn('--skip-material-navigation',command)
                data=dict(events=[dict(event_id=e,native_weight=w,original_source_sha256='a'*64)
                    for e,w in zip(unit['event_ids'],unit['native_weights'])],
                    sources=[dict(sha256=prep.sha(output))],maximum_events_per_source=None,
                    prefix_requested=False,summary=dict(source_read_counts=[dict(entire_input_file_read=True)]))
                with gzip.open(command[-1],'wt') as stream:json.dump(data,stream)
            with patch.object(prep.subprocess,'run',side_effect=scanner):
                features,audit=prep.feature_census(path,unit,value,output)
            self.assertEqual(len(calls),2);self.assertEqual(audit['events'],3)
            self.assertEqual(audit['navigation_fallback']['reason'],'material navigator CPU timeout')
            self.assertFalse(audit['usable_as_physics_weight'])


class DeferredCharmProfileTests(unittest.TestCase):
    def configuration(self,sample='qcd'):
        from test_shift_weighted_gen_audit import fixture,configuration
        ledger,_,_=fixture(sample=sample)
        source=configuration(ledger)
        source=source.replace("'Check:abortIfVeto = on'", "'Check:abortIfVeto = on', '411:mayDecay = off', '421:mayDecay = off'")
        return ledger,source

    def particle(self,barcode=7,pdg=411):
        return dict(barcode=barcode,pdg=pdg,status=1,momentum=[0.,0.,-10.,math.sqrt(100.+1.87**2)],
            generated_mass=1.87,production_vertex=-1,end_vertex=None)

    def test_declared_profile_changes_only_the_exact_ordered_D_frontier_suffix(self):
        from audit_shift_weighted_gen import validate_config,DEFERRED_CHARM_PROFILE,SOURCE_MODEL_SHA256
        ledger,source=self.configuration()
        self.assertNotEqual(validate_config(source,ledger,DEFERRED_CHARM_PROFILE),SOURCE_MODEL_SHA256)
        with self.assertRaises(ValueError):validate_config(source,ledger)
        for old,new in [('421:mayDecay = off','431:mayDecay = off'),('411:mayDecay = off','411:mayDecay = on'),
                        ("'411:mayDecay = off', '421:mayDecay = off'","'421:mayDecay = off', '411:mayDecay = off'"),
                        ('Beams:eB = 6800.','Beams:eB = 6500.')]:
            with self.subTest(old=old),self.assertRaises(ValueError):
                validate_config(source.replace(old,new),ledger,DEFERRED_CHARM_PROFILE)
        ledger,source=self.configuration('direct_jpsi')
        with self.assertRaisesRegex(ValueError,'requires QCD'):validate_config(source,ledger,DEFERRED_CHARM_PROFILE)

    def test_declared_source_still_rejects_an_added_decay_or_output_filter(self):
        from audit_shift_weighted_gen import DEFERRED_CHARM_PROFILE
        from shift_first_mpi_source_proposal import validate_uncensored_config
        ledger,source=self.configuration()
        source+='''
process.ProductionFilterSequence=cms.Sequence(process.generator)
process.generation_step=cms.Path(process.ProductionFilterSequence+process.pgen)
process.FEVTDEBUGoutput=cms.OutputModule('PoolOutputModule',SelectEvents=cms.untracked.PSet(SelectEvents=cms.vstring('generation_step')))
'''
        validate_uncensored_config(source,ledger,DEFERRED_CHARM_PROFILE)
        with self.assertRaisesRegex(ValueError,'additional filter'):
            validate_uncensored_config(source.replace('cms.Sequence(process.generator)',
                'cms.Sequence(process.generator+process.mugenfilter)'),ledger,DEFERRED_CHARM_PROFILE)

    def test_fresh_frontier_and_completed_graph_reject_old_decays_and_nested_D(self):
        from audit_shift_weighted_gen import deferred_charm_frontier,audit_deferred_charm_completion
        before=dict(particles=[self.particle(),self.particle(8,-421)])
        self.assertEqual(len(deferred_charm_frontier(before['particles'])),2)
        after=copy.deepcopy(before)
        for p in after['particles']:p.update(status=2,end_vertex=-2)
        result=audit_deferred_charm_completion(before,after)
        self.assertTrue(result['all_original_D_roots_completed'])
        for mutation in ('already_decayed','nested','changed_mass','uncompleted'):
            old,new=copy.deepcopy(before),copy.deepcopy(after)
            if mutation=='already_decayed':old['particles'][0].update(status=2,end_vertex=-2)
            if mutation=='nested':new['particles'].append(self.particle(9,-411))
            if mutation=='changed_mass':new['particles'][0]['generated_mass']+=.1
            if mutation=='uncompleted':new['particles'][0].update(status=1,end_vertex=None)
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):audit_deferred_charm_completion(old,new)

    def test_native_bank_requires_the_complete_fresh_source_census_but_no_pion_scales(self):
        from run_shift_sm_decay_pilot import validate
        from audit_shift_weighted_gen import DEFERRED_CHARM_PROFILE
        request=dict(diagnostic_only=True,new_GEN_exposure=0,arm='mixture',event_id=[1,1,1],native_weight=1.,decay_seed=71,
            original_source=dict(generator_profile=DEFERRED_CHARM_PROFILE,decay_dependent_filter=False,
                full_source_trials=10,saved_events=1,deferred_charm_frontiers=[dict(event_id=[1,1,1],parents=[])]),
            decay_bank=dict(base_kernel='native_ordinary',size=128,control_fraction=.1),
            publication_directory='/eos/user/j/jniedzie/shift_cmssw_support/synthetic')
        validate(request)
        ordinary=copy.deepcopy(request);ordinary.pop('decay_bank');ordinary['arm']='ordinary';validate(ordinary)
        for mutation in ('missing_census','old_profile','implicit_native_bank','bank_seed_overflow'):
            changed=copy.deepcopy(request)
            if mutation=='missing_census':changed['original_source'].pop('deferred_charm_frontiers')
            if mutation=='old_profile':changed['original_source']['generator_profile']='ordinary'
            if mutation=='implicit_native_bank':changed['decay_bank'].pop('base_kernel')
            if mutation=='bank_seed_overflow':changed['decay_seed']=899999922
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):validate(changed)

    def test_raw_source_opt_in_appends_only_D411_D421_and_keeps_the_fragment_bytes(self):
        import os
        import run_shift_smart_sm_source as worker
        from audit_shift_weighted_gen import DEFERRED_CHARM_PROFILE
        fragment=Path(worker.TOOLS)/'fragments/QCD_SoftMpiPartition_FixedTarget_13p6TeV_pythia8_cff.py'
        before=fragment.read_bytes()
        row=dict(family='qcd',bounds=[20.,-1.],events_or_trials=10,seed=71,run=30000001,
            generator_profile=DEFERRED_CHARM_PROFILE)
        request=dict(source_request=row,generate_only=True)
        calls=[]
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            def run(argv,log,**kwargs):
                calls.append(argv);cwd=kwargs.get('cwd',root)
                if argv[0]=='cmsDriver.py':(cwd/'gen_cfg.py').write_text('# native driver fixture\n')
                if argv[0]=='edmConfigDump':Path(log).write_text((cwd/'gen_cfg.py').read_text())
                if argv[0]=='python3':(cwd/'validation.json').write_text('{}')
            with patch.dict(os.environ,{'CMSSW_BASE':str(root/'private_release')}), \
                 patch.object(worker,'run',side_effect=run), \
                 patch.object(worker,'inspect_source',return_value='inspected'):
                self.assertEqual(worker.generate(request,root/'source'),'inspected')
            config=(root/'source/gen_cfg.py').read_text()
            self.assertEqual(config.count('411:mayDecay = off'),1)
            self.assertEqual(config.count('421:mayDecay = off'),1)
            self.assertNotIn('431:mayDecay',config)
            self.assertIn(DEFERRED_CHARM_PROFILE,calls[-1])
            for mutation in ('DY','zero_bin','production'):
                changed=copy.deepcopy(request)
                if mutation=='DY':changed['source_request']['family']='dy'
                if mutation=='zero_bin':changed['source_request']['bounds']=[0.,1.]
                if mutation=='production':changed['generate_only']=False
                with self.subTest(mutation=mutation),self.assertRaisesRegex(ValueError,'GEN-only pilot'):
                    worker.generate(changed,root/'rejected')
            self.assertFalse((root/'rejected').exists())
        self.assertEqual(fragment.read_bytes(),before)

    def test_frozen_tools_include_and_import_the_matching_native_bank_dependencies(self):
        import io
        import subprocess
        import tarfile
        from prepare_shift_sm_decay_validation import freeze_tools
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);original=root/'base.tar.gz'
            with tarfile.open(original,'w:gz') as archive:
                for source in (Path(__file__).resolve().parents[1]/'scripts').glob('*.py'):
                    archive.add(source,arcname='scripts/'+source.name)
                # The existing pilot capsule installs the analysis weight
                # helper in scripts; reproduce that qualified base layout.
                weight_helper=Path(__file__).resolve().parents[2]/'tea_shift_cmssw/utils/shift_event_weights.py'
                archive.add(weight_helper,arcname='scripts/shift_event_weights.py')
            bootstrap=root/'bootstrap.sh';bootstrap.write_text('#!/bin/sh\n')
            output=root/'frozen';output.mkdir()
            config=dict(base_tools=dict(path=str(original),sha256=prep.sha(original)),
                bootstrap=dict(path=str(bootstrap),sha256=prep.sha(bootstrap)))
            receipt=freeze_tools(config,output)
            with tarfile.open(receipt['capsule']) as archive:
                for name in ['audit_shift_weighted_gen.py','shift_first_mpi_source_proposal.py',
                             'run_shift_smart_sm_source.py','run_shift_sm_decay_pilot.py','shift_sm_decay_bank.py',
                             'shift_production_selection.py','run_shift_gen_to_nano.py','run_shift_sm_postprocessing.py']:
                    raw=archive.extractfile('scripts/'+name).read()
                    self.assertEqual(raw,(Path(__file__).resolve().parents[1]/'scripts'/name).read_bytes())
                archive.extractall(root/'extracted')
            code=('import sys; sys.path.insert(0,'+repr(str(root/'extracted/scripts'))+'); '
                  'import audit_shift_weighted_gen, shift_first_mpi_source_proposal, '
                  'run_shift_sm_decay_pilot, run_shift_sm_decay_collection, '
                  'shift_sm_decay_bank, run_shift_smart_sm_source; '
                  'assert shift_sm_decay_bank.validate_settings(dict(size=128,control_fraction=.1,base_kernel="native_ordinary"))=="native_ordinary"')
            imported=subprocess.run([sys.executable,'-I','-c',code],capture_output=True,text=True)
            self.assertEqual(imported.returncode,0,imported.stderr)


class PersistenceRoundoffTests(unittest.TestCase):
    def test_failed_photon_and_massive_signed_and_subnormal_cases(self):
        photon=[-.08279411007317763,-.27006711821923624,-.8492992675623033,.8950420989472605]
        result=prep.mass_shell_roundoff(photon,1.932630233625332e-8,.8950421263950256,
                                       .8950421263950255,.8950421263950255**2)
        self.assertLessEqual(result['signed_mass2_bound_fraction'],1.)
        self.assertGreater(result['energy2_residual_GeV2'],0.)
        for raw,mass,energy,p2 in [([0.,0.,0.,2.],2.,2.,0.),
                ([2.,0.,0.,1.],prep.float32(-math.sqrt(3.)),1.,4.),
                ([0.,0.,0.,1.e-200],0.,0.,0.)]:
            with self.subTest(raw=raw):
                result=prep.mass_shell_roundoff(raw,mass,energy,energy,p2)
                self.assertLessEqual(result['signed_mass2_bound_fraction'],1.)
        with self.assertRaises(ValueError):
            prep.mass_shell_roundoff([0.,0.,0.,1.e-200],prep.MASS_FLOAT32_SUBNORMAL,0.,0.,0.)

    def test_negative_z_cancellation_contains_native_and_cling_but_rejects_wrong_direction(self):
        raw=[-.276547949024909,.501305280942911,-3577.858347066592,3577.858392874128]
        low,high=prep.eta_encoding_interval(raw)
        for eta in (-9.433364868164062,-9.433363914489746):
            self.assertLessEqual(low,eta);self.assertLessEqual(eta,high)
        self.assertLess(high-low,2.e-6)
        self.assertFalse(low<=prep.float32(-9.4333)<=high)
        for raw in ([3.,4.,5.,8.],[3.,4.,-5.,8.],[3.,4.,0.,8.]):
            low,high=prep.eta_encoding_interval(raw)
            zs=raw[2]/math.hypot(raw[0],raw[1])
            eta=prep.float32(math.log(zs+math.sqrt(zs*zs+1.)))
            self.assertLessEqual(low,eta);self.assertLessEqual(eta,high)
        for raw in ([0.,0.,1.,1.],[1.,0.,1.e8,1.e8]):
            with self.assertRaises(ValueError):prep.eta_encoding_interval(raw)

    def test_one_percent_mass_or_energy_corruption_is_rejected(self):
        with self.assertRaises(ValueError):prep.mass_shell_roundoff([0.,0.,0.,2.],prep.float32(2.02),2.,2.,0.)
        with self.assertRaises(ValueError):prep.mass_shell_roundoff([0.,0.,0.,2.],2.,2.02,2.,0.)
        with self.assertRaises(ValueError):prep.mass_shell_roundoff([0.,0.,0.,2.],2.,-2.,2.,0.)

    def test_graph_weight_corruption_still_fails_exactly(self):
        before=dict(vertices=[dict(position=[1.,2.,3.,0.])],anchor=[1.,2.,3.,0.],weights=[.1],particles=[1])
        after=copy.deepcopy(before);after['weights'][0]*=1.01
        with self.assertRaises(ValueError):prep.compare_graphs(before,after,[1.,2.,3.],0.)

    def test_truth_vertex_or_ancestry_corruption_still_fails_exactly(self):
        class Polar:
            def E(self):return math.sqrt(26.)
            def P2(self):return 25.
        class Ref:
            def key(self):return 0
        class Candidate:
            def pdgId(self):return 13
            def status(self):return 1
            def px(self):return 3.
            def py(self):return 0.
            def pz(self):return 4.
            def energy(self):return math.sqrt(26.)
            def pt(self):return 3.
            def eta(self):return math.asinh(4./3.)
            def phi(self):return 0.
            def mass(self):return 1.
            def polarP4(self):return Polar()
            def vx(self):return 1.+self.vertex_error
            def vy(self):return 2.
            def vz(self):return 3.
            def numberOfMothers(self):return int(self.link_error)
            def motherRef(self,i):return Ref()
            def numberOfDaughters(self):return 0
        timed=dict(particles=[dict(barcode=1,pdg=13,status=1,momentum=[3.,0.,4.,math.sqrt(26.)],production_vertex=-1,end_vertex=None)],
                   vertices=[dict(barcode=-1,position=[10.,20.,30.,0.],incoming=[],outgoing=[1])])
        expected={1:dict(momentum=[3.,0.,4.,math.sqrt(26.)],position=[1.,2.,3.],polar=[3.,math.asinh(4./3.),0.])}
        for vertex_error,link_error in [(0.,False),(1.,False),(0.,True)]:
            c=Candidate();c.vertex_error=vertex_error;c.link_error=link_error
            def product(event,label,kind):return None,([1] if kind=='std::vector<int>' else [c])
            with patch.object(prep,'product',side_effect=product),patch.object(prep,'encoded_expected_particles',return_value=(expected,{})),patch.object(prep,'identity',return_value=[1,1,1]):
                if vertex_error or link_error:
                    with self.assertRaises(ValueError):prep.audit_genparticles(None,timed)
                else:
                    result=prep.audit_genparticles(None,timed)
                    self.assertEqual(result['stock_persistence_audit_schema'],'shift-genparticles-conditioning-v3')
                    self.assertFalse(result['stock_persistence_encoding_exact'])
                    self.assertTrue(result['mass_shell_roundoff_checks_passed'])


if __name__=='__main__':unittest.main()
