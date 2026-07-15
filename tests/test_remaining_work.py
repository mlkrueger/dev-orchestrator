"""remaining_work.py — continuation reconstruction for orchestrator respawn.

Given a milestone's ticket set + the run dir, it reports {done, blocked,
remaining} from log.jsonl so a fresh (respawned) orchestrator picks up exactly
what's left — never re-processing a ticket. The "no double-processing"
invariant is the whole point, so it's pinned here first.
"""

import json

from conftest import run_script


def write_log(run_dir, events):
    lines = []
    for e in events:
        lines.append(e if isinstance(e, str) else json.dumps(e))
    (run_dir / "log.jsonl").write_text("\n".join(lines) + ("\n" if lines else ""))


def remaining(tmp_path, tickets, events=None, make_log=True):
    rd = tmp_path / ".dev-orchestrator" / "runs" / "run-1"
    rd.mkdir(parents=True)
    if make_log:
        write_log(rd, events or [])
    proc = run_script("remaining_work.py", "--run-dir", str(rd),
                      "--tickets", ",".join(tickets), cwd=str(tmp_path))
    return proc


def parse(proc):
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_no_log_all_remaining(tmp_path):
    out = parse(remaining(tmp_path, ["A-1", "A-2", "A-3"], make_log=True))  # empty log file
    assert out["remaining"] == ["A-1", "A-2", "A-3"]
    assert out["done"] == [] and out["blocked"] == []


def test_missing_log_file_all_remaining(tmp_path):
    # run dir exists but no log yet (nothing dispatched) -> everything remains
    out = parse(remaining(tmp_path, ["A-1", "A-2"], make_log=False))
    assert out["remaining"] == ["A-1", "A-2"]


def test_done_excluded_from_remaining(tmp_path):
    out = parse(remaining(tmp_path, ["A-1", "A-2", "A-3"], [
        {"event": "ticket_done", "ticket": "A-1", "attempts": 1},
        {"event": "ticket_done", "ticket": "A-3", "attempts": 2},
    ]))
    assert out["done"] == ["A-1", "A-3"]
    assert out["remaining"] == ["A-2"]


def test_blocked_excluded_and_listed(tmp_path):
    out = parse(remaining(tmp_path, ["A-1", "A-2", "A-3"], [
        {"event": "ticket_blocked", "ticket": "A-2", "reason": "needs-grooming"},
    ]))
    assert out["blocked"] == ["A-2"]
    assert out["remaining"] == ["A-1", "A-3"]


def test_duplicate_done_is_idempotent(tmp_path):
    out = parse(remaining(tmp_path, ["A-1", "A-2"], [
        {"event": "ticket_done", "ticket": "A-1"},
        {"event": "ticket_done", "ticket": "A-1"},  # retried log write, same ticket
    ]))
    assert out["done"] == ["A-1"]        # listed once
    assert out["remaining"] == ["A-2"]


def test_done_wins_over_blocked(tmp_path):
    # a ticket that was blocked then later completed counts as done, not blocked
    out = parse(remaining(tmp_path, ["A-1"], [
        {"event": "ticket_blocked", "ticket": "A-1", "reason": "flaky"},
        {"event": "ticket_done", "ticket": "A-1"},
    ]))
    assert out["done"] == ["A-1"]
    assert out["blocked"] == []
    assert out["remaining"] == []


def test_out_of_set_events_ignored(tmp_path):
    # a done event for a ticket not in this milestone's set is irrelevant
    out = parse(remaining(tmp_path, ["A-1", "A-2"], [
        {"event": "ticket_done", "ticket": "Z-9"},
        {"event": "ticket_done", "ticket": "A-1"},
    ]))
    assert out["done"] == ["A-1"]
    assert out["remaining"] == ["A-2"]


def test_malformed_lines_skipped(tmp_path):
    out = parse(remaining(tmp_path, ["A-1", "A-2"], [
        "not json at all",
        "",
        {"event": "ticket_done", "ticket": "A-1"},
        "{ half json ",
    ]))
    assert out["done"] == ["A-1"]
    assert out["remaining"] == ["A-2"]


def test_ordering_follows_input(tmp_path):
    out = parse(remaining(tmp_path, ["A-3", "A-1", "A-2"], [
        {"event": "ticket_done", "ticket": "A-1"},
    ]))
    assert out["remaining"] == ["A-3", "A-2"]  # input order, minus done


def test_duplicate_input_tickets_deduped(tmp_path):
    out = parse(remaining(tmp_path, ["A-1", "A-1", "A-2"], []))
    assert out["remaining"] == ["A-1", "A-2"]


def test_other_events_ignored(tmp_path):
    # dispatch/gate/commit events never mark a ticket done
    out = parse(remaining(tmp_path, ["A-1"], [
        {"event": "dispatch", "ticket": "A-1", "agent": "implementer"},
        {"event": "gate", "ticket": "A-1", "gate": "qa", "verdict": "PASS"},
        {"event": "commit", "ticket": "A-1", "sha": "abc"},
    ]))
    assert out["remaining"] == ["A-1"]     # committed but no ticket_done -> still remaining


def test_compact_json_output(tmp_path):
    proc = remaining(tmp_path, ["A-1"], [])
    assert proc.returncode == 0
    assert ", " not in proc.stdout and ": " not in proc.stdout


def test_missing_run_dir_is_usage_error(tmp_path):
    proc = run_script("remaining_work.py", "--run-dir", str(tmp_path / "nope"),
                      "--tickets", "A-1", cwd=str(tmp_path))
    assert proc.returncode == 2
