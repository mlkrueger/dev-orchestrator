"""slack_notify.py — optional one-way Slack progress reporting, exercised as a
subprocess against a threaded mock Slack server (webhook + Web API).

The core contract is: silent no-op when unconfigured or gated out, fail-open on
any Slack error, and correct kind×level gating. Threading (bot transport) is
verified by seeding a thread file on the first post and asserting later posts
carry thread_ts.
"""

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "scripts", "slack_notify.py")


class Mock:
    def __init__(self):
        self.webhook_posts = []   # list of payload dicts hit on the webhook path
        self.api_posts = []       # list of (payload, auth) hit on chat.postMessage
        self.next_ts = "111.222"  # ts returned by chat.postMessage
        self.api_ok = True        # flip to False to simulate a Slack API error


@pytest.fixture
def slack_server():
    mock = Mock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path.startswith("/webhook"):
                mock.webhook_posts.append(body)
                self._send(200, "ok", raw=True)
                return
            if self.path.endswith("/chat.postMessage"):
                mock.api_posts.append((body, self.headers.get("Authorization")))
                if mock.api_ok:
                    self._send(200, {"ok": True, "ts": mock.next_ts})
                else:
                    self._send(200, {"ok": False, "error": "channel_not_found"})
                return
            self._send(404, {"ok": False, "error": "not_found"})

        def _send(self, code, payload, raw=False):
            self.send_response(code)
            self.send_header("Content-Type", "text/plain" if raw else "application/json")
            self.end_headers()
            self.wfile.write((payload if raw else json.dumps(payload)).encode())

    server = HTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    yield mock, base
    server.shutdown()


def write_config(repo, slack):
    d = repo / ".dev-orchestrator"
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.json").write_text(json.dumps({"slack": slack}))


def run(repo, *args, env_extra=None):
    env = {**os.environ}
    # scrub any real credentials from the ambient environment
    for k in ("SLACK_WEBHOOK_URL", "SLACK_BOT_TOKEN", "SLACK_CHANNEL"):
        env.pop(k, None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run([sys.executable, SCRIPT, *args],
                          capture_output=True, text=True, cwd=str(repo), env=env)


# --- enabled ----------------------------------------------------------------

def test_enabled_false_when_no_env(tmp_path, slack_server):
    write_config(tmp_path, {"notify": "milestones"})
    r = run(tmp_path, "enabled")
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["enabled"] is False and out["transport"] == "none"


def test_enabled_webhook(tmp_path, slack_server):
    _, base = slack_server
    r = run(tmp_path, "enabled", env_extra={"SLACK_WEBHOOK_URL": base + "/webhook"})
    out = json.loads(r.stdout)
    assert out["enabled"] is True and out["transport"] == "webhook"
    assert out["notify"] == "milestones"  # default


def test_enabled_bot_needs_channel(tmp_path, slack_server):
    r = run(tmp_path, "enabled", env_extra={"SLACK_BOT_TOKEN": "xoxb-x"})
    out = json.loads(r.stdout)
    assert out["enabled"] is False       # token but no channel
    assert "channel" in out.get("reason", "")


def test_enabled_off_level_disables(tmp_path, slack_server):
    _, base = slack_server
    write_config(tmp_path, {"notify": "off"})
    r = run(tmp_path, "enabled", env_extra={"SLACK_WEBHOOK_URL": base + "/webhook"})
    out = json.loads(r.stdout)
    assert out["enabled"] is False


# --- no-op paths (must never error) -----------------------------------------

def test_post_noop_when_unconfigured(tmp_path, slack_server):
    mock, _ = slack_server
    r = run(tmp_path, "post", "--kind", "run", "--text", "hi")
    assert r.returncode == 0
    assert mock.webhook_posts == [] and mock.api_posts == []


def test_post_noop_when_off(tmp_path, slack_server):
    mock, base = slack_server
    write_config(tmp_path, {"notify": "off"})
    r = run(tmp_path, "post", "--kind", "blocked", "--text", "x",
            env_extra={"SLACK_WEBHOOK_URL": base + "/webhook"})
    assert r.returncode == 0
    assert mock.webhook_posts == []      # off silences even blocked


# --- gating (kind x level) --------------------------------------------------

def test_milestone_kind_gated_out_at_run_level(tmp_path, slack_server):
    mock, base = slack_server
    write_config(tmp_path, {"notify": "run"})
    run(tmp_path, "post", "--kind", "milestone", "--text", "m",
        env_extra={"SLACK_WEBHOOK_URL": base + "/webhook"})
    assert mock.webhook_posts == []


def test_blocked_always_sends_unless_off(tmp_path, slack_server):
    mock, base = slack_server
    write_config(tmp_path, {"notify": "run"})
    run(tmp_path, "post", "--kind", "blocked", "--text", "b-boom",
        env_extra={"SLACK_WEBHOOK_URL": base + "/webhook"})
    assert len(mock.webhook_posts) == 1
    assert mock.webhook_posts[0]["text"] == "b-boom"


def test_escalation_always_sends(tmp_path, slack_server):
    mock, base = slack_server
    write_config(tmp_path, {"notify": "milestones"})
    run(tmp_path, "post", "--kind", "escalation", "--text", "esc",
        env_extra={"SLACK_WEBHOOK_URL": base + "/webhook"})
    assert len(mock.webhook_posts) == 1


def test_ticket_kind_only_at_all_level(tmp_path, slack_server):
    mock, base = slack_server
    write_config(tmp_path, {"notify": "milestones"})
    run(tmp_path, "post", "--kind", "ticket", "--text", "t",
        env_extra={"SLACK_WEBHOOK_URL": base + "/webhook"})
    assert mock.webhook_posts == []
    write_config(tmp_path, {"notify": "all"})
    run(tmp_path, "post", "--kind", "ticket", "--text", "t2",
        env_extra={"SLACK_WEBHOOK_URL": base + "/webhook"})
    assert len(mock.webhook_posts) == 1


def test_progress_sends_at_milestones_level(tmp_path, slack_server):
    mock, base = slack_server
    write_config(tmp_path, {"notify": "milestones"})
    run(tmp_path, "post", "--kind", "progress", "--text", "5 done",
        env_extra={"SLACK_WEBHOOK_URL": base + "/webhook"})
    assert len(mock.webhook_posts) == 1


# --- bot transport + threading ----------------------------------------------

def test_bot_post_uses_channel_and_auth(tmp_path, slack_server):
    mock, base = slack_server
    write_config(tmp_path, {"notify": "milestones", "channel": "#dev"})
    run(tmp_path, "post", "--kind", "milestone", "--text", "hello",
        env_extra={"SLACK_BOT_TOKEN": "xoxb-tok", "SLACK_API_BASE_UNUSED": "x",
                   "SLACK_BOT_API_URL": base})
    # bot transport hits the API base; we override it via env for the test
    assert len(mock.api_posts) == 1
    payload, auth = mock.api_posts[0]
    assert payload["channel"] == "#dev" and payload["text"] == "hello"
    assert auth == "Bearer xoxb-tok"


def test_bot_threads_run_updates(tmp_path, slack_server):
    mock, base = slack_server
    write_config(tmp_path, {"notify": "all", "channel": "#dev", "thread_per_run": True})
    thread_file = tmp_path / "slack-thread"
    env = {"SLACK_BOT_TOKEN": "xoxb-tok", "SLACK_BOT_API_URL": base}
    run(tmp_path, "post", "--kind", "run", "--text", "start",
        "--thread-file", str(thread_file), env_extra=env)
    run(tmp_path, "post", "--kind", "ticket", "--text", "next",
        "--thread-file", str(thread_file), env_extra=env)
    assert len(mock.api_posts) == 2
    first, second = mock.api_posts[0][0], mock.api_posts[1][0]
    assert "thread_ts" not in first            # first seeds the thread
    assert second["thread_ts"] == mock.next_ts  # second replies under it
    assert thread_file.read_text().strip() == mock.next_ts


# --- fail-open --------------------------------------------------------------

def test_api_error_is_fail_open(tmp_path, slack_server):
    mock, base = slack_server
    mock.api_ok = False
    write_config(tmp_path, {"notify": "milestones", "channel": "#dev"})
    r = run(tmp_path, "post", "--kind", "milestone", "--text", "x",
            env_extra={"SLACK_BOT_TOKEN": "xoxb-tok", "SLACK_BOT_API_URL": base})
    assert r.returncode == 0            # error did not break the run
    assert "channel_not_found" in r.stderr


def test_unreachable_slack_is_fail_open(tmp_path):
    write_config(tmp_path, {"notify": "milestones"})
    # a webhook URL that refuses connections
    r = run(tmp_path, "post", "--kind", "run", "--text", "x",
            env_extra={"SLACK_WEBHOOK_URL": "http://127.0.0.1:1/webhook"})
    assert r.returncode == 0
    assert "slack_notify:" in r.stderr


def test_webhook_wins_over_bot(tmp_path, slack_server):
    mock, base = slack_server
    write_config(tmp_path, {"notify": "milestones", "channel": "#dev"})
    run(tmp_path, "post", "--kind", "milestone", "--text", "x",
        env_extra={"SLACK_WEBHOOK_URL": base + "/webhook",
                   "SLACK_BOT_TOKEN": "xoxb-tok", "SLACK_BOT_API_URL": base})
    assert len(mock.webhook_posts) == 1 and mock.api_posts == []
