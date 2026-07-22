#!/usr/bin/env python3
"""slack_notify.py — optional one-way Slack progress reporting for a run.

The dev-orchestrator fleet posts run progress to Slack when — and only when —
the user has configured it. This is **report-only**: Claude speaks, Slack
listens. There is no reading of replies; clarifying questions still come back
through the Claude session.

Enable it two ways (pick one), secrets always via env, never in config:

  • Incoming webhook (simplest, post-only, no threading):
        export SLACK_WEBHOOK_URL='https://hooks.slack.com/services/…'
  • Bot token (threads a run's updates under one message):
        export SLACK_BOT_TOKEN='xoxb-…'
        # channel from slack.channel in config, or:
        export SLACK_CHANNEL='#dev-runs'      # name or channel id

Config lives in `.dev-orchestrator/config.json` under `"slack"` (all optional):

    "slack": {
      "notify": "milestones",   // off | run | milestones | all   (default milestones)
      "progress_every": 5,      // post a progress line every N done tickets (0 = off)
      "channel": "#dev-runs",   // bot-token transport only
      "thread_per_run": true,   // bot-token transport only
      "username": "dev-orchestrator"
    }

Notification kinds and the level at which each is sent (blocked/escalation/
decision always fire unless notify is off — they are the ones a human must see):

    kind         run   milestones   all
    run           ✓        ✓         ✓
    milestone     ·        ✓         ✓
    progress      ·        ✓         ✓
    ticket        ·        ·         ✓
    blocked       ✓        ✓         ✓
    escalation    ✓        ✓         ✓
    decision      ✓        ✓         ✓

Because gating lives here, callers can *always* invoke `post` with the right
`--kind`; the script decides whether it actually goes out. When Slack is not
configured, or `notify` is `off`, or the kind is below the level, `post` is a
silent no-op that exits 0. Slack must never break a run: any network/API error
is reported on stderr and still exits 0 (fail-open).

Subcommands:
    slack_notify.py enabled                       # {"enabled":bool,"transport":..,"notify":..}
    slack_notify.py post --kind <kind> --text <s> [--thread-file <path>] [--blocks-file <json>]

Deliberately stdlib-only (urllib), like bin/tracker: a hot-path helper that
must start fast under plain python3 with no dependency resolution.

Exit codes: 0 ok / no-op / fail-open, 2 usage error.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

# Web API base; override for tests via SLACK_BOT_API_URL (cf. bin/tracker's LINEAR_API_URL).
SLACK_API = os.environ.get("SLACK_BOT_API_URL", "https://slack.com/api")

# kind -> the lowest notify level at which it is sent. "always" fires for any
# level except off (the things a human must not miss).
KIND_LEVEL = {
    "run": "run",
    "milestone": "milestones",
    "progress": "milestones",
    "ticket": "all",
    "blocked": "always",
    "escalation": "always",
    "decision": "always",
}
LEVELS = ("off", "run", "milestones", "all")
# how much each level covers, as a rank; a kind is sent when its required level
# rank is ≤ the configured level rank.
_LEVEL_RANK = {"run": 1, "milestones": 2, "all": 3}


def usage_error(msg: str):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(2)


def warn(msg: str):
    """Report a Slack failure without failing the run (fail-open)."""
    print(f"slack_notify: {msg}", file=sys.stderr)


def load_config() -> dict:
    path = Path(".dev-orchestrator/config.json")
    if not path.is_file():
        return {}
    try:
        cfg = json.loads(path.read_text()) or {}
    except (OSError, json.JSONDecodeError):
        return {}
    slack = cfg.get("slack")
    return slack if isinstance(slack, dict) else {}


def resolve_notify(cfg: dict) -> str:
    level = str(cfg.get("notify", "milestones")).lower()
    return level if level in LEVELS else "milestones"


def transport(cfg: dict) -> tuple[str | None, dict]:
    """Return (transport, details). transport is 'webhook', 'bot', or None.
    Env is the only source of secrets; webhook wins if both are set (simpler,
    and its presence is the clearer signal of intent)."""
    webhook = os.environ.get("SLACK_WEBHOOK_URL")
    if webhook:
        return "webhook", {"url": webhook}
    token = os.environ.get("SLACK_BOT_TOKEN")
    if token:
        channel = os.environ.get("SLACK_CHANNEL") or cfg.get("channel")
        if not channel:
            return None, {"reason": "SLACK_BOT_TOKEN set but no channel (SLACK_CHANNEL or slack.channel)"}
        return "bot", {"token": token, "channel": channel}
    return None, {"reason": "no SLACK_WEBHOOK_URL or SLACK_BOT_TOKEN in env"}


def should_send(kind: str, level: str) -> bool:
    if level == "off":
        return False
    required = KIND_LEVEL.get(kind)
    if required is None:
        return False
    if required == "always":
        return True
    return _LEVEL_RANK[required] <= _LEVEL_RANK[level]


# ---------------------------------------------------------------------------
# HTTP (stdlib) — one attempt, fail-open. Slack is best-effort telemetry, not
# a gate; retry logic here would just delay a run on a Slack outage.
# ---------------------------------------------------------------------------

def _http_json(url: str, payload: dict, headers: dict) -> dict | None:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json", **headers},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        warn(f"HTTP {e.code} from Slack: {e.read().decode(errors='replace')[:200]}")
        return None
    except urllib.error.URLError as e:
        warn(f"could not reach Slack: {e}")
        return None
    # Webhooks reply with the literal "ok" (not JSON); Web API replies JSON.
    if raw.strip() == "ok":
        return {"ok": True}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        warn(f"unexpected Slack response: {raw[:200]}")
        return None


def post_webhook(url: str, text: str, blocks, username) -> None:
    payload: dict = {"text": text}
    if blocks:
        payload["blocks"] = blocks
    if username:
        payload["username"] = username
    _http_json(url, payload, {})  # webhooks give us no ts to thread on


def post_bot(details: dict, text: str, blocks, username, thread_file) -> None:
    payload: dict = {"channel": details["channel"], "text": text}
    if blocks:
        payload["blocks"] = blocks
    if username:
        payload["username"] = username
    thread_ts = _read_thread(thread_file)
    if thread_ts:
        payload["thread_ts"] = thread_ts
    resp = _http_json(f"{SLACK_API}/chat.postMessage", payload,
                      {"Authorization": f"Bearer {details['token']}"})
    if resp is None:
        return
    if not resp.get("ok"):
        warn(f"Slack API error: {resp.get('error', 'unknown')}")
        return
    # First message of the run seeds the thread; later posts reply under it.
    if thread_file and not thread_ts and resp.get("ts"):
        _write_thread(thread_file, resp["ts"])


def _read_thread(thread_file) -> str | None:
    if not thread_file:
        return None
    try:
        ts = Path(thread_file).read_text().strip()
        return ts or None
    except OSError:
        return None


def _write_thread(thread_file, ts: str) -> None:
    try:
        p = Path(thread_file)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(ts)
    except OSError as e:
        warn(f"could not persist thread ts to {thread_file}: {e}")


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def cmd_enabled(args):
    cfg = load_config()
    level = resolve_notify(cfg)
    trans, details = transport(cfg)
    enabled = bool(trans) and level != "off"
    out = {"enabled": enabled, "transport": trans or "none", "notify": level}
    if not enabled and details.get("reason"):
        out["reason"] = details["reason"]
    print(json.dumps(out, separators=(",", ":")))


def cmd_post(args):
    cfg = load_config()
    level = resolve_notify(cfg)
    if not should_send(args.kind, level):
        return  # silent no-op — kind below configured level, or off
    trans, details = transport(cfg)
    if not trans:
        return  # not configured — silent no-op

    blocks = None
    if args.blocks_file:
        try:
            blocks = json.loads(Path(args.blocks_file).read_text())
        except (OSError, json.JSONDecodeError) as e:
            warn(f"ignoring unreadable blocks file {args.blocks_file}: {e}")

    username = cfg.get("username")
    thread_file = args.thread_file if cfg.get("thread_per_run", True) else None

    if trans == "webhook":
        post_webhook(details["url"], args.text, blocks, username)
    else:
        post_bot(details, args.text, blocks, username, thread_file)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="slack_notify", description="Optional one-way Slack progress reporting.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("enabled", help="report whether Slack is configured and at what level")
    p.set_defaults(func=cmd_enabled)

    p = sub.add_parser("post", help="post a progress message (gated by kind + configured level)")
    p.add_argument("--kind", required=True, choices=sorted(KIND_LEVEL))
    p.add_argument("--text", required=True, help="plain-text message (also the notification fallback)")
    p.add_argument("--thread-file", help="path holding this run's thread ts (bot transport); "
                                         "seeded on first post, replied under thereafter")
    p.add_argument("--blocks-file", help="optional JSON file of Slack Block Kit blocks for rich formatting")
    p.set_defaults(func=cmd_post)
    return ap


def main():
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
