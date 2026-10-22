# Code style: AI-code. AI-generated independent scalar-loop/current controls.
import copy
import json
import math
from pathlib import Path
import sys
import unittest

import numpy as np

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from dark_photon_double_radiative import _dot,polarizations
from dark_photon_meson import boost
from dark_photon_neutral_vector_dipion import (
    amplitude_components,decay_width,differential_width,loop_polarized_amplitude,
    real_photon_loop,reference_model,rescattering_amplitude,rest_kinematics,
    scalar_loop_form_factors,sequential_amplitude)

CARD=ROOT/'config/physics/dark_photon_neutral_vector_dipion_2001.json'


def inputs():return json.loads(CARD.read_text())


def direct_triangle(P,Q,s,mass,order=56):
    """Independent real subthreshold 2D integral: no analytic inner logs."""
    nodes,weights=np.polynomial.legendre.leggauss(order);result=np.zeros(5)
    for node,weight in zip(nodes,weights):
        x=(node+1)/2
        for other,angular in zip(nodes,weights):
            y=(other+1)/2*(1-x);z=1-x-y
            denominator=mass**2-x*z*P-y*z*Q-x*y*s
            result+=weight*angular*(1-x)/4*np.array([
                x*y/denominator,x*(1-2*x)/denominator,y*(1-2*y)/denominator,
                (1-2*x)*(2*y-1)/denominator,math.log(denominator)])
    bubble=math.fsum(weight/2*math.log(mass**2-(node+1)/2*(1-(node+1)/2)*s)
        for node,weight in zip(nodes,weights))
    return result,bubble


class ScalarLoopTest(unittest.TestCase):
    def test_independent_subthreshold_triangle_and_seagull_cancel_ward_terms(self):
        P,Q,s,mass=.77**2,.22**2,.3**2,.5
        integral,bubble=direct_triangle(P,Q,s,mass)
        factors=scalar_loop_form_factors(P,Q,s,mass,order=56)
        self.assertAlmostEqual(factors['f1'].real,4*integral[0],places=12)
        self.assertAlmostEqual(factors['f2'].real,-2*integral[1]/Q,places=11)
        self.assertAlmostEqual(factors['metric_from_triangle_and_seagull'].real,bubble-2*integral[4],places=12)
        expected_metric=factors['f1']*(P+Q-s)/2+factors['f2']*P*Q
        self.assertLess(abs(factors['metric_from_triangle_and_seagull']-expected_metric),1.e-11)
        # The remaining nonmetric coefficients are independently integrated;
        # these equalities would fail if a triangle/seagull term were lost.
        self.assertAlmostEqual(-2*integral[2]/P,factors['f2'].real,places=11)
        self.assertAlmostEqual(-integral[3]/((s-P-Q)/2),factors['f2'].real,places=11)

    def test_physical_cut_metric_identity_and_published_photon_complex_loop(self):
        P=.77**2;mass=.13957
        for s in (.075,.09,.16,.3,.5):
            photon=scalar_loop_form_factors(P,0.,s,mass,order=128)
            expected=real_photon_loop(P,s,mass)
            self.assertLess(abs(photon['f1']*mass**2/4-expected),2.e-9)
            self.assertGreater(photon['f1'].imag,0.)
        for Q,s in ((.22**2,.09),(.3**2,.09),(.4**2,.08)):
            factors=scalar_loop_form_factors(P,Q,s,mass,order=128)
            metric=factors['f1']*(P+Q-s)/2+factors['f2']*P*Q
            self.assertLess(abs(factors['metric_from_triangle_and_seagull']-metric),5.e-7)
            self.assertLess(abs(factors['all_outgoing_pp_coefficient']+Q*factors['f2']),1.e-12)
            self.assertLess(abs(factors['all_outgoing_qq_coefficient']+P*factors['f2']),2.e-6)

    def test_second_tensor_has_a_finite_nonzero_photon_limit(self):
        P,s,mass=.77**2,.3**2,.5
        nodes,weights=np.polynomial.legendre.leggauss(56)
        limits=[]
        for Q in (0.,.005**2,.05**2,.22**2):
            direct=0.
            for a,wa in zip(nodes,weights):
                x=(a+1)/2;L=1-x
                for b,wb in zip(nodes,weights):
                    u=(b+1)/2;H=P*(1-u)+s*u
                    first=mass**2-x*L*H-Q*L**2*u*(1-u)
                    second=mass**2-x*L*H-Q*x*x*u*(1-u)
                    direct-=wa*wb/4*x*L*(1-2*x)**2*u*(1-u)/(first*second)
            limits.append(direct)
            if Q:
                actual=scalar_loop_form_factors(P,Q,s,mass,order=96)['f2']
                self.assertLess(abs(actual/direct-1),1.e-8)
        self.assertGreater(abs(limits[0]),.01)
        self.assertLess(abs(limits[1]/limits[0]-1),1.e-4)
        self.assertIsNone(scalar_loop_form_factors(P,0.,s,mass)['f2'])

    def test_cutkosky_delta_integral_checks_new_timelike_cut_and_sign(self):
        # Im(1/(Delta-i0))=+pi*delta(Delta), independently of all complex
        # logarithm/root-sheet choices made by the production calculation.
        P,s,mu=.77**2,.3**2,.13957**2
        nodes,weights=np.polynomial.legendre.leggauss(128)
        for Q in (.25**2,.3**2,.4**2):
            edges={0.,1.}
            for invariant in (P,s):
                if invariant>4*mu:
                    edges.update(float(root.real) for root in np.roots([invariant,-invariant,mu]))
            for root in np.roots([(P+Q-s)**2-4*P*Q,2*Q*(P-Q+s),Q*Q-4*Q*mu]):
                if abs(root.imag)<1.e-15 and 0<root.real<1:edges.add(float(root.real))
            points=sorted(edges);xy=xx=0.
            for low,high in zip(points,points[1:]):
                for node,weight in zip(nodes,weights):
                    theta=(node+1)*math.pi/4;x=low+(high-low)*math.sin(theta)**2
                    jac=(high-low)*math.sin(2*theta)*math.pi/4
                    B=x*(P+Q-s)-Q;C=mu-x*(1-x)*P;D=B*B-4*Q*C
                    if D<=0:continue
                    for y in ((-B-math.sqrt(D))/(2*Q),(-B+math.sqrt(D))/(2*Q)):
                        if 0<y<1-x:
                            xy+=weight*jac*x*y/math.sqrt(D)
                            xx+=weight*jac*x*(1-2*x)/math.sqrt(D)
            factors=scalar_loop_form_factors(P,Q,s,math.sqrt(mu),order=128)
            self.assertLess(abs(factors['f1'].imag/(4*math.pi*xy)-1),1.e-8)
            self.assertLess(abs(factors['f2'].imag/(-2*math.pi*xx/Q)-1),1.e-8)


class NeutralVectorCurrentTest(unittest.TestCase):
    def test_bose_symmetry_and_both_ward_identities_survive_boosts(self):
        card=inputs()
        for pdg in (113,223):
            current=reference_model(card,pdg,scalar_model='sigma478_324')
            M=card['rho' if pdg==113 else 'omega']['mass_gev']
            for mass in (0.,.22,.4):
                vectors=rest_kinematics(M,.13498,mass,(4*.13498**2+(M-mass)**2)/2,.31)
                for beta in ((0.,0.,0.),(.2,-.1,.7)):
                    P,q,first,second=[boost(vector,beta) for vector in vectors]
                    ep=boost((0.,1.,0.,0.),beta)
                    eq=boost(polarizations(vectors[1],mass)[0],beta)
                    ordinary=amplitude_components(P,q,first,second,ep,eq,model=current)
                    swapped=amplitude_components(P,q,second,first,ep,eq,model=current)
                    for name in ordinary:self.assertLess(abs(ordinary[name]-swapped[name]),1.e-12)
                    for first_pol,second_pol in ((P,eq),(ep,q)):
                        ward=amplitude_components(P,q,first,second,first_pol,second_pol,model=current)
                        self.assertLess(max(abs(value) for value in ward.values()),1.e-11)

    def test_local_epsilon_vertices_match_the_independent_primary_vmd_tensor(self):
        P,q,first,second=rest_kinematics(.77,.13498,0.,.4**2,.3)
        ep=(0.,1.,0.,0.);eq=(0.,1.,0.,0.)
        pole=dict(mass_gev=.782,width_gev=.00849,width_prescription='constant-primary-2001',
            coupling_product_gev_inverse2=1.)
        result=0j
        for pion in (first,second):
            R=tuple(a+b for a,b in zip(q,pion));A=_dot(ep,eq)*_dot(P,q)-_dot(ep,q)*_dot(eq,P)
            B=(-_dot(ep,eq)*_dot(P,R)*_dot(q,R)-_dot(ep,R)*_dot(eq,R)*_dot(P,q)
                +_dot(ep,q)*_dot(eq,R)*_dot(P,R)+_dot(eq,P)*_dot(ep,R)*_dot(q,R))
            result+=(_dot(R,R)*A+B)/complex(.782**2-_dot(R,R),-.782*.00849)
        actual=sequential_amplitude(P,q,first,second,ep,eq,[pole])
        self.assertLess(abs(actual-result),1.e-14)

    def test_published_photon_spectrum_fixes_phase_space_and_identical_pion_factor(self):
        card=inputs();current=reference_model(card,113,scalar_model='chiral',include_kaon_loop=False)
        current['vector_exchanges']=[]
        M=.77;pion=.13498;charged=.13957;g=card['conventions']['g_primary'];f=.0924;alpha=card['conventions']['alpha_em']
        for s in (.08,.12,.25,.45):
            primary=(alpha/(192*math.pi**5)*g*g/(4*math.pi)*M**4/charged**4*math.sqrt(s)/M
                *(1-s/M**2)**3*math.sqrt(1-4*pion*pion/s)*abs(real_photon_loop(M*M,s,charged))**2
                *((s-charged**2)/f**2)**2)
            actual=4*math.sqrt(s)*differential_width(M,pion,0.,s,.2,model=current,loop_order=128)['total']
            self.assertLess(abs(actual/primary-1),5.e-7)

    def test_published_photon_widths_and_coherent_interference(self):
        card=inputs()
        chiral=reference_model(card,113,scalar_model='chiral',include_kaon_loop=False)
        scalar=reference_model(card,113,scalar_model='sigma478_324',include_kaon_loop=False)
        c=decay_width(.77,.13498,0.,model=chiral,epsilon_em=1.,order=24,loop_order=96)
        s=decay_width(.77,.13498,0.,model=scalar,epsilon_em=1.,order=24,loop_order=96)
        targets=card['photon_controls_gev']
        self.assertLess(abs(c['loop']/targets['rho_chiral_loop']-1),.003)
        self.assertLess(abs(c['vector']/targets['rho_local_vmd']-1),.04)
        self.assertLess(abs(s['total']/targets['rho_sigma478_324_plus_vmd']-1),.025)
        self.assertGreater(s['interference'],0.)
        self.assertGreater(s['total'],s['loop']+s['vector'])
        omega=reference_model(card,223,scalar_model='sigma478_324',include_kaon_loop=False,include_mixing=False)
        w=decay_width(.782,.13498,0.,model=omega,epsilon_em=1.,order=24,loop_order=64)
        self.assertLess(abs(w['vector']/targets['omega_running_rho_vmd']-1),.06)

    def test_massive_loop_second_tensor_changes_a_physical_longitudinal_amplitude(self):
        P,q,first,second=rest_kinematics(.77,.13498,.3,.3**2,.4)
        ep=(0.,0.,0.,1.);eq=polarizations(q,.3)[2]
        factors=scalar_loop_form_factors(.77**2,.3**2,.3**2,.13957,order=128)
        total=loop_polarized_amplitude(P,q,ep,eq,factors)
        missing=loop_polarized_amplitude(P,q,ep,eq,dict(factors,f2=0.))
        self.assertGreater(abs(total-missing),.1*abs(total))

    def test_explicit_scalar_choice_adler_zero_closed_channel_and_failed_physical_claim(self):
        card=inputs()
        for name in card['scalar_scenarios']:
            current=reference_model(card,113,scalar_model=name)
            self.assertEqual(rescattering_amplitude(.13957**2,current['charged_scalar_loops'][0]['rescattering']),0.)
            self.assertEqual(decay_width(.77,.13498,.6,model=current,epsilon_em=.001)['total'],0.)
        current['physical_production_ready']=True
        P,q,a,b=rest_kinematics(.77,.13498,.3,.3**2,.4)
        with self.assertRaises(ValueError):amplitude_components(P,q,a,b,(0.,1.,0.,0.),(0.,1.,0.,0.),model=current)
        with self.assertRaises(ValueError):reference_model(card,113,scalar_model='invented')


if __name__=='__main__':unittest.main()
