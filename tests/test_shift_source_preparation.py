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
        value=plan();unit=value['units'][0]
        for mutation in (None,'weight','prefix','rawsha','missing'):
            with self.subTest(mutation=mutation),tempfile.TemporaryDirectory() as directory:
                path=Path(directory);output=path/'complete.root';output.write_bytes(b'synthetic')
                def scanner(command,**kwargs):
                    self.assertNotIn('--maximum-events-per-source',command)
                    data=dict(events=[dict(event_id=x,native_weight=w,original_source_sha256='a'*64,
                        zero_GEN_muon_control=True) for x,w in zip(unit['event_ids'],unit['native_weights'])],
                        sources=[dict(sha256=prep.sha(output))],maximum_events_per_source=None,
                        prefix_requested=False,summary=dict(source_read_counts=[dict(entire_input_file_read=True)]))
                    if mutation=='weight':data['events'][0]['native_weight']*=2
                    if mutation=='prefix':data['prefix_requested']=True
                    if mutation=='rawsha':data['events'][0]['original_source_sha256']='b'*64
                    if mutation=='missing':data['events'].pop()
                    with gzip.open(command[-1],'wt') as stream:json.dump(data,stream)
                with patch.object(prep.subprocess,'run',side_effect=scanner):
                    if mutation is None:
                        features,audit=prep.feature_census(path,unit,value,output)
                        self.assertTrue(audit['zero_muon_controls_retained'])
                        self.assertEqual(audit['events'],3)
                        self.assertFalse(audit['usable_as_physics_weight'])
                    else:
                        with self.assertRaises(ValueError):prep.feature_census(path,unit,value,output)


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
                    self.assertEqual(result['stock_persistence_audit_schema'],'shift-genparticles-conditioning-v2')
                    self.assertFalse(result['stock_persistence_encoding_exact'])
                    self.assertTrue(result['mass_shell_roundoff_checks_passed'])


if __name__=='__main__':unittest.main()
