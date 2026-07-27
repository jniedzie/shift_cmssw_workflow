#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKFLOW_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$WORKFLOW_ROOT/config/workflow.env"

: "${CHUNK:=0}"
if [[ ! "$CHUNK" =~ ^[0-9]+$ ]]; then
	echo "ERROR: CHUNK must be a non-negative integer (got '$CHUNK')" >&2
	exit 1
fi
PART="$(printf '%04d' "$CHUNK")"

cd "$CMSSW_SRC"
cmsenv
cd "$WORKFLOW_ROOT"

mkdir -p "$SAMPLE_DIR/samples/step1" "$SAMPLE_DIR/samples/step2" "$SAMPLE_DIR/samples/step3" "$SAMPLE_DIR/samples/step4" \
	"$SAMPLE_DIR/logs" \
	"$SAMPLE_DIR/configs/step1" "$SAMPLE_DIR/configs/step2" "$SAMPLE_DIR/configs/step3" "$SAMPLE_DIR/configs/step4"


export WORKFLOW_ROOT CMSSW_SRC SAMPLE_BASE SAMPLE_NAME CAMPAIGN_NAME SAMPLE_DIR PART
