#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKFLOW_ROOT="$(cd "$SCRIPT_DIR" && pwd)"
CHUNK="${1:-0}"
source "$WORKFLOW_ROOT/scripts/setup_cmssw.sh"
N_EVENTS="${2:-$N_EVENTS}"
WORKDIR="${WORKDIR:-$SAMPLE_DIR}"
OUTPUT_DIR="$SAMPLE_DIR/samples/step3"
CONFIG_DIR="$SAMPLE_DIR/configs/step3"
LOG_DIR="$SAMPLE_DIR/logs"

GEOMETRY="DB:Extended"
ERA="Run3_2024"
CONDITIONS="auto:phase1_2024_realistic"

mkdir -p "$WORKDIR" "$OUTPUT_DIR" "$CONFIG_DIR" "$LOG_DIR"
cd "$WORKDIR"

INPUT="$SAMPLE_DIR/samples/step2/events_step2_part${PART}.root"
if [[ ! -s "$INPUT" ]]; then
	echo "ERROR: $WORKDIR/$INPUT is missing or empty" >&2
	exit 1
fi

echo "=== Step 3: RAW2DIGI,L1Reco,RECO,RECOSIM,PAT -> MiniAODSIM (Run 3) ==="
cmsDriver.py step3 \
	--step RAW2DIGI,L1Reco,RECO,RECOSIM,PAT \
	--conditions "$CONDITIONS" \
	--datatier MINIAODSIM \
	--eventcontent MINIAODSIM \
	--geometry "$GEOMETRY" \
	--era "$ERA" \
	--filein "file:$INPUT" \
	--fileout "file:$OUTPUT_DIR/events_step3_part${PART}.root" \
	--python_filename "$CONFIG_DIR/events_step3_part${PART}_cfg.py" \
	--no_exec \
	-n "$N_EVENTS"

cmsRun "$CONFIG_DIR/events_step3_part${PART}_cfg.py" 2>&1 | tee "$LOG_DIR/step3_events_part${PART}.log"
