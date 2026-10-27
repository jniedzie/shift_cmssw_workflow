#!/usr/bin/env python3
# Code style: AI-code. Compare paired numerical-runtime controls without rate claims.
"""Read completed, identity-bound Nano controls and report every branch difference."""
import argparse
import hashlib
import json
import math
from pathlib import Path

from run_shift_sm_postprocessing import download
from shift_sm_response_labels import read_nano


def read(path):
    return json.loads(Path(path).read_text())


def digest(path):
    checksum = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            checksum.update(block)
    return checksum.hexdigest()


def equal(left, right):
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(equal(a, b) for a, b in zip(left, right))
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(equal(left[k], right[k]) for k in left)
    if isinstance(left, float) and isinstance(right, float) and math.isnan(left) and math.isnan(right):
        return True
    return left == right


def load_worker(pin, expected, target):
    directory = Path(pin['directory'])
    if digest(directory/'request.json') != pin['request_sha256']:
        raise ValueError('Paired worker request changed')
    request = read(directory/'request.json')
    source = request['detector_source']
    index = source['selected_indices'][request['selected_skip']]
    if (request['selected_count'] != 1 or source['event_ids'][index] != expected['event_id']
            or source['normalization_record']['gen_sha256'] != expected['gen_sha256']):
        raise ValueError('Paired worker does not cover the exact original')
    result, status = read(directory/'result.json'), read(directory/'status.json')
    if (result.get('complete') is not True or result.get('exit_code') != 0
            or status.get('complete') is not True or status.get('exit_code') != 0
            or result.get('request_sha256') != pin['request_sha256']
            or result['nano_sha256'] != result['nano_readback_sha256']):
        raise ValueError('Missing or failed control is not an empty physics response')
    download(result['nano_path'], target, result['nano_sha256'])
    labels = read_nano(target, [expected], result['nano_sha256'])
    return request, result, labels[0]


def run(request_path):
    import awkward as ak
    import uproot
    request = read(request_path)
    cases = []
    for index, case in enumerate(request['cases']):
        new_path = Path(f'new_{index}.root')
        new_request, result, label = load_worker(case['new'], case['expected'], new_path)
        row = dict(name=case['name'], new_result_sha256=digest(Path(case['new']['directory'])/'result.json'),
            event_id=case['expected']['event_id'], pair_counts=label['pair_counts'], differences=[],
            new_native_weight=label['native_weight'], new_sampling_probability=label['sampling_probability'])
        if case.get('old'):
            old_path = Path(f'old_{index}.root')
            old_request, old_result, old_label = load_worker(case['old'], case['expected'], old_path)
            # Output paths differ; scientific descriptor, input offset and RNG do not.
            for key in ('detector_job', 'selected_skip', 'selected_count'):
                if old_request[key] != new_request[key]:
                    raise ValueError('Paired input or random seed differs')
            left, right = dict(old_request['detector_source']), dict(new_request['detector_source'])
            for key in ('nano_base', 'metadata_base'):
                left.pop(key, None); right.pop(key, None)
            if left != right:
                raise ValueError('Paired source, sampling or normalization changed')
            with uproot.open(old_path, handler=uproot.source.file.MemmapSource) as old_file, \
                    uproot.open(new_path, handler=uproot.source.file.MemmapSource) as new_file:
                before, after = old_file['Events'], new_file['Events']
                if set(before.keys()) != set(after.keys()):
                    raise ValueError('Paired Nano branch sets differ')
                for branch in before.keys():
                    a, b = ak.to_list(before[branch].array()), ak.to_list(after[branch].array())
                    if not equal(a, b):
                        row['differences'].append(dict(branch=branch, old=repr(a), new=repr(b)))
                row['branches_compared'] = len(before.keys())
            row['old_pair_counts'] = old_label['pair_counts']
            row['all_Nano_event_branches_equal'] = not row['differences']
            old_path.unlink()
        else:
            row['original_failed_converter_reference'] = case['original_failure']
        new_path.unlink()
        cases.append(row)
    controls = [row for row in cases if 'all_Nano_event_branches_equal' in row]
    positive = any(row['pair_counts']['both_both'] for row in controls)
    report = dict(complete=True, request_sha256=digest(request_path), cases=cases,
        all_controls_equal=all(row['all_Nano_event_branches_equal'] for row in controls),
        reconstructed_BothBoth_control_seen=positive, no_new_production_exposure=True,
        ordinary_campaign_publication=False, runtime_migration_qualified=False,
        review_required=True)
    # A raw difference is never silently waived, including unused Rivet fields.
    Path('report.json').write_text(json.dumps(report, indent=2, allow_nan=False)+'\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('request', type=Path)
    run(parser.parse_args().request)
