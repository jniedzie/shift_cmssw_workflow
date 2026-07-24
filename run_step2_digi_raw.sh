#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKFLOW_ROOT="$(cd "$SCRIPT_DIR" && pwd)"
CHUNK="${1:-0}"
source "$WORKFLOW_ROOT/scripts/setup_cmssw.sh"
N_EVENTS="${2:-$N_EVENTS}"
WORKDIR="${WORKDIR:-$SAMPLE_DIR/step2}"
GEOMETRY="DB:Extended"
ERA="Run3_2024"
CONDITIONS="auto:phase1_2024_realistic"

mkdir -p "$WORKDIR"
cd "$WORKDIR"

INPUT="../step1/events_step1_part${PART}.root"
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
	--fileout "file:events_step2_part${PART}.root" \
	--python_filename "events_step2_part${PART}_cfg.py" \
	--no_exec \
	-n "$N_EVENTS"

cmsRun "events_step2_part${PART}_cfg.py" 2>&1 | tee "events_step2_part${PART}.log"
