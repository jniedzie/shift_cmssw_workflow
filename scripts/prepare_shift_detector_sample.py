#!/usr/bin/env python3
"""Freeze a weighted detector sample from an immutable GEN replay inventory.

Selection uses GEN weights alone.  Independent Bernoulli probabilities are
min(1, c * abs(native_weight)), with c fixed before the single sampling draw.
Native GEN and its trial denominators stay unchanged; selected observables use
native_weight / inclusion_probability.  This tool does not submit jobs.
"""
import argparse
from array import array
from collections import defaultdict
import gzip
import hashlib
import json
import math
from pathlib import Path
import shutil


HASH_SPACE = 1 << 256


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fit_scale(weights, target):
    """Solve sum(min(1,c*abs(w))) = min(target, number of nonzero weights)."""
    if type(target) is not int or target < 1:
        raise ValueError('Target events must be a positive integer')
    positive = sorted((abs(float(w)) for w in weights if w != 0), reverse=True)
    if any(not math.isfinite(w) for w in positive):
        raise ValueError('Native weights must be finite')
    if not positive:
        raise ValueError('A bin has no nonzero contribution to sample')
    desired = min(target, len(positive))
    if desired == len(positive):
        return max(1.0 / positive[-1], 1.0), desired
    # Build suffix totals from smallest to largest. Subtracting a saturated
    # dominant weight from the rounded grand total can erase the entire tail
    # (for example 1e20 + 1 + 1 + 1). Preserve each tail before adding it.
    suffix = array('d', [0.0]) * (len(positive) + 1)
    total, correction = 0.0, 0.0
    for index in range(len(positive) - 1, -1, -1):
        adjusted = positive[index] - correction
        updated = total + adjusted
        correction = (updated - total) - adjusted
        total = updated
        suffix[index] = total
    for saturated, weight in enumerate(positive):
        scale = (desired - saturated) / suffix[saturated]
        if scale * weight <= 1:
            return scale, desired
    raise ValueError('Cannot determine sampling scale')


def probability_threshold(probability):
    """Use every hash bit; a positive GEN contribution must retain support."""
    if not math.isfinite(probability) or not 0 <= probability <= 1:
        raise ValueError('Invalid inclusion probability')
    numerator, denominator = float(probability).as_integer_ratio()
    threshold = (numerator << 256) // denominator
    if probability > 0 and threshold == 0:
        raise ValueError('Positive contribution has no support in 256-bit selection')
    return threshold, threshold / HASH_SPACE


def inclusion_probability(weight, scale):
    probability = min(1.0, scale * abs(weight))
    if weight != 0 and probability == 0:
        raise ValueError('Positive absolute native weight lost floating-point probability support')
    return probability_threshold(probability)


def select_event(seed, stratum, gen_sha256, identity, raw_index, threshold):
    key = json.dumps([seed, stratum, gen_sha256, *identity, raw_index],
                     separators=(',', ':'), ensure_ascii=True).encode()
    draw = int.from_bytes(hashlib.sha256(key).digest(), 'big')
    return draw < threshold


def partition_jobs(sources, events_per_job):
    """Partition offsets in selected_indices; never interpret them as GEN offsets."""
    if type(events_per_job) is not int or events_per_job < 1:
        raise ValueError('Events per job must be positive')
    jobs = []
    for source in sources:
        for offset in range(0, source['events'], events_per_job):
            jobs.append((source['index'], offset,
                         min(events_per_job, source['events'] - offset), len(jobs)))
    return jobs


def _normalization_authorities(manifest):
    authorities = {}
    for kind in ('ordinary', 'weighted'):
        key = kind + '_manifest'
        if key not in manifest:
            continue
        path = Path(manifest[key])
        expected = manifest.get(key + '_sha256')
        if not expected or sha256(path) != expected:
            raise ValueError('Changed original normalization manifest: ' + str(path))
        authorities[kind] = dict(manifest=str(path), manifest_sha256=expected)
        if kind == 'weighted':
            accounting = path.parent / 'production_complete.json'
            record = json.loads(accounting.read_text())
            if not record.get('complete') or record.get('manifest_sha256') != expected:
                raise ValueError('Weighted GEN fixed-trial accounting is incomplete')
            authorities[kind].update(accounting=str(accounting),
                                     accounting_sha256=sha256(accounting),
                                     strata=record['strata'])
    return authorities


def prepare(input_campaign, output, eos_output, target_events, events_per_job, seed):
    input_campaign, output = Path(input_campaign).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError('Refusing an existing output campaign')
    if not seed or not eos_output.startswith('/eos/') or '\n' in eos_output:
        raise ValueError('An explicit sampling seed and absolute EOS output are required')
    if type(events_per_job) is not int or events_per_job < 1:
        raise ValueError('Events per job must be positive')
    manifest_path = input_campaign / 'manifest.json'
    manifest_bytes = manifest_path.read_bytes()
    parent_sha = hashlib.sha256(manifest_bytes).hexdigest()
    manifest = json.loads(manifest_bytes)
    if manifest.get('detector_sampling'):
        raise ValueError('Input must be the full GEN inventory, not an earlier detector sample')
    authorities = _normalization_authorities(manifest)
    cache, grouped, parent_records = [], defaultdict(list), []
    indices = set()
    # Read each large descriptor once: retain it for selection and publication.
    for summary in sorted(manifest['sources'], key=lambda row: row['index']):
        index = summary['index']
        if type(index) is not int or index < 0 or index in indices:
            raise ValueError('Duplicate or invalid source index')
        indices.add(index)
        path = input_campaign / 'sources' / f'source{index:05d}.json'
        source_bytes = path.read_bytes()
        source_sha = hashlib.sha256(source_bytes).hexdigest()
        descriptor = json.loads(source_bytes)
        identities = descriptor['event_ids']
        if (descriptor.get('selected_indices') is not None or
                descriptor['stratum'] != summary['stratum'] or
                descriptor['gen'] != summary['gen'] or len(identities) != summary['events']):
            raise ValueError('Descriptor differs from full-inventory source summary')
        weights = descriptor.get('weights')
        if weights is None:
            weights = [1.0] * len(identities)
        if len(weights) != len(identities) or any(not math.isfinite(w) for w in weights):
            raise ValueError('Missing, mismatched or nonfinite native weights')
        item = dict(index=index, descriptor=descriptor, weights=weights,
                    parent_descriptor_sha256=source_sha)
        cache.append(item)
        grouped[descriptor['stratum']].append(item)
        parent_records.append(dict(index=index, stratum=descriptor['stratum'],
            descriptor=str(path), descriptor_sha256=source_sha,
            gen=descriptor['gen'], gen_sha256=descriptor['gen_sha256'],
            events=len(identities), receipt=descriptor['receipt'],
            receipt_sha256=descriptor['receipt_sha256'],
            normalization_record=descriptor.get('normalization_record')))
    if set(grouped) != set(manifest['strata']):
        raise ValueError('Full inventory does not cover every declared bin')

    statistics = {}
    chosen_ids = set()
    selected_sources = []
    for stratum, items in grouped.items():
        weights = [weight for item in items for weight in item['weights']]
        if len(weights) != manifest['strata'][stratum]:
            raise ValueError('Full-inventory event count differs for ' + stratum)
        scale, expected_target = fit_scale(weights, target_events)
        sumw, sumw2 = math.fsum(weights), math.fsum(w * w for w in weights)
        expected_terms, expected_w2 = [], []
        selected_native, corrected, selected_variance = [], [], []
        saturated, zero, minimum_probability = 0, 0, 1.0
        for item in items:
            descriptor = item['descriptor']
            selected, probabilities = [], []
            chunk_expected, chunk_expected_w2 = [], []
            for raw_index, (identity, weight) in enumerate(zip(descriptor['event_ids'], item['weights'])):
                threshold, probability = inclusion_probability(weight, scale)
                if weight == 0:
                    zero += 1
                    continue
                if len(identity) != 3 or any(type(value) is not int or value < 0 for value in identity):
                    raise ValueError('Invalid parent event identity')
                minimum_probability = min(minimum_probability, probability)
                saturated += probability == 1
                chunk_expected.append(probability)
                chunk_expected_w2.append(weight * weight / probability)
                if not select_event(seed, stratum, descriptor['gen_sha256'], identity, raw_index, threshold):
                    continue
                key = tuple(identity)
                if key in chosen_ids:
                    raise ValueError('Duplicate selected event identity across GEN sources')
                chosen_ids.add(key)
                selected.append(raw_index)
                probabilities.append(probability)
                adjusted = weight / probability
                selected_native.append(weight)
                corrected.append(adjusted)
                selected_variance.append(adjusted * adjusted * (1 - probability))
            item['selected_indices'], item['sampling_probabilities'] = selected, probabilities
            expected_terms.append(math.fsum(chunk_expected))
            expected_w2.append(math.fsum(chunk_expected_w2))
            if selected:
                selected_sources.append(dict(index=item['index'], gen=descriptor['gen'],
                                             events=len(selected), stratum=stratum))
        expected_count = math.fsum(expected_terms)
        if not math.isclose(expected_count, expected_target, rel_tol=1e-10, abs_tol=1e-8):
            raise ValueError('Probability scale does not meet the frozen expected count')
        predicted_w2 = math.fsum(expected_w2)
        realized_sumw = math.fsum(corrected)
        realized_sumw2 = math.fsum(w * w for w in corrected)
        statistics[stratum] = dict(original_events=len(weights), original_sumw=sumw,
            original_sumw2=sumw2, original_ess=sumw * sumw / sumw2 if sumw2 else 0,
            requested_expected_events=target_events, scale=scale, expected_events=expected_count,
            saturated_probability_one=saturated, minimum_positive_probability=minimum_probability,
            zero_weight_events_omitted=zero, realized_events=len(corrected),
            selected_native_sumw=math.fsum(selected_native), corrected_sumw=realized_sumw,
            corrected_sumw2=realized_sumw2,
            realized_ess=realized_sumw * realized_sumw / realized_sumw2 if realized_sumw2 else 0,
            expected_corrected_sumw2=predicted_w2,
            prospective_ess_proxy=sumw * sumw / predicted_w2 if predicted_w2 else 0,
            expected_thinning_variance_sumw=max(0., predicted_w2 - sumw2),
            realized_thinning_variance_estimator_sumw=math.fsum(selected_variance),
            original_normalization_records=[r for r in parent_records if r['stratum'] == stratum])
        if not corrected:
            raise ValueError('Frozen sampling draw left a bin empty: ' + stratum)

    selected_sources.sort(key=lambda row: row['index'])
    jobs = partition_jobs(selected_sources, events_per_job)
    canaries = []
    for stratum in manifest['strata']:
        candidates = [row for row in selected_sources if row['stratum'] == stratum and row['events'] >= 3]
        if not candidates:
            raise ValueError('No source supports a two-event check after its first selected event in ' + stratum)
        candidate = max(candidates, key=lambda row: row['events'])
        canaries.append(dict(source=candidate['index'], skip=1, count=2,
                             job=800000 + candidate['index'], stratum=stratum))
    templates = {}
    for stage in range(1, 5):
        path = input_campaign / 'templates' / f'step{stage}.py'
        if sha256(path) != manifest['templates'][str(stage)]['sha256']:
            raise ValueError('Frozen detector recipe changed')
        templates[stage] = path
    if sha256(manifest_path) != parent_sha:
        raise ValueError('Input inventory manifest changed during preparation')

    plan = dict(schema='shift-detector-pps-sample-v1', parent_campaign=str(input_campaign),
        parent_manifest=str(manifest_path), parent_manifest_sha256=parent_sha,
        preparer_source_sha256=sha256(Path(__file__)),
        seed=seed, algorithm='independent-bernoulli-pps-absolute-native-weight-sha256-v1',
        probability='min(1,c*abs(native_GEN_weight)); c fixed before the single draw',
        hash_support_bits=256, no_topups=True, target_expected_events_per_bin=target_events,
        expected_events=math.fsum(s['expected_events'] for s in statistics.values()),
        selected_events=len(chosen_ids), planned_jobs=len(jobs), events_per_job=events_per_job,
        strata=statistics, normalization_authorities=authorities,
        analysis_weight='native_GEN_weight / sampling_probability; original parent denominators unchanged',
        zero_weight_policy='Omit exact zero native weights: their contribution to every weighted observable is zero.',
        uncertainty='Bernoulli thinning: sum_selected((W/p)^2*f^2*(1-p)); report realized ESS.',
        prospective_ess_is_planning_proxy=True, physics_valid=False, normalization_ready=False)
    new_manifest = dict(manifest)
    if 'approval_basis' in new_manifest:
        new_manifest['parent_approval_basis'] = new_manifest.pop('approval_basis')
    for key in list(new_manifest):
        if key.startswith('pilot_') or key in ('acceleration', 'reused_validation'):
            new_manifest.pop(key)
    new_manifest.update(eos_output=eos_output, events_per_job=events_per_job,
        sources=selected_sources, jobs=len(jobs), events=len(chosen_ids),
        strata={s: row['realized_events'] for s, row in statistics.items()},
        source_chunks={s: sum(row['stratum'] == s for row in selected_sources) for s in statistics},
        canary_passed=False, physics_valid=False, normalization_ready=False,
        detector_sampling=dict(schema=plan['schema'], plan='sampling_plan.json',
            seed=seed, parent_manifest_sha256=parent_sha, ledger='sampling_events.jsonl.gz'),
        preparation_basis='A single GEN-weight-only representative draw from the frozen parent inventory; canonical GEN, parent normalization and the archived detector recipe are preserved. Preparation does not establish child campaign authorization.')

    output.mkdir(parents=True)
    (output / 'sources').mkdir()
    (output / 'templates').mkdir()
    for stage, path in templates.items():
        shutil.copy2(path, output / 'templates' / f'step{stage}.py')
    with (output / 'sampling_events.jsonl.gz').open('wb') as raw:
        with gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0) as ledger:
            for item in cache:
                selected = item['selected_indices']
                if not selected:
                    continue
                descriptor = item['descriptor']
                for index, probability in zip(selected, item['sampling_probabilities']):
                    weight = item['weights'][index]
                    row = dict(source=item['index'], raw_index=index,
                        event_id=descriptor['event_ids'][index], stratum=descriptor['stratum'],
                        sampling_probability=probability, native_weight=weight,
                        corrected_weight=weight / probability, inverse_sampling_probability=1 / probability)
                    ledger.write((json.dumps(row, separators=(',', ':')) + '\n').encode())
    plan['sampling_ledger'] = dict(path='sampling_events.jsonl.gz',
                                  sha256=sha256(output / 'sampling_events.jsonl.gz'))
    (output / 'sampling_plan.json').write_text(json.dumps(plan, indent=2, allow_nan=False) + '\n')
    plan_sha = sha256(output / 'sampling_plan.json')
    new_manifest['detector_sampling']['plan_sha256'] = plan_sha
    for item in cache:
        if not item['selected_indices']:
            continue
        descriptor = dict(item['descriptor'])
        descriptor.update(output_base=eos_output + '/' + descriptor['stratum'],
            selected_indices=item['selected_indices'],
            sampling_probabilities=item['sampling_probabilities'],
            sampling=dict(schema=plan['schema'], seed=seed, plan_sha256=plan_sha,
                parent_manifest_sha256=parent_sha,
                parent_descriptor_sha256=item['parent_descriptor_sha256']))
        (output / 'sources' / f'source{item["index"]:05d}.json').write_text(json.dumps(descriptor, indent=2) + '\n')
    (output / 'manifest.json').write_text(json.dumps(new_manifest, indent=2, allow_nan=False) + '\n')
    (output / 'jobs.txt').write_text(''.join(f'{index:05d} {offset} {count} {job}\n' for index, offset, count, job in jobs))
    (output / 'canaries.json').write_text(json.dumps(canaries, indent=2) + '\n')
    (output / 'README.txt').write_text(
        'Weighted representative detector sample. The single GEN-weight-only draw is frozen in sampling_plan.json.\n'
        'Parent GEN, event IDs, native weights, physics configuration and normalization denominators are preserved.\n'
        'Job skip/count address selected_indices, not raw GEN entries. Use native_weight / sampling_probability in analysis.\n'
        'The full selected-event ledger is sampling_events.jsonl.gz. No adaptive top-ups are permitted by this plan.\n'
        'physics_valid=false and normalization_ready=false. This preparer does not submit jobs.\n')
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-campaign', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--eos-output', required=True)
    parser.add_argument('--target-events', type=int, default=10000)
    parser.add_argument('--events-per-job', type=int, default=10)
    parser.add_argument('--seed', required=True)
    args = parser.parse_args()
    plan = prepare(args.input_campaign, args.output, args.eos_output,
                   args.target_events, args.events_per_job, args.seed)
    print(json.dumps({k: plan[k] for k in ('schema', 'expected_events', 'selected_events', 'planned_jobs', 'seed')}))


if __name__ == '__main__':
    main()
