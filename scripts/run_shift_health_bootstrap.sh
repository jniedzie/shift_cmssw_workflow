#!/usr/bin/env bash
set -eo pipefail
scratch=${_CONDOR_SCRATCH_DIR:?Condor scratch required}
cd "$scratch"
export PATH=/usr/bin:/bin
unset LD_LIBRARY_PATH PYTHONPATH ROOTSYS CMSSW_BASE CMSSW_SEARCH_PATH CMSSW_RELEASE_BASE
export PYTHONDONTWRITEBYTECODE=1
python3 -c 'import hashlib,json; r=json.load(open("health_request.json")); assert hashlib.sha256(open("health_audit.py","rb").read()).hexdigest()==r["auditor_sha256"]'
python3 -c 'import hashlib,json; r=json.load(open("health_request.json")); assert hashlib.sha256(open("shift_condor_native.py","rb").read()).hexdigest()==r["condor_helper_sha256"]'
python3 -c 'import hashlib,json; r=json.load(open("health_request.json")); assert all(hashlib.sha256(open(n,"rb").read()).hexdigest()==s for n,s in r["audit_helper_sha256"].items())'
readarray -t bundle < <(python3 -c 'import json; m=json.load(open("health_request.json")); print(m["bundle_url"]); print(m["bundle_sha256"])')
xrdcp --silent "${bundle[0]}" payload.tar.gz
printf '%s  payload.tar.gz\n' "${bundle[1]}" | sha256sum -c -
mkdir payload
tar -xzf payload.tar.gz -C payload
source /cvmfs/cms.cern.ch/cmsset_default.sh
cd payload/CMSSW_17_0_0_pre4/src
scram b ProjectRename
eval "$(scram runtime -sh)"
export LD_LIBRARY_PATH="$(python3 -c 'import os; print(":".join(p for p in os.environ.get("LD_LIBRARY_PATH", "").split(":") if "/biglib/" not in p))')"
cd "$scratch"
exec python3 health_audit.py health_request.json
