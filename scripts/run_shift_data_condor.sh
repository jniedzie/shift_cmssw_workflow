#!/usr/bin/env bash
# Code style: AI-code. AI-generated isolated data-worker bootstrap.
set -euo pipefail
umask 077
persistent=${1:?persistent directory required}
request_sha=${2:?request SHA required}
tools_sha=${3:?tools SHA required}
runtime_url=${4:?runtime URL required}
runtime_sha=${5:?runtime SHA required}
scratch=${_CONDOR_SCRATCH_DIR:?Condor scratch required}
cd "$scratch"
bootstrap_started=$(date +%s.%N)
download_started=0
download_finished=0
runtime_ready=0
worker_pid=''
finish() {
    result=$?
    trap - EXIT
    set +e
    cd "$scratch"
    if [ ! -f status.json ]; then
        printf '{"complete":false,"exit_code":%s,"stage":"bootstrap"}\n' "$result" > status.json
    fi
    if [ ! -f result.json ]; then cp status.json result.json; fi
    env -u LD_LIBRARY_PATH -u LD_PRELOAD -u PYTHONPATH -u PYTHONHOME /usr/bin/python3 - "$bootstrap_started" "$download_started" "$download_finished" "$runtime_ready" <<'PY'
import json,sys,time
started,download_start,download_end,ready=map(float,sys.argv[1:])
with open('bootstrap_metrics.json','w') as output:
    json.dump(dict(total_elapsed_seconds=time.time()-started,
                   runtime_download_seconds=download_end-download_start if download_end else None,
                   runtime_setup_seconds=ready-download_end if ready else None),output)
PY
    files=()
    for name in status.json result.json bootstrap_metrics.json bootstrap.restricted.log cmsRun.restricted.log FrameworkJobReport.xml execute_cfg.py restricted_nano.root; do
        [ ! -f "$name" ] || files+=("$name")
    done
    if tar -czf return_payload.tar.gz.new "${files[@]}" &&
       tar -tzf return_payload.tar.gz.new >/dev/null &&
       mv return_payload.tar.gz.new return_payload.tar.gz; then
        :
    else
        # A native success without its returned ROOT is not a completed shard.
        # Preserve the original receipt and ROOT before reporting the failure.
        emergency="$persistent/attempt_history/archive_failure_$(date +%s)_$$"
        mkdir -p "$emergency"
        for name in "${files[@]}"; do
            cp -p "$name" "$emergency/$name" || exit 74
        done
        printf '{"complete":false,"exit_code":74,"stage":"archive_preservation"}\n' > status.json
        cp status.json result.json
        exit 74
    fi
    exit "$result"
}
trap finish EXIT
terminate() {
    if [ -n "$worker_pid" ]; then
        kill -TERM "$worker_pid" 2>/dev/null || true
        wait "$worker_pid" || true
    fi
    exit 143
}
trap terminate TERM INT
if [ -f return_payload.tar.gz ]; then
    previous_sha=$(sha256sum return_payload.tar.gz | cut -d' ' -f1)
    previous_directory="$persistent/attempt_history/$previous_sha"
    mkdir -p "$previous_directory"
    for name in return_payload.tar.gz result.json status.json; do
        [ ! -f "$name" ] || cp -n "$name" "$previous_directory/$name"
    done
fi
printf '%s  request.json\n%s  frozen_tools.tar.gz\n' "$request_sha" "$tools_sha" | sha256sum -c - > bootstrap.restricted.log 2>&1
mkdir payload runtime
{ tar -xzf frozen_tools.tar.gz -C payload
  download_started=$(date +%s.%N)
  timeout 600 /usr/bin/xrdcp --silent --cksum adler32 "$runtime_url" runtime.tar.gz
  download_finished=$(date +%s.%N)
  printf '%s  runtime.tar.gz\n' "$runtime_sha" | sha256sum -c -
  tar -xzf runtime.tar.gz -C runtime
  rm runtime.tar.gz
} >> bootstrap.restricted.log 2>&1
unset LD_LIBRARY_PATH LD_PRELOAD PYTHONPATH PYTHONHOME ROOTSYS CMSSW_BASE CMSSW_RELEASE_BASE CMSSW_SEARCH_PATH CONDA_PREFIX
export PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 SCRAM_ARCH=el9_amd64_gcc13
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
{
    source /cvmfs/cms.cern.ch/cmsset_default.sh
    cd "$scratch/runtime/CMSSW_17_0_0_pre4/src"
    scram b ProjectRename
    eval "$(scram runtime -sh)"
    export LD_LIBRARY_PATH="$(python3 -c 'import os;print(":".join(p for p in os.environ.get("LD_LIBRARY_PATH", "").split(":") if "/biglib/" not in p))')"
    cd "$scratch"
} >> bootstrap.restricted.log 2>&1
runtime_ready=$(date +%s.%N)
python3 "$scratch/payload/shift_data_attempts.py" "$persistent" "$scratch"
python3 "$scratch/payload/run_shift_data_reconstruction.py" request.json "$request_sha" --payload "$scratch/payload" &
worker_pid=$!
wait "$worker_pid"
