#!/usr/bin/env python3
"""Continuation reconstruction for milestone-orchestrator respawn.

When an orchestrator respawns (context budget reached, or a phase boundary),
the fresh one must pick up exactly what's left — never re-processing a ticket.
That decision is reconstructed from the run's durable record, not from any
context handed between orchestrators: this script reads `<run-dir>/log.jsonl`
and, against the milestone's ticket set, reports which tickets are `done`
(a `ticket_done` event), `blocked` (a `ticket_blocked` event), and therefore
`remaining` (neither).

`done` wins over `blocked` (a ticket blocked then later completed is done).
The log is the source of truth here because it is written locally the moment a
ticket closes out — the orchestrator still cross-checks the tracker, but this
math is what guarantees no double-processing.

Usage:
    remaining_work.py --run-dir <dir> --tickets A-1,A-2,A-3

Output: compact JSON {"done":[...],"blocked":[...],"remaining":[...]}, each a
subset of --tickets in input order. Exit 0 ok, 2 usage error.
"""

import argparse
import json
import os
import sys


def parse_log(run_dir):
    """Return (done_ids, blocked_ids) from log.jsonl. Missing log → empty sets
    (nothing dispatched yet). Malformed lines are skipped, not fatal."""
    done, blocked = set(), set()
    path = os.path.join(run_dir, "log.jsonl")
    if not os.path.isfile(path):
        return done, blocked
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            ticket = event.get("ticket")
            if not ticket:
                continue
            kind = event.get("event")
            if kind == "ticket_done":
                done.add(ticket)
            elif kind == "ticket_blocked":
                blocked.add(ticket)
    return done, blocked


def reconstruct(tickets, done_ids, blocked_ids):
    seen, ordered = set(), []
    for t in tickets:  # dedupe input, preserve first-seen order
        if t not in seen:
            seen.add(t)
            ordered.append(t)
    done = [t for t in ordered if t in done_ids]
    blocked = [t for t in ordered if t in blocked_ids and t not in done_ids]
    remaining = [t for t in ordered if t not in done_ids and t not in blocked_ids]
    return {"done": done, "blocked": blocked, "remaining": remaining}


def main():
    ap = argparse.ArgumentParser(description="Reconstruct remaining milestone work from the run log.")
    ap.add_argument("--run-dir", required=True, help="the active run directory")
    ap.add_argument("--tickets", required=True, help="comma-separated milestone ticket ids")
    args = ap.parse_args()

    if not os.path.isdir(args.run_dir):
        print(f"error: run dir {args.run_dir!r} does not exist", file=sys.stderr)
        sys.exit(2)

    tickets = [t.strip() for t in args.tickets.split(",") if t.strip()]
    done_ids, blocked_ids = parse_log(args.run_dir)
    result = reconstruct(tickets, done_ids, blocked_ids)
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
