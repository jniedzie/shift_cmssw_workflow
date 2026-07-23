#!/usr/bin/env bash

PWD=$(pwd)
WORKDIR="test_run4d126"
mkdir -p "$WORKDIR"
cd "$WORKDIR"

GEOMETRY="DB:Extended"
ERA="Run3_2024"
CONDITIONS="auto:phase1_2024_realistic"
BEAMSPOT="Realistic25ns13p6TeVEarly2023Collision"

echo "=== Step 1: GEN,SIM ==="
cmsDriver.py Configuration/GenProduction/python/QCD_pThat_15to30_13p6TeV_pythia8_cff.py \
  -s GEN,SIM -n 10 \
  --conditions $CONDITIONS \
  --beamspot $BEAMSPOT \
  --datatier GEN-SIM \
  --eventcontent FEVTDEBUG \
  --geometry $GEOMETRY \
  --era $ERA \
  --fileout file:step1.root \
  --python_filename step1_cfg.py \
  --no_exec \
  > step1.log 2>&1

cmsRun step1_cfg.py >> step1.log 2>&1

cd "$PWD"
