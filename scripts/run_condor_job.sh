#!/usr/bin/env bash
set -euo pipefail
: "${1:?job number must be passed as the first argument}"
CHUNK="$1"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKFLOW_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$WORKFLOW_ROOT/config/workflow.env"
mkdir -p "$STEP1_DIR" "$STEP2_DIR" "$STEP3_DIR" "$STEP4_DIR" "$LOG_DIR" \
	"$STEP1_CONFIG_DIR" "$STEP2_CONFIG_DIR" "$STEP3_CONFIG_DIR" "$STEP4_CONFIG_DIR"
"$WORKFLOW_ROOT/run_step1_generation.sh" "$CHUNK" "$N_EVENTS"
"$WORKFLOW_ROOT/run_step2_digi_raw.sh" "$CHUNK" "$N_EVENTS"
"$WORKFLOW_ROOT/run_step3_aod.sh" "$CHUNK" "$N_EVENTS"
"$WORKFLOW_ROOT/run_step4_exonanoAOD.sh" "$CHUNK" "$N_EVENTS"
