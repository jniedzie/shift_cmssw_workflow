"""Frozen lifetime importance sampling and expected-only scan recommendations.

Lengths are proper decay lengths ell=c*t_rest, in mm, not lab flight distance
or lab time. Target mean length always comes from an externally supplied full
physical width. This module supplies no production rate, BR, detector response,
background, exposure, CLs calculation, or permission to inspect collision data.

References: Curtin et al. https://arxiv.org/abs/1412.0018 (physical widths),
Veach & Guibas https://graphics.stanford.edu/papers/combine/ (balance sampling),
Cowan et al. https://arxiv.org/abs/1007.1727 (expected likelihood sensitivity).
"""
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
import math
from typing import Optional

from dark_photon_model import proper_decay_length_mm


def _positive(value, name):
    if not math.isfinite(value) or value <= 0:
        raise ValueError(name + " must be finite and positive")


def _logsum(values):
    values = list(values)
    peak = max(values, default=-math.inf)
    if peak == -math.inf:
        return peak
    if not math.isfinite(peak):
        raise ValueError("density/moment exceeds the supported numerical range")
    return peak + math.log(math.fsum(math.exp(value-peak) for value in values))


def _exp(value):
    """Keep log results authoritative when a displayed number overflows."""
    if value == -math.inf:
        return 0.0
    try:
        result = math.exp(value)
    except OverflowError:
        return None
    return result if math.isfinite(result) else None


@dataclass(frozen=True)
class LifetimeProposal:
    """Freeze fixed independent counts at each proper-length anchor before GEN.

    Each anchor stratum samples its own normalized exponential over [0,infinity).
    q is the balance mixture with fraction n_i/N. Counts and context may not be
    changed after inspecting events. Context identifies one mass/production/
    kinematics/decay channel/response contract; changing any requires another
    proposal. Adaptive generations are separate frozen iterations.
    """
    anchor_lengths_mm: tuple
    counts: tuple
    context_id: str

    def __post_init__(self):
        object.__setattr__(self, "anchor_lengths_mm", tuple(self.anchor_lengths_mm))
        object.__setattr__(self, "counts", tuple(self.counts))
        if not self.context_id or not isinstance(self.context_id, str):
            raise ValueError("a nonempty frozen physics context id is required")
        if not self.counts or len(self.counts) != len(self.anchor_lengths_mm):
            raise ValueError("each proper-length anchor needs a frozen count")
        for length in self.anchor_lengths_mm:
            _positive(length, "anchor proper length")
        if any(type(count) is not int or count <= 0 for count in self.counts):
            raise ValueError("anchor counts must be positive integers")

    @property
    def total_events(self):
        return sum(self.counts)

    @property
    def fractions(self):
        return tuple(count/self.total_events for count in self.counts)

    def configuration(self):
        return dict(schema="shift-lifetime-fixed-anchor-proposal-v1",
                    scheme="fixed-count-independent-anchor-balance-mixture",
                    support="all proper lengths in [0,infinity); no decay conditioning",
                    lengths="proper c*t_rest in mm; never lab time/distance",
                    fractions=list(self.fractions), full_gen_events=self.total_events,
                    **asdict(self))

    def log_density(self, proper_length_mm):
        if not math.isfinite(proper_length_mm) or proper_length_mm < 0:
            raise ValueError("proper length must be finite and nonnegative")
        return _logsum(math.log(fraction)-math.log(length)-proper_length_mm/length
                       for fraction, length in zip(self.fractions, self.anchor_lengths_mm))

    def log_weight(self, proper_length_mm, target_total_width_gev):
        """log(f_target/q); production and conditional-BR factors are separate.

        Evaluate q_i/f_target directly to retain precision even when both
        densities are too small to represent. Existing vertices do not move.
        """
        if not math.isfinite(proper_length_mm) or proper_length_mm < 0:
            raise ValueError("proper length must be finite and nonnegative")
        target = proper_decay_length_mm(target_total_width_gev)
        relative_logs = [math.log(fraction)+math.log(target/anchor)
                         + proper_length_mm*(1/target-1/anchor)
                         for fraction, anchor in zip(self.fractions, self.anchor_lengths_mm)]
        return -_logsum(relative_logs)

    def finite_unconditioned_variance(self, target_total_width_gev):
        # f_target^2/q has an integrable tail iff 2/lambda_target>1/lambda_max.
        target = proper_decay_length_mm(target_total_width_gev)
        return target < 2*max(self.anchor_lengths_mm)


@dataclass(frozen=True)
class LifetimeEvent:
    event_id: str
    proper_length_mm: float
    anchor_index: int
    accepted: bool
    context_id: str


def _moment_summary(log_values, groups, counts):
    """Exact fixed-stratum sample variance, evaluated with scaled log moments."""
    total = sum(counts)
    peak = max(log_values, default=-math.inf)
    shifted = [value-peak for value in log_values] if peak != -math.inf else list(log_values)
    first = _logsum(shifted)
    second = _logsum(2*value for value in shifted)
    ess = 0.0 if first == -math.inf else min(float(total), math.exp(2*first-second))
    if math.isclose(ess,total,rel_tol=1e-14):
        ess = float(total)
    variance_parts = []
    variance_known = all(count >= 2 for count in counts)
    for index, count in enumerate(counts):
        values = [shifted[row] for row in groups[index]]
        stratum_peak = max(values)
        if stratum_peak == -math.inf or count < 2:
            continue
        # n_i*s_i^2 = n_i/(n_i-1)*(sum(y^2)-sum(y)^2/n_i).
        # Center rescaled values instead of subtracting nearly equal moments.
        scaled = [math.exp(value-stratum_peak) for value in values]
        scaled_mean = math.fsum(scaled)/count
        squared_deviations = math.fsum((value-scaled_mean)**2 for value in scaled)
        if squared_deviations > 0:
            variance_parts.append(2*stratum_peak+math.log(squared_deviations)+math.log(count/(count-1)))
    variance_numerator = _logsum(variance_parts)
    log_mean = peak+first-math.log(total)
    log_se = peak+variance_numerator/2-math.log(total)
    relative_error = (None if not variance_known or first == -math.inf
                      else _exp(variance_numerator/2-first))
    return dict(estimate=_exp(log_mean), log_estimate=None if log_mean == -math.inf else log_mean,
                standard_error=_exp(log_se) if variance_known else None,
                relative_mc_error=relative_error, effective_sample_size=ess,
                variance_estimator="sum_i n_i*s_i^2/N^2; independent frozen strata",
                variance_estimated=variance_known)


def estimate_lifetime_acceptance(proposal, events, target_total_width_gev,
                                 full_gen_events, min_selected_ess=100,
                                 max_relative_mc_error=0.10):
    """Estimate sum(f_target/q * accepted)/N with the complete GEN denominator.

    Include every generated event, including escapes and rejected selections.
    The proposal counts must match the ledger exactly; duplicated identities
    and mixed physics contexts are rejected. This is only a lifetime-dependent
    acceptance component. Multiplying production/BR or detector-sampling
    weights requires their own frozen estimator and closure.
    """
    _positive(min_selected_ess, "minimum selected ESS")
    _positive(max_relative_mc_error, "maximum relative MC error")
    events = list(events)
    if type(full_gen_events) is not int or full_gen_events != proposal.total_events or len(events) != full_gen_events:
        raise ValueError("the full frozen GEN ledger, including unselected events, is required")
    if len({event.event_id for event in events}) != len(events):
        raise ValueError("duplicate GEN event identities")
    groups = defaultdict(list)
    log_weights = []
    for index, event in enumerate(events):
        if not event.event_id or event.context_id != proposal.context_id:
            raise ValueError("event context differs from the frozen physics proposal")
        if type(event.anchor_index) is not int or not 0 <= event.anchor_index < len(proposal.counts):
            raise ValueError("invalid lifetime anchor assignment")
        if type(event.accepted) is not bool:
            raise ValueError("accepted must be an explicit bool for every GEN row")
        groups[event.anchor_index].append(index)
        log_weights.append(proposal.log_weight(event.proper_length_mm, target_total_width_gev))
    if tuple(len(groups[index]) for index in range(len(proposal.counts))) != proposal.counts:
        raise ValueError("observed anchor counts differ from the proposal frozen before generation")
    all_moments = _moment_summary(log_weights, groups, proposal.counts)
    selected_logs = [weight if event.accepted else -math.inf
                     for event, weight in zip(events, log_weights)]
    selected = _moment_summary(selected_logs, groups, proposal.counts)
    finite_variance = proposal.finite_unconditioned_variance(target_total_width_gev)
    relative = selected["relative_mc_error"]
    passed = (finite_variance and selected["effective_sample_size"] >= min_selected_ess
              and relative is not None and relative <= max_relative_mc_error)
    mean, error = all_moments["estimate"], all_moments["standard_error"]
    closure_pull = ((mean-1)/error if mean is not None and error is not None and error > 0
                    else 0.0 if mean == 1 else None)
    normalization_check = (independent_closure(1.,0.,mean,error)
                           if mean is not None and error is not None
                           else dict(passed=False, reason="normalization variance/numerical range unresolved"))
    supported = passed and normalization_check["passed"]
    return dict(schema="shift-lifetime-acceptance-estimate-v1", proposal=proposal.configuration(),
        target_total_width_gev=target_total_width_gev,
        target_proper_mean_length_mm=proper_decay_length_mm(target_total_width_gev),
        full_gen_events=full_gen_events, accepted_gen_events=sum(event.accepted for event in events),
        acceptance=selected, density_normalization=all_moments,
        density_normalization_pull=closure_pull,
        density_normalization_check=normalization_check,
        finite_unconditioned_weight_variance=finite_variance,
        precision_goals=dict(min_selected_ess=min_selected_ess, max_relative_mc_error=max_relative_mc_error),
        precision_goals_passed=passed,
        numerical_support_checks_passed=supported,
        zero_observed_yield_unresolved=selected["effective_sample_size"] == 0,
        recommendation=("add a longer frozen anchor" if not finite_variance
                        else "collect independent GEN/response evidence" if not supported
                        else "test independent midpoint/anchor closure before using contours"),
        production_factor_included=False, branching_factor_included=False,
        physical_sensitivity_validated=False)


def independent_closure(reference, reference_error, independent, independent_error,
                         max_standard_deviations=2.0, relative_tolerance=0.02):
    """Numerical goal: |difference| <= z*combined_SE + fixed_relative*scale."""
    if not all(math.isfinite(x) and x >= 0 for x in (reference, reference_error, independent, independent_error,
                                                    max_standard_deviations, relative_tolerance)):
        raise ValueError("closure values and tolerances must be finite and nonnegative")
    error = math.hypot(reference_error, independent_error)
    difference = abs(reference-independent)
    tolerance = max_standard_deviations*error+relative_tolerance*max(reference, independent)
    return dict(passed=difference <= tolerance, absolute_difference=difference,
                combined_standard_error=error, absolute_tolerance=tolerance,
                max_standard_deviations=max_standard_deviations, relative_tolerance=relative_tolerance)


@dataclass(frozen=True)
class ScanNode:
    mass_gev: float
    epsilon: float
    score: Optional[float] = None
    score_uncertainty: Optional[float] = None
    model_domain: Optional[str] = None
    closure_validated: bool = False

    def __post_init__(self):
        _positive(self.mass_gev, "scan mass")
        _positive(self.epsilon, "scan epsilon")
        if self.score is not None and not math.isfinite(self.score):
            raise ValueError("unknown scores use None, never NaN/infinity/zero substitution")
        if self.score_uncertainty is not None and (not math.isfinite(self.score_uncertainty) or self.score_uncertainty < 0):
            raise ValueError("score uncertainty must be finite and nonnegative or unknown")
        if self.model_domain is not None and not self.model_domain:
            raise ValueError("model domain must be nonempty or explicitly unknown")


def recommend_scan_refinements(nodes, min_epsilon_step_dex=0.02,
                               min_mass_step_dex=0.02,
                               max_epsilon_gap_dex=0.25, max_mass_gap_dex=0.25,
                               boundary_score_band=0.2, uncertainty_sigma=2.0,
                               curvature_threshold=0.3):
    """Deterministic recommendations for every supported crossing and island.

    External score is log(expected_CLs/0.1): negative meets the declared 90%
    CLs criterion, positive does not. These utilities do not compute CLs or
    choose a likelihood. The caller supplies expected-only scores with model
    validity and uncertainty evidence. Unknown nodes are never interpreted as
    zero; model-domain boundaries are never crossed by interpolation.

    Gap infill searches for missed islands, but cannot prove absence of islands
    narrower than the chosen grid. Mandatory threshold/resonance masses must
    be seeded explicitly. Requests are advice, never adaptive job allocation.
    """
    for name, value in (("epsilon resolution", min_epsilon_step_dex), ("mass resolution", min_mass_step_dex),
                        ("epsilon gap", max_epsilon_gap_dex), ("mass gap", max_mass_gap_dex),
                        ("boundary band", boundary_score_band), ("uncertainty sigma", uncertainty_sigma),
                        ("curvature threshold", curvature_threshold)):
        _positive(value, name)
    if max_epsilon_gap_dex < min_epsilon_step_dex or max_mass_gap_dex < min_mass_step_dex:
        raise ValueError("maximum gaps cannot be smaller than minimum refinement resolutions")
    nodes = sorted(nodes, key=lambda node: (node.mass_gev, node.epsilon))
    if len({(node.mass_gev,node.epsilon) for node in nodes}) != len(nodes):
        raise ValueError("duplicate scan nodes")
    requests = {}
    unresolved_same_sign = []
    def request(mass, epsilon, action, reason, axis=None, bracket=None):
        key = (mass, epsilon, action)
        row = requests.setdefault(key, dict(mass_gev=mass, epsilon=epsilon, action=action, reasons=[], brackets=[]))
        if reason not in row["reasons"]:
            row["reasons"].append(reason)
        if bracket is not None:
            record = dict(axis=axis, endpoints=bracket)
            if record not in row["brackets"]:
                row["brackets"].append(record)
    for node in nodes:
        if node.model_domain is None:
            request(node.mass_gev, node.epsilon, "resolve_model", "unknown model validity")
        elif node.score is None:
            request(node.mass_gev, node.epsilon, "evaluate", "unknown expected-only score")
        elif node.score_uncertainty is None or (node.score_uncertainty > 0 and
                abs(node.score) <= uncertainty_sigma*node.score_uncertainty):
            request(node.mass_gev, node.epsilon, "improve_precision", "boundary sign unresolved by MC uncertainty")
        if node.score is not None and not node.closure_validated:
            request(node.mass_gev, node.epsilon, "validate_closure", "independent midpoint/anchor closure missing")
    for axis, fixed_name, varying_name, minimum, maximum in (
            ("epsilon", "mass_gev", "epsilon", min_epsilon_step_dex, max_epsilon_gap_dex),
            ("mass", "epsilon", "mass_gev", min_mass_step_dex, max_mass_gap_dex)):
        lines = defaultdict(list)
        for node in nodes:
            lines[getattr(node,fixed_name)].append(node)
        for fixed, line in sorted(lines.items()):
            line.sort(key=lambda node:getattr(node,varying_name))
            def midpoint(left, right, reason):
                lower, upper = getattr(left,varying_name), getattr(right,varying_name)
                if math.log10(upper/lower) <= minimum+1e-12:
                    return
                value = math.exp((math.log(lower)+math.log(upper))/2)
                mass, epsilon = (fixed,value) if axis == "epsilon" else (value,fixed)
                request(mass, epsilon, "infill", reason, axis,
                        [[left.mass_gev,left.epsilon],[right.mass_gev,right.epsilon]])
            for left, right in zip(line,line[1:]):
                if left.model_domain is None or left.model_domain != right.model_domain:
                    continue
                gap = math.log10(getattr(right,varying_name)/getattr(left,varying_name))
                if gap > maximum+1e-12:
                    midpoint(left,right,"coarse gap; narrow islands remain possible")
                if any(node.score is None or node.score_uncertainty is None for node in (left,right)):
                    continue
                precise = all(node.score_uncertainty == 0 or abs(node.score) > uncertainty_sigma*node.score_uncertainty
                              for node in (left,right))
                if not precise:
                    continue
                if left.score*right.score <= 0:
                    midpoint(left,right,"expected boundary crossing or touch")
                else:
                    unresolved_same_sign.append(dict(axis=axis,
                        endpoints=[[left.mass_gev,left.epsilon],[right.mass_gev,right.epsilon]],
                        endpoint_scores=[left.score,right.score], gap_dex=gap,
                        status="same-sign endpoints; interior islands not excluded",
                        model_domain=left.model_domain))
                    if min(abs(left.score),abs(right.score)) <= boundary_score_band:
                        midpoint(left,right,"near expected boundary")
            for left, middle, right in zip(line,line[1:],line[2:]):
                if left.model_domain is None or not (left.model_domain == middle.model_domain == right.model_domain):
                    continue
                if any(node.score is None or node.score_uncertainty is None for node in (left,middle,right)):
                    continue
                x0,x1,x2 = [math.log10(getattr(node,varying_name)) for node in (left,middle,right)]
                interpolation = left.score+(right.score-left.score)*(x1-x0)/(x2-x0)
                uncertainty = uncertainty_sigma*math.sqrt(math.fsum(node.score_uncertainty**2 for node in (left,middle,right)))
                if abs(middle.score-interpolation) > curvature_threshold+uncertainty:
                    midpoint(left,middle,"resolved nonmonotonic structure/curvature")
                    midpoint(middle,right,"resolved nonmonotonic structure/curvature")
    # Axis scans alone can miss an island wholly inside a coarse 2D cell.
    # Explore its centre even when all four known corners have the same sign.
    by_coordinate = {(node.mass_gev,node.epsilon): node for node in nodes}
    mass_axis = sorted({node.mass_gev for node in nodes})
    epsilon_axis = sorted({node.epsilon for node in nodes})
    for lower_mass,upper_mass in zip(mass_axis,mass_axis[1:]):
        mass_gap = math.log10(upper_mass/lower_mass)
        if mass_gap <= min_mass_step_dex+1e-12:
            continue
        for lower_epsilon,upper_epsilon in zip(epsilon_axis,epsilon_axis[1:]):
            epsilon_gap = math.log10(upper_epsilon/lower_epsilon)
            if epsilon_gap <= min_epsilon_step_dex+1e-12:
                continue
            if mass_gap <= max_mass_gap_dex+1e-12 and epsilon_gap <= max_epsilon_gap_dex+1e-12:
                continue
            coordinates = [(lower_mass,lower_epsilon),(lower_mass,upper_epsilon),
                           (upper_mass,lower_epsilon),(upper_mass,upper_epsilon)]
            corners = [by_coordinate.get(coordinate) for coordinate in coordinates]
            if any(corner is None or corner.model_domain is None or corner.score is None
                   or corner.score_uncertainty is None for corner in corners):
                continue
            if len({corner.model_domain for corner in corners}) != 1:
                continue
            mass = math.exp((math.log(lower_mass)+math.log(upper_mass))/2)
            epsilon = math.exp((math.log(lower_epsilon)+math.log(upper_epsilon))/2)
            if any(math.isclose(node.mass_gev,mass,rel_tol=1e-12) and
                   math.isclose(node.epsilon,epsilon,rel_tol=1e-12) for node in nodes):
                continue
            request(mass,epsilon,"infill","2D cell interior discovery; corner signs do not exclude an island",
                    "cell",[list(coordinate) for coordinate in coordinates])
    return dict(schema="shift-expected-only-adaptive-scan-recommendations-v1",
        score_convention="log(expected_CLs/0.1); negative meets the external 90% CLs criterion",
        thresholds=dict(min_epsilon_step_dex=min_epsilon_step_dex,min_mass_step_dex=min_mass_step_dex,
                        max_epsilon_gap_dex=max_epsilon_gap_dex,max_mass_gap_dex=max_mass_gap_dex,
                        boundary_score_band=boundary_score_band,uncertainty_sigma=uncertainty_sigma,
                        curvature_threshold=curvature_threshold),
        recommendations=sorted(requests.values(),key=lambda row:(row["action"],row["mass_gev"],row["epsilon"])),
        unresolved_same_sign_intervals=unresolved_same_sign,
        mandatory_feature_constraints="explicitly seed physical thresholds/resonances and both sides; no generic interpolation across them",
        assumed_monotonic_in_epsilon=False, interpolation_across_model_domains=False,
        contour_validated=False, physical_sensitivity_validated=False,
        resolution_scope="recommendations only; unresolved/unknown nodes do not imply zero sensitivity")
