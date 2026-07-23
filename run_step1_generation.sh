#!/usr/bin/env bash
set -euo pipefail

: "${CMSSW_BASE:?Run cmsenv first so CMSSW_BASE is defined}"
PWD0=$(pwd)
WORKDIR="${SHIFT_RUN_DIR:-${CMSSW_BASE}/../shift_runs/test_run4d126}"
N_EVENTS="${N_EVENTS:-10}"

GEOMETRY="DB:Extended"
ERA="Run3_2024"
CONDITIONS="auto:phase1_2024_realistic"
BEAMSPOT="Realistic25ns13p6TeVEarly2023Collision"
FRAGMENT="Configuration/GenProduction/QCD_pThat_15to30_13p6TeV_pythia8_cff.py"

trap 'cd "$PWD0"' EXIT
mkdir -p "$WORKDIR"
cd "$WORKDIR"

echo "=== Step 1: GEN,SIM (Run 3) ==="
cmsDriver.py "$FRAGMENT" \
	--conditions "$CONDITIONS" \
	--beamspot "$BEAMSPOT" \
	--datatier GEN-SIM \
	--eventcontent FEVTDEBUG \
	--geometry "$GEOMETRY" \
	--era "$ERA" \
	--fileout file:step1.root \
	--python_filename step1_cfg.py \
	--no_exec \
	-n "$N_EVENTS"

cmsRun step1_cfg.py 2>&1 | tee step1.log
