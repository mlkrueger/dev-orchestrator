"""ensure_env.py — run-preflight that installs the pre-commit checks gate
via core.hooksPath, backed by the .dev-orchestrator/environment.json ledger."""

import json
import os
import subprocess

import pytest

from conftest import run_script


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


@pytest.fixture
def git_repo(tmp_path):
    d = str(tmp_path)
    git(d, "init")
    git(d, "config", "user.email", "t@t.co")
    git(d, "config", "user.name", "t")
    return d


def env(cwd, *args):
    return run_script("ensure_env.py", "--json", *args, cwd=cwd)


def result(proc):
    return json.loads(proc.stdout.strip())


def ledger(cwd):
    p = os.path.join(cwd, ".dev-orchestrator", "environment.json")
    return json.load(open(p)) if os.path.isfile(p) else None


def test_installs_gate_for_node_repo(git_repo):
    (open(os.path.join(git_repo, "package.json"), "w")
     .write(json.dumps({"scripts": {"ci": "echo ok"}})))
    r = result(env(git_repo))
    assert r["status"] == "installed"
    assert r["checks_command"] == "npm run ci"
    assert os.access(os.path.join(git_repo, ".githooks", "pre-commit"), os.X_OK)
    assert git(git_repo, "config", "--get", "core.hooksPath").stdout.strip() == ".githooks"
    assert ledger(git_repo)["setup"]["git_hooks"]["installed"] is True


def test_fast_path_on_second_run(git_repo):
    open(os.path.join(git_repo, "package.json"), "w").write(json.dumps({"scripts": {"ci": "echo ok"}}))
    env(git_repo)
    r = result(env(git_repo))
    assert r["status"] == "ok"  # fast path, not reinstalled


def test_drift_is_detected_and_repaired(git_repo):
    open(os.path.join(git_repo, "package.json"), "w").write(json.dumps({"scripts": {"ci": "echo ok"}}))
    env(git_repo)
    hook = os.path.join(git_repo, ".githooks", "pre-commit")
    with open(hook, "a") as f:
        f.write("\n# tampered\n")
    r = result(env(git_repo))
    assert r["status"] == "installed" and r["drift"] is True


def test_command_change_regenerates(git_repo):
    open(os.path.join(git_repo, "package.json"), "w").write(json.dumps({"scripts": {"ci": "echo ok"}}))
    env(git_repo)
    r = result(env(git_repo, "--checks-command", "make check"))
    assert r["status"] == "installed"
    assert r["checks_command"] == "make check"
    assert "make check" in open(os.path.join(git_repo, ".githooks", "pre-commit")).read()


def test_needs_command_when_undetectable(git_repo):
    p = env(git_repo)
    assert p.returncode == 2
    assert result(p)["status"] == "needs_command"


def test_non_git_dir_skips(tmp_path):
    p = env(str(tmp_path))
    assert p.returncode == 0
    assert result(p)["status"] == "skipped"


def test_python_repo_detected(git_repo):
    os.mkdir(os.path.join(git_repo, "tests"))
    r = result(env(git_repo))
    assert "pytest" in r["checks_command"]


def test_installed_hook_blocks_failing_commit(git_repo):
    open(os.path.join(git_repo, "package.json"), "w").write(
        json.dumps({"scripts": {"ci": "exit 1"}}))
    env(git_repo)
    git(git_repo, "add", "-A")
    c = git(git_repo, "commit", "-m", "x")
    assert c.returncode != 0, "pre-commit gate must block a commit whose checks fail"


def test_check_mode_writes_nothing(git_repo):
    open(os.path.join(git_repo, "package.json"), "w").write(json.dumps({"scripts": {"ci": "echo ok"}}))
    r = result(env(git_repo, "--check"))
    assert r["status"] == "would_install"
    assert not os.path.exists(os.path.join(git_repo, ".githooks", "pre-commit"))
    assert git(git_repo, "config", "--get", "core.hooksPath").stdout.strip() == ""
    assert ledger(git_repo) is None


def test_check_mode_reports_ok_when_current(git_repo):
    open(os.path.join(git_repo, "package.json"), "w").write(json.dumps({"scripts": {"ci": "echo ok"}}))
    env(git_repo)
    assert result(env(git_repo, "--check"))["status"] == "ok"


def test_ledger_override_used_without_flag(git_repo):
    os.makedirs(os.path.join(git_repo, ".dev-orchestrator"))
    open(os.path.join(git_repo, ".dev-orchestrator", "environment.json"), "w").write(
        json.dumps({"schema": 1, "checks_command": "just ci"}))
    r = result(env(git_repo))
    assert r["checks_command"] == "just ci"
