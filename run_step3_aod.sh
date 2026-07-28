#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/scripts/setup_cmssw.sh"
N_EVENTS="${2:-$N_EVENTS}"
OUTPUT_DIR="$SAMPLE_DIR/samples/step3"
CONFIG_DIR="$SAMPLE_DIR/configs/step3"
LOG_DIR="$SAMPLE_DIR/logs"
GEOMETRY="DB:Extended"; ERA="Run3_2024"; CONDITIONS="auto:phase1_2024_realistic"
mkdir -p "$SAMPLE_DIR" "$OUTPUT_DIR" "$CONFIG_DIR" "$LOG_DIR"
cd "${WORKDIR:-$SAMPLE_DIR}"
INPUT="$SAMPLE_DIR/samples/step2/events_step2_part${PART}.root"
[[ -s "$INPUT" ]] || { echo "ERROR: $INPUT is missing or empty" >&2; exit 1; }
echo "=== Step 3: RAW2DIGI,L1Reco,RECO,RECOSIM -> AODSIM ==="
cmsDriver.py step3 --step RAW2DIGI,L1Reco,RECO,RECOSIM --conditions "$CONDITIONS" \
  --datatier AODSIM --eventcontent AODSIM --geometry "$GEOMETRY" --era "$ERA" \
  --filein "file:$INPUT" --fileout "file:$OUTPUT_DIR/events_AOD_part${PART}.root" \
  --python_filename "$CONFIG_DIR/events_AOD_part${PART}_cfg.py" --no_exec -n "$N_EVENTS"
cmsRun "$CONFIG_DIR/events_AOD_part${PART}_cfg.py" 2>&1 | tee "$LOG_DIR/step3_events_AOD_part${PART}.log"
