import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from audit_dark_photon_gen import validate_lifetime_sample


class FiniteLifetimeAuditTest(unittest.TestCase):
    def test_small_pilot_rejects_zero_and_proposal_scale_lifetimes(self):
        # Both cases passed the previous |mean_pull|<8 condition at N=20.
        for values in ([0.]*20,[1e-10]*20):
            with self.assertRaises(ValueError):
                validate_lifetime_sample(values,1.)

    def test_finite_n_gate_accepts_plausible_skewed_sample(self):
        values=[-math.log((i+.5)/20) for i in range(20)]
        result=validate_lifetime_sample(values,1.)
        self.assertTrue(result['passed'])
        self.assertLess(abs(result['mean_ratio']-1),.02)

    def test_rejects_wrong_long_lifetime(self):
        with self.assertRaises(ValueError):
            validate_lifetime_sample([10.]*20,1.)

    def test_invalid_draws_means_and_bounds(self):
        for values,mean,bound in (([],1.,1e-12),([-1.],1.,1e-12),([math.nan],1.,1e-12),
                                   ([math.inf],1.,1e-12),([1.],0.,1e-12),([1.],math.nan,1e-12),
                                   ([1.],1.,0.),([1.],1.,1.)):
            with self.assertRaises(ValueError):
                validate_lifetime_sample(values,mean,bound)

    def test_scale_invariance_and_exact_mean(self):
        for scale in (1e-9,1.,1e100):
            result=validate_lifetime_sample([scale]*20,scale)
            self.assertEqual(result['mean_ratio'],1.)
            self.assertEqual(result['log_tail_probability_upper_bound'],0.)


if __name__=='__main__':
    unittest.main()
