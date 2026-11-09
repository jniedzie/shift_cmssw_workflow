# Code style: AI-code. Published proton-bremsstrahlung and source-unit controls.
import cmath
import math
from pathlib import Path
import sys
import unittest
import random
import copy

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import dark_photon_bremsstrahlung as model
from dark_photon_senscalc import fr_variation_weights


class PublishedBremsstrahlung(unittest.TestCase):
    @staticmethod
    def source_momentum(mass,z,pt):
        pz=-z*math.sqrt(6800.**2-model.PROTON_MASS_GEV**2)
        return dict(id=32,mechanism='proton-bremsstrahlung',
                    p4=[math.hypot(mass,pt,pz),pt,0.,pz])

    def test_correlated_variations_preserve_recorded_event_and_nominal_weight(self):
        sampler=model.SourceSampler(model.sampling_profile(.7))
        candidate=self.source_momentum(.7,.3,.2)
        original=copy.deepcopy(candidate)
        weights=fr_variation_weights(sampler,candidate)
        self.assertEqual(candidate,original)
        self.assertEqual(weights['Central'],1.)
        self.assertAlmostEqual(weights['Lower'],.007274358598283819,places=14)
        self.assertAlmostEqual(weights['Upper'],8.981207585669456,places=12)
        # A spectrum variation is not a single cross-section multiplier.
        other=fr_variation_weights(sampler,self.source_momentum(.7,.7,1.))
        self.assertNotAlmostEqual(weights['Upper'],other['Upper'],places=3)

    def test_event_variations_reproduce_independently_integrated_physical_rates(self):
        import numpy as np
        # Independent adaptive dpT,dz integrations of the published scenarios.
        reference={'Lower':12789962.731319,'Central':1293147119.834526,
                   'Upper':16484869840.903057}
        sampler=model.SourceSampler(model.sampling_profile(.7))
        nodes,quad_weights=np.polynomial.legendre.leggauss(64)
        low,high=[math.log(z/(1-z)) for z in sampler.profile['z_range']]
        rates={name:0. for name in reference}
        for node,weight in zip(nodes,quad_weights):
            t=low+(node+1)*(high-low)/2
            for u_node,u_weight in zip(nodes,quad_weights):
                z,pt,nominal=sampler.coordinates(t,(u_node+1)/2)
                if nominal==0:continue
                variations=fr_variation_weights(sampler,self.source_momentum(.7,z,pt))
                for name in rates:
                    rates[name]+=nominal*variations[name]*weight*u_weight*(high-low)/4
        for name in rates:
            self.assertAlmostEqual(rates[name]/reference[name],1.,places=7)

    def test_variations_reject_missing_identity_wrong_mass_and_unsupported_support(self):
        sampler=model.SourceSampler(model.sampling_profile(.7))
        for field,value in [('id',4900113),('mechanism','rho-fragmentation'),
                            ('p4',[1.,0.,0.,1.]),('p4',[float('nan'),0.,0.,-1.])]:
            candidate=self.source_momentum(.7,.3,.2);candidate[field]=value
            with self.subTest(field=field,value=value),self.assertRaises(ValueError):
                fr_variation_weights(sampler,candidate)
        for z in (-.3,.001,.995):
            with self.subTest(z=z),self.assertRaisesRegex(ValueError,'beam direction or z support'):
                fr_variation_weights(sampler,self.source_momentum(.7,z,.2))
        endpoint=model.SourceSampler(model.sampling_profile(1.995))
        with self.assertRaisesRegex(ValueError,'no extrapolation'):
            fr_variation_weights(endpoint,self.source_momentum(1.995,.3,.2))

    def test_analytic_importance_bound_covers_resonances_variations_and_transverse_tails(self):
        rng=random.Random(1778)
        for mass in (.22,.7,.78,.9,1.019,1.4,1.8):
            for scale,variation in ((.5,'Lower'),(1.,'Central'),(2.,'Upper')):
                sampler=model.SourceSampler(model.sampling_profile(mass,lambda_p_gev=scale,variation=variation))
                for left,right,bound in sampler.cells:
                    for u in (1.e-12,.01,.1,.5,.9,1-1.e-12,rng.random()):
                        _,_,g=sampler.coordinates(left+(right-left)*rng.random(),u)
                        self.assertLessEqual(g,bound)

    def test_weighted_and_unweighted_source_reproduce_independent_rate_and_boost(self):
        sampler=model.SourceSampler(model.sampling_profile(.7))
        rows=[];weights=[];boosts=[]
        for trial in range(1,30001):
            c=sampler.candidate(trial,771,26.004691746871956)
            rows.append(dict(trial=trial,candidates=[c]))
            weights.append(c['weight_over_epsilon_squared'])
            boosts.append(math.hypot(*c['p4'][1:])/.7)
            self.assertLess(c['p4'][3],0.)
            self.assertAlmostEqual((c['p4'][0]-math.hypot(*c['p4'][1:]))*(c['p4'][0]+math.hypot(*c['p4'][1:])),.7**2,places=6)
        mean=math.fsum(weights)/len(weights)
        error=math.sqrt(math.fsum((w-mean)**2 for w in weights)/(len(weights)*(len(weights)-1)))
        # Independent adaptive dpT dz quadrature, recorded before adding this sampler.
        reference=1293147119.834457/(26.004691746871956*1.e9)
        self.assertLess(abs(mean-reference),5*error)
        from run_dark_photon_senscalc_gen import unweight
        selected,_=unweight(rows,772,sampler.coefficient_envelope)
        sample=[math.hypot(*c['p4'][1:])/.7 for c in selected]
        self.assertGreater(len(sample),1000)
        average=math.fsum(sample)/len(sample)
        se=math.sqrt(math.fsum((x-average)**2 for x in sample)/(len(sample)*(len(sample)-1)))
        self.assertLess(abs(average-6637.79),5*se)
        with self.assertRaisesRegex(ValueError,'technical unweighting bound'):
            sampler.candidate(1,771,24.)

    def test_author_ancillary_values_and_senscalc_complex_log_interpolation(self):
        # arXiv:2409.09123 ancillary table, also shipped unchanged by SensCalc.
        f1,f2=model.form_factors(.7)
        self.assertAlmostEqual(abs(f1-complex(9.32134752,1.96587286)),0.,places=12)
        self.assertAlmostEqual(abs(f2-complex(7.67516083,11.34118335)),0.,places=12)
        rows=model.form_factor_table(str(Path(model.__file__).resolve().parents[1]/'config/reference/senscalc'),'Central')
        low=next(row for row in rows if row[0]==.7);high=next(row for row in rows if row[0]==.705)
        middle=model.form_factors(math.sqrt(.7*.705))
        for value,a,b in zip(middle,low[1:],high[1:]):
            self.assertAlmostEqual(abs(value-cmath.exp((cmath.log(a)+cmath.log(b))/2)),0.,places=11)
        with self.assertRaisesRegex(ValueError,'no extrapolation'):model.form_factors(5.)

    def test_published_dirac_soft_limit_and_virtuality_suppression(self):
        z=.3;pt=1000.
        expected=model.ALPHA_EM/(math.pi*pt)*(1+(1-z)**2)/z
        value=model.splitting_density(.7,z,pt,factors=(1.+0j,0j),lambda_p_gev=1.e9)
        self.assertAlmostEqual(value/expected,1.,places=5)
        values=[model.splitting_density(.7,.3,.2,lambda_p_gev=scale) for scale in (.5,1.,2.)]
        self.assertEqual(values,sorted(values))
        self.assertTrue(all(value>0 for value in values))

    def test_pb_normalization_uses_the_NSD_cross_section_once(self):
        # Fixed ancillary form factors; reference formulas are Eqs 7,12,19,21,22.
        # This also checks the dpT^2 -> dpT Jacobian and the mb -> pb conversion.
        value=model.density_pb(.7,.3,.2,6800.)
        self.assertAlmostEqual(value/1179608903.0073035,1.,places=12)
        # Independent 70-digit evaluation of the paper's helicity coefficients,
        # using complex test currents to exercise the interference term.
        phase=model.splitting_density(.7,.3,.2,factors=(1+2j,-.2+.3j))
        self.assertAlmostEqual(phase/.001741789392298231963587748414467345566,1.,places=12)
        self.assertEqual(model.density_pb(.7,.9999999,100.,6800.),0.)
        with self.assertRaises(ValueError):model.density_pb(.7,.3,.2,6800.,model='unknown')
        with self.assertRaises(ValueError):model.density_pb(float('nan'),.3,.2,6800.)


if __name__=='__main__':unittest.main()
