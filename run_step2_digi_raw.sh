#!/usr/bin/env bash

PWD=$(pwd)
WORKDIR="test_run4d126"
cd "$WORKDIR"

GEOMETRY="DB:Extended"
ERA="Run3_2024"
CONDITIONS="auto:phase1_2024_realistic"

echo "=== Step 2: DIGI,L1,DIGI2RAW,HLT ==="
cmsDriver.py step2 \
  -s DIGI:pdigi_valid,L1TrackTrigger,L1,L1P2GT,DIGI2RAW,HLT:@relval2024 \
  --conditions $CONDITIONS \
  --datatier GEN-SIM-DIGI-RAW -n 10 \
  --eventcontent FEVTDEBUGHLT \
  --geometry $GEOMETRY \
  --era $ERA \
  --filein file:step1.root \
  --fileout file:step2.root \
  --python_filename step2_cfg.py \
  --no_exec \
  > step2.log 2>&1

cmsRun step2_cfg.py >> step2.log 2>&1

cd "$PWD"
see 