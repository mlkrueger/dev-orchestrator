#!/bin/bash
# Append an orchestration event to the active run's log with a UTC timestamp.
# Usage: log_event.sh '<json object without "ts">'
# No active run (.dev-orchestrator/current-run missing) -> silent no-op.
set -euo pipefail

ptr=".dev-orchestrator/current-run"
[ -f "$ptr" ] || exit 0
run_dir=$(<"$ptr")
[ -d "$run_dir" ] || exit 0

ts=$(date -u +%Y-%m-%dT%H:%M:%SZ) \
python3 -c '
import json, os, sys
obj = json.loads(sys.argv[1])
obj["ts"] = os.environ["ts"]
print(json.dumps(obj))
' "$1" >> "$run_dir/log.jsonl"
