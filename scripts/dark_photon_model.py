#!/usr/bin/env python3
"""Minimal visible hypercharge-mixing dark photon, without a Higgs portal.

The convention follows Curtin et al., arXiv:1412.0018, equations 2.1--2.12:
the hypercharge kinetic coefficient is +epsilon/cos(theta_W), so the low-mass
fermion coupling is +epsilon*e*Q. Changing the dark field's overall sign gives
the equally valid negative kinetic-term convention. Relative signs are fixed.

Mass inputs are physical eigenvalues. The fixed weak-angle scheme is the
authors' HAHM variable-MW scheme. Defaults reproduce HAHM v5's EW inputs,
not a fitted precision-EW scheme. A generator adapter must explicitly match
its alpha and weak-angle normalization to these inputs.

Only fermion tree widths are computed here. The total is a perturbative LO
diagnostic above 12 GeV, not a QCD-corrected physical width authority. Below
12 GeV a spectral/exclusive hadronic model is required and totals fail closed.
This module deliberately supplies no free-quark low-mass production model.
"""

from dataclasses import asdict, dataclass
import math


MODEL_REFERENCE = "https://arxiv.org/abs/1412.0018"
HAHM_COMMIT = "cd1394bc61e0dc593b12677304e33551923b440d"
HAHM_V5_SHA256 = "54fd17ed8e5b4f5cafc5d962ed54888d9e54aa6b37ac4c0f4d6e69b3041b3ab9"
HBAR_C_GEV_MM = 1.973269804e-13
PERTURBATIVE_MIN_MASS_GEV = 12.0
# Exact technical constants in the official Pythia 8.317 release source.
# They are generator limitations, never changes to detector electronics.
PYTHIA_8317_NARROW_MASS_GEV = 1e-6
PYTHIA_8317_PHASE_SPACE_MASS_MARGIN_GEV = 0.01


@dataclass(frozen=True)
class ElectroweakInputs:
    """Pinned HAHM v5 EW defaults; users must specify intentional overrides."""

    alpha_inverse: float = 127.9
    sin2_theta_w: float = 0.225
    m_z_gev: float = 91.188
    scheme: str = "fixed-weak-angle-physical-masses-tree"

    def __post_init__(self):
        if not math.isfinite(self.alpha_inverse) or self.alpha_inverse <= 0:
            raise ValueError("alpha_inverse must be finite and positive")
        if not math.isfinite(self.sin2_theta_w) or not 0 < self.sin2_theta_w < 1:
            raise ValueError("sin2_theta_w must be finite and strictly between 0 and 1")
        if not math.isfinite(self.m_z_gev) or self.m_z_gev <= 0:
            raise ValueError("m_z_gev must be finite and positive")
        if self.scheme != "fixed-weak-angle-physical-masses-tree":
            raise ValueError("unsupported electroweak input scheme")

    @property
    def e(self):
        return math.sqrt(4 * math.pi / self.alpha_inverse)

    @property
    def sin_theta_w(self):
        return math.sqrt(self.sin2_theta_w)

    @property
    def cos_theta_w(self):
        return math.sqrt(1 - self.sin2_theta_w)


DEFAULT_EW_INPUTS = ElectroweakInputs()


@dataclass(frozen=True)
class MixingParameters:
    mass_gev: float
    epsilon: float
    eta: float
    z0_mass_squared_gev2: float
    delta_squared: float
    tan_alpha: float
    sin_alpha: float
    cos_alpha: float
    em_coefficient: float
    neutral_current_coefficient: float
    eigenvalue_discriminant_gev4: float


@dataclass(frozen=True)
class FermionCouplings:
    """Interaction gamma_mu*(vector - axial*gamma5), in absolute units."""

    left: float
    right: float
    vector: float
    axial: float


@dataclass(frozen=True)
class Fermion:
    name: str
    pdg_id: int
    charge: float
    t3_left: float
    mass_gev: float
    colours: int


# These are HAHM v5's kinematic masses except the precise project muon mass.
# They are inputs for a tree-level comparison, not a QCD running-mass scheme.
FERMIONS = (
    Fermion("d", 1, -1 / 3, -0.5, 0.00467, 3),
    Fermion("u", 2, 2 / 3, 0.5, 0.0026, 3),
    Fermion("s", 3, -1 / 3, -0.5, 0.093, 3),
    Fermion("c", 4, 2 / 3, 0.5, 1.42, 3),
    Fermion("b", 5, -1 / 3, -0.5, 4.7, 3),
    Fermion("t", 6, 2 / 3, 0.5, 174.3, 3),
    Fermion("e", 11, -1, -0.5, 0.000511, 1),
    Fermion("nue", 12, 0, 0.5, 0, 1),
    Fermion("mu", 13, -1, -0.5, 0.1056583755, 1),
    Fermion("numu", 14, 0, 0.5, 0, 1),
    Fermion("tau", 15, -1, -0.5, 1.777, 1),
    Fermion("nutau", 16, 0, 0.5, 0, 1),
)


def mixing_parameters(mass_gev, epsilon, inputs=DEFAULT_EW_INPUTS):
    """Invert the 2x2 neutral mass matrix using stable eigenvalue branches.

    Define k=eta*sin(theta_W), z=m_Z^2, d=m_A'^2 and A=m_Z0^2.
    The matrix is A*[[1,-k],[-k,k^2+delta^2]]. Its trace/determinant
    give (1+k^2)*A^2-(z+d)*A+z*d=0. Rationalization avoids losing
    the lighter root and the tiny mixing angle to subtractive cancellation.
    """
    if not math.isfinite(mass_gev) or mass_gev <= 0:
        raise ValueError("dark-photon mass must be finite and positive")
    if not math.isfinite(epsilon) or epsilon == 0:
        raise ValueError("epsilon must be finite and nonzero")
    sw, cw = inputs.sin_theta_w, inputs.cos_theta_w
    if abs(epsilon) >= cw:
        raise ValueError("kinetic matrix requires abs(epsilon) < cos(theta_W)")
    z, d = inputs.m_z_gev**2, mass_gev**2
    eta = epsilon / (cw * math.sqrt(1 - epsilon**2 / cw**2))
    k = eta * sw
    discriminant = (z - d)**2 - 4 * k**2 * z * d
    # No favorable tolerance/clipping for unphysical or exactly degenerate cards.
    if discriminant <= 0 or not math.isfinite(discriminant):
        raise ValueError("physical A'/Z eigenvalues violate kinetic-mixing level splitting")
    sqrt_discriminant = math.sqrt(discriminant)
    if d < z:
        a = (z + d + sqrt_discriminant) / (2 * (1 + k**2))
    else:
        a = 2 * z * d / (z + d + sqrt_discriminant)
    delta_squared = z * d / a**2
    tan_alpha = -k * a / (a - d)
    cos_alpha = 1 / math.hypot(1, tan_alpha)
    sin_alpha = tan_alpha * cos_alpha
    # Express the coupling as EM plus neutral current. This retains the small
    # neutrino/axial component even where -sin(alpha)-k*cos(alpha) cancels.
    em = eta * cw * cos_alpha
    neutral = k * cos_alpha * d / (a - d)
    return MixingParameters(mass_gev, epsilon, eta, a, delta_squared,
                            tan_alpha, sin_alpha, cos_alpha, em, neutral,
                            discriminant)


def fermion_couplings(mass_gev, epsilon, charge, t3_left,
                     inputs=DEFAULT_EW_INPUTS):
    """Full hypercharge-mixing left/right couplings; no right-handed SM nu."""
    if not all(math.isfinite(x) for x in (charge, t3_left)):
        raise ValueError("fermion charges must be finite")
    mix = mixing_parameters(mass_gev, epsilon, inputs)
    g_z = inputs.e / (inputs.sin_theta_w * inputs.cos_theta_w)
    common = inputs.e * mix.em_coefficient * charge
    left = common + g_z * mix.neutral_current_coefficient * (
        t3_left - inputs.sin2_theta_w * charge)
    right = common - g_z * mix.neutral_current_coefficient * inputs.sin2_theta_w * charge
    vector = common + g_z * mix.neutral_current_coefficient * (
        t3_left / 2 - inputs.sin2_theta_w * charge)
    axial = g_z * mix.neutral_current_coefficient * t3_left / 2
    return FermionCouplings(left, right, vector, axial)


def pythia_couplings(mass_gev, epsilon, inputs=DEFAULT_EW_INPUTS):
    """Pythia NewGaugeBoson v/a: g/(4*cos(theta_W))*(v-a*gamma5).

    Inputs must equal Pythia's hard-process EW normalization. Universality
    should be disabled when using all entries returned here. This is a
    coupling translation only; it does not configure production/width/decay.
    """
    factor = 4 * inputs.sin_theta_w * inputs.cos_theta_w / inputs.e
    answer = {}
    for fermion in FERMIONS:
        couplings = fermion_couplings(mass_gev, epsilon, fermion.charge,
                                     fermion.t3_left, inputs)
        answer[fermion.name] = {"vector": factor * couplings.vector,
                                "axial": factor * couplings.axial}
    return answer


def partial_width_fermion(mass_gev, fermion_mass_gev, couplings, colours=1):
    """Exact tree-level massive-fermion width, zero at/below threshold."""
    if not math.isfinite(mass_gev) or mass_gev <= 0:
        raise ValueError("parent mass must be finite and positive")
    if not math.isfinite(fermion_mass_gev) or fermion_mass_gev < 0:
        raise ValueError("fermion mass must be finite and nonnegative")
    if colours not in (1, 3):
        raise ValueError("SM fermion colour multiplicity must be 1 or 3")
    if not all(math.isfinite(x) for x in (couplings.vector, couplings.axial)):
        raise ValueError("couplings must be finite")
    if mass_gev <= 2 * fermion_mass_gev:
        return 0.0
    ratio = (fermion_mass_gev / mass_gev)**2
    beta_squared = 1 - 4 * ratio
    return colours * mass_gev / (12 * math.pi) * math.sqrt(beta_squared) * (
        couplings.vector**2 * (1 + 2 * ratio) + couplings.axial**2 * beta_squared)


def fermion_widths_lo(mass_gev, epsilon, inputs=DEFAULT_EW_INPUTS,
                      masses_gev=None):
    """Individual tree widths; quark entries are absent below 12 GeV.

    A missing quark entry is unknown hadronic physics, never a zero width.
    This still permits exact lepton-threshold checks throughout the scan.
    """
    mixing_parameters(mass_gev, epsilon, inputs)
    masses_gev = dict(masses_gev or {})
    unknown = set(masses_gev) - {f.name for f in FERMIONS}
    if unknown:
        raise ValueError("unknown fermion masses: " + ", ".join(sorted(unknown)))
    answer = {}
    for fermion in FERMIONS:
        if fermion.colours == 3 and mass_gev < PERTURBATIVE_MIN_MASS_GEV:
            continue
        couplings = fermion_couplings(mass_gev, epsilon, fermion.charge,
                                     fermion.t3_left, inputs)
        answer[fermion.name] = partial_width_fermion(
            mass_gev, masses_gev.get(fermion.name, fermion.mass_gev),
            couplings, fermion.colours)
    return answer


def proper_decay_length_mm(total_width_gev):
    """Physical proper mean length from a supplied authoritative total width."""
    if not math.isfinite(total_width_gev) or total_width_gev <= 0:
        raise ValueError("total width must be finite and strictly positive")
    return HBAR_C_GEV_MM / total_width_gev


def fermion_widths_qcd_first_order(mass_gev, epsilon, alpha_s,
                                  inputs=DEFAULT_EW_INPUTS, masses_gev=None):
    """Independent Pythia perturbative-width check, valid only above 12 GeV.

    Pythia 8.317 ResonanceZprime uses exact massive tree widths and multiplies
    each quark contribution by 1+alphaS(mass^2)/pi. The supplied inputs must
    contain its actual alphaEM(mass^2), weak angle, Z mass and fermion masses.
    No QED, higher-order QCD, nonperturbative or near-Z interference correction
    is implied. This is a bounded pilot prescription, not final-width approval.
    """
    if mass_gev < PERTURBATIVE_MIN_MASS_GEV:
        raise ValueError("first-order QCD diagnostic requires mass >= 12 GeV")
    if not math.isfinite(alpha_s) or not 0 <= alpha_s < 1:
        raise ValueError("alpha_s must be finite and perturbative, 0 <= alpha_s < 1")
    widths = fermion_widths_lo(mass_gev, epsilon, inputs, masses_gev)
    quarks = {f.name for f in FERMIONS if f.colours == 3}
    return {name: width * (1 + alpha_s / math.pi) if name in quarks else width
            for name, width in widths.items()}


def validate_native_widths(native, epsilon, relative_tolerance=1e-10):
    """Close a natural Pythia nominal width table against independent algebra.

    ``native`` is the diagnostic dictionary from dark_photon_pythia. No
    special resWidth/resWidthOpen value is interpreted as a physical width:
    those contain propagator factors for gamma/Z/Z' and are not GeV widths.
    Only mWidth and onShellWidth are compared. This validates the implemented
    perturbative prescription, not its production rate or physics precision.
    """
    if not math.isfinite(relative_tolerance) or not 0 < relative_tolerance < 1:
        raise ValueError("relative_tolerance must be finite and between 0 and 1")
    alpha_em = float(native["alpha_em_at_mass"])
    if not math.isfinite(alpha_em) or alpha_em <= 0:
        raise ValueError("native alpha_EM must be finite and positive")
    ew = ElectroweakInputs(alpha_inverse=1 / alpha_em,
                           sin2_theta_w=float(native["sin2_theta_w"]),
                           m_z_gev=float(native["m_z_gev"]))
    masses = {f.name: float(native["fermion_masses_gev"][str(f.pdg_id)])
              for f in FERMIONS}
    expected = fermion_widths_qcd_first_order(
        float(native["mass_gev"]), epsilon, float(native["alpha_s_at_mass"]),
        ew, masses)
    by_id = {f.pdg_id: f.name for f in FERMIONS}
    actual = {}
    for channel in native["channels"]:
        products = channel["products"]
        width = float(channel["on_shell_width_gev"])
        if not math.isfinite(width) or width < 0:
            raise ValueError("native partial widths must be finite and nonnegative")
        if (len(products) == 2 and products[0] == -products[1]
                and abs(products[0]) in by_id):
            name = by_id[abs(products[0])]
            if name in actual:
                raise ValueError("duplicate native fermion channel: " + name)
            actual[name] = width
        elif width != 0:
            raise ValueError("unsupported nonzero native decay channel")
    if set(actual) != set(expected):
        raise ValueError("native fermion width table is incomplete")
    residuals = {}
    for name, expected_width in expected.items():
        actual_width = actual[name]
        if expected_width == 0:
            if actual_width != 0:
                raise ValueError("native width is nonzero below threshold: " + name)
            residuals[name] = 0.0
        else:
            residuals[name] = abs(actual_width / expected_width - 1)
            if residuals[name] > relative_tolerance:
                raise ValueError("native partial width does not close: " + name)
    expected_total = math.fsum(expected.values())
    actual_total = float(native["m_width_gev"])
    if not math.isfinite(actual_total) or actual_total <= 0:
        raise ValueError("native total width must be finite and positive")
    total_residual = abs(actual_total / expected_total - 1)
    if total_residual > relative_tolerance:
        raise ValueError("native total width does not close")
    return {
        "schema": "shift-dark-photon-native-width-algebra-closure-v1",
        "width_scope": "massive-fermion-tree-plus-first-order-QCD-pilot",
        "physical_width_validated": False,
        "production_rate_validated": False,
        "electroweak_inputs": asdict(ew),
        "alpha_s_at_mass": float(native["alpha_s_at_mass"]),
        "fermion_masses_gev": masses,
        "partial_widths_expected_gev": expected,
        "total_width_expected_gev": expected_total,
        "relative_partial_width_residuals": residuals,
        "maximum_relative_partial_width_residual": max(residuals.values()),
        "relative_total_width_residual": total_residual,
        "relative_tolerance": relative_tolerance,
        "proper_length_mm": proper_decay_length_mm(actual_total),
    }


def validate_native_proposal_support(native, global_mass_min_gev,
                                     global_mass_max_gev):
    """Reject Pythia 8.317 native DY proposals with collapsed mass support.

    ParticleDataEntry::initBWmass collapses mMin/mMax to m0 for Gamma<1e-6
    GeV. PhaseSpace2to1tauy then intersects global bounds with saved mMin,
    so a broad global window silently loses the below-pole half. Tiny widths
    also defeat floating-point Breit-Wigner integration. Width closure alone
    does not validate a generated hard-process rate.
    """
    if float(native["pythia_version"]) != 8.317:
        raise ValueError("native DY support guard is pinned to Pythia 8.317")
    mass, width = float(native["mass_gev"]), float(native["m_width_gev"])
    saved_min, saved_max = float(native["m_min_gev"]), float(native["m_max_gev"])
    values = (mass, width, saved_min, saved_max,
              global_mass_min_gev, global_mass_max_gev)
    if not all(math.isfinite(x) for x in values):
        raise ValueError("native mass, width and bounds must be finite")
    if width <= PYTHIA_8317_NARROW_MASS_GEV:
        raise ValueError("natural width is below the native Pythia DY support threshold")
    if not 0 <= saved_min < mass < saved_max:
        raise ValueError("native saved mass window must span both sides of the pole")
    if not 0 <= global_mass_min_gev < mass < global_mass_max_gev:
        raise ValueError("global proposal window must span both sides of the pole")
    lower = max(saved_min, global_mass_min_gev)
    upper = min(saved_max, global_mass_max_gev)
    if upper - lower <= PYTHIA_8317_PHASE_SPACE_MASS_MARGIN_GEV:
        raise ValueError("native hard-process mass window is numerically closed")
    return {
        "schema": "shift-dark-photon-native-proposal-support-v1",
        "pythia_version": 8.317,
        "natural_width_gev": width,
        "native_narrow_mass_threshold_gev": PYTHIA_8317_NARROW_MASS_GEV,
        "effective_mass_min_gev": lower,
        "effective_mass_max_gev": upper,
        "both_sides_of_pole_in_support": True,
        "production_rate_validated": False,
    }


def narrow_width_production_ratios(mass_gev, target_epsilon, proposal_epsilon,
                                  inputs=DEFAULT_EW_INPUTS):
    """Exact per-quark on-shell production factors in the narrow-width limit.

    This replaces a blind epsilon-squared rescaling. For a conditional muon
    decay also apply BR_target/BR_proposal once; lifetime/decay-window weights
    and polarization/finite-width validity are separate. This function cannot
    approve the native proposal rate or extrapolation near overlapping poles.
    """
    ratios = {}
    for fermion in FERMIONS:
        if fermion.colours != 3:
            continue
        target = fermion_couplings(mass_gev, target_epsilon, fermion.charge,
                                   fermion.t3_left, inputs)
        proposal = fermion_couplings(mass_gev, proposal_epsilon, fermion.charge,
                                     fermion.t3_left, inputs)
        ratios[fermion.name] = (target.left**2 + target.right**2) / (
            proposal.left**2 + proposal.right**2)
    return ratios


def model_table(mass_gev, epsilon, inputs=DEFAULT_EW_INPUTS, masses_gev=None):
    """Serialize the model and its explicitly LO-only perturbative diagnostic.

    This must not be used as a validated QCD width table. A production adapter
    must extract/freeze its QCD-corrected partial widths and reuse their total
    for branching fractions, the line shape, and proper lifetime.
    """
    if mass_gev < PERTURBATIVE_MIN_MASS_GEV:
        raise ValueError("a hadronic spectral/exclusive model is required below 12 GeV")
    mix = mixing_parameters(mass_gev, epsilon, inputs)
    widths = fermion_widths_lo(mass_gev, epsilon, inputs, masses_gev)
    total = math.fsum(widths.values())
    if not math.isfinite(total) or total <= 0:
        raise ValueError("invalid perturbative tree-level total width")
    return {
        "schema": "shift-dark-photon-model-diagnostic-v1",
        "benchmark": "minimal-visible-hypercharge-mixing-no-higgs-portal",
        "epsilon_convention": "low-mass-epsilon-times-e-times-J_EM",
        "reference": MODEL_REFERENCE,
        "hahm_commit": HAHM_COMMIT,
        "hahm_v5_archive_sha256": HAHM_V5_SHA256,
        "electroweak_inputs": asdict(inputs),
        "mixing": asdict(mix),
        "fermion_masses_gev": {
            f.name: (masses_gev or {}).get(f.name, f.mass_gev) for f in FERMIONS},
        "pythia_vector_axial_couplings": pythia_couplings(mass_gev, epsilon, inputs),
        "width_scope": "fermion-tree-level-only-QCD-uncorrected-diagnostic",
        "physical_width_validated": False,
        "partial_widths_lo_gev": widths,
        "total_width_lo_gev": total,
        "branching_fractions_lo": {name: width / total for name, width in widths.items()},
        "proper_length_lo_mm": proper_decay_length_mm(total),
    }
