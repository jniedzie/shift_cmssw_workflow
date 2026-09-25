#!/usr/bin/env python3
"""Audit exclusive SoftQCD/MPI classes and contiguous half-open pT bins."""
import argparse
from collections import defaultdict
import json
import math
from pathlib import Path

EXPECTED_BINS = ((0., 1.), (1., 2.), (2., 5.), (5., 10.), (10., 20.), (20., -1.))
PROCESS_CLASSES = {
    'QCD_SoftMpiPartition_FixedTarget_13p6TeV': 'qcd',
    'Charmonium_SoftMpiPartition_FixedTarget_13p6TeV': 'direct_jpsi',
}
MODEL_CONTRACT = 'pythia8-softqcd-nd-cp5-processlevel3-v1'
PARTITION_CONTRACT = 'direct-hard-jpsi-status23or33-v1'


def audit(records, inclusive_xsec_pb=None, inclusive_error_pb=0., max_pull=5.):
    groups = defaultdict(list)
    seen_chunks = set()
    for record in records:
        process = record.get('process')
        event_class = PROCESS_CLASSES.get(process)
        if record.get('schema') != 'shift-production-gen-v1' or not event_class:
            raise ValueError('Unsupported metadata record')
        if record.get('event_class') != event_class:
            raise ValueError('Process/event-class mismatch')
        if record.get('mpi_model_contract') != MODEL_CONTRACT or record.get('partition_contract') != PARTITION_CONTRACT:
            raise ValueError('Mixed or missing SoftQCD/MPI model contract')
        bounds = tuple(float(x) for x in record.get('configured_pthat_bounds', ()))
        if bounds not in EXPECTED_BINS:
            raise ValueError(f'Unsupported or missing partition bin: {bounds}')
        if record.get('generated_filter_efficiency') != 1.:
            raise ValueError('External filtering must not be folded into the partition cross section')
        identity = (event_class, bounds, int(record['chunk']))
        if identity in seen_chunks:
            raise ValueError(f'Duplicate class/bin/chunk metadata: {identity}')
        seen_chunks.add(identity)
        run = record.get('runs', [])
        if len(run) != 1:
            raise ValueError('Each chunk must contain exactly one generator run')
        xsec, error = float(run[0]['internal_xsec_pb']), float(run[0]['error_pb'])
        events = int(record['events'])
        if events <= 0 or not math.isfinite(xsec) or xsec <= 0 or not math.isfinite(error) or error < 0:
            raise ValueError('Invalid event count or cross-section estimate')
        groups[(event_class, bounds)].append((events, xsec, error))

    expected = {(event_class, bounds) for event_class in PROCESS_CLASSES.values() for bounds in EXPECTED_BINS}
    if set(groups) != expected:
        missing = sorted(expected-set(groups), key=str)
        extra = sorted(set(groups)-expected, key=str)
        raise ValueError(f'Incomplete partition suite; missing={missing}, extra={extra}')

    bins = []
    total_xsec, total_variance = 0., 0.
    for event_class in ('qcd', 'direct_jpsi'):
        for bounds in EXPECTED_BINS:
            chunks = groups[(event_class, bounds)]
            total_events = sum(item[0] for item in chunks)
            xsec = sum(n*x for n, x, _ in chunks)/total_events
            error = math.sqrt(sum((n*e)**2 for n, _, e in chunks))/total_events
            bins.append(dict(event_class=event_class, bounds=list(bounds), chunks=len(chunks),
                             events=total_events, cross_section_pb=xsec,
                             generator_stat_error_pb=error))
            total_xsec += xsec
            total_variance += error*error

    total_error = math.sqrt(total_variance)
    closure = None
    if inclusive_xsec_pb is not None:
        if not math.isfinite(inclusive_xsec_pb) or inclusive_xsec_pb <= 0:
            raise ValueError('Invalid inclusive reference cross section')
        if not math.isfinite(inclusive_error_pb) or inclusive_error_pb < 0:
            raise ValueError('Invalid inclusive reference uncertainty')
        denominator = math.hypot(total_error, inclusive_error_pb)
        pull = ((total_xsec-inclusive_xsec_pb)/denominator if denominator else
                (0. if total_xsec == inclusive_xsec_pb else math.inf))
        closure = dict(inclusive_xsec_pb=inclusive_xsec_pb,
                       inclusive_error_pb=inclusive_error_pb,
                       partition_sum_pb=total_xsec,
                       partition_sum_error_pb=total_error,
                       pull=pull, max_abs_pull=max_pull, passed=abs(pull) <= max_pull)
        if not closure['passed']:
            raise ValueError(f'Partition cross-section closure failed: pull={pull:.3g}')

    return dict(schema='shift-soft-mpi-partition-v1', model_contract=MODEL_CONTRACT,
                partition_contract=PARTITION_CONTRACT,
                event_classes=['qcd', 'direct_jpsi'], bins=bins,
                coverage='[0,infinity) independently within each complementary event class',
                boundary_policy='half-open [lower,upper), with the final upper edge unbounded',
                total_partition_cross_section_pb=total_xsec,
                total_partition_generator_stat_error_pb=total_error,
                inclusive_closure=closure,
                normalization_ready=closure is not None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('metadata', nargs='+', type=Path)
    parser.add_argument('--inclusive-xsec-pb', type=float)
    parser.add_argument('--inclusive-error-pb', type=float, default=0.)
    parser.add_argument('--max-pull', type=float, default=5.)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    records = [json.loads(path.read_text()) for path in args.metadata]
    report = audit(records, args.inclusive_xsec_pb, args.inclusive_error_pb, args.max_pull)
    with args.output.open('x') as stream:
        stream.write(json.dumps(report, indent=2) + '\n')
    print(f"Validated {len(records)} chunks across 12 exclusive class/bin strata")


if __name__ == '__main__':
    main()
