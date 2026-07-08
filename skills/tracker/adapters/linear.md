# Linear adapter

Maps canonical tracker operations to the Linear MCP server (`mcp__plugin_linear_linear__*` tools, from the `linear` plugin; tool prefix may vary by how the server is connected — match on the `_linear__` tool suffixes below). If these tools are deferred, load them via ToolSearch first, batching every tool you expect to need into one `select:` query.

## Operation mapping

| Canonical | Linear tool(s) | Notes |
|---|---|---|
| `list_milestones(project)` | `list_milestones` (needs `projectId` from `list_projects`) | Linear milestones belong to projects. If the team plans with cycles instead, use `list_cycles`. |
| `list_tickets(filter)` | `list_issues` | Filter by `projectId`, `milestoneId`, state, labels, team. Prefer one filtered list over per-ticket gets. |
| `get_ticket(id)` | `get_issue` | Returns relations — treat `blockedBy` issues as dependencies. Fetch comments with `list_comments` only when needed. |
| `create_ticket(fields)` | `save_issue` (no id) | Set team, project, milestone, labels, description in one call. |
| `update_ticket(id, fields)` | `save_issue` (with id) | Send only changed fields. |
| `set_status(id, status)` | `save_issue` with the resolved state | Resolve state ids per team via `list_issue_statuses`; see status mapping. |
| `add_comment(id, body)` | `save_comment` | Real markdown, real newlines (no `\n` escapes). |
| `add_dependency(id, blocked_by_id)` | `save_issue` relations, if supported; otherwise flag for manual linking | Verify the relation appears on `get_issue` afterward. |

## Status mapping

Resolve each team's actual workflow states once per session via `list_issue_statuses`, then map by state **type**:

| Canonical | Linear state type | Typical name |
|---|---|---|
| `todo` | `unstarted` | Todo |
| `in_progress` | `started` | In Progress |
| `in_review` | `started` (review-flavored) | In Review — fall back to In Progress if the team has no review state |
| `done` | `completed` | Done |
| `blocked` | team-specific | Use a `blocked` label + comment if no Blocked state exists |

## Label conventions

- Tier hints: labels named exactly `tier:simple`, `tier:standard`, `tier:complex`. Create missing ones with `create_issue_label` (check `list_issue_labels` first).
- Module hints: labels `mod:<area>` (e.g. `mod:api`, `mod:auth`, `mod:frontend`). Same create-if-missing flow.
- Resource hints: labels `resource:<name>` (e.g. `resource:db`). Same create-if-missing flow.

## Caveats

- Issue identifiers (`ABC-123`) and UUIDs are distinct; most tools accept the identifier — prefer it, it is what appears in logs and commits.
- `list_issues` paginates; when orchestrating a milestone, page through completely before planning dispatch order.
- Linear descriptions are markdown; keep the `## Acceptance criteria` heading intact when updating descriptions so criteria remain machine-findable.
