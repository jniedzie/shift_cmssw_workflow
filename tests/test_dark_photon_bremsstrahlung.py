# Code style: AI-code. Published proton-bremsstrahlung and source-unit controls.
import cmath
import math
from pathlib import Path
import sys
import unittest
import random

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import dark_photon_bremsstrahlung as model


class PublishedBremsstrahlung(unittest.TestCase):
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
