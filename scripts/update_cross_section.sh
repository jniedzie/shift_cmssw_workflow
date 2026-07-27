#!/usr/bin/env bash
set -euo pipefail

: "${1:?generator log is required}"
: "${2:?cross-section file is required}"
: "${3:?sample name is required}"

LOG_FILE="$1"
OUTPUT_FILE="$2"
SAMPLE="$3"
LOCK_FILE="${OUTPUT_FILE}.lock"

# Pythia8's statistics summary contains, depending on the CMSSW/Pythia
# version, either "sigmaGen = ..." or "sigmaGen ..." followed by sigmaErr.
line=$(rg -i 'sigmaGen' "$LOG_FILE" | tail -n 1 || true)
if [[ -z "$line" ]]; then
	echo "Could not find Pythia sigmaGen in $LOG_FILE" >&2
	exit 1
fi

if [[ "$line" =~ sigmaGen[[:space:]]*=?[[:space:]]*([0-9.eE+-]+)[[:space:]]*(GeV|mb|pb|fb)? ]]; then
	sigma="${BASH_REMATCH[1]}"
	unit="${BASH_REMATCH[2]:-mb}"
	error=""
[[ "$line" =~ (sigmaErr|error)[[:space:]]*=?[[:space:]]*([0-9.eE+-]+) ]] && error="${BASH_REMATCH[2]}"
else
	echo "Could not parse Pythia sigmaGen line: $line" >&2
	exit 1
fi

mkdir -p "$(dirname "$OUTPUT_FILE")"
exec 9>"$LOCK_FILE"
flock 9
tmp_file="${OUTPUT_FILE}.tmp.$$"
{
	printf '# Latest Pythia generator cross sections (updated atomically; unit is %s)\n' "$unit"
	printf '%s sigmaGen=%s' "$SAMPLE" "$sigma"
[[ -n "$error" ]] && printf ' sigmaErr=%s' "$error"
	printf '\n'
} > "$tmp_file"
mv -f "$tmp_file" "$OUTPUT_FILE"
