"""agent_budget.py — per-subagent tool-call budget (PreToolUse, all tools)."""

from conftest import run_hook


def call(repo, agent_id="ag-1", agent_type="dev-orchestrator:implementer"):
    payload = {"agent_id": agent_id, "agent_type": agent_type, "cwd": repo.path}
    return run_hook("agent_budget.py", payload, cwd=repo.path)


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
