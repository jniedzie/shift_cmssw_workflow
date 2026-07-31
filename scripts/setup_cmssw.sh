#!/usr/bin/env bash
set -uo pipefail

SETUP_SOURCED=0
[[ "${BASH_SOURCE[0]}" != "${0}" ]] && SETUP_SOURCED=1

setup_error() {
	echo "ERROR [setup_cmssw]: $*" >&2
	if [[ "$SETUP_SOURCED" -eq 1 ]]; then
		return 1
	fi
	exit 1
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKFLOW_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$WORKFLOW_ROOT/config/workflow.env"

[[ -n "${CMSSW_SRC:-}" ]] || { setup_error "CMSSW_SRC is not set in config/workflow.env"; return 1 2>/dev/null || exit 1; }
[[ -d "$CMSSW_SRC" ]] || { setup_error "CMSSW_SRC does not exist: $CMSSW_SRC"; return 1 2>/dev/null || exit 1; }

: "${CHUNK:=0}"
if [[ ! "$CHUNK" =~ ^[0-9]+$ ]]; then
	setup_error "CHUNK must be a non-negative integer (got '$CHUNK')"; return 1 2>/dev/null || exit 1
fi
PART="$(printf '%04d' "$CHUNK")"

echo "[setup_cmssw] Entering $CMSSW_SRC"
cd "$CMSSW_SRC"
if ! command -v cmsenv >/dev/null 2>&1; then
	setup_error "cmsenv is not available; initialize the CMSSW environment first"; return 1 2>/dev/null || exit 1
fi
if ! cmsenv; then
	setup_error "cmsenv failed in $CMSSW_SRC; the remote shell was left intact"; return 1 2>/dev/null || exit 1
fi

if [[ "${CMSSW_USE_BIGLIB:-0}" == 0 ]]; then
	# BigProducts bundle many packages into one large library.  A source edit
	# otherwise requires relinking that whole bundle and can mask a freshly
	# rebuilt package library.  Prefer granular libraries for development.
	GRANULAR_LIBRARY_PATH=""
	IFS=: read -r -a cmssw_library_dirs <<< "${LD_LIBRARY_PATH:-}"
	for library_dir in "${cmssw_library_dirs[@]}"; do
		[[ "$library_dir" == */biglib/* ]] && continue
		GRANULAR_LIBRARY_PATH="${GRANULAR_LIBRARY_PATH:+$GRANULAR_LIBRARY_PATH:}$library_dir"
	done
	export LD_LIBRARY_PATH="$GRANULAR_LIBRARY_PATH"
fi
[[ -n "${PYTHIA_CONFIG:-}" ]] || { setup_error "PYTHIA_CONFIG is not set in config/workflow.env"; return 1 2>/dev/null || exit 1; }
PYTHIA_FRAGMENT_NAME="$(basename "$PYTHIA_CONFIG")"
PYTHIA_FRAGMENT_DIR="$(dirname "$PYTHIA_CONFIG")"
FRAGMENT="$WORKFLOW_ROOT/fragments/$PYTHIA_FRAGMENT_NAME"
LINK_TARGET="$CMSSW_SRC/$PYTHIA_CONFIG"
LINK_DIR="$(dirname "$LINK_TARGET")"
PACKAGE_DIR="$(dirname "$LINK_DIR")"
[[ -f "$FRAGMENT" ]] || { setup_error "workflow fragment is missing: $FRAGMENT"; return 1 2>/dev/null || exit 1; }
if [[ "${CMSSW_PREPARED:-0}" != 1 ]] && ! mkdir -p "$LINK_DIR"; then
	setup_error "cannot create CMSSW fragment directory: $LINK_DIR"; return 1 2>/dev/null || exit 1
fi
if [[ "${CMSSW_PREPARED:-0}" != 1 && ! -f "$PACKAGE_DIR/BuildFile.xml" ]]; then
	if ! cp "$WORKFLOW_ROOT/Configuration/GenProduction/BuildFile.xml" "$PACKAGE_DIR/BuildFile.xml"; then
		setup_error "cannot install the CMSSW BuildFile: $PACKAGE_DIR/BuildFile.xml"; return 1 2>/dev/null || exit 1
	fi
fi
if [[ "${CMSSW_PREPARED:-0}" != 1 ]] && ! ln -sfn "$FRAGMENT" "$LINK_TARGET"
then
	setup_error "cannot create the Pythia fragment symlink: $LINK_TARGET"; return 1 2>/dev/null || exit 1
fi
echo "[setup_cmssw] Linked $PYTHIA_CONFIG"
if [[ "${CMSSW_PREPARED:-0}" != 1 ]]; then
	# SCRAM tracks source and dependency timestamps, so this is incremental:
	# only changed packages and their dependents are rebuilt.  Do not use
	# `scram b clean` here; a clean build is both unnecessary and expensive.
	CMSSW_BUILD_JOBS="${CMSSW_BUILD_JOBS:-8}"
	if [[ ! "$CMSSW_BUILD_JOBS" =~ ^[1-9][0-9]*$ ]]; then
		setup_error "CMSSW_BUILD_JOBS must be a positive integer (got '$CMSSW_BUILD_JOBS')"; return 1 2>/dev/null || exit 1
	fi
	if ! scram b -j "$CMSSW_BUILD_JOBS" >/dev/null; then
		setup_error "SCRAM failed while registering $PYTHIA_CONFIG"; return 1 2>/dev/null || exit 1
	fi
	if [[ -n "${AOD_TO_EXONANO_CUSTOMISE:-}" ]]; then
		# cmsDriver imports the customization during configuration generation.
		# Check that the one-time build exposed it to Python before Condor jobs
		# inherit CMSSW_PREPARED=1 and skip rebuilding.
		CUSTOMISE_MODULE="${AOD_TO_EXONANO_CUSTOMISE%%.*}"
		CUSTOMISE_MODULE="${CUSTOMISE_MODULE//\//.}"
		if ! python3 -c "import ${CUSTOMISE_MODULE}" >/dev/null 2>&1; then
			# SCRAM owns the generated Python package area.  Creating links there
			# manually can make __init__.py point back to itself and recurse during
			# import, so report the real import exception without altering it.
			python3 -c "import ${CUSTOMISE_MODULE}" || true
			setup_error "configured customization is not importable after incremental SCRAM build: $AOD_TO_EXONANO_CUSTOMISE"; return 1 2>/dev/null || exit 1
		fi
	fi
else
	echo "[setup_cmssw] Using prebuilt CMSSW release"
fi
cd "$WORKFLOW_ROOT"

mkdir -p "$STEP1_DIR" "$STEP2_DIR" "$STEP3_DIR" "$STEP4_DIR" "$LOG_DIR" \
	"$STEP1_CONFIG_DIR" "$STEP2_CONFIG_DIR" "$STEP3_CONFIG_DIR" "$STEP4_CONFIG_DIR"


export WORKFLOW_ROOT CMSSW_SRC SAMPLE_BASE SAMPLE_NAME CAMPAIGN_NAME SAMPLE_DIR PART \
	SAMPLES_DIR STEP1_DIR STEP2_DIR STEP3_DIR STEP4_DIR CONFIG_BASE_DIR \
	STEP1_CONFIG_DIR STEP2_CONFIG_DIR STEP3_CONFIG_DIR STEP4_CONFIG_DIR LOG_DIR \
	CROSS_SECTION_FILE
echo "[setup_cmssw] Environment ready (PART=$PART)"

output_is_valid() {
	local output="$1"
	[[ -s "$output" ]] || return 1
	if command -v edmFileUtil >/dev/null 2>&1; then
		edmFileUtil "$output" >/dev/null 2>&1
	fi
}
