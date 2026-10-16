# Code style: AI-code. AI-generated kinematic and normalization closure checks.
import math
from pathlib import Path
import random
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from dark_photon_meson import MUON_MASS_GEV,radiative_branching,transverse_muon_density,sample_decay,dot,transition_form_factor_abs2,mean_lab_beta_gamma


class MesonTest(unittest.TestCase):
    def test_kinematic_endpoint_and_epsilon_scaling(self):
        self.assertEqual(radiative_branching(.135,.22,.001,.98,1.),0.)
        first=radiative_branching(.548,.3,.001,.4,1.)
        self.assertAlmostEqual(radiative_branching(.548,.3,.002,.4,1.)/first,4.)
        self.assertAlmostEqual(radiative_branching(.548,.3,.001,.4,2.)/first,4.)

    def test_transverse_density_is_normalized_with_massive_muons(self):
        for mass in [.212,.3,.8]:
            values=[transverse_muon_density(c,mass) for c in [-1.,0.,1.]]
            self.assertAlmostEqual((values[0]+4*values[1]+values[2])/3,1.)

    def test_explicit_measured_transition_models_normalize_and_stay_finite(self):
        eta=dict(kind='single-pole',lambda_gev=.749)
        prime=dict(kind='finite-width-multipole',lambda_gev=.802,gamma_gev=.113)
        self.assertEqual(transition_form_factor_abs2(0.,eta),1.)
        self.assertEqual(transition_form_factor_abs2(0.,prime),1.)
        self.assertAlmostEqual(transition_form_factor_abs2(.3,eta),(1-.3**2/.749**2)**-2)
        self.assertTrue(math.isfinite(transition_form_factor_abs2(.802,prime)))
        self.assertGreater(transition_form_factor_abs2(.802,prime),1.)
        with self.assertRaises(ValueError):transition_form_factor_abs2(.749,eta)
        with self.assertRaises(ValueError):transition_form_factor_abs2(.3,{})

    def test_four_momentum_and_mass_shells_close_in_lab(self):
        rng=random.Random(761016003)
        parent=(math.hypot(20.,.958),0.,0.,-20.)
        for mass in [.22,.3,.5,.8]:
            for _ in range(25):
                row=sample_decay(parent,mass,1000.,rng)
                pair=tuple(a+b for a,b in zip(row['muon_p4'],row['antimuon_p4']))
                total=tuple(a+b for a,b in zip(pair,row['photon_p4']))
                for actual,expected in zip(total,parent):self.assertAlmostEqual(actual,expected,places=8)
                self.assertAlmostEqual(pair[0]**2-dot(pair[1:],pair[1:]),mass**2,places=8)
                for name in ['muon_p4','antimuon_p4']:
                    v=row[name];self.assertAlmostEqual(v[0]**2-dot(v[1:],v[1:]),MUON_MASS_GEV**2,places=8)
                self.assertTrue(row['unconditional_decay'])
                self.assertGreater(row['proper_decay_length_mm'],0)

    def test_full_angular_boost_mean_at_rest_endpoint_and_high_energy(self):
        parent_mass = .95778
        mass = .3
        self.assertAlmostEqual(mean_lab_beta_gamma((parent_mass,0.,0.,0.),mass),
            (parent_mass**2-mass**2)/(2*parent_mass*mass))
        for momentum, mass in ((.1,.3),(1.,.3),(30.,.3),(6000.,.9),(1.,.95777999)):
            parent = (math.hypot(momentum,parent_mass),0.,0.,-momentum)
            m2 = parent[0]**2-momentum**2
            recoil = (m2-mass**2)/(2*math.sqrt(m2))
            a = parent[0]/math.sqrt(m2)*(m2+mass**2)/(2*math.sqrt(m2))
            b = momentum/math.sqrt(m2)*recoil
            # Independent deterministic angular integration, including the
            # almost closed eta-prime endpoint and an extreme lab boost.
            count = 20000
            numeric = math.fsum(math.sqrt(max(0.,(a+b*(-1+(i+.5)*2/count))**2-mass**2))
                               for i in range(count))/(count*mass)
            self.assertAlmostEqual(mean_lab_beta_gamma(parent,mass)/numeric,1.,places=7)


if __name__=='__main__':unittest.main()
