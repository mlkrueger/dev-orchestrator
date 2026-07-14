#!/usr/bin/env python3
"""PreToolUse hook on the Agent tool: deterministic dispatch policy for fleet agents.

Prompt-based routing rules decay (console-v1: 34% of implementers ran on Opus
against a ~10% design bar, and Opus code-reviewer dispatches outnumbered Sonnet
7:2 — inverted from policy). This hook makes the rules mechanical:

1. Correlation — implementer/gate dispatches must begin with `TICKET: <id>`;
   milestone-orchestrator dispatches with `MILESTONE: <name>`. Without these
   lines the usage hook cannot attribute cost (55% of console-v1's spend was
   unattributed).
2. Opus justification — an Opus implementer or code-reviewer dispatch must
   carry a `TIER: complex` line. Escalations declare `ESCALATED: <from>` instead.
3. Fable ceiling — no fleet agent ever runs on a Fable-class model.

Scope: only enforces while a run is active (`.dev-orchestrator/current-run`
exists in cwd) and only for dev-orchestrator fleet subagent types — one-off
agent use stays unaffected. Fails open on any internal error: policy must
never take down a run over a hook bug.

Denials return permissionDecision "deny" with a reason the orchestrator can
act on (it re-dispatches with the missing line — a cheap in-turn retry).
"""

import json
import os
import re
import sys

FLEET_TICKET_AGENTS = {"implementer", "scope-guardian", "qa-verifier", "code-reviewer"}
FLEET_MILESTONE_AGENTS = {"milestone-orchestrator"}
OPUS_JUSTIFIED_AGENTS = {"implementer", "code-reviewer"}


def bare_type(subagent_type):
    return (subagent_type or "").split(":")[-1].strip().lower()


def run_active(cwd):
    ptr = os.path.join(cwd, ".dev-orchestrator", "current-run")
    if not os.path.isfile(ptr):
        return False
    with open(ptr, encoding="utf-8") as f:
        run_dir = f.read().strip()
    if not run_dir:
        return False
    if not os.path.isabs(run_dir):
        run_dir = os.path.join(cwd, run_dir)
    return os.path.isdir(run_dir)


def deny(reason):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


def main():
    payload = json.load(sys.stdin)
    if payload.get("tool_name") != "Agent":
        return
    cwd = payload.get("cwd") or os.getcwd()
    if not run_active(cwd):
        return

    tool_input = payload.get("tool_input") or {}
    agent = bare_type(tool_input.get("subagent_type"))
    fleet = FLEET_TICKET_AGENTS | FLEET_MILESTONE_AGENTS | {"ticket-smith"}
    if agent not in fleet:
        return

    prompt = tool_input.get("prompt") or ""
    model = (tool_input.get("model") or "").lower()

    if "fable" in model:
        deny(f"Policy: fleet agents never run on Fable-class models "
             f"(attempted {agent} on '{model}'). Ceiling is opus.")

    if agent in FLEET_TICKET_AGENTS:
        if not re.search(r"^TICKET:\s*\S+", prompt, re.MULTILINE):
            deny(f"Policy: every {agent} dispatch must begin with 'TICKET: <id>' on its "
                 f"own line — the usage hook correlates cost to tickets on it. "
                 f"Re-dispatch with that line first.")
    elif agent in FLEET_MILESTONE_AGENTS:
        if not re.search(r"^MILESTONE:\s*\S+", prompt, re.MULTILINE):
            deny("Policy: milestone-orchestrator briefs must begin with 'MILESTONE: <name>' "
                 "on its own line so orchestrator cost attributes to the milestone. "
                 "Re-dispatch with that line first.")

    if "opus" in model and agent in OPUS_JUSTIFIED_AGENTS:
        justified = (
            re.search(r"^TIER:\s*complex\b", prompt, re.MULTILINE | re.IGNORECASE)
            or re.search(r"^ESCALATED:\s*\S+", prompt, re.MULTILINE)
        )
        if not justified:
            deny(f"Policy: an opus {agent} dispatch requires justification — a "
                 f"'TIER: complex' line (ticket is labeled tier:complex) or an "
                 f"'ESCALATED: <from-tier>' line (retry ladder exhausted a lower tier). "
                 f"If neither applies, dispatch at sonnet instead.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # fail open: policy must never break a run
    sys.exit(0)
