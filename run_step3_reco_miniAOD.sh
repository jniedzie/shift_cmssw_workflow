#!/usr/bin/env bash
set -euo pipefail

PWD0=$(pwd)
if [[ -n "${CMSSW_BASE:-}" ]]; then
	DEFAULT_WORKDIR="$CMSSW_BASE/../shift_runs/test_run4d126"
else
	DEFAULT_WORKDIR="$PWD0/../shift_runs/test_run4d126"
fi
WORKDIR="${WORKDIR:-$DEFAULT_WORKDIR}"
N_EVENTS="${N_EVENTS:-10}"

GEOMETRY="DB:Extended"
ERA="Run3_2024"
CONDITIONS="auto:phase1_2024_realistic"

trap 'cd "$PWD0"' EXIT
mkdir -p "$WORKDIR"
cd "$WORKDIR"

if [[ ! -s step2.root ]]; then
	echo "ERROR: $WORKDIR/step2.root is missing or empty" >&2
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
	--filein file:step2.roo \
	--fileout file:step3.root \
	--python_filename step3_cfg.py \
	--no_exec \
	-n "$N_EVENTS"

cmsRun step3_cfg.py 2>&1 | tee step3.log
