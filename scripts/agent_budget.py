#!/usr/bin/env python3
"""PreToolUse hook (all tools): per-agent tool-call budget for fleet agents.

Console-v1 had no cap on a non-converging agent: 15 of 70 implementers ran
past 100 turns; the worst ticket (MKR-242) burned two implementers at 358 and
400 turns — 19% of the whole run's tokens — and cache-read cost grows
super-linearly with turn count. This hook counts tool calls per subagent
(keyed on the payload's `agent_id`, which is unique per subagent — verified
by probe 2026-07-14; `session_id` is shared with the parent and unusable)
and denies further calls once the budget is spent, with a wrap-up message.

Soft landing by design: the agent keeps its context and can still produce a
final report — it just can't burn more tool calls. The orchestrator treats a
budget-stopped agent as a failed attempt (or `needs-grooming` if the ticket
isn't converging).

Budgets are tool CALLS, not turns (~1.3 calls/turn observed). Defaults sized
from console-v1 healthy-agent distributions; override any of them in
.dev-orchestrator/config.json: {"tool_call_budgets": {"implementer": 200}}.

Scope: only while a run is active and only for dev-orchestrator fleet agent
types. Parent-session calls (no agent_id) and non-fleet agents are never
counted. Fails open.
"""

import json
import os
import sys

DEFAULT_BUDGETS = {
    # console-v1 healthy ranges: implementer avg 75 turns (runaways 100-400),
    # scope avg 12, qa avg 29, reviewer avg 19, orchestrator avg 111 turns.
    "implementer": 150,
    "scope-guardian": 50,
    "qa-verifier": 90,
    "code-reviewer": 60,
    "ticket-smith": 150,
    "milestone-orchestrator": 500,
}


def bare_type(agent_type):
    return (agent_type or "").split(":")[-1].strip().lower()


def resolve_run_dir(cwd):
    ptr = os.path.join(cwd, ".dev-orchestrator", "current-run")
    if not os.path.isfile(ptr):
        return None
    with open(ptr, encoding="utf-8") as f:
        run_dir = f.read().strip()
    if not run_dir:
        return None
    if not os.path.isabs(run_dir):
        run_dir = os.path.join(cwd, run_dir)
    return run_dir if os.path.isdir(run_dir) else None


def load_budgets(cwd):
    budgets = dict(DEFAULT_BUDGETS)
    path = os.path.join(cwd, ".dev-orchestrator", "config.json")
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                override = json.load(f).get("tool_call_budgets") or {}
            for k, v in override.items():
                if isinstance(v, int) and v > 0:
                    budgets[bare_type(k)] = v
        except (json.JSONDecodeError, OSError):
            pass
    return budgets


def log_exceeded(run_dir, agent_id, agent, count):
    from datetime import datetime, timezone
    event = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "event": "budget_exceeded",
        "agent_id": agent_id,
        "agent": agent,
        "tool_calls": count,
    }
    with open(os.path.join(run_dir, "log.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")


def main():
    payload = json.load(sys.stdin)
    agent_id = payload.get("agent_id")
    if not agent_id:
        return  # parent-session call — never budgeted
    agent = bare_type(payload.get("agent_type"))
    budgets = None
    cwd = payload.get("cwd") or os.getcwd()
    if agent in DEFAULT_BUDGETS:
        budgets = load_budgets(cwd)
    if budgets is None:
        return
    run_dir = resolve_run_dir(cwd)
    if not run_dir:
        return

    budget = budgets[agent]
    counter_dir = os.path.join(run_dir, "budgets")
    os.makedirs(counter_dir, exist_ok=True)
    counter_path = os.path.join(counter_dir, f"{agent_id}.count")
    count = 0
    if os.path.isfile(counter_path):
        try:
            with open(counter_path, encoding="utf-8") as f:
                count = int(f.read().strip() or 0)
        except ValueError:
            count = 0
    count += 1
    with open(counter_path, "w", encoding="utf-8") as f:
        f.write(str(count))

    if count <= budget:
        return
    if count == budget + 1:  # log once per agent
        log_exceeded(run_dir, agent_id, payload.get("agent_type"), count)
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"Tool-call budget exhausted ({budget} calls for {agent}). You are not "
                f"converging within budget — STOP working now. Do not retry tool calls. "
                f"Produce your final report immediately: what is complete, what is not, "
                f"and what the remaining work actually requires. The orchestrator will "
                f"treat this as a failed attempt or send the ticket back for grooming."
            ),
        }
    }))
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # fail open: budgeting must never break a run
    sys.exit(0)
