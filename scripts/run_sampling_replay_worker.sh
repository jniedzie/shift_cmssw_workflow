#!/bin/bash
set -eo pipefail
scan_root=$1
ledger=$2
chunk=$3
sim_seed=$4
lower=$5
upper=$6
tag=$7
generator_seed=$8
source /cvmfs/cms.cern.ch/cmsset_default.sh
set -a
source "$scan_root/workflow/config/campaigns/qcd_mu_enriched_10k_2023.env"
CMSSW_PREPARED=1
CMSSW_SRC=/afs/cern.ch/work/j/jniedzie/private/shift_cmssw/CMSSW_17_0_0_pre4/src
SAMPLE_BASE=/eos/home-j/jniedzie/shift_cmssw
GENERATOR_SEED=$generator_seed
SIMULATION_SEED=$sim_seed
SAMPLE_NAME=qcd
# The generator is read from persisted GEN; this fragment is checked by downstream setup only.
PROCESS=QCD_MuEnriched_FixedTarget_pThat_1to5GeV_13p6TeV
CAMPAIGN_NAME=WeightedReplay_qcdmu_pThat_${lower}to${upper}_${tag}_20260921_v1
source "$scan_root/workflow/config/workflow.env"
set +a
cd "$CMSSW_SRC"
eval "$(scram runtime -sh)"
python3 "$scan_root/workflow/scripts/run_sampling_replay.py" "$ledger" --chunk "$chunk" --sim-seed "$sim_seed" --template "$scan_root/step1_template.py"
