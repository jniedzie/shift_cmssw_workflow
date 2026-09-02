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
if [[ ! "$CHUNK" =~ ^[0-9]+$ ]]; then
	echo "ERROR: chunk must be a non-negative integer (got '$CHUNK')" >&2
	exit 2
fi
source "$WORKFLOW_ROOT/scripts/setup_cmssw.sh"
N_EVENTS="${POSITIONAL_ARGS[1]:-$N_EVENTS}"
WORKDIR="${WORKDIR:-$SAMPLE_DIR}"
OUTPUT_DIR="$STEP1_DIR"
CONFIG_DIR="$STEP1_CONFIG_DIR"

case "${DEBUG_MUON_PRIMARIES:-0}" in
	0|false|False) DEBUG_MUON_PRIMARIES_CMS="False" ;;
	1|true|True) DEBUG_MUON_PRIMARIES_CMS="True" ;;
	*) echo "ERROR: DEBUG_MUON_PRIMARIES must be 0/1 or false/true" >&2; exit 1 ;;
esac

case "${DEBUG_MUON_HITS:-0}" in
	0|false|False) DEBUG_MUON_HITS_CMS="False" ;;
	1|true|True) DEBUG_MUON_HITS_CMS="True" ;;
	*) echo "ERROR: DEBUG_MUON_HITS must be 0/1 or false/true" >&2; exit 1 ;;
esac

case "${DEBUG_MUON_TRACKING:-0}" in
	0|false|False) DEBUG_MUON_TRACKING_CMS="False" ;;
	1|true|True) DEBUG_MUON_TRACKING_CMS="True" ;;
	*) echo "ERROR: DEBUG_MUON_TRACKING must be 0/1 or false/true" >&2; exit 1 ;;
esac

case "${SHIFT_TO_CMS_TRANSPORT:-1}" in
	0|false|False) SHIFT_TO_CMS_TRANSPORT_CMS="False" ;;
	1|true|True) SHIFT_TO_CMS_TRANSPORT_CMS="True" ;;
	*) echo "ERROR: SHIFT_TO_CMS_TRANSPORT must be 0/1 or false/true" >&2; exit 1 ;;
esac

GENERATOR_SEED_BASE="$GENERATOR_SEED"
case "$GENERATOR_SEED_BASE" in
	random)
		# Reading from /dev/urandom avoids reusing cmsDriver's default seed.
		GENERATOR_SEED=$(od -An -N4 -tu4 /dev/urandom | tr -d ' ')
		GENERATOR_SEED=$((GENERATOR_SEED % 900000000 + 1))
		;;
	''|*[!0-9]*)
		echo "ERROR: GENERATOR_SEED must be 'random' or an integer from 1 through 900000000" >&2
		exit 1
		;;
	*)
		if (( ${#GENERATOR_SEED} > 9 )) || (( 10#$GENERATOR_SEED < 1 || 10#$GENERATOR_SEED > 900000000 )); then
			echo "ERROR: GENERATOR_SEED must be 'random' or an integer from 1 through 900000000" >&2
			exit 1
		fi
		GENERATOR_SEED=$(( (10#$GENERATOR_SEED_BASE - 1 + 10#$CHUNK) % 900000000 + 1 ))
		;;
esac

SIMULATION_SEED_BASE="$SIMULATION_SEED"
case "$SIMULATION_SEED_BASE" in
	random)
		SIMULATION_SEED=$(od -An -N4 -tu4 /dev/urandom | tr -d ' ')
		SIMULATION_SEED=$((SIMULATION_SEED % 900000000 + 1))
		;;
	''|*[!0-9]*)
		echo "ERROR: SIMULATION_SEED must be 'random' or an integer from 1 through 900000000" >&2
		exit 1
		;;
	*)
		if (( ${#SIMULATION_SEED} > 9 )) || (( 10#$SIMULATION_SEED < 1 || 10#$SIMULATION_SEED > 900000000 )); then
			echo "ERROR: SIMULATION_SEED must be 'random' or an integer from 1 through 900000000" >&2
			exit 1
		fi
		SIMULATION_SEED=$(( (10#$SIMULATION_SEED_BASE - 1 + 10#$CHUNK) % 900000000 + 1 ))
		;;
esac

case "$SHIFT_TIMING_MODE" in
	nominal|legacy|fixed) ;;
	*) echo "ERROR: SHIFT_TIMING_MODE must be nominal, legacy, or fixed" >&2; exit 1 ;;
esac
case "$SHIFT_TIMING_BEAM_DIRECTION_Z" in
	-1|1) ;;
	*) echo "ERROR: SHIFT_TIMING_BEAM_DIRECTION_Z must be -1 or 1" >&2; exit 1 ;;
esac
if [[ ! "$SHIFT_TIMING_BX_OFFSET" =~ ^-?[0-9]+$ ]]; then
	echo "ERROR: SHIFT_TIMING_BX_OFFSET must be an integer" >&2
	exit 1
fi
FLOAT_PATTERN='^-?([0-9]+([.][0-9]*)?|[.][0-9]+)([eE][+-]?[0-9]+)?$'
for timing_float in SHIFT_TIMING_PHASE_NS SHIFT_TIMING_FIXED_OFFSET_NS \
	SHIFT_TIMING_CMS_REFERENCE_Z_MM SHIFT_TIMING_BUNCH_SPACING_NS SHIFT_TIMING_LEGACY_OFFSET_CT_MM \
	SHIFT_G4_MAX_TRACK_TIME_NS SHIFT_G4_MAX_TRACK_TIME_FORWARD_NS; do
	if [[ ! "${!timing_float}" =~ $FLOAT_PATTERN ]]; then
		echo "ERROR: $timing_float must be a finite decimal number (got '${!timing_float}')" >&2
		exit 1
	fi
done
if [[ ! "$SHIFT_TIMING_MODEL_VERSION" =~ ^[A-Za-z0-9._-]+$ ]]; then
	echo "ERROR: SHIFT_TIMING_MODEL_VERSION may contain only letters, digits, '.', '_' and '-'" >&2
	exit 1
fi

source "$WORKFLOW_ROOT/scripts/configure_lss.sh"
configure_shift_lss

mkdir -p "$WORKDIR" "$OUTPUT_DIR" "$CONFIG_DIR" "$LOG_DIR"
cd "$WORKDIR"

# dCache/PNFS permits creating files but may reject truncating an existing
# file in place.  Generate and run the configuration locally, then archive a
# new snapshot in the campaign directory.
LOCAL_STEP1_DIR="$(mktemp -d /tmp/shift_cmssw_step1_XXXXXX)"
cleanup_step1_tmp() {
	local status=$?
	if [[ "$status" -eq 0 && "${KEEP_STEP1_TMP:-0}" != 1 ]]; then
		rm -rf "$LOCAL_STEP1_DIR"
	else
		echo "Step 1 temporary files retained in $LOCAL_STEP1_DIR" >&2
	fi
}
trap cleanup_step1_tmp EXIT
LOCAL_CONFIG="$LOCAL_STEP1_DIR/events_step1_part${PART}_cfg.py"
LOCAL_LOG="$LOCAL_STEP1_DIR/step1_events_part${PART}.log"
LOCAL_OUTPUT="$LOCAL_STEP1_DIR/events_step1_part${PART}.root"

OUTPUT="$OUTPUT_DIR/events_step1_part${PART}.root"
if [[ "$FORCE" -eq 1 && -e "$OUTPUT" ]]; then
	echo "Force rerun requested; removing existing Step 1 output: $OUTPUT"
	rm -f -- "$OUTPUT"
fi
if output_is_valid "$OUTPUT"; then
	echo "Step 1 output already exists and is valid: $OUTPUT"
	exit 0
fi
if [[ -e "$OUTPUT" ]]; then
	echo "Removing invalid Step 1 output before retry: $OUTPUT"
	rm -f -- "$OUTPUT"
fi

echo "=== Step 1: GEN,SIM (Run 3) ==="
echo "Generator random seed: $GENERATOR_SEED (configured base: $GENERATOR_SEED_BASE, chunk: $CHUNK)"
echo "Geant4 random seed: $SIMULATION_SEED (configured base: $SIMULATION_SEED_BASE, chunk: $CHUNK)"
echo "SHIFT timing: mode=$SHIFT_TIMING_MODE beamDirectionZ=$SHIFT_TIMING_BEAM_DIRECTION_Z bxOffset=$SHIFT_TIMING_BX_OFFSET phaseNs=$SHIFT_TIMING_PHASE_NS fixedOffsetNs=$SHIFT_TIMING_FIXED_OFFSET_NS modelVersion=$SHIFT_TIMING_MODEL_VERSION"
echo "SHIFT Geant4 transport time limits: central=${SHIFT_G4_MAX_TRACK_TIME_NS} ns forward=${SHIFT_G4_MAX_TRACK_TIME_FORWARD_NS} ns"
echo "SHIFT LSS material/field modes: $SHIFT_LSS_MATERIAL_MODE/$SHIFT_LSS_FIELD_MODE"
[[ -z "${SHIFT_LSS_CONTRACT_SHA256:-}" ]] || echo "SHIFT LSS contract SHA-256: $SHIFT_LSS_CONTRACT_SHA256"
cmsDriver.py "$PYTHIA_CONFIG" \
	--step GEN,SIM \
	--conditions "$CONDITIONS" \
	--beamspot "$BEAMSPOT" \
	--datatier GEN-SIM \
	--eventcontent FEVTDEBUG \
	--geometry "$GEOMETRY" \
	--era "$ERA" \
	--fileout "file:$LOCAL_OUTPUT" \
	--python_filename "$LOCAL_CONFIG" \
	--customise_commands "from IOMC.ShiftEventTiming.shiftEventTiming_customise import customiseShiftEventTiming; process = customiseShiftEventTiming(process, timingMode='${SHIFT_TIMING_MODE}', beamDirectionZ=${SHIFT_TIMING_BEAM_DIRECTION_Z}, bxOffset=${SHIFT_TIMING_BX_OFFSET}, phaseNs=${SHIFT_TIMING_PHASE_NS}, fixedOffsetNs=${SHIFT_TIMING_FIXED_OFFSET_NS}, cmsReferenceZmm=${SHIFT_TIMING_CMS_REFERENCE_Z_MM}, bunchSpacingNs=${SHIFT_TIMING_BUNCH_SPACING_NS}, legacyOffsetCtMm=${SHIFT_TIMING_LEGACY_OFFSET_CT_MM}, modelVersion='${SHIFT_TIMING_MODEL_VERSION}', maxTrackTimeNs=${SHIFT_G4_MAX_TRACK_TIME_NS}, maxTrackTimeForwardNs=${SHIFT_G4_MAX_TRACK_TIME_FORWARD_NS}); from PhysicsTools.ShiftMuonSegments.shiftMuonSegments_customise import customiseKeepShiftTruth; process.RandomNumberGeneratorService.generator.initialSeed = cms.untracked.uint32(${GENERATOR_SEED}); process.RandomNumberGeneratorService.g4SimHits.initialSeed = cms.untracked.uint32(${SIMULATION_SEED}); process.g4SimHits.Generator.DebugMuonPrimaries = cms.untracked.bool(${DEBUG_MUON_PRIMARIES_CMS}); process.g4SimHits.TrackingAction.DebugMuonPrimaryFates = cms.untracked.bool(${DEBUG_MUON_PRIMARIES_CMS}); process.g4SimHits.TrackingAction.DebugMuonTracking = cms.untracked.bool(${DEBUG_MUON_TRACKING_CMS}); process.g4SimHits.SteppingAction.DebugMuonTracking = cms.untracked.bool(${DEBUG_MUON_TRACKING_CMS}); process.g4SimHits.SteppingAction.CMStoZDCtransport = cms.bool(${SHIFT_TO_CMS_TRANSPORT_CMS}); process.g4SimHits.MuonSD.DebugMuonHits = cms.untracked.bool(${DEBUG_MUON_HITS_CMS}); process = customiseKeepShiftTruth(process)${SHIFT_LSS_SIMULATION_PYTHON}" \
	--no_exec \
	-n "$N_EVENTS"

CONFIG_SNAPSHOT="$CONFIG_DIR/events_step1_part${PART}_seed${GENERATOR_SEED}_cfg.py"
if ! cp "$LOCAL_CONFIG" "$CONFIG_SNAPSHOT"; then
	echo "WARNING: could not archive Step 1 config at $CONFIG_SNAPSHOT; continuing with local config" >&2
fi

cmsRun "$LOCAL_CONFIG" 2>&1 | tee "$LOCAL_LOG"

if ! output_is_valid "$LOCAL_OUTPUT"; then
	echo "ERROR: Step 1 cmsRun returned successfully but did not produce a valid local output: $LOCAL_OUTPUT" >&2
	exit 1
fi
stage_cmssw_output "$LOCAL_OUTPUT" "$OUTPUT"

LOG_SNAPSHOT="$LOG_DIR/step1_events_part${PART}_seed${GENERATOR_SEED}.log"
if ! cp "$LOCAL_LOG" "$LOG_SNAPSHOT"; then
	echo "WARNING: could not archive Step 1 log at $LOG_SNAPSHOT" >&2
fi

# Pythia prints the generated cross section and its statistical uncertainty in
# the end-of-job summary.  Keep one shared, latest value for this sample.
if ! "$WORKFLOW_ROOT/scripts/update_cross_section.sh" \
	"$LOCAL_LOG" \
	"$CROSS_SECTION_FILE" \
	"$(basename "$PYTHIA_CONFIG" .py)"; then
	# The validated GEN-SIM file is already published.  Shared aggregate
	# bookkeeping must not prevent later stages from consuming it.
	echo "WARNING: could not update aggregate cross-section bookkeeping; continuing with the published Step 1 output" >&2
fi

if [[ "$DEBUG_MUON_PRIMARIES_CMS" == True || "$DEBUG_MUON_HITS_CMS" == True || "$DEBUG_MUON_TRACKING_CMS" == True ]]; then
	echo
	echo "=== Step 1 muon debug summary ==="
	if ! "$WORKFLOW_ROOT/scripts/format_muon_debug.py" "$LOCAL_LOG"; then
		echo "No FixedTargetMuonDebug records were found in the Step 1 log."
	fi
fi
echo "Full Step 1 log: $LOG_SNAPSHOT"
