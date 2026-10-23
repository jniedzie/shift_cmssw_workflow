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
from pathlib import Path

from shift_material_preselection import checksum, prepare_selection, validate_policy


SCHEMA = 'shift-production-material-selection-v1'
DEPENDENCIES = ('shift_production_selection.py', 'shift_material_preselection.py')


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

    The generic detector worker already persists cumulative p and W/p in Double
    branches. Separate proposal corrections must already be in native weights;
    adding a second correction here would silently change their meaning.
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
        if row['previous_sampling_probability'] != 1. or row['proposal_correction'] != 1.:
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
