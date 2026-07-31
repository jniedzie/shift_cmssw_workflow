#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKFLOW_ROOT="$(cd "$SCRIPT_DIR" && pwd)"
CHUNK="${1:-0}"
source "$WORKFLOW_ROOT/scripts/setup_cmssw.sh"
N_EVENTS="${2:-$N_EVENTS}"
WORKDIR="${WORKDIR:-$SAMPLE_DIR}"
OUTPUT_DIR="$STEP4_DIR"
CONFIG_DIR="$STEP4_CONFIG_DIR"

GEOMETRY="DB:Extended"
# Keep these settings local to this stage: the preceding stages produce the
# AOD input, while this stage follows the Run 3 / 2025 NanoAOD recipe.
ERA="Run3,Run3_2025"
CONDITIONS="auto:phase1_2025_realistic"
N_THREADS="${N_THREADS:-4}"

case "${ENABLE_EXONANOAOD:-1}" in
	1|true|True)
		NANO_STEP="PAT,NANO:@EXO"
		OUTPUT_LABEL="EXONanoAOD"
		;;
	0|false|False)
		NANO_STEP="PAT,NANO"
		OUTPUT_LABEL="NanoAOD"
		;;
	*)
		echo "ERROR: ENABLE_EXONANOAOD must be 0/1 or false/true" >&2
		exit 1
		;;
esac

mkdir -p "$WORKDIR" "$OUTPUT_DIR" "$CONFIG_DIR" "$LOG_DIR"
cd "$WORKDIR"

# dCache/PNFS can reject cmsDriver's in-place configuration-file creation.
# Generate and run locally, then retain a campaign snapshot when possible.
LOCAL_STEP4_DIR="$(mktemp -d /tmp/shift_cmssw_step4_XXXXXX)"
cleanup_step4_tmp() {
	local status=$?
	if [[ "$status" -eq 0 && "${KEEP_STEP4_TMP:-0}" != 1 ]]; then
		rm -rf "$LOCAL_STEP4_DIR"
	else
		echo "Step 4 temporary files retained in $LOCAL_STEP4_DIR" >&2
	fi
}
trap cleanup_step4_tmp EXIT
LOCAL_CONFIG="$LOCAL_STEP4_DIR/events_${OUTPUT_LABEL}_part_${PART}_cfg.py"
LOCAL_LOG="$LOCAL_STEP4_DIR/step4_events_${OUTPUT_LABEL}_part_${PART}.log"

OUTPUT="$OUTPUT_DIR/events_${OUTPUT_LABEL}_part_${PART}.root"
if output_is_valid "$OUTPUT"; then
	echo "Step 4 output already exists and is valid: $OUTPUT"
	exit 0
fi

INPUT="$STEP3_DIR/events_AOD_part${PART}.root"
if [[ ! -s "$INPUT" ]]; then
	echo "ERROR: $WORKDIR/$INPUT is missing or empty" >&2
	exit 1
fi

CUSTOMISE_ARGS=()
[[ -n "${AOD_TO_EXONANO_CUSTOMISE:-}" ]] && CUSTOMISE_ARGS+=(--customise "$AOD_TO_EXONANO_CUSTOMISE")
echo "=== Step 4: AODSIM -> ${OUTPUT_LABEL} (Run 3) ==="
DRIVER_ARGS=(
	--step "$NANO_STEP"
	--conditions "$CONDITIONS"
	--datatier NANOAODSIM
	--eventcontent NANOAODSIM
	--geometry "$GEOMETRY"
	--era "$ERA"
	--filein "file:$INPUT"
	--fileout "file:$OUTPUT"
	--python_filename "$LOCAL_CONFIG"
	--nThreads "$N_THREADS"
	--no_exec
	-n "$N_EVENTS"
)
DRIVER_ARGS+=("${CUSTOMISE_ARGS[@]}")
cmsDriver.py step4 "${DRIVER_ARGS[@]}"

CONFIG_SNAPSHOT="$CONFIG_DIR/events_${OUTPUT_LABEL}_part_${PART}_cfg.py"
if ! cp "$LOCAL_CONFIG" "$CONFIG_SNAPSHOT"; then
	echo "WARNING: could not archive Step 4 config at $CONFIG_SNAPSHOT; continuing with local config" >&2
fi

cmsRun "$LOCAL_CONFIG" 2>&1 | tee "$LOCAL_LOG"

LOG_SNAPSHOT="$LOG_DIR/step4_events_${OUTPUT_LABEL}_part_${PART}.log"
if ! cp "$LOCAL_LOG" "$LOG_SNAPSHOT"; then
	echo "WARNING: could not archive Step 4 log at $LOG_SNAPSHOT" >&2
fi
