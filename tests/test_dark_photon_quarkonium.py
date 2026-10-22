# Code style: AI-code. AI-generated heavy-quarkonium rate regression controls.
import copy
import itertools
import json
import math
from pathlib import Path
import random
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from dark_photon_dipion_meson import _gauss_legendre
from dark_photon_quarkonium import (chi1_lattice_width, effective_pole_ratio,
    eta_c_form_factor, heavy_spin_e1_ratio)
from dark_photon_vector_meson import two_body_momentum

INPUTS=json.loads((ROOT/'config/physics/dark_photon_quarkonium_rates_2026.json').read_text())
MASSES=INPUTS['physical_reference_masses_gev']
ALPHA=INPUTS['alpha_em']


class QuarkoniumRateTest(unittest.TestCase):
    def lattice_width(self,mass,model=None,**kwargs):
        return chi1_lattice_width(MASSES['chi_c1'],MASSES['Jpsi'],mass,
            model or INPUTS['chi_c1_lattice'],alpha_em=ALPHA,**kwargs)

    def test_independent_primary_dalitz_widths(self):
        # Log(q2) quadrature is independent of the analytical conversion step
        # in the maintained rate function. The published total widths were
        # not used to tune any form factor or overall normalization.
        for lepton,target in ((.00051099895,2.869),(.1056583755,.1993)):
            lower=math.log(4*lepton**2)
            upper=math.log((MASSES['chi_c1']-MASSES['Jpsi'])**2)
            result=0.
            for point,weight in _gauss_legendre(192):
                q2=math.exp(lower+(upper-lower)*(point+1)/2)
                conversion=ALPHA/(3*math.pi)*math.sqrt(1-4*lepton**2/q2)*(1+2*lepton**2/q2)
                result+=weight*conversion*self.lattice_width(math.sqrt(q2))
            result*=(upper-lower)/2*1.e6
            self.assertLess(abs(result/target-1),.002)

    def test_exact_dilepton_angular_integral_reproduces_current_width(self):
        # Direct primary Eqs55,56, with finite muon mass, versus the
        # independently simplified gamma* width times conversion factor.
        M,m=MASSES['chi_c1'],MASSES['Jpsi']
        ml=.1056583755
        for mass in (.22,.25,.3,.4):
            q2=mass**2;beta2=1-4*ml**2/q2
            k=two_body_momentum(M,m,mass);k0=two_body_momentum(M,m,0.)
            e=.879*(1-.20*q2);t=-.0584*(1+.39*q2)*(k/k0)**2;c=-6.90*(1+.258*q2)
            a=q2*c*c+M*M*(1+4*ml*ml/q2)*(e*e+t*t)
            b=-beta2*(q2*c*c-M*M*(e*e+t*t))
            direct=ALPHA**2*(2/3)**2/(48*math.pi)*math.sqrt(beta2)*2*M*k/(q2*M**3)*(2*a+2*b/3)
            conversion=ALPHA/(3*math.pi*q2)*math.sqrt(beta2)*(1+2*ml*ml/q2)
            self.assertAlmostEqual(direct/(self.lattice_width(mass)*conversion),1.,places=13)

    def test_longitudinal_form_factor_is_required_only_for_massive_vector(self):
        removed=copy.deepcopy(INPUTS['chi_c1_lattice'])
        removed['form_factors']['C1']['CF']=0.
        self.assertEqual(self.lattice_width(0.),self.lattice_width(0.,removed))
        self.assertGreater(self.lattice_width(.4),self.lattice_width(.4,removed)*1.3)

    def test_rectangle_encloses_fit_corners_and_random_interior(self):
        rng=random.Random(861022)
        for mass in (0.,.22,.3,.4):
            low=self.lattice_width(mass,parameter_errors=3.,envelope='lower')
            high=self.lattice_width(mass,parameter_errors=3.,envelope='upper')
            variations=list(itertools.product((-1.,1.),repeat=6))
            variations += [tuple(rng.uniform(-1,1) for _ in range(6)) for _ in range(16)]
            for variation in variations:
                model=copy.deepcopy(INPUTS['chi_c1_lattice'])
                directions=iter(variation)
                for fit in model['form_factors'].values():
                    for key,error in (('CF','CF_error'),('DF_gev_minus2','DF_error_gev_minus2')):
                        fit[key]+=3*fit[error]*next(directions)
                value=self.lattice_width(mass,model)
                self.assertLessEqual(low*(1-1.e-14),value)
                self.assertLessEqual(value,high*(1+1.e-14))

    def test_electric_field_spin_sum_includes_longitudinal_and_ward_zero(self):
        M,m=MASSES['chi_c0'],MASSES['Jpsi']
        k0=two_body_momentum(M,m,0.)
        for mass in (.001,.22,.3):
            k=two_body_momentum(M,m,mass);omega=math.sqrt(k*k+mass*mass)
            polarizations=((0.,1.,0.,0.),(0.,0.,1.,0.),(k/mass,0.,0.,omega/mass))
            fields=[tuple(omega*e[i]-k*(i==3)*e[0] for i in (1,2,3)) for e in polarizations]
            direct=k/k0*sum(sum(x*x for x in field) for field in fields)/(2*k0*k0)
            self.assertAlmostEqual(direct,heavy_spin_e1_ratio(M,m,mass),places=12)
            self.assertEqual(tuple(omega*q-k*(i==3)*omega for i,q in enumerate((0.,0.,k),1)),(0.,0.,0.))

    def test_massless_and_endpoint_limits(self):
        M,m=MASSES['chi_c1'],MASSES['Jpsi']
        self.assertAlmostEqual(heavy_spin_e1_ratio(M,m,0.),1.,places=14)
        self.assertEqual(heavy_spin_e1_ratio(M,m,M-m),0.)
        self.assertEqual(self.lattice_width(M-m),0.)
        ratios=[]
        for distance in (1.e-5,1.e-7):
            mass=M-m-distance
            ratios.append(heavy_spin_e1_ratio(M,m,mass)/two_body_momentum(M,m,mass))
        self.assertLess(abs(ratios[1]/ratios[0]-1),.0001)

    def test_effective_pole_has_its_own_phase_space_and_rejects_internal_pole(self):
        M,m=MASSES['chi_c2'],MASSES['Jpsi'];model=INPUTS['chi_c2_effective_pole']
        mass=.4
        expected=two_body_momentum(M,m,mass)/two_body_momentum(M,m,0.)/(1-(mass/.71)**2)**2
        self.assertAlmostEqual(effective_pole_ratio(M,m,mass,model),expected,places=14)
        bad=dict(model,lambda_gev=.4)
        with self.assertRaises(ValueError):effective_pole_ratio(M,m,.22,bad)

    def test_eta_c_alternative_models_normalize_and_show_light_pole(self):
        nr=INPUTS['eta_c_nr_charm_pole'];vmd=INPUTS['eta_c_rho_jpsi_two_pole']
        self.assertEqual(eta_c_form_factor(0.,nr),1.)
        self.assertAlmostEqual(abs(eta_c_form_factor(0.,vmd)-1),0.,places=14)
        self.assertGreater(abs(eta_c_form_factor(.7,vmd))**2,abs(eta_c_form_factor(.7,nr))**2*10)
        with self.assertRaises(ValueError):eta_c_form_factor(1.,nr)

    def test_explicit_authority_and_fit_domain_required(self):
        model=copy.deepcopy(INPUTS['chi_c1_lattice']);model.pop('reference')
        with self.assertRaises(ValueError):self.lattice_width(.22,model)
        with self.assertRaises(ValueError):self.lattice_width(.5)
        with self.assertRaises(ValueError):self.lattice_width(.22,parameter_errors=-1)


if __name__=='__main__':unittest.main()
