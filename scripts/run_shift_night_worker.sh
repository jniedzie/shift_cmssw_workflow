#!/bin/bash
# Code style: AI-code. AI-generated frozen wrapper around the unchanged V10 detector worker.
set -euo pipefail
bootstrap_sha=${1:?bootstrap SHA required}
selection_file=${2:?selection basename required}
selection_sha=${3:?selection SHA required}
contract_sha=${4:?event contract SHA required}
job=${5:?job required}
epoch=${6:?epoch required}
count=${7:?event count required}
scratch=${_CONDOR_SCRATCH_DIR:?Condor scratch required}
cd "$scratch"
started_epoch=$(/usr/bin/date +%s)

finish() {
    result=$?
    trap - EXIT
    set +e
    cd "$scratch"
    python3 shift_night_worker.py finalise "$result" "$job" "$epoch" "$selection_sha" "$contract_sha"
    result=$?
    printf 'Night worker job %s finished with code %s.\n' "$job" "$result"
    exit "$result"
}
trap finish EXIT

printf '%s  bootstrap.json\n' "$bootstrap_sha" | sha256sum -c -
export PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
unset LD_LIBRARY_PATH LD_PRELOAD PYTHONPATH PYTHONHOME ROOTSYS CMSSW_BASE CMSSW_SEARCH_PATH CMSSW_RELEASE_BASE
/usr/bin/python3 shift_night_worker.py bootstrap
readarray -t frozen < <(/usr/bin/python3 - "$count" <<'PY'
import json,math,sys
data=json.load(open('bootstrap.json'));count=int(sys.argv[1]);assert 1<=count<=20
print(data['base_bundle_url']);print(data['base_bundle_sha256'])
print(data['bundle_url']);print(data['bundle_sha256']);print(data['worker_sha256'])
print(','.join(str(math.ceil(value*100/count)) for value in data['effective_stage_timeouts_seconds']))
print(data['config_timeout_seconds']);print(data['event_stall_timeout_seconds'])
print(data['application_timeout_seconds']);print(data['whole_job_computation_limit_seconds'])
print(data['return_reserve_seconds'])
PY
)
timeout 600 /usr/bin/xrdcp --silent --cksum adler32 "${frozen[0]}" base_bundle.tar.gz
printf '%s  base_bundle.tar.gz\n' "${frozen[1]}" | sha256sum -c -
mkdir base
tar -xzf base_bundle.tar.gz -C base
/usr/bin/python3 shift_night_worker.py prepare "$selection_file" "$selection_sha" "$contract_sha"
timeout 600 /usr/bin/xrdcp --silent --cksum adler32 "${frozen[2]}" runtime.tar.gz
printf '%s  runtime.tar.gz\n' "${frozen[3]}" | sha256sum -c -
mkdir payload
tar -xzf runtime.tar.gz -C payload
printf '%s  payload/workflow/scripts/run_shift_gen_to_nano.py\n' "${frozen[4]}" | sha256sum -c -
export SCRAM_ARCH=el9_amd64_gcc13
set +u
source /cvmfs/cms.cern.ch/cmsset_default.sh
cd payload/CMSSW_17_0_0_pre4/src
timeout 300 scram b ProjectRename
runtime_environment=$(timeout 120 scram runtime -sh)
eval "$runtime_environment"
set -u
export LD_LIBRARY_PATH="$(python3 -c 'import os;print(":".join(path for path in os.environ.get("LD_LIBRARY_PATH", "").split(":") if "/biglib/" not in path))')"
export TMPDIR="$scratch"
cd "$scratch"
setup_seconds=$(($(date +%s)-started_epoch))
application_budget=$((frozen[9]-setup_seconds-frozen[10]))
if (( application_budget > frozen[8] )); then application_budget=${frozen[8]}; fi
if (( application_budget < 60 )); then exit 1; fi
python3 shift_night_worker.py started
# The detector worker and all four templates are byte-identical to V10.
# Publication belongs to this wrapper, which verifies the complete EOS files.
timeout --signal=TERM --kill-after=60s "$application_budget" \
    python3 payload/workflow/scripts/run_shift_gen_to_nano.py source.json \
    --skip 0 --count "$count" --job "$job" --templates base/templates \
    --stage-timeouts "${frozen[5]}" --config-timeout "${frozen[6]}" \
    --event-stall-timeout "${frozen[7]}" >worker_runtime.log 2>&1
