"""clean.py — run-log lifecycle, including the active-run guard under both
relative and absolute current-run pointer forms (the regression fixed here)."""

import os

from conftest import run_script


def clean(repo, *args):
    return run_script("clean.py", *args, cwd=repo.path)


def test_list_marks_active_run(repo):
    p = clean(repo)  # relative pointer (default)
    assert p.returncode == 0
    assert "run-001" in p.stdout
    assert "active" in p.stdout
    assert "Stale" not in p.stdout


def test_list_marks_active_with_absolute_pointer(repo):
    repo.set_pointer(repo.run_dir)  # absolute form
    p = clean(repo)
    assert "active" in p.stdout
    assert "Stale" not in p.stdout, "absolute pointer must not be read as stale"


def test_all_protects_active_run_relative_pointer(repo):
    repo.make_run("run-000")
    p = clean(repo, "--all")
    assert p.returncode == 0
    assert os.path.isdir(repo.run_dir), "active run must survive --all"
    assert not os.path.isdir(os.path.join(repo.path, ".dev-orchestrator/runs/run-000"))


def test_all_protects_active_run_absolute_pointer(repo):
    """The regression: an absolute pointer used to bypass the active-run guard."""
    repo.set_pointer(repo.run_dir)
    repo.make_run("run-000")
    p = clean(repo, "--all")
    assert p.returncode == 0
    assert os.path.isdir(repo.run_dir), "active run must survive --all even with absolute pointer"


def test_keep_deletes_older_runs(repo):
    repo.make_run("run-000")  # older (sorts before run-001)
    p = clean(repo, "--keep", "1")
    assert os.path.isdir(repo.run_dir)  # run-001 is newest + active
    assert not os.path.isdir(os.path.join(repo.path, ".dev-orchestrator/runs/run-000"))


def test_dry_run_deletes_nothing(repo):
    repo.make_run("run-000")
    p = clean(repo, "--all", "--dry-run")
    assert "Would delete" in p.stdout
    assert os.path.isdir(os.path.join(repo.path, ".dev-orchestrator/runs/run-000"))


def test_genuinely_stale_pointer_reported(repo):
    repo.set_pointer(".dev-orchestrator/runs/GONE")
    p = clean(repo)
    assert "Stale" in p.stdout


def test_stale_pointer_cleared_on_delete(repo):
    repo.set_pointer(".dev-orchestrator/runs/GONE")
    clean(repo, "--all")
    assert not os.path.isfile(os.path.join(repo.path, ".dev-orchestrator/current-run"))


def test_deleting_active_run_by_id_refused(repo):
    p = clean(repo, "run-001")
    assert p.returncode != 0
    assert os.path.isdir(repo.run_dir)
