#!/bin/bash
echo "Running cmsDriver.py to prepare the generation config"

cmsDriver.py Configuration/GenProduction/QCD_pThat_15to30_13p6TeV_pythia8_cff.py \
  --python_filename qcd_gen_test.py \
  --eventcontent RAWSIM \
  --datatier GEN \
  --conditions auto:phase2_realistic_T38 \
  --beamspot NoSmear \
  --step GEN \
  --nThreads 2 -n 100 --no_exec

echo "Generation config prepared: qcd_gen_test.py"
