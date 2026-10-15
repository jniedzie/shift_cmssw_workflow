#!/usr/bin/env python3
# Code style: AI-code. AI-generated immutable night-worker deployment preparation.
import argparse
import json
from pathlib import Path
import shutil

from shift_night_plan import load_plan, sha


def prepare(plan_path, tail_bootstrap, destination):
    plan = load_plan(plan_path)
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=False)
    original = json.loads(Path(tail_bootstrap).read_text())
    bootstrap = {key: original[key] for key in (
        'bundle_url', 'bundle_sha256', 'worker_sha256', 'templates',
        'effective_stage_timeouts_seconds', 'config_timeout_seconds',
        'event_stall_timeout_seconds', 'application_timeout_seconds',
        'whole_job_computation_limit_seconds', 'response_contract_sha256')}
    bootstrap.update(schema='shift-frozen-night-worker-bootstrap-v1',
                     base_bundle_url='root://eosuser.cern.ch/' + plan['data'].get('output_base',
                         '/eos/user/j/jniedzie/shift_cmssw_support/ntuple_production/qcd_night_reco_20261014_v1') + '/base_bundle.tar.gz',
                     base_bundle_sha256=plan['data']['base_bundle_sha256'],
                     night_plan_sha256=plan['sha256'], return_reserve_seconds=1800,
                     publication='Versioned support EOS with whole-file SHA readback; scheduler-spooled rescue on failure',
                     analysis_release=False, physics_valid=False)
    (destination / 'bootstrap.json').write_text(json.dumps(bootstrap, indent=2) + '\n')
    scripts = Path(__file__).resolve().parent
    shutil.copy2(scripts / 'run_shift_night_worker.sh', destination / 'bootstrap.sh')
    (destination / 'bootstrap.sh').chmod(0o755)
    shutil.copy2(scripts / 'shift_night_worker.py', destination / 'shift_night_worker.py')
    description = '''# Code style: AI-code. AI-generated frozen scheduler-spooled night worker template.
universe = vanilla
initialdir = @SUPERVISOR_ROOT@
executable = @SUPERVISOR_ROOT@/worker/bootstrap.sh
arguments = BOOTSTRAP_SHA $(selection_basename) $(selection_sha) $(event_contract_sha) $(job) $(epoch) $(count)
should_transfer_files = YES
when_to_transfer_output = ON_EXIT
transfer_input_files = @SUPERVISOR_ROOT@/worker/bootstrap.json,@SUPERVISOR_ROOT@/worker/shift_night_worker.py,$(selection)
transfer_output_files = status.json,rescue_payload.tar.gz
transfer_output_remaps = "status.json=@SUPERVISOR_ROOT@/results/status$(job).json;rescue_payload.tar.gz=@SUPERVISOR_ROOT@/results/job$(job)_rescue_payload.tar.gz"
getenv = False
notification = Never
request_cpus = 1
request_memory = 6000
request_disk = 8000000
+WantIOProxy = true
+MaxRuntime = 21600
+ShiftNightWorker = true
+ShiftProductionSuite = true
+ShiftNightCampaign = "@CAMPAIGN_TAG@"
+ShiftSuiteTag = "@CAMPAIGN_TAG@"
+ShiftNightWaveNonce = "@WAVE_NONCE@"
+ShiftNightEpoch = $(epoch)
+ShiftNightJob = $(job)
+ShiftNightSourceIndex = $(source)
+ShiftNightSeed = $(seed)
+ShiftNtupleJob = $(job)
+ShiftDiagnosticOnly = true
+ShiftNightSelectionSHA256 = "$(selection_sha)"
+ShiftNightEventContractSHA256 = "$(event_contract_sha)"
+ShiftNightSourceGENSHA256 = "$(source_gen_sha)"
+ShiftNightResponseContractSHA256 = "RESPONSE_SHA"
+ShiftNightAdmissionStartEpoch = @START_EPOCH@
+ShiftNightAdmissionStopEpoch = @STOP_EPOCH@
+ShiftNightAdmissionLeaseEpoch = @LEASE_EPOCH@
+ShiftNightAdmissionDeferred = false
+ShiftNightDeferredNumJobStarts = -1
+ShiftNightProcessedEvents = -1
+ShiftNightScienceStarted = false
+ShiftNightBootstrapInProgress = false
+JobBatchName = "@CAMPAIGN_TAG@"
on_exit_remove = True
on_exit_hold = (ExitBySignal == True) || (ExitCode == 75)
periodic_release = False
leave_in_queue = (JobStatus == 4) && (time() - CompletionDate < 2592000)
periodic_hold = (JobStatus == 1) && (time() >= ShiftNightAdmissionStopEpoch || time() >= ShiftNightAdmissionLeaseEpoch)
periodic_hold_reason = "SHIFT night admission expired before reconstruction started"
log = @EVENT_LOG@
output = @SUPERVISOR_ROOT@/results/job$(job)_$(Cluster)_$(Process).out
error = @SUPERVISOR_ROOT@/results/job$(job)_$(Cluster)_$(Process).err
queue job,epoch,source,count,seed,selection,selection_basename,selection_sha,event_contract_sha,source_gen_sha from @JOB_LIST@
'''
    description = description.replace('BOOTSTRAP_SHA', sha(destination / 'bootstrap.json'))
    description = description.replace('RESPONSE_SHA', bootstrap['response_contract_sha256'])
    (destination / 'worker.sub.in').write_text(description)
    manifest = {'schema': 'shift-frozen-night-worker-files-v1',
                'files': {path.name: sha(path) for path in sorted(destination.iterdir()) if path.is_file()},
                'night_plan_sha256': plan['sha256'], 'analysis_release': False,
                'physics_valid': False, 'science_retries': 'Never automatic'}
    (destination / 'worker_manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('night_plan', type=Path)
    parser.add_argument('tail_bootstrap', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare(args.night_plan, args.tail_bootstrap, args.destination), indent=2))


if __name__ == '__main__':
    main()
