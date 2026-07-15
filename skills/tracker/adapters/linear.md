# Linear adapter — MCP fallback

**Primary path is `bin/tracker`** (see the tracker SKILL). This adapter is the fallback used only when `LINEAR_API_KEY` is unset: the canonical operations are driven through the Linear MCP server (`mcp__plugin_linear_linear__*` tools, from the `linear` plugin; the tool prefix may vary by how the server is connected — match on the `_linear__` suffixes below). If these tools are deferred, load them via ToolSearch first, batching every tool you expect into one `select:` query.

The canonical **status mapping** (by workflow-state *type*) and **label conventions** (`tier:`/`mod:`/`resource:`, create-if-missing incl. the workspace-collision reuse) are implemented authoritatively in `bin/tracker` — this doc no longer restates them. When driving the MCP fallback, resolve statuses by state type the same way the script does (below) rather than hardcoding state names.

## Operation mapping (fallback)

| Canonical | Linear tool(s) | Notes |
|---|---|---|
| `list_tickets(filter)` | `list_issues` | Filter by `projectId`/`milestoneId`/state/labels/team. Prefer one filtered list over per-ticket gets. |
| `get_ticket(id)` | `get_issue` (`includeRelations`) | Treat `blockedBy` relations as dependencies. Comments via `list_comments` only when needed. |
| `create_ticket(fields)` | `save_issue` (no id) | Set team, project, milestone, labels, description in one call. |
| `update_ticket(id, fields)` | `save_issue` (with id) | Send only changed fields. |
| `set_status(id, status)` | `save_issue` with the resolved state | Resolve state ids per team via `list_issue_statuses`; map by type (below). |
| `add_comment(id, body)` | `save_comment` | Real markdown, real newlines (no `\n` escapes). |
| `add_dependency(id, blocked_by_id)` | `save_issue` `blockedBy` relation | Verify the relation appears on `get_issue` afterward. |

## Status resolution by state type

Resolve each team's states once (`list_issue_statuses`) and map by **type**, never by name: `unstarted`→`todo`, `started`→`in_progress` (or `in_review` for a review-named started state), `completed`→`done`. For `blocked`, use a Blocked-named state if one exists, else apply a `blocked` label + comment (what `bin/tracker` does).

## Caveats

- Issue identifiers (`ABC-123`) and UUIDs are distinct; most tools accept the identifier — prefer it, it is what appears in logs and commits.
- `list_issues` paginates; when orchestrating a milestone, page through completely before planning dispatch order.
- Linear descriptions are markdown; keep the `## Acceptance criteria` heading intact when updating descriptions so criteria remain machine-findable (both paths parse it).
