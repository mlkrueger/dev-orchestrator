# dev-orchestrator

Autonomous, ticket-driven development for Claude Code. Point it at a groomed backlog and it plans the run, spawns one **milestone orchestrator** per milestone (keeping every orchestration context short), routes each ticket to the **cheapest capable model**, gates every change through **scope → QA → review**, escalates on failure, commits per ticket on a dedicated build branch, and keeps an **append-only run log** with exact token accounting for postmortems.

```
you ──/orchestrate──▶ top-level session (plan, confirm, dispatch, decide)
                          │  one subagent per milestone — fresh context each time
                          ▼
                 milestone-orchestrator (opus)
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
- Tracker-native blocked-by relations for dependencies (prose dependencies are ignored and flagged).

## Escalation & governance

- 2 failed gate attempts at a tier → escalate one tier (haiku→sonnet→opus). 2 failures at opus → ticket marked blocked with full history; the run moves on.
- The **scope-guardian** rejects any diff not attributable to the ticket. Sensitive areas (auth, schema/migrations, CI, deps, security config, payments) fail closed: touching them without explicit ticket sanction is an automatic bounce.
- Orchestrators cap at Opus. Nothing in this plugin ever dispatches a Fable-class model unless you explicitly approve it in-session.

## Run log & token accounting

Everything lands in `.dev-orchestrator/runs/<run-id>/log.jsonl` (gitignored):

- Orchestrators append lifecycle events (`dispatch`, `gate`, `escalate`, `commit`, `ticket_done`, …) via `scripts/log_event.sh`.
- A `SubagentStop` hook (`scripts/log_usage.py`) reads each finished subagent's transcript and appends **exact** token usage — input/output/cache split, model, turns, duration — correlated to its ticket via the `TICKET: <id>` first line of every dispatch prompt. Zero tokens spent on accounting; no self-reported estimates.
- `scripts/report.py` aggregates the log; pricing lives in `config/pricing.json` (override per-repo at `.dev-orchestrator/pricing.json`).

Schema: [docs/log-schema.md](docs/log-schema.md). The hook no-ops unless `.dev-orchestrator/current-run` exists, so one-off agent use stays noise-free.

## Tracker adapters

Agents speak a canonical ticket model (`skills/tracker/SKILL.md`); adapters map it to real tools. `linear` ships in-box. To add Jira/GitHub/etc.: copy `skills/tracker/adapters/linear.md`, map the operations and status table to your tracker's MCP tools, and set `{"tracker": "<name>"}` in `.dev-orchestrator/config.json`. Adapters are pure mappings — no policy.

## Unattended runs & permissions

The run is interactive-by-design at decision points, but the 8-hour middle should never stall on a permission prompt. Before a long run, curate the target repo's `.claude/settings.json` allowlist (edits, test/build commands, git, your tracker's MCP tools) — `/fewer-permission-prompts` builds one from your transcript history. Prompts that do fire surface in your session from any agent depth.

## Layout

```
.claude-plugin/{plugin.json, marketplace.json}
agents/        milestone-orchestrator, implementer, scope-guardian,
               qa-verifier, code-reviewer, ticket-smith
commands/      orchestrate.md, report.md, help.md
skills/tracker/{SKILL.md, adapters/linear.md}
hooks/hooks.json          SubagentStop → usage logging
scripts/       log_usage.py, log_event.sh, report.py
config/pricing.json       editable $/MTok table
docs/log-schema.md
```

## License

MIT
