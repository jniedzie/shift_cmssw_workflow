# Code style: AI-code. AI-generated whole-event decay-channel likelihood.
"""Full-support natural/forced J/psi decay sampling on a fixed source model.

If every distinct physical 443 is forced into a native channel set of
probability B_j, q_forced/f = I(all in that set)/product(B_j). A natural
component of fraction alpha therefore gives
q/f = alpha + (1-alpha)*I(all in the forced set)/product(B_j).

Fixed-source fractions use completed original trial allocations, including
valid zero/veto outcomes. Accepted-class fractions instead use complete original
accepted-event allocations, before decay-dependent admission. Missing or failed
jobs are not zero-contribution trials. Saved fixed-trial survivors are not source
exposures. This helper returns only
the decay likelihood; native generator weights and reconstruction admission
probabilities remain separate. It neither changes nor removes observable pairs.

Callers must establish an identical, decay-independent hard-source proposal,
the complete distinct-parent inventory, native channel probabilities and actual
channel membership. Unknown models or classifications cannot be guessed here.
"""
import math


def _natural_fraction(natural, forced, unit):
    if (type(natural) is not int or natural <= 0
            or type(forced) is not int or forced < 0):
        raise ValueError('Require positive natural '+unit+' and nonnegative forced '+unit+'; '
                         'forced-only sampling lacks inclusive decay support')
    alpha = natural / (natural + forced)
    beta = forced / (natural + forced)
    if not 0. < alpha <= 1. or (forced > 0 and not 0. < beta < 1.):
        raise ValueError('Source mixture fractions cannot be represented with positive support')
    return alpha


def natural_trial_fraction(natural_trials, forced_trials):
    """Freeze the natural component from fixed-source allocations, never saves."""
    return _natural_fraction(natural_trials, forced_trials, 'fixed-source trials')


def natural_event_fraction(natural_events, forced_events):
    """Use complete original accepted-class allocations before decay/admission.

    Both components must sample the same normalized hard class with unit native
    weights and no decay-dependent acceptance. Native Pythia internal trial
    counts estimate that class's cross section; they are not this allocation.
    """
    return _natural_fraction(natural_events, forced_events, 'accepted source events')


def whole_event_proposal(parents, *, natural_trials, forced_trials):
    """Return exact q/f and f/q for one complete, classified 443 event.

    Each parent supplies ``parent_barcode`` (one positive identifier per
    physical copy chain), ``pdg_id=443``, its positive native
    ``forced_channel_probability`` and boolean ``in_forced_channel``. Include
    secondary parents as well as direct hard parents. A known branching
    probability of one cannot accompany a channel outside that native set.

    Forced-only input is rejected: multiplying it by product(B_j) estimates
    only the all-forced-channel component, not arbitrary inclusive observables.
    """
    alpha = natural_trial_fraction(natural_trials, forced_trials)
    beta = forced_trials / (natural_trials + forced_trials)
    result = _channel_proposal(parents, alpha, beta)
    return dict(result, natural_source_trials=natural_trials, forced_source_trials=forced_trials,
                denominator_kind='fixed_source_trials')


def accepted_event_proposal(parents, *, natural_events, forced_events):
    """Full-support decay mixture in an explicitly accepted hard-class measure.

    Callers must qualify the common class/bin and its native pre-decay cross
    section independently. The histogram denominator is the combined accepted
    original census. Neither saved fixed-trial survivors nor selected/reco rows
    are accepted-source allocations. This adds no source exposure or survival
    correction and retains every observable pair.
    """
    alpha = natural_event_fraction(natural_events, forced_events)
    beta = forced_events / (natural_events + forced_events)
    result = _channel_proposal(parents, alpha, beta)
    return dict(result, natural_source_events=natural_events, forced_source_events=forced_events,
                denominator_kind='accepted_gen_events')


def _channel_proposal(parents, alpha, beta):
    if not isinstance(parents, (list, tuple)) or not parents:
        raise ValueError('Require a complete nonempty distinct physical 443 parent inventory')
    barcodes = []
    logs = []
    all_forced = True
    for parent in parents:
        if not isinstance(parent, dict) or parent.get('pdg_id') != 443:
            raise ValueError('Unknown or unsupported parent decay model; only native 443 is qualified')
        barcode = parent.get('parent_barcode')
        if type(barcode) is not int or barcode <= 0 or barcode in barcodes:
            raise ValueError('Parent copy chains require distinct positive barcodes')
        probability = parent.get('forced_channel_probability')
        if (isinstance(probability, bool) or not isinstance(probability, (int, float))
                or not math.isfinite(probability) or not 0. < probability <= 1.):
            raise ValueError('Unknown or invalid native forced-channel probability')
        channel = parent.get('in_forced_channel')
        if type(channel) is not bool:
            raise ValueError('Unknown native channel membership; refusing to classify the event')
        if probability == 1. and not channel:
            raise ValueError('Channel membership contradicts unit native branching probability')
        barcodes.append(barcode)
        logs.append(math.log(probability))
        all_forced = all_forced and channel
    log_product = math.fsum(logs)
    log_ratio = math.log(alpha)
    if all_forced and beta:
        other = math.log(beta) - log_product
        high = max(log_ratio, other)
        log_ratio = high + math.log1p(math.exp(-abs(log_ratio-other)))
    try:
        product = math.exp(log_product)
        ratio = math.exp(log_ratio)
        correction = 1. / ratio
    except OverflowError as error:
        raise ValueError('Decay likelihood exceeds finite double precision') from error
    if any(not math.isfinite(value) or value <= 0. for value in (product, ratio, correction)):
        raise ValueError('Decay likelihood lost positive finite double precision')
    return dict(parent_barcodes=barcodes, parent_count=len(barcodes),
                all_parents_in_forced_channel=all_forced,
                native_all_forced_channel_probability=product,
                natural_fraction=alpha, forced_fraction=beta, proposal_density_ratio=ratio,
                proposal_correction=correction)
