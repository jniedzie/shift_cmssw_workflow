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

report_progress() {
	local label="$1"
	local processed="$2"
	local total="$3"
	local step="$4"
	if (( processed == total || processed % step == 0 )); then
		printf '  %s: %d/%d file(s) processed\n' "$label" "$processed" "$total"
	fi
}

# Do not guess when the schedd cannot be queried: old jobs may still be
# writing both their Condor logs on AFS and their cmsRun logs on EOS.
echo "Checking the Condor queue for active workflow jobs..."
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
processed_condor=0
echo "Scanning $CONDOR_LOG_DIR for old Condor logs..."
mapfile -d '' condor_logs < <(find "$CONDOR_LOG_DIR" -mindepth 1 -maxdepth 1 -type f -print0)
condor_total=${#condor_logs[@]}
condor_progress_step=$(( (condor_total + 19) / 20 ))
(( condor_progress_step > 0 )) || condor_progress_step=1
for log_file in "${condor_logs[@]}"; do
	((processed_condor += 1))
	log_name="${log_file##*/}"
	if [[ ! "$log_name" =~ ^condor_([0-9]+)([.][0-9]+)?[.](out|err|log)$ ]]; then
		report_progress "Condor logs" "$processed_condor" "$condor_total" "$condor_progress_step"
		continue
	fi
	if [[ -n "${active_clusters[${BASH_REMATCH[1]}]:-}" ]]; then
		((preserved_condor += 1))
		report_progress "Condor logs" "$processed_condor" "$condor_total" "$condor_progress_step"
		continue
	fi
	rm -f -- "$log_file"
	((removed_condor += 1))
	report_progress "Condor logs" "$processed_condor" "$condor_total" "$condor_progress_step"
done
(( condor_total > 0 )) || echo "  Condor logs: no files to process"

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
processed_payload=0
echo "Scanning $PAYLOAD_LOG_DIR for old payload logs..."
mapfile -d '' payload_logs < <(find "$PAYLOAD_LOG_DIR" -mindepth 1 -maxdepth 1 -type f -name '*.log' -print0)
payload_total=${#payload_logs[@]}
payload_progress_step=$(( (payload_total + 19) / 20 ))
(( payload_progress_step > 0 )) || payload_progress_step=1
for log_file in "${payload_logs[@]}"; do
	((processed_payload += 1))
	rm -f -- "$log_file"
	((removed_payload += 1))
	report_progress "Payload logs" "$processed_payload" "$payload_total" "$payload_progress_step"
done
(( payload_total > 0 )) || echo "  Payload logs: no files to process"
printf 'Removed %d old payload log file(s) from %s\n' "$removed_payload" "$PAYLOAD_LOG_DIR"
