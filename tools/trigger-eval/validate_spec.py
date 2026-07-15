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

try:
    import yaml
except ImportError:
    sys.exit("validate_spec.py needs PyYAML — the spec's frontmatter is YAML and a regex "
             "approximation would pass files the runtime rejects. Install it: pip install pyyaml")

REPO_ROOT = Path(__file__).resolve().parents[2]

NAME_MAX = 64
DESC_MAX = 1024
COMPAT_MAX = 500
BODY_MAX_LINES = 500  # recommended, not normative
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

# Warn well before the cliff: a description at 97% of budget cannot absorb the
# next trigger fix, and you want to know that before you need the room.
DESC_WARN = int(DESC_MAX * 0.90)


class FrontmatterError(Exception):
    """The frontmatter is not loadable as YAML."""


def parse_frontmatter(text: str) -> dict[str, object]:
    """Parse the frontmatter as real YAML.

    A regex approximation is worse than useless here: it happily accepts input
    that a real YAML loader rejects (`description: [unterminated`), and Claude
    Code loads malformed frontmatter as *empty metadata* — so the skill ships
    with no description at all and can never trigger. A validator that green-
    lights that has inverted its own purpose. Parse it the way the runtime does.
    """
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        raise FrontmatterError("no YAML frontmatter block (`---` ... `---`) at the top of the file")
    try:
        loaded = yaml.safe_load(m.group(1))
    except yaml.YAMLError as e:
        raise FrontmatterError(f"frontmatter is not valid YAML: {e}") from e
    if loaded is None:
        raise FrontmatterError("frontmatter is empty")
    if not isinstance(loaded, dict):
        raise FrontmatterError(f"frontmatter must be a mapping, got {type(loaded).__name__}")
    return loaded


TEXT_FIELDS = ("name", "description", "compatibility")


def text_field(raw: dict, key: str, errors: list[str]) -> str | None:
    """Return a text field's value, or record an error if it isn't actually text.

    The spec's text fields have character limits, so they must be strings. But
    YAML coerces unquoted scalars: `description: no` becomes the boolean False,
    `description: 123` an int. str()-ing those would green-light metadata the
    runtime sees as a non-string and the author never intended — so reject the
    non-string outright and tell them to quote it, rather than stringifying it.
    """
    if key not in raw or raw[key] is None:
        return None
    value = raw[key]
    if not isinstance(value, str):
        errors.append(
            f"`{key}` parsed as {type(value).__name__} ({value!r}), not text — "
            f"quote it (e.g. `{key}: \"{value}\"`); the spec's text fields must be strings"
        )
        return None
    # Return the parsed string as-is. The character limits apply to the value the
    # runtime loads — collapsing whitespace here first would measure a shorter
    # string than the runtime sees and could pass an over-budget description.
    # Normalize only at the point of display, never before a length check.
    return value


def check(skill: Path) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    text = (skill / "SKILL.md").read_text()

    try:
        raw = parse_frontmatter(text)
    except FrontmatterError as e:
        # Fatal on its own: the runtime would load this skill with empty metadata,
        # so nothing downstream is worth checking.
        return [str(e)], []

    fm = {k: text_field(raw, k, errors) for k in TEXT_FIELDS}

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
