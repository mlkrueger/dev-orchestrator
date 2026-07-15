"""report.py — aggregate a run log into a markdown postmortem."""

import json

from conftest import run_script


def write_log(repo, events):
    with open(f"{repo.run_dir}/log.jsonl", "w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


def usage_event(**kw):
    base = {"event": "agent_usage", "ts": "2026-07-15T10:00:00Z",
            "input_tokens": 0, "output_tokens": 0,
            "cache_creation_tokens": 0, "cache_read_tokens": 0, "turns": 1}
    base.update(kw)
    return base


def report(repo):
    return run_script("report.py", cwd=repo.path)


def test_report_sums_cost_from_pricing(repo):
    # sonnet: input $3/Mtok, output $15/Mtok. 1M input + 1M output = $18.
    write_log(repo, [usage_event(ticket="A-1", agent="dev-orchestrator:implementer",
                                 model="claude-sonnet-5",
                                 input_tokens=1_000_000, output_tokens=1_000_000)])
    p = report(repo)
    assert p.returncode == 0
    assert "$18.00" in p.stdout
    assert "A-1" in p.stdout


def test_report_counts_gate_failures(repo):
    write_log(repo, [
        usage_event(ticket="A-1", model="claude-sonnet-5"),
        {"event": "gate", "ts": "2026-07-15T10:00:00Z", "gate": "review", "verdict": "REQUEST_CHANGES"},
        {"event": "gate", "ts": "2026-07-15T10:01:00Z", "gate": "review", "verdict": "APPROVE"},
    ])
    p = report(repo)
    assert "review: 1 of 2" in p.stdout


def test_report_shows_escalations(repo):
    write_log(repo, [
        usage_event(ticket="A-1", model="claude-sonnet-5"),
        {"event": "escalate", "ts": "2026-07-15T10:00:00Z", "ticket": "A-1", "from": "sonnet", "to": "opus"},
    ])
    p = report(repo)
    assert "A-1: sonnet→opus" in p.stdout


def test_report_flags_budget_stopped_agents(repo):
    write_log(repo, [
        usage_event(ticket="A-1", model="claude-sonnet-5"),
        {"event": "budget_exceeded", "ts": "2026-07-15T10:00:00Z",
         "agent": "dev-orchestrator:implementer", "tool_calls": 151},
    ])
    p = report(repo)
    assert "Budget-stopped agents" in p.stdout
    assert "implementer@151" in p.stdout


def test_report_warns_on_dispatch_without_usage(repo):
    write_log(repo, [{"event": "dispatch", "ts": "2026-07-15T10:00:00Z"}])
    p = report(repo)
    assert "warning" in p.stdout.lower()


def test_unknown_model_marked_unpriced(repo):
    write_log(repo, [usage_event(ticket="A-1", model="some-unknown-model", input_tokens=1000)])
    p = report(repo)
    assert "unpriced" in p.stdout.lower() or "unknown" in p.stdout.lower()

def test_report_surfaces_orchestrator_respawns(repo):
    write_log(repo, [
        usage_event(ticket="A-1", model="claude-sonnet-5"),
        {"event": "milestone_continue", "ts": "2026-07-15T10:05:00Z", "milestone": "M1", "remaining": 6},
        {"event": "milestone_continue", "ts": "2026-07-15T10:20:00Z", "milestone": "M1", "remaining": 2},
    ])
    p = report(repo)
    assert p.returncode == 0
    assert "Orchestrator respawns (context-budget):** 2" in p.stdout


def test_report_no_respawn_line_when_none(repo):
    write_log(repo, [usage_event(ticket="A-1", model="claude-sonnet-5")])
    p = report(repo)
    assert p.returncode == 0
    assert "respawns" not in p.stdout.lower()
