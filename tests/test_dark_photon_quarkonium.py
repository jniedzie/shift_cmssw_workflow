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
    eta_c_form_factor, heavy_spin_e1_ratio, hc_lattice_form_factors, hc_lattice_width,
    branching_coefficient, sample_decay, mean_lab_beta_gamma, chi1_current_amplitude)
from dark_photon_meson import MUON_MASS_GEV, boost, dot
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
        with self.assertRaises(ValueError):eta_c_form_factor(MASSES['eta_c']+1.e-6,nr)

    def test_eta_c_theoretical_continuations_cover_full_physical_interval(self):
        parent = MASSES['eta_c']
        for name in ('eta_c_nr_charm_pole', 'eta_c_rho_jpsi_two_pole'):
            current = INPUTS[name]
            self.assertEqual(current['valid_mass_range_gev'][1], parent)
            self.assertIn('theoretical', current['domain_scope'])
            for mass in (1.5, 1.8, 2., parent*(1-1.e-7), parent):
                self.assertTrue(math.isfinite(abs(eta_c_form_factor(mass, current))))
            model = channel_model('eta_c->gamma_Aprime')
            model['quarkonium_model'] = copy.deepcopy(current)
            for mass in (1.5, 1.8, 2., parent*(1-1.e-7)):
                expected = 2*model['br_gamma_gamma']*(1-(mass/parent)**2)**3*abs(eta_c_form_factor(mass, current))**2
                self.assertAlmostEqual(branching_coefficient(parent, mass, model)/expected, 1., places=12)
            self.assertEqual(branching_coefficient(parent, parent, model), 0.)

    def test_explicit_authority_and_fit_domain_required(self):
        model=copy.deepcopy(INPUTS['chi_c1_lattice']);model.pop('reference')
        with self.assertRaises(ValueError):self.lattice_width(.22,model)
        with self.assertRaises(ValueError):self.lattice_width(.5)
        with self.assertRaises(ValueError):self.lattice_width(.22,parameter_errors=-1)


def channel_model(channel):
    name,_,recoil=channel.partition('->')
    current=dict(eta_c='eta_c_nr_charm_pole',chi_c0='chi_c0_e1_benchmark',
                 chi_c1='chi_c1_lattice',chi_c2='chi_c2_effective_pole',
                 h_c='h_c_lattice',psi_2S='psi_2S_m1_benchmark')[name]
    photon=recoil=='gamma_Aprime'
    result=dict(decay_type='quarkonium-radiative',channel=channel,parent_polarization='unpolarized',
        recoil_mass_gev=0. if photon else MASSES['eta_c' if name in ('h_c','psi_2S') else 'Jpsi'],
        recoil_pdg_id=22 if photon else 441 if name in ('h_c','psi_2S') else 443,
        quarkonium_model=copy.deepcopy(INPUTS[current]))
    result['br_gamma_gamma' if photon else 'br_p_gamma']=.01
    if name=='chi_c2':result['angular_model']='leading-heavy-spin-e1'
    return result


class QuarkoniumDecayTest(unittest.TestCase):
    channels=('eta_c->gamma_Aprime','chi_c0->Jpsi_Aprime','chi_c1->Jpsi_Aprime',
              'chi_c2->Jpsi_Aprime','h_c->eta_c_Aprime','psi_2S->eta_c_Aprime')

    @staticmethod
    def scalar(a,b):
        return math.fsum((a[0]*b[0],*(-x*y for x,y in zip(a[1:],b[1:]))))

    def test_radiative_normalizations_and_closed_thresholds_are_distinct(self):
        for channel in self.channels:
            model=channel_model(channel);M=MASSES[channel.split('->')[0]]
            self.assertAlmostEqual(branching_coefficient(M,0.,model),.02 if channel.startswith('eta_c') else .01,places=14)
            self.assertEqual(branching_coefficient(M,M-model['recoil_mass_gev'],model),0.)
        # A pole J/psi -> eta_c transition cannot create an on-shell dimuon.
        self.assertLess(MASSES['Jpsi']-MASSES['eta_c'],2*MUON_MASS_GEV)
        self.assertGreater(MASSES['psi_2S']-MASSES['eta_c'],.7)

    def test_eta_c_high_mass_and_endpoint_samples_remain_on_shell(self):
        parent, rng = MASSES['eta_c'], random.Random(1761624101)
        for kind in ('eta_c_nr_charm_pole', 'eta_c_rho_jpsi_two_pole'):
            model = channel_model('eta_c->gamma_Aprime')
            model['quarkonium_model'] = copy.deepcopy(INPUTS[kind])
            for mass in (1.5, 1.8, 2., parent*(1-1.e-5)):
                for momentum in (0., 20.):
                    p4 = (math.hypot(parent, momentum), 0., 0., momentum)
                    row = sample_decay(p4, mass, 2., rng, model=model)
                    self.assertEqual(row['longitudinal_fraction'], 0.)
                    vectors = (row['muon_p4'], row['antimuon_p4'], row['photon_p4'])
                    for actual, expected in zip(map(sum, zip(*vectors)), p4):
                        self.assertLess(abs(actual-expected), 1.e-10)
                    for value, expected_mass in zip(vectors, (MUON_MASS_GEV, MUON_MASS_GEV, 0.)):
                        self.assertLess(abs(self.scalar(value, value)-expected_mass**2), 1.e-10)
            with self.assertRaises(ValueError):
                sample_decay((parent, 0., 0., 0.), parent, 2., rng, model=model)

    def test_attributed_physical_photon_inputs_define_each_massless_normalization(self):
        inputs=INPUTS['radiative_branching_inputs']
        self.assertEqual(set(inputs),set(self.channels))
        for channel in self.channels:
            calibration=inputs[channel];model=channel_model(channel)
            self.assertTrue(calibration['reference'].startswith('https://pdg.lbl.gov/2025/listings/'))
            self.assertGreater(calibration['page'],0)
            self.assertTrue(0<calibration['uncertainty']<calibration['value']<1)
            self.assertEqual(calibration['recoil_pdg_id'],model['recoil_pdg_id'])
            key=calibration['branching_key'];model[key]=calibration['value']
            parent=MASSES[channel.split('->')[0]]
            identical_photons=2 if key=='br_gamma_gamma' else 1
            self.assertAlmostEqual(branching_coefficient(parent,0.,model),
                                   identical_photons*calibration['value'],places=14)

    def test_absolute_lattice_and_photon_normalized_calibrations_cannot_be_mixed(self):
        for channel,model_key,width in [('chi_c1->Jpsi_Aprime','chi_c1_lattice',chi1_lattice_width),
                                         ('h_c->eta_c_Aprime','h_c_lattice',hc_lattice_width)]:
            model=channel_model(channel);M=MASSES[channel.split('->')[0]];recoil=model['recoil_mass_gev']
            model.pop('br_p_gamma');model.update(total_parent_width_gev=.0008,alpha_em=ALPHA)
            expected=width(M,recoil,.3,INPUTS[model_key],alpha_em=ALPHA)/.0008
            self.assertAlmostEqual(branching_coefficient(M,.3,model),expected,places=13)
            model['br_p_gamma']=.01
            with self.assertRaisesRegex(ValueError,'Choose one'):branching_coefficient(M,.3,model)

    def test_hc_current_matches_primary_direct_equation_and_integrated_widths(self):
        M,m=MASSES['h_c'],MASSES['eta_c'];model=INPUTS['h_c_lattice'];fit=model['form_factors']
        delta=M*M-m*m
        for mass in (.001,.22,.3,.5):
            q2=mass*mass;lam=(M*M+m*m-q2)**2-4*M*M*m*m
            f1=fit['C']*(1+fit['DF1_gev_minus2']*q2)
            f2=fit['C']*(1+fit['DF2_gev_minus2']*q2)
            direct=(lam/delta*f2-(delta+q2)*f1)/(2*q2)
            actual=hc_lattice_form_factors(M,m,mass,model)
            self.assertAlmostEqual(actual[0],f1,places=14)
            self.assertLess(abs(actual[1]-direct),1.e-8)
        self.assertTrue(all(math.isfinite(x) for x in hc_lattice_form_factors(M,m,0.,model)))
        for lepton,target in ((.00051099895,5.45),(.1056583755,.635)):
            lower=math.log(4*lepton**2);upper=math.log((M-m)**2);value=0.
            for node,weight in _gauss_legendre(256):
                q2=math.exp(lower+(upper-lower)*(node+1)/2)
                conversion=ALPHA/(3*math.pi)*math.sqrt(1-4*lepton**2/q2)*(1+2*lepton**2/q2)
                value+=weight*conversion*hc_lattice_width(M,m,math.sqrt(q2),model,alpha_em=ALPHA)
            value*=(upper-lower)/2*1.e6
            self.assertLess(abs(value/target-1),.003)

    def test_chi1_covariant_amplitude_ward_identity_and_independent_spin_sum(self):
        M,m=MASSES['chi_c1'],MASSES['Jpsi'];model=INPUTS['chi_c1_lattice']
        parents=((0.,1.,0.,0.),(0.,0.,1.,0.),(0.,0.,0.,1.))
        for mass in (.22,.3,.4):
            k=two_body_momentum(M,m,mass);k0=two_body_momentum(M,m,0.)
            omega,energy=math.hypot(k,mass),math.hypot(k,m)
            recoils=parents[:2]+((-k/m,0.,0.,energy/m),)
            vectors=parents[:2]+((k/mass,0.,0.,omega/mass),)
            sums=[sum(chi1_current_amplitude(M,m,mass,model,p,r,a)**2 for p in parents for r in recoils) for a in vectors]
            e=.879*(1-.20*mass*mass);m2=-.0584*(1+.39*mass*mass)*(k/k0)**2;c=-6.90*(1+.258*mass*mass)
            expected=[M*M*(e*e+m2*m2)]*2+[mass*mass*c*c]
            for actual,target in zip(sums,expected):self.assertAlmostEqual(actual,target,places=11)
            for p in parents:
                for r in recoils:
                    self.assertLess(abs(chi1_current_amplitude(M,m,mass,model,p,r,(omega,0.,0.,k))),1.e-12)

    def test_current_thresholds_polarization_authorities_and_chi2_spin_are_explicit(self):
        for channel in self.channels:
            model=channel_model(channel);M=MASSES[channel.split('->')[0]]
            parent=(M,0.,0.,0.)
            for mass,lifetime in [(2*MUON_MASS_GEV,1.),(M-model['recoil_mass_gev'],1.),(.22,0.)]:
                with self.assertRaises(ValueError):sample_decay(parent,mass,lifetime,random.Random(1),model=model)
            model['parent_polarization']='isotropic-but-polarized'
            with self.assertRaises(ValueError):sample_decay(parent,.22,1.,random.Random(1),model=model)
        model=channel_model('chi_c2->Jpsi_Aprime');model.pop('angular_model')
        self.assertGreater(branching_coefficient(MASSES['chi_c2'],.3,model),0.)
        with self.assertRaisesRegex(ValueError,'angular approximation'):
            sample_decay((MASSES['chi_c2'],0.,0.,0.),.3,1.,random.Random(1),model=model)

    def test_all_chains_close_on_shell_after_noncollinear_lorentz_boosts(self):
        rng=random.Random(2026102601)
        for channel in self.channels:
            model=channel_model(channel);M=MASSES[channel.split('->')[0]];mass=.3
            for boost_vector in [(0.,0.,0.),(.2,-.1,.9),(-.7,.3,.5)]:
                parent=boost((M,0.,0.,0.),boost_vector)
                for _ in range(8):
                    row=sample_decay(parent,mass,31.,rng,model=model)
                    for name,target in [('dark_photon_p4',mass),('recoil_p4',model['recoil_mass_gev']),
                                         ('muon_p4',MUON_MASS_GEV),('antimuon_p4',MUON_MASS_GEV)]:
                        vector=row[name]
                        self.assertLess(abs(self.scalar(vector,vector)-target*target),1.e-10*max(1.,vector[0]**2))
                    for i in range(4):
                        self.assertAlmostEqual(row['dark_photon_p4'][i]+row['recoil_p4'][i],parent[i],places=10)
                        self.assertAlmostEqual(row['muon_p4'][i]+row['antimuon_p4'][i],row['dark_photon_p4'][i],places=10)
                    self.assertTrue(row['unconditional_decay'])
                    self.assertAlmostEqual(row['decay_ct_mm']/row['dark_photon_p4'][0],row['proper_decay_length_mm']/mass,places=12)

    def test_recoil_density_is_normalized_positive_and_basis_transports_covariantly(self):
        rng=random.Random(2026102602)
        for channel in self.channels:
            model=channel_model(channel);M=MASSES[channel.split('->')[0]]
            parent=boost((M,0.,0.,0.),(.2,-.1,.9))
            for _ in range(10):
                row=sample_decay(parent,.3,1.,rng,model=model)
                matrix=row['recoil_spin_density_matrix'];basis=row['recoil_polarization_basis_p4']
                self.assertEqual(row['recoil_spin_correlations_required'],model['recoil_pdg_id']==443)
                if matrix is None:
                    self.assertEqual(basis,[]);continue
                self.assertAlmostEqual(sum(matrix[i][i][0] for i in range(len(matrix))),1.,places=13)
                for i in range(len(matrix)):
                    self.assertGreaterEqual(matrix[i][i][0],-1.e-13)
                    for j in range(len(matrix)):
                        self.assertEqual(matrix[i][j][1],0.)
                        self.assertAlmostEqual(matrix[i][j][0],matrix[j][i][0],places=13)
                        self.assertGreaterEqual(matrix[i][i][0]*matrix[j][j][0]-matrix[i][j][0]**2,-1.e-13)
                        self.assertAlmostEqual(self.scalar(basis[i],basis[j]),-float(i==j),places=11)
                    self.assertAlmostEqual(self.scalar(basis[i],row['recoil_p4']),0.,places=11)

    def test_scalar_e1_recoil_density_retains_conditional_azimuthal_correlations(self):
        model=channel_model('chi_c0->Jpsi_Aprime');M=MASSES['chi_c0'];mass=.3
        row=sample_decay((M,0.,0.,0.),mass,1.,random.Random(122),model=model)
        dark=row['dark_photon_p4'];muon=boost(row['muon_p4'],tuple(-x/dark[0] for x in dark[1:]))
        basis=row['recoil_polarization_basis_p4'];axis=tuple(x/math.sqrt(dot(dark[1:],dark[1:])) for x in dark[1:])
        direction=[dot(muon[1:],vector)/math.sqrt(dot(muon[1:],muon[1:])) for vector in [basis[0][1:],basis[1][1:],axis]]
        b=1-4*(MUON_MASS_GEV/mass)**2;fields=(dark[0],dark[0],mass)
        expected=[[fields[i]*fields[j]*(float(i==j)-b*direction[i]*direction[j]) for j in range(3)] for i in range(3)]
        trace=sum(expected[i][i] for i in range(3))
        for i in range(3):
            for j in range(3):self.assertAlmostEqual(row['recoil_spin_density_matrix'][i][j][0],expected[i][j]/trace,places=12)
        self.assertGreater(abs(row['recoil_spin_density_matrix'][0][1][0]),.001)

    def test_sampled_massive_dimuon_moments_and_proper_lifetime(self):
        rng=random.Random(2026102603)
        for channel in self.channels:
            model=channel_model(channel);M=MASSES[channel.split('->')[0]];mass=.3
            values=[sample_decay((M,0.,0.,0.),mass,8.,rng,model=model) for _ in range(1600)]
            fraction=values[0]['longitudinal_fraction'];b=1-4*(MUON_MASS_GEV/mass)**2
            transverse=(1/3-b/15)/(1-b/3);longitudinal=(1/3-b/5)/(1-b/3)
            target=(1-fraction)*transverse+fraction*longitudinal
            self.assertLess(abs(sum(row['helicity_cosine']**2 for row in values)/len(values)-target),.035)
            self.assertLess(abs(sum(row['proper_decay_length_mm'] for row in values)/len(values)-8.),.9)
            self.assertLess(abs(sum(row['helicity_cosine'] for row in values)/len(values)),.05)

    def test_two_body_mean_uses_the_actual_recoil_and_no_lifetime_selection(self):
        for channel in self.channels:
            model=channel_model(channel);M=MASSES[channel.split('->')[0]]
            actual=mean_lab_beta_gamma((M,0.,0.,0.),.3,model)
            expected=two_body_momentum(M,model['recoil_mass_gev'],.3)/.3
            self.assertAlmostEqual(actual,expected,places=12)

    def test_m1_branching_has_its_own_k_cubed_and_no_identical_photon_factor(self):
        model=channel_model('psi_2S->eta_c_Aprime');M,m=MASSES['psi_2S'],MASSES['eta_c']
        expected=.01*(two_body_momentum(M,m,.3)/two_body_momentum(M,m,0.))**3
        self.assertAlmostEqual(branching_coefficient(M,.3,model),expected,places=14)


if __name__=='__main__':unittest.main()
