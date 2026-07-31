#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKFLOW_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$WORKFLOW_ROOT/config/workflow.env"
mkdir -p "$STEP1_DIR" "$STEP2_DIR" "$STEP3_DIR" "$STEP4_DIR" "$LOG_DIR" \
	"$STEP1_CONFIG_DIR" "$STEP2_CONFIG_DIR" "$STEP3_CONFIG_DIR" "$STEP4_CONFIG_DIR"

printf 'Condor directories ready for %s\n' "$SAMPLE_DIR"
