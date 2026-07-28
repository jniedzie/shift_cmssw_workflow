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
if ! mkdir -p "$LINK_DIR"; then
	setup_error "cannot create CMSSW fragment directory: $LINK_DIR"; return 1 2>/dev/null || exit 1
fi
if [[ ! -f "$PACKAGE_DIR/BuildFile.xml" ]]; then
	if ! cp "$WORKFLOW_ROOT/Configuration/GenProduction/BuildFile.xml" "$PACKAGE_DIR/BuildFile.xml"; then
		setup_error "cannot install the CMSSW BuildFile: $PACKAGE_DIR/BuildFile.xml"; return 1 2>/dev/null || exit 1
	fi
fi
if ! ln -sfn "$FRAGMENT" "$LINK_TARGET"
then
	setup_error "cannot create the Pythia fragment symlink: $LINK_TARGET"; return 1 2>/dev/null || exit 1
fi
echo "[setup_cmssw] Linked $PYTHIA_CONFIG"
if ! scram b -j 1 >/dev/null; then
	setup_error "SCRAM failed while registering $PYTHIA_CONFIG"; return 1 2>/dev/null || exit 1
fi
cd "$WORKFLOW_ROOT"

mkdir -p "$SAMPLE_DIR/samples/step1" "$SAMPLE_DIR/samples/step2" "$SAMPLE_DIR/samples/step3" "$SAMPLE_DIR/samples/step4" \
	"$SAMPLE_DIR/logs" \
	"$SAMPLE_DIR/configs/step1" "$SAMPLE_DIR/configs/step2" "$SAMPLE_DIR/configs/step3" "$SAMPLE_DIR/configs/step4"


export WORKFLOW_ROOT CMSSW_SRC SAMPLE_BASE SAMPLE_NAME CAMPAIGN_NAME SAMPLE_DIR PART
echo "[setup_cmssw] Environment ready (PART=$PART)"
