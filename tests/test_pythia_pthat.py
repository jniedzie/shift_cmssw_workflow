from pathlib import Path
import math
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from pythia_pthat import born_pthat


class PthatTest(unittest.TestCase):
    def test_massless_identity(self):
        self.assertEqual(born_pthat(111, 1.25, 16., 0., 0.), 1.25)

    def test_constituent_mass_migration(self):
        original, shat, mass = 1.001, 16., .33
        stored = original * (shat-mass*mass)/shat
        self.assertLess(stored, 1.)
        self.assertAlmostEqual(born_pthat(113, stored, shat, mass, 0.), original)

    def test_two_massive_light_quarks(self):
        factor = math.sqrt(1.-4*.5**2/16.)
        self.assertAlmostEqual(born_pthat(112, 2.*factor, 16., .5, .5), 2.)

    def test_heavy_matrix_element_not_rescaled(self):
        self.assertEqual(born_pthat(121, 2., 64., 1.5, 1.5), 2.)

    def test_onium_mass_already_in_matrix_element(self):
        self.assertAlmostEqual(born_pthat(401, 1.2, 64., 3.1, 0.), 1.2)
        shat, m3, m4 = 64., 3.1, .33
        ratio = math.sqrt((shat-m3*m3-m4*m4)**2-4*m3*m3*m4*m4)/(shat-m3*m3)
        self.assertAlmostEqual(born_pthat(403, 1.001*ratio, shat, m3, m4), 1.001)

    def test_unphysical_and_unowned_rejected(self):
        for args in ((111,1.,0.,0.,0.), (999,1.,16.,0.,0.),
                     (112,1.,1.,1.,1.), (112,1.,1.,3.,1.)):
            with self.assertRaises(ValueError):
                born_pthat(*args)


if __name__ == '__main__':
    unittest.main()
