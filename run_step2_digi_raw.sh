#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKFLOW_ROOT="$(cd "$SCRIPT_DIR" && pwd)"
FORCE=0
POSITIONAL_ARGS=()
for argument in "$@"; do
	case "$argument" in
		-f|--force) FORCE=1 ;;
		-h|--help) echo "Usage: $(basename "$0") [--force] [chunk [events]]"; exit 0 ;;
		-*) echo "ERROR: unknown option: $argument" >&2; exit 2 ;;
		*) POSITIONAL_ARGS+=("$argument") ;;
	esac
done
if (( ${#POSITIONAL_ARGS[@]} > 2 )); then
	echo "ERROR: expected at most chunk and event-count arguments" >&2
	exit 2
fi
CHUNK="${POSITIONAL_ARGS[0]:-0}"
source "$WORKFLOW_ROOT/scripts/setup_cmssw.sh"
N_EVENTS="${POSITIONAL_ARGS[1]:-$N_EVENTS}"
WORKDIR="${WORKDIR:-$SAMPLE_DIR}"
OUTPUT_DIR="$STEP2_DIR"
CONFIG_DIR="$STEP2_CONFIG_DIR"

mkdir -p "$WORKDIR" "$OUTPUT_DIR" "$CONFIG_DIR" "$LOG_DIR"
cd "$WORKDIR"

# dCache/PNFS can reject cmsDriver's in-place configuration-file creation.
# Generate and run locally, then retain a campaign snapshot when possible.
LOCAL_STEP2_DIR="$(mktemp -d /tmp/shift_cmssw_step2_XXXXXX)"
cleanup_step2_tmp() {
	local status=$?
	if [[ "$status" -eq 0 && "${KEEP_STEP2_TMP:-0}" != 1 ]]; then
		rm -rf "$LOCAL_STEP2_DIR"
	else
		echo "Step 2 temporary files retained in $LOCAL_STEP2_DIR" >&2
	fi
}
trap cleanup_step2_tmp EXIT
LOCAL_CONFIG="$LOCAL_STEP2_DIR/events_step2_part${PART}_cfg.py"
LOCAL_LOG="$LOCAL_STEP2_DIR/step2_events_part${PART}.log"
LOCAL_OUTPUT="$LOCAL_STEP2_DIR/events_step2_part${PART}.root"

OUTPUT="$OUTPUT_DIR/events_step2_part${PART}.root"
if [[ "$FORCE" -eq 1 && -e "$OUTPUT" ]]; then
	echo "Force rerun requested; removing existing Step 2 output: $OUTPUT"
	rm -f -- "$OUTPUT"
fi
if output_is_valid "$OUTPUT"; then
	echo "Step 2 output already exists and is valid: $OUTPUT"
	exit 0
fi
if [[ -e "$OUTPUT" ]]; then
	echo "Removing invalid Step 2 output before retry: $OUTPUT"
	rm -f -- "$OUTPUT"
fi

INPUT="$STEP1_DIR/events_step1_part${PART}.root"
if [[ ! -s "$INPUT" ]]; then
	echo "ERROR: $WORKDIR/$INPUT is missing or empty" >&2
	exit 1
fi

echo "=== Step 2: DIGI,L1,DIGI2RAW,HLT (Run 3) ==="
cmsDriver.py step2 \
	--step DIGI:pdigi_valid,L1,DIGI2RAW,HLT:@relval2024,ENDJOB \
	--conditions "$CONDITIONS" \
	--datatier GEN-SIM-DIGI-RAW \
	--eventcontent FEVTDEBUGHLT \
	--geometry "$GEOMETRY" \
	--era "$ERA" \
	--filein "file:$INPUT" \
	--fileout "file:$LOCAL_OUTPUT" \
	--python_filename "$LOCAL_CONFIG" \
	--customise_commands "from PhysicsTools.ShiftMuonSegments.shiftMuonSegments_customise import customiseKeepShiftTruth; process = customiseKeepShiftTruth(process)" \
	--no_exec \
	-n "$N_EVENTS"

CONFIG_SNAPSHOT="$CONFIG_DIR/events_step2_part${PART}_cfg.py"
if ! cp "$LOCAL_CONFIG" "$CONFIG_SNAPSHOT"; then
	echo "WARNING: could not archive Step 2 config at $CONFIG_SNAPSHOT; continuing with local config" >&2
fi

cmsRun "$LOCAL_CONFIG" 2>&1 | tee "$LOCAL_LOG"

if ! output_is_valid "$LOCAL_OUTPUT"; then
	echo "ERROR: Step 2 cmsRun returned successfully but did not produce a valid local output: $LOCAL_OUTPUT" >&2
	exit 1
fi
stage_cmssw_output "$LOCAL_OUTPUT" "$OUTPUT"

LOG_SNAPSHOT="$LOG_DIR/step2_events_part${PART}.log"
if ! cp "$LOCAL_LOG" "$LOG_SNAPSHOT"; then
	echo "WARNING: could not archive Step 2 log at $LOG_SNAPSHOT" >&2
fi
