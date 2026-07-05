---
description: List or delete old dev-orchestrator run logs (.dev-orchestrator/runs/) - keep the last N, drop older-than-X, or remove specific runs
argument-hint: [--keep N | --older-than DAYS | --all | run-id ...]
---

Manage this repo's dev-orchestrator run directories (logs + token accounting under `.dev-orchestrator/runs/`).

Arguments: `$ARGUMENTS`

1. **If no arguments**, list what exists:

   ```
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/clean.py"
   ```

   Present the table verbatim. If it warns about a stale `current-run` pointer, explain the consequence (the usage hook keeps appending one-off agent usage to a dead run) and offer to clear it. Then suggest a cleanup, e.g. `--keep 10`, and stop — do not delete anything unasked.

2. **If arguments were given**, first preview with `--dry-run`:

   ```
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/clean.py" --dry-run $ARGUMENTS
   ```

   Show the user exactly what would be deleted and **confirm before proceeding**. On confirmation, re-run without `--dry-run` and report the result. Deletion is permanent — these logs are gitignored and exist nowhere else; a deleted run can no longer produce a `/dev-orchestrator:report`.

Selectors: `--keep N` (keep the newest N), `--older-than DAYS`, `--all`, or explicit run ids; they combine as a union. The active run (per `.dev-orchestrator/current-run`) is never deleted — if the user wants it gone, the run must not actually be in progress; verify that, then remove the pointer file first.
