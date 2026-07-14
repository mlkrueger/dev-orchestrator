#!/usr/bin/env python3
"""Plan-time backlog validator: deterministic run-readiness gate.

Usage: validate_plan.py [plan.json]   (no arg -> stdin)

Input: {"tickets": [{"id": "MKR-1", "tier": "simple|standard|complex",
                     "deps": ["MKR-0", ...], "criteria": true, "mods": ["api"]}]}
The ticket set must cover the WHOLE run, not one milestone — cross-milestone
dependencies are resolved against this set.

Exit 0 = run-ready. Exit 1 = failures printed; the run must not initialize.

Checks (each grounded in a console-v1 failure):
- FAIL tier-mix: complex share > COMPLEX_MAX_SHARE. Complex tickets route to
  Opus; console-v1 ran 34% of implementer work on Opus ($363 of $526) against
  a ~1-in-10 design bar. Over the bar -> the tickets are too big; send the
  backlog back to ticket-smith to split them. Decomposition happens at
  grooming time, never mid-run.
- FAIL unknown-dep: a dependency references a ticket outside the run set.
  MKR-278 blocked because prerequisite work existed only implicitly.
- FAIL missing tier / missing acceptance criteria: degrade routing and make
  the QA gate unverifiable.
- WARN missing mods: ticket serializes against everything (no parallelism).

Override the complex bar via .dev-orchestrator/config.json:
{"complex_max_share": 0.2}
"""

import json
import os
import sys

COMPLEX_MAX_SHARE = 0.15
VALID_TIERS = {"simple", "standard", "complex"}


def load_config_share():
    path = os.path.join(".dev-orchestrator", "config.json")
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                value = json.load(f).get("complex_max_share")
            if isinstance(value, (int, float)) and 0 < value <= 1:
                return float(value)
        except (json.JSONDecodeError, OSError):
            pass
    return COMPLEX_MAX_SHARE


def main():
    if len(sys.argv) > 1:
        with open(sys.argv[1], encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = json.load(sys.stdin)
    tickets = data.get("tickets") if isinstance(data, dict) else data
    if not isinstance(tickets, list) or not tickets:
        sys.exit("error: input must be {\"tickets\": [...]} with at least one ticket")

    ids = {t.get("id") for t in tickets}
    failures, warnings = [], []

    complex_ids = [t["id"] for t in tickets if t.get("tier") == "complex"]
    max_share = load_config_share()
    share = len(complex_ids) / len(tickets)
    if share > max_share:
        failures.append(
            f"tier-mix: {len(complex_ids)}/{len(tickets)} tickets ({share:.0%}) are "
            f"tier:complex — bar is {max_share:.0%}. Complex routes to Opus; over the "
            f"bar means tickets are too big. Send to ticket-smith to split: "
            f"{', '.join(complex_ids)}"
        )

    for t in tickets:
        tid = t.get("id") or "(no id)"
        tier = t.get("tier")
        if tier not in VALID_TIERS:
            failures.append(f"{tid}: missing or invalid tier label (got {tier!r})")
        if not t.get("criteria"):
            failures.append(f"{tid}: no acceptance criteria — QA gate cannot verify it")
        for dep in t.get("deps") or []:
            if dep not in ids:
                failures.append(
                    f"{tid}: depends on {dep}, which is not in this run's ticket set — "
                    f"either add it to the run or the dependency is phantom work "
                    f"(the MKR-278 failure mode)"
                )
        if not t.get("mods"):
            warnings.append(f"{tid}: no module hints — will serialize against every in-flight ticket")

    for w in warnings:
        print(f"WARN  {w}")
    for f_ in failures:
        print(f"FAIL  {f_}")
    if failures:
        print(f"\nNOT RUN-READY: {len(failures)} failure(s). Do not initialize the run; "
              f"groom the backlog (ticket-smith) and re-validate.")
        sys.exit(1)
    print(f"\nRUN-READY: {len(tickets)} tickets, {share:.0%} complex (bar {max_share:.0%}), "
          f"{len(warnings)} warning(s).")


if __name__ == "__main__":
    main()
