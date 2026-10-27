#!/usr/bin/env python3
# Code style: AI-code. AI-generated measured SM dimuon response labels.
"""Read actual Nano outcomes without changing GEN selection or event weights.

Both-Both is topology-only in the ordinary histogrammer. Good additionally
requires opposite charge, valid DCA, and 0 <= DCA <= 50 cm. Keep these labels
separate so an efficiency study cannot silently substitute one for another.
"""
import argparse
from bisect import bisect_right
import hashlib
import json
import math
from pathlib import Path
import re


SCHEMA = 'shift-sm-reconstruction-response-labels-v1'
PAIR_FIELDS = ('mass', 'pt', 'vx', 'vy', 'vz', 'dca', 'dcaValid', 'isOS',
               'topologyMin', 'topologyMax', 'muonIdx1', 'muonIdx2')
INTEGER_PAIR_FIELDS = ('dcaValid', 'isOS', 'topologyMin', 'topologyMax',
                       'muonIdx1', 'muonIdx2')
CATEGORIES = ('inclusive', 'both_both', 'opposite_sign', 'good',
              'both_both_opposite_sign', 'both_both_good')
TAILS = {'mass_gt_20': ('mass', 20.), 'mass_gt_50': ('mass', 50.),
         'pt_gt_10': ('pt', 10.), 'pt_gt_20': ('pt', 20.)}
SAMPLING_BRANCHES = ('shiftOriginalGenWeight', 'shiftSamplingProbability',
                     'shiftSamplingWeight', 'shiftSamplingGenWeight')
DEFAULT_HISTOGRAM_EDGES = {
    'mass': [0., 1., 2., 5., 10., 20., 50., 100., 200.],
    'pt': [0., 1., 2., 5., 10., 20., 50., 100., 200.],
    'vz': [-20000., -15000., -10000., -5000., 0., 5000., 10000., 15000., 20000.],
}


def finite(value):
    return value is not None and math.isfinite(value)


def pair_categories(pair):
    """Match the ordinary TEA topology and Good collection definitions."""
    both = pair['topologyMin'] == 2 and pair['topologyMax'] == 2
    opposite = pair['isOS'] == 1
    good = (opposite and pair['dcaValid'] == 1 and finite(pair['dca'])
            and 0. <= pair['dca'] <= 50.)
    return dict(inclusive=True, both_both=both, opposite_sign=opposite,
                good=good, both_both_opposite_sign=both and opposite,
                both_both_good=both and good)


def event_labels(pairs):
    """Count pairs and independent event successes separately, including tails."""
    counts = dict.fromkeys(CATEGORIES, 0)
    tails = {category: dict.fromkeys(TAILS, 0) for category in CATEGORIES}
    for pair in pairs:
        for category, accepted in pair_categories(pair).items():
            if not accepted:
                continue
            counts[category] += 1
            for name, (field, threshold) in TAILS.items():
                if finite(pair[field]) and pair[field] > threshold:
                    tails[category][name] += 1
    return dict(pair_counts=counts, event_pass={key: value > 0 for key, value in counts.items()},
                tail_counts=tails,
                tail_event_pass={key: {name: value > 0 for name, value in row.items()}
                                 for key, row in tails.items()})


def pair_histograms(pairs, edges=None):
    """Return event-level bin contributions, retaining under/overflow and NaNs.

    Each counts vector is [underflow, ordinary bins..., overflow]. Values equal
    to an upper edge enter the next bin, matching ROOT histogram binning.
    Sum these vectors by original event before calculating statistical errors.
    """
    edges = DEFAULT_HISTOGRAM_EDGES if edges is None else edges
    for field, axis in edges.items():
        if (field not in ('mass', 'pt', 'vz') or len(axis) < 2
                or any(not math.isfinite(x) for x in axis)
                or any(left >= right for left, right in zip(axis, axis[1:]))):
            raise ValueError('Require finite, increasing mass/pt/vz histogram edges')
    result = {category: {field: dict(counts=[0] * (len(axis)+1), nonfinite=0)
                         for field, axis in edges.items()} for category in CATEGORIES}
    for pair in pairs:
        for category, accepted in pair_categories(pair).items():
            if not accepted:
                continue
            for field, axis in edges.items():
                if not finite(pair[field]):
                    result[category][field]['nonfinite'] += 1
                else:
                    result[category][field]['counts'][bisect_right(axis, pair[field])] += 1
    return result


def expected_index(expected_events):
    """Reject ambiguous Nano identities instead of guessing their GEN parent."""
    result = {}
    for row in expected_events:
        identity = tuple(row['event_id'])
        if (len(identity) != 3 or any(type(x) is not int or x < 0 for x in identity)
                or not re.fullmatch('[0-9a-f]{64}', row['gen_sha256'])):
            raise ValueError('Require an explicit original GEN hash and run/lumi/event')
        if identity in result:
            raise ValueError('Duplicate or ambiguous run/lumi/event in this Nano input')
        weight, probability = row['native_weight'], row['sampling_probability']
        if not math.isfinite(weight) or not math.isfinite(probability) or not 0 < probability <= 1:
            raise ValueError('Require finite native weight and positive frozen probability')
        result[identity] = row
    return result


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1048576), b''):
            digest.update(block)
    return digest.hexdigest()


def read_nano(path, expected_events, expected_sha256=None, histogram_edges=None):
    """Read a complete frozen Nano population with exact original-event binding.

    expected_events rows contain gen_sha256, event_id, native_weight,
    sampling_probability, and optionally source_id. The hash identifies the
    caller's original raw GEN authority, not a newly completed/replayed GEN.
    All expected events must appear exactly once. Missing detector results must
    be classified by the inventory before calling this function, never as zeros.
    """
    import awkward as ak
    import numpy as np
    import uproot

    expected = expected_index(expected_events)
    if expected_sha256 is not None and file_sha256(path) != expected_sha256:
        raise ValueError('Nano SHA-256 differs from the published input receipt')
    branches = ['run', 'luminosityBlock', 'event', 'nShiftMuon', 'nShiftDimuonVertex',
                *SAMPLING_BRANCHES, *('ShiftDimuonVertex_'+name for name in PAIR_FIELDS)]
    # Workers stage their receipt-checked Nano locally. The explicit local
    # source avoids the asynchronous fsspec path, which can stall in CMSSW.
    with uproot.open(path, handler=uproot.source.file.MemmapSource) as source:
        tree = source['Events']
        missing = set(branches)-set(tree.keys())
        if missing:
            raise ValueError('Nano response branches are absent: '+', '.join(sorted(missing)))
        arrays = tree.arrays(branches, library='ak', how=dict)
    for branch in SAMPLING_BRANCHES:
        if ak.to_numpy(arrays[branch]).dtype != np.dtype('float64'):
            raise ValueError('Frozen sampling branch must remain Double: '+branch)
    rows, seen = [], set()
    for position in range(len(arrays['event'])):
        identity = tuple(int(arrays[name][position]) for name in ('run', 'luminosityBlock', 'event'))
        if identity not in expected or identity in seen:
            raise ValueError('Unexpected or duplicated original Nano event: '+str(identity))
        seen.add(identity)
        original = expected[identity]
        native, probability = original['native_weight'], original['sampling_probability']
        references = (native, probability, 1./probability, native/probability)
        for branch, reference in zip(SAMPLING_BRANCHES, references):
            actual = float(arrays[branch][position])
            if not math.isfinite(actual) or not math.isclose(actual, reference, rel_tol=1.e-10, abs_tol=1.e-300):
                raise ValueError('Frozen native weight/probability changed: '+branch)
        count = int(arrays['nShiftDimuonVertex'][position])
        if count < 0 or any(len(arrays['ShiftDimuonVertex_'+name][position]) != count for name in PAIR_FIELDS):
            raise ValueError('Inconsistent dimuon array lengths')
        pairs = []
        for number in range(count):
            pair, nonfinite = {}, []
            for name in PAIR_FIELDS:
                value = float(arrays['ShiftDimuonVertex_'+name][position][number])
                if not math.isfinite(value):
                    pair[name] = None
                    nonfinite.append(name)
                elif name in INTEGER_PAIR_FIELDS:
                    if value != int(value):
                        raise ValueError('Noninteger charge/topology/index field: '+name)
                    pair[name] = int(value)
                else:
                    pair[name] = value
            pair['nonfinite_fields'] = nonfinite
            pairs.append(pair)
        row = dict(gen_sha256=original['gen_sha256'], event_id=list(identity),
                   native_weight=native, sampling_probability=probability,
                   sampling_gen_weight=native/probability,
                   n_shift_muons=int(arrays['nShiftMuon'][position]), retained_pairs=pairs,
                   **event_labels(pairs))
        if 'source_id' in original:
            row['source_id'] = original['source_id']
        if histogram_edges is not None:
            row['pair_histograms'] = pair_histograms(pairs, histogram_edges)
        rows.append(row)
    if seen != set(expected):
        raise ValueError('Nano lacks frozen expected events; missing results are not physics zeros')
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('nano')
    parser.add_argument('expected_json', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--sha256')
    parser.add_argument('--histogram-contributions', action='store_true')
    args = parser.parse_args()
    expected = json.loads(args.expected_json.read_text())
    if isinstance(expected, dict):
        expected = expected['expected_events']
    edges = DEFAULT_HISTOGRAM_EDGES if args.histogram_contributions else None
    rows = read_nano(args.nano, expected, args.sha256, edges)
    result = dict(schema=SCHEMA, nano=args.nano, nano_sha256=args.sha256,
                  events=len(rows), categories=list(CATEGORIES),
                  histogram_edges=edges, rows=rows)
    args.output.write_text(json.dumps(result, allow_nan=False)+'\n')


if __name__ == '__main__':
    main()
