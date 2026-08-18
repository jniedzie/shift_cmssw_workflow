#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
	cat <<EOF
Usage: $(basename "$0") [--steps LIST] [--force] [--prebuilt] [--keep-logs]
       $(basename "$0") --force-steps LIST [--prebuilt] [--keep-logs]

Submit all configured jobs, running only the selected workflow steps.
LIST is a comma-separated subset of 1,2,3,4 ("step1" ... "step4" are also
accepted).  With no options, all steps run and existing valid outputs are
reused.  --force removes and recreates outputs for every selected step;
--force-steps LIST is shorthand for --steps LIST --force.
--prebuilt skips the submission-side SCRAM build after one explicit successful
build, allowing several configuration-only scans to share the same libraries.
Before submitting, logs from completed older jobs are removed from the local
Condor log directory and, when no older workflow jobs are active, from EOS.
--keep-logs disables this automatic cleanup.

Examples:
  $(basename "$0") --steps 3,4
  $(basename "$0") --force-steps 4
EOF
}

SELECTED_STEPS="1,2,3,4"
FORCE_SELECTED=0
USE_PREBUILT=0
KEEP_LOGS=0
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
		--prebuilt)
			USE_PREBUILT=1
			shift
			;;
		--keep-logs)
			KEEP_LOGS=1
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

# The submit description has an explicit `environment` attribute, so the
# canonical reconstruction settings must be serialized rather than inherited.
SUBMISSION_RECO_VARIABLES=(
	SHIFT_DT_MODE
	SHIFT_TRACKER_MODE
	SHIFT_ENABLE_GEM
	SHIFT_ENABLE_HCAL_DIAGNOSTICS
	SHIFT_ENABLE_ZDC_DIAGNOSTICS
	SHIFT_AUGMENT_DT_HITS
	SHIFT_AUGMENT_TRACKER_HITS
	SHIFT_USE_EXTENDED_TIMING
	SHIFT_REFIT_SECOND_SEED_ERROR_RESCALE
	SHIFT_REFIT_USE_SECOND_ITERATION
	SHIFT_REFIT_GEOMETRY_TARGET_MATERIAL
)
for variable_name in "${SUBMISSION_RECO_VARIABLES[@]}"; do
	export "$variable_name"
done

if [[ ! "$N_JOBS" =~ ^[1-9][0-9]*$ ]]; then
	printf 'N_JOBS must be a positive integer (got: %s)\n' "$N_JOBS" >&2
	exit 1
fi
if [[ ! "$STEP4_INPUTS_PER_JOB" =~ ^[1-9][0-9]*$ ]]; then
	printf 'STEP4_INPUTS_PER_JOB must be a positive integer (got: %s)\n' "$STEP4_INPUTS_PER_JOB" >&2
	exit 1
fi
if [[ "$STEP4_INPUTS_PER_JOB" != 1 && "$NORMALIZED_STEPS" != 4 ]]; then
	echo "STEP4_INPUTS_PER_JOB > 1 is only safe for a Step-4-only submission" >&2
	exit 1
fi
for variable_name in CONDOR_REQUEST_CPUS CONDOR_REQUEST_MEMORY_MB CONDOR_MAX_MATERIALIZE; do
	variable_value="${!variable_name}"
	if [[ ! "$variable_value" =~ ^[1-9][0-9]*$ ]]; then
		printf '%s must be a positive integer (got: %s)\n' "$variable_name" "$variable_value" >&2
		exit 1
	fi
done
for refit_variable in SHIFT_REFIT_SEED_MOMENTUM_SCALE SHIFT_REFIT_ENERGY_LOSS_SCALE; do
	refit_value="${!refit_variable}"
	if [[ ! "$refit_value" =~ ^[0-9]+([.][0-9]+)?$ ]] || [[ "$refit_value" =~ ^0+([.]0+)?$ ]]; then
		printf '%s must be a positive decimal (got: %s)\n' "$refit_variable" "$refit_value" >&2
		exit 1
	fi
done
case "$SHIFT_REFIT_DETAILED_MATERIAL_EFFECTS" in
	0|1) ;;
	*) printf 'SHIFT_REFIT_DETAILED_MATERIAL_EFFECTS must be 0 or 1 (got: %s)\n' "$SHIFT_REFIT_DETAILED_MATERIAL_EFFECTS" >&2; exit 1 ;;
esac
case "$SHIFT_REFIT_GEOMETRY_MATERIAL_EFFECTS" in
	0|1) ;;
	*) printf 'SHIFT_REFIT_GEOMETRY_MATERIAL_EFFECTS must be 0 or 1 (got: %s)\n' "$SHIFT_REFIT_GEOMETRY_MATERIAL_EFFECTS" >&2; exit 1 ;;
esac
for split_geometry_variable in SHIFT_REFIT_GEOMETRY_MATERIAL_FITTER SHIFT_REFIT_GEOMETRY_MATERIAL_SMOOTHER SHIFT_REFIT_LOG_GEOMETRY_COMPARISON; do
	case "${!split_geometry_variable}" in
		0|1) ;;
		*) printf '%s must be 0 or 1 (got: %s)\n' "$split_geometry_variable" "${!split_geometry_variable}" >&2; exit 1 ;;
	esac
done
if [[ "$SHIFT_REFIT_DETAILED_MATERIAL_EFFECTS" == 1 &&
      ( "$SHIFT_REFIT_GEOMETRY_MATERIAL_EFFECTS" == 1 || "$SHIFT_REFIT_GEOMETRY_MATERIAL_FITTER" == 1 || "$SHIFT_REFIT_GEOMETRY_MATERIAL_SMOOTHER" == 1 ) ]]; then
	echo "Detailed refit material and geometry refit material modes are mutually exclusive" >&2
	exit 1
fi
if [[ "$SHIFT_REFIT_LOG_GEOMETRY_COMPARISON" == 1 && "$SHIFT_REFIT_GEOMETRY_MATERIAL_EFFECTS" == 0 && "$SHIFT_REFIT_GEOMETRY_MATERIAL_FITTER" == 0 && "$SHIFT_REFIT_GEOMETRY_MATERIAL_SMOOTHER" == 0 ]]; then
	echo "SHIFT_REFIT_LOG_GEOMETRY_COMPARISON requires a geometry material mode" >&2
	exit 1
fi

"$SCRIPT_DIR/scripts/prepare_condor.sh"

# Standard CERN batch schedds reject /eos paths in submit-file attributes
# such as output, error, and log.  Keep Condor's own bookkeeping logs on the
# shared AFS workflow area; the job payload continues to write CMSSW logs and
# ROOT outputs to the campaign directory on EOS.
CONDOR_LOG_DIR="$SCRIPT_DIR/condor/logs/$CAMPAIGN_NAME"
mkdir -p "$CONDOR_LOG_DIR"

# Build and register CMSSW once, then let explicit configuration-only scans
# reuse that release without relinking libraries between concurrent clusters.
if [[ "$USE_PREBUILT" == 1 ]]; then
	export CMSSW_PREPARED=1
	source "$SCRIPT_DIR/scripts/setup_cmssw.sh"
else
	unset CMSSW_PREPARED
	source "$SCRIPT_DIR/scripts/setup_cmssw.sh"
	export CMSSW_PREPARED=1
fi
CMSSW_RUNTIME_FINGERPRINT="$(cmssw_runtime_fingerprint)"
[[ -n "$CMSSW_RUNTIME_FINGERPRINT" ]] || {
	echo "ERROR: failed to fingerprint the built CMSSW runtime" >&2
	exit 1
}

submit_file="$(mktemp "$WORKFLOW_ROOT/condor/shift_cmssw.XXXXXX.sub")"
trap 'rm -f "$submit_file"' EXIT
sed "s|<n_jobs>|$N_JOBS|g; s|<request_cpus>|$CONDOR_REQUEST_CPUS|g; s|<request_memory_mb>|$CONDOR_REQUEST_MEMORY_MB|g; s|<max_materialize>|$CONDOR_MAX_MATERIALIZE|g; s|<seed_momentum_scale>|$SHIFT_REFIT_SEED_MOMENTUM_SCALE|g; s|<energy_loss_scale>|$SHIFT_REFIT_ENERGY_LOSS_SCALE|g; s|<detailed_material_effects>|$SHIFT_REFIT_DETAILED_MATERIAL_EFFECTS|g; s|<geometry_material_effects>|$SHIFT_REFIT_GEOMETRY_MATERIAL_EFFECTS|g; s|<geometry_material_fitter>|$SHIFT_REFIT_GEOMETRY_MATERIAL_FITTER|g; s|<geometry_material_smoother>|$SHIFT_REFIT_GEOMETRY_MATERIAL_SMOOTHER|g; s|<log_geometry_comparison>|$SHIFT_REFIT_LOG_GEOMETRY_COMPARISON|g; s|<step4_inputs_per_job>|$STEP4_INPUTS_PER_JOB|g; s|<cmssw_runtime_fingerprint>|$CMSSW_RUNTIME_FINGERPRINT|g; s|<log_dir>|$CONDOR_LOG_DIR|g; s|<workflow_root>|$WORKFLOW_ROOT|g; s|<selected_steps>|$NORMALIZED_STEPS|g; s|<force_selected>|$FORCE_SELECTED|g" \
	"$WORKFLOW_ROOT/condor/shift_cmssw.sub" > "$submit_file"
printf 'Submitting %s jobs for step(s) %s (force=%s, Step-4 inputs/job=%s, seed scale=%s, energy-loss scale=%s, detailed=%s, geometry both/fitter/smoother=%s/%s/%s, CPUs=%s, memory=%s MB, max materialized=%s)\n' \
	"$N_JOBS" "$NORMALIZED_STEPS" "$FORCE_SELECTED" "$STEP4_INPUTS_PER_JOB" "$SHIFT_REFIT_SEED_MOMENTUM_SCALE" "$SHIFT_REFIT_ENERGY_LOSS_SCALE" "$SHIFT_REFIT_DETAILED_MATERIAL_EFFECTS" "$SHIFT_REFIT_GEOMETRY_MATERIAL_EFFECTS" "$SHIFT_REFIT_GEOMETRY_MATERIAL_FITTER" "$SHIFT_REFIT_GEOMETRY_MATERIAL_SMOOTHER" "$CONDOR_REQUEST_CPUS" \
	"$CONDOR_REQUEST_MEMORY_MB" "$CONDOR_MAX_MATERIALIZE"
printf 'Reconstruction: DT=%s, tracker=%s, GEM/HCAL/ZDC=%s/%s/%s, augment DT/tracker=%s/%s, extended timing=%s\n' \
	"$SHIFT_DT_MODE" "$SHIFT_TRACKER_MODE" "$SHIFT_ENABLE_GEM" "$SHIFT_ENABLE_HCAL_DIAGNOSTICS" "$SHIFT_ENABLE_ZDC_DIAGNOSTICS" \
	"$SHIFT_AUGMENT_DT_HITS" "$SHIFT_AUGMENT_TRACKER_HITS" "$SHIFT_USE_EXTENDED_TIMING"
if [[ "$KEEP_LOGS" == 0 ]]; then
	echo "Cleaning old Condor and payload logs before submission..."
	"$WORKFLOW_ROOT/scripts/cleanup_condor_logs.sh" \
		"$CONDOR_LOG_DIR" "$LOG_DIR" "$WORKFLOW_ROOT/scripts/run_condor_job.sh"
else
	echo "Keeping logs from older jobs (--keep-logs)"
fi
condor_submit "$submit_file"
