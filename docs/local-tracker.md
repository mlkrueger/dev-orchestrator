# Local tracker — a build plan file as your ticket source

Don't have an external tracker (Linear, Jira, …), or don't want one? Set the
tracker to `local` and dev-orchestrator uses a **build plan file** in your repo
as the ticket source. Ticket status (`in_progress`, `done`, …) is written back
into that file as work proceeds, so the plan doubles as the board and stays an
accurate source of truth to resume from.

Everything else — the orchestrator, the gates, token accounting, the
tracker-as-source-of-truth resume reconciliation — works identically; only the
backend behind `bin/tracker` changes.

## Enable it

```json
// .dev-orchestrator/config.json
{ "tracker": "local" }
```

By default the plan is `.dev-orchestrator/build-plan.yaml` (a `.yml` or `.json`
sibling is also auto-detected). Point it anywhere with:

```json
{ "tracker": "local", "local": { "plan": "dev-plan.yaml" } }
```

YAML plans need PyYAML (`pip install pyyaml`); `.json` plans need nothing.

## Plan format

Two shapes, both normalize the same. Nested milestones read most naturally:

```yaml
team: PAY                       # optional, cosmetic
milestones:
  - name: Payments v2
    tickets:
      - id: PAY-1
        title: Add the payment model
        status: todo            # todo | in_progress | in_review | done | blocked (default todo)
        tier: standard          # simple | standard | complex
        modules: [api, db]      # code areas the ticket may touch (parallel-safety + scope)
        resources: [db]         # shared mutable resources it contends for
        phase: 1                # optional ordering within the milestone
        depends_on: [PAY-0]     # blocked-by relations (also accepts `blocked_by:`)
        labels: [Improvement]   # any extra free-form labels
        description: |
          Create the model and migration.
          ## Acceptance criteria
          - persists a payment
          - rejects a negative amount
```

Or a flat list, each ticket naming its milestone:

```yaml
tickets:
  - id: PAY-1
    milestone: Payments v2
    title: Add the payment model
    tier: standard
    status: todo
    acceptance_criteria:        # explicit list, instead of a description heading
      - persists a payment
```

Field notes:

- **`status`** defaults to `todo` when omitted. The orchestrator sets it to
  `in_progress` when a ticket enters the pipeline and `done`/`blocked` at
  close-out — surgically, so only the `status:` line changes.
- **`acceptance_criteria`** may be an explicit list, or parsed from a
  `## Acceptance criteria` heading in `description` (same as every other
  backend). Both paths satisfy the QA gate.
- **`phase`** is surfaced to the orchestrator as a `phase:K` label, so
  phase-aware dispatch works exactly as it does on Linear.
- **`tier` / `modules` / `resources`** drive routing, parallel-dispatch safety,
  and resource locks — the same conventions as the `tier:` / `mod:` /
  `resource:` labels elsewhere.

## Operations

`bin/tracker` supports the full autonomous run loop against a local plan:

| Subcommand | Local behavior |
|---|---|
| `list --milestone <name> [--status s] [--label l]` | Canonical light rows. `--label` matches `tier:`/`mod:`/`resource:`/`phase:` pseudo-labels and free-form `labels`. |
| `get <id> [--comments]` | Full canonical ticket. |
| `set-status <id> <status>` | Edits the ticket's `status:` line **in place**, preserving comments and formatting (reserialize fallback only for irregular files / JSON plans). |
| `comment <id> --body-file <md>` | Appends to a companion store `.dev-orchestrator/comments/<id>.jsonl`; `get --comments` reads it back. Keeps status writes from reflowing your plan. |

**Grooming** (`create`, `update`, `add-dependency`) is done by **editing the
plan file directly** — add or edit tickets, milestones, and `depends_on`
relations in your editor. These are not CLI operations for the local backend
(they'd have to reflow the file); calling them returns a pointer to this doc.
ticket-smith, asked to groom a local backlog, edits the plan the same way.

## Durability & resume

The plan is your durable state — the same role the tracker plays for external
backends. On resume, `remaining_work.py` reconciles the local run log with the
plan's statuses, so an interrupted run picks up exactly what's left and never
redoes a committed ticket.

Two things to know about where it lives:

- **Default (`.dev-orchestrator/build-plan.yaml`)** is gitignored, like the run
  log — container-local. It survives orchestrator respawns and session restarts
  within the same environment, but not a fresh clone or a reclaimed container.
- **To carry status across clones**, point `local.plan` at a **committed** path
  (e.g. `dev-plan.yaml` at the repo root) and commit status changes yourself.
  Note that during a run the plan will then show as a modified tracked file; the
  orchestrator commits only each ticket's own files, so the plan's status
  updates are yours to commit (and to keep out of scope-guardian's way — add it
  to your commits deliberately, not via `git add -A`).

## Requirements

- `python3` (always).
- PyYAML for YAML plans (`pip install pyyaml`); omit it and use a `.json` plan
  for a zero-dependency setup.
- No API key, no MCP server, no network.
