#!/usr/bin/env bash
# CMS IR5 material+field production paired to the canonical v47 ATLAS run.
set -euo pipefail

campaign="${1:?usage: run_cms_lss_v47_comparison.sh CAMPAIGN [run_condor options]}"
shift
workflow_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace_dir="$(cd "$workflow_dir/.." && pwd)"

# This comparison is a frozen recipe. Do not inherit unrelated SHIFT study
# switches from the submit shell.
for inherited_setting in "${!SHIFT_@}"; do
  unset "$inherited_setting"
done

source_dir="${CMS_LSS_SOURCE_DIR:-/afs/cern.ch/work/j/jniedzie/private/cms_lss_fluka_description}"
payload_dir="${CMS_LSS_PAYLOAD_DIR:-$source_dir/cms_ir5_2023_z1100}"
gdml="${CMS_LSS_GDML_FILE:-$payload_dir/geometry/lhc_ir5_2023_physical_z1100.gdml}"
field_dir="${CMS_LSS_FIELD_DATA_DIRECTORY:-$payload_dir/field_maps}"

[[ -f "$source_dir/MB.inp" ]] || {
  echo "ERROR: required provider include is missing: $source_dir/MB.inp" >&2
  exit 1
}

# Select the newest staged manifest which both closes all native includes and
# describes the exact geometry/maps that will be used. An explicit override is
# available when validated artifacts live elsewhere.
manifest="${CMS_LSS_PAYLOAD_MANIFEST:-}"
if [[ -z "$manifest" ]]; then
  manifest="$(python3 - "$workspace_dir/validation/cms_lss_20260922" "$gdml" "$field_dir" <<'PY'
import hashlib
import json
from pathlib import Path
import sys

root, gdml, fields = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
required = [gdml, *(fields / name for name in ("MQXA.dat", "MQXB.dat", "MBXW.dat", "MQYana.dat"))]

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

matches = []
for candidate in sorted(root.glob("cmssw_payload_*/payload_manifest.json")):
    try:
        report = json.loads(candidate.read_text())
        recorded = {Path(name).name: value["sha256"] for name, value in report["files"].items()}
    except (KeyError, OSError, ValueError, TypeError):
        continue
    if (report.get("staging_complete") is True
            and report.get("unresolved_native_includes_not_consumed") == []
            and all(path.is_file() and recorded.get(path.name) == digest(path) for path in required)):
        matches.append(candidate)
if not matches:
    raise SystemExit("No complete staged manifest matches the CMS GDML and field maps")
print(matches[-1])
PY
)" || {
    echo "ERROR: no MB-complete staged CMS payload matches $payload_dir" >&2
    exit 1
  }
fi

python3 - "$workflow_dir/scripts/audit_cms_lss_source.py" "$source_dir" "$manifest" "$gdml" "$field_dir" <<'PY'
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

audit_script, source_dir, manifest_path, gdml, field_dir = map(Path, sys.argv[1:])
spec = importlib.util.spec_from_file_location("audit_cms_lss_source", audit_script)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
source = module.audit_bundle(source_dir)
deck = source["decks"].get("lhc_IR5_2023-2024.inp")
if not deck or not deck["preprocessing_complete"] or not deck["source_complete"]:
    raise SystemExit("CMS 2023 source deck is incomplete after resolving MB.inp")

manifest = json.loads(manifest_path.read_text())
if (manifest.get("staging_complete") is not True
        or manifest.get("unresolved_native_includes_not_consumed") != []):
    raise SystemExit("CMS staged payload still records unresolved native includes")
recorded = {Path(name).name: value["sha256"] for name, value in manifest["files"].items()}

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

for path in [gdml, *(field_dir / name for name in
                      ("MQXA.dat", "MQXB.dat", "MBXW.dat", "MQYana.dat"))]:
    if not path.is_file() or recorded.get(path.name) != digest(path):
        raise SystemExit(f"staged payload checksum mismatch: {path}")
PY

export CAMPAIGN_NAME="$campaign"
export SAMPLE_BASE="${SAMPLE_BASE:-/eos/home-j/jniedzie/shift_cmssw}" SAMPLE_NAME=jpsi
export PROCESS=Charmonium_FixedTarget_pThat_1to5GeV_13p6TeV
export N_JOBS=1000 N_EVENTS=10 STEP4_INPUTS_PER_JOB=1
export GENERATOR_SEED=13579 SIMULATION_SEED=24680
export COLLISION_YEAR=2023 PILEUP_MODE=none
export TRIGGER_SCENARIO=none TRIGGER_TIMELINE_MODE=none
export SHIFT_TIMING_MODE=nominal SHIFT_TIMING_BEAM_DIRECTION_Z=-1
export SHIFT_TIMING_BX_OFFSET=0 SHIFT_TIMING_PHASE_NS=0.0
export SHIFT_TIMING_FIXED_OFFSET_NS=0.0 SHIFT_TIMING_CMS_REFERENCE_Z_MM=0.0
export SHIFT_TIMING_BUNCH_SPACING_NS=25.0 SHIFT_TIMING_LEGACY_OFFSET_CT_MM=-148000.0
export SHIFT_TIMING_MODEL_VERSION=fixed-target-v1
export SHIFT_G4_MAX_TRACK_TIME_NS=5000.0 SHIFT_G4_MAX_TRACK_TIME_FORWARD_NS=5000.0
export SHIFT_TO_CMS_TRANSPORT=1

export CMSSW_USE_BIGLIB=0 ENABLE_EXONANOAOD=0 N_THREADS=1 N_STREAMS=0
export DEBUG_MUON_PRIMARIES=0 DEBUG_MUON_HITS=0 DEBUG_MUON_TRACKING=0
export TRACE_PRIMARY_MUON_PATHS=0
export CONDOR_REQUEST_MEMORY_MB=4000 CONDOR_JOB_FLAVOUR=workday

# Exact v47 reconstruction recipe.
export SHIFT_TARGET_DETAILED_MATERIAL=1 SHIFT_TARGET_NUMERICAL_COVARIANCE=0
export SHIFT_TARGET_CONSISTENT_BACKWARD_COVARIANCE=1
export SHIFT_TARGET_MEAN_ENERGY_LOSS_JACOBIAN=1
export SHIFT_TARGET_UNQUENCHED_IONIZATION_VARIANCE=1
export SHIFT_TARGET_FIELD_GRADIENT_JACOBIAN=1 SHIFT_TARGET_MOMENT_FIT=1
export SHIFT_USE_VERTEX_CONSTRAINED_REFIT=1
export SHIFT_USE_MATERIAL_AWARE_VERTEX_TRANSPORT=0
export SHIFT_USE_FORWARD_COMMON_VERTEX_FIT=0
export SHIFT_REFIT_SEED_MOMENTUM_SCALE=1.0
export SHIFT_REFIT_SECOND_SEED_ERROR_RESCALE=100.0
export SHIFT_REFIT_USE_SECOND_ITERATION=0 SHIFT_REFIT_ENERGY_LOSS_SCALE=1.0
export SHIFT_REFIT_DETAILED_MATERIAL_EFFECTS=0
export SHIFT_REFIT_GEOMETRY_MATERIAL_EFFECTS=0
export SHIFT_REFIT_GEOMETRY_MATERIAL_FITTER=0
export SHIFT_REFIT_GEOMETRY_MATERIAL_SMOOTHER=0
export SHIFT_REFIT_GEOMETRY_TARGET_MATERIAL=0
export SHIFT_REFIT_LOG_GEOMETRY_COMPARISON=0

# The sole physics-model change relative to v47: CMS IR5 material and field.
# Event generation remains on the +148 m side; the LSS payload is placed on
# both sides with the second copy related by a 180-degree y rotation.
export SHIFT_LSS_MATERIAL_MODE=external SHIFT_LSS_FIELD_MODE=cms_ir5_2023_z1100
export SHIFT_LSS_SYMMETRIC_TWO_SIDED=true
export SHIFT_LSS_GDML_FILE="$gdml"
export SHIFT_LSS_GDML_SHA256="$(python3 - "$gdml" <<'PY'
import hashlib
from pathlib import Path
import sys
print(hashlib.sha256(Path(sys.argv[1]).read_bytes()).hexdigest())
PY
)"
export SHIFT_LSS_FIELD_DATA_DIRECTORY="$field_dir"
export SHIFT_LSS_ARTIFACT_ORIGIN_IN_MODEL_CM=-500.0,2550.0,9550.5
export SHIFT_LSS_MODEL_ORIGIN_CM=0,0,0 SHIFT_LSS_MODEL_TO_CMS=1,0,0,0,1,0,0,0,1
export SHIFT_LSS_MINIMUM_ABS_Z_CM=1100 SHIFT_LSS_MATERIAL_BOUNDARY_ABS_Z_CM=14800
export SHIFT_LSS_GEANT4E_MOMENTUM_LIMIT_GEV=0.05
export SHIFT_LSS_GEANT4E_MAXIMUM_STEP_LENGTH_MM=0.2
export SHIFT_LSS_GEANT4E_MAXIMUM_PATH_LENGTH_CM=20000 SHIFT_LSS_FIELD_SCALE=1.0

unset SAMPLE_DIR SAMPLES_DIR CONFIG_BASE_DIR WORKDIR LOG_DIR CROSS_SECTION_FILE
unset STEP1_DIR STEP2_DIR STEP3_DIR STEP4_DIR
unset STEP1_CONFIG_DIR STEP2_CONFIG_DIR STEP3_CONFIG_DIR STEP4_CONFIG_DIR
unset TRIGGER_TIMELINE_DIR PIGGYBACK_DECISION_DIR
unset GEOMETRY ERA CONDITIONS BEAMSPOT HLT_MENU

exec "$workflow_dir/run_condor.sh" --prebuilt --keep-logs --steps 1,2,3,4 "$@"
