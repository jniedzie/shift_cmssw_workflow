# Code style: AI-code. AI-generated GEN lifetime and identity regression tests.
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from audit_dark_photon_gen import validate_lifetime_sample, validate_event_ids, proper_length_from_native_time


class AcceptedEventIdentityTest(unittest.TestCase):
    def setUp(self):
        self.legacy=dict(run_number=101,requested_events=3)
        self.accepted=dict(self.legacy,event_budget_mode='outputAccepted',maximum_framework_events=30)

    def test_new_mode_retains_generator_rejection_gaps(self):
        ids=[[101,1,2],[101,1,4],[101,1,30]]
        validate_event_ids(ids,self.accepted)
        with self.assertRaises(ValueError):
            validate_event_ids(ids,self.legacy)

    def test_duplicate_reversed_or_invalid_identity_fails(self):
        for ids in ([[101,1,2],[101,1,2],[101,1,3]],
                    [[101,1,2],[101,1,4],[101,1,3]],
                    [[101,1,0],[101,1,2],[101,1,3]],
                    [[102,1,1],[101,1,2],[101,1,3]],
                    [[101,2,1],[101,1,2],[101,1,3]],
                    [[101,1,1],[101,1,2],[101,1,31]],
                    [[101,1,True],[101,1,2],[101,1,3]]):
            with self.subTest(ids=ids),self.assertRaises(ValueError):
                validate_event_ids(ids,self.accepted)

    def test_exact_accepted_count_and_input_cap_are_required(self):
        with self.assertRaises(ValueError):
            validate_event_ids([[101,1,1],[101,1,2]],self.accepted)
        with self.assertRaises(ValueError):
            validate_event_ids([[101,1,1],[101,1,2],[101,1,3]],
                               dict(self.accepted,maximum_framework_events=31))
        with self.assertRaises(ValueError):
            validate_event_ids([[101,1,1],[101,1,2],[101,1,3]],
                               dict(self.accepted,event_budget_mode='unknown'))

    def test_legacy_exact_ids_still_pass(self):
        validate_event_ids([[101,1,1],[101,1,2],[101,1,3]],self.legacy)


class FiniteLifetimeAuditTest(unittest.TestCase):
    def test_prompt_lifetime_survives_sub_ulp_source_translation(self):
        native_delta_ct=1e-12
        shifted_start=-148000.
        shifted_end=shifted_start+native_delta_ct
        self.assertEqual(shifted_end-shifted_start,0.)
        length=proper_length_from_native_time(native_delta_ct,90.,900.)
        self.assertAlmostEqual(length,1e-13,places=25)
        self.assertTrue(validate_lifetime_sample([length]*100,length)['passed'])

    def test_native_lifetime_guard_remains_strict(self):
        for delta,mass,energy in [(0.,90.,900.),(-1e-12,90.,900.),(math.nan,90.,900.),
                                  (1.,0.,900.),(1.,90.,0.),(1.,90.,math.inf)]:
            with self.subTest(delta=delta,mass=mass,energy=energy),self.assertRaises(ValueError):
                proper_length_from_native_time(delta,mass,energy)

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
