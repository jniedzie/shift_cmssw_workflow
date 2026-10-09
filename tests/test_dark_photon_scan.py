import math
from pathlib import Path
import random
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from dark_photon_model import HBAR_C_GEV_MM
from dark_photon_scan import (LifetimeEvent, LifetimeProposal, ScanNode,
    estimate_lifetime_acceptance, independent_closure, recommend_scan_refinements)


class LifetimeScanTest(unittest.TestCase):
    def test_single_anchor_density_ratio_and_long_length_stability(self):
        proposal=LifetimeProposal((2.,),(100,),"m15-fixed-kinematics-v1")
        for length in (0,0.5,2,20,1e7):
            self.assertEqual(proposal.log_weight(length,HBAR_C_GEV_MM/2),0)
            expected=math.log(2/3)+length*(1/2-1/3)
            self.assertAlmostEqual(proposal.log_weight(length,HBAR_C_GEV_MM/3),expected,places=8)
        self.assertTrue(proposal.finite_unconditioned_variance(HBAR_C_GEV_MM/3.99))
        self.assertFalse(proposal.finite_unconditioned_variance(HBAR_C_GEV_MM/4))

    def test_mixture_density_and_exact_target_density_identity(self):
        proposal=LifetimeProposal((0.2,2,20),(10,30,60),"same-mass-and-production")
        for length in (0,0.1,1,10,100,1000):
            q=sum(fraction/anchor*math.exp(-length/anchor)
                  for fraction,anchor in zip(proposal.fractions,proposal.anchor_lengths_mm))
            self.assertAlmostEqual(math.exp(proposal.log_density(length))/q,1,places=13)
            actual=proposal.log_density(length)+proposal.log_weight(length,HBAR_C_GEV_MM/3)
            expected=-math.log(3)-length/3
            self.assertAlmostEqual(actual,expected,places=12)

    def test_analytic_interval_acceptance_with_frozen_stratified_ledger(self):
        proposal=LifetimeProposal((0.2,2,20),(6000,6000,6000),"m15-dy-mumu-common-response")
        rng=random.Random(24681357)
        rows=[]
        for anchor,(mean,count) in enumerate(zip(proposal.anchor_lengths_mm,proposal.counts)):
            for index in range(count):
                length=-mean*math.log1p(-rng.random())
                rows.append(LifetimeEvent(f"{anchor}:{index}",length,anchor,1<=length<=8,proposal.context_id))
        report=estimate_lifetime_acceptance(proposal,rows,HBAR_C_GEV_MM/3,len(rows))
        expected=math.exp(-1/3)-math.exp(-8/3)
        measured=report["acceptance"]
        self.assertLess(abs(measured["estimate"]-expected),4*measured["standard_error"])
        self.assertLess(abs(report["density_normalization"]["estimate"]-1),4*report["density_normalization"]["standard_error"])
        self.assertTrue(report["precision_goals_passed"])
        self.assertTrue(report["numerical_support_checks_passed"])
        self.assertFalse(report["physical_sensitivity_validated"])

    def test_stratified_variance_against_direct_small_sample_algebra(self):
        proposal=LifetimeProposal((1,4),(3,3),"context")
        lengths=(0.1,0.8,2.,0.5,5.,12.)
        rows=[LifetimeEvent(str(index),length,index//3,index%2==0,"context")
              for index,length in enumerate(lengths)]
        report=estimate_lifetime_acceptance(proposal,rows,HBAR_C_GEV_MM/2,6,min_selected_ess=1,max_relative_mc_error=1)
        values=[math.exp(proposal.log_weight(row.proper_length_mm,HBAR_C_GEV_MM/2)) if row.accepted else 0 for row in rows]
        variance=0
        for start in (0,3):
            stratum=values[start:start+3]
            mean=sum(stratum)/3
            sample_variance=sum((value-mean)**2 for value in stratum)/2
            variance+=3*sample_variance/36
        self.assertAlmostEqual(report["acceptance"]["estimate"],sum(values)/6,places=14)
        self.assertAlmostEqual(report["acceptance"]["standard_error"],math.sqrt(variance),places=14)

    def test_missing_escape_duplicate_context_and_wrong_counts_rejected(self):
        proposal=LifetimeProposal((1,4),(2,2),"context")
        good=[LifetimeEvent(str(index),1,index//2,False,"context") for index in range(4)]
        for rows in (good[:-1],good[:3]+[good[0]],
                     good[:3]+[LifetimeEvent("3",1,1,False,"other-mass")],
                     [LifetimeEvent(str(index),1,0,False,"context") for index in range(4)]):
            with self.assertRaises(ValueError):
                estimate_lifetime_acceptance(proposal,rows,HBAR_C_GEV_MM,4)
        report=estimate_lifetime_acceptance(proposal,good,HBAR_C_GEV_MM,4)
        self.assertTrue(report["zero_observed_yield_unresolved"])
        self.assertFalse(report["precision_goals_passed"])
        self.assertIsNone(report["acceptance"]["relative_mc_error"])
        import json
        json.dumps(report,allow_nan=False)

    def test_normalization_failure_cannot_look_numerically_supported(self):
        proposal=LifetimeProposal((1,),(200,),"context")
        rows=[LifetimeEvent(str(index),0,0,True,"context") for index in range(200)]
        # An impossible all-zero-length sample has apparently excellent ESS
        # and zero estimated error, but fails the known density normalization.
        report=estimate_lifetime_acceptance(proposal,rows,HBAR_C_GEV_MM/1.5,200)
        self.assertTrue(report["precision_goals_passed"])
        self.assertFalse(report["density_normalization_check"]["passed"])
        self.assertFalse(report["numerical_support_checks_passed"])

    def test_single_anchor_unity_moments_and_extreme_log_scaling(self):
        proposal=LifetimeProposal((2,),(200,),"context")
        rows=[LifetimeEvent(str(index),index/10,0,True,"context") for index in range(200)]
        result=estimate_lifetime_acceptance(proposal,rows,HBAR_C_GEV_MM/2,200)
        self.assertEqual(result["acceptance"]["estimate"],1)
        self.assertEqual(result["acceptance"]["standard_error"],0)
        self.assertEqual(result["acceptance"]["effective_sample_size"],200)
        import json
        json.dumps(result,allow_nan=False)
        from dark_photon_scan import _moment_summary
        # Huge common log offsets must not overflow squared-weight moments.
        huge=_moment_summary([1e200]*200,{0:list(range(200))},(200,))
        self.assertEqual(huge["effective_sample_size"],200)
        self.assertEqual(huge["relative_mc_error"],0)
        self.assertIsNone(huge["estimate"])
        json.dumps(huge,allow_nan=False)

    def test_unknown_variance_and_infinite_tail_variance_do_not_pass(self):
        proposal=LifetimeProposal((1,4),(1,1),"context")
        rows=[LifetimeEvent(str(index),1,index,True,"context") for index in range(2)]
        result=estimate_lifetime_acceptance(proposal,rows,HBAR_C_GEV_MM/8,2,min_selected_ess=1)
        self.assertFalse(result["finite_unconditioned_weight_variance"])
        self.assertFalse(result["precision_goals_passed"])
        self.assertFalse(result["acceptance"]["variance_estimated"])

    def test_independent_closure_combines_uncertainty_and_fixed_tolerance(self):
        self.assertTrue(independent_closure(0.1,0.001,0.104,0.001)["passed"])
        self.assertFalse(independent_closure(0.1,0.001,0.12,0.001)["passed"])


class AdaptiveScanTest(unittest.TestCase):
    def node(self,mass,epsilon,score,error=0.001,domain="validated-model-v1",closure=True):
        return ScanNode(mass,epsilon,score,error,domain,closure)

    def test_every_crossing_of_nonmonotonic_islands_is_refined(self):
        nodes=[self.node(15,epsilon,score) for epsilon,score in
               ((1e-9,1),(1e-8,-1),(1e-7,1),(1e-6,-1),(1e-5,1))]
        report=recommend_scan_refinements(nodes)
        crossing=[row for row in report["recommendations"] if "expected boundary crossing or touch" in row["reasons"]]
        self.assertEqual(len(crossing),4)
        for row,(left,right) in zip(crossing,zip(nodes,nodes[1:])):
            self.assertAlmostEqual(row["epsilon"]/math.sqrt(left.epsilon*right.epsilon),1,places=13)
        self.assertFalse(report["assumed_monotonic_in_epsilon"])
        self.assertEqual(report,recommend_scan_refinements(reversed(nodes)))

    def test_unknown_and_uncertain_nodes_are_never_zero_sensitivity(self):
        nodes=[self.node(15,1e-8,None,None),self.node(15,1.5e-8,-0.01,0.02)]
        rows=recommend_scan_refinements(nodes)["recommendations"]
        self.assertTrue(any(row["action"]=="evaluate" and row["epsilon"]==1e-8 for row in rows))
        self.assertTrue(any(row["action"]=="improve_precision" for row in rows))
        self.assertFalse(any(row["action"]=="infill" for row in rows))

    def test_no_interpolation_across_model_validity_domains(self):
        nodes=[self.node(5,1e-7,-1,domain="spectral-model"),
               self.node(15,1e-7,1,domain="perturbative-model"),
               self.node(50,1e-7,None,None,domain=None,closure=False)]
        rows=recommend_scan_refinements(nodes)["recommendations"]
        self.assertFalse(any(row["action"]=="infill" for row in rows))
        self.assertTrue(any(row["action"]=="resolve_model" for row in rows))

    def test_gap_curvature_and_declared_resolution(self):
        nodes=[self.node(15,epsilon,score) for epsilon,score in ((1e-8,2),(1e-7,0.4),(1e-6,2))]
        rows=recommend_scan_refinements(nodes)["recommendations"]
        self.assertTrue(any("resolved nonmonotonic structure/curvature" in row["reasons"] for row in rows))
        tiny=[self.node(15,1e-7,-1),self.node(15,1e-7*10**0.019,1)]
        self.assertFalse(recommend_scan_refinements(tiny)["recommendations"])

    def test_same_sign_half_decade_intervals_get_discovery_midpoints(self):
        nodes=[self.node(15,10**exponent,2) for exponent in (-10,-9.5,-9)]
        report=recommend_scan_refinements(nodes)
        infill=[row for row in report["recommendations"] if row["action"]=="infill"]
        self.assertEqual(len(infill),2)
        self.assertEqual(len(report["unresolved_same_sign_intervals"]),2)
        self.assertTrue(all("interior islands not excluded" in row["status"]
                            for row in report["unresolved_same_sign_intervals"]))

    def test_unknown_closure_and_duplicate_nodes(self):
        node=self.node(15,1e-7,1,closure=False)
        rows=recommend_scan_refinements([node])["recommendations"]
        self.assertEqual(rows[0]["action"],"validate_closure")
        with self.assertRaises(ValueError):
            recommend_scan_refinements([node,node])

    def test_same_sign_known_2d_cell_gets_geometric_centre_discovery(self):
        nodes=[self.node(mass,epsilon,2) for mass in (15,50) for epsilon in (1e-8,1e-7)]
        report=recommend_scan_refinements(nodes)
        cells=[row for row in report["recommendations"]
               if any(bracket["axis"]=="cell" for bracket in row["brackets"])]
        self.assertEqual(len(cells),1)
        self.assertAlmostEqual(cells[0]["mass_gev"]/math.sqrt(15*50),1,places=13)
        self.assertAlmostEqual(cells[0]["epsilon"]/math.sqrt(1e-8*1e-7),1,places=13)
        self.assertEqual(len(cells[0]["brackets"][0]["endpoints"]),4)
        self.assertFalse(report["contour_validated"])

    def test_unknown_or_mixed_domain_2d_cell_is_not_interpolated(self):
        good=[self.node(mass,epsilon,2) for mass in (15,50) for epsilon in (1e-8,1e-7)]
        for replacement in (self.node(50,1e-7,None,None),
                            self.node(50,1e-7,2,domain="other-model")):
            nodes=good[:-1]+[replacement]
            rows=recommend_scan_refinements(nodes)["recommendations"]
            self.assertFalse(any(bracket["axis"]=="cell" for row in rows for bracket in row["brackets"]))


if __name__=="__main__":
    unittest.main()
