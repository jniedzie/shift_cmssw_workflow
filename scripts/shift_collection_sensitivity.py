#!/usr/bin/env python3
"""Train-weighted marginal union bounds; no synthetic electronics or rules."""
import argparse
import csv
import hashlib
import json
from pathlib import Path


def union_bounds(probabilities):
    if any(not 0 <= p <= 1 for p in probabilities):
        raise ValueError('invalid probability')
    return max(probabilities, default=0), min(1, sum(probabilities))


def calculate(counts, mask, phases=(0, 5, 10, 15, 20), qs=(0.001, 0.01, 0.05)):
    if counts['format'] != 'shift-delay-efficiencies-v1':
        raise ValueError('unsupported counts')
    if mask['schema'] != 'cms-lpc-ip5-bunch-mask' or mask['orbit_slots'] != 3564:
        raise ValueError('unsupported mask')
    slots = mask['beam2_filled_bx_slots']
    collisions = set(mask['colliding_ip5_bx_slots'])
    if not slots or len(slots) != len(set(slots)) or any(not 1 <= s <= 3564 for s in slots):
        raise ValueError('invalid parent slots')
    if not collisions <= set(slots) & set(mask['beam1_filled_bx_slots']):
        raise ValueError('inconsistent collision mask')
    points = {p['delay_ns']: p for p in counts['points']}
    if len(points) != len(counts['points']):
        raise ValueError('duplicate delay')
    first = counts['points'][0]
    for p in points.values():
        if p['sources'] != first['sources']:
            raise ValueError('unpaired sources')
        for obj in ('muon', 'dimuon'):
            n, d = p[obj]['inclusive'], p[obj]['denominator']
            if not 0 <= n <= d or d <= 0 or d != first[obj]['denominator']:
                raise ValueError('invalid or changing denominator')
    rows, per_slot = [], []
    for phase in phases:
        # All phases use the same finite readout window. Never extrapolate.
        delays = [phase - 25*k for k in range(-7, 8)]
        if not set(delays) <= set(points):
            raise ValueError('missing exact response points')
        for obj in ('muon', 'dimuon'):
            for q in qs:
                lower, upper, multiplicity = [], [], []
                for slot in slots:
                    ps = [q * points[phase-25*k][obj]['inclusive'] / points[phase-25*k][obj]['denominator']
                          for k in range(-7, 8) if (slot-1+k) % 3564+1 in collisions]
                    lo, hi = union_bounds(ps)
                    lower.append(lo)
                    upper.append(hi)
                    multiplicity.append(sum(ps))
                    per_slot.append(dict(phase_ns=phase, object=obj, q=q, slot=slot, lower=lo, upper=hi))
                rows.append(dict(phase_ns=phase, object=obj, q=q,
                                 lower=sum(lower)/len(slots), upper=sum(upper)/len(slots),
                                 expected_reconstructed_readouts=sum(multiplicity)/len(slots),
                                 denominator=first[obj]['denominator']))
    return rows, per_slot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--counts', required=True, type=Path)
    parser.add_argument('--mask', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    args = parser.parse_args()
    counts, mask = [json.loads(p.read_text()) for p in (args.counts, args.mask)]
    rows, slots = calculate(counts, mask)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report = dict(schema='shift-collection-sensitivity-v1', physics_valid=False,
                  fill=mask['fill_number'], parent_slots=len(mask['beam2_filled_bx_slots']),
                  readout_bx_window=[-7, 7], results=rows,
                  assumptions=[
                      'Uniform production across filled Beam-2 slots; independent of signal kinematics.',
                      'q is the marginal probability per colliding BX of a usable stored ordinary CMS readout, after all real constraints.',
                      'q is a sensitivity parameter, not a measured rate or a proposed change to trigger settings.',
                      'Trigger opportunity independent of simulated signal; no assumption of independence between BX decisions.',
                      'Bounds use max(P_i) <= P(union_i) <= min(1,sum(P_i)); no reconstruction across separate events.',
                      'Physical timing already in response: query additional phase - 25*k only.',
                      'Bounds cover the stated finite window and empirical no-pileup sample only; no confidence interval or tail bound.',
                      'Muon and dimuon denominators are generator objects, not all produced SHIFT interactions.'],
                  inputs={str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (args.counts, args.mask)})
    (args.output_dir/'sensitivity.json').write_text(json.dumps(report, indent=2)+'\n')
    for name, records in [('summary.csv', rows), ('parent_slots.csv', slots)]:
        with (args.output_dir/name).open('w') as out:
            writer = csv.DictWriter(out, fieldnames=list(records[0]), lineterminator='\n')
            writer.writeheader()
            writer.writerows(records)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 2, figsize=(10, 4))
    for ax, obj in zip(axes, ('muon', 'dimuon')):
        selected = [r for r in rows if r['object'] == obj and r['q'] == .01]
        xs = [r['phase_ns'] for r in selected]
        lo = [100*r['lower'] for r in selected]
        hi = [100*r['upper'] for r in selected]
        ax.fill_between(xs, lo, hi, alpha=.2)
        ax.plot(xs, lo, 'o-', label='Lower union bound')
        ax.plot(xs, hi, 's-', label='Upper union bound')
        ax.set(xlabel='Additional SHIFT phase [ns]', ylabel='Collected generator objects [%]', title=obj.capitalize())
        ax.legend(fontsize=8)
        ax.grid(alpha=.2)
    fig.suptitle('Fill 9017; uniform B2 weights; assumed stored-readout probability = 1%\nNo-pileup sample; BX window [-7,+7]; bounds are not confidence intervals', fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, .87))
    fig.savefig(args.output_dir/'phase_bounds.pdf')
    fig.savefig(args.output_dir/'phase_bounds.png', dpi=160)
    plt.close(fig)
    print(json.dumps([r for r in rows if r['phase_ns'] == 0], indent=2))


if __name__ == '__main__':
    main()
