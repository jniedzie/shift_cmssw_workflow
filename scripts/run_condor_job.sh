#!/usr/bin/env bash
set -euo pipefail
: "${1:?job number must be passed as the first argument}"
CHUNK="$1"
: "${2:?absolute workflow root must be passed as the second argument}"
WORKFLOW_ROOT="$2"
: "${3:?comma-separated workflow steps must be passed as the third argument}"
SELECTED_STEPS="$3"
: "${4:?force flag must be passed as the fourth argument}"
FORCE_SELECTED="$4"
: "${5:?process name must be passed as the fifth argument}"
PROCESS="$5"
: "${6:?sample name must be passed as the sixth argument}"
SAMPLE_NAME="$6"
: "${7:?campaign name must be passed as the seventh argument}"
CAMPAIGN_NAME="$7"
: "${8:?sample base must be passed as the eighth argument}"
SAMPLE_BASE="$8"
: "${9:?sample directory must be passed as the ninth argument}"
SAMPLE_DIR="$9"
: "${10:?event count must be passed as the tenth argument}"
N_EVENTS="${10}"
export PROCESS SAMPLE_NAME CAMPAIGN_NAME SAMPLE_BASE SAMPLE_DIR N_EVENTS
if [[ "$WORKFLOW_ROOT" != /* ]]; then
	echo "ERROR [run_condor_job]: workflow root is not absolute: $WORKFLOW_ROOT" >&2
	exit 1
fi
if [[ ! -r "$WORKFLOW_ROOT/config/workflow.env" ]]; then
	echo "ERROR [run_condor_job]: workflow is not accessible on this worker: $WORKFLOW_ROOT" >&2
	exit 1
fi
source "$WORKFLOW_ROOT/config/workflow.env"
mkdir -p "$STEP1_DIR" "$STEP2_DIR" "$STEP3_DIR" "$STEP4_DIR" "$LOG_DIR" \
	"$STEP1_CONFIG_DIR" "$STEP2_CONFIG_DIR" "$STEP3_CONFIG_DIR" "$STEP4_CONFIG_DIR"

[[ "$FORCE_SELECTED" == 0 || "$FORCE_SELECTED" == 1 ]] || {
	echo "ERROR [run_condor_job]: force flag must be 0 or 1 (got '$FORCE_SELECTED')" >&2
	exit 2
}
declare -A RUN_STEP=()
IFS=',' read -r -a requested_steps <<< "$SELECTED_STEPS"
for step in "${requested_steps[@]}"; do
	[[ "$step" =~ ^[1-4]$ ]] || {
		echo "ERROR [run_condor_job]: invalid selected step '$step'" >&2
		exit 2
	}
	RUN_STEP["$step"]=1
done

run_step() {
	local step="$1"
	local script="$2"
	[[ -n "${RUN_STEP[$step]:-}" ]] || return 0
	local arguments=()
	[[ "$FORCE_SELECTED" == 1 ]] && arguments+=(--force)
	arguments+=("$CHUNK" "$N_EVENTS")
	echo "=== Condor chunk $CHUNK: running step $step (force=$FORCE_SELECTED) ==="
	"$WORKFLOW_ROOT/$script" "${arguments[@]}"
}

run_step 1 run_step1_generation.sh
run_step 2 run_step2_digi_raw.sh
run_step 3 run_step3_aod.sh
run_step 4 run_step4_exonanoAOD.sh
