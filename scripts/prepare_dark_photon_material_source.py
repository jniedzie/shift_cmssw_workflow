#!/usr/bin/env python3
# Code style: AI-code. AI-generated full-cohort DP material admission preparer.
"""Freeze shared admission from audited placed GEN, without changing its decays.

Run inside the pinned detector capsule. Large feature and draw evidence is
published on EOS; only the selected descriptor and compact receipt stay on AFS.
"""
import argparse
import copy
import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

from shift_production_selection import bind_source, prepare_default_selection

DEPENDENCIES = ('prepare_dark_photon_material_source.py', 'shift_gen_material_features.py',
                'audit_shift_material_scout.py', 'shift_material_path_score.py',
                'shift_production_selection.py', 'shift_material_preselection.py',
                'dark_photon_material_selection.py')


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            digest.update(block)
    return digest.hexdigest()


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False)+'\n').encode()


def immutable(path, value):
    path = Path(path)
    payload = encoded(value)
    if path.exists():
        if path.read_bytes() != payload:
            raise ValueError('Frozen material evidence changed: '+str(path))
    else:
        with path.open('xb') as stream:
            stream.write(payload)


def selection_inputs(records, parent, contract, contract_sha, cohort_id, seed):
    """Bind conditional signal GEN to its original fixed source exposure once."""
    identities = parent['event_ids']
    count = parent['events']
    exposure = contract.get('source_exposure')
    if exposure is None:
        # Direct DY has an accepted signal denominator and native Pythia trial
        # accounting, not a fixed unfiltered pp-collision exposure. Keep that
        # distinction and the existing amplitude/BR rate correction intact.
        denominator=parent.get('full_gen_denominator',{})
        ledger=parent.get('proposal_ledger')
        if (contract.get('mechanism') != 'pure-on-shell-drell-yan'
                or denominator.get('accepted_events') != count or not ledger
                or count > contract['requested_events']
                or contract.get('event_budget_mode') == 'outputAccepted'
                   and count != contract['requested_events']):
            raise ValueError('Direct DY requires complete native accepted-count and proposal accounting')
        exposure=dict(full_gen_denominator=copy.deepcopy(denominator),
            proposal_ledger=copy.deepcopy(ledger),
            production_rate_correction=contract['production_rate_correction'],
            cross_section_convention=contract['cross_section_convention'],
            technical_trials_are_not_collision_exposure=True)
    elif exposure['accepted_signal_events'] != count or count != contract['requested_events']:
        raise ValueError('Conditional meson source exposure differs from full GEN')
    if (count != len(identities)
            or len(set(map(tuple, identities))) != count
            or [row['event_id'] for row in records] != identities
            or not contract.get('source_placement')):
        raise ValueError('Material admission requires the full audited jaw-placed GEN census')
    source_sha = parent['gen_sha256']
    source_id = 'dp-conditional-'+source_sha
    events = []
    for record in records:
        if record['native_weight'] != 1.:
            raise ValueError('Conditional DP source changed its audited native unit weight')
        record.update(source_file_sha256=source_sha, original_source_sha256=source_sha,
                      source_id=source_id, original_source_identity_known=True)
        events.append(dict(source_id=source_id, original_source_sha256=source_sha,
            feature_source_sha256=source_sha, event_id=record['event_id'],
            graph_sha256=record['graph_sha256'], native_weight=record['native_weight'],
            proposal_draw_id='saved:'+':'.join(map(str,record['event_id'])),
            sampling_probability=1., previous_selection_domains=[],
            proposal_kind='ordinary', proposal_density_ratio=1., proposal_correction=1.,
            proposal_provenance_sha256=contract_sha, saved_decays_unchanged=True))
    source = dict(source_id=source_id, original_source_sha256=source_sha,
        feature_source_sha256s=[source_sha], decay_dependent_filter=True,
        full_gen_exposure=dict(source_exposure=copy.deepcopy(exposure),
            conditional_signal_GEN_events=count, contract_sha256=contract_sha,
            parent_GEN_inventory=copy.deepcopy(parent)))
    return dict(schema='shift-production-material-inputs-v1', cohort_id=cohort_id,
        selection_seed=seed,
        features=dict(schema='shift-gen-material-features-v1', events=records),
        authority=dict(schema='shift-material-admission-authority-v1',
            population_scope='representative_census', sources=[source], events=events))


def prepare(request):
    from export_dark_photon_cross_sections import publish_file_rpc
    from shift_gen_material_features import scan_source
    parent = request['parent']
    gen = Path(request['gen_directory'])
    descriptor_path = gen/'material_selected_source.json'
    receipt_path = gen/'material_selection_receipt.json'
    request_sha = hashlib.sha256(encoded(request)).hexdigest()
    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_text())
        if (receipt.get('complete') is not True or receipt['request_sha256'] != request_sha
                or sha(descriptor_path) != receipt['descriptor_sha256']):
            raise ValueError('Existing material draw differs; never redraw after reconstruction')
        return receipt
    if descriptor_path.exists():
        raise ValueError('Incomplete material preparation needs explicit recovery; preserve its descriptor')
    contract_path = gen/'contract.json'
    contract = json.loads(contract_path.read_text())
    if sha(contract_path) != request['contract_sha256']:
        raise ValueError('GEN contract changed before admission')
    geometry_info = request['geometry']
    geometry_path = Path(geometry_info['path'])
    private = Path('/afs/cern.ch/work/j/jniedzie/private/cms_lss_fluka_description')
    if (private not in geometry_path.parents or sha(geometry_path) != geometry_info['sha256']):
        raise ValueError('Require the pinned private material geometry in its protected directory')
    import ROOT
    ROOT.TGeoManager.LockDefaultUnits(False)
    ROOT.TGeoManager.SetDefaultUnits(ROOT.TGeoManager.kRootUnits)
    ROOT.TGeoManager.LockDefaultUnits(True)
    geometry = ROOT.TGeoManager.Import(str(geometry_path))
    if not geometry:
        raise ValueError('Cannot import pinned private material geometry')
    with tempfile.TemporaryDirectory(prefix='dp-material-source-',
                                     dir=os.environ.get('_CONDOR_SCRATCH_DIR','/tmp')) as temporary:
        scratch = Path(temporary)
        local = scratch/'gen.root'
        subprocess.run(['/usr/bin/xrdcp','--silent','--cksum','adler32',
            'root://eosuser.cern.ch/'+request['gen_publication']['path'],str(local)],
            check=True, timeout=900)
        if sha(local) != parent['gen_sha256']:
            raise ValueError('Downloaded GEN differs from the full audited parent')
        records = list(scan_source(local, 'shiftEventTime', geometry, request['frame']))
        inputs = selection_inputs(records, parent, contract, request['contract_sha256'],
                                  request['cohort_id'], request['selection_seed'])
        inputs['features'].update(geometry_sha256=geometry_info['sha256'],
            frame=copy.deepcopy(request['frame']), full_source_census=True,
            saved_decays_unchanged=True, material_is_ranking_only=True)
        ledger = prepare_default_selection(inputs)
        descriptor = bind_source(dict(gen_sha256=parent['gen_sha256'],
            event_ids=copy.deepcopy(parent['event_ids']),weights=[1.]*parent['events']),ledger)
        evidence = scratch/'material_admission.json.gz'
        with evidence.open('xb') as raw:
            with gzip.GzipFile(fileobj=raw, mode='wb', mtime=0) as compressed:
                compressed.write(encoded(dict(request=request, inputs=inputs, ledger=ledger)))
        publication = publish_file_rpc(evidence,
            request['output_base']+'/metadata/GEN/material_admission.json.gz')
        immutable(descriptor_path,descriptor)
        receipt = dict(schema='shift-dp-material-source-preparation-v1', complete=True,
            request_sha256=request_sha, descriptor_sha256=sha(descriptor_path),
            full_gen_events=parent['events'], selected_events=len(descriptor['selected_indices']),
            original_exposure_preserved=True, saved_decays_unchanged=True,
            selection_redraw_allowed=False, evidence_publication=publication,
            policy_sha256=ledger['policy_sha256'],
            ledger_sha256=descriptor['production_selection']['ledger_sha256'])
        immutable(receipt_path,receipt)
        return receipt


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(prepare(json.loads(args.request.read_text())),sort_keys=True))


if __name__ == '__main__':
    main()
