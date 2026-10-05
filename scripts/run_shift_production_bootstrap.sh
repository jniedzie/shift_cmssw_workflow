#!/usr/bin/env bash
# Tiny transferred entrypoint; custom CMSSW and workflow are frozen in EOS.
set -eo pipefail
manifest_sha=${1:?manifest SHA256}
mode=${2:?mode}
item=${3:?manifest item}
scratch=${_CONDOR_SCRATCH_DIR:-${SHIFT_DAG_TEST_SCRATCH:-}}
[[ "$scratch" == /* && "$scratch" != /afs/* && "$scratch" != /eos/* ]] || exit 2
cd "$scratch"
printf '%s  manifest.json\n' "$manifest_sha" | sha256sum -c -
export PATH=/usr/bin:/bin
unset LD_LIBRARY_PATH PYTHONPATH ROOTSYS CMSSW_BASE CMSSW_SEARCH_PATH CMSSW_RELEASE_BASE
export PYTHONDONTWRITEBYTECODE=1
readarray -t bundle < <(python3 -c 'import json; m=json.load(open("manifest.json")); print(m["bundle_url"]); print(m["bundle_sha256"])')
xrdcp --silent "${bundle[0]}" payload.tar.gz
printf '%s  payload.tar.gz\n' "${bundle[1]}" | sha256sum -c -
mkdir payload
tar -xzf payload.tar.gz -C payload
source /cvmfs/cms.cern.ch/cmsset_default.sh
cd payload/CMSSW_17_0_0_pre4/src
scram b ProjectRename
eval "$(scram runtime -sh)"
# Keep the granular custom libraries consistent with the validated workflow.
export LD_LIBRARY_PATH="$(python3 -c 'import os; print(":".join(p for p in os.environ.get("LD_LIBRARY_PATH", "").split(":") if "/biglib/" not in p))')"
export CMSSW_SRC="$scratch/payload/CMSSW_17_0_0_pre4/src"
export SHIFT_DAG_LOCAL_ROOT="$scratch/payload"
export TMPDIR="$scratch"
cd "$scratch"
exec python3 payload/workflow/scripts/run_shift_production_node.py manifest.json "$mode" "$item"
