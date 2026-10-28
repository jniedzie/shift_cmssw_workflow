# Code style: AI-code. AI-generated shared SM/BSM reconstruction admission.
"""Freeze material admission before SIM, independently of later reconstruction.

Inputs describe the placed, completed GEN graph and its original exposure.
This module does not move vertices, draw decays, install libraries, or supply
a material-survival weight. A new model must qualify its own GEN preparation
and coherent detector runtime before using this common admission stage.
"""
import copy
from collections import defaultdict
import json
import math
from pathlib import Path

from shift_material_preselection import checksum, prepare_selection, sha256, validate_policy


SCHEMA = 'shift-production-material-selection-v1'
DEPENDENCIES = ('shift_production_selection.py', 'shift_material_preselection.py')
DECAY_PROPOSAL_SCHEMA = 'shift-native-decay-proposal-v1'


def proposal_source_exposure(descriptor):
    """Preserve one original source denominator, independently of bank members."""
    source = descriptor['normalization_record']
    kind = source['denominator_kind']
    field = {'fixed_source_trials': 'full_source_trials',
             'accepted_gen_events': 'full_source_events'}.get(kind)
    if field is None or type(source.get(field)) is not int or source[field] <= 0:
        raise ValueError('Native proposal needs an explicit full original GEN denominator')
    sha256(source['gen_sha256'], 'original proposal source')
    if not isinstance(source.get('source_id'), str) or not source['source_id']:
        raise ValueError('Native proposal needs its original source ID')
    if (type(source.get('saved_events')) is not int
            or source['saved_events'] != len(descriptor['event_ids'])):
        raise ValueError('Native proposal needs its complete saved original GEN census')
    other = 'full_source_events' if kind == 'fixed_source_trials' else 'full_source_trials'
    if other in source or kind == 'accepted_gen_events' and source[field] != source['saved_events']:
        raise ValueError('Native proposal needs one unambiguous full original denominator')
    return dict(source_id=source['source_id'], original_source_sha256=source['gen_sha256'],
                denominator_kind=kind, **{field: source[field]})


def _native_bank_summary(row, exposure):
    """Audit the complete bank before freezing its compact detector authority."""
    from shift_sm_decay_bank import audit_record
    bank = row.get('native_decay_bank')
    if (not isinstance(bank, dict) or bank.get('schema') != 'shift-finite-decay-bank-v1'
            or bank.get('base_kernel') != 'native_ordinary'):
        raise ValueError('Only audited native ordinary banks are supported; channel mixtures need their own gate')
    selection = audit_record(bank, row['event_id'])
    if (bank.get('raw_original_GEN_sha256') != exposure['original_source_sha256']
            or bank.get('new_original_exposure') != 0
            or bank.get('one_reconstructed_state_per_original') is not True):
        raise ValueError('Native decay bank differs from its single original source exposure')
    member = bank['members'][selection['index']]
    if member.get('graph_sha256') != row['graph_sha256']:
        raise ValueError('Chosen bank member is not the prepared GEN graph')
    for item in bank['members']:
        for field in ('GEN_sha256', 'graph_sha256', 'audit_sha256', 'material_features_sha256'):
            sha256(item[field], 'native bank '+field)
    size = bank['size']
    probability = selection['probabilities'][selection['index']]
    pin = row.get('bank_record', {})
    sha256(pin.get('sha256'), 'native bank file receipt')
    if (pin.get('base_kernel') != 'native_ordinary' or pin.get('size') != size
            or pin.get('selected_index') != selection['index']
            or pin.get('selected_probability') != probability
            or pin.get('selection_correction') != selection['selection_correction']):
        raise ValueError('Native bank file receipt differs from its audited complete choice')
    return dict(size=size, selected_index=selection['index'], selected_probability=probability,
                control_fraction=bank['control_fraction'], record_sha256=pin['sha256'],
                record_contents_sha256=checksum(bank),
                selected_GEN_sha256=member['GEN_sha256'])


def _require_native_bank_source(descriptor):
    from audit_shift_weighted_gen import DEFERRED_CHARM_PROFILE
    source = descriptor['normalization_record']
    if (source.get('generator_profile') != DEFERRED_CHARM_PROFILE
            or source.get('source_family') != 'qcd_ordinary'
            or source.get('decay_dependent_filter') is not False):
        raise ValueError('Native banks require the explicit unfiltered deferred-D QCD source profile')


def _bind_decay_proposal(descriptor, rows, ledger_sha):
    marker = descriptor['decay_proposal']
    if marker != dict(schema=DECAY_PROPOSAL_SCHEMA):
        raise ValueError('Declare a new native per-original proposal; do not reopen a bound contract')
    _require_native_bank_source(descriptor)
    exposure = proposal_source_exposure(descriptor)
    census = []
    for row in rows:
        if (row['source_id'] != exposure['source_id']
                or row['original_source_sha256'] != exposure['original_source_sha256']):
            raise ValueError('Proposal authority differs from the original source')
        fields = ('event_id', 'graph_sha256', 'source_id', 'original_source_sha256',
                  'native_weight', 'proposal_draw_id', 'proposal_kind', 'proposal_density_ratio',
                  'proposal_correction', 'proposal_provenance_sha256', 'sampling_probability', 'sampling_gen_weight',
                  'proposal_gen_weight', 'admitted')
        item = {field: copy.deepcopy(row[field]) for field in fields}
        if row['proposal_kind'] == 'ordinary':
            if (row['proposal_density_ratio'] != 1. or row['proposal_correction'] != 1.
                    or row.get('native_decay_bank') or row.get('bank_record')):
                raise ValueError('Ordinary identity proposals require unit correction and no bank')
        elif row['proposal_kind'] == 'importance':
            item['native_bank'] = _native_bank_summary(row, exposure)
        else:
            raise ValueError('Unsupported native per-original proposal kind')
        census.append(item)
    contract = dict(schema=DECAY_PROPOSAL_SCHEMA, prepared_GEN_sha256=descriptor['gen_sha256'],
                    selection_ledger_sha256=ledger_sha, source_exposure=exposure, events=census,
                    density_measure='native ordinary identity or augmented finite-bank choice',
                    material_survival_correction_applied=False)
    contract['authority_sha256'] = checksum(contract)
    return contract


def selected_decay_proposals(descriptor, indices=None):
    """Check a full frozen authority before SIM, returning only the worker rows.

    Bank choice is an augmented-space importance correction, not another
    inclusion probability or physical survival factor. Global channel mixtures
    are intentionally unsupported until their normalization units are qualified.
    """
    contract = descriptor.get('decay_proposal')
    if contract is None:
        return None
    _require_native_bank_source(descriptor)
    if (contract.get('schema') != DECAY_PROPOSAL_SCHEMA
            or contract.get('material_survival_correction_applied') is not False
            or contract.get('prepared_GEN_sha256') != descriptor['gen_sha256']):
        raise ValueError('Unsupported or changed native decay proposal contract')
    body = {key: value for key, value in contract.items() if key != 'authority_sha256'}
    if checksum(body) != contract.get('authority_sha256'):
        raise ValueError('Frozen native decay proposal authority changed')
    if (contract['selection_ledger_sha256'] != descriptor['sampling']['plan_sha256']
            or contract['selection_ledger_sha256'] != descriptor['production_selection']['ledger_sha256']
            or contract['source_exposure'] != proposal_source_exposure(descriptor)):
        raise ValueError('Native proposal ledger or full source exposure changed')
    rows = contract['events']
    weights = descriptor.get('weights', [1.] * len(descriptor['event_ids']))
    if (len(rows) != len(descriptor['event_ids']) or len(weights) != len(rows)
            or [row['event_id'] for row in rows] != descriptor['event_ids']):
        raise ValueError('Native proposal must cover the complete ordered original GEN census')
    selected = descriptor['selected_indices']
    if (not isinstance(selected, list) or any(type(i) is not int or not 0 <= i < len(rows) for i in selected)
            or selected != sorted(set(selected))
            or len({tuple(row['event_id']) for row in rows}) != len(rows)):
        raise ValueError('Native proposal originals and selected offsets must be unique and ordered')
    if [i for i, row in enumerate(rows) if row.get('admitted') is True] != selected:
        raise ValueError('Native proposal admitted originals differ from the frozen selection')
    if len(selected) != len(descriptor['sampling_probabilities']):
        raise ValueError('Native proposal probabilities do not cover the frozen selected originals')
    for index, row in enumerate(rows):
        if (row['native_weight'] != weights[index]
                or row['source_id'] != contract['source_exposure']['source_id']
                or row['original_source_sha256'] != contract['source_exposure']['original_source_sha256']):
            raise ValueError('Native proposal changed original identities or weights')
        sha256(row['graph_sha256'], 'prepared proposal graph')
        sha256(row['proposal_provenance_sha256'], 'native proposal provenance')
        native, probability = row['native_weight'], row['sampling_probability']
        draw = row.get('proposal_draw_id')
        if (type(row.get('admitted')) is not bool
                or isinstance(draw, bool) or not isinstance(draw, (str, int))
                or isinstance(draw, str) and not draw or isinstance(draw, int) and draw < 0
                or not all(type(v) in (int, float) and math.isfinite(v) for v in (native, probability))
                or not 0 < probability <= 1):
            raise ValueError('Native proposal needs an exact draw and valid original W and p')
        density, correction = row['proposal_density_ratio'], row['proposal_correction']
        if (not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                    and math.isfinite(v) and v > 0 for v in (density, correction))
                or not math.isclose(density*correction, 1., rel_tol=2e-12)):
            raise ValueError('Native proposal needs reciprocal positive finite correction factors')
        if row['proposal_kind'] == 'ordinary':
            if density != 1. or correction != 1. or row.get('native_bank'):
                raise ValueError('Ordinary identity proposal changed its unit correction')
        elif row['proposal_kind'] == 'importance':
            bank = row.get('native_bank', {})
            size, choice = bank.get('size'), bank.get('selected_index')
            choice_probability = bank.get('selected_probability')
            floor = bank.get('control_fraction')
            if (type(size) is not int or not 2 <= size <= 256 or type(choice) is not int
                    or not 0 <= choice < size or not isinstance(choice_probability, (int, float))
                    or isinstance(choice_probability, bool) or not math.isfinite(choice_probability)
                    or not 0 < choice_probability <= 1
                    or not isinstance(floor, (int, float)) or isinstance(floor, bool)
                    or not math.isfinite(floor) or not 0 < floor <= 1
                    or choice_probability < floor/size*(1.-2e-12)
                    or not math.isclose(density, size*choice_probability, rel_tol=2e-12)):
                raise ValueError('Native bank correction differs from its frozen positive-support choice')
            sha256(bank['record_sha256'], 'complete native bank record')
            sha256(bank['record_contents_sha256'], 'complete parsed native bank authority')
            sha256(bank['selected_GEN_sha256'], 'selected native bank GEN')
        else:
            raise ValueError('Unsupported channel/global mixture proposal')
        detector, final = native/probability, native/probability*correction
        if (not all(math.isfinite(v) for v in (1./probability, detector, final))
                or native != 0 and final == 0
                or not all(type(row.get(field)) in (int, float) and math.isfinite(row[field])
                    and math.isclose(row[field], value, rel_tol=2e-12)
                    for field, value in (('sampling_gen_weight', detector), ('proposal_gen_weight', final)))):
            raise ValueError('Native proposal changed W/p or applied its correction more than once')
    for index, probability in zip(selected, descriptor['sampling_probabilities']):
        if rows[index]['sampling_probability'] != probability:
            raise ValueError('Native proposal changed the exact reconstruction inclusion probability')
    requested = selected if indices is None else indices
    if (not isinstance(requested, (list, tuple)) or any(type(i) is not int for i in requested)
            or len(requested) != len(set(requested)) or any(i not in selected for i in requested)):
        raise ValueError('Proposal worker slice includes repeated or unadmitted originals')
    return [copy.deepcopy(rows[i]) for i in requested], [copy.deepcopy(contract['source_exposure'])]


def default_policy(cohort_id, selection_seed):
    """Conservative provisional default; never retune it on detector outcomes."""
    policy = dict(
        schema='shift-material-admission-policy-v1', cohort_id=cohort_id,
        selection_domain='shift-production-material-v1:' + cohort_id,
        selection_seed=selection_seed, fold_seed='shift-original-event-training-v1',
        training_folds=10, control_probability=.1, unknown_probability=1.,
        momentum_scale_GeV=20., require_opposite_sign=True,
        rank_partial_parent_paths=True,
        two_endcap_pointing=dict(
            schema='shift-two-endcap-pointing-v3', z_planes_cm=[-1100., 1100.],
            radial_scale_cm=702., station_edge_scale_cm=50.,
            geometry_reference='Nominal 2023 CSC gas envelopes; rounded straight-ray ranking only',
            geometry_sha256='91eab289ebba75cc59de7daaa16d711a70c7b41c280394240561f788c670e279',
            station_envelopes=[
                dict(station=1, z_abs_cm=650., radial_min_cm=106., radial_max_cm=678.),
                dict(station=2, z_abs_cm=830., radial_min_cm=146., radial_max_cm=691.),
                dict(station=3, z_abs_cm=940., radial_min_cm=166., radial_max_cm=691.),
                dict(station=4, z_abs_cm=1030., radial_min_cm=186., radial_max_cm=691.)]),
        muon_column_priority=dict(schema='shift-muon-column-priority-v1',
            mass_stopping_power_GeV_cm2_g=.001688,
            reference='https://pdg.lbl.gov/2024/AtomicNuclearProperties/HTML/standard_rock.html'))
    validate_policy(policy)
    return policy


def prepare_default_selection(inputs):
    """Recompute the draw from full evidence, never trust an edited saved mask."""
    if inputs.get('schema') != 'shift-production-material-inputs-v1':
        raise ValueError('Require placed/completed GEN material inputs and original exposure')
    policy = default_policy(inputs['cohort_id'], inputs['selection_seed'])
    return prepare_selection(inputs['features'], inputs['authority'], policy)


def read_selection(path):
    return prepare_default_selection(json.loads(Path(path).read_text()))


def bind_source(descriptor, ledger):
    """Keep full GEN identities, weights and exposure; freeze selected offsets.

    The generic detector worker persists cumulative p and native W/p in Double
    branches. A separately declared native proposal retains its correction in
    a frozen authority; it never changes native GEN weights or p.
    """
    return bind_sources([descriptor], ledger)[0]


def bind_sources(descriptors, ledger):
    """Index a large cohort and hash its ledger once, before launching workers."""
    expected_policy = default_policy(ledger['policy']['cohort_id'], ledger['policy']['selection_seed'])
    if ledger['policy'] != expected_policy or ledger['policy_sha256'] != checksum(expected_policy):
        raise ValueError('New production requires the shared default material policy')
    by_source = defaultdict(list)
    for row in ledger['events']:
        by_source[row['feature_source_sha256']].append(row)
    ledger_sha = checksum(ledger)
    return [_bind_source(descriptor, ledger, by_source[descriptor['gen_sha256']], ledger_sha)
            for descriptor in descriptors]


def _bind_source(descriptor, ledger, rows, ledger_sha):
    if descriptor.get('selected_indices') is not None or descriptor.get('production_selection'):
        raise ValueError('Cannot reopen an existing reconstruction selection')
    by_id = {tuple(row['event_id']): row for row in rows}
    identities = descriptor['event_ids']
    if (len(by_id) != len(rows) or len(rows) != len(identities)
            or set(by_id) != set(map(tuple, identities))):
        raise ValueError('Material evidence must cover the exact full source GEN census')
    weights = descriptor.get('weights', [1.] * len(identities))
    if len(weights) != len(identities):
        raise ValueError('Native weights do not cover the full source census')
    selected, probabilities = [], []
    for index, (identity, weight) in enumerate(zip(identities, weights)):
        row = by_id[tuple(identity)]
        if row['native_weight'] != weight:
            raise ValueError('Material evidence changed a native GEN weight')
        if row['previous_sampling_probability'] != 1. or (
                descriptor.get('decay_proposal') is None and row['proposal_correction'] != 1.):
            raise ValueError('This full-inventory adapter requires unsampled inputs and native proposal weights')
        if row['admitted']:
            selected.append(index)
            probabilities.append(row['sampling_probability'])
    result = copy.deepcopy(descriptor)
    result.update(selected_indices=selected, sampling_probabilities=probabilities,
        sampling=dict(schema=SCHEMA, plan_sha256=ledger_sha),
        production_selection=dict(schema=SCHEMA, policy_sha256=ledger['policy_sha256'],
            policy=copy.deepcopy(ledger['policy']),
            ledger_sha256=ledger_sha, track_all_hadron_decays=True,
            maximum_untracked_hadron_flight_mm=1.e-5,
            physics_valid=False, normalization_ready=False))
    if descriptor.get('decay_proposal') is not None:
        result['decay_proposal'] = _bind_decay_proposal(descriptor,
            [by_id[tuple(identity)] for identity in identities], ledger_sha)
        selected_decay_proposals(result)
    return result


def validate_contract(contract):
    """Check the small frozen contract without importing CMSSW."""
    if (contract.get('schema') != SCHEMA or contract.get('track_all_hadron_decays') is not True
            or contract.get('maximum_untracked_hadron_flight_mm') != 1.e-5):
        raise ValueError('Invalid shared production material-transport contract')
    from shift_material_preselection import sha256
    sha256(contract.get('policy_sha256'), 'production policy')
    sha256(contract.get('ledger_sha256'), 'production ledger')
    policy = contract['policy']
    if (policy != default_policy(policy['cohort_id'], policy['selection_seed'])
            or checksum(policy) != contract['policy_sha256']):
        raise ValueError('Production material policy changed after admission')


def configure_transport(process, contract):
    """SHIFT-only SIM settings; leave BSM physics lists and reco modules intact.

    The frozen runtime must contain the coherent Generator, Application and
    plugin build. This sets configuration, not proof of runtime qualification.
    """
    if contract is None:
        return
    validate_contract(contract)
    import FWCore.ParameterSet.Config as cms
    process.g4SimHits.Generator.TrackAllHadronDecays = cms.untracked.bool(True)
    process.g4SimHits.Generator.MaxUntrackedHadronFlightMm = cms.untracked.double(1.e-5)


def guard_legacy_bsm_publication(plan, inventory):
    """Do not let the old unit-weight/full-GEN publisher accept thinned inputs.

    The common Nano worker supports W/p. The older BSM collector/finalizer does
    not yet qualify that handoff. Reject it at preparation, before any jobs.
    """
    for point in plan['points']:
        path = Path(point['gen_directory'])/'material_selected_source.json'
        if not path.is_file():
            raise ValueError('New BSM production requires the shared prepared material_selected_source.json '
                             'and a qualified selected-ID/weight publication handoff')
        source = json.loads(path.read_text())
        validate_contract(source['production_selection'])
        parent = inventory['points'][point['point']]
        if source['gen_sha256'] != parent['gen_sha256'] or source['event_ids'] != parent['event_ids']:
            raise ValueError('BSM admission descriptor differs from the full audited GEN parent')
        size = len(parent['event_ids'])
        if (source['selected_indices'] != list(range(size))
                or source['sampling_probabilities'] != [1.] * size
                or source.get('weights', [1.] * size) != [1.] * size):
            raise ValueError('The legacy BSM publisher assumes full GEN and unit weights; '
                             'qualify selected-ID and W/p grouping/publication before freezing thinned BSM jobs')
