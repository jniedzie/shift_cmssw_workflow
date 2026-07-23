#!/usr/bin/env bash

PWD=$(pwd)
WORKDIR="test_run4d126"
cd "$WORKDIR"

GEOMETRY="DB:Extended"
ERA="Run3_2024"
CONDITIONS="auto:phase1_2024_realistic"

echo "=== Step 3: RAW2DIGI,RECO,RECOSIM,PAT,VALIDATION,DQM ==="
cmsDriver.py step3 \
  -s RAW2DIGI,RECO,RECOSIM,PAT,VALIDATION:@phase2Validation+@miniAODValidation,DQM:@phase2+@miniAODDQM \
  --conditions $CONDITIONS \
  --datatier GEN-SIM-RECO,MINIAODSIM,DQMIO -n 10 \
  --eventcontent FEVTDEBUGHLT,MINIAODSIM,DQM \
  --geometry $GEOMETRY \
  --era $ERA \
  --filein file:step2.root \
  --fileout file:step3.root \
  --python_filename step3_cfg.py \
  --no_exec \
  > step3.log 2>&1

cmsRun step3_cfg.py >> step3.log 2>&1

cd "$PWD"
