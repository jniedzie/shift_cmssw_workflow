#!/usr/bin/env bash
# Four-mode reconstruction comparison. See the workspace SHIFT_RECONSTRUCTION.md.
set -euo pipefail

usage() {
  echo "Usage: $(basename "$0") control|material|field|combined [--check]"
  echo "Defaults to 1000 chunks of 10 events; N_JOBS may select a smaller prefix."
  echo "CHUNK_START may skip an already validated prefix for control or combined."
  echo "Control and combined reuse existing simulation; material and field run Steps 1-4."
}
[[ $# -ge 1 && $# -le 2 ]] || { usage >&2; exit 2; }
mode="$1"
check_only=0
if [[ $# == 2 ]]; then
  [[ "$2" == --check ]] || { usage >&2; exit 2; }
  check_only=1
fi
case "$mode" in
  control|material|field) campaign="lssPaired_${mode}_10k_2023_v3" ;;
  combined) campaign=lssPaired_materialField_10k_2023_v3 ;;
  *) usage >&2; exit 2 ;;
esac
requested_jobs="${N_JOBS:-1000}"
[[ "$requested_jobs" =~ ^[1-9][0-9]*$ ]] && (( requested_jobs <= 1000 )) || {
  echo "N_JOBS must select a prefix of 1 through 1000 chunks" >&2; exit 2;
}
chunk_start="${CHUNK_START:-0}"
[[ "$chunk_start" =~ ^(0|[1-9][0-9]*)$ ]] && (( chunk_start < requested_jobs )) || {
  echo "CHUNK_START must be an integer from 0 through N_JOBS-1" >&2; exit 2;
}
if [[ "$mode" == material || "$mode" == field ]]; then
  [[ "$chunk_start" == 0 ]] || { echo "CHUNK_START is supported for Step-4-only reuse" >&2; exit 2; }
fi
[[ "${N_EVENTS:-10}" == 10 && "${STEP4_INPUTS_PER_JOB:-1}" == 1 ]] || {
  echo "The paired input contract requires N_EVENTS=10 and STEP4_INPUTS_PER_JOB=1" >&2
  exit 2
}
[[ -z "${CONDOR_CHUNKS_FILE:-}" ]] || {
  echo "This wrapper selects complete paired chunk sets; unset CONDOR_CHUNKS_FILE" >&2
  exit 2
}
workflow_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
launcher="$workflow_dir/scripts/run_lss_paired_production.sh"
# This is one fixed comparison recipe, not a study-settings passthrough.
# Restore all SHIFT controls from the explicit settings below and the paired
# launcher's/workflow's defaults, including timing and SimHit-reference inputs.
for inherited_setting in "${!SHIFT_@}"; do
  unset "$inherited_setting"
done
export SAMPLE_BASE="${SAMPLE_BASE:-/eos/home-j/jniedzie/shift_cmssw}" SAMPLE_NAME=jpsi
export PROCESS=Charmonium_FixedTarget_pThat_1to5GeV_13p6TeV
export N_EVENTS=10 STEP4_INPUTS_PER_JOB=1
export CMSSW_USE_BIGLIB=0 ENABLE_EXONANOAOD=0 N_THREADS=1 N_STREAMS=0
export DEBUG_MUON_PRIMARIES=0 DEBUG_MUON_HITS=0 DEBUG_MUON_TRACKING=0 TRACE_PRIMARY_MUON_PATHS=0
export SHIFT_TARGET_DETAILED_MATERIAL=1 SHIFT_TARGET_CONSISTENT_BACKWARD_COVARIANCE=1
export SHIFT_TARGET_MEAN_ENERGY_LOSS_JACOBIAN=1 SHIFT_TARGET_FIELD_GRADIENT_JACOBIAN=1
export SHIFT_TARGET_UNQUENCHED_IONIZATION_VARIANCE=1 SHIFT_TARGET_MOMENT_FIT=1
export SHIFT_TARGET_NUMERICAL_COVARIANCE=0 SHIFT_USE_VERTEX_CONSTRAINED_REFIT=1
unset SAMPLE_DIR SAMPLES_DIR CONFIG_BASE_DIR WORKDIR LOG_DIR CROSS_SECTION_FILE
unset STEP1_DIR STEP2_DIR STEP3_DIR STEP4_DIR
unset STEP1_CONFIG_DIR STEP2_CONFIG_DIR STEP3_CONFIG_DIR STEP4_CONFIG_DIR
unset TRIGGER_TIMELINE_DIR PIGGYBACK_DECISION_DIR
unset GEOMETRY ERA CONDITIONS BEAMSPOT HLT_MENU

# Never silently keep a Step-4 result made with an older target-fit recipe.
# ROOT integrity/event-count validation remains the stage script's job.
python3 - "$SAMPLE_BASE/$SAMPLE_NAME/$campaign" "$mode" "$chunk_start" <<'PY'
import ast
from pathlib import Path
import sys

campaign, mode = Path(sys.argv[1]), sys.argv[2]
for chunk in range(int(sys.argv[3])):
    output = campaign / 'samples/step4' / f'events_NanoAOD_part_{chunk:04d}.root'
    if not output.is_file() or output.stat().st_size == 0:
        raise SystemExit(f'Cannot skip missing prefix output: {output}')
required = {
    'targetUseNumericalTransportCovariance': False,
    'targetUseConsistentBackwardCovariance': True,
    'targetUseMeanEnergyLossJacobian': True,
    'targetUseUnquenchedIonizationVariance': True,
    'targetUseFieldGradientJacobian': True,
    'targetUseForwardRefit': True,
    'targetUseMomentFit': True,
    'targetForwardMaxIterations': 32,
    'directionalRefitSeedMomentumScale': 1.0,
    'directionalRefitSecondSeedErrorRescale': 100.0,
    'directionalRefitUseSecondIteration': False,
    'directionalRefitEnergyLossScale': 1.0,
    'directionalRefitLogGeometryMaterialComparison': False,
}
required_customisation = {
    'targetUseDetailedMaterialPropagation': True,
    'useDetailedMaterialPropagation': mode in ('material', 'combined'),
    'directionalRefitUseDetailedMaterialEffects': False,
    'directionalRefitUseGeometryMaterialEffects': False,
    'directionalRefitUseGeometryMaterialEffectsInFitter': False,
    'directionalRefitUseGeometryMaterialEffectsInSmoother': False,
    'directionalRefitUseGeometryTargetMaterialEffects': False,
    'enableHcalDiagnostics': False,
    'enableZDCDiagnostics': False,
    'augmentDTHits': True,
    'augmentTrackerHits': False,
    'useExtendedTiming': False,
    'useVertexConstrainedRefit': True,
}

def scalar(node):
    if isinstance(node, ast.Call) and len(node.args) == 1:
        return ast.literal_eval(node.args[0])
    raise ValueError('expected one literal configuration value')

for output in sorted((campaign / 'samples/step4').glob('events_NanoAOD_part_*.root')):
    config = campaign / 'configs/step4' / (output.stem + '_cfg.py')
    if not config.is_file():
        raise SystemExit(f'Existing output has no archived configuration: {output}')
    tree = ast.parse(config.read_text())
    values, customisations, contract = {}, [], None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Attribute) and target.attr in required:
                values[target.attr] = scalar(node.value)
            if isinstance(target, ast.Attribute) and target.attr == 'shiftLssWorkflowContract':
                contract = {item.arg: scalar(item.value) for item in node.value.keywords
                            if item.arg in ('materialMode', 'fieldMode')}
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'customise':
            customisations.append({item.arg: ast.literal_eval(item.value) for item in node.keywords})
    expected_contract = {
        'materialMode': 'external' if mode in ('material', 'combined') else 'none',
        'fieldMode': 'ir1_atlas_proxy' if mode in ('field', 'combined') else 'none',
    }
    if (values != required or len(customisations) != 1
            or customisations[0] != required_customisation
            or (mode != 'control' and contract != expected_contract)
            or (mode == 'control' and contract not in (None, expected_contract))):
        raise SystemExit(f'Existing Step-4 configuration is incompatible with this campaign: {config}')
PY

# The site setup defines cmsenv as a shell function needed by the submission
# shell. Export it only for real submissions; --check stays read-only.
if (( ! check_only )) && ! command -v cmsenv >/dev/null 2>&1; then
  source /cvmfs/cms.cern.ch/cmsset_default.sh
fi
if (( ! check_only )); then
  export -f cmsenv
fi

if [[ "$mode" == material || "$mode" == field ]]; then
  # Changed transport changes SimHits, digis and AOD: no upstream stage from
  # another material/field mode is reusable. Existing valid outputs in this
  # same campaign are reused by the stage scripts.
  N_JOBS="$requested_jobs" "$launcher" "$mode" "$campaign" --steps 1,2,3,4 --check
  (( check_only )) && exit 0
  exec env N_JOBS="$requested_jobs" "$launcher" "$mode" "$campaign" --steps 1,2,3,4
fi

manifest_dir="$(mktemp -d /tmp/shift_lss_fourway_XXXXXX)"
trap 'rm -rf -- "$manifest_dir"' EXIT
python3 - "$SAMPLE_BASE/$SAMPLE_NAME" "$mode" "$requested_jobs" "$manifest_dir" "$chunk_start" <<'PY'
from pathlib import Path
import re
import sys

base, mode, count, destination = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3]), Path(sys.argv[4])
stem = 'control' if mode == 'control' else 'materialField'
primary = base / f'lssPaired_{stem}_10k_2023_v2'
fallback = base / 'lssPaired_control_10k_2023_v1'
groups = {'primary': [], 'fallback': []}

def normalized_config(path, ignore_chunk=False):
    text = '\n'.join(line for line in path.read_text().splitlines()
                     if not line.startswith('# with command line'))
    text = re.sub(r'lssPaired_control_10k_2023_v[12]', 'CONTROL_CAMPAIGN', text)
    text = re.sub(r'/tmp/shift_cmssw_step[123]_[A-Za-z0-9]+', '/tmp/STEP_WORK', text)
    for name in ('DebugMuonPrimaries', 'DebugMuonPrimaryFates', 'TracePrimaryTracksForVisualization'):
        text = re.sub(r'(' + name + r' = cms.untracked.bool\()True(\))', r'\1False\2', text)
    if ignore_chunk:
        text = re.sub(r'part\d{4}', 'partNNNN', text)
    return text

for chunk in range(int(sys.argv[5]), count):
    filename = f'events_AOD_part{chunk:04d}.root'
    path = primary / 'samples/step3' / filename
    if path.is_file() and path.stat().st_size > 0:
        groups['primary'].append(chunk)
        continue
    alternate = fallback / 'samples/step3' / filename
    if mode != 'control' or not alternate.is_file() or alternate.stat().st_size == 0:
        raise SystemExit(f'Missing required Step-3 input: {path}')
    # v1/v2 payload equality was independently checked on 30 events. Require
    # each fallback's archived production settings to agree, allowing only
    # logging and output/campaign path differences, before mixing chunks.
    for step in (1, 2, 3):
        paths = [list((root / f'configs/step{step}').glob(f'*part{chunk:04d}*_cfg.py'))
                 for root in (primary, fallback)]
        # Failed upstream jobs can leave no downstream v2 config. Steps 2/3
        # differ by filenames only: compare against the audited v2 template.
        # Step 1 must always retain its exact per-chunk generator/simulation seeds.
        template = step > 1 and not paths[0]
        if template:
            paths[0] = list((primary / f'configs/step{step}').glob('*part0000*_cfg.py'))
        if any(len(matches) != 1 for matches in paths):
            raise SystemExit(f'Ambiguous or missing Step-{step} config for fallback chunk {chunk}')
        if normalized_config(paths[0][0], template) != normalized_config(paths[1][0], template):
            raise SystemExit(f'Control Step-{step} config mismatch for fallback chunk {chunk}')
    groups['fallback'].append(chunk)

for name, chunks in groups.items():
    (destination / f'{name}.txt').write_text(''.join(f'{chunk}\n' for chunk in chunks))
    source = primary if name == 'primary' else fallback
    print(f'{name}: {len(chunks)} chunks from {source}/samples/step3')
PY

submit_group() {
  local group="$1" check="$2" source
  local manifest="$manifest_dir/$group.txt"
  [[ -s "$manifest" ]] || return 0
  if [[ "$group" == fallback ]]; then
    source="$SAMPLE_BASE/$SAMPLE_NAME/lssPaired_control_10k_2023_v1"
  elif [[ "$mode" == control ]]; then
    source="$SAMPLE_BASE/$SAMPLE_NAME/lssPaired_control_10k_2023_v2"
  else
    source="$SAMPLE_BASE/$SAMPLE_NAME/lssPaired_materialField_10k_2023_v2"
  fi
  local args=(--steps 4)
  [[ "$check" == 1 ]] && args+=(--check)
  N_JOBS="$(wc -l < "$manifest")" CONDOR_CHUNKS_FILE="$manifest" \
    STEP3_DIR="$source/samples/step3" STEP1_CONFIG_DIR="$source/configs/step1" \
    "$launcher" "$mode" "$campaign" "${args[@]}"
}
# Preflight every source before the first submission.
submit_group primary 1
submit_group fallback 1
(( check_only )) && exit 0
submit_group primary 0
submit_group fallback 0
