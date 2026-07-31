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

# dCache/PNFS can reject cmsDriver's in-place configuration-file creation.
# Generate and run locally, then retain a campaign snapshot when possible.
LOCAL_STEP3_DIR="$(mktemp -d /tmp/shift_cmssw_step3_XXXXXX)"
cleanup_step3_tmp() {
  local status=$?
  if [[ "$status" -eq 0 && "${KEEP_STEP3_TMP:-0}" != 1 ]]; then
    rm -rf "$LOCAL_STEP3_DIR"
  else
    echo "Step 3 temporary files retained in $LOCAL_STEP3_DIR" >&2
  fi
}
trap cleanup_step3_tmp EXIT
LOCAL_CONFIG="$LOCAL_STEP3_DIR/events_AOD_part${PART}_cfg.py"
LOCAL_LOG="$LOCAL_STEP3_DIR/step3_events_AOD_part${PART}.log"

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
  --python_filename "$LOCAL_CONFIG" --no_exec -n "$N_EVENTS"

CONFIG_SNAPSHOT="$CONFIG_DIR/events_AOD_part${PART}_cfg.py"
if ! cp "$LOCAL_CONFIG" "$CONFIG_SNAPSHOT"; then
  echo "WARNING: could not archive Step 3 config at $CONFIG_SNAPSHOT; continuing with local config" >&2
fi

cmsRun "$LOCAL_CONFIG" 2>&1 | tee "$LOCAL_LOG"

LOG_SNAPSHOT="$LOG_DIR/step3_events_AOD_part${PART}.log"
if ! cp "$LOCAL_LOG" "$LOG_SNAPSHOT"; then
  echo "WARNING: could not archive Step 3 log at $LOG_SNAPSHOT" >&2
fi
