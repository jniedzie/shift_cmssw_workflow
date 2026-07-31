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
# EXONanoAOD is provided by the standard EXO NanoAOD customization.  Keep
# these settings local to this stage: the preceding stages produce the AOD
# input, while this stage follows the EXONanoAOD Run 3 / 2025 recipe.
ERA="Run3,Run3_2025"
CONDITIONS="auto:phase1_2025_realistic"
N_THREADS="${N_THREADS:-4}"

mkdir -p "$WORKDIR" "$OUTPUT_DIR" "$CONFIG_DIR" "$LOG_DIR"
cd "$WORKDIR"

OUTPUT="$OUTPUT_DIR/events_EXONanoAOD_part_${PART}.root"
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
echo "=== Step 4: AODSIM -> EXONanoAOD (Run 3) ==="
cmsDriver.py step4 \
	--step PAT,NANO:@EXO \
	--conditions "$CONDITIONS" \
	--datatier NANOAODSIM \
	--eventcontent NANOAODSIM \
	--geometry "$GEOMETRY" \
	--era "$ERA" \
	--filein "file:$INPUT" \
	--fileout "file:$OUTPUT" \
	--python_filename "$CONFIG_DIR/events_EXONanoAOD_part_${PART}_cfg.py" \
	--nThreads "$N_THREADS" \
	--no_exec "${CUSTOMISE_ARGS[@]}" \
	-n "$N_EVENTS"

cmsRun "$CONFIG_DIR/events_EXONanoAOD_part_${PART}_cfg.py" 2>&1 | tee "$LOG_DIR/step4_events_EXONanoAOD_part_${PART}.log"
