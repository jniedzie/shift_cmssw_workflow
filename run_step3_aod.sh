#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
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
source "$SCRIPT_DIR/scripts/setup_cmssw.sh"
N_EVENTS="${POSITIONAL_ARGS[1]:-$N_EVENTS}"

case "$SHIFT_DT_MODE" in
	off) DT_ENABLED_CMSSW=False; DT_NAVIGATION=Standard; DT_NAVIGATION_CODE=0 ;;
	standard) DT_ENABLED_CMSSW=True; DT_NAVIGATION=Standard; DT_NAVIGATION_CODE=1 ;;
	direct) DT_ENABLED_CMSSW=True; DT_NAVIGATION=Direct; DT_NAVIGATION_CODE=2 ;;
	*) echo "ERROR: SHIFT_DT_MODE must be off, standard, or direct (got '$SHIFT_DT_MODE')" >&2; exit 1 ;;
esac
case "$SHIFT_TRACKER_MODE" in
	none|general|p5) ;;
	*) echo "ERROR: SHIFT_TRACKER_MODE must be none, general, or p5 (got '$SHIFT_TRACKER_MODE')" >&2; exit 1 ;;
esac
case "$SHIFT_ENABLE_GEM" in
	0) GEM_ENABLED_CMSSW=False ;;
	1) GEM_ENABLED_CMSSW=True ;;
	*) echo "ERROR: SHIFT_ENABLE_GEM must be 0 or 1 (got '$SHIFT_ENABLE_GEM')" >&2; exit 1 ;;
esac
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
if [[ ! "$SHIFT_RECO_VARIANT_CODE" =~ ^[0-9]+$ ]]; then
	echo "ERROR: SHIFT_RECO_VARIANT_CODE must be a non-negative integer (got '$SHIFT_RECO_VARIANT_CODE')" >&2
	exit 1
fi
OUTPUT_DIR="$STEP3_DIR"
CONFIG_DIR="$STEP3_CONFIG_DIR"
mkdir -p "$SAMPLE_DIR" "$OUTPUT_DIR" "$CONFIG_DIR" "$LOG_DIR"
cd "${WORKDIR:-$SAMPLE_DIR}"

# dCache/PNFS can reject cmsDriver's in-place configuration-file creation.
# Generate and run locally, then retain a campaign snapshot when possible.
LOCAL_STEP3_DIR="$(mktemp -d /tmp/shift_cmssw_step3_XXXXXX)"
cleanup_step3_tmp() {
  local status=$?
  if [[ "$status" -eq 0 && "${KEEP_STEP3_TMP:-0}" != 1 ]]; then
    rm -rf "$LOCAL_STEP3_DIR"
  else
    echo "Step 3 temporary files retained in $LOCAL_STEP3_DIR" >&2
  fi
}
trap cleanup_step3_tmp EXIT
LOCAL_CONFIG="$LOCAL_STEP3_DIR/events_AOD_part${PART}_cfg.py"
LOCAL_LOG="$LOCAL_STEP3_DIR/step3_events_AOD_part${PART}.log"
LOCAL_OUTPUT="$LOCAL_STEP3_DIR/events_AOD_part${PART}.root"

OUTPUT="$OUTPUT_DIR/events_AOD_part${PART}.root"
if [[ "$FORCE" -eq 1 && -e "$OUTPUT" ]]; then
	echo "Force rerun requested; removing existing Step 3 output: $OUTPUT"
	rm -f -- "$OUTPUT"
fi
if output_is_valid "$OUTPUT"; then
	echo "Step 3 output already exists and is valid: $OUTPUT"
	exit 0
fi
if [[ -e "$OUTPUT" ]]; then
	echo "Removing invalid Step 3 output before retry: $OUTPUT"
	rm -f -- "$OUTPUT"
fi
INPUT="$STEP2_DIR/events_step2_part${PART}.root"
[[ -s "$INPUT" ]] || { echo "ERROR: $INPUT is missing or empty" >&2; exit 1; }
echo "=== Step 3: RAW2DIGI,L1Reco,RECO,RECOSIM -> AODSIM ==="
echo "SHIFT reconstruction variant: $SHIFT_RECO_VARIANT (code $SHIFT_RECO_VARIANT_CODE)"
echo "Detector modes: DT=$SHIFT_DT_MODE tracker=$SHIFT_TRACKER_MODE GEM=$SHIFT_ENABLE_GEM HCALdiag=$SHIFT_ENABLE_HCAL_DIAGNOSTICS ZDCdiag=$SHIFT_ENABLE_ZDC_DIAGNOSTICS"
cmsDriver.py step3 --step RAW2DIGI,L1Reco,RECO,RECOSIM --conditions "$CONDITIONS" \
  --datatier AODSIM --eventcontent AODSIM --geometry "$GEOMETRY" --era "$ERA" \
  --filein "file:$INPUT" --fileout "file:$LOCAL_OUTPUT" \
  --python_filename "$LOCAL_CONFIG" --no_exec -n "$N_EVENTS" \
  --customise_commands "from PhysicsTools.ShiftMuonSegments.shiftMuonSegments_customise import customiseKeepShiftTruth, customiseRecoForShiftMuons, customiseTraversingShiftMuonReco, customiseRecoDebug; process = customiseKeepShiftTruth(process, keepHcalSimHits=${HCAL_DIAGNOSTICS_CMSSW}, keepZDCSimHits=${ZDC_DIAGNOSTICS_CMSSW}); process = customiseRecoForShiftMuons(process, numberOfSigma=5.0, maxHitChi2=100.0, seedPosition='in', doBackwardFilter=True, keepAllSeedSegments=True, navigationType='${DT_NAVIGATION}', pcaPropagator='SteppingHelixPropagatorAny', enableDTMeasurement=${DT_ENABLED_CMSSW}, enableGEMMeasurement=${GEM_ENABLED_CMSSW}); process = customiseTraversingShiftMuonReco(process, trackerMode='${SHIFT_TRACKER_MODE}', enableDTMeasurement=${DT_ENABLED_CMSSW}); process = customiseRecoDebug(process, enableDTMeasurement=${DT_ENABLED_CMSSW}, enableGEMMeasurement=${GEM_ENABLED_CMSSW}, trackerMode='${SHIFT_TRACKER_MODE}', enableHcalDiagnostics=${HCAL_DIAGNOSTICS_CMSSW}, enableZDCDiagnostics=${ZDC_DIAGNOSTICS_CMSSW}, dtNavigationMode=${DT_NAVIGATION_CODE}, recoVariantCode=${SHIFT_RECO_VARIANT_CODE})"

CONFIG_SNAPSHOT="$CONFIG_DIR/events_AOD_part${PART}_cfg.py"
if ! cp "$LOCAL_CONFIG" "$CONFIG_SNAPSHOT"; then
  echo "WARNING: could not archive Step 3 config at $CONFIG_SNAPSHOT; continuing with local config" >&2
fi

cmsRun "$LOCAL_CONFIG" 2>&1 | tee "$LOCAL_LOG"

if ! output_is_valid "$LOCAL_OUTPUT"; then
	echo "ERROR: Step 3 cmsRun returned successfully but did not produce a valid local output: $LOCAL_OUTPUT" >&2
	exit 1
fi
stage_cmssw_output "$LOCAL_OUTPUT" "$OUTPUT"

LOG_SNAPSHOT="$LOG_DIR/step3_events_AOD_part${PART}.log"
if ! cp "$LOCAL_LOG" "$LOG_SNAPSHOT"; then
  echo "WARNING: could not archive Step 3 log at $LOG_SNAPSHOT" >&2
fi
