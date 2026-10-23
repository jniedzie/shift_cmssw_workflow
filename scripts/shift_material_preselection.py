#!/usr/bin/env python3
# Code style: AI-code. AI-generated material-priority sampling and weight ledger.
"""Prepare a new, frozen reconstruction sample from full GEN material features.

The straight-path material score orders events; it is not a measured survival
efficiency. Geant4 still decides what survives. Every event has positive
inclusion probability, including zero-GEN-muon and unknown-geometry controls.
Decay proposal corrections and event admission probabilities stay separate.
This tool cannot enlarge or reopen a previously processed selection.
"""
import argparse
import gzip
import hashlib
import itertools
import json
import math
from pathlib import Path


def checksum(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def sha256(value, name):
    if (not isinstance(value, str) or len(value) != 64
            or any(x not in '0123456789abcdef' for x in value)):
        raise ValueError(name+' must be a lowercase SHA-256 digest')
    return value


def probability(value, name):
    if isinstance(value, bool) or not math.isfinite(value) or not 0 < value <= 1:
        raise ValueError(name+' must be finite and in (0, 1]')
    return float(value)


def event_identity(value):
    if (len(value) != 3 or any(isinstance(x, bool) or not isinstance(x, int)
                              or x <= 0 for x in value)):
        raise ValueError('An original event needs positive integer run/lumi/event')
    return tuple(value)


def validate_policy(policy):
    if policy['schema'] != 'shift-material-admission-policy-v1':
        raise ValueError('Unsupported material admission policy')
    for name in ('cohort_id', 'selection_domain', 'selection_seed', 'fold_seed'):
        if not isinstance(policy[name], str) or not policy[name]:
            raise ValueError(name+' needs a nonempty frozen string')
    probability(policy['control_probability'], 'control_probability')
    probability(policy['unknown_probability'], 'unknown_probability')
    if policy['unknown_probability'] < policy['control_probability']:
        raise ValueError('Unknown paths need at least the control probability')
    if type(policy.get('require_opposite_sign', False)) is not bool:
        raise ValueError('Opposite-sign pair priority must be a Boolean')
    if type(policy.get('rank_partial_parent_paths', False)) is not bool:
        raise ValueError('Partial parent-path priority must be a Boolean')
    scale = policy['momentum_scale_GeV']
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError('momentum_scale_GeV must be finite and positive')
    if (isinstance(policy['training_folds'], bool)
            or not isinstance(policy['training_folds'], int) or policy['training_folds'] < 2):
        raise ValueError('Need at least two original-event training folds')
    if policy.get('two_endcap_pointing') is not None:
        validate_pointing(policy['two_endcap_pointing'])
    if policy.get('muon_column_priority') is not None:
        validate_muon_column(policy['muon_column_priority'])


def validate_pointing(config):
    """Bind the straight-ray ranking scale to an explicit geometry reference."""
    if config['schema'] not in ('shift-two-endcap-pointing-v1', 'shift-two-endcap-pointing-v2',
                                'shift-two-endcap-pointing-v3'):
        raise ValueError('Unsupported two-endcap pointing policy')
    planes = config['z_planes_cm']
    if (len(planes) != 2 or any(isinstance(z, bool) or not math.isfinite(z) for z in planes)
            or not min(planes) < 0 < max(planes)):
        raise ValueError('Pointing needs one finite reference plane on each CMS endcap')
    radius = config['radial_scale_cm']
    if isinstance(radius, bool) or not math.isfinite(radius) or radius <= 0:
        raise ValueError('Pointing radial scale must be finite and positive')
    if not isinstance(config['geometry_reference'], str) or not config['geometry_reference']:
        raise ValueError('Pointing needs an explicit geometry reference')
    sha256(config['geometry_sha256'], 'pointing geometry reference')
    if config['schema'] in ('shift-two-endcap-pointing-v2', 'shift-two-endcap-pointing-v3'):
        width = config['station_edge_scale_cm']
        if isinstance(width, bool) or not math.isfinite(width) or width <= 0:
            raise ValueError('Chamber edge scale must be finite and positive')
        stations = config['station_envelopes']
        if not isinstance(stations, list) or not stations:
            raise ValueError('Chamber pointing needs station envelopes')
        names = set()
        for station in stations:
            if (type(station['station']) is not int or not 1 <= station['station'] <= 4
                    or station['station'] in names):
                raise ValueError('Chamber envelopes need distinct CSC station numbers')
            names.add(station['station'])
            for field in ('z_abs_cm', 'radial_min_cm', 'radial_max_cm'):
                value = station[field]
                if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                    raise ValueError('Chamber envelope coordinates must be finite and positive')
            if (station['radial_min_cm'] >= station['radial_max_cm']
                    or station['z_abs_cm'] >= min(abs(z) for z in planes)):
                raise ValueError('Chamber envelope must be ordered and inside both reference planes')


def quartic_priority(ratio):
    # Invert first for large offsets to avoid overflowing the quartic.
    return ((1./ratio)**4/(1.+(1./ratio)**4) if ratio > 1.
            else 1./(1.+ratio**4))


def station_pointing(birth, momentum, config):
    """Rank proximity to at least one CSC station envelope on each side.

    These broad radial envelopes omit chamber gaps, bending and scattering.
    The edge scale is a sampling choice, not an uncertainty on the geometry.
    """
    sides, score = [], 1.
    for side in (-1, 1):
        stations = []
        for station in config['station_envelopes']:
            z = side*station['z_abs_cm']
            if momentum[2] == 0 or (z-birth[2])/momentum[2] < 0:
                stations.append(None)
                continue
            distance = (z-birth[2])/momentum[2]
            radius = math.hypot(birth[0]+distance*momentum[0],
                                birth[1]+distance*momentum[1])
            if not math.isfinite(radius):
                raise ValueError('Nonfinite chamber projection')
            outside = max(station['radial_min_cm']-radius,
                          radius-station['radial_max_cm'], 0.)
            stations.append(dict(radius_cm=radius,
                                 priority=quartic_priority(outside/config['station_edge_scale_cm'])))
        score *= max((row['priority'] for row in stations if row is not None), default=0.)
        sides.append(stations)
    return dict(priority=score, projected_stations=sides)


def two_endcap_pointing(muons, config):
    """Softly rank rays near both endcaps; never declare detector acceptance.

    V1 retains its original outer-envelope score. V2 also ranks proximity to
    a CSC station on each side, including the central hole. These models omit
    chamber gaps, magnetic bending and scattering. V3 allows an
    endcap birth downstream of a reference plane when stations on both sides
    are still ahead. Only forward reference planes enter its outer score.
    A ray missing the envelope still has positive random control. Missing
    vertices stay unknown.
    """
    validate_pointing(config)
    score, radii, unknown, forward = 1., [], False, True
    chamber_scores = []
    station_forward = True
    for muon in muons:
        birth, momentum = muon.get('birth_cm'), muon.get('momentum_GeV')
        if birth is None or momentum is None:
            unknown = True
            radii.append(None)
            chamber_scores.append(None)
            continue
        if (len(birth) != 3 or len(momentum) != 3
                or any(isinstance(x, bool) or not math.isfinite(x)
                       for x in itertools.chain(birth, momentum))):
            raise ValueError('Pointing needs finite CMS birth and momentum coordinates')
        leg = []
        for z in config['z_planes_cm']:
            if momentum[2] == 0 or (z-birth[2])/momentum[2] < 0:
                leg.append(None)
                forward = False
                continue
            distance = (z-birth[2])/momentum[2]
            radius = math.hypot(birth[0]+distance*momentum[0],
                                birth[1]+distance*momentum[1])
            if not math.isfinite(radius):
                raise ValueError('Nonfinite straight-ray projection')
            leg.append(radius)
        radii.append(leg)
        if config['schema'] == 'shift-two-endcap-pointing-v3':
            ahead = [radius for radius in leg if radius is not None]
            if ahead:
                score *= quartic_priority(max(ahead)/config['radial_scale_cm'])
            else:
                score = 0.
        elif None in leg:
            score = 0.
        else:
            ratio = max(leg)/config['radial_scale_cm']
            score *= quartic_priority(ratio)
        if config['schema'] in ('shift-two-endcap-pointing-v2', 'shift-two-endcap-pointing-v3'):
            chambers = station_pointing(birth, momentum, config)
            score *= chambers['priority']
            chamber_scores.append(chambers)
            if config['schema'] == 'shift-two-endcap-pointing-v3':
                station_forward = station_forward and all(
                    any(row is not None for row in side) for side in chambers['projected_stations'])
    result = dict(priority=None if unknown else score, unknown=unknown,
                  both_planes_forward=None if unknown else forward, projected_radii_cm=radii)
    if config['schema'] in ('shift-two-endcap-pointing-v2', 'shift-two-endcap-pointing-v3'):
        result['station_pointing'] = chamber_scores
    if config['schema'] == 'shift-two-endcap-pointing-v3':
        result['both_sides_have_forward_stations'] = None if unknown else station_forward
    return result


def validate_muon_column(config):
    if config['schema'] != 'shift-muon-column-priority-v1':
        raise ValueError('Unsupported muon column priority')
    loss = config['mass_stopping_power_GeV_cm2_g']
    if isinstance(loss, bool) or not math.isfinite(loss) or loss <= 0:
        raise ValueError('Muon column priority needs a finite positive mass stopping-power scale')
    if not isinstance(config['reference'], str) or not config['reference']:
        raise ValueError('Muon column priority needs a declared scale reference')


def muon_column_priority(muons, config):
    """Compare a rough ionization scale times column with total momentum.

    This smooth ranking is not a range calculation or a survival probability.
    Material composition, radiative losses, straggling and central CMS are not
    modelled here. Unknown columns keep their existing protected probability.
    """
    validate_muon_column(config)
    score, ratios, unknown = 1., [], False
    for muon in muons:
        material = muon['material_to_CMS_entry']
        column = material.get('column_density_g_cm2')
        if not material.get('complete') or column is None:
            unknown = True
            ratios.append(None)
            continue
        p = muon['p_GeV']
        if (isinstance(column, bool) or not math.isfinite(column) or column < 0
                or isinstance(p, bool) or not math.isfinite(p) or p < 0):
            raise ValueError('Muon column priority needs finite nonnegative column and total momentum')
        if p == 0:
            score = 0.
            ratios.append(None)
            continue
        ratio = config['mass_stopping_power_GeV_cm2_g']*column/p
        if not math.isfinite(ratio):
            raise ValueError('Nonfinite muon column-to-momentum ratio')
        ratios.append(ratio)
        score *= ((1./ratio)**4/(1.+(1./ratio)**4) if ratio > 1.
                  else 1./(1.+ratio**4))
    return dict(priority=None if unknown else score, unknown=unknown,
                column_to_momentum_ratios=ratios)


def uniform(key):
    # An exactly representable 53-bit grid; <p admits no event when p=0.
    return (int(checksum(key)[:14], 16) >> 3)/float(1 << 53)


def grid_probability(value):
    """Record the actual probability of the 53-bit uniform comparison."""
    return math.ceil(probability(value, 'admission probability')*(1 << 53))/float(1 << 53)


def pair_priority(event, momentum_scale, require_opposite_sign=False,
                  rank_partial_parent_paths=False, pointing=None, muon_column=None):
    """Rank incoming pairs by total momentum and shared parent material paths.

    Opposite-sign priority is opt-in for new cohorts. Other events still keep
    the positive reconstruction control probability assigned by the ledger.
    """
    flights = {x['barcode']: x for x in event['parent_flights']}
    if len(flights) != len(event['parent_flights']):
        raise ValueError('Repeated ancestor flight barcode')
    candidates = []
    for muon in event['muons']:
        p = muon['p_GeV']
        if not math.isfinite(p) or p < 0:
            raise ValueError('Muon momentum must be finite and nonnegative')
        material = muon['material_to_CMS_entry']
        unknown_direction = material.get('unknown_reason') in (
            'missing muon production vertex', 'muon born inside unmodelled central CMS region')
        if muon['straight_line_towards_CMS_entry'] or unknown_direction:
            candidates.append(muon)
    best, best_pair, unknown_pair, best_incomplete = (
        0., None, bool(event.get('unknown_transport_ancestor_barcodes')), False)
    best_pointing, best_column = None, None
    for left, right in itertools.combinations(candidates, 2):
        if require_opposite_sign:
            if abs(left['pdg']) != 13 or abs(right['pdg']) != 13:
                raise ValueError('Opposite-sign muon priority needs explicit muon PDG IDs')
            if left['pdg']*right['pdg'] >= 0:
                continue
        parent_numbers = set(left['transport_parent_barcodes']) | set(right['transport_parent_barcodes'])
        if any(number not in flights for number in parent_numbers):
            raise ValueError('Muon references an absent ancestor flight')
        parents = [flights[number]['material'] for number in parent_numbers]
        known = all(x['full_saved_chord_coverage'] for x in parents)
        known = known and all(x['straight_line_towards_CMS_entry']
                              and x['material_to_CMS_entry']['complete'] for x in (left, right))
        if not known:
            unknown_pair = True
            if not rank_partial_parent_paths:
                continue
        # A known external prefix remains useful for ranking when the decay
        # ends inside unscored CMS. Missing material contributes no invented
        # depth, stays flagged unknown and never removes the control sample.
        depths = [x.get('interaction_depth_proxy') for x in parents]
        if not known and rank_partial_parent_paths:
            depths = [0. if value is None else value for value in depths]
        if any(x is None or not math.isfinite(x) or x < 0 for x in depths):
            raise ValueError('Known material paths need finite nonnegative depths')
        energy = math.prod((x['p_GeV']/math.hypot(x['p_GeV'], momentum_scale))**2
                           for x in (left, right))
        score = math.exp(-math.fsum(depths))*energy
        ray = two_endcap_pointing((left, right), pointing) if pointing is not None else None
        if ray is not None:
            if ray['unknown']:
                unknown_pair = True
            else:
                score *= ray['priority']
        column = muon_column_priority((left, right), muon_column) if muon_column is not None else None
        if column is not None:
            if column['unknown']:
                unknown_pair = True
            else:
                score *= column['priority']
        if score > best:
            best, best_pair = score, [left['barcode'], right['barcode']]
            best_incomplete = not known
            best_pointing = ray
            best_column = column
    result = dict(priority=best, preferred_pair_barcodes=best_pair,
                  unknown_potential_pair=unknown_pair, incoming_candidates=len(candidates),
                  ranked_with_incomplete_paths=best_incomplete)
    if pointing is not None:
        result['two_endcap_pointing'] = best_pointing
    if muon_column is not None:
        result['muon_column_priority'] = best_column
    return result


def admission_probability(ranking, weight, mean_abs_weight, policy):
    """One positive admission rule shared by production and response tests."""
    floor = probability(policy['control_probability'], 'control probability')
    weighted = min(1., floor*abs(weight)/mean_abs_weight) if mean_abs_weight else floor
    value = max(floor, weighted, floor+(1.-floor)*ranking['priority'])
    if ranking['unknown_potential_pair']:
        value = max(value, policy['unknown_probability'])
    return grid_probability(value)


def prepare_selection(features, authority, policy=None):
    """Bind an exact GEN census to source exposure and a single decay draw."""
    if policy is None:
        from shift_production_selection import default_policy
        cohort = checksum(authority)
        policy = default_policy('source-' + cohort, 'material-draw-' + cohort)
    validate_policy(policy)
    if features['schema'] != 'shift-gen-material-features-v1':
        raise ValueError('Unsupported GEN material features')
    if authority['schema'] != 'shift-material-admission-authority-v1':
        raise ValueError('Unsupported source/decay authority')
    if authority['population_scope'] not in ('conditional_diagnostic', 'representative_census'):
        raise ValueError('Declare whether the original population is conditional or representative')
    sources, feature_sources, source_names = {}, set(), set()
    for source in authority['sources']:
        original = sha256(source['original_source_sha256'], 'original source')
        if not source['source_id'] or original in sources or source['source_id'] in source_names:
            raise ValueError('Original source exposure must occur exactly once')
        source_names.add(source['source_id'])
        if not source['full_gen_exposure']:
            raise ValueError('Full original GEN exposure is required')
        sources[original] = source
        for feature_sha in source['feature_source_sha256s']:
            sha256(feature_sha, 'feature source')
            # A frozen merged GEN file can contain several original sources.
            # Its events still need an unambiguous original-source ledger key.
            pair = (feature_sha, original)
            if pair in feature_sources:
                raise ValueError('Repeated completed-input/original-source binding')
            feature_sources.add(pair)
    authorities = {}
    for row in authority['events']:
        key = (row['feature_source_sha256'], row['original_source_sha256'], event_identity(row['event_id']))
        if key in authorities:
            raise ValueError('Repeated event authority')
        authorities[key] = row
    records, seen = [], set()
    for event in features['events']:
        feature_sha = event['source_file_sha256']
        identity = event_identity(event['event_id'])
        original = sha256(event['original_source_sha256'], 'feature original source')
        key = (feature_sha, original, identity)
        if (feature_sha, original) not in feature_sources or key not in authorities:
            raise ValueError('Missing source-bound event authority')
        row = authorities.pop(key)
        source = sources[original]
        if (row['original_source_sha256'] != original or row['source_id'] != source['source_id']
                or row['graph_sha256'] != event['graph_sha256']):
            raise ValueError('Authority does not match original source and completed graph')
        if (event.get('original_source_sha256') not in (None, original)
                or event.get('source_id') not in (None, source['source_id'])):
            raise ValueError('Feature original identity differs from its authority')
        original_key = (original, identity)
        if original_key in seen:
            raise ValueError('V1 permits one proposal draw per original hard event')
        seen.add(original_key)
        if not isinstance(row['proposal_draw_id'], str) or not row['proposal_draw_id']:
            raise ValueError('A single explicit proposal draw identity is required')
        native = row['native_weight']
        if not math.isfinite(native) or native != event['native_weight']:
            raise ValueError('Native Double weight does not match GEN')
        old_p = probability(row['sampling_probability'], 'previous conditional probability')
        domains = row['previous_selection_domains']
        if (not isinstance(domains, list)
                or any(not isinstance(x, str) or not x for x in domains)
                or len(domains) != len(set(domains)) or policy['selection_domain'] in domains
                or (old_p != 1. and not domains)):
            raise ValueError('Previous selection needs independent, recorded RNG domains')
        density, correction = row['proposal_density_ratio'], row['proposal_correction']
        if (not all(math.isfinite(x) and x > 0 for x in (density, correction))
                or not math.isclose(density*correction, 1., rel_tol=2e-12)):
            raise ValueError('Need reciprocal positive full-mixture q/f and f/q')
        if row['proposal_kind'] not in ('ordinary', 'importance'):
            raise ValueError('Proposal kind must be explicit')
        if row['proposal_kind'] == 'ordinary' and (density != 1. or correction != 1.):
            raise ValueError('Explicit ordinary draws require q/f=f/q=1')
        # A saved muon filter selected a particular decay realization. Drawing
        # new lifetimes from that subset needs a different conditional density;
        # the unfiltered f/q supported here cannot supply that correction.
        decay_filtered = source.get('decay_dependent_filter', False)
        if type(decay_filtered) is not bool:
            raise ValueError('Declare decay-dependent source filtering as a Boolean')
        decay_filtered = decay_filtered or source.get('generator_profile') == 'shift-qcd-mu-enriched-cylinder-v1'
        if decay_filtered and (row.get('saved_decays_unchanged') is not True
                               or row['proposal_kind'] != 'ordinary'):
            raise ValueError('Decay-filtered sources require unchanged saved decays; redraw before the filter')
        sha256(row['proposal_provenance_sha256'], 'proposal provenance')
        before = (native/old_p)*correction
        if not math.isfinite(before) or (native != 0. and before == 0.):
            raise ValueError('Nonfinite pre-admission event weight')
        ranking = pair_priority(event, policy['momentum_scale_GeV'],
                                policy.get('require_opposite_sign', False),
                                policy.get('rank_partial_parent_paths', False),
                                policy.get('two_endcap_pointing'),
                                policy.get('muon_column_priority'))
        records.append(dict(row, ranking=ranking, pre_admission_weight=before))
    if authorities:
        raise ValueError('Authority includes events absent from this complete feature census')
    if not records:
        raise ValueError('An empty feature census cannot define an admission cohort')
    # Use the actual pre-admission magnitude, including both older factors.
    mean_abs = math.fsum(abs(x['pre_admission_weight']) for x in records)/len(records)
    policy_sha = checksum(policy)
    output = []
    for row in records:
        rank = row.pop('ranking')
        conditional = admission_probability(rank, row['pre_admission_weight'], mean_abs, policy)
        cumulative = probability(row['sampling_probability']*conditional, 'cumulative probability')
        group = [row['original_source_sha256'], row['event_id']]
        draw = uniform(['material-admission', policy['selection_domain'], policy['selection_seed'], group])
        fold = int(checksum(['training-fold', policy['fold_seed'], group]), 16) % policy['training_folds']
        sampling_weight = row['native_weight']/cumulative
        final_weight = sampling_weight*row['proposal_correction']
        if (not all(math.isfinite(x) for x in (sampling_weight, final_weight))
                or (row['native_weight'] != 0. and final_weight == 0.)):
            raise ValueError('Nonfinite final event weight')
        output.append(dict(row, previous_sampling_probability=row['sampling_probability'],
                           conditional_sampling_probability=conditional, sampling_probability=cumulative,
                           sampling_gen_weight=sampling_weight, proposal_gen_weight=final_weight,
                           material_priority=rank, admitted=draw < conditional, admission_draw=draw,
                           training_fold=fold, policy_sha256=policy_sha,
                           selection_domain=policy['selection_domain']))
    return dict(schema='shift-material-admission-ledger-v1', policy=policy, policy_sha256=policy_sha,
                diagnostic_only=True, normalization_ready=False,
                population_scope=authority['population_scope'],
                features_sha256=checksum(features), authority_sha256=checksum(authority),
                sources=authority['sources'], events=output,
                expected_new_reconstruction_events=math.fsum(x['conditional_sampling_probability'] for x in output),
                admitted_events=sum(x['admitted'] for x in output), original_events=len(output),
                full_GEN_exposure_counted_once=True, material_score_is_physics_weight=False,
                probability_grid_bits=53,
                geometry_survival_probability_calibrated=False,
                statistical_unit='original source and run/lumi/event; all pairs share this group',
                weight_formula='native_weight / cumulative_sampling_probability * proposal_correction')


def read_json(path):
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'rt') as stream:
        return json.load(stream)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('features', 'authority', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--policy', type=Path,
                        help='Study override; otherwise use the shared production default')
    args = parser.parse_args()
    result = prepare_selection(read_json(args.features), read_json(args.authority),
                               read_json(args.policy) if args.policy else None)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    opener = gzip.open if args.output.suffix == '.gz' else open
    with opener(args.output, 'xt') as target:
        json.dump(result, target, allow_nan=False, separators=(',', ':'))
        target.write('\n')
    print(json.dumps({k: result[k] for k in ('original_events', 'admitted_events',
                                             'expected_new_reconstruction_events', 'policy_sha256')}))


if __name__ == '__main__':
    main()
