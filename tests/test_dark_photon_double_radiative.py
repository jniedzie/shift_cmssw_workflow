# Code style: AI-code. AI-generated controls of neutral pseudoscalar VMD currents.
import math
import json
import random
import statistics
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from dark_photon_double_radiative import (LocalVMDCurrent,LocalVMDSampler,CoherentScalarSampler,ScalarPairCDF,branching_coefficient,
    empirical_reference_model,mean_lab_beta_gamma,polarizations,quark_model_couplings,sample_decay)
from dark_photon_meson import boost
from dark_photon_vector_meson import two_body_momentum
from dark_photon_meson import MUON_MASS_GEV


MASSES = {221:.547862,331:.95778,111:.1349768}
EMPIRICAL = {113:{111:.22,221:.48,331:.40},
             223:{111:.70,221:.135,331:.127},333:{111:.041,221:.2093,331:-.216}}


def reference_model(parent,recoil,*,quark=False):
    # Primary TablesI-II and Eqs11,24. The negative phi-eta' sign is fixed by
    # the primary quark mixing formula, not by a fit to the integrated width.
    couplings = quark_model_couplings(.70,.83,.65,math.radians(41.4),math.radians(3.3)) if quark else EMPIRICAL
    vectors = []
    for pdg,mass,width in [(113,.77526,.1491),(223,.78265,.00849),(333,1.019461,.004249)]:
        product = couplings[pdg][parent]*couplings[pdg][recoil]
        vectors.append(dict(mass_gev=mass,width_gev=width,coupling_product_gev_inverse2=product,
            absolute_product_ceiling_gev_inverse2=abs(product),
            width_prescription='rho-pipi-primary-eq10' if pdg==113 else 'constant',pion_mass_gev=.13957039))
    return dict(kind='local-vp-field-strength-vmd',reference='https://doi.org/10.1103/PhysRevD.102.034026',
        virtual_vertex_prescription='constant-local-couplings',omitted_scalar_and_chiral_terms=True,
        valid_mass_range_gev=[0.,MASSES[parent]-MASSES[recoil]],vectors=vectors)


def rest_event(parent_mass,recoil_mass,mass,cosine=.37,fraction=.45):
    pair_mass=recoil_mass+mass+fraction*(parent_mass-recoil_mass-mass)
    outer=(parent_mass**2-pair_mass**2)/(2*parent_mass)
    pair_energy=(parent_mass**2+pair_mass**2)/(2*parent_mass)
    inner=two_body_momentum(pair_mass,recoil_mass,mass)
    energy=(pair_mass**2+mass**2-recoil_mass**2)/(2*pair_mass)
    dark=boost((energy,inner*math.sqrt(1-cosine*cosine),0.,inner*cosine),(0.,0.,-outer/pair_energy))
    return (parent_mass,0.,0.,0.),(outer,0.,0.,outer),dark


def dot(a,b):
    return a[0]*b[0]-sum(x*y for x,y in zip(a[1:],b[1:]))


class DoubleRadiativeTest(unittest.TestCase):
    def test_maintained_reference_reproduces_independently_written_coupling_card(self):
        path=Path(__file__).resolve().parents[1]/'config/physics/dark_photon_double_radiative_vmd_2020.json'
        inputs=json.loads(path.read_text())
        for parent,recoil in [(221,111),(331,111),(331,221)]:
            generated=empirical_reference_model(inputs,parent,recoil,coupling_errors=0.)
            self.assertEqual(generated,reference_model(parent,recoil))
            envelope=empirical_reference_model(inputs,parent,recoil,coupling_errors=3.)
            self.assertTrue(all(a['absolute_product_ceiling_gev_inverse2']>=b['absolute_product_ceiling_gev_inverse2']
                                for a,b in zip(envelope['vectors'],generated['vectors'])))

    def test_two_ward_identities_hold_for_massive_currents_under_boosts(self):
        model=LocalVMDCurrent(reference_model(221,111))
        for mass in [.22,.3,.4]:
            parent,photon,dark=rest_event(MASSES[221],MASSES[111],mass)
            first,second=polarizations(photon,0.)[0],polarizations(dark,mass)[0]
            for beta in [(0.,0.,0.),(.2,-.3,.7)]:
                p,k,q,e1,e2=[boost(v,beta) for v in (parent,photon,dark,first,second)]
                scale=abs(model.amplitude(p,k,q,e1,e2))
                self.assertLess(abs(model.amplitude(p,k,q,k,e2)),1.e-12*scale)
                self.assertLess(abs(model.amplitude(p,k,q,e1,q)),1.e-12*scale)

    def test_covariant_vertices_recover_independent_primary_real_photon_formula(self):
        model=LocalVMDCurrent(reference_model(221,111))
        for fraction in [.15,.45,.8]:
            p,k,q=rest_event(MASSES[221],MASSES[111],0.,fraction=fraction)
            r=tuple(x-y for x,y in zip(p,k));s=tuple(x-y for x,y in zip(p,q))
            for first in polarizations(k,0.):
                for second in polarizations(q,0.):
                    a=dot(first,second)*dot(k,q)-dot(first,q)*dot(second,k)
                    b=(dot(first,q)*dot(second,p)*dot(p,k)+dot(second,k)*dot(first,p)*dot(p,q)
                       -dot(first,second)*dot(p,k)*dot(p,q)-dot(first,p)*dot(second,p)*dot(k,q))
                    expected=(model._coefficients(dot(s,s))[0]*((dot(p,q)-dot(p,p))*a-b)
                              +model._coefficients(dot(r,r))[0]*((dot(p,k)-dot(p,p))*a-b))
                    self.assertAlmostEqual(abs(model.amplitude(p,k,q,first,second)-expected),0.,places=17)

    def test_bose_symmetry_and_lorentz_scalar_amplitude(self):
        model=LocalVMDCurrent(reference_model(221,111))
        p,k,q=rest_event(MASSES[221],MASSES[111],.3)
        e1,e2=polarizations(k,0.)[1],polarizations(q,.3)[1]
        original=model.amplitude(p,k,q,e1,e2)
        self.assertAlmostEqual(abs(original-model.amplitude(p,q,k,e2,e1)),0.,places=16)
        moved=model.amplitude(*[boost(v,(.2,-.3,.6)) for v in (p,k,q,e1,e2)])
        self.assertAlmostEqual(abs(original-moved),0.,places=16)

    def test_massive_longitudinal_polarization_is_retained(self):
        model=LocalVMDCurrent(reference_model(221,111))
        p,k,q=rest_event(MASSES[221],MASSES[111],.25)
        longitudinal=polarizations(q,.25)[2]
        contribution=sum(abs(model.amplitude(p,k,q,first,longitudinal))**2 for first in polarizations(k,0.))
        self.assertGreater(contribution,0.)
        total=model.spin_sum(p,k,q,.25)
        self.assertGreaterEqual(total,contribution)

    def test_pointwise_envelope_bounds_coherent_interference(self):
        card=reference_model(331,111)
        envelope=LocalVMDCurrent(card)
        p,k,q=rest_event(MASSES[331],MASSES[111],.3)
        upper=envelope.spin_sum(p,k,q,.3,envelope=True)
        for index in range(20):
            shifted=reference_model(331,111)
            for j,row in enumerate(shifted['vectors']):
                phase=.31*(index+1)*(j+1)
                row['coupling_product_gev_inverse2']*=complex(math.cos(phase),math.sin(phase))
            self.assertLessEqual(LocalVMDCurrent(shifted).spin_sum(p,k,q,.3),upper*(1+1.e-14))

    def test_three_body_phase_space_and_photon_identical_factor(self):
        class ConstantAmplitude(LocalVMDCurrent):
            def spin_sum(self,*args,**kwargs):
                return 1.
        card=reference_model(331,111)
        card['valid_mass_range_gev']=[0.,1.]
        model=ConstantAmplitude(card)
        width=model.integrated_width(1.,0.,0.,1.,order=8)
        self.assertAlmostEqual(width/(1/(512*math.pi**3)),1.,places=12)
        photons=model.integrated_width(1.,0.,0.,1.,photon_reference=True,order=8)
        self.assertEqual(width/photons,2.)

    def test_primary_independent_massless_vmd_widths(self):
        # Targets and quoted coupling-input uncertainties are primary TableII.
        # Published rounded g values are not fitted back to these widths.
        targets=[(221,111,False,.16,.01),(331,111,False,570.,30.),
                 (331,221,False,21.2,1.2),(221,111,True,.16,.01),
                 (331,111,True,700.,40.),(331,221,True,19.1,1.)]
        for parent,recoil,quark,expected,error in targets:
            model=LocalVMDCurrent(reference_model(parent,recoil,quark=quark))
            actual=model.integrated_width(MASSES[parent],MASSES[recoil],0.,1.,photon_reference=True,order=16)*1.e9
            self.assertLess(abs(actual-expected),error)

    def test_quadrature_and_epsilon_scaling_at_physical_dark_mass(self):
        model=LocalVMDCurrent(reference_model(221,111))
        for mass in [.22,.3,.4]:
            first=model.integrated_width(MASSES[221],MASSES[111],mass,.001,order=16)
            second=model.integrated_width(MASSES[221],MASSES[111],mass,.002,order=32)
            self.assertAlmostEqual(second/first,4.,delta=4.e-5)
        closed=model.integrated_width(MASSES[221],MASSES[111],.5,.001)
        self.assertEqual(closed,0.)

    def test_missing_model_authority_or_virtual_domain_fails(self):
        for change in [dict(reference=''),dict(virtual_vertex_prescription='unspecified'),
                       dict(omitted_scalar_and_chiral_terms=False),dict(valid_mass_range_gev=None)]:
            with self.assertRaises(ValueError):
                LocalVMDCurrent(dict(reference_model(221,111),**change))


def source_model(parent,recoil):
    return dict(decay_type='pseudoscalar-double-radiative',recoil_mass_gev=MASSES[recoil],
        recoil_pdg_id=recoil,total_parent_width_gev=1.31e-6 if parent==221 else .000196,
        double_radiative_model=reference_model(parent,recoil))


class CorrelatedDoubleRadiativeProduction(unittest.TestCase):
    def test_individual_diagram_norm_and_analytic_envelope_cover_full_currents(self):
        rng=random.Random(7281)
        for parent,recoil,mass in [(221,111,0.),(221,111,.22),(221,111,.4),
                                  (331,111,.3),(331,111,.7),(331,221,.22)]:
            current=LocalVMDCurrent(reference_model(parent,recoil))
            sampler=LocalVMDSampler(current,MASSES[parent],MASSES[recoil],mass)
            for _ in range(100):
                p,k,q=rest_event(MASSES[parent],MASSES[recoil],mass,
                    cosine=rng.uniform(-1.,1.),fraction=rng.uniform(.001,.999))
                r=tuple(x-y for x,y in zip(p,k));s=tuple(x-y for x,y in zip(p,q))
                for index,internal in enumerate((r,s)):
                    first,second=(k,q) if index==0 else (q,k)
                    expected=(dot(first,internal)*dot(second,internal)-dot(internal,internal)*dot(first,second))**2
                    expected+=(dot(first,internal)**2-dot(internal,internal)*dot(first,first))*(
                        dot(second,internal)**2-dot(internal,internal)*dot(second,second))
                    actual=math.fsum(abs(current._kinematic_terms(p,k,q,a,b)[index])**2
                        for a in polarizations(k,0.) for b in polarizations(q,mass))
                    self.assertAlmostEqual(actual/expected,1.,delta=2.e-9)
                self.assertLessEqual(current.spin_sum(p,k,q,mass),sampler.envelope(dot(r,r),dot(s,s))*(1+2.e-12))

    def test_unweighted_acceptance_has_the_independent_integrated_rate_and_energy_moment(self):
        current=LocalVMDCurrent(reference_model(331,111))
        sampler=LocalVMDSampler(current,MASSES[331],MASSES[111],.3)
        width=current.integrated_width(MASSES[331],MASSES[111],.3,1.,order=24)
        probability=width*256*math.pi**3*MASSES[331]**3/sampler.envelope_integral
        expected_energy=current.integrated_width(MASSES[331],MASSES[111],.3,1.,order=24,
            moment=lambda dark:dark[0])/width
        rng=random.Random(290133);events=[sampler.sample_rest(rng) for _ in range(1200)]
        trials=sum(row['proposal_trials'] for row in events)
        # Geometric waiting times give Var(mean trials)=(1-p)/(N*p^2).
        sigma=math.sqrt((1-probability)/(len(events)*probability**2))
        self.assertAlmostEqual(trials/len(events),1/probability,delta=6*sigma)
        energies=[row['dark_photon_p4'][0] for row in events]
        self.assertAlmostEqual(statistics.mean(energies),expected_energy,
            delta=6*statistics.stdev(energies)/math.sqrt(len(energies)))

    def test_full_decay_closes_shells_vertices_lifetimes_and_rotations(self):
        rng=random.Random(187301)
        for pdg,recoil,mass in [(221,111,.22),(331,111,.3),(331,221,.22),
                               (331,111,MASSES[331]-MASSES[111]-1.e-5)]:
            model=source_model(pdg,recoil)
            for beta in [(0.,0.,0.),(.2,-.4,.7)]:
                parent=boost((MASSES[pdg],0.,0.,0.),beta)
                for _ in range(15):
                    row=sample_decay(parent,mass,120.,rng,model=model)
                    final=[row['dark_photon_p4'],*row['recoil_p4s']]
                    for component in range(4):
                        self.assertAlmostEqual(sum(v[component] for v in final),parent[component],delta=2.e-13)
                        self.assertAlmostEqual(row['muon_p4'][component]+row['antimuon_p4'][component],
                            row['dark_photon_p4'][component],delta=2.e-13)
                    for vector,expected in zip(final,[mass,MASSES[recoil],0.]):
                        self.assertAlmostEqual(dot(vector,vector),expected*expected,delta=3.e-13)
                    for key in ['muon_p4','antimuon_p4']:
                        self.assertAlmostEqual(dot(row[key],row[key]),MUON_MASS_GEV**2,delta=3.e-13)
                    length=row['proper_decay_length_mm'];dark=row['dark_photon_p4']
                    self.assertGreater(length,0.)
                    self.assertEqual(row['decay_ct_mm'],length*dark[0]/mass)
                    self.assertEqual(row['decay_displacement_mm'],tuple(length*x/mass for x in dark[1:]))
                    self.assertEqual(row['recoil_pdg_ids'],[recoil,22])
                    self.assertTrue(row['coherent_parent_replacement_required'])
                    self.assertTrue(row['scalar_chiral_and_additional_timelike_terms_omitted'])
                    self.assertTrue(row['unconditional_decay'])

    def test_actual_muon_helicity_moment_follows_each_coherent_spin_density(self):
        import numpy as np
        current=LocalVMDCurrent(reference_model(331,111));sampler=LocalVMDSampler(current,MASSES[331],MASSES[111],.6)
        rng=random.Random(62098);residuals=[];isotropic_residuals=[]
        for _ in range(1600):
            row=sampler.sample((MASSES[331],0.,0.,0.),0.,rng)
            dark=row['dark_photon_p4'];photon=row['recoil_p4s'][1]
            matrix,basis=sampler.spin_density(photon,dark)
            trace=sum(matrix[i][i].real for i in range(3));beta2=1-4*MUON_MASS_GEV**2/.6**2
            eigenvalues,eigenvectors=np.linalg.eigh(np.asarray(matrix).real)
            expected=(trace/3-beta2*(trace+2*eigenvalues[-1])/15)/((1-beta2/3)*trace)
            rest=boost(row['muon_p4'],tuple(-x/dark[0] for x in dark[1:]))
            rest_basis=[boost(vector,tuple(-x/dark[0] for x in dark[1:]))[1:] for vector in basis]
            axis=[sum(eigenvectors[j,-1]*rest_basis[j][i] for j in range(3)) for i in range(3)]
            norm=math.sqrt(sum(x*x for x in rest[1:]))
            cosine=sum(a*b for a,b in zip(rest[1:],axis))/norm
            residuals.append(cosine*cosine-expected);isotropic_residuals.append(1/3-expected)
            self.assertEqual(row['proper_decay_length_mm'],0.)
        error=statistics.stdev(residuals)/math.sqrt(len(residuals))
        self.assertAlmostEqual(statistics.mean(residuals),0.,delta=5*error)
        self.assertGreater(abs(statistics.mean(isotropic_residuals)),2*error)

    def test_production_coefficient_keeps_real_photon_normalization_and_exact_epsilon_power(self):
        model=source_model(221,111);current=LocalVMDCurrent(model['double_radiative_model'])
        coefficient=branching_coefficient(MASSES[221],0.,model)
        independent=current.integrated_width(MASSES[221],MASSES[111],0.,1.,photon_reference=True,order=32)
        self.assertAlmostEqual(coefficient*model['total_parent_width_gev']/independent,2.,delta=4.e-5)
        value=branching_coefficient(MASSES[221],.3,model)
        self.assertGreater(value,0.)
        self.assertEqual(branching_coefficient(MASSES[221],MASSES[221]-MASSES[111],model),0.)
        # Source integration owns epsilon_em^2 and BR(A'->mumu), once each.
        first=value*.001**2*.12;second=value*.002**2*.12
        self.assertEqual(second/first,4.)

    def test_same_seed_is_reproducible_and_boost_mean_matches_sampled_events(self):
        model=source_model(221,111);parent=boost((MASSES[221],0.,0.,0.),(.2,-.3,.7))
        first=sample_decay(parent,.3,80.,random.Random(13883),model=model)
        second=sample_decay(parent,.3,80.,random.Random(13883),model=model)
        self.assertEqual(first,second)
        expected=mean_lab_beta_gamma(parent,.3,model)
        rng=random.Random(81932);values=[]
        for _ in range(600):
            dark=sample_decay(parent,.3,80.,rng,model=model)['dark_photon_p4']
            values.append(math.sqrt(sum(x*x for x in dark[1:]))/.3)
        self.assertAlmostEqual(statistics.mean(values),expected,delta=6*statistics.stdev(values)/math.sqrt(len(values)))

    def test_invalid_or_closed_production_inputs_fail_without_redrawing(self):
        model=source_model(221,111);parent=(MASSES[221],0.,0.,0.)
        for mass,length in [(2*MUON_MASS_GEV,1.),(.5,1.),(.3,-1.),(.3,float('nan'))]:
            rng=random.Random(913);before=rng.getstate()
            with self.assertRaises(ValueError):sample_decay(parent,mass,length,rng,model=model)
            self.assertEqual(rng.getstate(),before)
        for key in ['total_parent_width_gev','recoil_mass_gev']:
            with self.assertRaises(ValueError):branching_coefficient(MASSES[221],.3,dict(model,**{key:0.}))


def scalar_model(parent,recoil,kind='escribano-2020-linear-sigma'):
    inputs=json.loads((Path(__file__).resolve().parents[1]/'config/physics/dark_photon_scalar_meson_2020.json').read_text())
    scalar=dict(inputs['reference_parameters'],parent_pdg_id=parent,recoil_pdg_id=recoil,
        parent_mass_gev=MASSES[parent],recoil_mass_gev=MASSES[recoil],kind=kind)
    return dict(reference_model(parent,recoil),kind='local-vp-field-strength-vmd-plus-scalar-loop',
        omitted_scalar_and_chiral_terms=False,scalar_current=scalar)


class ScalarDoubleRadiativeProduction(unittest.TestCase):
    def test_scalar_loop_matches_primary_real_photon_normalization_and_relative_phase(self):
        import cmath
        from dark_photon_scalar_meson import scalar_strong_amplitudes
        for parent,recoil in [(221,111),(331,111),(331,221)]:
            current=LocalVMDCurrent(scalar_model(parent,recoil))
            for fraction in [.1,.45,.8]:
                p,k,q=rest_event(MASSES[parent],MASSES[recoil],0.,fraction=fraction)
                invariant=2*dot(k,q)
                coefficient=0j
                for loop_mass,strong in scalar_strong_amplitudes(invariant,current.scalar):
                    z=invariant/loop_mass**2
                    if z<4:
                        f=-math.asin(math.sqrt(z)/2)**2
                    else:
                        beta=math.sqrt(1-4/z)
                        f=(math.log((1+beta)/(1-beta))-1j*math.pi)**2/4
                    loop=-1/(2*z)-2*f/z**2
                    coefficient+=2*current.scalar['alpha_em']/(math.pi*loop_mass**2)*loop*strong
                for first in polarizations(k,0.):
                    for second in polarizations(q,0.):
                        tensor=dot(first,second)*dot(k,q)-dot(first,q)*dot(second,k)
                        expected=coefficient*tensor
                        self.assertAlmostEqual(abs(current.scalar_amplitude(k,q,first,second)-expected),0.,delta=2.e-15)

    def test_scalar_and_full_currents_preserve_two_ward_identities_and_scalar_pair_spin(self):
        current=LocalVMDCurrent(scalar_model(331,221))
        for mass in [.22,.3,.4]:
            p,k,q=rest_event(MASSES[331],MASSES[221],mass)
            a,b=polarizations(k,0.)[0],polarizations(q,mass)[0]
            for beta in [(0.,0.,0.),(.2,-.3,.6)]:
                parent,photon,dark,first,second=[boost(v,beta) for v in (p,k,q,a,b)]
                scale=abs(current.amplitude(parent,photon,dark,first,second))
                self.assertLess(abs(current.amplitude(parent,photon,dark,photon,second)),1.e-11*scale)
                self.assertLess(abs(current.amplitude(parent,photon,dark,first,dark)),1.e-11*scale)
            direct=math.fsum(abs(current.scalar_amplitude(k,q,first,second))**2
                for first in polarizations(k,0.) for second in polarizations(q,mass))
            invariant=mass**2+2*dot(k,q)
            self.assertAlmostEqual(direct/current.scalar_spin_sum(invariant,mass**2),1.,places=12)

    def test_primary_scalar_component_widths_without_empirical_rescaling(self):
        targets=[(221,111,.00187,.0005),(331,111,.11,.13),(331,221,.014,3.29)]
        for parent,recoil,chiral,sigma in targets:
            for kind,target in [('escribano-2020-chpt',chiral),('escribano-2020-linear-sigma',sigma)]:
                current=LocalVMDCurrent(scalar_model(parent,recoil,kind))
                actual=ScalarPairCDF(current,MASSES[parent],MASSES[recoil],0.).width/2*1.e9
                # The primary prints rounded component widths and no exact
                # pion input card. The declared charged-loop convention has
                # at most7.4% discrepancy; no coupling is fitted to hide it.
                self.assertLess(abs(actual/target-1),.08)
                if parent==331 and recoil==221 and kind.endswith('linear-sigma'):
                    self.assertAlmostEqual(actual,3.27756974,delta=2.e-7)

    def test_full_scalar_proposal_rate_closes_against_independent_three_body_phase_space(self):
        current=LocalVMDCurrent(scalar_model(331,221))
        for mass in [.22,.3]:
            proposal=ScalarPairCDF(current,MASSES[331],MASSES[221],mass)
            class ScalarOnly(LocalVMDCurrent):
                def spin_sum(self,p,k,q,mass,**kwargs):
                    return math.fsum(abs(self.scalar_amplitude(k,q,a,b))**2
                        for a in polarizations(k,0.) for b in polarizations(q,mass))
            independent=ScalarOnly(scalar_model(331,221)).integrated_width(MASSES[331],MASSES[221],mass,1.,order=64)
            self.assertAlmostEqual(independent/proposal.width,1.,delta=2.e-5)
            for index in range(1,100):
                target=index/100*proposal.width
                class Quantile:
                    def random(self):return index/100
                value=proposal.sample(Quantile())
                partial=math.fsum(proposal.integral(low,min(value,high),40)
                    for low,high,_ in proposal.panels if low<value)
                self.assertAlmostEqual(partial/target,1.,delta=2.e-7)

    def test_coherent_mixture_has_rate_acceptance_and_energy_moment(self):
        parent,recoil,mass=331,221,.22
        current=LocalVMDCurrent(scalar_model(parent,recoil))
        sampler=CoherentScalarSampler(current,MASSES[parent],MASSES[recoil],mass)
        width=current.integrated_width(MASSES[parent],MASSES[recoil],mass,1.,order=32)
        probability=width/(2*(sampler.vector_width+sampler.scalar_cdf.width))
        expected=current.integrated_width(MASSES[parent],MASSES[recoil],mass,1.,order=32,
            moment=lambda dark:dark[0])/width
        rng=random.Random(98173);rows=[sampler.sample_rest(rng) for _ in range(1000)]
        trials=statistics.mean(row['proposal_trials'] for row in rows)
        self.assertAlmostEqual(trials,1/probability,
            delta=6*math.sqrt((1-probability)/(len(rows)*probability**2)))
        energies=[row['dark_photon_p4'][0] for row in rows]
        self.assertAlmostEqual(statistics.mean(energies),expected,
            delta=6*statistics.stdev(energies)/math.sqrt(len(rows)))

    def test_declared_full_current_survives_source_cache_and_correlated_decay(self):
        for parent,recoil,mass in [(221,111,.3),(331,111,.3),(331,221,.3)]:
            source=source_model(parent,recoil);source['double_radiative_model']=scalar_model(parent,recoil)
            row=sample_decay((MASSES[parent],0.,0.,0.),mass,10.,random.Random(parent),model=source)
            self.assertEqual(row['amplitude_model'],'coherent-local-vp-field-strength-vmd-plus-scalar-loop')
            self.assertEqual(row['scalar_current_model'],'escribano-2020-linear-sigma')
            self.assertFalse(row['scalar_chiral_and_additional_timelike_terms_omitted'])
            self.assertTrue(row['additional_timelike_operators_omitted'])
            for component in range(4):
                total=sum(p[component] for p in [row['dark_photon_p4'],*row['recoil_p4s']])
                self.assertAlmostEqual(total,(MASSES[parent],0.,0.,0.)[component],delta=2.e-13)
            self.assertTrue(row['unconditional_decay'])


class CachedDoubleRadiativeEvaluation(unittest.TestCase):
    def test_scalar_threshold_grazing_resolves_without_relaxing_the_rate_budget(self):
        # At mA=.25 the pion cut crosses both outer Dalitz boundaries.
        # The old inner-angle-only split did not converge through order80.
        # The target is the independent order80 integration recorded while
        # diagnosing that failure; no parameter or rate has been rescaled.
        current=LocalVMDCurrent(scalar_model(331,111))
        target=3.975135411101855e-7
        first=current.integrated_width(MASSES[331],MASSES[111],.25,1.,order=16)
        second=current.integrated_width(MASSES[331],MASSES[111],.25,1.,order=24)
        self.assertAlmostEqual(second/first,1.,delta=2.e-5)
        self.assertAlmostEqual(second/target,1.,delta=2.e-6)

    def test_near_pole_rate_interpolation_matches_independent_mass_integrals(self):
        from dark_photon_double_radiative import _source_inputs,_pole_mass_table,_unit_width
        for parent,recoil,mass in [(221,111,.3),(331,111,.3),(331,221,.3)]:
            with self.subTest(parent=parent,recoil=recoil):
                model=source_model(parent,recoil)
                model['double_radiative_model']=scalar_model(parent,recoil)
                recoil_mass,width,key=_source_inputs(model)
                table=_pole_mass_table(MASSES[parent],recoil_mass,mass,key)
                self.assertTrue(table.valid,dict(width=table.width_error,mean=table.mean_error))
                for fraction in (-.91,-.31,.27,.88):
                    actual=MASSES[parent]+fraction*table.radius
                    exact=_unit_width(actual,recoil_mass,mass,key)/width
                    self.assertAlmostEqual(branching_coefficient(actual,mass,model)/exact,1.,delta=5.e-8)
                # Values outside the checked interval keep their own exact
                # integration. They are never clamped onto the pole or edge.
                actual=MASSES[parent]+1.3*table.radius
                self.assertEqual(branching_coefficient(actual,mass,model),
                    _unit_width(actual,recoil_mass,mass,key)/width)

    def test_changed_boost_and_binary64_mass_do_not_repeat_the_amplitude_integral(self):
        from dark_photon_double_radiative import _source_inputs,_pole_mass_table,_mean_at_mass
        model=source_model(331,221);model['double_radiative_model']=scalar_model(331,221)
        recoil,_,key=_source_inputs(model);mass=.3
        table=_pole_mass_table(MASSES[331],recoil,mass,key)
        self.assertTrue(table.valid)
        # Warm only the necessary three rest-frame mass nodes. A later
        # boost and invariant-mass jitter must reuse their coherent current.
        table.means(math.sqrt(2.),1.)
        parent=boost((math.nextafter(MASSES[331],math.inf),0.,0.,0.),(.21,-.32,.71))
        actual=math.sqrt(dot(parent,parent));gamma=parent[0]/actual
        beta_gamma=math.sqrt(sum(x*x for x in parent[1:]))/actual
        exact=_mean_at_mass(actual,recoil,mass,key,gamma,beta_gamma)
        with patch.object(LocalVMDCurrent,'spin_sum',side_effect=AssertionError('Repeated amplitude quadrature')):
            measured=mean_lab_beta_gamma(parent,mass,model)
            self.assertAlmostEqual(measured/exact,1.,delta=5.e-8)
            self.assertGreater(branching_coefficient(math.nextafter(actual,math.inf),mass,model),0.)

    def test_near_endpoint_and_loop_threshold_keep_checked_rates_and_means(self):
        from dark_photon_double_radiative import _source_inputs,_pole_mass_table,_unit_width,_mean_at_mass
        model=source_model(331,221);model['double_radiative_model']=scalar_model(331,221)
        recoil,width,key=_source_inputs(model)
        for mass in (2*.13957039,MASSES[331]-recoil-1.e-5):
            table=_pole_mass_table(MASSES[331],recoil,mass,key)
            radius=min(1.e-7*MASSES[331],1.e-4*(MASSES[331]-recoil-mass))
            actual=MASSES[331]+.43*radius
            self.assertAlmostEqual(branching_coefficient(actual,mass,model)/
                (_unit_width(actual,recoil,mass,key)/width),1.,delta=5.e-8)
            parent=boost((actual,0.,0.,0.),(.17,-.23,.81))
            invariant=math.sqrt(dot(parent,parent));gamma=parent[0]/invariant
            beta_gamma=math.sqrt(sum(x*x for x in parent[1:]))/invariant
            exact=_mean_at_mass(invariant,recoil,mass,key,gamma,beta_gamma)
            measured=mean_lab_beta_gamma(parent,mass,model)
            self.assertAlmostEqual(measured/exact,1.,delta=5.e-8)
            if table is None or not table.valid:
                # Near a closing endpoint, cancellation in the analytic
                # angular primitive can exceed the tighter interpolation
                # budget. The exact original evaluator remains authoritative.
                self.assertEqual(measured,exact)


if __name__=='__main__':
    unittest.main()
