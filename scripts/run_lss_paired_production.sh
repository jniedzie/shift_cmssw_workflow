#!/usr/bin/env bash
# Reproducible IR1/ATLAS proxy comparison; caller controls sample size.
set -euo pipefail
mode="${1:?control, material, or combined}"
campaign="${2:?new campaign name}"
shift 2
workflow_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export CAMPAIGN_NAME="$campaign"
export N_JOBS="${N_JOBS:-1000}" N_EVENTS="${N_EVENTS:-10}"
export GENERATOR_SEED=13579 SIMULATION_SEED=24680
export COLLISION_YEAR=2023 PILEUP_MODE=none TRIGGER_SCENARIO=none TRIGGER_TIMELINE_MODE=none
export SHIFT_TIMING_MODE=nominal SHIFT_TIMING_BX_OFFSET=0 SHIFT_TIMING_PHASE_NS=0.0
# Full Geant4 point traces are deliberately opt-in: at production scale they
# duplicate multi-gigabyte logs on the submit host.  The reconstruction output
# remains sufficient for the paired efficiency and response comparison.
export DEBUG_MUON_PRIMARIES="${DEBUG_MUON_PRIMARIES:-0}"
export DEBUG_MUON_TRACKING="${DEBUG_MUON_TRACKING:-0}"
export TRACE_PRIMARY_MUON_PATHS="${TRACE_PRIMARY_MUON_PATHS:-0}"
export CONDOR_REQUEST_MEMORY_MB=4000
export CONDOR_JOB_FLAVOUR=workday
export SHIFT_LSS_MATERIAL_MODE=none SHIFT_LSS_FIELD_MODE=none
case "$mode" in
  control) ;;
  material) export SHIFT_LSS_MATERIAL_MODE=external ;;
  combined) export SHIFT_LSS_MATERIAL_MODE=external SHIFT_LSS_FIELD_MODE=ir1_atlas_proxy ;;
  *) echo "Unknown mode: $mode" >&2; exit 2 ;;
esac
export SHIFT_LSS_GDML_FILE=PhysicsTools/ShiftLssGeometry/data/ir1_atlas_proxy/lhc_ir1_halfspace_repaired_20260910.gdml
export SHIFT_LSS_GDML_SHA256=d6f9f6fa5658a55e365d5a80025522cac7bcab1ce6fb006ca27b5120f5f41f66
export SHIFT_LSS_ARTIFACT_ORIGIN_IN_MODEL_CM=0.0,4299.5,14575.200000105498
export SHIFT_LSS_MODEL_ORIGIN_CM=0,0,0 SHIFT_LSS_MODEL_TO_CMS=1,0,0,0,1,0,0,0,1
export SHIFT_LSS_MINIMUM_ABS_Z_CM=1100 SHIFT_LSS_MATERIAL_BOUNDARY_ABS_Z_CM=14800
export SHIFT_LSS_GEANT4E_MAXIMUM_STEP_LENGTH_MM=0.2
export SHIFT_USE_VERTEX_CONSTRAINED_REFIT=${SHIFT_USE_VERTEX_CONSTRAINED_REFIT:-1}
export SHIFT_LSS_GEANT4E_MAXIMUM_PATH_LENGTH_CM=20000 SHIFT_LSS_FIELD_SCALE=1.0
exec "$workflow_dir/run_condor.sh" --prebuilt --keep-logs "$@"
