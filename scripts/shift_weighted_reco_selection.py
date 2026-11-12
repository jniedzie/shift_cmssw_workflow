# Code style: AI-code.
"""Experimental GEN-weight admission planner; no production default or draw.

Apply only to new, complete, unsampled GEN cohorts. Known promising events
receive probability proportional to abs(native W)/sqrt(predicted cost), with
a positive floor. Unknown paths and explicitly protected GEN tail groups stay
at probability one. Rejected controls keep their separate probability.
Downstream weights remain W/p, once.

Predicted cost, any GEN tail-group lookup and the calibration must be frozen
before validation or SIM. A tail flag must come from GEN properties and a
training-only response model, never that event's reconstructed outcome.
Neither reconstructed outcomes nor measured material survival enter this rule.
The numerical planner alone does not qualify a production or its publication.
"""
import math


GRID = 1 << 52


def _finite(value, name, *, positive=False):
    if (type(value) not in (int, float) or not math.isfinite(value)
            or (positive and value <= 0)):
        raise ValueError(name + ' must be finite' + (' and positive' if positive else ''))
    return value


def _floor(value):
    _finite(value, 'minimum primary probability', positive=True)
    if value > 1 or value < 1 / GRID:
        raise ValueError('Primary probability floor must be on the supported positive scale')
    return math.ceil(value * GRID) / GRID


def primary_probability(native_weight, estimated_seconds, protected_unknown,
                        weight_scale, minimum_probability=.01, *, protected_tail=False):
    """Return the exact 52-bit inclusion probability, without changing W."""
    _finite(native_weight, 'native GEN weight')
    _finite(estimated_seconds, 'GEN-predicted reconstruction seconds', positive=True)
    _finite(weight_scale, 'frozen weight scale', positive=True)
    floor = _floor(minimum_probability)
    if type(protected_unknown) is not bool or type(protected_tail) is not bool:
        raise ValueError('Unknown-path and GEN tail-group protection must be explicit')
    if protected_unknown or protected_tail:
        return 1.
    score = abs(native_weight) / math.sqrt(estimated_seconds)
    value = max(floor, min(1., score / weight_scale))
    return math.ceil(value * GRID) / GRID


def inclusion_probability(native_weight, estimated_seconds, decision,
                          weight_scale, control_probability,
                          minimum_probability=.01, *, protected_tail=False):
    """Keep the physical GEN filter separate from computational thinning."""
    if (type(decision.get('passes_filter')) is not bool
            or type(decision.get('protected_unknown')) is not bool
            or (decision['protected_unknown'] and not decision['passes_filter'])):
        raise ValueError('Need a consistent, explicit GEN filter decision')
    _finite(control_probability, 'unchanged rejected-control probability', positive=True)
    if control_probability > 1:
        raise ValueError('Rejected-control probability cannot exceed one')
    if not decision['passes_filter']:
        _finite(native_weight, 'native GEN weight')
        _finite(estimated_seconds, 'GEN-predicted reconstruction seconds', positive=True)
        return control_probability
    return primary_probability(native_weight, estimated_seconds,
        decision['protected_unknown'], weight_scale, minimum_probability,
        protected_tail=protected_tail)


def fit_weight_scale(candidates, budget_fraction, minimum_probability=.01):
    """Calibrate on GEN primary candidates, retaining unknown-path protection.

    The budget is relative to reconstructing all these primary candidates.
    Rejected controls have their own unchanged budget and must be added in
    cost comparisons. Inputs contain no reconstructed pass labels. Calibrate
    per process/bin on training sources, then freeze for independent sources.
    """
    _finite(budget_fraction, 'primary cost fraction', positive=True)
    floor = _floor(minimum_probability)
    if budget_fraction > 1 or not candidates:
        raise ValueError('Need nonempty GEN candidates and a cost fraction at most one')
    costs, scores, protected = [], [], []
    for row in candidates:
        _finite(row['native_weight'], 'native GEN weight')
        cost = _finite(row['estimated_seconds'], 'GEN-predicted reconstruction seconds', positive=True)
        if (type(row['protected_unknown']) is not bool
                or type(row.get('protected_tail', False)) is not bool):
            raise ValueError('Unknown-path and GEN tail-group protection must be explicit')
        costs.append(cost)
        scores.append(abs(row['native_weight']) / math.sqrt(cost))
        protected.append(row['protected_unknown'] or row.get('protected_tail', False))
    total = math.fsum(costs)
    target = total * budget_fraction
    lower_cost = math.fsum(c * (1. if unknown else floor)
                           for c, unknown in zip(costs, protected))
    if target < lower_cost:
        raise ValueError('Requested budget cannot preserve protected candidates and the positive floor')

    def expected(scale):
        return math.fsum(c * primary_probability(row['native_weight'], c,
            row['protected_unknown'], scale, floor,
            protected_tail=row.get('protected_tail', False))
            for c, row in zip(costs, candidates))

    upper = max(max(scores), 1.e-300)
    while expected(upper) > target:
        upper *= 2
        if not math.isfinite(upper):
            raise ValueError('Weight scale exceeds supported numerical range')
    lower = 0.
    for _ in range(64):
        middle = (lower + upper) / 2
        if expected(middle) > target:
            lower = middle
        else:
            upper = middle
    return dict(schema='shift-weighted-reco-calibration-v1', weight_scale=upper,
        minimum_primary_probability=floor, requested_primary_cost_fraction=budget_fraction,
        expected_primary_cost_fraction=expected(upper) / total,
        mandatory_primary_cost_fraction=lower_cost / total,
        calibration_events=len(candidates),
        protected_unknown_events=sum(row['protected_unknown'] for row in candidates),
        protected_tail_events=sum(row.get('protected_tail', False) for row in candidates),
        probability_grid_bits=52, native_weights_changed=False,
        material_survival_weight_applied=False, production_qualified=False)
