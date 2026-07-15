"""Shared fixtures/helpers for the dev-orchestrator script + hook tests.

Every script is exercised the way Claude Code actually runs it: as a
subprocess, fed a crafted stdin payload (hooks) or argv/stdin (CLI scripts),
with cwd pointed at an isolated temp repo. Nothing imports the scripts into
the test process, so os.getcwd()/PLUGIN_ROOT resolution is tested for real.
"""

import json
import os
import shutil
import subprocess
import sys

import pytest

SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
PLUGIN_ROOT = os.path.dirname(SCRIPTS)


def run_script(name, *args, stdin=None, cwd=None):
    """Run scripts/<name> as a subprocess. Returns CompletedProcess."""
    return subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, name), *args],
        input=stdin,
        capture_output=True,
        text=True,
        cwd=cwd,
    )


def run_hook(name, payload, cwd=None):
    """Run a hook script with a JSON payload on stdin; parse stdout as JSON if present."""
    proc = run_script(name, stdin=json.dumps(payload), cwd=cwd)
    out = proc.stdout.strip()
    parsed = json.loads(out) if out else None
    return proc, parsed


@pytest.fixture
def repo(tmp_path):
    """An isolated target repo with an active run dir and current-run pointer.

    Returns an object exposing:
      .path        repo root (str)
      .run_dir     active run dir (str, relative form under the repo)
      .set_pointer(value)   overwrite current-run with an arbitrary string
      .make_run(id)         create another run dir, return its abs path
      .log_lines()          parsed list of the run's log.jsonl events
      .write_config(obj)    write .dev-orchestrator/config.json
    """
    base = tmp_path / ".dev-orchestrator"
    runs = base / "runs"
    active = runs / "run-001"
    active.mkdir(parents=True)
    (base / "current-run").write_text(".dev-orchestrator/runs/run-001")

    repo_path = str(tmp_path)
    active_dir = str(active)

    class Repo:
        path = repo_path
        run_dir = active_dir
        run_rel = ".dev-orchestrator/runs/run-001"

        def set_pointer(self, value):
            (base / "current-run").write_text(value)

        def make_run(self, rid):
            d = runs / rid
            d.mkdir(parents=True, exist_ok=True)
            return str(d)

        def log_lines(self):
            p = active / "log.jsonl"
            if not p.exists():
                return []
            return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]

        def write_config(self, obj):
            (base / "config.json").write_text(json.dumps(obj))

    return Repo()


@pytest.fixture
def fake_plugin(tmp_path):
    """Build an isolated plugin tree (a copy of one script) so PLUGIN_ROOT-relative
    scripts (check_changelog, notify_update) read a controlled plugin.json/CHANGELOG."""
    root = tmp_path / "plugin"
    (root / "scripts").mkdir(parents=True)
    (root / ".claude-plugin").mkdir()

    def build(script_name, version, changelog):
        shutil.copy(os.path.join(SCRIPTS, script_name), root / "scripts" / script_name)
        (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"version": version}))
        (root / "CHANGELOG.md").write_text(changelog)
        return str(root / "scripts" / script_name)

    build.root = str(root)
    return build
