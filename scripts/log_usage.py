#!/usr/bin/env python3
"""SubagentStop hook: append the finished subagent's actual token usage to the run log.

Reads the hook payload from stdin, parses the subagent transcript (JSONL),
sums per-API-call usage (which is how billing works), and appends one
`agent_usage` event to <run_dir>/log.jsonl.

Fails silent by design: this hook must never block or break a run. If there is
no active run (`.dev-orchestrator/current-run` missing in cwd), it exits 0
without logging so one-off agent use outside orchestration stays noise-free.

Event shape (see docs/log-schema.md):
{
  "ts": "...Z", "event": "agent_usage", "ticket": "ABC-123" | null,
  "agent": "<subagent type if known>", "model": "<model id>",
  "input_tokens": n, "output_tokens": n,
  "cache_creation_tokens": n, "cache_read_tokens": n,
  "turns": n, "duration_s": n, "source": "sidechain" | "main",
  "session_id": "..."
}
"""

import json
import os
import re
import sys
from datetime import datetime, timezone


def read_run_dir(cwd):
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


def warn(run_dir, reason, detail, payload):
    """Surface accounting failures in the run log instead of dying silently.

    The SubagentStop payload field naming (agent_transcript_path vs
    transcript_path) has varied across Claude Code versions — if it changes
    again, this is what makes it visible. report.py prints these warnings.
    """
    event = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "event": "usage_warning",
        "reason": reason,
        "detail": detail,
        "payload_keys": sorted(payload.keys()),
        "hint": "Inspect the SubagentStop hook payload for a renamed transcript-path field; see docs/log-schema.md 'Troubleshooting usage accounting'.",
        "session_id": payload.get("session_id"),
    }
    with open(os.path.join(run_dir, "log.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")


def entry_text(entry):
    """Best-effort extraction of an entry's message text."""
    msg = entry.get("message") or {}
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def parse_ts(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def main():
    payload = json.load(sys.stdin)
    cwd = payload.get("cwd") or os.getcwd()

    run_dir = read_run_dir(cwd)
    if not run_dir:
        return

    transcript_path = (
        payload.get("agent_transcript_path")
        or payload.get("agent_transcript")
        or payload.get("transcript_path")
    )
    if not transcript_path:
        warn(run_dir, "no transcript path in hook payload",
             "expected agent_transcript_path or transcript_path", payload)
        return
    transcript_path = os.path.expanduser(transcript_path)
    if not os.path.isfile(transcript_path):
        warn(run_dir, "transcript path does not exist", transcript_path, payload)
        return

    totals = {"input_tokens": 0, "output_tokens": 0,
              "cache_creation_tokens": 0, "cache_read_tokens": 0}
    turns = 0
    model = None
    ticket = None
    first_ts = last_ts = None
    sidechain = False

    with open(transcript_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("isSidechain"):
                sidechain = True
            ts = parse_ts(entry.get("timestamp"))
            if ts:
                first_ts = first_ts or ts
                last_ts = ts
            etype = entry.get("type")
            if ticket is None and etype == "user":
                m = re.search(r"^TICKET:\s*(\S+)", entry_text(entry), re.MULTILINE)
                if m:
                    ticket = m.group(1)
            if etype == "assistant":
                usage = (entry.get("message") or {}).get("usage") or {}
                if usage:
                    turns += 1
                    totals["input_tokens"] += usage.get("input_tokens", 0) or 0
                    totals["output_tokens"] += usage.get("output_tokens", 0) or 0
                    totals["cache_creation_tokens"] += usage.get("cache_creation_input_tokens", 0) or 0
                    totals["cache_read_tokens"] += usage.get("cache_read_input_tokens", 0) or 0
                model = (entry.get("message") or {}).get("model") or model

    if turns == 0:
        warn(run_dir, "no usage entries parsed from transcript",
             f"{transcript_path} had no assistant messages with usage; it may be "
             "the parent session transcript rather than the subagent's", payload)
        return

    event = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "event": "agent_usage",
        "ticket": ticket,
        "agent": payload.get("agent_type") or payload.get("subagent_type"),
        "model": model,
        **totals,
        "turns": turns,
        "duration_s": round((last_ts - first_ts).total_seconds(), 1) if first_ts and last_ts else None,
        "source": "sidechain" if sidechain else "main",
        "session_id": payload.get("session_id"),
    }

    with open(os.path.join(run_dir, "log.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # never block a run over accounting
    sys.exit(0)
