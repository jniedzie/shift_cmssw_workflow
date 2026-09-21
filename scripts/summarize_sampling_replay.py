#!/usr/bin/env python3
"""Validate replay completeness and aggregate explicitly weighted topology counts."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


def summarize(entry, base):
    path = Path(entry['ledger'])
    ledger = json.loads(path.read_text())
    ledger_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    counts, weighted, squares, times = Counter(), Counter(), Counter(), Counter()
    missing, failed, reports, identities = [], [], [], set()
    for chunk, selected in enumerate(ledger['chunks']):
        campaign = entry.get('chunk_campaigns', {}).get(str(chunk), entry['campaign'])
        report = base/'qcd'/campaign/'sampling_replay'/f'part{chunk:04d}'/'report.json'
        if not report.exists():
            missing.append(chunk)
            continue
        r = json.loads(report.read_text())
        if r['status'] != 'validated':
            failed.append(dict(chunk=chunk,error=r.get('error')))
            continue
        if r['ledger_sha256'] != ledger_hash or r['reconstructed_mass_read']:
            raise ValueError(f'Contract mismatch: {report}')
        if [row['sampling'] for row in r['rows']] != selected:
            raise ValueError('Selected IDs, probabilities or weights changed')
        for row in r['rows']:
            identity = tuple(row['id'])
            if identity in identities or row['sampling']['id'] != row['id']:
                raise ValueError('Repeated or mismatched identity')
            identities.add(identity)
            w = row['sampling']['inverse_probability']
            flags = dict(events=1, events_muon=row['reco']['muons']>0,
                         events_vertex=row['reco']['vertices']>0,
                         events_both_both=row['reco']['both_both']>0)
            for key, flag in flags.items():
                counts[key] += int(flag)
                weighted[key] += w*flag
                squares[key] += w*w*flag
            counts['random_control_events'] += row['sampling']['probability'] < 1.
        for key, val in r.items():
            if key.endswith('_wall_seconds'):
                times[key] += val
        reports.append(str(report))
    complete = not missing and not failed
    if complete and len(identities) != ledger['selected_events']:
        raise ValueError('Selected denominator mismatch')
    return dict(campaign=entry['campaign'],complete=complete,missing=missing,failed=failed,
        selected_events=ledger['selected_events'],framework_attempts=ledger['framework_attempts'],
        upstream_saved_events=ledger['upstream_saved_events'],counts=dict(counts),
        weighted_counts=dict(weighted),sum_squared_weights=dict(squares),wall_seconds=dict(times),
        reports=reports,physics_valid=False,normalization_ready=False,
        warning='Use this ledger denominator, not repeated parent GEN run/lumi counters or uncorrected Nano genWeight. Partial weighted sums are not estimates.')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('manifest',type=Path)
    p.add_argument('--base',type=Path,default=Path('/eos/home-j/jniedzie/shift_cmssw'))
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    groups=[summarize(e,args.base) for e in json.loads(args.manifest.read_text())]
    result=dict(groups=groups,complete=bool(groups) and all(g['complete'] for g in groups),
                physics_valid=False,normalization_ready=False,reconstructed_mass_read=False)
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    for g in groups:
        print(g['campaign'],'complete',g['complete'],'missing',len(g['missing']),
              'failed',len(g['failed']),'raw',g['counts'],'weighted',g['weighted_counts'])


if __name__=='__main__':
    main()
