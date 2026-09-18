#!/usr/bin/env python3
"""Combine complete, unfiltered QCD chunk metadata, never sum chunk cross sections.

Each independent chunk is a stratum weighted by its generated-event fraction.
For later histograms, event weight in pb is sigma_chunk / N_all_generated.
Multiply by a separately specified exposure in inverse pb only when justified.
"""
import argparse
import json
import math
from pathlib import Path


def combine(records, expected_chunks):
    if len(records) != expected_chunks or {r['chunk'] for r in records} != set(range(expected_chunks)):
        raise ValueError('Missing, duplicate, or unexpected chunks')
    if len({r['fragment_sha256'] for r in records}) != 1:
        raise ValueError('Mixed generator fragments')
    if any(r['schema'] != 'shift-production-gen-v1' or
           r['process'] != 'QCD_FixedTarget_pThat_1to5GeV_13p6TeV' or
           r['generated_filter_efficiency'] != 1 or r['forced_decay'] != 'none' or
           r['events'] <= 0 or r['sum_weights'] != r['events'] or
           r['sum_weights_squared'] != r['events'] or len(r['runs']) != 1
           for r in records):
        raise ValueError('Only matching unfiltered unit-weight QCD chunks are supported')
    total = sum(r['events'] for r in records)
    xsecs = [r['runs'][0]['internal_xsec_pb'] for r in records]
    errors = [r['runs'][0]['error_pb'] for r in records]
    if any(not math.isfinite(x) or x <= 0 for x in xsecs) or any(not math.isfinite(x) or x < 0 for x in errors):
        raise ValueError('Invalid cross-section estimate or uncertainty')
    sigma = sum(r['events']*x for r, x in zip(records, xsecs))/total
    error = math.sqrt(sum((r['events']*x)**2 for r, x in zip(records, errors)))/total
    return dict(schema='shift-qcd-mixture-v1', events=total, chunks=expected_chunks,
        cross_section_pb=sigma, independent_chunk_stat_error_pb=error,
        event_weight_pb_by_chunk={str(r['chunk']): x/total for r, x in zip(records, xsecs)},
        normalization='generated-event-fraction mixture of independent identical-phase-space chunks',
        warning='No luminosity, trigger, reconstruction or analysis efficiency multiplied in. Provisional geometry.',
        physics_valid=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('metadata_directory', type=Path)
    parser.add_argument('--expected-chunks', required=True, type=int)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    records = [json.loads(p.read_text()) for p in sorted(args.metadata_directory.glob('part*.json'))]
    report = combine(records, args.expected_chunks)
    with args.output.open('x') as stream:
        stream.write(json.dumps(report, indent=2) + '\n')
    print(f"Validated normalization inputs for {report['events']} generated QCD events")


if __name__ == '__main__':
    main()
