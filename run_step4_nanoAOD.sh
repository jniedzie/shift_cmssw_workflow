#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKFLOW_ROOT="$(cd "$SCRIPT_DIR" && pwd)"
CHUNK="${1:-0}"
source "$WORKFLOW_ROOT/scripts/setup_cmssw.sh"
N_EVENTS="${2:-$N_EVENTS}"
WORKDIR="${WORKDIR:-$SAMPLE_DIR/step4}"

GEOMETRY="DB:Extended"
ERA="Run3_2024"
CONDITIONS="auto:phase1_2024_realistic"

mkdir -p "$WORKDIR"
cd "$WORKDIR"

INPUT="../step3/events_step3_part${PART}.root"
if [[ ! -s "$INPUT" ]]; then
	echo "ERROR: $WORKDIR/$INPUT is missing or empty" >&2
	exit 1
fi

echo "=== Step 4: MiniAODSIM -> NanoAODSIM (Run 3) ==="
cmsDriver.py step4 \
	--step NANO \
	--conditions "$CONDITIONS" \
	--datatier NANOAODSIM \
	--eventcontent NANOAODSIM \
	--geometry "$GEOMETRY" \
	--era "$ERA" \
	--filein "file:$INPUT" \
	--fileout "file:events_NanoAOD_part_${PART}.root" \
	--python_filename "events_NanoAOD_part_${PART}_cfg.py" \
	--no_exec \
	-n "$N_EVENTS"

cmsRun "events_NanoAOD_part_${PART}_cfg.py" 2>&1 | tee "events_NanoAOD_part_${PART}.log"
