# Code style: AI-code.
"""Two-muon enrichment after jaw placement and complete natural decays.

The primary filter has its own generator cross-section accounting. A separate
positive-probability rejected stream measures what that filter misses in reco.
Material quantities are loose predictors, never extra survival weights. Only
an untouched full GEN census may receive this prospective reconstruction draw.
"""
import copy
import itertools
import math

from scan_shift_sm_muon_opportunities import ancestry_description, parent_union_description
from shift_material_preselection import checksum, event_identity, sha256
from shift_sm_response_features import _column_ratio, _station_misses


PROFILE = 'shift-qcd-two-muon-material-v1'
SCHEMA = 'shift-qcd-gen-filter-selection-v1'
LEDGER_SCHEMA = 'shift-qcd-gen-filter-ledger-v1'


def default_policy(**overrides):
    """Explicit provisional thresholds; no pT or parent-species requirement."""
    policy = dict(schema='shift-qcd-gen-filter-policy-v1', profile=PROFILE,
        max_two_station_miss_cm=500., max_parent_interaction_depth=2.,
        max_muon_column_loss_over_p=1., require_opposite_sign=False,
        rejected_controls_per_primary=.25,
        minimum_rejected_control_probability=1.e-5,
        maximum_rejected_control_probability=.01, unknown_probability=1.,
        provisional=True, material_survival_weight_applied=False)
    if set(overrides)-set(policy):
        raise ValueError('Unknown QCD GEN filter policy field')
    policy.update(overrides)
    validate_policy(policy)
    return policy


def validate_policy(policy):
    expected = {'schema', 'profile', 'max_two_station_miss_cm',
        'max_parent_interaction_depth', 'max_muon_column_loss_over_p',
        'require_opposite_sign', 'rejected_controls_per_primary',
        'minimum_rejected_control_probability', 'maximum_rejected_control_probability',
        'unknown_probability', 'provisional', 'material_survival_weight_applied'}
    if (set(policy) != expected or policy['schema'] != 'shift-qcd-gen-filter-policy-v1'
            or policy['profile'] != PROFILE or policy['provisional'] is not True
            or policy['material_survival_weight_applied'] is not False
            or policy['unknown_probability'] != 1.
            or type(policy['require_opposite_sign']) is not bool):
        raise ValueError('Invalid or unsupported QCD GEN filter policy')
    for name in ('max_two_station_miss_cm', 'max_parent_interaction_depth',
                 'max_muon_column_loss_over_p', 'rejected_controls_per_primary',
                 'minimum_rejected_control_probability', 'maximum_rejected_control_probability'):
        value = policy[name]
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError(name+' must be finite and positive')
    if not (policy['minimum_rejected_control_probability']
            <= policy['maximum_rejected_control_probability'] <= 1.):
        raise ValueError('Rejected control probability bounds must be ordered and at most one')
    if (math.ceil(policy['minimum_rejected_control_probability']*(1 << 52))
            > math.floor(policy['maximum_rejected_control_probability']*(1 << 52))):
        raise ValueError('Rejected control bounds contain no exact 52-bit probability')


def filter_event(event, policy=None):
    """Evaluate every same pair, keeping unscored plausible paths protected."""
    policy = default_policy() if policy is None else policy
    validate_policy(policy)
    muons = event['muons']
    if (len({m['barcode'] for m in muons}) != len(muons)
            or any(abs(m['pdg']) != 13 for m in muons)
            or event.get('GEN_muons', len(muons)) != len(muons)):
        raise ValueError('Need the complete unique GEN muon inventory')
    if len(muons) < 2:
        return dict(passes_filter=False, protected_unknown=False,
                    reason='fewer_than_two_GEN_muons', pair=None)
    parents = {p['barcode']: p for p in event.get('parent_flights', [])}
    nodes = {p['barcode']: p for p in event.get('ancestor_nodes', [])}
    if (len(parents) != len(event.get('parent_flights', []))
            or len(nodes) != len(event.get('ancestor_nodes', []))):
        raise ValueError('Duplicate parent or ancestor barcode')
    unknown_pair = None
    for pair in itertools.combinations(muons, 2):
        if policy['require_opposite_sign'] and pair[0]['pdg']*pair[1]['pdg'] >= 0:
            continue
        projections = [_station_misses(m) for m in pair]
        reasons, misses, bad_direction = [], [], False
        for muon, projection in zip(pair, projections):
            missing = muon.get('birth_cm') is None or muon.get('momentum_GeV') is None
            if missing:
                reasons.append('unknown detector projection')
            elif projection is None or projection['two_station_miss'] is None:
                bad_direction = True
            else:
                misses.append(projection['two_station_miss'])
        if bad_direction or any(miss > policy['max_two_station_miss_cm'] for miss in misses):
            continue
        if any(not isinstance(m.get('transport_parent_barcodes'), list) for m in pair):
            paths = dict(known_union_depth=None, all_parent_paths_known_and_fully_covered=False,
                         unknown_reasons=['missing parent inventory'], parent_records=[])
        else:
            paths = parent_union_description(pair, parents,
                event.get('unknown_transport_ancestor_barcodes'), nodes)
        for muon in pair:
            reasons.extend(ancestry_description(event, muon, nodes)['reasons'])
        reasons.extend(paths['unknown_reasons'])
        ratios = [_column_ratio(m) if m.get('material_to_CMS_entry') is not None
                  and m.get('p_GeV') is not None else None for m in pair]
        if any(ratio is None for ratio in ratios):
            reasons.append('unknown muon material or total momentum')
        # Missing continuation cannot undo material already scored on this
        # same pair. Shared parents occur once in parent_records. The sum is a
        # lower bound only; compatible unknown paths still remain protected.
        known_depths = [p.get('material', {}).get('interaction_depth_proxy')
                        for p in paths['parent_records']]
        prefix = math.fsum(value for value in known_depths
            if type(value) in (int, float) and math.isfinite(value) and value >= 0)
        if (any(ratio is not None and ratio > policy['max_muon_column_loss_over_p'] for ratio in ratios)
                or prefix > policy['max_parent_interaction_depth']):
            continue
        evidence = dict(muon_barcodes=[m['barcode'] for m in pair],
            two_station_miss_cm=max(misses) if len(misses) == 2 else None,
            parent_interaction_depth=paths['known_union_depth'],
            known_parent_prefix_depth_lower_bound=prefix,
            muon_total_momenta_GeV=[m.get('p_GeV') for m in pair],
            column_loss_over_p=ratios, unknown_reasons=sorted(set(reasons)))
        if reasons:
            if unknown_pair is None:
                unknown_pair = evidence
            continue
        if (paths['known_union_depth'] <= policy['max_parent_interaction_depth']
                and max(ratios) <= policy['max_muon_column_loss_over_p']):
            return dict(passes_filter=True, protected_unknown=False,
                        reason='qualified_muon_pair', pair=evidence)
    if unknown_pair is not None:
        return dict(passes_filter=True, protected_unknown=True,
                    reason='protected_unknown_pair', pair=unknown_pair)
    return dict(passes_filter=False, protected_unknown=False,
                reason='no_qualified_muon_pair', pair=None)


def _normalization(source, events):
    """Keep the original exposure, including logical trials with zero output."""
    passed = [row for row in events if row['filter']['passes_filter']]
    failed = [row for row in events if not row['filter']['passes_filter']]
    before = math.fsum(row['native_weight'] for row in events)
    after = math.fsum(row['native_weight'] for row in passed)
    kind = source['denominator_kind']
    if kind == 'fixed_source_trials':
        if 'full_source_events' in source:
            raise ValueError('Ambiguous original generator exposure')
        denominator, sigma = source['full_source_trials'], source['sigma_nd_pb']
        if type(denominator) is not int or len(events) > denominator:
            raise ValueError('Saved GEN census exceeds the original logical trial count')
    elif kind == 'accepted_gen_events':
        if 'full_source_trials' in source:
            raise ValueError('Ambiguous original generator exposure')
        denominator, sigma = source['full_source_events'], source['native_cross_section_pb']
        if denominator != len(events) or any(row['native_weight'] != 1. for row in events):
            raise ValueError('Accepted-event exposure requires the full unit-weight source census')
    else:
        raise ValueError('Unsupported original QCD generator exposure')
    if (type(denominator) is not int or denominator <= 0
            or type(sigma) not in (int, float) or not math.isfinite(sigma) or sigma <= 0):
        raise ValueError('Invalid full original generator exposure')
    return dict(denominator_kind=kind, original_denominator=denominator,
        source_cross_section_pb=sigma, saved_raw_MC_events=len(events),
        passing_raw_MC_events=len(passed), rejected_raw_MC_events=len(events)-len(passed),
        sumw_before=before, sumw_pass=after,
        sumw_fail=math.fsum(row['native_weight'] for row in failed),
        sumw2_before=math.fsum(row['native_weight']**2 for row in events),
        sumw2_pass=math.fsum(row['native_weight']**2 for row in passed),
        sumw2_fail=math.fsum(row['native_weight']**2 for row in failed),
        filtered_cross_section_pb=sigma*after/denominator,
        weighted_passing_fraction=after/before if before else None,
        filter_efficiency_applied_to_event_weights=False,
        full_original_exposure_preserved=True)


def rejected_control_probability(policy, nprimary, nrejected):
    """Budget controls from the full GEN census before any reconstruction.

    The requested expected count is a fraction of primary-passing raw MC
    events, with positive support for zero-primary sources. Quantization keeps
    the actual Bernoulli probability inside its declared bounds, so native W/p
    uses exactly the probability of the frozen draw.
    """
    validate_policy(policy)
    if any(type(n) is not int or n < 0 for n in (nprimary, nrejected)):
        raise ValueError('Control budgeting requires nonnegative full-GEN event counts')
    grid = 1 << 52
    lower = math.ceil(policy['minimum_rejected_control_probability']*grid)
    upper = math.floor(policy['maximum_rejected_control_probability']*grid)
    requested = policy['rejected_controls_per_primary']*nprimary/nrejected if nrejected else 0.
    return min(upper, max(lower, math.ceil(min(requested, 1.)*grid)))/grid


def _draw(seed, raw, identity):
    return int(checksum([PROFILE, 'rejected-control', seed, raw, list(identity)])[:13], 16)/(1 << 52)


def prepare_filter(events, source, policy, seed, *, before_first_sim,
                   previous_selection=None, proposal_rows=None):
    """Freeze W/p once on a full first-SIM census, independently of reco."""
    validate_policy(policy)
    if before_first_sim is not True or not isinstance(seed, str) or not seed:
        raise ValueError('Declare first-SIM preparation and a nonempty frozen seed')
    if not source['stratum'].startswith('qcd_'):
        raise ValueError('The two-muon generator filter is QCD-only')
    raw = sha256(source['gen_sha256'], 'original QCD source')
    if type(source['saved_events']) is not int or source['saved_events'] != len(events):
        raise ValueError('Filter requires the complete original saved GEN census')
    if previous_selection is not None and (
            previous_selection.get('selected_indices') != list(range(len(events)))
            or previous_selection.get('sampling_probabilities') != [1.]*len(events)
            or previous_selection.get('production_selection') is not None
            or previous_selection.get('qcd_gen_filter') is not None
            or previous_selection.get('sampling') is not None):
        raise ValueError('Cannot redraw a previous partial or frozen reconstruction selection')
    if proposal_rows is not None and len(proposal_rows) != len(events):
        raise ValueError('Native proposal rows must cover the full census')
    decisions = [filter_event(event, policy) for event in events]
    nprimary = sum(decision['passes_filter'] for decision in decisions)
    control_probability = rejected_control_probability(policy, nprimary, len(events)-nprimary)
    rows, seen = [], set()
    for index, (event, decision) in enumerate(zip(events, decisions)):
        identity = event_identity(event['event_id'])
        weight = event['native_weight']
        if (identity in seen or event.get('original_source_sha256') != raw
                or event.get('source_id') != source['source_id']
                or type(weight) not in (int, float) or not math.isfinite(weight)):
            raise ValueError('GEN identity, source binding or native weight differs')
        seen.add(identity)
        probability = 1. if decision['passes_filter'] else control_probability
        draw = _draw(seed, raw, identity)
        row = dict(source_id=source['source_id'], original_source_sha256=raw,
            feature_source_sha256=sha256(event['source_file_sha256'], 'prepared GEN'),
            graph_sha256=sha256(event['graph_sha256'], 'completed GEN graph'),
            event_index=index, event_id=list(identity), native_weight=weight,
            previous_sampling_probability=1., conditional_sampling_probability=probability,
            sampling_probability=probability, sampling_gen_weight=weight/probability,
            proposal_gen_weight=weight/probability, proposal_kind='ordinary',
            proposal_density_ratio=1., proposal_correction=1.,
            proposal_draw_id='saved-completed-graph:'+event['graph_sha256'],
            proposal_provenance_sha256=event['graph_sha256'],
            admitted=decision['passes_filter'] or draw < probability,
            admission_draw=draw, filter=decision)
        if proposal_rows is not None:
            original = proposal_rows[index]
            if (original['event_id'] != list(identity) or original['native_weight'] != weight
                    or original['sampling_probability'] != 1.
                    or original['proposal_density_ratio'] != 1. or original['proposal_correction'] != 1.
                    or original['proposal_kind'] != 'ordinary'):
                raise ValueError('Only unchanged native identity proposals before selection are supported')
            row.update({name: copy.deepcopy(original[name]) for name in (
                'proposal_draw_id', 'proposal_provenance_sha256', 'proposal_kind',
                'proposal_density_ratio', 'proposal_correction')})
        rows.append(row)
    if any(not math.isfinite(row['sampling_gen_weight']) for row in rows):
        raise ValueError('Nonfinite W/p after rejected-control sampling')
    return dict(schema=LEDGER_SCHEMA, profile=PROFILE, policy=copy.deepcopy(policy),
        policy_sha256=checksum(policy), selection_seed=seed, source=copy.deepcopy(source),
        source_exposure_sha256=checksum(source), events=rows,
        original_events=len(rows), admitted_events=sum(row['admitted'] for row in rows),
        expected_detector_raw_MC_events=math.fsum(row['sampling_probability'] for row in rows),
        selected_indices=[row['event_index'] for row in rows if row['admitted']],
        sampling_probabilities=[row['sampling_probability'] for row in rows if row['admitted']],
        rejected_control_probability_effective=control_probability, probability_grid_bits=52,
        control_budget_uses_complete_GEN_counts_only=True,
        expected_rejected_control_raw_MC_events=(len(events)-nprimary)*control_probability,
        normalization=_normalization(source, rows),
        primary_filter_and_rejected_control_are_separate=True,
        material_survival_correction_applied=False, full_GEN_exposure_counted_once=True,
        weight_formula='native_weight / reconstruction_sampling_probability')


def validate_contract(contract):
    validate_policy(contract['policy'])
    if (contract.get('schema') != SCHEMA or not contract.get('stratum', '').startswith('qcd_')
            or contract.get('policy_sha256') != checksum(contract['policy'])
            or contract.get('track_all_hadron_decays') is not True
            or contract.get('maximum_untracked_hadron_flight_mm') != 1.e-5):
        raise ValueError('Invalid QCD GEN filter material-transport contract')
    sha256(contract.get('ledger_sha256'), 'frozen QCD GEN filter ledger')


def bind_filter(descriptor, ledger):
    """Keep full GEN arrays/exposure while binding only selected offsets."""
    if (ledger.get('schema') != LEDGER_SCHEMA or ledger['policy_sha256'] != checksum(ledger['policy'])
            or ledger['source_exposure_sha256'] != checksum(ledger['source'])
            or descriptor['normalization_record'] != ledger['source']
            or descriptor.get('selected_indices') is not None
            or descriptor.get('production_selection') is not None
            or descriptor.get('qcd_gen_filter') is not None
            or descriptor.get('sampling') is not None):
        raise ValueError('Cannot bind changed exposure or reopen an existing selection')
    validate_policy(ledger['policy'])
    rows = ledger['events']
    if (descriptor['event_ids'] != [row['event_id'] for row in rows]
            or descriptor.get('weights', [1.]*len(rows)) != [row['native_weight'] for row in rows]
            or descriptor['stratum'] != ledger['source']['stratum']
            or any(row['feature_source_sha256'] != descriptor['gen_sha256'] for row in rows)
            or ledger['selected_indices'] != [i for i, row in enumerate(rows) if row['admitted']]
            or ledger['sampling_probabilities'] != [row['sampling_probability'] for row in rows if row['admitted']]):
        raise ValueError('Filter ledger differs from the complete prepared GEN source')
    if ledger['normalization'] != _normalization(ledger['source'], rows):
        raise ValueError('Filter normalization differs from its full native-weight census')
    nprimary = sum(row['filter']['passes_filter'] for row in rows)
    control_probability = rejected_control_probability(ledger['policy'], nprimary, len(rows)-nprimary)
    if ledger['rejected_control_probability_effective'] != control_probability:
        raise ValueError('Frozen rejected-control budget differs from its full GEN census')
    for index, row in enumerate(rows):
        probability = 1. if row['filter']['passes_filter'] else control_probability
        draw = _draw(ledger['selection_seed'], ledger['source']['gen_sha256'], row['event_id'])
        weight = row['native_weight']/probability
        if (type(row['filter']['passes_filter']) is not bool
                or row['event_index'] != index
                or row['original_source_sha256'] != ledger['source']['gen_sha256']
                or row['source_id'] != ledger['source']['source_id']
                or row['sampling_probability'] != probability
                or row['conditional_sampling_probability'] != probability
                or row['previous_sampling_probability'] != 1.
                or row['admission_draw'] != draw or row['admitted'] != (draw < probability)
                or row['sampling_gen_weight'] != weight or row['proposal_gen_weight'] != weight
                or row['proposal_density_ratio'] != 1. or row['proposal_correction'] != 1.):
            raise ValueError('Frozen filter draw or native W/p changed')
    ledger_sha = checksum(ledger)
    result = copy.deepcopy(descriptor)
    result.update(selected_indices=list(ledger['selected_indices']),
        sampling_probabilities=list(ledger['sampling_probabilities']),
        sampling=dict(schema=SCHEMA, plan_sha256=ledger_sha),
        production_selection=dict(schema=SCHEMA, stratum=descriptor['stratum'],
            policy_sha256=ledger['policy_sha256'], policy=copy.deepcopy(ledger['policy']),
            ledger_sha256=ledger_sha, track_all_hadron_decays=True,
            maximum_untracked_hadron_flight_mm=1.e-5,
            physics_valid=False, normalization_ready=False),
        qcd_gen_filter=dict(profile=PROFILE,
            raw_source_profile=ledger['source'].get('generator_profile'),
            normalization=copy.deepcopy(ledger['normalization']),
            rejected_control_probability_effective=control_probability,
            rejected_controls_per_primary=ledger['policy']['rejected_controls_per_primary'],
            ledger_sha256=ledger_sha, filter_efficiency_applied_to_event_weights=False))
    validate_contract(result['production_selection'])
    if descriptor.get('decay_proposal') is not None:
        from shift_production_selection import _bind_decay_proposal, selected_decay_proposals
        result['decay_proposal'] = _bind_decay_proposal(descriptor, rows, ledger_sha)
        selected_decay_proposals(result)
    return result
