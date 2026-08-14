#!/usr/bin/env python3
"""Deterministically validate a repository of portable agent skills."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path, PureWindowsPath
from urllib.parse import unquote

try:
    import yaml
except ImportError:
    sys.exit(
        "validate.py needs PyYAML to parse skill frontmatter. "
        "Install it with: pip install pyyaml"
    )

try:
    from markdown_it import MarkdownIt
except ImportError:
    sys.exit(
        "validate.py needs markdown-it-py to enumerate Markdown links. "
        "Install it with: pip install markdown-it-py"
    )


NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
ENV_TEMPLATE_SUFFIXES = (".env.example", ".env.sample", ".env.template")
PRIVATE_MARKERS = (
    "Digitizers/" + "marketing-skills",
    "Digitizers/" + "digitizer-os",
    "digitizer-" + "private",
)
POSIX_HOME_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:/Users|/home|/root)/[^\s`'\"]+"
)
WINDOWS_HOME_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:[A-Z]:[\\/]Users[\\/]|"
    r"(?<!http:)(?<!https:)(?:\\\\|//)[^\\/\s]+[\\/]Users[\\/])"
    r"[^\s`'\"]+"
)
LOCAL_ABSOLUTE_RE = re.compile(
    r"(?<![A-Za-z0-9/])/(?:workspace|workspaces|opt)"
    r"(?:/[^\s`'\"]*)?(?=$|[\s`'\"),.;:!?\]}>])"
)
URI_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


def fail(errors: list[str], path: Path, message: str) -> None:
    errors.append(f"{path.as_posix()}: {message}")


def contains_private_marker(text: str) -> str | None:
    folded = text.casefold()
    for marker in PRIVATE_MARKERS:
        if marker.casefold() in folded:
            return marker
    return None


def contains_machine_specific_path(text: str) -> bool:
    return bool(
        POSIX_HOME_RE.search(text)
        or WINDOWS_HOME_RE.search(text)
        or LOCAL_ABSOLUTE_RE.search(text)
    )


def frontmatter(
    path: Path, display_path: Path, errors: list[str]
) -> dict[str, object] | None:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        fail(errors, display_path, "missing opening frontmatter delimiter")
        return None
    try:
        end = lines[1:].index("---") + 1
    except ValueError:
        fail(errors, display_path, "missing standalone closing frontmatter delimiter")
        return None
    try:
        parsed = yaml.safe_load("\n".join(lines[1:end]))
    except yaml.YAMLError as exc:
        fail(errors, display_path, f"invalid YAML frontmatter: {exc}")
        return None
    if not isinstance(parsed, dict):
        fail(errors, display_path, "frontmatter must be an object")
        return None
    return parsed


_MD_PARSER: MarkdownIt | None = None


def link_targets(text: str) -> list[str]:
    """Enumerate link/image destinations per CommonMark.

    Walks the markdown-it-py token tree, so links inside nested lists and
    blockquotes are found, while fenced, inline and indented code never
    contribute targets. Reference links resolve through their definitions;
    definitions that are never used are still returned so their targets get
    validated. Undefined reference labels render as literal text per
    CommonMark and therefore yield no target. Duplicate targets collapse to
    one entry per document.
    """
    global _MD_PARSER
    if _MD_PARSER is None:
        # The commonmark preset caps container nesting at 20; content beyond
        # the cap is not tokenized, so a link buried deeper would silently
        # skip validation. 512 is far past anything a hand-written doc
        # reaches while keeping the parse bounded and deterministic.
        _MD_PARSER = MarkdownIt("commonmark", {"maxNesting": 512})
    env: dict[str, object] = {}
    tokens = _MD_PARSER.parse(text, env)
    targets: list[str] = []

    def walk(items) -> None:
        for token in items:
            if token.type == "link_open":
                targets.append(token.attrGet("href") or "")
            elif token.type == "image":
                targets.append(token.attrGet("src") or "")
            if token.children:
                walk(token.children)

    walk(tokens)
    references = env.get("references")
    if isinstance(references, dict):
        for definition in references.values():
            targets.append(definition.get("href") or "")
    return list(dict.fromkeys(targets))


def validate_one_link(
    repo: Path, path: Path, target: str, errors: list[str]
) -> None:
    target = target.strip()
    # Classify on the DECODED form: markdown-it percent-encodes backslashes,
    # so a raw target like C:%5CProjects%5Cguide.md would otherwise read as
    # URI scheme "C:" and be silently accepted as external.
    windows_drive = re.match(r"^[A-Za-z]:[\\/]", unquote(target))
    if (
        not target
        or target.startswith("#")
        or (URI_SCHEME_RE.match(target) and not windows_drive)
    ):
        return
    decoded = unquote(target.split("#", 1)[0].split("?", 1)[0])
    candidate = (path.parent / decoded).resolve()
    try:
        candidate.relative_to(repo)
    except ValueError:
        fail(
            errors,
            path.relative_to(repo),
            f"reference escapes repository: {target}",
        )
        return
    if not candidate.exists():
        fail(
            errors,
            path.relative_to(repo),
            f"reference does not resolve: {target}",
        )


def validate_links(repo: Path, path: Path, errors: list[str]) -> None:
    text = path.read_text(encoding="utf-8")
    for target in link_targets(text):
        validate_one_link(repo, path, target, errors)


def validate_triggers(
    path: Path, display_path: Path, errors: list[str]
) -> None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(errors, display_path, f"invalid JSON: {exc}")
        return
    if not isinstance(payload, list) or not payload:
        fail(errors, display_path, "trigger spec must be a non-empty array")
        return
    outcomes: set[bool] = set()
    for index, item in enumerate(payload):
        if not isinstance(item, dict) or set(item) != {"query", "should_trigger"}:
            fail(
                errors,
                display_path,
                f"item {index} must contain only query and should_trigger",
            )
            continue
        if not isinstance(item["query"], str) or not item["query"].strip():
            fail(
                errors,
                display_path,
                f"item {index} query must be a non-empty string",
            )
        if not isinstance(item["should_trigger"], bool):
            fail(
                errors,
                display_path,
                f"item {index} should_trigger must be boolean",
            )
        else:
            outcomes.add(item["should_trigger"])
    if outcomes != {False, True}:
        fail(
            errors,
            display_path,
            "trigger spec must include positive and negative examples",
        )


AGENT_PLUGINS_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
AGENT_PLUGINS_NAME_RE = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)*$")
AGENT_PLUGINS_FIELDS = {
    "$schema",
    "name",
    "version",
    "description",
    "author",
    "homepage",
    "repository",
    "license",
    "keywords",
    "extensions",
}
AGENT_PLUGINS_STRING_FIELDS = (
    "description",
    "homepage",
    "repository",
    "license",
)
# SemVer 2.0.0 (semver.org) — the grammar Agent Plugins 1.0 requires for
# the optional version field.
SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)
AGENT_PLUGINS_AUTHOR_FIELDS = {"name", "email", "url"}


def validate_agent_plugins_manifest(
    repo: Path, errors: list[str], *, required: bool
) -> None:
    """Validate the root Agent Plugins 1.0 manifest when present.

    Deliberately stricter than the specification in one respect: the spec
    treats unknown top-level fields as warnings, this validator treats them
    as errors so CI stays deterministic for our own manifest.
    """
    manifest_path = repo / "plugin.json"
    display_path = Path("plugin.json")
    if not manifest_path.is_file():
        if required:
            fail(
                errors,
                display_path,
                "required Agent Plugins 1.0 manifest is missing",
            )
        return
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        fail(errors, display_path, f"manifest is not readable UTF-8 JSON: {exc}")
        return
    if not isinstance(manifest, dict):
        fail(errors, display_path, "manifest must be a JSON object")
        return
    for field in sorted(set(manifest) - AGENT_PLUGINS_FIELDS):
        fail(errors, display_path, f"unknown manifest field {field!r}")
    if manifest.get("$schema") != AGENT_PLUGINS_SCHEMA:
        fail(errors, display_path, f"$schema must be {AGENT_PLUGINS_SCHEMA}")
    name = manifest.get("name")
    if (
        not isinstance(name, str)
        or not 1 <= len(name) <= 64
        or not AGENT_PLUGINS_NAME_RE.fullmatch(name)
    ):
        fail(
            errors,
            display_path,
            "name must be 1-64 lowercase alphanumerics with single"
            " internal hyphens or periods",
        )
    for field in AGENT_PLUGINS_STRING_FIELDS:
        if field in manifest and (
            not isinstance(manifest[field], str) or not manifest[field].strip()
        ):
            fail(errors, display_path, f"{field} must be non-empty text")
    if "version" in manifest:
        version = manifest["version"]
        if not isinstance(version, str) or not SEMVER_RE.fullmatch(version):
            fail(
                errors,
                display_path,
                "version must be a semantic version (SemVer 2.0.0)",
            )
    if "author" in manifest:
        author = manifest["author"]
        if not isinstance(author, dict):
            fail(errors, display_path, "author must be an object")
        else:
            for field in sorted(set(author) - AGENT_PLUGINS_AUTHOR_FIELDS):
                fail(errors, display_path, f"unknown author field {field!r}")
            for key in AGENT_PLUGINS_AUTHOR_FIELDS & set(author):
                if not isinstance(author[key], str) or not author[key].strip():
                    fail(
                        errors,
                        display_path,
                        f"author.{key} must be non-empty text",
                    )
    if "keywords" in manifest:
        keywords = manifest["keywords"]
        if not isinstance(keywords, list) or not all(
            isinstance(keyword, str) and keyword.strip() for keyword in keywords
        ):
            fail(
                errors,
                display_path,
                "keywords must be a list of non-empty strings",
            )
    if "extensions" in manifest and not isinstance(manifest["extensions"], dict):
        fail(errors, display_path, "extensions must be an object")


def validate_public_boundary(repo: Path, errors: list[str]) -> None:
    for path in public_boundary_paths(repo):
        if ".git" in path.parts or "__pycache__" in path.parts:
            continue
        relative = path.relative_to(repo)
        marker = contains_private_marker(relative.as_posix())
        if marker is not None:
            fail(errors, relative, f"private identifier exposed: {marker}")
        folded_name = path.name.casefold()
        is_env_template = folded_name.endswith(ENV_TEMPLATE_SUFFIXES)
        is_env_file = (
            folded_name == ".env"
            or folded_name == ".envrc"
            or folded_name.endswith(".env")
            or ".env." in folded_name
        )
        if is_env_file and not is_env_template:
            fail(
                errors,
                relative,
                "committed environment file is forbidden in public repos",
            )
            continue
        if path.is_symlink():
            if is_env_template:
                fail(
                    errors,
                    relative,
                    "environment template must be a regular file in public repos",
                )
            target = path.readlink()
            target_text = target.as_posix()
            resolved = (path.parent / target).resolve()
            escapes_repo = False
            try:
                resolved.relative_to(repo)
            except ValueError:
                escapes_repo = True
            if (
                target.is_absolute()
                or PureWindowsPath(target_text).is_absolute()
                or escapes_repo
                or contains_private_marker(target_text) is not None
                or contains_machine_specific_path(target_text)
            ):
                fail(errors, relative, "symlink target escapes public repository boundary")
            continue
        if not path.is_file():
            continue
        raw = path.read_bytes()
        if b"\0" in raw:
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if is_env_template:
            for number, line in enumerate(text.splitlines(), 1):
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if "=" not in stripped:
                    fail(errors, relative, f"invalid env template line {number}")
                    continue
                name, value = stripped.split("=", 1)
                if not ENV_NAME_RE.fullmatch(name):
                    fail(errors, relative, f"invalid env name on line {number}")
                placeholder = value.strip()
                allowed_placeholder = (
                    not placeholder
                    or re.fullmatch(r"\$\{[A-Z][A-Z0-9_]*\}", placeholder)
                    or re.fullmatch(r"<[^>\r\n]+>", placeholder)
                    or placeholder.casefold()
                    in {"changeme", "example", "placeholder", "replace_me", "xxx"}
                    or placeholder.casefold().startswith(("your-", "your_"))
                )
                if not allowed_placeholder:
                    fail(errors, relative, f"non-placeholder env value on line {number}")
        marker = contains_private_marker(text)
        if marker is not None:
            fail(errors, relative, f"private identifier exposed: {marker}")
        if contains_machine_specific_path(text):
            fail(errors, relative, "machine-specific absolute path exposed")


def public_boundary_paths(repo: Path) -> list[Path]:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "ls-files", "-z"],
            check=True,
            capture_output=True,
            text=False,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return fallback_public_boundary_paths(repo)
    tracked = [
        repo / item.decode("utf-8")
        for item in result.stdout.split(b"\0")
        if item
    ]
    return sorted(tracked)


def fallback_public_boundary_paths(repo: Path) -> list[Path]:
    paths: list[Path] = []
    for root, dirnames, filenames in os.walk(repo, followlinks=False):
        current = Path(root)
        paths.extend(current / name for name in dirnames)
        paths.extend(current / name for name in filenames)
    return sorted(paths)


def validate_repo(
    repo: Path,
    *,
    visibility: str,
    require_cloud_links: bool,
    require_agent_plugins_manifest: bool = False,
) -> list[str]:
    repo = repo.resolve()
    errors: list[str] = []
    validate_agent_plugins_manifest(
        repo, errors, required=require_agent_plugins_manifest
    )
    skills_root = repo / "skills"
    skill_directories = (
        sorted(path for path in skills_root.iterdir() if path.is_dir())
        if skills_root.is_dir()
        else []
    )
    skills: list[Path] = []
    for path in skill_directories:
        if not (path / "SKILL.md").is_file():
            fail(
                errors,
                path.relative_to(repo),
                "skill directory is missing SKILL.md",
            )
            continue
        skills.append(path)
    if not skills:
        return ["skills: no canonical skills found"]

    for skill in skills:
        relative_skill = skill.relative_to(repo)
        if not NAME_RE.fullmatch(skill.name):
            fail(errors, relative_skill, "directory name is not a valid skill name")
        metadata = frontmatter(
            skill / "SKILL.md", relative_skill / "SKILL.md", errors
        )
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
            validate_triggers(trigger_spec, trigger_spec.relative_to(repo), errors)

        if require_cloud_links:
            link = repo / ".claude" / "skills" / skill.name
            expected = Path("../..") / "skills" / skill.name
            if not link.is_symlink():
                fail(errors, link.relative_to(repo), "required relative cloud symlink is missing")
            elif link.readlink() != expected:
                fail(errors, link.relative_to(repo), f"expected symlink target {expected}")

    if require_cloud_links:
        cloud_root = repo / ".claude" / "skills"
        canonical_names = {skill.name for skill in skills}
        if cloud_root.is_dir():
            for entry in sorted(cloud_root.iterdir()):
                if entry.name not in canonical_names:
                    fail(
                        errors,
                        entry.relative_to(repo),
                        "noncanonical cloud skill entry",
                    )

    if visibility == "public":
        validate_public_boundary(repo, errors)
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--visibility", choices=("public", "private"), required=True)
    parser.add_argument("--require-cloud-links", action="store_true")
    parser.add_argument("--require-agent-plugins-manifest", action="store_true")
    args = parser.parse_args()
    errors = validate_repo(
        args.repo,
        visibility=args.visibility,
        require_cloud_links=args.require_cloud_links,
        require_agent_plugins_manifest=args.require_agent_plugins_manifest,
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
