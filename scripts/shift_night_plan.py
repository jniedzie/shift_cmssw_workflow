# Code style: AI-code. AI-generated pinned sparse-plan loader for night reconstruction.
"""Validate immutable selections separately from operational controller state."""
import gzip
import hashlib
import json
import math
from pathlib import Path
import re


def sha(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def valid_sha(value):
    return isinstance(value, str) and re.fullmatch(r'[a-f0-9]{64}', value) is not None


def pinned_path(root, name, expected):
    if not valid_sha(expected):
        raise ValueError('Invalid frozen file SHA256')
    path = Path(name)
    path = path if path.is_absolute() else Path(root) / path
    path = path.resolve()
    if not path.is_file() or sha(path) != expected:
        raise ValueError('Frozen night input changed or disappeared: ' + str(path))
    return path


def event_contract(job, response_sha):
    """Use native double hex values so a rounded weight cannot pass the receipt gate."""
    content = {'source_gen_sha256': job['source_gen_sha256'], 'raw_indices': job['raw_indices'],
               'event_ids': job['event_ids'], 'native_weights_hex': [float(value).hex() for value in job['native_weights']],
               'sampling_probabilities_hex': [float(value).hex() for value in job['sampling_probabilities']],
               'seed': job['seed'], 'response_contract_sha256': response_sha}
    return hashlib.sha256(json.dumps(content, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def load_plan(path):
    """Read only the compact plan index; epoch jobs are loaded one epoch at a time."""
    path = Path(path).resolve()
    data = json.loads(path.read_text())
    if data.get('schema') != 'shift-night-reconstruction-plan-v1':
        raise ValueError('Unsupported night plan schema')
    if not isinstance(data.get('tag'), str) or not re.fullmatch(r'[A-Za-z0-9_.-]+', data['tag']):
        raise ValueError('Night campaign tag must be a plain identifier')
    for key in ('response_contract_sha256', 'normalization_authority_sha256', 'base_bundle_sha256'):
        if not valid_sha(data.get(key)):
            raise ValueError('Missing immutable night authority: ' + key)
    epochs = data.get('epochs')
    if not isinstance(epochs, list) or not epochs:
        raise ValueError('Night plan needs at least one frozen epoch')
    previous_last = -1
    for position, epoch in enumerate(epochs, 1):
        if epoch.get('epoch') != position or epoch.get('q') != .01:
            raise ValueError('Require consecutive independently frozen q=.01 epochs')
        if not valid_sha(epoch.get('seed_hex256')) or not valid_sha(epoch.get('draw_manifest_sha256')):
            raise ValueError('Each epoch needs a full256-bit seed and draw manifest SHA')
        for key in ('population_originals', 'population_sources', 'work_events', 'work_jobs'):
            if type(epoch.get(key)) is not int or epoch[key] < 0:
                raise ValueError('Invalid frozen epoch count: ' + key)
        if epoch['population_originals'] <= 0 or epoch['population_sources'] <= 0:
            raise ValueError('The complete original population must be retained')
        if epoch['work_jobs'] <= 0 or not 1 <= epoch['work_events'] <= 20 * epoch['work_jobs']:
            raise ValueError('Frozen job/event counts disagree')
        if (type(epoch.get('first_job')) is not int or type(epoch.get('last_job')) is not int or
                epoch['first_job'] <= previous_last or epoch['last_job'] < epoch['first_job'] or
                epoch['last_job'] - epoch['first_job'] + 1 != epoch['work_jobs']):
            raise ValueError('Epoch job namespaces must be consecutive and disjoint')
        previous_last = epoch['last_job']
        for name in ('jobs', 'selection_ledger'):
            pinned_path(path.parent, epoch[name + '_path'], epoch[name + '_sha256'])
    seeds = [epoch['seed_hex256'] for epoch in epochs]
    if len(set(seeds)) != len(seeds):
        raise ValueError('Epoch draw seeds must be distinct')
    return {'path': path, 'sha256': sha(path), 'root': path.parent, 'data': data}


def load_epoch(plan, number):
    """Verify exact jobs and identities without loading the full GEN catalogue again."""
    metadata = plan['data']['epochs'][number - 1]
    path = pinned_path(plan['root'], metadata['jobs_path'], metadata['jobs_sha256'])
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'rt') as stream:
        jobs = [json.loads(line) for line in stream if line.strip()]
    if len(jobs) != metadata['work_jobs']:
        raise ValueError('Epoch job file count differs from its frozen manifest')
    expected_jobs = set(range(metadata['first_job'], metadata['last_job'] + 1))
    if {row.get('job') for row in jobs} != expected_jobs:
        raise ValueError('Epoch job identities differ from the frozen namespace')
    seen = set()
    for row in jobs:
        if any(type(row.get(key)) is not int for key in ('job', 'epoch', 'source', 'skip', 'count', 'seed')):
            raise ValueError('Job addressing must use exact integer values')
        if row['epoch'] != number or row['source'] < 0 or row['skip'] != 0 or not 1 <= row['count'] <= 20:
            raise ValueError('Require sparse per-job source views with skip0 and at most20 originals')
        if row['seed'] != 230000000 + row['job'] * 100 or not 1 <= row['seed'] <= 900000000:
            raise ValueError('Job seed differs from the unchanged worker formula or allowed range')
        if not valid_sha(row.get('source_gen_sha256')):
            raise ValueError('Job is missing its original GEN file identity')
        for name in ('raw_indices', 'event_ids', 'native_weights', 'sampling_probabilities'):
            if not isinstance(row.get(name), list) or len(row[name]) != row['count']:
                raise ValueError('Job does not cover exact selected originals: ' + name)
        if (row['raw_indices'] != sorted(row['raw_indices']) or
                any(type(raw) is not int or raw < 0 for raw in row['raw_indices'])):
            raise ValueError('Selected source indices must be nonnegative and sorted')
        if len(set(row['raw_indices'])) != row['count']:
            raise ValueError('Duplicate selected original in job')
        for raw, identity, native, probability in zip(row['raw_indices'], row['event_ids'],
                                                     row['native_weights'], row['sampling_probabilities']):
            if not isinstance(identity, list) or len(identity) != 3 or any(type(value) is not int for value in identity):
                raise ValueError('Original event identity must be an integer run/lumi/event triple')
            if type(native) not in (int, float) or not math.isfinite(native):
                raise ValueError('Native GEN weight must remain finite')
            if type(probability) not in (int, float) or not math.isfinite(probability) or not 0 < probability <= 1:
                raise ValueError('Union sampling probability is invalid')
            key = (row['source_gen_sha256'], tuple(identity))
            if key in seen:
                raise ValueError('Original event is scheduled twice within the epoch')
            seen.add(key)
        selection_path = pinned_path(plan['root'], row['selection_path'], row['selection_sha256'])
        row['selection_absolute_path'] = str(selection_path)
        computed = event_contract(row, plan['data']['response_contract_sha256'])
        if 'event_contract_sha256' in row and row['event_contract_sha256'] != computed:
            raise ValueError('Job event contract disagrees with exact native weights/probabilities')
        row['event_contract_sha256'] = computed
    if sum(row['count'] for row in jobs) != metadata['work_events']:
        raise ValueError('Epoch selected event count differs from its frozen manifest')
    return {'metadata': metadata, 'jobs': jobs, 'by_job': {row['job']: row for row in jobs}}
