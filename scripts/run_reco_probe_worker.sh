#!/bin/bash
# Code style: AI-code. AI-generated setup for one frozen reconstruction probe.
set -euo pipefail
umask 077
native_iwd="$1"
native_sandbox="${_CONDOR_SCRATCH_DIR:-$PWD}"
native_helper="$native_sandbox/reco_probe_worker.py"
cd "$native_iwd"
trap 'native_status=$?; native_command=$BASH_COMMAND; trap - ERR; /usr/bin/python3 "$native_helper" setup-failed "$native_iwd" "$native_sandbox" "$native_status" "$native_command"; exit "$native_status"' ERR
export PYTHONDONTWRITEBYTECODE=1
unset PYTHONHOME PYTHONPATH LD_LIBRARY_PATH LD_PRELOAD ROOTSYS
export PATH=/usr/bin:/bin SCRAM_ARCH=el9_amd64_gcc13
/usr/bin/python3 "$native_helper" prepare "$native_iwd" "$native_sandbox"
source /cvmfs/cms.cern.ch/cmsset_default.sh
cd "$native_sandbox/native_runtime/CMSSW_17_0_0_pre4/src"
scram b ProjectRename > "$native_iwd/relocation.log" 2>&1
native_runtime_environment="$(scram runtime -sh)"
eval "$native_runtime_environment"
export PYTHONDONTWRITEBYTECODE=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
export LD_LIBRARY_PATH="$native_sandbox/native_runtime/overlay:${LD_LIBRARY_PATH}"
cd "$native_iwd"
exec python3 "$native_helper" run "$native_iwd" "$native_sandbox"
