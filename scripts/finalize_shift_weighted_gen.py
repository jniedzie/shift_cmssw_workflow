#!/usr/bin/env python3
"""Close fixed-budget high production; never schedule adaptive statistics top-ups."""
import hashlib,json,math,sys
from pathlib import Path
from gate_shift_weighted_gen import aggregate
base=Path(sys.argv[1]);m=json.loads((base/'manifest.json').read_text());expected=hashlib.sha256((base/'manifest.json').read_bytes()).hexdigest();g=json.loads((base/'scientific_gate.json').read_text())
result=dict(schema='shift-weighted-high-bin-final-accounting-v1',manifest_sha256=expected,complete=False,physics_valid=False,normalization_ready=False,strata={},failures=[])
try:
 if not g.get('pass') or g.get('manifest_sha256')!=expected:raise ValueError('Scientific gate is not valid')
 ids=set()
 for stratum,allocation in g['production_plan'].items():
  rows=[r for r in m['jobs'] if r['stage']=='production' and r['stratum']==stratum and r['chunk_index']<allocation['chunks']];receipts=[]
  for row in rows:
   r=json.loads((base/'results'/f"{row['id']}.json").read_text())
   if not r.get('complete') or not r.get('generated_edm') or not r.get('publication_complete') or r.get('manifest_sha256')!=expected or r.get('request')!=row or r.get('algorithm')!=m['algorithm']:raise ValueError('Invalid completed production receipt: '+row['id'])
   for eventid in r['semantic_audit']['event_ids']:
    key=tuple(eventid)
    if key in ids:raise ValueError('Duplicate accepted event identity across strata/chunks')
    ids.add(key)
   receipts.append(r)
  summary=aggregate(receipts)
  if summary['trials']!=allocation['trials']:raise ValueError('Fixed trial budget incomplete: '+stratum)
  summary.update(target_effective_events=allocation['effective_event_target'],effective_target_met=summary['ess']>=allocation['effective_event_target'],chunks=len(receipts),normalization='sigmaND*sumW/Ntrials; per-event pbweight=sigmaND*1e9*W/Ntrials',pilots_excluded=True)
  result['strata'][stratum]=summary
  if not summary['effective_target_met']:result['failures'].append('Fixed-budget effective-statistics target unmet: '+stratum)
 result['events']=len(ids);result['complete']=not result['failures']
except Exception as error:result['failures'].append(repr(error))
tmp=base/'production_complete.json.tmp';tmp.write_text(json.dumps(result,indent=2)+'\n');tmp.replace(base/'production_complete.json');print(json.dumps(result,indent=2));sys.exit(0 if result['complete'] else 1)
