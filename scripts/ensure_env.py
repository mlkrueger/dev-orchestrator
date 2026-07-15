#!/usr/bin/env python3
"""Run-preflight: ensure the target repo gates every commit with local CI checks.

The problem: dev-orchestrator's implementers commit per ticket, and a human (or
CI) commits later — nothing guarantees lint/format/tests actually ran before a
commit lands. The fix is a git `pre-commit` hook wired through `core.hooksPath`
so it is committable and shared with the whole team, not a plugin hook that only
fires inside one Claude session.

This script is idempotent and ledger-backed. It records what it set up in
`.dev-orchestrator/environment.json` (gitignored, per-clone machine state) and
FAST-PATHS on the next run: if the ledger says installed and the on-disk hook's
sha still matches, it does nothing. Existence alone is not trusted — a stale or
hand-edited hook is detected via the recorded sha and re-offered.

Usage:
    ensure_env.py [--force] [--checks-command "npm run ci"] [--json]

Exit codes (the orchestrate preflight branches on these):
    0  set up now, or already set up (fast path)  -> proceed
    2  no checks command could be determined       -> ask the user, then re-run
                                                       with --checks-command
Unexpected errors print a warning and exit 0: a preflight bug must never take
down a run — the run proceeds without the gate, with the problem surfaced.

The installed hook embeds the checks command literally so a fresh clone (which
has no gitignored ledger) still runs the right checks. Changing the command
changes the hook sha, which the ledger uses to detect drift.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys

LEDGER_PATH = os.path.join(".dev-orchestrator", "environment.json")
HOOKS_DIR = ".githooks"
HOOK_PATH = os.path.join(HOOKS_DIR, "pre-commit")
SCHEMA = 1

HOOK_TEMPLATE = """\
#!/bin/sh
# Managed by dev-orchestrator. Runs the project's local CI checks before every
# commit so nothing lands unlinted/untested. Do not edit by hand — change
# `checks_command` in .dev-orchestrator/environment.json and re-run the
# dev-orchestrator preflight (scripts/ensure_env.py) to regenerate.
set -e
echo "[dev-orchestrator] pre-commit checks: {cmd}"
{cmd}
"""


def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def in_git_repo():
    r = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"],
                       capture_output=True, text=True)
    return r.returncode == 0 and r.stdout.strip() == "true"


def load_ledger():
    if os.path.isfile(LEDGER_PATH):
        try:
            with open(LEDGER_PATH, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_ledger(ledger):
    os.makedirs(os.path.dirname(LEDGER_PATH), exist_ok=True)
    with open(LEDGER_PATH, "w", encoding="utf-8") as f:
        json.dump(ledger, f, indent=2)
        f.write("\n")


def detect_checks_command():
    """Best-effort, conservative detection of a read-only checks command.

    Returns a command string or None. Prefers an explicit aggregate script
    (`ci`/`check`) and otherwise composes from the read-only scripts present,
    deliberately skipping mutating ones like `format` (no `:check` suffix).
    Node is the strongest signal; a Python test suite is the fallback.
    """
    if os.path.isfile("package.json"):
        try:
            with open("package.json", encoding="utf-8") as f:
                scripts = (json.load(f).get("scripts") or {})
        except (json.JSONDecodeError, OSError):
            scripts = {}
        for aggregate in ("ci", "check", "verify"):
            if aggregate in scripts:
                return f"npm run {aggregate}"
        ordered = ["lint", "typecheck", "check-types", "format:check", "test"]
        chosen = [f"npm run {s}" for s in ordered if s in scripts]
        if chosen:
            return " && ".join(chosen)

    has_pytest = (
        os.path.isfile("pytest.ini")
        or os.path.isdir("tests")
        or (os.path.isfile("pyproject.toml")
            and "pytest" in _read("pyproject.toml"))
    )
    if has_pytest:
        return "uvx --with pytest pytest" if _which("uv") else "pytest"

    return None


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def _which(name):
    return any(
        os.access(os.path.join(d, name), os.X_OK)
        for d in os.environ.get("PATH", "").split(os.pathsep) if d
    )


def current_hooks_path():
    r = subprocess.run(["git", "config", "--get", "core.hooksPath"],
                       capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def emit(result, as_json):
    if as_json:
        print(json.dumps(result))
    else:
        print(result["message"])


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--check", action="store_true",
                    help="report what would happen without writing anything (fast-path probe)")
    ap.add_argument("--force", action="store_true",
                    help="reinstall even if the ledger fast-path says it's current")
    ap.add_argument("--checks-command", default=None,
                    help="override the detected checks command")
    ap.add_argument("--json", action="store_true", help="emit a JSON result object")
    args = ap.parse_args()

    if not in_git_repo():
        emit({"status": "skipped", "reason": "not a git repository",
              "message": "Not a git repository — no pre-commit gate to install."}, args.json)
        return 0

    ledger = load_ledger()
    cmd = args.checks_command or ledger.get("checks_command") or detect_checks_command()
    if not cmd:
        emit({"status": "needs_command", "message": (
            "Could not determine a checks command for this repo. Re-run with "
            "--checks-command \"<cmd>\" (e.g. 'npm run ci'), or set checks_command "
            "in .dev-orchestrator/environment.json.")}, args.json)
        return 2

    desired = HOOK_TEMPLATE.format(cmd=cmd)
    desired_sha = sha256(desired)

    on_disk_sha = sha256(_read(HOOK_PATH)) if os.path.isfile(HOOK_PATH) else None
    hooks_ok = current_hooks_path() == HOOKS_DIR
    git_ledger = (ledger.get("setup") or {}).get("git_hooks") or {}
    fast_path = (
        not args.force
        and git_ledger.get("installed")
        and git_ledger.get("hook_sha") == desired_sha
        and on_disk_sha == desired_sha
        and hooks_ok
    )
    if fast_path:
        emit({"status": "ok", "checks_command": cmd,
              "message": f"Pre-commit gate already installed and current (checks: {cmd})."},
             args.json)
        return 0

    drift = os.path.isfile(HOOK_PATH) and on_disk_sha != desired_sha

    if args.check:
        status = "would_update_drift" if drift else "would_install"
        emit({"status": status, "drift": drift, "checks_command": cmd,
              "hooks_path": HOOKS_DIR,
              "message": (f"Would install pre-commit gate via core.hooksPath={HOOKS_DIR} "
                          f"(checks: {cmd})." if not drift else
                          f"Installed hook has drifted from the managed version; would "
                          f"regenerate it (checks: {cmd}).")}, args.json)
        return 0

    os.makedirs(HOOKS_DIR, exist_ok=True)
    with open(HOOK_PATH, "w", encoding="utf-8") as f:
        f.write(desired)
    os.chmod(HOOK_PATH, 0o755)
    subprocess.run(["git", "config", "core.hooksPath", HOOKS_DIR],
                   capture_output=True, text=True)

    ledger.setdefault("schema", SCHEMA)
    ledger["checks_command"] = cmd
    ledger.setdefault("setup", {})["git_hooks"] = {
        "installed": True,
        "hooks_path": HOOKS_DIR,
        "hook_sha": desired_sha,
    }
    save_ledger(ledger)

    verb = "Updated (drifted)" if drift else "Installed"
    emit({"status": "installed", "drift": drift, "checks_command": cmd,
          "hooks_path": HOOKS_DIR, "hook_sha": desired_sha,
          "message": (f"{verb} pre-commit gate via core.hooksPath={HOOKS_DIR} "
                      f"(checks: {cmd}). Commit {HOOK_PATH} to share it with the team.")},
         args.json)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as e:  # never take down a run over a preflight bug
        print(f"[dev-orchestrator] preflight warning: {e}; proceeding without gate.",
              file=sys.stderr)
        sys.exit(0)
