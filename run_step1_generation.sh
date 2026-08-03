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
OUTPUT_DIR="$STEP1_DIR"
CONFIG_DIR="$STEP1_CONFIG_DIR"

GEOMETRY="DB:Extended"
ERA="Run3_2024"
CONDITIONS="auto:phase1_2024_realistic"
BEAMSPOT="Realistic25ns13p6TeVEarly2023Collision"

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

case "$GENERATOR_SEED" in
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
		GENERATOR_SEED=$((10#$GENERATOR_SEED))
		;;
esac

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

OUTPUT="$OUTPUT_DIR/events_step1_part${PART}.root"
if [[ "$FORCE" -eq 1 && -e "$OUTPUT" ]]; then
	echo "Force rerun requested; removing existing Step 1 output: $OUTPUT"
	rm -f -- "$OUTPUT"
fi
if output_is_valid "$OUTPUT"; then
	echo "Step 1 output already exists and is valid: $OUTPUT"
	exit 0
fi

echo "=== Step 1: GEN,SIM (Run 3) ==="
echo "Generator random seed: $GENERATOR_SEED"
cmsDriver.py "$PYTHIA_CONFIG" \
	--step GEN,SIM \
	--conditions "$CONDITIONS" \
	--beamspot "$BEAMSPOT" \
	--datatier GEN-SIM \
	--eventcontent FEVTDEBUG \
	--geometry "$GEOMETRY" \
	--era "$ERA" \
	--fileout "file:$OUTPUT" \
	--python_filename "$LOCAL_CONFIG" \
	--customise_commands "process.RandomNumberGeneratorService.generator.initialSeed = cms.untracked.uint32(${GENERATOR_SEED}); process.g4SimHits.Generator.DebugMuonPrimaries = cms.untracked.bool(${DEBUG_MUON_PRIMARIES_CMS}); process.g4SimHits.TrackingAction.DebugMuonTracking = cms.untracked.bool(${DEBUG_MUON_TRACKING_CMS}); process.g4SimHits.SteppingAction.DebugMuonTracking = cms.untracked.bool(${DEBUG_MUON_TRACKING_CMS}); process.g4SimHits.SteppingAction.CMStoZDCtransport = cms.bool(${SHIFT_TO_CMS_TRANSPORT_CMS}); process.g4SimHits.MuonSD.DebugMuonHits = cms.untracked.bool(${DEBUG_MUON_HITS_CMS})" \
	--no_exec \
	-n "$N_EVENTS"

CONFIG_SNAPSHOT="$CONFIG_DIR/events_step1_part${PART}_seed${GENERATOR_SEED}_cfg.py"
if ! cp "$LOCAL_CONFIG" "$CONFIG_SNAPSHOT"; then
	echo "WARNING: could not archive Step 1 config at $CONFIG_SNAPSHOT; continuing with local config" >&2
fi

cmsRun "$LOCAL_CONFIG" 2>&1 | tee "$LOCAL_LOG"

LOG_SNAPSHOT="$LOG_DIR/step1_events_part${PART}_seed${GENERATOR_SEED}.log"
if ! cp "$LOCAL_LOG" "$LOG_SNAPSHOT"; then
	echo "WARNING: could not archive Step 1 log at $LOG_SNAPSHOT" >&2
fi

# Pythia prints the generated cross section and its statistical uncertainty in
# the end-of-job summary.  Keep one shared, latest value for this sample.
"$WORKFLOW_ROOT/scripts/update_cross_section.sh" \
	"$LOCAL_LOG" \
	"$CROSS_SECTION_FILE" \
	"$(basename "$PYTHIA_CONFIG" .py)"

echo
echo "=== Step 1 muon debug summary ==="
if ! "$WORKFLOW_ROOT/scripts/format_muon_debug.py" "$LOCAL_LOG"; then
	echo "No FixedTargetMuonDebug records were found in the Step 1 log."
fi
echo "Full Step 1 log: $LOG_SNAPSHOT"
