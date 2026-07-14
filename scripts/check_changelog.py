#!/usr/bin/env python3
"""Release gate: the version in plugin.json must have a CHANGELOG.md section.

Run before tagging a release (the /release flow, or CI later). Exit 1 when:
- plugin.json's version has no `## [<version>]` heading in CHANGELOG.md, or
- the [Unreleased] section still has content (it should have been moved into
  the new version's section as part of the bump).

Prints the release section on success — usable as the GitHub release body:
  gh release create v<version> --notes "$(python3 scripts/check_changelog.py)"
"""

import json
import os
import re
import sys

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    with open(os.path.join(PLUGIN_ROOT, ".claude-plugin", "plugin.json"), encoding="utf-8") as f:
        version = json.load(f).get("version")
    if not version:
        sys.exit("FAIL: no version in plugin.json")

    with open(os.path.join(PLUGIN_ROOT, "CHANGELOG.md"), encoding="utf-8") as f:
        text = f.read()

    sections = {
        m.group("ver"): m.group("body").strip()
        for m in re.finditer(r"^## \[(?P<ver>[^\]]+)\][^\n]*\n(?P<body>.*?)(?=^## \[|\Z)",
                             text, re.MULTILINE | re.DOTALL)
    }

    problems = []
    if version not in sections:
        problems.append(f"CHANGELOG.md has no '## [{version}]' section for plugin.json's version")
    unreleased = next((v for v in sections if v.lower() == "unreleased"), None)
    if unreleased and sections[unreleased]:
        problems.append("the [Unreleased] section still has content — move it into the "
                        f"[{version}] section before releasing")

    if problems:
        for p in problems:
            print(f"FAIL: {p}", file=sys.stderr)
        sys.exit(1)

    print(sections[version])


if __name__ == "__main__":
    main()
