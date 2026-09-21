#!/usr/bin/env python3
"""Summarize explicit sampling manifests without opening reconstructed ROOT data.

Gate counts are counterfactual diagnostics, not authorization to filter events.
Only validated pilot reports contribute. Missing/failed chunks remain visible.
"""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import re


def gates(row):
    forward = [m for m in row['muons'] if -10 < m['eta'] < 0]
    result = {'no_muon_system_simhit': not any(row['simhits'].values()),
              'fewer_than_two_gen_muons': len(row['muons']) < 2,
              'fewer_than_two_forward_gen_muons': len(forward) < 2,
              'fewer_than_two_reco_muons': row['reco']['muons'] < 2}
    for threshold in (5, 10, 20, 40, 80):
        result[f'fewer_than_two_forward_gen_muons_p_gt_{threshold}'] = sum(m['p'] > threshold for m in forward) < 2
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('manifest', type=Path)
    p.add_argument('--base', type=Path, default=Path('/eos/home-j/jniedzie/shift_cmssw'))
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    groups = defaultdict(lambda: dict(expected_chunks=0, validated_chunks=0,
        missing=[], failed=[], framework_attempts=0, generator_attempts=0,
        accepted_events=0, generator_failures=0, counts=Counter(),
        reconstruction=Counter(), gates=defaultdict(Counter), wall_seconds=Counter(),
        reports=[], internal_cross_sections_pb=[]))
    identities = set()
    for line in args.manifest.read_text().splitlines():
        if not line.strip() or line.startswith('#'):
            continue
        sample, mode, lo, hi, nevents, seed, chunk, tag = line.split()
        key = f'{sample}/{mode}/{lo}:{hi}'
        g = groups[key]
        g['expected_chunks'] += 1
        typ = 'jpsi' if sample == 'jpsi' else 'qcd'
        campaign = args.base / typ / f'SamplingScan_{sample}_pThat_{lo}to{hi}_{tag}_20260921_v1'
        path = campaign / 'sampling_pilot' / mode / f'part{int(chunk):04d}' / 'report.json'
        if not path.exists():
            g['missing'].append(int(chunk))
            continue
        report = json.loads(path.read_text())
        if report['status'] != 'validated':
            g['failed'].append(dict(chunk=int(chunk), error=report.get('error')))
            continue
        c, gen = report['contract'], report['generation']
        expected = dict(sample=sample, mode=mode, lower=float(lo), upper=float(hi),
                        attempted_events=int(nevents), seed=int(seed))
        if any(c[k] != v for k, v in expected.items()) or report['reconstructed_mass_read']:
            raise ValueError(f'Contract mismatch: {path}')
        if len(gen['rows']) != gen['accepted_events']:
            raise ValueError(f'Row count mismatch: {path}')
        g['validated_chunks'] += 1
        g['framework_attempts'] += int(nevents)
        g['generator_attempts'] += gen['attempted_events']
        g['accepted_events'] += gen['accepted_events']
        g['generator_failures'] += gen.get('generator_failures', int(nevents)-gen['attempted_events'])
        g['counts'].update(gen['counts'])
        g['reconstruction'].update(report.get('reconstruction', {}))
        g['reports'].append(str(path))
        g['internal_cross_sections_pb'].extend(gen['runs'])
        log = path.with_name('step1.log').read_text()
        for label, pattern in (
            ('step1_event_loop_cpu_seconds', r'TimeReport\s+event loop CPU/event =\s*([0-9.eE+-]+)'),
            ('g4sim_module_reported_seconds', r'TimeReport\s+([0-9.eE+-]+)\s+[0-9.eE+-]+\s+[0-9.eE+-]+\s+g4SimHits\s*$')):
            matches = re.findall(pattern, log, re.MULTILINE)
            if matches:
                g['wall_seconds'][label] += float(matches[-1])*int(nevents)
        for k, v in report.items():
            if k.endswith('_wall_seconds'):
                g['wall_seconds'][k] += v
        for row in gen['rows']:
            identity = (sample, mode, *row['id'])
            if identity in identities:
                raise ValueError(f'Duplicate event identity: {identity}')
            identities.add(identity)
            if mode != 'full':
                continue
            for name, rejects in gates(row).items():
                if rejects:
                    stats = g['gates'][name]
                    stats['would_skip_events'] += 1
                    stats['lost_events_with_reco_muon'] += row['reco']['muons'] > 0
                    stats['lost_events_with_vertex'] += row['reco']['vertices'] > 0
                    stats['lost_events_with_both_both'] += row['reco']['both_both'] > 0
    if not groups:
        raise ValueError('Empty sampling manifest')
    result = dict(physics_valid=False, normalization_ready=False,
        reconstructed_mass_read=False, manifest=str(args.manifest.resolve()),
        complete=all(g['validated_chunks'] == g['expected_chunks'] for g in groups.values()),
        groups=dict(groups))
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    for key, g in groups.items():
        print(key, 'chunks', f"{g['validated_chunks']}/{g['expected_chunks']}",
              'attempted', g['framework_attempts'], 'saved', g['accepted_events'],
              'reco', dict(g['reconstruction']), 'failures', len(g['failed']))


if __name__ == '__main__':
    main()
