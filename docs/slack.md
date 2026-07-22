# Slack progress reporting (optional)

dev-orchestrator can mirror a run's progress to Slack so you can follow an
unattended run from your phone. It is **report-only**: Claude posts, Slack
listens. Clarifying questions and decisions still come back through the Claude
session — the orchestrator does not read Slack replies.

When Slack is not configured, everything below is a silent no-op — the run
behaves exactly as before.

## Setup

Pick one transport. Secrets always live in the environment, never in
`config.json`.

### Option A — Incoming webhook (simplest, post-only)

1. Create an [incoming webhook](https://api.slack.com/messaging/webhooks) for
   the channel you want. Slack gives you a URL.
2. Export it where the run executes:
   ```bash
   export SLACK_WEBHOOK_URL='https://hooks.slack.com/services/…'
   ```

Webhooks post to their fixed channel and cannot thread, so each update is a
separate message.

### Option B — Bot token (threads a run's updates)

1. Create a Slack app with a bot token (`xoxb-…`) that has `chat:write`, and
   invite it to the channel.
2. Export the token and the target channel (name or id):
   ```bash
   export SLACK_BOT_TOKEN='xoxb-…'
   export SLACK_CHANNEL='#dev-runs'      # or set slack.channel in config.json
   ```

With a bot token, all of a run's updates thread under the first message, so a
run is one collapsed Slack thread instead of a wall of posts.

If both are set, the webhook wins.

## Configuration

All optional, in `.dev-orchestrator/config.json` under `"slack"`:

```json
{
  "slack": {
    "notify": "milestones",
    "progress_every": 5,
    "channel": "#dev-runs",
    "thread_per_run": true,
    "username": "dev-orchestrator"
  }
}
```

| Key | Default | Meaning |
|---|---|---|
| `notify` | `milestones` | Verbosity: `off` \| `run` \| `milestones` \| `all` (see table below). |
| `progress_every` | `5` | Post a progress line every N tickets closed within a milestone (advisory to the orchestrator; `0` disables the periodic line — milestone-end still posts). |
| `channel` | — | Target channel for the bot-token transport (env `SLACK_CHANNEL` overrides). Ignored for webhooks. |
| `thread_per_run` | `true` | Thread a run's updates under one message (bot transport only). |
| `username` | — | Override the display name of the posting bot. |

## What gets posted, at each verbosity

Blocked tickets, escalations, and decisions-needed **always** post unless
`notify` is `off` — they are the events a human must not miss.

| kind | `run` | `milestones` (default) | `all` |
|---|:--:|:--:|:--:|
| run start / end | ✓ | ✓ | ✓ |
| milestone start / end | · | ✓ | ✓ |
| progress (every N tickets) | · | ✓ | ✓ |
| per-ticket done | · | · | ✓ |
| ticket **blocked** | ✓ | ✓ | ✓ |
| tier **escalation** | ✓ | ✓ | ✓ |
| **decision** needed | ✓ | ✓ | ✓ |

## Guarantees

- **Fail-open.** Any Slack error (bad token, network blip, rate limit) is
  reported on stderr and the run continues — Slack is telemetry, never a gate.
  Posts are single-attempt; the orchestrator never retries or waits on Slack.
- **No-op when unconfigured.** With no `SLACK_WEBHOOK_URL`/`SLACK_BOT_TOKEN`,
  or `notify: off`, `slack_notify.py post` exits 0 having done nothing, so the
  orchestrator can call it unconditionally.
- **Report-only.** The orchestrator never reads Slack. Decisions and
  clarifying questions surface through the Claude session, as always.

## Under the hood

`scripts/slack_notify.py` is a stdlib-only helper (no dependencies, like
`bin/tracker`):

```
slack_notify.py enabled                          # {"enabled":bool,"transport":..,"notify":..}
slack_notify.py post --kind <kind> --text <s> [--thread-file <path>] [--blocks-file <json>]
```

`enabled` lets the orchestrator check once per run and skip all Slack steps if
off. `post` gates the message by `--kind` against the configured level, so
callers always call it and the script decides whether it goes out. `--kind` is
one of `run`, `milestone`, `progress`, `ticket`, `blocked`, `escalation`,
`decision`.
