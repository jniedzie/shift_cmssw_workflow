#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/config/workflow.env"

if [[ ! "$N_JOBS" =~ ^[1-9][0-9]*$ ]]; then
	printf 'N_JOBS must be a positive integer (got: %s)\n' "$N_JOBS" >&2
	exit 1
fi

"$SCRIPT_DIR/scripts/prepare_condor.sh"

# Standard CERN batch schedds reject /eos paths in submit-file attributes
# such as output, error, and log.  Keep Condor's own bookkeeping logs on the
# shared AFS workflow area; the job payload continues to write CMSSW logs and
# ROOT outputs to the campaign directory on EOS.
CONDOR_LOG_DIR="$SCRIPT_DIR/condor/logs/$CAMPAIGN_NAME"
mkdir -p "$CONDOR_LOG_DIR"

# Build and register the CMSSW packages once.  Do not inherit a stale
# CMSSW_PREPARED value from the caller: this submission-side setup must verify
# the release before Condor jobs are allowed to skip the build.
unset CMSSW_PREPARED
source "$SCRIPT_DIR/scripts/setup_cmssw.sh"
export CMSSW_PREPARED=1

submit_file="$(mktemp "$WORKFLOW_ROOT/condor/shift_cmssw.XXXXXX.sub")"
trap 'rm -f "$submit_file"' EXIT
sed "s|<n_jobs>|$N_JOBS|g; s|<log_dir>|$CONDOR_LOG_DIR|g; s|<workflow_root>|$WORKFLOW_ROOT|g" \
	"$WORKFLOW_ROOT/condor/shift_cmssw.sub" > "$submit_file"
condor_submit "$submit_file"
