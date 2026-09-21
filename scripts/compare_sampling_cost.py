#!/usr/bin/env python3
"""Compare recorded stage wall times, not a controlled CPU or end-to-end benchmark."""
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('baseline', type=Path)
    parser.add_argument('replay', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    baseline = json.loads(args.baseline.read_text())['groups']
    replay = json.loads(args.replay.read_text())
    if not replay['complete']:
        raise ValueError('Replay must be complete')
    comparisons = []
    for group in replay['groups']:
        report = json.loads(Path(group['reports'][0]).read_text())
        ledger = json.loads(Path(report['ledger']).read_text())
        parent = json.loads(Path(ledger['source_report']).read_text())['contract']
        key = f"qcdmu/full/{parent['lower']:g}:{parent['upper']:g}"
        reference = baseline[key]
        if reference['missing'] or reference['failed']:
            raise ValueError('Baseline bin must be complete')
        factor = group['framework_attempts'] / reference['framework_attempts']
        stages = {}
        for stage in range(1, 5):
            name = f'step{stage}_wall_seconds'
            unweighted = reference['wall_seconds'][name] * factor
            weighted = group['wall_seconds'][name]
            stages[str(stage)] = dict(baseline_scaled_seconds=unweighted,
                                      replay_seconds=weighted, ratio=unweighted/weighted)
        unweighted = sum(row['baseline_scaled_seconds'] for row in stages.values())
        weighted = sum(row['replay_seconds'] for row in stages.values())
        comparisons.append(dict(bin=key, framework_attempts=group['framework_attempts'],
            upstream_saved_events=group['upstream_saved_events'],
            simulated_events=group['counts']['events'], stages=stages,
            baseline_scaled_seconds=unweighted, replay_seconds=weighted,
            stage_wall_ratio=unweighted/weighted, saved_fraction=1-weighted/unweighted))
    unweighted = sum(row['baseline_scaled_seconds'] for row in comparisons)
    weighted = sum(row['replay_seconds'] for row in comparisons)
    result = dict(groups=comparisons, stage_wall_ratio=unweighted/weighted,
        saved_fraction=1-weighted/unweighted, reconstructed_mass_read=False,
        caveats=['Not a controlled matched-event or CPU benchmark; different events and workers.',
                 'Replay has <=5 selected events/job; baseline has 20 attempts/job.',
                 'Excludes failed jobs, queue time, source audits, publication and parent GEN production.',
                 'No demonstrated gain in precision of the rare both-both observable.'])
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
