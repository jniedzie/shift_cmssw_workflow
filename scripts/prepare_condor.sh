#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKFLOW_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$WORKFLOW_ROOT/config/workflow.env"
mkdir -p "$SAMPLE_DIR/samples/step1" "$SAMPLE_DIR/samples/step2" "$SAMPLE_DIR/samples/step3" "$SAMPLE_DIR/samples/step4" \
	"$SAMPLE_DIR/logs/step1" "$SAMPLE_DIR/logs/step2" "$SAMPLE_DIR/logs/step3" "$SAMPLE_DIR/logs/step4" \
	"$SAMPLE_DIR/configs/step1" "$SAMPLE_DIR/configs/step2" "$SAMPLE_DIR/configs/step3" "$SAMPLE_DIR/configs/step4" \
	"$SAMPLE_DIR/condor/logs" "$WORKFLOW_ROOT/condor/logs"
printf 'Condor directories ready for %s\n' "$SAMPLE_DIR"
