#!/usr/bin/env bash
# Condor transfers this small bootstrap; payload code/runtime are fetched once.
set -euo pipefail
bundle_url=${1:?bundle URL}
bundle_sha=${2:?bundle SHA256}
manifest_name=${3:?manifest filename}
row=${4:?manifest row}
job_id=${5:?Condor global job ID}
scratch=${_CONDOR_SCRATCH_DIR:-${RECOVERY_TEST_SCRATCH:-}}
[[ "$scratch" == /* && "$scratch" != /afs/* && "$scratch" != /eos/* ]] || exit 2
export PATH=/usr/bin:/bin
unset LD_LIBRARY_PATH PYTHONPATH ROOTSYS CMSSW_BASE CMSSW_SEARCH_PATH CMSSW_RELEASE_BASE
export PYTHONDONTWRITEBYTECODE=1
mkdir -p "$scratch/payload"
cd "$scratch/payload"
if [[ ! -f bundle.tar.gz ]]; then
  xrdcp --force --silent "$bundle_url" bundle.tar.gz.partial
  mv bundle.tar.gz.partial bundle.tar.gz
fi
printf '%s  bundle.tar.gz\n' "$bundle_sha" | sha256sum -c -
tar -xzf bundle.tar.gz
source /cvmfs/cms.cern.ch/cmsset_default.sh
cd CMSSW_17_0_0_pre4/src
scram b ProjectRename
eval "$(scram runtime -sh)"
export RECOVERY_JOB_ID="$job_id"
export RECOVERY_LOCAL_ROOT="$scratch/payload"
export CMSSW_SRC="$scratch/payload/CMSSW_17_0_0_pre4/src"
export TMPDIR="$scratch"
cd "$scratch/payload"
exec python3 workflow/scripts/run_recovery_manifest.py "$manifest_name" "$row"
