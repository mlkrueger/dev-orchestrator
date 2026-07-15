"""check_changelog.py (release gate) and notify_update.py (update surfacing).

Both resolve paths relative to their own plugin root, so they're run from an
isolated copied-in fake plugin tree rather than the real repo."""

import json
import subprocess
import sys

from conftest import run_script


CL = """# Changelog

## [Unreleased]

## [0.3.0] — 2026-07-14

### Added
- thing three

## [0.2.0] — 2026-07-08

### Changed
- thing two
"""


def run_copied(script_path, *args, stdin=None):
    return subprocess.run([sys.executable, script_path, *args],
                          input=stdin, capture_output=True, text=True)


# ---- check_changelog.py -------------------------------------------------

def test_passes_and_emits_section(fake_plugin):
    script = fake_plugin("check_changelog.py", "0.3.0", CL)
    p = run_copied(script)
    assert p.returncode == 0
    assert "thing three" in p.stdout


def test_fails_when_version_section_missing(fake_plugin):
    script = fake_plugin("check_changelog.py", "9.9.9", CL)
    p = run_copied(script)
    assert p.returncode == 1
    assert "no '## [9.9.9]'" in p.stderr


def test_fails_when_unreleased_has_content(fake_plugin):
    cl = CL.replace("## [Unreleased]\n", "## [Unreleased]\n\n### Fixed\n- leftover\n")
    script = fake_plugin("check_changelog.py", "0.3.0", cl)
    p = run_copied(script)
    assert p.returncode == 1
    assert "Unreleased" in p.stderr


def test_real_repo_changelog_has_section_for_current_version():
    """Sanity check against the actual repo: whatever plugin.json's version is,
    CHANGELOG must contain its section (guards manual bump/changelog drift)."""
    p = run_script("check_changelog.py")
    # Exit 1 is allowed ONLY when it's because Unreleased still has content
    # (a valid mid-development state); a missing version section is never ok.
    if p.returncode != 0:
        assert "Unreleased" in p.stderr and "no '## [" not in p.stderr


# ---- notify_update.py: changelog_since (pure function) ------------------

def test_changelog_since_returns_versions_after_last(fake_plugin):
    import importlib.util
    script = fake_plugin("notify_update.py", "0.3.0", CL)
    spec = importlib.util.spec_from_file_location("notify_update", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    since = mod.changelog_since("0.2.0")
    assert "0.3.0" in since
    assert "thing three" in since
    assert "0.2.0" not in since  # the boundary version itself is excluded
    assert "Unreleased" not in since
