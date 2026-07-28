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
	if ! scram b -j 1 >/dev/null; then
		setup_error "SCRAM failed while registering $PYTHIA_CONFIG"; return 1 2>/dev/null || exit 1
	fi
	if [[ -n "${AOD_TO_EXONANO_CUSTOMISE:-}" ]]; then
		# cmsDriver imports the customization during configuration generation.
		# Check that the one-time build exposed it to Python before Condor jobs
		# inherit CMSSW_PREPARED=1 and skip rebuilding.
		CUSTOMISE_MODULE="${AOD_TO_EXONANO_CUSTOMISE%%.*}"
		CUSTOMISE_MODULE="${CUSTOMISE_MODULE//\//.}"
		if ! python3 -c "import ${CUSTOMISE_MODULE}" >/dev/null 2>&1; then
			# Some prebuilt releases do not regenerate the Python package links for
			# source packages added after the release was created. Install the
			# standard CMSSW package links explicitly, then recheck the import.
			PYTHON_PACKAGE_DIR="$CMSSW_SRC/PhysicsTools/ShiftMuonSegments/python"
			PYTHON_INSTALL_DIR="$CMSSW_BASE/python/PhysicsTools/ShiftMuonSegments"
			if [[ -d "$PYTHON_PACKAGE_DIR" ]] && mkdir -p "$PYTHON_INSTALL_DIR"; then
				for python_file in "$PYTHON_PACKAGE_DIR"/*.py; do
					[[ -f "$python_file" ]] || continue
					ln -sfn "$python_file" "$PYTHON_INSTALL_DIR/$(basename "$python_file")"
				done
			fi
			if ! python3 -c "import ${CUSTOMISE_MODULE}" >/dev/null 2>&1; then
				setup_error "configured customization is not importable after SCRAM build: $AOD_TO_EXONANO_CUSTOMISE"; return 1 2>/dev/null || exit 1
			fi
		fi
	fi
else
	echo "[setup_cmssw] Using prebuilt CMSSW release"
fi
cd "$WORKFLOW_ROOT"

mkdir -p "$SAMPLE_DIR/samples/step1" "$SAMPLE_DIR/samples/step2" "$SAMPLE_DIR/samples/step3" "$SAMPLE_DIR/samples/step4" \
	"$SAMPLE_DIR/logs" \
	"$SAMPLE_DIR/configs/step1" "$SAMPLE_DIR/configs/step2" "$SAMPLE_DIR/configs/step3" "$SAMPLE_DIR/configs/step4"


export WORKFLOW_ROOT CMSSW_SRC SAMPLE_BASE SAMPLE_NAME CAMPAIGN_NAME SAMPLE_DIR PART
echo "[setup_cmssw] Environment ready (PART=$PART)"

output_is_valid() {
	local output="$1"
	[[ -s "$output" ]] || return 1
	if command -v edmFileUtil >/dev/null 2>&1; then
		edmFileUtil "$output" >/dev/null 2>&1
	fi
}
