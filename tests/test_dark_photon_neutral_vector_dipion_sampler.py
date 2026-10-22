# Code style: AI-code. AI-generated independent CDF and correlated-decay controls.
import json
import math
from pathlib import Path
import random
import sys
import unittest

import numpy as np

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from dark_photon_double_radiative import _dot,polarizations
from dark_photon_meson import boost,MUON_MASS_GEV
from dark_photon_neutral_vector_dipion import (NumericalDecayTable,_linear_cdf,
    amplitude_components,branching_coefficient,decay_width,differential_width,
    mean_lab_beta_gamma,reference_model,rest_kinematics,sample_decay)


def model(pdg=113,cells=64):
    reference=json.loads((ROOT/'config/physics/dark_photon_neutral_vector_dipion_2001.json').read_text())
    mass=.77511 if pdg==113 else .78265
    return dict(reference_parent_mass_gev=mass,pion_mass_gev=.13498,
        total_width_gev=.1491 if pdg==113 else .00849,
        current=reference_model(reference,pdg,scalar_model='sigma478_324'),
        numerical_cdf=dict(cells_s=cells,cells_cos=cells//2,loop_order=96),
        native_kinematic_precision=dict(relative_mass_limit=1.e-7,relative_endpoint_limit=1.e-6))


def independent_moments(inputs,mass,order=28):
    nodes,weights=np.polynomial.legendre.leggauss(order)
    parent=inputs['reference_parent_mass_gev'];pion=inputs['pion_mass_gev']
    low,high=4*pion*pion,(parent-mass)**2;sums=np.zeros(4)
    beta2=1-4*(MUON_MASS_GEV/mass)**2
    # Independent smooth-coordinate quadrature, without any CDF table values.
    for node,weight in zip(nodes,weights):
        node,weight=float(node),float(weight)
        t=(node+1)/2;s=low+(high-low)*math.sin(math.pi*t/2)**2
        jac=(high-low)*math.pi/2*math.sin(math.pi*t)
        for cosine,angular in zip(nodes,weights):
            cosine,angular=float(cosine),float(angular)
            density=differential_width(parent,pion,mass,s,cosine,model=inputs['current'])['total']
            P,q,first,second=rest_kinematics(parent,pion,mass,s,cosine);rho=np.zeros((3,3),complex)
            for ep in polarizations(P,parent):
                values=np.array([amplitude_components(P,q,first,second,ep,eq,model=inputs['current'])['total']
                    for eq in polarizations(q,mass)])
                rho+=np.outer(values,values.conjugate())/3
            trace=rho.trace().real
            angle=(1/3-beta2*(1+2*rho[2,2].real/trace)/15)/(1-beta2/3)
            value=weight/2*angular*jac*density
            sums+=value*np.array([1.,s,cosine*cosine,angle])
    return sums/sums[0]


class NumericalNeutralDipionSamplerTest(unittest.TestCase):
    def test_linear_cdf_matches_analytic_integrated_density(self):
        for first,second in ((0.,2.),(3.,0.),(2.,2.),(.1,7.)):
            for uniform in (.0001,.15,.5,.95,.9999):
                x=_linear_cdf(first,second,uniform)
                integral=(first*x+(second-first)*x*x/2)/((first+second)/2)
                self.assertAlmostEqual(integral,uniform,places=12)
        for values in ((0.,0.,.5),(-1.,2.,.5),(1.,1.,0.)):
            with self.assertRaises(ValueError):_linear_cdf(*values)

    def test_rate_refinement_and_sample_marginals_match_independent_current(self):
        inputs=model();mass=.3
        coarse=NumericalDecayTable(inputs['reference_parent_mass_gev'],inputs['pion_mass_gev'],mass,
            model=inputs['current'],cells_s=32,cells_cos=16)
        refined=NumericalDecayTable(inputs['reference_parent_mass_gev'],inputs['pion_mass_gev'],mass,
            model=inputs['current'],cells_s=128,cells_cos=64)
        exact=decay_width(inputs['reference_parent_mass_gev'],inputs['pion_mass_gev'],mass,
            model=inputs['current'],epsilon_em=1.,order=32,loop_order=128)['total']
        self.assertLess(abs(refined.integral/exact-1),1.e-4)
        self.assertLess(abs(refined.integral/exact-1),abs(coarse.integral/exact-1))
        target=independent_moments(inputs,mass)
        rng=random.Random(887123);rows=np.array([refined.sample(rng) for _ in range(30000)])
        self.assertLess(abs(rows[:,0].mean()-target[1]),.0008)
        self.assertLess(abs((rows[:,1]**2).mean()-target[2]),.007)
        self.assertEqual(len(refined.sha256),64);self.assertTrue(np.all(np.isfinite(rows)))

    def test_full_decay_closure_boosts_angles_and_unconditional_flight(self):
        rng=random.Random(993744);inputs=model();mass=.3;M=inputs['reference_parent_mass_gev']
        expected=independent_moments(inputs,mass)[3];lifetime=[];directions=[];angles=[]
        for index in range(2500):
            parent=(M,0.,0.,0.) if index>=40 else (math.hypot(M,400.),.1,-.2,math.sqrt(400.**2-.05))
            event=sample_decay(parent,mass,150.,rng,model=inputs)
            dark=event['dark_photon_p4'];first,second=event['recoil_p4s']
            total=tuple(math.fsum(v[i] for v in (dark,first,second)) for i in range(4))
            for a,b in zip(parent,total):self.assertLess(abs(a-b),4.e-10*max(1.,abs(a)))
            for vector,reference in ((dark,mass),(first,inputs['pion_mass_gev']),
                    (second,inputs['pion_mass_gev']),(event['muon_p4'],MUON_MASS_GEV),
                    (event['antimuon_p4'],MUON_MASS_GEV)):
                self.assertLess(abs(_dot(vector,vector)-reference**2),2.e-9)
            for axis in range(4):
                self.assertLess(abs(event['muon_p4'][axis]+event['antimuon_p4'][axis]-dark[axis]),2.e-10)
            self.assertEqual(event['recoil_pdg_ids'],[111,111])
            self.assertTrue(event['recoil_natural_decay_required']);self.assertTrue(event['unconditional_decay'])
            for displacement,momentum in zip(event['decay_displacement_mm'],dark[1:]):
                self.assertEqual(displacement,event['proper_decay_length_mm']*momentum/mass)
            lifetime.append(event['proper_decay_length_mm']/150.)
            if index>=40:
                axis=np.array(dark[1:])/math.sqrt(sum(x*x for x in dark[1:]))
                muon=boost(event['muon_p4'],tuple(-x/dark[0] for x in dark[1:]))
                cosine=np.dot(muon[1:],axis)/math.sqrt(sum(x*x for x in muon[1:]))
                angles.append(cosine*cosine);directions.append(axis)
        self.assertLess(abs(np.mean(lifetime)-1),.06)
        self.assertLess(np.max(np.abs(np.mean(directions,axis=0))),.045)
        self.assertLess(abs(np.mean(angles)-expected),.022)
        measured=[]
        for _ in range(700):
            event=sample_decay((M,0.,0.,0.),mass,150.,rng,model=inputs)
            measured.append(math.sqrt(sum(x*x for x in event['dark_photon_p4'][1:]))/mass)
        self.assertLess(abs(np.mean(measured)/mean_lab_beta_gamma((M,0.,0.,0.),mass,inputs)-1),.025)
        self.assertGreater(branching_coefficient(M,mass,inputs),0.)

    def test_omega_and_mass_endpoints_do_not_change_particle_authority(self):
        inputs=model(223,32);M=inputs['reference_parent_mass_gev']
        for mass in (.22,.4,.5):
            event=sample_decay((M,0.,0.,0.),mass,12.,random.Random(514),model=inputs)
            self.assertEqual(event['event_parent_mass_gev'],M)
            self.assertEqual(event['reference_parent_mass_gev'],M)
            self.assertEqual(event['dark_photon_mass_gev'],mass)
        with self.assertRaises(ValueError):sample_decay((M*1.0001,0.,0.,0.),.3,12.,random.Random(1),model=inputs)
        with self.assertRaises(ValueError):NumericalDecayTable(M,.13498,M-.26996,model=inputs['current'])


if __name__=='__main__':unittest.main()
