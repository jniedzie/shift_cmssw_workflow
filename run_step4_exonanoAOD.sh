#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKFLOW_ROOT="$(cd "$SCRIPT_DIR" && pwd)"
FORCE=0
POSITIONAL_ARGS=()
for argument in "$@"; do
	case "$argument" in
		-f|--force) FORCE=1 ;;
		-h|--help) echo "Usage: $(basename "$0") [--force] [chunk [events]]"; exit 0 ;;
		-*) echo "ERROR: unknown option: $argument" >&2; exit 2 ;;
		*) POSITIONAL_ARGS+=("$argument") ;;
	esac
done
if (( ${#POSITIONAL_ARGS[@]} > 2 )); then
	echo "ERROR: expected at most chunk and event-count arguments" >&2
	exit 2
fi
CHUNK="${POSITIONAL_ARGS[0]:-0}"
source "$WORKFLOW_ROOT/scripts/setup_cmssw.sh"
N_EVENTS="${POSITIONAL_ARGS[1]:-$N_EVENTS}"
WORKDIR="${WORKDIR:-$SAMPLE_DIR}"
OUTPUT_DIR="$STEP4_DIR"
CONFIG_DIR="$STEP4_CONFIG_DIR"
# The improved momentum refit uses Geant4e, whose propagator manager and
# tracking state are process-global.  cmsDriver writes its --nThreads setting
# after the NanoAOD customisation, so a value larger than one overrides the
# customisation's attempted single-thread safeguard and can crash inside
# G4ErrorPropagator::MakeOneStep.  Keep this stage explicitly single-threaded
# while the detailed-material refit is enabled.
N_THREADS="${N_THREADS:-1}"
if [[ "$N_THREADS" != 1 ]]; then
	echo "ERROR: Step 4 improved momentum refit requires N_THREADS=1 (got '$N_THREADS')" >&2
	exit 1
fi
N_STREAMS=1

case "${ENABLE_EXONANOAOD:-1}" in
	1|true|True)
		NANO_STEP="PAT,NANO:@EXO"
		OUTPUT_LABEL="EXONanoAOD"
		;;
	0|false|False)
		NANO_STEP="PAT,NANO"
		OUTPUT_LABEL="NanoAOD"
		;;
	*)
		echo "ERROR: ENABLE_EXONANOAOD must be 0/1 or false/true" >&2
		exit 1
		;;
esac

mkdir -p "$WORKDIR" "$OUTPUT_DIR" "$CONFIG_DIR" "$LOG_DIR"
cd "$WORKDIR"

# dCache/PNFS can reject cmsDriver's in-place configuration-file creation.
# Generate and run locally, then retain a campaign snapshot when possible.
LOCAL_STEP4_DIR="$(mktemp -d /tmp/shift_cmssw_step4_XXXXXX)"
cleanup_step4_tmp() {
	local status=$?
	if [[ "$status" -eq 0 && "${KEEP_STEP4_TMP:-0}" != 1 ]]; then
		rm -rf "$LOCAL_STEP4_DIR"
	else
		echo "Step 4 temporary files retained in $LOCAL_STEP4_DIR" >&2
	fi
}
trap cleanup_step4_tmp EXIT
LOCAL_CONFIG="$LOCAL_STEP4_DIR/events_${OUTPUT_LABEL}_part_${PART}_cfg.py"
LOCAL_LOG="$LOCAL_STEP4_DIR/step4_events_${OUTPUT_LABEL}_part_${PART}.log"
LOCAL_OUTPUT="$LOCAL_STEP4_DIR/events_${OUTPUT_LABEL}_part_${PART}.root"

OUTPUT="$OUTPUT_DIR/events_${OUTPUT_LABEL}_part_${PART}.root"
if [[ "$FORCE" -eq 1 && -e "$OUTPUT" ]]; then
	echo "Force rerun requested; removing existing Step 4 output: $OUTPUT"
	rm -f -- "$OUTPUT"
fi
if output_is_valid "$OUTPUT"; then
	echo "Step 4 output already exists and is valid: $OUTPUT"
	exit 0
fi
if [[ -e "$OUTPUT" ]]; then
	echo "Removing invalid Step 4 output before retry: $OUTPUT"
	rm -f -- "$OUTPUT"
fi

if [[ ! "$STEP4_INPUTS_PER_JOB" =~ ^[1-9][0-9]*$ ]]; then
	echo "ERROR: STEP4_INPUTS_PER_JOB must be a positive integer (got '$STEP4_INPUTS_PER_JOB')" >&2
	exit 1
fi
INPUT_START=$((10#$CHUNK * STEP4_INPUTS_PER_JOB))
INPUTS=()
for ((input_offset = 0; input_offset < STEP4_INPUTS_PER_JOB; ++input_offset)); do
	input_part="$(printf '%04d' "$((INPUT_START + input_offset))")"
	input="$STEP3_DIR/events_AOD_part${input_part}.root"
	if [[ ! -s "$input" ]]; then
		echo "ERROR: Step-4 grouped input is missing or empty: $input" >&2
		exit 1
	fi
	INPUTS+=("file:$input")
done
FILEIN="$(IFS=,; echo "${INPUTS[*]}")"
STEP4_N_EVENTS=$((N_EVENTS * STEP4_INPUTS_PER_JOB))

# --customise runs before cmsDriver's built-in NanoAOD customisations.  Those
# customisations can replace the Nano sequence, dropping modules added by our
# hook.  Run it as a command instead: cmsDriver places these commands at the
# end of the generated configuration, after the final EXO/Nano setup.
CUSTOMISE_COMMAND_ARGS=()
GROUPED_SOURCE_COMMAND=""
if [[ "$STEP4_INPUTS_PER_JOB" != 1 ]]; then
	# Independently generated chunks restart their EDM event numbering. They are
	# distinct events despite equal run/lumi/event IDs, so grouped test inputs
	# must not be discarded by PoolSource's cross-file duplicate check.
	GROUPED_SOURCE_COMMAND="; process.source.duplicateCheckMode = cms.untracked.string('noDuplicateCheck')"
fi
if [[ -n "${AOD_TO_EXONANO_CUSTOMISE:-}" ]]; then
	CUSTOMISE_MODULE="${AOD_TO_EXONANO_CUSTOMISE%%.*}"
	CUSTOMISE_FUNCTION="${AOD_TO_EXONANO_CUSTOMISE##*.}"
	CUSTOMISE_MODULE="${CUSTOMISE_MODULE//\//.}"
	case "$SHIFT_ENABLE_HCAL_DIAGNOSTICS" in
		0) HCAL_DIAGNOSTICS_CMSSW=False ;;
		1) HCAL_DIAGNOSTICS_CMSSW=True ;;
		*) echo "ERROR: SHIFT_ENABLE_HCAL_DIAGNOSTICS must be 0 or 1 (got '$SHIFT_ENABLE_HCAL_DIAGNOSTICS')" >&2; exit 1 ;;
	esac
	case "$SHIFT_ENABLE_ZDC_DIAGNOSTICS" in
		0) ZDC_DIAGNOSTICS_CMSSW=False ;;
		1) ZDC_DIAGNOSTICS_CMSSW=True ;;
		*) echo "ERROR: SHIFT_ENABLE_ZDC_DIAGNOSTICS must be 0 or 1 (got '$SHIFT_ENABLE_ZDC_DIAGNOSTICS')" >&2; exit 1 ;;
	esac
	case "$SHIFT_AUGMENT_DT_HITS" in 0) AUGMENT_DT_CMSSW=False ;; 1) AUGMENT_DT_CMSSW=True ;; *) echo "ERROR: SHIFT_AUGMENT_DT_HITS must be 0 or 1" >&2; exit 1 ;; esac
	case "$SHIFT_AUGMENT_TRACKER_HITS" in 0) AUGMENT_TRACKER_CMSSW=False ;; 1) AUGMENT_TRACKER_CMSSW=True ;; *) echo "ERROR: SHIFT_AUGMENT_TRACKER_HITS must be 0 or 1" >&2; exit 1 ;; esac
	case "$SHIFT_USE_EXTENDED_TIMING" in 0) EXTENDED_TIMING_CMSSW=False ;; 1) EXTENDED_TIMING_CMSSW=True ;; *) echo "ERROR: SHIFT_USE_EXTENDED_TIMING must be 0 or 1" >&2; exit 1 ;; esac
	if [[ ! "$SHIFT_REFIT_SEED_MOMENTUM_SCALE" =~ ^[0-9]+([.][0-9]+)?$ ]] ||
	   [[ "$SHIFT_REFIT_SEED_MOMENTUM_SCALE" =~ ^0+([.]0+)?$ ]]; then
		echo "ERROR: SHIFT_REFIT_SEED_MOMENTUM_SCALE must be a positive decimal (got '$SHIFT_REFIT_SEED_MOMENTUM_SCALE')" >&2
		exit 1
	fi
	if [[ ! "$SHIFT_REFIT_ENERGY_LOSS_SCALE" =~ ^[0-9]+([.][0-9]+)?$ ]] ||
	   [[ "$SHIFT_REFIT_ENERGY_LOSS_SCALE" =~ ^0+([.]0+)?$ ]]; then
		echo "ERROR: SHIFT_REFIT_ENERGY_LOSS_SCALE must be a positive decimal (got '$SHIFT_REFIT_ENERGY_LOSS_SCALE')" >&2
		exit 1
	fi
	if [[ ! "$SHIFT_REFIT_SECOND_SEED_ERROR_RESCALE" =~ ^[0-9]+([.][0-9]+)?$ ]] ||
	   [[ "$SHIFT_REFIT_SECOND_SEED_ERROR_RESCALE" =~ ^0+([.]0+)?$ ]]; then
		echo "ERROR: SHIFT_REFIT_SECOND_SEED_ERROR_RESCALE must be a positive decimal (got '$SHIFT_REFIT_SECOND_SEED_ERROR_RESCALE')" >&2
		exit 1
	fi
	case "$SHIFT_REFIT_USE_SECOND_ITERATION" in
		0) USE_SECOND_ITERATION_CMSSW=False ;;
		1) USE_SECOND_ITERATION_CMSSW=True ;;
		*) echo "ERROR: SHIFT_REFIT_USE_SECOND_ITERATION must be 0 or 1 (got '$SHIFT_REFIT_USE_SECOND_ITERATION')" >&2; exit 1 ;;
	esac
	case "$SHIFT_REFIT_DETAILED_MATERIAL_EFFECTS" in
		0) DETAILED_REFIT_MATERIAL_CMSSW=False ;;
		1) DETAILED_REFIT_MATERIAL_CMSSW=True ;;
		*) echo "ERROR: SHIFT_REFIT_DETAILED_MATERIAL_EFFECTS must be 0 or 1 (got '$SHIFT_REFIT_DETAILED_MATERIAL_EFFECTS')" >&2; exit 1 ;;
	esac
	case "$SHIFT_REFIT_GEOMETRY_MATERIAL_EFFECTS" in
		0) GEOMETRY_REFIT_MATERIAL_CMSSW=False ;;
		1) GEOMETRY_REFIT_MATERIAL_CMSSW=True ;;
		*) echo "ERROR: SHIFT_REFIT_GEOMETRY_MATERIAL_EFFECTS must be 0 or 1 (got '$SHIFT_REFIT_GEOMETRY_MATERIAL_EFFECTS')" >&2; exit 1 ;;
	esac
	case "$SHIFT_REFIT_GEOMETRY_MATERIAL_FITTER" in
		0) GEOMETRY_REFIT_FITTER_CMSSW=False ;;
		1) GEOMETRY_REFIT_FITTER_CMSSW=True ;;
		*) echo "ERROR: SHIFT_REFIT_GEOMETRY_MATERIAL_FITTER must be 0 or 1 (got '$SHIFT_REFIT_GEOMETRY_MATERIAL_FITTER')" >&2; exit 1 ;;
	esac
	case "$SHIFT_REFIT_GEOMETRY_MATERIAL_SMOOTHER" in
		0) GEOMETRY_REFIT_SMOOTHER_CMSSW=False ;;
		1) GEOMETRY_REFIT_SMOOTHER_CMSSW=True ;;
		*) echo "ERROR: SHIFT_REFIT_GEOMETRY_MATERIAL_SMOOTHER must be 0 or 1 (got '$SHIFT_REFIT_GEOMETRY_MATERIAL_SMOOTHER')" >&2; exit 1 ;;
	esac
	case "$SHIFT_REFIT_GEOMETRY_TARGET_MATERIAL" in
		0) GEOMETRY_TARGET_MATERIAL_CMSSW=False ;;
		1) GEOMETRY_TARGET_MATERIAL_CMSSW=True ;;
		*) echo "ERROR: SHIFT_REFIT_GEOMETRY_TARGET_MATERIAL must be 0 or 1 (got '$SHIFT_REFIT_GEOMETRY_TARGET_MATERIAL')" >&2; exit 1 ;;
	esac
	case "$SHIFT_REFIT_LOG_GEOMETRY_COMPARISON" in
		0) LOG_GEOMETRY_COMPARISON_CMSSW=False ;;
		1) LOG_GEOMETRY_COMPARISON_CMSSW=True ;;
		*) echo "ERROR: SHIFT_REFIT_LOG_GEOMETRY_COMPARISON must be 0 or 1 (got '$SHIFT_REFIT_LOG_GEOMETRY_COMPARISON')" >&2; exit 1 ;;
	esac
	if [[ "$SHIFT_REFIT_DETAILED_MATERIAL_EFFECTS" == 1 &&
	      ( "$SHIFT_REFIT_GEOMETRY_MATERIAL_EFFECTS" == 1 || "$SHIFT_REFIT_GEOMETRY_MATERIAL_FITTER" == 1 || "$SHIFT_REFIT_GEOMETRY_MATERIAL_SMOOTHER" == 1 ) ]]; then
		echo "ERROR: detailed and geometry-sampled refit material modes are mutually exclusive" >&2
		exit 1
	fi
	CUSTOMISE_COMMAND_ARGS+=(
		--customise_commands
		"from ${CUSTOMISE_MODULE} import ${CUSTOMISE_FUNCTION}; process = ${CUSTOMISE_FUNCTION}(process, directionalRefitUseDetailedMaterialEffects=${DETAILED_REFIT_MATERIAL_CMSSW}, directionalRefitUseGeometryMaterialEffects=${GEOMETRY_REFIT_MATERIAL_CMSSW}, directionalRefitUseGeometryMaterialEffectsInFitter=${GEOMETRY_REFIT_FITTER_CMSSW}, directionalRefitUseGeometryMaterialEffectsInSmoother=${GEOMETRY_REFIT_SMOOTHER_CMSSW}, directionalRefitUseGeometryTargetMaterialEffects=${GEOMETRY_TARGET_MATERIAL_CMSSW}, enableHcalDiagnostics=${HCAL_DIAGNOSTICS_CMSSW}, enableZDCDiagnostics=${ZDC_DIAGNOSTICS_CMSSW}, augmentDTHits=${AUGMENT_DT_CMSSW}, augmentTrackerHits=${AUGMENT_TRACKER_CMSSW}, useExtendedTiming=${EXTENDED_TIMING_CMSSW}); process.shiftMuonTable.directionalRefitSeedMomentumScale = cms.double(${SHIFT_REFIT_SEED_MOMENTUM_SCALE}); process.shiftMuonTable.directionalRefitSecondSeedErrorRescale = cms.double(${SHIFT_REFIT_SECOND_SEED_ERROR_RESCALE}); process.shiftMuonTable.directionalRefitUseSecondIteration = cms.bool(${USE_SECOND_ITERATION_CMSSW}); process.shiftMuonTable.directionalRefitEnergyLossScale = cms.double(${SHIFT_REFIT_ENERGY_LOSS_SCALE}); process.shiftMuonTable.directionalRefitLogGeometryMaterialComparison = cms.bool(${LOG_GEOMETRY_COMPARISON_CMSSW})${GROUPED_SOURCE_COMMAND}"
	)
elif [[ -n "$GROUPED_SOURCE_COMMAND" ]]; then
	CUSTOMISE_COMMAND_ARGS+=(--customise_commands "${GROUPED_SOURCE_COMMAND#; }")
fi
echo "=== Step 4: AODSIM -> ${OUTPUT_LABEL} (Run 3) ==="
echo "SHIFT reconstruction variant: $SHIFT_RECO_VARIANT (code $SHIFT_RECO_VARIANT_CODE)"
echo "Detector modes: DT=$SHIFT_DT_MODE tracker=$SHIFT_TRACKER_MODE GEM=$SHIFT_ENABLE_GEM HCALdiag=$SHIFT_ENABLE_HCAL_DIAGNOSTICS ZDCdiag=$SHIFT_ENABLE_ZDC_DIAGNOSTICS"
echo "Augmented measurements: DT=$SHIFT_AUGMENT_DT_HITS tracker=$SHIFT_AUGMENT_TRACKER_HITS extendedTiming=$SHIFT_USE_EXTENDED_TIMING"
echo "Directional refit seed momentum scale: $SHIFT_REFIT_SEED_MOMENTUM_SCALE"
echo "Directional refit second-pass seed error rescale: $SHIFT_REFIT_SECOND_SEED_ERROR_RESCALE"
echo "Directional refit use second iteration: $SHIFT_REFIT_USE_SECOND_ITERATION"
echo "Directional refit energy-loss scale: $SHIFT_REFIT_ENERGY_LOSS_SCALE"
echo "Directional refit detailed material effects: $SHIFT_REFIT_DETAILED_MATERIAL_EFFECTS"
echo "Directional refit geometry mean-loss material effects: $SHIFT_REFIT_GEOMETRY_MATERIAL_EFFECTS"
echo "Directional refit geometry material in fitter: $SHIFT_REFIT_GEOMETRY_MATERIAL_FITTER"
echo "Directional refit geometry material in smoother: $SHIFT_REFIT_GEOMETRY_MATERIAL_SMOOTHER"
echo "Directional refit geometry material on target leg: $SHIFT_REFIT_GEOMETRY_TARGET_MATERIAL"
echo "Directional refit log geometry comparison: $SHIFT_REFIT_LOG_GEOMETRY_COMPARISON"
echo "Step-4 grouped inputs: $STEP4_INPUTS_PER_JOB (parts $INPUT_START through $((INPUT_START + STEP4_INPUTS_PER_JOB - 1)))"
DRIVER_ARGS=(
	--step "$NANO_STEP"
	--conditions "$CONDITIONS"
	--datatier NANOAODSIM
	--eventcontent NANOAODSIM
	--geometry "$GEOMETRY"
	--era "$ERA"
	--filein "$FILEIN"
	--fileout "file:$LOCAL_OUTPUT"
	--python_filename "$LOCAL_CONFIG"
	--nThreads "$N_THREADS"
	--nStreams "$N_STREAMS"
	--no_exec
	-n "$STEP4_N_EVENTS"
)
DRIVER_ARGS+=("${CUSTOMISE_COMMAND_ARGS[@]}")
cmsDriver.py step4 "${DRIVER_ARGS[@]}"

CONFIG_SNAPSHOT="$CONFIG_DIR/events_${OUTPUT_LABEL}_part_${PART}_cfg.py"
if ! cp "$LOCAL_CONFIG" "$CONFIG_SNAPSHOT"; then
	echo "WARNING: could not archive Step 4 config at $CONFIG_SNAPSHOT; continuing with local config" >&2
fi

cmsRun "$LOCAL_CONFIG" 2>&1 | tee "$LOCAL_LOG"

# AFS libraries are shared directly with workers.  Detect an overlapping
# relink even when cmsRun happened to finish, rather than accepting an output
# produced against a runtime that changed during the job.
validate_cmssw_runtime

if ! output_is_valid "$LOCAL_OUTPUT"; then
	echo "ERROR: Step 4 cmsRun returned successfully but did not produce a valid local output: $LOCAL_OUTPUT" >&2
	exit 1
fi
stage_cmssw_output "$LOCAL_OUTPUT" "$OUTPUT"

LOG_SNAPSHOT="$LOG_DIR/step4_events_${OUTPUT_LABEL}_part_${PART}.log"
if ! cp "$LOCAL_LOG" "$LOG_SNAPSHOT"; then
	echo "WARNING: could not archive Step 4 log at $LOG_SNAPSHOT" >&2
fi
