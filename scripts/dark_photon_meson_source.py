# Code style: AI-code. AI-generated unbiased meson-collision sampling helpers.
"""Unit-weight signal candidates from an explicitly normalized parent source.

Keep every source collision, including those with no eligible parent, in the
source ledger. One chosen physical meson per retained collision is replaced;
all other particles retain their ordinary generated decay histories.

The result describes inclusive single-dark-photon production to leading order
in the rare branching probability. It is not a model of multiple simultaneous
rare decays in the same interaction. Transition form factors and dimuon BRs
must be supplied by a separately pinned physical model card.
"""
import math
from itertools import product

from dark_photon_meson import MUON_MASS_GEV, radiative_branching, transition_form_factor_abs2
import dark_photon_vector_meson as vector_meson

PARENTS = (221, 331, 113, 213, -213, 223, 333, 313, -313, 323, -323)


def exclusive_channels(model):
    """Channels share one physical parent inventory, not separate collisions."""
    return model.get('exclusive_channels', [model])


def charged_rho_parent_rate_model(pdg, model):
    """Use only the two owned channels of this same physical charged parent."""
    channels = exclusive_channels(model)
    if len(channels) == 2 and any(row.get('decay_type') == 'charged-vector-dipion'
                                  for row in channels):
        return dict(parent_pdg_id=pdg, exclusive_channels=[dict(row, pdg_id=pdg) for row in channels])
    return None


def dipion_channel(model):
    return model.get('decay_type') in ('pseudoscalar-dipion', 'vector-dipion', 'charged-vector-dipion')


def vector_dipion_channel(model):
    return model.get('decay_type') in ('vector-dipion', 'charged-vector-dipion')


def dipion_kernel(model):
    if model.get('decay_type') == 'charged-vector-dipion':
        import dark_photon_charged_rho_radiative
        return dark_photon_charged_rho_radiative
    if model.get('decay_type') == 'vector-dipion':
        import dark_photon_rho_radiative
        return dark_photon_rho_radiative
    import dark_photon_dipion_meson
    return dark_photon_dipion_meson


def recoil_threshold(model):
    if model.get('decay_type') == 'charged-vector-dipion':
        return model['charged_pion_mass_gev']+model['neutral_pion_mass_gev']
    if dipion_channel(model):
        return 2 * model['pion_mass_gev']
    return model.get('recoil_mass_gev', 0.) if vector_parent(model) else 0.


def vector_parent(model):
    return model.get('decay_type', 'pseudoscalar-photon') == 'vector-pseudoscalar'


def production_mechanism(card):
    mechanism = card.get('mechanism', 'eta-etaprime-radiative')
    if mechanism not in ('eta-etaprime-radiative', 'meson-radiative'):
        raise ValueError('Unknown meson production mechanism: ' + str(mechanism))
    if mechanism == 'meson-radiative' and not card.get('channels'):
        raise ValueError('Unified radiative production requires an explicit exclusive channel inventory')
    if mechanism == 'eta-etaprime-radiative' and any(
            vector_parent(channel) or dipion_channel(channel)
            for parent in card['parents'] for channel in exclusive_channels(parent)):
        raise ValueError('Vector and dipion channels require the explicitly named meson-radiative mechanism')
    return mechanism


def parent_branching_coefficient(parent_mass, signal_mass, model, br_mumu, *, native_precision=None):
    """One physical exclusive channel, with epsilon squared factored out."""
    if 'exclusive_channels' in model:
        return math.fsum(parent_branching_coefficient(parent_mass, signal_mass, channel, br_mumu,
                         native_precision=native_precision)
                         for channel in exclusive_channels(model))
    if dipion_channel(model):
        context = native_precision if vector_dipion_channel(model) else None
        return dipion_kernel(model).branching_coefficient(parent_mass, signal_mass, model,
                                                          **(context or {})) * br_mumu
    if vector_parent(model):
        value = vector_meson.radiative_branching(parent_mass, model['recoil_mass_gev'],
            signal_mass, .001, model['br_p_gamma'], model['form_factor_abs2'])
    else:
        value = radiative_branching(parent_mass, signal_mass, .001,
            model['br_gamma_gamma'], math.sqrt(model['form_factor_abs2']))
    return value / 1.e-6 * br_mumu


def parent_form_factor_abs2(signal_mass, parent):
    function = (vector_meson.transition_form_factor_abs2 if vector_parent(parent)
                else transition_form_factor_abs2)
    return function(signal_mass, parent['transition_form_factor'])


def resolved_parent_models(card, configuration, mass):
    """Resolve only explicitly declared exclusive channels at native support."""
    if card.get('mechanism') == 'meson-radiative':
        expected = [parent.get('channel') for parent in card['parents']]
        if (any(not isinstance(name, str) or not name for name in expected) or
                len(set(expected)) != len(expected) or card.get('channels') != expected):
            raise ValueError('Explicit channel names do not match the exclusive parent/recoil models')
    models = {}
    native = {row['pdg_id']: row for row in configuration['parents']}
    if len(native) != len(configuration['parents']):
        raise ValueError('Native parent species are repeated')
    for parent in card['parents']:
        pdg = parent['pdg_id']
        if pdg not in native:
            raise ValueError('Each declared parent requires unique native mass support')
        minimum, maximum = native[pdg]['mass_min_gev'], native[pdg]['mass_max_gev']
        if not 0 < minimum <= maximum:
            raise ValueError('Generator parent mass support is not finite and bounded')
        recoil = recoil_threshold(parent)
        opened = maximum > mass + recoil
        form_factor = parent_form_factor_abs2(mass, parent) if opened and not dipion_channel(parent) else 1.
        model = dict(mass_min_gev=minimum, mass_max_gev=maximum,
            form_factor_abs2=form_factor,
            relative_model_uncertainty=model_precision_budget(parent, mass) if opened else 0.,
            kinematically_open=opened, provenance=dict(pdg=parent['pdg_reference'],
                transition=parent.get('transition_form_factor', parent.get('dipion_model',
                    parent.get('rho_radiative_model', parent.get('charged_rho_radiative_model', {}))))['reference']))
        model['channel'] = parent.get('channel', str(pdg) + '->gamma_Aprime')
        if vector_parent(parent):
            for key in ('decay_type', 'recoil_pdg_id', 'recoil_mass_gev', 'br_p_gamma',
                        'parent_polarization', 'parent_mass_model'):
                model[key] = parent[key]
            if (native[pdg].get('mass_width_gev') != 0. or minimum != parent['mass_gev'] or
                    maximum != parent['mass_gev']):
                raise ValueError('Vector pole branching model requires an unchanged zero-width native source')
        elif dipion_channel(parent):
            keys = (('decay_type', 'charged_pion_mass_gev', 'neutral_pion_mass_gev',
                     'br_pipi', 'charged_rho_radiative_model', 'recoil_pdg_ids',
                     'parent_polarization', 'parent_mass_model')
                    if parent.get('decay_type') == 'charged-vector-dipion' else
                    ('decay_type', 'pion_mass_gev', 'br_pipi', 'rho_radiative_model',
                     'parent_polarization', 'parent_mass_model')
                    if parent.get('decay_type') == 'vector-dipion' else
                    ('decay_type', 'pion_mass_gev', 'br_pipi_gamma', 'dipion_model'))
            for key in keys:
                model[key] = parent[key]
            if vector_dipion_channel(parent):
                model['relative_numerical_mass_precision_budget'] = model.pop('relative_model_uncertainty')
            if (vector_dipion_channel(parent) and
                    (native[pdg].get('mass_width_gev') != 0. or
                     minimum != parent['mass_gev'] or maximum != parent['mass_gev'])):
                raise ValueError('Vector dipion rates require an unchanged zero-width native source')
        else:
            model['br_gamma_gamma'] = parent['br_gamma_gamma']
        if pdg not in models:
            models[pdg] = model
        else:
            previous = models[pdg]
            channels = exclusive_channels(previous)
            identity = lambda row: (row.get('decay_type', 'pseudoscalar-photon'),
                                    row.get('recoil_pdg_id', 22))
            if any(identity(row) == identity(model) for row in channels):
                raise ValueError('Duplicate exclusive channel for one physical parent')
            models[pdg] = dict(mass_min_gev=minimum, mass_max_gev=maximum,
                exclusive_channels=channels + [model])
    if set(models) != set(native):
        raise ValueError('The source inventory includes undeclared parent channels')
    validate_models(models)
    return models


def model_precision_budget(parent, signal_mass):
    """Measured BR/TFF one-sigma parameter-box envelope, not a tuned goal."""
    if vector_dipion_channel(parent):
        # This is a numerical-accuracy target, separate from the experimental
        # photon check and from unknown timelike hadronic-model uncertainties.
        return dipion_kernel(parent).NATIVE_KINEMATIC_RATE_PRECISION_LIMIT
    if dipion_channel(parent):
        normalization = parent['dipion_model']['normalization']
        return math.hypot(normalization['branching_fraction_stat'],
                          normalization['branching_fraction_syst']) / normalization['branching_fraction']
    transition = parent['transition_form_factor']
    if vector_parent(parent):
        if transition['kind'] != 'single-pole-slope':
            central = parent['br_p_gamma'] * parent_form_factor_abs2(signal_mass, parent)
            values = [branch * form for branch in
                (parent['br_p_gamma'] - parent['br_p_gamma_uncertainty'],
                 parent['br_p_gamma'] + parent['br_p_gamma_uncertainty'])
                for form in vector_meson.transition_form_factor_variations(signal_mass, transition)]
            return max(abs(value / central - 1) for value in values)
        central = parent['br_p_gamma'] * parent_form_factor_abs2(signal_mass, parent)
        error = math.hypot(transition['slope_stat_gev_minus2'], transition['slope_syst_gev_minus2'])
        values = []
        for branch_sign, slope_sign in product((-1, 1), repeat=2):
            branching = parent['br_p_gamma'] + branch_sign * parent['br_p_gamma_uncertainty']
            shifted = dict(transition, slope_gev_minus2=transition['slope_gev_minus2'] + slope_sign * error)
            values.append(branching * vector_meson.transition_form_factor_abs2(signal_mass, shifted))
        return max(abs(value / central - 1) for value in values)
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
        parent.pop('native_parent_precision_report', None)
        p4, native = parent['p4'], parent['native_mass_gev']
        if (len(p4) != 4 or not all(math.isfinite(value) for value in p4) or p4[0] <= 0 or
                not math.isfinite(native) or native <= 0):
            raise ValueError('Invalid native parent mass or four-vector')
        squared = math.fsum((p4[0]**2, *(-value*value for value in p4[1:])))
        if squared <= 0:
            raise ValueError('Native parent four-vector is not timelike')
        mass = math.sqrt(squared)
        deviation = abs(mass-native)/max(p4[0], 1.)
        if deviation > mass_tolerance:
            raise ValueError('Native parent violates the unchanged Pythia mass tolerance')
        model = models[parent['pdg_id']]
        native_precision = dict(native_mass_gev=native, native_energy_gev=p4[0],
                                native_mass_tolerance=mass_tolerance)
        owned_rates = charged_rho_parent_rate_model(parent['pdg_id'], model)
        if owned_rates is not None:
            native_precision['native_parent_rate_model'] = owned_rates
        for channel in exclusive_channels(model):
            recoil = recoil_threshold(channel)
            if (mass > signal_mass + recoil) != (native > signal_mass + recoil):
                raise ValueError('Native precision changes whether the rare decay is open')
            nominal = parent_branching_coefficient(native, signal_mass, channel, br_mumu)
            shifted = parent_branching_coefficient(mass, signal_mass, channel, br_mumu,
                                                   native_precision=native_precision)
            relative = shifted / nominal - 1 if nominal else 0.
            budget = channel.get('relative_numerical_mass_precision_budget',
                                 channel.get('relative_model_uncertainty'))
            if abs(relative) > budget:
                if channel.get('decay_type') != 'charged-vector-dipion' or owned_rates is None:
                    raise ValueError('Native mass precision effect exceeds the declared numerical or BR/TFF precision budget')
                # Near its endpoint this channel is tiny. Keep it, and retain
                # the independently checked bound on the SAME parent's full
                # contribution, including its entire conditional-shape weight.
                parent['native_parent_precision_report'] = dipion_kernel(channel).native_parent_precision_report(
                    mass, signal_mass, channel, **native_precision)
        coefficient = parent_branching_coefficient(native, signal_mass, model, br_mumu)
        kinematic = parent_branching_coefficient(mass, signal_mass, model, br_mumu,
                                                 native_precision=native_precision)
        relative = kinematic/coefficient-1 if coefficient else 0.
        parent.update(mass_gev=native, kinematic_mass_gev=mass, native_mass_shell_deviation=deviation,
                      native_mass_tolerance=mass_tolerance,
                      coefficient_relative_precision_shift=relative,
                      coefficient_difference_from_kinematic_mass=kinematic-coefficient)
        result.append(parent)
    return result


def validate_models(models):
    if not models or set(models) - set(PARENTS):
        raise ValueError('Supply explicit supported exclusive meson parent model cards')
    for pdg, model in models.items():
        if 'exclusive_channels' in model:
            channels = model['exclusive_channels']
            if not isinstance(channels, list) or len(channels) < 2:
                raise ValueError('A parent channel group requires several explicit channels')
            names = [row.get('channel') for row in channels]
            if any(not name for name in names) or len(set(names)) != len(names):
                raise ValueError('Exclusive channel names must be unique for a physical parent')
            for channel in channels:
                if any(channel[key] != model[key] for key in ('mass_min_gev', 'mass_max_gev')):
                    raise ValueError('Channels of one physical parent must share its native support')
                validate_models({pdg: channel})
            continue
        if not model.get('provenance'):
            raise ValueError('Parent branching/form-factor provenance is required')
        if model.get('decay_type', 'pseudoscalar-photon') not in ('pseudoscalar-photon', 'vector-pseudoscalar', 'pseudoscalar-dipion', 'vector-dipion', 'charged-vector-dipion'):
            raise ValueError('Unsupported exclusive radiative decay type')
        branching_key = ('br_pipi' if vector_dipion_channel(model) else
                         'br_pipi_gamma' if dipion_channel(model) else
                         'br_p_gamma' if vector_parent(model) else 'br_gamma_gamma')
        for key in ('mass_min_gev', branching_key, 'form_factor_abs2'):
            value = model.get(key)
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError('Finite explicit parent input required: ' + key)
        if model['mass_min_gev'] <= 0 or not 0 < model[branching_key] <= 1 or model['form_factor_abs2'] <= 0:
            raise ValueError('Invalid parent mass, branching fraction or form factor')
        if vector_parent(model):
            recoils = {113: (111,221), 213: (211,), -213: (-211,),
                       223: (111,221), 333: (111,221), 313: (311,), -313: (-311,),
                       323: (321,), -323: (-321,)}
            if (abs(pdg) not in (113, 213, 223, 333, 313, 323) or model.get('parent_polarization') != 'unpolarized' or
                    model.get('parent_mass_model') != 'zero-width-pole' or
                    type(model.get('recoil_pdg_id')) is not int or abs(model['recoil_pdg_id']) not in (111, 211, 221, 311, 321) or
                    not math.isfinite(model.get('recoil_mass_gev', float('nan'))) or model['recoil_mass_gev'] <= 0):
                raise ValueError('Require an explicit narrow-width unpolarized vector and physical pseudoscalar recoil')
            if model['recoil_pdg_id'] not in recoils[pdg]:
                raise ValueError('Unsupported exclusive vector transition or charge-conjugate assignment')
            if model.get('mass_max_gev') != model['mass_min_gev']:
                raise ValueError('Pole branching fractions require an explicitly zero-width vector source')
        elif model.get('decay_type') == 'charged-vector-dipion':
            if (abs(pdg) != 213 or model.get('parent_polarization') != 'unpolarized' or
                    model.get('parent_mass_model') != 'zero-width-pole' or
                    model.get('mass_max_gev') != model['mass_min_gev'] or
                    model.get('recoil_pdg_ids') != [211 if pdg > 0 else -211, 111]):
                raise ValueError('Charged rho dipion radiation requires its explicit pole, spin and charged-pion/pi0 assignment')
            inputs = model['charged_rho_radiative_model']
            if (model['charged_pion_mass_gev'] != inputs['charged_pion_mass_gev'] or
                    model['neutral_pion_mass_gev'] != inputs['neutral_pion_mass_gev'] or
                    model['mass_min_gev'] != inputs['reference_parent_mass_gev'] or
                    model['br_pipi'] != inputs['normalization']['branching_fraction']):
                raise ValueError('Charged rho dipion radiation differs from its explicit unequal masses or rate normalization')
            dipion_kernel(model).branching_coefficient(model['mass_min_gev'], .22, model)
        elif model.get('decay_type') == 'vector-dipion':
            if (pdg != 113 or model.get('parent_polarization') != 'unpolarized' or
                    model.get('parent_mass_model') != 'zero-width-pole' or
                    model.get('mass_max_gev') != model['mass_min_gev']):
                raise ValueError('Rho dipion radiation requires an explicit unpolarized neutral pole source')
            inputs = model['rho_radiative_model']
            if (model['pion_mass_gev'] != inputs['pion_mass_gev'] or
                    model['mass_min_gev'] != inputs['reference_parent_mass_gev'] or
                    model['br_pipi'] != inputs['normalization']['branching_fraction']):
                raise ValueError('Rho dipion radiation differs from its explicit mass or rate normalization')
            dipion_kernel(model).branching_coefficient(model['mass_min_gev'], .22, model)
        elif pdg not in (221, 331):
            raise ValueError('Pseudoscalar photon channel has an unsupported parent')
        if ('mass_max_gev' in model and (not math.isfinite(model['mass_max_gev']) or
                                       model['mass_max_gev'] < model['mass_min_gev'])):
            raise ValueError('Invalid generator-supported parent mass interval')


def coefficient_bound(ecm_gev, br_mumu, models, mass_gev=None):
    """Analytic bound on the sum of exclusive radiative probabilities / epsilon^2.

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
    for model in (channel for group in models.values() for channel in exclusive_channels(group)):
        if dipion_channel(model):
            if mass_gev is None:
                raise ValueError('Dipion proposal bounds require a fixed signal mass')
            # The narrow eta species have identical min/max support. A broad
            # dipion source needs a separately established envelope.
            if model['mass_min_gev'] != model['mass_max_gev']:
                raise ValueError('Dipion proposal requires an explicit narrow parent source')
            value = parent_branching_coefficient(model['mass_max_gev'], mass_gev, model, br_mumu)
            terms.append(math.floor(ecm_gev/model['mass_min_gev']) * value)
            continue
        if vector_parent(model):
            if mass_gev is None:
                phase = model['br_p_gamma'] * model['form_factor_abs2'] * br_mumu
            else:
                phase = parent_branching_coefficient(model['mass_max_gev'], mass_gev, model, br_mumu)
            terms.append(math.floor(ecm_gev/model['mass_min_gev']) * phase)
            continue
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
        for channel in exclusive_channels(model):
            value = parent_branching_coefficient(parent_mass, mass_gev, channel, br_mumu)
            weights.append((parent, channel, value))
    total = math.fsum(value for _, _, value in weights)
    if total > bound*(1+1.e-12):
        raise ValueError('Analytic source bound is violated; refusing biased clipping')
    probability = total/bound
    result = dict(parent_count=len(parents), coefficient_sum=total,
                  acceptance_probability=probability, accepted=False, parent_index=None)
    if total == 0 or rng.random() >= probability:
        return result
    draw = rng.random()*total
    cumulative = 0.
    for parent, channel, weight in weights:
        cumulative += weight
        if weight > 0 and draw < cumulative:
            result.update(accepted=True, parent_index=parent['index'], gen_weight=1.)
            if channel.get('channel'):
                result['selected_channel'] = channel['channel']
            return result
    raise RuntimeError('Weighted parent selection failed despite a finite positive sum')


def source_cross_section_pb(sigma_source_mb, epsilon, coefficient_sum, source_collisions):
    """Source sigma times the all-collision mean rare probability, BRs included."""
    if (not all(math.isfinite(value) for value in (sigma_source_mb, epsilon, coefficient_sum)) or
            sigma_source_mb <= 0 or not 0 < epsilon <= .1 or coefficient_sum < 0 or
            type(source_collisions) is not int or source_collisions <= 0):
        raise ValueError('Invalid complete source exposure or physical cross section')
    return sigma_source_mb*1.e9*epsilon**2*coefficient_sum/source_collisions
