#!/usr/bin/env python3
"""PreToolUse hook (all tools): per-agent tool-call + wall-clock budgets for fleet agents.

Console-v1 had no cap on a non-converging agent: 15 of 70 implementers ran
past 100 turns; the worst ticket (MKR-242) burned two implementers at 358 and
400 turns — 19% of the whole run's tokens — and cache-read cost grows
super-linearly with turn count. This hook counts tool calls per subagent
(keyed on the payload's `agent_id`, which is unique per subagent — verified
by probe 2026-07-14; `session_id` is shared with the parent and unusable)
and denies further calls once the budget is spent, with a wrap-up message.

It also enforces a per-agent WALL-CLOCK deadline (stall watchdog). The
openbrain studio-auth run lost 12.1 h — 41% of its wall clock — to one
qa-verifier that stalled for 12 hours across only 48 turns; the tool-call
budget never fired because stalls burn time, not calls. The hook records the
first-call timestamp per agent and denies further calls once the deadline
passes, with the same soft-landing wrap-up.

Soft landing by design: the agent keeps its context and can still produce a
final report — it just can't burn more tool calls. The orchestrator treats a
budget-stopped agent as a failed attempt (or `needs-grooming` if the ticket
isn't converging); a deadline-stopped GATE is a stall — re-dispatched fresh
once before counting as a failed attempt.

Budgets are tool CALLS, not turns (~1.3 calls/turn observed). Defaults sized
from console-v1 healthy-agent distributions; override any of them in
.dev-orchestrator/config.json: {"tool_call_budgets": {"implementer": 200}}.
Deadlines are MINUTES since the agent's first tool call; override via
{"wall_clock_minutes": {"qa-verifier": 45}} (0 disables for that agent).

Scope: only while a run is active and only for dev-orchestrator fleet agent
types. Parent-session calls (no agent_id) and non-fleet agents are never
counted. Fails open.
"""

import json
import os
import sys
import time

DEFAULT_BUDGETS = {
    # console-v1 healthy ranges: implementer avg 75 turns (runaways 100-400),
    # scope avg 12, qa avg 29, reviewer avg 19, orchestrator avg 111 turns.
    "implementer": 150,
    "scope-guardian": 50,
    "qa-verifier": 90,
    "code-reviewer": 60,
    "simple-gate": 75,
    "ticket-smith": 150,
    "milestone-orchestrator": 500,
}

DEFAULT_WALL_CLOCK_MINUTES = {
    # Healthy gates finish in 1-4 min; healthy implementers in 3-30 min (an
    # opus complex ticket hit 2.3 h legitimately, hence the loose bound).
    # milestone-orchestrator is exempt (0): it lives for a whole milestone
    # slice by design; respawn bounds it instead.
    "implementer": 150,
    "scope-guardian": 15,
    "qa-verifier": 30,
    "code-reviewer": 20,
    "simple-gate": 25,
    "ticket-smith": 60,
    "milestone-orchestrator": 0,
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


def load_config(cwd):
    path = os.path.join(cwd, ".dev-orchestrator", "config.json")
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f) or {}
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def load_budgets(config):
    budgets = dict(DEFAULT_BUDGETS)
    for k, v in (config.get("tool_call_budgets") or {}).items():
        if isinstance(v, int) and v > 0:
            budgets[bare_type(k)] = v
    return budgets


def load_deadlines(config):
    deadlines = dict(DEFAULT_WALL_CLOCK_MINUTES)
    for k, v in (config.get("wall_clock_minutes") or {}).items():
        if isinstance(v, (int, float)) and v >= 0:
            deadlines[bare_type(k)] = v
    return deadlines


def log_once(run_dir, marker_path, event):
    """Append `event` to the run log once, using `marker_path` as the guard."""
    if os.path.exists(marker_path):
        return
    from datetime import datetime, timezone
    event = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        **event,
    }
    with open(os.path.join(run_dir, "log.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")
    with open(marker_path, "w", encoding="utf-8") as f:
        f.write("1")


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
    agent_id = payload.get("agent_id")
    if not agent_id:
        return  # parent-session call — never budgeted
    agent = bare_type(payload.get("agent_type"))
    if agent not in DEFAULT_BUDGETS:
        return
    cwd = payload.get("cwd") or os.getcwd()
    run_dir = resolve_run_dir(cwd)
    if not run_dir:
        return
    config = load_config(cwd)
    budget = load_budgets(config)[agent]
    deadline_min = load_deadlines(config).get(agent, 0)

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

    # Wall-clock deadline (stall watchdog): epoch of the agent's first tool
    # call lives in <agent_id>.start; past the deadline, further calls are
    # denied so a stalled agent surfaces in minutes, not hours.
    now = time.time()
    start_path = os.path.join(counter_dir, f"{agent_id}.start")
    started = None
    if os.path.isfile(start_path):
        try:
            with open(start_path, encoding="utf-8") as f:
                started = float(f.read().strip() or 0) or None
        except ValueError:
            started = None
    if started is None:
        started = now
        with open(start_path, "w", encoding="utf-8") as f:
            f.write(str(now))

    if deadline_min and (now - started) > deadline_min * 60:
        elapsed_min = int((now - started) / 60)
        log_once(run_dir, os.path.join(counter_dir, f"{agent_id}.deadline"), {
            "event": "deadline_exceeded",
            "agent_id": agent_id,
            "agent": payload.get("agent_type"),
            "elapsed_min": elapsed_min,
            "deadline_min": deadline_min,
            "tool_calls": count,
        })
        deny(
            f"Wall-clock deadline exceeded ({elapsed_min} min elapsed, limit "
            f"{deadline_min} min for {agent}). Something you ran or waited on stalled — "
            f"STOP working now. Do not retry tool calls or wait further. Produce your "
            f"final report/verdict immediately from what you have already observed: "
            f"what is verified, what is not, and what stalled. The orchestrator treats "
            f"a deadline stop as a stall and will re-dispatch fresh."
        )

    if count <= budget:
        return
    log_once(run_dir, os.path.join(counter_dir, f"{agent_id}.exceeded"), {
        "event": "budget_exceeded",
        "agent_id": agent_id,
        "agent": payload.get("agent_type"),
        "tool_calls": count,
    })
    deny(
        f"Tool-call budget exhausted ({budget} calls for {agent}). You are not "
        f"converging within budget — STOP working now. Do not retry tool calls. "
        f"Produce your final report immediately: what is complete, what is not, "
        f"and what the remaining work actually requires. The orchestrator will "
        f"treat this as a failed attempt or send the ticket back for grooming."
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # fail open: budgeting must never break a run
    sys.exit(0)
