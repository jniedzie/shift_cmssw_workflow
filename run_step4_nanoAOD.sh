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

if [[ ! -s step3.root ]]; then
	echo "ERROR: $WORKDIR/step3.root is missing or empty" >&2
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
	--filein file:step3.roo \
	--fileout file:step4_nano.root \
	--python_filename step4_cfg.py \
	--no_exec \
	-n "$N_EVENTS"

cmsRun step4_cfg.py 2>&1 | tee step4.log
code --install-extension mkhl.shfmt --force
