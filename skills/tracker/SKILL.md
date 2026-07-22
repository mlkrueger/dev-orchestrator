---
name: tracker
description: Tracker-neutral ticket operations for the dev-orchestrator fleet. Use whenever an orchestrator or fleet agent needs to list, read, create, or update tickets, milestones, statuses, labels, or comments — it defines the canonical ticket model and resolves the configured tracker adapter (Linear by default).
---

# Tracker — neutral ticket operations

All dev-orchestrator components speak this canonical model. Operations run **script-first**: a small CLI (`bin/tracker`) implements the canonical operations against the tracker's API, emitting compact canonical JSON and keeping bulky tracker payloads out of the orchestrator's context. An **adapter** documents the same mapping for the fallback path (model-driven MCP calls) when no API key is available. Nothing outside `bin/tracker` and the adapter file may reference tracker-specific tool names or API shapes.

## How to run an operation

1. Read `.dev-orchestrator/config.json` in the repo root; use its `"tracker"` value (e.g. `"linear"`, `"local"`; no config → default `linear`).
2. **Script path (preferred).** Call `bin/tracker` via Bash — it routes to the configured backend, so the command is identical for every tracker:
   ```
   python3 "${CLAUDE_PLUGIN_ROOT}/bin/tracker" <subcommand> ...
   ```
   It prints canonical JSON to stdout (one compact object/array), exits non-zero on error. This is the path to use in orchestrated and headless/cron runs — it needs no MCP session. See the subcommand list below.
   - `linear` uses the Linear GraphQL API when `LINEAR_API_KEY` is set.
   - `local` is file-backed (no key, no MCP, no network — see *Local tracker* below); it is always available.
3. **Adapter fallback.** If the configured tracker is script-backed but its prerequisite is missing (e.g. `linear` with no `LINEAR_API_KEY`), read `adapters/<tracker>.md` **in this skill's directory** and drive the operation through its MCP mappings. If the adapter's MCP tools are not connected either, stop and tell the user which tracker/server or key is missing. (`local` has no fallback and needs none.)

## `bin/tracker` subcommands (Linear)

```
tracker list --milestone <name> [--status s] [--label l] [--team K]   # light canonical rows
tracker get <id> [--comments]                                         # full ticket incl. dependencies
tracker create --title T --description-file F [--labels a,b] [--team K]
tracker update <id> [--title T] [--description-file F] [--labels a,b]
tracker set-status <id> <todo|in_progress|in_review|done|blocked>
tracker comment <id> --body-file <md>
tracker add-dependency <id> --blocked-by <id>
```

Auth: `LINEAR_API_KEY`. Team for `list`/`create`: `--team`, else `linear.team` in `.dev-orchestrator/config.json`. The script owns status mapping (by state *type*) and label conventions (`tier:`/`mod:`/`resource:`, create-if-missing) — callers don't re-derive them.

## Local tracker (no external tracker)

Set `{"tracker": "local"}` to use a **build plan file** as the ticket source — for users with no Linear/Jira/etc. installed, or who don't want one. `bin/tracker` reads and writes the plan directly (implemented in `bin/tracker_local.py`); ticket status is written back into it, so the plan doubles as the board and stays an accurate source of truth to resume from.

- **Plan location:** `.dev-orchestrator/build-plan.yaml` by default (`.yml`/`.json` also detected); override with `"local": {"plan": "<path>"}`. YAML needs PyYAML; `.json` plans need nothing.
- **Format & schema:** see [docs/local-tracker.md](../../docs/local-tracker.md). Each ticket carries the canonical fields directly (`id`, `title`, `status`, `tier`, `modules`, `resources`, `phase`, `depends_on`, `acceptance_criteria`/`description`), under a `milestones:` tree or a flat `tickets:` list.
- **Supported ops:** `list`, `get`, `set-status`, `comment` — the full autonomous run loop. `set-status` edits only the ticket's `status:` line, preserving the rest of a hand-authored plan. Comments go to a companion store (`.dev-orchestrator/comments/<id>.jsonl`) so status writes never reflow the plan.
- **Grooming:** `create`/`update`/`add-dependency` are **not** CLI operations for `local` — groom by editing the plan YAML directly (this is also how ticket-smith grooms a local backlog). The CLI returns a clear pointer if called.
- **Durability:** the default plan lives under gitignored `.dev-orchestrator/` — container-local, like the run log. To carry status across clones, point `local.plan` at a committed path (and commit status changes yourself).

## Canonical ticket model

| Field | Meaning | Canonical form |
|---|---|---|
| `id` | Stable ticket identifier | tracker-native (e.g. `ABC-123`) |
| `title` | Imperative summary | string |
| `description` | Self-contained context + constraints | markdown |
| `acceptance_criteria` | Testable, observable checks | list in description under an `## Acceptance criteria` heading |
| `status` | Lifecycle state | `todo`, `in_progress`, `in_review`, `done`, `blocked` |
| `dependencies` | Tickets that must complete first | tracker-native blocked-by relations |
| `tier_hint` | Model routing | label: `tier:simple` \| `tier:standard` \| `tier:complex` |
| `module_hints` | Code areas the ticket should touch | labels `mod:<area>` (preferred) or a `Modules:` line in the description |
| `resource_hints` | Shared mutable resources the ticket contends for | labels `resource:<name>` (e.g. `resource:db`); tickets sharing one are serialized unless a `resource_pools` capacity is configured |
| `milestone` | Grouping for orchestration | tracker-native milestone/cycle/epic |

Conventions: tier and module hints are **labels** so they are filterable; acceptance criteria live in the description so they travel with the ticket text verbatim.

## Operations

Adapters must map each of these:

- `list_milestones(project)` → milestones with id, name, ticket counts
- `list_tickets(filter)` → tickets by milestone / project / status / label
- `get_ticket(id)` → full canonical ticket **including dependencies and comments**
- `create_ticket(fields)` → new ticket (DRAFT mode of ticket-smith)
- `update_ticket(id, fields)` → title/description/labels/milestone changes
- `set_status(id, status)` → canonical status names; the adapter translates to the tracker's workflow states
- `add_comment(id, body)` → markdown comment
- `add_dependency(id, blocked_by_id)` → blocked-by relation

## Rules for callers

- Resolve canonical statuses through the adapter's status mapping — never guess a tracker's workflow state names.
- When reading a ticket for dispatch, always fetch dependencies; prose like "after ABC-4" is NOT a dependency — flag it for grooming.
- Comments posted by agents should be compact markdown: what was done, gate results, attempts, token usage. No transcripts.
- Batch reads where the adapter allows (list once, don't get_ticket in a loop when the list payload suffices).

## Writing a new adapter

Copy `adapters/linear.md` as a template to `adapters/<name>.md`, map every operation and both label conventions, define the status mapping table, and set `"tracker": "<name>"` in `.dev-orchestrator/config.json`. Adapters must be pure mappings — no policy, no workflow logic.
