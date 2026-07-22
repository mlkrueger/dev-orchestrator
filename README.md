# dev-orchestrator

Autonomous, ticket-driven development for Claude Code. Point it at a groomed backlog and it plans the run, spawns one **milestone orchestrator** per milestone (keeping every orchestration context short), routes each ticket to the **cheapest capable model**, gates every change through **scope → QA → review**, escalates on failure, commits per ticket on a dedicated build branch, and keeps an **append-only run log** with exact token accounting for postmortems.

```
you ──/orchestrate──▶ top-level session (plan, confirm, dispatch, decide)
                          │  one subagent per milestone — fresh context each time
                          ▼
                 milestone-orchestrator (sonnet)
                          │  per ticket, cheapest capable tier
                          ▼
        implementer (haiku│sonnet│opus)  ──▶ scope-guardian ──▶ qa-verifier ──▶ code-reviewer ──▶ commit
                          ▲                        │ FAIL: back with violations (2 strikes → escalate tier)
                          └────────────────────────┘
```

## Design goals

- **Better outcomes** — independent gates (scope, empirical QA, code review) instead of trusting an implementer's "done".
- **Lower token cost** — tickets route to Haiku/Sonnet by tier hint; escalation is earned, never default. Opus implementation is a rare exception; Fable-class models are never used without explicit user approval.
- **Short, clean contexts** — one orchestrator per milestone; the top session keeps only summaries; agents report verdicts, not transcripts. This also limits the cost of cache eviction on long runs.
- **Safe parallelism** — dispatch respects ticket dependencies AND module-hint overlap; commits are per-ticket file lists, never `git add -A`.
- **Portable & shareable** — everything (agents, skills, hooks, scripts) ships in this plugin; ticket operations are tracker-neutral with a Linear adapter included.

## Install

```bash
# in Claude Code
/plugin marketplace add <your-github-user>/dev-orchestrator
/plugin install dev-orchestrator
```

Requires: a git repo, `python3`, and a connected tracker MCP server (Linear by default).

## Usage

### In-app help

```
/dev-orchestrator:help                          # usage overview
/dev-orchestrator:help how do tier labels work  # ask anything
```

### An orchestrated run

```
/dev-orchestrator:orchestrate Payments v2
```

1. Preflight (clean tree, tracker reachable) and readiness scan; offers ticket-smith grooming if tickets lack criteria/tier hints.
2. Presents the run plan (milestone order, branch, policies) and **waits for your approval**.
3. Executes milestones sequentially, each in a fresh milestone-orchestrator. Pauses to ask you on `needs-decision` items or repeated blocks.
4. Closes out with the run report. The build branch stays local — you review and push.

Flags: `--branch <name>`, `--dry-run` (plan only).

### Postmortem

```
/dev-orchestrator:report            # current/latest run
/dev-orchestrator:report 20260705-0930-payments
```

Token usage and cost by model / agent / ticket, retries, escalations, gate failures, wall time — plus a short qualitative read of what burned attempts and why.

### Cleaning up old runs

```
/dev-orchestrator:clean                 # list runs with size and date
/dev-orchestrator:clean --keep 10       # keep the newest 10, delete the rest
/dev-orchestrator:clean --older-than 30 # delete runs older than 30 days
```

Also accepts `--all` or explicit run ids; selectors combine as a union, and deletion is previewed and confirmed first. The active run (per `.dev-orchestrator/current-run`) is never deleted, and a stale `current-run` pointer left by a killed run — which would silently route one-off agent usage into a dead log — is detected and cleared.

### The fleet, one-off

Every agent works standalone — just ask:

| Agent | Persona | One-off use |
|---|---|---|
| `implementer` | Disciplined senior engineer; the ticket is the contract | "Have the implementer add X, nothing else" |
| `scope-guardian` | Governance gate; rejects sprawl | "Did my changes stay within <intent>?" |
| `qa-verifier` | Skeptical empiricist; runs everything, fixes nothing | "Verify these criteria against the running code" |
| `code-reviewer` | Pre-commit defect hunter; no style nits | "Review my working tree" |
| `ticket-smith` | Backlog craftsman; drafts & grooms to run-readiness | "Groom the Payments milestone" / "Draft tickets for X" |
| `milestone-orchestrator` | Delivery lead for one batch of tickets | "Work through these 3 tickets on this branch" |

## Ticket conventions

Groomed tickets carry (ticket-smith enforces all of these):

- `## Acceptance criteria` — testable, observable checks in the description.
- `tier:simple|standard|complex` label → routes to Haiku / Sonnet / Opus. Most tickets are `standard`; `complex` should be rare (~1 in 10) or your tickets are too big.
- `mod:<area>` labels — the modules the ticket may touch. These drive parallel-dispatch safety **and** scope-guardian audits.
- `resource:<name>` labels — shared mutable resources the ticket needs (e.g. `resource:db` when tests reset a shared database). Tickets sharing a resource label are serialized by default; declaring `"resource_pools": {"<name>": <capacity>}` in `.dev-orchestrator/config.json` lets up to N run concurrently, each dispatched with a distinct `RESOURCE_SLOT` for port/state isolation (only for harnesses that support it).
- Tracker-native blocked-by relations for dependencies (prose dependencies are ignored and flagged).

## Escalation & governance

- 2 failed gate attempts at a tier → escalate one tier (haiku→sonnet→opus). 2 failures at opus → ticket marked blocked with full history; the run moves on.
- The **scope-guardian** rejects any diff not attributable to the ticket. Sensitive areas (auth, schema/migrations, CI, deps, security config, payments) fail closed: touching them without explicit ticket sanction is an automatic bounce.
- Orchestrators cap at Opus. Nothing in this plugin ever dispatches a Fable-class model unless you explicitly approve it in-session.
- Milestone-orchestrators default to Sonnet (coordination is mechanical; the gates carry the judgment). Override with `{"orchestrator_model": "opus"}` in `.dev-orchestrator/config.json` for milestones dominated by `complex`-tier tickets.

## Run log & token accounting

Everything lands in `.dev-orchestrator/runs/<run-id>/log.jsonl` (gitignored):

- Orchestrators append lifecycle events (`dispatch`, `gate`, `escalate`, `commit`, `ticket_done`, …) via `scripts/log_event.sh`.
- A `SubagentStop` hook (`scripts/log_usage.py`) reads each finished subagent's transcript and appends **exact** token usage — input/output/cache split, model, turns, duration — correlated to its ticket via the `TICKET: <id>` first line of every dispatch prompt. Zero tokens spent on accounting; no self-reported estimates.
- `scripts/report.py` aggregates the log; pricing lives in `config/pricing.json` (override per-repo at `.dev-orchestrator/pricing.json`).

Schema: [docs/log-schema.md](docs/log-schema.md). The hook no-ops unless `.dev-orchestrator/current-run` exists, so one-off agent use stays noise-free.

Logs live only in the repo (gitignored) and are never pruned automatically — a run stays reportable until you delete it. Inspect with `jq`/`grep` or `/dev-orchestrator:report`; prune with `/dev-orchestrator:clean` (or plain `rm -rf .dev-orchestrator/runs/<run-id>` — same effect).

## Resumable state — the tracker is the source of truth

A run can be interrupted at any point — a reclaimed container, a killed session, a lost run dir. Because the run log (`log.jsonl`) is machine-local and gitignored, it can't be relied on to survive that; the **tracker** can. So every ticket is marked `in_progress` when work starts and `done`/`blocked` when it finishes, and those marks are the durable state a resumed run reads back.

On startup — fresh, respawned, or resumed after an interruption — each milestone-orchestrator reconciles both records: `scripts/remaining_work.py` folds the tracker's live statuses (`--tracker-status-file`) into the local log and treats a ticket as done if *either* says so. A run resumed with no local log still skips everything the board shows complete, and a tracker write that never landed still can't cause a committed ticket to be redone. Its `resync` output flags marks the log recorded but the tracker missed, and the orchestrator re-issues them — keeping the board accurate for the next interruption. Re-running `/orchestrate` on an interrupted run offers to resume it in place.

## Slack progress reporting

Optional, report-only. Point a webhook or bot token at a channel and a run's lifecycle — start/end, milestone boundaries, a progress line every few tickets, and always blocked tickets and escalations — mirrors to Slack, so you can follow an unattended run from your phone. Bot-token setups thread a whole run under one message. It fails open (a Slack outage never stalls a run) and no-ops entirely when unconfigured; the orchestrator never reads replies, so decisions still come back through the Claude session. Setup and verbosity levels: [docs/slack.md](docs/slack.md).

```json
// .dev-orchestrator/config.json — secrets stay in env (SLACK_WEBHOOK_URL or SLACK_BOT_TOKEN)
{ "slack": { "notify": "milestones", "progress_every": 5, "channel": "#dev-runs" } }
```

## Tracker adapters

Agents speak a canonical ticket model (`skills/tracker/SKILL.md`); adapters map it to real tools. `linear` ships in-box. To add Jira/GitHub/etc.: copy `skills/tracker/adapters/linear.md`, map the operations and status table to your tracker's MCP tools, and set `{"tracker": "<name>"}` in `.dev-orchestrator/config.json`. Adapters are pure mappings — no policy.

## Unattended runs & permissions

The run is interactive-by-design at decision points, but the 8-hour middle should never stall on a permission prompt. Before a long run, curate the target repo's `.claude/settings.json` allowlist (edits, test/build commands, git, your tracker's MCP tools) — `/fewer-permission-prompts` builds one from your transcript history. Prompts that do fire surface in your session from any agent depth.

## Versioning & changelog

`CHANGELOG.md` (Keep a Changelog format) is the source of truth for what each version changed and why; `.claude-plugin/plugin.json` is the version of record. Claude Code has no native changelog display for plugin updates, so this plugin surfaces its own: a `SessionStart` hook compares the installed version against the last one it announced (`~/.claude/dev-orchestrator.last-announced-version`) and, on the first session after an update, has Claude summarize the new versions' changelog sections. Unchanged version → silent; first install → silent.

Releasing: move `[Unreleased]` content into a new `## [<version>]` section, bump `plugin.json`, then run `python3 scripts/check_changelog.py` — it exits non-zero if the version lacks a changelog section or `[Unreleased]` still has content, and on success prints the section for use as the GitHub release body:

```
gh release create v<version> --notes "$(python3 scripts/check_changelog.py)"
```

## Layout

```
.claude-plugin/{plugin.json, marketplace.json}
agents/        milestone-orchestrator, implementer, scope-guardian,
               qa-verifier, code-reviewer, ticket-smith
commands/      orchestrate.md, report.md, clean.md, help.md
skills/tracker/{SKILL.md, adapters/linear.md}
hooks/hooks.json          SubagentStop → usage logging
                          PreToolUse → dispatch policy + per-agent budgets/deadlines
                          SessionStart → post-update changelog notice
scripts/       log_usage.py, log_event.sh, report.py, clean.py,
               dispatch_policy.py, agent_budget.py, validate_plan.py,
               remaining_work.py, slack_notify.py, ensure_env.py,
               notify_update.py, check_changelog.py
config/pricing.json       editable $/MTok table
docs/log-schema.md, docs/slack.md
CHANGELOG.md              source of truth for release notes
```

## License

MIT
