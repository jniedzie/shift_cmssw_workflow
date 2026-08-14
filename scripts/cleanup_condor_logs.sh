#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 3 ]]; then
	echo "Usage: $(basename "$0") CONDOR_LOG_DIR PAYLOAD_LOG_DIR WORKFLOW_EXECUTABLE" >&2
	exit 2
fi

CONDOR_LOG_DIR="$1"
PAYLOAD_LOG_DIR="$2"
WORKFLOW_EXECUTABLE="$3"
CONDOR_Q_BIN="${CONDOR_Q_BIN:-condor_q}"

for log_dir in "$CONDOR_LOG_DIR" "$PAYLOAD_LOG_DIR"; do
	if [[ -z "$log_dir" || "$log_dir" == / ]]; then
		echo "ERROR: refusing to clean unsafe log directory '$log_dir'" >&2
		exit 1
	fi
done

mkdir -p "$CONDOR_LOG_DIR" "$PAYLOAD_LOG_DIR"

# Do not guess when the schedd cannot be queried: old jobs may still be
# writing both their Condor logs on AFS and their cmsRun logs on EOS.
if ! queue_output="$($CONDOR_Q_BIN -constraint "Cmd == \"$WORKFLOW_EXECUTABLE\"" -autoformat ClusterId 2>/dev/null)"; then
	echo "WARNING: could not query active workflow jobs; old logs were not removed" >&2
	exit 0
fi

declare -A active_clusters=()
while IFS= read -r cluster_id; do
	[[ "$cluster_id" =~ ^[0-9]+$ ]] || continue
	active_clusters["$cluster_id"]=1
done <<< "$queue_output"

removed_condor=0
preserved_condor=0
while IFS= read -r -d '' log_file; do
	log_name="${log_file##*/}"
	if [[ ! "$log_name" =~ ^condor_([0-9]+)([.][0-9]+)?[.](out|err|log)$ ]]; then
		continue
	fi
	if [[ -n "${active_clusters[${BASH_REMATCH[1]}]:-}" ]]; then
		((preserved_condor += 1))
		continue
	fi
	rm -f -- "$log_file"
	((removed_condor += 1))
done < <(find "$CONDOR_LOG_DIR" -mindepth 1 -maxdepth 1 -type f -print0)

printf 'Removed %d old Condor log file(s) from %s' "$removed_condor" "$CONDOR_LOG_DIR"
if (( preserved_condor > 0 )); then
	printf '; preserved %d file(s) for active clusters' "$preserved_condor"
fi
printf '\n'

if (( ${#active_clusters[@]} > 0 )); then
	printf 'Preserving payload logs in %s while %d older workflow cluster(s) remain active\n' \
		"$PAYLOAD_LOG_DIR" "${#active_clusters[@]}"
	exit 0
fi

removed_payload=0
while IFS= read -r -d '' log_file; do
	rm -f -- "$log_file"
	((removed_payload += 1))
done < <(find "$PAYLOAD_LOG_DIR" -mindepth 1 -maxdepth 1 -type f -name '*.log' -print0)
printf 'Removed %d old payload log file(s) from %s\n' "$removed_payload" "$PAYLOAD_LOG_DIR"
