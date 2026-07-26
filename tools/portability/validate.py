#!/usr/bin/env python3
"""Deterministically validate a repository of portable agent skills."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote

import yaml


NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
PRIVATE_MARKERS = (
    "Digitizers/" + "marketing-skills",
    "Digitizers/" + "digitizer-os",
    "digitizer-" + "private",
)
TEXT_SUFFIXES = {".json", ".md", ".py", ".sh", ".txt", ".yaml", ".yml"}


def fail(errors: list[str], path: Path, message: str) -> None:
    errors.append(f"{path.as_posix()}: {message}")


def frontmatter(path: Path, errors: list[str]) -> dict[str, object] | None:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        fail(errors, path, "missing opening frontmatter delimiter")
        return None
    try:
        end = lines[1:].index("---") + 1
    except ValueError:
        fail(errors, path, "missing standalone closing frontmatter delimiter")
        return None
    try:
        parsed = yaml.safe_load("\n".join(lines[1:end]))
    except yaml.YAMLError as exc:
        fail(errors, path, f"invalid YAML frontmatter: {exc}")
        return None
    if not isinstance(parsed, dict):
        fail(errors, path, "frontmatter must be an object")
        return None
    return parsed


def validate_links(repo: Path, path: Path, errors: list[str]) -> None:
    text = path.read_text(encoding="utf-8")
    for raw in LINK_RE.findall(text):
        target = raw.strip().split(maxsplit=1)[0].strip("<>")
        if not target or target.startswith(("#", "http://", "https://", "mailto:")):
            continue
        decoded = unquote(target.split("#", 1)[0])
        candidate = (path.parent / decoded).resolve()
        try:
            candidate.relative_to(repo)
        except ValueError:
            fail(errors, path, f"reference escapes repository: {target}")
            continue
        if not candidate.exists():
            fail(errors, path, f"reference does not resolve: {target}")


def validate_triggers(path: Path, errors: list[str]) -> None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(errors, path, f"invalid JSON: {exc}")
        return
    if not isinstance(payload, list) or not payload:
        fail(errors, path, "trigger spec must be a non-empty array")
        return
    outcomes: set[bool] = set()
    for index, item in enumerate(payload):
        if not isinstance(item, dict) or set(item) != {"query", "should_trigger"}:
            fail(errors, path, f"item {index} must contain only query and should_trigger")
            continue
        if not isinstance(item["query"], str) or not item["query"].strip():
            fail(errors, path, f"item {index} query must be a non-empty string")
        if not isinstance(item["should_trigger"], bool):
            fail(errors, path, f"item {index} should_trigger must be boolean")
        else:
            outcomes.add(item["should_trigger"])
    if outcomes != {False, True}:
        fail(errors, path, "trigger spec must include positive and negative examples")


def validate_public_boundary(repo: Path, errors: list[str]) -> None:
    for path in sorted(repo.rglob("*")):
        if (
            not path.is_file()
            or ".git" in path.parts
            or "__pycache__" in path.parts
            or path.suffix not in TEXT_SUFFIXES
        ):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for marker in PRIVATE_MARKERS:
            if marker in text:
                fail(errors, path.relative_to(repo), f"private identifier exposed: {marker}")
        if re.search(r"(?<![A-Za-z0-9])(?:/Users|/home)/[^\s`'\"]+", text):
            fail(errors, path.relative_to(repo), "machine-specific absolute path exposed")


def validate_repo(
    repo: Path, *, visibility: str, require_cloud_links: bool
) -> list[str]:
    repo = repo.resolve()
    errors: list[str] = []
    skills_root = repo / "skills"
    skills = (
        sorted(
            path
            for path in skills_root.iterdir()
            if path.is_dir() and (path / "SKILL.md").is_file()
        )
        if skills_root.is_dir()
        else []
    )
    if not skills:
        return ["skills: no canonical skills found"]

    for skill in skills:
        relative_skill = skill.relative_to(repo)
        if not NAME_RE.fullmatch(skill.name):
            fail(errors, relative_skill, "directory name is not a valid skill name")
        metadata = frontmatter(skill / "SKILL.md", errors)
        if metadata is not None:
            if metadata.get("name") != skill.name:
                fail(errors, relative_skill / "SKILL.md", "frontmatter name must match directory")
            description = metadata.get("description")
            if not isinstance(description, str) or not description.strip():
                fail(errors, relative_skill / "SKILL.md", "description must be non-empty text")

        for doc in sorted(skill.rglob("*.md")):
            validate_links(repo, doc, errors)
        trigger_spec = skill / "evals" / "triggers.json"
        if trigger_spec.exists():
            validate_triggers(trigger_spec, errors)

        if require_cloud_links:
            link = repo / ".claude" / "skills" / skill.name
            expected = Path("../..") / "skills" / skill.name
            if not link.is_symlink():
                fail(errors, link.relative_to(repo), "required relative cloud symlink is missing")
            elif link.readlink() != expected:
                fail(errors, link.relative_to(repo), f"expected symlink target {expected}")

    if visibility == "public":
        validate_public_boundary(repo, errors)
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--visibility", choices=("public", "private"), required=True)
    parser.add_argument("--require-cloud-links", action="store_true")
    args = parser.parse_args()
    errors = validate_repo(
        args.repo,
        visibility=args.visibility,
        require_cloud_links=args.require_cloud_links,
    )
    for error in errors:
        print(f"error: {error}", file=sys.stderr)
    if errors:
        print(f"portability validation failed: {len(errors)} error(s)", file=sys.stderr)
        return 1
    count = sum(
        1
        for path in (args.repo / "skills").iterdir()
        if path.is_dir() and (path / "SKILL.md").is_file()
    )
    print(f"portability OK: {count} skills ({args.visibility})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
