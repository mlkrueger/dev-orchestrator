# Run log schema

Each run owns a directory: `.dev-orchestrator/runs/<run-id>/` in the target repo, containing `meta.json` (run parameters) and `log.jsonl` — an **append-only** event log. `.dev-orchestrator/current-run` holds the active run dir path; the usage hook and helper scripts no-op when it is absent.

Every line is one JSON object with at least `ts` (UTC ISO-8601, e.g. `2026-07-05T14:03:22Z`) and `event`.

## Events written by orchestrators (via `scripts/log_event.sh`)

| `event` | Fields | When |
|---|---|---|
| `run_start` | `run`, `branch`, `milestones`, `tickets` | Run initialized, branch created |
| `milestone_start` | `milestone`, `tickets` | Before spawning a milestone-orchestrator |
| `dispatch` | `ticket`, `agent`, `model`, `attempt`, `tier` | Every subagent dispatch |
| `gate` | `ticket`, `gate` (`scope`\|`qa`\|`review`), `verdict` (verbatim from the gate agent — scope: `PASS`\|`PASS_WITH_NOTES`\|`FAIL`, qa: `PASS`\|`FAIL`, review: `APPROVE`\|`REQUEST_CHANGES`), `detail` | Every gate verdict. `report.py` normalizes: `PASS`/`PASS_WITH_NOTES`/`APPROVE` count as pass, `FAIL`/`REQUEST_CHANGES` as fail; anything else is surfaced as nonstandard. |
| `escalate` | `ticket`, `from`, `to`, `reason` | Tier escalation after 2 failed attempts |
| `commit` | `ticket`, `sha`, `files` | Per-ticket commit made |
| `ticket_done` | `ticket`, `attempts`, `final_tier` | Ticket passed all gates and committed |
| `ticket_blocked` | `ticket`, `reason` | Ticket abandoned after opus failed twice |
| `milestone_end` | `milestone`, `done`, `blocked` | Milestone-orchestrator finished |
| `run_end` | `run`, `done`, `blocked` | Run closed out |

Keep `detail`/`reason` to one line — the log is for analytics, not transcripts.

## Events written by the SubagentStop hook (`scripts/log_usage.py`)

| `event` | Fields |
|---|---|
| `agent_usage` | `ticket` (parsed from the `TICKET: <id>` line of the dispatch prompt, else `null`), `milestone` (parsed from a `MILESTONE: <name>` line, else `null` — attributes orchestrator cost, which was 55% of console-v1 spend when unattributed), `agent`, `model`, `input_tokens`, `output_tokens`, `cache_creation_tokens`, `cache_read_tokens`, `turns`, `duration_s` (wall-clock first→last transcript timestamp — includes paused/asleep time; `report.py` computes run-level active time separately), `source` (`sidechain`\|`main`), `session_id` |
| `usage_warning` | `reason`, `detail`, `payload_keys` (what the hook payload actually contained), `hint` — written instead of `agent_usage` when the hook could not do its job; see Troubleshooting below |

## Events written by the PreToolUse budget hook (`scripts/agent_budget.py`)

| `event` | Fields |
|---|---|
| `budget_exceeded` | `agent_id`, `agent`, `tool_calls` — written once when a fleet agent exhausts its per-agent tool-call budget (defaults in the script; override via `tool_call_budgets` in `.dev-orchestrator/config.json`). Further tool calls are denied with a wrap-up instruction; the agent can still return its report. Counters live in `<run_dir>/budgets/<agent_id>.count`. |

Token counts are summed per API call from the subagent transcript, which matches how usage is billed (each call bills its full input context). Cost is therefore computed at report time as:

```
cost = input×rate_in + cache_write×rate_in×1.25 + cache_read×rate_in×0.1 + output×rate_out
```

with rates from `config/pricing.json` (repo override: `.dev-orchestrator/pricing.json`).

## Troubleshooting usage accounting

The hook finds the subagent transcript via the SubagentStop payload, trying `agent_transcript_path`, then `agent_transcript`, then `transcript_path`. **This field's name has varied across Claude Code versions** — if Claude Code renames it again, usage events stop appearing and the log fills with `usage_warning` events instead (and `report.py` flags "dispatches logged but zero agent_usage events").

If that happens:

1. Check a warning event's `payload_keys` — it lists every field the hook actually received; the transcript path is usually recognizable by name.
2. Confirm by dumping a live payload: temporarily add a hook command like `jq . >> /tmp/subagent-stop-payload.json` alongside the logger in `hooks/hooks.json`, run any subagent, and inspect the file.
3. Add the new field name to the fallback chain in `scripts/log_usage.py` (the `transcript_path = payload.get(...)` block) and, ideally, upstream a fix to this plugin.

A `usage_warning` with reason "no usage entries parsed from transcript" means the path resolved but pointed at something without assistant-message usage — typically the *parent* session transcript rather than the subagent's; that is also a payload-shape issue, same fix.

## Correlation contract

The hook cannot see which ticket a subagent served — correlation relies on the dispatch prompt carrying `TICKET: <id>` on its own line (implementers and gate agents) or `MILESTONE: <name>` (milestone-orchestrators). This is no longer honor-system: the `dispatch_policy.py` PreToolUse hook **denies** fleet dispatches missing the line while a run is active. The same hook enforces model policy — an Opus implementer/code-reviewer dispatch requires a `TIER: complex` or `ESCALATED: <from-tier>` line, and Fable-class models are denied for all fleet agents. It fails open on internal errors and stays inert when no run is active (`.dev-orchestrator/current-run` absent).
