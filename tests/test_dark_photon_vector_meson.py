# Code style: AI-code. AI-generated independent vector-meson physics checks.
import math
import cmath
import json
from pathlib import Path
import random
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from dark_photon_vector_meson import (MUON_MASS_GEV, two_body_momentum,
    radiative_branching,transition_form_factor_abs2,transition_form_factor_variations,
    transition_form_factor_phase_envelope,transition_form_factor_amplitude,
    sample_decay,mean_lab_beta_gamma)

SUPPLEMENT = Path(__file__).resolve().parents[1]/'config/campaigns/dark_photon_vector_radiative_supplement.json'
JPSI = Path(__file__).resolve().parents[1]/'config/physics/dark_photon_jpsi_transition_inputs.json'


class VectorMesonTest(unittest.TestCase):
    def test_jpsi_empirical_inputs_keep_corrected_tables_and_normalization_scenarios(self):
        pi, eta, _ = json.loads(JPSI.read_text())['parents']
        self.assertEqual(eta['transition_form_factor']['reference'],
                         'https://arxiv.org/html/1810.03091v5')
        self.assertEqual(len(eta['transition_form_factor']['bins']),20)
        self.assertEqual(transition_form_factor_abs2(.25,eta['transition_form_factor']),1.10)
        self.assertEqual(transition_form_factor_abs2(2.,eta['transition_form_factor']),9.15)
        self.assertEqual(eta['br_p_gamma'],.001104)
        self.assertEqual(len(pi['transition_form_factor']['bins']),30)
        self.assertEqual(transition_form_factor_abs2(.7,pi['transition_form_factor']),19.2)
        self.assertEqual(transition_form_factor_abs2(.78,pi['transition_form_factor']),42.6)
        self.assertEqual(transition_form_factor_abs2(2.,pi['transition_form_factor']),2.71)
        raw, shape = pi['normalization_scenarios']
        self.assertEqual(raw['br_p_gamma'],.0000356)
        self.assertEqual(shape['br_p_gamma'],.0000339)
        self.assertAlmostEqual(transition_form_factor_abs2(.7,shape['transition_form_factor']),19.2/1.21)
        for row in (pi,eta):
            model=row['transition_form_factor']
            self.assertEqual(transition_form_factor_abs2(0.,model),1.)
            with self.assertRaisesRegex(ValueError,'domain'):
                transition_form_factor_abs2(model['valid_mass_range_gev'][1]+1.e-6,model)
            self.assertIn('unpolarized',row['polarization_note'].lower())
            self.assertFalse(row['production_rate_validated'])

    def test_jpsi_resonance_residues_match_independent_partial_width_conventions(self):
        model=json.loads(JPSI.read_text())['parents'][2]['transition_form_factor']
        parent,recoil=model['parent_mass_gev'],model['recoil_mass_gev']
        photon_k=(parent**2-recoil**2)/(2*parent)
        photon_g2=12*math.pi*model['photon_branching_fraction']/photon_k**3
        for row in model['resonances']:
            vector=row['mass_gev']
            k=math.sqrt((parent**2-(recoil+vector)**2)*(parent**2-(recoil-vector)**2))/(2*parent)
            strong_g2=12*math.pi*row['parent_branching_fraction']/k**3
            r=(model['electron_mass_gev']/vector)**2
            electromagnetic_x2=3*row['electron_width_gev']/(model['alpha_em']*vector*math.sqrt(1-4*r)*(1+2*r))
            residue2=strong_g2/photon_g2*electromagnetic_x2
            expected=(row['parent_branching_fraction']/model['photon_branching_fraction']
                      *(photon_k/k)**3*electromagnetic_x2)
            self.assertAlmostEqual(residue2/expected,1.,places=14)
            reconstructed_br=photon_g2*residue2/electromagnetic_x2*k**3/(12*math.pi)
            self.assertAlmostEqual(reconstructed_br/row['parent_branching_fraction'],1.,places=14)

    def test_jpsi_narrow_vector_poles_reproduce_observed_cascade_branching(self):
        # Integrate the pole alone in s=mV^2+mV*Gamma*tan(theta). The
        # Breit-Wigner denominator cancels the Jacobian, independently
        # checking that no factor of alpha, mV or3 was lost in the residue.
        model=json.loads(JPSI.read_text())['parents'][2]['transition_form_factor']
        M,P=model['parent_mass_gev'],model['recoil_mass_gev']
        kg=(M*M-P*P)/(2*M)
        for row in model['resonances'][1:]:
            # Take a controlled narrow-width limit without changing the
            # measured residue. Finite physical-width line-shape corrections
            # need not vanish, and must not be hidden in a relaxed tolerance.
            m,w=row['mass_gev'],row['width_gev']/100
            k=math.sqrt((M*M-(P+m)**2)*(M*M-(P-m)**2))/(2*M)
            electron=model['electron_mass_gev'];alpha=model['alpha_em']
            r=electron**2/m**2
            residue2=row['parent_branching_fraction']/model['photon_branching_fraction']*(kg/k)**3*3*row['electron_width_gev']/(alpha*m*math.sqrt(1-4*r)*(1+2*r))
            # Symmetric30width window is finite and physically open forboth
            # narrow poles. Its exactCauchy fraction is retained below.
            angle=math.atan(30.)
            count=2000
            step=2*angle/count
            total=0.
            for index in range(count):
                theta=-angle+(index+.5)*step
                s=m*m+m*w*math.tan(theta)
                phase=math.sqrt((M*M-(P+math.sqrt(s))**2)*(M*M-(P-math.sqrt(s))**2))/(M*M-P*P)
                lepton=math.sqrt(1-4*electron*electron/s)*(1+2*electron*electron/s)
                total+=model['photon_branching_fraction']*alpha/(3*math.pi*s)*lepton*phase**3*residue2*m**3/w*step
            expected=row['parent_branching_fraction']*row['electron_width_gev']/w*(2*angle/math.pi)
            self.assertAlmostEqual(total/expected,1.,delta=1.e-6)

    def test_jpsi_photon_matching_phase_average_and_tight_phase_bounds(self):
        model=json.loads(JPSI.read_text())['parents'][2]['transition_form_factor']
        parent,recoil=model['parent_mass_gev'],model['recoil_mass_gev']
        photon_k=(parent**2-recoil**2)/(2*parent)
        for mass in (0.,.22,.7,.77526,.78266,1.01946,1.5,2.):
            f=1/(1-mass**2/model['continuum_pole_mass_gev']**2)
            terms=[]
            for row in model['resonances']:
                m,w=row['mass_gev'],row['width_gev']
                k=math.sqrt((parent**2-(recoil+m)**2)*(parent**2-(recoil-m)**2))/(2*parent)
                r=(model['electron_mass_gev']/m)**2
                a=math.sqrt(row['parent_branching_fraction']/model['photon_branching_fraction']
                    *(photon_k/k)**3*3*row['electron_width_gev']
                    /(model['alpha_em']*m*math.sqrt(1-4*r)*(1+2*r)))
                terms.append(a*(m*m/complex(m*m-mass*mass,-m*w)-f*m*m/complex(m*m,-m*w)))
            # The fullCartesian4phase grid cancels every cross term exactly.
            phases=(1.,1.j,-1.,-1.j)
            average=sum(abs(f+a*terms[0]+b*terms[1]+c*terms[2])**2
                        for a in phases for b in phases for c in phases)/64
            central=transition_form_factor_abs2(mass,model)
            self.assertAlmostEqual(central/average,1.,places=13)
            fixed_phases=(.7,-1.8,2.1)
            independent=f+sum(term*cmath.exp(1.j*phase) for term,phase in zip(terms,fixed_phases))
            self.assertAlmostEqual(abs(transition_form_factor_amplitude(mass,model,fixed_phases)-independent),0.,places=12)
            low,high=transition_form_factor_phase_envelope(mass,model)
            self.assertLessEqual(low,central)
            self.assertGreaterEqual(high,central)
            lengths=[f,*(abs(term) for term in terms)]
            self.assertAlmostEqual(high/sum(lengths)**2,1.,places=14)
            largest=lengths.index(max(lengths))
            if 2*max(lengths)>=sum(lengths):
                phases=[(-1. if largest==0 or index!=largest-1 else 1.)
                        *cmath.exp(-1.j*cmath.phase(term)) for index,term in enumerate(terms)]
                # If a resonance dominates, point it opposite the continuum;
                # if the continuum dominates, point all resonances opposite it.
                if largest:
                    phases=[-value for value in phases]
                attained=abs(f+sum(phase*term for phase,term in zip(phases,terms)))**2
                self.assertAlmostEqual(attained,low,delta=1.e-11*max(1.,high))
            if mass==0:
                self.assertEqual((central,low,high),(1.,1.,1.))
        self.assertEqual(len(model['resonances']),3)
        with self.assertRaisesRegex(ValueError,'phase'):
            transition_form_factor_amplitude(.25,model,[])
        with self.assertRaisesRegex(ValueError,'domain'):
            transition_form_factor_abs2(2.001,model)

    def test_jpsi_current_rejects_unqualified_poles_rates_and_phase_defaults(self):
        model=json.loads(JPSI.read_text())['parents'][2]['transition_form_factor']
        for update in ({'phase_prescription':None},{'continuum_pole_mass_gev':1.5},
                       {'propagator_width':'zero-width'},{'alpha_em':0.}):
            with self.assertRaises(ValueError):
                transition_form_factor_abs2(.25,dict(model,**update))
        for field,value in [('width_gev',0.),('electron_width_gev',1.),
                            ('parent_branching_fraction',-1.),('reference',None)]:
            changed=json.loads(json.dumps(model));changed['resonances'][0][field]=value
            with self.assertRaises(ValueError):
                transition_form_factor_abs2(.25,changed)
        changed=json.loads(json.dumps(model));changed['resonances'].append(changed['resonances'][0])
        with self.assertRaisesRegex(ValueError,'unique'):
            transition_form_factor_abs2(.25,changed)

    def test_running_width_pole_matches_published_complex_propagator(self):
        model = json.loads(SUPPLEMENT.read_text())['parents'][0]['transition_form_factor']
        self.assertEqual(model['kind'], 'running-width-vector-pole')
        pole, width, pion = (model[x] for x in ('pole_mass_gev','pole_width_gev','pion_mass_gev'))
        for mass in (0.,.22,2*pion,.4,pole,.8,.884):
            running = (width*pole**2/mass**2*((mass**2-4*pion**2)/(pole**2-4*pion**2))**1.5
                       if mass > 2*pion else 0.)
            expected = pole**4/((pole**2-mass**2)**2+mass**2*running**2)
            self.assertAlmostEqual(transition_form_factor_abs2(mass,model)/expected,1.,places=14)
        self.assertAlmostEqual(transition_form_factor_abs2(pole,model),(pole/width)**2,places=12)
        with self.assertRaisesRegex(ValueError,'domain'):
            transition_form_factor_abs2(.9,model)

    def test_chiral_contact_keeps_published_amplitude_and_normalization(self):
        parents = json.loads(SUPPLEMENT.read_text())['parents']
        for parent in parents:
            model=parent['transition_form_factor']
            if model['kind'] != 'chiral-contact-vector':
                continue
            mass=.22;pole=model['pole_mass_gev'];width=model['pole_width_gev']
            # At this mass the rho's two-pion width is zero; omega constant
            # width enters as sqrt(q2)*Gamma in Ivashyn Eq.(10).
            running=width if model['propagator_width']=='constant' else 0.
            couplings=model['couplings']
            amplitude=math.sqrt(2)*couplings['h_V']-couplings['sigma_V']*couplings['f_V']*mass**2/complex(mass**2-pole**2,mass*running)
            expected=abs(amplitude/(math.sqrt(2)*couplings['h_V']))**2
            self.assertAlmostEqual(transition_form_factor_abs2(mass,model)/expected,1.,places=14)
            self.assertAlmostEqual(transition_form_factor_abs2(0.,model),1.,places=14)
            self.assertFalse(model['model_discrepancy_quantified'])

    def test_strange_finite_pole_is_normalized_without_zero_width_singularity(self):
        parents = json.loads(SUPPLEMENT.read_text())['parents']
        for parent in parents:
            model=parent['transition_form_factor']
            if model['kind'] != 'finite-width-vector-pole':
                continue
            self.assertAlmostEqual(transition_form_factor_abs2(0.,model),1.,places=14)
            self.assertGreater(transition_form_factor_abs2(.3,model),1.)
            self.assertLess(model['valid_mass_range_gev'][1],model['pole_mass_gev'])
            with self.assertRaisesRegex(ValueError,'domain'):
                transition_form_factor_abs2(model['pole_mass_gev'],model)

    def test_measured_bin_validation_is_finite_and_has_explicit_domain(self):
        table=json.loads(SUPPLEMENT.read_text())['parents'][0]['measured_transition_validation']
        expected={.22:1.46,.25:1.22,.3:2.30,.4:3.14,.5:8.49,.7:17.4}
        for mass,value in expected.items():
            self.assertEqual(transition_form_factor_abs2(mass,table),value)
            self.assertTrue(all(value>0 for value in transition_form_factor_variations(mass,table)))
        self.assertEqual(transition_form_factor_abs2(0.,table),1.)
        with self.assertRaisesRegex(ValueError,'domain'):
            transition_form_factor_abs2(.700001,table)

    def test_parameter_box_retains_measured_errors_and_missing_model_uncertainty(self):
        card=json.loads(SUPPLEMENT.read_text())
        for parent in card['parents']:
            model=parent['transition_form_factor']
            central=transition_form_factor_abs2(.22,model)
            variations=transition_form_factor_variations(.22,model)
            self.assertLess(min(variations),central)
            self.assertGreater(max(variations),central)
            self.assertFalse(model['model_discrepancy_quantified'])
        slope=dict(kind='single-pole-slope',slope_gev_minus2=2.223,
            slope_stat_gev_minus2=.026,slope_syst_gev_minus2=.037,
            valid_mass_range_gev=[.211316751,.64],reference='https://arxiv.org/abs/1608.07898')
        values=transition_form_factor_variations(.4,slope)
        error=math.hypot(.026,.037)
        self.assertAlmostEqual(min(values),(1-(2.223-error)*.4**2)**-2)
        self.assertAlmostEqual(max(values),(1-(2.223+error)*.4**2)**-2)

    def test_charge_conjugates_have_equal_rates_and_signed_recoils(self):
        parents=json.loads(SUPPLEMENT.read_text())['parents']
        for pdg in (213,313,323):
            positive=next(row for row in parents if row['pdg_id']==pdg)
            negative=next(row for row in parents if row['pdg_id']==-pdg)
            self.assertEqual(positive['recoil_pdg_id'],-negative['recoil_pdg_id'])
            for key in ('mass_gev','recoil_mass_gev','br_p_gamma','transition_form_factor'):
                self.assertEqual(positive[key],negative[key])
            self.assertEqual(positive['parent_mass_model'],'zero-width-pole')

    def test_malformed_model_or_missing_width_does_not_silently_default(self):
        model=json.loads(SUPPLEMENT.read_text())['parents'][0]['transition_form_factor']
        for changed in (dict(model,pole_width_gev=0.),dict(model,propagator_width='unknown'),
                        dict(model,kaon_mass_gev=None),dict(model,pion_mass_gev=-1.),
                        dict(model,kind='invented')):
            with self.assertRaises(ValueError):
                transition_form_factor_abs2(.3,changed)

    def test_rate_matches_kloe_lambda_ratio_without_factor_two(self):
        for parent,recoil in ((.78266,.1349768),(1.01946,.547862),(.89555,.497611)):
            for mass in (0.,.1,.22):
                lam = parent**4+recoil**4+mass**4-2*(parent**2*recoil**2+parent**2*mass**2+recoil**2*mass**2)
                expected = .002**2*.0833*1.7*(lam/(parent**2-recoil**2)**2)**1.5
                actual = radiative_branching(parent,recoil,mass,.002,.0833,1.7)
                self.assertAlmostEqual(actual/expected,1.,places=13)
        self.assertAlmostEqual(radiative_branching(.78266,.1349768,0.,.002,.0833,1.),.002**2*.0833)
        first=radiative_branching(.78266,.1349768,.3,.001,.0833,1.)
        self.assertAlmostEqual(radiative_branching(.78266,.1349768,.3,.002,.0833,1.)/first,4.)

    def test_endpoint_and_massless_recoil_limits(self):
        endpoint=.78266-.1349768
        for mass in (endpoint,endpoint+.001):
            self.assertEqual(two_body_momentum(.78266,.1349768,mass),0.)
            self.assertEqual(radiative_branching(.78266,.1349768,mass,.001,.0833,1.),0.)
        self.assertGreater(two_body_momentum(.78266,.1349768,endpoint-1.e-12),0.)
        value=radiative_branching(.8,0.,.3,.001,.1,1.)
        self.assertAlmostEqual(value/(.001**2*.1),(1-(.3/.8)**2)**3)

    def test_explicit_na60_form_factor_domain_and_pole_gate(self):
        model=dict(kind='single-pole-slope',slope_gev_minus2=2.223,
                   valid_mass_range_gev=[.211316751,.64],reference='https://arxiv.org/abs/1608.07898')
        self.assertEqual(transition_form_factor_abs2(0.,model),1.)
        self.assertAlmostEqual(transition_form_factor_abs2(.4,model),(1-2.223*.4**2)**-2)
        with self.assertRaisesRegex(ValueError,'domain'):
            transition_form_factor_abs2(.65,model)
        with self.assertRaisesRegex(ValueError,'reference'):
            transition_form_factor_abs2(.3,{})
        with self.assertRaisesRegex(ValueError,'pole'):
            transition_form_factor_abs2(.7,dict(model,valid_mass_range_gev=[0.,.8]))

    def test_independent_lab_conservation_shells_and_spacetime(self):
        rng=random.Random(761016160)
        for parent_mass,recoil_mass,mass in ((.78266,.1349768,.22),(.78266,.1349768,.6),
                 (1.01946,.547862,.4),(.89555,.497611,.3),
                 (3.0969,.1349768,.22),(3.0969,.547862,.7),(3.0969,.95778,2.)):
            for p in ((0.,0.,0.),(1.,-2.,-30.)):
                parent=(math.sqrt(parent_mass**2+sum(x*x for x in p)),)+p
                for _ in range(15):
                    row=sample_decay(parent,recoil_mass,mass,120.,rng,polarization='unpolarized')
                    pair=tuple(math.fsum((a,b)) for a,b in zip(row['muon_p4'],row['antimuon_p4']))
                    for actual,expected in zip(pair,row['dark_photon_p4']):
                        self.assertAlmostEqual(actual,expected,places=9)
                    for actual,expected in zip((a+b for a,b in zip(pair,row['recoil_p4'])),parent):
                        self.assertAlmostEqual(actual,expected,places=9)
                    for field,shell in (('dark_photon_p4',mass),('recoil_p4',recoil_mass),
                                       ('muon_p4',MUON_MASS_GEV),('antimuon_p4',MUON_MASS_GEV)):
                        v=row[field];squared=math.fsum((v[0]**2,*(-x*x for x in v[1:])))
                        self.assertAlmostEqual(squared,shell**2,places=8)
                    proper=row['proper_decay_length_mm']
                    for distance,momentum in zip(row['decay_displacement_mm'],row['dark_photon_p4'][1:]):
                        self.assertAlmostEqual(distance,proper*momentum/mass,places=10)
                    self.assertAlmostEqual(row['decay_ct_mm'],proper*row['dark_photon_p4'][0]/mass)
                    self.assertTrue(row['recoil_natural_decay_required'])

    def test_unpolarized_axis_and_massive_muon_helicity_moments(self):
        # Independent inverse boost along the known A' axis, rather than
        # reading back the angles drawn by the sampler.
        count=16000
        for mass in (.212,.4):
            rng=random.Random(761016161)
            axis_first=axis_second=cosine_second=length_sum=0.
            for _ in range(count):
                row=sample_decay((.78266,0.,0.,0.),.1349768,mass,120.,rng,polarization='unpolarized')
                dark=row['dark_photon_p4'];muon=row['muon_p4']
                p=math.sqrt(sum(x*x for x in dark[1:]));axis=tuple(x/p for x in dark[1:])
                projection=sum(a*b for a,b in zip(muon[1:],axis))
                longitudinal=(dark[0]*projection-p*muon[0])/mass
                rest_momentum=math.sqrt(mass**2/4-MUON_MASS_GEV**2)
                cosine=longitudinal/rest_momentum
                axis_first+=axis[2];axis_second+=axis[2]**2;cosine_second+=cosine**2
                length_sum+=row['proper_decay_length_mm']
            r=(MUON_MASS_GEV/mass)**2
            expected=(2*(1+4*r)/3+2*(1-4*r)/5)/(8*(1+2*r)/3)
            self.assertAlmostEqual(axis_first/count,0.,delta=.015)
            self.assertAlmostEqual(axis_second/count,1/3,delta=.012)
            self.assertAlmostEqual(cosine_second/count,expected,delta=.012)
            self.assertAlmostEqual(length_sum/count,120.,delta=4.)

    def test_generic_massive_recoil_boost_mean_matches_angular_integral(self):
        for momentum,mass in ((0.,.3),(.1,.3),(1.,.3),(30.,.6),(6000.,.6),(1.,.64768319)):
            parent=(math.hypot(momentum,.78266),0.,0.,-momentum)
            parent_mass=math.sqrt(parent[0]**2-momentum**2)
            # Independent quadratic energy formulas and midpoint integral.
            energy=(parent_mass**2+mass**2-.1349768**2)/(2*parent_mass)
            p=math.sqrt(max(0.,energy**2-mass**2))
            a=parent[0]/parent_mass*energy;b=momentum/parent_mass*p
            count=20000
            numeric=math.fsum(math.sqrt(max(0.,(a+b*(-1+(i+.5)*2/count))**2-mass**2))
                              for i in range(count))/(count*mass)
            exact=mean_lab_beta_gamma(parent,.1349768,mass,polarization='unpolarized')
            self.assertAlmostEqual(exact/numeric,1.,places=7)

    def test_unknown_polarization_and_closed_dimuons_fail(self):
        for polarization,mass in (('unknown',.3),('transverse',.3),('unpolarized',.2)):
            with self.assertRaises(ValueError):
                sample_decay((.78266,0.,0.,0.),.1349768,mass,120.,random.Random(1),polarization=polarization)


if __name__=='__main__':
    unittest.main()
