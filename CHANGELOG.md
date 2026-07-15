# Changelog

All notable changes to the dev-orchestrator plugin. Format follows
[Keep a Changelog](https://keepachangelog.com/); versions follow semver
(`.claude-plugin/plugin.json` is the version of record).

Each version's section is what gets surfaced: in-session by the update-notify
hook the first time Claude starts after an update, and on GitHub as the
release body for that version's tag.

## [Unreleased]

## [0.4.0] — 2026-07-15

### Added
- **Commit-gate preflight** (`scripts/ensure_env.py`, wired into `/orchestrate` Phase 1): ensures the target repo runs its local CI checks before every commit by installing a shared `pre-commit` hook via `core.hooksPath=.githooks` (committable, so the whole team gets it — not a Claude-session-only plugin hook). Idempotent and ledger-backed: records what it set up in `.dev-orchestrator/environment.json` and fast-paths on later runs, verifying by sha so a stale or hand-edited hook is detected and re-offered. `--check` probes without writing; the checks command is auto-detected (npm `ci`/`check`/composed scripts, or pytest) and overridable via `--checks-command` or the ledger. Never installs without consent.
- **Script-first tracker CLI** (`bin/tracker`, MKR-439): a stdlib-only (no `requests`) Linear GraphQL client exposing the canonical tracker operations as subcommands — `list`, `get`, `create`, `update`, `set-status`, `comment`, `add-dependency` — that emit compact canonical JSON. Replaces model-mediated MCP ticket I/O on the per-ticket hot path (fewer tokens, no MCP schemas loaded) and works in headless/cron runs with no authenticated MCP session. Resolves canonical status by workflow-state *type* (never hardcoded state names), with a `blocked` label+comment fallback for teams that have no Blocked state, and reuses workspace-level labels on a name collision (the publish-linear v0.5.0 bug, not reintroduced). The MCP adapter stays as a documented fallback when `LINEAR_API_KEY` is unset.
- **Combined `simple-gate`** (`agents/simple-gate.md`, MKR-442): for `tier:simple` tickets, one Sonnet gate carrying both the qa-verifier and code-reviewer rubrics replaces two separate gate dispatches; a single verdict, counted as one gate on the retry ladder. Escalation past the simple tier restores the full scope→QA→review chain.
- Test suite (`tests/`, `pytest.ini`): 77 subprocess-driven tests covering every runtime script and hook (`validate_plan`, `clean`, `dispatch_policy`, `agent_budget`, `log_usage`, `report`, `ensure_env`, `check_changelog`, `notify_update`) plus the `bin/tracker` CLI (against a mock GraphQL server). Run with `uvx --with pytest pytest`.

### Changed
- **Dispatch by file path, not payload** (MKR-440): the milestone-orchestrator materializes each ticket to `<run-dir>/tickets/<id>.md` once and dispatches `TICKET_FILE:` paths instead of inlining ticket bodies on every dispatch and retry; gates write full findings to `<run-dir>/gates/…` and return only a verdict + ≤3-line summary. Orchestrator context growth drops from O(tickets × artifacts) to O(tickets). The dispatch-policy hook now also denies fleet ticket dispatches missing a `TICKET_FILE:` line pointing into the run dir.
- **Evidence-bearing implementer reports** (MKR-441): the implementer writes a structured report (real diffstat, per-criterion evidence, command outputs, touched-files rationale) to `<run-dir>/reports/…`; scope-guardian, qa-verifier, and code-reviewer verify against it and open source files only on mismatch, instead of each re-exploring from scratch. qa-verifier still runs everything itself and fails a "report/reality mismatch" outright.
- **Close-out discipline** (MKR-443): the milestone-orchestrator reduces each completed ticket to a one-line record and sources the end-of-milestone summary from the run log, instead of carrying finished tickets' dispatch/gate/retry traffic forward and re-sending it every turn.

### Fixed
- `clean.py` now normalizes the `current-run` pointer with `abspath` before matching it against run directories, so an absolute pointer (already accepted by the budget/dispatch/usage scripts) is handled consistently. Previously an absolute pointer was mis-read as a stale pointer, and — because the active-run match returned nothing — the guard protecting the active run was bypassed, so `--all`/`--keep 0` could delete the in-progress run.

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
