"""log_usage.py — SubagentStop hook: parse a subagent transcript into an
agent_usage event on the run log."""

import json

from conftest import run_hook


def write_transcript(tmp_path, entries):
    p = tmp_path / "transcript.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in entries))
    return str(p)


def assistant(model, inp=0, out=0, cw=0, cr=0, ts="2026-07-15T10:00:00Z"):
    return {"type": "assistant", "timestamp": ts, "message": {
        "model": model,
        "usage": {"input_tokens": inp, "output_tokens": out,
                  "cache_creation_input_tokens": cw, "cache_read_input_tokens": cr},
    }}


def user(text, ts="2026-07-15T09:59:00Z"):
    return {"type": "user", "timestamp": ts, "isSidechain": True,
            "message": {"content": text}}


def test_parses_usage_and_ticket(repo, tmp_path):
    tp = write_transcript(tmp_path, [
        user("TICKET: A-1\nBuild the thing"),
        assistant("claude-sonnet-5", inp=100, out=50, cw=10, cr=200, ts="2026-07-15T10:00:00Z"),
        assistant("claude-sonnet-5", inp=100, out=50, ts="2026-07-15T10:01:00Z"),
    ])
    payload = {"cwd": repo.path, "agent_transcript_path": tp,
               "agent_type": "dev-orchestrator:implementer", "session_id": "s1"}
    run_hook("log_usage.py", payload, cwd=repo.path)

    usage = [e for e in repo.log_lines() if e.get("event") == "agent_usage"]
    assert len(usage) == 1
    u = usage[0]
    assert u["ticket"] == "A-1"
    assert u["model"] == "claude-sonnet-5"
    assert u["input_tokens"] == 200
    assert u["output_tokens"] == 100
    assert u["cache_creation_tokens"] == 10
    assert u["cache_read_tokens"] == 200
    assert u["turns"] == 2
    assert u["source"] == "sidechain"


def test_parses_milestone_line(repo, tmp_path):
    tp = write_transcript(tmp_path, [
        user("MILESTONE: API layer\nRun the milestone"),
        assistant("claude-opus-4-8", inp=10, out=5),
    ])
    payload = {"cwd": repo.path, "agent_transcript_path": tp,
               "agent_type": "dev-orchestrator:milestone-orchestrator"}
    run_hook("log_usage.py", payload, cwd=repo.path)
    u = [e for e in repo.log_lines() if e.get("event") == "agent_usage"][0]
    assert u["milestone"] == "API layer"


def test_missing_transcript_path_warns(repo):
    payload = {"cwd": repo.path, "agent_type": "dev-orchestrator:implementer"}
    run_hook("log_usage.py", payload, cwd=repo.path)
    warns = [e for e in repo.log_lines() if e.get("event") == "usage_warning"]
    assert warns and "no transcript path" in warns[0]["reason"]


def test_no_usage_entries_warns(repo, tmp_path):
    tp = write_transcript(tmp_path, [user("TICKET: A-1\nhi")])  # no assistant usage
    payload = {"cwd": repo.path, "agent_transcript_path": tp,
               "agent_type": "dev-orchestrator:implementer"}
    run_hook("log_usage.py", payload, cwd=repo.path)
    warns = [e for e in repo.log_lines() if e.get("event") == "usage_warning"]
    assert warns and "no usage entries" in warns[0]["reason"]


def test_no_active_run_is_silent(tmp_path):
    """cwd with no current-run pointer -> hook no-ops, no crash."""
    tp = write_transcript(tmp_path, [assistant("claude-sonnet-5", inp=1, out=1)])
    payload = {"cwd": str(tmp_path), "agent_transcript_path": tp}
    proc, parsed = run_hook("log_usage.py", payload, cwd=str(tmp_path))
    assert proc.returncode == 0
    assert parsed is None
