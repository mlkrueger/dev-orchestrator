"""dispatch_policy.py — PreToolUse deny rules for fleet Agent dispatches."""

from conftest import run_hook


def dispatch(repo, subagent_type, model="sonnet", prompt="", tool_name="Agent"):
    payload = {
        "tool_name": tool_name,
        "cwd": repo.path,
        "tool_input": {"subagent_type": subagent_type, "model": model, "prompt": prompt},
    }
    return run_hook("dispatch_policy.py", payload, cwd=repo.path)


def is_deny(parsed):
    return bool(parsed) and parsed["hookSpecificOutput"]["permissionDecision"] == "deny"


# A TICKET_FILE path inside the fixture's active run dir (repo.run_rel).
TF = ".dev-orchestrator/runs/run-001/tickets/A-1.md"


def ticket_prompt(extra=""):
    """A well-formed fleet-ticket dispatch prompt: TICKET + TICKET_FILE lines."""
    p = f"TICKET: A-1\nTICKET_FILE: {TF}"
    return f"{p}\n{extra}" if extra else p


def test_implementer_missing_ticket_line_denied(repo):
    _, parsed = dispatch(repo, "dev-orchestrator:implementer", prompt="just build it")
    assert is_deny(parsed)
    assert "TICKET:" in parsed["hookSpecificOutput"]["permissionDecisionReason"]


def test_implementer_with_ticket_and_file_lines_allowed(repo):
    _, parsed = dispatch(repo, "dev-orchestrator:implementer", prompt=ticket_prompt("build it"))
    assert parsed is None


def test_ticket_line_but_no_ticket_file_denied(repo):
    _, parsed = dispatch(repo, "dev-orchestrator:implementer", prompt="TICKET: A-1\nbuild it")
    assert is_deny(parsed)
    assert "TICKET_FILE" in parsed["hookSpecificOutput"]["permissionDecisionReason"]


def test_ticket_file_outside_run_dir_denied(repo):
    _, parsed = dispatch(repo, "dev-orchestrator:qa-verifier",
                         prompt="TICKET: A-1\nTICKET_FILE: /tmp/A-1.md\ncheck it")
    assert is_deny(parsed)
    assert "run dir" in parsed["hookSpecificOutput"]["permissionDecisionReason"]


def test_ticket_file_absolute_into_run_dir_allowed(repo):
    abs_tf = f"{repo.run_dir}/tickets/A-1.md"  # absolute, still under .dev-orchestrator/runs/
    _, parsed = dispatch(repo, "dev-orchestrator:code-reviewer",
                         prompt=f"TICKET: A-1\nTICKET_FILE: {abs_tf}\nreview it")
    assert parsed is None


def test_ticket_file_required_for_every_gate(repo):
    for gate in ("scope-guardian", "qa-verifier", "code-reviewer", "simple-gate"):
        _, parsed = dispatch(repo, f"dev-orchestrator:{gate}", prompt="TICKET: A-1\ngo")
        assert is_deny(parsed), f"{gate} missing TICKET_FILE should be denied"
        assert "TICKET_FILE" in parsed["hookSpecificOutput"]["permissionDecisionReason"]


def test_simple_gate_well_formed_dispatch_allowed(repo):
    _, parsed = dispatch(repo, "dev-orchestrator:simple-gate", prompt=ticket_prompt("verify+review"))
    assert parsed is None


def test_opus_implementer_without_justification_denied(repo):
    _, parsed = dispatch(repo, "dev-orchestrator:implementer", model="opus",
                         prompt=ticket_prompt("go"))
    assert is_deny(parsed)
    assert "justification" in parsed["hookSpecificOutput"]["permissionDecisionReason"]


def test_opus_implementer_with_tier_complex_allowed(repo):
    _, parsed = dispatch(repo, "dev-orchestrator:implementer", model="opus",
                         prompt=ticket_prompt("TIER: complex\ngo"))
    assert parsed is None


def test_opus_implementer_with_escalated_allowed(repo):
    _, parsed = dispatch(repo, "dev-orchestrator:implementer", model="opus",
                         prompt=ticket_prompt("ESCALATED: sonnet\ngo"))
    assert parsed is None


def test_fable_model_denied_for_fleet(repo):
    _, parsed = dispatch(repo, "dev-orchestrator:implementer", model="claude-fable-5",
                         prompt="TICKET: A-1\ngo")
    assert is_deny(parsed)
    assert "Fable" in parsed["hookSpecificOutput"]["permissionDecisionReason"]


def test_milestone_orchestrator_needs_milestone_line(repo):
    _, parsed = dispatch(repo, "dev-orchestrator:milestone-orchestrator", prompt="go build")
    assert is_deny(parsed)
    assert "MILESTONE:" in parsed["hookSpecificOutput"]["permissionDecisionReason"]


def test_non_fleet_agent_untouched(repo):
    _, parsed = dispatch(repo, "general-purpose", prompt="anything")
    assert parsed is None


def test_inert_when_no_active_run(repo):
    repo.set_pointer(".dev-orchestrator/runs/GONE")  # run not active
    _, parsed = dispatch(repo, "dev-orchestrator:implementer", prompt="no ticket line")
    assert parsed is None, "policy must not enforce when no run is active"


def test_non_agent_tool_ignored(repo):
    _, parsed = dispatch(repo, "dev-orchestrator:implementer", prompt="x", tool_name="Bash")
    assert parsed is None
