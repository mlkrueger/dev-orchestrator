"""agent_budget.py — per-subagent tool-call + wall-clock budgets (PreToolUse, all tools)."""

import os
import time

from conftest import run_hook


def call(repo, agent_id="ag-1", agent_type="dev-orchestrator:implementer"):
    payload = {"agent_id": agent_id, "agent_type": agent_type, "cwd": repo.path}
    return run_hook("agent_budget.py", payload, cwd=repo.path)


def backdate_start(repo, agent_id, minutes):
    """Write the agent's first-call timestamp `minutes` into the past."""
    budgets = os.path.join(repo.run_dir, "budgets")
    os.makedirs(budgets, exist_ok=True)
    with open(os.path.join(budgets, f"{agent_id}.start"), "w") as f:
        f.write(str(time.time() - minutes * 60))


def test_under_budget_allows(repo):
    repo.write_config({"tool_call_budgets": {"implementer": 3}})
    for _ in range(3):
        _, parsed = call(repo)
        assert parsed is None


def test_over_budget_denies_with_wrapup(repo):
    repo.write_config({"tool_call_budgets": {"implementer": 2}})
    call(repo); call(repo)  # exhaust
    _, parsed = call(repo)  # 3rd call over budget of 2
    assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "budget" in parsed["hookSpecificOutput"]["permissionDecisionReason"].lower()


def test_budget_exceeded_logged_once(repo):
    repo.write_config({"tool_call_budgets": {"implementer": 1}})
    call(repo)          # 1: allowed
    call(repo); call(repo)  # 2,3: both over budget
    exceeded = [e for e in repo.log_lines() if e.get("event") == "budget_exceeded"]
    assert len(exceeded) == 1, "budget_exceeded must log exactly once per agent"
    assert exceeded[0]["tool_calls"] == 2


def test_parent_session_call_not_budgeted(repo):
    repo.write_config({"tool_call_budgets": {"implementer": 1}})
    payload = {"agent_type": "dev-orchestrator:implementer", "cwd": repo.path}  # no agent_id
    _, parsed = run_hook("agent_budget.py", payload, cwd=repo.path)
    assert parsed is None


def test_non_fleet_agent_not_budgeted(repo):
    repo.write_config({"tool_call_budgets": {"implementer": 1}})
    for _ in range(5):
        _, parsed = call(repo, agent_type="general-purpose")
        assert parsed is None


def test_separate_agents_have_separate_budgets(repo):
    repo.write_config({"tool_call_budgets": {"implementer": 1}})
    call(repo, agent_id="ag-A")
    _, parsed_b = call(repo, agent_id="ag-B")  # different agent, fresh budget
    assert parsed_b is None


# --- wall-clock deadline (stall watchdog) ---

QA = "dev-orchestrator:qa-verifier"


def test_fresh_agent_records_start_and_allows(repo):
    _, parsed = call(repo, agent_type=QA)
    assert parsed is None
    assert os.path.isfile(os.path.join(repo.run_dir, "budgets", "ag-1.start"))


def test_past_deadline_denies_with_stall_wrapup(repo):
    backdate_start(repo, "ag-1", minutes=31)  # qa-verifier default is 30
    _, parsed = call(repo, agent_type=QA)
    reason = parsed["hookSpecificOutput"]["permissionDecisionReason"]
    assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "deadline" in reason.lower()
    assert "stall" in reason.lower()


def test_under_deadline_allows(repo):
    backdate_start(repo, "ag-1", minutes=20)
    _, parsed = call(repo, agent_type=QA)
    assert parsed is None


def test_deadline_exceeded_logged_once(repo):
    backdate_start(repo, "ag-1", minutes=31)
    call(repo, agent_type=QA); call(repo, agent_type=QA)
    events = [e for e in repo.log_lines() if e.get("event") == "deadline_exceeded"]
    assert len(events) == 1, "deadline_exceeded must log exactly once per agent"
    assert events[0]["deadline_min"] == 30
    assert events[0]["elapsed_min"] >= 30


def test_deadline_config_override(repo):
    repo.write_config({"wall_clock_minutes": {"qa-verifier": 60}})
    backdate_start(repo, "ag-1", minutes=45)  # over default 30, under override 60
    _, parsed = call(repo, agent_type=QA)
    assert parsed is None


def test_deadline_zero_disables(repo):
    repo.write_config({"wall_clock_minutes": {"qa-verifier": 0}})
    backdate_start(repo, "ag-1", minutes=600)
    _, parsed = call(repo, agent_type=QA)
    assert parsed is None


def test_orchestrator_exempt_from_deadline_by_default(repo):
    backdate_start(repo, "ag-1", minutes=600)
    _, parsed = call(repo, agent_type="dev-orchestrator:milestone-orchestrator")
    assert parsed is None


def test_simple_gate_is_budgeted(repo):
    repo.write_config({"tool_call_budgets": {"simple-gate": 1}})
    call(repo, agent_type="dev-orchestrator:simple-gate")
    _, parsed = call(repo, agent_type="dev-orchestrator:simple-gate")
    assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"
