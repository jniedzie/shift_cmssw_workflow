#!/usr/bin/env python3
"""Audit exclusive SoftQCD/MPI classes and contiguous half-open pT bins."""
import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
from soft_mpi_model import MODEL_CONTRACT, PARTITION_CONTRACT, edm_run_offset

EXPECTED_BINS = ((0., 1.), (1., 2.), (2., 5.), (5., 10.), (10., 20.), (20., -1.))
PROCESS_CLASSES = {
    'QCD_SoftMpiPartition_FixedTarget_13p6TeV': 'qcd',
    'Charmonium_SoftMpiPartition_FixedTarget_13p6TeV': 'direct_jpsi',
}


def audit(records, inclusive_xsec_pb=None, inclusive_error_pb=0., max_pull=5.,
          max_relative_bin_error=0.2):
    groups = defaultdict(list)
    seen_chunks = set()
    model_digests = set()
    fragment_digests = defaultdict(set)
    reference_xsecs = []
    for record in records:
        process = record.get('process')
        event_class = PROCESS_CLASSES.get(process)
        if record.get('schema') != 'shift-production-gen-v1' or not event_class:
            raise ValueError('Unsupported metadata record')
        if record.get('event_class') != event_class:
            raise ValueError('Process/event-class mismatch')
        if record.get('mpi_model_contract') != MODEL_CONTRACT or record.get('partition_contract') != PARTITION_CONTRACT:
            raise ValueError('Mixed or missing SoftQCD/MPI model contract')
        model_digest = record.get('mpi_model_settings_sha256')
        fragment_digest = record.get('fragment_sha256')
        if not model_digest or not fragment_digest:
            raise ValueError('Missing source-model or fragment digest')
        model_digests.add(model_digest)
        fragment_digests[event_class].add(fragment_digest)
        bounds = tuple(float(x) for x in record.get('configured_pthat_bounds', ()))
        if bounds not in EXPECTED_BINS:
            raise ValueError(f'Unsupported or missing partition bin: {bounds}')
        if record.get('generated_filter_efficiency') != 1.:
            raise ValueError('External filtering must not be folded into the partition cross section')
        chunk = int(record['chunk'])
        if not 0 <= chunk < 100000 or record.get('edm_run_offset') != edm_run_offset(event_class, bounds):
            raise ValueError('Invalid or overlapping class/bin EDM run namespace')
        identity = (event_class, bounds, chunk)
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
        statistics = record.get('pythia_process_statistics')
        if (not statistics or statistics.get('accepted') != events or
                statistics.get('tried', 0) < statistics.get('selected', 0) or
                statistics.get('selected', 0) < events or
                not math.isclose(statistics.get('sigma_pb', 0), xsec, rel_tol=0.001)):
            raise ValueError('Missing or inconsistent Pythia trial statistics')
        tried = int(statistics['tried'])
        reference_xsecs.append(xsec*tried/events)
        groups[(event_class, bounds)].append((events, tried, xsec, error))

    if len(model_digests) != 1 or any(len(digests) != 1 for digests in fragment_digests.values()):
        raise ValueError('Mixed SoftQCD/MPI source model or fragment versions')
    reference = sum(reference_xsecs)/len(reference_xsecs)
    if any(abs(value/reference-1.) > 0.02 for value in reference_xsecs):
        raise ValueError('Inconsistent underlying non-diffractive cross sections')
    expected = {(event_class, bounds) for event_class in PROCESS_CLASSES.values() for bounds in EXPECTED_BINS}
    if set(groups) != expected:
        missing = sorted(expected-set(groups), key=str)
        extra = sorted(set(groups)-expected, key=str)
        raise ValueError(f'Incomplete partition suite; missing={missing}, extra={extra}')

    bins = []
    total_xsec, total_variance = 0., 0.
    all_bins_precise = True
    for event_class in ('qcd', 'direct_jpsi'):
        for bounds in EXPECTED_BINS:
            chunks = groups[(event_class, bounds)]
            total_events = sum(item[0] for item in chunks)
            total_trials = sum(item[1] for item in chunks)
            xsec = sum(trials*x for _, trials, x, _ in chunks)/total_trials
            error = math.sqrt(sum((trials*e)**2 for _, trials, _, e in chunks))/total_trials
            relative_error = error/xsec
            all_bins_precise &= relative_error <= max_relative_bin_error
            bins.append(dict(event_class=event_class, bounds=list(bounds), chunks=len(chunks),
                             events=total_events, pythia_trials=total_trials,
                             edm_run_offset=edm_run_offset(event_class, bounds),
                             cross_section_pb=xsec, generator_stat_error_pb=error,
                             relative_generator_stat_error=relative_error))
            total_xsec += xsec
            total_variance += error*error

    total_error = math.sqrt(total_variance)
    closure = None
    if not math.isfinite(max_relative_bin_error) or max_relative_bin_error <= 0:
        raise ValueError('Invalid maximum relative bin uncertainty')
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
        if not all_bins_precise:
            raise ValueError('Insufficient generator statistics in at least one bin')

    return dict(schema='shift-soft-mpi-partition-v1', model_contract=MODEL_CONTRACT,
                partition_contract=PARTITION_CONTRACT,
                event_classes=['qcd', 'direct_jpsi'], bins=bins,
                coverage='[0,infinity) independently within each complementary event class',
                boundary_policy='half-open [lower,upper), with the final upper edge unbounded',
                total_partition_cross_section_pb=total_xsec,
                total_partition_generator_stat_error_pb=total_error,
                inclusive_closure=closure,
                common_model_settings_sha256=next(iter(model_digests)),
                inferred_inclusive_cross_section_pb=reference,
                maximum_relative_bin_error=max_relative_bin_error,
                normalization_ready=closure is not None and all_bins_precise)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('metadata', nargs='+', type=Path)
    parser.add_argument('--inclusive-xsec-pb', type=float)
    parser.add_argument('--inclusive-error-pb', type=float, default=0.)
    parser.add_argument('--max-pull', type=float, default=5.)
    parser.add_argument('--max-relative-bin-error', type=float, default=0.2)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    records = [json.loads(path.read_text()) for path in args.metadata]
    report = audit(records, args.inclusive_xsec_pb, args.inclusive_error_pb,
                   args.max_pull, args.max_relative_bin_error)
    with args.output.open('x') as stream:
        stream.write(json.dumps(report, indent=2) + '\n')
    print(f"Validated {len(records)} chunks across 12 exclusive class/bin strata")


if __name__ == '__main__':
    main()
