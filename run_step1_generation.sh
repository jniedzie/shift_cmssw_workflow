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
LOG_DIR="$SAMPLE_DIR/logs"

GEOMETRY="DB:Extended"
ERA="Run3_2024"
CONDITIONS="auto:phase1_2024_realistic"
BEAMSPOT="Realistic25ns13p6TeVEarly2023Collision"

# Generate a fresh CMSSW-compatible seed for every generation invocation.
# Reading from /dev/urandom avoids reusing cmsDriver's default seed.
GENERATOR_SEED=$(od -An -N4 -tu4 /dev/urandom | tr -d ' ')
GENERATOR_SEED=$((GENERATOR_SEED % 900000000 + 1))

mkdir -p "$WORKDIR" "$OUTPUT_DIR" "$CONFIG_DIR" "$LOG_DIR"
cd "$WORKDIR"

echo "=== Step 1: GEN,SIM (Run 3) ==="
echo "Generator random seed: $GENERATOR_SEED"
cmsDriver.py "$PYTHIA_CONFIG" \
	--step GEN,SIM \
	--conditions "$CONDITIONS" \
	--beamspot "$BEAMSPOT" \
	--datatier GEN-SIM \
	--eventcontent FEVTDEBUG \
	--geometry "$GEOMETRY" \
	--era "$ERA" \
	--fileout "file:$OUTPUT_DIR/events_step1_part${PART}.root" \
	--python_filename "$CONFIG_DIR/events_step1_part${PART}_cfg.py" \
	--customise_commands "process.RandomNumberGeneratorService.generator.initialSeed = cms.untracked.uint32(${GENERATOR_SEED})" \
	--no_exec \
	-n "$N_EVENTS"

cmsRun "$CONFIG_DIR/events_step1_part${PART}_cfg.py" 2>&1 | tee "$LOG_DIR/step1_events_part${PART}.log"

# Pythia prints the generated cross section and its statistical uncertainty in
# the end-of-job summary.  Keep one shared, latest value for this sample.
"$WORKFLOW_ROOT/scripts/update_cross_section.sh" \
	"$LOG_DIR/step1_events_part${PART}.log" \
	"$SAMPLE_DIR/cross_sections.txt" \
	"$(basename "$PYTHIA_CONFIG" .py)"
