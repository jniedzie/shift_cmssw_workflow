#!/usr/bin/env bash
set -euo pipefail

PWD0=$(pwd)
if [[ -n "${CMSSW_BASE:-}" ]]; then
	DEFAULT_WORKDIR="$CMSSW_BASE/../shift_runs/test_run4d126"
else
	DEFAULT_WORKDIR="$PWD0/../shift_runs/test_run4d126"
fi
WORKDIR="${WORKDIR:-$DEFAULT_WORKDIR}"
N_EVENTS="${N_EVENTS:-10}"curl -L https://root.cern/js/7.11.0/JsRoot7110.tar.gz -o /tmp/JsRoot7110.tar.gz && tar -tzf /tmp/JsRoot7110.tar.gz |
GEOMETRY="DB:Extended"
ERA="Run3_2024"
CONDITIONS="auto:phase1_2024_realistic"

trap 'cd "$PWD0"' EXIT
mkdir -p "$WORKDIR"
cd "$WORKDIR"

if [[ ! -s step1.root ]]; then
	echo "ERROR: $WORKDIR/step1.root is missing or empty" >&2
	exit 1
fi

echo "=== Step 2: DIGI,L1,DIGI2RAW,HLT (Run 3) ==="code -
cmsDriver.py step2 \
	--step DIGI:pdigi_valid,L1,DIGI2RAW,HLT:@relval2024,ENDJOB \
	--conditions "$CONDITIONS" \
	--datatier GEN-SIM-DIGI-RAW \
	--eventcontent FEVTDEBUGHLT \
	--geometry "$GEOMETRY" \
	--era "$ERA" \
	--filein file:step1.root \
	--fileout file:step2.root \
	--python_filename step2_cfg.py \
	--no_exec \
	-n "$N_EVENTS"

cmsRun step2_cfg.py 2>&1 | tee step2.log
