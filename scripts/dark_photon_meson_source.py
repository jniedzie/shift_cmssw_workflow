# Code style: AI-code. AI-generated unbiased meson-collision sampling helpers.
"""Unit-weight signal candidates from an explicitly normalized parent source.

Keep every source collision, including those with no eligible parent, in the
source ledger. One chosen eta or eta-prime per retained collision is replaced;
all other particles retain their ordinary generated decay histories.

The result describes inclusive single-dark-photon production to leading order
in the rare branching probability. It is not a model of multiple simultaneous
rare decays in the same interaction. Transition form factors and dimuon BRs
must be supplied by a separately pinned physical model card.
"""
import math
from itertools import product

from dark_photon_meson import MUON_MASS_GEV, dot, radiative_branching, transition_form_factor_abs2

PARENTS = (221, 331)


def model_precision_budget(parent, signal_mass):
    """Measured BR/TFF one-sigma parameter-box envelope, not a tuned goal."""
    transition = parent['transition_form_factor']
    central = parent['br_gamma_gamma']*transition_form_factor_abs2(signal_mass, transition)
    lambdas = [transition['lambda_gev'] + sign*math.hypot(transition['lambda_stat_gev'],
               transition['lambda_syst_gev']) for sign in (-1, 1)]
    widths = ([transition['gamma_gev'] + sign*math.hypot(transition['gamma_stat_gev'],
               transition['gamma_syst_gev']) for sign in (-1, 1)]
              if 'gamma_gev' in transition else [None])
    branchings = [parent['br_gamma_gamma'] + sign*parent['br_gamma_gamma_uncertainty'] for sign in (-1, 1)]
    values = []
    for branching, pole, width in product(branchings, lambdas, widths):
        shifted = dict(transition, lambda_gev=pole)
        if width is not None:
            shifted['gamma_gev'] = width
        values.append(branching*transition_form_factor_abs2(signal_mass, shifted))
    return max(abs(value/central-1) for value in values)


def resolve_parent_kinematics(parents, mass_tolerance, signal_mass, models, br_mumu):
    """Preserve native momenta and bookkept masses; never repair or drop one.

    Pythia ErrorChecks.xml defines Check:mTolErr as the mass deviation divided
    by max(E,1 GeV). Use the actual p4 invariant consistently in the radiative
    decay. The native generated mass defines the branching probability, as in
    native generator mass handling. Record its numerical effect explicitly.
    """
    if not math.isfinite(mass_tolerance) or mass_tolerance <= 0:
        raise ValueError('An explicit native Pythia mass tolerance is required')
    result = []
    for original in parents:
        parent = dict(original)
        p4, native = parent['p4'], parent['native_mass_gev']
        if (len(p4) != 4 or not all(math.isfinite(value) for value in p4) or p4[0] <= 0 or
                not math.isfinite(native) or native <= 0):
            raise ValueError('Invalid native parent mass or four-vector')
        squared = p4[0]**2-dot(p4[1:], p4[1:])
        if squared <= 0:
            raise ValueError('Native parent four-vector is not timelike')
        mass = math.sqrt(squared)
        deviation = abs(mass-native)/max(p4[0], 1.)
        if deviation > mass_tolerance:
            raise ValueError('Native parent violates the unchanged Pythia mass tolerance')
        if (mass > signal_mass) != (native > signal_mass):
            raise ValueError('Native precision changes whether the rare decay is open')
        model = models[parent['pdg_id']]
        arguments = (signal_mass, .001, model['br_gamma_gamma'], math.sqrt(model['form_factor_abs2']))
        coefficient = radiative_branching(native, *arguments)/1.e-6*br_mumu
        kinematic = radiative_branching(mass, *arguments)/1.e-6*br_mumu
        relative = kinematic/coefficient-1 if coefficient else 0.
        if abs(relative) > model['relative_model_uncertainty']:
            raise ValueError('Native mass precision effect exceeds the measured BR/TFF uncertainty')
        parent.update(mass_gev=native, kinematic_mass_gev=mass, native_mass_shell_deviation=deviation,
                      coefficient_relative_precision_shift=relative,
                      coefficient_difference_from_kinematic_mass=kinematic-coefficient)
        result.append(parent)
    return result


def validate_models(models):
    if not models or set(models) - set(PARENTS):
        raise ValueError('Supply explicit eta/eta-prime parent model cards')
    for pdg, model in models.items():
        if not model.get('provenance'):
            raise ValueError('Parent branching/form-factor provenance is required')
        for key in ('mass_min_gev', 'br_gamma_gamma', 'form_factor_abs2'):
            value = model.get(key)
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError('Finite explicit parent input required: ' + key)
        if model['mass_min_gev'] <= 0 or not 0 < model['br_gamma_gamma'] <= 1 or model['form_factor_abs2'] <= 0:
            raise ValueError('Invalid parent mass, branching fraction or form factor')
        if ('mass_max_gev' in model and (not math.isfinite(model['mass_max_gev']) or
                                       model['mass_max_gev'] < model['mass_min_gev'])):
            raise ValueError('Invalid generator-supported parent mass interval')


def coefficient_bound(ecm_gev, br_mumu, models, mass_gev=None):
    """Analytic bound on sum_parent BR(P->gamma A') BR(A'->mumu) / epsilon^2.

    Each species' physical parent count is at most floor(Ecm/mass_min).
    Separate species bounds allow eta mesons produced by eta-prime decays.
    The phase-space suppression is at most one. No empirical maximum or
    favorable clipping is used to accept a source collision.
    """
    validate_models(models)
    if not math.isfinite(ecm_gev) or ecm_gev <= 0 or not math.isfinite(br_mumu) or not 0 < br_mumu <= 1:
        raise ValueError('Positive source energy and physical dimuon BR are required')
    if mass_gev is not None and (not math.isfinite(mass_gev) or mass_gev <= 2*MUON_MASS_GEV):
        raise ValueError('An open physical dimuon mass is required')
    terms = []
    for model in models.values():
        phase = 1.
        if mass_gev is not None and 'mass_max_gev' in model:
            phase = max(0., 1-(mass_gev/model['mass_max_gev'])**2)**3
        terms.append(math.floor(ecm_gev/model['mass_min_gev']) *
                     2*model['br_gamma_gamma']*model['form_factor_abs2']*br_mumu*phase)
    return math.fsum(terms)


def choose_collision(parents, mass_gev, br_mumu, models, bound, rng, native_weight=1.):
    """Accept a whole source collision, then select exactly one physical parent.

    The acceptance uses epsilon-factored probabilities to avoid underflow.
    Accepted candidate genWeight is one. Physical epsilon^2 is applied once
    when the full-source cross section is calculated, not to genWeight.
    """
    validate_models(models)
    if type(native_weight) not in (int, float) or native_weight != 1.:
        raise ValueError('Unit-weight parent production is required by this unweighting contract')
    if not math.isfinite(mass_gev) or mass_gev <= 2*MUON_MASS_GEV:
        raise ValueError('An open physical dimuon mass is required')
    if not math.isfinite(bound) or bound <= 0 or not math.isfinite(br_mumu) or not 0 < br_mumu <= 1:
        raise ValueError('Invalid analytic unweighting bound or dimuon BR')
    identifiers = set()
    weights = []
    for parent in parents:
        identity, pdg = parent['index'], parent['pdg_id']
        if type(identity) is not int or identity <= 0 or identity in identifiers:
            raise ValueError('A physical parent is repeated in the collision inventory')
        identifiers.add(identity)
        if pdg not in models:
            raise ValueError('Parent species has no explicit model card')
        model = models[pdg]
        parent_mass = parent['mass_gev']
        if not math.isfinite(parent_mass) or parent_mass < model['mass_min_gev']:
            raise ValueError('Generated parent mass violates the analytic source bound')
        if parent_mass > model.get('mass_max_gev', math.inf):
            raise ValueError('Generated parent mass violates the analytic phase-space bound')
        # epsilon=1 is not a physical parameter passed to radiative_branching:
        # divide a safely small reference epsilon back out of its exact law.
        reference = .001
        value = radiative_branching(parent_mass, mass_gev, reference,
            model['br_gamma_gamma'], math.sqrt(model['form_factor_abs2'])) / reference**2 * br_mumu
        weights.append(value)
    total = math.fsum(weights)
    if total > bound*(1+1.e-12):
        raise ValueError('Analytic source bound is violated; refusing biased clipping')
    probability = total/bound
    result = dict(parent_count=len(parents), coefficient_sum=total,
                  acceptance_probability=probability, accepted=False, parent_index=None)
    if total == 0 or rng.random() >= probability:
        return result
    draw = rng.random()*total
    cumulative = 0.
    for parent, weight in zip(parents, weights):
        cumulative += weight
        if weight > 0 and draw < cumulative:
            result.update(accepted=True, parent_index=parent['index'], gen_weight=1.)
            return result
    raise RuntimeError('Weighted parent selection failed despite a finite positive sum')


def source_cross_section_pb(sigma_source_mb, epsilon, coefficient_sum, source_collisions):
    """Source sigma times the all-collision mean rare probability, BRs included."""
    if (not all(math.isfinite(value) for value in (sigma_source_mb, epsilon, coefficient_sum)) or
            sigma_source_mb <= 0 or not 0 < epsilon <= .1 or coefficient_sum < 0 or
            type(source_collisions) is not int or source_collisions <= 0):
        raise ValueError('Invalid complete source exposure or physical cross section')
    return sigma_source_mb*1.e9*epsilon**2*coefficient_sum/source_collisions
