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


def source_record(receipt_path):
    """Import one audited mu-enriched source without changing W or exposure.

    A composite HF source additionally needs its SHA-bound full source-path
    proof. This admits a conditional detector diagnostic, not a production
    normalization or an extra correction to the native generator weight.
    """
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
    population = plan.get('source_scope',
        'Saved finite-cylinder mu-enriched GEN; unchanged decays. Inclusive material rate unvalidated.')
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
    result['bootstrap'] = dict(unit='original event', strata='pThat bin', seed=seed,
                              replicates=replicates, confidence=confidence, method='percentile')
    result['tail_improvement_proven'] = all(result['targets'][key]['conditional_gain_supported']
                                          for key in ('mass_gt20', 'pt_gt10'))
    result['scope'] = 'Conditional saved-GEN response; measured batch-average CPU model.'
    return result
