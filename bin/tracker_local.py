#!/usr/bin/env python3
"""tracker_local — file-backed tracker backend for the dev-orchestrator fleet.

For users with no external tracker (Linear/Jira/…) installed, or who simply
don't want one, a local **build plan** file is the ticket source. It carries
the canonical ticket model directly, and ticket status is written back into it
— so the plan doubles as the board, and remains an accurate source of truth to
resume from (within a run's environment, exactly like the run log; point
`local.plan` at a committed path to carry status across clones).

`bin/tracker` dispatches here when `.dev-orchestrator/config.json` sets
`{"tracker": "local"}`. This backend emits the SAME compact canonical JSON as
the Linear path, so every caller (orchestrators, `remaining_work.py`, the
resync repair loop) works unchanged. No API key, no MCP session — just a file.

Plan format (`.dev-orchestrator/build-plan.yaml` by default; override with
`local.plan`; `.json` is also accepted, needing no PyYAML). Either a nested
milestones tree or a flat ticket list; both normalize the same:

    team: PAY                     # optional, cosmetic
    milestones:
      - name: Payments v2
        tickets:
          - id: PAY-1
            title: Add the payment model
            status: todo          # todo|in_progress|in_review|done|blocked (default todo)
            tier: standard        # simple|standard|complex
            modules: [api, db]
            resources: [db]
            phase: 1
            depends_on: [PAY-0]   # or blocked_by:
            labels: [Improvement]
            acceptance_criteria:  # optional; else parsed from the description heading
              - persists a payment
            description: |
              Body…
              ## Acceptance criteria
              - persists a payment

Flat form: top-level `tickets:` list, each ticket carrying `milestone: <name>`.

Operations: the autonomous run needs read + status + comment, and those are
implemented fully. `set-status` edits the ticket's `status:` in place
(preserving the rest of your file — comments and all — with a reserialize
fallback for irregular formatting). Structural grooming (`create`, `update`,
`add-dependency`) is done by editing the plan YAML directly, not via the CLI —
those subcommands return a clear pointer here rather than reflowing your file.

Exit codes mirror bin/tracker: 0 ok, 1 runtime error, 2 usage/config error.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

CANONICAL_STATUSES = ("todo", "in_progress", "in_review", "done", "blocked")
DEFAULT_PLAN = ".dev-orchestrator/build-plan.yaml"
COMMENTS_DIR = ".dev-orchestrator/comments"


def usage_error(msg: str):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(2)


def runtime_error(msg: str):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def emit(obj):
    print(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))


# ---------------------------------------------------------------------------
# Plan location + loading
# ---------------------------------------------------------------------------

def plan_path(config: dict) -> Path:
    configured = (config.get("local") or {}).get("plan")
    if configured:
        return Path(configured)
    # default .yaml, but accept a sibling .yml / .json if that's what exists
    for candidate in (DEFAULT_PLAN, ".dev-orchestrator/build-plan.yml",
                      ".dev-orchestrator/build-plan.json"):
        if Path(candidate).is_file():
            return Path(candidate)
    return Path(DEFAULT_PLAN)


def load_doc(path: Path) -> dict:
    if not path.is_file():
        runtime_error(
            f"local tracker: no build plan at {path} — create it or set "
            f"local.plan in .dev-orchestrator/config.json (see docs/local-tracker.md)"
        )
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            runtime_error(f"parsing {path}: {e}")
    else:
        try:
            import yaml  # lazy: only YAML plans need it; .json plans are stdlib
        except ImportError:
            runtime_error(
                f"local tracker: reading {path} needs PyYAML (pip install pyyaml), "
                f"or use a .json build plan (no dependency)."
            )
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as e:  # type: ignore[name-defined]
            runtime_error(f"parsing {path}: {e}")
    if not isinstance(data, dict):
        runtime_error(f"{path}: build plan must be a mapping with 'milestones' or 'tickets'")
    return data


def iter_tickets(doc: dict):
    """Yield (ticket_dict, milestone_name) across both plan shapes."""
    milestones = doc.get("milestones")
    if isinstance(milestones, list):
        for ms in milestones:
            if not isinstance(ms, dict):
                continue
            name = ms.get("name")
            for t in ms.get("tickets") or []:
                if isinstance(t, dict):
                    yield t, name
    for t in doc.get("tickets") or []:  # flat form (also allowed alongside)
        if isinstance(t, dict):
            yield t, t.get("milestone")


def find_ticket(doc: dict, ticket_id: str):
    for t, ms in iter_tickets(doc):
        if str(t.get("id")) == ticket_id:
            return t, ms
    return None, None


# ---------------------------------------------------------------------------
# Canonical rendering — identical shape to bin/tracker's Linear path
# ---------------------------------------------------------------------------

def _parse_acceptance_criteria(description: str) -> list[str]:
    out, in_section = [], False
    for line in (description or "").splitlines():
        if re.match(r"^#{1,6}\s", line):
            in_section = bool(re.search(r"acceptance criteria", line, re.IGNORECASE))
            continue
        if in_section:
            m = re.match(r"^\s*[-*]\s+(?:\[[ xX]\]\s+)?(.*\S)", line)
            if m:
                out.append(m.group(1).strip())
    return out


def _as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(x) for x in value]
    return [str(value)]


def canonical_ticket(ticket: dict, milestone, *, full: bool, comments=None) -> dict:
    status = ticket.get("status") or "todo"
    modules = _as_list(ticket.get("modules"))
    out = {
        "id": str(ticket.get("id")),
        "title": ticket.get("title"),
        "status": status,
        "tier": ticket.get("tier"),
        "modules": modules,
        "resources": _as_list(ticket.get("resources")),
        "milestone": milestone,
    }
    if full:
        # Surface phase as a `phase:K` label — the orchestrator reads phase from
        # labels exactly as it does on Linear, so its logic is backend-agnostic.
        labels = _as_list(ticket.get("labels"))
        if ticket.get("phase") is not None:
            labels = labels + [f"phase:{ticket['phase']}"]
        deps = _as_list(ticket.get("depends_on")) or _as_list(ticket.get("blocked_by"))
        description = ticket.get("description") or ""
        criteria = _as_list(ticket.get("acceptance_criteria")) or _parse_acceptance_criteria(description)
        out["labels"] = labels
        out["dependencies"] = sorted(set(deps))
        out["acceptance_criteria"] = criteria
        out["description"] = description
    if comments is not None:
        out["comments"] = comments
    return out


def label_matches(ticket: dict, wanted: str) -> bool:
    """Match a `--label` filter against a ticket's fields, treating the managed
    field values (tier/mod/resource/phase) as pseudo-labels so `--label phase:2`
    et al. work the same way they do against Linear labels."""
    if wanted.startswith("tier:"):
        return ticket.get("tier") == wanted.split(":", 1)[1]
    if wanted.startswith("mod:"):
        return wanted.split(":", 1)[1] in _as_list(ticket.get("modules"))
    if wanted.startswith("resource:"):
        return wanted.split(":", 1)[1] in _as_list(ticket.get("resources"))
    if wanted.startswith("phase:"):
        return ticket.get("phase") is not None and str(ticket["phase"]) == wanted.split(":", 1)[1]
    return wanted in _as_list(ticket.get("labels"))


# ---------------------------------------------------------------------------
# Status write-back — surgical in-place edit, reserialize fallback
# ---------------------------------------------------------------------------

def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _surgical_set_status(text: str, ticket_id: str, status: str) -> str | None:
    """Replace (or insert) the `status:` field of the record carrying
    `id: <ticket_id>`, touching nothing else in the file. Returns the new text,
    or None if the record's bounds can't be located (caller reserializes).

    The record is bounded by its list-item marker (`- `), not by raw indent: a
    preceding ticket's `description: |` block is indented as deep as a field, so
    an indent-only scan would bleed across records. We find this record's opening
    `- ` marker at/above the id line, then stop at the next line that dedents to
    the marker column or shallower (the next sibling, or out of the list)."""
    lines = text.splitlines(keepends=True)
    id_pat = re.compile(r"^(\s*)(?:-\s+)?id:\s*['\"]?" + re.escape(ticket_id) + r"['\"]?\s*(?:#.*)?$")
    id_idx = next((i for i, ln in enumerate(lines) if id_pat.match(ln)), None)
    if id_idx is None:
        return None
    key_col = lines[id_idx].index("id:")  # field column (past the dash if inline)

    # this record's opening `- ` marker: nearest list item at/above the id line
    marker_idx = marker_col = None
    for i in range(id_idx, -1, -1):
        stripped = lines[i].lstrip(" ")
        indent = _indent_of(lines[i])
        if stripped.startswith("- ") and indent < key_col:
            marker_idx, marker_col = i, indent
            break
        if stripped and not stripped.startswith("#") and indent < key_col:
            return None  # dedented out of the record without finding a marker
    if marker_idx is None:
        return None

    # record ends at the first later line that dedents to the marker or beyond
    end = len(lines)
    for i in range(marker_idx + 1, len(lines)):
        stripped = lines[i].lstrip(" ")
        if not stripped or stripped.startswith("#"):
            continue
        if _indent_of(lines[i]) <= marker_col:
            end = i
            break

    status_pat = re.compile(r"^" + (" " * key_col) + r"status:\s*.*$")
    for i in range(marker_idx, end):
        if status_pat.match(lines[i]):
            nl = "\n" if lines[i].endswith("\n") else ""
            lines[i] = f"{' ' * key_col}status: {status}{nl}"
            return "".join(lines)

    # no status field on this record — insert one right after the id line
    nl = "\n" if lines[id_idx].endswith("\n") else "\n"
    lines.insert(id_idx + 1, f"{' ' * key_col}status: {status}{nl}")
    return "".join(lines)


def write_status(path: Path, doc: dict, ticket: dict, ticket_id: str, status: str):
    text = path.read_text(encoding="utf-8")
    new_text = None
    if path.suffix != ".json":
        new_text = _surgical_set_status(text, ticket_id, status)
    if new_text is not None:
        path.write_text(new_text, encoding="utf-8")
        # verify the edit actually took; otherwise fall through to reserialize
        if str((find_ticket(load_doc(path), ticket_id)[0] or {}).get("status")) == status:
            return
    # reserialize path (JSON plans, or a surgical edit that didn't verify)
    ticket["status"] = status
    _dump(path, doc)


def _dump(path: Path, doc: dict):
    if path.suffix == ".json":
        path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return
    try:
        import yaml
    except ImportError:
        runtime_error("writing a YAML plan needs PyYAML (pip install pyyaml)")
    path.write_text(
        yaml.safe_dump(doc, sort_keys=False, default_flow_style=False, allow_unicode=True),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Comments — a companion store, so status writes never reflow the plan
# ---------------------------------------------------------------------------

def _comments_file(ticket_id: str) -> Path:
    return Path(COMMENTS_DIR) / f"{ticket_id}.jsonl"


def read_comments(ticket_id: str) -> list[dict]:
    path = _comments_file(ticket_id)
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        out.append({"user": row.get("user"), "body": row.get("body", "")})
    return out


def append_comment(ticket_id: str, body: str):
    path = _comments_file(ticket_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"user": "dev-orchestrator", "body": body}, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def cmd_list(args, config):
    doc = load_doc(plan_path(config))
    rows = []
    for ticket, ms in iter_tickets(doc):
        if args.milestone is not None and ms != args.milestone:
            continue
        if args.status and (ticket.get("status") or "todo") != args.status:
            continue
        if args.label and not label_matches(ticket, args.label):
            continue
        rows.append(canonical_ticket(ticket, ms, full=False))
    emit(rows)


def cmd_get(args, config):
    doc = load_doc(plan_path(config))
    ticket, ms = find_ticket(doc, args.id)
    if ticket is None:
        runtime_error(f"ticket {args.id!r} not found in the local build plan")
    comments = read_comments(args.id) if args.comments else None
    emit(canonical_ticket(ticket, ms, full=True, comments=comments))


def cmd_set_status(args, config):
    if args.status not in CANONICAL_STATUSES:
        usage_error(f"status must be one of {', '.join(CANONICAL_STATUSES)}")
    path = plan_path(config)
    doc = load_doc(path)
    ticket, _ = find_ticket(doc, args.id)
    if ticket is None:
        runtime_error(f"ticket {args.id!r} not found in the local build plan")
    write_status(path, doc, ticket, args.id, args.status)
    emit({"id": args.id, "status": args.status, "state": f"local:{args.status}"})


def cmd_comment(args, config):
    doc = load_doc(plan_path(config))
    ticket, _ = find_ticket(doc, args.id)
    if ticket is None:
        runtime_error(f"ticket {args.id!r} not found in the local build plan")
    body = Path(args.body_file).read_text(encoding="utf-8")
    append_comment(args.id, body)
    emit({"id": args.id, "commented": True})


def _structural_unsupported(op: str):
    runtime_error(
        f"local tracker: '{op}' is not a CLI operation — groom the local build plan "
        f"by editing the YAML directly (add/edit tickets, milestones, depends_on). "
        f"The CLI provides read (list/get), set-status, and comment. See docs/local-tracker.md."
    )


def dispatch_local(args, config: dict):
    """Entry point from bin/tracker when tracker == 'local'."""
    if args.cmd == "list":
        cmd_list(args, config)
    elif args.cmd == "get":
        cmd_get(args, config)
    elif args.cmd == "set-status":
        cmd_set_status(args, config)
    elif args.cmd == "comment":
        cmd_comment(args, config)
    elif args.cmd in ("create", "update", "add-dependency"):
        _structural_unsupported(args.cmd)
    else:
        usage_error(f"unknown subcommand {args.cmd!r}")
