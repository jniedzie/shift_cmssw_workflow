#!/usr/bin/env python3
"""Freeze an authorized sampling pilot and prepare an explicit Condor manifest."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('output', type=Path)
    p.add_argument('--template', type=Path, required=True)
    args = p.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    workflow = Path(__file__).resolve().parents[1]
    snapshot = out / 'workflow'
    names = subprocess.check_output(['git', '-C', str(workflow), 'ls-files'], text=True).splitlines()
    names += ['scripts/run_sampling_pilot.py', 'scripts/prepare_sampling_scan.py',
              'scripts/pythia_pthat.py', 'scripts/run_qcd_boundary_recovery.sh',
              'scripts/summarize_sampling_scan.py', 'scripts/audit_sampling_gen_bins.py',
              'scripts/audit_campaign_event_counts.py', 'scripts/prepare_sampling_recovery.py',
              'scripts/prepare_weighted_sampling.py', 'scripts/run_sampling_replay.py',
              'scripts/run_sampling_replay_worker.sh', 'scripts/prepare_replay_jobs.py',
              'scripts/summarize_sampling_replay.py', 'scripts/validate_sampling_ledger.py',
              'scripts/write_sampling_bookkeeping.py', 'scripts/audit_sampling_stage_identities.py',
              'scripts/audit_replay_physics_config.py', 'scripts/audit_sampling_replay_counts.py',
              'scripts/compare_sampling_cost.py', 'scripts/prepare_expanded_sampling.py',
              'scripts/merge_sampling_nano.py', 'scripts/merge_complete_qcd.py',
              'scripts/inventory_sampling_storage.py', 'scripts/audit_storage_merges.py',
              'scripts/archive_obsolete_probe_metadata.py', 'scripts/prepare_control_retirement_list.py']
    for name in set(names):
        src = workflow / name
        if src.is_file():
            dst = snapshot / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    shutil.copy2(args.template, out / 'step1_template.py')
    wrapper = out / 'worker.sh'
    wrapper.write_text('''#!/bin/bash
set -eo pipefail
scan_root=$1
sample=$2
mode=$3
lower=$4
upper=$5
events=$6
seed=$7
chunk=$8
tag=$9
source /cvmfs/cms.cern.ch/cmsset_default.sh
set -a
source "$scan_root/workflow/config/campaigns/qcd_mu_enriched_10k_2023.env"
CMSSW_PREPARED=1
CMSSW_SRC=/afs/cern.ch/work/j/jniedzie/private/shift_cmssw/CMSSW_17_0_0_pre4/src
SAMPLE_BASE=/eos/home-j/jniedzie/shift_cmssw
GENERATOR_SEED=$seed
SIMULATION_SEED=$((seed+1000000))
N_EVENTS=$events
N_JOBS=1
case $sample in
 qcdmu) SAMPLE_NAME=qcd; PROCESS=QCD_MuEnriched_FixedTarget_pThat_1to5GeV_13p6TeV;;
 qcd) SAMPLE_NAME=qcd; PROCESS=QCD_FixedTarget_pThat_1to5GeV_13p6TeV;;
 jpsi) SAMPLE_NAME=jpsi; PROCESS=Charmonium_FixedTarget_pThat_1to5GeV_13p6TeV;;
 *) exit 2;;
esac
CAMPAIGN_NAME=SamplingScan_${sample}_pThat_${lower}to${upper}_${tag}_20260921_v1
source "$scan_root/workflow/config/workflow.env"
set +a
cd "$CMSSW_SRC"
eval "$(scram runtime -sh)"
python3 "$scan_root/workflow/scripts/run_sampling_pilot.py" --sample "$sample" --mode "$mode" --lower "$lower" --upper "$upper" --events "$events" --seed "$seed" --chunk "$chunk" --template "$scan_root/step1_template.py"
''')
    wrapper.chmod(0o755)
    (out / 'logs').mkdir()
    template = f'''universe = vanilla
executable = {wrapper}
arguments = "{out} $(sample) $(mode) $(lower) $(upper) $(events) $(seed) $(chunk) $(tag)"
getenv = True
should_transfer_files = NO
request_cpus = 1
request_memory = 4000 MB
+JobFlavour = "workday"
output = {out}/logs/$(ClusterId).$(Process).out
error = {out}/logs/$(ClusterId).$(Process).err
log = {out}/logs/$(ClusterId).log
queue sample,mode,lower,upper,events,seed,chunk,tag from {out}/JOBS
'''
    (out / 'smoke.jobs').write_text('qcdmu gen 1 2 100 9210101 0 smoke\njpsi gen 1 2 100 9210102 0 smoke\nqcdmu full 1 2 2 9210103 0 smoke\njpsi full 1 2 2 9210104 0 smoke\njpsi gen 0 1 10 9210105 0 lowptprobe\n')
    (out / 'smoke.sub').write_text(template.replace('JOBS', 'smoke.jobs'))
    rows = []
    for sample in ('qcdmu', 'jpsi'):
        for bi, (lo, hi) in enumerate(((1,2),(2,5),(5,10),(10,20),(20,-1))):
            base = 9220000 + (100000 if sample == 'jpsi' else 0) + 1000*bi
            rows.append(f'{sample} gen {lo} {hi} 5000 {base} 0 scan')
            for chunk in range(10):
                rows.append(f'{sample} full {lo} {hi} 20 {base+100+chunk} {chunk} scan')
    (out / 'scan.jobs').write_text('\n'.join(rows)+'\n')
    (out / 'scan.sub').write_text(template.replace('JOBS', 'scan.jobs'))
    for mode in ('gen', 'full'):
        (out / f'{mode}.jobs').write_text('\n'.join(r for r in rows if r.split()[1] == mode)+'\n')
        (out / f'{mode}.sub').write_text(template.replace('JOBS', f'{mode}.jobs'))
    (out / 'manifest.json').write_text(json.dumps(dict(
        workflow_head=subprocess.check_output(['git','-C',str(workflow),'rev-parse','HEAD'],text=True).strip(),
        full_chain_attempts=2000, gen_only_attempts=50000, frozen_workflow=str(snapshot),
        physics_valid=False, normalization_ready=False),indent=2)+'\n')
    print(out)


if __name__ == '__main__':
    main()
