# Code style: AI-code. Published proton-bremsstrahlung and source-unit controls.
import cmath
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import dark_photon_bremsstrahlung as model


class PublishedBremsstrahlung(unittest.TestCase):
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
