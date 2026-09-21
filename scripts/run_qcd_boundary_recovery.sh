#!/bin/bash
# Recover exact failed indices with original seeds; healthy outputs are reused.
set -eo pipefail
scan_root=$1
chunk=$2
source /cvmfs/cms.cern.ch/cmsset_default.sh
set -a
source "$scan_root/workflow/config/campaigns/qcd_mu_enriched_10k_2023.env"
CMSSW_PREPARED=1
CMSSW_SRC=/afs/cern.ch/work/j/jniedzie/private/shift_cmssw/CMSSW_17_0_0_pre4/src
SAMPLE_BASE=/eos/home-j/jniedzie/shift_cmssw
set +a
for stage in run_step1_generation.sh run_step2_digi_raw.sh run_step3_aod.sh run_step4_exonanoAOD.sh; do
    bash "$scan_root/workflow/$stage" "$chunk" 10
done
