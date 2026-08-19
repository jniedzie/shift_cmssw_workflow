#!/usr/bin/env bash

set -euo pipefail

if [[ $# -lt 2 || $# -gt 5 ]]; then
    echo "Usage: $0 INPUT_RAW OUTPUT_JSONL [MAX_EVENTS] [SOURCE_DATASET] [COLLISION_YEAR]" >&2
    exit 2
fi

input_raw="$1"
output_jsonl="$2"
max_events="${3:-100}"
source_dataset="${4:-}"
collision_year="${5:-${COLLISION_YEAR:-2023}}"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/shift_zero_bias.XXXXXX")"
unpacked_file="$tmp_dir/zero_bias_ugt.root"

cleanup() {
    rm -rf "$tmp_dir"
}
trap cleanup EXIT

if ! command -v cmsRun >/dev/null 2>&1; then
    echo "ERROR: cmsRun is unavailable; enter a CMSSW runtime first" >&2
    exit 1
fi

mkdir -p "$(dirname "$output_jsonl")"

cmsRun "$script_dir/zero_bias_unpack_cfg.py" \
    inputFiles="$input_raw" \
    outputFile="$unpacked_file" \
    maxEvents="$max_events" \
    collisionYear="$collision_year"

extract_args=(
    "$script_dir/extract_zero_bias_trigger_bits.py"
    "$unpacked_file"
    --output "$output_jsonl"
    --max-events "$max_events"
)
if [[ -n "$source_dataset" ]]; then
    extract_args+=(--source-dataset "$source_dataset")
fi
python3 "${extract_args[@]}"
