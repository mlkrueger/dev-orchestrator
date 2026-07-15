#!/usr/bin/env python3
"""Aggregate a dev-orchestrator run log into a markdown postmortem report.

Usage: report.py [run-id | run-dir]
  no arg -> .dev-orchestrator/current-run, else newest dir in .dev-orchestrator/runs/

Reads <run_dir>/log.jsonl (see docs/log-schema.md). Pricing: config/pricing.json
next to this script's plugin root, overridden by .dev-orchestrator/pricing.json.
"""

import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Gate agents emit per-gate vocabularies (scope: PASS|PASS_WITH_NOTES|FAIL,
# qa: PASS|FAIL, review: APPROVE|REQUEST_CHANGES, simple: PASS|FAIL) — normalize
# before counting. Gate names are dynamic, so new gates need no change here.
PASS_VERDICTS = {"PASS", "PASS_WITH_NOTES", "APPROVE"}
FAIL_VERDICTS = {"FAIL", "REQUEST_CHANGES"}

# Events further apart than this are idle time (paused session, sleeping
# machine), not run time — console-v1 logged 50h wall for ~10h of activity.
IDLE_GAP_S = 30 * 60


def resolve_run_dir(arg):
    base = ".dev-orchestrator"
    if arg:
        for candidate in (arg, os.path.join(base, "runs", arg)):
            if os.path.isdir(candidate):
                return candidate
        sys.exit(f"error: no run directory found for '{arg}'")
    ptr = os.path.join(base, "current-run")
    if os.path.isfile(ptr):
        run_dir = open(ptr, encoding="utf-8").read().strip()
        if os.path.isdir(run_dir):
            return run_dir
    runs = os.path.join(base, "runs")
    if os.path.isdir(runs):
        dirs = sorted(
            (d for d in os.listdir(runs) if os.path.isdir(os.path.join(runs, d))),
            reverse=True,
        )
        if dirs:
            return os.path.join(runs, dirs[0])
    sys.exit("error: no run found (searched .dev-orchestrator/current-run and .dev-orchestrator/runs/)")


def load_pricing():
    pricing = None
    for path in (
        os.path.join(".dev-orchestrator", "pricing.json"),
        os.path.join(PLUGIN_ROOT, "config", "pricing.json"),
    ):
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                pricing = json.load(f)
            break
    return pricing or {"per_mtok": [], "cache_read_multiplier": 0.1, "cache_write_multiplier": 1.25}


def rate_for(model, pricing):
    if not model:
        return None
    for entry in pricing.get("per_mtok", []):
        if entry.get("match", "") in model:
            return entry
    return None


def cost_usd(u, pricing):
    rate = rate_for(u.get("model"), pricing)
    if not rate:
        return None
    inp = rate["input"] / 1e6
    out = rate["output"] / 1e6
    return (
        (u.get("input_tokens") or 0) * inp
        + (u.get("cache_creation_tokens") or 0) * inp * pricing.get("cache_write_multiplier", 1.25)
        + (u.get("cache_read_tokens") or 0) * inp * pricing.get("cache_read_multiplier", 0.1)
        + (u.get("output_tokens") or 0) * out
    )


def parse_ts(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def fmt_cost(c):
    return f"${c:,.2f}" if c is not None else "unknown"


def fmt_tok(n):
    return f"{n:,}"


def main():
    run_dir = resolve_run_dir(sys.argv[1] if len(sys.argv) > 1 else None)
    log_path = os.path.join(run_dir, "log.jsonl")
    if not os.path.isfile(log_path):
        sys.exit(f"error: {log_path} does not exist")

    pricing = load_pricing()
    events = []
    with open(log_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    usage = [e for e in events if e.get("event") == "agent_usage"]

    def bucket(key_fn):
        agg = defaultdict(lambda: {"input_tokens": 0, "output_tokens": 0,
                                   "cache_creation_tokens": 0, "cache_read_tokens": 0,
                                   "agents": 0, "cost": 0.0, "unpriced": False, "model": None})
        for u in usage:
            b = agg[key_fn(u) or "(unknown)"]
            for k in ("input_tokens", "output_tokens", "cache_creation_tokens", "cache_read_tokens"):
                b[k] += u.get(k) or 0
            b["agents"] += 1
            b["model"] = u.get("model")
            c = cost_usd(u, pricing)
            if c is None:
                b["unpriced"] = True
            else:
                b["cost"] += c
        return agg

    def table(title, agg):
        lines = [f"### {title}", "",
                 "| " + title.split(" by ")[-1].title() + " | Agents | Input | Cache W | Cache R | Output | Cost |",
                 "|---|---|---|---|---|---|---|"]
        for key in sorted(agg, key=lambda k: -agg[k]["cost"]):
            b = agg[key]
            cost = "unknown" if b["unpriced"] else fmt_cost(b["cost"])
            lines.append(
                f"| {key} | {b['agents']} | {fmt_tok(b['input_tokens'])} | "
                f"{fmt_tok(b['cache_creation_tokens'])} | {fmt_tok(b['cache_read_tokens'])} | "
                f"{fmt_tok(b['output_tokens'])} | {cost} |"
            )
        lines.append("")
        return lines

    counts = defaultdict(int)
    gate_fails = defaultdict(int)
    gate_passes = defaultdict(int)
    odd_verdicts = defaultdict(int)
    for e in events:
        counts[e.get("event")] += 1
        if e.get("event") == "gate":
            verdict = (e.get("verdict") or "").upper()
            gate = e.get("gate") or "?"
            if verdict in FAIL_VERDICTS:
                gate_fails[gate] += 1
            elif verdict in PASS_VERDICTS:
                gate_passes[gate] += 1
            else:
                odd_verdicts[verdict or "(empty)"] += 1
    escalations = [e for e in events if e.get("event") == "escalate"]
    blocked = [e for e in events if e.get("event") == "ticket_blocked"]

    timestamps = sorted(t for t in (parse_ts(e.get("ts")) for e in events) if t)
    wall = active = None
    if timestamps:
        wall = timestamps[-1] - timestamps[0]
        idle_s = sum(
            gap for gap in (
                (b - a).total_seconds() for a, b in zip(timestamps, timestamps[1:])
            ) if gap > IDLE_GAP_S
        )
        active = wall - timedelta(seconds=idle_s)

    total_cost, unpriced = 0.0, False
    total = {"input_tokens": 0, "output_tokens": 0, "cache_creation_tokens": 0, "cache_read_tokens": 0}
    for u in usage:
        for k in total:
            total[k] += u.get(k) or 0
        c = cost_usd(u, pricing)
        if c is None:
            unpriced = True
        else:
            total_cost += c

    out = [f"## Run report — `{run_dir}`", ""]
    if wall is not None:
        out.append(f"- **Active time:** {active} (wall {wall}; gaps >{IDLE_GAP_S // 60}min counted as idle)")
    else:
        out.append("- **Active time:** unknown")
    out.append(f"- **Agents dispatched (with usage logged):** {len(usage)}")
    out.append(f"- **Tickets done / blocked:** {counts['ticket_done']} / {counts['ticket_blocked']}")
    if counts.get("milestone_continue"):
        out.append(f"- **Orchestrator respawns (context-budget):** {counts['milestone_continue']} "
                   "— milestones sliced to keep orchestrator context bounded")
    gate_lines = []
    for g in sorted(set(gate_fails) | set(gate_passes)):
        f_n, p_n = gate_fails.get(g, 0), gate_passes.get(g, 0)
        pct = f" ({f_n / (f_n + p_n) * 100:.0f}% reject)" if (f_n + p_n) else ""
        gate_lines.append(f"{g}: {f_n} of {f_n + p_n}{pct}")
    out.append("- **Gate failures:** " + (", ".join(gate_lines) or "none"))
    if odd_verdicts:
        out.append("- **Nonstandard gate verdicts (uncounted):** "
                   + ", ".join(f"{v}: {n}" for v, n in sorted(odd_verdicts.items())))
    out.append(f"- **Escalations:** " + (", ".join(
        f"{e.get('ticket')}: {e.get('from')}→{e.get('to')}" for e in escalations) or "none"))
    if blocked:
        out.append("- **Blocked:** " + "; ".join(
            f"{e.get('ticket')} ({e.get('reason', '?')})" for e in blocked))
    total_all = sum(total.values())
    cost_str = fmt_cost(total_cost) + (" + unpriced models" if unpriced else "")
    out.append(f"- **Total tokens:** {fmt_tok(total_all)} "
               f"(in {fmt_tok(total['input_tokens'])}, cache-w {fmt_tok(total['cache_creation_tokens'])}, "
               f"cache-r {fmt_tok(total['cache_read_tokens'])}, out {fmt_tok(total['output_tokens'])})")
    out.append(f"- **Estimated cost:** {cost_str}")
    exceeded = [e for e in events if e.get("event") == "budget_exceeded"]
    if exceeded:
        out.append(f"- **Budget-stopped agents:** {len(exceeded)} ("
                   + ", ".join(f"{(e.get('agent') or '?').split(':')[-1]}@{e.get('tool_calls')} calls"
                               for e in exceeded) + ")")
    out.append("")

    warnings = [e for e in events if e.get("event") == "usage_warning"]
    if counts["dispatch"] > 0 and not usage:
        warnings.append({
            "reason": f"{counts['dispatch']} dispatches logged but zero agent_usage events",
            "detail": "the SubagentStop hook likely never found a transcript",
            "hint": "Inspect the SubagentStop hook payload for a renamed transcript-path "
                    "field; see docs/log-schema.md 'Troubleshooting usage accounting'.",
        })
    if warnings:
        out.append("### ⚠ Usage-accounting warnings")
        out.append("")
        seen = set()
        for w in warnings:
            key = (w.get("reason"), w.get("detail"))
            if key in seen:
                continue
            seen.add(key)
            out.append(f"- **{w.get('reason')}** — {w.get('detail', '')}")
            if w.get("payload_keys"):
                out.append(f"  - payload keys seen: `{', '.join(w['payload_keys'])}`")
            out.append(f"  - {w.get('hint', '')}")
        out.append("")
        out.append("Token/cost figures below are incomplete while these warnings persist.")
        out.append("")
    def ticket_key(u):
        if u.get("ticket"):
            return u["ticket"]
        if u.get("milestone"):
            return f"(milestone: {u['milestone']})"
        return None

    out += table("Usage by model", bucket(lambda u: u.get("model")))
    out += table("Usage by agent", bucket(lambda u: u.get("agent")))
    out += table("Usage by ticket", bucket(ticket_key))

    print("\n".join(out))


if __name__ == "__main__":
    main()
