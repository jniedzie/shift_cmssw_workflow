# Code style: AI-code. AI-generated descriptive GEN features for response studies.
"""Describe GEN opportunities without selecting, drawing or weighting events.

Station misses use straight rays and the existing rounded CSC envelopes. They
omit bending, scattering and chamber gaps, so they are predictors, not cuts or
detector acceptance. Parent depths describe only known saved-flight prefixes;
an unknown continuation does not make a measured survival probability.
"""
import itertools
import math

from shift_production_selection import default_policy


STATIONS = default_policy('response-features-only', 'no-selection-draw')[
    'two_endcap_pointing']['station_envelopes']
MASS_STOPPING_POWER_GEV_CM2_G = .001688
OPPORTUNITY_MISS_MARGIN_CM = 500.
UNKNOWN_DIRECTION_REASONS = (
    'missing muon production vertex',
    'muon born inside unmodelled central CMS region',
)


def _finite_nonnegative(value, name):
    if isinstance(value, bool) or not math.isfinite(value) or value < 0:
        raise ValueError(name+' must be finite and nonnegative')
    return float(value)


def _station_misses(muon):
    """Best and second-best forward radial miss in each hemisphere."""
    birth, momentum = muon.get('birth_cm'), muon.get('momentum_GeV')
    if birth is None or momentum is None:
        return None
    if (len(birth) != 3 or len(momentum) != 3
            or any(not math.isfinite(x) for x in birth+momentum)):
        raise ValueError('Station projections require finite three-vectors')
    sides = {}
    for side in (-1, 1):
        misses = []
        for station in STATIONS:
            z = side*station['z_abs_cm']
            if momentum[2] == 0 or (z-birth[2])/momentum[2] < 0:
                continue
            distance = (z-birth[2])/momentum[2]
            radius = math.hypot(birth[0]+distance*momentum[0],
                                birth[1]+distance*momentum[1])
            if not math.isfinite(radius):
                raise ValueError('Nonfinite station projection')
            misses.append(max(station['radial_min_cm']-radius,
                              radius-station['radial_max_cm'], 0.))
        sides[side] = sorted(misses)
    if not all(sides.values()):
        return None
    near_side = 1 if birth[2] >= 0 else -1
    return dict(miss=max(sides[-1][0], sides[1][0]),
                near=sides[near_side][0], far=sides[-near_side][0],
                two_station_miss=(max(sides[-1][1], sides[1][1])
                    if len(sides[-1]) >= 2 and len(sides[1]) >= 2 else None))


def _incoming(muon):
    return (muon.get('straight_line_towards_CMS_entry') is True
            or muon['material_to_CMS_entry'].get('unknown_reason')
            in UNKNOWN_DIRECTION_REASONS)


def _parent_description(pair, parents):
    # Shared ancestors contribute one flight, even when both daughters are muons.
    numbers = set(pair[0]['transport_parent_barcodes'])
    numbers.update(pair[1]['transport_parent_barcodes'])
    if not numbers.issubset(parents):
        raise ValueError('Muon references an absent parent flight')
    depth, full = 0., True
    for number in numbers:
        material = parents[number]['material']
        value = material.get('interaction_depth_proxy')
        if value is not None:
            depth += _finite_nonnegative(value, 'Parent prefix depth')
        full = full and material.get('full_saved_chord_coverage') is True
    return depth, full, numbers


def _column_ratio(muon):
    """Rough ionization scale times column / momentum, not a range model."""
    material = muon['material_to_CMS_entry']
    column = material.get('column_density_g_cm2')
    if material.get('complete') is not True or column is None:
        return None
    column = _finite_nonnegative(column, 'Muon column')
    momentum = _finite_nonnegative(muon['p_GeV'], 'Muon momentum')
    if momentum == 0:
        return None
    return MASS_STOPPING_POWER_GEV_CM2_G*column/momentum


def feature_values(event):
    """Return a flat descriptive row for one complete saved GEN event.

    ``best_pair`` is the OS pair with the smallest worst one-station miss across
    both muons and hemispheres. Its other quantities describe that same pair;
    tied pairs keep their saved order. The two-station minimum is optimized
    separately over all OS pairs. ``min_parent_prefix_depth`` instead minimizes
    the union of known parent flights over every incoming OS candidate pair.
    It can be zero for an unknown path; coverage flags preserve that distinction.
    No eligible pair gives None, and the all-pairs-depth flag is false for an
    empty candidate set. All unknown-ancestor flags, including BSM, remain.

    Opportunity quantities search every OS pair within a fixed 5 m radial
    station-envelope margin, with stations ahead in both hemispheres. This is
    a descriptive population, not an event veto. Its maximum minimum energy
    and minimum column ratio can come from different pairs; an unknown column
    never substitutes zero. Neither quantity imposes an energy or pT cut.
    """
    muons = event['muons']
    if event.get('GEN_muons', len(muons)) != len(muons):
        raise ValueError('GEN muon count differs from the saved muon census')
    parents = {p['barcode']: p for p in event['parent_flights']}
    if len(parents) != len(event['parent_flights']):
        raise ValueError('Duplicate parent flight barcode')
    rays = {}
    reasons = set()
    for index, muon in enumerate(muons):
        if abs(muon['pdg']) != 13:
            raise ValueError('Saved muon census contains a non-muon')
        _finite_nonnegative(muon['p_GeV'], 'Muon momentum')
        _finite_nonnegative(muon['energy_GeV'], 'Muon energy')
        rays[index] = _station_misses(muon)
        if _incoming(muon):
            material = muon['material_to_CMS_entry']
            if material.get('complete') is not True:
                reasons.add(material.get('unknown_reason', 'unclassified muon path'))

    count_os = 0
    incoming_pairs, forward_pairs = [], []
    incoming_parents = set()
    for left, right in itertools.combinations(range(len(muons)), 2):
        pair = (muons[left], muons[right])
        if pair[0]['pdg']*pair[1]['pdg'] >= 0:
            continue
        count_os += 1
        depth, full, numbers = _parent_description(pair, parents)
        if all(_incoming(muon) for muon in pair):
            incoming_pairs.append(dict(depth=depth, full=full))
            incoming_parents.update(numbers)
        a, b = rays[left], rays[right]
        if a is None or b is None:
            continue
        ratios = [_column_ratio(muon) for muon in pair]
        two_station = [a['two_station_miss'], b['two_station_miss']]
        forward_pairs.append(dict(pair=pair, miss=max(a['miss'], b['miss']),
            near=max(a['near'], b['near']), far=max(a['far'], b['far']),
            two_station=max(two_station) if None not in two_station else None,
            column_ratio=max(ratios) if None not in ratios else None,
            parent_depth=depth, parent_full=full))

    incomplete = 0
    for number in incoming_parents:
        material = parents[number]['material']
        if material.get('full_saved_chord_coverage') is not True:
            incomplete += 1
            reasons.add(material.get('unknown_reason',
                'known external prefix only' if material.get('complete')
                else 'unclassified parent path'))
    best = min(forward_pairs, key=lambda row: row['miss']) if forward_pairs else None
    two_station = [row['two_station'] for row in forward_pairs if row['two_station'] is not None]
    opportunities = [row for row in forward_pairs
                     if row['miss'] <= OPPORTUNITY_MISS_MARGIN_CM]
    known_columns = [row['column_ratio'] for row in opportunities
                     if row['column_ratio'] is not None]
    pair = best['pair'] if best else None
    births = [muon['birth_cm'][2] for muon in pair] if pair else None
    return dict(n_gen_muons=len(muons), n_os_pairs=count_os,
        n_incoming_os_pairs=len(incoming_pairs), n_both_forward_os_pairs=len(forward_pairs),
        best_pair_miss_cm=best['miss'] if best else None,
        best_pair_two_station_miss_cm=min(two_station) if two_station else None,
        best_pair_near_miss_cm=best['near'] if best else None,
        best_pair_far_miss_cm=best['far'] if best else None,
        min_parent_prefix_depth=min(row['depth'] for row in incoming_pairs) if incoming_pairs else None,
        all_incoming_pairs_parent_depth_ge_10=bool(incoming_pairs) and all(row['depth'] >= 10 for row in incoming_pairs),
        best_pair_parent_prefix_depth=best['parent_depth'] if best else None,
        best_pair_full_parent_coverage=best['parent_full'] if best else False,
        best_pair_min_energy_GeV=min(muon['energy_GeV'] for muon in pair) if pair else None,
        best_pair_min_p_GeV=min(muon['p_GeV'] for muon in pair) if pair else None,
        best_pair_max_column_loss_over_p=best['column_ratio'] if best else None,
        best_pair_birth_z_min_cm=min(births) if births else None,
        best_pair_birth_z_max_cm=max(births) if births else None,
        opportunity_miss_margin_cm=OPPORTUNITY_MISS_MARGIN_CM,
        n_pointing_os_pairs=len(opportunities),
        n_pointing_pairs_known_muon_column=len(known_columns),
        n_pointing_pairs_full_parent_coverage=sum(row['parent_full'] for row in opportunities),
        max_pair_min_energy_GeV=max(min(muon['energy_GeV'] for muon in row['pair'])
            for row in opportunities) if opportunities else None,
        min_pair_max_column_loss_over_p=min(known_columns) if known_columns else None,
        min_parent_prefix_depth_among_pointing_pairs=min(row['parent_depth']
            for row in opportunities) if opportunities else None,
        n_incomplete_incoming_parent_paths=incomplete,
        n_unknown_ancestor_barcodes=len(event.get('unknown_transport_ancestor_barcodes', [])),
        unknown_material_reasons='; '.join(sorted(reasons)))
