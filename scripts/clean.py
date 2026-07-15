#!/usr/bin/env python3
"""List or delete dev-orchestrator run directories (.dev-orchestrator/runs/).

Usage: clean.py                          list runs (default)
       clean.py --keep N                 delete all but the newest N runs
       clean.py --older-than DAYS        delete runs older than DAYS days
       clean.py --all                    delete every run
       clean.py RUN_ID [RUN_ID ...]      delete specific runs
       clean.py --dry-run <selector>     show what would be deleted, delete nothing

Selectors combine: a run is deleted if ANY selector matches it. The active run
(the one .dev-orchestrator/current-run points at) is never deleted; runs are
otherwise plain gitignored files and safe to remove. A stale current-run pointer
(target directory missing) is reported on list and cleared on any delete action.
"""

import json
import os
import shutil
import sys
from datetime import datetime, timedelta, timezone

BASE = ".dev-orchestrator"
RUNS = os.path.join(BASE, "runs")
POINTER = os.path.join(BASE, "current-run")


def read_pointer():
    if os.path.isfile(POINTER):
        return open(POINTER, encoding="utf-8").read().strip()
    return None


def dir_size(path):
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def fmt_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:,.0f} B" if unit == "B" else f"{n:,.1f} {unit}"
        n /= 1024


def started_at(run_dir):
    meta = os.path.join(run_dir, "meta.json")
    if os.path.isfile(meta):
        try:
            with open(meta, encoding="utf-8") as f:
                ts = json.load(f).get("started_at")
            if ts:
                return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        except (json.JSONDecodeError, ValueError, OSError):
            pass
    try:
        return datetime.fromtimestamp(os.path.getmtime(run_dir), tz=timezone.utc)
    except OSError:
        return None


def collect_runs():
    if not os.path.isdir(RUNS):
        return []
    runs = []
    for name in sorted(os.listdir(RUNS), reverse=True):  # run-ids sort newest-first
        path = os.path.join(RUNS, name)
        if os.path.isdir(path):
            runs.append({"id": name, "path": path,
                         "started": started_at(path), "size": dir_size(path)})
    return runs


def main():
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    args = [a for a in args if a != "--dry-run"]

    keep = older_than = None
    delete_all = False
    explicit = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--keep":
            i += 1
            keep = int(args[i])
        elif a == "--older-than":
            i += 1
            older_than = float(args[i])
        elif a == "--all":
            delete_all = True
        elif a.startswith("-"):
            sys.exit(f"error: unknown flag '{a}' (see clean.py --help via docstring)")
        else:
            explicit.append(a)
        i += 1

    runs = collect_runs()
    pointer = read_pointer()
    # The pointer may be stored relative (as orchestrate.md writes it) or
    # absolute (as the other scripts' resolvers accept). abspath normalizes
    # both to an absolute path so the active-run match — and the guard that
    # protects the active run from deletion — works either way.
    pointer_norm = os.path.abspath(pointer) if pointer else None
    active = next((r for r in runs if os.path.abspath(r["path"]) == pointer_norm), None)
    stale_pointer = pointer is not None and active is None

    listing_only = keep is None and older_than is None and not delete_all and not explicit

    if listing_only:
        if not runs:
            print(f"No runs found under {RUNS}/.")
        else:
            print(f"## Runs — `{RUNS}/` ({len(runs)} total, {fmt_size(sum(r['size'] for r in runs))})")
            print()
            print("| Run | Started | Size | |")
            print("|---|---|---|---|")
            for r in runs:
                started = r["started"].strftime("%Y-%m-%d %H:%M") if r["started"] else "unknown"
                mark = "**active**" if active and r["id"] == active["id"] else ""
                print(f"| {r['id']} | {started} | {fmt_size(r['size'])} | {mark} |")
        if stale_pointer:
            print()
            print(f"⚠ Stale pointer: `{POINTER}` → `{pointer}` (missing). The usage hook "
                  f"appends to this dead path; run any delete action to clear it, or remove the file.")
        return

    unknown = [e for e in explicit if not any(r["id"] == e for r in runs)]
    if unknown:
        sys.exit(f"error: no such run(s): {', '.join(unknown)}")

    cutoff = None
    if older_than is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=older_than)

    targets = []
    for idx, r in enumerate(runs):
        if active and r["id"] == active["id"]:
            if r["id"] in explicit:
                sys.exit(f"error: '{r['id']}' is the active run (per {POINTER}); "
                         f"remove that pointer first if the run is truly finished")
            continue
        matched = (
            delete_all
            or r["id"] in explicit
            or (keep is not None and idx >= keep)
            or (cutoff is not None and r["started"] is not None and r["started"] < cutoff)
        )
        if matched:
            targets.append(r)

    verb = "Would delete" if dry_run else "Deleted"
    if not targets:
        print("Nothing to delete.")
    for r in targets:
        if not dry_run:
            shutil.rmtree(r["path"])
        started = r["started"].strftime("%Y-%m-%d %H:%M") if r["started"] else "unknown"
        print(f"{verb} {r['id']} ({started}, {fmt_size(r['size'])})")
    if targets:
        freed = fmt_size(sum(r["size"] for r in targets))
        print(f"{'Would free' if dry_run else 'Freed'} {freed} across {len(targets)} run(s).")

    if stale_pointer:
        if not dry_run:
            os.remove(POINTER)
            print(f"Cleared stale pointer {POINTER} (was → {pointer}).")
        else:
            print(f"Would clear stale pointer {POINTER} (→ {pointer}, missing).")
    kept = len(runs) - (0 if dry_run else len(targets))
    print(f"{kept} run(s) remain.")


if __name__ == "__main__":
    main()
