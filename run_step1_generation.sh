#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKFLOW_ROOT="$(cd "$SCRIPT_DIR" && pwd)"
CHUNK="${1:-0}"
source "$WORKFLOW_ROOT/scripts/setup_cmssw.sh"
N_EVENTS="${2:-$N_EVENTS}"
WORKDIR="${WORKDIR:-$SAMPLE_DIR}"
OUTPUT_DIR="$SAMPLE_DIR/samples/step1"
CONFIG_DIR="$SAMPLE_DIR/configs/step1"
LOG_DIR="$SAMPLE_DIR/logs/step1"

GEOMETRY="DB:Extended"
ERA="Run3_2024"
CONDITIONS="auto:phase1_2024_realistic"
BEAMSPOT="Realistic25ns13p6TeVEarly2023Collision"
FRAGMENT="Configuration/GenProduction/QCD_pThat_15to30_13p6TeV_pythia8_cff.py"

mkdir -p "$WORKDIR" "$OUTPUT_DIR" "$CONFIG_DIR" "$LOG_DIR"
cd "$WORKDIR"

echo "=== Step 1: GEN,SIM (Run 3) ==="
cmsDriver.py "$FRAGMENT" \
	--step GEN,SIM \
	--conditions "$CONDITIONS" \
	--beamspot "$BEAMSPOT" \
	--datatier GEN-SIM \
	--eventcontent FEVTDEBUG \
	--geometry "$GEOMETRY" \
	--era "$ERA" \
	--fileout "file:$OUTPUT_DIR/events_step1_part${PART}.root" \
	--python_filename "$CONFIG_DIR/events_step1_part${PART}_cfg.py" \
	--no_exec \
	-n "$N_EVENTS"

cmsRun "$CONFIG_DIR/events_step1_part${PART}_cfg.py" 2>&1 | tee "$LOG_DIR/events_step1_part${PART}.log"
