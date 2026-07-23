#!/usr/bin/env bash

PWD=$(pwd)
WORKDIR="test_run4d126"
cd "$WORKDIR"

GEOMETRY="DB:Extended"
ERA="Run3_2024"
CONDITIONS="auto:phase1_2024_realistic"

echo "=== Step 4: MiniAOD -> NanoAOD ==="
cmsDriver.py step4 \
  -s NANO \
  --conditions $CONDITIONS \
  --datatier NANOAODSIM \
  --eventcontent NANOAODSIM \
  --geometry $GEOMETRY \
  --era $ERA \
  --filein file:step3.root \
  --fileout file:step4_nano.root \
  --python_filename step4_cfg.py \
  --customise_commands "process.nanoSequenceMC.remove(process.ttbarCategoryTable); process.nanoSequenceMC.remove(process.categorizeGenTtbar); process.nanoSequenceMC.remove(process.trkMetTable); process.options.TryToContinue = cms.untracked.vstring('ProductNotFound')" \
  --no_exec \
  > step4.log 2>&1

cmsRun step4_cfg.py >> step4.log 2>&1

cd "$PWD"
