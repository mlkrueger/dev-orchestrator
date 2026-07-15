"""bin/tracker — script-first Linear ticket I/O, exercised as a subprocess
against a threaded mock GraphQL server (LINEAR_API_URL override), the way the
orchestrator actually runs it.

The mock routes on GraphQL operation keywords + variables; tests configure its
state (team workflow states, issues, labels, workspace-collision names) and
assert on the compact canonical JSON the CLI emits and the mutations it issued.
"""

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

TRACKER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin", "tracker")


# --- canned-node builders ---------------------------------------------------

STATES_NO_BLOCKED = [
    {"id": "s-todo", "name": "Todo", "type": "unstarted", "position": 0},
    {"id": "s-prog", "name": "In Progress", "type": "started", "position": 1},
    {"id": "s-review", "name": "In Review", "type": "started", "position": 2},
    {"id": "s-done", "name": "Done", "type": "completed", "position": 3},
    {"id": "s-cancel", "name": "Canceled", "type": "canceled", "position": 4},
]
STATES_WITH_BLOCKED = STATES_NO_BLOCKED + [
    {"id": "s-blocked", "name": "Blocked", "type": "started", "position": 5},
]


def issue_node(identifier, title="A title", state=None, labels=(), milestone=None,
               project=None, blocked_by=(), description=""):
    return {
        "id": f"uuid-{identifier}",
        "identifier": identifier,
        "title": title,
        "description": description,
        "state": state or {"name": "Todo", "type": "unstarted"},
        "labels": {"nodes": [{"id": f"lbl-{n}", "name": n} for n in labels]},
        "project": {"name": project} if project else None,
        "projectMilestone": {"name": milestone} if milestone else None,
        "relations": {"nodes": []},
        "inverseRelations": {"nodes": [{"type": "blocks", "issue": {"identifier": b}}
                                       for b in blocked_by]},
    }


class Mock:
    def __init__(self):
        self.team = {"id": "team-uuid", "key": "MKR", "name": "Mkrueger",
                     "states": {"nodes": STATES_NO_BLOCKED}}
        self.issues = {}                 # identifier -> node
        self.team_labels = {}            # name -> id (labels visible on the team)
        self.workspace_collision = set()  # names that 409 on create but exist workspace-wide
        # recorders
        self.updates = []                # (issue_uuid, input)
        self.comments = []               # (issue_uuid, body)
        self.relations = []              # (blocker_uuid, blocked_uuid)
        self.created = []                # inputs
        self.created_labels = []         # names


def _page(nodes):
    return {"nodes": nodes, "pageInfo": {"hasNextPage": False, "endCursor": None}}


def _resolve(mock, query, variables):
    q = query
    # ---- mutations ----
    if "issueLabelCreate" in q:
        name = variables["input"]["name"]
        if name in mock.workspace_collision:
            return {"__errors__": [{"message": "A label with this name already exists (duplicate label name)"}]}
        mock.created_labels.append(name)
        lid = f"lbl-new-{name}"
        mock.team_labels[name] = lid
        return {"issueLabelCreate": {"issueLabel": {"id": lid}}}
    if "issueLabels(filter" in q:  # workspace-wide collision lookup
        name = variables["n"]
        return {"issueLabels": {"nodes": [{"id": f"ws-{name}", "name": name}]}}
    if "issueUpdate" in q:
        mock.updates.append((variables["id"], variables["input"]))
        return {"issueUpdate": {"success": True}}
    if "commentCreate" in q:
        mock.comments.append((variables["input"]["issueId"], variables["input"]["body"]))
        return {"commentCreate": {"success": True}}
    if "issueRelationCreate" in q:
        mock.relations.append((variables["input"]["issueId"], variables["input"]["relatedIssueId"]))
        return {"issueRelationCreate": {"success": True}}
    if "issueCreate" in q:
        mock.created.append(variables["input"])
        return {"issueCreate": {"issue": {"id": "uuid-NEW", "identifier": "MKR-999",
                                          "url": "https://linear.app/mkrueger/issue/MKR-999"}}}
    # ---- reads ----
    if "teams(filter" in q:
        return {"teams": {"nodes": [mock.team]}}
    if "comments(first" in q:
        return {"issue": {"comments": _page([{"body": "hi", "user": {"name": "Bot"}}])}}
    if "labels(first: 250" in q or ("team(id" in q and "labels(first" in q):
        nodes = [{"id": v, "name": k} for k, v in mock.team_labels.items()]
        return {"team": {"labels": _page(nodes)}}
    if "number: { eq" in q:  # issue by identifier
        key, num = variables["k"], int(variables["n"])
        ident = f"{key}-{num}"
        node = mock.issues.get(ident)
        return {"issues": {"nodes": [node] if node else []}}
    if "projectMilestone: { name" in q:
        m = variables["m"]
        nodes = [n for n in mock.issues.values()
                 if (n.get("projectMilestone") or {}).get("name") == m]
        return {"issues": _page(nodes)}
    if "project: { name" in q:
        m = variables["m"]
        nodes = [n for n in mock.issues.values()
                 if (n.get("project") or {}).get("name") == m]
        return {"issues": _page(nodes)}
    raise AssertionError(f"mock: unrouted query:\n{q}")


@pytest.fixture
def mock_server():
    mock = Mock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            result = _resolve(mock, body["query"], body.get("variables") or {})
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            if "__errors__" in result:
                payload = {"errors": result["__errors__"]}
            else:
                payload = {"data": result}
            self.wfile.write(json.dumps(payload).encode())

    server = HTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    url = f"http://127.0.0.1:{server.server_address[1]}/graphql"
    yield mock, url
    server.shutdown()


def run_tracker(url, *args, cwd=None):
    env = {**os.environ, "LINEAR_API_KEY": "test-key", "LINEAR_API_URL": url}
    return subprocess.run([sys.executable, TRACKER, *args],
                          capture_output=True, text=True, cwd=cwd, env=env)


# --- canonical rendering (JSON shape) ---------------------------------------

def test_get_emits_canonical_shape(mock_server):
    mock, url = mock_server
    mock.issues["MKR-440"] = issue_node(
        "MKR-440", title="Dispatch by path",
        state={"name": "In Progress", "type": "started"},
        labels=["tier:standard", "mod:api", "mod:hooks", "resource:db", "Improvement"],
        milestone="Dispatch efficiency",
        blocked_by=["MKR-443"],
        description="Body text\n\n## Acceptance criteria\n- [ ] first crit\n- [ ] second crit\n",
    )
    r = run_tracker(url, "get", "MKR-440")
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["id"] == "MKR-440"
    assert out["status"] == "in_progress"
    assert out["tier"] == "standard"
    assert out["modules"] == ["api", "hooks"]
    assert out["resources"] == ["db"]
    assert out["labels"] == ["Improvement"]          # managed labels stripped
    assert out["milestone"] == "Dispatch efficiency"
    assert out["dependencies"] == ["MKR-443"]
    assert out["acceptance_criteria"] == ["first crit", "second crit"]


def test_get_is_compact_json(mock_server):
    mock, url = mock_server
    mock.issues["MKR-1"] = issue_node("MKR-1")
    r = run_tracker(url, "get", "MKR-1")
    assert r.returncode == 0, r.stderr
    assert ", " not in r.stdout and ": " not in r.stdout  # compact separators


def test_list_is_light_and_filters(mock_server):
    mock, url = mock_server
    mock.issues["MKR-1"] = issue_node("MKR-1", state={"name": "Todo", "type": "unstarted"},
                                      labels=["tier:simple"], milestone="M1")
    mock.issues["MKR-2"] = issue_node("MKR-2", state={"name": "Done", "type": "completed"},
                                      labels=["tier:complex"], milestone="M1")
    r = run_tracker(url, "list", "--milestone", "M1", "--team", "MKR", "--status", "todo")
    assert r.returncode == 0, r.stderr
    rows = json.loads(r.stdout)
    assert [x["id"] for x in rows] == ["MKR-1"]
    assert "description" not in rows[0]              # list stays light
    assert rows[0]["tier"] == "simple"


# --- status mapping (by state type, not hardcoded names) --------------------

@pytest.mark.parametrize("canonical,expect_state_id", [
    ("todo", "s-todo"),
    ("in_progress", "s-prog"),
    ("in_review", "s-review"),
    ("done", "s-done"),
])
def test_set_status_resolves_by_type(mock_server, canonical, expect_state_id):
    mock, url = mock_server
    mock.issues["MKR-5"] = issue_node("MKR-5")
    r = run_tracker(url, "set-status", "MKR-5", canonical)
    assert r.returncode == 0, r.stderr
    assert mock.updates == [("uuid-MKR-5", {"stateId": expect_state_id})]


def test_set_status_blocked_uses_blocked_state_when_present(mock_server):
    mock, url = mock_server
    mock.team["states"]["nodes"] = STATES_WITH_BLOCKED
    mock.issues["MKR-6"] = issue_node("MKR-6")
    r = run_tracker(url, "set-status", "MKR-6", "blocked")
    assert r.returncode == 0, r.stderr
    assert mock.updates == [("uuid-MKR-6", {"stateId": "s-blocked"})]
    assert mock.comments == []                        # real state → no label/comment fallback


def test_set_status_blocked_falls_back_to_label_and_comment(mock_server):
    mock, url = mock_server  # default team has no Blocked state
    mock.issues["MKR-7"] = issue_node("MKR-7", labels=["tier:simple"])
    r = run_tracker(url, "set-status", "MKR-7", "blocked")
    assert r.returncode == 0, r.stderr
    # applied a labelIds update (not a stateId one) and posted a comment
    assert len(mock.updates) == 1
    _, inp = mock.updates[0]
    assert "stateId" not in inp and "labelIds" in inp
    assert len(mock.comments) == 1
    out = json.loads(r.stdout)
    assert out["state"] == "label:blocked"


def test_status_maps_by_type_not_name(mock_server):
    """A team whose started state is named oddly still resolves in_progress by type."""
    mock, url = mock_server
    mock.team["states"]["nodes"] = [
        {"id": "s-x", "name": "Cooking", "type": "started", "position": 0},
        {"id": "s-y", "name": "Shipped", "type": "completed", "position": 1},
    ]
    mock.issues["MKR-8"] = issue_node("MKR-8")
    r = run_tracker(url, "set-status", "MKR-8", "in_progress")
    assert r.returncode == 0, r.stderr
    assert mock.updates == [("uuid-MKR-8", {"stateId": "s-x"})]


# --- label create-if-missing / workspace collision --------------------------

def test_create_reuses_workspace_label_on_collision(mock_server):
    mock, url = mock_server
    # "Improvement" is a workspace-level label: not on the team, 409s on create.
    mock.workspace_collision.add("Improvement")
    r = run_tracker(url, "create", "--title", "New ticket", "--labels", "Improvement", "--team", "MKR")
    assert r.returncode == 0, r.stderr
    # create was attempted, collided, and the CLI reused the workspace label —
    # no exception, and the issue was created with the resolved label id.
    assert mock.created, "issue should have been created"
    assert mock.created[0]["labelIds"] == ["ws-Improvement"]


def test_create_makes_missing_label(mock_server):
    mock, url = mock_server
    r = run_tracker(url, "create", "--title", "T", "--labels", "tier:simple", "--team", "MKR")
    assert r.returncode == 0, r.stderr
    assert "tier:simple" in mock.created_labels
    assert mock.created[0]["labelIds"] == ["lbl-new-tier:simple"]


def test_add_dependency_wires_blocker_blocks_blocked(mock_server):
    mock, url = mock_server
    mock.issues["MKR-10"] = issue_node("MKR-10")
    mock.issues["MKR-11"] = issue_node("MKR-11")
    r = run_tracker(url, "add-dependency", "MKR-10", "--blocked-by", "MKR-11")
    assert r.returncode == 0, r.stderr
    # MKR-11 blocks MKR-10
    assert mock.relations == [("uuid-MKR-11", "uuid-MKR-10")]
