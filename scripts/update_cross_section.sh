#!/usr/bin/env bash
set -euo pipefail

: "${1:?generator log is required}"
: "${2:?cross-section file is required}"
: "${3:?sample name is required}"

LOG_FILE="$1"
OUTPUT_FILE="$2"
SAMPLE="$3"
LOCK_FILE="${OUTPUT_FILE}.lock"

# This is aggregate bookkeeping, not a per-job output.  Keep the first
# successful value and do not require PNFS lock-file creation for every job.
if [[ -s "$OUTPUT_FILE" ]]; then
	exit 0
fi

before_line=$(grep -i 'Before Filter: total cross section' "$LOG_FILE" | tail -n 1 || true)
after_line=$(grep -i 'After filter: final cross section' "$LOG_FILE" | tail -n 1 || true)
if [[ -z "$before_line" || -z "$after_line" ]]; then
	echo "Could not find GenXsecAnalyzer cross sections in $LOG_FILE" >&2
	exit 1
fi

parse_cross_section() {
	local line="$1"
	printf '%s\n' "$line" | sed -nE 's/.*=[[:space:]]*([0-9.eE+-]+)[[:space:]]*\+[-][[:space:]]*([0-9.eE+-]+)[[:space:]]*(GeV|mb|pb|fb).*/\1 \2 \3/p'
}

read -r before before_error unit < <(parse_cross_section "$before_line") || {
	echo "Could not parse Before Filter cross section: $before_line" >&2
	exit 1
}
read -r after after_error after_unit < <(parse_cross_section "$after_line") || {
	echo "Could not parse After filter cross section: $after_line" >&2
	exit 1
}

mkdir -p "$(dirname "$OUTPUT_FILE")"
tmp_file="${OUTPUT_FILE}.tmp.$$"
{
	printf '# Latest GenXsecAnalyzer cross sections (updated atomically)\n'
	printf '%s before_filter=%s +- %s %s after_filter=%s +- %s %s\n' \
		"$SAMPLE" "$before" "$before_error" "$unit" "$after" "$after_error" "$after_unit"
} > "$tmp_file"
if [[ ! -e "$OUTPUT_FILE" ]]; then
	mv -f "$tmp_file" "$OUTPUT_FILE"
else
	rm -f "$tmp_file"
fi
