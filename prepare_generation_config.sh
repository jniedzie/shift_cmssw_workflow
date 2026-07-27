#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/config/workflow.env"

echo "Running cmsDriver.py to prepare the generation config"

cmsDriver.py "$PYTHIA_CONFIG" \
  --python_filename qcd_gen_test.py \
  --eventcontent RAWSIM \
  --datatier GEN \
  --conditions auto:phase2_realistic_T38 \
  --beamspot NoSmear \
  --step GEN \
  --nThreads 2 -n 100 --no_exec

echo "Generation config prepared: qcd_gen_test.py"
