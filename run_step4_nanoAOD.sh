#!/usr/bin/env bash
WORKDIR="test_run4d126"
mkdir -p "$WORKDIR"
cd "$WORKDIR"

GEOMETRY="ExtendedRun4D126"
ERA="Phase2C26I13M9"

echo "=== Step 4: MiniAOD -> NanoAOD ==="
cmsDriver.py step4 \
  -s NANO \
  --conditions auto:phase2_realistic_T37 \
  --datatier NANOAODSIM \
  --eventcontent NANOAODSIM \
  --geometry $GEOMETRY \
  --era $ERA \
  --filein file:step3.root \
  --fileout file:step4_nano.root \
  --python_filename step4_cfg.py \
  > step4.log 2>&1

cmsRun step4_cfg.py >> step4.log 2>&1
