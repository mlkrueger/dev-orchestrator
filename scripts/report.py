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
from datetime import datetime

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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
    for e in events:
        counts[e.get("event")] += 1
        if e.get("event") == "gate" and e.get("verdict") == "FAIL":
            gate_fails[e.get("gate") or "?"] += 1
    escalations = [e for e in events if e.get("event") == "escalate"]
    blocked = [e for e in events if e.get("event") == "ticket_blocked"]

    timestamps = [t for t in (parse_ts(e.get("ts")) for e in events) if t]
    wall = (max(timestamps) - min(timestamps)) if timestamps else None

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
    out.append(f"- **Wall time:** {wall}" if wall is not None else "- **Wall time:** unknown")
    out.append(f"- **Agents dispatched (with usage logged):** {len(usage)}")
    out.append(f"- **Tickets done / blocked:** {counts['ticket_done']} / {counts['ticket_blocked']}")
    out.append(f"- **Gate failures:** " + (", ".join(f"{g}: {n}" for g, n in sorted(gate_fails.items())) or "none"))
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
    out += table("Usage by model", bucket(lambda u: u.get("model")))
    out += table("Usage by agent", bucket(lambda u: u.get("agent")))
    out += table("Usage by ticket", bucket(lambda u: u.get("ticket")))

    print("\n".join(out))


if __name__ == "__main__":
    main()
