"""bin/tracker with the local (file-backed) backend, exercised as a subprocess
the way the orchestrator runs it — config selects `tracker: local`, and the
build plan YAML/JSON is both the ticket source and the board.

The two contracts that matter most: the canonical JSON out is byte-identical in
shape to the Linear path (so every caller is backend-agnostic), and a
`set-status` write edits only the ticket's status line, leaving the rest of a
hand-authored plan — comments and all — untouched.
"""

import json
import os
import subprocess
import sys

import pytest

TRACKER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin", "tracker")

yaml = pytest.importorskip("yaml")  # local YAML plans need PyYAML

PLAN = """\
team: PAY
milestones:
  - name: Payments v2
    tickets:
      - id: PAY-1        # the first ticket — this comment must survive
        title: Add the payment model
        status: todo
        tier: standard
        modules: [api, db]
        resources: [db]
        phase: 1
        depends_on: []
        description: |
          Create the model.
          ## Acceptance criteria
          - persists a payment
      - id: PAY-2
        title: Wire the endpoint
        tier: simple
        phase: 2
        depends_on: [PAY-1]
        labels: [Improvement]
"""


def make_repo(tmp_path, plan=PLAN, plan_name="build-plan.yaml", config=None):
    d = tmp_path / ".dev-orchestrator"
    d.mkdir(parents=True)
    cfg = config or {"tracker": "local"}
    (d / "config.json").write_text(json.dumps(cfg))
    (d / plan_name).write_text(plan)
    return tmp_path


def run(repo, *args):
    # deliberately NO LINEAR_API_KEY — the local backend must need no key
    env = {k: v for k, v in os.environ.items() if k != "LINEAR_API_KEY"}
    return subprocess.run([sys.executable, TRACKER, *args],
                          capture_output=True, text=True, cwd=str(repo), env=env)


def plan_text(repo, name="build-plan.yaml"):
    return (repo / ".dev-orchestrator" / name).read_text()


# --- routing + reads --------------------------------------------------------

def test_local_needs_no_api_key(tmp_path):
    repo = make_repo(tmp_path)
    r = run(repo, "list", "--milestone", "Payments v2")
    assert r.returncode == 0, r.stderr
    rows = json.loads(r.stdout)
    assert [t["id"] for t in rows] == ["PAY-1", "PAY-2"]


def test_list_is_light_and_compact(tmp_path):
    repo = make_repo(tmp_path)
    r = run(repo, "list", "--milestone", "Payments v2")
    rows = json.loads(r.stdout)
    assert "description" not in rows[0] and "labels" not in rows[0]  # light
    assert ", " not in r.stdout and ": " not in r.stdout            # compact


def test_list_filters_status_and_label(tmp_path):
    repo = make_repo(tmp_path)
    run(repo, "set-status", "PAY-1", "in_progress")   # PAY-2 stays default todo
    assert [t["id"] for t in json.loads(run(repo, "list", "--milestone", "Payments v2",
                                            "--status", "in_progress").stdout)] == ["PAY-1"]
    assert [t["id"] for t in json.loads(run(repo, "list", "--milestone", "Payments v2",
                                            "--status", "todo").stdout)] == ["PAY-2"]
    # phase is exposed as a pseudo-label so `--label phase:K` works like Linear
    assert [t["id"] for t in json.loads(run(repo, "list", "--milestone", "Payments v2",
                                            "--label", "phase:2").stdout)] == ["PAY-2"]
    assert [t["id"] for t in json.loads(run(repo, "list", "--milestone", "Payments v2",
                                            "--label", "tier:simple").stdout)] == ["PAY-2"]
    assert [t["id"] for t in json.loads(run(repo, "list", "--milestone", "Payments v2",
                                            "--label", "mod:api").stdout)] == ["PAY-1"]


def test_get_full_canonical_shape(tmp_path):
    repo = make_repo(tmp_path)
    out = json.loads(run(repo, "get", "PAY-1").stdout)
    assert out["id"] == "PAY-1" and out["status"] == "todo" and out["tier"] == "standard"
    assert out["modules"] == ["api", "db"] and out["resources"] == ["db"]
    assert out["dependencies"] == []
    assert out["labels"] == ["phase:1"]                    # phase surfaced as a label
    assert out["acceptance_criteria"] == ["persists a payment"]  # parsed from description
    assert out["milestone"] == "Payments v2"


def test_get_explicit_criteria_and_labels(tmp_path):
    repo = make_repo(tmp_path)
    out = json.loads(run(repo, "get", "PAY-2").stdout)
    assert out["dependencies"] == ["PAY-1"]
    assert "Improvement" in out["labels"] and "phase:2" in out["labels"]
    assert out["status"] == "todo"                          # default when absent


# --- set-status: surgical, format-preserving --------------------------------

def test_set_status_preserves_the_rest_of_the_file(tmp_path):
    repo = make_repo(tmp_path)
    before = plan_text(repo)
    r = run(repo, "set-status", "PAY-1", "in_progress")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == {"id": "PAY-1", "status": "in_progress", "state": "local:in_progress"}
    after = plan_text(repo)
    # only the status line changed; the comment and everything else is intact
    assert "# the first ticket — this comment must survive" in after
    assert "## Acceptance criteria" in after
    assert after == before.replace("        status: todo", "        status: in_progress")


def test_set_status_inserts_when_field_absent(tmp_path):
    repo = make_repo(tmp_path)
    run(repo, "set-status", "PAY-2", "done")               # PAY-2 has no status field
    out = json.loads(run(repo, "get", "PAY-2").stdout)
    assert out["status"] == "done"
    # PAY-1's comment still survives — the edit didn't bleed across records
    assert "this comment must survive" in plan_text(repo)


def test_set_status_across_multiple_tickets_is_stable(tmp_path):
    repo = make_repo(tmp_path)
    for tid, st in [("PAY-1", "in_progress"), ("PAY-2", "done"), ("PAY-1", "done")]:
        assert run(repo, "set-status", tid, st).returncode == 0
    assert json.loads(run(repo, "get", "PAY-1").stdout)["status"] == "done"
    assert json.loads(run(repo, "get", "PAY-2").stdout)["status"] == "done"
    assert "this comment must survive" in plan_text(repo)
    assert "## Acceptance criteria" in plan_text(repo)


def test_set_status_rejects_bad_status(tmp_path):
    repo = make_repo(tmp_path)
    r = run(repo, "set-status", "PAY-1", "shipped")
    assert r.returncode == 2 and "status must be one of" in r.stderr


# --- comments ---------------------------------------------------------------

def test_comment_round_trips_via_companion_store(tmp_path):
    repo = make_repo(tmp_path)
    body = tmp_path / "c.md"
    body.write_text("done in 1 attempt; qa PASS")
    assert run(repo, "comment", "PAY-1", "--body-file", str(body)).returncode == 0
    out = json.loads(run(repo, "get", "PAY-1", "--comments").stdout)
    assert out["comments"] == [{"user": "dev-orchestrator", "body": "done in 1 attempt; qa PASS"}]
    # the plan file itself was NOT touched by commenting
    assert "this comment must survive" in plan_text(repo)


# --- flat form + json plan --------------------------------------------------

def test_flat_ticket_list_form(tmp_path):
    plan = ("tickets:\n"
            "  - id: T-1\n    title: flat\n    milestone: M1\n    tier: standard\n    status: todo\n"
            "  - id: T-2\n    title: two\n    milestone: M1\n    tier: simple\n")
    repo = make_repo(tmp_path, plan=plan)
    rows = json.loads(run(repo, "list", "--milestone", "M1").stdout)
    assert [t["id"] for t in rows] == ["T-1", "T-2"]
    run(repo, "set-status", "T-2", "in_progress")
    assert json.loads(run(repo, "get", "T-2").stdout)["status"] == "in_progress"


def test_json_plan_needs_no_yaml(tmp_path):
    plan = json.dumps({"tickets": [
        {"id": "J-1", "title": "json", "milestone": "M", "tier": "standard", "status": "todo"},
    ]})
    repo = make_repo(tmp_path, plan=plan, plan_name="build-plan.json",
                     config={"tracker": "local", "local": {"plan": ".dev-orchestrator/build-plan.json"}})
    assert json.loads(run(repo, "list", "--milestone", "M").stdout)[0]["id"] == "J-1"
    run(repo, "set-status", "J-1", "done")
    doc = json.loads(plan_text(repo, "build-plan.json"))
    assert doc["tickets"][0]["status"] == "done"


# --- errors + unsupported ops -----------------------------------------------

def test_missing_ticket_is_runtime_error(tmp_path):
    repo = make_repo(tmp_path)
    r = run(repo, "get", "NOPE")
    assert r.returncode == 1 and "not found" in r.stderr


def test_missing_plan_is_runtime_error(tmp_path):
    d = tmp_path / ".dev-orchestrator"
    d.mkdir()
    (d / "config.json").write_text(json.dumps({"tracker": "local"}))
    r = run(tmp_path, "list", "--milestone", "M1")
    assert r.returncode == 1 and "no build plan" in r.stderr


@pytest.mark.parametrize("cmd,extra", [
    ("create", ["--title", "x"]),
    ("update", ["PAY-1", "--title", "x"]),
    ("add-dependency", ["PAY-1", "--blocked-by", "PAY-2"]),
])
def test_structural_ops_point_to_editing_the_yaml(tmp_path, cmd, extra):
    repo = make_repo(tmp_path)
    r = run(repo, cmd, *extra)
    assert r.returncode == 1
    assert "editing the YAML" in r.stderr


def test_default_backend_still_linear(tmp_path):
    # no tracker config -> Linear path, which requires an API key (proves routing
    # only diverts to local when explicitly configured)
    (tmp_path / ".dev-orchestrator").mkdir()
    (tmp_path / ".dev-orchestrator" / "config.json").write_text("{}")
    env = {k: v for k, v in os.environ.items() if k != "LINEAR_API_KEY"}
    r = subprocess.run([sys.executable, TRACKER, "list", "--milestone", "M", "--team", "MKR"],
                       capture_output=True, text=True, cwd=str(tmp_path), env=env)
    assert r.returncode == 2 and "LINEAR_API_KEY" in r.stderr
