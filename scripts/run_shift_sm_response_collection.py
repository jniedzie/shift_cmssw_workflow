#!/usr/bin/env python3
# Code style: AI-code. AI-generated collection of a frozen diagnostic SM benchmark.
"""Check actual Nano outcomes and keep benchmark q separate from original p.

These outputs are response-study evidence. They are not ordinary campaign
histograms, a new GEN exposure, or proof of physical tail-rate normalization.
"""
import argparse
from collections import defaultdict
import copy
import gzip
import hashlib
import json
import math
from pathlib import Path

from run_shift_sm_postprocessing import download, publish
from shift_sm_response_labels import read_nano
from summarize_shift_sm_response import response_statistics, summarize


def sha(path):
    checksum = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1048576), b''):
            checksum.update(block)
    return checksum.hexdigest()


def read(path):
    with Path(path).open('rb') as source:
        return json.loads(b''.join(iter(lambda: source.read(1048576), b'')))


def read_pin(pin):
    if sha(pin['path']) != pin['sha256']:
        raise ValueError('Frozen study input changed: '+pin['path'])
    return read(pin['path'])


def identity(row):
    return row['gen_sha256'], tuple(row['event_id'])


def verify_worker_request(request, selected):
    """Check the original selected offset, not a newly drawn production mask."""
    source = request['detector_source']
    position = selected['selected_position']
    index = selected['original_index']
    if (request['selected_skip'] != position or request['selected_count'] != 1
            or source['selected_indices'][position] != index
            or source['event_ids'][index] != selected['event_id']
            or source['weights'][index] != selected['native_weight']
            or source['sampling_probabilities'][position] != selected['existing_sampling_probability']
            or source['gen_sha256'] != selected['prepared_gen_sha256']
            or source['normalization_record']['gen_sha256'] != selected['gen_sha256']):
        raise ValueError('Worker changed an original event, weight or probability')
    if not source['nano_base'].startswith('/eos/user/j/jniedzie/shift_cmssw_support/'):
        raise ValueError('Diagnostic q must not leak into ordinary campaign publication')


def study_row(label, selected, result):
    """Native Nano W/p stays unchanged; only this analysis additionally uses q."""
    if (identity(label) != identity(selected)
            or label['native_weight'] != selected['native_weight']
            or label['sampling_probability'] != selected['existing_sampling_probability']):
        raise ValueError('Actual Nano identity or original W/p differs from the frozen draw')
    q = selected['benchmark_inclusion_probability']
    if not math.isfinite(q) or not 0 < q <= 1:
        raise ValueError('The independent benchmark probability must be positive')
    if (result['events'] != 1
            or result['nano']['event_ids'] != [selected['event_id']]
            or result['source_indices'] != [selected['original_index']]
            or result['sampling_probabilities'] != [selected['existing_sampling_probability']]):
        raise ValueError('Detector result does not cover exactly the selected original')
    old = selected['existing_sampling_probability']
    return dict(gen_sha256=selected['gen_sha256'], event_id=selected['event_id'],
        source_id=selected['source_id'], stratum=selected['stratum'],
        native_weight=selected['native_weight'], original_sampling_probability=old,
        benchmark_sampling_probability=q, sampling_probability=old*q,
        benchmark_region=selected['benchmark_region'],
        study_split='validation', prospective_holdout=True,
        original_source_fold=selected.get('source_fold'),
        original_source_split=selected.get('study_split'),
        features=copy.deepcopy(selected['features']), labels=copy.deepcopy(label),
        cost=dict(sim_wall_seconds_per_event=result['stages']['1']['seconds'],
                  payload_wall_seconds_per_event=result['wall_seconds'],
                  cost_kind='single_event_actual'))


def verify_exposure(exposure, plan):
    sources = exposure['sources']
    keys = [row['original_raw_gen_sha256'] for row in sources]
    if (len(keys) != len(set(keys))
            or sum(row['old_selected_events'] for row in sources) != plan['population_events']
            or not exposure.get('includes_sources_with_no_benchmark_event_drawn')):
        raise ValueError('Require full original population exposure, including undrawn sources')
    if sum(row['original_saved_events'] for row in sources) != exposure['original_saved_GEN_events']:
        raise ValueError('Original saved GEN denominator changed')


def run(request_path):
    request = read(request_path)
    stage = request['stage']
    if (request.get('schema') != 'shift-stream-payload-v1'
            or stage not in ('sm_response_canary', 'sm_response_collect')):
        raise ValueError('Unsupported diagnostic collection request')
    plan = read_pin(request['benchmark_plan'])
    exposure = read_pin(request['full_population_exposure'])
    verify_exposure(exposure, plan)
    selected = {identity(row): row for row in plan['selected']}
    if len(selected) != plan['selected_events']:
        raise ValueError('Duplicate original in frozen benchmark')
    rows, producers, seen = [], [], set()
    for number, record in enumerate(request['job_records']):
        original = record['selected']
        key = identity(original)
        if key not in selected or selected[key] != original or key in seen:
            raise ValueError('Unexpected or repeated benchmark original')
        seen.add(key)
        directory = Path(record['job_directory'])
        producer_request = read_pin(dict(path=str(directory/'request.json'), sha256=record['request_sha256']))
        verify_worker_request(producer_request, original)
        result, status = read(directory/'result.json'), read(directory/'status.json')
        if (result.get('complete') is not True or result.get('exit_code') != 0
                or status.get('complete') is not True or status.get('exit_code') != 0
                or result.get('request_sha256') != record['request_sha256']):
            raise ValueError('Missing/failed response is not a physics zero: '+str(directory))
        if result['nano_sha256'] != result.get('nano_readback_sha256'):
            raise ValueError('Missing independent producer Nano readback')
        expected = [dict(gen_sha256=original['gen_sha256'], event_id=original['event_id'],
            native_weight=original['native_weight'],
            sampling_probability=original['existing_sampling_probability'], source_id=original['source_id'])]
        local = Path(f'nano_{number:04d}.root')
        download(result['nano_path'], local, result['nano_sha256'])
        labels = read_nano(local, expected, expected_sha256=result['nano_sha256'])
        local.unlink()
        if len(labels) != 1:
            raise ValueError('The diagnostic worker must contain exactly one original')
        rows.append(study_row(labels[0], original, result))
        producers.append(dict(directory=str(directory), request_sha256=record['request_sha256'],
            result_sha256=sha(directory/'result.json'), nano_path=result['nano_path'],
            nano_sha256=result['nano_sha256']))
    if stage == 'sm_response_collect' and seen != set(selected):
        raise ValueError('Final response collector lacks frozen benchmark events')
    reference_result = None
    if stage == 'sm_response_canary':
        reference = request['positive_reference']
        local = Path('positive_reference.root')
        download(reference['nano_path'], local, reference['nano_sha256'])
        reference_rows = read_nano(local, reference['expected_events'], reference['nano_sha256'])
        local.unlink()
        positive = sum(row['pair_counts']['both_both'] for row in reference_rows)
        if not positive:
            raise ValueError('Actual positive Both-Both analysis reference did not pass')
        reference_result = dict(events=len(reference_rows), both_both_pairs=positive,
            nano_sha256=reference['nano_sha256'], reference_only_not_new_exposure=True)
    groups = defaultdict(list)
    for row in rows:
        groups[(row['stratum'], row['benchmark_region'])].append(row)
    cells = [dict(stratum=key[0], benchmark_region=key[1],
        both_both=response_statistics(sample, 'both_both'),
        both_both_good=response_statistics(sample, 'both_both_good')) for key, sample in sorted(groups.items())]
    summary = summarize(rows)
    summary.update(benchmark_plan_sha256=request['benchmark_plan']['sha256'],
        frozen_case_control_sample=True, raw_intervals_describe_case_control_sample=True,
        original_full_population_exposure=exposure, region_response=cells,
        complete_frozen_draw_collected=stage == 'sm_response_collect',
        old_native_Nano_probabilities_changed=False, ordinary_campaign_publication_qualified=False)
    table = Path('response_rows.jsonl.gz')
    with gzip.open(table, 'wt') as output:
        for row in rows:
            output.write(json.dumps(row, separators=(',', ':'), allow_nan=False)+'\n')
    summary_path = Path('response_summary.json.gz')
    with gzip.open(summary_path, 'wt') as output:
        json.dump(summary, output, separators=(',', ':'), allow_nan=False)
    if table.stat().st_size+summary_path.stat().st_size > request['maximum_publication_bytes']:
        raise ValueError('Diagnostic evidence exceeds its frozen storage reservation')
    publications = {name: publish(path, request['output_directory'])
                    for name, path in [('joined_rows', table), ('summary', summary_path)]}
    result = dict(schema='shift-sm-prospective-response-collection-v1', complete=True,
        request_sha256=sha(request_path), benchmark_plan_sha256=request['benchmark_plan']['sha256'],
        events=len(rows), publications=publications, producer_receipts=producers,
        known_positive_reference=reference_result, original_full_population_sources=len(exposure['sources']),
        complete_frozen_draw_collected=stage == 'sm_response_collect',
        physics_normalization_ready=False, production_policy_qualified=False,
        original_weights_and_p_unchanged=True, benchmark_q_applied_once_in_analysis=True)
    Path('report.json').write_text(json.dumps(result, indent=2)+'\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('request', type=Path)
    args = parser.parse_args()
    try:
        run(args.request)
    except Exception as error:
        Path('report.json').write_text(json.dumps(dict(complete=False, error=repr(error)))+'\n')
        raise


if __name__ == '__main__':
    main()
