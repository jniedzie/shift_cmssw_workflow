#!/usr/bin/env python3
# Code style: AI-code. AI-generated material-selection response qualification.
"""Compare a frozen selection against an independent random detector sample.

The sampling unit is an original event, not a pair. These are Poisson
second-moment planning comparisons, conditional on the saved GEN population.
They do not establish inclusive material normalization or trigger acceptance.
"""
import math
import random
from bisect import bisect_right

from shift_material_preselection import uniform, probability


TARGETS = {'mass_gt20': ('mass', 20.), 'mass_gt50': ('mass', 50.),
           'pt_gt10': ('pt', 10.), 'pt_gt20': ('pt', 20.)}

SHAPE_EDGES = {'mass': [0., 2., 4., 6., 8., 10., 15., 20., 30., 50., 75., 100., 150., 200.],
               'pt': [0., 1., 2., 3., 4., 5., 7.5, 10., 15., 20., 30., 50., 100.]}


def sampling_probabilities(sample):
    """Keep each original's declared inclusion probability, including controls."""
    values = sample.get('probabilities')
    if values is None:
        values = [sample['probability']]*len(sample['event_ids'])
    if len(values) != len(sample['event_ids']):
        raise ValueError('Sampling probabilities differ from original identities')
    return [probability(value, 'original sampling probability') for value in values]


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

    Baseline inclusion r is independent uniform sampling within each source.
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
