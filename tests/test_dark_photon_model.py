import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from dark_photon_model import (
    DEFAULT_EW_INPUTS, ElectroweakInputs, FermionCouplings,
    FERMIONS, fermion_couplings, fermion_widths_lo,
    fermion_widths_qcd_first_order, mixing_parameters, model_table,
    partial_width_fermion, proper_decay_length_mm, pythia_couplings,
    narrow_width_production_ratios, validate_native_proposal_support,
    validate_native_widths,
)


class DarkPhotonModelTest(unittest.TestCase):
    def test_mass_matrix_has_requested_eigenvalues_on_both_sides(self):
        # Independently form and diagonalize the matrix rather than repeat the
        # inverse formula. Both branches and visible displaced couplings matter.
        for mass in (0.22, 15, 50, 90, 92, 150):
            for epsilon in (1e-10, 1e-5, 1e-2):
                mix = mixing_parameters(mass, epsilon)
                a = mix.z0_mass_squared_gev2
                k = mix.eta * DEFAULT_EW_INPUTS.sin_theta_w
                c = a * (mix.delta_squared + k*k)
                trace = a + c
                spread = math.hypot(a - c, 2*k*a)
                high = (trace + spread) / 2
                # Stable small eigenvalue from matrix determinant / large one.
                low = a*a*mix.delta_squared / high
                self.assertAlmostEqual(low / min(mass**2, DEFAULT_EW_INPUTS.m_z_gev**2), 1, places=12)
                self.assertAlmostEqual(high / max(mass**2, DEFAULT_EW_INPUTS.m_z_gev**2), 1, places=12)
                # The dark eigenvector must also diagonalize the matrix.
                self.assertAlmostEqual((-a*mix.sin_alpha-k*a*mix.cos_alpha) / mass**2,
                                       -mix.sin_alpha, places=12)

    def test_full_couplings_against_published_chiral_expression(self):
        ew = DEFAULT_EW_INPUTS
        for mass in (0.22, 15, 50, 90, 100):
            mix = mixing_parameters(mass, 0.005)
            g_z = ew.e / (ew.sin_theta_w * ew.cos_theta_w)
            for q, t3 in ((-1, -0.5), (2/3, 0.5), (-1/3, -0.5), (0, 0.5)):
                got = fermion_couplings(mass, 0.005, q, t3)
                for t, actual in ((t3, got.left), (0, got.right)):
                    hypercharge = q-t
                    independent = g_z * (-mix.sin_alpha * (
                        t*ew.cos_theta_w**2-hypercharge*ew.sin_theta_w**2)
                        + mix.eta*mix.cos_alpha*ew.sin_theta_w*hypercharge)
                    self.assertAlmostEqual(actual, independent, places=15)

    def test_tiny_epsilon_and_photon_limit_keep_axial_and_neutrino_modes(self):
        epsilon = 1e-10
        mass = 1e-5
        ew = DEFAULT_EW_INPUTS
        c = fermion_couplings(mass, epsilon, -1, -0.5)
        self.assertAlmostEqual(c.vector / (-epsilon*ew.e), 1, places=12)
        mix = mixing_parameters(mass, epsilon)
        self.assertNotEqual(mix.sin_alpha, 0)
        nu = fermion_couplings(mass, epsilon, 0, 0.5)
        # Low-mass axial/neutrino coupling scales as epsilon*m^2/m_Z^2.
        leading = epsilon*ew.e / (2*ew.cos_theta_w**2) * mass**2/ew.m_z_gev**2
        self.assertAlmostEqual(nu.left/leading, 1, places=12)
        self.assertEqual(nu.right, 0)
        self.assertAlmostEqual(c.axial / (-nu.left/2), 1, places=12)

    def test_massive_muon_threshold_and_photon_width(self):
        mu = 0.1056583755
        c = FermionCouplings(0.2, 0.2, 0.2, 0)
        for mass in (2*mu-1e-7, 2*mu):
            self.assertEqual(partial_width_fermion(mass, mu, c), 0)
        for mass in (2*mu+1e-7, 0.22, 15):
            beta = math.sqrt(1-4*mu**2/mass**2)
            expected = 0.2**2 * mass / (12*math.pi) * (1+2*mu**2/mass**2)*beta
            self.assertAlmostEqual(partial_width_fermion(mass, mu, c)/expected, 1, places=12)

    def test_width_against_independent_left_right_formula(self):
        mass, mf = 15, 4.7
        for left, right in ((0.3, 0.3), (0.3, -0.3), (0.3, 0)):
            c = FermionCouplings(left, right, (left+right)/2, (left-right)/2)
            expected = 3/(24*math.pi*mass)*math.sqrt(1-4*mf**2/mass**2) * (
                mass**2*(left**2+right**2)-mf**2*(-6*left*right+left**2+right**2))
            self.assertAlmostEqual(partial_width_fermion(mass, mf, c, 3)/expected, 1, places=12)

    def test_pythia_normalization_reconstructs_absolute_couplings(self):
        ew = ElectroweakInputs(alpha_inverse=137.035999084, sin2_theta_w=0.2312)
        translated = pythia_couplings(15, 1e-8, ew)
        prefactor = ew.e / (4*ew.sin_theta_w*ew.cos_theta_w)
        absolute = fermion_couplings(15, 1e-8, -1, -0.5, ew)
        self.assertAlmostEqual(prefactor*translated["mu"]["vector"]/absolute.vector, 1, places=14)
        self.assertAlmostEqual(prefactor*translated["mu"]["axial"]/absolute.axial, 1, places=14)

    def test_widths_brs_lifetime_and_negative_epsilon(self):
        table = model_table(15, 1e-8)
        widths = table["partial_widths_lo_gev"]
        self.assertTrue(all(width >= 0 and math.isfinite(width) for width in widths.values()))
        self.assertEqual(widths["t"], 0)
        self.assertGreater(widths["numu"], 0)
        self.assertAlmostEqual(math.fsum(table["branching_fractions_lo"].values()), 1, places=14)
        self.assertFalse(table["physical_width_validated"])
        negative = fermion_widths_lo(15, -1e-8)
        self.assertEqual(widths, negative)
        positive = fermion_widths_lo(15, 1e-9)
        for name, width in widths.items():
            if width:
                self.assertAlmostEqual(positive[name]/width, 0.01, places=13)
        self.assertAlmostEqual(proper_decay_length_mm(1.973269804e-13), 1, places=14)

    def test_missing_hadronic_physics_fails_closed(self):
        for mass in (0.2114, 0.22, 1, 5, 11.99):
            leptonic = fermion_widths_lo(mass, 1e-8)
            self.assertIn("mu", leptonic)
            self.assertNotIn("u", leptonic)
            with self.assertRaises(ValueError):
                model_table(mass, 1e-8)

    def test_first_order_qcd_matches_independent_pythia_normalization(self):
        ew = ElectroweakInputs(alpha_inverse=130.535034178901,
                              sin2_theta_w=0.2312, m_z_gev=91.1876)
        masses = {f.name: f.mass_gev for f in FERMIONS}
        mass, epsilon, alpha_s = 15, 1e-7, 0.16280711044575402
        got = fermion_widths_qcd_first_order(mass, epsilon, alpha_s, ew, masses)
        normalized = pythia_couplings(mass, epsilon, ew)
        prefactor = mass / (48 * ew.alpha_inverse * ew.sin2_theta_w * (1-ew.sin2_theta_w))
        for f in FERMIONS:
            if 2*f.mass_gev >= mass:
                self.assertEqual(got[f.name], 0)
                continue
            r = (f.mass_gev/mass)**2
            beta = math.sqrt(1-4*r)
            va = normalized[f.name]
            expected = prefactor*beta*(va["vector"]**2*(1+2*r)+va["axial"]**2*beta**2)
            if f.colours == 3:
                expected *= 3*(1+alpha_s/math.pi)
            self.assertAlmostEqual(got[f.name]/expected, 1, places=12)

    def test_native_closure_rejects_changed_missing_and_extra_widths(self):
        import copy
        ew = DEFAULT_EW_INPUTS
        widths = fermion_widths_qcd_first_order(15, 1e-7, 0.163)
        native = {
            "mass_gev": 15, "alpha_em_at_mass": 1/ew.alpha_inverse,
            "alpha_s_at_mass": 0.163, "sin2_theta_w": ew.sin2_theta_w,
            "m_z_gev": ew.m_z_gev,
            "fermion_masses_gev": {str(f.pdg_id): f.mass_gev for f in FERMIONS},
            "m_width_gev": math.fsum(widths.values()),
            "channels": [{"products": [f.pdg_id, -f.pdg_id],
                          "on_shell_width_gev": widths[f.name]} for f in FERMIONS],
            "effective_resonance_function": 1e18,
        }
        result = validate_native_widths(native, 1e-7)
        self.assertLess(result["maximum_relative_partial_width_residual"], 1e-14)
        self.assertFalse(result["production_rate_validated"])
        changed = copy.deepcopy(native)
        changed["channels"][0]["on_shell_width_gev"] *= 1.01
        missing = copy.deepcopy(native)
        missing["channels"].pop()
        extra = copy.deepcopy(native)
        extra["channels"].append({"products": [24, -24], "on_shell_width_gev": 1e-20})
        for invalid in (changed, missing, extra):
            with self.assertRaises(ValueError):
                validate_native_widths(invalid, 1e-7)
        with self.assertRaises(ValueError):
            fermion_widths_qcd_first_order(5, 1e-7, 0.163)
        for alpha_s in (-1, 1, float("nan")):
            with self.assertRaises(ValueError):
                fermion_widths_qcd_first_order(15, 1e-7, alpha_s)

    def test_invalid_physical_and_numerical_inputs(self):
        for mass, epsilon in ((-1, 1e-8), (float("nan"), 1e-8), (15, 0),
                              (15, float("inf")), (15, 1), (91.188, 1e-8),
                              (91.187, 0.01)):
            with self.assertRaises(ValueError):
                mixing_parameters(mass, epsilon)
        for inputs in ({"alpha_inverse": 0}, {"sin2_theta_w": 1},
                       {"m_z_gev": float("nan")}, {"scheme": "unknown"}):
            with self.assertRaises(ValueError):
                ElectroweakInputs(**inputs)
        for width in (0, -1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                proper_decay_length_mm(width)

    def test_native_mass_support_collapse_is_rejected(self):
        good = {"pythia_version": 8.317, "mass_gev": 15,
                "m_width_gev": 2e-6, "m_min_gev": 14.85, "m_max_gev": 15.15}
        result = validate_native_proposal_support(good, 14.85, 15.15)
        self.assertTrue(result["both_sides_of_pole_in_support"])
        for update in ({"m_width_gev": 2e-7}, {"m_min_gev": 15, "m_max_gev": 15},
                       {"pythia_version": 8.318}, {"m_width_gev": float("nan")}):
            with self.assertRaises(ValueError):
                validate_native_proposal_support(dict(good, **update), 12, 18)
        with self.assertRaises(ValueError):
            validate_native_proposal_support(good, 14.999, 15.001)

    def test_exact_quark_rate_factors_and_photon_like_scaling(self):
        ew = DEFAULT_EW_INPUTS
        ratios = narrow_width_production_ratios(15, 1e-7, 0.005)
        self.assertAlmostEqual(ratios["u"], ratios["c"], places=16)
        self.assertNotEqual(ratios["u"], ratios["d"])
        for fermion in FERMIONS:
            if fermion.colours != 3:
                continue
            target = fermion_couplings(15, 1e-7, fermion.charge, fermion.t3_left)
            proposal = fermion_couplings(15, 0.005, fermion.charge, fermion.t3_left)
            # Alternate vector/axial representation of the partonic strength.
            independent = (target.vector**2+target.axial**2)/(proposal.vector**2+proposal.axial**2)
            self.assertAlmostEqual(ratios[fermion.name]/independent, 1, places=14)
        tiny = narrow_width_production_ratios(0.22, 1e-8, 1e-7, ew)
        for ratio in tiny.values():
            self.assertAlmostEqual(ratio, 0.01, places=13)


if __name__ == "__main__":
    unittest.main()
