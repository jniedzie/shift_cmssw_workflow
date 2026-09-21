#!/usr/bin/env python3
"""MC-only sampling ledger with nonzero support; never an analysis selection."""
import argparse
import hashlib
import json
import math
from pathlib import Path


def sampling_probability(row, threshold=10., floor=.1):
    if not math.isfinite(threshold) or threshold < 0 or not 0 < floor <= 1:
        raise ValueError('Require a finite threshold and strictly positive retention floor')
    high = sum(-10 < m['eta'] < 0 and m['p'] > threshold for m in row['muons']) >= 2
    return 1. if high else floor


def uniform(identity, salt):
    key = ':'.join(map(str, (salt, *identity))).encode()
    return (int.from_bytes(hashlib.sha256(key).digest()[:8], 'big') >> 11) / 2**53


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('report', type=Path)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--attempts', type=int, default=500)
    p.add_argument('--threshold', type=float, default=10.)
    p.add_argument('--floor', type=float, default=.1)
    p.add_argument('--salt', type=int, default=9213001)
    p.add_argument('--chunk-size', type=int, default=5)
    args = p.parse_args()
    r = json.loads(args.report.read_text())
    if r['status'] != 'validated' or r['contract']['mode'] != 'gen':
        raise ValueError('Require a validated GEN report')
    if r['contract']['sample'] != 'qcdmu':
        raise ValueError('This bounded replay pilot supports only the audited mu-enriched QCD parent')
    if not 0 < args.attempts <= r['contract']['attempted_events'] or args.chunk_size <= 0:
        raise ValueError('Invalid population or chunk size')
    population = [row for row in r['generation']['rows'] if row['id'][2] <= args.attempts]
    if any(row['id'][:2] != [r['contract']['seed'], 1] for row in r['generation']['rows']):
        raise ValueError('This bounded prefix ledger requires a single known run and lumisection')
    if len({tuple(row['id']) for row in population}) != len(population):
        raise ValueError('Duplicate identities')
    ledger, selected = [], []
    for row in population:
        q = sampling_probability(row, args.threshold, args.floor)
        keep = q == 1. or uniform(row['id'], args.salt) < q
        entry = dict(id=row['id'], probability=q, inverse_probability=1./q,
                     selected=keep, reason='two_forward_muons' if q == 1 else 'random_control')
        ledger.append(entry)
        if keep:
            selected.append(entry)
    chunks = [selected[i:i+args.chunk_size] for i in range(0, len(selected), args.chunk_size)]
    result = dict(schema='shift-mc-sampling-ledger-v1', source_report=str(args.report.resolve()),
        source_report_sha256=hashlib.sha256(args.report.read_bytes()).hexdigest(),
        framework_attempts=args.attempts, upstream_saved_events=len(population),
        upstream_not_saved_events=args.attempts-len(population),
        upstream_not_saved_ids=[[r['contract']['seed'],1,i] for i in range(1,args.attempts+1)
                               if i not in {row['id'][2] for row in population}],
        selected_events=len(selected), threshold_GeV=args.threshold, retention_floor=args.floor,
        salt=args.salt, rows=ledger, chunks=chunks, physics_valid=False,
        normalization_ready=False, sum_inverse_probability=sum(x['inverse_probability'] for x in selected),
        sum_squared_inverse_probability=sum(x['inverse_probability']**2 for x in selected),
        warning='Sampling weights only, not cross-section weights. Never combine with parent sample as independent background.')
    with args.output.open('x') as f:
        json.dump(result, f, indent=2)
    print(f"{len(population)} upstream saved -> {len(selected)} selected in {len(chunks)} chunks; floor={args.floor}")


if __name__ == '__main__':
    main()
