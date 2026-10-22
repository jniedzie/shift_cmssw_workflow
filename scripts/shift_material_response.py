#!/usr/bin/env python3
# Code style: AI-code. AI-generated material-selection response qualification.
"""Compare a frozen selection against an independent detector sample.

The sampling unit is an original event, not a pair. These are Poisson
second-moment planning comparisons, conditional on the saved GEN population.
They do not establish inclusive material normalization or trigger acceptance.
"""
import hashlib
import json
import math
from pathlib import Path
import random
import zipfile
from bisect import bisect_right

from shift_material_preselection import uniform, probability


TARGETS = {'mass_gt20': ('mass', 20.), 'mass_gt50': ('mass', 50.),
           'pt_gt10': ('pt', 10.), 'pt_gt20': ('pt', 20.)}

SHAPE_EDGES = {'mass': [0., 2., 4., 6., 8., 10., 15., 20., 30., 50., 75., 100., 150., 200.],
               'pt': [0., 1., 2., 3., 4., 5., 7.5, 10., 15., 20., 30., 50., 100.]}


def source_record(receipt_path, *, family='qcd', audit_directory=None, gen_path=None):
    """Import one audited mu-enriched source without changing W or exposure.

    The explicit family selects its native accounting adapter. QCD remains
    the default. DY needs staged receipt-bound artifacts; J/psi accepts either
    the original returned archive or staged native audit files. Relocated GEN
    paths must match the original publication hash. A composite HF source
    additionally needs its SHA-bound full source-path
    proof. This admits a conditional detector diagnostic, not a production
    normalization or an extra correction to the native generator weight.
    """
    if family == 'jpsi':
        return weighted_jpsi_source_record(receipt_path, audit_directory, gen_path)
    if family == 'dy':
        return dy_source_record(receipt_path, audit_directory, gen_path)
    if family != 'qcd':
        raise ValueError('Declare the supported source family: qcd, jpsi or dy')
    from audit_shift_weighted_gen import ALGORITHM as base_algorithm, close, validate_ledger
    from qcd_mu_enrichment import PROFILE
    from shift_first_mpi_source_proposal import ALGORITHM as hf_algorithm, validate

    def require(condition, message):
        if not condition:
            raise ValueError(message)

    path = Path(receipt_path).resolve()
    record = json.loads(path.read_text())
    require(record.get('schema') == 'shift-weighted-gen-receipt-v3'
            and all(record.get(name) is True for name in ('complete', 'generated_edm', 'publication_complete'))
            and record.get('physics_valid') is False and record.get('normalization_ready') is False,
            'Needs a complete audited diagnostic source receipt')
    request, ledger, semantic = record['request'], record['ledger'], record['semantic_audit']
    trials = validate_ledger(ledger)
    require(record.get('generator_profile') == PROFILE and request['sample'] == 'qcd'
            and semantic.get('generator_profile') == PROFILE and semantic.get('complete') is True
            and semantic.get('healthy') is True and semantic['ledger'] == ledger
            and semantic['algorithm'] == ledger['algorithm'] == base_algorithm,
            'Muon-enriched source/native ledger audit differs')
    require(ledger['requested_trials'] == request['trials'] == semantic['full_source_trial_denominator']
            and ledger['run'] == request['run'] and [ledger['lower'], ledger['upper']] == request['bounds']
            and semantic.get('no_second_filter_efficiency') is True,
            'Full native source exposure differs from its receipt')
    arrays = [semantic[name] for name in ('event_ids', 'weights', 'scale', 'charged')]
    count = semantic['events']
    require(type(count) is int and count > 0 and record['event_count'] == count
            and all(isinstance(values, list) and len(values) == count for values in arrays),
            'Empty or incomplete saved source census')
    identities = set()
    for identity, weight, scale, charged in zip(*arrays):
        require(isinstance(identity, list) and len(identity) == 3
                and all(type(value) is int for value in identity)
                and identity[:2] == [ledger['run'], 1] and 1 <= identity[2] <= trials
                and tuple(identity) not in identities, 'Repeated or out-of-budget source original')
        identities.add(tuple(identity))
        require(math.isfinite(weight) and weight > 0 and math.isfinite(scale)
                and scale >= ledger['lower'] and (ledger['upper'] == -1 or scale < ledger['upper'])
                and type(charged) is int and charged >= 0, 'Invalid recorded source W/scale/multiplicity')
    # Reconcile only fields actually recorded in the native audit. Its SHA-
    # bound bytes below certify the real EDM process/ownership checks; those
    # event fields must not be invented to rerun a downstream approximation.
    filters = semantic['filter_records']
    require(filters and all(row['total_negative'] == row['pass_negative'] == 0 for row in filters)
            and sum(row['total_positive'] for row in filters) == ledger['accepted']
            and sum(row['pass_positive'] for row in filters) == count <= ledger['accepted']
            and semantic['source_accepted'] == ledger['accepted'], 'Source filter census differs')
    before = math.fsum(row['sum_weights'] for row in filters)
    before2 = math.fsum(row['sum_weights2'] for row in filters)
    after = math.fsum(arrays[1])
    after2 = math.fsum(weight**2 for weight in arrays[1])
    require(0 < after <= before and 0 < after2 <= before2, 'Saved weights exceed full source')
    for actual, expected, label in (
            (before, ledger['sumw'], 'Full source weights'),
            (before2, ledger['sumw2'], 'Full source squared weights'),
            (after, math.fsum(row['sum_pass_weights'] for row in filters), 'Saved weights'),
            (after2, math.fsum(row['sum_pass_weights2'] for row in filters), 'Saved squared weights'),
            (semantic['sumw'], after, 'Native audit saved weights'),
            (semantic['sumw2'], after2, 'Native audit saved squared weights'),
            (ledger['sigma_mb'], ledger['sigma_nd_mb']*before/trials, 'Full source cross section'),
            (ledger['sigma_error_mb'], ledger['sigma_nd_mb']*math.sqrt(
                max(0., before2-before*before/trials)/(trials*(trials-1))), 'Full source cross-section error'),
            (semantic['sigma_weighted_mb'], ledger['sigma_nd_mb']*after/trials, 'Saved cross section'),
            (semantic['error_weighted_mb'], ledger['sigma_nd_mb']*math.sqrt(
                max(0., after2-after*after/trials)/(trials*(trials-1))), 'Saved cross-section error'),
            (semantic['weighted_run_info']['internal_xsec_pb'], ledger['sigma_mb']*1.e9, 'Full source run rate'),
            (semantic['weighted_run_info']['error_pb'], ledger['sigma_error_mb']*1.e9, 'Full source run error'),
            (semantic['weighted_run_info']['filter_efficiency'], 1., 'No second filter efficiency')):
        close(actual, expected, label)
    publications = {entry['name']: entry for entry in record['publication']}
    require(len(publications) == len(record['publication']), 'Repeated source publication')
    def artifact(name):
        item, declared = publications[name], record['artifacts'][name]
        require(item.get('independent_sha256_readback') is True and item['bytes'] == declared['bytes']
                and item['sha256'] == declared['sha256']
                and item['path'] == record['publication_directory']+'/'+name,
                'Source publication differs from audited artifact: '+name)
        return item
    gen = artifact('gen.root')
    require(gen['sha256'] == semantic['input_sha256']
            and artifact('proposal_ledger.json')['sha256'] == semantic['ledger_sha256']
            and artifact('resolved_gen_cfg.py')['sha256'] == semantic['config_sha256']
            and semantic['auditor_sha256'] == record['source_inputs']['audit_shift_weighted_gen.py'],
            'Source GEN/native-audit provenance differs')
    with zipfile.ZipFile(path.with_name('artifacts.zip')) as archive:
        native_audit_bytes = archive.read('semantic_audit.json')
        ledger_bytes = archive.read('proposal_ledger.json')
        proof_bytes = archive.read('source_proposal_audit.json') if record['algorithm'] == hf_algorithm else None
    require(hashlib.sha256(native_audit_bytes).hexdigest() == artifact('semantic_audit.json')['sha256']
            and json.loads(native_audit_bytes) == semantic
            and hashlib.sha256(ledger_bytes).hexdigest() == semantic['ledger_sha256']
            and json.loads(ledger_bytes) == ledger, 'Native source accounting bytes differ from receipt')
    source = dict(source_id=request['id'], stratum=request['stratum'],
        url='root://eosuser.cern.ch/'+gen['path'], gen_sha256=gen['sha256'],
        saved_events=count, full_source_trials=trials,
        sigma_nd_pb=ledger['sigma_nd_mb']*1.e9,
        mean_abs_native_weight=after/count,
        original_receipt=str(path), original_receipt_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        generator_profile=PROFILE, generator_algorithm=record['algorithm'],
        base_sampler_algorithm=base_algorithm, decay_dependent_filter=True,
        saved_decays_unchanged=True, physics_normalization_ready=False)
    if record['algorithm'] == hf_algorithm:
        manifest = dict(algorithm=hf_algorithm, generator_profile=PROFILE,
            source_proposal=record['source_proposal'], inputs=record['source_inputs'],
            maximum_publication_bytes_per_worker=record['publication_size_guard']['reserved_bytes'])
        proposal = validate(manifest, request)
        proof = record['source_proposal_audit']
        proof_publication = artifact('source_proposal_audit.json')
        # The actual returned bytes bind the proof to its published SHA; do not
        # substitute a reserialized or inferred audit for the native evidence.
        require(hashlib.sha256(proof_bytes).hexdigest() == proof_publication['sha256']
                and json.loads(proof_bytes) == proof, 'Missing or changed full source-path proof')
        require(all(proof.get(name) is True for name in ('complete', 'passed', 'natural_decays',
                    'all_intrinsic_retry_paths_checked', 'source_only_native_weight_checked'))
                and proof['algorithm'] == hf_algorithm and proof['base_sampler_algorithm'] == base_algorithm
                and proof['source_proposal'] == proposal and proof['full_source_trials'] == request['trials']
                and proof['first_scatter_calls'] == ledger['proposal_calls']
                and proof['prefilter_accepted_events'] == ledger['accepted']
                and proof['saved_events'] == count, 'Incomplete or inconsistent HF source-path proof')
        require(proof['GEN_sha256'] == gen['sha256'] and proof['ledger_sha256'] == semantic['ledger_sha256']
                and proof['semantic_audit_sha256'] == artifact('semantic_audit.json')['sha256']
                and proof['history_sha256'] == artifact('hard_channel_history.jsonl')['sha256']
                and proof['prefilter_GEN_sha256'] == artifact('prefilter.root')['sha256']
                and proof['auditor_sha256'] == record['source_inputs']['shift_first_mpi_source_proposal.py'],
                'HF source proof is not bound to the published source/history/auditors')
        source.update(source_proposal=proposal, source_proposal_audit=proof_publication,
            native_weight_already_contains_source_correction=True,
            detector_weight_formula='Native W / reconstruction inclusion probability; no second HF or filter factor.')
    else:
        require(record['algorithm'] == base_algorithm and record.get('source_proposal') is None,
                'Unknown or undeclared source proposal algorithm')
    return source


def weighted_jpsi_source_record(receipt_path, audit_directory=None, gen_path=None):
    """Keep the native fixed-trial forced-J/psi convention, without a BR guess."""
    from audit_shift_weighted_gen import ALGORITHM, close, validate_config, validate_ledger
    from generation_publication import sha256
    path = Path(receipt_path).resolve()
    record = json.loads(path.read_text())
    if (record.get('schema') != 'shift-weighted-gen-receipt-v3'
            or not all(record.get(key) is True for key in ('complete', 'generated_edm', 'publication_complete'))
            or record.get('generator_profile', 'ordinary') != 'ordinary' or record.get('algorithm') != ALGORITHM
            or record.get('source_proposal') is not None
            or record.get('physics_valid') is not False or record.get('normalization_ready') is not False):
        raise ValueError('J/psi needs a complete ordinary fixed-trial diagnostic source')
    request, ledger, audit = record['request'], record['ledger'], record['semantic_audit']
    trials = validate_ledger(ledger)
    if (request['sample'] != 'jpsi' or ledger['sample'] != 'direct_jpsi'
            or request['trials'] != trials or request['run'] != ledger['run']
            or request['bounds'] != [ledger['lower'], ledger['upper']]
            or audit.get('schema') != 'shift-weighted-gen-semantic-audit-v1'
            or audit.get('sample') != 'direct_jpsi' or audit.get('generator_profile', 'ordinary') != 'ordinary'
            or audit.get('complete') is not True or audit.get('healthy') is not True
            or audit.get('ledger') != ledger or audit.get('algorithm') != ALGORITHM
            or audit.get('requested') != trials or audit.get('tried') != trials
            or audit.get('bounds') != request['bounds']
            or audit.get('forced_decay') != '443 -> 13 -13; branching convention remains an independent gate'):
        raise ValueError('J/psi request, full-trial ledger or forced-decay audit differs')
    publications = {item['name']: item for item in record['publication']}
    if len(publications) != len(record['publication']):
        raise ValueError('Repeated J/psi publication')

    def artifact(name):
        item = publications[name]
        if (item.get('independent_sha256_readback') is not True
                or item['path'] != record['publication_directory']+'/'+name
                or {key: item[key] for key in ('bytes', 'sha256')} != record['artifacts'][name]):
            raise ValueError('J/psi publication differs from native artifact: '+name)
        return item

    names = ('semantic_audit.json', 'proposal_ledger.json', 'resolved_gen_cfg.py')
    if audit_directory is None:
        with zipfile.ZipFile(path.with_name('artifacts.zip')) as archive:
            payloads = {name: archive.read(name) for name in names}
    else:
        payloads = {}
        for name in names:
            local = Path(audit_directory)/name
            if local.is_symlink():
                raise ValueError('J/psi staged native audit must be an actual file')
            payloads[name] = local.read_bytes()
    for name, expected in (('semantic_audit.json', audit), ('proposal_ledger.json', ledger)):
        data = payloads[name]
        if (len(data) != artifact(name)['bytes']
                or hashlib.sha256(data).hexdigest() != artifact(name)['sha256'] or json.loads(data) != expected):
            raise ValueError('J/psi returned native accounting bytes differ')
    config = payloads['resolved_gen_cfg.py']
    if (len(config) != artifact('resolved_gen_cfg.py')['bytes']
            or hashlib.sha256(config).hexdigest() != artifact('resolved_gen_cfg.py')['sha256']):
        raise ValueError('J/psi resolved configuration differs from publication')
    model = validate_config(config.decode(), ledger)
    gen = artifact('gen.root')
    if gen_path is not None:
        local = Path(gen_path)
        if local.is_symlink() or local.stat().st_size != gen['bytes'] or sha256(local) != gen['sha256']:
            raise ValueError('Relocated J/psi GEN differs from the native publication')
        gen_url = 'root://eosuser.cern.ch/'+str(local) if str(local).startswith('/eos/') else str(local)
    else:
        gen_url = 'root://eosuser.cern.ch/'+gen['path']
    if (audit['input_sha256'] != gen['sha256']
            or audit['ledger_sha256'] != artifact('proposal_ledger.json')['sha256']
            or audit['config_sha256'] != hashlib.sha256(config).hexdigest()
            or audit['auditor_sha256'] != record['source_inputs']['audit_shift_weighted_gen.py']
            or audit['source_model_settings_sha256'] != model):
        raise ValueError('J/psi native GEN/accounting/configuration binding differs')
    count = ledger['accepted']
    arrays = [audit[key] for key in ('event_ids', 'weights', 'scale', 'charged')]
    if count < 1 or audit['events'] != count or record['event_count'] != count or any(len(x) != count for x in arrays):
        raise ValueError('J/psi needs the complete saved GEN census')
    identities = set()
    for identity, weight, scale, charged in zip(*arrays):
        if (len(identity) != 3 or any(type(x) is not int for x in identity)
                or identity[:2] != [ledger['run'], 1] or not 1 <= identity[2] <= trials
                or tuple(identity) in identities or not math.isfinite(weight) or weight <= 0
                or not math.isfinite(scale) or scale < ledger['lower']
                or (ledger['upper'] != -1 and scale >= ledger['upper'])
                or type(charged) is not int or charged < 0):
            raise ValueError('Invalid J/psi original identity, weight or direct-state scale')
        identities.add(tuple(identity))
    sumw, sumw2 = math.fsum(arrays[1]), math.fsum(w*w for w in arrays[1])
    sigma = ledger['sigma_nd_mb']*sumw/trials
    error = ledger['sigma_nd_mb']*math.sqrt(max(0., sumw2-sumw*sumw/trials)/(trials*(trials-1)))
    for actual, expected, label in (
            (sumw, ledger['sumw'], 'J/psi full accepted weights'),
            (sumw2, ledger['sumw2'], 'J/psi full accepted squared weights'),
            (audit['sumw'], sumw, 'J/psi audited weight sum'),
            (audit['sumw2'], sumw2, 'J/psi audited squared-weight sum'),
            (ledger['sigma_mb'], sigma, 'J/psi fixed-trial cross section'),
            (ledger['sigma_error_mb'], error, 'J/psi fixed-trial uncertainty'),
            (audit['sigma_weighted_mb'], sigma, 'J/psi audited cross section'),
            (audit['error_weighted_mb'], error, 'J/psi audited uncertainty'),
            (audit['weighted_run_info']['internal_xsec_pb'], sigma*1.e9, 'J/psi named run cross section'),
            (audit['weighted_run_info']['error_pb'], error*1.e9, 'J/psi named run uncertainty'),
            (audit['weighted_run_info']['filter_efficiency'], 1., 'J/psi no external efficiency')):
        close(actual, expected, label)
    return dict(source_id=request['id'], stratum=request['stratum'], source_family='jpsi',
        url=gen_url, gen_sha256=gen['sha256'],
        saved_events=count, full_source_trials=trials, sigma_nd_pb=ledger['sigma_nd_mb']*1.e9,
        denominator_kind='fixed_source_trials', mean_abs_native_weight=sumw/count,
        original_receipt=str(path), original_receipt_sha256=sha256(path),
        generator_profile='ordinary', generator_algorithm=ALGORITHM,
        timing_input_label='generatorSmeared',
        decay_dependent_filter=False, saved_decays_unchanged=True,
        forced_decay=audit['forced_decay'], forced_decay_branching_convention_ready=False,
        additional_branching_factor_applied=False, physics_normalization_ready=False,
        diagnostic_only=True, config_sha256=audit['config_sha256'],
        source_model_scope='Unchanged native source; jaw/source-material qualification remains separate.')


def dy_source_record(receipt_path, audit_directory, gen_path=None):
    """Import native accepted-event LO DY accounting; these are not trial counts.

    The caller stages the three original receipt-bound artifacts. No network
    access, regenerated metadata, inferred filter efficiency or BR correction
    is hidden in this adapter. A native FWLite replay still qualifies transport.
    """
    from fixed_target_generation import beam_settings, process_settings
    from generation_publication import sha256
    if audit_directory is None:
        raise ValueError('DY requires the original staged gen.root, contract.json and validation.json')
    path, directory = Path(receipt_path).resolve(), Path(audit_directory)
    record = json.loads(path.read_text())
    if (record.get('schema') != 'shift-dag-receipt-v1' or record.get('complete') is not True
            or record.get('physics_valid') is not False or record.get('normalization_ready') is not False
            or not record.get('stratum', '').startswith('dy_')):
        raise ValueError('DY needs a complete native ordinary GEN receipt')
    artifacts = {}
    for name in ('gen.root', 'contract.json', 'validation.json'):
        entries = [item for item in record['artifacts'] if Path(item['path']).name == name]
        local = Path(gen_path) if name == 'gen.root' and gen_path is not None else directory/name
        if (len(entries) != 1 or local.is_symlink() or not local.is_file()
                or local.stat().st_size != entries[0]['bytes'] or sha256(local) != entries[0]['sha256']):
            raise ValueError('DY staged artifact differs from native receipt: '+name)
        artifacts[name] = entries[0]
    contract = json.loads((directory/'contract.json').read_text())
    audit = json.loads((directory/'validation.json').read_text())
    count = record['events']
    if (type(count) is not int or count < 1 or record['requested_events'] != count
            or contract.get('schema') != 'shift-gen-pilot-v1' or contract.get('sample') != 'dy'
            or contract.get('year') != 2023
            or contract.get('requested_events') != count or contract.get('bin_variable') != 'mHat'
            or contract.get('filtering') != 'none' or contract.get('forced_decay') != '23 -> 13 -13'
            or contract.get('detector_simulated') is not False or contract.get('normalization_ready') is not False
            or contract.get('physics_valid') is not False or audit != record['semantic_audit']
            or audit.get('generator_filter') != 'none' or audit.get('physics_valid') is not False
            or audit.get('normalization_ready') is not False or audit['counts']['events'] != count
            or audit['hard_process_codes'] != {'221': count}
            or audit['sum_weights'] != count or audit['sum_weights_squared'] != count):
        raise ValueError('DY native count, process ownership, filtering or forced-decay convention differs')
    bounds = [contract['lower'], contract['upper']]
    if (contract['process_settings'] != process_settings('dy', *bounds)
            or contract['beam_settings'] != beam_settings(6800.) or contract['beam_energy_GeV'] != 6800.
            or contract['decay_policy'] != 'CMS lifetime cutoff; long-lived particles retained for transport'):
        raise ValueError('DY source model/decay contract differs from the supported native generator')
    identities = record['event_ids']
    if (len(identities) != count or len({tuple(x) for x in identities}) != count
            or any(len(x) != 3 or any(type(v) is not int for v in x)
                   or x[:2] != [contract['run_number'], 1] or not 1 <= x[2] <= count for x in identities)):
        raise ValueError('DY requires the complete native generated-event identity census')
    runs = audit['run_info']
    if (len(runs) != 1 or any(not math.isfinite(runs[0][name]) or runs[0][name] < 0
                            for name in ('internal_xsec_pb', 'error_pb'))
            or runs[0]['internal_xsec_pb'] <= 0):
        raise ValueError('DY requires one finite positive native run cross section')
    mass_range = audit['generated_mass_range']
    if (len(mass_range) != 2 or any(not math.isfinite(x) for x in mass_range)
            or mass_range[0] < bounds[0]-1.e-7 or mass_range[1] < mass_range[0]
            or (bounds[1] != -1 and mass_range[1] >= bounds[1]+1.e-7)):
        raise ValueError('DY generated masses differ from their declared mass bin')
    gen = artifacts['gen.root']
    gen_url = ('root://eosuser.cern.ch/'+str(gen_path) if str(gen_path).startswith('/eos/') else str(gen_path)) \
              if gen_path is not None else 'root://eosuser.cern.ch/'+gen['path']
    return dict(source_id=record['stratum']+':part'+str(record['chunk']), stratum=record['stratum'],
        source_family='dy', url=gen_url, gen_sha256=gen['sha256'],
        saved_events=count, full_source_events=count, denominator_kind='accepted_gen_events',
        native_cross_section_pb=runs[0]['internal_xsec_pb'], native_cross_section_error_pb=runs[0]['error_pb'],
        mean_abs_native_weight=1., original_receipt=str(path), original_receipt_sha256=sha256(path),
        generator_profile='ordinary', generator_algorithm='native-unfiltered-unit-weight-LO-DY',
        timing_input_label='VtxSmeared',
        decay_dependent_filter=False, saved_decays_unchanged=True, bounds=bounds,
        decay_policy=contract['decay_policy'], source_model=contract['source_model'],
        forced_decay=contract['forced_decay'], forced_decay_branching_convention_ready=False,
        additional_branching_factor_applied=False, physics_normalization_ready=False,
        diagnostic_only=True, contract_sha256=artifacts['contract.json']['sha256'])


def source_normalization(source):
    """Return the declared native denominator and cross section without conflation."""
    kind = source.get('denominator_kind', 'fixed_source_trials')
    if kind == 'fixed_source_trials':
        count, sigma = source['full_source_trials'], source['sigma_nd_pb']
        if 'full_source_events' in source:
            raise ValueError('A source cannot declare two normalization denominator kinds')
    elif kind == 'accepted_gen_events':
        count, sigma = source['full_source_events'], source['native_cross_section_pb']
        if 'full_source_trials' in source or 'sigma_nd_pb' in source:
            raise ValueError('Accepted LO GEN exposure must not be labelled as source trials')
    else:
        raise ValueError('Unknown native source denominator kind')
    if type(count) is not int or count < 1 or not math.isfinite(sigma) or sigma <= 0:
        raise ValueError('Native source denominator and cross section must be positive')
    return kind, count, sigma


def source_timing_input(source):
    """Choose the native unshifted graph; DY already timed generatorSmeared.

    This reproduces the existing physical source time once. It does not change
    detector electronics, integration windows, BX rules or trigger settings.
    """
    label = source.get('timing_input_label', 'generatorSmeared')
    if label not in ('generatorSmeared', 'VtxSmeared'):
        raise ValueError('Unsupported native source timing input')
    if source.get('source_family') == 'dy' and label != 'VtxSmeared':
        raise ValueError('Native DY generatorSmeared is already timed; use original VtxSmeared once')
    return label


def source_exposures(sources):
    """Pool repeated sources by stratum; never mix accepted events and source trials."""
    exposures, kinds, seen = {}, {}, set()
    for source in sources:
        kind, count, _ = source_normalization(source)
        key = source['stratum']
        if source['gen_sha256'] in seen:
            raise ValueError('Repeated original source exposure')
        seen.add(source['gen_sha256'])
        if kinds.setdefault(key, kind) != kind:
            raise ValueError('Different native denominator conventions share one stratum')
        exposures[key] = exposures.get(key, 0)+count
    return exposures, kinds


def source_rate_weight(source, native_weight, pooled_exposure=None):
    _, count, sigma = source_normalization(source)
    denominator = count if pooled_exposure is None else pooled_exposure
    if type(denominator) is not int or denominator < count or not math.isfinite(native_weight):
        raise ValueError('Invalid pooled source exposure or native weight')
    return native_weight*sigma/denominator


def sampling_probabilities(sample):
    """Keep each original's declared inclusion probability, including controls."""
    values = sample.get('probabilities')
    if values is None:
        values = [sample['probability']]*len(sample['event_ids'])
    if len(values) != len(sample['event_ids']):
        raise ValueError('Sampling probabilities differ from original identities')
    return [probability(value, 'original sampling probability') for value in values]


def response_reporting_scope(plan):
    """Describe the declared cohort without implying wider GEN coverage."""
    sources = plan['sources']
    sampled_sources = {sample['source_id'] for sample in plan['samples']}
    design = plan.get('sampling_design')
    if not design:
        probabilities = {}
        for sample in plan['samples']:
            probabilities.setdefault(sample['source_id'], set()).update(sampling_probabilities(sample))
        if all(len(values) == 1 for values in probabilities.values()):
            design = 'Uniform inclusion within each declared saved source; not the full production.'
        else:
            design = 'Per-original inclusion probabilities declared in the response plan.'
    ordinary = any(source.get('source_family') in ('dy', 'jpsi') for source in sources.values())
    population = plan.get('source_scope',
        ('Saved native GEN; unchanged source and decays. Forced-decay and inclusive material rates unvalidated.'
         if ordinary else
         'Saved finite-cylinder mu-enriched GEN; unchanged decays. Inclusive material rate unvalidated.'))
    mechanism = plan.get('mechanism_study', 'mechanism' in (design+' '+population).lower())
    scope = (f'{len(sampled_sources)} sources with selected originals; '
             f'{len(sources)} declared source exposures. {design}')
    return dict(sampling_design=design, source_population_scope=population,
        source_inclusion_scope=scope, declared_source_count=len(sources),
        sampled_source_count=len(sampled_sources), mechanism_study=mechanism,
        reference_label=('GEN-mass-selected originals' if mechanism
                         else 'Declared reconstruction sample, weighted'),
        inclusive_reconstructed_tail_rates_estimable=False,
        reconstructed_tail_rate_scope=(
            'GEN-mass-selected mechanism study only. Cannot estimate inclusive reconstructed-tail rates; '
            'excluded GEN originals can enter reco tails through charge or momentum errors.' if mechanism
            else 'Conditional on the declared saved-GEN population and sampling design; '
                 'inclusive material normalization remains unvalidated.'))


def shape_comparison(rows, policy):
    """One predeclared hash thinning of the complete benchmark, with exact W/p.

    Error sums group all pairs in each original before squaring. Reference and
    candidate share events; difference variances are conditional thinning
    variances, rather than the sum of two independent-histogram errors.
    """
    out = {}
    for field, edges in SHAPE_EDGES.items():
        hist = dict(edges=edges, reference=[0.]*(len(edges)-1), candidate=[0.]*(len(edges)-1),
                    reference_variance=[0.]*(len(edges)-1), candidate_variance=[0.]*(len(edges)-1),
                    difference_variance=[0.]*(len(edges)-1), overflow_originals=0)
        for row in rows:
            counts = [0]*(len(edges)-1)
            overflow = False
            for pair in row['retained_pairs']:
                if not pair['isOS'] or pair['topologyMin'] != 2 or pair['topologyMax'] != 2:
                    continue
                value = pair[field]
                if value is None or not math.isfinite(value) or value < edges[0]:
                    continue
                index = bisect_right(edges, value)-1
                if index == len(counts):
                    overflow = True
                    continue
                counts[index] += 1
            hist['overflow_originals'] += overflow
            group = [row['original_source_sha256'], row['event_id']]
            admitted = uniform(['material-admission', policy['selection_domain'], policy['selection_seed'], group]) < row['candidate_probability']
            p = row['candidate_probability']
            for i, count in enumerate(counts):
                contribution = count*row['rate_weight']/row['benchmark_probability']
                hist['reference'][i] += contribution
                hist['reference_variance'][i] += contribution**2
                hist['candidate_variance'][i] += contribution**2/p
                hist['difference_variance'][i] += contribution**2*(1.-p)/p
                if admitted:
                    hist['candidate'][i] += contribution/p
        out[field] = hist
    return out


def retained_tail_counts(pairs):
    counts = dict.fromkeys(TARGETS, 0)
    for pair in pairs:
        if not pair['isOS'] or pair['topologyMin'] != 2 or pair['topologyMax'] != 2:
            continue
        for target, (field, threshold) in TARGETS.items():
            if pair[field] is not None and math.isfinite(pair[field]) and pair[field] > threshold:
                counts[target] += 1
    return counts


def compare(rows, minimum_positive_originals=30, minimum_effective_count=25.):
    """Estimate second moment times measured CPU, keeping every original once.

    Baseline inclusion r is the declared per-original response probability.
    Candidate inclusion p is frozen before detector response is observed.
    Rate weights already include the source's full trial normalization once.
    Detector CPU is a measured batch average; feature CPU is charged for every
    original in the candidate, including those it would reject.
    """
    if not rows:
        raise ValueError('No completed response originals')
    seen = set()
    for row in rows:
        key = (row['original_source_sha256'], tuple(row['event_id']))
        if key in seen:
            raise ValueError('Repeated original response; do not count repeated draws independently')
        seen.add(key)
        for name in ('benchmark_probability', 'candidate_probability'):
            if not math.isfinite(row[name]) or not 0 < row[name] <= 1:
                raise ValueError('Response needs positive, finite inclusion probabilities')
        for name in ('detector_cpu_seconds', 'feature_cpu_seconds'):
            if not math.isfinite(row[name]) or row[name] < 0:
                raise ValueError('Invalid measured CPU cost')
        if not math.isfinite(row['rate_weight']):
            raise ValueError('Nonfinite rate weight')
    baseline_cpu = math.fsum(x['detector_cpu_seconds']/x['benchmark_probability'] for x in rows)
    candidate_cpu = math.fsum((x['feature_cpu_seconds'] +
                              x['candidate_probability']*x['detector_cpu_seconds']) /
                             x['benchmark_probability'] for x in rows)
    out = dict(originals=len(rows), baseline_cpu_seconds=baseline_cpu,
               candidate_cpu_seconds=candidate_cpu, targets={},
               cpu_model='Measured per-batch detector average; all feature CPU charged to candidate.',
               tail_improvement_proven=False)
    for target in TARGETS:
        contributions = []
        for row in rows:
            count = row['tail_counts'][target]
            if type(count) is not int or count < 0:
                raise ValueError('Tail count must be a nonnegative integer')
            contributions.append((row, count*row['rate_weight']))
        total = math.fsum(a/x['benchmark_probability'] for x, a in contributions)
        baseline_m2 = math.fsum(a*a/x['benchmark_probability'] for x, a in contributions)
        candidate_m2 = math.fsum(a*a/(x['benchmark_probability']*x['candidate_probability'])
                                 for x, a in contributions)
        positives = sum(x['tail_counts'][target] > 0 for x in rows)
        observed_m2 = math.fsum((a/x['benchmark_probability'])**2 for x, a in contributions)
        neff = total*total/observed_m2 if observed_m2 else 0.
        gain = baseline_m2*baseline_cpu/(candidate_m2*candidate_cpu) if candidate_m2*candidate_cpu else None
        out['targets'][target] = dict(positive_originals=positives, weighted_sum=total,
            baseline_second_moment=baseline_m2, candidate_second_moment=candidate_m2,
            baseline_effective_count=neff, precision_per_CPU_gain_estimate=gain,
            statistics_sufficient=positives >= minimum_positive_originals and neff >= minimum_effective_count)
    # A point estimate, even with enough entries, needs independent uncertainty
    # and shape-closure checks before it can become a production qualification.
    return out


def bootstrap_comparison(rows, replicates=1000, seed=72020, confidence=.95):
    """Original-event bootstrap within pThat bins; never resample individual pairs.

    The interval describes conditional sampling precision with measured batch
    CPU averages. It cannot establish the missing inclusive transport closure.
    """
    if replicates < 100:
        raise ValueError('Need at least 100 bootstrap replicates')
    if not 0 < confidence < 1:
        raise ValueError('Confidence must lie strictly between zero and one')
    result = compare(rows)
    groups = {}
    for row in rows:
        groups.setdefault(row['stratum'], []).append(row)
    generator = random.Random(seed)
    samples = {target: [] for target in TARGETS}
    # Keep the validated original table. Bootstrap duplicates represent draw
    # multiplicities and must not bypass compare()'s scientific duplicate gate.
    for _ in range(replicates):
        indices = [group[generator.randrange(len(group))]
                   for group in groups.values() for _ in group]
        baseline_cpu = math.fsum(x['detector_cpu_seconds']/x['benchmark_probability'] for x in indices)
        candidate_cpu = math.fsum((x['feature_cpu_seconds'] +
                                  x['candidate_probability']*x['detector_cpu_seconds']) /
                                 x['benchmark_probability'] for x in indices)
        for target in TARGETS:
            baseline = math.fsum((x['tail_counts'][target]*x['rate_weight'])**2 /
                                 x['benchmark_probability'] for x in indices)
            candidate = math.fsum((x['tail_counts'][target]*x['rate_weight'])**2 /
                                  (x['benchmark_probability']*x['candidate_probability']) for x in indices)
            if candidate*candidate_cpu:
                samples[target].append(baseline*baseline_cpu/(candidate*candidate_cpu))
    for target, values in samples.items():
        record = result['targets'][target]
        interval = None
        if len(values) == replicates:
            values.sort()
            alpha = (1.-confidence)/2.
            interval = [values[int(alpha*(replicates-1))], values[int((1.-alpha)*(replicates-1))]]
        record.update(precision_per_CPU_gain_interval=interval,
            precision_per_CPU_gain_95pct_bootstrap=interval if confidence == .95 else None,
            conditional_gain_supported=bool(record['statistics_sufficient'] and interval and interval[0] > 1.),
            bootstrap_nonempty_replicates=len(values))
    result['bootstrap'] = dict(unit='original event', strata='declared source bin', seed=seed,
                              replicates=replicates, confidence=confidence, method='percentile')
    result['tail_improvement_proven'] = all(result['targets'][key]['conditional_gain_supported']
                                          for key in ('mass_gt20', 'pt_gt10'))
    result['scope'] = 'Conditional saved-GEN response; measured batch-average CPU model.'
    return result
