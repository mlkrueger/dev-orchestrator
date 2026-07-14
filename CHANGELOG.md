# Changelog

All notable changes to the dev-orchestrator plugin. Format follows
[Keep a Changelog](https://keepachangelog.com/); versions follow semver
(`.claude-plugin/plugin.json` is the version of record).

Each version's section is what gets surfaced: in-session by the update-notify
hook the first time Claude starts after an update, and on GitHub as the
release body for that version's tag.

## [Unreleased]

## [0.3.0] — 2026-07-14

### Added
- **Dispatch-policy hook** (`scripts/dispatch_policy.py`, PreToolUse on Agent): while a run is active, denies fleet dispatches missing the `TICKET:`/`MILESTONE:` correlation line, denies Opus implementer/code-reviewer dispatches without a `TIER: complex` or `ESCALATED: <from-tier>` justification, and denies Fable-class models outright. Console-v1 ran 34% of implementer work on Opus against a ~10% design bar; routing policy is now mechanical, not prompt-hoped.
- **Per-agent tool-call budgets** (`scripts/agent_budget.py`, PreToolUse on all tools): counts tool calls per subagent (`agent_id`) and denies past budget with a wrap-up instruction — soft landing, the agent still returns its report. Stops the runaway-implementer failure mode (console-v1's worst ticket burned 19% of the whole run's tokens on two 350+-turn implementers). Defaults per agent type; override via `tool_call_budgets` in `.dev-orchestrator/config.json`. Logs `budget_exceeded`.
- **Plan-time backlog validator** (`scripts/validate_plan.py`), wired as a hard gate in `/orchestrate` Phase 4: refuses to initialize a run when >15% of tickets are `tier:complex` (configurable `complex_max_share`), when a dependency points outside the run's ticket set (phantom-prerequisite mode that blocked MKR-278), or when tickets lack tier/criteria.
- **Milestone cost attribution**: `log_usage.py` parses a `MILESTONE: <name>` brief line so orchestrator usage — 55% of console-v1 spend, previously `(unknown)` — attributes to its milestone in reports.
- **Changelog surfacing**: this file, plus a `SessionStart` hook (`scripts/notify_update.py`) that announces what changed the first session after a plugin update, and `scripts/check_changelog.py` as a release gate that also emits the GitHub release body.

### Changed
- `report.py` normalizes gate-verdict vocabulary (`APPROVE`/`REQUEST_CHANGES`/`PASS_WITH_NOTES`); review rejections were undercounted ~45% before. Gate lines now show reject rates; nonstandard verdicts are surfaced instead of dropped.
- `report.py` reports **active time** vs wall time (gaps >30 min counted as idle). Console-v1's "2-day run" was ~10.4h active; per-agent `duration_s` remains wall-clock and absorbs machine sleep.
- Milestone-orchestrator: too-big tickets are bounced back as `needs-grooming` — decomposition is grooming-time only (ticket-smith/spec-kit); orchestrators never invent subtasks mid-run.
- Legal gate-verdict vocabulary per gate is now specified in the log schema and orchestrator prompt (no more `PENDING`).

## [0.2.0] — 2026-07-08

### Changed
- Milestone-orchestrator defaults to **Sonnet** (was Opus) — coordination is mechanical; the gates carry the judgment. Override with `orchestrator_model` in `.dev-orchestrator/config.json`. In the console-v1 field run, Opus orchestration was 51% of total cost.
- **Synchronous child dispatch is an iron rule**: no backgrounding, no Monitor/await, no message-resume; fresh agent per rework round. Ending a turn to wait replayed the orchestrator transcript on every resume.
- Gate tuning: scope-guardian skipped for `tier:simple`, Sonnet when sibling tickets are in flight; qa-verifier at Haiku for `tier:simple`; implementer file lists passed to all gates.
- Dispatch briefs end with a "Begin now" imperative; agent frontmatter trimmed to one example each.

### Added
- `resource:<name>` exclusive-lock labels to auto-serialize tickets contending for shared mutable resources (e.g. a shared test database).
- Implementer first-pass pitfall checklist (auth fail-closed, key scoping, injection, error paths, degenerate inputs) to cut rework.

## [0.1.0] — 2026-07-05

### Added
- Initial release: autonomous ticket-driven development for Claude Code. `/orchestrate` plans milestones and spawns one milestone-orchestrator per milestone; fleet personas (implementer, scope-guardian, qa-verifier, code-reviewer, ticket-smith); tiered model routing (`tier:simple|standard|complex` → Haiku/Sonnet/Opus) with a 2-attempts-then-escalate ladder capped at Opus.
- Tracker-neutral ticket skill with Linear adapter.
- Token accounting via SubagentStop hook reading actual transcript usage; `/report` postmortem analytics; `/clean` run-log lifecycle; `/help`.
