#!/bin/bash
# Code style: AI-code. AI-generated frozen streaming-stage bootstrap.
set -euo pipefail
request_sha=${1:?request SHA required}
tools_sha=${2:?tools archive SHA required}
request_name=${3:-request.json}
scratch=${_CONDOR_SCRATCH_DIR:?Condor scratch required}
cd "$scratch"
export PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
unset LD_LIBRARY_PATH LD_PRELOAD PYTHONPATH PYTHONHOME ROOTSYS CMSSW_BASE CMSSW_SEARCH_PATH CMSSW_RELEASE_BASE

finish() {
    result=$?
    trap - EXIT
    set +e
    cd "$scratch"
    env -u LD_LIBRARY_PATH -u LD_PRELOAD -u PYTHONPATH -u PYTHONHOME \
        /usr/bin/python3 tools/scripts/run_shift_stream_payload.py finish "$request_name" "$result"
    receipt_result=$?
    if [ "$result" -eq 0 ]; then result=$receipt_result; fi
    # Output files must exist even when runtime setup failed.
    if [ ! -f status.json ]; then
        printf '{"complete":false,"exit_code":1,"error":"Bootstrap failed before receipt finalization"}\n' > status.json
    fi
    if [ ! -f result.json ]; then cp status.json result.json; fi
    if [ ! -f return_payload.tar.gz ]; then tar -czf return_payload.tar.gz --files-from /dev/null; fi
    if [ -n "${SHIFT_TEA_ALIAS_DIRECTORY:-}" ]; then
        rm -rf -- "$SHIFT_TEA_ALIAS_DIRECTORY"
    fi
    exit "$result"
}
trap finish EXIT
printf '%s  %s\n' "$request_sha" "$request_name" | sha256sum -c -
printf '%s  frozen_tools.tar.gz\n' "$tools_sha" | sha256sum -c -
mkdir tools
tar -xzf frozen_tools.tar.gz -C tools
readarray -t settings < <(/usr/bin/python3 tools/scripts/run_shift_stream_payload.py settings "$request_name")
timeout 600 /usr/bin/xrdcp --silent --cksum adler32 "${settings[1]}" runtime.tar.gz
printf '%s  runtime.tar.gz\n' "${settings[2]}" | sha256sum -c -
mkdir runtime
tar -xzf runtime.tar.gz -C runtime
rm runtime.tar.gz
if [ "${settings[0]}" = cmssw ]; then
    export SCRAM_ARCH=el9_amd64_gcc13
    set +u
    source /cvmfs/cms.cern.ch/cmsset_default.sh
    cd "$scratch/runtime/CMSSW_17_0_0_pre4/src"
    scram b ProjectRename
    source <(scram runtime -sh)
    set -u
    export LD_LIBRARY_PATH="$(python3 -c 'import os;print(":".join(p for p in os.environ.get("LD_LIBRARY_PATH", "").split(":") if "/biglib/" not in p))')"
elif [ "${settings[0]}" = tea ]; then
    source "$scratch/tools/tea/utils/activate_streaming_runtime.sh" "$scratch/runtime" "$scratch/tools/tea"
else
    exit 2
fi
mkdir "$scratch/native_clients"
for client in xrdcp xrdfs; do
    cat > "$scratch/native_clients/$client" <<'NATIVE'
#!/bin/bash
set -euo pipefail
client=${0##*/}
exec env -u LD_LIBRARY_PATH -u LD_PRELOAD -u PYTHONPATH -u PYTHONHOME \
    -u ROOTSYS -u CONDA_PREFIX PATH=/usr/bin:/bin "/usr/bin/$client" "$@"
NATIVE
    chmod +x "$scratch/native_clients/$client"
done
export PATH="$scratch/native_clients:$PATH"
export TMPDIR="$scratch"
cd "$scratch"
timeout --signal=TERM --kill-after=60s "${settings[3]}" \
    python3 tools/scripts/run_shift_stream_payload.py run "$request_name"
