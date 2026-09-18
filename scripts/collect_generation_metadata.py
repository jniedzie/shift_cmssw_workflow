#!/usr/bin/env python3
"""Combine complete inclusive or mu-enriched QCD metadata without summing cross sections.

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
    processes = {r['process'] for r in records}
    allowed = {'QCD_FixedTarget_pThat_1to5GeV_13p6TeV',
               'QCD_MuEnriched_FixedTarget_pThat_1to5GeV_13p6TeV'}
    if len(processes) != 1 or not processes <= allowed:
        raise ValueError('Mixed or unsupported QCD process definitions')
    process = next(iter(processes))
    filtered = process.startswith('QCD_MuEnriched_')
    if any(r['schema'] != 'shift-production-gen-v1' or
           r['forced_decay'] != 'none' or
           r['events'] <= 0 or r['sum_weights'] != r['events'] or
           r['sum_weights_squared'] != r['events'] or len(r['runs']) != 1
           for r in records):
        raise ValueError('Only matching unit-weight QCD chunks are supported')
    attempts = [r.get('attempted_events', r['events']) for r in records]
    accepted = [r.get('accepted_events', r['events']) for r in records]
    if any(ntry <= 0 or nsave <= 0 or nsave > ntry for ntry, nsave in zip(attempts, accepted)):
        raise ValueError('Invalid attempted/accepted event counts')
    if any(not math.isclose(r['generated_filter_efficiency'], nsave/ntry,
                            rel_tol=1.e-12, abs_tol=1.e-15)
           for r, ntry, nsave in zip(records, attempts, accepted)):
        raise ValueError('Recorded filter efficiency does not match attempted/accepted counts')
    if not filtered and any(ntry != nsave or r['generated_filter_efficiency'] != 1
                            for r, ntry, nsave in zip(records, attempts, accepted)):
        raise ValueError('Unfiltered QCD records contain a filter loss')
    total_attempted, total_accepted = sum(attempts), sum(accepted)
    xsecs = [r['runs'][0]['internal_xsec_pb'] for r in records]
    errors = [r['runs'][0]['error_pb'] for r in records]
    if any(not math.isfinite(x) or x <= 0 for x in xsecs) or any(not math.isfinite(x) or x < 0 for x in errors):
        raise ValueError('Invalid cross-section estimate or uncertainty')
    sigma = sum(ntry*x for ntry, x in zip(attempts, xsecs))/total_attempted
    sigma_error = math.sqrt(sum((ntry*x)**2 for ntry, x in zip(attempts, errors)))/total_attempted
    selected_sigma = sum(nsave*x for nsave, x in zip(accepted, xsecs))/total_attempted
    selected_sigma_error = math.sqrt(sum((nsave*x)**2 for nsave, x in zip(accepted, errors)))/total_attempted
    efficiency = total_accepted / total_attempted
    return dict(schema='shift-qcd-mixture-v1', process=process,
        events=total_accepted, attempted_events=total_attempted, accepted_events=total_accepted,
        chunks=expected_chunks, cross_section_pb=selected_sigma,
        inclusive_cross_section_pb=sigma, inclusive_cross_section_error_pb=sigma_error,
        selected_cross_section_pb=selected_sigma,
        selected_cross_section_generator_stat_error_pb=selected_sigma_error,
        filter_efficiency=efficiency,
        filter_efficiency_binomial_error=math.sqrt(efficiency*(1.-efficiency)/total_attempted),
        event_weight_pb_by_chunk={str(r['chunk']): x/total_attempted for r, x in zip(records, xsecs)},
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
