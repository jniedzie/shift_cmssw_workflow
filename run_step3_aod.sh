#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHUNK="${1:-0}"
source "$SCRIPT_DIR/scripts/setup_cmssw.sh"
N_EVENTS="${2:-$N_EVENTS}"
OUTPUT_DIR="$STEP3_DIR"
CONFIG_DIR="$STEP3_CONFIG_DIR"
GEOMETRY="DB:Extended"; ERA="Run3_2024"; CONDITIONS="auto:phase1_2024_realistic"
mkdir -p "$SAMPLE_DIR" "$OUTPUT_DIR" "$CONFIG_DIR" "$LOG_DIR"
cd "${WORKDIR:-$SAMPLE_DIR}"
OUTPUT="$OUTPUT_DIR/events_AOD_part${PART}.root"
if output_is_valid "$OUTPUT"; then
	echo "Step 3 output already exists and is valid: $OUTPUT"
	exit 0
fi
INPUT="$STEP2_DIR/events_step2_part${PART}.root"
[[ -s "$INPUT" ]] || { echo "ERROR: $INPUT is missing or empty" >&2; exit 1; }
echo "=== Step 3: RAW2DIGI,L1Reco,RECO,RECOSIM -> AODSIM ==="
cmsDriver.py step3 --step RAW2DIGI,L1Reco,RECO,RECOSIM --conditions "$CONDITIONS" \
  --datatier AODSIM --eventcontent AODSIM --geometry "$GEOMETRY" --era "$ERA" \
  --filein "file:$INPUT" --fileout "file:$OUTPUT" \
  --python_filename "$CONFIG_DIR/events_AOD_part${PART}_cfg.py" --no_exec -n "$N_EVENTS"
cmsRun "$CONFIG_DIR/events_AOD_part${PART}_cfg.py" 2>&1 | tee "$LOG_DIR/step3_events_AOD_part${PART}.log"
