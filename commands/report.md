---
description: Postmortem analytics for a dev-orchestrator run - token cost by model/agent/ticket, retries, escalations, gate failures, runtime
argument-hint: [run-id (defaults to current/latest run)]
---

Generate the postmortem report for a dev-orchestrator run.

Arguments: `$ARGUMENTS`

1. Run the aggregator:

   ```
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/report.py" $ARGUMENTS
   ```

   With no argument it resolves `.dev-orchestrator/current-run`, falling back to the most recent directory under `.dev-orchestrator/runs/`. Pass a run id or run dir path to target an older run.

2. Present the script's markdown output verbatim (totals by model, by agent, by ticket; cost estimate; retries/escalations/gate failures; wall time).

3. Add a short qualitative postmortem by reading ONLY the non-usage events from the run's `log.jsonl` (`grep -v '"agent_usage"' <run_dir>/log.jsonl`):
   - Which tickets burned retries or escalated, and what the gate `detail` lines say went wrong.
   - Whether failures cluster (one flaky module? criteria that were untestable? sprawl from one ticket shape?).
   - 2–3 concrete recommendations: tickets that should have been tiered differently, grooming gaps, tickets that should be split next run.

Keep the qualitative section under ~15 lines. If the log or run directory is missing, say what was searched and stop — do not reconstruct numbers from memory.

Cost pricing comes from `config/pricing.json` in the plugin, overridable per-repo at `.dev-orchestrator/pricing.json` (same shape). Models without a price entry are reported with token counts and "unknown" cost — mention the override path if that happens.
