#!/usr/bin/env bash

WORKDIR="test_run4d126"
mkdir -p "$WORKDIR"
cd "$WORKDIR"

GEOMETRY="ExtendedRun4D126"
ERA="Phase2C26I13M9"

echo "=== Step 1: GEN,SIM ==="
cmsDriver.py Configuration/GenProduction/python/QCD_pThat_15to30_13p6TeV_pythia8_cff.py \
  -s GEN,SIM -n 10 \
  --conditions auto:phase2_realistic_T37_13TeV \
  --beamspot DBrealisticHLLHC \
  --datatier GEN-SIM \
  --eventcontent FEVTDEBUG \
  --geometry $GEOMETRY \
  --era $ERA \
  --fileout file:step1.root \
  --python_filename step1_cfg.py \
  > step1.log 2>&1
cmsRun step1_cfg.py >> step1.log 2>&1
