#!/usr/bin/env python3
# Code style: AI-code.
"""Qualify isolated detector replays against their exact completed originals."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import tempfile
import time

from shift_condor_native import query, run_condor
from run_shift_sm_postprocessing import download


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def checked(pin):
    if sha(pin['path']) != pin['sha256']:
        raise ValueError('Replay authority changed: ' + pin['path'])
    return read(pin['path'])


def same_physics(original, replay):
    """Permit an output namespace and cohort label, keeping the actual draw."""
    reference = copy.deepcopy(replay)
    reference['cohort_id'] = original['cohort_id']
    for key in ('nano_base', 'metadata_base'):
        reference['detector_source'][key] = original['detector_source'][key]
        destination = Path(replay['detector_source'][key])
        if (not str(destination).startswith('/eos/user/j/jniedzie/shift_cmssw_support/')
                or '..' in destination.parts or destination == Path(original['detector_source'][key])):
            raise ValueError('Control outputs need a separate authorized namespace')
    if reference != original:
        raise ValueError('Control replay changed inputs, seeds, weights or sampling')


def terminal(control, endpoint=None):
    """Wait only for this exact native attempt, never adopt a stale failure."""
    cluster = control['cluster_id']
    directory = str(Path(control['replay_request']['path']).parent)
    location = [] if endpoint is None else ['-name', endpoint['schedd'], '-pool', endpoint['pool']]
    if location:
        text = run_condor(['/usr/bin/condor_q', *location, str(cluster), '-json',
                          '-attributes', 'ClusterId,ProcId,Iwd,JobStatus'], timeout=45)
        ads = json.loads(text) if text.strip() else []
    else:
        ads = query('ClusterId == ' + str(cluster),
                    ['ClusterId', 'ProcId', 'Iwd', 'JobStatus'], timeout=45)
    if ads:
        if len(ads) != 1 or ads[0]['Iwd'] != directory or ads[0]['ProcId'] != 0:
            raise ValueError('Control native job identity changed')
        if ads[0]['JobStatus'] == 5:
            raise ValueError('Control is held; preserve failure instead of admitting production')
        return None
    rows = json.loads(run_condor(['/usr/bin/condor_history', *location, str(cluster), '-limit', '1',
        '-json', '-attributes', 'ClusterId,ProcId,Iwd,ExitCode,ExitBySignal'], timeout=45))
    if (len(rows) != 1 or rows[0].get('Iwd') != directory or rows[0].get('ExitCode') != 0
            or rows[0].get('ExitBySignal')):
        raise ValueError('Control lacks successful native terminal evidence')
    log = (Path(directory) / 'events.log').read_text()
    pattern = r'(?m)^005 \(' + str(cluster) + r'\.000\.000\).*?Normal termination \(return value 0\)'
    if not re.search(pattern, log, re.S):
        raise ValueError('Control has no matching native success event')
    return rows[0]


def validate(control, expected_library_sha256):
    from shift_nano_integrity import compare_replay_values
    from shift_sm_response_labels import read_nano
    original_request = checked(control['original_request'])
    replay_request = checked(control['replay_request'])
    same_physics(original_request, replay_request)
    original = checked(control['original_result'])
    directory = Path(control['replay_request']['path']).parent
    replay = read(directory / 'result.json')
    status = read(directory / 'status.json')
    for value, request in ((original, control['original_request']), (replay, control['replay_request'])):
        if (value.get('complete') is not True or value.get('exit_code') != 0
                or value.get('request_sha256') != request['sha256']):
            raise ValueError('Control receipt is incomplete or belongs to another request')
    if not status.get('complete') or status.get('exit_code') != 0:
        raise ValueError('Control native transfer status is incomplete')
    for key in ('seed', 'source_gen_sha256', 'source_indices', 'sampling_probabilities', 'events', 'job'):
        if original[key] != replay[key]:
            raise ValueError('Control identity changed: ' + key)
    applied = replay['reviewed_21nm_recovery']
    if (applied['actual_runtime_libraries']['libSimG4CoreGenerators.so']['sha256'] != expected_library_sha256
            or applied['approved_applied_limit_mm'] != 1.e-5):
        raise ValueError('Control did not use the declared numerical-only library and 10 nm limit')
    source = original_request['detector_source']
    expected = []
    for position in range(original_request['selected_skip'],
                          original_request['selected_skip'] + original_request['selected_count']):
        index = source['selected_indices'][position]
        expected.append(dict(source_id=source['normalization_record']['source_id'],
            gen_sha256=source['normalization_record']['gen_sha256'], event_id=source['event_ids'][index],
            native_weight=source['weights'][index], sampling_probability=source['sampling_probabilities'][position]))
    with tempfile.TemporaryDirectory(prefix='SM_control_comparison_') as temporary:
        paths, labels = [], []
        for name, result in (('original', original), ('replay', replay)):
            if result['nano_sha256'] != result['nano_readback_sha256']:
                raise ValueError('Producer did not independently read back its control Nano')
            path = Path(temporary) / (name + '.root')
            download(result['nano_path'], path, result['nano_sha256'])
            paths.append(path)
            labels.append(read_nano(path, expected, result['nano_sha256']))
        equivalence = compare_replay_values(*paths)
        if labels[0] != labels[1] or not any(row['event_pass']['both_both_good'] for row in labels[0]):
            raise ValueError('Known good Both-Both control or its kinematics changed')
    return dict(source_id=source['normalization_record']['source_id'],
        original_request=control['original_request'], replay_request=control['replay_request'],
        replay_result_sha256=sha(directory / 'result.json'), events=replay['events'],
        all_branch_comparison=equivalence, labels_unchanged=True, good_both_both_preserved=True,
        new_source_exposure=0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('request', type=Path)
    args = parser.parse_args()
    request = read(args.request)
    controls = request['controls']
    if len(controls) < 2 or len({c['cluster_id'] for c in controls}) != len(controls):
        raise ValueError('Require at least two independent native control jobs')
    deadline = time.time() + request['wait_seconds']
    endpoint_path = Path(__file__).resolve().parents[1] / 'native_endpoint.json'
    endpoint = read(endpoint_path) if endpoint_path.exists() else None
    while True:
        native = [terminal(control, endpoint) for control in controls]
        if all(native):
            break
        if time.time() >= deadline:
            raise TimeoutError('Known good controls have not closed; production stays gated')
        time.sleep(30)
    results = [validate(control, request['expected_generator_library_sha256']) for control in controls]
    proof = dict(complete=True, scientific_comparison_passed=True, checked_epoch=time.time(),
                 request_sha256=sha(args.request), controls=results, native_terminals=native,
                 actual_primary_converter_qualification=checked(request['primary_converter_qualification']),
                 additional_source_exposure=0)
    primary = proof['actual_primary_converter_qualification']
    if (primary.get('complete') is not True
            or primary.get('runtime_sha256') != request['expected_runtime_sha256']):
        raise ValueError('Downloaded runtime primary-converter qualification is incomplete')
    Path('control_qualification.json').write_text(json.dumps(proof, indent=2, allow_nan=False) + '\n')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        Path('control_qualification.json').write_text(json.dumps(dict(
            complete=False, error=repr(error), additional_source_exposure=0), indent=2) + '\n')
        raise
