#!/usr/bin/env bash

WORKDIR="test_run4d126"
mkdir -p "$WORKDIR"
cd "$WORKDIR"

GEOMETRY="ExtendedRun4D126"
ERA="Phase2C26I13M9"

echo "=== Step 2: DIGI,L1,DIGI2RAW,HLT ==="
cmsDriver.py step2 \
  -s DIGI:pdigi_valid,L1TrackTrigger,L1,L1P2GT,DIGI2RAW,HLT:@relvalRun4 \
  --conditions auto:phase2_realistic_T37 \
  --datatier GEN-SIM-DIGI-RAW -n 10 \
  --eventcontent FEVTDEBUGHLT \
  --geometry $GEOMETRY \
  --era $ERA \
  --filein file:step1.root \
  --fileout file:step2.root \
  --python_filename step2_cfg.py \
  > step2.log 2>&1
cmsRun step2_cfg.py >> step2.log 2>&1
