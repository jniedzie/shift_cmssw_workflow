# Code style: AI-code. AI-generated parent-flight and neutral-kaon accounting helpers.
"""Keep a long-lived parent's flight separate from its dark-photon flight.

Positions are (x, y, z, ct), in mm; momenta are (E, px, py, pz), in GeV.
The caller supplies the qualified mass/lifetime authorities and, when needed,
one uniform variate. Nothing here changes a generator setting or samples a
second lifetime for a parent with an existing native end vertex.

These helpers describe straight flight in vacuum. They do not qualify material
attenuation, neutral-kaon regeneration, coherent mixing or secondary sources.
Their result must not be published as a physically complete source prediction
or an upper bound on the full inclusive signal.
"""
import math


NEUTRAL_KAON_IDS = {311, -311, 130, 310}


def _four(values, name):
    if (not isinstance(values, (tuple, list)) or len(values) != 4 or
            any(type(value) not in (int, float) or not math.isfinite(value)
                for value in values)):
        raise ValueError('Require four finite values for ' + name)
    return tuple(float(value) for value in values)


def _positive(value, name):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ValueError('Require an explicit finite positive ' + name)
    return float(value)


def _flight_displacement(momentum, mass, proper):
    return tuple(proper*value/mass for value in momentum[1:] + momentum[:1])


def _native_displacement(origin, endpoint, expected):
    actual = tuple(end-start for start, end in zip(origin, endpoint))
    if not all(math.isfinite(value) for value in actual + expected):
        raise ValueError('Native parent flight exceeds the finite coordinate range')
    if actual[3] < 0:
        raise ValueError('Native parent decay precedes its production')
    # Coordinate subtraction at a far-away source can lose low bits. Allow
    # floating-point roundoff at that actual scale, not a free physical shift.
    scale = max(abs(value) for value in origin + endpoint + expected)
    tolerance = 32*math.ulp(scale)
    residual = max(abs(value-target) for value, target in zip(actual, expected))
    if residual > tolerance:
        raise ValueError('Native parent decay point does not follow its momentum/lifetime')
    return actual, residual, tolerance


def parent_flight(parent_p4, parent_mass_gev, production_position_mm,
                  mean_proper_ctau_mm, *, mass_authority, lifetime_authority,
                  uniform=None, native_decay_position_mm=None,
                  native_sampled_proper_flight_mm=None,
                  native_stored_decay_position_mm=None,
                  mass_shell_relative_tolerance=0.):
    """Preserve a native flight, or draw one unconditional vacuum flight.

    The displacement is (px, py, pz, E) / m times the proper flight in mm.
    The supplied mass is the generator's qualified flight-mass authority; no
    momentum is rescaled to repair a native mass-shell discrepancy here.
    A final Pythia particle may already have a sampled tau and stored vDec,
    despite having no HepMC end vertex. Supply those through the separate
    native_sampled/native_stored arguments; never draw that lifetime again.
    A nonzero extra mass-shell budget must come from the caller's qualified
    native precision contract. The default allows floating-point roundoff only.
    """
    momentum = _four(parent_p4, 'parent momentum')
    origin = _four(production_position_mm, 'parent production position')
    mass = _positive(parent_mass_gev, 'parent mass')
    mean = _positive(mean_proper_ctau_mm, 'parent proper lifetime')
    for authority in (mass_authority, lifetime_authority):
        if not isinstance(authority, str) or not authority.strip():
            raise ValueError('Pin the explicit parent mass and lifetime authorities')
    squared = tuple(value*value for value in momentum + (mass,))
    if not all(math.isfinite(value) for value in squared):
        raise ValueError('Parent momentum/mass exceeds the finite arithmetic range')
    invariant = math.fsum([squared[0]] + [-value for value in squared[1:4]])
    if momentum[0] <= 0 or invariant <= 0 or not math.isfinite(invariant):
        raise ValueError('Parent momentum must be finite, future directed and timelike')
    if (type(mass_shell_relative_tolerance) not in (int, float) or
            not math.isfinite(mass_shell_relative_tolerance) or
            not 0 <= mass_shell_relative_tolerance < 1):
        raise ValueError('Supply a finite nonnegative qualified mass-shell budget below one')
    mass_roundoff = 8*math.ulp(max(squared))
    mass_tolerance = mass_roundoff + mass_shell_relative_tolerance*mass*mass
    mass_residual = abs(invariant-mass*mass)
    if mass_residual > mass_tolerance:
        raise ValueError('Parent momentum differs from its explicit flight-mass authority')

    native_sampled = (native_sampled_proper_flight_mm is not None or
                      native_stored_decay_position_mm is not None)
    position_residual, position_tolerance = None, None
    if native_sampled:
        proper = native_sampled_proper_flight_mm
        if (type(proper) not in (int, float) or not math.isfinite(proper) or proper < 0 or
                native_stored_decay_position_mm is None):
            raise ValueError('Pin both the sampled native proper flight and its stored decay point')
        if uniform is not None or native_decay_position_mm is not None:
            raise ValueError('Do not replace a sampled native lifetime or invent an existing end vertex')
        endpoint = _four(native_stored_decay_position_mm, 'stored native decay point')
        displacement, position_residual, position_tolerance = _native_displacement(
            origin, endpoint, _flight_displacement(momentum, mass, proper))
        method = 'preserved-native-sampled-lifetime-without-end-vertex'
    elif native_decay_position_mm is not None:
        if uniform is not None:
            raise ValueError('Do not sample a second lifetime for a native end vertex')
        endpoint = _four(native_decay_position_mm, 'native parent decay position')
        proper = (endpoint[3]-origin[3])*mass/momentum[0]
        displacement, position_residual, position_tolerance = _native_displacement(
            origin, endpoint, _flight_displacement(momentum, mass, proper))
        method = 'preserved-native-end-vertex'
    else:
        if (type(uniform) not in (int, float) or not math.isfinite(uniform) or
                not 0 <= uniform < 1):
            raise ValueError('Supply one explicit uniform variate in [0, 1)')
        proper = -mean*math.log1p(-uniform)
        displacement = _flight_displacement(momentum, mass, proper)
        endpoint = tuple(start+step for start, step in zip(origin, displacement))
        if not all(math.isfinite(value) for value in endpoint):
            raise ValueError('Parent flight exceeds the finite coordinate range')
        method = 'unconditional-exponential-vacuum-flight'

    return dict(schema='shift-vacuum-parent-flight-v1', parent_p4=list(momentum),
        parent_mass_gev=mass, parent_invariant_mass_squared_gev2=invariant,
        mass_shell_residual_gev2=mass_residual, mass_shell_roundoff_gev2=mass_roundoff,
        mass_shell_relative_tolerance=mass_shell_relative_tolerance,
        mass_authority=mass_authority, lifetime_authority=lifetime_authority,
        mean_proper_ctau_mm=mean, proper_flight_mm=proper, uniform=uniform,
        production_position_mm=list(origin), decay_position_mm=list(endpoint),
        displacement_mm=list(displacement), lifetime_method=method,
        native_end_vertex_present=native_decay_position_mm is not None,
        native_sampled_proper_flight_mm=native_sampled_proper_flight_mm,
        maximum_native_coordinate_residual_mm=position_residual,
        native_coordinate_roundoff_mm=position_tolerance,
        unconditional_new_flight=True if not native_sampled and native_decay_position_mm is None else None,
        transport_model='straight-flight-in-vacuum',
        material_attenuation_modelled=False, regeneration_modelled=False,
        coherent_neutral_kaon_mixing_modelled=False, secondary_sources_modelled=False,
        physical_publication_ready=False, inclusive_signal_upper_bound=False)


def neutral_kaon_chain_ownership(particles):
    """Select the native KL/KS realization once, without a second 1/2 weight.

    Input rows use the native inventory's index, pdg_id, mother_indices and
    daughter_indices. The whole neutral-kaon ancestry must be present. A
    flavor K0/K0bar record is ancestry, never a second radiative parent. The
    native incoherent KL/KS selection is recorded, not promoted to a coherent
    mixing or material-regeneration calculation.
    """
    indexed = {}
    for row in particles:
        index = row.get('index')
        if type(index) is not int or index <= 0 or index in indexed:
            raise ValueError('Native particle indices must be positive and unique')
        if type(row.get('pdg_id')) is not int:
            raise ValueError('Native particle PDG identifiers must be explicit integers')
        for key in ('mother_indices', 'daughter_indices'):
            values = row.get(key)
            if (not isinstance(values, list) or
                    any(type(value) is not int or value <= 0 for value in values) or
                    len(set(values)) != len(values)):
                raise ValueError('Require the explicit unique native ancestry: ' + key)
        indexed[index] = row

    def neutral_mothers(row):
        return [indexed[index] for index in row['mother_indices']
                if index in indexed and indexed[index]['pdg_id'] in NEUTRAL_KAON_IDS]

    def chain(row):
        result, seen = [], set()
        while True:
            index = row['index']
            if index in seen:
                raise ValueError('Neutral-kaon ancestry contains a cycle')
            seen.add(index)
            result.append(row)
            mothers = neutral_mothers(row)
            if not mothers:
                return list(reversed(result))
            if len(mothers) != 1:
                raise ValueError('Several neutral-kaon mothers need a separate interaction model')
            mother = mothers[0]
            if index not in mother['daughter_indices']:
                raise ValueError('Neutral-kaon mother and daughter records disagree')
            row = mother

    # Check even closed copy cycles that would otherwise have no terminal row.
    for row in indexed.values():
        if row['pdg_id'] in NEUTRAL_KAON_IDS:
            chain(row)

    owners, roots = [], set()
    for row in indexed.values():
        if row['pdg_id'] not in (130, 310):
            continue
        daughters = [indexed[index] for index in row['daughter_indices'] if index in indexed]
        if any(child['pdg_id'] == row['pdg_id'] for child in daughters):
            continue
        ancestry = chain(row)
        states = {item['pdg_id'] for item in ancestry if item['pdg_id'] in (130, 310)}
        if len(states) != 1:
            raise ValueError('A changing KL/KS state needs a mixing or regeneration model')
        root = ancestry[0]['index']
        if root in roots:
            raise ValueError('Several radiative parents claim one physical neutral-kaon chain')
        roots.add(root)
        owners.append(dict(parent_index=row['index'], parent_pdg_id=row['pdg_id'],
            neutral_kaon_chain_indices=[item['index'] for item in ancestry],
            flavor_ancestor_indices=[item['index'] for item in ancestry if abs(item['pdg_id']) == 311],
            eigenstate_selection='native-incoherent-realization', additional_eigenstate_weight=1.))
    return sorted(owners, key=lambda row: row['parent_index'])
