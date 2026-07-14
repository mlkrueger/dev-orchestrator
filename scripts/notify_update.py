#!/usr/bin/env python3
"""SessionStart(startup) hook: surface the changelog the first session after an update.

Claude Code has no native changelog display for plugin updates (verified
against docs 2026-07-14: no UI, no plugin.json field). This hook closes the
gap: it remembers the last plugin version it announced (state file in
~/.claude/) and, when .claude-plugin/plugin.json's version differs, injects
the CHANGELOG.md sections for every version between — so Claude opens the
first post-update session by telling the user what they got and why.

Quiet by design: emits nothing when the version is unchanged, and on first
install just records the version silently. Fails open — a broken changelog
must never break session start.
"""

import json
import os
import re
import sys

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_FILE = os.path.expanduser("~/.claude/dev-orchestrator.last-announced-version")


def current_version():
    with open(os.path.join(PLUGIN_ROOT, ".claude-plugin", "plugin.json"), encoding="utf-8") as f:
        return json.load(f).get("version")


def changelog_since(last_version):
    """Return CHANGELOG.md sections for versions after last_version (newest first)."""
    path = os.path.join(PLUGIN_ROOT, "CHANGELOG.md")
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        text = f.read()
    sections = []
    for m in re.finditer(r"^## \[(?P<ver>[^\]]+)\][^\n]*\n(?P<body>.*?)(?=^## \[|\Z)",
                         text, re.MULTILINE | re.DOTALL):
        ver = m.group("ver")
        if ver.lower() == "unreleased":
            continue
        if ver == last_version:
            break
        sections.append(f"## {ver}\n{m.group('body').strip()}")
    return "\n\n".join(sections) if sections else None


def main():
    payload = json.load(sys.stdin)
    if payload.get("source") not in (None, "startup"):
        return  # only on fresh session start, not resume/clear/compact

    version = current_version()
    if not version:
        return

    last = None
    if os.path.isfile(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            last = f.read().strip() or None

    if last == version:
        return

    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        f.write(version)

    if last is None:
        return  # first install: record silently, nothing to diff against

    changes = changelog_since(last)
    if not changes:
        changes = "(no changelog entries found between these versions)"

    context = (
        f"<plugin-update-notice>\n"
        f"The dev-orchestrator plugin was updated since the user's last session: "
        f"{last} -> {version}. At the START of your first reply, briefly inform the "
        f"user of the update and summarize what changed and why it matters (2-5 "
        f"lines; do not paste the raw changelog unless asked). Changelog for the "
        f"new version(s):\n\n{changes}\n"
        f"</plugin-update-notice>"
    )
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context,
        }
    }))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # fail open: never break session start
    sys.exit(0)
