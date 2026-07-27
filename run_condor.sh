#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/config/workflow.env"

if [[ ! "$N_JOBS" =~ ^[1-9][0-9]*$ ]]; then
	printf 'N_JOBS must be a positive integer (got: %s)\n' "$N_JOBS" >&2
	exit 1
fi

"$SCRIPT_DIR/scripts/prepare_condor.sh"

submit_file="$(mktemp "$SCRIPT_DIR/condor/shift_cmssw.XXXXXX.sub")"
trap 'rm -f "$submit_file"' EXIT
sed "s|<n_jobs>|$N_JOBS|g; s|<sample_dir>|$SAMPLE_DIR|g" \
	"$SCRIPT_DIR/condor/shift_cmssw.sub" > "$submit_file"
condor_submit "$submit_file"
