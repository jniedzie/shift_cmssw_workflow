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
OUTPUT_DIR="$STEP2_DIR"
CONFIG_DIR="$STEP2_CONFIG_DIR"

PILEUP_ARGS=()
PILEUP_CUSTOMISE=""
case "$PILEUP_MODE" in
	none)
		;;
	standard|run3_2024)
		if [[ -z "$PILEUP_INPUT" ]]; then
			echo "ERROR: PILEUP_MODE=$PILEUP_MODE requires PILEUP_INPUT" >&2
			echo "Use filelist:/absolute/path for Condor, or das:$PILEUP_DATASET for an interactive test." >&2
			exit 1
		fi
		if [[ "$PILEUP_INPUT" == filelist:* ]]; then
			PILEUP_FILE_LIST="${PILEUP_INPUT#filelist:}"
			if [[ "$PILEUP_FILE_LIST" != /* || ! -s "$PILEUP_FILE_LIST" ]]; then
				echo "ERROR: pileup file list must be an absolute, non-empty readable file: $PILEUP_FILE_LIST" >&2
				exit 1
			fi
		fi
		case "$PILEUP_SEED" in
			random)
				PILEUP_SEED="$(od -An -N4 -tu4 /dev/urandom | tr -d ' ')"
				PILEUP_SEED=$((PILEUP_SEED % 900000000 + 1))
				;;
			''|*[!0-9]*)
				echo "ERROR: PILEUP_SEED must be 'random' or an integer from 1 through 900000000" >&2
				exit 1
				;;
			*)
				if (( ${#PILEUP_SEED} > 9 )) || (( 10#$PILEUP_SEED < 1 || 10#$PILEUP_SEED > 900000000 )); then
					echo "ERROR: PILEUP_SEED must be 'random' or an integer from 1 through 900000000" >&2
					exit 1
				fi
				PILEUP_SEED=$((10#$PILEUP_SEED))
				;;
		esac
		PILEUP_ARGS+=(--pileup "$PILEUP_SCENARIO" --pileup_input "$PILEUP_INPUT")
		PILEUP_CUSTOMISE="; process.RandomNumberGeneratorService.mix.initialSeed = cms.untracked.uint32(${PILEUP_SEED})"
		;;
	*)
		echo "ERROR: PILEUP_MODE must be none or standard (got '$PILEUP_MODE')" >&2
		exit 1
		;;
esac

if [[ ! "$N_EVENTS" =~ ^[1-9][0-9]*$ ]]; then
	echo "ERROR: event count must be a positive integer (got '$N_EVENTS')" >&2
	exit 1
fi

TRIGGER_TIMELINE_ARGS=()
case "$TRIGGER_TIMELINE_MODE" in
	none)
		;;
	zero_bias_proxy)
		for trigger_input_variable in TRIGGER_LIBRARY_JSONL TRIGGER_L1_MENU_JSON; do
			trigger_input="${!trigger_input_variable}"
			if [[ "$trigger_input" != /* || ! -s "$trigger_input" ]]; then
				echo "ERROR: $trigger_input_variable must be an absolute, non-empty file when TRIGGER_TIMELINE_MODE=zero_bias_proxy (got '$trigger_input')" >&2
				exit 1
			fi
		done
		for bx_variable in TRIGGER_TIMELINE_START_BX TRIGGER_TIMELINE_END_BX; do
			if [[ ! "${!bx_variable}" =~ ^-?[0-9]+$ ]]; then
				echo "ERROR: $bx_variable must be an integer (got '${!bx_variable}')" >&2
				exit 1
			fi
		done
		if (( TRIGGER_TIMELINE_END_BX < TRIGGER_TIMELINE_START_BX )); then
			echo "ERROR: TRIGGER_TIMELINE_END_BX must not precede TRIGGER_TIMELINE_START_BX" >&2
			exit 1
		fi
		case "$TRIGGER_RULE_MODE" in
			none)
				;;
			run3)
				if [[ ! "$TRIGGER_RULE_HISTORY_START_BX" =~ ^-?[0-9]+$ ]]; then
					echo "ERROR: TRIGGER_RULE_HISTORY_START_BX must be an integer" >&2
					exit 1
				fi
				TRIGGER_TIMELINE_ARGS+=(
					--trigger-rule-mode run3
					--trigger-rule-history-start-bx "$TRIGGER_RULE_HISTORY_START_BX"
				)
				;;
			*)
				echo "ERROR: TRIGGER_RULE_MODE must be none or run3 (got '$TRIGGER_RULE_MODE')" >&2
				exit 1
				;;
		esac
		case "$TRIGGER_TIMELINE_SEED" in
			random)
				TRIGGER_TIMELINE_EFFECTIVE_SEED="$(od -An -N4 -tu4 /dev/urandom | tr -d ' ')"
				TRIGGER_TIMELINE_EFFECTIVE_SEED=$((TRIGGER_TIMELINE_EFFECTIVE_SEED % 900000000 + 1))
				;;
			''|*[!0-9]*)
				echo "ERROR: TRIGGER_TIMELINE_SEED must be 'random' or an integer from 1 through 900000000" >&2
				exit 1
				;;
			*)
				if (( ${#TRIGGER_TIMELINE_SEED} > 9 )) || (( 10#$TRIGGER_TIMELINE_SEED < 1 || 10#$TRIGGER_TIMELINE_SEED > 900000000 )); then
					echo "ERROR: TRIGGER_TIMELINE_SEED must be 'random' or an integer from 1 through 900000000" >&2
					exit 1
				fi
				TRIGGER_TIMELINE_EFFECTIVE_SEED=$(( (10#$TRIGGER_TIMELINE_SEED - 1 + 10#$CHUNK) % 900000000 + 1 ))
				;;
		esac
		if [[ -n "$TRIGGER_COLLIDING_BX_FILE" && -n "$TRIGGER_COLLIDING_BX_MASK" ]]; then
			echo "ERROR: set only one of TRIGGER_COLLIDING_BX_FILE and TRIGGER_COLLIDING_BX_MASK" >&2
			exit 1
		fi
		if [[ -n "$TRIGGER_COLLIDING_BX_MASK" ]]; then
			if [[ "$TRIGGER_COLLIDING_BX_MASK" != /* || ! -s "$TRIGGER_COLLIDING_BX_MASK" ]]; then
				echo "ERROR: TRIGGER_COLLIDING_BX_MASK must be an absolute, non-empty file when set" >&2
				exit 1
			fi
			if [[ ! "$TRIGGER_REFERENCE_BX_SLOT" =~ ^[0-9]+$ ]] || (( 10#$TRIGGER_REFERENCE_BX_SLOT < 1 || 10#$TRIGGER_REFERENCE_BX_SLOT > 3564 )); then
				echo "ERROR: TRIGGER_REFERENCE_BX_SLOT must be an integer from 1 through 3564 with TRIGGER_COLLIDING_BX_MASK" >&2
				exit 1
			fi
			if [[ "$TRIGGER_SHIFT_BEAM" != 1 && "$TRIGGER_SHIFT_BEAM" != 2 ]]; then
				echo "ERROR: TRIGGER_SHIFT_BEAM must be 1 or 2 with TRIGGER_COLLIDING_BX_MASK" >&2
				exit 1
			fi
			if [[ "$TRIGGER_RUN_FILL_MAP" != /* || ! -s "$TRIGGER_RUN_FILL_MAP" ]]; then
				echo "ERROR: TRIGGER_RUN_FILL_MAP must be an absolute, non-empty file with TRIGGER_COLLIDING_BX_MASK" >&2
				exit 1
			fi
			TRIGGER_TIMELINE_ARGS+=(
				--colliding-bx-mask "$TRIGGER_COLLIDING_BX_MASK"
				--reference-bx-slot "$TRIGGER_REFERENCE_BX_SLOT"
				--shift-beam "$TRIGGER_SHIFT_BEAM"
				--run-fill-map "$TRIGGER_RUN_FILL_MAP"
			)
		elif [[ -n "$TRIGGER_COLLIDING_BX_FILE" ]]; then
			if [[ "$TRIGGER_COLLIDING_BX_FILE" != /* || ! -s "$TRIGGER_COLLIDING_BX_FILE" ]]; then
				echo "ERROR: TRIGGER_COLLIDING_BX_FILE must be an absolute, non-empty file when set" >&2
				exit 1
			fi
			if [[ -n "$TRIGGER_REFERENCE_BX_SLOT" || -n "$TRIGGER_SHIFT_BEAM" ]]; then
				echo "ERROR: TRIGGER_REFERENCE_BX_SLOT/TRIGGER_SHIFT_BEAM require TRIGGER_COLLIDING_BX_MASK" >&2
				exit 1
			fi
			TRIGGER_TIMELINE_ARGS+=(--colliding-bx-file "$TRIGGER_COLLIDING_BX_FILE")
		elif [[ -n "$TRIGGER_REFERENCE_BX_SLOT" || -n "$TRIGGER_SHIFT_BEAM" ]]; then
			echo "ERROR: TRIGGER_REFERENCE_BX_SLOT/TRIGGER_SHIFT_BEAM require TRIGGER_COLLIDING_BX_MASK" >&2
			exit 1
		fi
		if [[ -n "$TRIGGER_GROUP_ID" ]]; then
			TRIGGER_TIMELINE_ARGS+=(--group-id "$TRIGGER_GROUP_ID")
		fi
		;;
	*)
		echo "ERROR: TRIGGER_TIMELINE_MODE must be none or zero_bias_proxy (got '$TRIGGER_TIMELINE_MODE')" >&2
		exit 1
		;;
esac

ensure_trigger_timeline() {
	[[ "$TRIGGER_TIMELINE_MODE" == zero_bias_proxy ]] || return 0
	local timeline_output="$TRIGGER_TIMELINE_DIR/trigger_timeline_part${PART}.jsonl"
	if [[ "$FORCE" -eq 1 && -e "$timeline_output" ]]; then
		echo "Force rerun requested; removing existing trigger timeline: $timeline_output"
		rm -f -- "$timeline_output"
	fi
	if [[ -s "$timeline_output" ]]; then
		echo "Trigger timeline already exists: $timeline_output"
		return 0
	fi
	mkdir -p "$TRIGGER_TIMELINE_DIR"
	echo "Generating empirical ZeroBias trigger timeline: $timeline_output"
	python3 "$WORKFLOW_ROOT/scripts/sample_zero_bias_trigger_timeline.py" \
		"$TRIGGER_LIBRARY_JSONL" \
		--l1-menu "$TRIGGER_L1_MENU_JSON" \
		--output "$timeline_output" \
		--start-bx "$TRIGGER_TIMELINE_START_BX" \
		--end-bx "$TRIGGER_TIMELINE_END_BX" \
		--signal-events "$N_EVENTS" \
		--seed "$TRIGGER_TIMELINE_EFFECTIVE_SEED" \
		"${TRIGGER_TIMELINE_ARGS[@]}"
	[[ -s "$timeline_output" ]] || {
		echo "ERROR: trigger timeline generator did not produce a non-empty output" >&2
		return 1
	}
}
READOUT_DIAGNOSTICS_CUSTOMISE=""
case "${SHIFT_READOUT_DIAGNOSTICS:-0}" in
	0|false|False) ;;
	1|true|True)
		READOUT_DIAGNOSTICS_CUSTOMISE="; [output.outputCommands.extend(('keep *_simMuonDTDigis_*_*', 'keep *_simMuonCSCDigis_*_*', 'keep *_simMuonGEMDigis_*_*', 'keep *_simDtTriggerPrimitiveDigis_*_*', 'keep *_simCscTriggerPrimitiveDigis_*_*', 'keep *_simMuonGEMPadDigis_*_*', 'keep *_simMuonGEMPadDigiClusters_*_*', 'keep *_simBmtfDigis_*_*', 'keep *_simKBmtfDigis_*_*', 'keep *_simOmtfDigis_*_*', 'keep *_simEmtfDigis_*_*', 'keep *_simEmtfShowers_*_*', 'keep *_simGmtStage2Digis_*_*')) for output in process.outputModules_().values()]"
		;;
	*) echo "ERROR: SHIFT_READOUT_DIAGNOSTICS must be 0/1 or false/true" >&2; exit 1 ;;
esac

SIMHIT_REFERENCE_CUSTOMISE=""
if [[ -n "$SHIFT_SIMHIT_REFERENCE_BX_OFFSET" ]]; then
	if [[ "$PILEUP_MODE" != none ]]; then
		echo "ERROR: same-SimHit reference timing is restricted to PILEUP_MODE=none" >&2
		exit 1
	fi
	if [[ ! "$SHIFT_SIMHIT_REFERENCE_BX_OFFSET" =~ ^-?[0-9]+$ ]]; then
		echo "ERROR: SHIFT_SIMHIT_REFERENCE_BX_OFFSET must be empty or an integer" >&2
		exit 1
	fi
	if [[ ! "$SHIFT_SIMHIT_REFERENCE_PHASE_NS" =~ ^-?([0-9]+([.][0-9]*)?|[.][0-9]+)$ ]]; then
		echo "ERROR: SHIFT_SIMHIT_REFERENCE_PHASE_NS must be a finite decimal number" >&2
		exit 1
	fi
	case "$SHIFT_READOUT_DIAGNOSTICS" in
		1|true|True) ;;
		*) echo "ERROR: same-SimHit reference timing requires SHIFT_READOUT_DIAGNOSTICS=1" >&2; exit 1 ;;
	esac
	if [[ "$SHIFT_SIMHIT_REFERENCE_INPUT" != /* || ! -s "$SHIFT_SIMHIT_REFERENCE_INPUT" ]]; then
		echo "ERROR: SHIFT_SIMHIT_REFERENCE_INPUT must be one absolute, non-empty Step-1 file" >&2
		exit 1
	fi
	SIMHIT_REFERENCE_CUSTOMISE="; from IOMC.ShiftEventTiming.shiftSimHitTiming_customise import customiseShiftSimHitReferenceTiming; process = customiseShiftSimHitReferenceTiming(process, bxOffset=${SHIFT_SIMHIT_REFERENCE_BX_OFFSET}, phaseNs=${SHIFT_SIMHIT_REFERENCE_PHASE_NS}, bunchSpacingNs=${SHIFT_TIMING_BUNCH_SPACING_NS})"
fi

mkdir -p "$WORKDIR" "$OUTPUT_DIR" "$CONFIG_DIR" "$LOG_DIR"
cd "$WORKDIR"

# dCache/PNFS can reject cmsDriver's in-place configuration-file creation.
# Generate and run locally, then retain a campaign snapshot when possible.
LOCAL_STEP2_DIR="$(mktemp -d /tmp/shift_cmssw_step2_XXXXXX)"
cleanup_step2_tmp() {
	local status=$?
	if [[ "$status" -eq 0 && "${KEEP_STEP2_TMP:-0}" != 1 ]]; then
		rm -rf "$LOCAL_STEP2_DIR"
	else
		echo "Step 2 temporary files retained in $LOCAL_STEP2_DIR" >&2
	fi
}
trap cleanup_step2_tmp EXIT
LOCAL_CONFIG="$LOCAL_STEP2_DIR/events_step2_part${PART}_cfg.py"
LOCAL_LOG="$LOCAL_STEP2_DIR/step2_events_part${PART}.log"
LOCAL_OUTPUT="$LOCAL_STEP2_DIR/events_step2_part${PART}.root"

# CMSSW's standard pileup MixingModule creates/opens histProbFunction.root
# relative to the process working directory.  Running many jobs from the shared
# campaign directory makes them race on that file (and can expose a partially
# written ROOT file over EOS).  Keep all such runtime artifacts in this job's
# private local scratch directory.
cd "$LOCAL_STEP2_DIR"

OUTPUT="$OUTPUT_DIR/events_step2_part${PART}.root"
if [[ "$FORCE" -eq 1 && -e "$OUTPUT" ]]; then
	echo "Force rerun requested; removing existing Step 2 output: $OUTPUT"
	rm -f -- "$OUTPUT"
fi
if output_is_valid "$OUTPUT"; then
	echo "Step 2 output already exists and is valid: $OUTPUT"
	ensure_trigger_timeline
	exit 0
fi
if [[ -e "$OUTPUT" ]]; then
	echo "Removing invalid Step 2 output before retry: $OUTPUT"
	rm -f -- "$OUTPUT"
fi

if [[ -n "$SHIFT_SIMHIT_REFERENCE_BX_OFFSET" ]]; then
	INPUT="$SHIFT_SIMHIT_REFERENCE_INPUT"
else
	INPUT="$STEP1_DIR/events_step1_part${PART}.root"
fi
if [[ ! -s "$INPUT" ]]; then
	echo "ERROR: $WORKDIR/$INPUT is missing or empty" >&2
	exit 1
fi

echo "=== Step 2: DIGI,L1,DIGI2RAW,HLT (Run 3) ==="
echo "Pileup: mode=$PILEUP_MODE scenario=${PILEUP_SCENARIO:-none} input=${PILEUP_INPUT:-none} seed=${PILEUP_SEED:-none}"
if [[ -n "$SHIFT_SIMHIT_REFERENCE_BX_OFFSET" ]]; then
	echo "Same-SimHit reference timing: bx=$SHIFT_SIMHIT_REFERENCE_BX_OFFSET phase_ns=$SHIFT_SIMHIT_REFERENCE_PHASE_NS"
fi
cmsDriver.py step2 \
	--step "DIGI:pdigi_valid,L1,DIGI2RAW,HLT:${HLT_MENU},ENDJOB" \
	--conditions "$CONDITIONS" \
	--datatier GEN-SIM-RAW \
	--eventcontent GENRAW \
	--geometry "$GEOMETRY" \
	--era "$ERA" \
	--filein "file:$INPUT" \
	--fileout "file:$LOCAL_OUTPUT" \
	--python_filename "$LOCAL_CONFIG" \
	--customise_commands "from PhysicsTools.ShiftMuonSegments.shiftMuonSegments_customise import customiseKeepShiftTruth; process = customiseKeepShiftTruth(process, keepMergedTrackTruth=False, keepSimMuonRPCDigis=True, keepPileupPlayback=True)${PILEUP_CUSTOMISE}${READOUT_DIAGNOSTICS_CUSTOMISE}${SIMHIT_REFERENCE_CUSTOMISE}" \
	"${PILEUP_ARGS[@]}" \
	--no_exec \
	-n "$N_EVENTS"

CONFIG_SNAPSHOT="$CONFIG_DIR/events_step2_part${PART}_cfg.py"
if ! cp "$LOCAL_CONFIG" "$CONFIG_SNAPSHOT"; then
	echo "WARNING: could not archive Step 2 config at $CONFIG_SNAPSHOT; continuing with local config" >&2
fi

cmsRun "$LOCAL_CONFIG" 2>&1 | tee "$LOCAL_LOG"

if ! output_is_valid "$LOCAL_OUTPUT"; then
	echo "ERROR: Step 2 cmsRun returned successfully but did not produce a valid local output: $LOCAL_OUTPUT" >&2
	exit 1
fi
stage_cmssw_output "$LOCAL_OUTPUT" "$OUTPUT"

LOG_SNAPSHOT="$LOG_DIR/step2_events_part${PART}.log"
if ! cp "$LOCAL_LOG" "$LOG_SNAPSHOT"; then
	echo "WARNING: could not archive Step 2 log at $LOG_SNAPSHOT" >&2
fi

ensure_trigger_timeline
