#!/usr/bin/env python3
"""Write tiny per-attempt records; unprocessed entries never imply physical zeros."""
import argparse
import json
from pathlib import Path
from validate_sampling_ledger import validate


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('manifest',type=Path)
    p.add_argument('--base',type=Path,default=Path('/eos/home-j/jniedzie/shift_cmssw'))
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    n=0
    with args.output.open('x') as out:
        for entry in json.loads(args.manifest.read_text()):
            ledger=json.loads(Path(entry['ledger']).read_text());validate(ledger)
            by_id={tuple(row['id']):row for row in ledger['rows']}
            reco={}
            for chunk in range(len(ledger['chunks'])):
                campaign=entry.get('chunk_campaigns',{}).get(str(chunk),entry['campaign'])
                path=args.base/'qcd'/campaign/'sampling_replay'/f'part{chunk:04d}'/'report.json'
                if not path.exists():continue
                report=json.loads(path.read_text())
                if report['status']=='validated':
                    for row in report['rows']:reco[tuple(row['id'])]=row['reco']
            identities=sorted(set(by_id)|{tuple(i) for i in ledger['upstream_not_saved_ids']})
            for identity in identities:
                choice=by_id.get(identity)
                selected=bool(choice and choice['selected'])
                state=('upstream_not_saved' if choice is None else 'not_simulated' if not selected
                       else 'processed' if identity in reco else 'pending_or_failed')
                row=dict(id=identity,campaign=entry['campaign'],state=state,
                    sampling_probability=choice['probability'] if choice else None,
                    sampling_weight_for_sum=choice['inverse_probability'] if selected else 0.,
                    reco=reco.get(identity),physics_valid=False,normalization_ready=False)
                out.write(json.dumps(row,separators=(',',':'))+'\n');n+=1
    print(f'Wrote {n} compact bookkeeping records; unprocessed reco is null, not a measured zero')


if __name__=='__main__':main()
