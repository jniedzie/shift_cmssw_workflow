#!/usr/bin/env python3
# Code style: AI-code. AI-generated measured GEN-to-dimuon response summaries.
"""Describe measured response; do not deploy cuts or change existing weights.

Raw event probabilities and weighted pair information are different quantities.
The intervals below describe raw Monte Carlo event successes. Source proposals,
incomplete cohorts and provisional normalization limit their interpretation.
"""
import argparse
from bisect import bisect_right
from collections import defaultdict
import gzip
import json
import math
from pathlib import Path

from shift_sm_response_labels import CATEGORIES, TAILS


FEATURE_AXES = {
    'best_pair_miss_cm': [0., 50., 100., 500., 1000., 5000., 10000.],
    'best_pair_min_energy_GeV': [0., 2., 5., 10., 20., 50., 100., 500., 1000.],
    'max_pair_min_energy_GeV': [0., 2., 5., 10., 20., 50., 100., 500., 1000.],
    'min_parent_prefix_depth': [0., .1, 1., 3., 10., 30., 100.],
    'best_pair_max_column_loss_over_p': [0., .1, .3, 1., 3., 10., 100.],
    'min_pair_max_column_loss_over_p': [0., .1, .3, 1., 3., 10., 100.],
    'best_pair_birth_z_min_cm': [-20000., -15000., -1100., 0., 1100., 15000., 20000.],
}
MAP_CATEGORIES = ('inclusive', 'both_both', 'both_both_good')


def wilson_interval(passed, count, z=1.959963984540054):
    """Raw-event binomial interval, never a weighted absolute-rate interval."""
    if not 0 <= passed <= count:
        raise ValueError('Invalid independent-event counts')
    if count == 0:
        return [None, None]
    fraction = passed/count
    denominator = 1.+z*z/count
    center = (fraction+z*z/(2*count))/denominator
    half = z*math.sqrt(fraction*(1-fraction)/count+z*z/(4*count*count))/denominator
    return [max(0., center-half), min(1., center+half)]


def target_value(row, category, tail=None):
    if tail is None:
        return row['labels']['pair_counts'][category]
    return row['labels']['tail_counts'][category][tail]


def response_statistics(rows, category, tail=None):
    """Group several pairs from one event before computing second moments."""
    values = [target_value(row, category, tail) for row in rows]
    weights = []
    for row in rows:
        probability = row['sampling_probability']
        native = row['native_weight']
        if not 0 < probability <= 1 or not math.isfinite(native):
            raise ValueError('Invalid frozen sampling probability or native weight')
        weights.append(native/probability)
    passed = sum(value > 0 for value in values)
    sumw = math.fsum(weights)
    sumw2 = math.fsum(weight*weight for weight in weights)
    contributions = [weight*value for weight, value in zip(weights, values)]
    total = math.fsum(contributions)
    second = math.fsum(value*value for value in contributions)
    positives = math.fsum(weight for weight, value in zip(weights, values) if value > 0)
    return dict(events=len(rows), positive_originals=passed, pairs=sum(values),
        raw_event_probability=passed/len(rows) if rows else None,
        raw_event_probability_interval95=wilson_interval(passed, len(rows)),
        zero_observation_upper95_raw=(1.-.05**(1./len(rows))) if rows and not passed else None,
        sum_native_over_probability=sumw, sum_squared_native_over_probability=sumw2,
        weighted_conditional_event_probability=positives/sumw if sumw else None,
        weighted_pair_sum=total, event_level_pair_second_moment=second,
        effective_pair_contributions=total*total/second if second else 0.,
        weighted_denominator_effective_events=sumw*sumw/sumw2 if sumw2 else 0.,
        zero_observation_is_zero_physical_rate=False)


def feature_bin(value, axis):
    if value is None:
        return 'unknown'
    if not math.isfinite(value):
        raise ValueError('Nonfinite feature; unavailable values must be explicit null')
    index = bisect_right(axis, value)
    if index == 0:
        return 'underflow'
    if index == len(axis):
        return 'overflow'
    return str(index-1)


def candidate_regions(features):
    """Predeclared descriptive regions, not production vetoes or probabilities."""
    miss = features['best_pair_miss_cm']
    no_os = features['n_os_pairs'] == 0
    no_both_forward_pair = features.get('n_both_forward_os_pairs', 0) == 0
    poor_pointing = no_os or no_both_forward_pair or (miss is not None and miss > 500.)
    deep_parent = features['all_incoming_pairs_parent_depth_ge_10']
    return dict(no_gen_os_pair=no_os, poor_both_endcap_pointing_5m=poor_pointing,
                all_incoming_pairs_deep_parent_prefix=deep_parent,
                poor_pointing_or_deep_parent=poor_pointing or deep_parent)


def sampling_forecast(rows, category, region, floor, tail=None):
    """Counterfactual planning comparison conditional on observed outcomes.

    A demoted positive event increases the weight second moment by 1/p_new.
    Counting only saved event slots would miss that statistical penalty. Stage
    costs are wall time and may be equal-share proxies for multi-event jobs.
    This calculation neither draws events nor changes an existing probability.
    """
    if not 0 < floor <= 1:
        raise ValueError('A candidate needs positive control support')
    base_cost, new_cost, base_second, new_second = [], [], [], []
    base_events, new_events = [], []
    positive = demoted_positive = 0
    allocated = 0
    for row in rows:
        old = row['sampling_probability']
        native = row['native_weight']
        cost = row['cost']['sim_wall_seconds_per_event']
        if not 0 < old <= 1 or not math.isfinite(cost) or cost < 0:
            raise ValueError('Invalid response probability or wall cost')
        low = candidate_regions(row['features'])[region]
        proposal = floor if low else 1.
        count = target_value(row, category, tail)
        positive += count > 0
        demoted_positive += low and count > 0
        allocated += row['cost']['cost_kind'] != 'single_event_actual'
        base_cost.append(cost/old)
        new_cost.append(cost*proposal/old)
        base_second.append(native*native*count*count/old)
        new_second.append(native*native*count*count/(old*proposal))
        base_events.append(1./old)
        new_events.append(proposal/old)
    reference = math.fsum(base_cost)*math.fsum(base_second)
    proposed = math.fsum(new_cost)*math.fsum(new_second)
    return dict(control_floor=floor, low_probability_region=region,
        raw_positive_originals=positive, demoted_positive_originals=demoted_positive,
        expected_event_count_ratio=math.fsum(new_events)/math.fsum(base_events) if rows else None,
        estimated_SIM_wall_ratio=math.fsum(new_cost)/math.fsum(base_cost) if math.fsum(base_cost) else None,
        pair_second_moment_ratio=math.fsum(new_second)/math.fsum(base_second) if math.fsum(base_second) else None,
        conditional_information_per_SIM_wall_gain=reference/proposed if proposed else None,
        allocated_multi_event_cost_rows=allocated,
        gain_uncertainty_not_qualified=True, production_policy_qualified=False,
        zero_tail_outcomes_cannot_qualify_gain=positive == 0)


def summarize(rows):
    keys = [(row['gen_sha256'], tuple(row['event_id'])) for row in rows]
    if len(keys) != len(set(keys)):
        raise ValueError('Overlapping Nano snapshots or repeated original events')
    groups = defaultdict(list)
    for row in rows:
        if row['study_split'] not in ('development', 'validation'):
            raise ValueError('An untouched source split must precede response tuning')
        groups[(row['stratum'], row['study_split'])].append(row)
    summaries = []
    for (stratum, split), sample in sorted(groups.items()):
        totals = {category: response_statistics(sample, category) for category in CATEGORIES}
        tails = {category: {tail: response_statistics(sample, category, tail) for tail in TAILS}
                 for category in MAP_CATEGORIES}
        maps = {}
        for feature, axis in FEATURE_AXES.items():
            cells = defaultdict(list)
            for row in sample:
                cells[feature_bin(row['features'][feature], axis)].append(row)
            maps[feature] = dict(edges=axis, cells={key: {
                category: response_statistics(cell, category) for category in MAP_CATEGORIES}
                for key, cell in cells.items()})
        regions = {}
        for region in candidate_regions(sample[0]['features']):
            rejected = [row for row in sample if candidate_regions(row['features'])[region]]
            retained = [row for row in sample if not candidate_regions(row['features'])[region]]
            regions[region] = dict(rejected_events=len(rejected), retained_events=len(retained),
                rejected={category: response_statistics(rejected, category) for category in MAP_CATEGORIES},
                retained={category: response_statistics(retained, category) for category in MAP_CATEGORIES},
                hard_veto_qualified=False)
        forecasts = {}
        if all('cost' in row for row in sample):
            for region in regions:
                forecasts[region] = {category: [sampling_forecast(sample, category, region, floor)
                    for floor in (.1, .01, .001)] for category in MAP_CATEGORIES}
        summaries.append(dict(stratum=stratum, study_split=split, totals=totals,
                              tails=tails, efficiency_maps=maps, candidate_regions=regions,
                              counterfactual_sampling=forecasts))
    return dict(schema='shift-sm-measured-response-summary-v1', events=len(rows), groups=summaries,
        original_event_is_error_unit=True, sampling_correction_applied_once=True,
        raw_intervals_are_not_weighted_rate_intervals=True,
        complete_cohort_conditioning_may_be_informative=True,
        physics_normalization_ready=False, production_policy_qualified=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('joined_jsonl', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    opener = gzip.open if args.joined_jsonl.suffix == '.gz' else open
    with opener(args.joined_jsonl, 'rt') as stream:
        rows = [json.loads(line) for line in stream if line.strip()]
    result = summarize(rows)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False)+'\n')


if __name__ == '__main__':
    main()
