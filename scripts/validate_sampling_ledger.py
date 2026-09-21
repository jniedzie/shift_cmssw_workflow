#!/usr/bin/env python3
"""Recompute every sampling decision and denominator from the immutable GEN report."""
import argparse
import hashlib
import json
import math
from pathlib import Path
from prepare_weighted_sampling import sampling_probability, uniform


def validate(ledger):
    path=Path(ledger['source_report'])
    if hashlib.sha256(path.read_bytes()).hexdigest()!=ledger['source_report_sha256']:
        raise ValueError('Parent report digest mismatch')
    parent=json.loads(path.read_text())
    if parent['status']!='validated' or parent['contract']['mode']!='gen':
        raise ValueError('Parent is not validated GEN')
    if not 0<ledger['framework_attempts']<=parent['contract']['attempted_events']:
        raise ValueError('Invalid parent attempt prefix')
    population=[row for row in parent['generation']['rows'] if row['id'][2]<=ledger['framework_attempts']]
    if [row['id'] for row in population]!=[row['id'] for row in ledger['rows']]:
        raise ValueError('Population identity mismatch')
    selected=[]
    for row, entry in zip(population,ledger['rows']):
        q=sampling_probability(row,ledger['threshold_GeV'],ledger['retention_floor'])
        keep=q==1 or uniform(row['id'],ledger['salt'])<q
        if entry['probability']!=q or entry['inverse_probability']!=1/q or entry['selected']!=keep:
            raise ValueError('Probability, inverse weight or decision mismatch')
        if keep:selected.append(entry)
    if [e for chunk in ledger['chunks'] for e in chunk]!=selected:
        raise ValueError('Chunk population mismatch')
    if len(selected)!=ledger['selected_events'] or len(population)!=ledger['upstream_saved_events']:
        raise ValueError('Count mismatch')
    if not math.isclose(sum(e['inverse_probability'] for e in selected),ledger['sum_inverse_probability']):
        raise ValueError('Weight sum mismatch')
    if not math.isclose(sum(e['inverse_probability']**2 for e in selected),ledger['sum_squared_inverse_probability']):
        raise ValueError('Squared-weight sum mismatch')
    absent=ledger['upstream_not_saved_ids']
    all_ids=[tuple(e['id']) for e in ledger['rows']]+[tuple(e) for e in absent]
    expected={(parent['contract']['seed'],1,i) for i in range(1,ledger['framework_attempts']+1)}
    if len(all_ids)!=len(expected) or set(all_ids)!=expected or len(absent)!=ledger['upstream_not_saved_events']:
        raise ValueError('Framework-attempt identity partition mismatch')
    return dict(status='validated',framework_attempts=len(expected),saved=len(population),selected=len(selected))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('ledger',type=Path,nargs='+')
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    reports={str(path):validate(json.loads(path.read_text())) for path in args.ledger}
    args.output.write_text(json.dumps(reports,indent=2)+'\n')
    print(json.dumps(reports))


if __name__=='__main__':main()
