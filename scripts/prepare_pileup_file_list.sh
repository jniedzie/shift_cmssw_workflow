#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKFLOW_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$WORKFLOW_ROOT/config/workflow.env"

OUTPUT="${1:-$WORKFLOW_ROOT/config/pileup_files_run3_${COLLISION_YEAR}.txt}"
MAX_FILES="${2:-0}"
RSE="${3:-$PILEUP_RSE}"
if [[ "$OUTPUT" != /* ]]; then
	echo "ERROR: output file must be an absolute path" >&2
	exit 2
fi
if [[ ! "$MAX_FILES" =~ ^[0-9]+$ ]]; then
	echo "ERROR: max-files must be a non-negative integer" >&2
	exit 2
fi
mkdir -p "$(dirname "$OUTPUT")"
TEMP_OUTPUT="$(mktemp /tmp/shift_pileup_files_XXXXXX)"
cleanup() {
	rm -f -- "$TEMP_OUTPUT"
}
trap cleanup EXIT

if [[ -n "$RSE" ]]; then
	if ! command -v rucio >/dev/null 2>&1 && [[ -r /cvmfs/cms.cern.ch/rucio/setup-py3.sh ]]; then
		# shellcheck disable=SC1091
		source /cvmfs/cms.cern.ch/rucio/setup-py3.sh
	fi
	command -v rucio >/dev/null 2>&1 || {
		echo "ERROR: rucio is not available (required for PILEUP_RSE=$RSE)" >&2
		exit 1
	}
	echo "Querying available Rucio replicas for $PILEUP_DATASET at $RSE"
	rucio replica list file --pfns --protocols root --rses "$RSE" \
		"cms:$PILEUP_DATASET" > "$TEMP_OUTPUT"
else
	command -v dasgoclient >/dev/null 2>&1 || {
		echo "ERROR: dasgoclient is not available" >&2
		exit 1
	}
	echo "WARNING: PILEUP_RSE is empty; the DAS inventory may contain files with no readable replica" >&2
	dasgoclient -query="file dataset=$PILEUP_DATASET" > "$TEMP_OUTPUT"
fi
if [[ "$MAX_FILES" -gt 0 ]]; then
	sed -n "1,${MAX_FILES}p" "$TEMP_OUTPUT" > "${TEMP_OUTPUT}.limited"
	mv "${TEMP_OUTPUT}.limited" "$TEMP_OUTPUT"
fi
if [[ ! -s "$TEMP_OUTPUT" ]]; then
	echo "ERROR: no pileup files found for $PILEUP_DATASET${RSE:+ at $RSE}" >&2
	exit 1
fi
if ! awk '(index($0, "/store/") == 1 || index($0, "root://") == 1) && $0 ~ /[.]root$/ { next } { exit 1 }' "$TEMP_OUTPUT"; then
	echo "ERROR: pileup query returned an unexpected file name" >&2
	exit 1
fi

# Write via a temporary sibling and rename so readers never see a partial list.
STAGED_OUTPUT="${OUTPUT}.partial.$$"
cp "$TEMP_OUTPUT" "$STAGED_OUTPUT"
mv "$STAGED_OUTPUT" "$OUTPUT"
echo "Wrote $(wc -l < "$OUTPUT") pileup files to $OUTPUT"
echo "Configure PILEUP_INPUT=filelist:$OUTPUT"
