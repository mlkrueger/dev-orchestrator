---
description: How to use dev-orchestrator - commands, the agent fleet, ticket conventions, and workflow. Ask a specific question or get the full overview.
argument-hint: [question, e.g. "how do tier labels work"]
---

Explain how to use the dev-orchestrator plugin, using its own documentation as the source of truth.

Arguments: `$ARGUMENTS`

1. Read `${CLAUDE_PLUGIN_ROOT}/README.md`.

2. **If arguments contain a question**, answer just that question from the README. If the answer lives in a specific component, read it for detail before answering: command definitions in `${CLAUDE_PLUGIN_ROOT}/commands/`, agent definitions in `${CLAUDE_PLUGIN_ROOT}/agents/`, ticket model and adapters in `${CLAUDE_PLUGIN_ROOT}/skills/tracker/`, log schema in `${CLAUDE_PLUGIN_ROOT}/docs/log-schema.md`.

3. **If no arguments**, present a compact usage overview (the plugin is already installed — skip installation):
   - What the plugin does, in 2–3 sentences, with the pipeline diagram from the README.
   - The commands with example invocations and flags: `/dev-orchestrator:orchestrate`, `/dev-orchestrator:report`, `/dev-orchestrator:clean`.
   - The one-off fleet table (agent → what to ask it).
   - Ticket conventions required for a good run (acceptance criteria, `tier:` labels, `mod:` labels, blocked-by relations) — note that `ticket-smith` grooms these automatically.
   - Prerequisites to flag if unmet: check that this is a git repo, that a tracker MCP server is connected (per `.dev-orchestrator/config.json`, default Linear), and that `python3` exists. Mention the permissions-allowlist advice for unattended runs.
   - Close by pointing at `/dev-orchestrator:help <question>` for anything deeper.

Keep the overview under ~40 lines. Do not paste the README verbatim — summarize, and quote exact command syntax only.
