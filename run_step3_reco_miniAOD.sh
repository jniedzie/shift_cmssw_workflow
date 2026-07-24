#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKFLOW_ROOT="$(cd "$SCRIPT_DIR" && pwd)"
CHUNK="${1:-0}"
source "$WORKFLOW_ROOT/scripts/setup_cmssw.sh"
N_EVENTS="${2:-$N_EVENTS}"
WORKDIR="${WORKDIR:-$SAMPLE_DIR/step3}"

GEOMETRY="DB:Extended"
ERA="Run3_2024"
CONDITIONS="auto:phase1_2024_realistic"

mkdir -p "$WORKDIR"
cd "$WORKDIR"

INPUT="../step2/events_step2_part${PART}.root"
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
	--fileout "file:events_step3_part${PART}.root" \
	--python_filename "events_step3_part${PART}_cfg.py" \
	--no_exec \
	-n "$N_EVENTS"

cmsRun "events_step3_part${PART}_cfg.py" 2>&1 | tee "events_step3_part${PART}.log"
