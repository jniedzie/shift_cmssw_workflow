#!/usr/bin/env python3
"""Bounded DAG PRE predicate: do not submit chunks outside frozen pilot budget."""
import hashlib,json,sys
from pathlib import Path
manifest,expected,node=sys.argv[1:]
if hashlib.sha256(Path(manifest).read_bytes()).hexdigest()!=expected:raise ValueError('Frozen manifest changed')
m=json.loads(Path(manifest).read_text());r=next(j for j in m['jobs'] if j['id']==node)
g=json.loads(Path('scientific_gate.json').read_text())
if g.get('schema')!='shift-weighted-high-bin-scientific-gate-v2' or not g.get('pass') or g.get('manifest_sha256')!=expected:raise ValueError('Scientific gate missing or changed')
limit=g['production_plan'][r['stratum']]['chunks']
if not isinstance(limit,int) or not 1<=limit<=m['max_chunks_per_stratum']:raise ValueError('Unbounded production allocation')
sys.exit(0 if r['chunk_index']<limit else 99)
