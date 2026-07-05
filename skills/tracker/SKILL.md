---
name: tracker
description: Tracker-neutral ticket operations for the dev-orchestrator fleet. Use whenever an orchestrator or fleet agent needs to list, read, create, or update tickets, milestones, statuses, labels, or comments — it defines the canonical ticket model and resolves the configured tracker adapter (Linear by default).
---

# Tracker — neutral ticket operations

All dev-orchestrator components speak this canonical model. An **adapter** maps it onto a concrete tracker's tools. Nothing outside the adapter file may reference tracker-specific tool names.

## Adapter resolution

1. Read `.dev-orchestrator/config.json` in the repo root; use its `"tracker"` value (e.g. `"linear"`).
2. No config → default to `linear`.
3. Load the adapter: read `adapters/<tracker>.md` **in this skill's directory** and follow its mappings for every operation below. If the adapter's MCP tools are not connected, stop and tell the user which tracker/server is missing.

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
