# Code style: AI-code. AI-generated neutral semileptonic-kaon physics controls.
import json
import math
import os
import random
import statistics
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from dark_photon_kaon_semileptonic import (CHIRAL_LEFT,CollierLoops,GAMMA,IDENTITY,
    LOOP_FACTOR,METRIC,NeutralCurrentEvaluator,REFERENCE,build_collier_adapter,
    chiral_parameters,current_operators,dot,leading_current,leading_charged_current,neutral_contact_tensors,
    neutral_em_corrections,phase_space_density,polarization_basis,rest_kinematics,
    slash,spin_density,weak_vertex,uniform_production_point,weighted_production_point,
    sample_weighted_rest_decay)
from dark_photon_meson import MUON_MASS_GEV,boost


CARD=Path(__file__).resolve().parents[1]/'config/physics/dark_photon_kl3_chpt_2008.json'


def model(lepton='electron'):
    card=json.loads(CARD.read_text())
    return dict(card['form_factor_model'],lepton_mass_gev=card['explicit_conventions'][lepton+'_mass_gev'])


def point(card,mass,*,first_fraction=.4,weak_fraction=.3):
    M,pi,ml=[card[name] for name in ('parent_mass_gev','pion_mass_gev','lepton_mass_gev')]
    gap=M-pi-ml-mass
    return rest_kinematics(M,pi,ml,mass,(pi+mass+first_fraction*gap)**2,
        (ml+weak_fraction*gap)**2,.31,-.27,1.1)


class LeadingSemileptonicCurrent(unittest.TestCase):
    def test_charged_initial_leg_current_ward_soft_limit_and_explicit_order_guard(self):
        card=dict(model(),kind='chpt-p2-charged-kaon-semileptonic-control',
            parent_mass_gev=.493677,pion_mass_gev=.1349768)
        for lepton,masses in [('electron',[.22,.25,.3]),('muon',[.22,.25])]:
            card['lepton_mass_gev']=model(lepton)['lepton_mass_gev']
            for mass in masses:
                p,pi,l,nu,q=point(card,mass)
                current=leading_charged_current(p,pi,l,nu,q)
                self.assertLess(float(np.max(np.abs(np.einsum('r,r,rij->ij',METRIC,q,current)))),2.e-14)
                rho=spin_density(p,pi,l,nu,q,card,order='p2-control',epsilon_em=.001)['total']
                self.assertGreater(np.trace(rho).real,0.)
                with self.assertRaisesRegex(ValueError,'not implemented'):
                    current_operators(p,pi,l,nu,q,card,order='p2-plus-p4')
        card['lepton_mass_gev']=model()['lepton_mass_gev'];errors=[]
        for scale in (.01,.001,.0001):
            p,pi,l,nu,q=rest_kinematics(card['parent_mass_gev'],card['pion_mass_gev'],
                card['lepton_mass_gev'],.22*scale,(card['pion_mass_gev']+.4*scale)**2,.04,.31,-.27,1.1)
            state=polarization_basis(q)[0]
            actual=np.einsum('r,r,rij->ij',state,METRIC,leading_charged_current(p,pi,l,nu,q))
            expected=slash(p+pi)@CHIRAL_LEFT*(dot(l,state)/dot(l,q)-dot(p,state)/dot(p,q))/math.sqrt(2)
            errors.append(float(np.max(np.abs(actual-expected)))/float(np.max(np.abs(expected))))
        self.assertLess(errors[1],errors[0]/5)
        self.assertLess(errors[2],errors[1]/5)

    def test_light_weak_pair_keeps_conservation_without_recovering_gamma_from_beta(self):
        card=model();M,pi,ml=[card[name] for name in ('parent_mass_gev','pion_mass_gev','lepton_mass_gev')]
        for pair_mass in (ml*(1+1.e-6),ml*1.01,.003):
            for angle in (-.999,.31,.999):
                p,pion,l,nu,q=rest_kinematics(M,pi,ml,.0003,.24**2,pair_mass**2,
                    angle,-.27,1.1)
                self.assertLess(float(np.max(np.abs(p-pion-l-nu-q))),128*math.ulp(1.))
                # The strict current admission check remains unchanged.
                current=leading_current(p,pion,l,nu,q)
                self.assertTrue(np.all(np.isfinite(current)))

    def test_four_body_shells_vertices_and_full_matrix_ward_identity(self):
        for lepton,masses in [('electron',[.22,.25,.3]),('muon',[.22,.25])]:
            card=model(lepton)
            for mass in masses:
                rest=point(card,mass)
                for beta in [(0.,0.,0.),(.2,-.3,.7)]:
                    p,pi,l,nu,q=[np.asarray(boost(v,beta)) for v in rest]
                    np.testing.assert_allclose(p,pi+l+nu+q,rtol=2.e-14,atol=2.e-14)
                    for vector,target in [(p,card['parent_mass_gev']),
                            (pi,card['pion_mass_gev']),(l,card['lepton_mass_gev']),
                            (nu,0.),(q,mass)]:
                        self.assertAlmostEqual(float(dot(vector,vector)),target**2,delta=2.e-14)
                    current=leading_current(p,pi,l,nu,q)
                    ward=np.einsum('r,r,rij->ij',METRIC,q,current)
                    self.assertLess(float(np.max(np.abs(ward))),2.e-14)

    def test_low_soft_limit_contains_both_charged_legs_and_contact(self):
        card=model();M,pi,ml=[card[name] for name in ('parent_mass_gev','pion_mass_gev','lepton_mass_gev')]
        errors=[]
        for scale in [.01,.001,.0001]:
            p,pion,l,nu,q=rest_kinematics(M,pi,ml,.22*scale,
                (pi+.4*scale)**2,.04,.31,-.27,1.1)
            state=polarization_basis(q)[0]
            actual=np.einsum('r,r,rij->ij',state,METRIC,leading_current(p,pion,l,nu,q))
            expected=slash(p+pion)@CHIRAL_LEFT*(dot(l,state)/dot(l,q)-dot(pion,state)/dot(pion,q))
            errors.append(float(np.max(np.abs(actual-expected)))/float(np.max(np.abs(expected))))
        self.assertLess(errors[1],errors[0]/5)
        self.assertLess(errors[2],errors[1]/5)

    def test_positive_spin_density_lorentz_transport_and_epsilon_power(self):
        card=model();rest=point(card,.25);states=polarization_basis(rest[-1])
        first=spin_density(*rest,card,order='p2-control',epsilon_em=.001,polarizations=states)['total']
        self.assertGreaterEqual(float(np.linalg.eigvalsh(first).min()),-1.e-30)
        self.assertGreater(float(first[2,2].real),0.)
        second=spin_density(*rest,card,order='p2-control',epsilon_em=.002,polarizations=states)['total']
        np.testing.assert_allclose(second,4*first,rtol=1.e-14,atol=1.e-40)
        beta=(.23,-.15,.65)
        moved=[boost(v,beta) for v in rest];basis=[boost(v,beta) for v in states]
        actual=spin_density(*moved,card,order='p2-control',epsilon_em=.001,polarizations=basis)['total']
        np.testing.assert_allclose(actual,first,rtol=2.e-12,atol=1.e-32)

    def test_absolute_four_body_phase_space_matches_massless_analytic_integral(self):
        nodes,weights=np.polynomial.legendre.leggauss(60)
        nodes=(nodes+1)/2;weights=weights/2
        M=1.;value=0.
        for first,w1 in zip(nodes,weights):
            for fraction,w2 in zip(nodes,weights):
                second=(M-first)*fraction
                jacobian=4*first*second*(M-first)
                value+=w1*w2*jacobian*8*math.pi*phase_space_density(M,0.,0.,0.,first**2,second**2)
        expected=M**3/(49152*math.pi**5)
        self.assertAlmostEqual(value/expected,1.,delta=3.e-6)


class WeightedSemileptonicDecay(unittest.TestCase):
    def test_conversion_factor_matches_explicit_pair_spin_trace_and_two_body_phase_space(self):
        # This checks the absolute alpha/(3*pi*q²) conversion independently
        # of any integrated rate quoted in TableII. Sum actual pair angles,
        # spin projectors and dPhi2*dq²/(2*pi), keeping all three DP states.
        card=model('muon');epsilon=.001;pair_lepton=.000511
        nodes,weights=np.polynomial.legendre.leggauss(6)
        for mass in (.003,.03,.22):
            p,pion,l,nu,q=point(card,mass)
            operators=leading_current(p,pion,l,nu,q)
            conjugates=np.array([GAMMA[0]@a.conjugate().T@GAMMA[0] for a in operators])
            coefficient=4*math.pi*card['alpha_em']*card['fermi_constant_gev_minus2']**2*card['v_us']**2/2
            hadron=coefficient*np.einsum('ij,mjk,kl,nli->mn',slash(nu),operators,
                slash(l)-card['lepton_mass_gev']*IDENTITY,conjugates)
            momentum=math.sqrt((mass/2-pair_lepton)*(mass/2+pair_lepton))
            integral=0.
            for cosine,weight in zip(nodes,weights):
                for phi in np.arange(12)*2*math.pi/12:
                    spatial=momentum*np.array([math.sqrt(1-cosine*cosine)*math.cos(phi),
                        math.sqrt(1-cosine*cosine)*math.sin(phi),cosine])
                    minus,plus=[np.array(boost((mass/2,*(sign*spatial)),q[1:]/q[0]))
                        for sign in (1.,-1.)]
                    pair=np.einsum('ij,mjk,kl,nli->mn',slash(minus)+pair_lepton*IDENTITY,
                        GAMMA,slash(plus)-pair_lepton*IDENTITY,GAMMA)
                    square=np.einsum('mn,m,n,mn',hadron,METRIC,METRIC,pair).real
                    # Pair e²/q^4, dPhi2=p/(16*pi²*m)dOmega and dq²/(2*pi).
                    integral+=square*(4*math.pi*card['alpha_em']/mass**4)*momentum/(
                        16*math.pi**2*mass)*weight*(2*math.pi/12)/(2*math.pi)
            rho=spin_density(p,pion,l,nu,q,card,order='p2-control',epsilon_em=epsilon)['total']
            r=(pair_lepton/mass)**2
            converted=np.trace(rho).real/epsilon**2*card['alpha_em']/(
                3*math.pi*mass**2)*math.sqrt(1-4*r)*(1+2*r)
            self.assertAlmostEqual(integral/converted,1.,delta=2.e-11)

    def test_uniform_proposal_has_the_absolute_massless_four_body_volume(self):
        card=dict(model(),parent_mass_gev=1.,pion_mass_gev=1.e-6,lepton_mass_gev=0.)
        nodes,weights=np.polynomial.legendre.leggauss(64)
        integral=0.
        for a,wa in zip((nodes+1)/2,weights/2):
            for b,wb in zip((nodes+1)/2,weights/2):
                row=uniform_production_point(card,1.e-6,(a,b,.31,.42,.7))
                integral+=wa*wb*row['phase_space_weight']
        self.assertAlmostEqual(integral/(1/(49152*math.pi**5)),1.,delta=3.e-6)

    def test_actual_correlated_decay_preserves_shells_flight_weight_and_helicity_moment(self):
        rng=random.Random(824813);card=model();mass=.25
        residuals=[];proper=[]
        beta2=1-4*(MUON_MASS_GEV/mass)**2
        for _ in range(1200):
            row=sample_weighted_rest_decay(card,mass,200.,rng,
                order='p2-control',epsilon_em=.001)
            vectors=[np.asarray(row[k]) for k in
                ('pion_p4','charged_lepton_p4','neutrino_p4','dark_photon_p4')]
            np.testing.assert_allclose(sum(vectors),row['parent_p4'],atol=3.e-14,rtol=3.e-14)
            for v,m in zip(vectors,(card['pion_mass_gev'],card['lepton_mass_gev'],0.,mass)):
                self.assertAlmostEqual(float(dot(v,v)),m*m,delta=2.e-14)
            dark=vectors[-1];mu=np.asarray(row['muon_p4']);anti=np.asarray(row['antimuon_p4'])
            np.testing.assert_allclose(mu+anti,dark,atol=3.e-14,rtol=3.e-14)
            self.assertAlmostEqual(float(dot(mu,mu)),MUON_MASS_GEV**2,delta=2.e-14)
            self.assertAlmostEqual(float(dot(anti,anti)),MUON_MASS_GEV**2,delta=2.e-14)
            rho=np.array(row['density_matrix_real'])+1j*np.array(row['density_matrix_imaginary'])
            trace=float(np.trace(rho).real)
            expected=(trace/3-beta2*(trace+2*rho[2,2].real)/15)/((1-beta2/3)*trace)
            rest=boost(mu,tuple(-x/dark[0] for x in dark[1:]))
            cosine=float(np.dot(rest[1:],dark[1:])/(np.linalg.norm(rest[1:])*np.linalg.norm(dark[1:])))
            residuals.append(cosine*cosine-expected)
            proper.append(row['proper_decay_length_mm'])
            self.assertGreater(row['width_weight_gev'],0.)
            self.assertFalse(row['dimuon_branching_included'])
            self.assertFalse(row['parent_flight_included'])
            self.assertFalse(row['production_qualified'])
            self.assertEqual(row['decay_ct_mm'],proper[-1]*dark[0]/mass)
            self.assertEqual(row['decay_displacement_mm'],tuple(proper[-1]*x/mass for x in dark[1:]))
        self.assertAlmostEqual(statistics.mean(residuals),0.,
            delta=5*statistics.stdev(residuals)/math.sqrt(len(residuals)))
        self.assertAlmostEqual(statistics.mean(proper),200.,delta=6*200/math.sqrt(len(proper)))

    def test_proposal_weight_has_exact_epsilon_squared_and_is_not_a_unit_event(self):
        card=model('muon');coordinates=(.3,.4,.2,.7,.45)
        first=weighted_production_point(card,.22,coordinates,order='p2-control',epsilon_em=.001)
        second=weighted_production_point(card,.22,coordinates,order='p2-control',epsilon_em=.002)
        self.assertEqual(second['width_weight_gev']/first['width_weight_gev'],4.)
        self.assertFalse(first['production_qualified'])
        rng=random.Random(2819)
        first=sample_weighted_rest_decay(card,.22,0.,rng,order='p2-control',epsilon_em=.001)
        self.assertEqual(first['proper_decay_length_mm'],0.)
        self.assertIn('Nonunit',first['output_gen_weight_convention'])
        for mass,length in [(2*MUON_MASS_GEV,1.),(.4,1.),(.22,-1.)]:
            before=rng.getstate()
            with self.assertRaises(ValueError):
                sample_weighted_rest_decay(card,mass,length,rng,order='p2-control',epsilon_em=.001)
            self.assertEqual(before,rng.getstate())


@unittest.skipUnless(os.environ.get('CMSSW_BASE') and shutil.which('scram'),
    'Requires the qualified CMSSW Collier/Fortran runtime')
class NeutralSemileptonicLoopCurrent(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory(prefix='shift-kl3-loop-')
        cls.addClassCleanup(cls.temporary.cleanup)
        description=subprocess.check_output(['scram','tool','info','collier'],
            cwd=Path(os.environ['CMSSW_BASE'])/'src',text=True)
        bases=[row.split('=',1)[1] for row in description.splitlines() if row.startswith('COLLIER_BASE=')]
        if len(bases)!=1:
            raise RuntimeError('Ambiguous selected Collier tool')
        cls.path=build_collier_adapter(cls.temporary.name,collier_base=bases[0],compiler=shutil.which('gfortran'))
        cls.loops=CollierLoops(cls.path)
        cls.evaluator=NeutralCurrentEvaluator(cls.path)

    def test_full_current_weighted_sample_uses_the_qualified_density(self):
        rng=random.Random(63276)
        for lepton,masses in [('electron',[.22,.25,.3]),('muon',[.22,.25])]:
            card=model(lepton)
            for mass in masses:
                for _ in range(3):
                    row=sample_weighted_rest_decay(card,mass,100.,rng,self.evaluator,
                        order='p2-plus-p4',epsilon_em=.001)
                    self.assertGreater(row['width_weight_gev'],0.)
                    self.assertFalse(row['production_qualified'])
                    rho=np.array(row['density_matrix_real'])+1j*np.array(row['density_matrix_imaginary'])
                    self.assertGreaterEqual(float(np.linalg.eigvalsh(rho).min()),
                        -128*np.finfo(float).eps*float(np.trace(rho).real))

    def test_independent_feynman_parameter_a_b_and_all_c_tensor_coefficients(self):
        loops=self.loops;scale2=loops.scale**2
        m1,m2,m3=.2,.2,.3
        p,pi,l,nu,q=point(model(),.22)
        q2,w2,Q2=[float(dot(v,v)) for v in (q,l+nu,q+l+nu)]
        # A direct two-dimensional integration has no tensor reduction or
        # library denominator convention to copy. This point is below cuts.
        nodes,weights=np.polynomial.legendre.leggauss(80);nodes=(nodes+1)/2;weights=weights/2
        reference=np.zeros(12,dtype=complex)
        for x,wx in zip(nodes,weights):
            for f,wf in zip(nodes,weights):
                y=(1-x)*f;weight=wx*wf*(1-x)
                D=(1-x-y)*m1+x*m2+y*m3-x*(1-x-y)*q2-y*(1-x-y)*Q2-x*y*w2
                self.assertGreater(D,0.)
                scalar=-1/(LOOP_FACTOR*D);metric=-(1+math.log(D/scale2))/(2*LOOP_FACTOR)
                reference+=weight*np.array([scalar,x*scalar,y*scalar,metric,x*x*scalar,x*y*scalar,
                    y*y*scalar,x*metric,y*metric,x*x*y*scalar,x*y*y*scalar,y**3*scalar])
        values=loops.c(m1,m2,m3,q2,w2,Q2)
        np.testing.assert_allclose(list(values.values()),reference,rtol=5.e-8,atol=2.e-14)
        self.assertAlmostEqual(loops.a(m1).real,-m1*math.log(m1/scale2)/LOOP_FACTOR,places=15)
        D=(1-nodes)*m1+nodes*m3-nodes*(1-nodes)*w2
        log=np.log(D/scale2)
        expected=[-np.dot(weights,1+log)/LOOP_FACTOR,-np.dot(weights,nodes*(1+log))/LOOP_FACTOR,
            -np.dot(weights,nodes**2*(1+log))/LOOP_FACTOR,-np.dot(weights,D*log)/(2*LOOP_FACTOR)]
        np.testing.assert_allclose(list(loops.b(m1,m3,w2).values()),expected,rtol=5.e-10,atol=2.e-14)

    def test_fplus_zero_matches_closed_ademollo_gatto_mass_function(self):
        # Closed mass/log expression for the O(p4) correction at t=0,
        # independently of the tensor integral backend. The correction has
        # no LEC and vanishes in the SU(3) equal-mass limit.
        for M,pi in [(.49767,.13957),(.48,.16),(.2,.2)]:
            card=dict(model(),parent_mass_gev=M,pion_mass_gev=pi)
            eta=math.sqrt((4*M*M-pi*pi)/3);f=card['fpi_gev']
            def H(a,b):
                if a==b:return 0.
                return -(a*a+b*b+2*a*a*b*b/(a*a-b*b)*math.log(b*b/(a*a)))/(128*math.pi**2*f*f)
            expected=1+1.5*(H(M,pi)+H(M,eta))
            p=np.array([M,0.,0.,0.]);momentum=(M*M-pi*pi)/(2*M)
            pion=np.array([(M*M+pi*pi)/(2*M),0.,0.,momentum])
            actual=1+weak_vertex(p+pion,p-pion,card,self.loops)[0]
            self.assertAlmostEqual(actual.real,expected,delta=1.e-12)
            self.assertLess(abs(actual.imag),1.e-14)

    def test_g2_matches_independent_onshell_bijnens_talavera_formula(self):
        card=model();loops=self.loops;par=chiral_parameters(card,loops)
        k,p,e,f=[par[name] for name in ('kaon2','pion2','eta2','fpi_gev')]
        for t in [.02,.05,.09]:
            pi_energy=(k+p-t)/(2*math.sqrt(k));pi_momentum=math.sqrt(pi_energy**2-p)
            parent=np.array([math.sqrt(k),0.,0.,0.]);pion=np.array([pi_energy,0.,0.,pi_momentum])
            actual=weak_vertex(parent+pion,parent-pion,card,loops)[1]
            pk,ke=loops.b(p,k,t),loops.b(k,e,t)
            expected=(4*(k-p)*par['l5']-2*(k-p)*par['l9']+loops.a(e)/2-5*loops.a(p)/12+7*loops.a(k)/12+
                pk['B']*(-p/12-5*k/12+5*t/12)+ke['B']*(p/12-7*k/12+t/4)+
                pk['B1']*(-7*p/12+19*k/12-5*t/12)+ke['B1']*(-11*p/12+23*k/12-t/4)+
                pk['B21']*(3*(p-k)/2-5*t/6)+ke['B21']*(3*(p-k)/2-t/2)-
                5*pk['B22']/6-ke['B22']/2)/f**2
            self.assertAlmostEqual(abs(actual-expected),0.,delta=3.e-15)

    def test_raw_contact_limit_requires_the_derived_mass_reversal(self):
        card=model();backend=self.evaluator.coli
        remainders=[];printed_remainders=[]
        for mass in [.003,.0003]:
            p,pi,l,nu,q=point(card,mass)
            gc=weak_vertex(p+pi,p-pi,card,backend);gd=weak_vertex(p+pi+q,p-pi-q,card,backend)
            target=gd[0]*(p+pi+q)+gd[1]*(p-pi-q)-gc[0]*(p+pi)-gc[1]*(p-pi)
            corrected=neutral_contact_tensors(p,pi,q,card,backend)['total']
            printed=neutral_contact_tensors(p,pi,q,dict(card,mixed_mass_b1_routing='printed-equation-30'),backend)['total']
            remainders.append(float(np.max(np.abs(METRIC*q@corrected-target))))
            printed_remainders.append(float(np.max(np.abs(METRIC*q@printed-target))))
        self.assertLess(remainders[1],remainders[0]/80)
        self.assertGreater(printed_remainders[1],printed_remainders[0]/2)
        with self.assertRaisesRegex(ValueError,'finite Ward defect'):
            current_operators(*point(card,.22),dict(card,mixed_mass_b1_routing='printed-equation-30'),
                backend,order='p2-plus-p4')

    def test_finite_current_ward_uv_scale_and_longitudinal_real_photon_limit(self):
        card=model();low=[];transverse=[]
        for mass in [.22,.25,.3,.03,.003,.0003]:
            coordinates=point(card,mass)
            result=self.evaluator.density(*coordinates,card,epsilon_em=.001)
            density=result['densities']['total']
            np.testing.assert_allclose(density,density.conjugate().T,atol=1.e-32)
            self.assertGreaterEqual(float(np.linalg.eigvalsh(density).min()),-1.e-30)
            for current in current_operators(*coordinates,card,self.evaluator.coli,order='p2-plus-p4').values():
                ward=np.einsum('r,r,rij->ij',METRIC,coordinates[-1],current)
                self.assertLess(float(np.max(np.abs(ward))),2.e-13)
            if mass<.04:
                low.append(float(density[2,2].real));transverse.append(float(density[0,0].real+density[1,1].real))
        self.assertLess(low[1]/transverse[1],low[0]/transverse[0]/50)
        self.assertLess(low[2]/transverse[2],low[1]/transverse[1]/80)
        coordinates=point(card,.22)
        normal=self.evaluator.density(*coordinates,card,epsilon_em=.001)['densities']['total']
        uv=NeutralCurrentEvaluator(self.path,lambda0=3.).density(*coordinates,card,epsilon_em=.001)['densities']['total']
        scale=NeutralCurrentEvaluator(self.path,scale_gev=1.).density(*coordinates,card,epsilon_em=.001)['densities']['total']
        np.testing.assert_allclose(uv,normal,rtol=2.e-8,atol=1.e-31)
        np.testing.assert_allclose(scale,normal,rtol=2.e-8,atol=1.e-31)

    def test_flagged_tensor_requires_both_algorithms_physical_matrix_and_scale(self):
        card=model();coordinates=point(card,.3)
        first=self.evaluator.density(*coordinates,card,epsilon_em=.001)
        report=first['numerical_validation']
        self.assertEqual(report['method'],'COLI-DD-physical-current-and-scale-comparison')
        self.assertTrue(report['tensor_accuracy_flags'])
        self.assertTrue(all(row['error']==0 and row['accuracy']==-1 for row in report['tensor_accuracy_flags']))
        self.assertLess(report['relative_operator_disagreement'],report['physical_matrix_target'])
        self.assertLess(report['relative_density_disagreement'],report['physical_matrix_target'])
        # Cached loop values must retain their original warning, rather than
        # turning the same point into an apparently unflagged success.
        second=self.evaluator.density(*coordinates,card,epsilon_em=.001)
        self.assertEqual(second['numerical_validation']['tensor_accuracy_flags'],report['tensor_accuracy_flags'])
        np.testing.assert_array_equal(second['densities']['total'],first['densities']['total'])


if __name__=='__main__':
    unittest.main()
