#!/usr/bin/env bash
set -euo pipefail
# Preserve the submission interface; cleanup protects EOS checkpoint provenance.
if [[ $# -ne 3 ]]; then
    echo "Usage: $(basename "$0") CONDOR_LOG_DIR PAYLOAD_LOG_DIR WORKFLOW_EXECUTABLE" >&2
    exit 2
fi
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SCRIPT_DIR/cleanup_condor_logs.py" "$@"
