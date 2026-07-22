#!/usr/bin/env python3
"""Continuation reconstruction for milestone-orchestrator respawn and resume.

When an orchestrator respawns (context budget reached, or a phase boundary),
the fresh one must pick up exactly what's left — never re-processing a ticket.
That decision is reconstructed from the run's durable record, not from any
context handed between orchestrators.

There are two durable records, and this script reconciles them:

1. `<run-dir>/log.jsonl` — written locally the moment a ticket closes out.
   Fast and precise, but **machine-local and gitignored**: a reclaimed
   container, a fresh clone, or a lost run dir takes it with them.
2. The **tracker** itself — the ticket's `status`, which survives anything the
   run's disk does. This is the source of truth to resume from when the local
   log is gone or incomplete.

Given the milestone's ticket set, this reports which tickets are `done`,
`blocked`, and therefore `remaining` (neither). The log is read by default;
pass `--tracker-status-file` (the JSON `bin/tracker list` emits, or an
`{id: status}` object) to fold the tracker in. When both are present a ticket
counts as done if **either** source says so — so a run resumed with an empty
log still skips everything the tracker already shows complete, and a tracker
write that never landed still can't cause a committed ticket to be redone.

`done` wins over `blocked` (a ticket blocked then later completed is done).

`resync` (emitted only with a tracker file) lists tickets the log has closed
but the tracker does not yet reflect — a `set-status` write that never landed.
The orchestrator re-issues those so the board stays an accurate source of
truth; it is advisory and never affects the `remaining` math.

Usage:
    remaining_work.py --run-dir <dir> --tickets A-1,A-2,A-3
    remaining_work.py --run-dir <dir> --tickets A-1,A-2 --tracker-status-file s.json

Output: compact JSON {"done":[...],"blocked":[...],"remaining":[...],
"resync":[{"id","want"}...]}, each ticket list a subset of --tickets in input
order. Exit 0 ok, 2 usage error.
"""

import argparse
import json
import os
import sys


def parse_log(run_dir):
    """Return (done_ids, blocked_ids) from log.jsonl. Missing log → empty sets
    (nothing dispatched yet, or the log was lost). Malformed lines are skipped,
    not fatal."""
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


def parse_tracker(path):
    """Return (done_ids, blocked_ids) from a tracker-status file, or None if no
    path was given. Accepts either the array `bin/tracker list` emits (objects
    carrying `id` and canonical `status`) or a plain `{id: status}` object.
    Only `done`/`blocked` statuses matter here; a malformed or unreadable file
    is a usage error — the caller asked to trust the tracker, so silently
    ignoring it would be worse than stopping."""
    if path is None:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"error: reading tracker-status file {path!r}: {e}", file=sys.stderr)
        sys.exit(2)

    if isinstance(data, dict):
        rows = [{"id": k, "status": v} for k, v in data.items()]
    elif isinstance(data, list):
        rows = data
    else:
        print(f"error: tracker-status file {path!r} must be a list or object", file=sys.stderr)
        sys.exit(2)

    done, blocked = set(), set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        tid, status = row.get("id"), row.get("status")
        if not tid:
            continue
        if status == "done":
            done.add(tid)
        elif status == "blocked":
            blocked.add(tid)
    return done, blocked


def reconstruct(tickets, log, tracker):
    """Merge the log and (optional) tracker views into done/blocked/remaining
    plus a resync list. `log` and `tracker` are each (done, blocked) tuples;
    `tracker` may be None.

    A ticket is done if EITHER source calls it done (union) — this is what lets
    a resume with a lost log still skip completed work, and a dropped tracker
    write still not cause a redo. blocked is the union minus done. resync flags
    tickets the log has closed that the tracker does not yet reflect."""
    seen, ordered = set(), []
    for t in tickets:  # dedupe input, preserve first-seen order
        if t not in seen:
            seen.add(t)
            ordered.append(t)

    log_done, log_blocked = log
    trk_done, trk_blocked = tracker if tracker is not None else (set(), set())

    done_ids = log_done | trk_done
    blocked_ids = (log_blocked | trk_blocked) - done_ids

    done = [t for t in ordered if t in done_ids]
    blocked = [t for t in ordered if t in blocked_ids]
    remaining = [t for t in ordered if t not in done_ids and t not in blocked_ids]

    resync = []
    if tracker is not None:
        for t in ordered:
            if t in log_done and t not in trk_done:
                resync.append({"id": t, "want": "done"})
            elif t in blocked_ids and t in log_blocked and t not in trk_blocked and t not in trk_done:
                resync.append({"id": t, "want": "blocked"})

    return {"done": done, "blocked": blocked, "remaining": remaining, "resync": resync}


def main():
    ap = argparse.ArgumentParser(description="Reconstruct remaining milestone work from the run log and tracker.")
    ap.add_argument("--run-dir", required=True, help="the active run directory")
    ap.add_argument("--tickets", required=True, help="comma-separated milestone ticket ids")
    ap.add_argument("--tracker-status-file",
                    help="JSON of tracker statuses (bin/tracker list output, or an {id:status} object) "
                         "to reconcile against — the durable source of truth on resume")
    args = ap.parse_args()

    if not os.path.isdir(args.run_dir):
        print(f"error: run dir {args.run_dir!r} does not exist", file=sys.stderr)
        sys.exit(2)

    tickets = [t.strip() for t in args.tickets.split(",") if t.strip()]
    log = parse_log(args.run_dir)
    tracker = parse_tracker(args.tracker_status_file)
    result = reconstruct(tickets, log, tracker)
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
