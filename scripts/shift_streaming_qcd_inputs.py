#!/usr/bin/env python3
# Code style: AI-code. AI-generated streaming QCD selection and exposure contracts.
"""Prepare fixed GEN cohorts without using detector outcomes or finish order.

Each histogram keeps native W/p. Only a complete, predeclared cohort may use
sigmaND_pb / sum(full source trials); rejected source/filter events remain in
that denominator. GEN feature priorities are sampling cues, not acceptance.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import uuid

from qcd_mu_enrichment import PROFILE, receipt_estimator

STRATA = ('qcd_0to1', 'qcd_1to2', 'qcd_2to5', 'qcd_5to10', 'qcd_10to20', 'qcd_20toinf')
DEFAULT_POLICY = dict(schema='shift-stream-GEN-selection-v1', mode='GEN-stratified-fixed-quota',
    seed='qcd-mu-enhanced-stream-20261016-v1', quotas=dict(tail=6, forward_OS=3, remainder=1),
    training_seed='qcd-mu-enhanced-original-split-v1', training_folds=10)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def load(path):
    return json.loads(Path(path).read_text())


def write_new(path, value):
    """Publish a new immutable descriptor atomically; never overwrite a live one."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temporary.open('x') as stream:
            stream.write(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)+'\n')
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def validate_policy(policy):
    if policy.get('schema') != 'shift-stream-GEN-selection-v1' or policy.get('mode') not in (
            'GEN-stratified-fixed-quota', 'uniform-fixed-quota'):
        raise ValueError('Unknown immutable GEN selection policy')
    if not policy.get('seed') or not policy.get('training_seed') or policy['seed'] == policy['training_seed']:
        raise ValueError('Selection and original-event training split need distinct frozen seeds')
    if type(policy.get('training_folds')) is not int or policy['training_folds'] < 2:
        raise ValueError('Need at least two independent original-event training folds')
    quotas = policy.get('quotas')
    if not isinstance(quotas, dict) or set(quotas) != {'tail', 'forward_OS', 'remainder'}:
        raise ValueError('Declare all three GEN-only sampling strata')
    if any(type(n) is not int or n < 1 for n in quotas.values()):
        raise ValueError('Every nonempty GEN stratum needs positive sampling support')
    return policy


def planned_cohort(manager, prefix=1, policy=None):
    """Choose the fixed prefix from planned rows, before consulting completions."""
    if prefix is not None and (type(prefix) is not int or prefix < 1):
        raise ValueError('Positive predetermined source-prefix length required')
    manager = Path(manager).resolve()
    policy = json.loads(json.dumps(DEFAULT_POLICY if policy is None else policy))
    validate_policy(policy)
    ordinary, weighted = load(manager/'ordinary/manifest.json'), load(manager/'weighted/manifest.json')
    gate = load(manager/'weighted/scientific_gate.json')
    hashes = {kind:sha(manager/kind/'manifest.json') for kind in ('ordinary', 'weighted')}
    if (not gate.get('pass') or gate.get('manifest_sha256') != hashes['weighted']
            or gate.get('generator_profile') != PROFILE
            or any(m.get('generator_profile') != PROFILE for m in (ordinary, weighted))):
        raise ValueError('The weighted fixed budget/profile has not passed its frozen gate')
    limit = gate['production_plan']['qcd_20toinf']['chunks']
    rows = []
    for kind, manifest in [('ordinary', ordinary), ('weighted', weighted)]:
        for request in manifest['jobs']:
            if request['stage'] != 'production' or (prefix is not None and request['chunk_index'] >= prefix):
                continue
            if kind == 'weighted' and request['chunk_index'] >= limit:
                continue
            rows.append(dict(source_id=request['id'], stratum=request['stratum'], request=request,
                manifest=str(manager/kind/'manifest.json'), manifest_sha256=hashes[kind],
                receipt=str(manager/kind/'results'/(request['id']+'.json'))))
    for stratum in STRATA:
        indices = sorted(row['request']['chunk_index'] for row in rows if row['stratum'] == stratum)
        expected_count = prefix if prefix is not None else len(indices)
        if not indices or indices != list(range(expected_count)):
            raise ValueError('Prefix exceeds the fixed production budget in '+stratum)
    rows.sort(key=lambda row:(STRATA.index(row['stratum']), row['request']['chunk_index']))
    cohort_id = 'qcd_GEN_complete_production' if prefix is None else 'qcd_GEN_prefix_'+str(prefix).zfill(6)
    plan = dict(schema='shift-streaming-source-plan-v1', cohort_id=cohort_id,
        generator_profile=PROFILE, prefix_sources_per_stratum=prefix, source_slots=rows,
        selection_policy=policy, policy_sha256=digest(policy),
        membership_basis='Fixed chunk-index prefix of the scientific manifests, independent of availability and detector outcomes',
        all_declared_sources_and_selected_originals_required=True, incomplete_preview='raw counts or unit area only',
        physical_acceptance_validated=False)
    plan['plan_sha256'] = digest(plan)
    return plan


def validate_plan(plan):
    value = dict(plan)
    checksum = value.pop('plan_sha256', None)
    if value.get('schema') != 'shift-streaming-source-plan-v1' or digest(value) != checksum:
        raise ValueError('Frozen source-cohort plan changed')
    validate_policy(plan['selection_policy'])
    if digest(plan['selection_policy']) != plan['policy_sha256']:
        raise ValueError('Frozen selection policy changed')


def planned_campaign(manager, policy=None):
    """One immutable sampling policy for all 900 ordinary and allocated high rows."""
    return planned_cohort(manager, prefix=None, policy=policy)


def source_descriptor(receipt_path, manifest_path):
    """Read a published production receipt, including its complete source exposure."""
    receipt_path, manifest_path = Path(receipt_path), Path(manifest_path)
    record, manifest = load(receipt_path), load(manifest_path)
    request, audit = record['request'], record['semantic_audit']
    if (request['stage'] != 'production' or record.get('complete') is not True
            or record.get('publication_complete') is not True or record.get('exit_status') != 0
            or record.get('manifest_sha256') != sha(manifest_path)
            or request not in manifest['jobs'] or record.get('source_inputs') != manifest['inputs']
            or record.get('generator_profile') != PROFILE or manifest.get('generator_profile') != PROFILE
            or audit.get('generator_profile') != PROFILE or not audit.get('complete') or not audit.get('healthy')
            or request['stratum'] not in STRATA):
        raise ValueError('Source is not a matching completed mu-enhanced production receipt')
    ids, weights = audit['event_ids'], audit['weights']
    count = record['event_count']
    if (count != audit['events'] or len(ids) != count or len(weights) != count
            or len({tuple(identity) for identity in ids}) != count):
        raise ValueError('Source saved-event identity/weight arrays differ')
    for identity, weight in zip(ids, weights):
        if (len(identity) != 3 or any(type(v) is not int for v in identity)
                or identity[0] != request['run'] or identity[1] != 1
                or not 1 <= identity[2] <= request['trials']
                or not math.isfinite(weight) or weight <= 0):
            raise ValueError('Invalid original identity or native GEN weight')
    if record['schema'] == 'shift-weighted-gen-receipt-v3':
        estimate = receipt_estimator(record)
        trials, nd = record['ledger']['tried'], record['ledger']['sigma_nd_mb']*1.e9
        if trials != request['trials']:
            raise ValueError('Weighted source lost fixed-trial exposure')
    elif record['schema'] == 'shift-mu-enriched-ordinary-gen-receipt-v1':
        trials = audit['full_source_trial_denominator']
        accepted = audit['source_accepted']
        if (type(trials) is not int or trials < accepted or accepted != request['trials']
                or any(weight != 1. for weight in weights) or audit.get('no_second_filter_efficiency') is not True):
            raise ValueError('Ordinary source/filter accounting differs')
        nd = audit['native_generator_run_info']['internal_xsec_pb']*trials/accepted
        if not math.isclose(nd, audit['sigma_nd_mb']*1.e9, rel_tol=1.e-9):
            raise ValueError('Ordinary ND reference differs from full native exposure')
        filters = audit['filter_records']
        if (sum(row['total_positive'] for row in filters) != accepted
                or sum(row['pass_positive'] for row in filters) != count
                or any(row['total_negative'] or row['pass_negative'] for row in filters)):
            raise ValueError('Ordinary muon filter lost source or saved events')
        if not math.isclose(audit['sigma_mb']*1.e9, nd*count/trials, rel_tol=1.e-9, abs_tol=1.e-300):
            raise ValueError('Muon filter efficiency was applied twice or exposure changed')
    else:
        raise ValueError('Unsupported GEN production receipt')
    files = {item['name']:item for item in record['publication']}
    gen = files['gen.root']
    if (gen['path'] != request['output_base']+'/'+request['id']+'/gen.root'
            or gen['sha256'] != audit['input_sha256'] or gen['bytes'] <= 0
            or any(not item.get('independent_sha256_readback') for item in files.values())):
        raise ValueError('GEN publication is not bound to the audited source')
    return dict(schema='shift-streaming-GEN-source-v1', source_id=request['id'], stratum=request['stratum'],
        generator_profile=PROFILE, request=request, receipt=str(receipt_path.resolve()), receipt_sha256=sha(receipt_path),
        gen=gen['path'], gen_sha256=gen['sha256'], gen_bytes=gen['bytes'], gen_path_resolved=True,
        event_ids=ids, weights=weights, complete_muon_decays=False,
        normalization_record=files.get('proposal_ledger.json', files['semantic_audit.json']),
        normalization=dict(schema='shift-stream-source-exposure-v1', source_id=request['id'],
            stratum=request['stratum'], gen_sha256=gen['sha256'], sigma_nd_pb=nd,
            full_source_trials=trials, source_saved_events=count, native_saved_sumw=math.fsum(weights),
            histogram_weight='native_gen_weight/reco_inclusion_probability', no_second_filter_efficiency=True))


def discover_sources(plan):
    """Missing planned files remain pending, rather than being replaced by fast ones."""
    if isinstance(plan, (str, Path)):
        plan = planned_cohort(plan, prefix=None)
    validate_plan(plan)
    sources, pending = [], []
    for slot in plan['source_slots']:
        if not Path(slot['receipt']).exists():
            pending.append(slot['source_id'])
            continue
        source = source_descriptor(slot['receipt'], slot['manifest'])
        if source['request'] != slot['request'] or sha(slot['manifest']) != slot['manifest_sha256']:
            raise ValueError('Fixed source slot changed')
        sources.append(source)
    return dict(schema='shift-stream-source-discovery-v1', cohort_id=plan['cohort_id'],
                complete=not pending, pending_source_ids=pending, sources=sources)


def eligible(muon):
    pt = math.hypot(muon['px'], muon['py'])
    eta = math.asinh(muon['pz']/pt) if pt else math.copysign(math.inf, muon['pz'])
    return (muon.get('x_mm') is not None and -10. < eta < 0.
            and 0. <= muon['z_mm'] <= 151000. and math.hypot(muon['x_mm'], muon['y_mm']) <= 8000.)


def feature_stratum(muons):
    """Use only saved stable GEN daughters; never infer detector acceptance."""
    muons = [muon for muon in muons if eligible(muon)]
    tail = any(math.hypot(m['px'], m['py']) > 5. for m in muons)
    forward = False
    for index, a in enumerate(muons):
        for b in muons[index+1:]:
            if a['pdg_id']*b['pdg_id'] >= 0:
                continue
            momentum = math.fsum((a[key]+b[key])**2 for key in ('px', 'py', 'pz'))
            mass = math.sqrt(max(0., (a['energy']+b['energy'])**2-momentum))
            pair_pt = math.hypot(a['px']+b['px'], a['py']+b['py'])
            tail |= mass > 10. or pair_pt > 10.
            forward |= all(math.sqrt(m['px']**2+m['py']**2+m['pz']**2) > 10. for m in (a, b))
    return 'tail' if tail else ('forward_OS' if forward else 'remainder')


def scan_gen_features(source, input_path):
    """Run inside the existing CMSSW runtime; reuse the worker's downloaded GEN."""
    if sha(input_path) != source['gen_sha256']:
        raise ValueError('GEN feature input differs from frozen publication')
    from DataFormats.FWLite import Events, Handle
    records = []
    for raw_index, event in enumerate(Events(str(input_path))):
        auxiliary = event.eventAuxiliary()
        identity = [int(auxiliary.run()), int(auxiliary.luminosityBlock()), int(auxiliary.event())]
        info = Handle('GenEventInfoProduct')
        event.getByLabel('generator', info)
        if not info.isValid() or raw_index >= len(source['event_ids']):
            raise ValueError('Missing source GEN record or unexpected event')
        weight = float(info.product().weight())
        if identity != source['event_ids'][raw_index] or weight != source['weights'][raw_index]:
            raise ValueError('Feature scan changed native identities or Double weights')
        handle = Handle('edm::HepMCProduct')
        event.getByLabel(('generator', 'unsmeared'), handle)
        if not handle.isValid():
            raise ValueError('Missing original GEN HepMC')
        hep = handle.product().GetEvent()
        if float(hep.weights()[0]) != weight:
            raise ValueError('HepMC nominal weight differs')
        muons = []
        iterator, end = hep.particles_begin(), hep.particles_end()
        while iterator != end:
            particle = iterator.__deref__()
            iterator.__preinc__()
            if abs(int(particle.pdg_id())) != 13 or int(particle.status()) != 1:
                continue
            m, vertex = particle.momentum(), particle.production_vertex()
            v = vertex.position() if vertex else None
            muons.append(dict(pdg_id=int(particle.pdg_id()), px=float(m.px()), py=float(m.py()),
                pz=float(m.pz()), energy=float(m.e()), x_mm=float(v.x()) if v else None,
                y_mm=float(v.y()) if v else None, z_mm=float(v.z()) if v else None))
        records.append(dict(event_id=identity, native_weight=weight, feature_stratum=feature_stratum(muons)))
    if len(records) != len(source['event_ids']):
        raise ValueError('Incomplete GEN feature population')
    return dict(schema='shift-stream-GEN-features-v1', complete=True, gen_sha256=source['gen_sha256'],
        source_id=source['source_id'], events=records, uses_only_original_stable_GEN_muons=True,
        definition='A: filter-region muon pT>5 or OS mass/pair-pT>10; B: remaining filter-region OS pair with both p>10; C: remainder',
        detector_acceptance_claim=False)


def hash_rank(seed, domain, identity):
    return int(digest([seed, domain, identity]), 16)


def select_events(source, plan, features=None):
    validate_plan(plan)
    policy = plan['selection_policy']
    slots = [slot for slot in plan['source_slots'] if slot['source_id'] == source['source_id']]
    if len(slots) != 1 or slots[0]['request'] != source['request']:
        raise ValueError('GEN source is not a member of the predetermined cohort')
    ids, weights = source['event_ids'], source['weights']
    if policy['mode'] == 'GEN-stratified-fixed-quota':
        if (not features or features.get('schema') != 'shift-stream-GEN-features-v1'
                or features.get('gen_sha256') != source['gen_sha256']
                or features.get('source_id') != source['source_id']
                or features.get('uses_only_original_stable_GEN_muons') is not True
                or len(features['events']) != len(ids)):
            raise ValueError('Need complete matching upstream-only GEN features before priority sampling')
        labels = []
        for index, row in enumerate(features['events']):
            if row['event_id'] != ids[index] or row['native_weight'] != weights[index]:
                raise ValueError('Feature rows do not preserve source order, IDs and native weights')
            label = row['feature_stratum']
            if label not in policy['quotas']:
                raise ValueError('Unknown GEN priority stratum')
            labels.append(label)
    else:
        labels = ['remainder']*len(ids)
    selected, populations = [], {}
    for label in ('tail', 'forward_OS', 'remainder'):
        population = [index for index, value in enumerate(labels) if value == label]
        quota = policy['quotas'][label] if policy['mode'] != 'uniform-fixed-quota' else sum(policy['quotas'].values())
        number = min(quota, len(population))
        probability = number/len(population) if population else None
        populations[label] = dict(population=len(population), selected=number, inclusion_probability=probability)
        order = sorted(population, key=lambda index:(hash_rank(policy['seed'], 'selection', ids[index]), ids[index]))
        for index in order[:number]:
            selected.append(dict(raw_index=index, event_id=ids[index], native_weight=weights[index],
                sampling_probability=probability, sampling_gen_weight=weights[index]/probability,
                training_fold=hash_rank(policy['training_seed'], 'training', ids[index]) % policy['training_folds'],
                feature_stratum=label))
    selected.sort(key=lambda row:row['raw_index'])
    result = dict(source)
    source_selection = dict(source_id=source['source_id'], gen_sha256=source['gen_sha256'],
        policy_sha256=plan['policy_sha256'], features_sha256=digest(features) if features is not None else None)
    result.update(cohort_id='source_'+source['source_id'], preview_cohort_id=plan['cohort_id'], selection_complete=True,
        selected_indices=[row['raw_index'] for row in selected],
        sampling_probabilities=[row['sampling_probability'] for row in selected], selected_events=selected,
        sampling=dict(schema='shift-stream-fixed-GEN-sampling-v1', plan_sha256=digest(source_selection),
            source_selection_contract=source_selection,
            selection_policy_sha256=plan['policy_sha256'], populations=populations,
            features_sha256=digest(features) if features is not None else None,
            no_detector_outcomes_used=True, positive_inclusion_for_every_nonempty_stratum=True,
            native_weight_convention='Native W unchanged; shiftSamplingGenWeight=W/p',
            variance_note='Fixed-size samples without replacement; original events, not pairs, are independent units'),
        complete_muon_decays=False, physics_valid=False, normalization_ready=False)
    return result


def closed_normalization(plan, sources, observed):
    """Require every fixed source and selected original, including zero-muon events."""
    validate_plan(plan)
    if {s['source_id'] for s in sources} != {slot['source_id'] for slot in plan['source_slots']}:
        raise ValueError('Cannot normalize an incomplete or fastest-finished source cohort')
    if len({s['source_id'] for s in sources}) != len(sources) or len({s['gen_sha256'] for s in sources}) != len(sources):
        raise ValueError('Duplicate GEN source exposure')
    expected = {}
    by_stratum = {}
    for source in sources:
        slots = [slot for slot in plan['source_slots'] if slot['source_id'] == source['source_id']]
        sampling = source['sampling']
        if (len(slots) != 1 or slots[0]['request'] != source['request']
                or sampling['selection_policy_sha256'] != plan['policy_sha256']
                or sampling['plan_sha256'] != digest(sampling['source_selection_contract'])
                or source['complete_muon_decays'] is not False):
            raise ValueError('Source selection plan or saved decay graph changed')
        for row in source['selected_events']:
            key = tuple(row['event_id'])
            if key in expected:
                raise ValueError('Duplicate original in cohort')
            expected[key] = row
        by_stratum.setdefault(source['stratum'], []).append(source['normalization'])
    seen = set()
    for row in observed:
        key = tuple(row['event_id'])
        if key in seen or key not in expected:
            raise ValueError('Duplicate or unexpected reconstructed original')
        seen.add(key)
        if any(row[field] != expected[key][field] for field in ('native_weight', 'sampling_probability')):
            raise ValueError('Reconstruction changed native weight or inclusion probability')
    if seen != set(expected):
        raise ValueError('Partial reconstruction cohort is diagnostic only')
    result = {}
    for stratum, records in by_stratum.items():
        nd = records[0]['sigma_nd_pb']
        if any(not math.isclose(row['sigma_nd_pb'], nd, rel_tol=1.e-9) for row in records):
            raise ValueError('GEN ND reference differs across sources')
        denominator = sum(row['full_source_trials'] for row in records)
        result[stratum] = dict(schema='shift-stream-source-exposure-v1', stratum=stratum,
            sigma_nd_pb=nd, full_source_trials=denominator, source_saved_events=sum(row['source_saved_events'] for row in records),
            native_saved_sumw=math.fsum(row['native_saved_sumw'] for row in records),
            source_exposures=records, scale_pb_per_histogram_weight=nd/denominator,
            histogram_weight='native_gen_weight/reco_inclusion_probability', no_second_filter_efficiency=True)
    return dict(schema='shift-stream-closed-normalization-v1', cohort_id=plan['cohort_id'],
        plan_sha256=plan['plan_sha256'], complete=True, strata=result,
        selected_originals=len(expected), all_declared_source_exposures_counted_once=True,
        physical_detector_response_validated=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    plan = sub.add_parser('plan'); plan.add_argument('--manager', required=True); plan.add_argument('--prefix', type=int, default=1); plan.add_argument('--output', required=True)
    discover = sub.add_parser('discover'); discover.add_argument('--plan', required=True); discover.add_argument('--output', required=True)
    scan = sub.add_parser('scan'); scan.add_argument('--source', required=True); scan.add_argument('--input'); scan.add_argument('--output', required=True)
    select = sub.add_parser('select'); select.add_argument('--plan', required=True); select.add_argument('--source', required=True); select.add_argument('--features'); select.add_argument('--output', required=True)
    args = parser.parse_args()
    if args.command == 'plan': value = planned_cohort(args.manager, args.prefix)
    elif args.command == 'discover': value = discover_sources(load(args.plan))
    elif args.command == 'scan':
        source = load(args.source)
        if args.input:
            value = scan_gen_features(source, args.input)
        else:
            from run_shift_gen_to_nano import copy_gen_input
            with tempfile.TemporaryDirectory(prefix='shift-stream-GEN-') as directory:
                path = Path(directory)/'gen.root'
                copy_gen_input(source, path)
                value = scan_gen_features(source, path)
    else: value = select_events(load(args.source), load(args.plan), load(args.features) if args.features else None)
    write_new(args.output, value)
    print(json.dumps(dict(command=args.command, output=str(Path(args.output).resolve()), sha256=sha(args.output))))


if __name__ == '__main__':
    main()
