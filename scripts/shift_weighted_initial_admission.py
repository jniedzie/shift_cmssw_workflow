#!/usr/bin/env python3
"""DAGMan deferred PRE barrier: no worker may start before owned registration."""
import json,sys
from pathlib import Path
base=Path(sys.argv[1]);expected=sys.argv[2]
try:
 marker=json.loads((base/'initial_admission_enabled.json').read_text())
 submission=json.loads((base/'submission.json').read_text())
except FileNotFoundError:sys.exit(100)
if marker.get('schema')!='shift-weighted-initial-admission-v1' or marker.get('manifest_sha256')!=expected or marker.get('controller_id')!=submission.get('controller_id') or marker.get('enabled') is not True:raise ValueError('Admission marker does not bind the registered controller/manifest')
sys.exit(0)
