# Code style: AI-code. AI-generated kinematic and normalization closure checks.
import math
from pathlib import Path
import random
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from dark_photon_meson import MUON_MASS_GEV,radiative_branching,transverse_muon_density,sample_decay,dot


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


if __name__=='__main__':unittest.main()
