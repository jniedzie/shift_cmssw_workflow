#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKFLOW_ROOT="$SCRIPT_DIR"

usage() {
	cat <<EOF
Usage: $(basename "$0") [--steps LIST] [--force] [--prebuilt] [--keep-logs] [--check]
       $(basename "$0") --force-steps LIST [--prebuilt] [--keep-logs]

Submit all configured jobs, running only the selected workflow steps.
LIST is a comma-separated subset of 1,2,3,4 ("step1" ... "step4" are also
accepted).  With no options, all steps run and existing valid outputs are
reused.  --force removes and recreates outputs for every selected step;
--force-steps LIST is shorthand for --steps LIST --force.
--prebuilt skips the submission-side SCRAM build after one explicit successful
build, allowing several configuration-only scans to share the same libraries.
Before submitting, logs from completed older jobs are removed from the local
Condor log directory and, when no older workflow jobs are active, from EOS.
--keep-logs disables this automatic cleanup.
--check validates configuration and trigger inputs without building, cleaning,
or contacting Condor.

Examples:
  $(basename "$0") --steps 3,4
  $(basename "$0") --force-steps 4
EOF
}

SELECTED_STEPS="1,2,3,4"
FORCE_SELECTED=0
USE_PREBUILT=0
KEEP_LOGS=0
CHECK_ONLY=0
while (( $# )); do
	case "$1" in
		--steps)
			[[ $# -ge 2 ]] || { echo "ERROR: --steps requires a list" >&2; exit 2; }
			SELECTED_STEPS="$2"
			shift 2
			;;
		--force-steps)
			[[ $# -ge 2 ]] || { echo "ERROR: --force-steps requires a list" >&2; exit 2; }
			SELECTED_STEPS="$2"
			FORCE_SELECTED=1
			shift 2
			;;
		-f|--force)
			FORCE_SELECTED=1
			shift
			;;
		--prebuilt)
			USE_PREBUILT=1
			shift
			;;
		--keep-logs)
			KEEP_LOGS=1
			shift
			;;
		--check)
			CHECK_ONLY=1
			shift
			;;
		-h|--help)
			usage
			exit 0
			;;
		*)
			echo "ERROR: unknown option: $1" >&2
			usage >&2
			exit 2
			;;
	esac
done

NORMALIZED_STEPS=""
IFS=',' read -r -a requested_steps <<< "$SELECTED_STEPS"
for requested_step in "${requested_steps[@]}"; do
	case "$requested_step" in
		1|step1) step=1 ;;
		2|step2) step=2 ;;
		3|step3) step=3 ;;
		4|step4) step=4 ;;
		*) echo "ERROR: invalid step '$requested_step'; expected a comma-separated subset of 1,2,3,4" >&2; exit 2 ;;
	esac
	if [[ ",$NORMALIZED_STEPS," != *",$step,"* ]]; then
		NORMALIZED_STEPS="${NORMALIZED_STEPS:+$NORMALIZED_STEPS,}$step"
	fi
done
[[ -n "$NORMALIZED_STEPS" ]] || { echo "ERROR: no workflow steps selected" >&2; exit 2; }

source "$SCRIPT_DIR/config/workflow.env"
source "$SCRIPT_DIR/scripts/configure_lss.sh"
configure_shift_lss

# The submit description has an explicit `environment` attribute, so campaign
# identity, pileup, and reconstruction settings must be serialized rather than
# inherited.
SUBMISSION_VARIABLES=(
	COLLISION_YEAR
	GEOMETRY
	ERA
	CONDITIONS
	BEAMSPOT
	PILEUP_MODE
	PILEUP_SCENARIO
	PILEUP_DATASET
	PILEUP_INPUT
	PILEUP_SEED
	PILEUP_SEQUENTIAL
	SHIFT_TIMING_MODE
	SHIFT_TIMING_BEAM_DIRECTION_Z
	SHIFT_TIMING_BX_OFFSET
	SHIFT_TIMING_PHASE_NS
	SHIFT_TIMING_FIXED_OFFSET_NS
	SHIFT_TIMING_CMS_REFERENCE_Z_MM
	SHIFT_TIMING_BUNCH_SPACING_NS
	SHIFT_TIMING_LEGACY_OFFSET_CT_MM
	SHIFT_TIMING_MODEL_VERSION
	SHIFT_G4_MAX_TRACK_TIME_NS
	SHIFT_G4_MAX_TRACK_TIME_FORWARD_NS
	SHIFT_LSS_MATERIAL_MODE
	SHIFT_LSS_FIELD_MODE
	SHIFT_LSS_GDML_FILE
	SHIFT_LSS_GDML_SHA256
	SHIFT_LSS_MODEL_ORIGIN_CM
	SHIFT_LSS_MODEL_TO_CMS
	SHIFT_LSS_MINIMUM_ABS_Z_CM
	SHIFT_LSS_MATERIAL_BOUNDARY_ABS_Z_CM
	SHIFT_LSS_GEANT4E_MOMENTUM_LIMIT_GEV
	SHIFT_LSS_GEANT4E_MAXIMUM_STEP_LENGTH_MM
	SHIFT_LSS_GEANT4E_MAXIMUM_PATH_LENGTH_CM
	SHIFT_LSS_FIELD_SCALE
	SHIFT_LSS_DETECTOR_ELEMENT_NAME
	SHIFT_LSS_OVERLAP_TOLERANCE_CM
	TRIGGER_SCENARIO
	TRIGGER_TIMELINE_MODE
	TRIGGER_LIBRARY_JSONL
	TRIGGER_L1_MENU_JSON
	TRIGGER_GROUP_ID
	TRIGGER_TIMELINE_START_BX
	TRIGGER_TIMELINE_END_BX
	TRIGGER_TIMELINE_SEED
	TRIGGER_COLLIDING_BX_FILE
	TRIGGER_COLLIDING_BX_MASK
	TRIGGER_REFERENCE_SLOT_MODE
	TRIGGER_REFERENCE_BX_SLOT
	TRIGGER_SHIFT_BEAM
	TRIGGER_RUN_FILL_MAP
	TRIGGER_RULE_MODE
	TRIGGER_RULE_HISTORY_START_BX
	TRIGGER_TIMELINE_DIR
	PIGGYBACK_DECISION_DIR
	PIGGYBACK_FILTER_RECONSTRUCTION
	PIGGYBACK_FILTER_LEVEL
	SHIFT_DT_MODE
	SHIFT_TRACKER_MODE
	SHIFT_ENABLE_GEM
	SHIFT_ENABLE_HCAL_DIAGNOSTICS
	SHIFT_ENABLE_ZDC_DIAGNOSTICS
	SHIFT_AUGMENT_DT_HITS
	SHIFT_AUGMENT_TRACKER_HITS
	SHIFT_USE_EXTENDED_TIMING
	SHIFT_REFIT_SECOND_SEED_ERROR_RESCALE
	SHIFT_REFIT_USE_SECOND_ITERATION
	SHIFT_REFIT_GEOMETRY_TARGET_MATERIAL
)
for variable_name in "${SUBMISSION_VARIABLES[@]}"; do
	export "$variable_name"
done

if [[ ! "$N_JOBS" =~ ^[1-9][0-9]*$ ]]; then
	printf 'N_JOBS must be a positive integer (got: %s)\n' "$N_JOBS" >&2
	exit 1
fi
if [[ ! "$STEP4_INPUTS_PER_JOB" =~ ^[1-9][0-9]*$ ]]; then
	printf 'STEP4_INPUTS_PER_JOB must be a positive integer (got: %s)\n' "$STEP4_INPUTS_PER_JOB" >&2
	exit 1
fi
case "$PILEUP_SEQUENTIAL" in
	0|1) ;;
	*) printf 'PILEUP_SEQUENTIAL must be 0 or 1 (got: %s)\n' "$PILEUP_SEQUENTIAL" >&2; exit 1 ;;
esac
if [[ "$STEP4_INPUTS_PER_JOB" != 1 && "$NORMALIZED_STEPS" != 4 ]]; then
	echo "STEP4_INPUTS_PER_JOB > 1 is only safe for a Step-4-only submission" >&2
	exit 1
fi
for variable_name in CONDOR_REQUEST_CPUS CONDOR_REQUEST_MEMORY_MB CONDOR_MAX_MATERIALIZE; do
	variable_value="${!variable_name}"
	if [[ ! "$variable_value" =~ ^[1-9][0-9]*$ ]]; then
		printf '%s must be a positive integer (got: %s)\n' "$variable_name" "$variable_value" >&2
		exit 1
	fi
done
for refit_variable in SHIFT_REFIT_SEED_MOMENTUM_SCALE SHIFT_REFIT_ENERGY_LOSS_SCALE; do
	refit_value="${!refit_variable}"
	if [[ ! "$refit_value" =~ ^[0-9]+([.][0-9]+)?$ ]] || [[ "$refit_value" =~ ^0+([.]0+)?$ ]]; then
		printf '%s must be a positive decimal (got: %s)\n' "$refit_variable" "$refit_value" >&2
		exit 1
	fi
done
case "$SHIFT_REFIT_DETAILED_MATERIAL_EFFECTS" in
	0|1) ;;
	*) printf 'SHIFT_REFIT_DETAILED_MATERIAL_EFFECTS must be 0 or 1 (got: %s)\n' "$SHIFT_REFIT_DETAILED_MATERIAL_EFFECTS" >&2; exit 1 ;;
esac
case "$SHIFT_REFIT_GEOMETRY_MATERIAL_EFFECTS" in
	0|1) ;;
	*) printf 'SHIFT_REFIT_GEOMETRY_MATERIAL_EFFECTS must be 0 or 1 (got: %s)\n' "$SHIFT_REFIT_GEOMETRY_MATERIAL_EFFECTS" >&2; exit 1 ;;
esac
for split_geometry_variable in SHIFT_REFIT_GEOMETRY_MATERIAL_FITTER SHIFT_REFIT_GEOMETRY_MATERIAL_SMOOTHER SHIFT_REFIT_LOG_GEOMETRY_COMPARISON; do
	case "${!split_geometry_variable}" in
		0|1) ;;
		*) printf '%s must be 0 or 1 (got: %s)\n' "$split_geometry_variable" "${!split_geometry_variable}" >&2; exit 1 ;;
	esac
done
if [[ "$SHIFT_REFIT_DETAILED_MATERIAL_EFFECTS" == 1 &&
      ( "$SHIFT_REFIT_GEOMETRY_MATERIAL_EFFECTS" == 1 || "$SHIFT_REFIT_GEOMETRY_MATERIAL_FITTER" == 1 || "$SHIFT_REFIT_GEOMETRY_MATERIAL_SMOOTHER" == 1 ) ]]; then
	echo "Detailed refit material and geometry refit material modes are mutually exclusive" >&2
	exit 1
fi
if [[ "$SHIFT_REFIT_LOG_GEOMETRY_COMPARISON" == 1 && "$SHIFT_REFIT_GEOMETRY_MATERIAL_EFFECTS" == 0 && "$SHIFT_REFIT_GEOMETRY_MATERIAL_FITTER" == 0 && "$SHIFT_REFIT_GEOMETRY_MATERIAL_SMOOTHER" == 0 ]]; then
	echo "SHIFT_REFIT_LOG_GEOMETRY_COMPARISON requires a geometry material mode" >&2
	exit 1
fi
if [[ "$SHIFT_LSS_MATERIAL_MODE" == external && "$SHIFT_REFIT_GEOMETRY_TARGET_MATERIAL" == 1 ]]; then
	echo "SHIFT_LSS_MATERIAL_MODE=external selects detailed target-leg navigation and is mutually exclusive with SHIFT_REFIT_GEOMETRY_TARGET_MATERIAL=1" >&2
	exit 1
fi
if [[ ",$NORMALIZED_STEPS," == *,4,* &&
      ( "$SHIFT_LSS_MATERIAL_MODE" != none || "$SHIFT_LSS_FIELD_MODE" != none ) ]]; then
	if [[ "${AOD_TO_EXONANO_CUSTOMISE:-}" != "PhysicsTools/ShiftMuonSegments/shiftMuonSegments_customise.customise" ]]; then
		echo "LSS reconstruction requires the canonical ShiftMuonSegments Step-4 customisation" >&2
		exit 1
	fi
fi

if [[ "$TRIGGER_SCENARIO" == piggyback_central ]]; then
	[[ "$PILEUP_MODE" == standard && "$SHIFT_TIMING_MODE" == nominal ]] || {
		echo "Piggyback preflight requires standard pileup and nominal physical SHIFT timing" >&2
		exit 1
	}
	python3 - "$SHIFT_TIMING_PHASE_NS" "$SHIFT_TIMING_BUNCH_SPACING_NS" <<'PY'
import math
import sys

phase = float(sys.argv[1])
spacing = float(sys.argv[2])
if not math.isfinite(phase) or not math.isfinite(spacing) or spacing <= 0.0:
    raise SystemExit("Piggyback timing phase and bunch spacing must be finite, with positive spacing")
if phase < 0.0 or phase >= spacing:
    raise SystemExit("Piggyback timing requires 0 <= SHIFT_TIMING_PHASE_NS < SHIFT_TIMING_BUNCH_SPACING_NS")
PY
	[[ "$TRIGGER_TIMELINE_MODE" == zero_bias_proxy && "$TRIGGER_TIMELINE_START_BX" == 0 && "$TRIGGER_TIMELINE_END_BX" == 0 && "$TRIGGER_RULE_MODE" == recorded ]] || {
		echo "Piggyback preflight requires a BX-0 ZeroBias timeline in recorded-L1A mode" >&2
		exit 1
	}
	[[ -z "$TRIGGER_COLLIDING_BX_FILE" ]] || {
		echo "Piggyback preflight rejects legacy relative-BX fixtures" >&2
		exit 1
	}
	[[ "$TRIGGER_TIMELINE_SEED" =~ ^[1-9][0-9]*$ && ${#TRIGGER_TIMELINE_SEED} -le 9 && 10#$TRIGGER_TIMELINE_SEED -le 900000000 ]] || {
		echo "Piggyback preflight requires a fixed trigger seed in 1..900000000" >&2
		exit 1
	}
	case "$PIGGYBACK_FILTER_RECONSTRUCTION:$PIGGYBACK_FILTER_LEVEL" in
		0:raw|0:persisted|1:raw|1:persisted) ;;
		*) echo "Invalid piggyback reconstruction filter configuration" >&2; exit 1 ;;
	esac
	for input_path in "$TRIGGER_LIBRARY_JSONL" "$TRIGGER_L1_MENU_JSON" "$TRIGGER_COLLIDING_BX_MASK" "$TRIGGER_RUN_FILL_MAP"; do
		[[ "$input_path" == /* && -s "$input_path" ]] || {
			echo "Piggyback preflight requires an absolute non-empty input: $input_path" >&2
			exit 1
		}
	done
	PIGGYBACK_PREFLIGHT_ARGS=(
		--l1-menu "$TRIGGER_L1_MENU_JSON"
		--start-bx 0 --end-bx 0 --signal-events 1 --seed "$TRIGGER_TIMELINE_SEED"
		--colliding-bx-mask "$TRIGGER_COLLIDING_BX_MASK"
		--reference-slot-mode "$TRIGGER_REFERENCE_SLOT_MODE"
		--shift-beam "$TRIGGER_SHIFT_BEAM"
		--run-fill-map "$TRIGGER_RUN_FILL_MAP"
		--trigger-rule-mode recorded
	)
	[[ -z "$TRIGGER_GROUP_ID" ]] || PIGGYBACK_PREFLIGHT_ARGS+=(--group-id "$TRIGGER_GROUP_ID")
	[[ "$TRIGGER_REFERENCE_SLOT_MODE" != fixed ]] || PIGGYBACK_PREFLIGHT_ARGS+=(--reference-bx-slot "$TRIGGER_REFERENCE_BX_SLOT")
	PIGGYBACK_PREFLIGHT_OUTPUT="$(mktemp /tmp/shift_piggyback_preflight_XXXXXX.jsonl)"
	if ! python3 "$SCRIPT_DIR/scripts/sample_zero_bias_trigger_timeline.py" \
		"$TRIGGER_LIBRARY_JSONL" --output "$PIGGYBACK_PREFLIGHT_OUTPUT" \
		"${PIGGYBACK_PREFLIGHT_ARGS[@]}"; then
		rm -f -- "$PIGGYBACK_PREFLIGHT_OUTPUT"
		echo "Piggyback trigger-input preflight failed; submission aborted" >&2
		exit 1
	fi
	rm -f -- "$PIGGYBACK_PREFLIGHT_OUTPUT"
	echo "Piggyback trigger-input preflight passed"
elif [[ "$TRIGGER_SCENARIO" != none ]]; then
	echo "TRIGGER_SCENARIO must be none or piggyback_central" >&2
	exit 1
fi

if [[ "$CHECK_ONLY" == 1 ]]; then
	echo "Configuration preflight passed; no build, cleanup, or submission performed"
	exit 0
fi

"$SCRIPT_DIR/scripts/prepare_condor.sh"

# Standard CERN batch schedds reject /eos paths in submit-file attributes
# such as output, error, and log.  Keep Condor's own bookkeeping logs on the
# shared AFS workflow area; the job payload continues to write CMSSW logs and
# ROOT outputs to the campaign directory on EOS.
CONDOR_LOG_DIR="$SCRIPT_DIR/condor/logs/$CAMPAIGN_NAME"
mkdir -p "$CONDOR_LOG_DIR"

# Build and register CMSSW once, then let explicit configuration-only scans
# reuse that release without relinking libraries between concurrent clusters.
if [[ "$USE_PREBUILT" == 1 ]]; then
	export CMSSW_PREPARED=1
	source "$SCRIPT_DIR/scripts/setup_cmssw.sh"
else
	unset CMSSW_PREPARED
	source "$SCRIPT_DIR/scripts/setup_cmssw.sh"
	export CMSSW_PREPARED=1
fi
CMSSW_RUNTIME_FINGERPRINT="$(cmssw_runtime_fingerprint)"
[[ -n "$CMSSW_RUNTIME_FINGERPRINT" ]] || {
	echo "ERROR: failed to fingerprint the built CMSSW runtime" >&2
	exit 1
}

# Workers share the AFS checkout and execute the stage scripts hours after
# submission. Freeze the small workflow tree so an unrelated edit cannot
# change a script while a running shell is reading it. The CMSSW release and
# campaign outputs remain at their configured shared locations.
SNAPSHOT_PARENT="$WORKFLOW_ROOT/condor/runtime_snapshots/$CAMPAIGN_NAME"
SNAPSHOT_ROOT="$SNAPSHOT_PARENT/$(date -u +%Y%m%dT%H%M%SZ)-$$"
mkdir -p "$SNAPSHOT_ROOT"
rsync -a \
	--exclude=.git \
	--exclude=/condor/logs \
	--exclude=/condor/runtime_snapshots \
	"$WORKFLOW_ROOT/" "$SNAPSHOT_ROOT/"
chmod -R a-w "$SNAPSHOT_ROOT"
echo "Frozen workflow snapshot: $SNAPSHOT_ROOT"

submit_file="$(mktemp "$WORKFLOW_ROOT/condor/shift_cmssw.XXXXXX.sub")"
trap 'rm -f "$submit_file"' EXIT
sed "s|<n_jobs>|$N_JOBS|g; s|<request_cpus>|$CONDOR_REQUEST_CPUS|g; s|<request_memory_mb>|$CONDOR_REQUEST_MEMORY_MB|g; s|<max_materialize>|$CONDOR_MAX_MATERIALIZE|g; s|<seed_momentum_scale>|$SHIFT_REFIT_SEED_MOMENTUM_SCALE|g; s|<energy_loss_scale>|$SHIFT_REFIT_ENERGY_LOSS_SCALE|g; s|<detailed_material_effects>|$SHIFT_REFIT_DETAILED_MATERIAL_EFFECTS|g; s|<geometry_material_effects>|$SHIFT_REFIT_GEOMETRY_MATERIAL_EFFECTS|g; s|<geometry_material_fitter>|$SHIFT_REFIT_GEOMETRY_MATERIAL_FITTER|g; s|<geometry_material_smoother>|$SHIFT_REFIT_GEOMETRY_MATERIAL_SMOOTHER|g; s|<log_geometry_comparison>|$SHIFT_REFIT_LOG_GEOMETRY_COMPARISON|g; s|<step4_inputs_per_job>|$STEP4_INPUTS_PER_JOB|g; s|<cmssw_runtime_fingerprint>|$CMSSW_RUNTIME_FINGERPRINT|g; s|<log_dir>|$CONDOR_LOG_DIR|g; s|<workflow_root>|$SNAPSHOT_ROOT|g; s|<selected_steps>|$NORMALIZED_STEPS|g; s|<force_selected>|$FORCE_SELECTED|g; s|<process>|$PROCESS|g; s|<sample_name>|$SAMPLE_NAME|g; s|<campaign_name>|$CAMPAIGN_NAME|g; s|<sample_base>|$SAMPLE_BASE|g; s|<sample_dir>|$SAMPLE_DIR|g; s|<n_events>|$N_EVENTS|g" \
	"$WORKFLOW_ROOT/condor/shift_cmssw.sub" > "$submit_file"
printf 'Submitting %s jobs for step(s) %s (force=%s, Step-4 inputs/job=%s, seed scale=%s, energy-loss scale=%s, detailed=%s, geometry both/fitter/smoother=%s/%s/%s, CPUs=%s, memory=%s MB, max materialized=%s)\n' \
	"$N_JOBS" "$NORMALIZED_STEPS" "$FORCE_SELECTED" "$STEP4_INPUTS_PER_JOB" "$SHIFT_REFIT_SEED_MOMENTUM_SCALE" "$SHIFT_REFIT_ENERGY_LOSS_SCALE" "$SHIFT_REFIT_DETAILED_MATERIAL_EFFECTS" "$SHIFT_REFIT_GEOMETRY_MATERIAL_EFFECTS" "$SHIFT_REFIT_GEOMETRY_MATERIAL_FITTER" "$SHIFT_REFIT_GEOMETRY_MATERIAL_SMOOTHER" "$CONDOR_REQUEST_CPUS" \
	"$CONDOR_REQUEST_MEMORY_MB" "$CONDOR_MAX_MATERIALIZE"
printf 'Reconstruction: DT=%s, tracker=%s, GEM/HCAL/ZDC=%s/%s/%s, augment DT/tracker=%s/%s, extended timing=%s\n' \
	"$SHIFT_DT_MODE" "$SHIFT_TRACKER_MODE" "$SHIFT_ENABLE_GEM" "$SHIFT_ENABLE_HCAL_DIAGNOSTICS" "$SHIFT_ENABLE_ZDC_DIAGNOSTICS" \
	"$SHIFT_AUGMENT_DT_HITS" "$SHIFT_AUGMENT_TRACKER_HITS" "$SHIFT_USE_EXTENDED_TIMING"
printf 'Pileup: mode=%s, scenario=%s, input=%s, seed=%s, sequential=%s\n' \
	"$PILEUP_MODE" "$PILEUP_SCENARIO" "${PILEUP_INPUT:-none}" "$PILEUP_SEED" "$PILEUP_SEQUENTIAL"
printf 'Timing: mode=%s, BX/phase=%s/%s ns, Geant4 central/forward limits=%s/%s ns\n' \
	"$SHIFT_TIMING_MODE" "$SHIFT_TIMING_BX_OFFSET" "$SHIFT_TIMING_PHASE_NS" "$SHIFT_G4_MAX_TRACK_TIME_NS" "$SHIFT_G4_MAX_TRACK_TIME_FORWARD_NS"
printf 'LSS: material=%s, field=%s, transform origin=%s\n' \
	"$SHIFT_LSS_MATERIAL_MODE" "$SHIFT_LSS_FIELD_MODE" "${SHIFT_LSS_MODEL_ORIGIN_CM:-unset}"
[[ -z "${SHIFT_LSS_CONTRACT_SHA256:-}" ]] || printf 'LSS contract SHA-256: %s\n' "$SHIFT_LSS_CONTRACT_SHA256"
printf 'Trigger: scenario=%s, timeline=%s, BX range=%s..%s, seed=%s, rules=%s, referenceSlots=%s, reconstructionFilter=%s/%s\n' \
	"$TRIGGER_SCENARIO" "$TRIGGER_TIMELINE_MODE" "$TRIGGER_TIMELINE_START_BX" "$TRIGGER_TIMELINE_END_BX" \
	"$TRIGGER_TIMELINE_SEED" "$TRIGGER_RULE_MODE" "$TRIGGER_REFERENCE_SLOT_MODE" \
	"$PIGGYBACK_FILTER_RECONSTRUCTION" "$PIGGYBACK_FILTER_LEVEL"
if [[ "$KEEP_LOGS" == 0 ]]; then
	echo "Cleaning old Condor and payload logs before submission..."
	"$WORKFLOW_ROOT/scripts/cleanup_condor_logs.sh" \
		"$CONDOR_LOG_DIR" "$LOG_DIR" "$WORKFLOW_ROOT/scripts/run_condor_job.sh"
else
	echo "Keeping logs from older jobs (--keep-logs)"
fi
condor_submit "$submit_file"
