#!/usr/bin/env bash
set -euo pipefail
scripts_dir="${1:?absolute frozen scripts directory}"
shift
cmssw_src="${1:?CMSSW src}"
shift
source /cvmfs/cms.cern.ch/cmsset_default.sh
cd "$cmssw_src"
eval "$(scram runtime -sh)"
export MPLCONFIGDIR="${_CONDOR_SCRATCH_DIR:-/tmp/jniedzie}/matplotlib-lss-completion"
exec python3 "$scripts_dir/wait_for_lss_plots.py" "$@"
