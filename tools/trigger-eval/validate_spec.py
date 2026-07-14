#!/usr/bin/env python3
"""Check every skill in this repo against the Agent Skills specification.

Spec: https://agentskills.io/specification

The limits are hard and silent — nothing warns you as a description creeps
toward 1024 characters, and a skill that blows the limit is malformed, not
merely verbose. Since tuning a description to fix a trigger defect *grows* it
(see tools/trigger-eval), the two tools belong side by side: one tells you the
description doesn't fire, the other tells you how much room you have left to
fix that.

    python3 tools/trigger-eval/validate_spec.py

Exits non-zero on any violation.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

NAME_MAX = 64
DESC_MAX = 1024
COMPAT_MAX = 500
BODY_MAX_LINES = 500  # recommended, not normative
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

# Warn well before the cliff: a description at 97% of budget cannot absorb the
# next trigger fix, and you want to know that before you need the room.
DESC_WARN = int(DESC_MAX * 0.90)


def parse_frontmatter(text: str) -> dict[str, str]:
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        return {}
    fields: dict[str, str] = {}
    for raw in re.finditer(
        r"^([a-z][a-z-]*):\s*(?:>-?|\|)?[ \t]*\n?((?:.|\n)*?)(?=\n[a-z][a-z-]*:|\Z)",
        m.group(1),
        re.M,
    ):
        fields[raw.group(1)] = " ".join(raw.group(2).split())
    return fields


def check(skill: Path) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    text = (skill / "SKILL.md").read_text()
    fm = parse_frontmatter(text)

    name = fm.get("name")
    if not name:
        errors.append("missing required field `name`")
    else:
        if name != skill.name:
            errors.append(f"`name` is {name!r} but the directory is {skill.name!r} — spec requires they match")
        if len(name) > NAME_MAX:
            errors.append(f"`name` is {len(name)} chars, limit {NAME_MAX}")
        if not NAME_RE.match(name):
            errors.append(f"`name` {name!r} must be lowercase a-z0-9 with single internal hyphens")

    desc = fm.get("description")
    if not desc:
        errors.append("missing required field `description`")
    elif len(desc) > DESC_MAX:
        errors.append(f"`description` is {len(desc)} chars, limit {DESC_MAX} — MALFORMED")
    elif len(desc) >= DESC_WARN:
        warnings.append(
            f"`description` is {len(desc)}/{DESC_MAX} chars — only {DESC_MAX - len(desc)} left "
            f"to absorb a trigger fix"
        )

    compat = fm.get("compatibility")
    if compat and len(compat) > COMPAT_MAX:
        errors.append(f"`compatibility` is {len(compat)} chars, limit {COMPAT_MAX}")

    lines = len(text.splitlines())
    if lines > BODY_MAX_LINES:
        warnings.append(f"SKILL.md is {lines} lines; spec recommends under {BODY_MAX_LINES} — move detail to REFERENCE.md")

    return errors, warnings


def main() -> int:
    skills = sorted(p for p in REPO_ROOT.iterdir() if (p / "SKILL.md").is_file())
    if not skills:
        print("no skills found", file=sys.stderr)
        return 2

    total_errors = 0
    for skill in skills:
        errors, warnings = check(skill)
        total_errors += len(errors)
        status = "FAIL" if errors else ("warn" if warnings else "ok")
        print(f"  {status:>4}  {skill.name}")
        for e in errors:
            print(f"          error: {e}")
        for w in warnings:
            print(f"          warn:  {w}")

    print(f"\n  {len(skills)} skills, {total_errors} spec violations\n")
    return 1 if total_errors else 0


if __name__ == "__main__":
    sys.exit(main())
