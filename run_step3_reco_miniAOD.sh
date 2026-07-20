#!/usr/bin/env bash

WORKDIR="test_run4d126"
mkdir -p "$WORKDIR"
cd "$WORKDIR"

GEOMETRY="ExtendedRun4D126"
ERA="Phase2C26I13M9"

echo "=== Step 3: RAW2DIGI,RECO,RECOSIM,PAT (no VALIDATION/DQM — dropped for speed) ==="
cmsDriver.py step3 \
  -s RAW2DIGI,RECO,RECOSIM,PAT \
  --conditions auto:phase2_realistic_T37 \
  --datatier GEN-SIM-RECO,MINIAODSIM -n 10 \
  --eventcontent FEVTDEBUGHLT,MINIAODSIM \
  --geometry $GEOMETRY \
  --era $ERA \
  --filein file:step2.root \
  --fileout file:step3.root \
  --python_filename step3_cfg.py \
  > step3.log 2>&1
cmsRun step3_cfg.py >> step3.log 2>&1
