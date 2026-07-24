#!/usr/bin/env bash
set -euo pipefail
: "${1:?job number must be passed as the first argument}"
CHUNK="$1"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKFLOW_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$WORKFLOW_ROOT/config/workflow.env"
mkdir -p "$SAMPLE_DIR/step1" "$SAMPLE_DIR/step2" "$SAMPLE_DIR/step3" "$SAMPLE_DIR/step4"
"$WORKFLOW_ROOT/run_step1_generation.sh" "$CHUNK" "$N_EVENTS"
"$WORKFLOW_ROOT/run_step2_digi_raw.sh" "$CHUNK" "$N_EVENTS"
"$WORKFLOW_ROOT/run_step3_reco_miniAOD.sh" "$CHUNK" "$N_EVENTS"
"$WORKFLOW_ROOT/run_step4_nanoAOD.sh" "$CHUNK" "$N_EVENTS"
