"""validate_plan.py — the plan-time run-readiness gate."""

import json

from conftest import run_script


def validate(tickets, cwd=None):
    return run_script("validate_plan.py", stdin=json.dumps({"tickets": tickets}), cwd=cwd)


def test_run_ready_plan_exits_zero():
    p = validate([
        {"id": "A-1", "tier": "simple", "criteria": True, "mods": ["api"]},
        {"id": "A-2", "tier": "standard", "deps": ["A-1"], "criteria": True, "mods": ["ui"]},
    ])
    assert p.returncode == 0
    assert "RUN-READY" in p.stdout


def test_unknown_dep_fails():
    p = validate([{"id": "A-1", "tier": "simple", "criteria": True, "deps": ["GHOST-9"], "mods": ["x"]}])
    assert p.returncode == 1
    assert "GHOST-9" in p.stdout
    assert "NOT RUN-READY" in p.stdout


def test_missing_tier_and_criteria_fail():
    p = validate([{"id": "A-1", "mods": ["x"]}])
    assert p.returncode == 1
    assert "missing or invalid tier" in p.stdout
    assert "no acceptance criteria" in p.stdout


def test_complex_share_over_bar_fails():
    tickets = [{"id": f"A-{i}", "tier": "complex", "criteria": True, "mods": ["x"]} for i in range(5)]
    tickets += [{"id": f"B-{i}", "tier": "simple", "criteria": True, "mods": ["x"]} for i in range(5)]
    p = validate(tickets)  # 50% complex, default bar 15%
    assert p.returncode == 1
    assert "tier-mix" in p.stdout


def test_complex_share_configurable(repo):
    repo.write_config({"complex_max_share": 0.6})
    tickets = [{"id": f"A-{i}", "tier": "complex", "criteria": True, "mods": ["x"]} for i in range(5)]
    tickets += [{"id": f"B-{i}", "tier": "simple", "criteria": True, "mods": ["x"]} for i in range(5)]
    p = validate(tickets, cwd=repo.path)  # 50% complex, bar raised to 60%
    assert p.returncode == 0, p.stdout


def test_missing_mods_is_warning_not_failure():
    p = validate([{"id": "A-1", "tier": "simple", "criteria": True}])
    assert p.returncode == 0
    assert "no module hints" in p.stdout


def test_empty_input_errors():
    p = run_script("validate_plan.py", stdin=json.dumps({"tickets": []}))
    assert p.returncode != 0
