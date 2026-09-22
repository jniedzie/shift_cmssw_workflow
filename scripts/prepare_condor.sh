#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKFLOW_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$WORKFLOW_ROOT/config/workflow.env"
mkdir -p "$STEP1_DIR" "$STEP2_DIR" "$STEP3_DIR" "$STEP4_DIR" "$LOG_DIR" \
	"$STEP1_CONFIG_DIR" "$STEP2_CONFIG_DIR" "$STEP3_CONFIG_DIR" "$STEP4_CONFIG_DIR"
if [[ "$CLEANUP_PREVIOUS_STEP" == 1 ]]; then
	# Create shared parents once before workers race to publish their chunks.
	mkdir -p "$SAMPLE_DIR/generation_metadata" "$SAMPLE_DIR/chain_metadata"
fi

printf 'Condor directories ready for %s\n' "$SAMPLE_DIR"
