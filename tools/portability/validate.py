#!/usr/bin/env python3
"""Deterministically validate a repository of portable agent skills."""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

try:
    import yaml
except ImportError:
    sys.exit(
        "validate.py needs PyYAML to parse skill frontmatter. "
        "Install it with: pip install pyyaml"
    )


NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
REFERENCE_USE_RE = re.compile(r"(?<!\\)!?\[([^\]]+)\]\[([^\]]*)\]")
REFERENCE_DEF_RE = re.compile(
    r"^[ \t]{0,3}\[([^\]]+)\]:[ \t]*(.+)$",
    re.MULTILINE,
)
ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
FENCED_CODE_START_RE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")
INLINE_CODE_RE = re.compile(r"(`+)(.+?)\1")
ENV_TEMPLATE_SUFFIXES = (".env.example", ".env.sample", ".env.template")
CREDENTIAL_NAME_RE = re.compile(
    r"(?i)(?:api[_-]?key|token|secret|password|credential|database[_-]?url|access[_-]?key|client[_-]?secret)"
)
CREDENTIAL_ASSIGNMENT_RE = re.compile(
    r"""(?ix)
    (?P<name>["']?[A-Za-z_][A-Za-z0-9_.-]*["']?)
    \s*(?:=|:)\s*
    (?P<value>"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|[^\s,}\]]+)
    """
)
LIST_ITEM_RE = re.compile(r"^[ \t]{0,3}(?:[-+*]|\d+[.)])[ \t]+")
PRIVATE_MARKERS = (
    "Digitizers/" + "marketing-skills",
    "Digitizers/" + "digitizer-os",
    "digitizer-" + "private",
)
POSIX_HOME_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:/Users|/home|/root)/[^\s`'\"]+"
)
WINDOWS_HOME_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:[A-Z]:\\Users\\|\\\\[^\\\s]+\\Users\\)"
    r"[^\s`'\"]+"
)
LOCAL_ABSOLUTE_RE = re.compile(
    r"(?<![A-Za-z0-9])/(?:workspace|workspaces|opt)/[^\s`'\"]+"
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


def is_placeholder_value(value: str) -> bool:
    value = value.strip().strip("\"'`")
    folded = value.casefold()
    return (
        not value
        or value == "..."
        or re.fullmatch(r"\$[A-Z][A-Z0-9_]*", value) is not None
        or re.fullmatch(r"\$\{[A-Z][A-Z0-9_]*\}", value) is not None
        or re.fullmatch(r"<[^>\r\n]+>", value) is not None
        or folded in {"changeme", "example", "placeholder", "redacted", "replace_me", "xxx"}
        or folded.startswith(("your-", "your_"))
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


def markdown_target(raw: str) -> str | None:
    raw = raw.strip()
    if raw.startswith("<"):
        closing = raw.find(">")
        return None if closing < 0 else raw[1:closing]
    return raw.split(maxsplit=1)[0] if raw else ""


def inline_link_targets(text: str) -> list[str]:
    targets: list[str] = []
    index = 0
    while index < len(text):
        if text[index] == "!":
            bracket_index = index + 1
            label_start = (
                index + 2
                if bracket_index < len(text)
                and text[bracket_index] == "["
                and not is_escaped(text, bracket_index)
                else 0
            )
        else:
            label_start = (
                index + 1
                if text[index] == "[" and not is_escaped(text, index)
                else 0
            )
        if not label_start:
            index += 1
            continue

        label_end = closing_bracket(text, label_start)
        if label_end < 0 or label_end + 1 >= len(text) or text[label_end + 1] != "(":
            index += 1
            continue

        start = label_end + 2
        depth = 1
        escaped = False
        for index in range(start, len(text)):
            character = text[index]
            if escaped:
                escaped = False
                continue
            if character == "\\":
                escaped = True
                continue
            if character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
                if depth == 0:
                    targets.append(text[start:index])
                    break
        index += 1
    return targets


def is_escaped(text: str, index: int) -> bool:
    backslashes = 0
    index -= 1
    while index >= 0 and text[index] == "\\":
        backslashes += 1
        index -= 1
    return backslashes % 2 == 1


def closing_bracket(text: str, start: int) -> int:
    depth = 1
    escaped = False
    for index in range(start, len(text)):
        character = text[index]
        if escaped:
            escaped = False
            continue
        if character == "\\":
            escaped = True
            continue
        if character == "[":
            depth += 1
        elif character == "]":
            depth -= 1
            if depth == 0:
                return index
    return -1


def strip_fenced_code(text: str) -> str:
    lines = text.splitlines(keepends=True)
    kept: list[str] = []
    fence_marker: str | None = None
    fence_length = 0
    for line in lines:
        if fence_marker is None:
            match = FENCED_CODE_START_RE.match(line)
            if match:
                fence_marker = match.group(1)[0]
                fence_length = len(match.group(1))
                kept.append("\n" if line.endswith("\n") else "")
                continue
            kept.append(line)
            continue
        close = line.rstrip("\r\n")
        if re.fullmatch(
            rf"[ \t]{{0,3}}{re.escape(fence_marker)}{{{fence_length},}}[ \t]*",
            close,
        ):
            fence_marker = None
            fence_length = 0
        kept.append("\n" if line.endswith("\n") else "")
    return "".join(kept)


def strip_indented_code(text: str) -> str:
    kept: list[str] = []
    in_list_item = False
    for line in text.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        if not content.strip():
            kept.append(line)
            continue

        if LIST_ITEM_RE.match(content):
            in_list_item = True
            kept.append(line)
            continue

        indent = 0
        for character in content:
            if character == " ":
                indent += 1
            elif character == "\t":
                indent += 4
                break
            else:
                break

        if indent >= 4 and not (in_list_item and "\t" not in content[:indent] and indent < 8):
            kept.append("\n" if line.endswith("\n") else "")
        else:
            kept.append(line)
            if indent < 4:
                in_list_item = False
    return "".join(kept)


def credential_findings(path: Path, text: str) -> list[int]:
    if path.suffix == ".py":
        return python_credential_findings(text)
    return regex_credential_findings(text)


def regex_credential_findings(text: str) -> list[int]:
    findings: list[int] = []
    for number, line in enumerate(text.splitlines(), 1):
        for match in CREDENTIAL_ASSIGNMENT_RE.finditer(line):
            name = match.group("name").strip("\"'")
            if CREDENTIAL_NAME_RE.search(name) and not is_placeholder_value(
                match.group("value")
            ):
                findings.append(number)
    return findings


def python_credential_findings(text: str) -> list[int]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return regex_credential_findings(text)

    findings: list[int] = []

    def maybe_add(name: str | None, value: ast.AST, line: int) -> None:
        if not name or not CREDENTIAL_NAME_RE.search(name):
            return
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            if not is_placeholder_value(value.value):
                findings.append(line)

    def target_name(target: ast.AST) -> str | None:
        if isinstance(target, ast.Name):
            return target.id
        if isinstance(target, ast.Attribute):
            return target.attr
        if isinstance(target, ast.Subscript) and isinstance(target.slice, ast.Constant):
            if isinstance(target.slice.value, str):
                return target.slice.value
        return None

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                maybe_add(target_name(target), node.value, node.lineno)
        elif isinstance(node, ast.AnnAssign):
            maybe_add(target_name(node.target), node.value, node.lineno)
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    maybe_add(key.value, value, getattr(value, "lineno", node.lineno))
    return findings


def validate_one_link(
    repo: Path, path: Path, raw: str, errors: list[str]
) -> None:
    target = markdown_target(raw)
    if target is None:
        fail(
            errors,
            path.relative_to(repo),
            f"angle-bracketed reference is not closed: {raw}",
        )
        return
    if not target or target.startswith("#"):
        return
    scheme = URI_SCHEME_RE.match(target)
    if scheme and not (
        len(scheme.group(0)) == 2
        and len(target) > 2
        and target[2] in ("\\", "/")
    ):
        return
    decoded = unquote(target.split("#", 1)[0])
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
    text = strip_fenced_code(text)
    text = strip_indented_code(text)
    text = INLINE_CODE_RE.sub("", text)
    for raw in inline_link_targets(text):
        raw = raw.strip()
        validate_one_link(repo, path, raw, errors)

    definitions = {
        label.casefold(): target
        for label, target in REFERENCE_DEF_RE.findall(text)
    }
    for label, target in definitions.items():
        validate_one_link(repo, path, target, errors)
    for text_label, explicit_label in REFERENCE_USE_RE.findall(text):
        label = (explicit_label or text_label).casefold()
        if label not in definitions:
            fail(
                errors,
                path.relative_to(repo),
                f"reference label is not defined: {label}",
            )


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


def validate_public_boundary(repo: Path, errors: list[str]) -> None:
    for path in public_boundary_paths(repo):
        if ".git" in path.parts or "__pycache__" in path.parts:
            continue
        relative = path.relative_to(repo)
        marker = contains_private_marker(relative.as_posix())
        if marker is not None:
            fail(errors, relative, f"private identifier exposed: {marker}")
        if path.is_symlink():
            target = path.readlink()
            target_text = "\n".join((target.as_posix(), str(target)))
            resolved = (path.parent / target).resolve()
            escapes_repo = False
            try:
                resolved.relative_to(repo)
            except ValueError:
                escapes_repo = True
            if (
                target.is_absolute()
                or escapes_repo
                or contains_private_marker(target_text) is not None
                or contains_machine_specific_path(target_text)
            ):
                fail(errors, relative, "symlink target escapes public repository boundary")
            continue
        if not path.is_file():
            continue
        is_env_template = path.name.endswith(ENV_TEMPLATE_SUFFIXES)
        is_env_file = (
            path.name == ".env"
            or path.name == ".envrc"
            or path.name.endswith(".env")
            or ".env." in path.name
        )
        if is_env_file and not is_env_template:
            fail(
                errors,
                relative,
                "committed environment file is forbidden in public repos",
            )
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
                if not is_placeholder_value(value):
                    fail(errors, relative, f"non-placeholder env value on line {number}")
        for number in credential_findings(relative, text):
            fail(
                errors,
                relative,
                f"credential value is not a placeholder on line {number}",
            )
        marker = contains_private_marker(text)
        if marker is not None:
            fail(errors, relative, f"private identifier exposed: {marker}")
        if contains_machine_specific_path(text):
            fail(errors, relative, "machine-specific absolute path exposed")


def git_tracked_paths(repo: Path) -> list[Path] | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "ls-files", "-z"],
            check=True,
            capture_output=True,
            text=False,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    return sorted(
        repo / os.fsdecode(item)
        for item in result.stdout.split(b"\0")
        if item
    )


def public_boundary_paths(repo: Path) -> list[Path]:
    tracked = git_tracked_paths(repo)
    if tracked is not None:
        return tracked
    return fallback_public_boundary_paths(repo)


def fallback_public_boundary_paths(repo: Path) -> list[Path]:
    paths: list[Path] = []
    for root, dirnames, filenames in os.walk(repo, followlinks=False):
        current = Path(root)
        paths.extend(current / name for name in dirnames)
        paths.extend(current / name for name in filenames)
    return sorted(paths)


def skill_directories(repo: Path, skills_root: Path) -> list[Path]:
    if not skills_root.is_dir():
        return []
    tracked = git_tracked_paths(repo)
    if tracked is None:
        return sorted(path for path in skills_root.iterdir() if path.is_dir())
    directories: set[Path] = set()
    for path in tracked:
        relative = path.relative_to(repo)
        if len(relative.parts) >= 2 and relative.parts[0] == "skills":
            directories.add(repo / "skills" / relative.parts[1])
    return sorted(path for path in directories if path.is_dir())


def validate_repo(
    repo: Path, *, visibility: str, require_cloud_links: bool
) -> list[str]:
    repo = repo.resolve()
    errors: list[str] = []
    skills_root = repo / "skills"
    skills: list[Path] = []
    for path in skill_directories(repo, skills_root):
        if not (path / "SKILL.md").is_file():
            fail(
                errors,
                path.relative_to(repo),
                "skill directory is missing SKILL.md",
            )
            continue
        skills.append(path)
    if not skills:
        errors.append("skills: no canonical skills found")
        return errors

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
        for path in skill_directories(
            args.repo.resolve(), args.repo.resolve() / "skills"
        )
        if (path / "SKILL.md").is_file()
    )
    print(f"portability OK: {count} skills ({args.visibility})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
