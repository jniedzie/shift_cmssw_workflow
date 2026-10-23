# Code style: AI-code. AI-generated frozen-canary production admission.
"""Release a preallocated production after its real downstream tests pass.

This writes no favourable replacement physics settings and performs no retry.
The ordinary controller still checks live storage before admitting each batch.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
from datetime import datetime, timezone


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            digest.update(block)
    return digest.hexdigest()


def source_evidence(config, batch):
    directory = Path(config['directory']) / 'batches' / batch['batch']
    status_path = directory / 'source_status.json'
    if not status_path.exists() or read(status_path).get('complete') is not True:
        return None
    status = read(status_path)
    for name, expected in status['metadata_sha256'].items():
        if sha(name) != expected:
            raise ValueError('Automatic admission found changed GEN evidence: ' + name)
    gen = directory / 'gen'
    contract = read(gen / 'contract.json')
    audit = read(gen / 'validation.json')
    manifest = read(gen / 'manifest.json')
    publication = status['publication']
    if (not audit.get('runtime_validated')
            or contract.get('meson_model_card_sha256') != sha(config['model_card'])
            or contract['source_placement']['profile_sha256'] != config['jaw_source_profile']['sha256']
            or manifest['worker_local_runtime']['archive_sha256'] != config['gen_runtime']['sha256']
            or manifest['output_sha256'] != status['parent']['gen_sha256']
            or publication['sha256'] != status['parent']['gen_sha256']
            or publication.get('readback_sha256') != publication['sha256']
            or audit['event_ids'] != status['parent']['event_ids']
            or contract['source_exposure']['fixed_source_collisions'] != batch['source_collisions']
            or contract['source_exposure']['accepted_signal_events'] != status['parent']['events']):
        raise ValueError('Automatic admission GEN does not qualify this frozen model/runtime/source')
    material_path = gen / 'material_selection_receipt.json'
    if not material_path.exists():
        return None
    material = read(material_path)
    descriptor_path = gen / 'material_selected_source.json'
    if (material.get('complete') is not True
            or material.get('full_gen_events') != status['parent']['events']
            or sha(descriptor_path) != material['descriptor_sha256']
            or material.get('original_exposure_preserved') is not True
            or material.get('saved_decays_unchanged') is not True
            or material.get('selection_redraw_allowed') is not False):
        raise ValueError('Automatic admission requires an immutable full-cohort material draw')
    evidence = material['evidence_publication']
    if evidence.get('sha256') != evidence.get('readback_sha256'):
        raise ValueError('Material evidence lacks its verified readback')
    from shift_production_selection import validate_contract
    descriptor = read(descriptor_path)
    validate_contract(descriptor['production_selection'])
    if (descriptor['gen_sha256'] != status['parent']['gen_sha256']
            or descriptor['event_ids'] != status['parent']['event_ids']
            or len(descriptor['selected_indices']) != material['selected_events']):
        raise ValueError('Material descriptor lost the original full GEN population')
    return dict(batch=batch['batch'], mass_gev=batch['mass_gev'],
        source_status_sha256=sha(status_path), material_receipt_sha256=sha(material_path),
        full_gen_events=material['full_gen_events'], selected_events=material['selected_events'])


def fullchain_evidence(config, batch, entry, transport_factory=None):
    if not entry.get('published') or entry.get('detector_native_complete') is not True:
        return None
    directory = Path(config['directory']) / 'batches' / batch['batch'] / 'production'
    plan = read(directory / 'plan.json')
    row = plan['points'][0]
    report_path = directory / 'completed_points' / (batch['point'] + '.json')
    report = read(report_path)
    if (report.get('rate_only') or report.get('complete') is not True
            or report.get('full_gen_events') != row['full_gen_events']
            or report.get('events') != row['events']
            or report.get('weight_branch') != 'shiftSamplingGenWeight'
            or type(report.get('canonical_sm_histogram_axes_verified')) is not int
            or report['canonical_sm_histogram_axes_verified'] < 1
            or len(report.get('canonical_sm_histogram_authority_sha256', '')) != 64
            or plan['detector_runtime']['runtime_sha256'] != config['detector_runtime']['runtime_sha256']):
        raise ValueError('Automatic admission needs a complete weighted full-chain canary')
    selected = row['material_selection']
    expected_sumw = math.fsum(1. / p for p in selected['sampling_probabilities'])
    if not math.isclose(report['histogram_sumw'], expected_sumw, rel_tol=1.e-12):
        raise ValueError('Canary histogram weights do not preserve W/p')
    from watch_dark_photon_production import EosRpc, completed_histogram
    factory = transport_factory or EosRpc
    transport = factory('root://eosuser.cern.ch', directory / 'automatic_admission_readback')
    candidate = dict(source=dict(directory=directory, campaign=plan['campaign'],
        plan_sha256=sha(directory / 'plan.json'), seal_sha256=sha(directory / 'seal.json')),
        row=row, output=Path(row['final_output_base']))
    result = completed_histogram(candidate, transport)
    if result is None:
        return None
    published = entry['published']
    for key in ('histogram_path', 'histogram_sha256', 'point_receipt_sha256'):
        if published.get(key) != result[key]:
            raise ValueError('Canary controller publication differs from the exact readback')
    return dict(batch=batch['batch'], point=batch['point'],
        point_receipt_sha256=sha(report_path), histogram_sha256=result['histogram_sha256'],
        events=row['events'], full_gen_events=row['full_gen_events'], histogram_sumw=expected_sumw)


def maybe_create_bulk_gate(config, state):
    settings = config.get('automatic_bulk_admission')
    if settings is None:
        return None
    gate_path = Path(config['bulk_storage_gate'])
    config_sha = sha(Path(config['directory']) / 'config.json')
    if gate_path.exists():
        gate = read(gate_path)
        if (gate.get('automatic_canary_admission') is not True
                or gate.get('worklist_sha256') != config_sha
                or gate.get('model_card_sha256') != sha(config['model_card'])
                or gate.get('per_batch_forecast_bytes') != settings['per_batch_forecast_bytes']):
            raise ValueError('Existing automatic bulk gate belongs to another frozen request')
        return gate
    if settings.get('schema') != 'shift-dp-automatic-bulk-admission-v1':
        raise ValueError('Unknown automatic admission contract')
    forecasts = settings['per_batch_forecast_bytes']
    batches = config['batches']
    if (set(forecasts) != {batch['batch'] for batch in batches}
            or any(type(value) is not int or value <= 0 for value in forecasts.values())
            or sum(forecasts.values()) > config['maximum_meson_forecast_bytes']):
        raise ValueError('Automatic admission changed the frozen batch/storage allocation')
    entries = state.get('batches', {})
    gen_batches = [b for b in batches if b.get('gen_qualification_only')]
    expected_masses = set(config['coverage_audit']['masses_gev'])
    if {b['mass_gev'] for b in gen_batches} != expected_masses:
        raise ValueError('Automatic admission needs native source qualification at every requested mass')
    sources = []
    for batch in gen_batches:
        if not entries.get(batch['batch'], {}).get('gen_qualification', {}).get('complete'):
            return None
        evidence = source_evidence(config, batch)
        if evidence is None:
            return None
        proof = entries[batch['batch']]['gen_qualification']
        for key in ('source_status_sha256', 'material_receipt_sha256'):
            if key in evidence and proof.get(key) != evidence[key]:
                raise ValueError('Native qualification changed before automatic admission')
        sources.append(evidence)
    canaries = [b for b in batches if b['operational_canary'] and not b.get('gen_qualification_only')]
    if len(canaries) < 2 or not any(b['prompt'] for b in canaries) or not any(not b['prompt'] for b in canaries):
        raise ValueError('Need separate prompt and displaced full-chain canaries')
    chains = []
    for batch in canaries:
        if source_evidence(config, batch) is None:
            return None
        result = fullchain_evidence(config, batch, entries.get(batch['batch'], {}))
        if result is None:
            return None
        chains.append(result)
    gate = dict(schema='shift-meson-bulk-storage-gate-v1', complete=True,
        automatic_canary_admission=True, all_requested_mass_GEN_audits_passed=True,
        worklist_sha256=config_sha, model_card_sha256=sha(config['model_card']),
        per_batch_forecast_bytes=forecasts, total_meson_forecast_bytes=sum(forecasts.values()),
        source_qualification=sources, fullchain_qualification=chains,
        created_utc=datetime.now(timezone.utc).isoformat(),
        storage_remains_checked_before_each_admission=True)
    # Publish a complete gate atomically. A killed controller must not leave a
    # half-written gate that needs manual repair over the weekend.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=gate_path.parent,
                prefix='.' + gate_path.name + '.', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(gate, stream, indent=2, sort_keys=True)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, gate_path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return gate
