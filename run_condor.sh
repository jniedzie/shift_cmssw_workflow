#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
	cat <<EOF
Usage: $(basename "$0") [--steps LIST] [--force]
       $(basename "$0") --force-steps LIST

Submit all configured jobs, running only the selected workflow steps.
LIST is a comma-separated subset of 1,2,3,4 ("step1" ... "step4" are also
accepted).  With no options, all steps run and existing valid outputs are
reused.  --force removes and recreates outputs for every selected step;
--force-steps LIST is shorthand for --steps LIST --force.

Examples:
  $(basename "$0") --steps 3,4
  $(basename "$0") --force-steps 4
EOF
}

SELECTED_STEPS="1,2,3,4"
FORCE_SELECTED=0
while (( $# )); do
	case "$1" in
		--steps)
			[[ $# -ge 2 ]] || { echo "ERROR: --steps requires a list" >&2; exit 2; }
			SELECTED_STEPS="$2"
			shift 2
			;;
		--force-steps)
			[[ $# -ge 2 ]] || { echo "ERROR: --force-steps requires a list" >&2; exit 2; }
			SELECTED_STEPS="$2"
			FORCE_SELECTED=1
			shift 2
			;;
		-f|--force)
			FORCE_SELECTED=1
			shift
			;;
		-h|--help)
			usage
			exit 0
			;;
		*)
			echo "ERROR: unknown option: $1" >&2
			usage >&2
			exit 2
			;;
	esac
done

NORMALIZED_STEPS=""
IFS=',' read -r -a requested_steps <<< "$SELECTED_STEPS"
for requested_step in "${requested_steps[@]}"; do
	case "$requested_step" in
		1|step1) step=1 ;;
		2|step2) step=2 ;;
		3|step3) step=3 ;;
		4|step4) step=4 ;;
		*) echo "ERROR: invalid step '$requested_step'; expected a comma-separated subset of 1,2,3,4" >&2; exit 2 ;;
	esac
	if [[ ",$NORMALIZED_STEPS," != *",$step,"* ]]; then
		NORMALIZED_STEPS="${NORMALIZED_STEPS:+$NORMALIZED_STEPS,}$step"
	fi
done
[[ -n "$NORMALIZED_STEPS" ]] || { echo "ERROR: no workflow steps selected" >&2; exit 2; }

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
sed "s|<n_jobs>|$N_JOBS|g; s|<log_dir>|$CONDOR_LOG_DIR|g; s|<workflow_root>|$WORKFLOW_ROOT|g; s|<selected_steps>|$NORMALIZED_STEPS|g; s|<force_selected>|$FORCE_SELECTED|g" \
	"$WORKFLOW_ROOT/condor/shift_cmssw.sub" > "$submit_file"
printf 'Submitting %s jobs for step(s) %s (force=%s)\n' "$N_JOBS" "$NORMALIZED_STEPS" "$FORCE_SELECTED"
condor_submit "$submit_file"
