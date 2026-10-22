# Code style: AI-code. AI-generated controls of neutral pseudoscalar VMD currents.
import math
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from dark_photon_double_radiative import LocalVMDCurrent,empirical_reference_model,polarizations,quark_model_couplings
from dark_photon_meson import boost
from dark_photon_vector_meson import two_body_momentum


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


if __name__=='__main__':
    unittest.main()
